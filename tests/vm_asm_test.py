#!/usr/bin/env python3
"""The `.v8s` TEXT-ASSEMBLY front end (mosaik_vm.asm).

Spec 15 requires the toolchain to EMIT a text listing; this reads one back, so a
program written as VM8 assembly -- the form the spec's reference playground uses
for every example -- assembles into the same `CompiledProgram` an authored
`.evt.toml` event list produces, and therefore runs on RefVM, emits a
`scripts.mos`, and links into a ROM through the ordinary pipeline.

This checks:
  * the dialect: labels, `.string` (with \\n), `.board`, RPN token streams,
    SWITCH tables, MENU blobs, the CHANGE_SCENE sugar, `self`, 0x hex;
  * label resolution -- forward AND backward, through JUMP / CALL / THREAD;
  * EQUIVALENCE with the event-list compiler: the same program authored both ways
    assembles to byte-identical bytecode;
  * it runs on RefVM (the assembled blob is executable, not just well-formed);
  * the pack engine-state ids (spec 5.3): 16 + button = held, 24 = a box is open;
  * errors are diagnosed with a line number, not a traceback;
  * the shipped projects/vm-snake program assembles and still matches its
    committed src/scripts.mos (so a dialect change cannot silently drift it),
    and the reference playground's Snake example is that same file verbatim.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik_vm import Compiler, RefVM, VmError          # noqa: E402
from mosaik_vm.asm import assemble_v8s                  # noqa: E402
from mosaik_vm.isa import OPS, ST_HELD_BASE, ST_UI_OPEN  # noqa: E402
from mosaik import MosaikCompiler                       # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(label, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + label
          + (("  " + detail) if detail and not cond else ""))
    return bool(cond)


# --------------------------------------------------------------- the dialect
DIALECT = """
; a comment line
.board
.string 0 "HELLO\\nWORLD"
.string 2 "SECOND"

main:
    SET_CONST 5, 0x10          ; hex operand
    RPN VAR 5 PUSH 2 MUL
    SET_VAR 6
    CALL helper
    THREAD worker
    IF done                    ; forward label
    JUMP main                  ; backward label

helper:
    A_SET_POS self, 1, 2       ; `self` operand
    RET

worker:
    IDLE
    JUMP worker

done:
    STOP
"""


def dialect():
    ok = True
    prog = assemble_v8s(DIALECT)
    ok &= check("assembles", len(prog.code) > 0)
    ok &= check(".board recorded", getattr(prog, "v8s_board", False) is True)
    ok &= check("strings interned by id, gaps filled",
                prog.strings[0] == "HELLO\nWORLD" and prog.strings[1] == ""
                and prog.strings[2] == "SECOND")
    # The FIRST label is the boot entry, and every label is exported.
    ok &= check("first label is the boot entry", prog.offsets.get("main") == 0)
    ok &= check("every label exported as an entry",
                {"main", "helper", "worker", "done"} <= set(prog.offsets))
    # Labels resolve to real offsets, forwards and backwards.
    ok &= check("labels resolve to distinct offsets",
                len(set(prog.offsets.values())) == 4
                and prog.offsets["done"] > prog.offsets["helper"])
    ok &= check("hex operand read", prog.code[1] == 5 and prog.code[2] == 0x10)
    ok &= check("`self` lowered to the SELF actor id",
                prog.code[prog.offsets["helper"]] == OPS["A_SET_POS"][0]
                and prog.code[prog.offsets["helper"] + 1] == 0xFE)
    return ok


# --------- equivalence: the same program as .v8s and as an event list --------
EQUIV_V8S = """
main:
    SET_CONST 0, 7
    RPN VAR 0 PUSH 1 ADD
    SET_VAR 0
    STOP
"""

EQUIV_EVENTS = [{"name": "main", "events": [
    {"event": "set_var", "var": "v0", "value": 7},
    {"event": "set_var", "var": "v0", "expr": "v0 + 1"},
    {"event": "stop"},
]}]


def equivalence():
    a = assemble_v8s(EQUIV_V8S)
    b = Compiler().compile(EQUIV_EVENTS)
    ok = check("v8s == evt.toml bytecode (same program, two front ends)",
               a.code == b.code)
    if not ok:
        print("       v8s: %s" % list(a.code))
        print("       evt: %s" % list(b.code))
    ok &= check("v8s names its numeric heap cells", "v0" in a.variables)
    return ok


# ------------------------------- it actually RUNS ---------------------------
RUN_V8S = """
.string 0 "HI"

main:
    SET_CONST 1, 0
loop:
    RPN VAR 1 PUSH 1 ADD
    SET_VAR 1
    IDLE
    RPN VAR 1 PUSH 3 GE
    IF loop
    STOP
