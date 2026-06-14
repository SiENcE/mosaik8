# Zelda-slice

A Zelda-like **vertical slice** built as a feasibility spike for a GB-Studio-style
game-framework layer on top of mosaik8. Full plan + living findings:
[`docs/zelda-slice-plan.md`](../../docs/zelda-slice-plan.md).

Status: **Phase 4** (combat). On top of Phases 1-3: a room-2 arena with a fixed
enemy pool, a sword swing (B), enemy HP, contact damage + i-frames, and death→
respawn. Builds on all nine consoles; combat verified on Game Boy. The Lynx
renders the arena but drops the HUD under its per-frame Suzy budget (the bkg
composite is the cost — see the plan's Phase 4 findings). (Phase 0 = scaffold;
1 = walking/collision/camera; 2 = rooms/transitions/worldmap; 3 = dialogue/items/
HUD.)

## What's here

- `assets/gen_sprites.py` — procedural asset generator (no source art). Writes:
  - `sprites.png` + `sprites.sprites.json` — a named-sprite sheet
    (`player_down`/`up`/`side`, `npc`, `enemy`, `chest` = 16×16 metasprites +
    an 8×8 `heart`), 4 indexed colours (GB 2bpp), 25 tiles (under the Lynx
    32-tile sprite-table ceiling).
  - `tiles_data.txt` — the room tileset (floor/wall/door/decoration) + three
    32×32 rooms and a worldmap as mosaik `const` arrays to paste into the `.mos`.
- `src/` — three modules (cross-file linked into one ROM):
  - `mapdata.mos` — the shared room tileset + the four 32×32 tilemaps.
  - `world.mos` — pure, stateless engine helpers: tile collision, the camera
    clamp, proximity (all parameter-driven).
  - `zelda_slice.mos` — the stateful game (entry module): walking, the scene
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
python projects/zelda-slice/assets/gen_sprites.py   # regenerate assets
python mosaik8.py build projects/zelda-slice         # all nine consoles -> build/

# Game Boy (PyBoy) -- inspect OAM/BG; Lynx/PCE (libretro harness)
python emu/libretro/run_lynx.py projects/zelda-slice/build/lynx/zelda-slice.lnx 400 --png out.png
python emu/libretro/run_lynx.py projects/zelda-slice/build/pce/zelda-slice.pce 200 --core mednafen_pce_fast --png out.png
```

## Phase 0 result (the headline)

Builds on all nine. Game Boy + PC Engine render the room **and** the sprites.
**On the Lynx the room background renders but the sprites do not** — `graphics.bkg`
and the sprite engine don't coexist there (they share the Suzy blit pipeline +
RAM). Picking the Lynx room-render strategy is the first design decision the slice
forces; see the plan's Findings section.
