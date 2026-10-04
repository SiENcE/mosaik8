#!/usr/bin/env python3
"""PyBoy proof for the wide-scroll spike (Stage 0 of the large-levels-u16 plan).

Proves COLUMN-STREAMING past the 256 px / 32-tile hardware wrap: two MARKER columns
sit at logical columns 40 (tile 2) and 55 (tile 3) -- pixels 320 and 440, both well
past the 256 px wrap. Their hardware ring-buffer slots are 40 % 32 = 8 and 55 % 32 = 23.

  * BEFORE scrolling there, those hardware columns hold near logical columns (ground,
    tile 1) -- NO marker.
  * AFTER auto-scrolling to camx = 320, the streaming has written logical 40 into
    hardware column 8 and logical 55 into hardware column 23 -- so reading the BG map
    there shows the markers, and SCX = 320 % 256 = 64.

A static 32-tile upload + u8 coords cannot do this; the column streaming can.

Run:  python projects/wide-scroll-spike/verify.py
"""
import os
import sys

ROM = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "build", "gameboy", "wide-scroll-spike.gb")


def _col(pb, c, rows=18):
    return [pb.memory[0x9800 + r * 32 + c] for r in range(rows)]


def main():
    from pyboy import PyBoy
    pb = PyBoy(ROM, window="null")

    # Advance past the DMG BIOS boot + crt0 until main()'s boot fill has populated
    # the WHOLE visible row with ground (every hardware column 0..31 of row 0 == 1).
    # That first all-ground frame is right after the fill, with camx still 0 -- before
    # any far column has streamed in. (Polling a single cell caught a mid-fill / BIOS
    # logo frame.)
    f = 0
    while f < 300 and not all(pb.memory[0x9800 + c] == 1 for c in range(32)):
        pb.tick(); f += 1
    before8, before23 = _col(pb, 8), _col(pb, 23)
    before_scx = pb.memory[0xFF43]

    # Auto-scroll right; tick well past the STOP_X = 320 clamp so camx settles there.
    for _ in range(400):
        pb.tick()
    after8, after23 = _col(pb, 8), _col(pb, 23)
    after_scx = pb.memory[0xFF43]
    pb.stop()

    print("BEFORE (camx~0)   SCX=%3d  hw col 8=%s  hw col 23=%s"
          % (before_scx, sorted(set(before8)), sorted(set(before23))))
    print("AFTER  (camx=320) SCX=%3d  hw col 8=%s  hw col 23=%s"
          % (after_scx, sorted(set(after8)), sorted(set(after23))))

    checks = [
        ("before: no marker in the recycled hw columns",
         2 not in before8 and 3 not in before23),
        ("after: logical col 40 (px 320, past the wrap) -> hw col 8 = marker 2",
         all(t == 2 for t in after8)),
        ("after: logical col 55 (px 440, past the wrap) -> hw col 23 = marker 3",
         all(t == 3 for t in after23)),
        ("after: SCX = camx mod 256 = 64", after_scx == 64),
    ]
    ok = True
    for label, cond in checks:
        print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
        ok = ok and cond
    print("\nStage 0 column-streaming PROVEN" if ok else "\nSTAGE 0 PROOF FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
