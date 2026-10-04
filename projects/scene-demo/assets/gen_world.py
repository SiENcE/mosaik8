#!/usr/bin/env python3
"""Generate the scene-demo's world: tiles.png + world.toml.

Two 32x32 rooms (a field and a cave) joined by a door each way, in the Layer-3
declarative format. Run this, then transpile to mosaik:

    python projects/scene-demo/assets/gen_world.py
    python mosaik_scenes.py projects/scene-demo/world.toml -o projects/scene-demo/src/scenes.mos
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from mosaik_assets import write_png_indexed
import toml

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

# Tile ids == background-tile indices: 0 floor, 1 deco, 2 door, 3 wall. A
# <=4-entry indexed PNG maps each pixel's palette index straight to its GB
# colour, so four solid tiles give four distinct shades.
PAL = [(248, 248, 248), (168, 168, 168), (96, 96, 96), (0, 0, 0)]
FLOOR, DECO, DOOR, WALL = 0, 1, 2, 3

W = H = 32


def make_tiles_png():
    # 8x32 = four 8x8 tiles stacked, each a solid palette index.
    idx = [FLOOR] * 64 + [DECO] * 64 + [DOOR] * 64 + [WALL] * 64
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 32, idx, PAL)


def room(doors, decos=()):
    """A walled 32x32 room: wall border, floor inside, `doors`/`decos` cells set."""
    m = []
    for y in range(H):
        row = []
        for x in range(W):
            if (x, y) in doors:
                row.append(DOOR)
            elif x == 0 or y == 0 or x == W - 1 or y == H - 1:
                row.append(WALL)
            elif (x, y) in decos:
                row.append(DECO)
            else:
                row.append(FLOOR)
        m.append(row)
    return m


def main():
    make_tiles_png()
    # Doors are TWO tiles wide so the 16px (2-tile) player fits through.
    field = room({(15, 31), (16, 31)}, decos={(8, 8), (24, 8), (8, 24), (24, 24)})
    cave = room({(15, 0), (16, 0)}, decos={(16, 16)})
    world = {
        "world": {"module": "scenes", "map_w": W, "map_h": H},
        "tileset": {"png": "tiles.png"},
        "kinds": {"player": 0, "npc": 1, "chest": 2},
        "scene": [
            {"name": "field", "map": field,
             "object": [{"kind": "player", "x": 120, "y": 120}]},
            {"name": "cave", "map": cave, "object": []},
        ],
        # (from, trigger cell) -> (to, entry pixel). The player is aligned to
        # px=120 (cells 15-16, centre cell 16), so both door columns can trigger;
        # entries sit a couple cells from the far door so arriving never
        # re-triggers it.
        # The trigger is the player's CENTRE cell at the doorway. The 16px (top-
        # left origin) player presses against the bottom edge with its centre on
        # row 31, but against the top edge with its centre on row 1 (not 0) -- so
        # the cave's top door triggers on row 1 while its tiles stay on row 0.
        "door": [
            {"from": "field", "tx": 16, "ty": 31, "to": "cave", "ex": 120, "ey": 24},
            {"from": "field", "tx": 15, "ty": 31, "to": "cave", "ex": 120, "ey": 24},
            {"from": "cave", "tx": 16, "ty": 1, "to": "field", "ex": 120, "ey": 224},
            {"from": "cave", "tx": 15, "ty": 1, "to": "field", "ex": 120, "ey": 224},
        ],
    }
    with open(os.path.join(PROJ, "world.toml"), "w", encoding="utf-8") as f:
        toml.dump(world, f)
    print("wrote tiles.png + world.toml")


if __name__ == "__main__":
    main()
