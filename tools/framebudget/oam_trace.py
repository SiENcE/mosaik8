#!/usr/bin/env python3
"""Dump OAM every frame, for an exact A/B across an optimisation.

A same-position move LATCH is only correct if the hardware ends every frame
holding exactly what it held before - so the honest test is not "does it still
look right", it is "is the shadow OAM frame-for-frame identical". This walks a
scripted regime in one room and prints a digest per frame; run it on the two
builds and diff.

Usage: oam_trace.py ROM SYM.noi --room N [--frames 400]
                    [--regime idle|walk|fire|hold]

`fire` TAPS the button, and a tap re-anchors the regime's own input even
though the taps are paced in game frames: the press is asserted between two
LCD ticks, so a build with a different LCD-per-game-frame ratio reads it one
VM frame earlier and the stream then differs for the LIFETIME of whatever
that tap launched. Measured 2026-09-06 on room 8: identical for 190 frames,
different for 49, identical again, with the two builds launching shots on
different game frames. `hold` HOLDS the button for the whole window instead -
no host-side edge is left, and the same A/B was byte-identical.
"""
import hashlib
import re
import sys


def symbols(noi):
    out = {}
    for line in open(noi, encoding="utf-8", errors="replace"):
        m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
        if m:
            out[m.group(1)] = int(m.group(2), 16)
    return out


def main():
    rom, noi = sys.argv[1], sys.argv[2]
    arg = lambda k, d: (sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d)
    room = int(arg("--room", "5"))
    frames = int(arg("--frames", "400"))
    regime = arg("--regime", "idle")
    s = symbols(noi)
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null")
    g = lambda n: s[n] & 0xFFFF

    def w16(a, v):
        pb.memory[a], pb.memory[a + 1] = v & 0xFF, (v >> 8) & 0xFF

    for _ in range(120):
        pb.tick()
    pb.memory[g("_vm_core_pend_code")] = 2
    pb.memory[g("_vm_core_pend_a")] = room
    w16(g("_vm_core_pend_b"), 40)
    w16(g("_vm_core_pend_c"), 72)
    # SETTLE IN GAME FRAMES, NOT LCD FRAMES - the same trap as the autofire
    # taps below, one step earlier, and it is what makes an A/B across a
    # THRESHOLD readable at all. A flat `for _ in range(300): tick()` lets a
    # faster build run MORE VM frames before the window opens, so the two
    # streams start at different animator phases and diverge a few frames in
    # for a reason that is not the change (measured 2026-08-28: with the
    # render gate on, the town room settles ~12 VM frames further along than
    # with it pinned off, and its NPC then steps one frame earlier).
    rows = []
    settled = [0]
    anchor = s["_vm_core_run_scripts"]

    def on_frame(_c):
        # ONE hook (PyBoy allows one per address): it counts the settle, then
        # samples. The sample is taken at the frame's first stage, so the two
        # streams are aligned by GAME frame on both sides of the window.
        settled[0] += 1
        if settled[0] > 240:
            rows.append(bytes(pb.memory[0xFE00:0xFEA0]))

    pb.hook_register(anchor >> 16, anchor & 0xFFFF, on_frame, None)
    t = 0
    while settled[0] < 240 and t < 3000:
        pb.tick()
        t += 1
    if regime == "walk":
        pb.button_press("right")
    if regime == "hold":
        # No EDGE for the rest of the window: see the module docstring. What
        # is left is the game's own clock, which the per-game-frame sampling
        # below already aligns.
        pb.button_press("a")
    # SAMPLE ONCE PER GAME FRAME, NOT PER LCD FRAME. An optimisation that
    # saves cycles changes how many LCD frames a VM frame takes, so an
    # LCD-paced trace shifts wholesale and every regime "differs" for the one
    # reason that is not a defect. Hooking the frame's first stage aligns the
    # two streams by construction.
    t = 0
    while len(rows) < frames:
        if regime == "fire":
            # ...and the INPUT is paced in game frames too, for the same
            # reason: an LCD-paced tap pattern reaches a faster build at a
            # different point in the script.
            k = len(rows)
            if k % 10 == 0:
                pb.button_press("a")
            elif k % 10 == 5:
                pb.button_release("a")
        pb.tick()
        t += 1
        if t > frames * 6:
            break
    h = hashlib.md5()
    for r in rows:
        h.update(r)
    print("room %d %s: %d game frames, OAM stream %s"
          % (room, regime, len(rows), h.hexdigest()))
    print("first 12:", " ".join(hashlib.md5(r).hexdigest()[:8] for r in rows[:12]))
    # --dump prints EVERY game frame's digest, so an A/B can name the first
    # frame that differs instead of only "the streams differ" - which is the
    # difference between "a behaviour changed" and "timing drift compounded"
    # (the moved-edge A/B).
    if "--dump" in sys.argv:
        for i, r in enumerate(rows):
            print("%04d %s" % (i, hashlib.md5(r).hexdigest()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
