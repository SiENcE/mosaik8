#!/usr/bin/env python3
"""Generate vm-palanim -- the per-FRAME SPRITE PALETTE proof (Stage R).

One clip field, three behaviours, plus the control that guards the sentinel:

- `blinker`  alternates sprite palettes 1 and 2 frame by frame over ONE piece
  of art (`pal = [1, 2]`) - the reference engine's savepoint shimmer. The art dedupes:
  a recolour costs no tiles.
- `flasher`  alternates OBP0/OBP1 (`pal = [0, 16]`, bit 4 = the DMG select) -
  how a plain Game Boy flashes a sprite, its only way to. On the colour
  consoles bit 4 is ignored and the sprite sits still on palette 0, honestly.
- `stately`  recolours per STATE (idle `pal = [3]`, walk `pal = [2]`) - the
  exploding-mine shape: one palette standing, another the moment it moves.
- the PLAYER is deliberately UNCOLOURED: its F_PAL entries are the 255
  no-palette sentinel, so the animator must leave its palette alone while the
  three actors recolour around it. That is the regression this project pins on
  real hardware (an uncoloured default of 0 once flattened every coloured
  actor in a room).

Per console (the portable `sprite.set_palette` story): GBC/Pocket show all of
it; the DMG shows the flasher (2 palettes: OBP0/OBP1); Lynx/PCE show the
recolours on their 4 sprite slots; SMS/GG ignore every write (one sprite
palette) and render the art in it, honestly. The NES is excluded the way
vm-danim excludes it: vm.canim's clip callbacks carry 4 argument bytes and a
6502 function-pointer call takes 2.

Emits tiles.png + world.toml + sprites.png + studio.toml [animations] +
src/scenes.mos + src/clips.mos.

Run: python projects/vm-palanim/assets/gen.py
     python -m mosaik_vm projects/vm-palanim/scripts -o projects/vm-palanim/src/scripts.mos
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
# The sprite sheet's own 4 colours (index 0 transparent). What each INDEX
# shows is the palette's business - that is the whole demonstration.
SPR = [(101, 255, 0), (232, 232, 240), (136, 136, 160), (24, 24, 40)]
W, H = 20, 18


def tile(fill):
    if fill == 0:
        return [0] * 64
    return [3 if (r == 0 or c == 0) else 2 for r in range(8) for c in range(8)]


def orb(frame):
    """A 16x16 orb: outline 3, body 2, highlight 1. Frame B moves the
    highlight, so the frame STEP is visible even where the palette write is
    ignored (SMS/GG)."""
    g = [[0] * 16 for _ in range(16)]
    for y in range(2, 14):
        for x in range(2, 14):
            if (x - 8) * (x - 8) + (y - 8) * (y - 8) <= 36:
                g[y][x] = 2
    for y in range(2, 14):
        for x in range(2, 14):
            if g[y][x] == 2 and (g[y - 1][x] == 0 or g[y + 1][x] == 0
                                 or g[y][x - 1] == 0 or g[y][x + 1] == 0):
                g[y][x] = 3
    hx = 5 if frame == 0 else 9
    for y in (5, 6):
        for x in (hx, hx + 1):
            g[y][x] = 1
    return g


def main():
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 16,
                      tile(0) + tile(1), PAL)
    grid = [[0] * W for _ in range(H)]
    for x in range(W):
        grid[H - 1][x] = 1
    world = {
        "world": {"module": "scenes", "map_w": W, "map_h": H, "vm": True},
        "tileset": {"png": "tiles.png"},
        "kinds": {"blinker": 0, "flasher": 1, "stately": 2, "hero": 3},
        "scene": [{"name": "room", "scene_type": "topdown", "map": grid}],
    }
    with open(os.path.join(PROJ, "world.toml"), "w", encoding="utf-8") as f:
        f.write(toml.dumps(world))
    data, base_dir = load_world(os.path.join(PROJ, "world.toml"))
    with open(os.path.join(PROJ, "src", "scenes.mos"), "w", encoding="utf-8") as f:
        f.write(transpile(data, base_dir))

    f0, f1 = orb(0), orb(1)
    sheet_w = 32
    flat = [0] * (sheet_w * 16)
    for y in range(16):
        for x in range(16):
            flat[y * sheet_w + x] = f0[y][x]
            flat[y * sheet_w + 16 + x] = f1[y][x]
    png = os.path.join(PROJ, "assets", "sprites.png")
    write_png_indexed(png, sheet_w, 16, flat, SPR, trns=[0])
    write_sprite_manifest(png, [("orb1", [0, 0, 16, 16]),
                                ("orb2", [16, 0, 16, 16])])
    cell = {n: off for (n, off, _w, _h) in sheet_sprite_defs(png)}

    # The whole feature is these `pal` lists (and the hero's ABSENCE of one).
    anims = {
        "blinker": {"idle": {"period": 24, "frames": ["orb1", "orb2"],
                             "pal": [1, 2]}},
        "flasher": {"idle": {"period": 24, "frames": ["orb1", "orb2"],
                             "pal": [0, 16]}},
        "stately": {"idle": {"period": 24, "frames": ["orb1"], "pal": [3]},
                    "walk": {"period": 12, "frames": ["orb1", "orb2"],
                             "pal": [2]}},
        "hero": {"idle": {"period": 8, "frames": ["orb1"]},
                 "walk": {"period": 8, "frames": ["orb1", "orb2"]}},
    }
    with open(os.path.join(PROJ, "studio.toml"), "w", encoding="utf-8") as f:
        f.write(toml.dumps({"animations": anims}))
    kind_ids = {"blinker": 0, "flasher": 1, "stately": 2, "hero": 3}
    with open(os.path.join(PROJ, "src", "clips.mos"), "w", encoding="utf-8") as f:
        f.write(mosaik_anim.transpile_kinds(anims, cell, kind_ids,
                                            module="clips"))
    print("vm-palanim: 2-tile scene, orb sheet, 3 coloured clips + 1 control"
          " -> clips.mos (F_PAL)")


if __name__ == "__main__":
    main()
