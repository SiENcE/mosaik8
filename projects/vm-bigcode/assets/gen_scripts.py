#!/usr/bin/env python3
"""Generate vm-bigcode's authored event lists + compile them to src/scripts.mos.

The point of this sample is a LARGE bytecode blob: a wander loop of many
`actor_move_to` waypoints (each ~4 bytes) so the compiled CODE comfortably
exceeds the Lynx streaming threshold (512 B). On the Lynx the interpreter's
fetch() then page-streams the blob from the cart archive; on the GB family the
same blob stays resident (byte-identical seam). The route is a deterministic
lissajous path that keeps the actor on-screen so the streamed fetch is visibly
exercised.

Run: python projects/vm-bigcode/assets/gen_scripts.py
"""
import math
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PROJ))     # the mosaik8/ checkout root

N = 1000                   # waypoints -> ~1000 * 4 = ~4 KB of move_to ops (a big
                           # blob so the Lynx page cache is a decisive net win)
CX, CY = 78, 66            # centre (GB screen 160x144, actor is 8x8)
AX, AY = 60, 46            # amplitude (keeps 18..138 x, 20..112 y)


def waypoints():
    pts = []
    for i in range(N):
        t = 2.0 * math.pi * i / N
        x = int(round(CX + AX * math.sin(3 * t)))
        y = int(round(CY + AY * math.sin(2 * t)))
        pts.append((x, y))
    return pts


def main():
    scripts_dir = os.path.join(PROJ, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)

    with open(os.path.join(scripts_dir, "main.evt.toml"), "w", encoding="utf-8") as f:
        f.write("# vm-bigcode boot: place the actor, then loop the big wander route.\n")
        f.write('[[script]]\nname = "main"\nevents = [\n')
        f.write('  { event = "actor_activate", actor = 0, tile = 0, x = 78, y = 66 },\n')
        f.write('  { event = "actor_set_speed", actor = 0, speed = 2 },\n')
        f.write('  { event = "start_thread", script = "wander" },\n')
        f.write('  { event = "stop" },\n')
        f.write("]\n")

    with open(os.path.join(scripts_dir, "wander.evt.toml"), "w", encoding="utf-8") as f:
        f.write("# A long lissajous wander route -- %d waypoints, a deliberately\n" % N)
        f.write("# LARGE bytecode blob to exercise the Lynx bytecode page cache.\n")
        f.write('[[script]]\nname = "wander"\nloop = true\nevents = [\n')
        for (x, y) in waypoints():
            f.write('  { event = "actor_move_to", actor = 0, x = %d, y = %d },\n' % (x, y))
        f.write("]\n")

    out = os.path.join(PROJ, "src", "scripts.mos")
    cmd = [sys.executable, os.path.join(ROOT, "mosaik_vm.py"), scripts_dir, "-o", out]
    subprocess.run(cmd, check=True, cwd=ROOT)
    print("wrote", out)


if __name__ == "__main__":
    main()
