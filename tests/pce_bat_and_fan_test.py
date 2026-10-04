"""PC Engine background BAT writes and sprite FANS, measured in VRAM.

Four faults the showcase RPG's PCE slice drew on screen (record
`docs/done/pce-rendering-parity.md`), each pinned here by building one
fixture, running it on Beetle PCE Fast and reading the VDC's VRAM back:

  * `bkg.set_attrs` READS a BAT entry back to keep its character code. cc65's
    optimizer compiled `lo | hi << 8` as $0202, $0203, $0202 -- and reading
    VRR high advances MARR, so every cell took the NEXT cell's low byte and a
    recoloured row copied codes sideways (a streamed room drew the previous
    room's map). The read is assembly now: a row keeps its codes.
  * a BACKGROUND-space `text.clear_area` must reach the BAT's scroll replicas
    (columns +32/+64/+96, rows +32), as `bkg.set_tiles` writes them; conio's
    cclearxy writes the primary entry only, so a scrolled screen showed the
    previous room in the replicas.
  * `sprite.set_palette` on a metasprite base recolours the whole FAN (it set
    the base cell only: a 2x2 NPC drew three quarters in palette 0), and a
    later `set_meta` that grows the fan carries the palette to the new cells.
  * a fan's lower rows past the 224-line screen PARK: the u8 `y + row * 8`
    wrapped them onto the top of the screen.
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


ROW = [0, 1, 2, 3, 3, 2, 1, 0]
PALS = [1, 2, 3, 0, 1, 2, 3, 0]

FIXTURE = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    import "graphics.sprite"
    import "graphics.palette"
    import "graphics.text"

    -- four distinct 2bpp tiles (each row byte pair differs per tile)
    const TILES: array[u8, 64] = [
        0x01,0x00, 0x01,0x00, 0x01,0x00, 0x01,0x00, 0x01,0x00, 0x01,0x00, 0x01,0x00, 0x01,0x00,
        0x00,0x02, 0x00,0x02, 0x00,0x02, 0x00,0x02, 0x00,0x02, 0x00,0x02, 0x00,0x02, 0x00,0x02,
        0x04,0x04, 0x04,0x04, 0x04,0x04, 0x04,0x04, 0x04,0x04, 0x04,0x04, 0x04,0x04, 0x04,0x04,
        0x18,0x00, 0x18,0x00, 0x18,0x00, 0x18,0x00, 0x18,0x00, 0x18,0x00, 0x18,0x00, 0x18,0x00
    ]
    const ROW: array[u8, 8] = [%(row)s]
    const PALS: array[u8, 8] = [%(pals)s]
    const FILL: array[u8, 8] = [1, 1, 1, 1, 1, 1, 1, 1]

    function main() {
        bkg.set_data(0, 4, TILES)
        bkg.set_tiles(0, 0, 8, 1, ROW)
        bkg.set_attrs(0, 0, 8, 1, PALS)
        bkg.set_tiles(0, 2, 8, 1, FILL)
        text.clear_area(2, 2, 3, 1)
        sprite.set_data(0, 4, TILES)
        sprite.set_meta(0, 0, 2, 2)
        sprite.set_palette(0, 2)
        sprite.set_meta(0, 0, 3, 2)
        sprite.move(0, 40, 40)
        sprite.set_meta(8, 0, 2, 2)
        sprite.move(8, 80, 250)
        loop {
            video.wait_vblank()
        }
    }
}
''' % {"row": ", ".join(map(str, ROW)), "pals": ", ".join(map(str, PALS))}

SATB = 0x7F00           # GBS_VRAM_SATB
BKG_CHAR0 = 0x4000 >> 4  # GBS_VRAM_BKG / 16: the character code of tile 0
VISIBLE_END = 64 + 224   # SATB y of the first line past the 224-line screen


def _core():
    p = os.path.join(ROOT, "emu", "libretro", "mednafen_pce_fast_libretro.dll")
    return p if os.path.isfile(p) else None


def _build(tmp):
    proj = os.path.join(tmp, "fx")
    os.makedirs(os.path.join(proj, "src"))
    with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
        f.write(FIXTURE)
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "fx"\nversion = "0.1.0"\n'
                'target_platforms = ["pce"]\n\n[source]\nfolder = "src/"\n\n'
                '[build]\noutput_dir = "build"\n')
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "pce", proj], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    rom = os.path.join(proj, "build", "pce", "fx.pce")
    return (rom if r.returncode == 0 and os.path.isfile(rom) else None,
            (r.stdout + r.stderr)[-1500:])


def _vram(rom, frames=60):
    """Run `rom` and return the VDC's 32 K-word VRAM as a list of words."""
    sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
    import lynx_probe as lp  # noqa: F401 - installs the NULL-frame patch
    from libretro import SessionBuilder
    from libretro.drivers.path import ExplicitPathDriver
    core = _core()
    sysdir = os.path.join(ROOT, "emu", "libretro")
    b = (SessionBuilder.defaults(core).with_content(rom)
         .with_paths(ExplicitPathDriver(corepath=core, system=sysdir, save=sysdir,
                                        assets=sysdir, playlist=sysdir))
         .with_perf(None))
    with b.build() as s:
        for _ in range(frames):
            s.run()
        ptr, size = s.core.get_memory_data(3), s.core.get_memory_size(3)  # VIDEO_RAM
        if not ptr or not size:
            return None
        raw = bytes((ctypes.c_uint8 * size).from_address(
            ctypes.cast(ptr, ctypes.c_void_p).value))
    return [raw[2 * i] | raw[2 * i + 1] << 8 for i in range(size // 2)]


def test_rom():
    print("PCE BAT + sprite fans (Beetle PCE Fast)")
    from mosaik8_build import cc65_available
    if not cc65_available() or not _core():
        print("  [SKIP] cc65 or the mednafen_pce_fast core not installed")
        return
    tmp = tempfile.mkdtemp(prefix="pcebat_")
    try:
        rom, out = _build(tmp)
        check("the fixture builds", rom is not None, out)
        if not rom:
            return
        v = _vram(rom)
        check("the core exposes VRAM", v is not None)
        if v is None:
            return

        def bat(x, y):
            return v[(y << 7) + x]

        codes = [(bat(x, 0) & 0x0FFF) - BKG_CHAR0 for x in range(8)]
        pals = [bat(x, 0) >> 12 for x in range(8)]
        check("a recoloured row keeps its tile codes (the read-back)",
              codes == ROW, "codes %s, want %s" % (codes, ROW))
        check("...and takes each cell's palette (slot + 2)",
              pals == [p + 2 for p in PALS], "pals %s" % pals)

        cleared = bat(2, 2)
        check("the clear wrote the primary cells (not the fill tile)",
              (cleared & 0x0FFF) != BKG_CHAR0 + 1
              and all(bat(x, 2) == cleared for x in (2, 3, 4)), hex(cleared))
        reps = [v[((2 + dy) << 7) + x + dx] for x in (2, 3, 4)
                for dx in (0, 32, 64, 96) for dy in (0, 32)]
        check("...and every BAT scroll replica of them",
              all(e == cleared for e in reps),
              "replicas %s" % sorted(set(hex(e) for e in reps)))
        check("...and left its neighbours alone",
              (bat(1, 2) & 0x0FFF) == BKG_CHAR0 + 1
              and (bat(5, 2) & 0x0FFF) == BKG_CHAR0 + 1)

        attr = [v[SATB + 4 * s + 3] & 0x000F for s in range(6)]
        check("set_palette on a fan base recolours every cell, and a grown "
              "fan carries it to the new cells", attr == [2] * 6, "attrs %s" % attr)

        ys = [v[SATB + 4 * s] for s in range(8, 12)]
        check("a fan row past the bottom of the screen parks (no u8 wrap "
              "onto the top)", all(y >= VISIBLE_END for y in ys), "SATB y %s" % ys)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_park():
    """The GB park y (200) is ON the PCE's 224-line screen.

    `sprite.move(s, 200, 200)` is off-screen on every GBDK console and the
    Lynx, but the PCE drew the parked sprites at (200, 200) - a stray sprite
    in every small room of the showcase RPG. Every park in lib/vm and the
    generated room-load sweep forks to `SCREEN_HEIGHT` on the PCE; the
    generator carries the fork only for a world that targets the PCE."""
    import re
    print("the PCE park")
    for name in ("actor", "emote", "player", "projectile"):
        with open(os.path.join(ROOT, "lib", "vm", name + ".mos"), encoding="utf-8") as f:
            src = f.read()
        gb = re.findall(r"sprite\.move\((\w+), 200, 200\)", src)
        pce = re.findall(r"sprite\.move\((\w+), 200, SCREEN_HEIGHT\)", src)
        check("lib/vm/%s.mos: every 200,200 park has its PCE twin (%d)"
              % (name, len(gb)), gb and gb == pce, "%s vs %s" % (gb, pce))
    from mosaik_vm.rooms import emit_rooms_mos
    base = {"types": ["topdown"], "scene_count": 2, "uniform": True,
            "map_w": 20, "map_h": 18, "has_objects": True, "has_ent": False,
            "has_trig": False, "has_doors": False,
            "clips": {"meta_w": 2, "meta_h": 2}}
    on = emit_rooms_mos(dict(base, targets_pce=True))
    off = emit_rooms_mos(dict(base))
    check("a PCE-targeting world's room-load sweep parks at SCREEN_HEIGHT there",
          "sprite.move(s, 200, SCREEN_HEIGHT)" in on
          and "sprite.move(s, 200, 200)" in on)
    check("... and any other world's sweep is the plain park (byte-identical)",
          "sprite.move(s, 200, SCREEN_HEIGHT)" not in off
          and 'platform == "pce"' not in off and "sprite.move(s, 200, 200)" in off)


LYNX_FIXTURE = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "graphics.palette"

    const TILES: array[u8, 16] = [0x18,0x18, 0x3C,0x3C, 0x7E,0x7E, 0xFF,0xFF,
                                  0xFF,0xFF, 0x7E,0x7E, 0x3C,0x3C, 0x18,0x18]

    function main() {
        sprite.set_data(0, 1, TILES)
        sprite.set_meta(0, 0, 2, 2)
        sprite.set_palette(0, 2)
        sprite.set_meta(0, 0, 3, 2)
        sprite.move(0, 40, 40)
        loop {
            video.wait_vblank()
        }
    }
}
'''


def test_lynx_fan_palette():
    """The Lynx had the PCE's fault: a metasprite is w x h SCBs and
    `sprite.set_palette` repointed the BASE SCB's pens only. Read the six
    cells' `penpal` bytes (offset 15 of the 32-byte padded SCB) back."""
    print("Lynx fan palette (Beetle Lynx / Handy)")
    from mosaik8_build import cc65_available
    lib = os.path.join(ROOT, "emu", "libretro")
    cores = [os.path.join(lib, n) for n in ("mednafen_lynx_libretro.dll", "handy_libretro.dll")]
    if not cc65_available() or not any(os.path.isfile(c) for c in cores):
        print("  [SKIP] cc65 or a Lynx core not installed")
        return
    tmp = tempfile.mkdtemp(prefix="lynxfan_")
    try:
        proj = os.path.join(tmp, "lf")
        os.makedirs(os.path.join(proj, "src"))
        with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
            f.write(LYNX_FIXTURE)
        with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
            f.write('[project]\nname = "lf"\nversion = "0.1.0"\n'
                    'target_platforms = ["lynx"]\n\n[source]\nfolder = "src/"\n\n'
                    '[build]\noutput_dir = "build"\n')
        r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                            "--debug", "--platform", "lynx", proj], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        rom = os.path.join(proj, "build", "lynx", "lf.lnx")
        check("the Lynx fixture builds", r.returncode == 0 and os.path.isfile(rom),
              (r.stdout + r.stderr)[-1200:])
        if r.returncode:
            return
        sys.path.insert(0, lib)
        import lynx_probe as lp
        from libretro import SessionBuilder
        from libretro.drivers.path import ExplicitPathDriver
        labels = lp.load_labels(rom + ".lbl")
        b = (SessionBuilder.defaults(lp.CORE).with_content(rom)
             .with_paths(ExplicitPathDriver(corepath=lp.CORE, system=lp.SYSTEM_DIR,
                                            save=lp.SYSTEM_DIR, assets=lp.SYSTEM_DIR,
                                            playlist=lp.SYSTEM_DIR))
             .with_perf(None))
        with b.build() as s:
            for _ in range(120):
                s.run()
            ram = lp.Ram(s)
            scb, pp = labels["_gbs_scb"], labels["_gbs_pal_penpal"]
            want = (ram.read(pp + 4), ram.read(pp + 5))     # gbs_pal_penpal[2]
            cells = [(ram.read(scb + 32 * c + 15), ram.read(scb + 32 * c + 16))
                     for c in range(6)]
        check("set_palette on a Lynx fan base repoints every cell's pens, and "
              "a grown fan carries them", cells == [want] * 6,
              "cells %s, want %s" % (cells, want))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


FADE_FIXTURE = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    import "graphics.palette"

    const SOLID: array[u8, 16] = [0xFF,0xFF, 0xFF,0xFF, 0xFF,0xFF, 0xFF,0xFF,
                                  0xFF,0xFF, 0xFF,0xFF, 0xFF,0xFF, 0xFF,0xFF]
    const ROW: array[u8, 16] = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    function main() {
        var n: u16 = 0
        bkg.set_data(0, 1, SOLID)
        for r in 0..8 {
            bkg.set_tiles(0, r, 16, 1, ROW)
        }
        palette.set_bkg(0, palette.rgb(255, 255, 255), palette.rgb(255, 0, 0),
                        palette.rgb(0, 255, 0), palette.rgb(0, 0, 255))
        loop {
            video.wait_vblank()
            n += 1
            if n == 60 {
                palette.fade(1)
            }
            if n == 120 {
                palette.fade(2)
            }
            if n == 180 {
                palette.fade(3)
            }
            if n == 240 {
                -- loaded while BLACK: must stay black until the ramp back
                palette.set_bkg(0, palette.rgb(255, 255, 255), palette.rgb(255, 0, 0),
                                palette.rgb(0, 255, 0), palette.rgb(0, 0, 255))
            }
            if n == 300 {
                palette.fade(0)
            }
        }
    }
}
'''


def test_pce_fade():
    """`palette.fade` was a no-op stub on the PCE, so its rooms could not hide
    being built. It scales the VCE through a shadow now: each level darkens
    the screen, level 3 is black, a palette LOADED while black stays black,
    and level 0 restores the exact picture."""
    print("PCE palette.fade (Beetle PCE Fast)")
    from mosaik8_build import cc65_available
    if not cc65_available() or not _core():
        print("  [SKIP] cc65 or the mednafen_pce_fast core not installed")
        return
    from PIL import Image, ImageStat
    tmp = tempfile.mkdtemp(prefix="pcefade_")
    try:
        proj = os.path.join(tmp, "fd")
        os.makedirs(os.path.join(proj, "src"))
        with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
            f.write(FADE_FIXTURE)
        with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
            f.write('[project]\nname = "fd"\nversion = "0.1.0"\n'
                    'target_platforms = ["pce"]\n\n[source]\nfolder = "src/"\n\n'
                    '[build]\noutput_dir = "build"\n')
        r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                            "--platform", "pce", proj], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        rom = os.path.join(proj, "build", "pce", "fd.pce")
        check("the fade fixture builds", r.returncode == 0 and os.path.isfile(rom),
              (r.stdout + r.stderr)[-1200:])
        if r.returncode:
            return
        lum = {}
        for fr in (50, 110, 170, 230, 290, 350):
            png = os.path.join(tmp, "f%d.png" % fr)
            subprocess.run([sys.executable, os.path.join(ROOT, "emu", "libretro", "run_lynx.py"),
                            rom, str(fr + 4), "--core", "mednafen_pce_fast", "--png", png],
                           capture_output=True, cwd=ROOT)
            # the DISPLAY only: the lines above it are the overscan border
            # (VCE $100), which is not a palette the game writes
            lum[fr] = ImageStat.Stat(
                Image.open(png).convert("L").crop((0, 24, 256, 216))).mean[0]
        seq = [round(lum[f]) for f in (50, 110, 170, 230, 290, 350)]
        check("each level darkens the screen, level 3 is black",
              lum[50] > lum[110] > lum[170] > lum[230] and lum[230] < 1, str(seq))
        check("a palette loaded while black stays black", lum[290] < 1, str(seq))
        check("level 0 restores the picture", abs(lum[350] - lum[50]) < 1, str(seq))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_menu_row():
    """A script menu re-anchors to the PCE screen's bottom, as on SMS/GG.

    The authored row fits the SHORTEST screen (12 rows), so on the PCE's 28 a
    menu sat in the MIDDLE of the screen, above the text boxes that are
    already bottom-anchored there (the showcase RPG's title)."""
    print("the PCE menu row")
    with open(os.path.join(ROOT, "lib", "vm", "core.mos"), encoding="utf-8") as f:
        src = f.read()
    arm = src[src.index("case OP_MENU {"):]
    arm = arm[:arm.index("if vm_waiting[c] == 0 {")]
    check("OP_MENU re-anchors the row on the PCE too",
          'platform == "sms" or platform == "gamegear" or platform == "pce" {' in arm
          and "row = SCREEN_ROWS - 1 - count" in arm)


