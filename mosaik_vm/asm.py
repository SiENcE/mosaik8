"""mosaik_vm.asm - a text-assembly (`.v8s`) FRONT END for the VM8 toolchain.

Spec §15 requires the toolchain to EMIT a text assembly listing; this reads one
back, so a program written as VM8 assembly (the form the spec's reference
playground uses for its examples) assembles to the same `CompiledProgram` an
authored `.evt.toml` event list produces -- and therefore runs on `RefVM`,
emits a `scripts.mos`, and builds into a ROM through the ordinary pipeline.

The dialect is the spec playground's, so its examples are portable verbatim:

    ; comment                        -- to end of line (a ';' inside a string is content)
    .string <id> "text"              -- intern a string at an explicit id (\\n allowed)
    .board                           -- playground-only render hint; recorded, not executed
    label:                           -- a jump/call/thread target
    MNEM a, b                        -- operands per the ISA table; `self` = the SELF actor
    RPN PUSH 5 VAR 3 ADD             -- token stream, terminated automatically
    MENU var <v> row <r> ids [a, b]  -- the choice blob
    SWITCH <n>, <val>, <target> ...  -- the jump table
    CHANGE_SCENE r, x, y             -- sugar for RAISE 2, r, x, y

Operands accept decimal, 0x hex, a label name (resolved to its u16 offset), or
`self`. Labels are the program's entry points: the FIRST one is the boot entry,
and every label is exported as an entry offset, so `.v8s` and `.evt.toml`
programs are interchangeable downstream.
"""

import re

from .isa import (OPS, RPN, SELF_ACTOR, Label, VmError, _op_size,
                  iter_instructions)
from .compiler import Compiler, CompiledProgram

# Opcodes whose operand 0 is a HEAP CELL index. Spec 15 requires the toolchain
# to fail loudly on a heap index >= VM_HEAP, and these are every site that can
# name one from an operand (MENU's destination and the RPN `VAR` read are the
# other two, checked where they are parsed).
_HEAP_OPERAND0 = ("SET_CONST", "SET_VAR", "HANDLE", "HANDLE_NEXT")


def _check_cell(idx, what, ln):
    """Refuse a heap index past the VM heap (spec 15's compile-time hard caps).

    Silently wrapping one is a wrong-behaviour bug, not a build error: the
    runtime bounds-guards its heap sites, so an over-cap write simply does
    nothing and the program misbehaves with no diagnostic anywhere."""
    if not 0 <= idx < Compiler.HEAP_CAP:
        raise VmError("line %d: %s addresses heap cell %d, but the heap has %d "
                      "cells (0..%d)" % (ln, what, idx, Compiler.HEAP_CAP,
                                         Compiler.HEAP_CAP - 1))
    return idx

_STRING_RE = re.compile(r'^\s*\.string\s+(\d+)\s+"((?:[^"\\]|\\.)*)"\s*(;.*)?$')
_LABEL_RE = re.compile(r"^([A-Za-z_]\w*):$")
_INSTR_RE = re.compile(r"^([A-Za-z_.]\w*)\s*(.*)$")
_MENU_RE = re.compile(r"^var\s+(\d+)\s+row\s+(\d+)\s+ids\s*\[([\d\s,]*)\]$", re.I)


def _num(tok, labels, what, ln):
    """A numeric operand: decimal / 0x hex / `self` / a label name."""
    t = str(tok).strip()
    if t.lower() == "self":
        return SELF_ACTOR
    if t in labels:
        return labels[t]
    try:
        return int(t, 0)
    except ValueError:
        raise VmError("line %d: %s expects a number or a known label, got %r"
                      % (ln, what, tok))


class _Item:
    """One assembled element: a label anchor or an instruction's bytes."""
    __slots__ = ("kind", "name", "mnem", "ln", "text", "size", "emit")

    def __init__(self, kind, **kw):
        self.kind = kind
        for k, v in kw.items():
            setattr(self, k, v)


