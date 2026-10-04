#!/usr/bin/env python3
"""Trigger FORCE RE-FIRE - the reference engine's PLATFORM door press.

The reference engine's trigger scan takes a third `force` argument and only its PLATFORM
state passes anything but FALSE: `INPUT_PLATFORM_FORCE_TRIGGER`, engine-field
default `INPUT_UP_PRESSED` (states/platform.c; topdown/shmup pass FALSE). With
force TRUE, `trigger_activate_at_intersection` skips the "don't reactivate if
the hit trigger has not CHANGED" early return, so standing INSIDE a trigger
and pressing the button re-runs its ENTER script. That is the only way a
platformer door works: the script is `if held_up() { change_scene }`, the
player walks into the doorway first and presses up second, so one latch and
no force spends the trigger on the walk in.

What is pinned here:

  * the DEFAULT is byte-identical. `vm.trigger` forks on VM_TRIG_NO_FORCE;
    absent (unresolvable) and stated TRUE produce the same C, and neither
    carries the edge state or the button read.
  * the force arm re-fires ONLY the enter script and ONLY when the hit did
    not change (a LEAVE is never forced - the reference's leave arm still
    needs `hit != last`).
  * set_force arms MASKED (tf_prev = 1): a button held across a scene change
    is not a press in the new room.
  * ONE world fact decides the arm AND the call: rooms.mos emits
    `trigger.set_force(` off `[scenes] trigger_force`, and
    `mosaik8_build._vm_dispatch_defines` states VM_TRIG_NO_FORCE off that
    call's presence.
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


_CORE_STUB = ('module "vm.core" {\n'
              '    function spawn(entry: u16) -> u8 { return 0 }\n'
              '    export spawn\n}')


def _compile_trigger(defines, call_force):
    with open(os.path.join(ROOT, "lib", "vm", "trigger.mos"), encoding="utf-8") as f:
        trig = f.read()
    force = "trigger.set_force(4)" if call_force else ""
    main = ('module "main" {\n    import "vm.trigger"\n'
            '    function main() { trigger.add(0, 0, 8, 8, 1) %s '
            'trigger.update(0, 0, 8, 8) }\n}' % force)
    return MosaikCompiler().compile_program(
        [("core.mos", _CORE_STUB), ("trigger.mos", trig), ("main.mos", main)],
        platform="gameboy", defines=defines)


def test_default_is_byte_identical():
    absent = _compile_trigger(None, call_force=False)
    stated = _compile_trigger({"VM_TRIG_NO_FORCE": True}, call_force=False)
    check("an ABSENT flag and a stated-TRUE one compile identically",
          absent == stated)
    check("...and neither carries the edge state",
          "tf_btn" not in absent and "tf_prev" not in absent
          and "force_edge" not in absent)


def test_force_arm():
    c = _compile_trigger({"VM_TRIG_NO_FORCE": False}, call_force=True)
    check("the force arm declares the edge state",
          "vm_trigger_tf_btn" in c and "vm_trigger_tf_prev" in c)
    sf = c[c.index("void vm_trigger_set_force(uint8_t b) {"):]
    sf = sf[:sf.index("\n}")]
    check("set_force stores button + 1 (0 = off, 255 wraps to off)",
          re.search(r"tf_btn\s*=\s*\(?.*b.*\+\s*1", sf) is not None, sf)
    check("...and arms MASKED (a held button is not a press in the new room)",
          re.search(r"tf_prev\s*=\s*1", sf) is not None, sf)
    body = c[c.index("void vm_trigger_update(uint16_t px, uint16_t py, "
                     "uint8_t pw, uint8_t ph) {"):]
    body = body[:body.index("\n}")]
    check("update rolls the edge every frame (force_edge called first)",
          "force_edge" in body
          and body.index("force_edge") < body.index("hit_at"), body)
    check("the forced spawn is the ENTER entry of the STANDING trigger",
          body.count("vm_trigger_tentry[hit]") >= 2, body)
    check("...and no LEAVE is ever forced (enter-only arm has no tleave)",
          "tleave" not in body)


def test_rooms_call():
    base = {"types": ["platform", "topdown"], "has_triggers": True}
    off = mosaik_vm.emit_rooms_mos(dict(base))
    check("a world with no [scenes] trigger_force emits NO set_force call",
          "set_force" not in off)
    on = mosaik_vm.emit_rooms_mos(dict(base, tforce=[4, 255]))
    check("a mixed world emits the per-room TFORCE table",
          "const TFORCE: array[u8, 2] = [ 4, 255 ]" in on
          and "trigger.set_force(TFORCE[rm])" in on)
    allf = mosaik_vm.emit_rooms_mos(dict(base, types=["platform"], tforce=[4]))
    check("a uniform world passes the one literal, no table",
          "trigger.set_force(4)" in allf and "TFORCE" not in allf)


def test_build_states_the_flag_off_the_call():
    scripts = "const CODE: array[u8, 1] = [0]\n"
    rooms_off = "function load_room(rm: u8) { }"
    rooms_on = rooms_off + "\n        trigger.set_force(TFORCE[rm])"
    d_off = _vm_dispatch_defines([("scripts.mos", scripts),
                                  ("rooms.mos", rooms_off)])
    d_on = _vm_dispatch_defines([("scripts.mos", scripts),
                                 ("rooms.mos", rooms_on)])
    check("no set_force call -> the plain arm",
          d_off.get("VM_TRIG_NO_FORCE") is True,
          repr(d_off.get("VM_TRIG_NO_FORCE")))
    check("a set_force call -> the force arm",
          d_on.get("VM_TRIG_NO_FORCE") is False,
          repr(d_on.get("VM_TRIG_NO_FORCE")))


def main():
    print("Trigger force re-fire (the platform door press)")
    print("=" * 50)
    print("[the default arm stays byte-identical]")
    test_default_is_byte_identical()
    print("[the force arm]")
    test_force_arm()
    print("[one world fact: the call and the flag]")
    test_rooms_call()
    test_build_states_the_flag_off_the_call()
    print("=" * 50)
    if failed:
        print("%d check(s) FAILED" % failed)
        return 1
    print("All trigger force checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
