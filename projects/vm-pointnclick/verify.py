#!/usr/bin/env python3
"""vm-pointnclick -- the POINT-AND-CLICK scene type, measured on a real ROM (W7j).

    python projects/vm-pointnclick/verify.py [--platform gameboy]

The room is exactly one GB screen (20x18 tiles), so the camera never moves and
OAM carries the cursor's WORLD position with nothing but GBDK's (8, 16) bias in
it. Every check below therefore reads two bytes of OAM - and the hover check
reads a third, the drawn TILE.

What each check is actually about, and where the reference says so
(the reference VM's `src/states/pointnclick.c`):

  * NO COLLISION. The cursor walks straight through tile column 6, which is
    SOLID in the collision layer. A topdown handler stops dead on it.
  * THE DIAGONAL IS NORMALISED. `upoint_translate_angle` moves 90/128 of
    `move_speed` per axis at 45 degrees; ours moves an exact 3/4 (quarter-pixel
    accumulator), so the two walks are compared as a RATIO rather than against
    an absolute the VM clock would have to agree with.
  * THE CLAMP IS THE MAP. The cursor's box stays inside the room on all four
    sides - there is no wall to stop it, only the room.
  * A TRIGGER DOES NOT FIRE ON CONTACT. The reference asks
    `trigger_at_intersection`, a query, and never
    `trigger_activate_at_intersection`.
  * THE HOVER POSE. ANIM_CURSOR / ANIM_CURSOR_HOVER, which our clip tables
    reach as facing 0 and facing 3 - and the pose is picked by a scripted ACTOR
    under the cursor as well as by a scripted trigger.
  * A INTERACTS WITH THE OVERLAP. The trigger's enter script and the actor's On
    Interact each teleport the cursor somewhere no walk could reach, so the
    landing position says WHICH one ran.

The three parking buttons (START home, B onto the hotspot, SELECT onto the
shopkeeper) are why the hover and click checks never depend on how far a walk
got. Only the two movement checks move.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

#: GBDK's OAM bias: a sprite at screen (0, 0) is written to OAM as (8, 16).
OAM_BIAS_X, OAM_BIAS_Y = 8, 16
#: vm.player's PLAYER_SLOT - the OAM object the cursor draws through.
PLAYER_SLOT = 8

#: The positions assets/gen.py authored. Keep the two files in step.
HOME = (16, 24)
HOT_PARK = (112, 120)
NPC = (40, 120)
HOT_LANDING = (8, 8)
NPC_LANDING = (144, 8)
#: The solid column, in world pixels - what the cursor must walk THROUGH.
WALL_X0, WALL_X1 = 48, 55
#: The room, and the cursor's 8x8 box: the clamp lands at (room - box).
ROOM_W, ROOM_H = 160, 144
BOX = 8

#: How long a measured walk holds the pad. 2 px per VM frame from x = 16 stays
#: well short of the right-hand clamp, which would read exactly like a slow walk.
WALK = 40
#: ...and how long the clamp checks hold it: far more than the room is wide.
LONG = 200

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAILED] %s%s" % (label, ("  -- " + detail) if detail else ""))


def cursor(pb):
    """The cursor's WORLD position (the camera cannot move in this room)."""
    base = 0xFE00 + PLAYER_SLOT * 4
    return (pb.memory[base + 1] - OAM_BIAS_X, pb.memory[base] - OAM_BIAS_Y)


def cursor_tile(pb):
    """The tile the cursor is drawing - ANIM_CURSOR or ANIM_CURSOR_HOVER."""
    return pb.memory[0xFE00 + PLAYER_SLOT * 4 + 2]


def hold(pb, buttons, frames=WALK):
    for b in buttons:
        pb.button_press(b)
    for _ in range(frames):
        pb.tick(1, False)
    for b in buttons:
        pb.button_release(b)
    pb.tick(4, False)
    return cursor(pb)


