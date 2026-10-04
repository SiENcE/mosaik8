#!/usr/bin/env python3
"""Trigger ON LEAVE - the falling edge of the trigger latch.

The reference engine's `trigger_activate_at_intersection` runs the LEAVE arm of the
trigger the player box just left, and it is a mechanic rather than a flourish:
a trigger that ATTACHES an input script on entry has to detach it on exit, or
the binding outlives the rect. The platformer conversion's sign is exactly that shape - enter
attaches "press up to read", leave removes it - so without the falling edge
`up` opened the sign's text box from anywhere in the room.

Its runtime shares ONE script pointer per trigger and tells the arms apart by
thread ARG 0 (`script_execute(..., 1)` on enter, `..., 2)` on leave). That is
an artefact of its `trigger_t`, not the semantics: a reference-engine scene resource authors `script`
and `leaveScript` as SEPARATE arrays and so does a world.toml, so a second
entry table reproduces it with no argument protocol and no per-trigger flag.

What is pinned here:

  * the DEFAULT is byte-identical. `vm.trigger` forks on VM_TRIG_ENTER_ONLY;
    absent (unresolvable) and stated TRUE produce the same C, and neither
    carries the second table - so a world that binds no On Leave pays neither
    the code nor its BSS.
  * ONE world fact decides the arm AND the call. `mosaik_scenes` emits a
    `trigger_leave` selector only for a world that binds one, `rooms.mos`
    emits the 6-argument `trigger.add` off the same fact, and
    `mosaik8_build._vm_dispatch_defines` states the flag off the selector's
    presence. A generated CALL and its generated DEFINITION cannot disagree.
  * ENTER BEFORE LEAVE, the reference's order when a step crosses straight
    from one rect into another.
  * the leave arm KEEPS a trigger that carries only a leave script (the rect
    has to be tracked for a falling edge to exist) and still drops one that
    carries neither.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm  # noqa: E402
from mosaik import MosaikCompiler  # noqa: E402
from mosaik_scenes import transpile  # noqa: E402
from mosaik_assets import write_png_indexed  # noqa: E402
from mosaik8_build import _vm_dispatch_defines  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def _world(leave=True):
    trig = [{"from": "room", "tx": 1, "ty": 1, "on_enter": "hello"},
            {"from": "room", "tx": 2, "ty": 2, "on_enter": "bye"}]
    if leave:
        trig[1]["on_leave"] = "farewell"
    return {"world": {"module": "scenes", "map_w": 4, "map_h": 4, "vm": True},
            "tileset": {"png": "tiles.png"},
            "kinds": {"player": 0},
            "scene": [{"name": "room", "map": [[0] * 4 for _ in range(4)]}],
            "trigger": trig}


# -- the engine module, compiled both ways -----------------------------------

_CORE_STUB = ('module "vm.core" {\n'
              '    function spawn(entry: u16) -> u8 { return 0 }\n'
              '    export spawn\n}')


def _compile_trigger(defines, six_arg):
    """The real `lib/vm/trigger.mos` against a stub vm.core, with a caller that
    uses the arity the selected arm defines."""
    with open(os.path.join(ROOT, "lib", "vm", "trigger.mos"), encoding="utf-8") as f:
        trig = f.read()
    add = ("trigger.add(0, 0, 8, 8, 1, 2)" if six_arg
           else "trigger.add(0, 0, 8, 8, 1)")
    main = ('module "main" {\n    import "vm.trigger"\n'
            '    function main() { %s trigger.update(0, 0, 8, 8) }\n}' % add)
    return MosaikCompiler().compile_program(
        [("core.mos", _CORE_STUB), ("trigger.mos", trig), ("main.mos", main)],
        platform="gameboy", defines=defines)


def test_default_is_byte_identical():
    absent = _compile_trigger(None, six_arg=False)
    stated = _compile_trigger({"VM_TRIG_ENTER_ONLY": True}, six_arg=False)
    check("an ABSENT flag and a stated-TRUE one compile identically",
          absent == stated,
          "the build always states it for a VM8 game, so this is what keeps a "
          "non-VM8 program and a leave-free world on the same code")
    check("...and neither carries the second entry table",
          "tleave" not in absent and "tleave" not in stated)
    check("...and `add` keeps its 5-argument form",
          re.search(r"vm_trigger_add\(uint16_t x, uint16_t y, uint8_t w, "
                    r"uint8_t h, uint16_t entry\)", absent) is not None)


def test_leave_arm():
    c = _compile_trigger({"VM_TRIG_ENTER_ONLY": False}, six_arg=True)
    check("the leave arm declares the second entry table",
          "vm_trigger_tleave[" in c)
    check("...and `add` takes the leave entry",
          re.search(r"vm_trigger_add\(uint16_t x, uint16_t y, uint8_t w, "
                    r"uint8_t h, uint16_t entry, uint16_t leave\)", c)
          is not None)
    # the DEFINITION, not the forward declaration that precedes it
    body = c[c.index("void vm_trigger_update(uint16_t px, uint16_t py, "
                     "uint8_t pw, uint8_t ph) {"):]
    body = body[:body.index("\n}")]
    check("update spawns the LEAVE entry of the trigger just left",
          "vm_trigger_tleave[vm_trigger_last]" in body, body)
    # The reference runs the newly hit trigger's enter arm and only THEN the
    # old one's leave arm, so the two threads are scheduled in that order.
    check("ENTER is spawned before LEAVE (the reference's order)",
          body.index("vm_trigger_tentry[hit]") < body.index("vm_trigger_tleave["),
          body)
    check("...and the latch advances only after both",
          body.rindex("vm_trigger_last = hit")
          > body.index("vm_trigger_tleave["), body)


def test_leave_only_trigger_is_kept():
    """A rect with ONLY a leave arm still has to be tracked - there is no
    falling edge off a rect the latch never held. One with NEITHER arm is
    dropped, exactly as the enter-only form drops a scriptless trigger."""
    with open(os.path.join(ROOT, "lib", "vm", "trigger.mos"), encoding="utf-8") as f:
        mos = f.read()
    arm = mos[mos.index("function add(x: u16, y: u16, w: u8, h: u8, entry: u16, leave: u16)"):]
    arm = arm[:arm.index("\n        }")]
    check("the leave arm drops only a trigger with NEITHER script",
          re.search(r"if\s+entry\s*==\s*NO_SCRIPT\s+and\s+leave\s*==\s*NO_SCRIPT",
                    arm) is not None, arm[:300])
    check("...and stores the leave entry beside the enter one",
          "tleave[tn] = leave" in arm, arm[:400])


# -- one world fact drives the selector, the call and the flag ---------------

def test_selector_is_emitted_only_when_bound(tmpdir):
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    write_png_indexed(os.path.join(tmpdir, "tiles.png"), 8, 8, [0] * 64, pal)
    without = transpile(_world(leave=False), tmpdir)
    check("a world that binds no On Leave emits NO trigger_leave selector",
          "function trigger_leave(" not in without)
    check("...and never exports the name either",
          "trigger_leave" not in without)
    with_ = transpile(_world(leave=True), tmpdir)
    check("...and one that does emits it",
          "function trigger_leave(" in with_ and "ENTRY_farewell" in with_)
    check("...and exports it beside trigger_enter",
          re.search(r"export[\s\S]*trigger_leave", with_) is not None)
    # The selector indexes by the trigger's flatten position, like every other
    # slot selector - trigger 1 here, not trigger 0.
    body = with_[with_.index("function trigger_leave("):]
    body = body[:body.index("\n    }")]
    check("...indexed by the trigger's own position",
          "if i == 1 {" in body, body)


def test_rooms_call_matches_the_arm():
    off = mosaik_vm.emit_rooms_mos({"types": ["topdown"], "has_triggers": True})
    on = mosaik_vm.emit_rooms_mos({"types": ["topdown"], "has_triggers": True,
                                   "has_trig_leave": True})
    check("without On Leave rooms.mos calls the 5-argument add",
          "trigger.add(ttx * 8, tty * 8, tws, ths, scenes.trigger_enter(i))" in off)
    check("...and never names the selector it did not emit",
          "trigger_leave" not in off)
    check("with On Leave it calls the 6-argument add",
          "scenes.trigger_enter(i), scenes.trigger_leave(i))" in on)


def test_build_states_the_flag_off_the_selector():
    scripts = "const CODE: array[u8, 1] = [0]\n"
    scenes_off = "function trigger_enter(i: u16) -> u16 { return 0xFFFF }"
    scenes_on = (scenes_off
                 + "\nfunction trigger_leave(i: u16) -> u16 { return 0xFFFF }")
    d_off = _vm_dispatch_defines([("scripts.mos", scripts),
                                  ("scenes.mos", scenes_off)])
    d_on = _vm_dispatch_defines([("scripts.mos", scripts),
                                 ("scenes.mos", scenes_on)])
    check("no selector -> the enter-only arm",
          d_off.get("VM_TRIG_ENTER_ONLY") is True,
          repr(d_off.get("VM_TRIG_ENTER_ONLY")))
    check("a selector -> the falling-edge arm",
          d_on.get("VM_TRIG_ENTER_ONLY") is False,
          repr(d_on.get("VM_TRIG_ENTER_ONLY")))


def main():
    import tempfile
    print("Trigger On Leave (the falling edge)")
    print("=" * 50)
    print("[the default arm stays byte-identical]")
    test_default_is_byte_identical()
    print("[the falling-edge arm]")
    test_leave_arm()
    test_leave_only_trigger_is_kept()
    print("[one world fact: the selector, the call and the flag]")
    with tempfile.TemporaryDirectory() as tmp:
        test_selector_is_emitted_only_when_bound(tmp)
    test_rooms_call_matches_the_arm()
    test_build_states_the_flag_off_the_selector()
    print("=" * 50)
    if failed:
        print("%d check(s) FAILED" % failed)
        return 1
    print("All trigger On Leave checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
