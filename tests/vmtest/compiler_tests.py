"""The event compiler: golden blob, artifacts, RefVM basics, operand forms.

Split out of tests/vm_test.py (2026-08-26); run via tests/vm_test.py."""
import os
import sys

from .common import *  # noqa: F401,F403 - FAILS/check/m + shared helpers
from .common import FAILS, _run_expr, check, m
from .common import SPIKE_BLOB, SPIKE_SCRIPTS, ROOT



def test_golden_blob():
    print("[compile: golden blob]")
    prog = m.Compiler().compile(SPIKE_SCRIPTS)
    check(list(prog.code) == SPIKE_BLOB, "compiled blob is byte-identical to hand-assembly")
    check(prog.offsets == {"main": 0, "route0": 39, "route1": 66}, "script offsets")
    check(prog.entry == 0, "boot entry = main @0")
    check(prog.strings == ["HELLO FROM VM8\nPRESS A"], "interned strings")


def test_artifacts():
    print("[artifacts: scripts.mos / map / vms]")
    prog = m.Compiler().compile(SPIKE_SCRIPTS)
    mos = prog.to_scripts_mos()
    check('module "scripts"' in mos, "scripts module emitted")
    check("function fetch(off: u16) -> u8" in mos, "fetch callback emitted")
    check("const ENTRY_main: u16 = 0" in mos, "ENTRY_main const")
    check("const ENTRY_route1: u16 = 66" in mos, "ENTRY_route1 const")
    # The text origin comes from CORE, not from baked rows: core sizes the box
    # from this string's line count (border + lines + border, bottom-anchored -
    # the reference engine's geometry) BEFORE drawing the frame, so asking it is what keeps
    # the frame and the text agreeing. A baked `SCREEN_ROWS - 3` meant a 3-line
    # message printed over its own bottom border. Still SCREEN_ROWS-relative
    # underneath, so the box stays on-screen on the 12-row Lynx.
    check('text.print_string(core.box_left(), core.box_top(), "HELLO FROM VM8")' in mos,
          "string line 1 lowered")
    check('text.print_string(core.box_left(), core.box_top() + 1, "PRESS A")' in mos,
          "string line 2 lowered")
    # ... and the box's height is data: one byte per string.
    check("const STR_LINES: array[u8, " in mos and "function text_lines(id: u8) -> u8" in mos,
          "text_lines selector emitted (the box-height seam)")
    dbg = prog.to_map()
    check(dbg["total"] == 93 and dbg["entry"] == 0, "map total/entry")
    check(dbg["scripts"]["route0"] == 39, "map records script offsets")
    check(any(d["op"] == "THREAD" for d in dbg["instructions"]), "map records instructions")
    vms = prog.to_vms()
    check("main:" in vms and "THREAD     route0" in vms, ".vms labels + symbolic targets")


def test_refvm_behaviour():
    print("[RefVM: full spike behaviour]")
    prog = m.Compiler().compile(SPIKE_SCRIPTS)
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(70)
    a0, a1 = vm.pos(0), vm.pos(1)
    vm.run(40)
    b0, b1 = vm.pos(0), vm.pos(1)
    check(a0 != b0 and a1 != b1, "both actors walk concurrently")
    # into the lock cutscene (main WAITs 120 then locks)
    vm.run(120)
    check(vm.lockcount == 1, "cutscene locked")
    check(vm.box_open == 1 and vm.text_log == [0], "textbox opened with string 0")
    check(vm.pos(0) == (72, 72), "actor0 scripted to cutscene centre")
    frozen = vm.pos(1)
    vm.run(30)
    check(vm.pos(1) == frozen, "lock freezes actor1 while box is open")
    vm.run(1, a_at=[0])
    vm.run(20)
    check(vm.box_open == 0, "A dismissed the textbox")
    check(vm.pos(1) != frozen, "routes resumed after unlock")


def test_wait_timing():
    print("[RefVM: WAIT counts exact frames]")
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "wait", "frames": 5},
            {"event": "set_var", "var": "done", "value": 1},
            {"event": "stop"},
        ]},
    ])
    check(prog.variables == {"done": 0}, "variable index assigned")
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(5)
    check(vm.heap[0] == 0, "var still 0 through all 5 wait frames")
    vm.run(1)
    check(vm.heap[0] == 1, "var set on the 6th frame")
    check(vm.active[0] == 0, "main thread STOPped")


