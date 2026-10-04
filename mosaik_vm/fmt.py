"""mosaik_vm.fmt - Shared mosaik-source emit helpers (identifiers, arrays, RPN disassembly)."""

from .isa import OPS, RPN, SELF_ACTOR, iter_instructions


_RPN_NAME = {v: k for k, v in RPN.items()}


def _disasm_rpn(b):
    """Readable postfix for the .vms artifact, e.g. `PUSH 5 VAR 0 ADD`."""
    out, i = [], 0
    b = bytes(b)
    while i < len(b):
        tk = b[i]
        i += 1
        name = _RPN_NAME.get(tk, "?%02x" % tk)
        if tk == RPN["PUSH"]:
            out.append("PUSH %d" % (b[i] | (b[i + 1] << 8)))
            i += 2
        elif tk == RPN["RAND"]:
            out.append("RAND %d" % (b[i] | (b[i + 1] << 8)))
            i += 2
        elif tk in (RPN["VAR"], RPN["ACTOR_X"], RPN["ACTOR_Y"], RPN["GET_STATE"],
                    RPN["ACTOR_HP"], RPN["ACTOR_MOVING"], RPN["ARG"]):
            out.append("%s %d" % (name, b[i]))
            i += 1
        elif tk == RPN["END"]:
            break
        else:
            out.append(name)
    return " ".join(out)


# Operands that name a CODE offset (rendered as a label when one is known) and
# ones that name an ACTOR (rendered `self` for the SELF sentinel). Everything
# else prints as a plain number.
_TARGET_OPERAND = {"JUMP": (0,), "THREAD": (0,), "THREADN": (0,), "IF": (0,),
                   "CALL": (0,), "TIMER_SET": (2,), "INPUT_ATTACH": (1,),
                   "MUSIC_PLAY": (0,), "SET_PLAYER_HIT": (0,)}
_ACTOR_OPERAND = {"A_ACTIVATE": (0,), "A_SET_POS": (0,), "A_MOVE_TO": (0,),
                  "A_SPEED": (0,), "A_MOVE_START": (0,), "A_SET_CLIP": (0,),
                  "A_SET_POS_E": (0,), "A_MOVE_START_E": (0,),
                  "A_DEACTIVATE": (0,), "A_SET_GROUP": (0,), "A_SET_HP": (0,),
                  "A_DAMAGE": (0,), "A_SET_DIR": (0,), "SELF": (0,)}


def disasm(code, labels=None):
    """Disassemble a compiled bytecode blob into per-instruction records.

    Returns ``[{off, size, op, text, label, labels}]`` -- one entry per
    instruction, in blob order, where `text` is the rendered assembly line,
    `labels` lists every label anchored at that offset (several may share one:
    a `sub:` immediately followed by its `sub_loop:`) and `label` is the first
    of them, or None. The output is `.v8s`-shaped, so a listing reads like the
    source it came from and re-assembles to the same bytes.

    This is the BLOB-side listing (spec 15: one ISA table, assembler and
    disassembler generated from it). `CompiledProgram.to_vms()` is the
    compiler-side one and prints a program the compiler just lowered; this one
    reads bytes back, which is what lets a debugger point at a live PC. Both
    step through `isa.iter_instructions`, so their operand widths cannot drift.

    `labels` is an optional ``{name: offset}`` map (a `CompiledProgram.offsets`);
    jump/call/thread targets resolve through it.
    """
    code = bytes(code)
    by_off = {}
    for name, off in (labels or {}).items():
        by_off.setdefault(int(off), []).append(name)

    def _target(v):
        got = by_off.get(v)
        return got[0] if got else str(v)

    out = []
    for off, name, size, rpn in iter_instructions(code):
        body = code[off + 1:off + size]
        if name == "RPN":
            parts = []
            for tok, val in rpn:
                parts.append(tok if val is None else "%s %d" % (tok, val))
            text = "RPN        %s" % " ".join(parts)
        elif name == "MENU":
            n = body[2]
            text = "MENU       var %d row %d ids %s" % (
                body[0], body[1], list(body[3:3 + n]))
        elif name == "SWITCH":
            n = body[0]
            pairs = []
            for k in range(n):
                b = body[1 + k * 4:5 + k * 4]
                val = b[0] | (b[1] << 8)
                tgt = b[2] | (b[3] << 8)
                pairs.append("%d, %s" % (val - 0x10000 if val > 0x7FFF else val,
                                         _target(tgt)))
            text = ("SWITCH     %d, %s" % (n, ", ".join(pairs))).rstrip(", ")
        else:
            widths = OPS[name][1]
            args, i = [], 0
            for k, w in enumerate(widths):
                if w == "u16":
                    v = body[i] | (body[i + 1] << 8)
                    i += 2
                else:
                    v = body[i]
                    i += 1
                if k in _TARGET_OPERAND.get(name, ()):
                    args.append(_target(v))
                elif k in _ACTOR_OPERAND.get(name, ()) and v == SELF_ACTOR:
                    args.append("self")
                else:
                    args.append(str(v))
            text = ("%-10s %s" % (name, ", ".join(args))).rstrip()
        anchors = by_off.get(off, [])
        out.append({"off": off, "size": size, "op": name, "text": text,
                    "label": anchors[0] if anchors else None,
                    "labels": list(anchors)})
    return out


def _ident(name):
    out = "".join(c if c.isalnum() else "_" for c in str(name)).lower()
    if not out or out[0].isdigit():
        out = "s_" + out
    return out


def _escape(text):
    """Escape a display string for emission inside a mosaik "..." literal.

    Beyond backslash/quote, it FOLDS C0 control chars (a stray '\\n' in a HUD
    label) to a space and ASCII-REPLACES non-ASCII (matching the string-blob
    path's `.encode('ascii','replace')`, since the console fonts are ASCII) --
    so neither can break the generated source or leak raw bytes into a literal.
    Multi-line DIALOGUE is split into per-line print_string calls BEFORE this,
    so a legitimate newline never reaches here; the fold only guards the odd
    label/tiles text that isn't line-split. Byte-identical for ASCII text with
    no control chars (every existing sample)."""
    s = "".join(" " if ord(c) < 0x20 else c for c in str(text))
    s = s.encode("ascii", "replace").decode("ascii")
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _emit_array(lines, c_type, name, n, values, per_line=16):
    lines.append("    const %s: array[%s, %d] = [" % (name, c_type, n))
    for i in range(0, len(values), per_line):
        chunk = ", ".join(str(v) for v in values[i:i + per_line])
        tail = "," if i + per_line < len(values) else ""
        lines.append("        %s%s" % (chunk, tail))
    lines.append("    ]")
