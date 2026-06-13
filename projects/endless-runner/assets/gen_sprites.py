#!/usr/bin/env python3
"""Regenerate the Endless Runner artwork.

The sprites come straight from Dr. Ludos' ORIGINAL spritesheet,
`_samples/running-knight/game/gfx_sprites.pcx` (read with Pillow), cut at the
rectangles the original `game/Makefile` feeds to sp65. Each sprite is snapped
to a multiple of 8 px (mosaik tiles are 8x8) and packed -- with the original
16-colour palette -- into one small indexed sheet + a sidecar manifest:

  * assets/sprites.png            -- knight0, knight1, pillar, chest, each a
                                     16x32 (2x4-tile) sprite, index 0 = the
                                     transparent magenta key (tRNS).
  * assets/sprites.sprites.json   -- the manifest (<name>_tile / _w / _h).
  * assets/bkg_data.txt           -- the scrolling-ground TILESET + TILEMAP as
                                     mosaik const arrays (paste into the .mos).

Colour tiering is automatic: on the Atari Lynx the sheet is encoded 4bpp and
`palette.load_sprite16` loads the original 16 colours into the Mikey pens; on
every other console it is luma-quantised to the 4-shade GB model and coloured
with `graphics.palette` (greys on the Game Boy / Mega Duck).

Run from the repository root:
    python projects/endless-runner/assets/gen_sprites.py
"""

import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, os.pardir))
from mosaik_assets import write_png_indexed, shades_to_gb_tiles  # noqa: E402

PCX = os.path.join(HERE, os.pardir, os.pardir, os.pardir,
                   "_samples", "running-knight", "game", "gfx_sprites.pcx")

# name -> (source rect in the PCX from game/Makefile --slice,
#          where to blit it inside its 16x32 sheet cell). Two knight frames +
#          pillar + chest = 32 Lynx tiles (the full 32-tile sprite table).
# The chest (16x16) sits in the lower half of its cell so it shares the ground
# line with the full-height pillar/knight.
CELL_W, CELL_H = 16, 32
SPRITES = [
    ("knight0", (64, 0, 16, 32), (0, 0)),     # gfx_knight01
    ("knight1", (128, 0, 16, 32), (0, 0)),    # gfx_knight05 (clear stride)
    ("pillar",  (64, 32, 16, 32), (0, 0)),    # gfx_pillar (top 32 of its 35)
    ("chest",   (80, 32, 16, 16), (0, 16)),   # gfx_chest -> lower half
]


def gen_sprite_sheet():
    im = Image.open(PCX).convert("P")
    pal = im.getpalette()
    palette = [tuple(pal[i * 3:i * 3 + 3]) for i in range(16)]
    # Index 0 is the transparent key, so its colour is never drawn on a sprite.
    # Repurpose it as a ground colour: on the Lynx (no scrolling tilemap -- the
    # Suzy composite + 16x32 sprites would blow the per-frame blit budget) the
    # present clears to pen 0, giving a solid ground backdrop.
    palette[0] = (56, 80, 48)
    px = im.load()

    sheet_w = CELL_W * len(SPRITES)
    rows = [[0] * sheet_w for _ in range(CELL_H)]    # 0 = transparent key
    manifest = {}
    for col, (name, (sx, sy, sw, sh), (ox, oy)) in enumerate(SPRITES):
        cx = col * CELL_W
        manifest[name] = [cx, 0, CELL_W, CELL_H]
        for yy in range(sh):
            for xx in range(sw):
                rows[oy + yy][cx + ox + xx] = px[sx + xx, sy + yy]

    out = os.path.join(HERE, "sprites.png")
    # index 0 transparent (tRNS) so the magenta key drops out on the 2bpp
    # luma path too, not just the Lynx 4bpp path.
    write_png_indexed(out, sheet_w, CELL_H, rows, palette, trns=[0])
    with open(os.path.join(HERE, "sprites.sprites.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print("wrote sprites.png (%dx%d) + manifest: %s"
          % (sheet_w, CELL_H, ", ".join(manifest)))


# --- Background: a scrolling textured ground (8x8 GB 2bpp tiles + 32x32 map) ---

def bkg_tiles():
    def g(): return [[1] * 8 for _ in range(8)]      # base ground = colour 1
    t0 = g()
    t1 = g(); t1[2][2] = 2; t1[5][5] = 2
    t2 = g()
    for x in range(8):
        t2[6][x] = 2; t2[7][x] = 2
    t3 = g(); t3[3][1] = 3; t3[3][2] = 3; t3[6][5] = 3
    return [t0, t1, t2, t3]


def bkg_map():
    m = []
    for y in range(32):
        row = []
        for x in range(32):
            h = (x * 7 + y * 13) % 17
            row.append(2 if h == 0 else 3 if h == 1 else 1 if h in (2, 3) else 0)
        m.append(row)
    return m


def mos_array(name, typ, flat, per_line=16):
    out = ["    const %s: array[%s, %d] = [" % (name, typ, len(flat))]
    for i in range(0, len(flat), per_line):
        out.append("        %s," % ", ".join("0x%02X" % b for b in flat[i:i + per_line]))
    out[-1] = out[-1].rstrip(",")
    out.append("    ]")
    return "\n".join(out)


def gen_bkg():
    flat_tiles = bytearray()
    for t in bkg_tiles():
        flat_tiles += shades_to_gb_tiles(8, 8, t)
    flat_map = [b for row in bkg_map() for b in row]
    text = (mos_array("BKG_TILES", "u8", list(flat_tiles)) + "\n\n"
            + mos_array("BKG_MAP", "u8", flat_map, per_line=32) + "\n")
    with open(os.path.join(HERE, "bkg_data.txt"), "w") as f:
        f.write(text)
    print("wrote bkg_data.txt (4 tiles, 32x32 map)")


if __name__ == "__main__":
    gen_sprite_sheet()
    gen_bkg()
