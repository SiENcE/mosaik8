#!/usr/bin/env python3
"""The turned Lynx background's strip SLIDE draws what a rebuild draws.

`[build] lynx_orientation = "portrait_*"`: the background is screen-wide
strips that slide a column each time the camera crosses a tile. Since
2026-10-13 a slide MOVES a strip's lines along a few spare bytes
(`gbs_bkg_off`, `GBS_BKG_SPARE`) instead of copying them, and a copy only
re-centres a strip whose spare has run out. A camera that JUMPS more than a
column rebuilds the strips from the map instead, a different path.

Pinned here (cc65 + a Lynx core): in both portraits, a camera walked 1 px a
frame up (and down) to a place, through enough tiles that every strip
re-centres more than once each way, shows exactly the picture of a camera
that jumped straight there; and the walk's pictures move (the check could
not pass on two blank screens). Measured when the move replaced the copy:
every picture of a slow / fast / jumping scroll fixture identical to the
copying engine's (403 of 403; the one other differing frame was the screen
memory before the first draw).
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

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


PROGRAM = '''
module "main" {
    import "graphics.bkg"
    import "platform.video"
    const MAP: array[u8, 1024] = [%(map)s]
    function main() {
        bkg.set_data(0, t_tile_count, t_tiles)
        bkg.set_tiles(0, 0, 32, 32, MAP)
        video.enable_lcd()
        video.show_background()
        var x: u8 = 0
        var y: u8 = %(y0)d
        bkg.move(x, y)
        for i in 0..20 {
            video.wait_vblank()
        }
        %(move)s
        loop {
            video.wait_vblank()
        }
    }
    export main
}
'''
WALK = '''for i in 0..%d {
            y = y + %d
            bkg.move(x, y)
            video.wait_vblank()
        }'''
JUMP = '''y = %d
        bkg.move(x, y)'''
STEPS = 96                      # 12 tiles: every strip re-centres several times


def _sheet(path):
    from PIL import Image
    im = Image.new("P", (128, 8), 0)
    im.putpalette([0, 0, 0, 85, 85, 85, 170, 170, 170, 255, 255, 255] + [0] * 756)
    px = im.load()
    for t in range(16):
        for y in range(8):
            for x in range(8):
                px[t * 8 + x, y] = (((x >> 1) + y + t) % 4) if (x + y * 3 + t) % 5 else (t % 3) + 1
    im.save(path)


def _project(tmp, name, orient, y0, move):
    proj = os.path.join(tmp, name)
    os.makedirs(os.path.join(proj, "src"))
    os.makedirs(os.path.join(proj, "assets"))
    _sheet(os.path.join(proj, "assets", "t.png"))
    cells = ", ".join(str((x * 3 + y * 7) % 16) for y in range(32) for x in range(32))
    with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
        f.write(PROGRAM % {"map": cells, "y0": y0, "move": move})
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "sl"\nversion = "0.1.0"\n'
                'target_platforms = ["lynx"]\n\n[source]\nfolder = "src/"\n\n'
                '[assets]\nsprites = ["assets/t.png"]\n\n'
                '[build]\noutput_dir = "build"\nlynx_orientation = "%s"\n' % orient)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "lynx", proj], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    rom = os.path.join(proj, "build", "lynx", "sl.lnx")
    return (rom if r.returncode == 0 and os.path.isfile(rom) else None), r.stdout + r.stderr


def _shot(rom, frames, png):
    from PIL import Image
    subprocess.run([sys.executable, os.path.join(ROOT, "emu", "libretro", "run_lynx.py"),
                    rom, str(frames), "--png", png], capture_output=True, cwd=ROOT)
    return Image.open(png).convert("RGB") if os.path.isfile(png) else None


def _lynx_core():
    for stem in ("mednafen_lynx", "handy"):
        for ext in ("_libretro.dll", "_libretro.so"):
            if os.path.isfile(os.path.join(ROOT, "emu", "libretro", stem + ext)):
                return True
    return False


def test_rom():
    print("ROM (Lynx core): a walked camera shows what a jump to the same place shows")
    from mosaik8_build import cc65_available
    if not cc65_available() or not _lynx_core():
        print("  [SKIP] cc65 or a Lynx core not installed")
        return
    try:
        from PIL import ImageChops
    except ImportError:
        print("  [SKIP] Pillow not installed")
        return
    tmp = tempfile.mkdtemp(prefix="lynxslide_")
    try:
        for orient in ("portrait_left", "portrait_right"):
            for tag, step in (("up", 1), ("down", 255)):
                end = (STEPS * step) & 255
                walk, out_w = _project(tmp, "%s_%s_w" % (orient, tag), orient, 0,
                                       WALK % (STEPS, step))
                jump, out_j = _project(tmp, "%s_%s_j" % (orient, tag), orient, 0,
                                       JUMP % end)
                label = "%s, %s %d px" % (orient, tag, STEPS)
                check("%s: both fixtures build" % label, walk and jump, (out_w + out_j)[-1200:])
                if not (walk and jump):
                    continue
                a = _shot(walk, 400, os.path.join(tmp, "%s_%s_w.png" % (orient, tag)))
                b = _shot(jump, 400, os.path.join(tmp, "%s_%s_j.png" % (orient, tag)))
                mid = _shot(walk, 80, os.path.join(tmp, "%s_%s_m.png" % (orient, tag)))
                if a is None or b is None or mid is None:
                    check("%s: shots taken" % label, False)
                    continue
                check("%s: the walked picture is the jumped one" % label,
                      ImageChops.difference(a, b).getbbox() is None)
                check("%s: and the walk moved the picture" % label,
                      ImageChops.difference(a, mid).getbbox() is not None
                      and len(a.getcolors(1 << 16) or []) > 1)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_rom()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
