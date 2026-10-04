#!/usr/bin/env python3
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""The 8 -> 4 palette slot FOLD and the project default slot rows
(`mosaik_scenes/palette_fold.py`).

SMS, Game Gear, NES and PC Engine address four palette slots a layer and mask
a slot with `& 3`, so a tile on slot 5 used to show slot 1's colours there.
Verifies:
- the fold RULE (slot 0 pinned, used 1..3 in place, 4..7 into free positions
  by usage, the rest onto the nearest palette, same-palette slots are one);
- a world that uses no slot >= 4, or whose project builds for none of the four
  consoles, is byte-identical (no fork);
- a folding world forks TILE_PAL / BKG_PAL / SPR_PAL / KIND_PAL / KIND_TPAL,
  compiles on every console, and the GB-family C is IDENTICAL to the same
  world in a project that does not fold (the fork costs them nothing);
- `[world] default_bkg_palettes` / `default_spr_palettes` fill a scene that
  has no rows, and are absent-identical.
"""

import mosaik_assets as M
import mosaik_scenes.loaders as L
from mosaik_scenes.transpile import transpile
from mosaik_scenes import palette_fold as F
from mosaik import MosaikCompiler

RED = ["FF0000"] * 4
RED2 = ["F00000"] * 4
BLUE = ["0000FF"] * 4
GREEN = ["00FF00"] * 4
WHITE = ["FFFFFF"] * 4


def _lib(*rows):
    return [[F._parse_rgb(c) for c in r] for r in rows]


def test_rule():
    lib = _lib(WHITE, RED, BLUE, GREEN, RED2, BLUE)
    sel = [0, 1, 2, 3, 4, 5, 0, 0]
    # slot 5 names BLUE like slot 2, so it is slot 2; slot 4 (RED2) has no
    # free position left (1, 2, 3 all used) and merges onto RED (slot 1).
    remap, kept = F.fold_slots({0: 9, 1: 3, 2: 1, 3: 1, 4: 2, 5: 4}, sel, lib)
    assert kept == [0, 1, 2, 3], kept
    assert remap[:6] == [0, 1, 2, 3, 1, 2], remap
    # Used 1..3 keep their number; 4..7 take the free positions, most used
    # first; unused slots park on 0.
    lib = _lib(WHITE, RED, BLUE, GREEN, RED2, WHITE, GREEN, BLUE)
    remap, kept = F.fold_slots({0: 1, 2: 5, 6: 2, 7: 7}, list(range(8)), lib)
    assert kept == [0, 7, 2, 6], kept
    assert remap == [0, 0, 2, 0, 0, 0, 3, 1], remap
    # Over budget: the leftover goes to the NEAREST kept palette.
    remap, kept = F.fold_slots({1: 1, 2: 1, 3: 1, 4: 1}, list(range(8)),
                               _lib(WHITE, RED, BLUE, GREEN, RED2))
    assert remap[4] == 1, remap
    print("  [PASS] the fold rule: 0 pinned, 1..3 in place, 4..7 by usage, "
          "the rest to the nearest palette")


def _world(d, tile_palette, kind_slot=1, cells=None, targets=None,
           extra_world="", scene_rows=True):
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    idx = [[(x // 8) % 4 for x in range(64)] for _ in range(8)]
    M.write_png_indexed(os.path.join(d, "t.png"), 64, 8, idx, pal)
    if targets is not None:
        with open(os.path.join(d, "mosaik.toml"), "w", encoding="utf-8") as f:
            f.write('[project]\nname = "p"\ntarget_platforms = [%s]\n'
                    % ", ".join('"%s"' % t for t in targets))
    body = ['[world]', 'module = "scenes"', 'map_w = 4', 'map_h = 2',
            'vm = true', extra_world, '',
            '[tileset]', 'png = "t.png"', '',
            '[kinds]', 'player = 0', 'npc = 1', '']
    for i, c in enumerate((WHITE, RED, BLUE, GREEN, RED2, GREEN, BLUE, RED)):
        body += ['[[palette]]', 'name = "p%d"' % i,
                 'colors = [%s]' % ", ".join('"%s"' % x for x in c), '']
    body += ['[kind_palettes]', 'npc = %d' % kind_slot, '']
    if cells is not None:
        body += ['[kind_tile_palettes]', 'npc = %r' % (cells,), '']
    body += ['[[scene]]', 'name = "room"',
             'map = [[0,1,2,3],[4,5,6,7]]',
             'collision = [[0,0,0,0],[0,0,0,0]]',
             'tile_palette = %r' % (tile_palette,)]
    if scene_rows:
        body += ['bkg_palettes = [0, 1, 2, 3, 4, 5, 6, 7]',
                 'spr_palettes = [0, 1, 2, 3, 4, 5, 6, 7]']
    body += ['[[scene.object]]', 'kind = "player"', 'x = 8', 'y = 8', '']
    with open(os.path.join(d, "world.toml"), "w", encoding="utf-8") as f:
        f.write("\n".join(body))
    world, base = L.load_world(os.path.join(d, "world.toml"))
    return transpile(world, base)


FOLDING = ["gameboy_color", "sms", "gamegear", "nes", "pce"]


def test_no_fold_is_identical():
    low = [0, 1, 2, 3, 0, 1, 2, 3]
    a = _world(tempfile.mkdtemp(), low,
               targets=["gameboy_color", "sms", "gamegear"])
    b = _world(tempfile.mkdtemp(), low, targets=["gameboy_color"])
    c = _world(tempfile.mkdtemp(), low)
    assert a == b == c, "a world on slots 0..3 changed with the target set"
    # NES / PC Engine targets load 4 slots, not 8: their setters mask `& 3`, so
    # an 8-slot load wrote slots 4..7 back over 0..3.
    d4 = _world(tempfile.mkdtemp(), low, targets=["gameboy_color", "nes"])
    assert "palette.load_bkg_set(0, 4, BKG_PAL, off)" in d4
    assert "palette.load_bkg_set(0, 4, BKG_PAL, off)" not in a
    high = [0, 1, 5, 6, 7, 1, 5, 6]
    d = _world(tempfile.mkdtemp(), high, kind_slot=6,
               targets=["gameboy", "gameboy_color"])
    e = _world(tempfile.mkdtemp(), high, kind_slot=6)
    assert d == e, "a GB-family-only project emitted the fold"
    assert "Folded to the 4" not in d
    print("  [PASS] no slot >= 4, or no 4-slot target: byte-identical")


_SHELL = '''module "main" {
    import "scenes"
    import "graphics.bkg"
    import "graphics.sprite"
    import "graphics.palette"
    import "platform.video"
    import "platform.assets"

    function main() {
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        scenes.load_palettes(0)
        scenes.paint(0)
        scenes.paint_attrs(0)
        var s: u8 = scenes.kind_pal_at(1)
        scenes.paint_actor(0, 1, 1, 1)
        video.enable_lcd()
        loop { video.wait_vblank() }
    }
}
'''


def _compile(src, plat):
    return MosaikCompiler().compile_program(
        [("scenes.mos", src), ("main.mos", _SHELL)], platform=plat)


def test_fold_emits_and_costs_the_gb_nothing():
    high = [0, 1, 5, 6, 7, 1, 5, 6]
    folded = _world(tempfile.mkdtemp(), high, kind_slot=6, cells=[6],
                    targets=FOLDING)
    plain = _world(tempfile.mkdtemp(), high, kind_slot=6, cells=[6],
                   targets=["gameboy_color"])
    assert folded != plain
    for tok in ("TILE_PAL", "BKG_PAL", "SPR_PAL", "KIND_PAL", "KIND_TPAL"):
        n = folded.count("const %s:" % tok)
        assert n >= 2, "%s not forked (%d)" % (tok, n)
    # Used: 0, 1 (in place), 5 and 6 (two tiles each) take the free 2 and 3,
    # and 7 (RED, one tile) merges onto slot 1, the nearest palette. Slots
    # are "the same" only when they name the same library INDEX.
    body = folded.split("Folded to the 4 hardware slots (palette_fold).")
    tile_arm = [b for b in body if "const TILE_PAL:" in b.split("}")[0]]
    assert tile_arm, "no folded TILE_PAL arm"
    vals = tile_arm[0].split("const TILE_PAL:", 1)[1].split("= [", 1)[1]
    vals = [int(v) for v in vals.split("]")[0].replace("\n", " ").split(",")]
    assert max(vals) <= 3, vals
    assert vals[:8] == [0, 1, 2, 3, 1, 1, 2, 3], vals
    for plat in ("gameboy", "gameboy_color", "sms", "gamegear", "nes", "pce",
                 "analogue_pocket", "megaduck"):
        cf = _compile(folded, plat)
        assert not cf.startswith("Compilation error"), (plat, cf[:300])
        if plat in ("gameboy", "gameboy_color", "analogue_pocket", "megaduck"):
            cp = _compile(plain, plat)
            assert cf == cp, "%s C changed with the fold" % plat
    print("  [PASS] a folding world forks five tables, compiles everywhere, "
          "and the GB family's C is identical")


def test_defaults():
    low = [0, 1, 0, 1, 0, 1, 0, 1]
    with_rows = _world(tempfile.mkdtemp(), low)
    none = _world(tempfile.mkdtemp(), low, scene_rows=False)
    assert "const BKG_PAL:" in with_rows and "const BKG_PAL:" not in none
    dflt = _world(tempfile.mkdtemp(), low, scene_rows=False,
                  extra_world="default_bkg_palettes = [0, 1, 2, 3, 4, 5, 6, 7]\n"
                              "default_spr_palettes = [0, 1, 2, 3, 4, 5, 6, 7]")
    assert dflt == with_rows, "defaults did not stand in for the scene rows"
    world = {"world": {"default_bkg_palettes": [3]},
             "scene": [{"bkg_palettes": [1]}, {}]}
    assert F.scene_palette_rows(world) == ([[1], [3]], [None, None])
    print("  [PASS] default slot rows fill a scene without its own")


def test_kind_slot_beside_cell_rows():
    """A kind with a whole-sprite slot and no per-cell row, in a world where
    another kind has one, wears its slot on every cell (the generated room
    loader colours every kind through paint_actor) - it used to draw on slot 0
    and read into the next kind's row."""
    d = tempfile.mkdtemp()
    _world(d, [0] * 8, kind_slot=2, cells=[1, 3, 1, 3])
    wp = os.path.join(d, "world.toml")
    text = open(wp, encoding="utf-8").read()
    text = text.replace("npc = 1\n", "npc = 1\ngem = 2\n", 1)
    text = text.replace("[kind_palettes]\nnpc = 2",
                        "[kind_palettes]\nnpc = 2\ngem = 3")
    with open(wp, "w", encoding="utf-8") as f:
        f.write(text)
    w, base = L.load_world(wp)
    out = transpile(w, base)

    def array(name):
        body = out[out.index("const %s:" % name):]
        return [int(v) for v in
                body.split("= [", 1)[1].split("]")[0].replace("\n", " ").split(",")]
    vals, offs = array("KIND_TPAL"), array("KIND_TPAL_OFF")
    assert vals[offs[2]:offs[2] + 4] == [3, 3, 3, 3], (vals, offs)
    assert vals[offs[1]:offs[1] + 4] == [1, 3, 1, 3], (vals, offs)
    assert vals[offs[0]] == 0 and offs[1] - offs[0] == 1, (vals, offs)
    print("  [PASS] a kind slot beside per-cell rows becomes a full row of that slot")


def test_rooms_gate_reads_defaults():
    """The rooms generator's colour gate and the transpiler's must agree, or
    a room calls a load_palettes nobody emitted."""
    import inspect
    from mosaik_vm.rooms import generate
    src = inspect.getsource(generate)
    assert "scene_palette_rows(world)" in src
    assert "spr_fold(world)" in src
    # The actor loop's `fk` is what paint_actor / kind_pal_at read, so it is
    # declared for a coloured world even without residency or mixed sizes (a
    # uniform 16x16 world could not compile: `vm-palettes` found it).
    from mosaik_vm.rooms import emit_load
    load = inspect.getsource(emit_load)
    assert 'info.get("kind_tpal") or info.get("kind_pal"))' in load
    print("  [PASS] generate_rooms reads the same rows and the same sprite fold")


if __name__ == "__main__":
    print("Palette slot fold + default slot rows")
    test_rule()
    test_no_fold_is_identical()
    test_fold_emits_and_costs_the_gb_nothing()
    test_defaults()
    test_kind_slot_beside_cell_rows()
    test_rooms_gate_reads_defaults()
    print("All palette-fold checks passed")
