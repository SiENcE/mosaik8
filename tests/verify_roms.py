#!/usr/bin/env python3
"""Optional behavioral ROM checks (not part of the unit suite).

Building a ROM only proves it links; this script drives emulators to check the
flagship sprite samples actually *run*:

- Game Boy: PyBoy (headless) asserts the bounce ball sweeps the whole
  screen-pixel play field, pong's paddle stack renders at the right spot, and
  the shmup project (starfall) plays: the ship steers, A fires a rising
  bullet, and enemies descend.
- Lynx: `--lynx` drives the starfall ROM through the libretro harness
  (emu/libretro/run_lynx.py) twice and screen-diffs the frames to confirm the
  ship moves under input.

Run after `python tests/run_all.py --samples` (which also builds the shmup
project):

    python tests/verify_roms.py            # PyBoy checks (needs: pip install pyboy)
    python tests/verify_roms.py --lynx     # also screen-diff the Lynx shmup
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "samples", "build")
SHMUP_BUILD = os.path.join(ROOT, "projects", "shmup", "build")


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def pyboy_checks():
    from pyboy import PyBoy

    ok = True

    # bounce: sprite 0 visible and sweeping the whole play field. The
    # screen-pixel contract on GB means OAM = screen + (8,16); bounds
    # 4..SCREEN-12 => OAM x in [12,156], y in [20,148].
    pb = PyBoy(os.path.join(BUILD, "gameboy", "bounce.gb"), window="null")
    xs, ys = [], []
    for _ in range(1500):
        pb.tick()
        ys.append(pb.memory[0xFE00])
        xs.append(pb.memory[0xFE01])
    pb.stop()
    ok &= check("bounce ball sweeps the play field",
                max(xs) >= 154 and max(ys) >= 146
                and min(x for x in xs if x) <= 14)

    # pong: ball moves; paddle segments stacked 8px apart at screen x=16
    # (OAM x 24).
    pb = PyBoy(os.path.join(BUILD, "gameboy", "pong.gb"), window="null")
    ball_x = set()
    for _ in range(600):
        pb.tick()
        ball_x.add(pb.memory[0xFE01])
    paddle = [(pb.memory[0xFE00 + 4 * i], pb.memory[0xFE01 + 4 * i])
              for i in (1, 2, 3)]
    pb.stop()
    ok &= check("pong ball moves and paddle stack renders at x=16",
                len(ball_x) > 20 and all(x == 24 for _, x in paddle)
                and paddle[1][0] == paddle[0][0] + 8
                and paddle[2][0] == paddle[1][0] + 8)

    # starfall (the asset-pipeline shmup project): ship steers right, A
    # fires a bullet that rises, enemies descend. OAM slots: 0 = player,
    # 1..2 = bullets (parked at y=160 when dead), 3..6 = enemies.
    rom = os.path.join(SHMUP_BUILD, "gameboy", "starfall.gb")
    if not os.path.isfile(rom):
        return ok & check("starfall.gb (missing — run "
                          "`python mosaik8.py build projects/shmup` first)", False)
    pb = PyBoy(rom, window="null")
    for _ in range(120):
        pb.tick()
    ship_x0 = pb.memory[0xFE01]
    enemy_y0 = [pb.memory[0xFE00 + 4 * i] for i in (3, 4, 5, 6)]
    pb.button_press("right")
    for _ in range(30):
        pb.tick()
    pb.button_release("right")
    ship_x1 = pb.memory[0xFE01]
    ok &= check("starfall ship steers right", ship_x1 >= ship_x0 + 20)

    pb.button_press("a")
    for _ in range(5):
        pb.tick()
    pb.button_release("a")
    bullet_y0 = pb.memory[0xFE00 + 4]
    for _ in range(15):
        pb.tick()
    bullet_y1 = pb.memory[0xFE00 + 4]
    ok &= check("starfall A fires a rising bullet",
                bullet_y0 < 160 and (bullet_y1 < bullet_y0 or bullet_y1 == 160))

    enemy_y1 = [pb.memory[0xFE00 + 4 * i] for i in (3, 4, 5, 6)]
    ok &= check("starfall enemies move",
                enemy_y0 != enemy_y1 and any(y > 16 for y in enemy_y1))
    pb.stop()
    return ok


def lynx_shmup_check():
    """Screen-diff the Lynx starfall ROM: holding LEFT must move the ship."""
    from PIL import Image

    rom = os.path.join(SHMUP_BUILD, "lynx", "starfall.lnx")
    if not os.path.isfile(rom):
        return check("starfall.lnx (missing — run "
                     "`python mosaik8.py build projects/shmup` first)", False)

    def run(png, *press):
        cmd = [sys.executable, os.path.join(ROOT, "emu", "libretro", "run_lynx.py"),
               rom, "220", "--png", png] + [a for p in press
                                            for a in ("--press", p)]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        return proc.returncode == 0 and os.path.isfile(png)

    def ship_min_x(png):
        """Leftmost lit pixel in the ship's band (the bottom rows)."""
        img = Image.open(png).convert("L")
        w, h = img.size
        px = img.load()
        cols = [x for x in range(w) for y in range(h - 14, h)
                if px[x, y] > 80]
        return min(cols) if cols else None

    base_png = os.path.join(SHMUP_BUILD, "lynx", "_verify_base.png")
    left_png = os.path.join(SHMUP_BUILD, "lynx", "_verify_left.png")
    if not (run(base_png) and run(left_png, "LEFT@100-220")):
        return check("starfall.lnx runs in the libretro harness", False)
    base_x, left_x = ship_min_x(base_png), ship_min_x(left_png)
    return check("starfall ship steers left on the Lynx (%s -> %s)"
                 % (base_x, left_x),
                 base_x is not None and left_x is not None
                 and left_x <= base_x - 20)


def main():
    print("ROM behavioral checks")
    print("=" * 50)
    ok = pyboy_checks()
    if "--lynx" in sys.argv:
        ok &= lynx_shmup_check()
    print()
    print("All ROM checks passed" if ok else "ROM checks FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
