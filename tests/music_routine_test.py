"""W7h - `MUSIC_ROUTINE` (0x49): a hUGE `6xy` cell runs a VM8 script.

The reference engine's `EVENT_SET_MUSIC_ROUTINE` / the reference VM's `vm_music_routine`. Four slots;
the effect's own parameter byte picks one (`& 3`) and carries the argument
(`>> 4`); the script is spawned from the MAIN LOOP, never from the driver's
interrupt. `projects/vm-musicroutine/verify.py` is the ROM-level gate (14
checks, in `project_verify_test`'s default path) - this file pins the pieces it
cannot see from outside:

  * the RefVM's semantics, which are the spec's;
  * the source contracts in `lib/vm/core.mos` (the LOCK gate, the scene reset,
    the one drain site);
  * the generated C, in both directions - a project that attaches no routine
    must get no thunk, no queue, no table and a NULL `routines` field, which is
    what kept the pre-W7h engine SAFE (a `6xy` into a NULL table crashes the
    ROM, measured);
  * the by-use glue wiring, since the registration and the C body it calls are
    emitted on two different conditions and going out of step is an undefined
    symbol.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik import MosaikCompiler        # noqa: E402
from mosaik_vm import isa, glue          # noqa: E402
from mosaik_vm.compiler import Compiler  # noqa: E402
from mosaik_vm.refvm import RefVM        # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- %s" % (detail,)) if detail else ""))


def build(scripts):
    """Compile an event-list program and hand back a booted RefVM."""
    prog = Compiler().compile(scripts)
    vm = RefVM(prog.code, prog.entry)
    return prog, vm


SCRIPTS = [
    {"name": "main", "events": [
        {"event": "music_routine", "slot": 1, "script": "hit"},
        {"event": "music_routine", "slot": 2, "script": "hit2"},
        {"event": "stop"},
    ]},
    {"name": "hit", "events": [
        {"event": "set_var", "var": "runs", "expr": "runs + 1"},
        {"event": "set_var", "var": "arg0", "expr": "arg(0)"},
        {"event": "wait", "frames": 30},
        {"event": "stop"},
    ]},
    {"name": "hit2", "events": [
        {"event": "set_var", "var": "other", "expr": "other + 1"},
        {"event": "stop"},
    ]},
]


def test_isa():
    print("\n[the opcode]")
    code, ops = isa.OPS["MUSIC_ROUTINE"]
    check("MUSIC_ROUTINE is 0x49, the last of the retired battle block",
          code == 0x49, hex(code))
    check("its operands are (slot u8, entry u16)", ops == ["u8", "u16"],
          repr(ops))
    used = [v[0] for v in isa.OPS.values()]
    check("...and nothing else claims that byte",
          used.count(0x49) == 1)


def test_refvm_semantics():
    print("\n[RefVM: the slot, the argument and the busy gate]")
    prog, vm = build(SCRIPTS)
    cells = prog.variables
    vm.frame()                                  # main attaches both slots
    check("attaching writes the slot the operand names",
          vm.music_routine_entry[1] is not None
          and vm.music_routine_entry[2] is not None
          and vm.music_routine_entry[0] is None,
          repr(vm.music_routine_entry))

    # The driver would push the effect's PARAMETER BYTE; 0x51 = slot 1, arg 5.
    vm.music_routine_q.append(0x51)
    vm.frame()
    check("the queued byte picks the slot with `& 3`",
          vm.heap[cells["runs"]] == 1, vm.heap[cells["runs"]])
    check("...and carries the argument in `>> 4`, as arg(0)",
          vm.heap[cells["arg0"]] == 5, vm.heap[cells["arg0"]])
    check("the spawn is logged with both", vm.music_routine_log == [(1, 5)],
          repr(vm.music_routine_log))

    # The first instance is still inside its `wait 30`.
    vm.music_routine_q.append(0x21)             # slot 1 again, arg 2
    vm.frame()
    check("a second fire while the first instance RUNS is dropped",
          vm.heap[cells["runs"]] == 1, vm.heap[cells["runs"]])
    check("...and the argument is not overwritten",
          vm.heap[cells["arg0"]] == 5, vm.heap[cells["arg0"]])

    # ...but a DIFFERENT slot is gated on its own.
    vm.music_routine_q.append(0x32)             # slot 2, arg 3
    vm.frame()
    check("a different slot fires while slot 1 is held",
          vm.heap[cells["other"]] == 1, vm.heap[cells["other"]])

    # ...and once the first instance ends, slot 1 is free again.
    for _ in range(40):
        vm.frame()
    vm.music_routine_q.append(0x11)             # slot 1, arg 1
    vm.frame()
    check("the slot frees when its instance ends",
          vm.heap[cells["runs"]] == 2, vm.heap[cells["runs"]])
    check("...with the NEW argument", vm.heap[cells["arg0"]] == 1,
          vm.heap[cells["arg0"]])


def test_refvm_unattached_aborts_the_drain():
    print("\n[RefVM: an unattached slot aborts the drain - the reference VM's own quirk]")
    prog, vm = build(SCRIPTS)
    cells = prog.variables
    vm.frame()
    # Slot 0 is attached to nothing. Reference-VM CONSUMES the item and RETURNS, so
    # everything behind it in the queue waits a frame. Copied deliberately.
    vm.music_routine_q.append(0x40)             # slot 0, unattached
    vm.music_routine_q.append(0x32)             # slot 2, behind it
    vm.frame()
    check("the unattached item is consumed and the rest is left queued",
          vm.heap[cells["other"]] == 0 and vm.music_routine_q == [0x32],
          "other=%d q=%s" % (vm.heap[cells["other"]], vm.music_routine_q))
    vm.frame()
    check("...and drains on the NEXT frame", vm.heap[cells["other"]] == 1,
          vm.heap[cells["other"]])


def test_refvm_lock_and_scene():
    print("\n[RefVM: the lock gate and the scene reset]")
    prog, vm = build(SCRIPTS)
    cells = prog.variables
    vm.frame()
    vm.lockcount = 1
    vm.music_routine_q.append(0x51)
    vm.frame()
    check("a cutscene LOCK freezes the drain, as it freezes the timers",
          vm.heap[cells["runs"]] == 0 and vm.music_routine_q == [0x51],
          "runs=%d q=%s" % (vm.heap[cells["runs"]], vm.music_routine_q))
    vm.lockcount = 0
    vm.frame()
    check("...and it runs once the lock lifts", vm.heap[cells["runs"]] == 1,
          vm.heap[cells["runs"]])


CORE = open(os.path.join(ROOT, "lib", "vm", "core.mos"),
            encoding="utf-8").read()


def test_core_source_contracts():
    print("\n[lib/vm/core.mos]")
    sites = (CORE.count("music_events_update()")
             - CORE.count("function music_events_update()"))
    check("the drain has exactly ONE call site", sites == 1, sites)
    # The call site must be inside run()'s unlocked block, beside the timers -
    # the reference VM runs music_events_update() in the same `if (!VM_ISLOCKED())`.
    body = CORE[CORE.index("tick_input_attach()     -- input scripts"):]
    body = body[:body.index("run_scripts()")]
    check("...and it is inside the unlocked block, after the timers",
          "music_events_update()" in body)
    check("the whole thing folds behind VM_OP_MUSIC_ROUTINE",
          CORE.count("if VM_OP_MUSIC_ROUTINE {") >= 4,
          CORE.count("if VM_OP_MUSIC_ROUTINE {"))
    check("a scene change clears the registrations",
          "music_rt_on = 0" in CORE)
    check("attachment is a BITMASK, not a sentinel in the entry array",
          "music_rt_on |= 1 << slot" in CORE
          and "var music_rt_on: u8" in CORE)
    check("the busy gate goes through core's own alive_gen",
          "alive_gen(music_rt_ctx[slot], music_rt_gen[slot]) == 0" in CORE)
    check("the seam setter is exported", "set_music_routines" in
          CORE[CORE.index("    export boot, run,"):])


USER = '''
module "main" {
    import "platform.video"
    import "vm.core"
    import "vm.music_huge"
    import "scripts"
    function main() {
        video.enable_lcd()
        core.boot(scripts.fetch, scripts.render_text, scripts.ENTRY_main)
        core.set_music_routines(music_huge.routine_next)
        core.run()
    }
    export main
}
'''


def test_generated_c_both_ways():
    print("\n[the generated C, with the define and without]")
    scripts = open(os.path.join(ROOT, "projects", "vm-musicroutine", "src",
                                "scripts.mos"), encoding="utf-8").read()
    huge = '''
module "probe" {
    import "native.huge"
    var v: u16
    function tick() { v = huge.routine_next() }
    export tick
}
'''
    main = '''
module "main" {
    import "probe"
    function main() { probe.tick() }
    export main
}
'''
    for on in (True, False):
        c = MosaikCompiler()
        out = c.compile_program([("p.mos", huge), ("m.mos", main)],
                                platform="gameboy",
                                defines={"VM_OP_MUSIC_ROUTINE": on} if on
                                else None)
        assert not out.startswith("Compilation error"), out
        want = "on" if on else "off"
        check("%s: the thunk is emitted iff the blob attaches a routine" % want,
              ("void hUGETrackerRoutine(" in out) == on)
        check("%s: the driver's routines table likewise" % want,
              ("const hUGERoutine_t gbs_huge_routines[16]" in out) == on)
        check("%s: the queue reader likewise" % want,
              ("uint16_t gbs_huge_routine_next(void) {" in out) == on)
        if on:
            check("on: the thunk returns unless tick == 0 (once per ROW)",
                  "if (tick) return;" in out)
            check("on: the ring drops the OLDEST on overflow",
                  "gbs_huge_rtt = (gbs_huge_rtt + 1) & (GBS_HUGE_RTQ - 1);"
                  in out)
            check("on: the drain reads it under a CRITICAL section",
                  re.search(r"CRITICAL \{\s*\n\s*gbs_huge_rtt", out)
                  is not None)


def test_glue_wiring_is_by_use():
    print("\n[the glue registration, and the scan that decides it]")
    root = os.path.join(ROOT, "projects", "vm-musicroutine")
    check("the sample's scripts read as a user",
          glue._uses_music_routine(root))
    check("a project with no such event does not",
          not glue._uses_music_routine(os.path.join(ROOT, "projects",
                                                    "vm-quest")))
    on = glue.emit_glue_mos(True, False, audio={"enabled": True, "gb": "huge"},
                            music_routine=True)
    off = glue.emit_glue_mos(True, False, audio={"enabled": True, "gb": "huge"},
                             music_routine=False)
    check("a user's glue registers the reader",
          "core.set_music_routines(music_huge.routine_next)" in on)
    check("a non-user's glue is unchanged",
          "set_music_routines" not in off)


if __name__ == "__main__":
    print("MUSIC_ROUTINE (W7h) checks")
    print("=" * 50)
    test_isa()
    test_refvm_semantics()
    test_refvm_unattached_aborts_the_drain()
    test_refvm_lock_and_scene()
    test_core_source_contracts()
    test_generated_c_both_ways()
    test_glue_wiring_is_by_use()
    print("=" * 50)
    if failed:
        print("%d FAILED, %d passed" % (failed, passed))
        sys.exit(1)
    print("All music-routine checks passed (%d)" % passed)
