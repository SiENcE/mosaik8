#!/usr/bin/env python3
"""Generate vm-danim -- the DATA-DRIVEN VM8 animation proof (Option X). Unlike
vm-clipdemo (shell code drives vm.clip), here a SCRIPT event (`actor_set_clip`)
assigns the clip and the fixed shell + the runtime (vm.anim, driven by
core.set_anim) animate the actor automatically -- the studio-VM model.

Emits tiles.png + world.toml ([kinds] walker) + sprites.png (a 2-frame creature) +
studio.toml [animations.walker] + src/clips.mos (mosaik_anim.transpile_kinds).

Run: python projects/vm-danim/assets/gen.py
     python mosaik_vm.py projects/vm-danim -o projects/vm-danim/src/scripts.mos
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PROJ))
sys.path.insert(0, ROOT)

from mosaik_assets import (write_png_indexed, write_sprite_manifest,   # noqa: E402
                           sheet_sprite_defs)
from mosaik_scenes import transpile, load_world                        # noqa: E402
import mosaik_anim                                                      # noqa: E402
import toml                                                             # noqa: E402

PAL = [(224, 248, 207), (136, 192, 112), (48, 104, 80), (8, 24, 32)]
# A 16-colour creature palette (index 0 = transparent) -- the 4bpp colour tier:
# on the Lynx + PC Engine the sprite shows all these; the GB family / SMS / GG
# down-tier to their 4-colour best (see setup_spr_pal in src/main.mos).
SPR = [
    (24, 24, 40),     # 0  transparent (a dark value, not a garish key: on the PCE the
    (24, 24, 40),     # 1  dark outline
    (232, 232, 240),  # 2  white (eyes)
    (248, 216, 72),   # 3  yellow (asymmetric mark)
    (96, 152, 232),   # 4  body blue - lightest
    (72, 120, 224),   # 5  body blue
    (56, 96, 200),    # 6  body blue - mid
    (40, 72, 168),    # 7  body blue - dark
    (232, 96, 72),    # 8  red (feet)
    (120, 208, 112),  # 9  green (belly)
    (200, 120, 232),  # 10 purple accent
    (248, 160, 64),   # 11 orange (pupils)
    (64, 216, 208),   # 12 teal accent
    (232, 232, 128),  # 13 pale (unused-spare)
    (160, 96, 64),    # 14 brown (unused-spare)
    (24, 24, 24),     # 15 black (unused-spare)
]
W, H = 20, 18


def tile(fill):
    if fill == 0:
        return [0] * 64
    return [3 if (r == 0 or c == 0) else 2 for r in range(8) for c in range(8)]


def creature(frame):
    g = [[0] * 16 for _ in range(16)]
    # body with a vertical blue GRADIENT (indices 4-7) so >4 colours show
    grad = [4, 4, 5, 5, 5, 6, 6, 6, 7, 7, 7, 7]     # 12 rows (y 2..13)
    for i, y in enumerate(range(2, 14)):
        for x in range(3, 13):
            g[y][x] = grad[i]
    for y in range(2, 14):                          # dark outline (1)
        g[y][3] = 1
        g[y][12] = 1
    for x in range(3, 13):
        g[2][x] = 1
        g[13][x] = 1
    for y in range(9, 13):                          # green belly patch (9)
        for x in range(5, 11):
            g[y][x] = 9
    g[10][7] = 12                                   # teal + purple accents
    g[10][8] = 12
    g[11][6] = 10
    g[11][9] = 10
    g[6][6] = 2                                     # white eyes (2) + orange pupils (11)
    g[6][9] = 2
    g[7][6] = 11
    g[7][9] = 11
    g[8][11] = 3                                    # yellow asymmetric mark (FLIP_X visible)
    g[9][11] = 3
    feet = [(14, 4), (14, 5), (14, 10), (14, 11)] if frame == 0 \
        else [(14, 4), (15, 5), (15, 10), (14, 11)]
    for (y, x) in feet:
        g[y][x] = 8                                 # red feet (8)
    return g


def main():
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 16, tile(0) + tile(1), PAL)

    grid = [[0] * W for _ in range(H)]
    for x in range(W):
        grid[H - 1][x] = 1
    world = {
        "world": {"module": "scenes", "map_w": W, "map_h": H, "vm": True},
        "tileset": {"png": "tiles.png"},
        "kinds": {"walker": 0},
        "scene": [{"name": "room", "scene_type": "topdown", "map": grid}],
    }
    with open(os.path.join(PROJ, "world.toml"), "w", encoding="utf-8") as f:
        f.write(toml.dumps(world))
    data, base_dir = load_world(os.path.join(PROJ, "world.toml"))
    with open(os.path.join(PROJ, "src", "scenes.mos"), "w", encoding="utf-8") as f:
        f.write(transpile(data, base_dir))

    f0, f1 = creature(0), creature(1)
    sheet_w = 32
    flat = [0] * (sheet_w * 16)
    for y in range(16):
        for x in range(16):
            flat[y * sheet_w + x] = f0[y][x]
            flat[y * sheet_w + 16 + x] = f1[y][x]
    png = os.path.join(PROJ, "assets", "sprites.png")
    # trns=[0] marks index 0 transparent: on the 4bpp tier (Lynx/PCE) index 0 is
    # transparent by convention, and on the 2bpp down-tier (a >4-colour indexed PNG
    # goes through the luma path, which would otherwise render index 0 opaque) the
    # alpha=0 makes it transparent -- so the creature's corners stay clear everywhere.
    write_png_indexed(png, sheet_w, 16, flat, SPR, trns=[0])
    write_sprite_manifest(png, [("cr1", [0, 0, 16, 16]), ("cr2", [16, 0, 16, 16])])
    cell = {n: off for (n, off, _w, _h) in sheet_sprite_defs(png)}

    # the walker's clips: an idle + a directional walk whose LEFT mirrors RIGHT
    anims = {"walker": {
        "idle": {"period": 8, "frames": ["cr1"]},
        "walk": {"period": 8, "flip_left": True, "down": ["cr1", "cr2"],
                 "up": ["cr1", "cr2"], "right": ["cr1", "cr2"]},
    }}
    with open(os.path.join(PROJ, "studio.toml"), "w", encoding="utf-8") as f:
        f.write(toml.dumps({"animations": anims}))
    # SMS/GG soft-flip bake: mirror the RIGHT cells the walk's LEFT derives from, so the
    # no-sprite-flip consoles show the correct facing (the clips module forks on platform).
    cells = mosaik_anim._mirror_cells(
        clip for clips in anims.values() for clip in clips.values())
    bake = mosaik_anim.build_flip_bake(cells, [png])
    with open(os.path.join(PROJ, "src", "clips.mos"), "w", encoding="utf-8") as f:
        f.write(mosaik_anim.transpile_kinds(anims, cell, {"walker": 0}, module="clips",
                                            bake=bake))

    print("vm-danim: 2-tile scene, creature sheet, [animations.walker] -> clips.mos")


if __name__ == "__main__":
    main()
