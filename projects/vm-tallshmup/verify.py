#!/usr/bin/env python3
"""Verify vm-tallshmup -- a vertical shmup stage TALLER than the hardware
background (20 x 120 tiles), rows streamed by engine.scroll2d under the shmup
camera (`player.setup_tall_shmup`), with 24 placed 2x2 beacons (96 sprite
objects) handed sprite slots as they come on screen (`[build] oam_on_wake`).

PyBoy (GB):
  * the camera starts at the BOTTOM, scrolls UP and STOPS at the top (SCY 0);
  * the finish line reaches the tilemap only once the camera has crossed it
    (no cell at the start, the whole 2 x 20 at the end): the streamer wrote
    it, nothing painted it up front;
  * EVERY beacon is seen drawn as a whole 2x2 block (four objects) at its
    world position against the scrolling camera, and no beacon is ever drawn
    with the wrong number of objects while wholly on screen. 24 beacons are
    96 objects against a 40-object table: under the static layout every
    beacon past the first nine had no sprite at all;
  * no beacon object is drawn anywhere it does not belong (a range handed to
    two owners would draw one actor's cells at the other's position);
  * a DRONE that shuttles across the right edge parks and WAKES again and
    again (usually onto another range) and is drawn whole every time;
  * the ship stays on screen and is CARRIED by the scroll (the reference
    engine's shmup moves the player by every scroll step): with no d-pad
    input its screen row holds while the camera climbs; B fires a shot that
    climbs the screen.

PyBoy (GB), a copy with `oam_on_wake` OFF (the static layout, where the shot
block re-bases onto PARKED actors' entries): no shot is drawn fanned into a
parked actor's 2x2 record, and the drone - first in slot order, the shot
block borrowing its entries whenever it is off the edge - comes back drawn
whole every time.

Game Gear (Genesis Plus GX through emu/libretro/retro.py; a 28-row name table
and a 224 px vertical wrap): the finish line streams in at the top only at
the end, and the beacon placed at world y 480 is drawn ON the band at row 60
(480 px) - the sprites and the streamed background agree there too.

    python projects/vm-tallshmup/verify.py
"""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PROJ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJ, "assets"))

from gen_world import BEACONS, BAND_ROWS, W  # noqa: E402

FAILS = []
SHIP_TILE, BULLET_TILE = 0, 5      # the sheet: ship 0, beacon 1-4, bullet 5,
DRONE_TILES = (6, 7, 8, 9)          # ... drone 6-9
FINISH_TILE, BAND_TILE = 3, 2
FRAMES = 3200          # 816 px of stage at one px a GAME frame, plus a long hold at the top


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def _build(plat, ext):
    rom = os.path.join(PROJ, "build", plat, "vm-tallshmup." + ext)
    if not os.path.exists(rom):
        subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", plat, PROJ], check=True, cwd=ROOT)
    return rom


def _beacon_at(x, wy):
    """The beacon whose 16x16 box holds screen-x `x` / world-y `wy` (mod 256)."""
    for k, (bx, by) in enumerate(BEACONS):
        if bx <= x < bx + 16 and (wy - by) % 256 < 16:
            return k
    return None


