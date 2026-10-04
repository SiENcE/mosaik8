"""Scene-type emission, samples, scaffold, spec vectors, dispatch lockstep.

Split out of tests/vm_test.py (2026-08-26); run via tests/vm_test.py."""
import os
import sys

from .common import *  # noqa: F401,F403 - FAILS/check/m + shared helpers
from .common import FAILS, _run_expr, check, m
from .common import SPIKE_BLOB, SPIKE_SCRIPTS, ROOT



def test_scene_type_emission():
    print("[scenes: SCENE_TYPE is VM-gated + additive]")
    try:
        import mosaik_scenes as scn
    except Exception as e:  # pragma: no cover
        print("  skip: mosaik_scenes import failed:", e)
        return
    if m.toml is None:
        print("  skip: toml not installed")
        return
    base = os.path.join(ROOT, "projects", "game-slice")
    world_path = os.path.join(base, "world.toml")
    if not os.path.isfile(world_path):
        print("  skip: game-slice world not present")
        return
    w = m.toml.load(world_path)
    base_out = scn.transpile(w, base)
    check("SCENE_TYPE" not in base_out, "non-VM world emits NO SCENE_TYPE (byte-identical)")

    w2 = m.toml.load(world_path)
    w2.setdefault("world", {})["vm"] = True
    w2["scene"][0]["scene_type"] = "menu"
    vm_out = scn.transpile(w2, base)
    check("SCENE_TYPE" in vm_out, "VM world emits SCENE_TYPE")
    check("const SCTYPE_MENU: u8 = 5" in vm_out, "SCTYPE_* enum consts emitted")
    # scene 0 = menu (5), the rest default topdown (0)
    n = len(w2["scene"])
    check(("[%d, " % 5) in vm_out or ("[%d]" % 5) in vm_out or ("[ %d" % 5) in vm_out
          or ("= [\n        5," in vm_out) or ("5, 0" in vm_out),
          "scene 0 typed as menu in SCENE_TYPE")


def test_new_samples_compile():
    print("[samples: vm-switch / vm-camlock event lists compile]")
    if m.toml is None:
        print("  skip: toml not installed")
        return
    for name, need in (("vm-switch", "main"), ("vm-camlock", "lockcam"),
                       ("vm-cam", "main"), ("vm-input", "go_right"),
                       ("vm-shoot", "fire"), ("vm-doors", "main"), ("vm-ui", "main"),
                       ("vm-bigcode", "wander"), ("vm-quest", "elder_talk"),
                       ("vm-combat", "main"), ("vm-rpg", "elder_talk"), ("vm-uiquest", "title_init"),
                       ("vm-shop", "shop"), ("vm-overworld", "villager_talk"),
                       ("vm-dialogue", "merchant_talk"), ("vm-save", "bump"),
                       ("vm-sound", "sfx_coin"), ("vm-music", "play_song"),
                       ("vm-uilatch", "talk_a"), ("vm-cutmusic", "run_cutscene")):
        d = os.path.join(ROOT, "projects", name, "scripts")
        if not os.path.isdir(d):
            print("  skip: %s scripts not present" % name)
            continue
        prog = m.compile_path(d)
        check(len(prog.code) > 0 and need in prog.offsets,
              "%s compiles (has %s script)" % (name, need))
    # vm-bigcode is the bytecode-streaming proof: its blob must exceed the Lynx
    # streaming threshold (else it would stay resident and prove nothing).
    bc = os.path.join(ROOT, "projects", "vm-bigcode", "scripts")
    if os.path.isdir(bc):
        from mosaik.codegen.generator import CodeGenerator
        prog = m.compile_path(bc)
        check(len(prog.code) > CodeGenerator.LYNX_CODE_STREAM_THRESHOLD,
              "vm-bigcode blob (%d B) exceeds the Lynx stream threshold" % len(prog.code))


