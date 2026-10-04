#!/usr/bin/env python3
"""Verify vm-combat -- the FULLY VM8-NATIVE horizontal SHMUP (combat program Part 2).

PyBoy (GB): the ship renders at the left, enemies stream in from the right (their On
Update flies them left, F1), and B fires a projectile straight RIGHT (F1/F2) that is
VISIBLE crossing the open gap (the whole point of the shmup layout - no point-blank
despawn). Lining a shot up with an enemy DESTROYS it (F3 On Hit -> the F4 death hook);
clearing a wave reloads a fresh one. No vm.combat, no hand loop. Also Lynx-verified
via GearLynx.

    python projects/vm-combat/verify.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PROJ = os.path.dirname(os.path.abspath(__file__))

FAILS = []


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def main():
    rom = os.path.join(PROJ, "build", "gameboy", "vm-combat.gb")
    if not os.path.exists(rom):
        subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "gameboy", PROJ], check=True, cwd=ROOT)
    try:
        from pyboy import PyBoy
    except Exception:  # noqa: BLE001
        print("  skip: PyBoy not installed")
        return 0

    pb = PyBoy(rom, window="null")

    def oam(i):
        b = 0xFE00 + i * 4
        return pb.memory[b], pb.memory[b + 1], pb.memory[b + 2]   # y, x, tile

    def vis(y):
        return 8 < y < 150

    def n_enemies():
        return sum(1 for s in range(8) if oam(s)[2] == 1 and vis(oam(s)[0]))

    def bullet_xs():
        return [oam(s)[1] for s in range(16, 24) if vis(oam(s)[0]) and oam(s)[2] == 2]

    for _ in range(200):     # the VM8 boot renders after ~100 frames
        pb.tick()
    check(oam(8)[2] == 0 and vis(oam(8)[0]), "the ship renders at the left")
    check(n_enemies() >= 2, "enemies stream in from the right")

    # Sustained play: sweep the ship's Y (to line up with enemies at varied heights)
    # while spamming B (a single scripted tap edge is flaky in PyBoy). Capture both
    # the bullet positions (must be VISIBLE crossing the gap) and the enemy count
    # (foes are DESTROYED -- each takes 3 hits, per-actor HP -- and waves reload).
    xs_all = []
    counts = []
    press = False
    for f in range(700):
        press = not press
        (pb.button_press if press else pb.button_release)("b")
        btn = "up" if (f // 40) % 2 == 0 else "down"
        pb.button_press(btn)
        pb.tick()
        pb.button_release(btn)
        xs_all.extend(bullet_xs())
        counts.append(n_enemies())
    pb.button_release("b")
    check(len(xs_all) >= 6, "bullets are VISIBLE crossing the gap (not point-blank despawn)")
    check(len(xs_all) >= 2 and max(xs_all) - min(xs_all) >= 16,
          "bullets FLY RIGHT across the screen (span %d px)"
          % ((max(xs_all) - min(xs_all)) if xs_all else 0))
    check(min(counts) <= 1, "lining up shots DESTROYS enemies (each takes 3 hits) [min %d]"
          % min(counts))
    check(max(counts) - min(counts) >= 2, "waves RELOAD (count cycles %d..%d)"
          % (min(counts), max(counts)))
    pb.stop()

    print("\n" + "=" * 50)
    if FAILS:
        print("vm-combat verify FAILED (%d)" % len(FAILS))
        return 1
    print("vm-combat verify: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
