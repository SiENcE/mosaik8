#!/usr/bin/env python3
"""Generate vm-clipdemo -- the reference proof of the VM8 animation system
(`lib/vm/clip.mos` + `mosaik_anim.py`): a single-screen platformer with a player
AND a pacing enemy, both animated via `vm.clip` from studio `[animations]` clips.
The enemy uses **flip_left** (its LEFT facing mirrors its RIGHT frames via FLIP_X).

Emits: tiles.png (2 tiles) + world.toml (one 20x18 scene) + sprites.png (a 2-frame
creature sheet, used for both) + sprites.sprites.toml + studio.toml [animations] +
src/pclips.mos (player clips) + src/eclips.mos (enemy clips, flip_left).

Run: python projects/vm-clipdemo/assets/gen.py
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

PAL = [(224, 248, 207), (136, 192, 112), (48, 104, 80), (8, 24, 32)]        # bkg (green)
SPR = [(255, 0, 255), (232, 232, 232), (72, 120, 224), (16, 16, 16)]        # sprite pens
W = H = 20


def tile_solid(fill):
    """One 8x8 tile: fill 0 (sky) or a solid brick pattern."""
    if fill == 0:
        return [0] * 64
    px = []
    for r in range(8):
        for c in range(8):
            px.append(3 if (r == 0 or c == 0) else 2)     # dark top/left edge, mid body
    return px


def creature(frame):
    """A 16x16 creature (index 0 transparent / 1 light / 2 body / 3 dark), 2 walk
    frames (the feet shift). Used for the player and the enemy alike."""
    g = [[0] * 16 for _ in range(16)]
    for y in range(2, 14):                 # rounded body
        for x in range(3, 13):
            g[y][x] = 2
    for y in range(2, 14):                 # outline
        g[y][3] = 3
        g[y][12] = 3
    for x in range(3, 13):
        g[2][x] = 3
        g[13][x] = 3
    g[6][6] = 1                            # eyes
    g[6][9] = 1
    g[7][6] = 3
    g[7][9] = 3
    g[8][11] = 1                           # an asymmetric cheek mark (so FLIP_X shows)
    g[9][11] = 1
    # feet (shift between frames = the waddle)
    if frame == 0:
        feet = [(14, 4), (14, 5), (14, 10), (14, 11)]
    else:
        feet = [(14, 4), (15, 5), (15, 10), (14, 11)]
    for (y, x) in feet:
        g[y][x] = 3
    return g


def rows(flat, w):
    return [flat[i:i + w] for i in range(0, len(flat), w)]


def main():
    # ---- tiles.png: 2 tiles (sky, brick) stacked 8x16 ---------------------- #
    idx = tile_solid(0) + tile_solid(1)
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 16, idx, PAL)

    # ---- one 20x18 scene: sky, a ground floor + two blocks ----------------- #
    grid = [[0] * W for _ in range(H)]
    for x in range(W):
        grid[H - 1][x] = 1
        grid[H - 2][x] = 1
    for x in range(6, 10):
        grid[H - 6][x] = 1                # a floating ledge
    world = {
        "world": {"module": "scenes", "map_w": W, "map_h": H, "vm": True},
        "tileset": {"png": "tiles.png"},
        "collision": {"solid": [1]},
        "kinds": {"player": 0, "enemy": 1},
        "scene": [{"name": "room", "scene_type": "platform", "map": grid,
                   "object": [{"kind": "player", "x": 24, "y": 40},
                              {"kind": "enemy", "x": 120, "y": 128}]}],
    }
    with open(os.path.join(PROJ, "world.toml"), "w", encoding="utf-8") as f:
        f.write(toml.dumps(world))
    data, base_dir = load_world(os.path.join(PROJ, "world.toml"))
    with open(os.path.join(PROJ, "src", "scenes.mos"), "w", encoding="utf-8") as f:
        f.write(transpile(data, base_dir))

    # ---- creature sheet: 2 frames (cr1, cr2), used for player + enemy ------ #
    f0, f1 = creature(0), creature(1)
    sheet_w = 32
    flat = [0] * (sheet_w * 16)
    for y in range(16):
        for x in range(16):
            flat[y * sheet_w + x] = f0[y][x]
            flat[y * sheet_w + 16 + x] = f1[y][x]
    png = os.path.join(PROJ, "assets", "sprites.png")
    write_png_indexed(png, sheet_w, 16, flat, SPR)
    write_sprite_manifest(png, [("cr1", [0, 0, 16, 16]), ("cr2", [16, 0, 16, 16])])
    cell = {n: off for (n, off, _w, _h) in sheet_sprite_defs(png)}

    # ---- clips: player (flat idle/walk) + enemy (walk, flip_left) ---------- #
    player_clips = {
        "idle": {"period": 8, "frames": ["cr1"]},
        "walk": {"period": 6, "frames": ["cr1", "cr2"]},
    }
    enemy_clips = {
        "walk": {"period": 8, "flip_left": True, "right": ["cr1", "cr2"]},
    }
    with open(os.path.join(PROJ, "studio.toml"), "w", encoding="utf-8") as f:
        f.write(toml.dumps({"animations": {"player": player_clips,
                                           "enemy": enemy_clips}}))
    with open(os.path.join(PROJ, "src", "pclips.mos"), "w", encoding="utf-8") as f:
        f.write(mosaik_anim.transpile(player_clips, cell, module="pclips"))
    with open(os.path.join(PROJ, "src", "eclips.mos"), "w", encoding="utf-8") as f:
        f.write(mosaik_anim.transpile(enemy_clips, cell, module="eclips"))

    print("vm-clipdemo: 2-tile scene 20x18, creature sheet 2 frames, "
          "player clips %s + enemy clips %s (flip_left)"
          % (list(player_clips), list(enemy_clips)))


if __name__ == "__main__":
    main()
