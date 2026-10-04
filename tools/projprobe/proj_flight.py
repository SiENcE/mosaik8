#!/usr/bin/env python3
"""A SHOT's flight, read straight off OAM so the same instrument runs on two
builds without a symbol file: its speed in px per LCD frame, how far it
travels, and the frame it despawns.

The descriptor change (plan item 1.3) must not move any of those - it changes
what a shot DRAWS, not how it flies - so this is the A/B control beside
`proj_desc_probe.py`, which reads the pool state itself.

Usage: proj_flight.py ROM.gb [--base 23] [--frames 400]
"""
import sys

# A parked shot has TWO spellings, and a probe that knows only one reads a
# hidden sprite as a flying one. The dense fan parks through `sprite.move`
# (200, 200), which the move helper biases to OAM (208, 216); the LIST fan
# parks each child at the codegen's own `(0, GBS_SPR_PARK_Y)` = OAM (0, 200),
# because an authored dy added to a biased 216 would wrap back on screen.
PARKED = ((216, 208), (200, 0))


def main():
    rom = sys.argv[1]
    base = 23
    if "--base" in sys.argv:
        base = int(sys.argv[sys.argv.index("--base") + 1])
    frames = 400
    if "--frames" in sys.argv:
        frames = int(sys.argv[sys.argv.index("--frames") + 1])

    from pyboy import PyBoy
    pb = PyBoy(rom, window="null")
    OAM = 0xFE00

    def obj(n):
        a = OAM + n * 4
        return (pb.memory[a], pb.memory[a + 1], pb.memory[a + 2],
                pb.memory[a + 3])

    def run(n=1):
        for _ in range(n):
            pb.tick()

    def tap(btn, after=20):
        pb.button_press(btn)
        run(8)
        pb.button_release(btn)
        run(after)

    run(620)
    tap("start", 60)
    tap("a", 0)

    ys, xs, tiles, props, live = [], [], set(), set(), 0
    for _ in range(frames):
        run(1)
        y, x, t, p = obj(base)
        if (y, x) in PARKED or y >= 200:
            if live:
                break
            continue
        live += 1
        ys.append(y)
        xs.append(x)
        tiles.add(t)
        props.add(p)
        _y2, x2, t2, p2 = obj(base + 1)
        if live == 1:
            print("first drawn frame: %d:(y%d x%d t%d p%02X)  %d:(y%d x%d t%d p%02X)"
                  % (base, y, x, t, p, base + 1, _y2, x2, t2, p2))
            print("  second object dx = %d, props = %02X" % (x2 - x, p2))
    if not ys:
        print("no shot drawn at base %d" % base)
        pb.stop(save=False)
        return
    steps = [ys[i] - ys[i + 1] for i in range(len(ys) - 1)]
    steps = [s for s in steps if s > 0]
    print("frames drawn      : %d" % live)
    print("travel            : %d px (y %d -> %d)" % (ys[0] - ys[-1], ys[0],
                                                      ys[-1]))
    print("px per LCD frame  : %.2f" % ((ys[0] - ys[-1]) / float(live - 1)))
    print("distinct tiles    : %d %s" % (len(tiles), sorted(tiles)))
    print("distinct props    : %s" % sorted("%02X" % p for p in props))
    pb.stop(save=False)


if __name__ == "__main__":
    main()
