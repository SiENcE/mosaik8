#!/usr/bin/env python3
"""Split ANY per-frame window into its inner calls (60fps plan, Stage 0's rule).

The generalised `render_split_probe`: hand it a symbol list and it reports
cycles AND CALLS PER GAME FRAME for each, attributing every span to the tag
that OPENED it. The call count is the reliable half - a span runs to the NEXT
hook, so it always carries the caller's tail, and reading a per-call figure as
"what this function costs" gave the wrong answer three times (a fast path that
returns every time still opens the span that pays for the loop tail).

Usage: split_probe.py --rom ROM.gb --noi SYM.noi --syms a,b,c [--room 0]
       [--frames 400] [--hold right]
"""
import re
import sys
from collections import Counter

arg = lambda n, d=None: (sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d)
ROM = arg("--rom")
NOI = arg("--noi")
if not (ROM and NOI):
    print(__doc__)
    print("!! --rom ROM.gb and --noi SYM.noi are required (a -Wl-j pair: "
          "tools/framebudget/build_noi.py <project>)")
    sys.exit(2)
ROOM = int(arg("--room", 0))
FRAMES = int(arg("--frames", 400))
HOLD = arg("--hold")
SYMS = [x if x.startswith("_") else "_" + x for x in arg("--syms", "").split(",") if x]
FIRST = SYMS[0]

s = {}
for line in open(NOI, encoding="utf-8", errors="replace"):
    m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
    if m:
        s[m.group(1)] = int(m.group(2), 16)
missing = [n for n in SYMS if n not in s]
if missing:
    print("!! not in the .noi (stale relink?):", ", ".join(missing))

from pyboy import PyBoy
pb = PyBoy(ROM, window="null")
g = lambda n: s[n] & 0xFFFF
ev, rec, frames = [], [False], [0]

def mk(tag, first=False):
    def hit(_c):
        if rec[0]:
            ev.append((pb._cycles(), tag))
            if first:
                frames[0] += 1
    return hit

for n in SYMS:
    if n in s:
        v = s[n]
        pb.hook_register(v >> 16, v & 0xFFFF, mk(n, n == FIRST), None)

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

cyc, calls = Counter(), Counter()
for j in range(len(ev) - 1):
    c, tag = ev[j]
    cyc[tag] += ev[j + 1][0] - c
    calls[tag] += 1
n = max(frames[0], 1)
print("room %d  %s  %d game frames (anchor %s)" % (ROOM, HOLD or "idle", n, FIRST))
print("  %-30s %10s %8s %10s" % ("inner stage", "cyc/frame", "calls/f", "cyc/call"))
for tag, _v in sorted(cyc.items(), key=lambda t: -t[1]):
    print("  %-30s %10.0f %8.2f %10.0f"
          % (tag, cyc[tag] / n, calls[tag] / n, cyc[tag] / max(1, calls[tag])))
print("  %-30s %10.0f" % ("TOTAL", sum(cyc.values()) / n))
pb.stop(save=False)
