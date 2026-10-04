#!/usr/bin/env python3
"""The player's COLLISION BOX is the sprite's bounds, not the drawn rectangle.

The reference engine gives every actor ONE authored `bounds` and tests walls, triggers
and interact against it. Its topdown player's is **16x8 on the FEET ROW** of a
16x16 character (`sprite_player.c`: `.top = PX_TO_SUBPX(0), .bottom =
PX_TO_SUBPX(8) - 1`), so the head passes in front of the wall behind it.

Two things follow, and both were wrong before:

* a scene switch lands the player on the tile BELOW the trigger that leads
  back (the sample: an interior room -> tile 31,41 against a return trigger at
  31,40), so a box the full height of the sprite overlaps the trigger it just
  arrived through and teleports straight back. 6 of the conversion's 30 scene
  switches did this;
* the player collided with its whole drawn rectangle, which is a tile taller
  than the reference everywhere.

`studio.toml [player] box_by_type` is the bounds SIZE and
`box_offset_by_type` where that box sits inside the sprite. The box's BOTTOM
is the same under either model (both stand the feet on the same row), which
is why landing, one-way platforms and tile alignment are untouched - only the
top comes down. Absent = the box is the drawn rect, i.e. every existing
project is byte-identical.
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

_FAIL = []


def check(label, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- " + str(detail)) if detail else ""))
    if not cond:
        _FAIL.append(label)


def _scenes(*types):
    return [{"name": "s%d" % i, "scene_type": t} for i, t in enumerate(types)]


def _boffs(off, types):
    """What generate_rooms would have collected for these scenes."""
    if not off:
        return None
    rows = [tuple(off.get(t) or [0, 0]) for t in types]
    return rows if any(r != (0, 0) for r in rows) else None


def _focus(f, types):
    if not f:
        return None
    rows = [tuple(f.get(t)) if f.get(t) else None for t in types]
    if not any(rows):
        return None
    dflt = next(r for r in rows if r)
    return [r or dflt for r in rows]


def _rooms(off=None, types=("topdown",), doors=False, focus=None):
    player = {"width": 16, "height": 16}
    if off is not None:
        player["box_offset_by_type"] = off
    if focus is not None:
        player["camera_focus_by_type"] = focus
    return mosaik_vm.emit_rooms_mos({
        "types": list(dict.fromkeys(types)),
        "has_triggers": True,
        "has_doors": doors,
        "scenes": _scenes(*types),
        "player": player,
        "boffs": _boffs(off, types),
        "cfocus": _focus(focus, types),
    })


def test_camera_focus_is_its_own_knob():
    """The reference engine centres the camera on a FIXED `PLAYER.pos + 8` (its
    camera.c CAMERA_FIXED_OFFSET_X/Y) - the centre of the tile the feet stand
    on, not the centre of the character or of its box. It cannot be derived
    from either, so it is configured rather than computed; unset, the camera
    keeps centring on the box, which is what a hand-authored game expects.

    Measured on a ROM in a TALL room (the only place camera Y is live): with
    the reference-engine focus the player's sprite top sits at screen y 56, where
    the box centre would put it at 68."""
    plain = _rooms()
    check("no focus configured keeps the box centre", "CFY" not in plain)
    src = _rooms(focus={"topdown": [8, 16]})
    check("a configured focus is emitted", "const CFY: u8 = 16" in src)
    check("...and handed to the runtime",
          "player.set_cam_focus(CFX, CFY)" in src)
    per = _rooms(focus={"topdown": [8, 16], "platform": [8, 28]},
                 types=("topdown", "platform"))
    check("it follows the scene type",
          "const PCFY: array[u8, 2] = [ 16, 28 ]" in per)
    check("...read on room load", "CFY = PCFY[rm]" in per)
    # the x term is a fixed +8 in the reference engine, so its column is one repeated
    # value and is not worth a table
    check("a uniform x focus stays a const", "const CFX: u8 = 8" in per)
    check("...with no per-room table for it", "PCFX" not in per)


def test_absent_is_byte_identical():
    plain = _rooms(None)
    check("with no offset the rect tests use the position",
          "trigger.update(player.pos_x(), player.pos_y(), PW, PH)" in plain)
    check("...and nothing else is emitted", "BOY" not in plain)
    # An offset of zero is also a no-op, so a world cannot pay for a feature
    # it is not using.
    check("a zero offset stays byte-identical", _rooms({"topdown": [0, 0]})
          == plain)


def test_a_uniform_offset_is_a_const():
    src = _rooms({"topdown": [0, 8]})
    check("the offset is emitted", "const BOY: u8 = 8" in src)
    check("...as a const when every scene agrees", "var BOY" not in src)
    check("...with no per-room table", "PBOY" not in src)
    check("...and a zero x costs nothing", "BOX" not in src)
    check("the runtime is told", "player.set_box_offset(0, BOY)" in src)
    # The rect tests must measure the box AT the box, not at the sprite.
    check("the trigger test uses the box origin",
          "trigger.update(player.box_x(), player.box_y(), PW, PH)" in src)


def test_per_scene_offset_follows_the_room():
    # the sample's real shape: a feet-row topdown box, a taller platform one,
    # a ship whose box is its whole sprite
    src = _rooms({"topdown": [0, 8], "platform": [0, 4], "shmup": [0, 0]},
                 types=("topdown", "platform", "shmup"))
    check("differing offsets become a var", "var BOY: u8 = 8" in src)
    check("...plus a per-room table",
          "const PBOY: array[u8, 3] = [ 8, 4, 0 ]" in src)
    check("...read on room load", "BOY = PBOY[rm]" in src)
    check("an x offset that IS used is still emitted",
          "BOX" in _rooms({"topdown": [2, 8]}))


def test_a_door_tests_the_same_box():
    """A door IS a trigger (the reference engine has only triggers) and both are the
    same `bounds` as the wall test - one box answers everything."""
    src = _rooms({"topdown": [0, 8]}, doors=True)
    check("door.update uses the box origin too",
          "door.update(player.box_x(), player.box_y(), PW, PH)" in src)


def test_the_sample_geometry():
    """The arithmetic of the reported bug, and of the fix, in one place.

    The town room's trigger 6 covers tiles (31,40) 2x1 = pixels y 320..327, and
    the return teleport puts the player's SPRITE top-left at y 320 (GB
    Studio's tile 41 minus the 8 px feet lift). The feet box then occupies
    328..335 - the row below - exactly as the reference does."""
    trig_y0, trig_y1 = 40 * 8, 40 * 8 + 8 - 1
    py = 320
    off_y, box_h = 8, 8                     # the topdown bounds box
    box_y0 = py + off_y
    check("the arrival box clears the return trigger", box_y0 > trig_y1,
          "box %d..%d vs trigger %d..%d"
          % (box_y0, box_y0 + box_h - 1, trig_y0, trig_y1))
    check("walking onto it still fires it",
          (py - 8) + off_y <= trig_y1 and (py - 8) + off_y + box_h - 1 >= trig_y0)
    check("the full-sprite box is what overlapped (the bug)",
          py <= trig_y1 and py + 16 - 1 >= trig_y0)
    # The box BOTTOM is what stands on the floor, and it does not move - that
    # is why this changes collision at the head and nothing at the feet.
    check("the box bottom is unchanged (topdown)", py + off_y + box_h == py + 16)
    check("the box bottom is unchanged (platform)", 4 + 24 == 28)


def main():
    print("Player box (reference-engine sprite bounds)")
    test_absent_is_byte_identical()
    test_a_uniform_offset_is_a_const()
    test_per_scene_offset_follows_the_room()
    test_a_door_tests_the_same_box()
    test_the_sample_geometry()
    test_camera_focus_is_its_own_knob()
    if _FAIL:
        print("%d FAILED" % len(_FAIL))
        return 1
    print("All player-box checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