def test_scaffold():
    print("[scaffold: New Project produces a compilable VM project]")
    import tempfile
    import shutil
    tmp = tempfile.mkdtemp(prefix="vmscaffold_")
    try:
        root = os.path.join(tmp, "hello-vm")
        written = m.scaffold_project(root, "hello-vm")
        for rel in ("mosaik.toml", os.path.join("src", "main.mos"),
                    os.path.join("scripts", "main.evt.toml"),
                    os.path.join("src", "scripts.mos")):
            check(os.path.isfile(os.path.join(root, rel)), "scaffold wrote %s" % rel)
        shell = open(os.path.join(root, "src", "main.mos")).read()
        check("core.boot(scripts.fetch, scripts.render_text, scripts.ENTRY_main)" in shell,
              "shell boots the interpreter with the scripts module")
        # The shell's doc COMMENT must not contain a quoted `import "..."`: the build's
        # import scanner is a regex over raw text (comments included), so a quoted import
        # in a comment is read as a real import -> a fresh scaffold (no clips.mos yet)
        # failed to build on the bogus `clips` / `vm.canim` import.
        import re as _re
        scanned = _re.findall(r'import\s+"([^"]+)"', shell)
        check("clips" not in scanned and "vm.canim" not in scanned,
              "the shell imports no clips/vm.canim (a fresh scaffold builds): %s" % scanned)
        # the starter events must compile cleanly to a blob
        prog = m.compile_path(os.path.join(root, "scripts"))
        check(len(prog.code) > 0 and "main" in prog.offsets, "starter events compile")
        check("wander" in prog.offsets, "starter has a wander thread")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_vm8_spec_vectors():
    # The worked conformance vectors of docs/vm8-spec.md §17 (V1-V9), run as raw
    # hand-assembled blobs against the reference VM.
    print("[VM8 spec §17: worked vectors V1-V9]")

    # V1 -- arithmetic: SET_CONST heap[0]=5; RPN VAR 0, PUSH 3, ADD; SET_VAR heap[1].
    vm = m.RefVM(bytes([0x09, 0, 5, 0,
                        0x08, 0x02, 0, 0x01, 3, 0, 0x10, 0xFF,
                        0x0A, 1,
                        0x00]))
    vm.frame()
    check(vm.heap[0] == 5 and vm.heap[1] == 8 and vm.active[0] == 0,
          "V1: heap[0]=5, heap[1]=8 after frame 1, thread dead")

    # V2 -- WAIT timing: WAIT n yields on n frames (the reference VM's wait_frames).
    vm = m.RefVM(bytes([0x03, 2, 0x00]))
    vm.frame()
    check(vm.active[0] == 1, "V2: WAIT 2 yields on frame 1 (thread alive)")
    vm.frame()
    check(vm.active[0] == 1, "V2: WAIT 2 yields on frame 2 (thread alive)")
    vm.frame()
    check(vm.active[0] == 0, "V2: WAIT 2 dies on frame 3")
    vm = m.RefVM(bytes([0x03, 1, 0x00]))
    vm.frame()
    check(vm.active[0] == 1, "V2: WAIT 1 yields exactly once")
    vm.frame()
    check(vm.active[0] == 0, "V2: WAIT 1 dies on frame 2")
    vm = m.RefVM(bytes([0x03, 0, 0x00]))
    vm.frame()
    check(vm.active[0] == 0, "V2: WAIT 0 dies on frame 1 without yielding")

    # V3 -- lock leak: LOCK; STOP releases the lock via §11; other threads runnable.
    # sibling @2: RPN VAR2 PUSH1 ADD; SET_VAR 2; IDLE; JUMP 2
    code = bytes([0x04, 0x00,
                  0x08, 0x02, 2, 0x01, 1, 0, 0x10, 0xFF, 0x0A, 2, 0x02, 0x01, 2, 0])
    vm = m.RefVM(code)
    vm._spawn(2)
    vm.frame()
    check(vm.lockcount == 0, "V3: lock ... stop leaves lockcount == 0")
    n0 = vm.heap[2]
    vm.frame()
    check(vm.heap[2] == n0 + 1, "V3: the sibling thread keeps running")

    # V4 -- handle join: HANDLE_NEXT 3; THREAD child; child = WAIT 2; STOP.
    code = bytes([0x14, 3, 0x06, 6, 0, 0x00,      # main @0, child @6
                  0x03, 2, 0x00])
    vm = m.RefVM(code)
    vm.frame()
    check(vm.heap[3] == 1, "V4: heap[3] reads 1 while the child runs")
    vm.frame()
    check(vm.heap[3] == 1, "V4: still 1 (WAIT 2 yields a second frame)")
    vm.frame()
    check(vm.heap[3] == 0, "V4: heap[3] reads 0 from the frame the child STOPs")

    # V5 -- args: push 7; THREADN child, 1; the child's RPN ARG 0 pushes 7.
    code = bytes([0x08, 0x01, 7, 0, 0xFF,         # main @0: push 7
                  0x07, 10, 0, 1,                 # THREADN child@10, n=1
                  0x00,                           # STOP @9; child @10
                  0x08, 0x0A, 0, 0xFF,            # child @10: RPN ARG 0
                  0x0A, 5,                        # SET_VAR heap[5]
                  0x00])
    vm = m.RefVM(code)
    vm.frame()
    check(vm.heap[5] == 7, "V5: THREADN passes 7 into the child's arg[0]")

    # V6 -- exception teardown: a lock-holding, UI-owning thread RAISEs 2,4,40,60.
    # survivor @11: RPN VAR0 PUSH1 ADD; SET_VAR 0; IDLE; JUMP 11
    code = bytes([0x06, 11, 0, 0x04, 0x0F, 0x02, 4, 40, 0, 60, 0,
                  0x08, 0x02, 0, 0x01, 1, 0, 0x10, 0xFF, 0x0A, 0, 0x02, 0x01, 11, 0])
    vm = m.RefVM(code)
    vm.box_open = 1
    vm.ui_owner = 0
    vm.tmr_active[0], vm.tmr_period[0], vm.tmr_count[0] = 1, 90, 90
    vm.in_active[0] = 1
    vm.frame()
    check(vm.active[0] == 0 and vm.lockcount == 0, "V6: raiser dead, lock released")
    check(vm.box_open == 0 and vm.ui_owner == 255, "V6: the raiser's box closed")
    check(vm.cur_scene == 4 and vm.change_log == [(4, 40, 60)],
          "V6: step 4 set cur_scene=4 and called the scene pack (4, 40, 60)")
    check(all(t == 0 for t in vm.tmr_active) and all(a == 0 for a in vm.in_active),
          "V6: timers + input attachments reset")
    # ... and the sibling does NOT run the next frame: a scene change kills
    # every context but exempt_ctx (spec 10; corrected 2026-08-09 - it used
    # to survive, which let a room's threads outlive their room).
    before = vm.heap[0]
    vm.frame()
    check(vm.heap[0] == before,
          "V6: a scene change killed the sibling thread too")

    # V7 -- guards. SET_VAR 144 pops but writes nothing (heap is 128).
    vm = m.RefVM(bytes([0x08, 0x01, 5, 0, 0xFF,   # push 5
                        0x0A, 144,                # SET_VAR 144: pop, no write
                        0x0A, 0,                  # SET_VAR 0: pops underflow-0
                        0x00]))
    vm.frame()
    check(vm.heap[0] == 0 and vm.stack[0] == [],
          "V7: SET_VAR 144 popped the 5 and wrote nothing")
    # RPN DIV on an empty stack: two underflow-zeros popped, div-by-0 pushes 0.
    vm = m.RefVM(bytes([0x09, 1, 99, 0,           # heap[1] = 99 (sentinel)
                        0x08, 0x13, 0xFF,         # RPN: DIV on an empty stack
                        0x0A, 1,                  # SET_VAR heap[1] = the pushed 0
                        0x00]))
    vm.frame()
    check(vm.heap[1] == 0 and vm.stack[0] == [],
          "V7: DIV on an empty stack pushes 0 (underflow pops clamp to 0)")
    # unknown opcode 0xFF ends the thread and runs the §11 cleanup.
    vm = m.RefVM(bytes([0x04, 0xFF]))             # LOCK; opcode 0xFF
    vm.frame()
    check(vm.active[0] == 0 and vm.lockcount == 0,
          "V7: opcode 0xFF ends the thread and releases its lock (§11)")

    # V8 -- budget: JUMP 0 at offset 0 consumes exactly QUANT dispatches this
    # frame; another thread still receives its quantum.
    code = bytes([0x01, 0, 0,                     # @0: JUMP 0 (tight loop)
                  0x08, 0x02, 4, 0x01, 1, 0, 0x10, 0xFF, 0x0A, 4, 0x02, 0x01, 3, 0])
    vm = m.RefVM(code)
    vm._spawn(3)
    dispatches = []
    orig_step = vm._step
    vm._step = lambda c: (dispatches.append(c), orig_step(c))[1]
    vm.frame()
    check(dispatches.count(0) == vm.QUANT,
          "V8: the JUMP-0 loop got exactly QUANT (%d) dispatches" % vm.QUANT)
    check(vm.active[0] == 1 and vm.heap[4] == 1,
          "V8: the loop thread survives and the other thread still ran")

    # V9 -- SWITCH fall-through: push v; SWITCH 2 (1->20, 2->25); pc lands past
    # the WHOLE table on no match.
    def _switch_result(v):
        code = bytes([0x08, 0x01, v & 0xFF, (v >> 8) & 0xFF, 0xFF,
                      0x0C, 2, 1, 0, 20, 0, 2, 0, 25, 0,   # table ends @15
                      0x09, 0, 1, 0, 0x00,                 # @15 fall-through: heap[0]=1
                      0x09, 0, 2, 0, 0x00,                 # @20 case 1: heap[0]=2
                      0x09, 0, 3, 0, 0x00])                # @25 case 2: heap[0]=3
        vm = m.RefVM(code)
        vm.frame()
        return vm.heap[0]
    check(_switch_result(5) == 1, "V9: no match falls through past the whole table")
    check(_switch_result(1) == 2, "V9: value 1 jumps to its case target")
    check(_switch_result(2) == 3, "V9: value 2 jumps to its case target")


