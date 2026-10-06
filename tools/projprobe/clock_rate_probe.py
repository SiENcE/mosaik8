#!/usr/bin/env python3
"""How fast does a scripted mover really move, in px per LCD frame? (2026-08-30)

A speed authored in VM frames reaches the screen as the product of the
authored step and the ROOM's actual VM frame rate, and the second half moves
whenever the per-frame work does. So a duration or speed is QUOTED only after
it is measured on the ROM, and two ROMs (two builds, or a ROM and the game it
was ported from) are compared with the same instrument.

Method (symmetric, OAM only - a ROM without a symbol file works too): boot,
optionally press Start, let the first object that falls on its own drop with
no other input, and track its lowest row per LCD frame. An OAM entry whose y
travels more than `--min-span` px during the run is part of that mover;
nothing else should move without input.

THE RATE IS MEASURED ON STRICTLY INCREASING SPANS ONLY. The fall has pause
plateaus (the arrival wait, the respawn), and a run extended through them
with `>=` averages the gaps in: the recorded trap is a move of exactly 1.00
reading as 0.76.

With `--noi` (ours only) it also counts GAME frames per LCD frame over the
same window, which is the other half of the product.

Usage: clock_rate_probe.py ROM.gb [--label X] [--frames N] [--noi SYM.noi]
                          [--start F] [--min-span 64] [--max-stall 4]

`--start F` presses Start at LCD frame F (default 620, past a title screen's
own load); `--start none` presses nothing. The window opens 90 frames later.
"""
import re
import sys


def main():
    rom = sys.argv[1]
    arg = lambda n, d=None: (sys.argv[sys.argv.index(n) + 1]
                             if n in sys.argv else d)
    n = int(arg("--frames", 1400))
    label = arg("--label", rom)
    noi = arg("--noi")
    # How far an entry must travel to count as the falling mover. Two ROMs
    # can draw the same fall over different visible spans (one rests it from
    # a lifted spawn), so the threshold is a knob and the RATE is what is
    # compared.
    minspan = int(arg("--min-span", 64))

    from pyboy import PyBoy
    pb = PyBoy(rom, window="null")

    # Optional: count GAME frames (ours only - needs a -Wl-j relink).
    gf = [0]
    if noi:
        syms = {}
        for line in open(noi, encoding="utf-8", errors="replace"):
            m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
            if m:
                syms[m.group(1)] = int(m.group(2), 16)
        anchor = next((s for s in ("_vm_core_run_scripts", "_gbs_wait_vblank")
                       if s in syms), None)
        if anchor:
            v = syms[anchor]
            pb.hook_register(v >> 16, v & 0xFFFF, lambda _c: gf.__setitem__(0, gf[0] + 1), None)
        else:
            print("!! no anchor symbol in the .noi (stale relink?)")

    def run(k=1):
        for _ in range(k):
            pb.tick()

    start = arg("--start", "620")
    if start.lower() == "none":
        run(90)
    else:
        run(int(start))
        pb.button_press("start")
        run(8)
        pb.button_release("start")
        run(90)

    gf[0] = 0
    ys = []
    for _ in range(n):
        run(1)
        oam = pb.memory[0xFE00:0xFEA0]
        vis = [(i, oam[i * 4]) for i in range(40)
               if 0 < oam[i * 4] < 160 and 0 < oam[i * 4 + 1] < 168]
        ys.append(vis)
    game_frames = gf[0]
    pb.stop(save=False)

    lo, hi = {}, {}
    for vis in ys:
        for i, y in vis:
            lo[i] = min(lo.get(i, 255), y)
            hi[i] = max(hi.get(i, 0), y)
    fan = sorted(i for i in lo if hi[i] - lo[i] > minspan)
    if not fan:
        print("== %s ==\n  no falling fan found in %d frames" % (label, n))
        return 1

    track = [max((y for i, y in vis if i in fan), default=None) for vis in ys]

    # THE RATE IS MEASURED OVER ONE FALLING EPISODE, and the estimator has to
    # separate two kinds of plateau. A mover at 2 px per GAME frame in a room
    # running 2 LCD frames per game frame holds its y on every other LCD
    # frame, so "strictly increasing per LCD frame" finds runs of length 1 and
    # reports nothing; but a run extended through the ARRIVAL WAIT averages a
    # dead second in and reports a move of 1.00 as 0.76 (the recorded trap).
    # So: non-decreasing, and break when it has stalled for more than
    # `--max-stall` consecutive frames. That keeps the stepping gaps (which
    # ARE the motion) and cuts the wait (which is not).
    stall_max = int(arg("--max-stall", 4))
    best = (0, 0, 0)
    k = 0
    while k < len(track):
        if track[k] is None:
            k += 1
            continue
        j, stall = k, 0
        while j + 1 < len(track) and track[j + 1] is not None:
            if track[j + 1] > track[j]:
                stall = 0
            elif track[j + 1] == track[j]:
                stall += 1
                if stall > stall_max:
                    break
            else:
                break
            j += 1
        while j > k and track[j] == track[j - 1]:
            j -= 1                      # trim the trailing stall
        if track[j] - track[k] > best[0]:
            best = (track[j] - track[k], k, j)
        k = max(j + 1, k + 1)
    best = (best[2] - best[1], best[1], best[2])

    span, a, b = best
    print("== %s ==" % label)
    print("  fan entries %s" % fan)
    if span < 8:
        print("  no usable falling span (longest strictly increasing = %d)" % span)
        return 1
    px = track[b] - track[a]
    print("  fall: %d px over %d LCD frames = %.2f px per LCD frame"
          % (px, span, px / float(span)))
    if noi and game_frames:
        print("  game frames: %d in %d LCD = %.2f LCD per game frame"
              % (game_frames, n, n / float(game_frames)))
        print("  => %.2f px per GAME frame" % (px / float(span) * (n / float(game_frames))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
