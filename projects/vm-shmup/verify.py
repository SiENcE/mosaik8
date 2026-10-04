#!/usr/bin/env python3
"""Verify vm-shmup -- the VM8 VERTICAL shmup over an ENDLESS white starfield.

PyBoy (GB): the ship renders near the BOTTOM, enemies stream DOWN from the top and
CONTINUOUSLY re-enter from the top edge (no static wave), B fires straight UP (bullets
climb and DESPAWN at the top -- no bottom-wrap), a shot spawns an EXPLOSION tile at the
impact, and the star BACKGROUND scrolls DOWN forever (shmup endless-loop, dir 3). The
HP HUD lives on the fixed GB WINDOW layer (0x9C00) -- it never scrolls, and there is no
change_scene to tear it down.

Then, across the window-LESS tilemap consoles (SMS / Game Gear / PCE) through the
libretro harness: the HUD band holds its SCREEN rows while the starfield scrolls under
it. Those consoles draw the band's text with SPRITES for exactly that reason - a
plotted label is a map cell and used to ride the level away.

    python projects/vm-shmup/verify.py
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


def _ink_rows(img, x0=8, x1=24, floor=4, band=16):
    """The rows carrying the HUD label's ink in the `x0..x1` strip (the "HP"
    cells), within the bottom `band` rows - the label's own band, so a ship or an
    enemy crossing the same columns higher up is not mistaken for it. `floor`
    filters a passing star out: a glyph row lights 4+ pixels across the two
    characters, a star lights one."""
    rgb = img.convert("RGB")
    px = rgb.load()
    w, h = rgb.size
    bg = max(rgb.getcolors(maxcolors=1 << 16) or [(0, (0, 0, 0))])[1]
    return {y for y in range(max(0, h - band), h)
            if sum(1 for x in range(x0, min(x1, w)) if px[x, y] != bg) >= floor}


def verify_pinned_hud():
    """The HUD band must hold its SCREEN position on every console, including the
    window-less tilemap ones. SMS / Game Gear / PCE plot text into the scene map,
    so a label drawn there used to scroll away with the endless starfield (the GB
    family has the window layer, the Lynx redraws the band into every present).
    Two sample frames, 180 frames apart: the label's rows must be the same."""
    import shutil
    import tempfile
    harness = os.path.join(ROOT, "emu", "libretro", "run_lynx.py")
    try:
        from PIL import Image     # noqa: F401
    except Exception:             # noqa: BLE001
        print("  skip: Pillow not installed (cross-console HUD check)")
        return
    if not os.path.isfile(harness):
        print("  skip: no libretro harness (cross-console HUD check)")
        return
    tmp = tempfile.mkdtemp(prefix="vmshmup_")
    try:
        for plat, ext, core in (("sms", "sms", "genesis_plus_gx"),
                                ("gamegear", "gg", "genesis_plus_gx"),
                                ("pce", "pce", "mednafen_pce_fast")):
            rom = os.path.join(PROJ, "build", plat, "vm-shmup." + ext)
            if not os.path.exists(rom):
                subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                                "--platform", plat, PROJ], check=True, cwd=ROOT)
            shots = []
            for frames in (120, 300):
                png = os.path.join(tmp, "%s_%d.png" % (plat, frames))
                r = subprocess.run([sys.executable, harness, rom, str(frames),
                                    "--core", core, "--png", png],
                                   capture_output=True, cwd=ROOT)
                if r.returncode or not os.path.exists(png):
                    shots = []
                    break
                from PIL import Image as _I
                shots.append(_ink_rows(_I.open(png)))
            if not shots:
                print("  skip: %s core unavailable (cross-console HUD check)" % plat)
                continue
            early, late = shots
            check(bool(early) and early == late,
                  "%s: the HUD label holds its screen rows while the level scrolls "
                  "(%s -> %s)" % (plat, sorted(early)[:1], sorted(late)[:1]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    rom = os.path.join(PROJ, "build", "gameboy", "vm-shmup.gb")
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
        return 8 < y < 152

    def scy():
        return pb.memory[0xFF42]

    def win_has_hud():
        # the HP heart gauge tiles (100/101) live in the WINDOW tilemap (0x9C00),
        # a FIXED layer -- present iff the window HUD is still up (never scrolled).
        return any(pb.memory[0x9C00 + i] in (100, 101) for i in range(32))

    def n_enemies():
        return sum(1 for s in range(8) if oam(s)[2] == 1 and vis(oam(s)[0]))

    def enemy_top():   # an enemy sprite near the top edge = a fresh spawn entering
        return any(oam(s)[2] == 1 and oam(s)[0] <= 20 for s in range(8))

    def proj(tile):
        return [(oam(s)[1], oam(s)[0]) for s in range(16, 24)
                if oam(s)[2] == tile and vis(oam(s)[0])]

    for _ in range(200):     # the VM8 boot renders after ~100 frames
        pb.tick()
    check(oam(8)[2] == 0 and vis(oam(8)[0]) and oam(8)[0] > 90,
          "the ship renders in the LOWER band (y=%d)" % oam(8)[0])
    check(pb.memory[0xFF4A] == 136 and win_has_hud(),
          "the HP HUD is on the fixed WINDOW layer (WY=%d)" % pb.memory[0xFF4A])

    scys = set()
    for _ in range(120):
        pb.tick()
        scys.add(scy())
    check(len(scys) >= 8, "the starfield SCROLLS endlessly (SCY takes %d values)" % len(scys))

    # Sustained play: sweep X to line up with descending foes while spamming B (a lone
    # scripted tap edge is flaky in PyBoy). Capture explosion tiles (a kill/hit spawns
    # tile 4), top-edge spawns, the HUD's persistence, and any bullet at a WRAPPED
    # bottom position (the old bug: a shot leaving the top reappeared at the bottom).
    booms = 0
    spawned = False
    hud_ok = True
    wrapped = False
    bullet_min_y = 200
    for f in range(900):
        (pb.button_press if f % 2 == 0 else pb.button_release)("b")
        btn = "left" if (f // 40) % 2 == 0 else "right"
        pb.button_press(btn)
        pb.tick()
        pb.button_release(btn)
        booms += len(proj(4))
        spawned = spawned or enemy_top()
        for (bx, by) in proj(2):
            bullet_min_y = min(bullet_min_y, by)
            if by > 138:            # a live bullet BELOW its spawn band = a top-exit
                wrapped = True      # that wrapped around to the bottom (the old bug)
        if f > 30 and not win_has_hud():
            hud_ok = False
    pb.button_release("b")

    check(bullet_min_y < 120, "bullets CLIMB up the screen, above the ship (min y=%d)"
          % bullet_min_y)
    check(not wrapped, "an off-screen shot is REMOVED, not wrapped to the bottom")
    check(booms >= 3, "hitting an enemy shows an EXPLOSION (tile-4 frames seen: %d)" % booms)
    check(spawned, "enemies CONTINUOUSLY re-enter from the top edge (not a static wave)")
    check(hud_ok and win_has_hud(),
          "the HUD stays on the window the whole run (never scrolls off / disappears)")
    pb.stop()

    verify_pinned_hud()

    print("\n" + "=" * 50)
    if FAILS:
        print("vm-shmup verify FAILED (%d)" % len(FAILS))
        return 1
    print("vm-shmup verify: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
