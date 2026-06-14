#!/usr/bin/env python3
"""Generate the Zelda-slice artwork (Phase 0 of docs/zelda-slice-plan.md).

Everything here is procedural -- no source art -- so the slice has a tiny,
self-contained, re-generatable asset set while the gameplay systems are built.
It writes:

  * assets/sprites.png          -- a NAMED-SPRITE SHEET: player_down/up/side,
                                   npc, enemy, chest (each 16x16 = 2x2 tiles) +
                                   an 8x8 heart HUD icon. 4 indexed colours
                                   (index 0 = transparent magenta key), so it
                                   stays GB 2bpp on every console -- 25 tiles,
                                   under the Lynx 32-tile sprite table.
  * assets/sprites.sprites.json -- the manifest (<name> -> [x,y,w,h] px), cut by
                                   the pipeline into <name>_tile / _w / _h.
  * assets/tiles_data.txt       -- the room TILESET (floor/wall/door/decoration,
                                   8x8 GB 2bpp) + four 32x32 TILEMAPS
                                   (ROOM0/1/2 + WORLDMAP) as mosaik const arrays
                                   to paste into the .mos.

Colour is the GB model: tiles/sprites carry shades 0..3; index/shade 0 is the
sprite-transparent / bkg-paper colour, and graphics.palette maps 1..3 to real
RGB per console (greys on the Game Boy / Mega Duck).

Run from the repository root:
    python projects/zelda-slice/assets/gen_sprites.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, os.pardir))
from mosaik_assets import write_png_indexed, shades_to_gb_tiles  # noqa: E402

# 4 indexed colours. These are only previews -- the ROM recolours shades 1..3
# via graphics.palette. Index 0 is the transparent key (tRNS) + bkg paper.
PALETTE = [
    (255, 0, 255),     # 0 transparent key / paper
    (224, 232, 200),   # 1 light
    (120, 148, 96),    # 2 mid
    (40, 48, 72),      # 3 dark / outline
]

# ---------------------------------------------------------------- pixel helpers


def blank(w, h):
    return [[0] * w for _ in range(h)]


def frect(g, x0, y0, x1, y1, c):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if 0 <= y < len(g) and 0 <= x < len(g[0]):
                g[y][x] = c


def disc(g, cx, cy, r, c):
    for y in range(len(g)):
        for x in range(len(g[0])):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r:
                g[y][x] = c


# ---------------------------------------------------------------- the sprites


def humanoid(face, fill):
    """A 16x16 little person. face: 'down' / 'up' / 'side'. fill = body shade."""
    g = blank(16, 16)
    # Head (outline ring + fill).
    disc(g, 7.5, 4, 3.6, 3)
    disc(g, 7.5, 4, 2.6, 1)
    if face == "down":
        g[4][6] = 3; g[4][9] = 3                 # two eyes
    elif face == "side":
        g[4][9] = 3                              # one eye (faces right)
    # Body (outline box + tunic fill).
    frect(g, 4, 8, 11, 13, 3)
    frect(g, 5, 9, 10, 12, fill)
    # Legs.
    g[14][6] = 3; g[15][6] = 3
    g[14][9] = 3; g[15][9] = 3
    return g


def enemy():
    g = blank(16, 16)
    disc(g, 7.5, 7.5, 6.2, 3)        # round body outline
    disc(g, 7.5, 7.5, 5.0, 2)        # body fill (mid)
    g[1][3] = 3; g[2][3] = 3         # left horn
    g[1][12] = 3; g[2][12] = 3       # right horn
    g[6][5] = 3; g[6][10] = 3        # angry eyes
    g[7][5] = 3; g[7][10] = 3
    frect(g, 6, 10, 9, 11, 3)        # mouth
    return g


def chest():
    g = blank(16, 16)
    frect(g, 2, 4, 13, 14, 3)        # body outline
    frect(g, 3, 5, 12, 13, 2)        # body fill
    frect(g, 2, 4, 13, 7, 3)         # lid band
    frect(g, 3, 5, 12, 6, 1)         # lid highlight
    frect(g, 7, 7, 8, 10, 3)         # lock plate
    g[8][7] = 1                      # keyhole glint
    return g


def heart():
    g = blank(8, 8)
    rows = [
        "..3..3..",
        ".3113113",
        ".3111113",
        ".3111113",
        "..31113.",
        "...313..",
        "....3...",
        "........",
    ]
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            g[y][x] = 0 if ch == "." else int(ch)
    return g


def gen_sprite_sheet():
    side = humanoid("side", 1)               # faces right (eye on the right)
    left = [row[::-1] for row in side]       # pre-mirrored: faces left
    cells = [
        ("player_down", humanoid("down", 1), 16),
        ("player_up",   humanoid("up", 1),   16),
        ("player_side", side,                16),
        # A dedicated left-facing frame instead of FLIP_X: the SMS/Game Gear VDP
        # has no hardware sprite-flip bit, so flipping a metasprite there reverses
        # the cell layout without mirroring the tiles (garbled). Pre-mirrored art
        # renders correctly on every console with no flip.
        ("player_left", left,                16),
        ("npc",         humanoid("down", 2), 16),
        ("enemy",       enemy(),             16),
        ("chest",       chest(),             16),
        ("heart",       heart(),             8),
    ]
    sheet_w = sum(w for _, _, w in cells)
    rows = blank(sheet_w, 16)
    manifest = {}
    x = 0
    for name, g, w in cells:
        h = len(g)
        manifest[name] = [x, 0, w, h]
        for yy in range(h):
            for xx in range(w):
                rows[yy][x + xx] = g[yy][xx]
        x += w

    out = os.path.join(HERE, "sprites.png")
    write_png_indexed(out, sheet_w, 16, rows, PALETTE, trns=[0])
    with open(os.path.join(HERE, "sprites.sprites.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print("wrote sprites.png (%dx16) + manifest: %s"
          % (sheet_w, ", ".join(manifest)))


# ---------------------------------------------------------------- the tileset


def room_tiles():
    # tile 0 floor, 1 wall, 2 door, 3 decoration (8x8, shades 0..3).
    floor = [[1] * 8 for _ in range(8)]
    floor[2][5] = 2; floor[5][2] = 2

    wall = [[3] * 8 for _ in range(8)]
    for x in range(8):
        wall[3][x] = 2; wall[7][x] = 2        # mortar courses
    for y in range(8):
        wall[y][0] = 2

    door = [[2] * 8 for _ in range(8)]
    frect_grid(door, 1, 0, 6, 7, 1)
    door[4][5] = 3                            # handle

    deco = [[1] * 8 for _ in range(8)]
    frect_grid(deco, 2, 2, 5, 5, 3)
    deco[3][3] = 2; deco[4][4] = 2
    return [floor, wall, door, deco]


def frect_grid(g, x0, y0, x1, y1, c):
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            g[y][x] = c


def room_map(door_edges):
    """32x32 room: wall border, floor interior, 2-wide door(s) on edge(s)."""
    m = [[0] * 32 for _ in range(32)]
    for y in range(32):
        for x in range(32):
            if x == 0 or y == 0 or x == 31 or y == 31:
                m[y][x] = 1                    # wall
            else:
                m[y][x] = 0                    # floor
    # A few decorations.
    for (dx, dy) in [(5, 5), (26, 6), (8, 24), (23, 22)]:
        m[dy][dx] = 3
    # Door (tile 2) punched through each chosen edge, centred and 3 tiles wide
    # (cols/rows 15-17) so a 16x16 player has slack to pass without pixel-exact
    # alignment.
    for edge in door_edges:
        if edge == "north":
            m[0][15] = 2; m[0][16] = 2; m[0][17] = 2
        elif edge == "south":
            m[31][15] = 2; m[31][16] = 2; m[31][17] = 2
        elif edge == "west":
            m[15][0] = 2; m[16][0] = 2; m[17][0] = 2
        elif edge == "east":
            m[15][31] = 2; m[16][31] = 2; m[17][31] = 2
    return m


def world_map():
    """A decorative worldmap screen: a checker of floor/decoration with a frame."""
    m = [[0] * 32 for _ in range(32)]
    for y in range(32):
        for x in range(32):
            if x == 0 or y == 0 or x == 31 or y == 31:
                m[y][x] = 1
            elif (x + y) % 4 == 0:
                m[y][x] = 3
            else:
                m[y][x] = 0
    return m


def mos_array(name, typ, flat, per_line=16):
    out = ["    const %s: array[%s, %d] = [" % (name, typ, len(flat))]
    for i in range(0, len(flat), per_line):
        out.append("        %s," % ", ".join("0x%02X" % b for b in flat[i:i + per_line]))
    out[-1] = out[-1].rstrip(",")
    out.append("    ]")
    return "\n".join(out)


def gen_tiles():
    flat_tiles = bytearray()
    for t in room_tiles():
        flat_tiles += shades_to_gb_tiles(8, 8, t)
    blocks = [mos_array("ROOM_TILES", "u8", list(flat_tiles))]
    # Connectivity: room0 is the hub (east -> room1, south -> room2);
    # room1's west door and room2's north door lead back to room0.
    for name, edges in [("ROOM0_MAP", ["east", "south"]),
                        ("ROOM1_MAP", ["west"]),
                        ("ROOM2_MAP", ["north"])]:
        flat = [b for row in room_map(edges) for b in row]
        blocks.append(mos_array(name, "u8", flat, per_line=32))
    flat = [b for row in world_map() for b in row]
    blocks.append(mos_array("WORLD_MAP", "u8", flat, per_line=32))
    with open(os.path.join(HERE, "tiles_data.txt"), "w") as f:
        f.write("\n\n".join(blocks) + "\n")
    print("wrote tiles_data.txt (4 tiles, 3 rooms + worldmap, 32x32)")


if __name__ == "__main__":
    gen_sprite_sheet()
    gen_tiles()