def test_vm8_bitwise():
    print("[VM8: bitwise RPN group (B_AND/B_OR/B_XOR/SHL/SHR/B_NOT)]")
    # via the expression compiler (C precedence: | < ^ < & < shift, ~ unary)
    cases = {
        "12 & 10": 8,
        "12 | 10": 14,
        "12 ^ 10": 6,
        "1 << 4": 16,
        "256 >> 4": 16,
        "~0": -1,
        "~5 & 255": 250,
        "1 | 2 ^ 3 & 5": 3,        # & then ^ then | (C precedence)
        "1 << 2 + 1": 8,           # + binds tighter than << (C precedence)
        "1 << 20": 16,             # shift count masked to 0..15 (20 & 15 = 4)
        "(0 - 1) >> 12": 15,       # SHR is LOGICAL on the u16 bits
    }
    for expr, want in cases.items():
        got = _run_expr(expr)
        check(got == want, "%s == %d (got %d)" % (expr, want, got))
    # via raw RPN token bytes (B_AND 0x15, B_OR 0x16, B_XOR 0x17, SHL 0x18,
    # SHR 0x19, B_NOT 0x44)
    code = bytes([0x08, 0x01, 12, 0, 0x01, 10, 0, 0x15, 0xFF, 0x0A, 0,   # 12 & 10
                  0x08, 0x01, 12, 0, 0x01, 10, 0, 0x16, 0xFF, 0x0A, 1,   # 12 | 10
                  0x08, 0x01, 12, 0, 0x01, 10, 0, 0x17, 0xFF, 0x0A, 2,   # 12 ^ 10
                  0x08, 0x01, 1, 0, 0x01, 18, 0, 0x18, 0xFF, 0x0A, 3,    # 1 << (18&15)
                  0x08, 0x01, 0xF0, 0xFF, 0x01, 4, 0, 0x19, 0xFF, 0x0A, 4,  # 0xFFF0 >> 4
                  0x08, 0x01, 0, 0, 0x44, 0xFF, 0x0A, 5,                 # ~0
                  0x00])
    vm = m.RefVM(code)
    vm.frame()
    check(vm.heap[0] == 8 and vm.heap[1] == 14 and vm.heap[2] == 6,
          "raw RPN B_AND/B_OR/B_XOR: 8 / 14 / 6")
    check(vm.heap[3] == 4, "raw RPN SHL masks the count (1 << 18 == 1 << 2 == 4)")
    check(vm.heap[4] == 0x0FFF, "raw RPN SHR is logical on u16 (0xFFF0 >> 4 == 0x0FFF)")
    check(vm.heap[5] == -1, "raw RPN B_NOT 0 == -1")


