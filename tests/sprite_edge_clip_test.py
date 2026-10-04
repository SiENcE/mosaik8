#!/usr/bin/env python3
"""A metasprite scrolling off a screen edge must PARK its outside columns, not
WRAP them onto the opposite edge (`sprite.move_world`).

`sprite.move`'s x is a `uint8_t`, so a world-space renderer whose actor has
scrolled past the left edge hands the fan an origin that has ALREADY wrapped:
screen x -11 arrives as 245, and the fan then walks +8 per column - so the
leading columns draw at the far RIGHT of the screen and the rest wrap round to
the left. Measured on the reference-engine sample conversion (GB ROM, walking right
through the parallax room) as OAM x, a 9-column animated actor came out as

    low = [5, 13, 21, 29, 37, 45, 53]   high = [253]

- seven columns correctly placed and a ghost at the right edge. It is symmetric
(an actor ENTERING from the right wraps its trailing columns onto the left) and
it is not console-specific, though the SMS shows it worst: its screen IS the
whole 256 px plane, so no u8 value is off-screen and the wrap cannot even be
detected inside the fan. Hence a SIGNED entry point.

After: 0 split frames in either direction, over 700 frames each way.

The rules this pins:

* `sprite.move` is UNTOUCHED - `gbs_clip_hi == 0` is the no-clip state and it
  is what plain BSS gives, so a program that never calls move_world is
  byte-identical (the golden snapshots pin that half).
* the sign is resolved ONCE per metasprite into a column range, so the walk -
  which is the frame's biggest single cost - stays 8-bit and pays two compares
  per COLUMN.
* the range is `x + 8c + DEVICE_SPRITE_PX_OFFSET_X` in 0..255: that offset is
  the console's negative headroom and it differs wildly (8 on the GB, 0 on the
  SMS, 48 on the Game Gear, whose viewport is a centre crop of the same plane),
  which is why a constant margin in the CALLER cannot do this job.
* under FLIP_X the gbdk fan reverses the walk, so the bounds are mirrored in
  the setter and the walk's test keeps one shape. The cc65 fan mirrors per
  CHILD instead (`cc = w - 1 - c`), so its loop index already IS the screen
  column and its bounds are not mirrored.
* a clipped column is PARKED, not skipped. A blank one (set_meta_mask) is
  already parked and must stay put; this one was DRAWN last frame.
* `sprite.meta_cols` is the other half: vm.actor's off-window margin was a flat
  16 px, which parks a 72 px actor while seven of its nine columns are still on
  screen. The margin is the sprite's own width now.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FAILED = []

META = '''
module "main" {
    import "graphics.sprite"
    function main() {
        sprite.set_meta(0, 0, 4, 4)
        var sx: i16 = 10
        var sy: i16 = 20
        sprite.move_world(0, sx, sy)
        var w: u8 = sprite.meta_cols(0)
        sprite.move(1, w, 4)
    }
}
'''

PLAIN = META.replace("        sprite.move_world(0, sx, sy)\n", "") \
            .replace("        var w: u8 = sprite.meta_cols(0)\n",
                     "        var w: u8 = 4\n")


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def compile_for(src, platform):
    return MosaikCompiler().compile_program([("t.mos", src)], platform=platform)


def main():
    print("sprite.move_world: park the columns off a screen edge")

    for plat in ("gameboy", "sms", "gamegear", "nes"):
        c = compile_for(META, plat)
        check("gbs_move_sprite_world" in c, "%s: the signed entry exists" % plat)
        check("int16_t x, int16_t y" in c, "%s: ...and it really is signed" % plat)
        check("uint8_t gbs_clip_lo;" in c and "uint8_t gbs_clip_hi;" in c,
              "%s: the column range is per-program state" % plat)
        check("c < clo || c >= chi" in c,
              "%s: the walk tests the hoisted range per column" % plat)
        check("gbs_clip_hi ? gbs_clip_hi : w" in c,
              "%s: hi == 0 is the NO-CLIP state (plain BSS)" % plat)
        check("gbs_meta_cols" in c, "%s: meta_cols is emitted" % plat)
        check("DEVICE_SPRITE_PX_OFFSET_X) - x" in c,
              "%s: the bound is the console's own sprite offset, not a "
              "constant margin" % plat)

    # ...and NOTHING of it in a program that never asks. This is the
    # byte-identical half: plain sprite.move must keep its own behaviour.
    for plat in ("gameboy", "sms", "lynx", "pce"):
        c = compile_for(PLAIN, plat)
        # (match on the STATE, not on a bare `clo` - "clock" contains it, and
        # the cc65 prelude is full of clock())
        check("gbs_move_sprite_world" not in c and "gbs_clip_lo" not in c
              and "= gbs_clip_hi ?" not in c,
              "%s: a program without move_world carries none of it" % plat)

    for plat in ("lynx", "pce"):
        c = compile_for(META, plat)
        check("void gbs_move_sprite_world(uint8_t nb, int16_t x, int16_t y)" in c,
              "%s: the cc65 backend has it too" % plat)
        check("cc < clo || cc >= chi" in c,
              "%s: ...and tests the SCREEN column, which its fan mirrors per "
              "child rather than by reversing the walk" % plat)

    # The gbdk fan reverses the walk under FLIP_X, so the bounds mirror in the
    # setter - or a flipped actor would clip the wrong end.
    gb = compile_for(META, "gameboy")
    check("t = (uint8_t)(w - hi); hi = (uint8_t)(w - lo); lo = t;" in gb,
          "gbdk: FLIP_X mirrors the bounds in the setter, not in the walk")
    sms = compile_for(META, "sms")
    check("w - hi" not in sms,
          "...and a console with no hardware flip never mirrors them")

    # vm.actor is the caller, and the margin half.
    with open(os.path.join(ROOT, "lib", "vm", "actor.mos"), encoding="utf-8") as f:
        actor = f.read()
    check("sprite.move_world(base, sx, sy)" in actor,
          "vm.actor places through the signed entry")
    check("var sx: i16 = wx - cx" in actor and "var sy: i16 = wy - cy" in actor,
          "...with the screen position computed SIGNED")
    check("sprite.meta_cols(base) * 8" in actor,
          "off_window's LEFT margin is the sprite's own width")
    # ...and it is asked LAST. The verdict is one OR over four terms and only
    # this one needs the sprite's width, so the cheap bounds settle first: a
    # horizontally scrolling room parks most of its pool off to the RIGHT,
    # where `wx > lim_x` decides it outright, and asking every one of them how
    # wide it was drawn cost 3,459 cycles a frame in the shooter room. `base` is a
    # parameter for the same reason - re-deriving it here made base_of the
    # most-called function in the frame (9.62 calls against 5.31 looked-at
    # slots).
    ow = actor[actor.index("local function off_window("):]
    ow = ow[:ow.index(chr(10) + "    }")]
    check("local function off_window(i: u8, base: u8, cx: u16, cy: u16)" in actor,
          "...and off_window takes the base its caller already has")
    check(ow.index("wx > lim_x") < ow.index("sprite.meta_cols("),
          "...with the cheap bounds answered BEFORE meta_cols is asked")
    check("wy + 16 < cy" in actor,
          "...and the VERTICAL margin stays 16: nothing wraps on that axis, "
          "and widening it walks the SMS's 0xD0 sprite-list terminator")

    print("=" * 50)
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for w in _FAILED:
            print("  - " + w)
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
