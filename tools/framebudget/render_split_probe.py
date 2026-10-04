#!/usr/bin/env python3
"""Split vm.actor.render's window into its inner calls.

The rule: SPLIT THE BIGGEST WINDOW BEFORE OPTIMISING IT. render is
the biggest per-frame stage in the town room idle (~25k cycles), so hook the
calls it makes and report cycles + CALLS PER GAME FRAME for each - the
call count is what says whether a cost is per-slot or fixed.

Usage: render_split_probe.py --rom ROM.gb --noi SYM.noi [--room 0]
       [--frames 400] [--hold right]
"""
import re
import sys
from collections import Counter
from pyboy import PyBoy

ROM = (sys.argv[sys.argv.index("--rom") + 1] if "--rom" in sys.argv
       else None)
NOI = (sys.argv[sys.argv.index("--noi") + 1] if "--noi" in sys.argv
       else None)
if not (ROM and NOI):
    print(__doc__)
    print("!! --rom ROM.gb and --noi SYM.noi are required (a -Wl-j pair: "
          "tools/framebudget/build_noi.py <project>)")
    sys.exit(2)
ROOM = int(sys.argv[sys.argv.index("--room") + 1]) if "--room" in sys.argv else 0
FRAMES = int(sys.argv[sys.argv.index("--frames") + 1]) if "--frames" in sys.argv else 400
HOLD = sys.argv[sys.argv.index("--hold") + 1] if "--hold" in sys.argv else None

#: Entry symbols INSIDE the render walk, plus the stage boundaries either
#: side so the split adds up to the stage the harness reports.
INNER = ["_vm_actor_render", "_vm_player_cam_x", "_vm_player_cam_y",
         "_vm_actor_base_of", "_vm_actor_off_window", "_vm_actor_place",
         "_vm_emote_update"]

s = {}
for line in open(NOI, encoding="utf-8", errors="replace"):
    m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
    if m:
        s[m.group(1)] = int(m.group(2), 16)

pb = PyBoy(ROM, window="null")
pb.set_emulation_speed(0)
g = lambda n: s[n] & 0xFFFF

ev = []
rec = [False]
frames = [0]

def mk(tag, first=False):
    def hit(_c):
        if rec[0]:
            ev.append((pb._cycles(), tag))
            if first:
                frames[0] += 1
    return hit

for n in INNER:
    if n in s:
        v = s[n]
        pb.hook_register(v >> 16, v & 0xFFFF, mk(n, n == "_vm_actor_render"), None)

def w16(a, v):
    pb.memory[a], pb.memory[a + 1] = v & 0xFF, (v >> 8) & 0xFF

for _ in range(120):
    pb.tick()
pb.memory[g("_vm_core_pend_code")] = 2
pb.memory[g("_vm_core_pend_a")] = ROOM
w16(g("_vm_core_pend_b"), 40)
w16(g("_vm_core_pend_c"), 72)
for _ in range(300):
    pb.tick()
if HOLD:
    pb.button_press(HOLD)
for _ in range(30):
    pb.tick()
rec[0] = True
for _ in range(FRAMES):
    pb.tick()
rec[0] = False

# Attribute each span to the tag that OPENED it; a span that ends at the
# next render entry closes the walk.
cyc = Counter()
calls = Counter()
for j in range(len(ev) - 1):
    c, tag = ev[j]
    cyc[tag] += ev[j + 1][0] - c
    calls[tag] += 1
n = max(frames[0], 1)
print("room %d  %s  %d game frames" % (ROOM, HOLD or "idle", n))
print("  %-26s %10s %8s" % ("inner stage", "cyc/frame", "calls/frame"))
for tag, _ in sorted(cyc.items(), key=lambda t: -t[1]):
    print("  %-26s %10.0f %8.2f" % (tag, cyc[tag] / n, calls[tag] / n))
print("  %-26s %10.0f" % ("TOTAL (render window)", sum(cyc.values()) / n))
pb.stop(save=False)
