#!/usr/bin/env python3
"""A_MOVE_OPTS (0x1F) -- the reference engine's per-move `moveType` + `useCollisions`.

Both are carried on every reference-engine move and neither was converted, which is
two separate visible defects in the sample town:

  * the HIDER's escape route is four legs of authored AXIS ORDER around the
    buildings, so moving both axes at once cut the corner straight through
    them (its moves ask for no collision at all - the route IS the collision);
  * the PET OWNER's On Update random walk asks for `walls` + `actors`, so
    without them it strolled through the scenery and through the townsfolk.

The semantics are the reference engine's own (`vm_actor.c`):
  * WALLS clip the destination - `check_collision_horizontal/_vertical` shorten
    the target to just before the first solid tile, in the axis order;
  * ACTORS are tested while stepping and a hit ENDS the move where it stands
    (its `THIS->flags = 0` + idle animation), so the waiting script continues
    rather than pushing at the blocker forever.

Design points this pins, both of which cost a regression to find:
  * the latch is ONE-SHOT, so a following plain move is unaffected and a program
    that emits none is byte-identical;
  * `step_to` stays STATELESS - two threads can have a blocking move in flight
    on the SAME actor (the cutscene spike does exactly that), so the target and
    the options are parameters, never per-actor "current move" state.
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

import mosaik_vm
from mosaik_vm import isa, refvm

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- " + detail) if detail else ""))


def _vm(events, actors=None, solid=None, speed=2):
    p = mosaik_vm.Compiler().compile([{"name": "main", "events": events}])
    vm = refvm.RefVM(p.code, entry=p.entry)
    vm.player_box = (0, 0)                  # no player in the way unless asked
    for i, (x, y) in enumerate(actors or []):
        a = vm.actors[i]
        a.active, a.x, a.y, a.speed = 1, x, y, speed
        a.box = (16, 16, 0, 0)
    if solid is not None:
        vm.solid_at = solid
    return vm, p


def main():
    code, ops = isa.OPS["A_MOVE_OPTS"]
    check("A_MOVE_OPTS is 0x1F", code == 0x1F, "0x%02X" % code)
    check("it takes (mode, coll)", ops == ["u8", "u8"], repr(ops))

    # --- byte-identical off --------------------------------------------------
    plain = mosaik_vm.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_move_to", "actor": 0, "x": 40, "y": 40},
        {"event": "stop"}]}])
    check("a plain move emits NO latch",
          "VM_OP_A_MOVE_OPTS" not in isa.decode_blob(plain.code)[0])

    # --- AXIS ORDER: the Hider's problem ------------------------------------
    for mode, first_axis in (("horizontal", "x"), ("vertical", "y")):
        vm, _p = _vm([{"event": "actor_move_to", "actor": 0, "x": 100, "y": 100,
                       "mode": mode}, {"event": "stop"}],
                     actors=[(20, 20)])
        vm.frame()
        a = vm.actors[0]
        moved_x, moved_y = a.x != 20, a.y != 20
        check("moveType %s closes %s first" % (mode, first_axis),
              (moved_x and not moved_y) if first_axis == "x"
              else (moved_y and not moved_x),
              "after one frame: (%d,%d)" % (a.x, a.y))
    vm, _p = _vm([{"event": "actor_move_to", "actor": 0, "x": 100, "y": 100},
                  {"event": "stop"}], actors=[(20, 20)])
    vm.frame()
    check("the default moves both axes at once",
          vm.actors[0].x != 20 and vm.actors[0].y != 20)

    # --- WALLS clip the destination -----------------------------------------
    # a wall column at x >= 64; the actor starts at 16 and is sent to 120
    wall = lambda x, y: x >= 64
    vm, _p = _vm([{"event": "actor_move_to", "actor": 0, "x": 120, "y": 16,
                   "collide_with": 1}, {"event": "stop"},],
                 actors=[(16, 16)], solid=wall)
    for _ in range(200):
        vm.frame()
        if not vm.ctx_active(0) if hasattr(vm, "ctx_active") else False:
            break
    a = vm.actors[0]
    check("a move into a wall STOPS at it", 16 < a.x <= 56,
          "ended at x=%d (the wall starts at 64, the box is 16 wide)" % a.x)
    check("...and the script is released, not stuck",
          vm.heap is not None and a.x != 120)

    # --- ACTORS end the move ------------------------------------------------
    vm, _p = _vm([{"event": "actor_move_to", "actor": 0, "x": 120, "y": 16,
                   "collide_with": 2},
                  {"event": "set_var", "var": "after", "value": 1},
                  {"event": "stop"}],
                 actors=[(16, 16), (72, 16)])
    for _ in range(200):
        vm.frame()
        if vm.heap[0]:
            break
    a = vm.actors[0]
    check("a move into another actor ENDS there", 16 < a.x <= 60,
          "ended at x=%d (the blocker's box starts at 72)" % a.x)
    check("...and the waiting script CONTINUES (the reference engine ends the move)",
          vm.heap[0] == 1)

    # --- and it does not block on a NON-solid actor -------------------------
    vm, _p = _vm([{"event": "actor_move_to", "actor": 0, "x": 120, "y": 16,
                   "collide_with": 2}, {"event": "stop"}],
                 actors=[(16, 16), (72, 16)])
    vm.actors[1].solid = 0
    for _ in range(200):
        vm.frame()
    check("an actor whose collision is OFF is walked through",
          vm.actors[0].x == 120, "x=%d" % vm.actors[0].x)

    # --- the PLAYER counts (the reference engine walks the same list) ------------------
    vm, _p = _vm([{"event": "actor_move_to", "actor": 0, "x": 120, "y": 16,
                   "collide_with": 2}, {"event": "stop"}],
                 actors=[(16, 16)])
    vm.player_box = (16, 16)
    vm.player_x, vm.player_y = 72, 16
    for _ in range(200):
        vm.frame()
    check("the PLAYER blocks a moving actor too", vm.actors[0].x <= 60,
          "x=%d" % vm.actors[0].x)

    # --- the latch is ONE-SHOT ----------------------------------------------
    vm, _p = _vm([{"event": "actor_move_to", "actor": 0, "x": 120, "y": 16,
                   "collide_with": 2},
                  {"event": "actor_move_to", "actor": 0, "x": 140, "y": 16},
                  {"event": "stop"}],
                 actors=[(16, 16), (72, 16)])
    for _ in range(400):
        vm.frame()
    check("the SECOND, plain move is not affected by the first's options",
          vm.actors[0].x == 140,
          "x=%d (it must walk through the blocker this time)" % vm.actors[0].x)

    # --- the latch is CAPTURED, not re-read every frame ---------------------
    # Only two moving actors in one room show this: the blocking op used to
    # re-read the global latch each frame, so a SECOND thread starting its own
    # move consumed it mid-flight and the first one silently lost its axis
    # order. Found on a ROM trace of the sample town, where the Hider's
    # vertical-first leg turned diagonal as soon as the Pet Owner wandered.
    vm, _p = _vm([{"event": "actor_move_to", "actor": 0, "x": 100, "y": 100,
                   "mode": "vertical"},
                  {"event": "stop"}], actors=[(20, 20), (60, 60)])
    vm.frame()
    vm.frame()
    vm.pend_mopt = 0            # what another thread's plain move would do
    for _ in range(6):
        vm.frame()
    a = vm.actors[0]
    check("a concurrent move cannot steal the axis order mid-flight",
          a.x == 20 and a.y > 20,
          "after 8 frames: (%d,%d) - x must still be held" % (a.x, a.y))

    # --- step_to stays STATELESS (the cutscene-spike regression) ------------
    with open(os.path.join(ROOT, "lib", "vm", "actor.mos"), encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"function step_to\(i: u8, tx: u16, ty: u16\) -> bool \{"
                  r"(.*?)\n    \}", src, re.S)
    check("vm.actor.step_to exists", m is not None)
    if m:
        body = m.group(1)
        check("step_to writes no per-actor move state",
              "a_tx[i] =" not in body and "a_ty[i] =" not in body
              and "a_moving[i] =" not in body)
        check("step_to passes the target + options into step_once",
              "step_once(i, tx, ty, opt)" in body)
    check("step_once takes them as parameters",
          "local function step_once(i: u8, tx: u16, ty: u16, opt: u8)" in src)
    check("the stepping body is in the PACK, not the interpreter",
          "function set_move_opts(mode: u8, coll: u8)" in src)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
