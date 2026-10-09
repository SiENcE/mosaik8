#!/usr/bin/env python3
"""Baked auto-tile rules (mosaik_scenes/autotile.py, `[scene.autotile] bake`).

A scene's auto-tile rule picks, per logical cell, an edge tile from its four
neighbours (a variant on some fully surrounded cells, a picture behind the
rest). With `bake = true` the transpiler writes those picks into the built
map as ordinary tile ids, so a runtime that does not auto-tile shows the
picture an editor draws. Pinned here:

- every one of the 16 neighbour masks lands on the right tileset id (the
  edge sheet's row-major place after the images before it);
- the `other` picture fills the non-material cells, anchored to the bottom
  for `row_from = "bottom"`;
- variants follow the hash; a manifest sheet is read in the ENGINE's sprite
  order, column-major under the project's `[build] obj_8x16`;
- a rule WITHOUT `bake` (and a world without a rule) transpiles
  byte-identically to the same world without the table;
- a sheet that is not in the scene's tileset, or a tile past the sheet's end,
  is refused with a message that names it;
- the baked world compiles.
"""
import copy
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler  # noqa: E402
from mosaik_assets import write_png_indexed, write_sprite_manifest  # noqa: E402
from mosaik_scenes import transpile, SceneError  # noqa: E402
from mosaik_scenes.autotile import Rule, bake_world, sheet_tile_origins  # noqa: E402

GREYS = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
_OK = True


def check(label, cond):
    global _OK
    _OK = _OK and bool(cond)
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    return bool(cond)


def _pngs(d):
    """logic.png: 2 tiles (0 sea, 1 ground) - edges.png: 20 tiles in a row
    (16 edges + 4 variants) - far.png: a 2x2 picture."""
    write_png_indexed(os.path.join(d, "logic.png"), 16, 8, [0] * 64 + [3] * 64, GREYS)
    write_png_indexed(os.path.join(d, "edges.png"), 160, 8, [1] * (160 * 8), GREYS)
    write_png_indexed(os.path.join(d, "far.png"), 16, 16, [2] * 256, GREYS)


def _world(rule, rows, pngs=("logic.png", "edges.png", "far.png")):
    return {
        "world": {"module": "scenes", "map_w": len(rows[0]), "map_h": len(rows)},
        "tileset": {"pngs": list(pngs)},
        "kinds": {},
        "scene": [{"name": "a", "map": [list(r) for r in rows], "object": [],
                   **({"autotile": rule} if rule is not None else {})}],
    }


def _baked(world, d):
    return bake_world(world, d)["scene"][0]["map"]