"""


def runs():
    """`IF` jumps when the popped value is FALSE, so this counts to 3 and stops."""
    prog = assemble_v8s(RUN_V8S)
    vm = RefVM(prog.code, entry=prog.entry)
    for _ in range(12):
        vm.frame()
    ok = check("assembled blob runs on RefVM (counted to 3)", vm.heap[1] == 3)
    ok &= check("thread ended after the loop", not any(vm.active))
    return ok


# ------------------- SWITCH / MENU / CHANGE_SCENE / pack state --------------
BLOBS = """
.string 0 "YES"
.string 1 "NO"

main:
    RPN VAR 0
    SWITCH 2, 0, zero, 1, one
    JUMP after

zero:
    MENU var 3 row 4 ids [0, 1]
    JUMP after

one:
    CHANGE_SCENE 2, 8, 9

after:
    STOP
"""


def blobs():
    ok = True
    prog = assemble_v8s(BLOBS)
    code = prog.code
    sw = code.index(OPS["SWITCH"][0])
    ok &= check("SWITCH emits its case count", code[sw + 1] == 2)
    mn = prog.offsets["zero"]
    ok &= check("MENU blob carries dest/row/count",
                code[mn] == OPS["MENU"][0] and code[mn + 1] == 3
                and code[mn + 2] == 4 and code[mn + 3] == 2)
    # RAISE is code:u8, a:u8, b:u16, c:u16 -- so CHANGE_SCENE 2,8,9 lays down
    # code 2 (the CHANGE_SCENE exception), room 2, then x and y as u16 pairs.
    cs = prog.offsets["one"]
    ok &= check("CHANGE_SCENE is sugar for RAISE 2",
                code[cs] == OPS["RAISE"][0] and code[cs + 1] == 2
                and code[cs + 2] == 2 and code[cs + 3] == 8 and code[cs + 5] == 9)
    # RefVM services the pending exception at frame step 4 (spec 10).
    vm = RefVM(prog.code, entry=prog.entry)
    vm.heap[0] = 1                                  # take the `one` branch
    for _ in range(4):
        vm.frame()
    ok &= check("CHANGE_SCENE reaches the scene change", vm.cur_scene == 2,
                "cur_scene=%d" % vm.cur_scene)
    return ok


# ------------------------- the pack engine-state ids ------------------------
PACK_STATE = """
main:
    SET_CONST 0, 0
    SET_CONST 1, 0
loop:
    IDLE
    RPN GET_STATE 22 GET_STATE 23 ADD
    SET_VAR 0
    RPN GET_STATE 24
    SET_VAR 1
    RPN GET_STATE 29
    SET_VAR 2
    JUMP loop
