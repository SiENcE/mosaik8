# MosaiK8 game framework

A game-authoring layer for mosaik: scenes, actors, collision, a follow camera,
dialogue, items and a HUD - one source, all nine consoles.
This document is the **architecture + the per-console rulebook + the how-to for adding
a genre**; the working **reference implementation** is `projects/game-slice`.

> The framework sits **on top of** the mosaik language and the MosaiK8 build
> tool. For toolchain setup and the `build` CLI see the
> [README](../README.md); for the language itself (syntax, types, modules, the
> stdlib, the per-console support matrix) see
> [`mosaik_lang_spec.md`](mosaik_lang_spec.md). This doc assumes both.

## What it is (and what it is *not*)

Mosaik is a *compiler*, so the framework is **not** a bytecode VM and **not**
compiler-builtin stdlib. (The repo's bytecode-VM path exists *separately*: the
**VM8** virtual machine in `lib/vm/` runs game logic authored as event-script
DATA - see [`vm8-spec.md`](vm8-spec.md) and the `projects/vm-*` samples. This
document is about the hand-written, native-code framework.) It is:

- a set of **reusable mosaik patterns/modules** layered on the existing stdlib
  (`graphics.*`, `platform.*`) + cross-file module linking, and
- a **reference game** (`projects/game-slice`) that realises them on all nine
  consoles, with the per-console gotchas worked out.

The decision not to make `game.*` compiler-builtin is deliberate: mosaik is
whole-program / one-TU with **private module vars** and **by-value params**, so
stateful systems can't cleanly live behind a generic stdlib boundary (see
"Modularity" below). The framework is therefore *source you compose*, not a
runtime you link.

## The three layers

- **Layer 1 - game systems:** scene manager,
  game-state, tile collision, follow camera, dialogue box, item/inventory, HUD,
  and an actor pool. These exist as mosaik code in `projects/game-slice/src/`.
- **Layer 2 - genre loops:** `genre.topdown` / `platform` / `shmup` - the
  standard input→intent→move→collide→camera loop for a genre, so a game writes
  only the differences. The slice's main loop *is* the top-down loop, ready to
  generalise (see "Layer 2" below).
- **Layer 3 - declarative scene format:** a `.gbsres`-equivalent
  (split-per-resource TOML/JSON) transpiled to mosaik. The slice's hand-written
  rooms + the `do_transition` edge table are exactly the data such a format must
  round-trip (see "Layer 3" below).

## Modularity: what splits cleanly, what doesn't

mosaik's constraints decide the module boundaries (learned splitting the slice
into `scenes` / `world` / `game_slice`):

- **Pure, stateless helpers modularise cleanly** - `world.mos` (tile collision,
  camera clamp, proximity) takes everything by parameter and is genuinely
  reusable/copy-pasteable across games. Cross-module `const`-array indexing works
  (`scenes.ROOM0_MAP[idx]`), so **data also modularises** (`scenes.mos`, which
  is GENERATED from `world.toml` by the Layer-3 transpiler; the slice's own
  `assets/gen_world.py` writes that TOML).
- **Stateful systems can live behind an owner module - exported state is
  shared.** An exported module `var`/array compiles to a plain global that other
  modules read *and write* (`camera.camx = …` → `camera_camx = …`;
  `actors.x[i] = …` → `actors_x[i] = …`), so a system *can* own its state and let
  the game touch it directly - state does **not** have to be threaded through
  calls. (An earlier note here said module vars are private and shared state is
  impossible; that was wrong - only *non-exported* vars are private.) The
  by-value limit only bites when you *pass* an array/struct as a parameter.
  **Rule of thumb:** singleton system state (camera, scene, dialogue) → an owner
  module with exported state + verbs; multi-entity hot state (the actor pool) →
  an owner module with exported struct-of-arrays the game's AI indexes directly;
  game-unique state → the game module. The AI/intent *behaviour* lives in the
  game, written inline in the loop, not injected.
- **Arrays can't be passed by value**, so a helper that needs a tilemap takes a
  *room id* and selects the map itself (`world.tile_at(room, …)`) or reads it
  from an exported global - it can't receive the map as an argument.
