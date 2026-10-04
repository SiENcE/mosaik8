#!/usr/bin/env python3
"""Are the parallax bands' scroll writes landing on their own scanlines?

In a parallax room the per-scanline SCX must split at the SAME band-boundary
lines every frame. A band write held off (an ISR running long at the wrong
time) shows as splits at VARYING lines and as frames whose top band carries
the previous band's scroll. Prints the split-line histogram and the count of
frames whose split set differs from the modal one.

Usage: parallax_band_check.py ROM.gb SYM.noi ROOM [frames] [--hold]

`--hold` pins `gbs_mdrv_hold` so the music ISR stands down for the whole
measurement: the CONTROL that separates "the tick displaces the band write"
from "this build is differently phased" on ONE binary (the pin-the-flag-off
rule from the R2 caches).
"""
import sys, re
from collections import Counter

ROM, NOI, ROOM = sys.argv[1], sys.argv[2], int(sys.argv[3])
FRAMES = int(sys.argv[4]) if len(sys.argv) > 4 and not sys.argv[4].startswith("-") else 300
HOLD = "--hold" in sys.argv

from pyboy import PyBoy

s = {}
for line in open(NOI, encoding="utf-8", errors="replace"):
    m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
    if m:
        s[m.group(1)] = int(m.group(2), 16)
g = lambda n: s[n] & 0xFFFF

pb = PyBoy(ROM, window="null")
pb.set_emulation_speed(0)


def w16(a, v):
    pb.memory[a], pb.memory[a + 1] = v & 0xFF, (v >> 8) & 0xFF


for _ in range(120):
    pb.tick()
pb.memory[g("_vm_core_pend_code")] = 2
pb.memory[g("_vm_core_pend_a")] = ROOM
w16(g("_vm_core_pend_b"), 80)
w16(g("_vm_core_pend_c"), 80)
for _ in range(300):
    pb.tick()

if HOLD:
    pb.memory[g("_gbs_mdrv_hold")] = 1   # the ISR stands down for the run

pb.button_press("right")
split_sets = Counter()
line_hist = Counter()
for _ in range(FRAMES):
    pb.tick(1, True)
    if HOLD:
        pb.memory[g("_gbs_mdrv_hold")] = 1
    pos = pb.screen.tilemap_position_list
    sc = [(pos[ly][0], pos[ly][1]) for ly in range(144)]
    splits = tuple(ly for ly in range(1, 144) if sc[ly] != sc[ly - 1])
    split_sets[splits] += 1
    for ly in splits:
        line_hist[ly] += 1
pb.button_release("right")

modal, n = split_sets.most_common(1)[0]
odd = FRAMES - n
print("modal split lines: %s  (%d/%d frames)" % (list(modal), n, FRAMES))
print("frames off the modal split set: %d" % odd)
print("split-line histogram: %s"
      % {ly: c for ly, c in sorted(line_hist.items())})
pb.stop(save=False)
