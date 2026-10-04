#!/usr/bin/env python3
"""Generate the platformer's Layer-3 world: tiles.png + world.toml + scenes.mos.

The level used to be PROCEDURAL (main.mos computed each cell's collision type in
`tile_at`). It is now Layer-3 DATA with a real **collision LAYER**: one scene
whose `map` holds the visual tile indices and whose parallel `collision` array
holds the per-cell collision TYPE (AIR / SOLID / one-way PLATFORM) -- decoupled
from the graphic, the reference-engine model the collision-layer plan lifts in. main.mos
samples `scenes.collision_at` via engine.collision's box_blocked / box_floor, so the
one-way platform rides the layer end-to-end (proof that PLATFORM/box_floor work
on real ROMs). The two arrays happen to be equal here (visual == type), but they
are independent -- paint a hidden ledge and only the collision changes.

Run, then build:
    python projects/platformer/assets/gen_world.py
    python mosaik8.py build --platform gameboy projects/platformer
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PROJ))   # the mosaik8/ checkout
sys.path.insert(0, ROOT)

from mosaik_assets import write_png_indexed          # noqa: E402
from mosaik_scenes import transpile, load_world       # noqa: E402
import toml                                            # noqa: E402

W = H = 32
AIR, SOLID, PLATFORM = 0, 1, 2
PAL = [(248, 248, 248), (168, 168, 168), (96, 96, 96), (0, 0, 0)]

# The three visual tiles (2bpp), indexed by collision type: 0 = blank (AIR),
# 1 = a solid block (SOLID), 2 = a one-way platform (a thick top edge). Same
# bytes the procedural main.mos shipped, so the level looks identical.
TILES_2BPP = [
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    0xFF, 0, 0xFF, 0, 0xFF, 0, 0xFF, 0, 0xFF, 0, 0xFF, 0, 0xFF, 0, 0xFF, 0,
    0xFF, 0xFF, 0xFF, 0xFF, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
]


def tile_at(x, y):
    """The procedural level (unchanged): ground, side walls, two one-way ledges."""
    if y >= 224:
        return SOLID
    if x < 8 or x > 247:
        return SOLID
    if 192 <= y < 200 and 48 <= x < 112:
        return PLATFORM
    if 160 <= y < 168 and 144 <= x < 208:
        return PLATFORM
    return AIR


def tiles_2bpp_to_indices(data):
    out = []
    for t in range(len(data) // 16):
        for r in range(8):
            lo, hi = data[t * 16 + r * 2], data[t * 16 + r * 2 + 1]
            for x in range(8):
                bit = 7 - x
                out.append(((lo >> bit) & 1) | (((hi >> bit) & 1) << 1))
    return out


def rows(flat, w):
    return [flat[i:i + w] for i in range(0, len(flat), w)]


def main():
    # tiles.png: the 3 visual tiles stacked (8 x 24), indexed.
    idx = tiles_2bpp_to_indices(TILES_2BPP)
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 24, idx, PAL)

    # Sample the procedural level into a 32x32 grid (cell = 8 px). The visual
    # map and the collision layer are separate arrays (equal here by design).
    flat = [tile_at(cx * 8, cy * 8) for cy in range(H) for cx in range(W)]
    world = {
        "world": {"module": "scenes", "map_w": W, "map_h": H},
        "tileset": {"png": "tiles.png"},
        "scene": [{"name": "level", "map": rows(flat, W),
                   "collision": rows(flat, W)}],
    }
    with open(os.path.join(PROJ, "world.toml"), "w", encoding="utf-8") as f:
        f.write(toml.dumps(world))

    data, base_dir = load_world(os.path.join(PROJ, "world.toml"))
    src = transpile(data, base_dir)
    with open(os.path.join(PROJ, "src", "scenes.mos"), "w", encoding="utf-8") as f:
        f.write(src)
    print("platformer: wrote tiles.png, world.toml, src/scenes.mos "
          "(%d-tile tileset, one scene with a collision layer)"
          % (len(TILES_2BPP) // 16))


if __name__ == "__main__":
    main()
