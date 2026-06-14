#!/usr/bin/env python3
"""Layer-3 scene transpiler (mosaik_scenes.py) -- game-framework Phase 4.

Transpiles a small two-scene world (a PNG tileset + maps + objects + a door)
to a mosaik module and checks: the emitted module + a game using it compile on
a GBDK and a cc65 console, the generated tables / selector / door data are
present, and transpilation is deterministic.
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
    # 8x16 indexed PNG = two 8x8 tiles: floor (index 0) over wall (index 3).
    # A <=4-entry indexed PNG maps indices straight to GB colour values.
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    idx = [0] * 64 + [3] * 64
    write_png_indexed(path, 8, 16, idx, pal)


WORLD = {
    "world": {"module": "scenes", "map_w": 4, "map_h": 4},
    "tileset": {"png": "tiles.png"},
    "kinds": {"player": 0, "npc": 1, "chest": 2},
    "scene": [
        {"name": "field",
         "map": [[1, 1, 1, 1], [1, 0, 0, 1], [1, 0, 0, 1], [1, 1, 1, 1]],
         "object": [{"kind": "npc", "x": 16, "y": 16},
                    {"kind": "chest", "x": 24, "y": 16}]},
        {"name": "cave",
         "map": [1, 1, 1, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 1, 1, 1],  # flat form
         "object": [{"kind": "player", "x": 8, "y": 8}]},
    ],
    "door": [{"from": "field", "tx": 2, "ty": 3, "to": "cave", "ex": 16, "ey": 8}],
}

# A game that imports the generated module and uses the selector + tables.
GAME = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    import "scenes"
    var room: u8 = 0
    function tile_here(px: u8, py: u8) -> u8 {
        return scenes.map_tile(room, (py / 8) * scenes.MAP_W + (px / 8))
    }
    function try_doors(cx: u8, cy: u8) {
        for d in 0..scenes.DOOR_COUNT {
            if scenes.DOOR_FROM[d] == room and scenes.DOOR_TX[d] == cx and scenes.DOOR_TY[d] == cy {
                room = scenes.DOOR_TO[d]
            }
        }
    }
    function main() {
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        var k: u8 = 0
        for i in 0..scenes.OBJ_COUNT {
            if scenes.OBJ_SCENE[i] == 0 and scenes.OBJ_KIND[i] == scenes.KIND_NPC {
                k = scenes.OBJ_X[i]
            }
        }
        try_doors(2, 3)
        video.wait_vblank()
    }
    export main
}
'''


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("Scene transpiler (Layer 3 / Phase 4)")
    print("=" * 50)
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        make_tileset_png(os.path.join(tmp, "tiles.png"))
        src = transpile(WORLD, tmp)
        src2 = transpile(WORLD, tmp)

        ok &= check("transpile is deterministic", src == src2)
        ok &= check("emits module + per-scene maps + selector",
                    'module "scenes"' in src
                    and "const FIELD_MAP: array[u8, 16]" in src
                    and "const CAVE_MAP: array[u8, 16]" in src
                    and "function map_tile(scene: u8, idx: u16)" in src)
        ok &= check("emits the tileset from the PNG pipeline",
                    "const TILE_COUNT: u8 = 2" in src
                    and "const TILESET: array[u8, 32]" in src)
        ok &= check("emits object + door tables + kind constants",
                    "const OBJ_COUNT: u8 = 3" in src
                    and "const DOOR_COUNT: u8 = 1" in src
                    and "const KIND_NPC: u8 = 1" in src
                    and "DOOR_FROM" in src and "DOOR_TO" in src
                    and "DOOR_TX" in src and "DOOR_EX" in src)

        # The generated module + a game that uses it compile on both backends.
        for platform in ("gameboy", "lynx"):
            out = MosaikCompiler().compile_program(
                [("main.mos", GAME), ("scenes.mos", src)], platform=platform)
            ok &= check("[%s] generated scenes module + game compile" % platform,
                        not out.startswith("Compilation error:"))
            ok &= check("[%s] cross-module scene access lowers" % platform,
                        "scenes_map_tile(" in out
                        and "scenes_DOOR_FROM[" in out
                        and "scenes_TILESET" in out)

    print("=" * 50)
    print("All scene-transpiler checks passed" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
