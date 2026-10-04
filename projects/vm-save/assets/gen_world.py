#!/usr/bin/env python3
"""Generate vm-room's world: tiles.png + world.toml (a VM8 scene game).

A single screen-sized (20x18) walled room with a painted COLLISION LAYER, a
player start and an NPC placement -- the data a VM8 topdown scene consumes
(vm.player walks the player with native collision; a script wanders the NPC).
`[world] vm = true` makes the transpiler emit the SCENE_TYPE table.

    python projects/vm-room/assets/gen_world.py
    python mosaik_scenes.py projects/vm-room/world.toml -o projects/vm-room/src/scenes.mos
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from mosaik_assets import write_png_indexed
import toml

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

PAL = [(224, 224, 224), (0, 0, 0)]     # 0 floor (light), 1 wall (black)
FLOOR, WALL = 0, 1
W, H = 20, 18                          # GB screen = 20x18 tiles


def make_tiles_png():
    idx = [FLOOR] * 64 + [WALL] * 64   # two solid 8x8 tiles
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 16, idx, PAL)


def main():
    make_tiles_png()
    tiles, coll = [], []
    for y in range(H):
        trow, crow = [], []
        for x in range(W):
            border = x == 0 or y == 0 or x == W - 1 or y == H - 1
            trow.append(WALL if border else FLOOR)
            crow.append(1 if border else 0)     # 1 = COLLIDE_SOLID
        tiles.append(trow)
        coll.append(crow)

    world = {
        "world": {"module": "scenes", "map_w": W, "map_h": H, "vm": True},
        "tileset": {"png": "tiles.png"},
        "kinds": {"player": 0, "npc": 1},
        "scene": [
            {"name": "room", "scene_type": "topdown", "map": tiles, "collision": coll,
             "object": [
                 {"kind": "player", "x": 80, "y": 72},
                 {"kind": "npc", "x": 32, "y": 32},
             ]},
        ],
    }
    with open(os.path.join(PROJ, "world.toml"), "w") as f:
        toml.dump(world, f)
    print("wrote", os.path.join(PROJ, "world.toml"))


if __name__ == "__main__":
    main()
