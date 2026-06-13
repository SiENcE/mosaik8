#!/usr/bin/env python3
"""Regenerate swatch.png, the colorlab gem tile.

The checked-in PNG is the asset the build consumes (via `[assets] sprites`
in mosaik.toml); this script is only needed to change the artwork.

It is an 8x8 indexed PNG with a 4-entry palette, so each palette index is
the literal GB colour value (see mosaik_assets.py): 0 = transparent, 1..3 =
the gem's highlight / body / outline. For programs that import
graphics.palette the build also emits `swatch_palette` (the four colours
below, converted to the target's native format), so `palette.load_sprite`
recolours the gem with its *authored* colours on every console — the
asset-pipeline color path. colorlab loads it into sprite slot 0.

Run from the repository root:  python projects/colorlab/assets/gen_swatch.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir, os.pardir, os.pardir))
from mosaik_assets import write_png_indexed

GEM = [
    "...33...",
    "..3223..",
    ".322223.",
    "32211223",
    "32211223",
    ".322223.",
    "..3223..",
    "...33...",
]

# The gem's authored colours: 0 transparent (magenta editor marker), then an
# ice-blue jewel — highlight, body, deep outline.
PALETTE = [(255, 0, 255), (170, 235, 255), (40, 170, 220), (20, 60, 120)]


def main():
    rows = [[int(GEM[y][x].replace(".", "0")) for x in range(8)]
            for y in range(8)]
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "swatch.png")
    write_png_indexed(out, 8, 8, rows, PALETTE)
    print("wrote", out)


if __name__ == "__main__":
    main()
