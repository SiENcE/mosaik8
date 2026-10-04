#!/usr/bin/env python3
"""Asset-streaming opt-in threading (Stage A.3) -- scene transpiler.

`[world] stream = true` routes each per-scene map upload in the generated
`paint()` through the asset-residency seam (`assets.use(MAP)` +
`bkg.set_tiles(..., assets.ptr(MAP))`) so the Lynx can load it from the cart per
room (Stage B). This checks the threading is:

  * OFF by default      -- a world without `stream` emits today's code (no seam,
                           no platform.assets import): the additive guarantee.
  * present when ON     -- the import + assets.use/ptr appear in paint().
  * byte-identical in C  -- a program built over the streamed module generates C
                           identical to the non-streamed one (the seam lowers to
                           no-op + the const pointer today), on GBDK and cc65.
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
from mosaik_assets import write_png_indexed
from mosaik_scenes import transpile


def make_tileset_png(path):
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    write_png_indexed(path, 8, 16, [0] * 64 + [3] * 64, pal)


BASE = {
    "world": {"module": "scenes", "map_w": 4, "map_h": 4},
    "tileset": {"png": "tiles.png"},
    "kinds": {"player": 0},
    "scene": [
        {"name": "field", "map": [1, 1, 1, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 1, 1, 1],
         "object": [{"kind": "player", "x": 8, "y": 8}]},
        {"name": "cave", "map": [1, 1, 1, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 1, 1, 1],
         "object": []},
    ],
}

# A driver that loads a room (so paint() -- the threaded function -- is live).
GAME = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    import "scenes"
    var room: u8 = 0
    function main() {
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        scenes.paint(room)
        video.wait_vblank()
    }
    export main
}
'''


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("Asset-streaming opt-in (Stage A.3)")
    print("=" * 50)
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        make_tileset_png(os.path.join(tmp, "tiles.png"))

        off = transpile(BASE, tmp)
        on = transpile(dict(BASE, world=dict(BASE["world"], stream=True)), tmp)

        # OFF is the additive baseline: no seam, no platform.assets import.
        ok &= check("stream off: no seam in the module (additive default)",
                    "platform.assets" not in off
                    and "assets.use(" not in off
                    and "assets.ptr(" not in off
                    and "bkg.set_tiles(0, 0, MAP_W, MAP_H, FIELD_MAP)" in off)

        # ON: paint() routes the map through the seam + imports platform.assets.
        ok &= check("stream on: paint() uses the asset-residency seam",
                    'import "platform.assets"' in on
                    and "assets.use(FIELD_MAP)" in on
                    and "bkg.set_tiles(0, 0, MAP_W, MAP_H, assets.ptr(FIELD_MAP))" in on
                    and "assets.use(CAVE_MAP)" in on)

        # On a directly-mapped console (gameboy) the seam is transparent: the
        # streamed module compiles to C byte-identical to the non-streamed one
        # (use -> nothing, ptr -> the const symbol) -- the resident maps stay put.
        cg_off = MosaikCompiler().compile_program(
            [("main.mos", GAME), ("scenes.mos", off)], platform="gameboy")
        cg_on = MosaikCompiler().compile_program(
            [("main.mos", GAME), ("scenes.mos", on)], platform="gameboy")
        ok &= check("[gameboy] streamed module compiles",
                    not cg_on.startswith("Compilation error:"))
        ok &= check("[gameboy] streamed C is BYTE-IDENTICAL to non-streamed",
                    cg_on == cg_off)

        # On the Lynx the opt-in actually streams (Stage B): the per-scene map
        # consts leave the resident image and paint()/map_tile route through the
        # cart loader/cache -- so the C differs from both the non-streamed Lynx
        # build and from the gameboy build.
        cl_off = MosaikCompiler().compile_program(
            [("main.mos", GAME), ("scenes.mos", off)], platform="lynx")
        comp = MosaikCompiler()
        cl_on = comp.compile_program(
            [("main.mos", GAME), ("scenes.mos", on)], platform="lynx")
        ok &= check("[lynx] streamed module compiles",
                    not cl_on.startswith("Compilation error:"))
        ok &= check("[lynx] streaming strips the resident map + adds the loader",
                    cl_on != cl_off and "gbs_asset_ptr" in cl_on
                    and "lseek(1" in cl_on)
        ok &= check("[lynx] no bare resident map symbol survives",
                    "FIELD_MAP" not in cl_on)
        ok &= check("[lynx] the build can recover the cart archive blob",
                    len(comp.code_generator.streamed_archive) > 0)

    print("=" * 50)
    print("All asset-streaming opt-in checks passed" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
