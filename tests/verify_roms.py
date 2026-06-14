#!/usr/bin/env python3
"""Optional behavioral ROM checks (not part of the unit suite).

Building a ROM only proves it links; this script drives emulators to check the
flagship sprite samples actually *run*:

- Game Boy: PyBoy (headless) asserts the bounce ball sweeps the whole
  screen-pixel play field, pong's paddle stack renders at the right spot,
  the banked sample's bank(2)/bank(3) functions return the same values the
  home bank prints as literals (MBC5 banked calls really run),
  the shmup project (starfall) plays: the ship steers, A fires a rising
  bullet, and enemies descend — the background project scrolls its
  tilemap (SCX advances) with the walker sprite centred in OAM — and the
  colors sample (graphics.palette) renders real RGB on the .gbc build
  (with A cycling the sprite palette) while the .gb build quantizes to
  the 4 DMG greys.
- Lynx: `--lynx` drives the starfall ROM through the libretro harness
  (emu/libretro/run_lynx.py) twice and screen-diffs the frames to confirm the
  ship moves under input, then screen-diffs the background project (the
  Suzy composite-background engine) scrolling under RIGHT, and checks the
  colors sample paints the Mikey pen partition (blue backdrop + warm/green
  sprite ramps).
- PC Engine: `--pce` runs the bounce ROM on the Beetle PCE Fast core
  (installed by setup_tools.py) and checks the VDC sprite engine renders the
  8x8 ball and that it moves between frames, then screen-diffs the
  background project (the VDC BAT engine) scrolling under RIGHT, and checks
  the colors sample's VCE writes (blue backdrop, white text ink, colored
  sprite ramps).
- SMS / Game Gear / NES: `--sms` / `--gg` / `--nes` screen-diff the background
  project on the Genesis Plus GX (SMS/GG) and FCEUmm (NES) cores (installed by
  setup_tools.py): the scene renders, scrolls under RIGHT, and keeps distinct
  green hues (the dark tree tops must not quantize to grey). These consoles
  have one sprite palette / coarse or no per-tile palettes, so the full
  colors/colorlab palette checks (which need 4 sprite palettes) don't apply.

Run after `python tests/run_all.py --samples` (which also builds the shmup
project):

    python tests/verify_roms.py            # PyBoy checks (needs: pip install pyboy)
    python tests/verify_roms.py --lynx     # also screen-diff the Lynx shmup
    python tests/verify_roms.py --pce      # also check the PCE sprite engine
    python tests/verify_roms.py --sms --gg --nes  # the GBDK colour consoles
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(ROOT, "samples", "build")
SHMUP_BUILD = os.path.join(ROOT, "projects", "shmup", "build")
BKG_BUILD = os.path.join(ROOT, "projects", "background", "build")
COLORLAB_BUILD = os.path.join(ROOT, "projects", "colorlab", "build")
ZELDA_BUILD = os.path.join(ROOT, "projects", "zelda-slice", "build")
BOXP_BUILD = os.path.join(ROOT, "projects", "box-pusher", "build")
SCENE_BUILD = os.path.join(ROOT, "projects", "scene-demo", "build")


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

    # banked: ROM banking (bank(N)). The sample prints each value twice --
    # once as a home-bank literal, once computed in ROM bank 2/3 through the
    # sdcc banked-call trampoline -- so the tilemap rows must hold identical
    # tiles (no font knowledge needed), and the header must say MBC5/64 KB.
    rom = os.path.join(BUILD, "gameboy", "banked.gb")
    with open(rom, "rb") as f:
        header = f.read(0x150)
    pb = PyBoy(rom, window="null")
    for _ in range(120):
        pb.tick()

    def bg_row(y):
        return [pb.memory[0x9800 + 32 * y + x] for x in range(12, 20)]

    rows = {y: bg_row(y) for y in (3, 4, 6, 7)}
    pb.stop()
    ok &= check("banked: bank-2 value matches the home-bank literal",
                rows[3] == rows[4] and any(rows[3]))
    ok &= check("banked: bank-3 value matches the home-bank literal",
                rows[6] == rows[7] and any(rows[6]))
    ok &= check("banked: cartridge header is MBC5 / 64 KB",
                header[0x147] == 0x19 and header[0x148] == 0x01
                and os.path.getsize(rom) == 65536)

    # colors (graphics.palette): the .gbc build must show real RGB (the dark
    # blue bkg paper and both sprite ramps) and A must cycle sprite 0's
    # palette slot; the .gb build of the same source must quantize every
    # color to the 4 DMG greys.
    rom = os.path.join(BUILD, "gameboy_color", "colors.gbc")
    if os.path.isfile(rom):
        pb = PyBoy(rom, window="null")
        for _ in range(120):
            pb.tick()
        img = pb.screen.image.convert("RGB")
        colors = [c for _n, c in img.getcolors(maxcolors=4096)]
        non_grey = [c for c in colors if not (c[0] == c[1] == c[2])]
        before = set(img.getdata())
        pb.button_press("a")
        for _ in range(20):
            pb.tick()
        pb.button_release("a")
        pb.tick()
        after = set(pb.screen.image.convert("RGB").getdata())
        pb.stop()
        ok &= check("colors.gbc renders RGB palettes (%d non-grey colors)"
                    % len(non_grey), len(non_grey) >= 4)
        ok &= check("colors.gbc A cycles the sprite palette", before != after)

        pb = PyBoy(os.path.join(BUILD, "gameboy", "colors.gb"), window="null")
        for _ in range(120):
            pb.tick()
        colors = [c for _n, c in
                  pb.screen.image.convert("RGB").getcolors(maxcolors=4096)]
        pb.stop()
        ok &= check("colors.gb quantizes to the DMG greys",
                    len(colors) >= 2
                    and all(c[0] == c[1] == c[2] for c in colors))
    else:
        ok &= check("colors.gbc (missing — run `python mosaik8.py build "
                    "--platform gbc samples/colors.mos` first)", False)

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

    # background project: the tilemap scrolls (SCX advances while RIGHT is
    # held) and the walker sprite sits at the screen centre (OAM = screen
    # pixel (76,68) + (8,16)).
    rom = os.path.join(BKG_BUILD, "gameboy", "background.gb")
    if not os.path.isfile(rom):
        return ok & check("background.gb (missing — run "
                          "`python mosaik8.py build projects/background` first)",
                          False)
    pb = PyBoy(rom, window="null")
    for _ in range(120):
        pb.tick()
    scx0 = pb.memory[0xFF43]
    walker = (pb.memory[0xFE00], pb.memory[0xFE01])
    pb.button_press("right")
    for _ in range(60):
        pb.tick()
    pb.button_release("right")
    scx1 = pb.memory[0xFF43]
    pb.stop()
    ok &= check("background scrolls right (SCX %d -> %d)" % (scx0, scx1),
                (scx1 - scx0) % 256 >= 50)
    ok &= check("background walker sprite centred", walker == (84, 84))

    # colorlab (graphics.palette showcase): the .gbc build must paint a rich
    # colour grid (per-tile bkg palettes × sprite palettes — many non-grey
    # colours) and A must swap the colour theme; the .gb build degrades to
    # greys.
    rom = os.path.join(COLORLAB_BUILD, "gameboy_color", "colorlab.gbc")
    if os.path.isfile(rom):
        pb = PyBoy(rom, window="null")
        for _ in range(120):
            pb.tick()
        img = pb.screen.image.convert("RGB")
        non_grey = [c for _n, c in img.getcolors(maxcolors=4096)
                    if not (c[0] == c[1] == c[2])]
        before = set(img.getdata())
        pb.button_press("a")
        for _ in range(6):
            pb.tick()
        pb.button_release("a")
        for _ in range(4):
            pb.tick()
        after = set(pb.screen.image.convert("RGB").getdata())
        pb.stop()
        ok &= check("colorlab.gbc paints a colour grid (%d non-grey colors)"
                    % len(non_grey), len(non_grey) >= 12)
        ok &= check("colorlab.gbc A swaps the colour theme", before != after)

        pb = PyBoy(os.path.join(COLORLAB_BUILD, "gameboy", "colorlab.gb"),
                   window="null")
        for _ in range(120):
            pb.tick()
        greys = all(c[0] == c[1] == c[2] for _n, c in
                    pb.screen.image.convert("RGB").getcolors(maxcolors=4096))
        pb.stop()
        ok &= check("colorlab.gb degrades to greys", greys)
    else:
        ok &= check("colorlab.gbc (missing — run `python mosaik8.py build "
                    "projects/colorlab` first)", False)

    # zelda-slice (the game-framework slice): walking through room 0's south
    # door swaps the scene's BG tilemap and spawns the room-2 enemy pool; the
    # sword (B) then clears the arena (live OAM falls as enemies die).
    rom = os.path.join(ZELDA_BUILD, "gameboy", "zelda-slice.gb")
    if os.path.isfile(rom):
        pb = PyBoy(rom, window="null")

        def zt(n):
            for _ in range(n):
                pb.tick()

        def zhold(b, n):
            pb.button_press(b)
            zt(n)
            pb.button_release(b)
            zt(2)

        def zlive():
            return sum(1 for s in range(40) if 0 < pb.memory[0xFE00 + 4 * s] < 152)

        def zmapsum():
            return sum(pb.memory[0x9800 + i] for i in range(1024))

        zt(150)
        room0 = zmapsum()
        zhold("right", 42)     # align to the south-door columns
        zhold("down", 150)     # walk through the south door into room 2
        arena = zlive()
        ok &= check("zelda-slice enters the room-2 arena (scene changes)",
                    zmapsum() != room0)
        ok &= check("zelda-slice arena spawns the enemy pool", arena >= 18)
        for _ in range(8):
            pb.button_press("b")
            pb.button_press("down")
            zt(20)
            pb.button_release("b")
            pb.button_release("down")
            zt(4)
        ok &= check("zelda-slice sword clears the arena (OAM drops)",
                    zlive() <= arena - 8)
        pb.stop()
    else:
        ok &= check("zelda-slice.gb (missing — run `python mosaik8.py build "
                    "projects/zelda-slice` first)", False)

    # box-pusher (game-framework Phase 3): a grid Sokoban composing game.pad
    # (d-pad EDGE detection) + game.camera, a genre unlike the slice. Three DOWN
    # taps push crate 0 (cell 3,6) onto its goal (3,7) -- its sprite tile flips
    # to the on-goal frame -- and the follow camera scrolls. (DOWN is used
    # because PyBoy drives it cleanly; the cc65/Lynx build is the cross-check
    # for the edge logic on the other directions.)
    rom = os.path.join(BOXP_BUILD, "gameboy", "box-pusher.gb")
    if os.path.isfile(rom):
        pb = PyBoy(rom, window="null")
        for _ in range(90):
            pb.tick()
        tile0 = pb.memory[0xFE06]    # crate 0 = sprite slot 1, OAM tile byte
        scy0 = pb.memory[0xFF42]
        for _ in range(3):           # push crate 0 down onto its goal
            pb.button_press("down")
            pb.tick()
            pb.button_release("down")
            for _ in range(10):
                pb.tick()
        crate_done = pb.memory[0xFE06]
        scy1 = pb.memory[0xFF42]
        pb.stop()
        ok &= check("box-pusher: crate pushed onto its goal (tile flips)",
                    tile0 != 2 and crate_done == 2)
        ok &= check("box-pusher: follow camera scrolls (SCY advances)",
                    scy1 > scy0)
    else:
        ok &= check("box-pusher.gb (missing — run `python mosaik8.py build "
                    "projects/box-pusher` first)", False)

    # scene-demo (game-framework Phase 4, Layer 3): a two-room world whose maps
    # and door edges are DATA (world.toml transpiled to the scenes module).
    # Walking south through the field's doorway repaints the BG tilemap to the
    # cave; walking back north through the cave's door restores the field map.
    rom = os.path.join(SCENE_BUILD, "gameboy", "scene-demo.gb")
    if os.path.isfile(rom):
        pb = PyBoy(rom, window="null")

        def smap():
            return sum(pb.memory[0x9800 + i] for i in range(1024))

        for _ in range(70):
            pb.tick()
        field = smap()
        pb.button_press("down")
        for _ in range(160):
            pb.tick()
        pb.button_release("down")
        for _ in range(10):
            pb.tick()
        cave = smap()
        pb.button_press("up")
        for _ in range(170):
            pb.tick()
        pb.button_release("up")
        for _ in range(10):
            pb.tick()
        back = smap()
        pb.stop()
        ok &= check("scene-demo: south door switches room (BG map changes)",
                    cave != field)
        ok &= check("scene-demo: north door returns to the field room",
                    back == field)
    else:
        ok &= check("scene-demo.gb (missing — run `python mosaik8.py build "
                    "projects/scene-demo` first)", False)
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


def bkg_scroll_check(platform, core=None):
    """Screen-diff the background project ROM on a cc65 console: the tilemap
    scene must render (several distinct shades, not a blank frame) and
    holding RIGHT must scroll it (large screen diff vs the idle frame)."""
    from PIL import Image

    ext = {"lynx": "lnx", "pce": "pce",
           "sms": "sms", "gamegear": "gg", "nes": "nes"}[platform]
    rom = os.path.join(BKG_BUILD, platform, "background." + ext)
    if not os.path.isfile(rom):
        return check("background.%s (missing — run `python mosaik8.py build "
                     "projects/background` first)" % ext, False)

    def run(png, frames, *press):
        cmd = [sys.executable, os.path.join(ROOT, "emu", "libretro", "run_lynx.py"),
               rom, str(frames), "--png", png]
        if core:
            cmd += ["--core", core]
        cmd += [a for p in press for a in ("--press", p)]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        return proc.returncode == 0 and os.path.isfile(png)

    idle_png = os.path.join(BKG_BUILD, platform, "_verify_idle.png")
    right_png = os.path.join(BKG_BUILD, platform, "_verify_right.png")
    if not (run(idle_png, 300) and run(right_png, 360, "RIGHT@120-360")):
        return check("background.%s runs in the libretro harness" % ext, False)
    idle = Image.open(idle_png).convert("RGB")
    right = Image.open(right_png).convert("RGB")
    shades = len(set(idle.getdata()))
    diff = sum(1 for a, b in zip(idle.getdata(), right.getdata()) if a != b)
    total = idle.size[0] * idle.size[1]
    ok = check("%s background scene renders (%d shades)" % (platform, shades),
               shades >= 3)
    ok &= check("%s background scrolls under RIGHT (%d/%d px changed)"
                % (platform, diff, total), diff > total // 10)
    # The daylight scene is a green-ramp palette (grass + tree tops). A muddy
    # dark green collapses to grey under 2-bit (SMS/GG) / NES-master-palette
    # quantization, so assert at least two distinct green hues survive (proof
    # the tree tops are not grey -- the projects/background palette fix).
    greens = {c for _n, c in idle.getcolors(maxcolors=65536)
              if c[1] > c[0] + 24 and c[1] > c[2] + 24}
    ok &= check("%s background keeps distinct green hues (%d)"
                % (platform, len(greens)), len(greens) >= 2)
    return ok


def colors_check(platform, core=None):
    """The colors sample on a cc65 console must show non-grey colors: the
    dark blue backdrop (graphics.palette's bkg slot 0 color 0) plus warm and
    green sprite ramps — proof the Mikey pen partition / PCE VCE writes
    landed. (All other cc65 content is greyscale, so any strong hue here
    comes from graphics.palette.)"""
    from PIL import Image

    ext = {"lynx": "lnx", "pce": "pce"}[platform]
    rom = os.path.join(BUILD, platform, "colors." + ext)
    if not os.path.isfile(rom):
        return check("colors.%s (missing — run `python mosaik8.py build "
                     "--platform %s samples/colors.mos` first)"
                     % (ext, platform), False)
    png = os.path.join(BUILD, platform, "_verify_colors.png")
    cmd = [sys.executable, os.path.join(ROOT, "emu", "libretro", "run_lynx.py"),
           rom, "300", "--png", png]
    if core:
        cmd += ["--core", core]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not os.path.isfile(png):
        return check("colors.%s runs in the libretro harness" % ext, False)
    colors = {c for _n, c in
              Image.open(png).convert("RGB").getcolors(maxcolors=65536)}
    blue = [c for c in colors if c[2] > c[0] + 30 and c[2] > c[1] + 30]
    warm = [c for c in colors if c[0] > c[2] + 60]
    green = [c for c in colors if c[1] > c[0] + 60 and c[1] > c[2] + 60]
    return check("%s colors sample shows the palette hues "
                 "(blue %d, warm %d, green %d)"
                 % (platform, len(blue), len(warm), len(green)),
                 bool(blue and warm and green))


def colorlab_check(platform, core=None):
    """The colorlab showcase on a cc65 console must paint several strong,
    distinct hues (the colour grid + the four sprite-palette gems) — proof
    graphics.palette drives the hardware, not just greyscale."""
    from PIL import Image

    ext = {"lynx": "lnx", "pce": "pce"}[platform]
    rom = os.path.join(COLORLAB_BUILD, platform, "colorlab." + ext)
    if not os.path.isfile(rom):
        return check("colorlab.%s (missing — run `python mosaik8.py build "
                     "projects/colorlab` first)" % ext, False)
    png = os.path.join(COLORLAB_BUILD, platform, "_verify_colors.png")
    cmd = [sys.executable, os.path.join(ROOT, "emu", "libretro", "run_lynx.py"),
           rom, "200", "--png", png]
    if core:
        cmd += ["--core", core]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0 or not os.path.isfile(png):
        return check("colorlab.%s runs in the libretro harness" % ext, False)
    colors = {c for _n, c in
              Image.open(png).convert("RGB").getcolors(maxcolors=65536)}
    # Strongly saturated colours (max channel well above min) -> real hues.
    hues = [c for c in colors if max(c) - min(c) >= 60]
    return check("%s colorlab shows many hues (%d saturated colours)"
                 % (platform, len(hues)), len(hues) >= 4)


def pce_bounce_check():
    """Screen-diff the PCE bounce ROM: the ball sprite must render and move."""
    from PIL import Image

    rom = os.path.join(BUILD, "pce", "bounce.pce")
    if not os.path.isfile(rom):
        return check("bounce.pce (missing — run `python mosaik8.py build "
                     "--platform pce samples/bounce.mos` first)", False)

    def run(png, frames):
        cmd = [sys.executable, os.path.join(ROOT, "emu", "libretro", "run_lynx.py"),
               rom, str(frames), "--core", "mednafen_pce_fast", "--png", png]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        return proc.returncode == 0 and os.path.isfile(png)

    def ball_bbox(png):
        img = Image.open(png).convert("L")
        return img.point(lambda p: 255 if p > 40 else 0).getbbox()

    a_png = os.path.join(BUILD, "pce", "_verify_a.png")
    b_png = os.path.join(BUILD, "pce", "_verify_b.png")
    if not (run(a_png, 200) and run(b_png, 260)):
        return check("bounce.pce runs in the libretro harness", False)
    a, b = ball_bbox(a_png), ball_bbox(b_png)
    ok = check("PCE ball sprite renders 8x8 (%s)" % (a,),
               a is not None and (a[2] - a[0]) == 8 and (a[3] - a[1]) == 8)
    ok &= check("PCE ball moves between frames (%s -> %s)" % (a, b), a != b)
    return ok


def main():
    print("ROM behavioral checks")
    print("=" * 50)
    ok = pyboy_checks()
    if "--lynx" in sys.argv:
        ok &= lynx_shmup_check()
        ok &= bkg_scroll_check("lynx")
        ok &= colors_check("lynx")
        ok &= colorlab_check("lynx")
    if "--pce" in sys.argv:
        ok &= pce_bounce_check()
        ok &= bkg_scroll_check("pce", core="mednafen_pce_fast")
        ok &= colors_check("pce", core="mednafen_pce_fast")
        ok &= colorlab_check("pce", core="mednafen_pce_fast")
    # SMS / Game Gear (Genesis Plus GX) and NES (FCEUmm): the GBDK colour
    # consoles, behaviourally checkable now that setup_tools.py installs their
    # cores. They have one sprite palette / coarse (NES) or no (SMS) per-tile
    # palettes, so the full colors/colorlab palette checks don't apply; the
    # background project (a single bkg palette) is the portable colour check.
    if "--sms" in sys.argv:
        ok &= bkg_scroll_check("sms", core="genesis_plus_gx")
    if "--gg" in sys.argv:
        ok &= bkg_scroll_check("gamegear", core="genesis_plus_gx")
    if "--nes" in sys.argv:
        ok &= bkg_scroll_check("nes", core="fceumm")
    print()
    print("All ROM checks passed" if ok else "ROM checks FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
