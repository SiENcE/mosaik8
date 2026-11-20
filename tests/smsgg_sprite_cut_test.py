#!/usr/bin/env python3
"""`sprite.cut_y` on the SMS / Game Gear parks an object whose BOTTOM would
reach the box, so no sprite overhangs the box's border.

The two consoles have no window layer and no raster sprite disable, so the
box sprite cut is a per-object FILTER in the placement leaf (`gbs_spr_put`).
It tested the object's TOP until 2026-11-20, and a sprite standing just above
an open box drew over its border by up to its own height (seen on
projects/vm-fjaum, SMS). The setter now lowers the threshold by the object
height less one (7, or 15 in 8x16 mode); the per-frame compare is unchanged.

Pinned here:
  * codegen: the setter's lift for 8x8 and 8x16 objects, 255 still means
    off, and a program that cuts WITHOUT a metasprite compiles (it had no
    park row, `GBS_SPR_PARK_Y`, until the same day);
  * the ROM (GBDK + genesis_plus_gx), SMS and Game Gear: a solid 8x8 sprite
    straddling the cut row (4 px above, 4 px below) is not drawn, and a
    control sprite wholly above the row is.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler  # noqa: E402

passed = failed = 0
CUT = 96

PROGRAM = '''
module "main" {
    import "graphics.sprite"
    import "platform.video"
    function main() {
        sprite.set_data(0, s_tile_count, s_tiles)
        video.enable_lcd()
        video.show_sprites()
        sprite.set_tile(0, 0)
        sprite.set_tile(1, 0)
        sprite.cut_y(%d)
        loop {
            sprite.move(0, 40, %d)
            sprite.move(1, 80, %d)
            video.wait_vblank()
        }
    }
    export main
}
''' % (CUT, CUT - 4, CUT - 12)


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def test_codegen():
    print("codegen: the cut's threshold is the object's bottom")
    src = PROGRAM.replace("s_tile_count, s_tiles", "1, s_tiles")
    src = src.replace('    function main() {',
                      '    const s_tiles: array[u8, 16] = [0, 0, 0, 0, 0, 0, 0, 0, '
                      '0, 0, 0, 0, 0, 0, 0, 0]\n    function main() {')
    for plat in ("sms", "gamegear"):
        c = MosaikCompiler().compile_program([("main.mos", src)], platform=plat)
        check("%s: compiles with the cut and no metasprite" % plat,
              not c.startswith("Compilation error") and "#define GBS_SPR_PARK_Y 200" in c,
              c[:300] if c.startswith("Compilation error") else "")
        check("%s: 8x8 objects lift the threshold by 7" % plat,
              "((t > 7) ? (uint8_t)(t - 7) : 0)" in c)
        check("%s: 255 still turns the cut off" % plat, "(y == 255) ? 255" in c)
        c16 = MosaikCompiler().compile_program([("main.mos", src)], platform=plat,
                                               obj_8x16=True)
        check("%s: 8x16 objects lift it by 15" % plat,
              "((t > 15) ? (uint8_t)(t - 15) : 0)" in c16)


def test_rom():
    print("ROM (genesis_plus_gx): no object overhangs the cut")
    from mosaik8_build import gbdk_available
    core_path = os.path.join(ROOT, "emu", "libretro", "genesis_plus_gx_libretro.dll")
    if not os.path.isfile(core_path):
        core_path = core_path[:-4] + ".so"
    if not gbdk_available() or not os.path.isfile(core_path):
        print("  [SKIP] GBDK or the genesis_plus_gx core not installed")
        return
    try:
        from PIL import Image
    except ImportError:
        print("  [SKIP] Pillow not installed")
        return
    sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
    tmp = tempfile.mkdtemp(prefix="smsggcut_")
    try:
        for plat, ext in (("sms", ".sms"), ("gamegear", ".gg")):
            proj = os.path.join(tmp, plat)
            os.makedirs(os.path.join(proj, "src"))
            os.makedirs(os.path.join(proj, "assets"))
            im = Image.new("P", (8, 8), 3)
            im.putpalette([255, 255, 255, 170, 170, 170, 85, 85, 85, 0, 0, 0] + [0] * 756)
            im.save(os.path.join(proj, "assets", "s.png"))
            with open(os.path.join(proj, "assets", "s.sprites.toml"), "w") as f:
                f.write('[[sprite]]\nname = "b"\nrect = [0, 0, 8, 8]\n')
            with open(os.path.join(proj, "src", "main.mos"), "w") as f:
                f.write(PROGRAM)
            with open(os.path.join(proj, "mosaik.toml"), "w") as f:
                f.write('[project]\nname = "cut"\nversion = "0.1.0"\n'
                        'target_platforms = ["%s"]\n\n[source]\nfolder = "src/"\n\n'
                        '[assets]\nsprites = ["assets/s.png"]\n\n'
                        '[build]\noutput_dir = "build"\n' % plat)
            r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                                "--platform", plat, proj], capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            rom = os.path.join(proj, "build", plat, "cut" + ext)
            check("%s: the fixture builds" % plat, r.returncode == 0 and os.path.isfile(rom),
                  (r.stdout + r.stderr)[-1200:])
            if not os.path.isfile(rom):
                continue
            # One ROM per process (retro.Core): a child runs it.
            code = ("import sys; sys.path.insert(0, %r); import retro\n"
                    "c = retro.Core(%r, %r); c.run(120); c.image().save(%r); c.close()"
                    % (os.path.join(ROOT, "emu", "libretro"), core_path, rom,
                       os.path.join(proj, "shot.png")))
            subprocess.run([sys.executable, "-c", code], capture_output=True, timeout=120)
            shot = os.path.join(proj, "shot.png")
            if not os.path.isfile(shot):
                check("%s: a frame was captured" % plat, False)
                continue
            img = Image.open(shot).convert("L")
            w, h = img.size
            px = img.load()

            def rows(x0, x1):
                ys = [y for y in range(h) for x in range(x0, min(x1, w)) if px[x, y] < 40]
                return (min(ys), max(ys)) if ys else None
            straddler, control = rows(30, 60), rows(70, 100)
            check("%s: the control sprite above the cut draws (rows %s)" % (plat, control),
                  control == (CUT - 12, CUT - 5))
            check("%s: the sprite straddling the cut row is parked (rows %s)" % (plat, straddler),
                  straddler is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_codegen()
    test_rom()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
