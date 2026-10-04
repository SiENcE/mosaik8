# Game-slice

A top-down action-adventure **vertical slice** built as a feasibility spike for a reference-engine-style
game-framework layer on top of mosaik8. The framework it realises is documented in
[`docs/game-framework.md`](../../docs/game-framework.md).

Status: **Phase 4** (combat). On top of Phases 1-3: a room-2 arena with a fixed
enemy pool, a sword swing (B), enemy HP, contact damage + i-frames, and death→
respawn. Builds on all nine consoles; combat verified on Game Boy. On the Lynx a
dense combat scene can drop a foreground sprite under the per-frame Suzy budget
(the graphics.bkg row-strip engine's load is the cost; real hardware fares better
than the stricter emulator core). (Phase 0 = scaffold;
1 = walking/collision/camera; 2 = rooms/transitions/worldmap; 3 = dialogue/items/
HUD.)

## What's here

- `assets/gen_sprites.py` — procedural asset generator (no source art). Writes:
  - `sprites.png` + `sprites.sprites.toml` — a named-sprite sheet
    (`player_down`/`up`/`side`, `npc`, `enemy`, `chest` = 16×16 metasprites +
    an 8×8 `heart`), 4 indexed colours (GB 2bpp), 25 tiles (under the Lynx
    32-tile sprite-table ceiling).
  - `tiles_data.txt` — the room tileset + room/worldmap data (historical; the
    maps are now authored as Layer-3 data — see below).
- `assets/gen_world.py` — generates the **Layer-3 world** from the room data:
  `tiles.png` (the 4 room tiles) + `world.toml` — the four 32×32 scenes
  (room0/room1/room2/worldmap), the **object placements** (player/npc/chest in
  room0, the enemy pool in room2) and the **room doors**. `world.toml` is the
  source of truth the IDE's Scene editor opens (paint tiles, drag objects, wire
  doors); `mosaik_scenes.py` transpiles it to `src/scenes.mos`:
  `python projects/game-slice/assets/gen_world.py` then
  `python mosaik_scenes.py projects/game-slice/world.toml -o projects/game-slice/src/scenes.mos`.
- `src/` — three modules (cross-file linked into one ROM):
  - `scenes.mos` — **generated** from `world.toml`: the room tileset, the four
    32×32 tilemaps + `map_tile`/`paint`, the object table (`OBJ_*`) and the door
    table (`DOOR_*`). (Replaced the old hand-written `mapdata.mos`; the game
    reads objects/doors from here instead of hard-coded constants.)
  - `world.mos` — pure, stateless engine helpers: tile collision, the camera
    clamp, proximity (all parameter-driven); reads the maps via `scenes`.
  - `game_slice.mos` — the stateful game (entry module): walking, the scene
    manager (3 rooms, hub = room 0 east→room1 / south→room2, fade-transition
    doors, START worldmap), A-button paged NPC dialogue, the chest→key pickup,
    and a sprite HUD (hearts + key). NPC/chest/enemy live in room 0 (combat =
    Phase 4).

Controls: D-pad walks; walk into a doorway to change rooms (room 0 south →
room 2 arena); **B swings the sword**; A talks to the NPC (when close) and
advances dialogue; walk into the chest to grab the key; START toggles the
worldmap.

In the test matrix: `python tests/run_all.py --samples` builds it for all nine,
and `python tests/verify_roms.py` runs the Game Boy behavioural check (scene
transition + arena spawn + sword clears the arena).

## Build & run

```bash
python projects/game-slice/assets/gen_sprites.py   # regenerate assets
python mosaik8.py build projects/game-slice         # all nine consoles -> build/

# Game Boy (PyBoy) -- inspect OAM/BG; Lynx/PCE (libretro harness)
python emu/libretro/run_lynx.py projects/game-slice/build/lynx/game-slice.lnx 400 --png out.png
python emu/libretro/run_lynx.py projects/game-slice/build/pce/game-slice.pce 200 --core mednafen_pce_fast --png out.png
```

## Phase 0 result (the headline)

Builds on all nine. Game Boy + PC Engine render the room **and** the sprites.
**On the Lynx the room background renders but the sprites do not** — `graphics.bkg`
and the sprite engine don't coexist there (they share the Suzy blit pipeline +
RAM). Picking the Lynx room-render strategy is the first design decision the slice
forces; see the plan's Findings section.