def verify_gb():
    try:
        from pyboy import PyBoy
    except Exception:             # noqa: BLE001
        print("  skip: PyBoy not installed")
        return
    rom = _build("gameboy", "gb")
    pb = PyBoy(rom, window="null", sound_emulated=False)

    def sprites():
        out = []
        for i in range(40):
            y, x, t = (pb.memory[0xFE00 + i * 4 + k] for k in range(3))
            if 0 < y < 160 and 0 < x < 168:
                out.append((x - 8, y - 16, t))
        return out

    def bg_count(tid):
        return sum(1 for a in range(0x9800, 0x9C00) if pb.memory[a] == tid)

    pb.tick(150, False)              # boot + room load; the scroll has begun
    finish_start = bg_count(FINISH_TILE)
    scys, whole, partial, stray, ship_ok, peak = [], set(), [], [], True, 0
    ship_rows = set()        # the ship's screen y on every frame the camera moved
    max_shots = [0]
    # the DRONE: how often it came back on screen, and every frame it was
    # wholly on screen but not drawn as its four cells
    drone_wakes, drone_seen, drone_bad, drone_whole = 0, False, [], 0
    for f in range(150, FRAMES):
        # Keep shots in flight the whole way up: the shot block re-bases above
        # every woken beacon, and a beacon waking INTO it must keep its cells.
        if f % 12 == 0:
            pb.button_press("b")
        elif f % 12 == 2:
            pb.button_release("b")
        pb.tick(1, False)
        scy = pb.memory[0xFF42]
        scys.append(scy)
        spr = sprites()
        peak = max(peak, len(spr))
        ships = [(x, y) for x, y, t in spr if t == SHIP_TILE]
        if not ships or not (0 <= ships[0][1] <= 136):
            ship_ok = False
        elif len(scys) > 1 and scys[-1] != scys[-2]:
            ship_rows.add(ships[0][1])
        count = {}
        drone = [(x, y, t) for x, y, t in spr if t in DRONE_TILES]
        if drone and not drone_seen:
            drone_wakes += 1
        drone_seen = bool(drone)
        if drone and min(x for x, _, _ in drone) <= 160 - 16 - 8:
            if sorted(t for _, _, t in drone) == list(DRONE_TILES):
                drone_whole += 1
            else:
                drone_bad.append((f, sorted(t for _, _, t in drone)))
        shots = sum(1 for x, y, t in spr if t == BULLET_TILE)
        max_shots[0] = max(max_shots[0], shots)
        for x, y, t in spr:
            if t in (SHIP_TILE, BULLET_TILE) or t in DRONE_TILES or y >= 136:
                continue
            k = _beacon_at(x, (y + scy) % 256)
            if k is None:
                stray.append((f, x, y, t))
                continue
            count[k] = count.get(k, 0) + 1
        for k, n in count.items():
            sy = (BEACONS[k][1] - scy) % 256     # its screen y, when on screen
            # wholly on screen AND above the ship's row, which the walk skips
            inside = 0 <= sy <= 136 - 16
            if n == 4:
                whole.add(k)
            elif inside:
                partial.append((f, k, n))
    finish_end = bg_count(FINISH_TILE)
    check(scys[0] != 0 and scys[-60:] == [0] * 60,
          "the camera scrolls from the bottom and stops at the top (SCY %d -> 0, "
          "held for the last 60 frames)" % scys[0])
    steps = sum(1 for a, b in zip(scys, scys[1:]) if a != b)
    check(steps > 700, "the scroll moved on %d frames (816 px of stage)" % steps)
    check(finish_start == 0 and finish_end == 2 * W,
          "the finish line streamed in only at the top (%d cells at the start, "
          "%d at the end; want 0 and %d)" % (finish_start, finish_end, 2 * W))
    check(whole == set(range(len(BEACONS))),
          "every one of the %d placed beacons (%d sprite objects) was drawn as "
          "a whole 2x2 block at its world position (%d were; missing %s)"
          % (len(BEACONS), 4 * len(BEACONS), len(whole),
             sorted(set(range(len(BEACONS))) - whole)[:8]))
    check(not partial,
          "no beacon wholly on screen was drawn with a missing or extra object "
          "(%d frames off: %s)" % (len(partial), partial[:3]))
    check(not stray,
          "no beacon object was drawn where no beacon is (%d: %s)"
          % (len(stray), stray[:3]))
    check(peak <= 40, "the sprite objects in use peaked at %d (the table has 40)"
          % peak)
    check(max_shots[0] >= 2, "shots were in flight while the beacons woke (up to "
                             "%d at once)" % max_shots[0])
    check(drone_wakes >= 3 and drone_whole > 50 and not drone_bad,
          "the drone that shuttles off the right edge came back %d times and "
          "was drawn whole every time it was on screen (%d whole frames, %d "
          "bad: %s)" % (drone_wakes, drone_whole, len(drone_bad), drone_bad[:3]))
    check(ship_ok, "the ship stays on screen")
    check(len(ship_rows) == 1,
          "the scroll carries the ship: its screen y held at %s on every frame "
          "the camera moved (one row wanted)" % sorted(ship_rows)[:6])
    check(bg_count(BAND_TILE) >= W,
          "a band row (%s) streamed into the tilemap" % (BAND_ROWS,))

    # B fires: a shot appears above the ship and climbs (after the loop's own
    # shots have run out their 40-frame life).
    pb.tick(90, False)
    pb.button_press("b")
    pb.tick(2, False)
    pb.button_release("b")
    ys = []
    for _ in range(10):
        pb.tick(1, False)
        ys += [y for x, y, t in sprites() if t == BULLET_TILE]
    check(len(ys) >= 2 and ys[-1] < ys[0],
          "B fires a shot that climbs the screen (%s)" % ys[:6])
    pb.stop(save=False)