- **The framework is mostly *called*, not *calling*** (control stays in the
  game's loop), and input is handled per-button (the stdlib exposes no raw
  pad-word read; bitwise operators exist since 2026-07). The exceptions are
  `engine.anim`, which *calls back* into a game-supplied animation handler via
  mosaik's first-class function pointers (see
  [Animation](#animation--gameanim-callback-driven)), and the three background
  streamers (`engine.scroll` / `scroll2d` / `scrollpx`), which call a
  game-supplied `gather` / `tile_at` to read the wide map.

## The Layer-1 systems (where each lives in the slice)

| System | What it does | In the slice |
|---|---|---|
| **scene** | room ids, `load_map(id)`, `change_room` (fade→load→reposition), worldmap toggle, door→scene transitions | `game_slice.mos` |
| **state** | global + per-scene vars, inventory flags, HP | `game_slice.mos` |
| **collision** | 16×16 box vs the tilemap (4 corners), axis-separated for wall-sliding | `world.mos` (`box_solid`/`tile_at`) |
| **camera** | centre on the player, clamp to the room, scroll the bkg | `world.mos` (`cam_for`) |
| **dialog** | paged text box, A advances; per-backend text (below) | `game_slice.mos` |
| **items/HUD** | chest→key pickup; hearts + key icon as **sprites** (portable, no window/text layer) | `game_slice.mos` |
| **actor** | fixed enemy pool (struct-of-arrays), sword hitbox, HP/stun/contact-damage/death | `game_slice.mos` |

A transition fade and the HUD are deliberately **portable by construction**: the
fade is a `palette` ramp to black (works on colour consoles + DMG), and the HUD
is fixed-screen **sprites** (no window layer needed - works even on the Lynx).

## Per-console rulebook (the hard-won gotchas)

These are the cross-console rules every game on this framework must respect:

- **Input has no edge detection.** `input.pressed` == `input.held` (both read the
  current pad). "One press = one action" needs manual prev-frame tracking
  (`prev_a`/`a_edge`). Candidate stdlib fix or a future `game.input`.
- **Avoid C/cc65 keyword identifiers.** Single-module names pass through to C
  unmangled, so e.g. a function named `near` builds on GBDK (sdcc) but breaks the
  cc65 (Lynx/PCE) build. Avoid `near`/`far`/`fastcall`/…
- **Colour degrades per console, and never errors.** Palette slot counts,
  per-tile background palettes and per-cell sprite palettes all vary; a console
  that lacks one is an honest no-op, so a target-neutral room painter needs no
  `if platform` fork. See "Colour" below for the table and the rules.
- **SMS / Game Gear have no hardware sprite flip** (`PLATFORM_CAPS`
  `has_sprite_flip` false). The metasprite engine therefore leaves the block
  **unflipped** on those consoles (it used to reverse the cell layout without
  mirroring the tiles → garbled); `FLIP_X`/`FLIP_Y` are honest no-ops there. To
  actually face both ways, use **dedicated/pre-mirrored frames** (the slice
  ships `player_left`) - portable on every console.
- **`graphics.text` = GBDK `printf`, too big for the NES.** It overflows NROM
  into an unbootable 128 KB banked ROM. The codegen now emits the text helpers
  only when text is *actually used* (`text_used`), so gate text off on the NES
  (conditional-compile dialogue to no-ops) and it links no `printf`. NES = no
  on-screen text unless a `printf`-free text path is added.
- **Text coordinates are per-backend.** GBDK writes to the *scrolling* BG map
  (add the camera tile offset); PCE conio + Lynx tgi use fixed *screen* cells.
  Select with `if platform == …`.
- **The GB-family text layer has more than `print_string`** (all gated on the
  CALL, byte-identical for a non-user; `mosaik/codegen/gbdk_text.py`):
  `text.to_window(origin_row, box_rows)` PREPARES the window band and
  `text.win_reveal()` SHOWS it, once the caller has drawn into it (switching
  the layer on first displays the half-built box, one LCD frame per stage);
  `text.win_overlay_cut(y)` stops the overlay at scanline `y` (the window goes
  off and the sprites come back below it, GB Studio's `overlay_cut_scanline`);
  `text.glyph_buffer(base, count)` keeps the font in ROM and rasterizes glyphs
  on demand into a tile band (GB family + SMS/GG); the variable-width renderer
  `text.vwf_start` / `vwf_glyph` / `vwf_nl` / `vwf_number` rides that band as
  a ring and keys its widths on the FONT's magenta marker column. A project
  font is `mosaik.toml [assets] font`; a `<font>.json` beside it carries GB
  Studio's `mapping` (which typed text prints which cell), applied by
  `mosaik_vm.compiler.project_font_map` over the compiled string table.
- **Lynx text must be drawn *after* the present.** The Lynx sprite/bkg engine
  repaints the frame each `present`, so dialogue text is drawn after `wait_vblank`
  to overlay the (frozen, single-buffered) room - the "freeze + overlay" idea.
- **Lynx bkg + sprites: the Suzy budget (reworked twice).** The original Lynx
  bkg engine composited the whole 32×32 map into one 256×256 sprite and re-blit
  it with up to four wrapped offsets - most of the 60 Hz budget. The second
  engine composited only the **visible window** into one small sprite, re-blit
  once per frame, recompositing from the logical tile map **only when the camera
  crosses a tile boundary**. That single windowed blit kept foreground sprites
  rock-stable, but the full-window recomposite landed on one frame every 8 px of
  scroll and **stuttered** on real hardware. The current engine (SPRDEMO4 idiom)
  draws the map as a **vertical ring of screen-spanning (~416 px) row strips**  - 
  one Suzy sprite per visible map row. Horizontal scroll is pure SCB position
  (the wide strip needs no wrap copy); vertical scroll recomposites one strip per
  tile crossing, composited a few columns per frame while the entering row is
  off-screen, so neither axis pays a per-tile recomposite spike. The present
  stays **double-buffered** (every frame repaints an off-screen page and flips,
  so static-camera sprites never tear). **Tradeoff:** 16 wide strips ≈ 53 k
  px/frame; real hardware is stutter-free on both axes, but the heavier load can
  intermittently flicker a foreground sprite on the stricter **Beetle/Mednafen
  Lynx core** under the harness (a core artifact, not a real-HW bug - use Handy as
  the proxy). See the rework note below.

## Layer 2 - genre loops

The slice's main loop is the top-down body: per frame - read input, edge-detect
buttons, axis-separated walk-with-collision + facing, camera follow, scene/door
handling, actor update, draw, present, post-present dialogue overlay. This is now
realised as **composable source modules** in [`lib/engine/` + `lib/genre/`](../lib)
(used via the shared `lib/` search path, or vendored to override; the game owns
the loop and *calls into* them. The modules that call *back* into game code are
`engine.anim`, via an animation callback - see [Animation](#animation--gameanim-callback-driven)
 -  and the three scroll streamers, via a column `gather` / `tile_at` callback):

- **Tier-A engine** (genre-agnostic): `engine.pad` (per-button input edge
  detection - both the action buttons *and* the d-pad, for one-step-per-tap grid
  movement), `engine.camera` (follow camera with exported, shared `camx`/`camy`:
  `follow`, the pure `axis` clamp, `set` to pin the view by hand, `approach` +
  `pan_to(tx, ty, step)` for a scripted pan that returns true on arrival),
  `engine.collision` (the pure box-corner test `any_solid` on tile ids, plus the
  collision-LAYER pair `box_blocked` / `box_floor` over the cell types `NONE` /
  `SOLID` / `PLATFORM`, where PLATFORM is one-way), plus the owner modules
  `engine.scene` (current scene id + worldmap/overlay bookkeeping), `engine.dialogue`
  (the paged-box state machine + the per-backend text-cell coordinates - the
  GBDK scrolling-map offset gotcha, encapsulated, and no `printf` linked so the
  NES stays clean - its `col`/`row` delegate to `engine.box`), `engine.box` (the
  shared windowed-text primitive: per-backend text-cell `col`/`row` + a portable
  framed `draw_box` 9-slice over the text layer, NES no-op; `engine.dialogue`
  builds on it, and a menu's frame is drawn by the CALLER through it),
  `engine.hud` (sprite hearts/icon verbs), `engine.menu` (a cursor-driven
  option-list primitive: `cursor` / `set_cursor` / `reset` / `arm()` /
  `nav(count)` / `confirmed()`, with its own Up/Down + A edge latches so it needs
  no `engine.pad`; it deliberately does NOT import `engine.box` - the game calls
  `box.draw_box` itself, so a menu user that never frames a box links none of
  the ~1 KB draw code; the peer of `engine.dialogue`, the game owns the labels
  + actions), `engine.sequence` (a beat + frame-timer state machine for intros
  and cutscenes: `step` / `timer` / `start` / `at(n)` / `to(n)` / `advance` /
  `tick` / `elapsed(frames)`; pairs with `camera.pan_to`), the three
  background STREAMERS for maps wider than the hardware ring - `engine.scroll`
  (horizontal column streaming for a > 256 px map: `seed` / `fill` / `refill` /
  `refill_cols` / `refill_rows` / `update(camx, gather)` + the shared `buf` /
  `abuf` / `attrs`), `engine.scroll2d` (both axes for a free-roam world:
  `seed2d` / `fill2d` / `refill2d` / `refill_cols2d` / `refill_band2d` /
  `update2d(camx, camy, tile_at)`; the row period is the console's background
  height, 28 on SMS/GG; not on the NES, its 4-byte callback exceeds the 6502
  pointer-call limit) and `engine.scrollpx` (column streaming with up to three
  PARALLAX bands: `band` / `arm` / `FIXED` + the `scroll` verbs; not on the NES
  for the same reason) - and `engine.anim` (callback-driven sprite animation  - 
  see [Animation](#animation--gameanim-callback-driven)).
  The game keeps the loop, the map upload, the transition sequencing and the
  dialogue strings; the modules own the reusable state + verbs.
- **Tier-B top-down kit**: `genre.topdown` (`facing4` / `anim2` / `toward` + the
  `FACE_*` constants) and `lib/genre/topdown_template.mos` - the canonical,
  copy-me loop skeleton with the **invariant body** in a fixed order and
  `FILL IN` markers for what the game supplies:
  - the input→intent mapping (which buttons do what),
  - the per-actor update (enemy AI, NPC behaviour),
  - the scene table (maps + object lists + door edges, → Layer 3).

A second game proves it generalises: **`projects/box-pusher`** (a Sokoban-style
grid puzzle, deliberately unlike the slice) composes `lib/` (via the shared
search path) **à la carte** - a grid genre uses `engine.pad` + `engine.camera` and
legitimately *not* `collision`/`topdown` (it checks cell types and moves a whole
cell at a time). Building it is what *added* the d-pad edges to `engine.pad`: a new
genre extended the framework without touching the others.

A second genre proves the extensibility: **`projects/platformer`** (a
side-scrolling platformer) reuses every Tier-A module *unchanged* (`engine.pad`
for the jump edge, `engine.camera`, `engine.collision`) and adds only a tiny new
Tier-B kit, **`genre.platformer`** (`fall(vy, grav, maxfall)` - gravity
integration, signed `i8` velocity). A new genre is a new kit module + the genre
loop; the shared engine is untouched.

A third genre, **`genre.shmup`** (`overlap`/`cooldown`/`off_top`  - 
sprite-vs-sprite AABB collision, a frame-timer countdown, and the u8-safe
rising-projectile despawn), makes the point about *à la carte* composition: a
shmup has a **static playfield** (the background scrolls, but there is no follow
camera), so it composes `engine.pad` + `genre.shmup` and legitimately *not*
`engine.camera`/`engine.collision` - collision here is sprite-vs-sprite, not
vs-tilemap. `projects/shmup` (Starfall) is the worked game; `shmup_template.mos`
is the copy-me loop. The **[Adding a genre](#adding-a-genre)** section below walks
through the recipe step by step.

A fourth kit, **`genre.combat`** (`overlap` / `within` / `cooldown` /
`step_toward` / `hitbox_x` / `hitbox_y` - sprite-vs-sprite AABB, centre
proximity for the sword-hit and contact-damage tests, a frame-timer countdown
for attack windows and i-frames, a one-step chase toward the player, and the
sword hitbox centre by facing), generalises the shmup pool/AABB shape to a
roaming top-down fight. As with every kit the enemy POOL stays in the game as
struct-of-arrays; the slice hand-writes exactly this shape in `game_slice.mos`.

> **Studio note - authored sprite hitboxes.** The engine's sprite-vs-sprite math
> (`genre.combat`'s `within`/`overlap`, `genre.shmup`'s `overlap`) is unchanged.
> The studio's sprite editor lets a game author a per-kind hitbox rect
> (`studio.toml [hitbox.<kind>]`) inside the sprite frame. On the VM8 path the
> generated `rooms.load_room` registers it as the actor's ONE box through
> `vm.entity.set_box` (`mosaik_vm/rooms/emit_load.py`): it decides player
> blocking, what a projectile hits and what a push probes; a world that authors
> none falls back to the drawn rectangle and is byte-identical. (The component
> composer that first lowered these boxes was retired in 2026-07.)

## Animation - `engine.anim` (callback-driven)

Every animated entity used to hand-roll the same three lines inline: a tick
counter, a `% period` frame select, and a `sprite.set_meta`/`set_tile` swap.
`engine.anim` factors that out. It owns the **timing**; your game owns the
**frames**, supplied as an *apply callback*. This is the framework's one place
that calls *back* into game code, and it is built directly on mosaik's
[first-class function pointers](mosaik_lang_spec.md#28-callbacks-function-pointers--animation).

### The apply callback

An animator is registered with a callback of type `function(u8, u8)` - it
receives `(slot, frame)` and does the actual swap. The game owns the
frame→graphic mapping; the module never calls `sprite.*` itself:

```mosaik
-- frame -> which graphic. `slot` is the sprite/OAM slot the animator drives.
function knight_frame(slot: u8, frame: u8) {
    if frame == 0 { sprite.set_meta(slot, knight0_tile, knight0_w, knight0_h) }
    else          { sprite.set_meta(slot, knight1_tile, knight1_w, knight1_h) }
}
```

The handler is passed **by name** (`knight_frame`, no `&`), stored in the
animator pool, and called through a function pointer. Callbacks carry no
captured environment, so a handler reads game state through shared module-level
`var`s (the same way `engine.camera` shares `camx`). It must be a **home-bank**
function - referencing a `bank(N)` function as a callback is a compile error on
the Game Boy family (see the spec §2.8).

### API

| Call | Effect |
|------|--------|
| `anim.set(i, period, count, cb)` | Register a **looping** animator `i`: `count` frames, one step every `period` ticks, applied by `cb(i, frame)`. Applies frame 0 immediately. |
| `anim.play_once(i, period, count, cb)` | Register a **one-shot** animator: play `count` frames once, then stop on the last frame and raise `done(i)` (explosion, chest-open). |
| `anim.tick()` | Advance every active animator one tick; fire the callback when the frame STEPPED (every frame on the Lynx and the PCE, see below). Call once per frame. |
| `anim.active(i)` / `anim.done(i)` | Is animator `i` still running? / did a one-shot just finish? - poll these to hide the sprite or advance state when a one-shot ends. |
| `anim.set_period(i, period)` | Change the step rate without disturbing the frame/clock (e.g. faster while running). |
| `anim.reset(i, frame)` | Jump to `frame` and apply it now (e.g. snap to the standing frame when stopped). |
| `anim.clear(i)` | Stop animator `i` (leaves the current graphic in place). |

The slot index `i` (0..`MAXA`) is the *animator* slot; it is independent of
the sprite/OAM slot the callback drives (they're often the same number, as
above). `MAXA` is `VM_ANIM_SLOTS` = `max(8, [build] actor_pool)`
(`mosaik8_build.py`): a hand-written game gets 8, and a VM8 project whose
`actor_pool` is bigger gets a pool that covers every actor slot, because
`vm.canim` indexes the animators BY ACTOR SLOT.

### The whole loop

```mosaik
import "engine.anim"
-- ...knight_frame defined as above...

function main() {
    sprite.set_data(0, sprites_tile_count, sprites_tiles)
    anim.set(KNIGHT, 8, 2, knight_frame)     -- 2 frames, step every 8 ticks
    -- ...
    loop {
        -- speed the stride up while a "run" button is held
        if run == 1 { anim.set_period(KNIGHT, 4) } else { anim.set_period(KNIGHT, 8) }
        anim.tick()                          -- advances + re-asserts the frame
        sprite.move(KNIGHT, x, y)
        video.wait_vblank()
    }
}
```

For a single-tile sprite the callback is just `sprite.set_tile(slot, frame)`;
to stop animating while standing, call `anim.reset(i, 0)` instead of `tick()`.

### When the callback fires (the Lynx rule, and the cost of the others)

`anim.tick()` fires the callback **every frame on the Lynx and the PCE only**;
on the persistent-OAM consoles (the GB family, SMS/GG, NES) it fires only when
the frame really STEPPED (`if platform == "lynx" or platform == "pce"` in
`lib/engine/anim.mos`). The every-frame call is the Lynx rule: its present
rebuilds the sprite composite, so a metasprite must be re-asserted or it drops
out of the frame. It used to be carried unconditionally to consoles whose OAM
persists, where a redundant re-assert is a no-op by construction but not a free
one: measured on the GB Studio sample conversion's town room idle (three armed animators, none
stepping), 2,825 cycles per call, 8,474 a frame, all to conclude that nothing
changed. What makes the stepped-only rule safe is that `set` / `play_once` /
`reset` APPLY IMMEDIATELY, so every input to a callback other than `frame` has
a re-arm behind it; a callback must therefore be a pure function of `frame`
plus state its own driver re-arms on, never of something that changes between
steps unannounced. `tick()` also walks only `n_on` armed slots, so an idle
pool costs one compare.

### When it fits - and when to stay inline

`engine.anim` models a **fixed-period frame cycle** on one sprite/metasprite.
Worked uses: `projects/background` (the walker), `lib/genre/topdown_template.mos` (the walk
cycle), and `samples/graphics_showcase` (the ship). It is *not* a fit - and
those programs rightly keep their logic inline - for:

- **Facing selection** (`projects/game-slice`): one static metasprite per
  direction, no time-cycled frames.
- **One-shot effects** (`projects/shmup`): an explosion shown for N frames then
  gone - a timer/state change, not a loop.
- **Free-running / register-driven flicker** (a free-running register-driven
  sample): the flame tile is picked from the GB `REG_DIV` timer, deliberately
  not periodic.

## Layer 3 - declarative scene format

Realised as the **`mosaik_scenes/`** package (`python -m mosaik_scenes
world.toml -o scenes.mos`; `cli.py`, the loaders, `palette_fold.py`, and the
`transpile/` sub-package where `context.analyse` feeds the emitters over one
shared line buffer), a world-TOML → mosaik transpiler alongside
`mosaik_assets.py`, reusing its PNG→tiles pipeline for the tileset.
The format expresses exactly what the slice pinned:

- a **scene**: a 32×32 tilemap (≤256×256 px scrolls seamlessly in the u8
  hardware ring; a LARGER room is streamed, see below) + a shared tileset (a
  PNG),
- **per-scene object placements** (`kind` + position; `kind` names map to id
  constants `KIND_*`),
- **door edges**: `(from-scene, trigger cell) → (to-scene, entry pixel)`.

It emits a `scenes` module: the tileset + each scene's map as `const` arrays,
`map_tile(scene, idx)` (picks the map by id - arrays can't be passed by value)
and `paint(scene)` (uploads a room's map), plus the object and door tables. The
game `import`s it and composes the framework around the data - editing the world
needs **no code change**, just re-running the transpiler. Worked example:
**`projects/scene-demo`** (a two-room world joined by doors, generated by
`assets/gen_world.py` → `world.toml` → `scenes.mos`); transpiler tested in
`tests/scene_transpile_test.py`. The transpiler accepts **either** a single
`world.toml` **or** a split-per-resource world **directory** (`world.toml`
header + `scenes/<name>.toml` per scene + optional `doors.toml`, the `.gbsres`
analogue) - both assemble to the same world and emit a byte-identical module.

Three **optional** `world.toml` features (each emitted only when present, so plain
worlds are byte-identical):

- **Several tileset images** - `[tileset] pngs = ["a.png", "b.png"]` merges the
  PNGs into one combined tile table (concatenated in load order; image *k*'s tiles
  occupy a contiguous index range after the earlier ones). Back-compat with
  `[tileset] png`. The combined table must stay ≤256 tiles.
- **Dead-tile packing** - `[world] pack_tiles = true` emits *only* the tiles the
  maps reference, remapped to a compact `0..k-1` range (so an oversized / merged
  tileset costs only what's used). Opt-in; skipped when animated tiles are present.
- **Animated background tiles** - each `[[animated_tile]]` (`tile`, `frames`
  = source tile indices, `period`, `count`) is the tile-data swap: a fixed map tile
  whose pixel *data* is rewritten on a timer so every cell using it animates at once.
  It lowers to a self-contained **`scenes.anim_tick()`** the game loop calls once
  per frame (an inline per-tile timer + `bkg.set_data`, no `engine.anim` slot).
  Worked example: `projects/game-slice` (animated water).
- **Script-written background tiles** - `[[replace_tile]]` rows (`name`, `png`)
  are the world's REPLACEMENT BANK: the same tile-data swap with the frame
  chosen by a VM8 event (`BKG_TILE` / `BKG_TILE_E`) instead of a timer, which is
  GB Studio's `EVENT_REPLACE_TILE_XY`. The rows are CONCATENATED in world order
  into one bank the event's `src` indexes, so reordering them renumbers every
  authored write. It lowers to **`scenes.replace_tile(room, dst, src)`**,
  registered through the opt-in `core.set_bkg_tile` seam; a world without a bank
  emits none of it. Like an animation's frames the bank is baked as data and
  nothing uploads it, so it costs no background VRAM - only the 16 bytes a write
  names reach the hardware. Cap 255 tiles (the `src` operand is a u8); an
  out-of-bank `src` is a no-op. Worked example: the RPG check conversion (a shop
  wallet, an HP readout driven by its own scripts, and a journal page, all drawn
  on the background; a local-only conversion, not part of the repository).

**Scene types.** `[[scene]] scene_type` is one of `topdown` / `platform` /
`adventure` / `shmup` / `logo` / `menu` / `pointnclick` (`SCENE_TYPES` in
`mosaik_scenes/transpile/context.py`; the list index IS the `SCTYPE_*` id a
built ROM carries, so entries are only ever APPENDED). `pointnclick` (id 6) is
a cursor player: eight directions, no wall collision, clamped to the room, no
automatic trigger scan, and A interacts with whatever the cursor OVERLAPS.
**Wide / roam is derived per room from its SIZE, never a flag**: a room that
fits the hardware ring (32 columns; 32 rows, 28 on SMS/GG) is painted, a wider
platform / shmup room streams columns through `engine.scroll`, and a wider
and/or taller topdown / adventure / pointnclick room roams both axes through
`engine.scroll2d` (`mosaik_vm/rooms/generate.py`).

The world format has since grown more optional keys, each emitted only when
present. In `world.toml`: the residency opt-ins `[world] stream` /
`paint_table` / `metatiles`; `[world] pal_write` (a script may write a library
palette at run time - `PAL_SET`, a seam under `core.set_pal_write`); the COLOUR
keys (see "Colour" below); a per-scene `[[scene]] animated_tile` row set beside
the world-level `[[animated_tile]]` (same shape, `anim_tick_at`); and
`[[scene]] parallax` (up to three scanline bands of `rows` at `speed` = a
shift 0..15, i.e. 1/2^n, or `"fixed"`; driven by `engine.scrollpx`, which the
generated `rooms.mos` imports); and `[kind_variants]`, a placed PLACEHOLDER
kind that the room load swaps for the kind a heap variable picks:

```toml
[kind_variants]
foe = { var = "f_sp", kinds = ["mon_a", "mon_b", "mon_c"] }
```

Wherever `foe` is placed, `load_room` reads `f_sp` and places `kinds[f_sp]`
instead (its sheet, clips, palette and box); a value outside the list keeps
`foe`. One room then serves every variant (a battle room, any foe) instead of
one room per variant, and only the chosen sheet is uploaded. It needs
`studio.toml [sprites] residency = "room"`; the variable must be one a script
uses. The room-load knobs live in **`studio.toml [scenes]`**, read by
`generate_rooms`: `fade` (LCD frames held per darkness step on a room load,
absent = an instant cut), `fade_style` (`"white"` | absent = the engine's
black), `overlay_cut` (the scanline at which the window overlay stops; GB
family only; absent / 0 / 150 = no cut), `glyph_text` (glyph-buffer text,
engine G16), and `trigger_force` (the scene names whose trigger scan re-fires
on a button press, plus `trigger_force_button`).

**The `vm.core` seams.** A generated `rooms.mos` (or a hand-written shell)
wires the world into the fixed runtime through REGISTRATION seams, each an
opt-in that costs nothing unwired; all are in the export list at the bottom of
`lib/vm/core.mos`: `set_player` (the per-frame player draw), `set_save` /
`set_save_slots` (battery SRAM + GB Studio's three slots), `set_start_update`,
`set_redraw` / `set_redraw_rows` (the SMS/GG box-close repaint), `set_ui_freeze`,
`set_box_min`, `set_bkg_tile`, `set_pal_write` (exported on its own line, but
exported), `set_music_driver` / `set_music_ctl`, and `rooms.set_on_load(cb)`,
which `generate_rooms` emits only when the shell asks for an on-load hook and
which then runs LAST in every room load.

## Colour - palettes on every console

Two independent questions, and it helps to keep them apart:

* **What colours exist** - `graphics.palette` loads them into the hardware.
* **Which colours a given tile or sprite uses** - the per-tile background
  palette map and the per-sprite / per-cell sprite palette pick a *slot*.

Everything here is **additive**: a world that colours nothing emits none of it,
and every console that cannot do a part of it degrades to an honest no-op
rather than an error, so one target-neutral game builds everywhere.

### The portable model

A **palette slot holds 4 colours**, matching the Game Boy: background colour 0
is the paper, sprite colour 0 is transparent. How many slots you get per layer
is `PLATFORM_CAPS`:

| console | bkg slots | sprite slots | per-TILE bkg palettes |
|---|---|---|---|
| Game Boy Color, Analogue Pocket | 8 | 8 | yes |
| NES | 4 | 4 | yes, at 16x16 px granularity |
| PC Engine | 4 | 4 | yes |
| Game Boy, Mega Duck | 1 | 2 (`OBP0`/`OBP1`) | no (4 greys) |
| SMS, Game Gear | 4 (in the tile upload: CRAM `slot*4..`) ¹ | 4 (a kind's sheet upload) ¹ | yes, baked into the uploaded tile |
| Atari Lynx | 1 | 4 | no (one 16-pen palette, partitioned) |

¹ Two layers, both true: `PLATFORM_CAPS` counts `bkg_palettes` 1 /
`spr_palettes` 1 for SMS/GG (the hardware has ONE 16-entry CRAM bank per
layer; a slot is a nibble map applied at tile upload, not a register the
runtime selects), while the studio's palette editor answers 4
because that is how many slots the upload fold gives an author.

**Slot 0 is the portable guarantee.** On the four 4-slot consoles (SMS, Game
Gear, NES, PC Engine) a world that uses slots 4..7 is **folded** at transpile
time for any project that targets one of them (`mosaik_scenes/palette_fold.py`):
slot 0 stays 0, used slots 1..3 keep their number, 4..7 take the free positions
by usage and the rest merge onto the nearest palette. Elsewhere out-of-range
slots are masked or ignored at run time, never an error.

Note the four consoles with no per-tile background palettes are not
second-class: SMS/GG, the Lynx and the PCE reach 16 colours through the **4bpp
background tier** instead (a > 4-colour tileset + `palette.load_bkg16`), which
is a better fit for their hardware. Per-tile 4-colour palettes are the Game Boy
Color's and the NES's answer to the same problem.

### Background colour: a palette per TILE

A coloured world declares five things, all optional and all in `world.toml`:

```toml
[[palette]]                                    # the world's palette LIBRARY
name = "cave_wall"
colors = ["F8E8C8", "D89048", "A82820", "082048"]   # or [[r,g,b], ...]

[[scene]]
bkg_palettes = [0, 1, 2, 3, 4, 5, 5, 7]        # 8 hardware slots -> library index
spr_palettes = [1, 2, 0, 0, 0, 0, 0, 1]
tile_palette = [0, 0, 4, 4, 5, 5, ...]         # per TILE -> background slot
```

A scene without its own rows loads `[world] default_bkg_palettes` /
`default_spr_palettes` (8 library indices each) when the world declares them.

The transpiler emits per-scene palette tables plus
**`scenes.load_palettes(scene)`**, a per-tile map with **`tile_pal(scene,
tile)`**, and **`scenes.paint_attrs(scene)`**, which uploads a painted room's
whole attribute map. A generated `rooms.mos` calls `load_palettes` before the
paint and `paint_attrs` after it; a hand-written shell does the same.

**The map is per TILE, not per map CELL, and that is the design.** A cell's
palette is looked up from the tile it already holds, which buys three things:

* it **composes with `stream` / `paint_table` / `metatiles` unchanged**, because
  every reader goes through the `map_tile` it already used - including a
  streamed wide/roam room, which is never painted and colours its columns
  through the same lookup;
* it is roughly **ten times smaller** (one byte per tile, capped at 255 per
  tileset, instead of one per cell);
* it stays **exact** as long as whoever builds the tileset puts the palette in
  the tile DEDUP KEY, so a tile drawn under two palettes simply becomes two
  tiles. (Picking one dominant palette per tile instead was measured on a real
  GB Studio project and miscoloured 5% of its cells.)

The colour words are **portable 5-5-5 RGB**, like `BKG_PALETTE16`: `scenes.mos`
is one source compiled for every console, so it cannot bake native words and
the rounding happens at run time. That is why `palette.load_bkg_set` /
`load_sprite_set` take 5-5-5 while `palette.load_bkg` / `load_sprite` take
native words - the first pair reads GENERATED tables, the second an asset's own
baked `<name>_palette`.

### Sprite colour: per actor, or per cell of one actor

Two verbs, and they are exclusive - pick the one that matches the art:

```toml
[kind_palettes]                                # one palette for the whole actor
npc = 3

[kind_tile_palettes]                           # ... or one per 8x8 CELL of its
player = [1, 0, 1, 0, 2, 2, 2, 2]              # metasprite (hair/face/body)
```

* `sprite.set_palette(id, slot)` colours a whole sprite; on a metasprite it
  fans over every child.
* `sprite.set_meta_palettes(base, w, h, data, off)` colours it **cell by cell**.
  The map is ROW-MAJOR in 8x8-TILE units, the same units `sprite.set_meta`
  takes, so the authored data is target-neutral; only the runtime fan knows the
  console's object order (8x8 mode walks it 1:1, 8x16 OBJ mode takes cell
  `(2*pair)*w + col` per object, and the cc65 consoles loop their per-slot
  setter because a metasprite child there IS a real sprite slot).

The transpiler emits `scenes.paint_actor(base, kind, w, h)` for the second;
`generate_rooms` calls it at actor activation, passing the kind's metasprite
size from the clips module.

**`set_prop` and the palette verbs own DIFFERENT bits of the same byte.**
`sprite.set_prop` writes flip/priority and leaves each slot's palette bits
alone; the palette verbs write the palette and leave the flip alone. Without
that split a facing flip erases the colour - `vm.canim` re-asserts `FLIP_X`
through `set_prop` on every turn and re-fans `set_meta` every frame, so a
coloured actor snapped back to palette 0 the moment it moved. The bits are
preserved **per hardware slot**, not per metasprite base, which is what lets one
metasprite carry a different palette in each cell.

### Rules and gotchas

* **Never hold a raw pointer into a table you did not pin resident.** The
  per-cell sprite map is applied ONCE and then lives in OAM, because the first
  version remembered a `const uint8_t *` per base and re-applied it later - and
  the moment engine CODE banking co-located the table into another bank, that
  pointer was read from the wrong bank and every actor came out on palette 7
  (`0xFF & 7`). The glyph rasterizer documents the same trap.
* **A short per-cell row is read PAST.** The runtime reads `w*h` cells for
  whatever actor it is colouring, so once ANY kind has a per-cell map, EVERY
  kind needs a full-length row (fill an unmapped kind's with its own slot). A
  1-byte placeholder scattered a 42-cell sprite across eight palettes.
* **Keep a generated table's only reader inside its own module.** That is why
  `load_palettes` and `paint_actor` live in `scenes.mos` rather than in the
  caller: engine CODE banking then co-locates the data into that module's ROM
  bank. Reading them from `rooms.mos` instead pinned about 2.8 KB in the Game
  Boy's resident image, straight over bank 0.
* **Every `graphics.palette` verb is resident.** A prelude helper cannot bank,
  so each entry point is emitted only when it is actually called - importing
  the module for one verb no longer drags in all of it.
* **A frame palette does NOTHING on SMS/GG.** Neither console has a
  per-sprite palette select (sprites always read the sprite CRAM bank), so
  `gbs_sprite_palette` is generated as an honest no-op there
  (`mosaik/codegen/gbdk_palette.py`) and a kind's slot is baked into its tile
  data at upload. The 8 -> 4 fold applies to what the BUILD writes, never to a
  runtime per-sprite write.
* **The 16-colour tier: a > 4-colour tileset's PLTE IS the palette.** A
  <= 4-colour tileset is the slot tier above; a tileset PNG with more than four
  colours takes the 4bpp background tier instead (`mosaik_scenes/transpile/
  context.py`): the tileset is forked per platform (packed-nibble tiles on the
  4bpp consoles, 2bpp elsewhere), its authored 16-colour PLTE is emitted as
  `BKG_PALETTE16` for `palette.load_bkg16`, and per-tile slot edits do not
  apply. The Lynx joins that fork only with `[world] lynx_bkg16`.
* **The order trap for imported art.** A GB Studio PNG's 4-entry PLTE is
  ordered DARKEST-first, the opposite of mosaik's "a <= 4-entry index IS the GB
  colour" rule (where 0 is LIGHTEST). Anything reading such a PNG must remap by
  luma; do not change `png_to_gb_tiles`' contract, which the rest of the engine
  depends on.

### Where it lives

`graphics.palette` / `graphics.bkg` / `graphics.sprite` in
`docs/mosaik_lang_spec.md` (the normative surface); the emitters in
`mosaik/codegen/gbdk_palette.py` + `cc65_palette.py`; the world data in the
`mosaik_scenes/transpile/` package (`emit_palettes.py`) + `palette_fold.py`;
the room wiring in the `mosaik_vm/rooms/` package. Tests:
`tests/colour_tier_test.py` and `tests/palette_test.py`. Three models live
side by side: the original portable GB palette model, the per-tile colour
tier, and the 16-colour (4bpp) background alternative.

## Audio - the VM8 music packs

Music is an opt-in pack behind the `MUSIC_SONG` / `music_play` ops, wired by
the generated `src/glue.mos` through `core.set_music_driver`; unwired, nothing
links. Two drop-in drivers share that seam: **`vm.music`** (`lib/vm/music.mos`,
the portable generic-channel driver for every console: the MAIN LOOP owns the
tick, and a WATCHDOG interrupt covers the one unsplittable call a room load
makes, so tempo stays independent of the frame rate) and **`vm.music_huge`**
(`lib/vm/music_huge.mos`, the real hUGEDriver over `native.huge`; **GB family
only**, a compile error elsewhere; a 64 Hz timer interrupt that NESTS when the
program has a scanline consumer; ~2 KB resident). A long blocking operation
(a room load, a streamer refill) must call **`core.music_pump()`** so the song
does not stall. The MUTE mask is **bit SET = channel muted** (hUGE's own
polarity), and **`MUSIC_PLAY` clears it**, so a new song is all audible.

## Lynx bkg rework - row-strip ring

The Lynx bkg engine (`_emit_cc65_bkg_engine` in `mosaik/codegen/cc65_bkg.py`) has had
three forms. **(1) Full-map composite:** the whole 32×32 map blit as one 256×256
sprite with up to four wrapped offsets - the blit dominated the 60 Hz budget.
**(2) Windowed composite:** only the **visible window** (screen + 1 tile margin)
composited into one small sprite, re-blit each frame and recomposited from the
logical map (`gbs_bkg_compose`) **only when the camera crossed a tile boundary**.
That single windowed blit kept foreground sprites stable, but the full-window tile
copy (`win_w*win_h` ≈ 294 cells, ~a frame's work on the 65C02) landed on one
frame every 8 px of scroll, so *continuous* scrolling **stuttered** (most visible
in `projects/background`, which scrolls 1 px/frame whenever a D-pad is held).

**(3) Row-strip ring (current).** The engine keeps the GB background *model* (a
256-tile table + a **logical 32×32 tile-index map** `gbs_bkg_map`, u8 scroll wrap
mod 256) but draws the map the SPRDEMO4 way - a **vertical ring of screen-spanning
literal row strips**, one `TYPE_BACKNONCOLL` Suzy sprite per visible map row
(`gbs_bkg_strip[GBS_BKG_STRIPS]`), positioned independently each frame. Scrolling
*moves SCBs*, it never re-lays-out pixels:

- **Horizontal scroll is pure SCB position** (`hpos = -x`), with **no wrap copy**.
  Each strip is `GBS_BKG_STRIP_W` tiles wide = the whole 256-px scroll period +
  the screen (52 tiles on the Lynx), map columns repeating (`col c & 31`), so a
  *single* strip placed at `-x` covers the screen at any scroll 0..255 - crossing
  a vertical tile boundary needs no recomposite and no second sprite. (An earlier
  256-px-strip version needed a wrap copy per row at `hpos+256` to cover the seam,
  which doubled the per-frame SCB count to ~32 during horizontal scroll and
  **flickered foreground sprites on real hardware**; the wide strip keeps it at
  one SCB per row, the same count as the smooth vertical case.)
- **Vertical scroll recomposites one slot per tile cross** (the row newly
  entering at the bottom). A row stays in the same ring slot for its whole visible
  life and is composited *once* on entry - and it **enters off-screen, ~16 frames
  before it scrolls into view**, so the engine composites it **incrementally**,
  `GBS_BKG_AMORT` columns/frame (16; `gbs_bkg_compose_cols` + `gbs_bkg_strip_col[]`),
  rather than all at once. So no single frame pays the whole strip - the vertical
  per-tile recomposite *spike* (which stuttered when strips first went wide) is
  amortized away. A draw-time full-compose fallback keeps it correct if a fast
  scroll/first load outruns the amortizer. `AMORT` must clear a full strip within
  the off-screen margin at the game's scroll speed - 16 keeps up with a 2px/frame
  follow-camera; 12 (enough for the 1px/frame `background` demo) hitched
  periodically on game-slice's faster scroll. `GBS_BKG_STRIPS` is a power of two
  dividing the 32-row map (16 on the Lynx), so the ring (slot `= (ty0+i) %
  STRIPS`, `vpos = i*8 - frac`) rotates +1 per tile step even across the wrap.

So both the horizontal wrap-copy flicker and the vertical recomposite spike are
gone, and both axes draw at 16 SCBs/frame. The present stays **double-buffered**
(`tgi_setdrawpage(1)` + `tgi_updatedisplay()` each frame), so every shown frame is
complete and **static-camera sprites stay put** (`colorlab` gems, the game-slice
HUD) - the property the windowed rework restored and this one keeps.

### Tradeoff: per-frame pixel load vs the Beetle core (pinned)

To make horizontal scroll wrap-copy-free, each strip is ~416 px wide (52 tiles),
so the ring blits **16 SCBs × ~416 px ≈ 53 k px/frame** (vs the windowed engine's
single ~screen-sized blit). On **real hardware** this is stutter-free on *both*
axes (verified: horizontal smooth, and the amortized vertical recomposite removes
the vertical stutter). The heavier per-frame Suzy load can **intermittently
flicker a foreground sprite on the stricter Beetle/Mednafen Lynx core** under the
libretro harness - a core artifact (same family as the ~16–32 Suzy per-frame
sprite ceiling that caps the side-scroller sample on Beetle), **not** a real-HW bug; use
the **Handy** core as the HW proxy. The libretro harness also can't *see* the
stutter the rework fixes (it runs every frame to completion, no real-time clock),
so the stutter win is confirmed by
the cost model + hardware while the harness confirms "renders + scrolls
correctly." If the Beetle pixel load matters more than the stutter for a given
program, the windowed composite (git history) is the 1-blit alternative.

## Building a game on the framework

1. Author assets with a project `gen_sprites.py` (named-sprite sheet + tilemaps),
   as in `projects/game-slice/assets/`.
2. Pull the framework into your project - two ways, pick one:
   - **Shared `lib/` search path (no copying).** `import "engine.camera"` resolves
     to `lib/engine/camera.mos` automatically (the importer's own folder is
     searched first, then `[lib] paths` from your `mosaik.toml`, the `MOSAIK_LIB`
     env var, and the default `lib/` next to the tool). Tree-shaking pulls in
     only the modules you import.
   - **Vendor the modules.** Copy the ones you use into your `[source]` folder
     (project mode compiles everything under it). A vendored copy **wins** over
     the lib root (see `projects/vendor-override`).
3. Write your game module with the Layer-1 systems you need (the slice's
   `world.mos` pure helpers + a generated `scenes` module from a `world.toml`
   are a good starting point).
4. Respect the per-console rulebook above (it is the difference between "builds"
   and "runs on all nine").
5. Add the project to `tests/run_all.py` `PROJECT_DIRS` and a behavioural check
   to `tests/verify_roms.py` (the slice has a Game Boy one).

### `[build]` keys (`mosaik.toml`)

The keys the build applies are `APPLIED_KEYS['build']` in `mosaik8_build.py`
(anything else under `[build]` is warned as unknown; `optimization_level` and
`debug_symbols` are recognised but not yet acted on). Every one is optional and
absent = byte-identical; each `get_*` reader's docstring is the full contract.

- `output_dir` - where the ROMs go (default `build`).
- `rom_size` / `ram_size` - the cartridge ROM size in 16 KB banks (unset = auto,
  which must cover every `bank(N)`) and the battery-RAM size in 8 KB banks.
- `shake_exports` - opt-in declaration-level tree-shaking.
- `code_banks` - a list of module names whose function bodies leave the GB
  family's resident image for a switchable bank (`vm.core` / `scripts` refused).
- `bank_bytecode` - put the VM8 bytecode blob in a ROM bank on the GB family
  instead of the resident image (needs `code_banks`).
- `vm_quant` - VM8 instructions per thread per frame (default 16); a
  correctness lever, since a starved thread reads as a broken program.
- `frame_lock` - hold a game frame to N display frames (1 = off; a FLOOR, not a
  ceiling) so a conversion's clock ratio is the constant it assumes.
- `actor_pool` / `trigger_pool` - the VM8 runtime slot pools (default 8 each,
  BSS on every console; `generate_rooms` refuses a room over them). `actor_pool`
  also sizes `VM_ANIM_SLOTS = max(8, actor_pool)`.
- `park_updates` - kill an off-window actor's On Update thread and respawn it
  from the top when the camera returns (GB Studio's model; semantic).
- `actor_deactivate` - GB Studio's offscreen deactivation: an actor the camera
  left is taken OUT of every per-frame walk (semantic; only the camera wakes it).
- `actor_scan` / `proj_scan` - re-test a parked actor / collision-test a shot
  one frame in N (1, 2, 4 or 8; a mask); GB Studio's own defaults are 4.
- `proj_under_lock` - shots keep flying under a cutscene lock, as in gbvm.
- `move_lcd` - pace a native auto-move by elapsed LCD frames; measured to
  destroy a conversion's balance, kept OFF.
- `obj_8x16` - 8x16 OBJ sprite mode on the six consoles in
  `platforms.OBJ16_CONSOLES` (a WxH metasprite costs W*H/2 objects).
- `sms_start_button` - the SMS pad's button 1 also answers as Start (a
  per-project choice; the PAUSE NMI stays a real Start either way).
- `bkg_max_tiles` / `bkg_strip_w` / `sprite_max_tiles` / `sprite_max_slots` -
  the cc65 Lynx budgets: resident background tile table (1..256), row-strip
  width in tiles (16..64), Suzy sprite tile table and sprite SLOT count
  (1..40 each), all reclaiming the scarce Lynx MAIN; author-owned contracts.
- `lynx_stack_size` - the Lynx C stack in bytes (default 512, 128..4096).
- `lynx_bkg16` - force the 4bpp Lynx background engine for a HAND-WRITTEN game
  (a scenes/VM8 world uses `[world] lynx_bkg16` instead).
- `lynx_code_resident` - keep the VM8 blob resident in Lynx RAM instead of
  streaming it from the cart (measured: a VM frame 3 -> 2 LCD frames on
  the falling-block assembly sample, for ~2 KB of MAIN).

## Adding a genre

A practical, step-by-step recipe for extending the framework with a new genre
(top-down, platformer, shmup, puzzle, …). The architecture and the constraint
list are above; this is the *recipe*.

### The mental model (one rule)

> **Your game owns the loop and *calls into* the framework. The framework does
> not drive your game logic.**

mosaik *does* have first-class function pointers, but the genre kits deliberately
don't use them to run your game - there is no "engine" that owns the loop and
invokes your `update_enemy()`. (The lone framework callback is `engine.anim`'s
animation handler - for swapping a sprite's frame, not for game logic.) Instead
you compose modules:

| Tier | What | Example |
|---|---|---|
| **A. Engine** (genre-agnostic) | reused by every genre | `engine.pad`, `engine.camera`, `engine.collision` |
| **B. Genre kit** | the *pure* mechanics of one genre + a loop pattern | `genre.topdown`, `genre.platformer`, `genre.shmup` |
| **C. Your game** | owns `main()` + the loop, imports A + B, supplies behaviour inline | `projects/platformer/src/main.mos` |

A genre is added as a **Tier-B module + the genre loop**. You touch *nothing* in
Tier A or other genres.

### What goes where

- **Pure, stateless mechanics → the Tier-B kit.** Anything that takes everything
  by parameter and returns a value: gravity (`genre.platformer.fall`), facing
  (`genre.topdown.facing4`), a chase step (`genre.topdown.toward`). These are the
  only things that modularise cleanly.
- **The loop and all behaviour → your game module.** AI, the input→intent
  mapping, interactions, and the move-with-collision loop *stay in the game*  - 
  because they call back and forth with game state and sample the game's own
  tilemap (which can't be passed by value). Write them inline.
- **Shared state → a Tier-A owner module.** Exported module vars compile to
  shared globals (read *and* write across modules), so a singleton like the
  camera owns `camx`/`camy` and the game reads them directly.

### The constraints that shape a genre kit

(Verified against the compiler - design around these.)

1. **Kits expose verbs, they don't drive your game** → control stays in the
   game's loop. (mosaik *has* function pointers - `engine.anim` uses one for the
   frame swap - but a genre kit calling back into your AI would invert control,
   so kits don't.)
2. **No generics** → a kit's data shapes/sizes are fixed; multi-entity state
   (an actor pool) lives in the game as struct-of-arrays.
3. **No array-by-value params** → helpers take an *id* and select internally, or
   read an exported global; they can't receive a map/array as an argument.
4. **Bitwise operators exist (`& | ^ << >>`, since 2026-07), but the stdlib
   exposes no raw pad word** → handle input per-button (see `engine.pad`);
   masking is for your own flags (a mute mask, a collision bitset).
5. **Newline-terminated parser** → a call's arguments must stay on one line.
6. **Signed math is `i8`/`i16`** → fine for velocities (see `genre.platformer`),
   but mixing with `u8` positions needs care (resolve motion a pixel at a time).

### Recipe (worked example - how the platformer was added)

#### 1. Write the Tier-B kit module - `lib/<genre>.mos`

Put *only* the genre's pure math here. Keep it tiny; most of a genre is the loop,
which lives in the game.

```mosaik
-- genre.platformer: gravity integration (the genre's reusable physics).
module "genre.platformer" {
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
(`genre.platformer` → `platformer.fall`). It **must not** collide with a stdlib
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

`solid(bx, by)` is the game's vendored tile sampler feeding `engine.collision`:

```mosaik
function solid(bx: u8, by: u8) -> bool {
    return collision.any_solid(tile_at(bx + 1, by + 1), tile_at(bx + 14, by + 1),
                               tile_at(bx + 1, by + 14), tile_at(bx + 14, by + 14), SOLID)
}
```

`tile_at` reads either a procedural level (the platformer) or the generated
`scenes` module (`projects/scene-demo`, from a `world.toml` via
the `mosaik_scenes/` package - the Layer-3 data format).

#### 3. Pull the framework into your project

Either the **shared `lib/` search path** (`import "engine.pad"` resolves to
`lib/engine/pad.mos` automatically; tree-shaking pulls in only what you import) or
**vendor** the modules into your `[source]` folder (a vendored copy wins over the
lib root). See "Building a game on the framework" above.

#### 4. Respect the per-console rulebook

The framework encapsulates most gotchas, but a new genre must still honour them
(full list in the "Per-console rulebook" section above):

- **Input has no edge** → use `engine.pad` (edges) for discrete actions; `input.held`
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
  libretro harness for Lynx/PCE/SMS/GG/NES - see the
  [language spec](mosaik_lang_spec.md#56-testing-roms)).
- If you vendored, add the copies to the sync map in
  `tests/game_framework_test.py` so they can't drift from `lib/`.
- If your kit has pure helpers worth asserting, add a compile/lowering check
  there too.

That's the whole loop: **new kit module + genre loop in the game + import (lib
path or vendored) + tests.** Tier A and the other genres never change - that is
the design working.
