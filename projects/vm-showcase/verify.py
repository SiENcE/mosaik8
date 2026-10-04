#!/usr/bin/env python3
"""Behavioural verify for vm-showcase (PyBoy, headless).

  1. boots to the title MENU (menu scene: no player sprite on screen)
  2. A on option 0 -> the FIELD: the player spawns and WALKS; the NPC wanders
  3. walking east through the door -> the MEADOW (platform scene)
  4. the meadow player can DOUBLE JUMP (air_jumps=1: two A presses climb
     higher than one)
  5. a fresh boot picking SHMUP SKYWAY -> SCY auto-scrolls upward by itself

    python projects/vm-showcase/verify.py
"""

import os
import sys

PROJ = os.path.dirname(os.path.abspath(__file__))
ROM = os.path.join(PROJ, "build", "gameboy", "vm-showcase.gb")
PLAYER_OAM = 0xFE00 + 8 * 4          # vm.player's PLAYER_SLOT = OAM entry 8

fails = []


def check(ok, msg):
    print(("  [PASS] " if ok else "  [FAIL] ") + msg)
    if not ok:
        fails.append(msg)


def boot():
    from pyboy import PyBoy
    pb = PyBoy(ROM, window="null")
    for _ in range(120):
        pb.tick()
    return pb


def press(pb, btn, frames=3, settle=6):
    pb.button_press(btn)
    for _ in range(frames):
        pb.tick()
    pb.button_release(btn)
    for _ in range(settle):
        pb.tick()


def player_xy(pb):
    return pb.memory[PLAYER_OAM + 1], pb.memory[PLAYER_OAM]


