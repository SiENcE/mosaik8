#!/usr/bin/env python3
"""vm-doors world: TWO screen-sized rooms (20x18 = 160x144, no scroll) joined by
[[door]] entries. The doors are DATA in world.toml -> DOOR_* tables; the shell
auto-registers them as native triggers (vm.trigger.add_door), so there are NO
hand-authored rects and NO per-door scripts. After running this, regenerate
scenes: python mosaik_scenes.py projects/vm-doors/world.toml -o
projects/vm-doors/src/scenes.mos"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
from mosaik_assets import write_png_indexed
import toml
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAL = [(224, 224, 224), (96, 96, 96), (0, 0, 0)]
FLOOR, GRID, WALL = 0, 1, 2
W, H = 20, 18
DOOR_ROWS = (8, 9)          # the doorway gap rows (world pixel y ~ 64-79)


def room(door_side):
    tiles, coll = [], []
    for y in range(H):
        trow, crow = [], []
        for x in range(W):
            edge = x == 0 or y == 0 or x == W - 1 or y == H - 1
            gap = ((door_side == "right" and x == W - 1 and y in DOOR_ROWS) or
                   (door_side == "left" and x == 0 and y in DOOR_ROWS))
            if edge and not gap:
                trow.append(WALL); crow.append(1)
            elif (x + y) % 5 == 0:
                trow.append(GRID); crow.append(0)
            else:
                trow.append(FLOOR); crow.append(0)
        tiles.append(trow); coll.append(crow)
    return tiles, coll


def main():
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 24,
                      [FLOOR] * 64 + [GRID] * 64 + [WALL] * 64, PAL)
    t0, c0 = room("right")
    t1, c1 = room("left")
    world = {
        "world": {"module": "scenes", "map_w": W, "map_h": H, "vm": True},
        "tileset": {"png": "tiles.png"},
        "kinds": {"player": 0},
        "scene": [
            {"name": "west", "scene_type": "topdown", "map": t0, "collision": c0,
             "object": [{"kind": "player", "x": 24, "y": 68}]},
            {"name": "east", "scene_type": "topdown", "map": t1, "collision": c1,
             "object": []},
        ],
        # doors are DATA: from-scene + trigger cell -> to-scene + entry pixel.
        "door": [
            {"from": "west", "tx": W - 1, "ty": 8, "to": "east", "ex": 20,  "ey": 68},
            {"from": "east", "tx": 0,     "ty": 8, "to": "west", "ex": 132, "ey": 68},
        ],
    }
    with open(os.path.join(PROJ, "world.toml"), "w") as f:
        toml.dump(world, f)
    print("wrote", os.path.join(PROJ, "world.toml"))


if __name__ == "__main__":
    main()
