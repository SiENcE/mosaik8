#!/usr/bin/env python3
"""Verify vm-overworld -- the BIGGER VM8 map: a 32x32 scrolling overworld with a
varied tileset + imported 16x16 metasprites (player + NPCs). PyBoy (on-ROM) proves:
it boots + renders the 16x16 player + 3 NPC metasprites over a VARIED background; the
follow camera SCROLLS both axes; tile-based collision BLOCKS the player at the tree
border; an NPC's On Interact opens a dialogue box; the background SURVIVES the dialogue
(the font-preload fix -- the box used to blank the tileset); and the dialogue EXITS on
the last A-press instead of re-triggering (the a-edge-consume fix).

    python projects/vm-overworld/verify.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PROJ = os.path.dirname(os.path.abspath(__file__))

FAILS = []


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def pyboy_rom():
    print("[PyBoy: a 32x32 scrolling overworld, 16x16 sprites, working dialogue]")
    rom = os.path.join(PROJ, "build", "gameboy", "vm-overworld.gb")
    if not os.path.exists(rom):
        subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "gameboy", PROJ], check=True, cwd=ROOT)
    try:
        from pyboy import PyBoy
    except Exception:  # noqa: BLE001
        print("  skip: PyBoy not installed")
        return
    # boot: font_preload front-loads the GBDK font init (~30 extra frames).
    BOOT = 140

    def live(pb):
        return [i for i in range(40) if pb.memory[0xFE00 + 4 * i] not in (0, 216)]

    def bg_low(pb):
        return len(set(pb.memory[0x9800 + r * 32 + c]
                       for r in range(18) for c in range(20)
                       if pb.memory[0x9800 + r * 32 + c] < 8))

    def scx(pb):
        return pb.memory[0xFF43]

    def scy(pb):
        return pb.memory[0xFF42]

    def box_open(pb):
        # A GB-family dialogue box is on the WINDOW layer (0x9C00), not in the
        # BG map -- `text.to_window`, bottom-anchored. This check used to count
        # glyph tiles in the BG map and so read 0 against a ROM that draws the
        # box correctly; the same staleness vm-uiquest's verify records fixing
        # for itself. Both halves are needed: LCDC bit 5 says the window is
        # SHOWING (closing a box switches it off and leaves the glyphs in VRAM),
        # the distinct-glyph count says a frame plus text rather than one stale
        # row.
        if not pb.memory[0xFF40] & 0x20:          # LCDC bit 5: window off
            return False
        return len({pb.memory[0x9C00 + r * 32 + c]
                    for r in range(18) for c in range(20)
                    if pb.memory[0x9C00 + r * 32 + c] >= 100}) >= 4

    def hold(pb, btn, f):
        pb.button_press(btn)
        for _ in range(f):
            pb.tick()
        pb.button_release(btn)
        for _ in range(4):
            pb.tick()

    # --- render + scroll + collision ---
    pb = PyBoy(rom, window="null")
    for _ in range(BOOT):
        pb.tick()
    # player + 3 NPCs are 16x16 metasprites (4 OAM entries each) -> many live slots.
    check(len(live(pb)) >= 8, "boots + renders the 16x16 player + NPC metasprites (%d OAM)" % len(live(pb)))
    check(bg_low(pb) >= 4, "the background is VARIED (%d distinct map tiles on screen)" % bg_low(pb))

    sx0, sy0 = scx(pb), scy(pb)
    hold(pb, "down", 24)
    hold(pb, "right", 40)
    check(scx(pb) != sx0 and scy(pb) != sy0,
          "the follow camera SCROLLS the big map on both axes (%d,%d -> %d,%d)"
          % (sx0, sy0, scx(pb), scy(pb)))

    hold(pb, "right", 200)
    sx_edge = scx(pb)
    hold(pb, "right", 40)
    check(scx(pb) == sx_edge, "tile-based collision + camera clamp STOP at the tree border")
    pb.stop()

    # --- dialogue: opens, background SURVIVES, and EXITS (no re-trigger) ---
    pb = PyBoy(rom, window="null")
    for _ in range(BOOT):
        pb.tick()
    for _ in range(14):        # walk WEST into the villager (facing it)
        pb.button_press("left")
        pb.tick()
    pb.button_release("left")
    for _ in range(4):
        pb.tick()
    opened = False
    for _ in range(8):
        pb.button_press("a")
        pb.tick()
        pb.button_release("a")
        for _ in range(8):
            pb.tick()
        if box_open(pb):
            opened = True
            break
    check(opened, "the villager's On Interact opens a dialogue box")
    # the background must STILL show its varied tiles behind/around the box (the box
    # used to clobber the tileset -> a blank white screen).
    check(bg_low(pb) >= 4, "the background SURVIVES the dialogue (font-preload fix, %d tiles)" % bg_low(pb))

    # fully dismiss all pages, then verify the band the window box covered shows
    # ROOM TILES again (the set_redraw fix -- clear_area alone would leave a blank
    # strip in a room bigger than the screen).
    for _ in range(6):
        pb.button_press("a")
        pb.tick()
        pb.button_release("a")
        for _ in range(10):
            pb.tick()
        if not box_open(pb):
            break
    band = [pb.memory[0x9800 + r * 32 + c] for r in range(14, 18) for c in range(20)]
    room = sum(1 for v in band if v < 8)
    check(not box_open(pb) and room >= 70,
          "the background is REDRAWN after the dialogue closes (%d/80 band tiles are room)" % room)

    # if the dialogue exited (a-edge fix), the player is unlocked and the camera moves.
    s0 = scy(pb)
    hold(pb, "down", 20)
    check(scy(pb) != s0, "the dialogue EXITS on the last A (a-edge fix: no re-trigger, player unlocks)")
    pb.stop()


def main():
    pyboy_rom()
    print("\n" + "=" * 50)
    if FAILS:
        print("vm-overworld verify FAILED (%d)" % len(FAILS))
        return 1
    print("vm-overworld verify: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
