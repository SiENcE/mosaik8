#!/usr/bin/env python3
"""Generate vm-showcase -- the VM8 ALL-SCENE-TYPES showcase, NO game code.

One project, four rooms, each a different scene TYPE over the SAME fixed engine:

  0 title   (menu)     -- a player-less title MENU (scene On Init runs a `menu`
                          script; the pick change_scene's into a room)
  1 field   (topdown)  -- d-pad walk; a wandering NPC you can TALK to (entity
                          On Update / On Interact slots), a ding TRIGGER, a door
  2 meadow  (platform) -- run + jump under gravity with a DOUBLE JUMP
                          (studio.toml [player] air_jumps = 1); ledges + a door
  3 skyway  (shmup)    -- VERTICAL AUTO-SCROLL (the scene is taller than the
                          screen, so it scrolls UP from the bottom); solid side
                          walls; the exit door waits at the top

Everything is DATA: the world (world.toml), the logic (scripts/main.evt.toml),
the music (scripts/songs.toml), the player physics (studio.toml [player]). The
shell (src/main.mos) is the fixed slim boot; ALL world wiring lives in the
GENERATED src/rooms.mos (mosaik_vm.generate_rooms), which emits ONLY the scene
types + packs this world uses -- with [build] shake_exports the unused engine
surface never reaches the ROM.

    python projects/vm-showcase/assets/gen_world.py
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

import toml                                   # noqa: E402
from mosaik_assets import write_png_indexed   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

# 4 stacked 8x8 tiles: 0 floor (light) / 1 grass / 2 path / 3 wall (dark)
PAL = [(236, 236, 236), (120, 184, 120), (200, 176, 130), (40, 40, 40)]
FLOOR, GRASS, PATH, WALL = 0, 1, 2, 3
W, H = 20, 18                                 # world default = one GB screen


def make_tiles_png():
    idx = []
    for tile in (FLOOR, GRASS, PATH, WALL):
        idx += [tile] * 64
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 32, idx, PAL)


def _grid(w, h, fill=FLOOR):
    return [[fill] * w for _ in range(h)]


def _border(rows, tile=WALL):
    w, h = len(rows[0]), len(rows)
    for x in range(w):
        rows[0][x] = tile
        rows[h - 1][x] = tile
    for y in range(h):
        rows[y][0] = tile
        rows[y][w - 1] = tile
    return rows


def _coll_from(rows, solid_tiles=(WALL,)):
    return [[1 if t in solid_tiles else 0 for t in row] for row in rows]


def scene_title():
    # 32 tiles wide = 256 px = the GB BG-map width, so the title backdrop scrolls
    # SEAMLESSLY (bkg wraps at 256) -- the decoupled `scroll_bg` demo (a menu scene
    # with no player, its background sliding while you pick).
    rows = _border(_grid(32, H, FLOOR))
    for y in range(2, H - 2, 3):              # decorative grass bands across the width
        for x in range(2, 30, 2):
            rows[y][x] = GRASS
    return {"name": "title", "scene_type": "menu", "on_init": "title",
            "map_w": 32, "map": rows}


def scene_field():
    rows = _border(_grid(W, H, FLOOR))
    for x in range(2, W - 1):                 # a path toward the east door
        rows[9][x] = PATH
    for y, x in ((3, 5), (4, 14), (12, 4), (13, 15), (6, 9)):
        rows[y][x] = GRASS
    rows[9][W - 1] = PATH                     # the door gap in the east wall
    coll = _coll_from(rows)
    coll[9][W - 1] = 0                        # walkable door cell
    return {"name": "field", "scene_type": "topdown", "map": rows,
            "collision": coll,
            "object": [
                {"kind": "player", "x": 80, "y": 72},
                # the NPC's behavior slots ARE the VM8 entity model: On Update
                # wanders it (a long-lived looping thread), On Interact talks.
                {"kind": "npc", "x": 32, "y": 32,
                 "on_update": "wander", "on_interact": "npc_talk"},
            ]}


MW = 32                                        # meadow: 32 tiles = 256 px wide


def scene_meadow():
    rows = _grid(MW, H, FLOOR)
    for x in range(MW):                        # the ground
        rows[16][x] = GRASS
        rows[17][x] = WALL
    for y in range(H):                         # end walls
        rows[y][0] = WALL
        rows[y][MW - 1] = WALL
    # floating ledges -- the higher ones need the DOUBLE jump
    ledges = ((13, 6, 10), (10, 12, 16), (7, 18, 22), (11, 24, 28))
    for ly, x0, x1 in ledges:
        for x in range(x0, x1):
            rows[ly][x] = PATH
    rows[14][MW - 1] = PATH                    # the east door gap
    rows[15][MW - 1] = PATH
    coll = _coll_from(rows, solid_tiles=(WALL, PATH))
    for x in range(MW):
        coll[16][x] = 1                        # grass top row is the floor
    coll[14][MW - 1] = 0
    coll[15][MW - 1] = 0
    return {"name": "meadow", "scene_type": "platform", "map_w": MW,
            "map": rows, "collision": coll, "object": []}


SH_H = 32                                      # skyway: 32 rows = 256 px tall


def scene_skyway():
    rows = _grid(W, SH_H, FLOOR)
    for y in range(SH_H):                      # solid side walls (shmup collision)
        rows[y][0] = WALL
        rows[y][W - 1] = WALL
    for y, x in ((28, 6), (24, 13), (20, 4), (16, 10), (12, 15), (8, 7),
                 (5, 11), (26, 10), (18, 6), (10, 3)):
        rows[y][x] = GRASS                     # clouds to make the scroll visible
        rows[y][x + 1] = GRASS
    for x in range(8, 12):
        rows[1][x] = PATH                      # the exit pad at the top
    # On Init retimes the shmup auto-scroll via the shmup_scroll EVENT (the scroll
    # is decoupled from the player -- a scene drives its own scroll, like the title).
    return {"name": "skyway", "scene_type": "shmup", "map_h": SH_H,
            "on_init": "sky_init",
            "map": rows, "collision": _coll_from(rows), "object": []}


def main():
    make_tiles_png()
    world = {
        # stream + paint_table are what fit this "everything" showcase on the
        # tight consoles: `stream` moves the maps off the resident image (Lynx
        # cart / GB banks) and `paint_table` replaces the per-scene paint code
        # with ONE interpreter over concatenated maps (that CODE saving is what
        # brings the PC Engine back under its 32 KB cart).
        "world": {"module": "scenes", "map_w": W, "map_h": H, "vm": True,
                  "stream": True, "paint_table": True,
                  "start_scene": "title"},
        "tileset": {"png": "tiles.png"},
        "kinds": {"player": 0, "npc": 1},
        "scene": [scene_title(), scene_field(), scene_meadow(), scene_skyway()],
        "door": [
            # field east edge -> meadow west; meadow east -> skyway bottom;
            # skyway top pad -> back to the field.
            {"from": "field", "tx": 19, "ty": 9, "to": "meadow", "ex": 12, "ey": 96},
            {"from": "meadow", "tx": 31, "ty": 15, "to": "skyway", "ex": 76, "ey": 232},
            {"from": "skyway", "tx": 9, "ty": 1, "to": "field", "ex": 80, "ey": 72},
        ],
        # a scriptable on-enter TRIGGER: a ding as you approach the field door
        "trigger": [
            {"from": "field", "tx": 16, "ty": 8, "tw": 2, "th": 3,
             "on_enter": "ding"},
        ],
    }
    with open(os.path.join(PROJ, "world.toml"), "w", encoding="utf-8") as f:
        f.write(toml.dumps(world))

    # studio.toml: the PLAYER PHYSICS (data the generated rooms.mos bakes into
    # consts -- edited in the studio's Inspector, Player physics form) + the NPC
    # kind's DEFAULT behavior slots (prefab-lite: copied onto newly-placed NPCs).
    studio = {
        # the meadow's variable-height jump feel -- all player-MOVEMENT DATA. The shmup
        # BACKGROUND scroll is NOT here (decoupled): the skyway scrolls at the genre
        # default and its On Init retimes it with the shmup_scroll event (below).
        "player": {"air_jumps": 1,        # DOUBLE JUMP in the meadow
                   "jump": 4,
                   "jump_hold": 8,        # VARIABLE HEIGHT: hold A to jump higher
                   "coyote": 6,           # jump just after leaving a ledge
                   "jump_buffer": 6,      # a pre-landing press still fires
                   # main.lua momentum + wall play (opt-in, all decoupled):
                   "run_accel": 2,        # the run RAMPS to top speed (momentum)
                   "run_decel": 3,        # brakes to a stop
                   "air_control": 8,      # half mid-air steering
                   "wall_slide": 2,       # slide down a wall at 2 px/frame
                   "wall_jump_x": 3,      # wall jump kicks 3 px/frame away
                   "wall_jump_y": 5},     # ...and 5 up
        "kind_slots": {"npc": {"on_update": "wander",
                               "on_interact": "npc_talk"}},
        # Audio per console GROUP (mosaik_vm.generate_glue). The theme plays on
        # the GB family + SMS/GG; the Atari LYNX is "off" because this showcase
        # links EVERY scene handler, the whole VM8 core and the Suzy bkg engine,
        # and the music driver does not fit the console's single ~46.6 KB MAIN
        # on top of that (projects/vm-music is the Lynx audio proof). "off" also
        # keeps the driver out of the PC Engine build, which has no music
        # backend at all.
        "audio": {"gb": "vm", "smsgg": "vm", "lynx": "off"},
    }
    with open(os.path.join(PROJ, "studio.toml"), "w", encoding="utf-8") as f:
        f.write(toml.dumps(studio))

    scripts_dir = os.path.join(PROJ, "scripts")
    os.makedirs(scripts_dir, exist_ok=True)
    events = {
        "script": [
            {"name": "main", "events": [
                {"event": "music_song", "song": "theme"},
                {"event": "stop"},
            ]},
            # scene 0 (title) On Init: scroll the backdrop (the GENERIC, player-
            # decoupled scroll_bg -- a MENU scene, no player) then the modal menu.
            {"name": "title", "events": [
                {"event": "scroll_bg", "vx": 2, "vy": 0},   # slide the title backdrop right
                {"event": "lock"},
                {"event": "menu", "var": "choice", "row": 8,
                 "options": ["TOP-DOWN FIELD", "PLATFORM MEADOW",
                             "SHMUP SKYWAY"]},
                {"event": "unlock"},
                {"event": "if", "cond": "choice == 0",
                 "then": [{"event": "change_scene", "room": 1, "x": 80, "y": 72}],
                 "else": [
                     {"event": "if", "cond": "choice == 1",
                      "then": [{"event": "change_scene", "room": 2,
                                "x": 12, "y": 96}],
                      "else": [{"event": "change_scene", "room": 3,
                                "x": 76, "y": 232}]},
                 ]},
                {"event": "stop"},
            ]},
            # the skyway (shmup) scene's On Init: drive the auto-scroll via the
            # shmup_scroll EVENT (pace 2 = 30 px/s upward) -- background scroll is
            # event-driven, not a player property.
            {"name": "sky_init", "events": [
                {"event": "shmup_scroll", "pace": 2},
                {"event": "stop"},
            ]},
            # the field NPC's slots (SELF-bound by the entity registry)
            {"name": "wander", "loop": True, "events": [
                {"event": "actor_move_to", "actor": "self", "x": 120, "y": 40},
                {"event": "actor_move_to", "actor": "self", "x": 120, "y": 110},
                {"event": "actor_move_to", "actor": "self", "x": 40, "y": 110},
                {"event": "actor_move_to", "actor": "self", "x": 40, "y": 40},
            ]},
            {"name": "npc_talk", "events": [
                {"event": "lock"},
                {"event": "sound_sfx", "id": 4},
                {"event": "text",
                 "string": "WELCOME! THE DOORS\nLINK ALL 4 SCENE\nTYPES. TRY THEM!"},
                {"event": "unlock"},
                {"event": "stop"},
            ]},
            {"name": "ding", "events": [
                {"event": "sound_sfx", "id": 0},
                {"event": "stop"},
            ]},
        ],
    }
    with open(os.path.join(scripts_dir, "main.evt.toml"), "w",
              encoding="utf-8") as f:
        f.write(toml.dumps(events))

    # a small looping theme (pulse lead + noise ticks) the glue auto-wires
    def row(frames, note, drum):
        return [frames, note, 0, 0, drum, 0, 0]
    songs = {"song": {"theme": {
        "channels": ["pulse", "noise"],
        "rows": [
            row(12, 25, 30), row(12, 29, 0), row(12, 32, 30), row(12, 29, 0),
            row(12, 34, 30), row(12, 32, 0), row(12, 29, 30), row(12, 27, 0),
        ],
    }}}
    with open(os.path.join(scripts_dir, "songs.toml"), "w",
              encoding="utf-8") as f:
        f.write(toml.dumps(songs))

    # -- the generated modules (exactly what the studio does on save) --------
    import mosaik_scenes
    import mosaik_vm
    w, base = mosaik_scenes.load_world(os.path.join(PROJ, "world.toml"))
    src_dir = os.path.join(PROJ, "src")
    os.makedirs(src_dir, exist_ok=True)
    with open(os.path.join(src_dir, "scenes.mos"), "w", encoding="utf-8") as f:
        f.write(mosaik_scenes.transpile(w, base))
    prog = mosaik_vm.compile_path(scripts_dir)
    with open(os.path.join(src_dir, "scripts.mos"), "w", encoding="utf-8") as f:
        f.write(prog.to_scripts_mos())
    mosaik_vm.generate_songs(PROJ)
    mosaik_vm.generate_glue(PROJ)
    mosaik_vm.generate_rooms(PROJ)
    print("vm-showcase regenerated.")


if __name__ == "__main__":
    main()
