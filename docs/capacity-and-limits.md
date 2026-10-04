# Capacity & limits

What bounds the size of a MosaiK8 game - **why** each limit exists, what to do
when you reach it, and how the **residency levers** let a *world* grow past the
resident image: **asset streaming / banking** (`[world] stream`), **the one
generic paint interpreter** (`[world] paint_table`) and **2x2 metatile
compression** (`[world] metatiles`).

> **This file is the reasoning; [`cheat-sheet.md`](cheat-sheet.md) is the
> numbers.** Every hard figure - per-console tile/sprite/palette/colour caps,
> the VM8 sizing table, the scene and world caps, the Lynx buffer maths, the
> `mosaik.toml` knobs, and the source file that owns each one - lives there and
> is not repeated here. Where this file needs a figure to make a point, it
> quotes it and links back.

These limits apply to **both project styles** (the GB-Studio-style **VM8**
path and the **code-only** path) because they come from the hardware.

> Where MosaiK8 differs from GB Studio: GB Studio counts most limits **per scene**;
> MosaiK8 has **one shared tileset per world** and otherwise counts per **console**,
> because one MosaiK8 project builds for all nine.

---

## Part 1 - Hardware limits: what they mean, and what to do

### Background tiles - one shared tileset per world
Tiles are **shared by content** (two scenes using the same tile store it once),
so building many rooms from a common set is cheap. A Tiled `.tmx` import that
would exceed the table is **rejected** cleanly. **When you reach it:** reuse
tiles, delete unused (`[world] pack_tiles` repacks), or split into
more worlds. Metatile compression (Part 2, lever 3) shrinks the *maps*, not the
tileset - it does not buy tile variety.

**Two things that look like tile variety and are not, because they replace a
tile's PIXELS rather than adding an index:** `[[animated_tile]]` (a timer) and
`[[replace_tile]]` (an event - a wallet, an HP bar, a journal page). Both bake
their art as data and upload only the 16 bytes a swap actually names, so
neither spends a background tile at all and neither counts against the tile
table. The cost is ROM, not VRAM. The flip side is the reason they are cheap:
the map keeps ONE index, so every cell using it changes together - that is the
feature for water and flowers, and it is the trap for a numeric readout, whose
cells must be authored as distinct tiles for the digits to differ. (Bank cap in
the cheat sheet.)

### Scene size - why the default is 32 x 28
**28 rows** is deliberate: it is the shortest hardware background (the SMS /
Game Gear name table), so a default world is shown and scrolled in full on every
console. Per-scene sizes are allowed (`map_w`/`map_h`). **The
per-console catch:** the scrollable background is only as tall as the name
table, and those differ per console - a map taller than the target's table
cannot be fully shown there. Keep maps at or under 28 rows for a game that
targets every console.

Rooms past 32 columns column-stream automatically; wide *and* tall topdown rooms
2D-stream. Wide/roam is derived per room from its own size, never a flag, and a
wide room must not be painted.

