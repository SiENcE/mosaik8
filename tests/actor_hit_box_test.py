#!/usr/bin/env python3
"""AN ACTOR HAS ONE COLLISION BOX, AND EVERYTHING COLLIDES AGAINST IT.

Reported from play on the two reference-engine conversions: a 16x16 falling hazard in
the shooter conversion could only be shot through the tile at its top-left
corner, and a 16x16 rock in the reference-engine sample conversion (the town room, and the ones
in a later room) pushed into a wall as if it were 8x8.

Both were the same shape of bug. `vm.actor.set_box` already registered GB
Studio's authored sprite `bounds` per actor - the studio writes it as
`studio.toml [hitbox.<kind>]` and `rooms.mos` uploads it at room load - but
only ONE consumer read it (`blocked`, the player-blocking test). The other two
places the runtime collides against an actor each carried their own hardcoded
8x8:

* `vm.projectile.overlaps` compared TOP-LEFT ANCHORS less than 8 px apart. That
  is an 8x8-vs-8x8 test whatever the actor is drawn at, so the shooter conversion's
  falling hazard (16x16 drawn, a 14x14 box at +2) was hittable over a quarter of its body and
  its turret over a ninth of the aim arc that is the whole mechanic.
* `vm.actor.push` probed the two leading-edge corners of an 8x8 CELL. Pushing a
  16x16 rock right therefore tested x+7 - a point inside the rock itself - so
  it slid a full tile into the wall; pushing it down tested only its upper half,
  so its lower half walked through terrain.

The reference engine keeps a single `bounds` per actor and collides everything against it
(`core/actor.c`), which is what this restores: `set_box` is the one box, read
back through `box_w_of`/`box_h_of`/`box_x_of`/`box_y_of`.

Two consequences worth stating, because they are what make the fix safe:

* **An unregistered box (w == 0) keeps the 8x8 default.** A hand-written game
  that never calls `set_box`, and a kind whose authored box opts it out of
  BLOCKING, both behave exactly as before.
* **Registering a box does not make an actor solid.** `blocked()` is only wired
  to the player (`player.set_actor_block`) under `[scenes] solid_actors`, so
  `generate_rooms` can now register boxes for a PROJECTILE game as well without
  changing how it plays - which is what a shooter whose enemies are
  walk-through needs to be hittable over its whole body.
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
from mosaik_vm.refvm import RefVM
from mosaik_vm.rooms import emit_rooms_mos

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FAILED = []


def _exported(src, name):
    """Is `name` in ANY of a module's `export` statements?

    NOT `src.split("export")[-1]`: a module may have several export lines, and
    taking only the last one reads as "not exported" the moment a new one is
    appended below it - which is exactly what W7j and W7c each did to a
    different module, breaking a green assertion for a reason that had nothing
    to do with what it pins.
    """
    return any(name in ln for ln in src.splitlines()
               if ln.strip().startswith("export "))


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


BASE = {"types": ["topdown"], "uniform": True, "has_collision": True,
        "has_objects": True, "has_player_kind": True, "has_entity": True}
CLIPS = {"meta_w": 2, "meta_h": 2, "per_kind_size": True,
         "flip": False, "player": True}


def _shoot(actor_xy, shot_xy, box=(8, 8, 0, 0)):
    """Fire one projectile straight up from `shot_xy` at an actor placed at
    `actor_xy` with collision box `box`; return True if the shot struck it.

    The shot is given zero velocity and a life long enough to be tested, so
    what is measured is the OVERLAP, not the flight."""
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "actor_activate", "actor": 0, "tile": 1,
             "x": actor_xy[0], "y": actor_xy[1]},
            {"event": "projectile", "x": shot_xy[0], "y": shot_xy[1],
             "tile": 0, "life": 30, "vx": 0, "vy": 0},
            {"event": "stop"}]}])
    vm = RefVM(prog.code, entry=prog.entry)
    vm.proj_hit = lambda slot: None          # arm the native hit test
    vm.actors[0].box = box
    for _ in range(4):
        vm.frame()
    return bool(vm.proj_hits)


def test_projectile_hits_the_whole_box():
    print("\n[a shot hits an actor over its registered box]")
    # A 16x16 actor at (64, 64). The shot's own box is 8x8, so its top-left may
    # sit anywhere in 57..79 on each axis and still overlap.
    big = (16, 16, 0, 0)
    check(_shoot((64, 64), (64, 64), big), "top-left corner: hit (as before)")
    check(_shoot((64, 64), (72, 72), big),
          "BOTTOM-RIGHT quarter: hit (the falling-hazard bug - this used to miss)")
    check(_shoot((64, 64), (79, 79), big), "the far edge of the box: hit")
    check(not _shoot((64, 64), (80, 64), big),
          "one pixel past the right edge: miss")
    check(not _shoot((64, 64), (64, 80), big),
          "one pixel past the bottom edge: miss")
    check(not _shoot((64, 64), (56, 64), big),
          "one pixel short of the left edge: miss")

    print("\n[the box OFFSET is honoured]")
    # The shooter conversion's falling hazard: 16x16 drawn, a 14x14 box inset
    # 2 px from the left.
    hazard = (14, 14, 2, 0)
    # the box starts at x = 64 + 2 = 66, so an 8 px shot must reach past it:
    # its top-left at 58 ends exactly AT 66 (half-open = miss), at 59 it laps.
    check(not _shoot((64, 64), (58, 64), hazard),
          "the 2 px inset really is outside the box")
    check(_shoot((64, 64), (59, 64), hazard), "one pixel further in: hit")
    check(not _shoot((64, 64), (80, 64), hazard),
          "past the box's right edge (66 + 14): miss")

    print("\n[an unregistered box keeps the 8x8 default]")
    # (8, 8, 0, 0) is what RefVM and the runtime both fall back to, so a game
    # that never calls set_box is unchanged.
    check(_shoot((64, 64), (64, 64)), "coincident: hit")
    check(_shoot((64, 64), (71, 71)), "7 px away on both axes: hit")
    check(not _shoot((64, 64), (72, 64)), "8 px away: miss (the old boundary)")


def test_projectile_source_reads_the_box():
    """The console runtime and the reference VM must agree, and the console
    half is mosaik source - so it is asserted at the source level."""
    # The pool walk itself moved into `vm.actor.hit_scan` (2026-08-31, the
    # framebudget census: asking from vm.projectile cost two cross-bank
    # accessor calls per SLOT and four more per candidate). The invariant is
    # unchanged and is asserted where the code now lives - what matters is
    # that a shot is tested against the actor's REGISTERED box, not which
    # module holds the loop.
    print("\n[the shot-vs-actor test reads vm.actor's box]")
    src = _read("lib", "vm", "projectile.mos")
    actor = _read("lib", "vm", "actor.mos")
    scan = actor.split("function hit_scan(")[1].split("\n    function ")[0]
    check("a_bw[i]" in scan and "a_bh[i]" in scan,
          "the actor's box SIZE is read, not assumed 8x8")
    check("a_box_x[i]" in scan and "a_box_y[i]" in scan,
          "the box OFFSET is added to the actor position")
    check("aw = 8" in scan and "ah = 8" in scan,
          "an unregistered box (w == 0) falls back to the 8x8 default")
    check("a_group[i] & mask" in scan,
          "the shot's mask still selects the target's GROUP")
    check("actor.hit_scan(" in src,
          "vm.projectile asks once per shot, not once per slot "
          "(the Lynx/PCE fold keeps the old loop - tests/proj_hit_scan_test.py)")
    check("player.box_w()" in src and "player.box_h()" in src,
          "an enemy shot tests the PLAYER's own box too")
    check("dx < 8 and dy < 8" not in src,
          "the anchor-distance test is gone")

    print("\n[lib/vm/actor.mos exports the box]")
    actor = _read("lib", "vm", "actor.mos")
    for fn in ("box_w_of", "box_h_of", "box_x_of", "box_y_of"):
        check(("function %s(" % fn) in actor and _exported(actor, fn)
              or ("function %s(" % fn) in actor,
              "accessor %s exists" % fn)
    check("export box_w_of, box_h_of, box_x_of, box_y_of" in actor,
          "the four accessors are exported")


def test_push_probes_the_box():
    print("\n[actor_push probes the actor's own box]")
    src = _read("lib", "vm", "actor.mos")
    push = src.split("function push(")[1].split("\n    function ")[0]
    check("a_bw[i]" in push and "a_bh[i]" in push,
          "push reads the registered box size")
    check("bw = 8" in push, "an unregistered box keeps the 8x8 probe")
    check("edge_solid(" in push, "the leading edge is SWEPT, not sampled twice")
    check("bw - 1" in push and "bh - 1" in push,
          "the far edge is the box's, not the cell's +7")
    check("player.terrain_solid(nx + 7" not in src,
          "the old fixed +7 corner probes are gone")
    # the sweep itself: first pixel, every tile across, and always the far one
    edge = src.split("local function edge_solid(")[1].split("\n    function ")[0]
    check("probe(ex, ey, horiz, 0)" in edge, "probes the near corner")
    check("o += 8" in edge, "steps a tile at a time across the edge")
    check("probe(ex, ey, horiz, last)" in edge,
          "always probes the FAR corner (a 14 px box would stop at 8)")


def test_rooms_registers_boxes_for_a_projectile_game():
    print("\n[generate_rooms registers the box for a projectile game]")
    boxes = {0: (16, 16, 0, 0), 1: (14, 14, 2, 0)}

    # (a) a shooter whose actors do NOT block the player still needs boxes.
    info = dict(BASE, clips=CLIPS, kind_boxes=boxes, nkinds=2,
                uses_projectile=True, solid_actors=False)
    src = emit_rooms_mos(info)
    check("const KBW: array[u8, 2] = [ 16, 14 ]" in src,
          "the per-kind box table is emitted")
    check("actor.set_box(slot, KBW[fk], KBH[fk], KBX[fk], KBY[fk])" in src,
          "every placed actor registers its box")
    check("player.set_actor_block(" not in src,
          "...and is NOT made solid (blocking stays behind solid_actors)")

    # (b) solid_actors alone is still enough, as before.
    src = emit_rooms_mos(dict(BASE, clips=CLIPS, kind_boxes=boxes, nkinds=2,
                              solid_actors=True))
    check("actor.set_box(slot, KBW[fk], KBH[fk], KBX[fk], KBY[fk])" in src,
          "solid_actors alone still registers the box")
    check("player.set_actor_block(actor.blocked)" in src,
          "...and wires the player-blocking seam")

    # (c) a world that needs neither is byte-identical: no table, no call.
    src = emit_rooms_mos(dict(BASE, clips=CLIPS, kind_boxes=boxes, nkinds=2))
    check("actor.set_box(" not in src and "const KBW" not in src,
          "a world with neither use emits nothing (byte-identical)")


def main():
    test_projectile_hits_the_whole_box()
    test_projectile_source_reads_the_box()
    test_push_probes_the_box()
    test_rooms_registers_boxes_for_a_projectile_game()
    print()
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("actor_hit_box_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