def test_menu_paper_and_fade_wiring():
    """Three source/generator contracts, each a fix measured on the showcase
    RPG (record `docs/done/pce-rendering-parity.md`, second pass):

    * an UNFRAMED menu clears its own band on a console with no window layer
      (the Game Gear title showed the room through the menu's spaces) - and
      the GB family, whose menu is on the window, keeps its bytes;
    * vm.core's room-load fade-OUT ramps on the PCE (it cut straight to black);
    * a COLOURED, fading world that targets the PCE puts it in the generated
      fade guards; any other world's rooms.mos is unchanged."""
    print("menu paper + PCE fade wiring")
    with open(os.path.join(ROOT, "lib", "vm", "core.mos"), encoding="utf-8") as f:
        src = f.read()
    arm = src[src.index("case OP_MENU {"):]
    arm = arm[:arm.index("menu.nav(count)")]
    at = arm.find("AN UNFRAMED MENU NEEDS ITS PAPER")
    paper = arm[at:] if at >= 0 else ""
    check("an unframed menu clears its band on SMS/GG/PCE/NES",
          'if platform == "sms" or platform == "gamegear" or platform == "pce" or platform == "nes" {'
          in paper and "text.clear_area(0, row - 1, SCREEN_COLS, count + 2)" in paper)
    check("... and not on the GB family (its menu is on the window)",
          bool(paper) and '"gameboy"' not in paper.split("text.clear_area")[0])
    fo = src[src.index("local function fade_out()"):]
    fo = fo[:fo.index("\n    }\n")]
    check("the room-load fade-out ramps on the PCE", 'platform == "pce"' in fo
          and 'platform == "lynx"' not in fo)
    from mosaik_vm.rooms import emit_rooms_mos
    base = {"types": ["topdown"], "scene_count": 2, "uniform": True,
            "map_w": 20, "map_h": 18, "has_objects": False, "has_ent": False,
            "has_trig": False, "has_doors": False, "fade": 4, "colour": True}
    on = emit_rooms_mos(dict(base, targets_pce=True))
    check("a coloured fading PCE world fades and registers the colour fade on the PCE",
          on.count('platform == "gamegear" or platform == "pce" {') >= 3, str(
              on.count('platform == "gamegear" or platform == "pce" {')))
    for label, info in (("not targeting the PCE", dict(base)),
                        ("without colour", dict(base, targets_pce=True, colour=False))):
        check("... and a world %s carries no PCE fade clause" % label,
              'platform == "gamegear" or platform == "pce" {' not in emit_rooms_mos(info))


if __name__ == "__main__":
    test_menu_paper_and_fade_wiring()
    test_menu_row()
    test_lynx_fan_palette()
    test_pce_fade()
    test_park()
    test_rom()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
