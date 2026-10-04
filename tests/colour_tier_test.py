#!/usr/bin/env python3
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""The per-tile background palette COLOUR tier (Stage E of the reference-engine
fidelity programme).

The model: a world names a palette LIBRARY, each scene says which library
palette its 8 hardware background / sprite slots hold, and each TILE of its
tileset says which slot it renders with. A map cell's attribute is the slot of
the tile it holds, which is what makes colour compose with `stream` /
`paint_table` / `metatiles` unchanged -- every reader goes through the
`map_tile` it already used.

Verifies:
- the three new stdlib verbs exist on every console (real where the hardware
  is, an honest no-op elsewhere) and the same source compiles for all nine;
- the transpiler emits BKG_PAL / SPR_PAL / load_palettes / TILE_PAL / tile_pal
  / paint_attrs / KIND_PAL only for a world that colours something;
- a colourless world is BYTE-IDENTICAL (the additive rule);
- colour composes with stream / paint_table / metatiles;
- `sprite.set_prop` no longer clobbers the palette bits a `sprite.set_palette`
  wrote (the flip used to reset every actor to OBJ palette 0);
- the concatenated per-scene tileset table CHUNKS rather than emitting one
  symbol past a ROM bank.
"""

import mosaik_assets as M
import mosaik_scenes.loaders as L
from mosaik_scenes.transpile import transpile, _rgb555
from mosaik import MosaikCompiler, PLATFORM_CAPS

CONSOLES = ("gameboy", "gameboy_color", "analogue_pocket", "megaduck",
            "sms", "gamegear", "nes", "lynx", "pce")
TILE_PAL_CONSOLES = ("gameboy_color", "analogue_pocket", "nes", "pce")


def _tileset(d, name="t.png", colors=4):
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)][:colors]
    idx = [[(x // 8) % colors for x in range(32)] for _ in range(8)]
    M.write_png_indexed(os.path.join(d, name), 32, 8, idx, pal)


def _world(colour=True, extra_world="", scene_extra=""):
    d = tempfile.mkdtemp()
    _tileset(d)
    body = ['[world]', 'module = "scenes"', 'map_w = 4', 'map_h = 2',
            'vm = true', extra_world, '',
            '[tileset]', 'png = "t.png"', '',
            '[kinds]', 'player = 0', 'npc = 1', '']
    if colour:
        body += ['[[palette]]', 'name = "grass"',
                 'colors = ["F8E8C8", "D89048", "A82820", "082048"]', '',
                 '[[palette]]', 'name = "water"',
                 'colors = [[40, 80, 200], [20, 40, 120], [10, 20, 60], [0, 0, 0]]',
                 '', '[kind_palettes]', 'npc = 3', '']
    for i, (nm, mp) in enumerate((("room_a", "[[0,1,2,3],[3,2,1,0]]"),
                                  ("room_b", "[[1,1,1,1],[2,2,2,2]]"))):
        body += ['[[scene]]', 'name = "%s"' % nm, 'map = %s' % mp,
                 'collision = [[0,0,0,0],[1,1,1,1]]', scene_extra]
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


def test_additive():
    """A world that colours nothing emits none of it."""
    plain = _world(colour=False)
    for tok in ("BKG_PAL", "SPR_PAL", "load_palettes", "TILE_PAL", "tile_pal",
                "paint_attrs", "KIND_PAL"):
        assert tok not in plain, "colourless world emitted %s" % tok
    print("  [PASS] a colourless world is untouched by the colour tier")


def test_emission():
    col = _world()
    for tok in ("const PAL_SLOTS: u8 = 8", "const PAL_STRIDE: u8 = 32",
                "const BKG_PAL:", "const SPR_PAL:", "function load_palettes(",
                "const TILE_PAL:", "const TILE_PAL_OFF:", "function tile_pal(",
                "function paint_attrs(", "const KIND_PAL:"):
        assert tok in col, "missing %r" % tok
    # The library colours land as portable 5-5-5 words, scene-major.
    assert str(_rgb555("F8E8C8")) in col
    # Two scenes x 8 slots x 4 colours.
    assert "const BKG_PAL: array[u16, 64]" in col, col[:0]
    assert "const SPR_PAL: array[u16, 64]" in col
    # Per-KIND sprite slot: kind 1 (npc) -> slot 3, kind 0 (player) -> 0.
    body = col[col.index("const KIND_PAL:"):]
    assert body.split("= [", 1)[1].split("]")[0].strip() == "0, 3", body[:120]
    print("  [PASS] the tables, the loader and the two selectors are emitted")


def test_every_console_compiles():
    """One coloured world, nine consoles: real where the hardware is, an
    honest no-op elsewhere -- so a generated room painter needs no fork."""
    col = _world()
    shell = '''module "main" {
    import "scenes"
    import "graphics.bkg"
    import "platform.video"

    function main() {
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        scenes.load_palettes(0)
        scenes.paint(0)
        scenes.paint_attrs(0)
        video.enable_lcd()
        loop { video.wait_vblank() }
    }
}
'''
    for p in CONSOLES:
        c = MosaikCompiler().compile_program(
            [("scenes.mos", col), ("main.mos", shell)], platform=p)
        assert not c.startswith("Compilation error"), (p, c.splitlines()[0])
        if p not in ("sms", "gamegear"):
            assert "gbs_load_bkg_set" in c, p
        real = PLATFORM_CAPS[p]["has_tile_palettes"]
        assert real == (p in TILE_PAL_CONSOLES), p
        if p == "gameboy_color":
            # bkg.set_attrs IS GBDK's set_bkg_attributes there (no wrapper).
            assert "set_bkg_attributes(" in c, p
        elif p in ("sms", "gamegear"):
            # No attribute layer there: paint_attrs' whole per-cell walk
            # FOLDS AWAY (colour is baked into the tile upload instead -
            # tests/sms_colour_test.py), and load_palettes loads the CRAM
            # from BANKED scenes code (the port's own set_palette) instead
            # of the resident prelude loaders. The Game Gear converts its
            # 5-5-5 words there with scenes_cram; the SMS carries a table
            # already fitted at BUILD time, so it has no converter at all.
            assert ("scenes_cram" in c) == (p == "gamegear"), p
            assert "set_palette(0, 2," in c, p
            assert "gbs_load_bkg_set" not in c, p
            # A SHARED-tileset world colours its tiles through the slotted
            # upload too (it used to be per-scene tilesets only, so every
            # hand-made world drew all of its tiles through slot 0 here).
            assert "gbs_bkg_data_pal(" in c, p
        elif p == "pce":
            # The attribute upload is REAL: each cell's slot rides the
            # engine's per-cell palette writer into the BAT (it was a void
            # stub, so every tile drew through slot 0).
            assert "gbs_bkg_palette_fill((uint8_t)(x + cx)" in c, p
        elif not real:
            assert "(void)x; (void)y; (void)w; (void)h; (void)data;" in c, p
        if p == "lynx":
            # The set loaders fill what the pen partition holds (bkg slot 0,
            # sprite slots 0..3) and STOP, since the sprite setter masks `& 3`.
            assert "(void)slot; (void)count; (void)colors; (void)off;" not in c, p
            assert ">= 4) break;" in c and ">= 1) break;" in c, p
        if p in ("gameboy_color", "gameboy", "nes", "pce", "lynx"):
            assert "gbs_bkg_data_pal(" not in c, p
    print("  [PASS] one coloured world compiles for all %d consoles"
          % len(CONSOLES))


def test_composes_with_residency():
    """Colour rides `map_tile`, so it needs nothing new from the residency
    modes -- the whole point of a per-TILE table over a per-cell layer."""
    for label, extra in (("stream", "stream = true"),
                         ("paint_table", "paint_table = true"),
                         ("metatiles", "metatiles = true"),
                         ("stream+paint_table",
                          "stream = true\npaint_table = true"),
                         ("all three",
                          "stream = true\npaint_table = true\nmetatiles = true")):
        col = _world(extra_world=extra)
        assert "function paint_attrs(" in col, label
        assert "tile_pal(scene, map_tile(scene, base + x))" in col, label
        c = MosaikCompiler().compile_program([("scenes.mos", col)],
                                             platform="gameboy_color")
        assert not c.startswith("Compilation error"), (label, c.splitlines()[0])
    print("  [PASS] colour composes with stream / paint_table / metatiles")


def test_set_prop_keeps_the_palette():
    """The flip must not erase the colour. vm.canim re-asserts FLIP_X through
    sprite.set_prop on every facing change; before the split that wrote the
    whole OAM byte and every actor snapped back to palette 0."""
    src = '''module "main" {
    import "graphics.sprite"
    import "graphics.palette"
    import "platform.video"

    function main() {
        sprite.set_meta(0, 0, 2, 2)
        sprite.set_palette(0, 3)
        sprite.set_prop(0, FLIP_X)
        video.enable_lcd()
        loop { video.wait_vblank() }
    }
}
'''
    for p in ("gameboy_color", "nes"):
        c = MosaikCompiler().compile_program([("m.mos", src)], platform=p)
        assert not c.startswith("Compilation error"), (p, c.splitlines()[0])
        # set_prop KEEPS each slot's own palette bits; set_palette writes them
        # through the raw fan, which it alone owns.
        assert "#define GBS_KEEP_PAL(slot, prop)" in c, p
        setter = c[c.index("void gbs_set_sprite_prop"):]
        setter = setter[:setter.index("\n}")]
        assert "GBS_KEEP_PAL" in setter, p
        pal = c[c.index("void gbs_sprite_palette"):]
        pal = pal[:pal.index("\n}")]
        assert "gbs_fan_prop(nb, " in pal, p
        raw = c[c.index("static void gbs_fan_prop"):]
        raw = raw[:raw.index("\n}")]
        assert "GBS_KEEP_PAL" not in raw, p
    # A program that never colours a sprite keeps the single, unmerged setter.
    plain = src.replace('    import "graphics.palette"\n', "") \
               .replace("        sprite.set_palette(0, 3)\n", "")
    c = MosaikCompiler().compile_program([("m.mos", plain)],
                                         platform="gameboy_color")
    assert "gbs_fan_prop" not in c
    assert "GBS_KEEP_PAL" not in c
    print("  [PASS] set_prop keeps the palette; a colourless program is unchanged")


def test_meta_palettes():
    """sprite.set_meta_palettes: a palette per CELL of ONE metasprite, so an
    actor can wear several (the reference engine's player: hair, face, body). Nothing is
    remembered - each child's palette lives in its own OAM byte and the re-fans
    preserve it, because a remembered pointer into a BANKED table dangles."""
    src = '''module "main" {
    import "graphics.sprite"
    import "graphics.palette"
    import "platform.video"

    const CELLPAL: array[u8, 8] = [1, 0, 1, 0, 2, 2, 2, 2]

    function main() {
        sprite.set_meta(0, 0, 2, 4)
        sprite.set_meta_palettes(0, 2, 4, CELLPAL, 0)
        sprite.set_prop(0, FLIP_X)
        video.enable_lcd()
        loop { video.wait_vblank() }
    }
}
'''
    for p in CONSOLES:
        c = MosaikCompiler().compile_program([("m.mos", src)], platform=p)
        assert not c.startswith("Compilation error"), (p, c.splitlines()[0])
        assert "gbs_set_meta_pal" in c, p
        if p in ("sms", "gamegear"):
            # one sprite palette there: an honest no-op
            assert "(void)nb; (void)w; (void)h;" in c, p
            continue
        body = c[c.index("void gbs_set_meta_pal"):]
        body = body[:body.index("\n}")]
        assert "gbs_meta_pal[" not in c, "%s remembers the map" % p
        if PLATFORM_CAPS[p]["framework"] == "gbdk":
            # ... and set_meta re-tiles every child every frame, so its fan
            # must not flatten the colours it just wrote.
            meta = c[c.index("void gbs_set_metasprite"):]
            meta = meta[:meta.index("\n}")]
            assert "GBS_KEEP_PAL" in meta, p
    # 8x16 OBJ mode: an object is a vertical PAIR of cells, enumerated
    # column-major, so cell = (2*pair)*w + col.
    c = MosaikCompiler().compile_program([("m.mos", src)],
                                         platform="gameboy_color",
                                         obj_8x16=True)
    assert "m[(uint8_t)((p << 1) * w + c)]" in c
    print("  [PASS] per-cell sprite palettes, on every console + in 8x16 mode")


def test_tileset_table_chunks_at_a_bank():
    """A concatenated per-scene tileset table may not exceed one ROM bank (a
    banked array is read in place while its bank is mapped). Past that it is
    split into chunks with a per-scene TS_BLK, instead of emitting a symbol
    the GB linker would refuse."""
    from mosaik_scenes.transpile import _TS_CHUNK
    d = tempfile.mkdtemp()
    _tileset(d)
    # Per-scene tilesets, each distinct, together well past one bank.
    n_scenes, tiles_each = 12, 120
    body = ['[world]', 'module = "scenes"', 'map_w = 2', 'map_h = 1',
            'paint_table = true', '', '[tileset]', 'png = "t.png"', '',
            '[kinds]', 'player = 0', '']
    for i in range(n_scenes):
        nm = "ts%d.png" % i
        pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
        # Tile 0 encodes the scene index in its pixels, so no two scene
        # tilesets dedup onto one another (identical blocks legitimately share).
        idx = [[(((i >> (y % 4)) & 1) * 3 if x < 8 else ((x // 8) + y) % 4)
                for x in range(tiles_each * 8)] for y in range(8)]
        M.write_png_indexed(os.path.join(d, nm), tiles_each * 8, 8, idx, pal)
        body += ['[[scene]]', 'name = "s%d"' % i, 'map = [[0,1]]',
                 'tileset = "%s"' % nm, '']
    with open(os.path.join(d, "world.toml"), "w", encoding="utf-8") as f:
        f.write("\n".join(body))
    world, base = L.load_world(os.path.join(d, "world.toml"))
    out = transpile(world, base)
    assert n_scenes * tiles_each * 16 > _TS_CHUNK, "test data no longer overflows"
    assert "const TILESETS2:" in out, "the table did not chunk"
    assert "const TS_BLK:" in out
    for name in ("TILESETS", "TILESETS2"):
        n = int(out.split("const %s: array[u8, " % name)[1].split("]")[0])
        assert n <= _TS_CHUNK, "%s is %d bytes, past a ROM bank" % (name, n)
    # One chunk (every world before this) emits no TS_BLK at all.
    one = _world(extra_world="paint_table = true",
                 scene_extra='tileset = "t.png"')
    assert "TS_BLK" not in one and "TILESETS2" not in one
    print("  [PASS] the tileset table chunks at a ROM bank; one chunk is unchanged")


if __name__ == "__main__":
    print("Colour tier (per-tile background palettes)")
    test_additive()
    test_emission()
    test_every_console_compiles()
    test_composes_with_residency()
    test_set_prop_keeps_the_palette()
    test_meta_palettes()
    test_tileset_table_chunks_at_a_bank()
    print("All colour-tier checks passed")
