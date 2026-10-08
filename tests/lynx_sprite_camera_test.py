#!/usr/bin/env python3
"""`lynx.sprite_camera(first, count, x, y)` (native.lynx): a sprite camera
on Suzy's HOFF / VOFF.

The slots [first, first + count) hold WORLD positions; the present chains them
apart and draws them with the camera as the screen offset, then puts the
offsets back and draws every other slot (a HUD) on top. A pan therefore
rewrites no SCB and a game copies no coordinates (the Lynx shooter subtracted
its pan into scratch arrays for every pool, every frame). Culling is on the
logical screen position, world - camera in u8, exactly what a game that
subtracted would have handed `sprite.move`.

Pinned here:
  * codegen: the state, the setter and the two-chain present only when the
    program CALLS it on the Lynx (baked and 8x8 engines, the portrait HOFF /
    VOFF maps); nothing of it for a native.lynx program that does not; a
    `(void)` no-op on the PC Engine and the GB family, which the build names
    as degraded; `lynx.screen_shake` keeps its VOFF under the camera;
  * the ROM (cc65 + a Lynx core): for the 8x8 engine, the whole-sprite engine
    and portrait, a fixture drawn through the camera is PIXEL-IDENTICAL to the
    same fixture that subtracts the camera itself, at three camera positions,
    over a background, with a flipped sprite, a sprite the camera culls and a
    screen-space slot that must not move.
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
from mosaik.platforms import degraded_uses  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


CALLS = '''
module "main" {
    import "graphics.sprite"
    import "platform.video"
    import "native.lynx"
    function main() {
        video.enable_lcd()
        video.show_sprites()
        sprite.move(0, 40, 50)
        lynx.sprite_camera(0, 1, 3, 4)
        %s
        loop {
            video.wait_vblank()
        }
    }
    export main
}
'''


def compile_c(platform, extra="", **kw):
    return MosaikCompiler().compile_program([("main.mos", CALLS % extra)],
                                            platform=platform, **kw)


def test_codegen():
    print("codegen")
    for kw in ({}, {"lynx_sprites": "whole"}):
        c = compile_c("lynx", **kw)
        tag = "whole" if kw else "8x8"
        check("lynx %s: compiles" % tag, not c.startswith("Compilation error"), c[:300])
        check("lynx %s: the setter is real" % tag,
              "void gbs_lynx_sprite_camera(uint8_t first, uint8_t count, uint8_t x, uint8_t y) {\n"
              "    uint8_t e" in c)
        check("lynx %s: the world chain draws under HOFF/VOFF, then they go back" % tag,
              "SUZY.hoff = (unsigned)gbs_cam_x;" in c
              and "SUZY.voff = (unsigned)gbs_cam_y + gbs_lynx_shk;" in c
              and "SUZY.hoff = 0; SUZY.voff = gbs_lynx_shk;" in c)
        check("lynx %s: the world chain is drawn BEFORE the screen chain" % tag,
              c.index("tgi_sprite(wh);") < c.index("tgi_sprite(h);"))
    for orient, h, v in (("portrait_left", "(unsigned)(-(int)gbs_cam_y)", "(unsigned)gbs_cam_x"),
                         ("portrait_right", "(unsigned)gbs_cam_y", "(unsigned)(-(int)gbs_cam_x)")):
        c = compile_c("lynx", lynx_orientation=orient)
        check("%s: the camera turns with the screen" % orient,
              ("SUZY.hoff = %s;" % h) in c and ("SUZY.voff = %s + gbs_lynx_shk;" % v) in c)
    shk = compile_c("lynx", extra="lynx.screen_shake(2)")
    check("lynx: screen_shake records its VOFF for the camera chain",
          "void gbs_lynx_screen_shake(uint8_t yoff) { gbs_lynx_shk = yoff; SUZY.voff = yoff; }" in shk)

    plain = MosaikCompiler().compile_program([("main.mos", (CALLS % "").replace(
        "lynx.sprite_camera(0, 1, 3, 4)", "lynx.screen_shake(1)"))], platform="lynx")
    check("lynx, native.lynx without the call: none of it",
          "gbs_cam_" not in plain and "SUZY.hoff" not in plain
          and "void gbs_lynx_screen_shake(uint8_t yoff) { SUZY.voff = yoff; }" in plain)
    for plat in ("pce", "gameboy", "sms"):
        c = compile_c(plat)
        check("%s: compiles to a (void) no-op" % plat,
              not c.startswith("Compilation error")
              and "(void)first; (void)count; (void)x; (void)y;" in c, c[:300])
        gb_plain = MosaikCompiler().compile_program([("main.mos", (CALLS % "").replace(
            "lynx.sprite_camera(0, 1, 3, 4)", "lynx.screen_shake(1)"))], platform=plat)
        check("%s: no call, no stub" % plat, "gbs_lynx_sprite_camera" not in gb_plain)
    named = {t for t, _m in degraded_uses("pce")} | {t for t, _m in degraded_uses("gameboy")}
    check("the build names the no-op as degraded off the Lynx, and not on it",
          ("call", "lynx", "sprite_camera") in named
          and ("call", "lynx", "sprite_camera") not in {t for t, _m in degraded_uses("lynx")})


# ---------------------------------------------------------------------------
# The ROM A/B: camera vs the game subtracting the camera itself.
# ---------------------------------------------------------------------------

CAMS = [(0, 0), (13, 21), (37, 6)]      # held 64 loop passes each
# Mid-hold, measured on the fixture: the first frame is drawn by emulator
# frame ~50 and the camera steps at ~120 and ~190 (the start-up and the
# upload take the first frames; the third camera then holds for good).
SHOTS = [85, 155, 260]

PROGRAM = '''
module "main" {
    import "graphics.sprite"
    import "graphics.bkg"
    import "platform.video"
    import "native.lynx"
    const MAP: array[u8, 260] = [%(map)s]
    const CX: array[u8, 3] = [%(cx)s]
    const CY: array[u8, 3] = [%(cy)s]
    -- world positions; slot 3 is screen space (a HUD)
    const WX: array[u8, 3] = [60, 30, 4]
    const WY: array[u8, 3] = [40, 70, 2]
    function main() {
        sprite.set_data(0, s_tile_count, s_tiles)
        bkg.set_data(0, s_tile_count, s_tiles)
        bkg.set_tiles(0, 0, 20, 13, MAP)
        video.enable_lcd()
        video.show_background()
        video.show_sprites()
        for s in 0..4 {
            sprite.set_tile(s, 0)
        }
        sprite.set_prop(1, FLIP_X)
        sprite.move(3, 140, 90)
        var f: u16 = 0
        loop {
            var k: u8 = f >> 6
            if k > 2 {
                k = 2
            }
            var cx: u8 = CX[k]
            var cy: u8 = CY[k]
            %(draw)s
            f = f + 1
            video.wait_vblank()
        }
    }
    export main
}
'''
DRAW_CAM = '''for s in 0..3 {
                sprite.move(s, WX[s], WY[s])
            }
            lynx.sprite_camera(0, 3, cx, cy)'''
DRAW_REF = '''for s in 0..3 {
                sprite.move(s, WX[s] - cx, WY[s] - cy)
            }'''


def _sheet(path):
    """A 16x16 4-colour sheet, asymmetric so a flip or a turn shows."""
    from PIL import Image
    im = Image.new("P", (16, 16), 0)
    im.putpalette([255, 255, 255, 170, 170, 170, 85, 85, 85, 0, 0, 0] + [0] * 756)
    px = im.load()
    for x in range(16):
        px[x, 0] = 3                    # a top bar
    for y in range(16):
        px[0, y] = 2                    # a left column
    for i in range(1, 8):
        px[i, i] = 1                    # a diagonal from the corner
    px[15, 15] = 3
    im.save(path)
    with open(path[:-4] + ".sprites.toml", "w", encoding="utf-8") as f:
        f.write('[[sprite]]\nname = "big"\nrect = [0, 0, 16, 16]\n')


def _project(tmp, name, draw, build):
    proj = os.path.join(tmp, name)
    os.makedirs(os.path.join(proj, "src"))
    os.makedirs(os.path.join(proj, "assets"))
    _sheet(os.path.join(proj, "assets", "s.png"))
    cells = ", ".join(str((x * 3 + y * 5) % 4) for y in range(13) for x in range(20))
    src = PROGRAM % {"map": cells, "draw": draw,
                     "cx": ", ".join(str(c[0]) for c in CAMS),
                     "cy": ", ".join(str(c[1]) for c in CAMS)}
    with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
        f.write(src)
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "cam"\nversion = "0.1.0"\n'
                'target_platforms = ["lynx"]\n\n[source]\nfolder = "src/"\n\n'
                '[assets]\nsprites = ["assets/s.png"]\n\n'
                '[build]\noutput_dir = "build"\n' + build)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "lynx", proj], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    rom = os.path.join(proj, "build", "lynx", "cam.lnx")
    return (rom if r.returncode == 0 and os.path.isfile(rom) else None), r.stdout + r.stderr


def _shot(rom, frames, png):
    subprocess.run([sys.executable, os.path.join(ROOT, "emu", "libretro", "run_lynx.py"),
                    rom, str(frames), "--png", png], capture_output=True, cwd=ROOT)
    return png if os.path.isfile(png) else None


def _lynx_core():
    for stem in ("mednafen_lynx", "handy"):
        if os.path.isfile(os.path.join(ROOT, "emu", "libretro", stem + "_libretro.dll")) \
                or os.path.isfile(os.path.join(ROOT, "emu", "libretro", stem + "_libretro.so")):
            return True
    return False


def test_rom():
    print("ROM (Lynx core): camera == the game subtracting it")
    from mosaik8_build import cc65_available
    if not cc65_available() or not _lynx_core():
        print("  [SKIP] cc65 or a Lynx core not installed")
        return
    try:
        from PIL import Image, ImageChops
    except ImportError:
        print("  [SKIP] Pillow not installed")
        return
    tmp = tempfile.mkdtemp(prefix="lynxcam_")
    try:
        for mode, build in (("8x8", ""), ("whole", 'lynx_sprites = "whole"\n'),
                            ("portrait", 'lynx_orientation = "portrait_left"\n')):
            cam, out_c = _project(tmp, mode + "_c", DRAW_CAM, build)
            ref, out_r = _project(tmp, mode + "_r", DRAW_REF, build)
            check("%s: both fixtures build" % mode, cam and ref, (out_c + out_r)[-1200:])
            if not (cam and ref):
                continue
            prev = None
            for k, frame in enumerate(SHOTS):
                a = _shot(cam, frame, os.path.join(tmp, "%s_c%d.png" % (mode, k)))
                b = _shot(ref, frame, os.path.join(tmp, "%s_r%d.png" % (mode, k)))
                if not (a and b):
                    check("%s cam %s: both shots taken" % (mode, CAMS[k]), False)
                    continue
                ia, ib = Image.open(a).convert("RGB"), Image.open(b).convert("RGB")
                check("%s cam %s: pixel-identical" % (mode, CAMS[k]),
                      ImageChops.difference(ia, ib).getbbox() is None)
                if prev is not None:
                    check("%s cam %s: the picture moved with the camera" % (mode, CAMS[k]),
                          ImageChops.difference(prev, ia).getbbox() is not None)
                prev = ia
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_codegen()
    test_rom()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
