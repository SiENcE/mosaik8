#!/usr/bin/env python3
"""A sprite y of 240..255 is the band ABOVE the screen on the baked Lynx
engine (`[build] lynx_sprites` / `lynx_orientation`), as on the Game Boy.

The positions the sprite verbs take are bytes. On the Game Boy family the
16 px sprite offset makes y 240..255 wrap to just above the screen, so a
game spawns an enemy at y 240 and it SLIDES in from the top. The baked Lynx
engine read the byte as unsigned: y 240 was far below the screen, and the
enemy appeared whole at y 0 (the Lynx shooter, 2026-10-12). Now
`gbs_spr_place`, the `sprite.plot` fast path and both present chains read
y >= 240 as y - 256 (`GBS_SY` / `GBS_YOUT`); 224, the batch verbs' park,
stays hidden.

Pinned here:
  * codegen: the baked engine (landscape, both portraits) places and culls
    through GBS_SY / GBS_YOUT, the camera chain too; the 8x8 engine is
    untouched;
  * the ROM (cc65 + a Lynx core): a sprite at y 248, by `sprite.move` and by
    `sprite.plot`, shows at the top exactly the lower half the same sprite
    shows at y 0 (rows 8..15), in landscape, both portraits and under
    `lynx.sprite_camera`; nothing else is drawn, so the parked slot (y 224)
    stays hidden. Without the fix the top rows are empty and this fails.
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
    import "graphics.sprite"
    import "platform.video"
    %(cam_import)s
    const XS: array[u8, 1] = [%(px)d]
    const YS: array[u8, 1] = [%(y)d]
    function main() {
        sprite.set_data(0, s_tile_count, s_tiles)
        video.enable_lcd()
        video.show_sprites()
        for s in 0..3 {
            sprite.set_tile(s, 0)
        }
        %(cam)s
        loop {
            sprite.move(0, %(mx)d, %(y)d)
            sprite.plot(1, 1, XS, YS, 1)
            sprite.move(2, %(kx)d, 224)          -- parked: never drawn
            video.wait_vblank()
        }
    }
    export main
}
'''

# whole sprites everywhere: turned without them, a sprite is its first 8x8
# tile, whose lower half (what y 248 shows) is nothing
WHOLE = 'lynx_sprites = "whole"\n'
MODES = (("landscape", WHOLE, 1),
         ("portrait_left", WHOLE + 'lynx_orientation = "portrait_left"\n', 1),
         ("portrait_right", WHOLE + 'lynx_orientation = "portrait_right"\n', 3),
         ("camera", WHOLE + 'lynx_orientation = "portrait_left"\n', 1))
CAM_X = 6


def test_codegen():
    print("codegen")
    src = (PROGRAM % {"cam_import": "", "cam": "", "px": 50, "y": 248,
                      "mx": 10, "kx": 80}).replace("s_tile_count, s_tiles", "1, XS")
    for kw in ({"lynx_sprites": "whole"}, {"lynx_orientation": "portrait_left"},
               {"lynx_orientation": "portrait_right"}):
        c = MosaikCompiler().compile_program([("main.mos", src)], platform="lynx", **kw)
        tag = list(kw.values())[0]
        check("%s: compiles" % tag, not c.startswith("Compilation error"), c[:300])
        check("%s: y 240..255 reads as y - 256 (GBS_SY)" % tag,
              "#define GBS_SY(v) ((int)(v) - ((uint8_t)(v) >= 240 ? 256 : 0))" in c)
        check("%s: gbs_spr_place and the plot fast path place through it" % tag,
              c.count("GBS_SY(gbs_spr_ly[nb])") == 1 and "GBS_SY(y)" in c)
        check("%s: the chain culls the band above the top in (GBS_YOUT)" % tag,
              "GBS_YOUT(gbs_spr_ly[s])" in c and "gbs_spr_ly[s] >= GBS_LOG_H" not in c)
    cam = (PROGRAM % {"cam_import": 'import "native.lynx"',
                      "cam": "lynx.sprite_camera(0, 2, 6, 0)", "px": 50, "y": 248,
                      "mx": 10, "kx": 80}).replace("s_tile_count, s_tiles", "1, XS")
    c = MosaikCompiler().compile_program([("main.mos", cam)], platform="lynx",
                                         lynx_orientation="portrait_left")
    check("camera: the world chain culls relative to the camera through GBS_YOUT",
          "GBS_YOUT(gbs_spr_ly[s] - gbs_cam_y)" in c)
    plain = MosaikCompiler().compile_program([("main.mos", src)], platform="lynx")
    check("the 8x8 engine is untouched", "GBS_SY" not in plain and "GBS_YOUT" not in plain)


def _sheet(path):
    """A 16x16 sheet with a different picture in every row, so the half that
    shows can only match the right half."""
    from PIL import Image
    im = Image.new("P", (16, 16), 0)
    im.putpalette([255, 0, 255, 255, 255, 255, 170, 170, 170, 85, 85, 85] + [0] * 756)
    px = im.load()
    for y in range(16):
        for x in range(16):
            if (x * 3 + y * 5) % 7 < 3:
                px[x, y] = 1 + (x + y) % 3
    im.save(path)
    with open(path[:-4] + ".sprites.toml", "w", encoding="utf-8") as f:
        f.write('[[sprite]]\nname = "big"\nrect = [0, 0, 16, 16]\n')


def _project(tmp, name, y, build, camera):
    proj = os.path.join(tmp, name)
    os.makedirs(os.path.join(proj, "src"))
    os.makedirs(os.path.join(proj, "assets"))
    _sheet(os.path.join(proj, "assets", "s.png"))
    off = CAM_X if camera else 0
    src = PROGRAM % {"cam_import": 'import "native.lynx"' if camera else "",
                     "cam": ("lynx.sprite_camera(0, 3, %d, 0)" % CAM_X) if camera else "",
                     "px": 50 + off, "y": y, "mx": 10 + off, "kx": 80 + off}
    with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
        f.write(src)
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "top"\nversion = "0.1.0"\n'
                'target_platforms = ["lynx"]\n\n[source]\nfolder = "src/"\n\n'
                '[assets]\nsprites = ["assets/s.png"]\n\n'
                '[build]\noutput_dir = "build"\n' + build)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "lynx", proj], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    rom = os.path.join(proj, "build", "lynx", "top.lnx")
    return (rom if r.returncode == 0 and os.path.isfile(rom) else None), r.stdout + r.stderr


def _lynx_core():
    for stem in ("mednafen_lynx", "handy"):
        for ext in ("_libretro.dll", "_libretro.so"):
            if os.path.isfile(os.path.join(ROOT, "emu", "libretro", stem + ext)):
                return True
    return False


def test_rom():
    print("ROM (Lynx core): y 248 shows the lower half of y 0's picture at the top")
    from mosaik8_build import cc65_available
    if not cc65_available() or not _lynx_core():
        print("  [SKIP] cc65 or a Lynx core not installed")
        return
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("  [SKIP] Pillow not installed")
        return
    tmp = tempfile.mkdtemp(prefix="lynxtop_")
    try:
        for mode, build, turns in MODES:
            camera = mode == "camera"
            above, out_a = _project(tmp, mode + "_a", 248, build, camera)
            at0, out_b = _project(tmp, mode + "_b", 0, build, camera)
            check("%s: both fixtures build" % mode, above and at0, (out_a + out_b)[-1200:])
            if not (above and at0):
                continue
            turned = 0 if mode == "landscape" else turns
            a = _logical_turned(above, os.path.join(tmp, mode + "_a.png"), turned)
            b = _logical_turned(at0, os.path.join(tmp, mode + "_b.png"), turned)
            if a is None or b is None:
                check("%s: both shots taken" % mode, False)
                continue
            w = a.size[0]
            top_a = a.crop((0, 0, w, 8)).tobytes()
            half_b = b.crop((0, 8, w, 16)).tobytes()
            blank = a.crop((0, 0, w, 8)).getcolors(1 << 16)
            check("%s: the band above the top shows (the top rows are not blank)" % mode,
                  blank is not None and len(blank) > 1)
            check("%s: and it is exactly the lower half of the sprite at y 0" % mode,
                  top_a == half_b)
            rest = a.crop((0, 8, w, a.size[1])).getcolors(1 << 16)
            check("%s: nothing else is drawn (the parked slot stays hidden)" % mode,
                  rest is not None and len(rest) == 1, str(rest)[:200])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _logical_turned(rom, png, turns):
    """The ROM's frames 120 and 121 merged (a static, sprite-only Lynx program
    shows its sprites on ONE of the two flip pages, a known engine fault, so a
    single frame may be the empty page), turned back upright."""
    from PIL import Image, ImageChops
    shots = []
    for frames in (120, 121):
        out = png[:-4] + "_%d.png" % frames
        subprocess.run([sys.executable, os.path.join(ROOT, "emu", "libretro", "run_lynx.py"),
                        rom, str(frames), "--png", out], capture_output=True, cwd=ROOT)
        if not os.path.isfile(out):
            return None
        shots.append(Image.open(out).convert("RGB"))
    im = ImageChops.lighter(shots[0], shots[1])
    for _ in range(turns):
        im = im.transpose(Image.ROTATE_90)
    return im


if __name__ == "__main__":
    test_codegen()
    test_rom()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
