#!/usr/bin/env python3
"""Does every scanline render at ITS OWN BAND'S scroll, on every frame?

THE INSTRUMENT THAT FOUND THREE OF THE FOUR LYC-MERGE DEFECTS (2026-09-21).
Each of them was a
SINGLE wrong frame, which is why they survived a green suite: every gate in
`projects/lyc-merge-lab/verify.py` settles first and then reads one frame, so
it measures the steady state and is blind to the frame an arm or a disarm
actually happened on.

The question it asks needs no picture of a correct frame. The ROM knows where
its bands end (`gbs_px_last[]`) and what each is to be drawn with
(`gbs_px_livex[]`), so the render is compared against the ROM'S OWN INTENT.

FOUR RULES, each of which cost a wasted measurement to learn:

  * READ THE SHADOW BEFORE THE TICK. `gbs_px_livex` is committed in V-blank,
    which is the END of `pb.tick()`; reading it afterwards gives the values
    the NEXT frame will use, and with a moving camera every band then reads
    one off - wrong frames on a correct ROM.
  * SKIP UNIFORM FRAMES. A fade takes the screen to one grey level, and the
    room-change disarm usually lands there: counting those reports a defect
    nobody can see, and dismissing them as "a faded transition" hid a REAL
    one for two passes. A frame with one distinct colour is not evidence
    either way.
  * EXCLUDE THE BOUNDARY LINES. The ISR writes in H-blank, so a stop's own
    line is legitimately either value. That one-line question is a separate,
    measured, still-open defect (~0.5% of frames) and folding it in here
    drowns the signal.
  * PROVE THE PROBE CAN REPORT. `--inject` mislabels one band on one frame
    and the run must flag it. Three separate zeros this session were an
    instrument that had silently stopped measuring - a probe latched onto the
    wrong room, a walking comparison whose camera moved every frame, and a
    sprite mask that blanked the whole band. A zero from a probe that has not
    shown it can fail is not a measurement.

The WRAM addresses are read from the `.map`, not hardcoded: adding one byte of
LYC state (`gbs_lyc_drop`) shifted every symbol after it and silently turned
three probes into readers of the wrong bytes.

Usage:
    band_truth.py ROM.gb MAP [--dmg] [--frames N] [--inject] [--route cutscene]
                  [--room N --noi SYM.noi [--at X,Y] [--route pace]]

The routes play the game until a parallax room arms its bands. `--room N`
pokes that room instead (vm.core's pending exception, RAISE 2, 300 frames in;
needs the `-Wl-j` `.noi` from `tools/framebudget/build_noi.py`, since the
`.map` truncates names), which is the way in for a project whose band room is
not reached by walking right. `--at X,Y` is where the poke puts the player
(default 40,72). The `walk` route drifts right then left and can leave a short
room by its door; `--route pace` walks right and back by the SAME amount, with
jumps, so a poked player stays in the room. The first-party subject is
`projects/vm-shardlings` room 7, the Ridgeway:

    band_truth.py ROM MAP --room 7 --noi SYM.noi --at 200,104 --route pace

Exit 1 if any VISIBLE frame renders a band at another band's scroll, or if no
visible band frame was measured at all.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(os.path.dirname(HERE))

#: The LYC/parallax state, in the order `gbdk_lyc.py` declares it. Only the
#: FIRST symbol is looked up; the rest follow it, because sdldgb's map
#: truncates every name to nine characters and `_gbs_px_l` is both
#: `gbs_px_last` and `gbs_px_livex`.
_LAYOUT = ("gbs_px_n", 1), ("gbs_px_last", 3), ("gbs_px_shx", 3), \
          ("gbs_px_livex", 3)


def wram(map_text):
    """`gbs_px_n` and the three arrays after it, from the .map.

    The map prints symbols in address order with names cut to nine
    characters, so `_gbs_px_n` (which is unique at that length) is found and
    the rest are counted off it.
    """
    base = None
    for m in re.finditer(r"0000(C[0-9A-F]{3})\s+_gbs_px_n\b", map_text):
        base = int(m.group(1), 16)
    if base is None:
        raise SystemExit("no _gbs_px_n in the map: this ROM has no merged LYC owner")
    out, at = {}, base
    for name, size in _LAYOUT:
        out[name] = at
        at += size
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("map")
    ap.add_argument("--dmg", action="store_true")
    ap.add_argument("--frames", type=int, default=20000)
    ap.add_argument("--inject", action="store_true",
                    help="mislabel a band on one frame; the run MUST flag it")
    ap.add_argument("--room", type=int, default=None,
                    help="poke this room (RAISE 2) instead of walking to one")
    ap.add_argument("--noi", help="the -Wl-j .noi, needed by --room")
    ap.add_argument("--at", default="40,72",
                    help="X,Y the --room poke puts the player at (pixels)")
    ap.add_argument("--route", default="walk", choices=("walk", "cutscene", "pace"),
                    help="walk = hold right into a wide parallax room and "
                         "walk-and-talk; cutscene = tap Start/A through a "
                         "boot sequence of cutscenes; pace = right and back "
                         "by the same amount (for a --room poke)")
    a = ap.parse_args()

    try:
        from pyboy import PyBoy
    except ImportError:
        print("PyBoy not installed -- skipping")
        return 0
    import numpy as np

    sym = wram(open(a.map, encoding="utf-8", errors="ignore").read())
    pend = None
    if a.room is not None:
        if not a.noi:
            raise SystemExit("--room needs --noi (a -Wl-j symbol file)")
        noi = {}
        for line in open(a.noi, encoding="utf-8", errors="replace"):
            p = line.split()
            if len(p) == 3 and p[0] == "DEF":
                noi[p[1]] = int(p[2], 16) & 0xFFFF
        pend = [noi["_vm_core_pend_" + k] for k in ("code", "a", "b", "c")]
    pb = PyBoy(a.rom, window="null", sound_emulated=False, cgb=not a.dmg)
    px_n, px_last, px_livex = sym["gbs_px_n"], sym["gbs_px_last"], sym["gbs_px_livex"]

    inpx = -1
    seen = faded = 0
    bad = []
    if a.route == "walk":
        pb.button_press("right")
    for f in range(a.frames):
        if pend and f == 300:
            code, pa, pb_, pc = pend
            pb.memory[pa] = a.room
            ax, ay = (int(v) for v in a.at.split(","))
            pb.memory[pb_], pb.memory[pb_ + 1] = ax & 0xFF, ax >> 8
            pb.memory[pc], pb.memory[pc + 1] = ay & 0xFF, ay >> 8
            pb.memory[code] = 2
        if inpx < 0:
            if a.route == "cutscene":
                if f % 30 == 0: pb.button("start")
                if f % 30 == 15: pb.button("a")
            else:
                if f % 37 == 0: pb.button("a")
                if f % 53 == 0: pb.button("start")
        elif a.route == "cutscene":
            if f % 30 == 0: pb.button("start")
            if f % 30 == 15: pb.button("a")
        elif a.route == "pace":
            t = (f - inpx) % 240
            if t == 0: pb.button_press("right")
            elif t == 90: pb.button_release("right")
            elif t in (100, 220): pb.button("a")
            elif t == 120: pb.button_press("left")
            elif t == 210: pb.button_release("left")
        else:
            t = (f - inpx) % 320
            if t == 0: pb.button_press("right")
            elif t == 90: pb.button_release("right")
            elif t in (100, 140): pb.button("a")
            elif t == 180: pb.button_press("left")
            elif t == 250: pb.button_release("left")
            elif t == 260: pb.button_press("up")
            elif t == 270: pb.button_release("up")

        n = pb.memory[px_n]
        last = [pb.memory[px_last + i] for i in range(3)]
        live = [pb.memory[px_livex + i] for i in range(3)]
        pb.tick(1, True)

        if inpx < 0:
            # ENTRY IS THE ROM'S OWN STATE, never a guess from the picture: a
            # version that keyed on "any scroll boundary" latched in a
            # different room and then skipped every frame, reporting 0.
            if pb.memory[px_n] >= 2:
                inpx = f
                if a.route == "walk":
                    pb.button_release("right")
            continue
        if n < 2:
            continue
        img = np.array(pb.screen.ndarray[:, :, 0])
        if len(np.unique(img)) < 2:
            faded += 1
            continue
        if a.inject and seen == 200:
            live[0] = (live[0] + 7) & 0xFF
        seen += 1
        scx = [r[0] for r in pb.screen.tilemap_position_list[:144]]
        lo, wrong = 0, []
        for i in range(n):
            hi = last[i] if last[i] else 143
            for y in range(lo + 1, hi):
                if scx[y] != live[i]:
                    wrong.append((y, scx[y], live[i], i))
            lo = hi + 1
        if wrong:
            bad.append((f, len(wrong), wrong[0]))

    pb.stop(save=False)
    print("%s (%s, route %s)" % (os.path.basename(a.rom),
                                 "dmg" if a.dmg else "cgb", a.route))
    print("  entered the parallax room at frame %d" % inpx)
    print("  VISIBLE frames measured : %d  (%d faded frames skipped)" % (seen, faded))
    print("  frames with a band at the wrong scroll: %d" % len(bad))
    for b in bad[:15]:
        print("     f=%d  %d wrong lines, first (line %d, scx %d, want %d, band %d)"
              % (b[0], b[1], b[2][0], b[2][1], b[2][2], b[2][3]))
    if not seen:
        print("  [FAIL] no visible parallax frame was measured: the route "
              "never armed a band room (try --room N --noi SYM.noi)")
        return 1
    if a.inject:
        ok = bool(bad)
        print("  [%s] the probe can REPORT: the injected band was %s"
              % ("PASS" if ok else "FAIL", "caught" if ok else "MISSED"))
        return 0 if ok else 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
