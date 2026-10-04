#!/usr/bin/env python3
"""Generate vm-room2's world: two screen-sized rooms joined by doorways.

Room 0 has a gap in its RIGHT wall; room 1 a gap in its LEFT wall. The shell
registers a trigger rect at each gap (vm.trigger); walking into it spawns a door
SCRIPT that raises CHANGE_SCENE (the doors->triggers->scripts model, §9). The gap
cells are painted floor + marked non-solid so the player can reach them.

    python projects/vm-room2/assets/gen_world.py
    python mosaik_scenes.py projects/vm-room2/world.toml -o projects/vm-room2/src/scenes.mos
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from mosaik_assets import write_png_indexed
import toml

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

PAL = [(224, 224, 224), (0, 0, 0)]
FLOOR, WALL = 0, 1
W, H = 20, 18
GAP_ROWS = (8, 9)               # doorway rows


def make_tiles_png():
    idx = [FLOOR] * 64 + [WALL] * 64
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 16, idx, PAL)


def room(gap_col):
    """A walled room with a doorway gap at column `gap_col`, rows GAP_ROWS."""
    tiles, coll = [], []
    for y in range(H):
        trow, crow = [], []
        for x in range(W):
            gap = (x == gap_col and y in GAP_ROWS)
            border = x == 0 or y == 0 or x == W - 1 or y == H - 1
            solid = border and not gap
            trow.append(WALL if solid else FLOOR)
            crow.append(1 if solid else 0)
        tiles.append(trow)
        coll.append(crow)
    return tiles, coll


def main():
    make_tiles_png()
    r0_t, r0_c = room(W - 1)     # gap in the RIGHT wall
    r1_t, r1_c = room(0)         # gap in the LEFT wall
    world = {
        "world": {"module": "scenes", "map_w": W, "map_h": H, "vm": True},
        "tileset": {"png": "tiles.png"},
        "kinds": {"player": 0},
        "scene": [
            {"name": "west", "scene_type": "topdown", "map": r0_t, "collision": r0_c,
             "object": [{"kind": "player", "x": 24, "y": 68}]},
            {"name": "east", "scene_type": "topdown", "map": r1_t, "collision": r1_c,
             "object": []},
        ],
    }
    with open(os.path.join(PROJ, "world.toml"), "w") as f:
        toml.dump(world, f)
    print("wrote", os.path.join(PROJ, "world.toml"))


if __name__ == "__main__":
    main()
