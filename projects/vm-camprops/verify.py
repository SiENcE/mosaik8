#!/usr/bin/env python3
"""vm-camprops -- the follow camera's OPTIONS, measured on a real ROM (W7b).

    python projects/vm-camprops/verify.py [--platform gameboy]

THE WHOLE CHECK IS ONE EQUATION, which is why it needs no camera register and
no frame counting. Walking right at a steady speed the follow camera settles
exactly where the dead zone leaves it:

    camera = focus - half - offset - deadzone

`focus` is the player's CENTRE (its top-left + 4 on an 8 px box), so the
player's own SCREEN position holds at `half - 4 + offset + deadzone` and every
feature reads back as a different value of that one number. Half a GB screen is
80 across and 72 down, and OAM x/y carry GBDK's (8, 16) bias.

Five phases, a button each (the first is the boot state):

    (none)   dead zone 0, offset 0, follow both  -> screen x 76
    A        dead zone 40 on both axes           -> screen x 116, y 108
    B        dead zone 0, offset 24 across       -> screen x 100
    SELECT   follow the horizontal ONLY          -> y climbs off the screen
    START    follow both, block scrolling right  -> x climbs off the screen

EVERY PHASE TELEPORTS THE PLAYER HOME FIRST, and that is the check's own
safety rather than tidiness: the room is 256 px wide against a 160 px screen,
so the horizontal camera runs 0..96, and a walk that reaches the right wall
reads EXACTLY like a camera a dead zone held back. The first version of this
file did reach it, and read a clamped camera as a 60 px dead zone.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

#: Half the GB screen, per axis - what `vm.player`'s HALFW / HALFH are.
HALF_X, HALF_Y = 80, 72
#: The camera follows the player's FOCUS, which is its centre (top-left + half
#: of the 8 px box), so every settled reading is 4 px short of half a screen.
FOCUS = 4
#: GBDK's OAM bias: a sprite at screen (0, 0) is written to OAM as (8, 16).
OAM_BIAS_X, OAM_BIAS_Y = 8, 16
#: The player walks 2 px per VM frame, so a settled reading is within one step.
STEP = 2
#: How long a phase walks. 2 px per VM frame from x = 24 leaves the camera well
#: inside the room (see the note above) with the dead zone already crossed.
WALK = 60

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAILED] %s%s" % (label, ("  -- " + detail) if detail else ""))


def player_screen(pb):
    """The player sprite's screen position, from OAM object 8 (PLAYER_SLOT)."""
    base = 0xFE00 + 8 * 4
    return (pb.memory[base + 1] - OAM_BIAS_X, pb.memory[base] - OAM_BIAS_Y)


def walk(pb, buttons, frames=WALK):
    """Hold `buttons` for `frames` LCD frames; return the settled reading."""
    for b in buttons:
        pb.button_press(b)
    for _ in range(frames):
        pb.tick(1, False)
    for b in buttons:
        pb.button_release(b)
    pb.tick(4, False)
    return player_screen(pb)


def phase(pb, button):
    """Tap `button` to run its phase script, then let the teleport settle."""
    pb.button_press(button)
    pb.tick(4, False)
    pb.button_release(button)
    for _ in range(20):
        pb.tick(1, False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="gameboy")
    args = ap.parse_args()
    rom = os.path.join(HERE, "build", args.platform,
                       "vm-camprops.gb" if args.platform == "gameboy"
                       else "vm-camprops.gbc")
    if not os.path.isfile(rom):
        print("SKIP: %s not built" % os.path.basename(rom))
        return 0
    try:
        from pyboy import PyBoy
    except ImportError:
        print("SKIP: PyBoy is not installed")
        return 0

    pb = PyBoy(rom, window="null", sound_emulated=False)
    try:
        for _ in range(90):                     # boot, paint, first VM frames
            pb.tick(1, False)

        # ---- the boot state: a plain follow, nothing tuned -----------------
        want = HALF_X - FOCUS
        x, _y = walk(pb, ["right"])
        check("with nothing tuned the player holds at half a screen",
              abs(x - want) <= STEP, "screen x %d, wanted %d" % (x, want))

        # ---- A: a 40 px dead zone on both axes ----------------------------
        phase(pb, "a")
        x, y = walk(pb, ["right", "down"])
        check("a 40 px dead zone lets the player run 40 px ahead across",
              abs(x - (HALF_X - FOCUS + 40)) <= STEP,
              "screen x %d, wanted %d" % (x, HALF_X - FOCUS + 40))
        check("... and 40 px ahead down",
              abs(y - (HALF_Y - FOCUS + 40)) <= STEP,
              "screen y %d, wanted %d" % (y, HALF_Y - FOCUS + 40))

        # ---- B: no dead zone, a 24 px follow offset -----------------------
        phase(pb, "b")
        x, _y = walk(pb, ["right"])
        check("a 24 px follow offset draws the player 24 px right of centre",
              abs(x - (HALF_X - FOCUS + 24)) <= STEP,
              "screen x %d, wanted %d" % (x, HALF_X - FOCUS + 24))

        # ---- SELECT: follow the horizontal only ---------------------------
        # The vertical camera holds where the teleport left it, so walking down
        # walks the player DOWN THE SCREEN instead of scrolling the room.
        phase(pb, "select")
        x0, y0 = player_screen(pb)
        x, y = walk(pb, ["down"])
        check("a horizontal-only follow leaves the vertical camera alone",
              y > y0 + 40, "screen y %d -> %d (it should climb)" % (y0, y))
        check("... and holds the horizontal where it is while standing still",
              abs(x - x0) <= STEP, "screen x %d -> %d" % (x0, x))

        # ---- START: preventScroll right -----------------------------------
        phase(pb, "start")
        x0, _y0 = player_screen(pb)
        x, _y = walk(pb, ["right"])
        check("blocking scroll right stops the camera dead",
              x > x0 + 40, "screen x %d -> %d (it should climb)" % (x0, x))
    finally:
        pb.stop(save=False)

    print("=" * 50)
    if failed:
        print("%d check(s) FAILED, %d passed" % (failed, passed))
        return 1
    print("All vm-camprops checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
