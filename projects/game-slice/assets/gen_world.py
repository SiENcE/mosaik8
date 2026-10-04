#!/usr/bin/env python3
"""Generate the game-slice's Layer-3 world: tiles.png + world.toml.

Converts the room tileset (`ROOM_TILES`, 4 tiles) and the four 32x32 tilemaps
(`ROOM0/1/2_MAP`, `WORLD_MAP`) that lived as `const` arrays in src/mapdata.mos
into the declarative scene format the IDE's Scene editor reads -- four scenes
(room0, room1, room2, worldmap), in the id order the game uses (0..3). The
tileset round-trips exactly (2bpp -> indexed PNG -> 2bpp). Run, then transpile:

    python projects/game-slice/assets/gen_world.py
    python mosaik_scenes.py projects/game-slice/world.toml -o projects/game-slice/src/scenes.mos
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from mosaik_assets import write_png_indexed
import toml

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

PAL = [(248, 248, 248), (168, 168, 168), (96, 96, 96), (0, 0, 0)]
# Scene order == the game's room-id constants (ROOM0=0 .. WORLDMAP=3).
SCENES = [("room0", "ROOM0_MAP"), ("room1", "ROOM1_MAP"),
          ("room2", "ROOM2_MAP"), ("worldmap", "WORLD_MAP")]
KINDS = {"player": 0, "npc": 1, "chest": 2, "enemy": 3}
DOOR_TILE = 2

# Object placements per scene (kind, x, y) -- the game reads these from the
# generated OBJ table, so they are editable in the IDE's Scene editor.
OBJECTS = {
    "room0": [("player", 40, 40), ("npc", 56, 56), ("chest", 200, 48)],
    "room2": [("enemy", 60, 60), ("enemy", 184, 80), ("enemy", 120, 176)],
}


def door_cells(flat, w=32):
    return [(i % w, i // w) for i, v in enumerate(flat) if v == DOOR_TILE]


def build_doors(maps):
    """Door table from the DOOR tiles in each room, matching the slice's layout:
    room0 east edge -> room1, room0 south edge -> room2, and the return doors.
    Entries place the player one tile inside the opposite door (no re-trigger)."""
    doors = []
    for tx, ty in door_cells(maps["room0"]):
        if tx == 31:
            doors.append(("room0", tx, ty, "room1", 8, 120))    # east -> room1 W
        elif ty == 31:
            doors.append(("room0", tx, ty, "room2", 120, 8))    # south -> room2 N
    for tx, ty in door_cells(maps["room1"]):
        doors.append(("room1", tx, ty, "room0", 232, 120))      # west -> room0 E
    for tx, ty in door_cells(maps["room2"]):
        doors.append(("room2", tx, ty, "room0", 120, 232))      # north -> room0 S
    return doors


def parse_u8_array(src, name):
    m = re.search(r"const\s+%s\s*:\s*array\[u8,\s*\d+\]\s*=\s*\[(.*?)\]" % name,
                  src, re.S)
    if not m:
        raise SystemExit("array %s not found" % name)
    return [int(tok, 0) for tok in re.findall(r"0x[0-9A-Fa-f]+|\d+", m.group(1))]


def tiles_2bpp_to_indices(data):
    out = []
    for t in range(len(data) // 16):
        for r in range(8):
            lo, hi = data[t * 16 + r * 2], data[t * 16 + r * 2 + 1]
            for x in range(8):
                bit = 7 - x
                out.append(((lo >> bit) & 1) | (((hi >> bit) & 1) << 1))
    return out


def rows(flat, w):
    return [flat[i:i + w] for i in range(0, len(flat), w)]


def main():
    # The room tileset + maps live as `const` arrays in tiles_data.txt (the
    # original data dump); world.toml is generated from it.
    with open(os.path.join(HERE, "tiles_data.txt"), encoding="utf-8") as f:
        src = f.read()
    tileset = parse_u8_array(src, "ROOM_TILES")
    n_tiles = len(tileset) // 16

    idx = tiles_2bpp_to_indices(tileset)
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 8 * n_tiles, idx, PAL)

    maps = {}
    scenes = []
    for name, arr in SCENES:
        flat = parse_u8_array(src, arr)
        maps[name] = flat
        objs = [{"kind": k, "x": x, "y": y}
                for (k, x, y) in OBJECTS.get(name, [])]
        scenes.append({"name": name, "map": rows(flat, 32), "object": objs})

    doors = [{"from": f, "tx": tx, "ty": ty, "to": t, "ex": ex, "ey": ey}
             for (f, tx, ty, t, ex, ey) in build_doors(maps)]

    world = {
        "scene": scenes,
        "door": doors,
        "world": {"module": "scenes", "map_w": 32, "map_h": 32,
                  "scene_order": [s["name"] for s in scenes]},
        "tileset": {"png": "tiles.png"},
        "kinds": KINDS,
        # Which tile ids collide (the world's solid-tile set). The transpiler
        # exports this as scenes.SOLID_TILES / scenes.is_solid(t), so world.mos
        # reads it instead of hardcoding -- editable here (or in the studio) and
        # the per-cell inference arrays it also emits are tree-shaken out (we use
        # the tile-based path, not collision_at, so the Lynx build stays small).
        "collision": {"solid": [1]},   # tile id 1 = the wall
        # Animated water (reference-engine tile-data swap): map tile 4 -- a slot above the
        # 4-tile tileset -- cycles its pixel DATA through source tiles 0, 2, 0. The
        # scene transpiler lowers this to scenes.anim_tick(), which game_slice.mos
        # calls once per frame.
        "animated_tile": [{"name": "water", "tile": 4, "count": 2, "period": 20,
                           "frames": [0, 2, 0]}],
    }
    with open(os.path.join(PROJ, "world.toml"), "w", encoding="utf-8") as f:
        f.write(toml.dumps(world))
    print("wrote tiles.png (%d tiles), world.toml (%d scenes, %d doors, %d objects)"
          % (n_tiles, len(scenes), len(doors),
             sum(len(s["object"]) for s in scenes)))


if __name__ == "__main__":
    main()