def _static_copy(tmp):
    """A copy of the project with `oam_on_wake` OFF: the static layout, where
    the projectile block re-bases onto PARKED actors' entries (P1). The drone
    goes first (slot 0, a static range at the bottom of the table) and the
    beacons up by it are dropped, so the beacons holding static ranges have
    parked for good when it shuttles and the shot block borrows the drone's
    own entries whenever it is off the edge. Scenes, rooms and glue are
    regenerated (a build does not)."""
    import re
    import shutil
    dst = os.path.join(tmp, "vm-tallshmup")
    shutil.copytree(PROJ, dst, ignore=shutil.ignore_patterns("build"))
    mt = os.path.join(dst, "mosaik.toml")
    text = open(mt, encoding="utf-8").read()
    assert "oam_on_wake = true" in text
    open(mt, "w", encoding="utf-8").write(
        text.replace("oam_on_wake = true", "oam_on_wake = false"))
    wp = os.path.join(dst, "world.toml")
    w = open(wp, encoding="utf-8").read()
    drone = re.search(r'\[\[scene\.object\]\]\nkind = "drone"\n(?:[a-z_]+ = .*\n?)+', w)
    w = w[:drone.start()] + w[drone.end():]
    k = w.index('[[scene.object]]\nkind = "beacon"')
    w = w[:k] + drone.group(0).rstrip("\n") + "\n" + w[k:]
    w = re.sub(r'\[\[scene\.object\]\]\nkind = "beacon"\nid = \d+\nx = \d+\ny = (\d+)\n',
               lambda m: "" if int(m.group(1)) < 300 else m.group(0), w)
    open(wp, "w", encoding="utf-8").write(w)
    import mosaik_scenes
    import mosaik_vm
    world, base = mosaik_scenes.load_world(wp)
    open(os.path.join(dst, "src", "scenes.mos"), "w", encoding="utf-8").write(
        mosaik_scenes.core.transpile(world, base))
    mosaik_vm.generate_rooms(dst)
    mosaik_vm.generate_glue(dst)
    subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                    "--platform", "gameboy", dst], check=True, cwd=ROOT,
                   stdout=subprocess.DEVNULL)
    return os.path.join(dst, "build", "gameboy", "vm-tallshmup.gb")


