#!/usr/bin/env python3
"""Verify vm-uiquest -- the VM8 PORT of the composed `ui-quest` UI showcase.

PyBoy (GB): the title MENU (framed, custom font) shows at boot with no player; A
starts the field (player appears); reading the SIGN opens a framed dialogue; and
Start opens the pause OVERLAY menu -- every box framed + closing cleanly.

A "framed box is open" is read off the **WINDOW layer** (0x9C00), because that is
where GB-family UI text goes (`text.to_window`, bottom-anchored) -- it used to be
plotted into the BG map, and this suite still looked there long after the overlay
shipped, so all three box checks failed against a ROM that draws them correctly.
Two things it takes together, and BOTH are needed:

  * **LCDC bit 5, the window ENABLE** -- closing a box switches the window OFF and
    leaves its map alone, so the glyphs of the last box stay in VRAM. Counting
    tiles alone reports a box that is no longer on screen.
  * the count of DISTINCT font tiles in the window map -- a real box is a frame
    plus text (many glyphs); one stale row or a bare fill is not.

    python projects/vm-uiquest/verify.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJ = os.path.dirname(os.path.abspath(__file__))

FAILS = []


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def main():
    rom = os.path.join(PROJ, "build", "gameboy", "vm-uiquest.gb")
    if not os.path.exists(rom):
        subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "gameboy", PROJ], check=True, cwd=ROOT)
    try:
        from pyboy import PyBoy
    except Exception:  # noqa: BLE001
        print("  skip: PyBoy not installed")
        return 0

    def box_open(pb):
        # Is a framed box on screen? See the module docstring: the window ENABLE
        # says "showing", the distinct-glyph count says "a frame + text". Both
        # the MENU and the DIALOGUE are bottom-anchored in WINDOW space, so the
        # band is the window map's own rows, not screen rows.
        if not pb.memory[0xFF40] & 0x20:          # LCDC bit 5: window off
            return False
        return len({pb.memory[0x9C00 + cy * 32 + cx]
                    for cy in range(18) for cx in range(20)
                    if pb.memory[0x9C00 + cy * 32 + cx] >= 100}) >= 4

    def player_vis(pb):
        y = pb.memory[0xFE00 + 8 * 4]
        return 16 <= y < 150

    def hold(pb, b, f):
        pb.button_press(b)
        for _ in range(f):
            pb.tick()
        pb.button_release(b)
        for _ in range(4):
            pb.tick()

    def tapA(pb, n=1):
        # a 6-tick cadence (an A-edge every 7 frames) -- PyBoy is sensitive to the
        # exact button rhythm vs the interact poll; 6 fires reliably.
        for _ in range(n):
            pb.button_press("a"); pb.tick(); pb.button_release("a")
            for _ in range(6):
                pb.tick()

    pb = PyBoy(rom, window="null")
    for _ in range(125):
        pb.tick()
    print("[title]")
    check(box_open(pb) and not player_vis(pb),
          "the title MENU (framed, custom font) shows; no player on the title")

    print("[start -> field]")
    tapA(pb, 2)                        # confirm START (cursor already on START)
    for _ in range(20):
        pb.tick()
    check(player_vis(pb) and not box_open(pb),
          "START enters the field (player visible, the menu box cleared)")

    print("[sign dialogue]")
    hold(pb, "up", 34)                 # walk up onto the sign (40, 24)
    hold(pb, "left", 10)
    talk = False
    for _ in range(10):
        tapA(pb, 1)
        if box_open(pb):
            talk = True
            break
    check(talk, "reading the sign opens a framed dialogue (custom font)")
    tapA(pb, 3)                        # page through + dismiss
    for _ in range(6):
        pb.tick()

    print("[pause overlay]")
    pb.button_press("start"); pb.tick(); pb.button_release("start")
    for _ in range(16):
        pb.tick()
    check(box_open(pb), "Start opens the pause OVERLAY menu")
    pb.stop()

    print("\n" + "=" * 50)
    if FAILS:
        print("vm-uiquest verify FAILED (%d)" % len(FAILS))
        return 1
    print("vm-uiquest verify: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