def tap(pb, button, settle=20):
    """Press `button` for a few frames, then let whatever it started finish."""
    pb.button_press(button)
    pb.tick(4, False)
    pb.button_release(button)
    for _ in range(settle):
        pb.tick(1, False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="gameboy")
    args = ap.parse_args()
    rom = os.path.join(HERE, "build", args.platform,
                       "vm-pointnclick.gb" if args.platform == "gameboy"
                       else "vm-pointnclick.gbc")
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
        # 150, not 90: `font_preload` pushes the first RENDERED frame out to
        # about 98, so a 90-frame boot window samples an empty OAM and reads
        # the cursor at the hardware origin.
        for _ in range(150):                    # boot, paint, first VM frames
            pb.tick(1, False)

        x0, y0 = cursor(pb)
        check("the cursor starts where the scene places it",
              (x0, y0) == HOME, "at (%d, %d), wanted %s" % (x0, y0, HOME))

        # ---- the walk: through the wall, and a normalised diagonal ---------
        x, y = hold(pb, ["right"])
        across = x - HOME[0]
        check("the cursor walks THROUGH the solid column (no wall collision)",
              x > WALL_X1 + BOX,
              "stopped at x %d; the wall is %d..%d" % (x, WALL_X0, WALL_X1))
        check("...on the row it started on", y == HOME[1],
              "y %d, wanted %d" % (y, HOME[1]))

        tap(pb, "start")
        x, y = hold(pb, ["right", "down"])
        diag = x - HOME[0]
        check("a diagonal covers about three quarters of a cardinal step",
              across > 0 and 0.6 <= diag / across <= 0.9,
              "%d px diagonal against %d px cardinal (ratio %.2f)"
              % (diag, across, (diag / across) if across else 0.0))
        check("...the same on both axes",
              abs((y - HOME[1]) - diag) <= 2,
              "%d px down against %d px across" % (y - HOME[1], diag))

        # ---- the clamp is the ROOM, on all four sides ----------------------
        tap(pb, "start")
        x, _y = hold(pb, ["right"], LONG)
        check("the cursor clamps its box to the right edge of the room",
              x == ROOM_W - BOX, "x %d, wanted %d" % (x, ROOM_W - BOX))
        _x, y = hold(pb, ["down"], LONG)
        check("...and to the bottom", y == ROOM_H - BOX,
              "y %d, wanted %d" % (y, ROOM_H - BOX))
        x, y = hold(pb, ["left", "up"], LONG)
        check("...and to the top-left corner", (x, y) == (0, 0),
              "at (%d, %d), wanted (0, 0)" % (x, y))

        # ---- a trigger does NOT fire on contact ---------------------------
        tap(pb, "b")                            # park ON the hotspot rect
        x, y = cursor(pb)
        check("B parks the cursor on the hotspot", (x, y) == HOT_PARK,
              "at (%d, %d), wanted %s" % (x, y, HOT_PARK))
        for _ in range(40):
            pb.tick(1, False)
        x, y = cursor(pb)
        check("standing on a trigger does NOT run it (no auto-fire)",
              (x, y) == HOT_PARK,
              "the cursor moved to (%d, %d) - the enter script fired" % (x, y))

        # ---- the hover pose ------------------------------------------------
        hover_tile = cursor_tile(pb)
        tap(pb, "start")
        idle_tile = cursor_tile(pb)
        check("the cursor draws a DIFFERENT cell over a scripted trigger",
              hover_tile != idle_tile,
              "hover tile %d, idle tile %d" % (hover_tile, idle_tile))

        # ---- ...and the click ----------------------------------------------
        tap(pb, "b")
        tap(pb, "a")
        x, y = cursor(pb)
        check("A over a trigger runs its ENTER script", (x, y) == HOT_LANDING,
              "landed at (%d, %d), wanted %s" % (x, y, HOT_LANDING))

        # ---- the actor half: same rule, by OVERLAP -------------------------
        tap(pb, "select")                       # park ON the shopkeeper
        x, y = cursor(pb)
        check("SELECT parks the cursor on the shopkeeper", (x, y) == NPC,
              "at (%d, %d), wanted %s" % (x, y, NPC))
        check("...and an actor with a script hovers the cursor too",
              cursor_tile(pb) == hover_tile,
              "tile %d, wanted the hover tile %d"
              % (cursor_tile(pb), hover_tile))
        x, y = cursor(pb)
        check("standing on an actor does NOT run its On Interact",
              (x, y) == NPC,
              "the cursor moved to (%d, %d)" % (x, y))
        tap(pb, "a")
        x, y = cursor(pb)
        check("A over an actor runs its On Interact", (x, y) == NPC_LANDING,
              "landed at (%d, %d), wanted %s" % (x, y, NPC_LANDING))
    finally:
        pb.stop(save=False)

    print("=" * 50)
    if failed:
        print("%d check(s) FAILED, %d passed" % (failed, passed))
        return 1
    print("All vm-pointnclick checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