### Sprites on screen - the count, and the per-scanline cliff
Two independent limits: how many hardware sprites exist, and how many can be
*fetched on one scanline* (the classic flicker limit). What counts is the
**peak OAM** (player + busiest room's objects + HUD) and the busiest scanline.
**When you reach it:** static decoration belongs in the
tilemap, not in sprites; fewer or smaller objects per room. In a VM8 game the
actor pool, the player, the combat pool (`vm.combat`) and a sprite HUD all draw
sprites - budget them together, not separately.

**Projectiles take a contiguous BLOCK, and it is sized last.** `vm.projectile`
reserves `NPROJ x cell_w x cell_h` objects (halved under `[build] obj_8x16`)
starting above the room's actor fans - so a launch sprite bigger than one
hardware object multiplies the reservation, which is why the cell is capped at
2 x 2 tiles. When a room's actors leave less than that, the
pool **gives up SHOTS, not ground**: it shrinks the number of simultaneous
projectiles rather than moving its base down over the fans. (Moving the base is
what it used to do, and a live shot then overwrote whatever actor owned those
slots - on a shoot-'em-up conversion the score digits turned into shot art.) So a
very busy room fires fewer bullets at once; it never corrupts the cast.

### Sprite tiles - the art a build can address
Distinct from the slot count above: this is how much sprite *art* fits, and it
is the tightest on the Lynx / PC Engine. Count it per targeted console and
per KIND: the per-kind figure is what tells you which kind to cut - a total is
not a decision.

### Palettes - slot 0 is the portable guarantee
Slot **0** works on every console (luma-quantized to grey on DMG / Mega Duck);
extra slots exist only on colour consoles. Author against slot 0 and treat the
rest as enhancement, or the DMG build loses information you did not budget for.

### Colour depth - one art, three tiers
The universal interchange is **GB 2bpp** (4 colours per tile); a **4bpp**
16-colour tier kicks in **automatically** when an asset is a >4-colour indexed
PNG and the target renders it. It exists independently for SPRITES
(`PLATFORM_CAPS.sprite_bpp`, which MUST call `palette.load_sprite16`) and
BACKGROUNDS (`PLATFORM_CAPS.bkg_bpp`, `palette.load_bkg16`). Both are
byte-identical when unused, and a `<=4`-colour tileset is byte-identical
everywhere. Multiple palettes let a colour console put many more colours *on
screen at once* even at 4 per tile.

- **4bpp SPRITES** ship on the **Lynx + PC Engine** (`projects/vm-danim`).
- **4bpp BACKGROUNDS** ship on **PCE + SMS/GG** (always-on) and on the **Lynx**
  per project (`[world] lynx_bkg16`), because the Lynx has no tilemap and its
  Suzy strip buffers *double* at 4bpp - see the next section for what that
  actually costs. Both Lynx background engines (row-strip and the wide
  `engine.scroll` column engine) are 4bpp-capable.
- GB / GBC / NES backgrounds stay 2bpp and down-tier by luma **rank**, not by
  absolute threshold - a mid-range 16-colour palette would otherwise crush to
  one flat shade.

Samples: `projects/bkg16-demo`, `vm-bkg16`, `wide-bkg16`, `pal-lab`.
**Still deferred:** SMS/GG **native** 4bpp SPRITES (their VDP 4bpp is planar
and has no mosaik-emitted native-tile path yet), and PCE per-tile 16-colour
background palette selection.
Per-console tiers and on-screen colour ranges: cheat-sheet Part 1 ▸ Colour.

### Atari Lynx background RAM - the real ceiling
On every other console the background is hardware (a tilemap the VDP scans).
The Lynx has none, so the background is **composited into Suzy literal buffers
in RAM**, and those buffers - not the cart, not the tile count - are what caps a
Lynx game. Two engines, picked automatically from the room's width; 4bpp doubles
the per-line bytes. **The buffer costs, the measured 3-scene slice, and the
practical rule ("a ~20-tile non-scrolling room can be 16-colour, a 32-wide
scrolling one cannot") are in cheat-sheet Part 4.** The short version for
planning: **budget a Lynx room's width before its art.**

`STRIP_W` is already minimal - a strip spans *scroll range + screen*, so
horizontal scroll is pure SCB repositioning with no recompose.

**Known headroom nobody has taken yet:** the WIDE engine holds all 32 columns
full-height when a screen-width ring of ~22 would do (~4.4 KB), and the ROW
engine could trade `STRIP_W` 33 -> 21 (3,072 B, 6,144 at 4bpp) by recompositing
on horizontal tile crossings instead of repositioning.

### Collision layer - optional, watch the Lynx
The painted per-cell collision layer (none / solid / one-way platform) is
optional: a scene with no painted layer costs nothing and the build infers
solidity from the wall tile. A painted layer **doubles that scene's data**,
which matters most on the **Lynx**, where cart and RAM share one space. Paint a
layer when you need *decoupled* collision, not for parity with the tile-based
check. Both levers below move that cost: with `paint_table` the layer streams
alongside the map, and with `metatiles` an aligned layer disappears into the
metatile attribute byte entirely.

### Text & dialogue - the tileset budget, and how to remove it
By default the console font lives in the background tile table, so a
**GB-family** program that prints text must leave the font room; past that, the
scene's art overwrites the glyphs and text renders as garbage.

**Set `[scenes] glyph_text` (in the project's `studio.toml`) and that budget
disappears.** The font moves into ROM and
each character is rasterized on demand into a small band above the scene art -
GB Studio's own model - so a room may use nearly the whole table. It costs about
a kilobyte of Game Boy bank 0 and a few extra frames on the first box in a room.
It is real on the SMS / Game Gear too, where it **pays for itself** by dropping
GBDK's `printf` and `font_ibm` - on a z80 target, check what the C library links
before cutting content.

On the **NES** the `printf` routine is large - conditional-compile text away
where it is not needed. A VM8 dialogue box is drawn at the bottom of the screen
(`SCREEN_ROWS`-relative) on every console and **sizes itself to the message**;
keep a line short enough for the narrowest screen. **Dialogue strings are
resident** on the Lynx today, which makes the unique-string cap the
highest-value remaining Lynx fix (Part 4).

### Objects & doors - authored freely, resident nonetheless
Object placements and door connections flatten into tables the game reads, so
there is no small fixed cap on how many you author - but how many **render at
once** is bounded by the OAM limits above, and the tables themselves are
**resident** (a handful of bytes each). So *many rooms x many actors* is bounded
by RAM, not by cart size. The per-world `*_COUNT` ceilings are enforced at
generate time (cheat-sheet Part 3).

---

## Part 2 - Large-world residency (how many rooms fit)

Two things scale a *world*: the map/collision **data**, and the per-scene
**code** that uploads it. Before the capacity program both were resident, so on
the Lynx (one shared MAIN for CODE + RODATA + DATA + BSS, no banking, no
overlays) a game hit the wall within a handful of rooms.

**Lever 1 - asset streaming / banking (`[world] stream`).** The
per-scene maps + collision move off-resident: the **Lynx** streams them from the
cart into a 2-slot LRU cache; the **directly-mapped** consoles bank them (GB
family MBC5, SMS/GG Sega mapper, NES UNROM) and switch-then-read in place. So
the map *data* scales with the cart.

**Lever 2 - the generic paint interpreter (`[world] paint_table`).**
Before this, `paint()` / `map_tile()` / `collision_at()` emitted a **per-scene
dispatch arm** (`if scene == i { ... }`) of resident CODE per room, so even with
the data streamed the **room count was capped by resident code**. `paint_table`
**concatenates** every scene's map (and collision) into one flat `MAPS` /
`COLLISION` array plus a per-scene offset table `MAP_OFF[]`, so the three
functions collapse to an **O(1)** indexed read: **adding a room adds table rows,
not code.**

**Lever 3 - 2x2 metatile compression (`[world] metatiles`).** The DATA
multiplier under both levers above: every 2x2 tile block content-deduplicates
into one shared table and each scene map shrinks to `(w/2)x(h/2)` metatile ids.
Where the painted collision never varies inside a 2x2 block it rides the
metatile as ONE attribute byte and the per-cell collision arrays disappear
entirely - auto-detected, and a 1-tile-thick wall keeps the per-cell layer. The
one bound is CONTENT: authored and structured worlds fit under the distinct-block
cap, procedural noise may not.

**They compose, all three.** With `stream` + `paint_table` the concatenated
array is archived whole and the **current room's window** streams from the Lynx
cart (a cache slot sized to the *widest* scene) via the range-windowed residency
seam - so the room count stops growing **both** resident code **and** resident
data at once. `metatiles` then shrinks the cart archive, the GB banks and the
range window alike (proven on a quantized 44-room `bigworld-paint`: GB
background identical across a streamed door transition, Lynx framebuffer
pixel-identical).

**Per-console reach.** `paint_table` is NOT Lynx-only - it composes on every
banking console. The directly-mapped consoles used to go less far, because each
concatenated array had to fit **one 16 KB bank** (a const array is read in place
while its bank is mapped), and past that the answer was to drop back to plain
`[world] stream`, where each per-scene map is its OWN bank
(`projects/bigworld`).

**That cap is gone as of 2026-08-14**: the concatenation CHUNKS at a bank -
`MAPS`/`MAPS2`, `COLLISION`/`COLLISION2`, a per-scene `MAP_BLK` saying which
and `MAP_OFF` the offset within it, every read through one shared reader. Each
scene's block still has to fit a bank WHOLE (only the concatenation chunks, not
a single map), which is a new hard number in cheat-sheet Part 3. So the GB
family now reaches the same
cell-cap territory as the Lynx, and the per-console ceilings in cheat-sheet
Part 3 are flagged there as needing re-measurement rather than re-guessed. The
`paint_table` **win over plain stream** is unchanged: the removed per-room
*code*, which matters most on the tight Lynx MAIN.

All three are **separate, explicit** `world.toml` opt-ins and deliberately not
auto-linked. Until
2026-08-14 the sharp edge was that `paint_table` could turn a working
plain-`stream` GB build into a "const array > one 16 KB bank" error on a big
multi-console world; the chunking above removed that, so the remaining reason
to choose deliberately is cost, not breakage. Metatiles still quarter the map
bytes, which is the cheaper answer when it applies (no 2x2 block may mix
collision types, and the world must stay under 255 distinct metatiles - the GB
Studio sample needs 944, which is why it chunks instead).

---

## Part 3 - Capacity at a cart size

An early size review (2026-07-08) estimated a 256 KB Lynx cart at "~100-200
rooms" - but that count **ignored the per-scene resident `paint()` code**,
which in practice overflowed the MAIN long before the cart filled ("CODE does
not scale... it is now the wall"). **The paint interpreter removes exactly that
term**, so the cart-bound estimate is now *achievable*.

Measured with a **bare loop** (a walk-with-collision-and-doors game and nothing
else, to isolate the paint interpreter's room-count win from any feature code - the way
`bigworld-paint` was first built, before it grew the feature breadth in Part 2):

| Rooms (20x18) | `stream` only | `stream` + `paint_table` |
|---|---|---|
| ~70 | at the Lynx resident-code wall | fits, lots of headroom |
| **120** | **overflows Lynx MAIN by 4,430 B** | **builds + boots** (87 KB cart archive, .lnx 103 KB) |
| up to 182 | n/a (overflowed long ago) | fits up to the concatenated-cell cap |

So the paint interpreter lifts the *bare-loop* Lynx room ceiling from **~70
rooms** (resident-code-bound, even with the data streamed) to the cell /
scene-id / cart limit. It also removes the archive's block-alignment waste (the
maps are one concatenated blob, not one 1,024-B-aligned asset per room). *Adding
feature code (a full tileset, sprites, menus, strings) spends that headroom* -
the working-tree `bigworld-paint` maxes every other cap at once and so tops out
lower (Part 4, and the ceilings table in cheat-sheet Part 3).

**What did NOT change (still the real binders for a "big" Lynx game):**

- **Resident engine CODE.** Every VM8 *kit* competes for the same budget; "every
  kit at once" (vm-rpg) still needs code levers (opt-in packs, `[build]
  code_banks`, overlays). The paint interpreter buys room *count*, not kit
  *breadth*. `[build] code_banks` on the Lynx now makes cold modules cart
  OVERLAYS (a RAM window at the bottom of RAM, loaded on a miss at ~13 ms a
  KB), but only COLD code can go there: a measured four-room VM8 RPG still
  runs 13.1 KB of engine code per frame, which stays resident (`hot`) with
  the whole gbs runtime beside it. Overlays buy a full VM8 game ~14 KB, not
  the ~40 KB such a game is over; the next binders are the background
  strip ring and the runtime library itself.
- **Dialogue strings** - resident on the Lynx (the single highest-value
  remaining fix).
- **Art variety** - one world tileset across every room is the visible art
  ceiling, and the Lynx sprite tile table is the tightest anywhere.
- **Actor density / heap** - the fixed VM8 pools.
- **Save** - the `platform.save` battery-SRAM engine ships on the GB family
  (`projects/save-demo`); the Lynx and SMS save paths are still to come.

On the **GB family** none of the residency limits bind meaningfully (MBC5 scales
to megabytes, WRAM is plentiful); use plain `[world] stream` for a big GB world.

### What binds on the GB family instead: the resident (bank 0) image

The cart is not the constraint here - **bank 0 is**. The home bank is always
mapped and everything that cannot bank lives in it: the interpreter's hot
path, the prelude helpers, seam-registration stubs, initialized globals, and
any const array a resident function reads. Past the ceiling (cheat sheet,
Cartridge) the build REFUSES the ROM, because bank 1 would silently overwrite
the tail - a black-screen boot rather than an error. **Measure on GBC**: the
colour build of a project runs several hundred bytes heavier than its GB
sibling, so it is the target that says yes or no.

What to do when you reach it, cheapest first:

- **Bank the module** (`[build] code_banks`). Free for anything cold. `vm.core`
  may be listed - it `bank(0)`-pins its own hot path and banks the rest.
  `scripts` is refused (its blob is read per fetched byte).
- **Bank the bytecode blob** (`[build] bank_bytecode`) - the single biggest
  knob, and free in frame-rate terms for an event-script game.
- **Check what is resident with no resident CALLER.** A prelude helper cannot
  bank, so one that only banked code calls used to be pure waste; the codegen
  now emits the metasprite family per TU by use, which is where the last big
  win came from. `tools/projprobe/prelude_audit.py` answers this
  question directly, and the same treatment is still open for the text helpers.
- **Give a const table an accessor in its own module**, so it banks with its
  reader - but only if that module's own readers bank too.
- **Count the SEAM SURFACE before adding a feature**, not its code size. A
  banked entry point whose address is taken costs a resident stub (~8 B); an
  extra argument on a hot indirect call can cost more than the stub saves.
  Two measured attempts to "simplify" seams both came out net worse.

What is left at the project level is content: drop a scene, turn music off,
or fall back from hUGEDriver to `vm.music` (~1.8 KB, the big one, and a
deliberate fidelity loss). Reach for the structural levers above first.

---

## Part 4 - Demos

- **`projects/bigworld`** - the streaming / banking residency proof: 28 rooms
  (32x28 map + collision, ~49 KB) that overflow both the Lynx MAIN and the GB
  home bank resident, and build + run with `[world] stream` (Lynx cart streaming
  AND GB/SMS/GG/NES banking). The directly-mapped, per-scene story.
- **`projects/bigworld-paint`** - the paint-interpreter stream-compose proof,
  extended to push every OTHER per-console hard cap at the same time (a full
  background tileset, the full sprite tile table, 4 actors/room, a pause menu, a
  shop, and unique on-screen strings) to find the REAL simultaneous ceiling.
  **Cross-console:** `target_platforms = ["gameboy", "gameboy_color", "sms",
  "gamegear", "lynx"]` - `paint_table` + `stream` banks the concatenated arrays
  on the GB family / SMS / GG exactly like plain streaming, so ONE source targets
  all five. The default is **44 rooms + 60 codex (104 unique strings)**, the
  largest that builds on all five at once (the Lynx MAIN is the binding target);
  reproduce the Lynx-max run with `BWP_SCENES=63` targeting only lynx. All five
  verified end-to-end (PyBoy GB/GBC full functional: sprites render per room,
  D-pad + door transitions, A opens the shop or a per-room sign, B the pause
  menu, Codex browses with Up/Down; SMS/GG boot on genesis_plus_gx; Lynx on
  Handy). Pushing toward the Lynx max (64 rooms = 257 placed objects) also
  surfaced a genuine latent bug - a silent infinite loop from an unbounded u8
  `OBJ_COUNT`, now enforced at generate time (cheat-sheet Part 3). Plain
  `[world] stream` without the table (`projects/bigworld`) used to be
  the escape hatch for a world past the GB-family bank cap; since the
  concatenation chunks (2026-08-14) that cap no longer binds, so this project
  now demonstrates the plain-stream path rather than being the way out of one.
- **`projects/vm-bigcode`** - the bytecode-blob streaming proof (a script
  blob that overflows resident but fits streamed).

---

## Part 5 - Speed, the other limit

Everything above bounds how BIG a game can be. Frame rate bounds how it FEELS,
it is measured the same way, and since 2026-08-15 it has the same shape: a
budget, a ceiling, and levers. The figures live in
[`cheat-sheet.md`](cheat-sheet.md) "Timing"; the reasoning is here.

**The ceiling is one LCD frame of work: 70,224 cycles.** Cross it and the game
frame costs two LCD frames, so the rate halves in one step - frame rate is a
CLIFF, not a slope, and a change that removes 20% of the work can show up as
nothing at all or as a doubling depending on which side of the line you were.
That is also why a ratio ("we are 2.4x off") misleads: the useful question is
always how many cycles from 70,224.

**It is per ROOM and per ACTIVITY, not per console.** An ordinary room idles at
the full 60 Hz with about 6x headroom and walks at ~31 Hz; the heaviest room in
the GB Studio sample conversion (255 tiles wide, a full 15-actor pool) is roughly 2x
over the line in both. So "the engine runs at N fps" is not a meaningful
sentence, and a per-console figure is not either.

**What to do when you reach it**, cheapest first:

| Lever | What it buys | Cost |
|---|---|---|
| `[build] park_updates` | offscreen actors stop running their On Update scripts, GB Studio's own lifecycle | a semantic change: an offscreen NPC freezes |
| `[build] actor_scan = 4` | the off-window TEST itself is amortised, halving the render pass on a big level | a parked actor wakes up to 3 frames late |
| fewer actors in the room | the per-actor cost is the frame | content |
| a narrower room | the wide column engine is the other big consumer | content |

Past those, what is left is a DESIGN cost rather than a knob: our dense WxH
metasprite fan against GB Studio's sparse descriptor, and the fact that a
banked module's accessors are trampolines. Micro-optimisation does not reach
either, which is why the "not reachable by trimming" rule in the cheat sheet
still stands for the heavy rooms even though ordinary ones now hit 60.

**Movement speed rides all of this**, because the game simulates in VM frames
while GB Studio simulates in LCD frames. It is currently within 4% of the
reference and **must not be rescaled to close that** - a conversion's scale
factor of 2 VM frames per LCD frame is right for a room running at 2.0, the
frame-rate work brought every room near there, and retuning it would break the
common case to serve the outlier. If a heavy room feels slow, take the levers
above.

**Measuring it is where the mistakes are.** The cheat sheet lists the three
traps (a profiler run with no `--nav` measures the title screen; world x is u8
so a long walk wraps; per-regime rates vary between runs because a faster build
reaches different content). Each of them has produced a confidently wrong
number in this repo. Screenshot the frame before believing anything.
