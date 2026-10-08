#!/usr/bin/env python3
"""The Lynx-native modes: `[build] lynx_sprites = "whole"` and
`[build] lynx_orientation = "portrait_left" | "portrait_right"`.

Contract pinned here:
  * the build-time images (`mosaik/codegen/lynx_images.py`): the literal line
    layout the run-time converter writes, a named rectangle baked as ONE image
    with its first tile addressing it, inner tiles addressing nothing, the
    per-tile singles table, and the quarter turns as the screen maps
    px = 159 - ly, py = lx (left) and px = ly, py = 101 - lx (right);
  * the codegen: either knob selects the BAKED engine (`gbs_img_table`,
    `gbs_spr_place`), portrait turns SCREEN_WIDTH/HEIGHT, the d-pad and the
    background; the defaults emit none of it (byte-identical, also proved by
    regenerating every Lynx sample);
  * the refusals, by name: a streamed sheet, a wide streamed level in portrait;
  * the build config: unknown values are refused.
The picture itself was checked pixel-exact on the Handy core for all six
sprite x orientation combinations (a scrolled background with an asymmetric
tile, a 16x16 metasprite, its FLIP_X twin and an 8x8 sprite).
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler
from mosaik.codegen import lynx_images as LI

FAILS = []


def check(label, cond):
    print(("[PASS] " if cond else "[FAIL] ") + label)
    if not cond:
        FAILS.append(label)


def _tile2(rows):
    """8 rows of 8 colour indices -> one GB 2bpp tile (16 bytes)."""
    out = []
    for r in rows:
        lo = hi = 0
        for x, v in enumerate(r):
            lo |= (v & 1) << (7 - x)
            hi |= ((v >> 1) & 1) << (7 - x)
        out += [lo, hi]
    return bytes(out)


def test_images():
    # an asymmetric 8x8: a top bar of 3, a left column of 2, one 1 at (7, 7)
    rows = [[0] * 8 for _ in range(8)]
    for x in range(8):
        rows[0][x] = 3
    for y in range(1, 8):
        rows[y][0] = 2
    rows[7][7] = 1
    t = _tile2(rows)
    blob = LI.bake(t, 2, 0, 1, 1)
    check("a blob opens with the LOGICAL size", blob[:2] == bytes([8, 8]))
    data = blob[2:]
    check("8x8 2bpp literal = 8 x [0x04, a, b, 0x00] + end",
          len(data) == 33 and all(data[i * 4] == 4 and data[i * 4 + 3] == 0
                                  for i in range(8)) and data[32] == 0)
    check("pixels pack MSB-first (top bar of 3 = 0xFF 0xFF)",
          data[1] == 0xFF and data[2] == 0xFF)
    L = LI.logical_pixels(t, 2, 0, 1, 1)
    check("logical_pixels reads the tile back", L == rows)
    for orient in ('portrait_left', 'portrait_right'):
        P = LI.rotate(L, orient)
        ok = True
        for ly in range(8):
            for lx in range(8):
                # image-local form of the screen maps (top-left at 0, 0)
                px, py = ((7 - ly, lx) if orient == 'portrait_left'
                          else (ly, 7 - lx))
                ok &= P[py][px] == L[ly][lx]
        check("%s puts every pixel where the screen map says" % orient, ok)
    # a non-square rectangle swaps its physical size and keeps the logical one
    two = t + t                                  # a 16x8 named sprite
    b = LI.bake(two, 2, 0, 2, 1, 'portrait_left')
    lines = 0
    i = 2
    while b[i]:
        lines += 1
        i += b[i]
    check("16x8 turned is 16 physical lines of 8 px, header still 16x8",
          b[:2] == bytes([16, 8]) and lines == 16 and b[2] == 4)
    # the per-tile tables
    sheet = t * 6                                 # 6 tiles: a 2x2 rect + 2 loose
    blobs, table, one = LI.sheet_images(sheet, 2, [(0, 2, 2)], None, singles=True)
    check("the rectangle's FIRST tile draws the whole 16x16",
          blobs[table[0]][:2] == bytes([16, 16]))
    check("its inner tiles draw nothing", table[1:4] == [None, None, None])
    check("a tile outside every rectangle draws its own 8x8",
          blobs[table[4]][:2] == bytes([8, 8]) and blobs[table[5]][:2] == bytes([8, 8]))
    check("singles: EVERY tile has its own 8x8 (list / mask children)",
          all(blobs[one[k]][:2] == bytes([8, 8]) for k in range(6)))
    _b, _t, none = LI.sheet_images(sheet, 2, [(0, 2, 2)])
    check("no singles table unless asked", none is None)
    t4 = bytes(range(32))
    b4 = LI.bake(t4, 4, 0, 1, 1)
    check("4bpp rows are [0x06, 4 bytes, 0x00] and copy the nibbles",
          b4[2] == 6 and b4[3:7] == t4[0:4] and b4[7] == 0)


_SPR = ('module "main" {\n    import "graphics.sprite"\n    import "graphics.bkg"\n'
        '    import "platform.input"\n    import "platform.video"\n'
        '    const T: array[u8, 16] = [255, 0, 255, 0, 255, 0, 255, 0, 255, 0, 255, 0, 255, 0, 255, 0]\n'
        '    var row: array[u8, 4]\n'
        '    function main() {\n'
        '        bkg.set_data(0, 1, T)\n        bkg.set_tiles(0, 0, 4, 1, row)\n'
        '        bkg.move(1, 2)\n'
        '        sprite.set_data(0, 1, T)\n        sprite.set_tile(0, 0)\n'
        '        sprite.set_prop(0, FLIP_X)\n        sprite.move(0, 10, SCREEN_HEIGHT - 20)\n'
        '        loop { if input.held(INPUT_UP) { sprite.move(0, 1, 1) } video.wait_vblank() }\n'
        '    }\n    export main\n}\n')


def _c(**kw):
    return MosaikCompiler().compile_program([("m.mos", _SPR)], platform="lynx", **kw)


def test_codegen():
    base = _c()
    check("default compiles", not base.startswith("Compilation error"))
    same = _c(lynx_sprites='tiles', lynx_orientation='landscape')
    check("explicit defaults are byte-identical", same == base)
    check("the default has no baked engine",
          "gbs_img_table" not in base and "gbs_spr_place" not in base
          and "gbs_turn_pad" not in base and "gbs_bkg_rot" not in base)
    # the 8x8 engine's flip: a Suzy sprite mirrors about its reference point,
    # so the reference moves to the far edge (it drew FLIP_X 7 px left)
    check("8x8 engine: a flip moves the reference by 7",
          "if (d & HFLIP) q->s.hpos += (c0 & HFLIP) ? 7 : -7;" in base
          and "if (d & VFLIP) q->s.vpos += (c0 & VFLIP) ? 7 : -7;" in base)
    check("8x8 engine: a move onto a flipped axis adds 7",
          "if (q->s.sprctl0 & HFLIP) hx += 7;" in base
          and "if (q->s.sprctl0 & VFLIP) vy += 7;" in base)
    check("8x8 engine: the present culls on the drawn 8x8",
          "if (q->s.sprctl0 & HFLIP) lx -= 7;" in base)
    whole = _c(lynx_sprites='whole')
    check("whole compiles", not whole.startswith("Compilation error"), )
    check("whole: the baked engine and its image lookup",
          "gbs_img_table" in whole and "gbs_spr_place" in whole
          and "gbs_conv_tile" not in whole)
    check("whole landscape keeps the landscape screen and pad",
          "#define SCREEN_WIDTH  160" in whole and "gbs_turn_pad" not in whole)
    for orient in ('portrait_left', 'portrait_right'):
        c = _c(lynx_orientation=orient)
        check("%s compiles" % orient, not c.startswith("Compilation error"))
        check("%s: the program sees 102x160" % orient,
              "#define SCREEN_WIDTH  102" in c and "#define SCREEN_HEIGHT 160" in c)
        check("%s: the d-pad turns" % orient, "gbs_turn_pad(" in c)
        check("%s: tiles turn on upload, the scroll maps" % orient,
              "gbs_bkg_rot(" in c and "void gbs_move_bkg" in c)
        check("%s: a streamed row re-composes single columns" % orient,
              "gbs_bkg_compose_cols(p, pr, c, (uint8_t)(c + 1))" in c)
    gb = MosaikCompiler().compile_program([("m.mos", _SPR)], platform="gameboy",
                                          lynx_sprites='whole',
                                          lynx_orientation='portrait_left')
    gb0 = MosaikCompiler().compile_program([("m.mos", _SPR)], platform="gameboy")
    check("the knobs are ignored off the Lynx", gb == gb0)


def test_narrow_strips():
    """Portrait: screen-width strips slid by the camera, not 52-tile ones."""
    for orient in ('portrait_left', 'portrait_right'):
        c = _c(lynx_orientation=orient)
        check("%s: strips are screen + 1 wide" % orient,
              "#define GBS_BKG_STRIP_W   21" in c)
        check("%s: the present slides them, one memmove a strip" % orient,
              "gbs_bkg_slide();" in c and "memmove(o + 1, o + 1 + 2," in c
              and "memmove(o + 1 + 2, o + 1," in c)
        check("%s: compose reads the map through the base column" % orient,
              "map_r[(uint8_t)(gbs_bkg_cb + cx) & 31]" in c)
    land = _c()
    check("landscape keeps its full-period strips and no slide",
          "gbs_bkg_slide" not in land and "#define GBS_BKG_STRIP_W   52" in land)


_SHEET = ('module "main" {\n    import "graphics.sprite"\n%s'
          '    function main() {\n        sprite.set_data(0, obj_tile_count, obj_tiles)\n'
          '        sprite.set_tile(0, 0)\n%s        loop { }\n    }\n    export main\n}\n')


def test_raw_tiles():
    """A sheet named only by sprite.set_data is drawn from its images: the raw
    tile bytes are not emitted. Named anywhere else, they stay."""
    data = bytes(range(64))                       # 4 tiles, 2bpp
    kw = dict(platform="lynx", assets=[("obj", data, 2)],
              sheet_rects={"obj": [(0, 2, 2)]}, lynx_sprites='whole')
    c = MosaikCompiler().compile_program([("m.mos", _SHEET % ("", ""))], **kw)
    check("sprite-only sheet compiles", not c.startswith("Compilation error"))
    check("...without its raw tiles",
          "const uint8_t obj_tiles[" not in c
          and "#define obj_tiles ((const uint8_t *)gbs_lt_obj)" in c)
    check("...and the lookup takes the image table itself",
          "if (d == (const uint8_t *)gbs_lt_obj) return gbs_lt_obj;" in c)
    both = _SHEET % ('    import "graphics.bkg"\n', "        bkg.set_data(0, obj_tile_count, obj_tiles)\n")
    c2 = MosaikCompiler().compile_program([("m.mos", both)], **kw)
    check("a sheet the background also uploads keeps its raw tiles",
          "const uint8_t obj_tiles[" in c2 and "#define obj_tiles" not in c2)
    c3 = MosaikCompiler().compile_program([("m.mos", _SHEET % ("", ""))],
                                          platform="lynx", assets=[("obj", data, 2)])
    check("the 8x8 engine keeps them", "const uint8_t obj_tiles[" in c3)


def test_refusals():
    wide = _SPR.replace('    import "platform.input"\n',
                        '    import "platform.input"\n    import "engine.scroll"\n')
    # the wide engine keys on the IMPORT; a stub stands in for lib/engine
    stub = 'module "engine.scroll" {\n    export ping\n    function ping() { }\n}\n'
    c = MosaikCompiler().compile_program([("m.mos", wide), ("s.mos", stub)],
                                         platform="lynx",
                                         lynx_orientation='portrait_left')
    check("a wide streamed level in portrait is refused by name",
          c.startswith("Compilation error") and "portrait" in c)


def test_config():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from mosaik8_build import BuildConfig
    d = tempfile.mkdtemp(prefix="lxcfg_")
    p = os.path.join(d, "mosaik.toml")

    def cfg(extra):
        with open(p, "w") as f:
            f.write('[project]\nname = "x"\n[build]\n' + extra)
        return BuildConfig(p)

    c = cfg("")
    check("defaults: tiles, landscape",
          c.get_lynx_sprites() == 'tiles' and c.get_lynx_orientation() == 'landscape')
    c = cfg('lynx_sprites = "whole"\nlynx_orientation = "portrait_right"\n')
    check("set values read back",
          c.get_lynx_sprites() == 'whole' and c.get_lynx_orientation() == 'portrait_right')
    for key, bad in (("lynx_sprites", "big"), ("lynx_orientation", "upside")):
        c = cfg('%s = "%s"\n' % (key, bad))
        try:
            (c.get_lynx_sprites if key == "lynx_sprites" else c.get_lynx_orientation)()
            ok = False
        except ValueError:
            ok = True
        check("an unknown %s is refused" % key, ok)
    check("both keys are known to the config-honesty check",
          {'lynx_sprites', 'lynx_orientation'} <= BuildConfig.APPLIED_KEYS['build'])


def main():
    test_images()
    test_codegen()
    test_narrow_strips()
    test_raw_tiles()
    test_refusals()
    test_config()
    print()
    if FAILS:
        print("FAILED (%d): %s" % (len(FAILS), "; ".join(FAILS)))
        return 1
    print("lynx_native_modes_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
