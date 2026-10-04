#!/usr/bin/env python3
"""vm-saveslots -- the reference engine's three save slots, measured on a real ROM (W7c).

    python projects/vm-saveslots/verify.py [--platform gameboy]

The room is exactly one GB screen, so the camera never moves and OAM carries
the player's world position with nothing but GBDK's (8, 16) bias in it. The
game TELEPORTS the player to `8 + mark * 8` whenever it wants to show `mark`,
so **the readout is a tile column and the check is one byte of OAM** - no text,
no font, no VM clock (the vm-camprops idiom).

One button per verb (see `assets/gen.py`):

    A  bump      SELECT reset      UP/RIGHT/DOWN save slot 1/2/3
    B  load 2    LEFT  clear 2     START peek slot 3's mark -> probe

What the four checks below are really about:

  * THREE SLOTS ARE THREE SLOTS. Save 1 at mark 1, 2 at mark 2, 3 at mark 3,
    then load slot 2 - a single-slot engine gives back whichever was written
    LAST (3), so the value 2 is only reachable if the writes went to different
    places.
  * CLEAR is a slot, not the cart. Clearing slot 2 must leave slots 1 and 3
    alone, and a load of the cleared slot must be a NO-OP rather than garbage.
  * PEEK does not load. It reads slot 3's `mark` into `probe` while the live
    `mark` is something else - and the live one must not move.
  * AN EMPTY SLOT PEEKS 0, which is the reference engine's own lowering (its `_ifConst`
    tail writes 0 into the destination when the slot holds no save).

The load path is why the readout is drawn from the scene's On Init as well as
from the buttons: a LOAD raises LOAD_COMPLETE, which re-enters the room and
teleports the player to the SAVED position, so the position after a load is
the saved one - which is exactly what makes it readable.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

#: GBDK's OAM bias, and vm.player's PLAYER_SLOT.
OAM_BIAS_X, OAM_BIAS_Y = 8, 16
PLAYER_SLOT = 8

#: The two readout rows `assets/gen.py` authored.
SHOW_Y, PROBE_Y = 64, 96

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAILED] %s%s" % (label, ("  -- " + detail) if detail else ""))


def player(pb):
    b = 0xFE00 + PLAYER_SLOT * 4
    return (pb.memory[b + 1] - OAM_BIAS_X, pb.memory[b] - OAM_BIAS_Y)


def value(pb):
    """The readout as a NUMBER: the player stands at 8 + v * 8."""
    x, _y = player(pb)
    return (x - 8) // 8


def tap(pb, button, settle=24):
    pb.button_press(button)
    pb.tick(4, False)
    pb.button_release(button)
    for _ in range(settle):
        pb.tick(1, False)


def bump_to(pb, n):
    """SELECT back to 0, then A n times - so a check never depends on what the
    previous one left behind."""
    tap(pb, "select")
    for _ in range(n):
        tap(pb, "a")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="gameboy")
    args = ap.parse_args()
    rom = os.path.join(HERE, "build", args.platform,
                       "vm-saveslots.gb" if args.platform == "gameboy"
                       else "vm-saveslots.gbc")
    if not os.path.isfile(rom):
        print("SKIP: %s not built" % os.path.basename(rom))
        return 0
    try:
        from pyboy import PyBoy
    except ImportError:
        print("SKIP: PyBoy is not installed")
        return 0

    # A FRESH cart every run: the .sav beside the ROM is this sample's whole
    # subject, and a leftover one would let a check pass on last run's data.
    sav = os.path.splitext(rom)[0] + ".sav"
    if os.path.isfile(sav):
        os.remove(sav)

    pb = PyBoy(rom, window="null", sound_emulated=False)
    try:
        # 150, not 90: font_preload pushes the first RENDERED frame out to ~98.
        for _ in range(150):
            pb.tick(1, False)
        check("the readout starts at 0", value(pb) == 0,
              "player at %s" % (player(pb),))

        # ---- an empty slot peeks 0, and does not load ---------------------
        bump_to(pb, 4)
        check("A bumps the readout", value(pb) == 4, "read %d" % value(pb))
        tap(pb, "start")                        # peek slot 3, which is empty
        px, py = player(pb)
        check("peeking an EMPTY slot writes 0", (px, py) == (8, PROBE_Y),
              "probe readout at (%d, %d)" % (px, py))

        # ---- three slots are three slots ----------------------------------
        bump_to(pb, 1)
        tap(pb, "up")                           # slot 1 <- 1
        bump_to(pb, 2)
        tap(pb, "right")                        # slot 2 <- 2
        bump_to(pb, 3)
        tap(pb, "down")                         # slot 3 <- 3
        bump_to(pb, 9)                          # and the live heap somewhere else
        tap(pb, "b")                            # load slot 2
        check("loading slot 2 gives back slot 2, not the last write",
              value(pb) == 2, "read %d (a one-slot engine reads 3)" % value(pb))

        # ---- peek reads a slot without loading it -------------------------
        bump_to(pb, 7)
        tap(pb, "start")                        # peek slot 3's mark -> probe
        px, py = player(pb)
        check("peeking slot 3 reads the 3 that was saved there",
              (px, py) == (8 + 3 * 8, PROBE_Y),
              "probe readout at (%d, %d)" % (px, py))
        # ...and the LIVE heap is untouched: re-drawing `mark` must still say 7
        tap(pb, "a")
        check("...and the live variable was not loaded over", value(pb) == 8,
              "read %d, wanted 8 (7 + the bump)" % value(pb))

        # ---- clear is one slot -------------------------------------------
        tap(pb, "left")                         # clear slot 2
        bump_to(pb, 5)
        tap(pb, "b")                            # load slot 2 -> nothing there
        check("loading a CLEARED slot is a no-op", value(pb) == 5,
              "read %d - something was restored" % value(pb))
        tap(pb, "start")                        # slot 3 is still there
        px, py = player(pb)
        check("...and it left the other slots alone",
              (px, py) == (8 + 3 * 8, PROBE_Y),
              "probe readout at (%d, %d)" % (px, py))
    finally:
        pb.stop(save=False)

    print("=" * 50)
    if failed:
        print("%d check(s) FAILED, %d passed" % (failed, passed))
        return 1
    print("All vm-saveslots checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
