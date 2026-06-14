# Scene-Demo — the game-framework Layer-3 (declarative scene format) showcase

A multi-room world whose **maps, door edges and object placements are data**, not
code: `world.toml` is transpiled to a mosaik `scenes` module by
`mosaik_scenes.py` — the GB-Studio `.gbsres` analogue from
[`docs/game-framework-plan.md`](../../docs/game-framework-plan.md) (Phase 4). One
source, all nine consoles.

Two rooms (a field and a cave) joined by a door each way. Walk onto a doorway and
you cross to the other room; the room's whole tilemap is repainted from the
generated data. Editing the world — new rooms, new doors, moved objects — needs
**no code change**, just editing `world.toml` (or its generator) and re-running
the transpiler.

## The pipeline

```
assets/gen_world.py   ->  world.toml      (the declarative format: tileset + scenes + doors + objects)
mosaik_scenes.py      ->  src/scenes.mos  (const maps + a map_tile/paint selector + object/door tables)
mosaik8.py build      ->  ROMs            (the game composes the framework around the generated data)
```

Regenerate:

```
python projects/scene-demo/assets/gen_world.py
python mosaik_scenes.py projects/scene-demo/world.toml -o projects/scene-demo/src/scenes.mos
python mosaik8.py build projects/scene-demo
```

## How it composes the framework

`src/main.mos` owns the loop and *calls into* the generated `scenes` module +
the vendored engine modules (no callbacks):

- **`scenes`** (generated) — `scenes.map_tile(room, idx)` for collision lookups,
  `scenes.paint(room)` to upload a room's map, and the `DOOR_*` / `OBJ_*` /
  `KIND_*` tables for transitions and object placement.
- **`game.camera`** — the follow camera (vendored in `src/game/`).
- **`game.collision`** — the box-vs-walls corner test (vendored).

The door table is `(from-scene, trigger cell) -> (to-scene, entry pixel)`. One
gotcha pinned in `gen_world.py`: the trigger is the player's **centre** cell, and
the 16px top-left-origin player presses against the bottom edge with its centre
on row 31 but the top edge with its centre on row 1 — so top-edge doors trigger
on row 1.

## Controls

D-pad walks; step onto a doorway (the grey tile in a wall) to change rooms.