def verify_gb_static():
    """The STATIC layout (oam_on_wake off): the projectile block borrows the
    entries of parked actors, whose park latched their 2x2 metasprite record
    there. A shot drawn with set_tile + move on that record re-tiled all four
    children and fanned them out (the bullet as a 2x2 block), and an actor
    waking on entries the block had used kept the shot's record and tiles
    (open item 1.12b). Pre-fix this arm measured 2,146 fanned-shot frames and
    a drone never once drawn whole (2,213 bad frames)."""
    try:
        from pyboy import PyBoy
    except Exception:             # noqa: BLE001
        print("  skip: PyBoy not installed (static-layout arm)")
        return
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        rom = _static_copy(tmp)
        pb = PyBoy(rom, window="null", sound_emulated=False)
        pb.tick(150, False)
        fanned, max_shots = [], 0
        wakes, seen, whole, bad = 0, False, 0, []
        for f in range(150, FRAMES):
            if f % 12 == 0:
                pb.button_press("b")
            elif f % 12 == 2:
                pb.button_release("b")
            pb.tick(1, False)
            spr = []
            for i in range(40):
                y, x, t = (pb.memory[0xFE00 + i * 4 + k] for k in range(3))
                if 0 < y < 160 and 0 < x < 168:
                    spr.append((x - 8, y - 16, t))
            shots = [(x, y) for x, y, t in spr if t == BULLET_TILE]
            max_shots = max(max_shots, len(shots))
            # an object drawn right of / below / diagonal to a shot by exactly
            # one cell is a fan the shot dragged along (shots fly in one column
            # and are never 8 px apart)
            for sx, sy in shots:
                if any((x, y) in ((sx + 8, sy), (sx, sy + 8), (sx + 8, sy + 8))
                       for x, y, _ in spr):
                    fanned.append(f)
                    break
            drone = [(x, y, t) for x, y, t in spr if t in DRONE_TILES]
            if drone and not seen:
                wakes += 1
            seen = bool(drone)
            if drone and min(x for x, _, _ in drone) <= 160 - 16 - 8:
                if sorted(t for _, _, t in drone) == list(DRONE_TILES):
                    whole += 1
                else:
                    bad.append((f, sorted(t for _, _, t in drone)))
        pb.stop(save=False)
    check(max_shots >= 2 and not fanned,
          "static layout: no shot drew a parked actor's metasprite record "
          "(%d frames with a shot fanned into a block, first %s; up to %d "
          "shots in flight)" % (len(fanned), fanned[:3], max_shots))
    check(wakes >= 3 and whole > 300 and not bad,
          "static layout: the drone the shot block borrows from came back %d "
          "times and was drawn whole every time (%d whole frames, %d bad: %s)"
          % (wakes, whole, len(bad), bad[:3]))


def verify_gg():
    cores = glob.glob(os.path.join(ROOT, "emu", "libretro", "genesis_plus_gx_libretro.*"))
    try:
        from PIL import Image     # noqa: F401
    except Exception:             # noqa: BLE001
        print("  skip: Pillow not installed (Game Gear check)")
        return
    if not cores:
        print("  skip: no Genesis Plus GX core in emu/libretro/ (Game Gear check)")
        return
    sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
    from retro import Core
    rom = _build("gamegear", "gg")
    core = Core(cores[0], rom)
    early_finish, late_finish, on_band, bands_seen = None, None, 0, 0
    bx = BEACONS[12][0]

    def finish_top(img):
        px = img.load()
        row = [px[x, 2] for x in range(img.size[0])]
        return sum(1 for a, b in zip(row, row[1:]) if a != b) > 20

    for f in range(0, FRAMES, 10):
        core.run(10)
        img = core.image().convert("RGB")
        if f == 200:
            early_finish = finish_top(img)
        px = img.load()
        w, h = img.size
        paper = px[2, h - 1]
        # a BAND row: the same non-paper colour right across the left 100 px
        band = [y for y in range(h)
                if px[2, y] != paper
                and all(px[x, y] == px[2, y] for x in range(0, 100))]
        if len(band) >= 8 and 8 <= band[0] and band[-1] < h - 8:
            bands_seen += 1
            hit = [y for y in band
                   if any(px[x, y] != px[2, y] for x in range(bx, bx + 16))]
            if len(hit) >= 6:
                on_band += 1
        late_finish = finish_top(img)
    core.close()
    check(early_finish is False and late_finish is True,
          "Game Gear: the finish line streamed in at the top only at the end "
          "(frame 200: %s, end: %s)" % (early_finish, late_finish))
    check(on_band >= 3,
          "Game Gear: the beacon at world y 480 is drawn ON the band at row 60 "
          "(%d of %d sampled band frames)" % (on_band, bands_seen))


def main():
    print("vm-tallshmup -- a tall shmup stage, sprite slots handed out on wake")
    verify_gb()
    verify_gb_static()
    verify_gg()
    print("FAILED: %d" % len(FAILS) if FAILS else "all checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
