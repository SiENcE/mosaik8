#!/usr/bin/env python3
"""SHOW / HIDE A POOL ACTOR (`A_VISIBLE` 0x3D) - a DRAW flag, not a retire.

The reference engine's `ACTOR_FLAG_HIDDEN` is tested in `actors_render` and NOWHERE else
(`core/actor.c` lines 202 and 228), so a hidden actor there keeps updating,
keeps colliding and is still found by the interact probe. Ours used to lower
Hide to `actor_deactivate` (retire), which matches on everything a CUTSCENE
prop needs and is wrong for the other thing the reference engine hides actors for: an
INVISIBLE INTERACTION HOTSPOT, an actor placed on a spot of floor and hidden in
its On Init so that its On Interact is a script attached to the ground.

The RPG check conversion's innkeeper is exactly one, and it carries that project's only
`EVENT_SAVE_DATA` - retired, it could not be talked to and the save was
unreachable in normal play.

Two invariants this pins, because both were free to get wrong:

  * hiding is ORTHOGONAL to active. `A_REACTIVATE` must not un-hide, exactly as
    the reference engine's `vm_actor_activate` does not clear the flag (it clears
    ACTOR_FLAG_DISABLED, a different bit tested by the same render guard).
  * a hidden actor is still SOLID and still ACTIVE. That is the whole point of
    the change, so it is asserted rather than assumed.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm
from mosaik_vm import isa
from mosaik_vm.refvm import RefVM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _vm(events):
    prog = mosaik_vm.Compiler().compile([{"name": "main", "events": events}])
    return RefVM(prog.code, entry=prog.entry), prog


def test_refvm():
    print("\n[RefVM: the flag]")
    vm, _ = _vm([
        {"event": "actor_activate", "actor": 3, "tile": 0, "x": 40, "y": 40},
        {"event": "actor_visible", "actor": 3, "on": 0},
        {"event": "wait", "frames": 2},
        {"event": "actor_visible", "actor": 3, "on": 1},
        {"event": "stop"}])
    check(vm.actors[3].visible == 1, "a slot starts DRAWN")
    vm.frame()
    check(vm.actors[3].visible == 0, "hide takes")
    for _ in range(4):
        vm.frame()
    check(vm.actors[3].visible == 1, "show puts it back")


def test_it_is_not_a_retire():
    print("\n[hidden is LIVE: the difference from actor_deactivate]")
    vm, _ = _vm([
        {"event": "actor_activate", "actor": 2, "tile": 0, "x": 64, "y": 48},
        {"event": "actor_visible", "actor": 2, "on": 0},
        {"event": "stop"}])
    vm.frame()
    a = vm.actors[2]
    check(a.visible == 0, "hidden")
    check(a.active == 1,
          "...and still ACTIVE - which is what keeps it in every scan that is "
          "not the render pass (interact, collision, update)")
    check((a.x, a.y) == (64, 48),
          "it keeps its position (%d,%d)" % (a.x, a.y))
    check(a.solid == 1, "it still blocks the player")

    # The control: deactivate is still a retire, and this change must not have
    # turned one into the other.
    vm2, _ = _vm([
        {"event": "actor_activate", "actor": 2, "tile": 0, "x": 64, "y": 48},
        {"event": "actor_deactivate", "actor": 2},
        {"event": "stop"}])
    vm2.frame()
    check(vm2.actors[2].active == 0,
          "CONTROL: actor_deactivate still retires (0x28 is untouched)")


def test_reactivate_does_not_unhide():
    print("\n[orthogonal to active, as in the reference engine's own vm_actor_activate]")
    vm, _ = _vm([
        {"event": "actor_activate", "actor": 1, "tile": 0, "x": 8, "y": 8},
        {"event": "actor_visible", "actor": 1, "on": 0},
        {"event": "actor_deactivate", "actor": 1},
        {"event": "actor_reactivate", "actor": 1},
        {"event": "stop"}])
    vm.frame()
    check(vm.actors[1].active == 1, "reactivate brings it back to life")
    check(vm.actors[1].visible == 0,
          "...but does NOT un-hide it - the reference engine's vm_actor_activate clears "
          "ACTOR_FLAG_DISABLED and leaves ACTOR_FLAG_HIDDEN alone")


def test_self_operand():
    print("\n[the `self` operand encodes like every other actor op]")
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "actor_visible", "actor": "self", "on": 0},
            {"event": "stop"}]}])
    i = prog.code.index(0x3D)
    check(prog.code[i + 1] == isa.SELF_ACTOR,
          "the SELF sentinel is the actor operand, so a per-instance slot "
          "script hides ITS OWN actor without naming a pool index")


def test_engine_lockstep():
    print("\n[lockstep across the surfaces]")
    check(isa.OPS["A_VISIBLE"] == (0x3D, ["u8", "u8"]),
          "the ISA row is 0x3D, (actor, on)")
    check(isa.OPS["PLAYER_VISIBLE"] == (0x34, ["u8"]),
          "PLAYER_VISIBLE is UNCHANGED at 0x34 - the two are separate ops on "
          "purpose (dispatch pruning makes an unused arm free, where widening "
          "a shared op taxes every project that uses it)")
    core = _read("lib", "vm", "core.mos")
    check("const OP_A_VISIBLE = 0x3D" in core,
          "core.mos's opcode const matches the ISA byte")
    check("if VM_OP_A_VISIBLE {" in core,
          "the arm carries its dispatch-pruning guard, spelled as the ISA "
          "names it")
    check("actor.set_visible(" in core,
          "the arm calls vm.actor directly (no seam: vm.core imports vm.actor)")
    actor = _read("lib", "vm", "actor.mos")
    check("var a_vis: array[u8, VM_ACTOR_POOL]" in actor,
          "vm.actor owns the flag, one byte per slot")
    check("function set_visible" in actor and "export set_visible" in actor,
          "...and exports the setter")
    # Per SITE, not a count: vm.actor has TWO render() definitions (the
    # SCAN_ALL arm and the amortised one) and a bare tally would stay green
    # with one of them unguarded, which is a hidden actor that draws on every
    # project that sets `[build] actor_scan`.
    arms = actor.split("function render(locked: u8)")[1:]
    check(len(arms) == 2, "vm.actor still has the two render arms")
    for n, arm in enumerate(arms):
        body = arm[:arm.index("\n        }")]
        check("if a_vis[i] == 0 {" in body,
              "render arm %d stands down on a hidden actor" % n)
    check("if a_vis[i] == 0 {" in actor.split("function repos")[1][:400],
          "repos() stands down too - a facing flip must not draw a hidden "
          "actor (it is the one path that forces a move through a park)")
    check("a_vis[i] = 1" in actor.split("function reset")[1][:900],
          "reset() re-arms every slot, so a hidden actor cannot leak into the "
          "next room")
    react = actor.split("function reactivate")[1][:600]
    check("a_vis" not in react,
          "reactivate() does NOT touch the flag (orthogonal to active)")
    spec = _read("docs", "vm8-spec.md")
    row = [l for l in spec.splitlines() if "| A_VISIBLE |" in l]
    check(bool(row) and "ACTOR_FLAG_HIDDEN" in row[0],
          "the spec documents it against the reference engine's own flag")


def test_byte_identical_off():
    print("\n[byte-identical off]")
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [{"event": "wait", "frames": 1},
                                    {"event": "stop"}]}])
    check(0x3D not in prog.code,
          "a program that never hides an actor emits no 0x3D, so the arm "
          "prunes away and the resident image is unchanged")


if __name__ == "__main__":
    print("=== actor_visible_test: A_VISIBLE is a DRAW flag ===")
    test_refvm()
    test_it_is_not_a_retire()
    test_reactivate_does_not_unhide()
    test_self_operand()
    test_engine_lockstep()
    test_byte_identical_off()
    print()
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        sys.exit(1)
    print("actor_visible_test: all passed")