"""


def pack_state():
    """16 + button id = that button HELD; 24 = a text box or menu is open.

    These are what INPUT_ATTACH cannot answer -- the attach fires on a rising
    EDGE, so a bytecode auto-repeat (the DAS loop of the falling-block assembly
    sample) has to poll whether the button is STILL down. They were missing
    from the runtime, which left that sample steerable in the playground and
    not on a ROM. (vm-snake's title wait reads 16 + A and 16 + START today.)
    """
    ok = check("ISA names the pack ids", ST_HELD_BASE == 16 and ST_UI_OPEN == 24)
    prog = assemble_v8s(PACK_STATE)
    vm = RefVM(prog.code, entry=prog.entry)
    vm.frame()
    vm.frame(held=("left",))                        # id 22 = 16 + 6 (LEFT)
    ok &= check("held(LEFT) reads 1 through GET_STATE 22", vm.heap[0] == 1)
    vm.frame(held=("right",))                       # id 23 = 16 + 7 (RIGHT)
    ok &= check("held(RIGHT) reads 1 through GET_STATE 23", vm.heap[0] == 1)
    vm.frame()
    ok &= check("neither held reads 0", vm.heap[0] == 0)
    ok &= check("ui_open reads 0 with no box up", vm.heap[1] == 0)
    vm.box_open = 1
    vm.frame()
    ok &= check("ui_open reads 1 while a box is up", vm.heap[1] == 1)
    # 29 = pad_held, the BATCHED read: every held button in one bitmask,
    # bit = the portable button id (a=1, b=2, ... right=128). One GET_STATE
    # + one B_AND is the whole of an await-any-input poll.
    vm.frame(held=("a", "right"))
    ok &= check("pad_held packs a + right as 1|128", vm.heap[2] == 129)
    vm.frame()
    ok &= check("pad_held reads 0 with nothing down", vm.heap[2] == 0)
    return ok


# ------------------------------- diagnostics --------------------------------
def diagnostics():
    ok = True
    for label, src in (
            ("unknown mnemonic", "main:\n    FLOOBLE 1\n"),
            ("unknown label operand", "main:\n    JUMP nowhere\n"),
            ("no entry label", "    STOP\n"),
    ):
        try:
            assemble_v8s(src)
            ok &= check("%s rejected" % label, False)
        except VmError as e:
            ok &= check("%s rejected (%s)" % (label, str(e)[:44]), True)
        except Exception as e:                       # noqa: BLE001
            ok &= check("%s rejected with VmError, not %s"
                        % (label, type(e).__name__), False)
    return ok


# --------------- the flat API, the heap cap, and the disassembler -----------
def toolchain():
    """`asm` is part of the flat `mosaik_vm` API, the heap scan DECODES rather
    than byte-scans, and the blob disassembler round-trips through the
    assembler (spec 15: one ISA table, assembler and disassembler from it)."""
    import mosaik_vm as mv

    ok = check("mosaik_vm.assemble_v8s is flat", callable(
        getattr(mv, "assemble_v8s", None)))
    ok &= check("mosaik_vm.assemble_v8s_path is flat", callable(
        getattr(mv, "assemble_v8s_path", None)))
    ok &= check("mosaik_vm.asm submodule is reachable",
                getattr(mv, "asm", None) is not None)

    # The heap scan used to read OPERAND bytes as opcodes, so a program that
    # touches cells 0..5 reported cells up to whatever byte followed a 0x09.
    prog = assemble_v8s("main:\n"
                        "    SET_CONST 5, 200\n"    # operand 200 is NOT a cell
                        "    RPN VAR 7\n"
                        "    SET_VAR 3\n")
    ok &= check("heap scan decodes (highest cell 7 -> 8 names, not 201)",
                len(prog.variables) == 8, "got %d" % len(prog.variables))
    ok &= check("heap scan never exceeds the heap cap",
                all(i < Compiler.HEAP_CAP for i in prog.variables.values()))

    # Spec 15: heap indices < VM_HEAP, failing loudly, at every site that names one.
    cap = Compiler.HEAP_CAP
    for label, src in (
        ("SET_CONST", "main:\n    SET_CONST %d, 1\n" % cap),
        ("SET_VAR", "main:\n    SET_VAR %d\n" % cap),
        ("HANDLE", "main:\n    HANDLE %d\n" % cap),
        ("HANDLE_NEXT", "main:\n    HANDLE_NEXT %d\n" % cap),
        ("RPN VAR", "main:\n    RPN VAR %d\n" % cap),
        ("MENU dest", "main:\n    MENU var %d row 1 ids [0, 1]\n" % cap),
    ):
        try:
            assemble_v8s(src)
            ok &= check("over-cap %s refused" % label, False)
        except VmError as e:
            ok &= check("over-cap %s refused (line %s)" % (label,
                        str(e).split(":")[0].replace("line ", "")),
                        "heap cell" in str(e))

    # The disassembler: every byte accounted for, and the listing re-assembles
    # to the SAME bytes and the SAME labels (aliased anchors included).
    src = ("main:\n"
           "    SET_CONST 0, 7\n"
           "    RPN VAR 0 PUSH 3 ADD\n"
           "    SET_VAR 1\n"
           "    IF tail\n"
           "    SWITCH 2, 1, tail, -2, main\n"
           "    MENU var 2 row 1 ids [0, 1]\n"
           "    A_SET_POS self, 4, 5\n"
           "    CALL tail\n"
           "tail:\n"
           "alias:\n"
           "    STOP\n")
    prog = assemble_v8s(src)
    listing = mv.disasm(prog.code, prog.offsets)
    ok &= check("disasm covers every byte",
                sum(i["size"] for i in listing) == len(prog.code))
    ok &= check("disasm resolves a jump target to its label",
                any(i["text"] == "IF         tail" for i in listing))
    ok &= check("disasm renders the SELF actor as `self`",
                any(i["text"].startswith("A_SET_POS  self") for i in listing))
    ok &= check("disasm keeps aliased anchors",
                any(len(i["labels"]) == 2 for i in listing))
    lines = []
    for it in listing:
        lines.extend(lb + ":" for lb in it["labels"])
        lines.append("    " + it["text"])
    again = assemble_v8s("\n".join(lines))
    ok &= check("disasm -> assembler round-trips to the same bytes",
                again.code == prog.code)
    ok &= check("disasm -> assembler round-trips to the same labels",
                again.offsets == prog.offsets)

    # An explicitly written END used to emit a SECOND terminator byte, which the
    # next decode reads as a stray opcode (a corrupt blob, no diagnostic).
    ok &= check("an explicit RPN END does not double-terminate",
                assemble_v8s("main:\n    RPN VAR 0 PUSH 1 ADD END\n").code
                == assemble_v8s("main:\n    RPN VAR 0 PUSH 1 ADD\n").code)
    return ok


# ------------------------- the shipped Snake program ------------------------
def snake():
    """projects/vm-snake: a whole game as VM8 assembly -- the assembler's real
    workload (SWITCH tables of 20 and 64 arms, every RPN shape the game uses)."""
    v8s = os.path.join(ROOT, "projects", "vm-snake", "scripts", "snake.v8s")
    mos = os.path.join(ROOT, "projects", "vm-snake", "src", "scripts.mos")
    if not os.path.exists(v8s):
        return check("projects/vm-snake/scripts/snake.v8s is present", False)
    prog = assemble_v8s(open(v8s, encoding="utf-8").read())
    ok = check("snake.v8s assembles (%d bytes, %d entries)"
               % (len(prog.code), len(prog.offsets)), len(prog.code) > 1000)
    ok &= check("snake declares .board", getattr(prog, "v8s_board", False) is True)
    if os.path.exists(mos):
        emitted = prog.to_scripts_mos()
        committed = open(mos, encoding="utf-8").read()
        same = emitted.replace("\r\n", "\n") == committed.replace("\r\n", "\n")
        ok &= check("committed src/scripts.mos matches the assembler", same)
        if not same:
            print("       regenerate: python projects/vm-snake/assets/gen_scripts.py")
    # And the emitted module has to be real mosaik that compiles to C. It reads
    # the open box's text origin from vm.core (core sizes the box from the
    # string's line count, so a baked row could disagree with the frame), which
    # a real build gets from lib/vm/core.mos -- stubbed here so this stays a
    # test of the EMITTED module rather than of the runtime.
    core_stub = ('module "vm.core" {\n'
                 '    function box_top() -> u8 { return SCREEN_ROWS - 3 }\n'
                 '    function box_left() -> u8 { return 2 }\n'
                 '    function var_get(idx: u8) -> i16 { return 0 }\n'
                 '    function set_code_window(base: addr, enter: function() -> u8, '
                 'leave: function(u8)) { }\n'
                 '    function set_code_window_res(base: addr) { }\n'
                 '    export box_top, box_left, var_get, set_code_window, set_code_window_res\n}\n')
    for plat in ("gameboy", "sms", "lynx", "pce"):
        c = MosaikCompiler().compile_program(
            [("scripts.mos", prog.to_scripts_mos()), ("core.mos", core_stub)],
            platform=plat)
        ok &= check("snake scripts.mos compiles to C on %s" % plat,
                    not c.startswith("Compilation error")
                    # two modules now, so the symbol mangles per module
                    and "scripts_fetch(uint16_t off)" in c)
    return ok


# ------------------- the reference playground carries the same Snake ----------
def playground_snake():
    """docs/vm8-playground.html's Snake example IS projects/vm-snake's program,
    not a copy that can drift: the example is read out of the page the way the
    studio reads it (a JS template literal) and compared with the file."""
    import re
    html_path = os.path.join(ROOT, "docs", "vm8-playground.html")
    v8s = os.path.join(ROOT, "projects", "vm-snake", "scripts", "snake.v8s")
    html = open(html_path, encoding="utf-8").read()
    block = html[html.index("const EXAMPLES"):html.index("if (typeof module")]
    pat = re.compile(r"\{ name: '([^']+)', quant: (\d+), src: `(.*?)`\s*\},", re.S)
    found = {m.group(1): (int(m.group(2)), m.group(3)) for m in pat.finditer(block)}
    snake = [n for n in found if n.startswith("Snake")]
    ok = check("the playground carries one Snake example: %s" % sorted(found),
               len(snake) == 1)
    if not snake:
        return False
    quant, src = found[snake[0]]
    src = src.replace("\\`", "`").replace("\\$", "$").replace("\\\\", "\\")
    same = src == open(v8s, encoding="utf-8").read()
    ok &= check("its source is snake.v8s verbatim", same)
    if not same:
        print("       re-embed projects/vm-snake/scripts/snake.v8s in the page")
    ok &= check("it runs at the spec-minimum QUANT, as the sample does", quant == 16)
    return ok


def main():
    ok = True
    for name, fn in (("dialect", dialect), ("equivalence", equivalence),
                     ("runs on RefVM", runs), ("blob operands", blobs),
                     ("pack engine state", pack_state),
                     ("diagnostics", diagnostics),
                     ("toolchain (flat API / heap cap / disasm)", toolchain),
                     ("vm-snake", snake),
                     ("the playground's Snake", playground_snake)):
        print("\n-- %s" % name)
        ok &= fn()
    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