def test_negative_var():
    print("[RefVM: SET_CONST i16 sign]")
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "set_var", "var": "hp", "value": -3},
            {"event": "stop"},
        ]},
    ])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(1)
    check(vm.heap[0] == -3, "negative value round-trips as i16")


def test_self_actor_operand():
    print("[RefVM: the SELF actor operand -> the thread's bound actor]")
    # actor = "self" compiles to the SELF_ACTOR sentinel byte; the runtime
    # resolves it to the executing thread's bound actor (vm.entity's per-instance
    # slot binding). Bind ctx 0 to actor 3, move "self" -> actor 3 moves, not 0.
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "actor_set_pos", "actor": "self", "x": 50, "y": 60},
            {"event": "stop"},
        ]},
    ])
    check(m.SELF_ACTOR in prog.code, "the SELF sentinel byte is in the blob")
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.set_self(0, 3)
    vm.run(1)
    check((vm.actors[3].x, vm.actors[3].y) == (50, 60), "SELF moved the bound actor (3)")
    check((vm.actors[0].x, vm.actors[0].y) == (0, 0), "actor 0 was NOT moved")

    # An unbound thread's SELF falls back to actor 0 (never out of the pool).
    vm2 = m.RefVM(prog.code, entry=prog.entry)   # no set_self
    vm2.run(1)
    check((vm2.actors[0].x, vm2.actors[0].y) == (50, 60),
          "unbound SELF falls back to actor 0")

    # A numeric actor id still passes through unchanged (byte-identical authoring).
    prog2 = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "actor_set_pos", "actor": 2, "x": 7, "y": 8},
            {"event": "stop"},
        ]},
    ])
    vm3 = m.RefVM(prog2.code, entry=prog2.entry)
    vm3.set_self(0, 3)                            # self bound, but the op uses id 2
    vm3.run(1)
    check((vm3.actors[2].x, vm3.actors[2].y) == (7, 8), "a literal actor id is unaffected by self")


def test_self_in_expression():
    print("[RefVM: SELF inside an expression -> the bound actor's coord]")
    # actor_x(self)/actor_y(self) in an RPN expression resolve to the thread's
    # BOUND actor (the entity slot's actor), not a hardcoded id -- so a per-instance
    # On Update can branch on ITS OWN position.
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_set_pos", "actor": "self", "x": 90, "y": 30},
        {"event": "set_var", "var": "sx", "expr": "actor_x(self)"},
        {"event": "set_var", "var": "sy", "expr": "actor_y(self)"},
        {"event": "stop"},
    ]}])
    check(m.SELF_ACTOR in prog.code, "SELF sentinel present in the expression blob")
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.set_self(0, 5)                             # bind ctx 0 to actor 5
    vm.run(2)
    check(vm.heap[prog.variables["sx"]] == 90, "actor_x(self) read the bound actor's x")
    check(vm.heap[prog.variables["sy"]] == 30, "actor_y(self) read the bound actor's y")

    # An unbound thread's SELF expression falls back to actor 0.
    vm2 = m.RefVM(prog.code, entry=prog.entry)
    vm2.run(2)
    check(vm2.heap[prog.variables["sx"]] == 90 and vm2.actors[0].x == 90,
          "unbound SELF in an expression falls back to actor 0")

    # A literal actor id in an expression is unaffected by the self binding.
    prog2 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_activate", "actor": 2, "tile": 0, "x": 12, "y": 34},
        {"event": "set_var", "var": "lx", "expr": "actor_x(2)"},
        {"event": "stop"},
    ]}])
    vm3 = m.RefVM(prog2.code, entry=prog2.entry)
    vm3.set_self(0, 5)
    vm3.run(2)
    check(vm3.heap[prog2.variables["lx"]] == 12, "a literal actor id in an expr ignores self")


def test_project_file_roundtrip():
    print("[project scripts/*.evt.toml round-trips to the golden]")
    scripts_dir = os.path.join(ROOT, "projects", "vm-spike", "scripts")
    if not os.path.isdir(scripts_dir):
        print("  skip: vm-spike/scripts not present")
        return
    if m.toml is None:
        print("  skip: toml not installed")
        return
    prog = m.compile_path(scripts_dir)
    check(list(prog.code) == SPIKE_BLOB, "authored .evt.toml compiles to the golden blob")
