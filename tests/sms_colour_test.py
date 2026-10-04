#!/usr/bin/env python3
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""SMS / Game Gear COLOUR off the same 2bpp art (gbs-fidelity Stage I gap 2).

Those consoles have no per-tile attribute layer, but their VDP stores every
background/sprite tile at 4bpp and GBDK's z80 port exposes the 2bpp
expansion's nibble map (set_tile_2bpp_data) -- so a 2bpp tile can land at ANY
four of the 16 CRAM entries. The engine rides that:

- `bkg.set_data_pal(first, count, data, off, slot)` uploads 2bpp tiles
  rendered through background palette SLOT (CRAM entries slot*4..slot*4+3);
  `sprite.set_data_pal(first, count, data, slot)` is the sprite mirror, with
  pixel 0 staying the VDP's transparent. Plain uploads everywhere else.
- `palette.load_bkg_set` / `load_sprite_set` are REAL on SMS/GG: slots 0..3
  land in CRAM (16 entries a layer = four 4-colour slots), so the colour
  tier's per-scene `scenes.load_palettes` loads real hardware there.
- The scene transpiler's per-scene tileset upload forks `if platform ==
  "sms"/"gamegear"` for a COLOURED world: each tile uploads through
  `tile_pal(scene, t)`. Every other console keeps the plain upload
  (the fork folds), and a colourless world is byte-identical everywhere.

Verifies emission on sms/gamegear, the fold on the GB family, and the
byte-identical rule for colourless worlds.
"""

import mosaik_assets as M
import mosaik_scenes.loaders as L
from mosaik_scenes.transpile import transpile
from mosaik import MosaikCompiler


def _tileset(d, name):
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    idx = [[(x // 8) % 4 for x in range(32)] for _ in range(8)]
    M.write_png_indexed(os.path.join(d, name), 32, 8, idx, pal)


def _world(colour=True, extra_world=""):
    """A world with PER-SCENE tilesets (the gbs-import shape)."""
    d = tempfile.mkdtemp()
    _tileset(d, "t.png")
    _tileset(d, "a.png")
    _tileset(d, "b.png")
    body = ['[world]', 'module = "scenes"', 'map_w = 4', 'map_h = 2',
            'vm = true', extra_world, '',
            '[tileset]', 'png = "t.png"', '',
            '[kinds]', 'player = 0', 'npc = 1', '']
    if colour:
        body += ['[[palette]]', 'name = "ui"',
                 'colors = ["F8E8C8", "D89048", "A82820", "082048"]', '',
                 '[[palette]]', 'name = "water"',
                 'colors = [[40, 80, 200], [20, 40, 120], [10, 20, 60], [0, 0, 0]]',
                 '', '[kind_palettes]', 'npc = 3', '']
    for i, (nm, ts) in enumerate((("room_a", "a.png"), ("room_b", "b.png"))):
        body += ['[[scene]]', 'name = "%s"' % nm,
                 'map = [[0,1,2,3],[3,2,1,0]]',
                 'collision = [[0,0,0,0],[1,1,1,1]]',
                 'tileset = "%s"' % ts]
        if colour:
            body += ['bkg_palettes = [0, 1]', 'spr_palettes = [1, 0, 0, 1]',
                     'tile_palette = [0, 0, 1, 1]']
        if i == 0:
            body += ['[[scene.object]]', 'kind = "player"', 'x = 8', 'y = 8']
        body.append('')
    with open(os.path.join(d, "world.toml"), "w", encoding="utf-8") as f:
        f.write("\n".join(body))
    world, base = L.load_world(os.path.join(d, "world.toml"))
    return transpile(world, base)


_SHELL = '''module "main" {
    import "scenes"
    import "graphics.bkg"
    import "graphics.sprite"
    import "graphics.palette"
    import "platform.video"
    import "platform.assets"

    function main() {
        scenes.load_palettes(0)
        scenes.paint(0)
        video.enable_lcd()
        loop { }
    }
}
'''


def _compile(scenes_mos, platform):
    return MosaikCompiler().compile_program(
        [("scenes.mos", scenes_mos), ("main.mos", _SHELL)], platform=platform)


def test_slotted_upload_emitted():
    col = _world()
    assert 'if platform == "sms" or platform == "gamegear" {' in col
    # ts_slots fills the RAM slot buffer (banked), then ONE helper call
    # uploads every tile through its slot.
    assert "ts_slots(scene," in col
    assert "bkg.set_data_pal(0," in col
    assert "var TS_PAL_BUF: array[u8," in col
    print("  [PASS] a coloured per-scene-tileset world forks the upload")


def test_table_mode_forks_too():
    """paint_table + stream (the gbs-import shape): the table-driven upload
    forks the same way, through the warmed range window."""
    col = _world(extra_world="paint_table = true\nstream = true")
    assert 'if platform == "sms" or platform == "gamegear" {' in col
    assert "bkg.set_data_pal(0, tsc, assets.ptr_range(" in col
    for plat in ("sms", "gameboy"):
        c = _compile(col, plat)
        assert ("gbs_bkg_data_pal" in c) == (plat == "sms"), plat
    print("  [PASS] paint_table+stream: the table-driven upload forks too")


def test_colourless_world_untouched():
    plain = _world(colour=False)
    for tok in ("set_data_pal", 'platform == "sms"'):
        assert tok not in plain, "colourless world emitted %r" % tok
    print("  [PASS] a colourless world keeps the plain upload")


def test_sms_c_output():
    col = _world()
    for plat in ("sms", "gamegear"):
        c = _compile(col, plat)
        # the slotted upload helper, built on the port's nibble map
        assert "gbs_bkg_data_pal" in c, plat
        assert "set_tile_2bpp_data" in c, plat
        # the CRAM load happens in BANKED scenes code (ONE native
        # set_palette for both banks), NOT through resident prelude
        # loaders / gbs_rgb - every prelude byte is bank-0 rent this console
        # cannot spare. The Game Gear converts its portable 5-5-5 words on
        # the way with scenes_cram; the SMS is too shallow to round at
        # runtime and its table is fitted at BUILD time, so the load is a
        # plain copy with no converter at all (test_sms_palette_is_fitted).
        assert ("scenes_cram" in c) == (plat == "gamegear"), plat
        assert "set_palette(0, 2," in c, plat
        assert "gbs_load_bkg_set" not in c, plat
        assert "nowhere to go" not in c, plat
        # the four nibble maps are a TABLE (a per-tile 16-bit multiply is
        # the most expensive thing on this path, in resident code)
        assert "{ 0x3210, 0x7654, 0xBA98, 0xFEDC }" in c, plat
        # ONE shared upload function (a definition, plus its prototype), not
        # a copy inlined into each of paint() and warm(): it reads the
        # residency seam, so it is pinned RESIDENT and a second copy is
        # bank-0 image
        assert c.count("void scenes_ts_upload(uint8_t scene) {") == 1, plat
    print("  [PASS] sms/gamegear C: slotted upload + banked CRAM loading")


def test_load_bkg_set_api_is_real_on_sms():
    """A HAND-WRITTEN program calling palette.load_bkg_set still gets real
    CRAM on SMS/GG (the generated path avoids the helpers purely for
    resident-size reasons)."""
    shell = '''module "main" {
    import "graphics.palette"
    import "platform.video"

    const PAL: array[u16, 32] = [
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 ]

    function main() {
        palette.load_bkg_set(0, 4, PAL, 0)
        video.enable_lcd()
        loop { }
    }
}
'''
    c = MosaikCompiler().compile_program([("main.mos", shell)],
                                         platform="sms")
    assert "gbs_load_set(" in c
    assert "set_palette_entry(" in c
    assert "nowhere to go" not in c
    print("  [PASS] palette.load_bkg_set is real CRAM on sms")


def test_fold_on_other_consoles():
    col = _world()
    for plat in ("gameboy", "gameboy_color", "nes"):
        c = _compile(col, plat)
        assert "gbs_bkg_data_pal" not in c, \
            "%s kept the folded-away SMS arm" % plat
        assert "bkg.set_data_pal" not in c, plat
    print("  [PASS] the fork folds away on the GB family / NES")


def test_sprite_slot_upload_needs_no_helper():
    """A sprite SHEET uploads whole through one slot, so it needs no engine
    helper at all: `palette.set_2bpp` is the port's own 2bpp -> CRAM nibble
    map (which four entries a tile's pixel values expand onto), so the
    ordinary sprite.set_data lands the sheet on slot*4.. . An inline no-op on
    every other GBDK console, so the call costs nothing there."""
    shell = '''module "main" {
    import "graphics.sprite"
    import "graphics.palette"
    import "platform.video"

    const SHEET: array[u8, 32] = [
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15 ]

    function main() {
        palette.set_2bpp(0xBA90)
        sprite.set_data(0, 2, SHEET)
        palette.set_2bpp(0x3210)
        video.enable_lcd()
        loop { }
    }
}
'''
    for plat in ("sms", "gamegear", "gameboy", "nes"):
        c = MosaikCompiler().compile_program([("main.mos", shell)],
                                             platform=plat)
        assert not c.startswith("Compilation error"), (plat, c.splitlines()[0])
        assert "set_2bpp_palette(%dU)" % 0xBA90 in c, plat   # a u16 literal carries U (review L-2)
        # no engine helper is emitted for it on ANY console
        assert "gbs_spr_data_pal" not in c, plat
    print("  [PASS] the sprite slot rides set_2bpp_palette, no helper")


def test_sms_palette_is_fitted():
    """The SMS palette table is FITTED at build time, not rounded at runtime.

    2 bits a channel is 64 colours, shallow enough that independent
    per-channel nearest -- which is Euclidean-nearest in RGB, so it looks
    optimal -- sends every desaturated mid-tone onto the grey diagonal. It
    greyed out the reference-engine sample's pink parallax sky band. The fitter
    picks a whole 4-colour palette at once and refuses to grey a chromatic
    entry; the Game Gear (4096 colours) needs none of it and keeps the
    portable table plus the runtime converter."""
    # A dusty rose: nearest-per-channel is (170,170,170), a flat grey.
    got = M.fit_palette_levels([(208, 136, 176)], 4)[0]
    assert got[0] != got[1] or got[1] != got[2], \
        "a chromatic colour was allowed to land on grey: %r" % (got,)
    # A near-neutral one still may (and should) go grey.
    assert M.fit_palette_levels([(160, 165, 162)], 4)[0] == (2, 2, 2)
    # Distinct entries stay distinct, and duplicates stay equal (a reference-engine
    # sprite palette repeats its first colour).
    fit = M.fit_palette_levels(
        [(247, 189, 239), (247, 189, 239), (224, 80, 144), (58, 58, 115)], 4)
    assert fit[0] == fit[1], fit
    assert len(set(fit)) == 3, fit

    col = _world()
    # The SMS arm carries a u8 (one CRAM byte per entry) table and no
    # converter; the other arm keeps the portable 5-5-5 words.
    assert 'if platform == "sms" {' in col
    assert "const BKG_PAL: array[u8," in col
    assert "const BKG_PAL: array[u16," in col
    sms = _compile(col, "sms")
    assert "scenes_cram" not in sms
    gg = _compile(col, "gamegear")
    assert "scenes_cram" in gg
    print("  [PASS] the SMS palette table is fitted at build time")


def test_shared_tileset_world_uploads_through_slots():
    """A coloured world WITHOUT per-scene tilesets (every hand-made world)
    re-uploads its shared tileset through the scene's tile slots in paint()
    on SMS/GG, and nowhere else."""
    d = tempfile.mkdtemp()
    _tileset(d, "t.png")
    body = ['[world]', 'module = "scenes"', 'map_w = 4', 'map_h = 2', '',
            '[tileset]', 'png = "t.png"', '', '[kinds]', 'player = 0', '',
            '[[palette]]', 'name = "a"',
            'colors = ["F8E8C8", "D89048", "A82820", "082048"]', '',
            '[[palette]]', 'name = "b"',
            'colors = ["C8E8F8", "4890D8", "2028A8", "080820"]', '',
            '[[scene]]', 'name = "room"', 'map = [[0,1,2,3],[3,2,1,0]]',
            'bkg_palettes = [0, 1]', 'tile_palette = [0, 1, 0, 1]', '']
    with open(os.path.join(d, "world.toml"), "w", encoding="utf-8") as f:
        f.write("\n".join(body))
    world, base = L.load_world(os.path.join(d, "world.toml"))
    col = transpile(world, base)
    assert "function ts_upload(scene: u8) {" in col
    assert "bkg.set_data_pal(0, TILE_COUNT, TILESET, TS_PAL_BUF)" in col
    for plat in ("sms", "gamegear", "gameboy_color"):
        c = _compile(col, plat)
        assert not c.startswith("Compilation error"), (plat, c[:200])
        assert ("gbs_bkg_data_pal" in c) == (plat != "gameboy_color"), plat
    print("  [PASS] a shared-tileset world uploads through its slots on SMS/GG")


if __name__ == "__main__":
    test_shared_tileset_world_uploads_through_slots()
    test_slotted_upload_emitted()
    test_table_mode_forks_too()
    test_colourless_world_untouched()
    test_sms_palette_is_fitted()
    test_sms_c_output()
    test_load_bkg_set_api_is_real_on_sms()
    test_fold_on_other_consoles()
    test_sprite_slot_upload_needs_no_helper()
    print("=" * 50)
    print("All SMS/GG colour checks passed")
