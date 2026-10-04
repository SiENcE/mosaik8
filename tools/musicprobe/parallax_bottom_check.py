#!/usr/bin/env python3
"""LOOK at a parallax room's BOTTOM band, and say what it is showing.

The reported picture (the platformer conversion's first tutorial
room): the lowest rows of the screen carry
tiles that belong nowhere near the scene - the tutorial's labelled legend art
("P0P1P2P3", "H1H2H3") that is authored BELOW the playable area of a 22-row
map. Only the LAST parallax band carries the vertical scroll, and it streams to
the bottom of the MAP rather than of the screen, so if that band renders at the
wrong SCY - or its tilemap rows were never written - the legend appears.

A band-split counter cannot see this (the split lines are right; the CONTENT
under them is wrong), so this reports what a player sees:

  * the distinct pixel images of the bottom N rows over the run (a still band
    that shimmers has several; a scrolling one has many by construction, so
    walk the SAME path on both builds and compare like with like),
  * the per-scanline SCY the last band publishes,
  * PNGs, because the deciding evidence for this class is a picture.

Usage: parallax_bottom_check.py ROM.gb SYM.noi ROOM OUT_PREFIX [frames]
"""
import sys, re
from collections import Counter

ROM, NOI, ROOM, OUT = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
FRAMES = int(sys.argv[5]) if len(sys.argv) > 5 else 300
BOTTOM = 24                      # the lowest 3 tile rows

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


for _ in range(150):
    pb.tick()
pb.memory[g("_vm_core_pend_code")] = 2
pb.memory[g("_vm_core_pend_a")] = ROOM
w16(g("_vm_core_pend_b"), 40)
w16(g("_vm_core_pend_c"), 80)
for _ in range(240):
    pb.tick()

bottoms, scys = Counter(), Counter()
shots = {}
pb.button_press("right")
for i in range(FRAMES):
    pb.tick(1, True)
    img = pb.screen.ndarray[:, :, 0]
    bottoms[bytes(img[144 - BOTTOM:].tobytes())] += 1
    pos = pb.screen.tilemap_position_list
    scys[pos[143][1]] += 1
    if i in (FRAMES // 3, 2 * FRAMES // 3):
        shots[i] = pb.screen.image.copy()
pb.button_release("right")
for i, im in shots.items():
    im.save("%s_f%d.png" % (OUT, i))
print("distinct bottom-%d-row images: %d over %d frames"
      % (BOTTOM, len(bottoms), FRAMES))
print("last-band SCY values seen: %s"
      % sorted(scys)[:8] + (" ..." if len(scys) > 8 else ""))
print("saved %s" % ", ".join("%s_f%d.png" % (OUT, i) for i in shots))
pb.stop(save=False)
