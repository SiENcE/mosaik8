# MosaiK8 game framework

A GB-Studio-style game-authoring layer for mosaik: scenes, actors, collision, a
follow camera, dialogue, items and a HUD — one source, all nine consoles. This
document is the **architecture + the per-console rulebook + the how-to for adding
a genre**; the working **reference implementation** is `projects/zelda-slice`
(built across phases in `docs/done/zelda-slice-plan.md`). Tags: ✅ shipped /
🔭 planned.

> The framework sits **on top of** the mosaik language and the MosaiK8 build
> tool. For toolchain setup and the `build` CLI see the
> [README](../README.md); for the language itself (syntax, types, modules, the
> stdlib, the per-console support matrix) see
> [`mosaik_lang_spec.md`](mosaik_lang_spec.md). This doc assumes both.

## What it is (and what it is *not*)

GB Studio is a fixed C/VM runtime driven by declarative data + visual scripting.
mosaik is a *compiler*, so the framework is **not** a bytecode VM and **not**
compiler-builtin stdlib. It is:

- a set of **reusable mosaik patterns/modules** layered on the existing stdlib
  (`graphics.*`, `platform.*`) + cross-file module linking, and
- a **reference game** (`projects/zelda-slice`) that realises them on all nine
  consoles, with the per-console gotchas worked out.

The decision not to make `game.*` compiler-builtin is deliberate: mosaik is
whole-program / one-TU with **private module vars** and **by-value params**, so
stateful systems can't cleanly live behind a generic stdlib boundary (see
"Modularity" below). The framework is therefore *source you compose*, not a
runtime you link.

## The three layers

- **Layer 1 — game systems (✅, realised in the slice):** scene manager,
  game-state, tile collision, follow camera, dialogue box, item/inventory, HUD,
  and an actor pool. These exist as mosaik code in `projects/zelda-slice/src/`.
- **Layer 2 — genre loops (🔭):** `game.topdown` / `platform` / `shmup` — the
  standard input→intent→move→collide→camera loop for a genre, so a game writes
  only the differences. The slice's main loop *is* the top-down loop, ready to
  generalise (see "Layer 2" below).
- **Layer 3 — declarative scene format (🔭):** a `.gbsres`-equivalent
  (split-per-resource TOML/JSON) transpiled to mosaik. The slice's hand-written
  rooms + the `do_transition` edge table are exactly the data such a format must
  round-trip (see "Layer 3" below).

## Modularity: what splits cleanly, what doesn't

mosaik's constraints decide the module boundaries (learned splitting the slice
into `mapdata` / `world` / `zelda_slice`):

- **Pure, stateless helpers modularise cleanly** — `world.mos` (tile collision,
  camera clamp, proximity) takes everything by parameter and is genuinely
  reusable/copy-pasteable across games. Cross-module `const`-array indexing works
  (`mapdata.ROOM0_MAP[idx]`), so **data also modularises** (`mapdata.mos`).
- **Stateful systems can live behind an owner module — exported state is
  shared.** An exported module `var`/array compiles to a plain global that other
  modules read *and write* (`camera.camx = …` → `camera_camx = …`;
  `actors.x[i] = …` → `actors_x[i] = …`), so a system *can* own its state and let
  the game touch it directly — state does **not** have to be threaded through
  calls. (An earlier note here said module vars are private and shared state is
  impossible; that was wrong — only *non-exported* vars are private.) The
  by-value limit only bites when you *pass* an array/struct as a parameter.
  **Rule of thumb:** singleton system state (camera, scene, dialogue) → an owner
  module with exported state + verbs; multi-entity hot state (the actor pool) →
  an owner module with exported struct-of-arrays the game's AI indexes directly;
  game-unique state → the game module. The *behaviour* still lives in the game
  (no callbacks), so AI/intent are written inline, not injected.
- **Arrays can't be passed by value**, so a helper that needs a tilemap takes a
  *room id* and selects the map itself (`world.tile_at(room, …)`) or reads it
  from an exported global — it can't receive the map as an argument.
