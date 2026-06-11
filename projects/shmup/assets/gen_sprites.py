#!/usr/bin/env python3
"""Regenerate sprites.png, the shmup's pregenerated tile sheet.

The checked-in PNG is the asset the build consumes (via `[assets] sprites`
in mosaik.toml); this script is only needed to change the artwork.

The sheet is a 32x8 indexed PNG with a 4-entry palette, so each palette
index is the literal GB colour value (see mosaik_assets.py): 0 = transparent,
1 = light, 2 = dark, 3 = black ink. Tiles left to right:

    0: player ship   1: enemy   2: bullet   3: explosion

Run from the repository root:  python projects/shmup/assets/gen_sprites.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, os.pardir, os.pardir))
from mosaik_assets import write_png_indexed

SHIP = [
    "...33...",
    "...33...",
    "..3223..",
    "..3223..",
    ".322223.",
    "33222233",
    "3.1221.3",
    "..3..3..",
]

ENEMY = [
    "..3333..",
    ".322223.",
    "33211233",
    "33222233",
    ".333333.",
    "3.3..3.3",
    "3......3",
    ".3....3.",
]

BULLET = [
    "........",
    "...11...",
    "...22...",
    "...33...",
    "...33...",
    "...22...",
    "........",
    "........",
]

BOOM = [
    "3..33..3",
    ".3.11.3.",
    "..1221..",
    "31222213",
    "31222213",
    "..1221..",
    ".3.11.3.",
    "3..33..3",
]

TILES = [SHIP, ENEMY, BULLET, BOOM]

# Palette: index 0 is the transparent colour (magenta, the usual marker in
# editors); 1..3 go light -> dark so the PNG previews like the GB rendering.
PALETTE = [(255, 0, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]


def main():
    rows = [[int(tile[y][x].replace(".", "0"))
             for tile in TILES for x in range(8)]
            for y in range(8)]
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sprites.png")
    write_png_indexed(out, 8 * len(TILES), 8, rows, PALETTE)
    print("wrote", out)


if __name__ == "__main__":
    main()
