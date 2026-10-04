#!/usr/bin/env python3
"""vm-cam world: a 32x28 room (256x224 px) bigger than the GB screen, so the
follow camera scrolls -- until a script LOCKS it (SET_STATE camera_lock). After
running this, regenerate scenes: python mosaik_scenes.py projects/vm-camlock/world.toml
-o projects/vm-camlock/src/scenes.mos"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
from mosaik_assets import write_png_indexed
import toml
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAL = [(224,224,224),(96,96,96),(0,0,0)]
FLOOR, GRID, WALL = 0, 1, 2
W, H = 32, 28
def main():
    write_png_indexed(os.path.join(PROJ,"tiles.png"), 8, 24, [FLOOR]*64+[GRID]*64+[WALL]*64, PAL)
    tiles, coll = [], []
    for y in range(H):
        trow, crow = [], []
        for x in range(W):
            border = x==0 or y==0 or x==W-1 or y==H-1
            pillar = (x % 8 == 4 and y % 6 == 3)
            if border or pillar:
                trow.append(WALL); crow.append(1)
            elif (x + y) % 4 == 0:
                trow.append(GRID); crow.append(0)
            else:
                trow.append(FLOOR); crow.append(0)
        tiles.append(trow); coll.append(crow)
    world = {
        "world": {"module":"scenes","map_w":W,"map_h":H,"vm":True},
        "tileset": {"png":"tiles.png"},
        "kinds": {"player":0},
        "scene": [{"name":"field","scene_type":"topdown","map":tiles,"collision":coll,
                   "object":[{"kind":"player","x":128,"y":112}]}],
    }
    with open(os.path.join(PROJ,"world.toml"),"w") as f: toml.dump(world,f)
    print("wrote", os.path.join(PROJ,"world.toml"))
if __name__=="__main__": main()
