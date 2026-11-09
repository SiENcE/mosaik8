"""mosaik_vm.asm - a text-assembly (`.v8s`) FRONT END for the VM8 toolchain.

Spec §15 requires the toolchain to EMIT a text assembly listing; this reads one
back, so a program written as VM8 assembly (the form the spec's reference
playground uses for its examples) assembles to the same `CompiledProgram` an
authored `.evt.toml` event list produces -- and therefore runs on `RefVM`,
emits a `scripts.mos`, and builds into a ROM through the ordinary pipeline.

The dialect is the spec playground's, so its examples are portable verbatim:

    ; comment                        -- to end of line (a ';' inside a string is content)
    .string <id> "text"              -- intern a string at an explicit id (\\n allowed;
                                        0..65535, past 255 only a text box reaches it)
    .board                           -- playground-only render hint; recorded, not executed
    label:                           -- a jump/call/thread target
    MNEM a, b                        -- operands per the ISA table; `self` = the SELF actor
    RPN PUSH 5 VAR 3 ADD             -- token stream, terminated automatically
    MENU var <v> row <r> ids [a, b]  -- the choice blob
    SWITCH <n>, <val>, <target> ...  -- the jump table
    CHANGE_SCENE r, x, y             -- sugar for RAISE 2, r, x, y
    UI_TEXT <id past 255>            -- sugar for STR_PAGE id >> 8 + UI_TEXT id & 255

Operands accept decimal, 0x hex, a label name (resolved to its u16 offset), or
`self`. Labels are the program's entry points: the FIRST one is the boot entry,
and every label is exported as an entry offset, so `.v8s` and `.evt.toml`
programs are interchangeable downstream.
"""

import os
import re

from .isa import (MIN_VM_STACK, NARGS, OPS, RPN, SELF_ACTOR, Label, VmError,
                  _RPN_OPERAND, _op_size, _rpn_max_depth, iter_instructions)
from .compiler import Compiler, CompiledProgram

# Opcodes whose operand 0 is a HEAP CELL index. Spec 15 requires the toolchain
# to fail loudly on a heap index >= VM_HEAP, and these are every site that can
# name one from an operand (MENU's destination and the RPN `VAR` read are the
# other two, checked where they are parsed).
_HEAP_OPERAND0 = ("SET_CONST", "SET_VAR", "HANDLE", "HANDLE_NEXT")
# ...and the ops whose heap cell is a LATER operand (review #23 / H4: the
# runtime of DATA_PEEK wrote `heap[dst]` unguarded). opcode -> operand indexes.
_HEAP_OPERANDS = {"DATA_PEEK": (0, 1), "TBL_GET": (2,), "A_GET_DIR": (1,),
                  "GET_IND": (0, 1), "SET_IND": (0,)}


def _fit(v, width, what, ln):
    """An operand VALIDATED, not masked (spec §15, review #23): a u8 takes
    -128..255 and a u16 -32768..65535 (a negative is a two's-complement
    spelling); anything else used to assemble silently to its low bits."""
    lo, hi = (-0x80, 0xFF) if width == 1 else (-0x8000, 0xFFFF)
    if not lo <= v <= hi:
        raise VmError("line %d: %s operand %d does not fit %s (%d..%d)"
                      % (ln, what, v, "u8" if width == 1 else "u16", lo, hi))
    return v


def _check_cell(idx, what, ln, cap=None):
    """Refuse a heap index past the VM heap (spec 15's compile-time hard caps).

    Silently wrapping one is a wrong-behaviour bug, not a build error: the
    runtime bounds-guards its heap sites, so an over-cap write simply does
    nothing and the program misbehaves with no diagnostic anywhere. `cap` is
    the project's `[build] vm_heap` (E2), the class default (128) without one."""
    cap = Compiler.HEAP_CAP if cap is None else cap
    if not 0 <= idx < cap:
        raise VmError("line %d: %s addresses heap cell %d, but the heap has %d "
                      "cells (0..%d)" % (ln, what, idx, cap, cap - 1))
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