- **No callbacks and no bitwise operators.** The framework is *called*, never
  *calls* (control stays in the game's loop), and input is handled per-button
  (no pad-word masking).

## The Layer-1 systems (where each lives in the slice)

| System | What it does | In the slice |
|---|---|---|
| **scene** | room ids, `load_map(id)`, `change_room` (fade→load→reposition), worldmap toggle, door→scene transitions | `zelda_slice.mos` |
| **state** | global + per-scene vars, inventory flags, HP | `zelda_slice.mos` |
| **collision** | 16×16 box vs the tilemap (4 corners), axis-separated for wall-sliding | `world.mos` (`box_solid`/`tile_at`) |
| **camera** | centre on the player, clamp to the room, scroll the bkg | `world.mos` (`cam_for`) |
| **dialog** | paged text box, A advances; per-backend text (below) | `zelda_slice.mos` |
| **items/HUD** | chest→key pickup; hearts + key icon as **sprites** (portable, no window/text layer) | `zelda_slice.mos` |
| **actor** | fixed enemy pool (struct-of-arrays), sword hitbox, HP/stun/contact-damage/death | `zelda_slice.mos` |

A transition fade and the HUD are deliberately **portable by construction**: the
fade is a `palette` ramp to black (works on colour consoles + DMG), and the HUD
is fixed-screen **sprites** (no window layer needed — works even on the Lynx).

## Per-console rulebook (the hard-won gotchas)

These are the cross-console rules every game on this framework must respect.
Fuller context: project memory + `docs/done/zelda-slice-plan.md` Findings.

- **Input has no edge detection.** `input.pressed` == `input.held` (both read the
  current pad). "One press = one action" needs manual prev-frame tracking
  (`prev_a`/`a_edge`). Candidate stdlib fix or a future `game.input`.
- **Avoid C/cc65 keyword identifiers.** Single-module names pass through to C
  unmangled, so e.g. a function named `near` builds on GBDK (sdcc) but breaks the
  cc65 (Lynx/PCE) build. Avoid `near`/`far`/`fastcall`/…
- **SMS / Game Gear have no hardware sprite flip** (`PLATFORM_CAPS`
  `has_sprite_flip` false). The metasprite engine therefore leaves the block
  **unflipped** on those consoles (it used to reverse the cell layout without
  mirroring the tiles → garbled); `FLIP_X`/`FLIP_Y` are honest no-ops there. To
  actually face both ways, use **dedicated/pre-mirrored frames** (the slice
  ships `player_left`) — portable on every console.
- **`graphics.text` = GBDK `printf`, too big for the NES.** It overflows NROM
  into an unbootable 128 KB banked ROM. The codegen now emits the text helpers
  only when text is *actually used* (`text_used`), so gate text off on the NES
  (conditional-compile dialogue to no-ops) and it links no `printf`. NES = no
  on-screen text unless a `printf`-free text path is added.
- **Text coordinates are per-backend.** GBDK writes to the *scrolling* BG map
  (add the camera tile offset); PCE conio + Lynx tgi use fixed *screen* cells.
  Select with `if platform == …`.
- **Lynx text must be drawn *after* the present.** The Lynx sprite/bkg engine
  repaints the frame each `present`, so dialogue text is drawn after `wait_vblank`
  to overlay the (frozen, single-buffered) room — the "freeze + overlay" idea.
- **Lynx bkg + sprites: the Suzy budget (reworked twice).** The original Lynx
  bkg engine composited the whole 32×32 map into one 256×256 sprite and re-blit
  it with up to four wrapped offsets — most of the 60 Hz budget. The second
  engine composited only the **visible window** into one small sprite, re-blit
  once per frame, recompositing from the logical tile map **only when the camera
  crosses a tile boundary**. That single windowed blit kept foreground sprites
  rock-stable, but the full-window recomposite landed on one frame every 8 px of
  scroll and **stuttered** on real hardware. The current engine (SPRDEMO4 idiom)
  draws the map as a **vertical ring of screen-spanning (~416 px) row strips** —
  one Suzy sprite per visible map row. Horizontal scroll is pure SCB position
  (the wide strip needs no wrap copy); vertical scroll recomposites one strip per
  tile crossing, composited a few columns per frame while the entering row is
  off-screen, so neither axis pays a per-tile recomposite spike. The present
  stays **double-buffered** (every frame repaints an off-screen page and flips,
  so static-camera sprites never tear). **Tradeoff:** 16 wide strips ≈ 53 k
  px/frame; real hardware is stutter-free on both axes, but the heavier load can
  intermittently flicker a foreground sprite on the stricter **Beetle/Mednafen
  Lynx core** under the harness (a core artifact, not a real-HW bug — use Handy as
  the proxy). See the rework note below.

## Layer 2 — genre loops (✅ top-down kit shipped; other genres 🔭)

The slice's main loop is the top-down body: per frame — read input, edge-detect
buttons, axis-separated walk-with-collision + facing, camera follow, scene/door
handling, actor update, draw, present, post-present dialogue overlay. This is now
realised as **composable source modules** in [`lib/game/`](../lib/game) (used via
the shared `lib/` search path, or vendored to override; the game owns the loop
and *calls into* them — there are no callbacks):

- **Tier-A engine** (genre-agnostic): `game.pad` (per-button input edge
  detection — both the action buttons *and* the d-pad, for one-step-per-tap grid
  movement), `game.camera` (follow camera with exported, shared `camx`/`camy`),
  `game.collision` (the pure box-corner test), plus the owner modules
  `game.scene` (current scene id + worldmap/overlay bookkeeping), `game.dialogue`
  (the paged-box state machine + the per-backend text-cell coordinates — the
  GBDK scrolling-map offset gotcha, encapsulated, and no `printf` linked so the
  NES stays clean) and `game.hud` (sprite hearts/icon verbs). The game keeps the
  loop, the map upload, the transition sequencing and the dialogue strings; the
  modules own the reusable state + verbs.
- **Tier-B top-down kit**: `game.topdown` (`facing4` / `anim2` / `toward` + the
  `FACE_*` constants) and `lib/game/topdown_template.mos` — the canonical,
  copy-me loop skeleton with the **invariant body** in a fixed order and
  `FILL IN` markers for what the game supplies:
  - the input→intent mapping (which buttons do what),
  - the per-actor update (enemy AI, NPC behaviour),
  - the scene table (maps + object lists + door edges, → Layer 3).

A second game proves it generalises: **`projects/box-pusher`** (a Sokoban-style
grid puzzle, deliberately unlike the slice) composes `lib/game/` (via the shared
search path) **à la carte** — a grid genre uses `game.pad` + `game.camera` and
legitimately *not* `collision`/`topdown` (it checks cell types and moves a whole
cell at a time). Building it is what *added* the d-pad edges to `game.pad`: a new
genre extended the framework without touching the others.

A second genre proves the extensibility: **`projects/platformer`** (a
side-scrolling platformer) reuses every Tier-A module *unchanged* (`game.pad`
for the jump edge, `game.camera`, `game.collision`) and adds only a tiny new
Tier-B kit, **`game.platformer`** (`fall(vy, grav, maxfall)` — gravity
integration, signed `i8` velocity). A new genre is a new kit module + the genre
loop; the shared engine is untouched. A future `game.shmup` (auto-scroll + fire)
follows the same recipe — the **[Adding a genre](#adding-a-genre)** section below
walks through it step by step.

## Layer 3 — declarative scene format (✅ transpiler shipped)

Realised as **`mosaik_scenes.py`**, a world-TOML → mosaik transpiler (the
GB-Studio `.gbsres` analogue) alongside `mosaik_assets.py`, reusing its
PNG→tiles pipeline for the tileset. The format expresses exactly what the slice
pinned:

- a **scene**: a 32×32 tilemap (≤256×256 px for seamless u8 scroll; larger needs
  map streaming, still 🔭) + a shared tileset (a PNG),
- **per-scene object placements** (`kind` + position; `kind` names map to id
  constants `KIND_*`),
- **door edges**: `(from-scene, trigger cell) → (to-scene, entry pixel)`.

It emits a `scenes` module: the tileset + each scene's map as `const` arrays,
`map_tile(scene, idx)` (picks the map by id — arrays can't be passed by value)
and `paint(scene)` (uploads a room's map), plus the object and door tables. The
game `import`s it and composes the framework around the data — editing the world
needs **no code change**, just re-running the transpiler. Worked example:
**`projects/scene-demo`** (a two-room world joined by doors, generated by
`assets/gen_world.py` → `world.toml` → `scenes.mos`); transpiler tested in
`tests/scene_transpile_test.py`. The transpiler accepts **either** a single
`world.toml` **or** a split-per-resource world **directory** (`world.toml`
header + `scenes/<name>.toml` per scene + optional `doors.toml`, the `.gbsres`
analogue) — both assemble to the same world and emit a byte-identical module.

## Lynx bkg rework — shipped ✅ (row-strip ring; supersedes the windowed composite)

The Lynx bkg engine (`_emit_cc65_bkg_engine` in `mosaik/codegen/cc65.py`) has had
three forms. **(1) Full-map composite:** the whole 32×32 map blit as one 256×256
sprite with up to four wrapped offsets — the blit dominated the 60 Hz budget.
**(2) Windowed composite:** only the **visible window** (screen + 1 tile margin)
composited into one small sprite, re-blit each frame and recomposited from the
logical map (`gbs_bkg_compose`) **only when the camera crossed a tile boundary**.
That single windowed blit kept foreground sprites stable, but the full-window tile
copy (`win_w*win_h` ≈ 294 cells, ~a frame's work on the 65C02) landed on one
frame every 8 px of scroll, so *continuous* scrolling **stuttered** (most visible
in `projects/background`, which scrolls 1 px/frame whenever a D-pad is held).

**(3) Row-strip ring (current).** The engine keeps the GB background *model* (a
256-tile table + a **logical 32×32 tile-index map** `gbs_bkg_map`, u8 scroll wrap
mod 256) but draws the map the SPRDEMO4 way — a **vertical ring of screen-spanning
literal row strips**, one `TYPE_BACKNONCOLL` Suzy sprite per visible map row
(`gbs_bkg_strip[GBS_BKG_STRIPS]`), positioned independently each frame. Scrolling
*moves SCBs*, it never re-lays-out pixels:

- **Horizontal scroll is pure SCB position** (`hpos = -x`), with **no wrap copy**.
  Each strip is `GBS_BKG_STRIP_W` tiles wide = the whole 256-px scroll period +
  the screen (52 tiles on the Lynx), map columns repeating (`col c & 31`), so a
  *single* strip placed at `-x` covers the screen at any scroll 0..255 — crossing
  a vertical tile boundary needs no recomposite and no second sprite. (An earlier
  256-px-strip version needed a wrap copy per row at `hpos+256` to cover the seam,
  which doubled the per-frame SCB count to ~32 during horizontal scroll and
  **flickered foreground sprites on real hardware**; the wide strip keeps it at
  one SCB per row, the same count as the smooth vertical case.)
- **Vertical scroll recomposites one slot per tile cross** (the row newly
  entering at the bottom). A row stays in the same ring slot for its whole visible
  life and is composited *once* on entry — and it **enters off-screen, ~16 frames
  before it scrolls into view**, so the engine composites it **incrementally**,
  `GBS_BKG_AMORT` columns/frame (`gbs_bkg_compose_cols` + `gbs_bkg_strip_col[]`),
  rather than all at once. So no single frame pays the whole strip — the vertical
  per-tile recomposite *spike* (which stuttered when strips first went wide) is
  amortized away. A draw-time full-compose fallback keeps it correct if a fast
  scroll/first load outruns the amortizer. `GBS_BKG_STRIPS` is a power of two
  dividing the 32-row map (16 on the Lynx), so the ring (slot `= (ty0+i) %
  STRIPS`, `vpos = i*8 - frac`) rotates +1 per tile step even across the wrap.

So both the horizontal wrap-copy flicker and the vertical recomposite spike are
gone, and both axes draw at 16 SCBs/frame. The present stays **double-buffered**
(`tgi_setdrawpage(1)` + `tgi_updatedisplay()` each frame), so every shown frame is
complete and **static-camera sprites stay put** (`colorlab` gems, the zelda-slice
HUD) — the property the windowed rework restored and this one keeps.

### Tradeoff: per-frame pixel load vs the Beetle core (pinned)

To make horizontal scroll wrap-copy-free, each strip is ~416 px wide (52 tiles),
so the ring blits **16 SCBs × ~416 px ≈ 53 k px/frame** (vs the windowed engine's
single ~screen-sized blit). On **real hardware** this is stutter-free on *both*
axes (verified: horizontal smooth, and the amortized vertical recomposite removes
the vertical stutter). The heavier per-frame Suzy load can **intermittently
flicker a foreground sprite on the stricter Beetle/Mednafen Lynx core** under the
libretro harness — a core artifact (same family as the ~16–32 Suzy per-frame
sprite ceiling that caps `endless-runner` on Beetle), **not** a real-HW bug; use
the **Handy** core as the HW proxy. The libretro harness also can't *see* the
stutter the rework fixes (it runs every frame to completion, no real-time clock),
so the stutter win is confirmed by
the cost model + hardware while the harness confirms "renders + scrolls
correctly." If the Beetle pixel load matters more than the stutter for a given
program, the windowed composite (git history) is the 1-blit alternative.

## Building a game on the framework

1. Author assets with a project `gen_sprites.py` (named-sprite sheet + tilemaps),
   as in `projects/zelda-slice/assets/`.
2. Pull the framework into your project — two ways, pick one:
   - **Shared `lib/` search path (no copying).** `import "game.camera"` resolves
     to `lib/game/camera.mos` automatically (the importer's own folder is
     searched first, then `[lib] paths` from your `mosaik.toml`, the `MOSAIK_LIB`
     env var, and the default `lib/` next to the tool). Tree-shaking pulls in
     only the modules you import.
   - **Vendor the modules.** Copy the ones you use into your `[source]` folder
     (project mode compiles everything under it). A vendored copy **wins** over
     the lib root (see `projects/vendor-override`).
3. Write your game module with the Layer-1 systems you need (the slice's
   `world.mos` pure helpers + the `mapdata` pattern are a good starting point).
4. Respect the per-console rulebook above (it is the difference between "builds"
   and "runs on all nine").
5. Add the project to `tests/run_all.py` `PROJECT_DIRS` and a behavioural check
   to `tests/verify_roms.py` (the slice has a Game Boy one).

## Adding a genre

A practical, step-by-step recipe for extending the framework with a new genre
(top-down, platformer, shmup, puzzle, …). The architecture and the constraint
list are above; this is the *recipe*.

### The mental model (one rule)

> **Your game owns the loop and *calls into* the framework. The framework never
> calls your code.**

mosaik has **no callbacks / function pointers**, so there is no "engine" that
runs your game and invokes your `update_enemy()`. Instead you compose modules:

| Tier | What | Example |
|---|---|---|
| **A. Engine** (genre-agnostic) | reused by every genre | `game.pad`, `game.camera`, `game.collision` |
| **B. Genre kit** | the *pure* mechanics of one genre + a loop pattern | `game.topdown`, `game.platformer` |
| **C. Your game** | owns `main()` + the loop, imports A + B, supplies behaviour inline | `projects/platformer/src/main.mos` |

A genre is added as a **Tier-B module + the genre loop**. You touch *nothing* in
Tier A or other genres.

### What goes where

- **Pure, stateless mechanics → the Tier-B kit.** Anything that takes everything
  by parameter and returns a value: gravity (`game.platformer.fall`), facing
  (`game.topdown.facing4`), a chase step (`game.topdown.toward`). These are the
  only things that modularise cleanly.
- **The loop and all behaviour → your game module.** AI, the input→intent
  mapping, interactions, and the move-with-collision loop *stay in the game* —
  because they call back and forth with game state and sample the game's own
  tilemap (which can't be passed by value). Write them inline.
- **Shared state → a Tier-A owner module.** Exported module vars compile to
  shared globals (read *and* write across modules), so a singleton like the
  camera owns `camx`/`camy` and the game reads them directly.

### The constraints that shape a genre kit

(Verified against the compiler — design around these.)

1. **No callbacks** → the kit exposes verbs the game calls; it can't drive the game.
2. **No generics** → a kit's data shapes/sizes are fixed; multi-entity state
   (an actor pool) lives in the game as struct-of-arrays.
3. **No array-by-value params** → helpers take an *id* and select internally, or
   read an exported global; they can't receive a map/array as an argument.
4. **No bitwise operators** (`& | << >>`) → handle input per-button (see
   `game.pad`), not by masking a pad word.
5. **Newline-terminated parser** → a call's arguments must stay on one line.
6. **Signed math is `i8`/`i16`** → fine for velocities (see `game.platformer`),
   but mixing with `u8` positions needs care (resolve motion a pixel at a time).

### Recipe (worked example — how the platformer was added)

#### 1. Write the Tier-B kit module — `lib/game/<genre>.mos`

Put *only* the genre's pure math here. Keep it tiny; most of a genre is the loop,
which lives in the game.

```mosaik
-- game.platformer: gravity integration (the genre's reusable physics).
module "game.platformer" {
    -- vy += grav, clamped to terminal speed. Signed i8: up negative, down positive.
    function fall(vy: i8, grav: i8, maxfall: i8) -> i8 {
        var v: i8 = vy + grav
        if v > maxfall { return maxfall }
        return v
    }
    export fall
}
```

Module-naming rules: the module name's **last dotted segment becomes the alias**
(`game.platformer` → `platformer.fall`). It **must not** collide with a stdlib
alias (`video`, `input`, `sprite`, `bkg`, `window`, `text`, `draw`, `palette`,
`sound`, `hw`, `system`, `hardware`, `lynx`) or another module.

#### 2. Write the genre loop in your game module

The invariant body, in a fixed order, calling Tier-A + your kit. Supply the
behaviour inline. From `projects/platformer/src/main.mos`:

```mosaik
loop {
    pad.update()                                   -- 1. latch input edges

    -- 2. intent -> horizontal move with wall collision (level-triggered)
    if input.held(INPUT_LEFT)  { if not solid(px - SPD, py) { px -= SPD } }
    else if input.held(INPUT_RIGHT) { if not solid(px + SPD, py) { px += SPD } }

    -- 3. genre action: jump on the A/UP EDGE, only when grounded (no air-jump)
    if grounded == 1 and (pad.a() or pad.up()) { vy = JUMP_VY  grounded = 0 }

    -- 4. genre physics (the kit) + per-pixel vertical resolve (lands exactly)
    vy = platformer.fall(vy, GRAV, MAXFALL)
    grounded = 0
    if vy > 0 { ... move down 1px at a time; on a floor hit vy = 0, grounded = 1 ... }
    else if vy < 0 { ... move up; on a ceiling hit vy = 0 ... }

    -- 5. camera follow, then place the sprite in screen space
    camera.follow(px + 8, py + 8, HALFW, HALFH, MAXCAMX, MAXCAMY)
    sprite.move(0, px - camera.camx, py - camera.camy)

    video.wait_vblank()                            -- 6. present (paces the frame)
}
```

`solid(bx, by)` is the game's vendored tile sampler feeding `game.collision`:

```mosaik
function solid(bx: u8, by: u8) -> bool {
    return collision.any_solid(tile_at(bx + 1, by + 1), tile_at(bx + 14, by + 1),
                               tile_at(bx + 1, by + 14), tile_at(bx + 14, by + 14), SOLID)
}
```

`tile_at` reads either a procedural level (the platformer) or the generated
`scenes` module (`projects/scene-demo`, from a `world.toml` via
`mosaik_scenes.py` — the Layer-3 data format).

#### 3. Pull the framework into your project

Either the **shared `lib/` search path** (`import "game.pad"` resolves to
`lib/game/pad.mos` automatically; tree-shaking pulls in only what you import) or
**vendor** the modules into your `[source]` folder (a vendored copy wins over the
lib root). See "Building a game on the framework" above.

#### 4. Respect the per-console rulebook

The framework encapsulates most gotchas, but a new genre must still honour them
(full list in the "Per-console rulebook" section above):

- **Input has no edge** → use `game.pad` (edges) for discrete actions; `input.held`
  for continuous movement.
- **SMS / Game Gear have no hardware sprite flip** → use dedicated/pre-mirrored
  frames per facing, not `FLIP_X`.
- **`graphics.text` = GBDK `printf`, too big for the NES** → conditional-compile
  text off on the NES, or use sprite HUDs.
- **Lynx text after `present`**; **Lynx bkg + many sprites** mind the Suzy budget.

#### 5. Test it

- Add the project to `tests/run_all.py` `PROJECT_DIRS` (builds it on all nine
  consoles in the matrix).
- Add a behavioural check to `tests/verify_roms.py` (PyBoy for the GB family; the
  libretro harness for Lynx/PCE/SMS/GG/NES — see the
  [language spec](mosaik_lang_spec.md#56-testing-roms)).
- If you vendored, add the copies to the sync map in
  `tests/game_framework_test.py` so they can't drift from `lib/game/`.
- If your kit has pure helpers worth asserting, add a compile/lowering check
  there too.

That's the whole loop: **new kit module + genre loop in the game + import (lib
path or vendored) + tests.** Tier A and the other genres never change — that is
the design working.
