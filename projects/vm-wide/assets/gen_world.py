#!/usr/bin/env python3
"""vm-wide world: a 64x18 (512x144 px) platformer level, WIDER than the screen,
so the u16 camera COLUMN-STREAMS the background (engine.scroll). A distinct
MARKER column at col 40 (px 320, far past the 256px hardware wrap) proves content
streams in. Regenerate + transpile:
    python projects/vm-wide/assets/gen_world.py
    python mosaik_scenes.py projects/vm-wide/world.toml -o projects/vm-wide/src/scenes.mos
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)
from mosaik_assets import write_png_indexed
import toml
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAL = [(224,224,224),(0,0,0),(120,120,120)]
FLOOR, WALL, MARK = 0, 1, 2
W, H = 64, 18
def main():
    write_png_indexed(os.path.join(PROJ,"tiles.png"), 8, 24, [FLOOR]*64+[WALL]*64+[MARK]*64, PAL)
    tiles, coll = [], []
    for y in range(H):
        trow, crow = [], []
        for x in range(W):
            solid = (x==0 or x==W-1 or y>=H-2)
            if solid:
                trow.append(WALL); crow.append(1)
            elif x==40 and 1<=y<=H-3:
                trow.append(MARK); crow.append(0)          # a visual marker stripe
            else:
                trow.append(FLOOR); crow.append(0)
        tiles.append(trow); coll.append(crow)
    world = {
        "world": {"module":"scenes","map_w":W,"map_h":H,"vm":True},
        "tileset": {"png":"tiles.png"},
        "kinds": {"player":0},
        "scene": [{"name":"level","scene_type":"platform","map":tiles,"collision":coll,
                   "object":[{"kind":"player","x":24,"y":100}]}],
    }
    with open(os.path.join(PROJ,"world.toml"),"w") as f: toml.dump(world,f)
    print("wrote", os.path.join(PROJ,"world.toml"))
if __name__=="__main__": main()