def main():
    print("Baked auto-tile rules")
    print("=" * 50)
    with tempfile.TemporaryDirectory() as d:
        _pngs(d)
        # logic 0..1, edges 2..21, far 22..25 (row-major: 22 23 / 24 25)
        rule = {"material": [1], "tiles": "edges.png", "other": "far.png",
                "edge_x": "other", "edge_y": "other", "bake": True}
        ok = True
        for m in range(16):
            rows = [[0, 0, 0], [0, 1, 0], [0, 0, 0]]
            for bit, (x, y) in ((1, (1, 0)), (2, (2, 1)), (4, (1, 2)), (8, (0, 1))):
                if m & bit:
                    rows[y][x] = 1
            ok &= _baked(_world(rule, rows), d)[1][1] == 2 + m
        check("all 16 masks bake to edges.png tile m (id 2 + m)", ok)

        rows = [[0, 0, 0, 0], [0, 1, 1, 0], [0, 0, 0, 0]]
        got = _baked(_world(dict(rule, row_from="bottom"), rows), d)
        # bottom row (y 2) = the picture's bottom row: 24 25 24 25
        check("other: the bottom row shows the picture's bottom row (row_from bottom)",
              got[2] == [24, 25, 24, 25] and got[1][0] == 22 and got[0][0] == 24)
        got = _baked(_world(rule, rows), d)
        check("other: tiled from the top-left (row_from top)",
              got[0] == [22, 23, 22, 23] and got[1][3] == 25)
        check("the ground pair: W edge (2 + 2) and E edge (2 + 8)",
              got[1][1] == 2 + 2 and got[1][2] == 2 + 8)

        # Variants: a field of ground, 4 of 16 by the hash, first 16, count 4.
        vr = dict(rule, edge_x="extend", edge_y="extend",
                  variants={"first": 16, "count": 4, "chance": 4, "hash": [7, 13]})
        rows = [[1] * 8 for _ in range(6)]
        got = _baked(_world(vr, rows), d)
        want = [[2 + (16 + h if h < 4 else 15) for x in range(8)
                 for h in [(x * 7 + y * 13) & 15]] for y in range(6)]
        check("variants: (x*7 + row*13) & 15 < 4 -> tile 16 + h, else the full tile 15",
              got == want and any(v != 17 for r in got for v in r))

        # wrap: a loop. A 1-wide map, land, land, SEA: the top cell's north is
        # the bottom cell (sea), where "extend" would have used the top cell
        # itself (land) - the fixture must tell the two apart.
        wr = dict(rule, edge_x="extend", edge_y="wrap")
        got = _baked(_world(wr, [[1], [1], [0]]), d)
        ext = _baked(_world(dict(wr, edge_y="extend"), [[1], [1], [0]]), d)
        check("edge_y wrap: the top cell's north is the bottom row (E|S|W = 2 + 14)",
              got[0][0] == 2 + 14 and ext[0][0] == 2 + 15)

        # By CONTENT: the scene's own tileset is ONE combined picture (logic,
        # then the 16 edge tiles in REVERSE order), the rule still names the
        # edge sheet - each pick lands on the combined tile with its pixels.
        def tile_rows(k):
            return [[3 if (x == k % 8 and y == k // 8) else 1 for x in range(8)]
                    for y in range(8)]
        edge_tiles = [tile_rows(k) for k in range(16)]
        def write_tiles(path, tl):
            px = []
            for y in range(8):
                for t in tl:
                    px += t[y]
            write_png_indexed(os.path.join(d, path), 8 * len(tl), 8, px, GREYS)
        write_tiles("edges16.png", edge_tiles)
        logic = [[[0] * 8 for _ in range(8)], [[3] * 8 for _ in range(8)]]
        write_tiles("combo.png", logic + list(reversed(edge_tiles)))
        cr = {"material": [1], "tiles": "edges16.png", "edge_x": "other",
              "edge_y": "other", "bake": True}
        cw = _world(cr, [[0, 1, 1, 0]], pngs=("logic.png",))
        cw["scene"][0]["tileset"] = "combo.png"
        got = _baked(cw, d)
        # cell 1: E neighbour -> mask 2; cell 2: W -> mask 8. combo id of edge
        # tile m = 2 + (15 - m).
        check("by content: a combined per-scene tileset gets the picks' own pixels",
              got[0][1] == 2 + 13 and got[0][2] == 2 + 7 and got[0][0] == 0)

        # Unbaked rules pass the world through untouched.
        plain = _world(None, rows)
        noted = _world(dict(rule, bake=False), rows)
        check("a rule without bake leaves the world as is",
              bake_world(noted, d) is noted and bake_world(plain, d) is plain)
        check("...and transpiles byte-identically to the world without it",
              transpile(copy.deepcopy(noted), d) == transpile(copy.deepcopy(plain), d))
        baked_src = transpile(_world(rule, [[0, 1, 1], [0, 1, 1], [0, 0, 0]]), d)
        check("a baked rule changes the built map",
              baked_src != transpile(_world(None, [[0, 1, 1], [0, 1, 1], [0, 0, 0]]), d))
        out = MosaikCompiler().compile_program([("scenes.mos", baked_src)], platform="gameboy")
        check("the baked world compiles", not out.startswith("Compilation error:"))

        # Refusals.
        try:
            bake_world(_world(rule, rows, pngs=("logic.png", "far.png")), d)
            check("a sheet missing from the tileset is refused", False)
        except SceneError as e:
            check("a sheet missing from the tileset is refused, naming it",
                  "edges.png" in str(e))
        try:
            bake_world(_world(dict(vr, variants={"first": 30, "count": 4, "chance": 16}),
                              rows), d)
            check("a variant past the sheet's end is refused", False)
        except SceneError as e:
            check("a variant past the sheet's end is refused", "past the end" in str(e))
        try:
            Rule.from_dict(dict(rule, bake="yes"))
            check("bake must be a boolean", False)
        except ValueError:
            check("bake must be a boolean", True)

        # A manifest sheet: the engine's sprite order, column-major under obj_8x16.
        write_png_indexed(os.path.join(d, "tall.png"), 16, 16, [1] * 256, GREYS)
        write_sprite_manifest(os.path.join(d, "tall.png"), [("t", [0, 0, 16, 16])])
        check("manifest order, row-major",
              sheet_tile_origins(os.path.join(d, "tall.png"), 16, 16)
              == [(0, 0), (8, 0), (0, 8), (8, 8)])
        check("manifest order under obj_8x16, column-major",
              sheet_tile_origins(os.path.join(d, "tall.png"), 16, 16, obj16=True)
              == [(0, 0), (0, 8), (8, 0), (8, 8)])
        with open(os.path.join(d, "mosaik.toml"), "w", encoding="utf-8") as f:
            f.write("[project]\nname = \"p\"\n[build]\nobj_8x16 = true\n")
        # quad.png: four 16x16 sprites in a 64 x 16 picture (16 tiles, 8 columns),
        # after logic.png (2 tiles) and far.png (4): base 6. [[1, 1]]: cell 0 has
        # an E neighbour (mask 2), cell 1 a W one (mask 8). Under obj_8x16 sheet
        # tile 2 = (8, 0) -> id 6 + 1 = 7 and tile 8 = sprite 2's first, (32, 0)
        # -> id 6 + 4 = 10; row-major per sprite tile 2 = (0, 8) -> id 6 + 8 = 14.
        write_png_indexed(os.path.join(d, "quad.png"), 64, 16, [1] * (64 * 16), GREYS)
        write_sprite_manifest(os.path.join(d, "quad.png"),
                              [("s%d" % k, [16 * k, 0, 16, 16]) for k in range(4)])
        r2 = dict(rule, tiles="quad.png")
        world = _world(r2, [[1, 1]], pngs=("logic.png", "far.png", "quad.png"))
        check("obj_8x16 project: a manifest sheet reads column-major per sprite (7, 10)",
              _baked(world, d)[0] == [7, 10])
        os.remove(os.path.join(d, "mosaik.toml"))
        check("without obj_8x16: row-major per sprite (14, 10)",
              _baked(world, d)[0] == [14, 10])
    print("\n" + ("ALL PASS" if _OK else "FAILURES"))
    return 0 if _OK else 1


if __name__ == "__main__":
    sys.exit(main())