def test_vm8_threads():
    print("[VM8: THREADN args + HANDLE/HANDLE_NEXT joins + RAISE 1 RESET]")

    # THREADN via the event compiler: start_thread with args -> arg(n) in the child.
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "set_var", "var": "gold", "value": 4},
            {"event": "start_thread", "script": "child", "args": ["7", "gold + 1", "3"]},
            {"event": "stop"}]},
        {"name": "child", "events": [
            {"event": "set_var", "var": "a0", "expr": "arg(0)"},
            {"event": "set_var", "var": "a1", "expr": "arg(1)"},
            {"event": "set_var", "var": "a2", "expr": "arg(2)"},
            {"event": "set_var", "var": "a3", "expr": "arg(3)"},
            {"event": "stop"}]}])
    check(0x07 in list(prog.code), "start_thread with args emits THREADN (0x07)")
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(3)
    V = prog.variables
    check([vm.heap[V["a0"]], vm.heap[V["a1"]], vm.heap[V["a2"]], vm.heap[V["a3"]]]
          == [7, 5, 3, 0],
          "the child reads arg(0..2) = 7, 5, 3; an unpassed arg(3) reads 0")
    # the compiler rejects more than NARGS args
    try:
        m.Compiler().compile([
            {"name": "main", "events": [
                {"event": "start_thread", "script": "main",
                 "args": ["1", "2", "3", "4", "5"]}]}])
        check(False, "start_thread with 5 args rejected (NARGS = 4)")
    except m.VmError:
        check(True, "start_thread with 5 args rejected (NARGS = 4)")

    # raw THREADN with n=5 > NARGS: all 5 values are POPPED, the extra
    # (last-pushed) is dropped, arg[0..3] get the first four pushed.
    code = bytes([0x08,
                  0x01, 1, 0, 0x01, 2, 0, 0x01, 3, 0, 0x01, 4, 0, 0x01, 5, 0,
                  0xFF,                            # push 1,2,3,4,5 (@0..16)
                  0x07, 22, 0, 5,                  # THREADN child, n=5 (@17)
                  0x00,                            # STOP (@21); child @22
                  0x08, 0x0A, 0, 0xFF, 0x0A, 0,    # heap[0] = arg(0)
                  0x08, 0x0A, 1, 0xFF, 0x0A, 1,
                  0x08, 0x0A, 2, 0xFF, 0x0A, 2,
                  0x08, 0x0A, 3, 0xFF, 0x0A, 3,
                  0x08, 0x0A, 4, 0xFF, 0x0A, 4,    # arg(4) is OOB -> 0
                  0x00])
    vm = m.RefVM(code)
    vm.frame()
    check([vm.heap[i] for i in range(5)] == [1, 2, 3, 4, 0],
          "THREADN n=5: extras popped + dropped, arg[0..3] = 1,2,3,4, arg(4) reads 0")
    check(vm.stack[0] == [], "THREADN popped ALL 5 values off the parent's stack")

    # HANDLE_NEXT / THREAD join lifecycle: heap[h] = 1 while the child runs,
    # 0 from the frame it STOPs.
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "handle_next", "var": "hjob"},
            {"event": "start_thread", "script": "job"},
            {"event": "stop"}]},
        {"name": "job", "events": [
            {"event": "wait", "frames": 3}, {"event": "stop"}]}])
    check(0x14 in list(prog.code), "handle_next emits HANDLE_NEXT (0x14)")
    vm = m.RefVM(prog.code, entry=prog.entry)
    hi = prog.variables["hjob"]
    vm.frame()
    check(vm.heap[hi] == 1, "join cell reads 1 while the child runs (frame 1)")
    vm.frame()
    check(vm.heap[hi] == 1, "join cell still 1 mid-wait (frame 2)")
    vm.frame()
    check(vm.heap[hi] == 1, "join cell still 1 mid-wait (frame 3)")
    vm.frame()
    check(vm.heap[hi] == 0, "join cell reads 0 from the frame the child dies")

    # HANDLE (self-bind): the cell zeroes on ANY death path, incl. external kill.
    prog = m.Compiler().compile([
        {"name": "main", "loop": True, "events": [
            {"event": "handle", "var": "h"},
            {"event": "idle"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    hi = prog.variables["h"]
    vm.frame()
    check(vm.heap[hi] == 1, "HANDLE binds the running thread (cell reads 1)")
    vm.kill(0)
    check(vm.heap[hi] == 0, "an external kill() zeroes the handle cell (§11.4)")

    # RAISE 1 (RESET): kill all threads, KEEP the heap, restart context 0 at entry.
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "set_var", "var": "boots", "expr": "boots + 1"},
            {"event": "if", "cond": "boots == 1", "then": [
                {"event": "start_thread", "script": "spin"},
                {"event": "reset"}]},
            {"event": "stop"}]},
        {"name": "spin", "loop": True, "events": [{"event": "idle"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    bi = prog.variables["boots"]
    vm.frame()
    check(vm.reset_log == [1], "RAISE 1 serviced at step 4 (reset_log)")
    check(sum(vm.active) == 1 and vm.active[0] == 1,
          "RESET killed every thread (spin too) and re-activated context 0")
    check(vm.heap[bi] == 1, "the heap SURVIVES a RESET (boots still 1)")
    vm.frame()
    check(vm.heap[bi] == 2, "the restarted main ran again (boots -> 2)")
    check(sum(vm.active) == 0, "second boot takes the else path and STOPs")


def test_dispatch_lockstep_and_pruning():
    """The opcode-dispatch PRUNE: `lib/vm/core.mos` guards every arm of its
    opcode switch (and every RPN token group) on a `VM_OP_*` / `VM_RPN_*` flag
    the build derives from the program's own bytecode, so an instruction the
    blob cannot contain costs no ROM.

    Two things have to hold or the fold is unsafe:
      * every `case OP_X` name is a real ISA opcode and every ISA opcode has an
        arm -- a mismatch means either a guard that never resolves (dead arm
        kept, harmless) or an opcode with no handler (a hang, not harmless);
      * every arm is actually guarded, so nothing is silently unprunable.
    """
    print("[dispatch: core.mos <-> ISA lockstep + every arm guarded]")
    import re
    core = os.path.join(ROOT, "lib", "vm", "core.mos")
    src = open(core, encoding="utf-8").read()

    cases = re.findall(r"case (OP_\w+) \{", src)
    case_names = {c[3:] for c in cases}
    ops = set(m.OPS)
    check(case_names == ops,
          "core.mos case arms match mosaik_vm.OPS exactly (only-in-core=%s, only-in-ISA=%s)"
          % (sorted(case_names - ops), sorted(ops - case_names)))

    # Each arm's body must open with its own guard.
    unguarded = [c for c in cases
                 if not re.search(r"case %s \{\s*\r?\n\s*if VM_%s \{" % (c, c), src)]
    check(not unguarded, "every opcode arm is guarded by its VM_OP_* flag (%s)"
          % (sorted(unguarded)[:4] or "all guarded"))

    # Falling out of the switch (what a PRUNED arm does) must stop the thread,
    # exactly like the unknown-opcode default -- never run on with a desynced pc.
    tail = src.split("case OP_SET_PLAYER_HIT")[-1]
    check("vm_active[c] = 0" in tail.split("reset_scene_ui")[0].rsplit("}", 3)[0],
          "a pruned arm falls through to the runaway-thread bail")

    # The build turns a blob into those flags: only the opcodes present are on.
    from mosaik8_build import _vm_dispatch_defines
    quest = os.path.join(ROOT, "projects", "vm-quest", "src", "scripts.mos")
    if os.path.isfile(quest):
        d = _vm_dispatch_defines([("scripts.mos", open(quest, encoding="utf-8").read())])
        on = {k[len("VM_OP_"):] for k, v in d.items() if v and k.startswith("VM_OP_")}
        truth = {i["op"] for i in
                 m.compile_path(os.path.join(ROOT, "projects", "vm-quest", "scripts")).debug}
        check(on == truth, "the build's flags match the blob's opcodes (%d on)" % len(on))
        check(len(on) < len(ops),
              "a real game leaves most arms prunable (%d of %d used)" % (len(on), len(ops)))
    # A program with no VM blob gets NO defines, so its output is byte-identical.
    check(_vm_dispatch_defines([("a.mos", 'module "m" { }')]) == {},
          "a non-VM program yields no defines (byte-identical)")


def test_every_opcode_executes():
    """ISA-driven smoke test (review V-11): EVERY opcode in isa.OPS and every
    RPN token in isa.RPN executes at least once in the reference VM with
    zeroed operands, on a fresh machine and again with a non-empty stack, a
    bound actor and an armed latch, without raising. It pins nothing about
    semantics -- the per-feature tests do that -- but it is the net V-2 fell
    through: a waiting SHAKE crashed the RefVM and no test reached the path.
    A new opcode is covered the day it lands in the table."""
    print("[ISA smoke: every opcode + RPN token executes in the RefVM]")
    crashed = []
    for name, (op, operands) in sorted(m.OPS.items(), key=lambda kv: kv[1][0]):
        if operands is None:                     # the three variable-length forms
            body = {"RPN": [0x08, 0xFF], "SWITCH": [0x0C, 0],
                    "MENU": [0x31, 0, 0, 0]}[name]
        else:
            body = [op] + [0] * sum(2 if w == "u16" else 1 for w in operands)
        # ...followed by a STOP so a non-waitable op ends cleanly; a waitable
        # one rewinds and yields, and three frames are enough to see it.
        code = bytes(body + [0x00])
        for variant in ("fresh", "primed"):
            try:
                vm = m.RefVM(code, entry=0)
                if variant == "primed":
                    vm.stack[0] = [3, 2, 1, 5]        # values for the _E pops
                    vm.set_self(0, 1)
                    vm.shk_wait = 1                  # an armed SHAKE latch
                    vm.actors[0].active = 1
                for _ in range(3):
                    vm.frame(held=["a"])
            except Exception as e:                   # noqa: BLE001 - the point
                crashed.append("%s(%s): %s: %s" % (name, variant, type(e).__name__, e))
    check(not crashed, "every opcode executes without raising (%s)"
          % (crashed[:3] or "all %d" % len(m.OPS)))
    crashed = []
    for name, tk in sorted(m.RPN.items(), key=lambda kv: kv[1]):
        imm = m.isa._RPN_OPERAND.get(tk, 0)
        code = bytes([0x08, 0x01, 1, 0, 0x01, 2, 0, tk] + [0] * imm + [0xFF, 0x0A, 0, 0x00])
        try:
            vm = m.RefVM(code, entry=0)
            vm.frame()
        except Exception as e:                       # noqa: BLE001
            crashed.append("%s: %s: %s" % (name, type(e).__name__, e))
    check(not crashed, "every RPN token evaluates without raising (%s)"
          % (crashed[:3] or "all %d" % len(m.RPN)))