def assemble_v8s(text, heap_cap=None):
    """Assemble VM8 text assembly into a CompiledProgram.

    Two passes, like the spec playground: pass 1 fixes each item's SIZE so
    label offsets are known, pass 2 emits bytes with labels resolved. Raises
    VmError with a line number on the first problem. `heap_cap` is the
    project's `[build] vm_heap` (heap.project_heap_cap; E2), 128 when None.
    """
    cap = Compiler.HEAP_CAP if heap_cap is None else heap_cap
    strings, board = {}, False
    items = []

    for ln, raw in enumerate(text.splitlines(), 1):
        sm = _STRING_RE.match(raw)
        if sm:
            sid = int(sm.group(1))
            if sid >= Compiler.STRING_MAX:          # E3: 256 pages of 256
                raise VmError("line %d: string id %d > %d"
                              % (ln, sid, Compiler.STRING_MAX - 1))
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
                # The operand widths are the ISA's ONE table (`_RPN_OPERAND`,
                # what the decoder steps by): a hand list here missed
                # ACTOR_MOVING's actor byte, so `RPN ACTOR_MOVING 3` failed to
                # re-assemble its own listing (review #24).
                width = _RPN_OPERAND.get(code, 0)
                if width:
                    if i >= len(toks):
                        raise VmError("line %d: RPN %s needs an operand" % (ln, t))
                    v = _fit(_num(toks[i], {}, "RPN %s" % t, ln), width,
                             "RPN %s" % t, ln)
                    if t == "VAR":
                        _check_cell(v, "RPN VAR", ln, cap)
                    if t == "ARG" and not 0 <= v < NARGS:
                        raise VmError("line %d: RPN ARG %d: a thread has %d "
                                      "arguments (0..%d)" % (ln, v, NARGS, NARGS - 1))
                    i += 1
                    body.append(v & 0xFF)
                    if width == 2:
                        body.append((v >> 8) & 0xFF)
            # Spec §15: the stream must fit the expression stack, which DROPS a
            # push past it on console (the compiler checks its own streams).
            depth = _rpn_max_depth(bytes(body))
            if depth > MIN_VM_STACK:
                raise VmError("line %d: RPN needs %d stack cells, the VM has %d"
                              % (ln, depth, MIN_VM_STACK))
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
            _check_cell(int(mm.group(1)), "MENU destination", ln, cap)
            for v in ids:
                if not 0 <= v <= 255:
                    # E3: an option id is one byte; strings past 255 are for
                    # text boxes only (STR_PAGE).
                    raise VmError("line %d: MENU option string %d: menu options "
                                  "must be among strings 0..255" % (ln, v))
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
        if mnem == "UI_TEXT" and len(ops) == 1:
            sid = _num(ops[0], {}, "UI_TEXT", ln)
            if 255 < sid < Compiler.STRING_MAX:     # E3: a string past page 0
                pg, lo = sid >> 8, sid & 0xFF
                items.append(_Item("op", mnem="STR_PAGE", ln=ln, text=line,
                                   size=_op_size("STR_PAGE"),
                                   emit=lambda _lab, pg=pg: bytes([OPS["STR_PAGE"][0], pg])))
                ops = [str(lo)]
        if mnem not in OPS:
            raise VmError("line %d: unknown opcode %r" % (ln, im.group(1)))

        if mnem == "SWITCH":
            n = _num(ops[0], {}, "SWITCH count", ln) if ops else -1
            if n < 0 or len(ops) != 1 + n * 2:
                raise VmError("line %d: SWITCH syntax is "
                              "`SWITCH <n>, <value>, <target> ...`" % ln)
            if n > 0xFF:                    # review #21: the count is a u8
                raise VmError("line %d: SWITCH has %d cases, at most 255" % (ln, n))
            pairs = list(ops[1:])

            def _emit_switch(labels, n=n, pairs=pairs, ln=ln):
                out = bytearray([OPS["SWITCH"][0], n & 0xFF])
                for k in range(0, len(pairs), 2):
                    val = _fit(_num(pairs[k], labels, "SWITCH value", ln), 2,
                               "SWITCH value", ln) & 0xFFFF
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
                v = _fit(_num(tok, labels, mnem, ln), 2 if w == "u16" else 1, mnem, ln)
                if k == 0 and mnem in _HEAP_OPERAND0:
                    _check_cell(v, mnem, ln, cap)
                if k in _HEAP_OPERANDS.get(mnem, ()):
                    _check_cell(v, mnem, ln, cap)
                if (mnem in ("INPUT_ATTACH", "INPUT_DETACH") and k == 0
                        and (v & 0x7F) >= 8):
                    # H5: buttons are 0..7 (bit 7 is INPUT_ATTACH's override
                    # flag); the runtime indexes an 8-entry table with it.
                    raise VmError("line %d: %s button %d: buttons are 0..7 (+128 "
                                  "for the override flag)" % (ln, mnem, v & 0x7F))
                if mnem == "THREADN" and k == 1 and not 0 <= v <= NARGS:
                    raise VmError("line %d: THREADN passes %d arguments, a thread "
                                  "has %d" % (ln, v, NARGS))
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
    variables = {"v%d" % i: i for i in range(_heap_cells_used(code, cap))}

    prog = CompiledProgram(bytes(code), dict(labels), order, strtab,
                           variables, debug, {})
    prog.v8s_board = board                          # playground render hint
    return prog


def _heap_cells_used(code, cap=None):
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
    return min(hi, Compiler.HEAP_CAP if cap is None else cap)


def v8s_heap_cap(path):
    """The heap a `.v8s` file is assembled against: its project's
    `[build] vm_heap` when it lives in `<project>/scripts/`, else the default
    (E2: a `.v8s` used to check every cell against 128)."""
    from .heap import project_heap_cap
    d = os.path.dirname(os.path.abspath(path))
    if os.path.basename(d) == "scripts":
        return project_heap_cap(os.path.dirname(d))
    return Compiler.HEAP_CAP


def assemble_v8s_path(path):
    """Assemble a `.v8s` file against its project's heap."""
    with open(path, "r", encoding="utf-8") as f:
        return assemble_v8s(f.read(), heap_cap=v8s_heap_cap(path))
