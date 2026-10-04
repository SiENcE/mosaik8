#!/usr/bin/env python3
"""What does the player actually DRAW, row by row, on screen?

Built for "the player stands one pixel higher than in the reference". OAM alone
cannot answer that: our composer lays a metasprite into one rectangle sliced on
a 16 px grid while the reference engine places each object at its authored offset, so the
two ROMs hold DIFFERENT object counts at different y for the same picture. The
comparable thing is the PIXELS - so this composites the live OAM against the
OBJ tile data in VRAM and prints the screen rows they land on.

    pose_check.py ROM [--top N] [--hold right] [--presses N]

`--top N` waits for the frame whose topmost object sits at OAM y N. USE IT:
The reference engine's platform idle is three frames authored at tile y 13/12/13, so the
reference BOBS its upper half by a pixel and a single sample lands on either
pose. Ours keeps one idle frame, so an unmatched A/B invents
a 1 px offset that is not there.

`--hold` collects every DISTINCT pose seen while a button is held, which is how
a walk cycle is compared without matching phase.

Two traps that cost a wrong reading each:
  * `pb.tick(1, False)` does NOT render - screenshots come back white.
  * a fully transparent object is still live OAM; score the tile data, not the
    entry.
"""
import argparse
import collections
import sys

OAM, VRAM = 0xFE00, 0x8000


def _compose(pb):
    """{(screen_y, screen_x): colour index} over every live OBJ, 8x16 mode."""
    grid = {}
    tops = []
    for i in range(40):
        oy, ox = pb.memory[OAM + 4 * i], pb.memory[OAM + 4 * i + 1]
        if not (0 < oy < 160 and 0 < ox < 168):
            continue
        tile, attr = pb.memory[OAM + 4 * i + 2], pb.memory[OAM + 4 * i + 3]
        tops.append(oy)
        sy, sx = oy - 16, ox - 8
        xf, yf = bool(attr & 0x20), bool(attr & 0x40)
        for k in range(16):
            row = 15 - k if yf else k
            a = VRAM + ((tile & 0xFE) * 16) + row * 2
            lo, hi = pb.memory[a], pb.memory[a + 1]
            for b in range(8):
                bit = b if xf else 7 - b
                v = ((lo >> bit) & 1) | (((hi >> bit) & 1) << 1)
                if v:
                    grid[(sy + k, sx + b)] = v
    return grid, tops


def _pose(pb):
    """The composed sprite as a tuple of rows, left-aligned on its own art -
    so two ROMs drawing the same picture at different OAM offsets compare
    equal. `None` when nothing is drawn."""
    grid, _tops = _compose(pb)
    if not grid:
        return None
    y0, y1 = min(y for y, _ in grid), max(y for y, _ in grid)
    x0 = min(x for _, x in grid)
    width = max(x for _, x in grid) - x0 + 1
    return tuple("".join(".123"[grid.get((y, x0 + x), 0)] for x in range(width))
                 for y in range(y0, y1 + 1))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("--top", type=int,
                    help="wait for the frame whose topmost object is at this "
                         "OAM y (match the reference's idle bob)")
    ap.add_argument("--hold", help="collect distinct poses while this is held")
    ap.add_argument("--presses", type=int, default=22,
                    help="start/a pairs to walk the boot sequence (default 22, "
                         "which reaches the platformer conversion's "
                         "first tutorial room)")
    args = ap.parse_args()

    from pyboy import PyBoy
    pb = PyBoy(args.rom, window="null")

    def run(n):
        for _ in range(n):
            pb.tick(1, True)         # RENDER - False leaves the screen blank

    def tap(b, after=30):
        pb.button_press(b)
        run(8)
        pb.button_release(b)
        run(after)

    run(400)
    for _ in range(args.presses):
        tap("start", after=20)
        tap("a", after=140)

    if args.hold:
        pb.button_press(args.hold)
        run(40)
        seen = collections.Counter()
        for _ in range(240):
            run(1)
            p = _pose(pb)
            if p:
                seen[p] += 1
        pb.button_release(args.hold)
        print("%d distinct pose(s) while holding %s" % (len(seen), args.hold))
        for p, n in sorted(seen.items(), key=lambda kv: -kv[1]):
            print("  --- seen %d frame(s), %d row(s)" % (n, len(p)))
            for r in p:
                print("     |%s|" % r)
        return 0

    run(180)
    if args.top is not None:
        for _ in range(400):
            _g, tops = _compose(pb)
            if tops and min(tops) == args.top:
                break
            run(1)
        else:
            print("never saw a topmost object at OAM y %d" % args.top)
            return 1

    grid, tops = _compose(pb)
    if not grid:
        print("nothing drawn")
        return 1
    y0, y1 = min(y for y, _ in grid), max(y for y, _ in grid)
    x0, x1 = min(x for _, x in grid), max(x for _, x in grid)
    print("OAM y %s   SCX=%d SCY=%d   drawn rows %d..%d, cols %d..%d"
          % (sorted(set(tops)), pb.memory[0xFF43], pb.memory[0xFF42],
             y0, y1, x0, x1))
    for y in range(y0, y1 + 1):
        print("  %3d |%s|" % (y, "".join(".123"[grid.get((y, x), 0)]
                                         for x in range(x0, x1 + 1))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
