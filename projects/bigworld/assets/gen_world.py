#!/usr/bin/env python3
"""Generate the bigworld fixture: tiles.png + world.toml.

A SYNTHETIC many-scene world whose resident image is structurally too big for
the Atari Lynx (the 46,648-byte MAIN area holds CODE + RODATA + DATA + BSS
together, with no banking and no overlays). Every scene's 32x28 map (896 B) and
its parallel collision layer (896 B) is a `const` array, so N scenes cost
N * 1792 B of resident data on their own -- past ~26 scenes that alone exceeds
the whole Lynx area, before a single byte of code.

This is the worked proof of Lynx asset streaming: with
`[world] stream = true` (set below) the per-scene maps + collision stream from the
cart on room load, so the world BUILDS under the 46.6 KB MAIN area and runs on the
Lynx; remove that flag and the resident data overflows MAIN by ~17 KB (the problem
streaming solves). On every directly-mapped console the data stays resident in ROM
(byte-identical -- streaming is a Lynx-only build path).

It ships no clever art -- it is a deliberately oversized world.
On the directly-mapped consoles (GB/GBC/...) the same world overflows the 16 KB
home bank instead (mosaik does not bank `const` data yet), which is the same
"large game needs a residency story on every platform" point.

Run, then transpile:
    python projects/bigworld/assets/gen_world.py
    python mosaik_scenes.py projects/bigworld/world.toml -o projects/bigworld/src/scenes.mos
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from mosaik_assets import write_png_indexed
import toml

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

# Tile ids == background-tile indices: 0 floor, 1 deco, 2 door, 3 wall. A
# <=4-entry indexed PNG maps each pixel's palette index straight to its GB
# colour, so four solid tiles give four distinct shades.
PAL = [(248, 248, 248), (168, 168, 168), (96, 96, 96), (0, 0, 0)]
FLOOR, DECO, DOOR, WALL = 0, 1, 2, 3

# 32x28 is the MosaiK8 default scene size: 28 rows is the
# shortest console background, so the map is shown/scrolled in full on every
# console (the Lynx BG engine is 32 rows, so 28 fits). The fixture uses the
# default rather than an oversized 32x32 map.
W, H = 32, 28

# How many rooms. 28 * 1792 B of map+collision const data = 50,176 B, already
# past the Lynx 46,648-byte MAIN area before any code -- a guaranteed resident
# overflow regardless of code size. Bump this to grow the world.
N_SCENES = 28


def make_tiles_png():
    # 8x32 = four 8x8 tiles stacked, each a solid palette index.
    idx = [FLOOR] * 64 + [DECO] * 64 + [DOOR] * 64 + [WALL] * 64
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 32, idx, PAL)


def room(index, left_door, right_door):
    """A walled 32x32 room. `left_door`/`right_door` punch a 2-tall doorway in
    the side wall (rows 15-16) when set, so adjacent rooms connect. A couple of
    deco cells vary the map per room (and keep the data non-trivial)."""
    m = []
    for y in range(H):
        row = []
        for x in range(W):
            border = x == 0 or y == 0 or x == W - 1 or y == H - 1
            if left_door and x == 0 and y in (15, 16):
                row.append(DOOR)
            elif right_door and x == W - 1 and y in (15, 16):
                row.append(DOOR)
            elif border:
                row.append(WALL)
            elif (x + y + index) % 11 == 0:
                row.append(DECO)
            else:
                row.append(FLOOR)
        m.append(row)
    return m


def collision_for(m):
    """Solid where the visual tile is WALL (the door cells stay passable)."""
    return [[1 if c == WALL else 0 for c in row] for row in m]


def main():
    make_tiles_png()

    scenes = []
    doors = []
    for i in range(N_SCENES):
        has_left = i > 0
        has_right = i < N_SCENES - 1
        m = room(i, has_left, has_right)
        sc = {
            "name": "room%02d" % i,
            "map": m,
            "collision": collision_for(m),
            "object": ([{"kind": "player", "x": 120, "y": 120}] if i == 0 else []),
        }
        scenes.append(sc)
        # Chain rooms left<->right. The trigger is the player's CENTRE cell at
        # the doorway (rows 15-16, centre row 16). Enter the neighbour just
        # inside its opposite doorway so arrival never re-triggers.
        if has_right:
            doors.append({"from": "room%02d" % i, "tx": 31, "ty": 16,
                          "to": "room%02d" % (i + 1), "ex": 24, "ey": 120})
        if has_left:
            doors.append({"from": "room%02d" % i, "tx": 0, "ty": 16,
                          "to": "room%02d" % (i - 1), "ex": 216, "ey": 120})

    world = {
        # stream = true: the per-scene maps + collision layers are streamed from
        # the Lynx cart on room load instead of held
        # resident -- which is what makes this 28-room world BUILD on the Lynx
        # (without it the resident data overflows MAIN by ~17 KB). Byte-identical
        # on every other console (the maps stay resident in ROM there).
        "world": {"module": "scenes", "map_w": W, "map_h": H, "stream": True},
        "tileset": {"png": "tiles.png"},
        "kinds": {"player": 0},
        "scene": scenes,
        "door": doors,
    }
    out = os.path.join(PROJ, "world.toml")
    with open(out, "w", encoding="utf-8") as f:
        toml.dump(world, f)
    print("wrote %s (%d scenes, %d doors)" % (out, len(scenes), len(doors)))


if __name__ == "__main__":
    main()
