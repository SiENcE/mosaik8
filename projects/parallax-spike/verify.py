#!/usr/bin/env python3
"""Assert SCANLINE PARALLAX on a real ROM, in PyBoy.

The spike draws ONE marker column (tile 2, solid black) at logical column 20 in
all three bands. At camera `camx` band i shows the map at `camx >> shift[i]`, so
the marker lands at screen x `20*8 - (camx >> shift[i])` - three different
places in the same frame. That is the whole claim, and it is unfaked: a
background with one scroll register can only put it in one place.

The camera is not readable from outside, so the test does not assume it: it
takes the BOTTOM band (shift 0, the playfield) as the reference, derives camx
from where its marker sits, and then predicts the other two.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ROM = os.path.join(ROOT, "build", "gameboy", "parallax-spike.gb")
BANDS = [(0, 6, 4, 5), (6, 6, 1, 20), (12, 6, 0, 30)]  # row, rows, shift, marker col
FRAMES = 120

_FAILED = []


def check(cond, what):
    print(("  [PASS] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def marker_x(img, row0, rows):
    """Screen x of the marker column's left edge within a band, or None.

    The marker is the only BLACK column; the field is a flat mid grey. Scan the
    band's middle scanline for the leftmost run of >= 4 black pixels - 4, not 8,
    so a marker CLIPPED by the screen edge is still found (the slow band's sits
    near the right edge for most of the run).
    """
    y = row0 * 8 + rows * 8 // 2
    px = img.load()
    run = 0
    for x in range(img.width):
        if px[x, y] == 0:
            run += 1
            if run == 4:
                return x - 3
        else:
            run = 0
    return None


def main():
    if not os.path.exists(ROM):
        print("build the ROM first: python mosaik8.py build --platform gameboy "
              "projects/parallax-spike")
        return 1
    from pyboy import PyBoy
    pb = PyBoy(ROM, window="null", sound_emulated=False)
    while pb.frame_count < FRAMES:             # let the camera scroll a while
        pb.tick(1, True)
    img = pb.screen.image.convert("L")
    xs = [marker_x(img, r0, n) for r0, n, _s, _m in BANDS]
    pb.stop(save=False)

    print("marker x per band:", xs)
    check(all(x is not None for x in xs),
          "the marker column is visible in all three bands")
    if not all(x is not None for x in xs):
        return 1
    check(len(set(xs)) == 3,
          "the three bands show it at THREE different x (a single scroll "
          "register cannot)")

    # The bottom band is shift 0, so it reads the camera back directly.
    camx = BANDS[2][3] * 8 - xs[2]
    print("camera derived from the bottom band: %d px" % camx)
    check(camx > 0, "the camera has scrolled")
    for i, (_r0, _n, shift, mcol) in enumerate(BANDS):
        want = mcol * 8 - (camx >> shift)
        # +/-1 px: the ISR writes SCX one scanline into the band, and the
        # camera advances between the frame the bands were published in.
        check(abs(xs[i] - want) <= 1,
              "band %d (shift %d) marker at x=%d, expected %d"
              % (i, shift, xs[i], want))

    print()
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All parallax checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