def assemble_v8s(text):
    """Assemble VM8 text assembly into a CompiledProgram.

    Two passes, like the spec playground: pass 1 fixes each item's SIZE so
    label offsets are known, pass 2 emits bytes with labels resolved. Raises
    VmError with a line number on the first problem.
    """
    strings, board = {}, False
    items = []

    for ln, raw in enumerate(text.splitlines(), 1):
        sm = _STRING_RE.match(raw)
        if sm:
            sid = int(sm.group(1))
            if sid > 255:
                raise VmError("line %d: string id %d > 255" % (ln, sid))
            strings[sid] = (sm.group(2).replace("\\n", "\n")
                            .replace('\\"', '"').replace("\\\\", "\\"))
            continue
        line = re.sub(r";.*$", "", raw).strip()
        if not line:
            continue
        if line == ".board":
            board = True                 # a playground render hint; harmless here
            continue
        lm = _LABEL_RE.match(line)
        if lm:
            items.append(_Item("label", name=lm.group(1), ln=ln, size=0))
            continue
        im = _INSTR_RE.match(line)
        if not im:
            raise VmError("line %d: unreadable line %r" % (ln, line))
        mnem, rest = im.group(1).upper(), im.group(2).strip()

        if mnem == "RPN":
            toks = [t for t in re.split(r"[\s,]+", rest) if t]
            body, i = bytearray(), 0
            while i < len(toks):
                t = toks[i].upper()
                i += 1
                if t not in RPN:
                    raise VmError("line %d: unknown RPN token %r" % (ln, t))
                if t == "END":
                    # The terminator is appended below, so an explicitly written
                    # END is redundant. Emitting it too used to leave a SECOND
                    # END byte in the stream, which the next decode reads as a
                    # stray opcode -- a corrupt blob with no diagnostic.
                    continue
                code = RPN[t]
                body.append(code)
                # PUSH/RAND carry a u16; the actor/var/state/arg reads a u8.
                width = 2 if t in ("PUSH", "RAND") else (
                    1 if t in ("VAR", "ACTOR_X", "ACTOR_Y", "ACTOR_HP",
                               "GET_STATE", "ARG") else 0)
                if width:
                    if i >= len(toks):
                        raise VmError("line %d: RPN %s needs an operand" % (ln, t))
                    v = _num(toks[i], {}, "RPN %s" % t, ln)
                    if t == "VAR":
                        _check_cell(v, "RPN VAR", ln)
                    i += 1
                    body.append(v & 0xFF)
                    if width == 2:
                        body.append((v >> 8) & 0xFF)
            body.append(RPN["END"])
            items.append(_Item("op", mnem=mnem, ln=ln, text=line,
                               size=1 + len(body),
                               emit=lambda _lab, b=bytes(body): bytes([OPS["RPN"][0]]) + b))
            continue

        if mnem == "MENU":
            mm = _MENU_RE.match(rest)
            if not mm:
                raise VmError("line %d: MENU syntax is "
                              "`MENU var <v> row <r> ids [id, ...]`" % ln)
            ids = [int(s) for s in mm.group(3).split(",") if s.strip()]
            if not 1 <= len(ids) <= 255:
                raise VmError("line %d: MENU needs 1..255 ids" % ln)
            _check_cell(int(mm.group(1)), "MENU destination", ln)
            blob = bytes([int(mm.group(1)) & 0xFF, int(mm.group(2)) & 0xFF,
                          len(ids)] + [v & 0xFF for v in ids])
            items.append(_Item("op", mnem=mnem, ln=ln, text=line,
                               size=1 + len(blob),
                               emit=lambda _lab, b=blob: bytes([OPS["MENU"][0]]) + b))
            continue

        ops = [t for t in re.split(r"[\s,]+", rest) if t] if rest else []
        if mnem == "CHANGE_SCENE":                  # spec §6 sugar for RAISE 2
            if len(ops) != 3:
                raise VmError("line %d: CHANGE_SCENE expects room, x, y" % ln)
            mnem, ops = "RAISE", ["2"] + ops
        if mnem not in OPS:
            raise VmError("line %d: unknown opcode %r" % (ln, im.group(1)))

        if mnem == "SWITCH":
            n = _num(ops[0], {}, "SWITCH count", ln) if ops else -1
            if n < 0 or len(ops) != 1 + n * 2:
                raise VmError("line %d: SWITCH syntax is "
                              "`SWITCH <n>, <value>, <target> ...`" % ln)
            pairs = list(ops[1:])

            def _emit_switch(labels, n=n, pairs=pairs, ln=ln):
                out = bytearray([OPS["SWITCH"][0], n & 0xFF])
                for k in range(0, len(pairs), 2):
                    val = _num(pairs[k], labels, "SWITCH value", ln) & 0xFFFF
                    tgt = _num(pairs[k + 1], labels, "SWITCH target", ln) & 0xFFFF
                    out += bytes([val & 0xFF, val >> 8, tgt & 0xFF, tgt >> 8])
                return bytes(out)

            items.append(_Item("op", mnem=mnem, ln=ln, text=line,
                               size=2 + n * 4, emit=_emit_switch))
            continue

        widths = OPS[mnem][1]
        if len(ops) != len(widths):
            raise VmError("line %d: %s expects %d operands, got %d"
                          % (ln, mnem, len(widths), len(ops)))

        def _emit_op(labels, mnem=mnem, ops=ops, widths=widths, ln=ln):
            out = bytearray([OPS[mnem][0]])
            for k, (tok, w) in enumerate(zip(ops, widths)):
                v = _num(tok, labels, mnem, ln)
                if k == 0 and mnem in _HEAP_OPERAND0:
                    _check_cell(v, mnem, ln)
                if w == "u16":
                    out += bytes([v & 0xFF, (v >> 8) & 0xFF])
                else:
                    out.append(v & 0xFF)
            return bytes(out)

        items.append(_Item("op", mnem=mnem, ln=ln, text=line,
                           size=_op_size(mnem), emit=_emit_op))

    # Pass 1: label offsets (sizes are already fixed above).
    labels, order, off = {}, [], 0
    for it in items:
        if it.kind == "label":
            if it.name in labels:
                raise VmError("line %d: duplicate label %r" % (it.ln, it.name))
            labels[it.name] = off
            order.append(it.name)
        else:
            off += it.size
    if not order:
        raise VmError("no labels: a program needs at least one entry (e.g. `main:`)")

    # Pass 2: emit, resolving labels.
    code, debug = bytearray(), []
    for it in items:
        if it.kind == "label":
            continue
        debug.append({"off": len(code), "script": order[0], "event": it.ln,
                      "op": it.mnem, "text": it.text})
        b = it.emit(labels)
        if len(b) != it.size:
            raise VmError("line %d: %s sized %d but emitted %d bytes (assembler bug)"
                          % (it.ln, it.mnem, it.size, len(b)))
        code += b
    if len(code) > 0xFFFF:
        raise VmError("blob is %d bytes, over the 65535-byte cap (u16 PC)" % len(code))

    # The string table is id-indexed; fill any gap so ids stay stable.
    smax = (max(strings) + 1) if strings else 0
    strtab = [strings.get(i, "") for i in range(smax)]
    # `.v8s` addresses heap cells by NUMBER, so synthesise names for the tools
    # that show variables (the studio's Variables dock, the disassembler).
    variables = {"v%d" % i: i for i in range(_heap_cells_used(code))}

    prog = CompiledProgram(bytes(code), dict(labels), order, strtab,
                           variables, debug, {})
    prog.v8s_board = board                          # playground render hint
    return prog


def _heap_cells_used(code):
    """Highest heap index the program touches, +1 -- just enough to name the cells
    a `.v8s` program actually uses (it addresses them numerically).

    This DECODES the blob (`isa.iter_instructions`) rather than scanning bytes.
    The old byte scan treated any byte that happened to equal the SET_CONST /
    SET_VAR opcode as an instruction, operand bytes included, so the falling-block assembly sample reported
    256 cells against a 128-cell heap. Every heap site counts: the operand-0 ops,
    MENU's destination, and the RPN `VAR` read."""
    hi = 0
    code = bytes(code)
    for off, name, _size, rpn in iter_instructions(code):
        if name in _HEAP_OPERAND0 or name == "MENU":
            hi = max(hi, code[off + 1] + 1)
        elif name == "RPN":
            for tok, val in rpn:
                if tok == "VAR":
                    hi = max(hi, val + 1)
    return min(hi, Compiler.HEAP_CAP)


def assemble_v8s_path(path):
    """Assemble a `.v8s` file."""
    with open(path, "r", encoding="utf-8") as f:
        return assemble_v8s(f.read())
