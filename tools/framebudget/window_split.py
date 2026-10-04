#!/usr/bin/env python3
"""Split ONE stage window into its inner calls, scoped to the window.

`render_split_probe.py` attributes every span between two hooked entries,
including the ones AFTER the stage ended - so the stage's closing symbol
gets charged the whole rest of the frame and the table does not add up to
the stage. This one takes the window's OPEN and CLOSE symbols and only
accumulates spans between them, so the total IS the stage's cost as
`framebudget.py` reports it.

Usage:
  window_split.py --rom ROM.gb --noi SYM.noi \
      --open _vm_actor_render --close _vm_emote_update \
      --inner _vm_actor_base_of,_vm_actor_off_window,... [--room 0]
"""
import re
import sys
from collections import Counter
from pyboy import PyBoy


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


ROM = arg("--rom")
NOI = arg("--noi")
if not (ROM and NOI):
    print(__doc__)
    print("!! --rom ROM.gb and --noi SYM.noi are required (a -Wl-j pair: "
          "tools/framebudget/build_noi.py <project>)")
    sys.exit(2)
ROOM = int(arg("--room", 0))
FRAMES = int(arg("--frames", 600))
HOLD = arg("--hold")
FIRE = "--fire" in sys.argv
OPEN = arg("--open", "_vm_actor_render")
CLOSE = arg("--close", "_vm_emote_update")
INNER = [t for t in (arg("--inner", "") or "").split(",") if t]

s = {}
for line in open(NOI, encoding="utf-8", errors="replace"):
    m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
    if m:
        s[m.group(1)] = int(m.group(2), 16)

pb = PyBoy(ROM, window="null")
pb.set_emulation_speed(0)
g = lambda n: s[n] & 0xFFFF

ev, rec, gf = [], [False], [0]


def mk(tag):
    def hit(_c):
        if rec[0]:
            ev.append((pb._cycles(), tag))
            if tag == OPEN:
                gf[0] += 1
    return hit


missing = []
for n in [OPEN, CLOSE] + INNER:
    if n in s:
        v = s[n]
        pb.hook_register(v >> 16, v & 0xFFFF, mk(n), None)
    else:
        missing.append(n)
if missing:
    print("!! no symbol:", ", ".join(missing))


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
for t in range(FRAMES):
    if FIRE:
        k = gf[0]
        if k % 10 == 0:
            pb.button_press("a")
        elif k % 10 == 5:
            pb.button_release("a")
    pb.tick()
rec[0] = False

cyc, calls = Counter(), Counter()
inside = False
for j in range(len(ev) - 1):
    c, tag = ev[j]
    if tag == OPEN:
        inside = True
    if inside:
        cyc[tag] += ev[j + 1][0] - c
        calls[tag] += 1
    if tag == CLOSE:
        inside = False
n = max(gf[0], 1)
if "--seq" in sys.argv:
    from collections import defaultdict
    seqs = defaultdict(list)
    cur = None
    for j in range(len(ev) - 1):
        c, tag = ev[j]
        if tag == OPEN:
            cur = []
        if cur is not None:
            cur.append((tag, ev[j + 1][0] - c))
            if tag == CLOSE:
                seqs[tuple(t for t, _d in cur)].append(cur)
                cur = None
    short = {}
    for k, pat in enumerate(sorted({t for p_ in seqs for t in p_})):
        short[pat] = chr(ord("a") + k) if k < 26 else "?"
    print("  key: " + "  ".join("%s=%s" % (v, k) for k, v in short.items()))
    for pat, runs in sorted(seqs.items(), key=lambda t: -len(t[1]))[:3]:
        print("  pattern %s  x%d" % ("".join(short[t] for t in pat), len(runs)))
        for pos in range(len(pat) - 1):
            avg = sum(r[pos][1] for r in runs) / len(runs)
            print("     %02d %-30s %8.0f" % (pos, pat[pos], avg))
print("room %d  %s  %d game frames  window %s -> %s"
      % (ROOM, ("fire" if FIRE else HOLD or "idle"), n, OPEN, CLOSE))
print("  %-28s %10s %8s" % ("inner", "cyc/frame", "calls/frame"))
for tag, _v in sorted(cyc.items(), key=lambda t: -t[1]):
    if tag == CLOSE:
        continue
    print("  %-28s %10.1f %8.2f" % (tag, cyc[tag] / n, calls[tag] / n))
print("  %-28s %10.1f"
      % ("TOTAL", sum(v for k, v in cyc.items() if k != CLOSE) / n))
pb.stop(save=False)
