#!/usr/bin/env python3
"""PyBoy proof for the wide-tall (2D) spike (Stage 4 of the large-levels-u16 plan).

Proves BOTH-AXES column+row streaming past the 256 px wrap. Two MARKER tiles sit at far
cells (40,40)=tile 2 and (50,50)=tile 3 -- px (320,320) and (400,400), past the wrap on
BOTH axes. Their hardware ring cells are (40%32,40%32)=(8,8) and (50%32,50%32)=(18,18).

  * BEFORE: the window is cols/rows 0..31 (no marker) -> those hw cells are ground.
  * AFTER auto-scrolling DIAGONALLY to camx=camy=320: column AND row streaming have
    written logical (40,40)/(50,50) into hw (8,8)/(18,18), and SCX=SCY=320%256=64.

Run:  python projects/wide-tall-spike/verify.py
"""
import os
import sys

ROM = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "build", "gameboy", "wide-tall-spike.gb")


def _cell(pb, col, row):
    return pb.memory[0x9800 + row * 32 + col]


def main():
    from pyboy import PyBoy
    pb = PyBoy(ROM, window="null")

    # Advance past the DMG BIOS + the 2D boot fill until row 0 is ground everywhere
    # (the fill finished; camx/camy still ~0).
    f = 0
    while f < 400 and not all(_cell(pb, c, 0) == 1 for c in range(32)):
        pb.tick(); f += 1
    before = (_cell(pb, 8, 8), _cell(pb, 18, 18))
    before_scx, before_scy = pb.memory[0xFF43], pb.memory[0xFF42]

    # Auto-scroll diagonally; tick well past the STOP=320 clamp so camx=camy settle.
    for _ in range(500):
        pb.tick()
    after = (_cell(pb, 8, 8), _cell(pb, 18, 18))
    after_scx, after_scy = pb.memory[0xFF43], pb.memory[0xFF42]
    pb.stop()

    print("BEFORE  SCX=%3d SCY=%3d  hw(8,8)=%d hw(18,18)=%d"
          % (before_scx, before_scy, before[0], before[1]))
    print("AFTER   SCX=%3d SCY=%3d  hw(8,8)=%d hw(18,18)=%d"
          % (after_scx, after_scy, after[0], after[1]))

    checks = [
        ("before: no marker at the recycled hw cells", before == (1, 1)),
        ("after: logical (40,40) [px 320,320, past both wraps] -> hw (8,8) = marker 2",
         after[0] == 2),
        ("after: logical (50,50) [px 400,400, past both wraps] -> hw (18,18) = marker 3",
         after[1] == 3),
        ("after: the camera scrolled on BOTH axes (SCX=SCY=64)",
         after_scx == 64 and after_scy == 64),
    ]
    ok = True
    for label, cond in checks:
        print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
        ok = ok and cond
    print("\nStage 4 2D (both-axes) streaming PROVEN" if ok else "\nSTAGE 4 PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
