#!/usr/bin/env python3
"""A metasprite's fan SHAPE is latched per OAM base, and outlives its owner.

`gbs_move_sprite(base, x, y)` does not take a size: it reads `gbs_meta_w[base]`
/ `gbs_meta_h[base]`, the shape the last `set_meta` at that base latched, and
fans `w * (h >> 1)` objects. So the shape is HARDWARE STATE keyed by OAM base,
and every room load reshuffles who owns which base.

The bug this pins (found 2026-08-15 in the reference-engine conversion, reported as a
stray sprite on entering the town room from the long walk-in room):

  * in the platform room the player is the reference engine's 16x32 sprite - under
    `obj_8x16` a FOUR object fan at base 0, so actor bases start at 4;
  * in the topdown room it is 16x16 - a TWO object fan, so actor 0 gets base 2
    and owns OAM slots 2 and 3;
  * `canim.set_player(kind)` swapped the kind but left the hardware latch
    describing the PREVIOUS one, and `vm.core` runs vm.player's handler BEFORE
    the animator - so the first move of the new room fanned FOUR objects onto
    the arrival spot, and the animator then shrank the latch to two without
    ever moving objects 2 and 3 back;
  * the room-load OAM sweep could not help: it runs before any of this;
  * actor 0's `park()` then moved only its own base (slot 2), because a slot
    whose latch was never set by its new owner is a single sprite - leaving
    slot 3 on screen for the life of the room, wearing whatever tile happened
    to be in it. That last part is why it read as a RANDOM sprite: measured at
    tile 6 on one build and tile 26 on another, same slot, same pixel.

Measured on a real GB ROM (PyBoy, walking the transition): before, OAM slot 3
sat at y=104 x=24 with the player at y=88 x=16/24; after, slot 3 is parked at
y=216 x=208 and only the player's two objects are on screen.

The fix is to assert the new kind's fan where the shape CHANGES, which is
`set_player`. Through `draw_frame`, so a per-object DESCRIPTOR kind asserts its
list rather than a dense rectangle.

Not pinned here because it is a property of the whole frame rather than of one
call: the placeholder frame `set_player` uploads is replaced by `draw_player`
in the SAME frame (`p_uok = 0`), before present, so nothing wrong is displayed.
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

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _src(mod):
    return open(os.path.join(ROOT, "lib", "vm", "%s.mos" % mod),
                encoding="utf-8").read()


def _body(src, name):
    m = re.search(r"function %s\([^)]*\)[^{]*\{" % re.escape(name), src)
    if not m:
        return ""
    i, depth = m.end(), 1
    while i < len(src) and depth:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return src[m.end():i]


def test_set_player_asserts_the_fan():
    print("\n[set_player asserts the new kind's fan shape]")
    body = _body(_src("canim"), "set_player")
    check(body != "", "canim.set_player is found")
    check("draw_frame(p_base, kind" in body,
          "it asserts the fan through draw_frame (so a DESCRIPTOR kind "
          "asserts its list, not a dense rectangle)")
    check("if kind != 255 {" in body,
          "255 (no player clip) asserts nothing - there is no kind to size")
    check("p_uok = 0" in body,
          "the real frame still re-uploads this same frame, so the "
          "placeholder is never displayed")


def test_the_ordering_that_makes_it_necessary():
    print("\n[the ordering the fix depends on]")
    core = open(os.path.join(ROOT, "lib", "vm", "core.mos"),
                encoding="utf-8").read()
    run = _body(core, "run")
    # vm.player's handler moves the player; g_anim() is vm.canim.tick_all.
    # If these ever swap, the fix is unnecessary but harmless - if the MOVE
    # stops preceding the DRAW, re-read this test before "simplifying" it.
    ph = run.find("g_player")
    an = run.find("g_anim()")
    check(ph != -1 and an != -1, "run() calls the player handler and g_anim")
    check(ph < an,
          "the player MOVES before the animator DRAWS - which is why a stale "
          "latch reaches the hardware at all")


def test_move_has_no_size_argument():
    print("\n[why the shape is latched rather than passed]")
    # sprite.move takes (id, x, y) - no size. That is the whole reason a fan
    # shape has to be state, and therefore has to be re-asserted on a change.
    act = _src("actor")
    check("sprite.move(base, 200, 200)" in act,
          "park() moves by BASE only (the fan follows the latch)")
    m = re.search(r"local function park\(i: u8\) \{", act)
    check(m is not None, "vm.actor.park exists")


def test_actors_have_the_same_protection():
    print("\n[the ACTOR half of the rule, which already existed]")
    # An actor's base is repacked every room load, so an actor's fan can shrink
    # across a change exactly like the player's. `set_base` has always reset the
    # record for that reason - its own comment records the failure it fixed (a
    # room re-using a big animated actor's base for a 1-tile icon moved all 21 children
    # on screen). MEASURED on the real ROM after the player fix: walking
    # the town room until all nine placed actors had been visible produced ZERO
    # objects belonging to a parked actor. So this is not an open hazard, it is
    # the precedent the player fix copies - keep both.
    body = _body(_src("actor"), "set_base")
    check(body != "", "vm.actor.set_base is found")
    check("sprite.set_meta(b, a_tile[i], 1, 1)" in body,
          "set_base RESETS the base slot's fan record to 1x1, so the first "
          "place() of the new room cannot fan through the previous room's "
          "shape")
    check("if b != NO_OAM {" in body,
          "...and not for a slot the room allocator gave no OAM")


def main():
    print("=" * 50)
    print("A metasprite fan shape is latched per OAM base")
    print("=" * 50)
    test_set_player_asserts_the_fan()
    test_the_ordering_that_makes_it_necessary()
    test_move_has_no_size_argument()
    test_actors_have_the_same_protection()
    print("\n" + "=" * 50)
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All player fan shape checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
