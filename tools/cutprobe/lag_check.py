#!/usr/bin/env python3
"""Is the placement residual a one-frame CAMERA LAG? (plan 6.8)

place_check found every non-pinned actor alternating between residual +0 and
+2 while the PINNED one stays exact. Two readings fit that:

  (a) a one-frame lag - OAM was written against the PREVIOUS camera, so the
      residual is exactly the camera's step, and only on frames it stepped.
  (b) something rate-related (an actor placed against a band's scroll).

They are told apart by CORRELATION: under (a) residual == (cam_now - cam_prev)
on every frame, including the zero-step frames. Under (b) it would not track
the step at all. The pinned actor is the control - it has no camera term, so
it must stay 0 either way.
"""
import sys

SCENE, NSLOT = 4, 8


def symbols(noi):
    out = {}
    for line in open(noi, encoding="utf-8", errors="replace"):
        p = line.split()
        if len(p) == 3 and p[0] == "DEF":
            try:
                out[p[1]] = int(p[2], 16)
            except ValueError:
                pass
    return out


rom, noi = sys.argv[1], sys.argv[2]
label = sys.argv[sys.argv.index("--label") + 1] if "--label" in sys.argv else rom
s = symbols(noi)
from pyboy import PyBoy
pb = PyBoy(rom, window="null")
OAM = 0xFE00
SC, AX = s["_vm_core_cur_scene"], s["_vm_actor_a_x"]
APIN, ABASE = s["_vm_actor_a_pin"], s["_vm_actor_a_base"]
CAM = s["_vm_player_camx16"]

u8 = lambda a: pb.memory[a]
u16 = lambda a: pb.memory[a] | (pb.memory[a + 1] << 8)


def run(n):
    for _ in range(n):
        pb.tick()


def tap(b, after=24):
    pb.button_press(b); run(8); pb.button_release(b); run(after)


run(300)
for _ in range(24):
    if u8(SC) == SCENE:
        break
    tap("start"); tap("a")

prev = None
match_step = miss = 0
by_step = {}
pin_bad = 0
for _f in range(400):
    if u8(SC) != SCENE:
        break
    cam = u16(CAM)
    step = None if prev is None else cam - prev
    for i in range(NSLOT):
        b = u8(ABASE + i)
        if b == 255:
            continue
        oy, ox = pb.memory[OAM + 4 * b], pb.memory[OAM + 4 * b + 1]
        if not (0 < oy < 160 and 0 < ox < 168):
            continue
        wx = u16(AX + 2 * i)
        if u8(APIN + i):
            if ox != wx + 8:
                pin_bad += 1
            continue
        r = ox - (wx - cam + 8)
        if step is not None:
            by_step.setdefault(step, {}).setdefault(r, 0)
            by_step[step][r] += 1
            if r == step:
                match_step += 1
            else:
                miss += 1
    prev = cam
    run(1)

print("\n=== %s: residual vs the camera's own step ===" % label)
print("%-8s %s" % ("cam step", "residuals seen (residual: samples)"))
for st in sorted(by_step):
    print("%-8s %s" % (st, "  ".join("%+d: %d" % (r, n)
                                     for r, n in sorted(by_step[st].items()))))
tot = match_step + miss
# TWO headline numbers, because they mean OPPOSITE things and it is easy to
# read the wrong one as a pass:
#   "residual == camera step" is the LAG SIGNATURE - high means the OAM was
#   written against the PREVIOUS camera. It is the diagnosis, not the goal.
#   "residual == 0" is CORRECT PLACEMENT, which is what a fix has to raise.
zero = sum(n for st in by_step for r, n in by_step[st].items() if r == 0)
print("\nlag signature (residual == camera step): %d of %d samples (%.1f%%)"
      % (match_step, tot, 100.0 * match_step / max(1, tot)))
print("CORRECT placement (residual == 0):        %d of %d samples (%.1f%%)"
      % (zero, tot, 100.0 * zero / max(1, tot)))
for st in sorted(k for k in by_step if k != 0):
    n = sum(by_step[st].values())
    z = by_step[st].get(0, 0)
    print("   on frames the camera STEPPED %+d: %d of %d correct (%.1f%%)"
          % (st, z, n, 100.0 * z / max(1, n)))
print("pinned actor off its own x on %d samples (must be 0)" % pin_bad)
