#!/usr/bin/env python3
"""The concatenated MAPS/COLLISION tables CHUNKED at a ROM bank (paint_table).

A const array is read in place while its ONE ROM bank is mapped, so no single
symbol may cross one (16 KB on the GB, the tightest per-symbol ceiling of any
target). `[world] paint_table` concatenates every scene's map into one `MAPS`
symbol, which reached that ceiling as soon as the reference-engine sample kept its
255-wide SHMUP room whole (14,274 map cells -> 18,288).

The symptom was NOT a link error. The importer measured the concatenation
against a 14,336-cell gate and silently dropped `paint_table` past it, which
put the per-scene paint/map_tile chains back in the RESIDENT image (over bank 0
on the GB family) - so the room was CROPPED instead, losing 15 actors and a
trigger. Chunking is what lifts that.

Same rule the per-scene tilesets (`_TS_CHUNK`) and the song CELLS blob
(`songs.CELL_CHUNK`) already follow. Contract pinned here:

  * a world that fits ONE bank is byte-identical - one `MAPS` symbol, no
    `MAP_BLK`, no `map_cell`, and the historic direct-index reads.
  * past a bank the concatenation splits into `MAPS`/`MAPS2`, each scene's
    block lives WHOLE inside one chunk, `MAP_BLK` says which and `MAP_OFF` is
    the offset WITHIN it.
  * MAPS and COLLISION split on the SAME boundaries, so one `MAP_BLK` indexes
    both (their per-scene blocks are the same w*h length).
  * the fork lives in ONE shared reader per table (`map_cell`/`col_cell`), not
    inlined at each of the four read sites - duplicating a seam fork cost
    ~510 B of resident image the last time it was tried.
  * every chunk gets its own `assets.range_base` under `[world] stream`: the
    seam keys on the SYMBOL, and an unregistered chunk reads cold, which on the
    Lynx is an empty level rather than an error.
  * a SINGLE scene bigger than a bank is a clear error (only the concatenation
    can be chunked, not one scene's map).
  * the module still compiles on a banking console and on the Lynx.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import shutil
import tempfile

from mosaik import MosaikCompiler
from mosaik_assets import write_png_indexed
from mosaik_scenes import transpile, SceneError

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _world(n_scenes, w, h, stream=True):
    """`n_scenes` rooms of w*h cells each, under stream + paint_table."""
    scenes = [{
        "name": "room%d" % i,
        "scene_type": "platform",
        "map_w": w, "map_h": h,
        "map": [(i + 1) % 4 for _ in range(w * h)],
        "collision": [(1 if x == 0 else 0) for _ in range(h) for x in range(w)],
    } for i in range(n_scenes)]
    return {
        "world": {"module": "scenes", "vm": True, "map_w": w, "map_h": h,
                  "stream": stream, "paint_table": True},
        "tileset": {"png": "tiles.png"},
        "kinds": {"player": 0},
        "scene": scenes,
    }


def _transpile(world):
    tmp = tempfile.mkdtemp(prefix="mapchunk_")
    try:
        pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
        write_png_indexed(os.path.join(tmp, "tiles.png"), 8, 32,
                          [0] * 64 + [1] * 64 + [2] * 64 + [3] * 64, pal)
        return transpile(world, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


#: 20 rooms of 32x28 = 17,920 cells, comfortably past one 16,384-cell bank
#: while every single room stays far inside one.
_BIG = dict(n_scenes=20, w=32, h=28)
#: 8 rooms of 32x28 = 7,168 cells, one chunk.
_SMALL = dict(n_scenes=8, w=32, h=28)


def test_one_chunk_is_byte_identical():
    print("\n[byte-identical under a bank]")
    src = _transpile(_world(**_SMALL))
    check("MAP_BLK" not in src, "no MAP_BLK table for a world that fits a bank")
    check("map_cell" not in src, "no map_cell reader either")
    check("col_cell" not in src, "no col_cell reader either")
    check("const MAPS2" not in src, "no second chunk symbol")
    check("return assets.range_byte(MAPS, MAP_OFF[scene], idx)" in src,
          "map_tile keeps its historic direct window read")
    check("return assets.range_byte(COLLISION, MAP_OFF[scene], idx)" in src,
          "collision_at keeps its historic direct window read")


def _array(src, name):
    """(declared length, [values]) of an emitted `const NAME: array[t, n] = [...]`."""
    head = src.split("const %s: array[" % name, 1)[1]
    n = int(head.split("]", 1)[0].split(",")[1])
    body = head.split("= [", 1)[1].split("]", 1)[0]
    return n, [int(v) for v in body.replace("\n", "").split(",") if v.strip()]


def test_two_chunks_split_on_scene_boundaries():
    print("\n[past a bank]")
    src = _transpile(_world(**_BIG))
    check("const MAPS2" in src, "MAPS splits into a second chunk symbol")
    check("const COLLISION2" in src, "COLLISION splits the same way")
    check("const MAP_BLK" in src, "MAP_BLK says which chunk each scene is in")
    # Each scene is 32*28 = 896 cells; 16384 // 896 = 18 rooms fit chunk 0.
    _, blk = _array(src, "MAP_BLK")
    check(blk == [0] * 18 + [1] * 2,
          "18 rooms fill chunk 0, the last 2 go to chunk 1 (got %r)" % (blk[-4:],))
    # ... and the offsets restart at 0 in the new chunk.
    _, offs = _array(src, "MAP_OFF")
    check(offs[18] == 0, "MAP_OFF restarts at 0 in chunk 1 (got %d)" % offs[18])
    check(offs[17] == 17 * 896, "chunk 0's last room keeps its running offset")
    # every chunk holds whole rooms only, and nothing is lost between them
    n0, _ = _array(src, "MAPS")
    n1, _ = _array(src, "MAPS2")
    check(n0 == 18 * 896, "chunk 0 holds exactly 18 whole rooms (%d)" % n0)
    check(n1 == 2 * 896, "chunk 1 holds the remaining 2 (%d)" % n1)
    check(n0 + n1 == 20 * 896, "the two chunks together are the whole world")
    check(n0 <= 16384 and n1 <= 16384, "neither chunk crosses a ROM bank")
    c0, _ = _array(src, "COLLISION")
    check(c0 == n0, "COLLISION splits on the SAME boundary as MAPS")


def test_the_fork_lives_in_one_reader():
    print("\n[one shared reader]")
    src = _transpile(_world(**_BIG))
    check("function map_cell(blk: u8, off: u16, idx: u16) -> u8 {" in src,
          "map_cell is the ONE map reader")
    check("function col_cell(blk: u8, off: u16, idx: u16) -> u8 {" in src,
          "col_cell is the ONE collision reader")
    check("return map_cell(MAP_BLK[scene], MAP_OFF[scene], idx)" in src,
          "map_tile routes through it")
    check("return col_cell(MAP_BLK[scene], MAP_OFF[scene], idx)" in src,
          "collision_at routes through it")
    # MAPS2 may be named by the reader, by paint/warm's ptr_range/use_range
    # forks (a pointer cannot be returned from a helper), by its own
    # declaration and by the export list - but by nothing else. Exactly ONE
    # line may READ a cell out of it, which is what "one shared reader" means.
    named = [l.strip() for l in src.splitlines() if "MAPS2" in l]
    stray = [l for l in named
             if not (l.startswith("const MAPS2") or l.startswith("export ")
                     or l.startswith("--")
                     or "range_byte" in l or "ptr_range" in l
                     or "use_range" in l or "range_base" in l)]
    check(not stray, "MAPS2 is named only by the reader, the seam calls, its "
                     "decl and the export list (stray: %r)" % (stray[:2],))
    check(len([l for l in named if "range_byte" in l]) == 1,
          "exactly ONE line reads a cell out of MAPS2")


def test_every_chunk_is_registered_with_the_seam():
    print("\n[range seam]")
    src = _transpile(_world(**_BIG))
    paint = src.split("function paint(")[1].split("\n    }")[0]
    for sym in ("MAPS", "MAPS2", "COLLISION", "COLLISION2"):
        check("assets.range_base(%s," % sym in paint,
              "paint registers %s with the range seam" % sym)
    check("if MAP_BLK[scene] == 1 { bkg.set_tiles(0, 0, w, h, "
          "assets.ptr_range(MAPS2, off, n)) }" in paint,
          "the map upload forks on the chunk")


def test_a_single_scene_past_a_bank_is_a_clear_error():
    print("\n[one scene too big]")
    try:
        _transpile(_world(n_scenes=2, w=255, h=100))
        msg, raised = "", False
    except SceneError as e:
        msg, raised = str(e), True
    except Exception as e:                                       # noqa: BLE001
        msg, raised = "wrong type: %s" % e, False
    check(raised, "a 25,500-cell scene raises SceneError (%s)" % msg[:60])
    check("split the scene" in msg, "the error says what to do about it")


def test_it_compiles():
    print("\n[compiles]")
    src = _transpile(_world(**_BIG))
    for plat in ("gameboy", "lynx"):
        try:
            out = MosaikCompiler().compile_program(
                [("main.mos", _MAIN), ("scenes.mos", src)], platform=plat)
            ok = not out.startswith("Compilation error:")
            err = "" if ok else out.splitlines()[0]
        except Exception as e:                                   # noqa: BLE001
            ok, err = False, str(e)
        check(ok, "the chunked module compiles on %s (%s)" % (plat, err[:70]))


#: Touches BOTH chunks through every reader, so an unexported chunk symbol or
#: a mis-emitted fork is a compile error rather than a silent wrong tile.
_MAIN = '''
module "main" {
    import "graphics.bkg"
    import "scenes"
    function main() {
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        scenes.paint(0)
        scenes.paint(19)
        var t: u8 = scenes.map_tile(19, 5) + scenes.map_tile(0, 5)
        var c: u8 = scenes.collision_at(19, 5) + scenes.collision_at(0, 5)
        bkg.set_tiles(0, 0, 1, 1, scenes.TILESET)
    }
}
'''


def main():
    print("=" * 50)
    print("MAPS/COLLISION chunked at a ROM bank")
    print("=" * 50)
    test_one_chunk_is_byte_identical()
    test_two_chunks_split_on_scene_boundaries()
    test_the_fork_lives_in_one_reader()
    test_every_chunk_is_registered_with_the_seam()
    test_a_single_scene_past_a_bank_is_a_clear_error()
    test_it_compiles()
    print("\n" + "=" * 50)
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All map-chunk checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