def main():
    # -- 1) title menu: player-less --------------------------------------
    pb = boot()
    px, py = player_xy(pb)
    check(py == 0 or py >= 144 + 16 or px == 0,
          "title: the player sprite is parked off-screen (menu scene)")
    # the title's On Init runs the GENERIC, player-decoupled scroll_bg(2,0): the
    # backdrop slides right (SCX advances) with NO player in the scene.
    scx0 = pb.memory[0xFF43]
    for _ in range(60):
        pb.tick()
    scx1 = pb.memory[0xFF43]
    check(scx1 != scx0,
          "title: the backdrop scrolls with no player (scroll_bg, SCX %d->%d)"
          % (scx0, scx1))

    # -- 2) A -> option 0 -> the field ------------------------------------
    press(pb, "a", settle=30)
    px, py = player_xy(pb)
    check(80 <= px <= 96 and 80 <= py <= 96,
          "field: player spawned at its authored start (OAM %d,%d)" % (px, py))
    x0, _ = player_xy(pb)
    press(pb, "left", frames=20, settle=2)
    x1, _ = player_xy(pb)
    check(x1 < x0, "field: the player walks (topdown handler)")
    # the NPC (actor slot 0) wanders by itself -- its OAM moves with no input
    n0 = (pb.memory[0xFE00], pb.memory[0xFE01])
    for _ in range(90):
        pb.tick()
    n1 = (pb.memory[0xFE00], pb.memory[0xFE01])
    check(n0 != n1, "field: the NPC wanders on its own (On Update slot)")

    # -- 3) east through the door -> the meadow (platform) ---------------
    pb.button_press("right")
    for _ in range(400):
        pb.tick()
        px, py = player_xy(pb)
        if px < 60 and 100 <= py:            # re-entered at meadow (12, 96->floor)
            break
    pb.button_release("right")
    for _ in range(40):                       # settle: fall to the floor
        pb.tick()
    px, py = player_xy(pb)
    check(px < 60, "meadow: the east door changed the scene (OAM x=%d)" % px)
    check(py >= 120, "meadow: gravity dropped the player to the floor (y=%d)" % py)
    # the field NPC (actor slot 0) must NOT linger in the meadow (actor.reset on
    # a scene change clears the pool; the sprite is parked off-screen y>=144+16).
    ny = pb.memory[0xFE00]
    check(ny == 0 or ny >= 160,
          "meadow: the field NPC did NOT follow across the scene (OAM y=%d)" % ny)

    # -- 4) jump feel: variable height (hold) + double jump --------------
    # jump_peak(hold_frames, double): jump, holding A for `hold_frames`, optionally
    # air-jumping mid-arc; returns the peak (minimum OAM y). Lower = higher.
    def jump_peak(hold_frames, double):
        top = 255
        pb.button_press("a")
        for f in range(50):
            pb.tick()
            if f == hold_frames:
                pb.button_release("a")
            if double and f == hold_frames + 2:   # re-press mid-air -> the 2nd jump
                pb.button_press("a")
            if double and f == hold_frames + 4:
                pb.button_release("a")
            top = min(top, pb.memory[PLAYER_OAM])
        pb.button_release("a")
        for _ in range(60):                        # land + settle
            pb.tick()
        return top

    # VARIABLE JUMP HEIGHT (jump_hold=8): holding A jumps markedly higher than a tap.
    tap = jump_peak(1, False)
    held = jump_peak(10, False)
    check(held < tap - 6,
          "meadow: HOLD jumps higher than a TAP (variable height %d vs %d)"
          % (held, tap))
    # DOUBLE JUMP: a second (air) press beats a single tap of the same length.
    single = jump_peak(1, False)
    dbl = jump_peak(1, True)
    check(dbl < single - 4,
          "meadow: DOUBLE JUMP climbs higher (peak %d vs %d)" % (dbl, single))
    # MOMENTUM (run_accel=2): holding RIGHT RAMPS the run up from rest instead of
    # snapping to top speed -- later per-frame steps exceed the earliest ones.
    for _ in range(40):
        pb.tick()                                  # settle on the floor at rest
    xs = []
    pb.button_press("right")
    for _ in range(18):
        pb.tick()
        xs.append(pb.memory[PLAYER_OAM + 1])
    pb.button_release("right")
    early = xs[4] - xs[0]
    late = xs[-1] - xs[-5]
    check(late > early,
          "meadow: the run RAMPS UP (momentum accel, %dpx -> %dpx)" % (early, late))
    # WALL JUMP (wall_slide/wall_jump): press LEFT into the left wall, ground-jump,
    # then A mid-slide kicks the player back to the RIGHT (away from the wall).
    pb.button_press("left")
    for _ in range(90):
        pb.tick()                                  # accelerate into the left wall
    wx = pb.memory[PLAYER_OAM + 1]                 # seated against the wall
    pb.button_press("a"); pb.tick(); pb.button_release("a")   # ground jump
    for _ in range(5):
        pb.tick()                                  # rise + start sliding down the wall
    pb.button_press("a"); pb.tick(); pb.button_release("a")   # WALL JUMP
    kicked = wx
    for _ in range(12):
        pb.tick()
        kicked = max(kicked, pb.memory[PLAYER_OAM + 1])   # furthest RIGHT of the kick
    pb.button_release("left")
    check(kicked > wx + 2,
          "meadow: WALL JUMP kicks away from the wall (%d -> %d)" % (wx, kicked))
    pb.stop()

    # -- 5) fresh boot -> SHMUP SKYWAY: SCY auto-scrolls ------------------
    pb = boot()
    press(pb, "down", settle=8)
    press(pb, "down", settle=8)
    press(pb, "a", settle=40)
    scy0 = pb.memory[0xFF42]
    for _ in range(120):
        pb.tick()
    scy1 = pb.memory[0xFF42]
    # scroll_pace = 2 -> 0.5 px/frame: 120 frames of no input must scroll ~60 px
    check(scy0 > scy1 and (scy0 - scy1) >= 40,
          "skyway: vertical AUTO-SCROLL runs by itself (SCY %d -> %d)"
          % (scy0, scy1))
    px, py = player_xy(pb)
    check(0 < py < 160, "skyway: the player rides the window (OAM y=%d)" % py)
    pb.stop()

    print()
    if fails:
        print("FAILURES:")
        for m in fails:
            print("  -", m)
        sys.exit(1)
    print("vm-showcase: ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
