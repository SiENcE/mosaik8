# MosaiK8 numbers cheat sheet

Every hard number a game author hits, with the file that owns it. Modelled on
GB Studio Central's "GB Studio Numbers Cheat Sheet", but where that sheet
describes ONE console, this one describes **nine**, so almost every figure is
per console.

**How to read it.** Two kinds of number live here and they are not the same
thing:

- **Hardware** - what the console can do. You cannot change it.
- **Engine** - what MosaiK8's runtime currently allocates. Some are knobs
  (`mosaik.toml [build]`), some are structural.

Where they differ the engine number is the one that binds, and it is called
out.

**This file owns the numbers.** Why each limit exists, what to do when you
reach it, how the residency levers work and compose, and the worked demos are
in [`capacity-and-limits.md`](capacity-and-limits.md), which quotes figures
from here rather than restating them.

> **The one rule that has no number:** every optional feature is opt-in and
> byte-identical when unused. Nothing below costs you anything until you use it.

---

## Part 1 - The numbers at a glance

### Tiles

- **8x8** - a background tile, every console.
- **8x8** - a sprite tile by default; **8x16** with `[build] obj_8x16`
  (GB family, LCDC bit 2 + a column-major reorder).
- **16x16** - 4 tiles, the common metasprite (2x2).
- **256** - entries in the background tile table (u8 index).
- **255** - distinct tiles a world tileset may hold (`[world] pack_tiles`
  repacks to fit; 256 is the table, 255 the authored ceiling).
- **192** - background tiles the **SMS / Game Gear** actually address
  (192..247 IS the name table, 248..255 IS the sprite attribute table).
  Parking anything at the top of a 256-table corrupts the map there, silently,
  while the GB build looks correct.
- **139** - background tiles a **GB-family program that prints text** may use
  on the resident-font path: the active font's 96 glyphs sit at **139..234**
  (two throwaway font loads pad 0..138; the codegen says ~132, but the run-time
  probe lands at 139 - MEASURED 2026-09-24 in PyBoy on the vm-quest, vm-dialogue
  and vm-rpg GB builds, 'A' = glyph 33 at tile 172). Art above 138 overwrites
  glyphs.
- **256** - background tiles with **no font in VRAM** (`[scenes] glyph_text`
  in the project's `studio.toml`). The font moves to ROM and glyphs
  rasterize on demand. Costs ~1 KB of GB bank 0 and ~6 LCD frames on a cold
  box. Real on GB family + SMS/GG, no-op on NES/Lynx/PCE.
- **946 B / +178 B** - with no project font, the glyph source is GBDK's
  LINKED `font_ibm` (946 B, resident), against the 768 B table the engine
  used to paste: +178 B of resident image, measured on GB and SMS 2026-09-26;
  a banked build that used to bank the table pays the whole 946 B. A project
  font (every GB Studio conversion has one) is unchanged. Owner:
  `mosaik/codegen/gbdk_font.py`.
- **128** - GB OBJ tiles that do not collide with background data
  (0x8000..0x87FF; 128+ lands in the BG table in 0x8800 mode). This is the
  per-room sprite working set.
- **124** - the same ceiling **when a world uses an emote** (the bubble
  reserves 4). On SMS/GG the ceiling stays **128**, because their sprite
  patterns are a separate **192-slot** area (tile ids 256..447) and the emote
  sits at 188.
- **40** - the Lynx / PC Engine sprite tile table (`CC65_MAX_TILES`). On the
  PC Engine a per-room residency world GROWS it to its busiest room's need
  (the rooms generator's `SPR_TILE_NEED`): up to **64** at VRAM `$3000`, past
  that the patterns move to `$5000`, **188** at most (`PCE_MAX_PATTERNS`).
  One GB tile is a whole 16x16 VDC pattern (64 words) there.
- **255** - distinct 2x2 blocks under `[world] metatiles` (u8 metatile id).
  Authored maps fit; procedural noise may not.

### Sprites on screen

- **40** - hardware sprites (OAM objects) on the **GB family**.
- **10** - GB sprites fetched per scanline. The 11th and beyond are dropped.
- **64** - hardware sprites on **SMS / Game Gear / NES**; **8** per line.
- **40** - sprite slots (SCBs) the Lynx / PCE engines allocate
  (`CC65_MAX_SPRITES`), with a **~32 live per frame** soft cap where the
  bundled Lynx cores start blanking. The PC Engine grows it to a residency
  world's busiest room (`SPR_SLOT_NEED`), up to the SATB's **64**.
- **w x h** - OAM objects one metasprite costs (a 2x2 costs 4), HALVED under
  `[build] obj_8x16`. A masked blank column (`sprite.set_meta_mask`) costs
  **0** tiles and parks its objects.
- **2 per placed 8x16 cell** - OAM objects a `[[frame]]` descriptor costs
  (`D_FAN`, 8x8 units), **1** under an effective `obj_8x16`; its tiles are
  **2 per distinct cell**; the generated `D_FAN` is the count. The FIRST
  object of a frame draws on top.
- **67 B** - bank 0 the descriptor (list) machinery costs a banked VM project
  (one kind, GB = GBC, 2026-09-23); **3,118 B** of image when unbanked.
- **16** - largest metasprite in tiles on GB family / SMS / GG / NES
  (`max_metasprite_tiles`); **64** on Lynx / PCE. Both cc65 engines still
  draw a metasprite as **w x h** slots, one per 8x8 tile (an SCB on the Lynx,
  a 16x16 VDC sprite on the PCE): a 56-tile sprite is 56 slots.
- **200** - the y a parked sprite goes to (`GBS_SPR_PARK_Y`). NOT 0: that is
  off-screen only on the GB family, whose OAM is biased by (8, 16), while the
  z80 ports write their SAT directly and (0, 0) is the VISIBLE corner. It also
  avoids the SMS's **0xD0 = 208**, which TERMINATES the sprite list.
- **NPROJ x w x h** - the contiguous OAM block `vm.projectile` reserves for a
  room, where w x h is the launch sheet's CELL (`[projectiles] cell_w/cell_h`,
  at most **2 x 2** tiles, 16 x 16 px). It sizes itself to the space
  left below the actor fans and gives up SHOTS when there is not enough -
  never its base, which would walk backwards over the fans.

### Screen

| Console | Pixels | Text cells | Background map |
|---|---|---|---|
| Game Boy / Color / Pocket / Mega Duck | 160 x 144 | 20 x 18 | 32 x 32 |
| Game Gear | 160 x 144 | 20 x 18 visible | 32 x 28 |
| Sega Master System | 256 x 192 | 32 x 24 | 32 x 28 |
| NES | 256 x 240 | 32 x 30 | 32 x 30 |
| Atari Lynx | 160 x 102 | 20 x 12 | no tilemap (composited) |
| PC Engine | 256 x 224 | 32 x 28 | BAT 128 wide |

- **12** - `isa.MIN_SCREEN_ROWS`, the shortest screen (Lynx / PCE). Any
  coordinate the TOOLCHAIN picks is anchored against it, so it lands on every
  console. An authored coordinate is honoured verbatim.
- **28** - keep scene maps at or under this many rows for a world that targets
  every console (the SMS/GG name table is the shortest background).
- **32 x 28** - the default scene size (256 x 224 px).

### Raster effects (the per-scanline scroll table)

`bkg.raster*` (reference `raster-effects.md`). Real on the GB family (x and
y) and SMS / Game Gear (x only); a no-op on NES, Lynx and PCE.

| | GB family | SMS / Game Gear |
|---|---|---|
| Lines in the table | 144 | 192 (SMS), 144 (GG; screen line 0 = VDP line 24) |
| Playback | **53** machine cycles a line from `first` down, both scroll writes done **31** in | the lines from `first` down, WHOLE (the handler stays in the interrupt) |
| Playback, table on the lower 80 lines | **24 %** of a frame (**12 %** at GBC double speed) | about **30 %** on Game Gear |
| `raster_curve` | **41** machine cycles a line | **142** T-states a line |
| `raster_stripes` | **24** machine cycles a line | no-op |
| The same road loop in compiled C | about **210** machine cycles a line | |

- **1** - owners of the GB STAT vector while the table is armed: it cannot
  be combined with `bkg.parallax*`, `text.win_sprite_cut` or
  `text.win_overlay_cut` (a compile error).
- **2** - `system.cpu_fast` (GBC only) multiplies TIMER-clocked rates by two;
  v-blank-clocked ones are unchanged.
- **32** - helper calls a tile for `bkg.set_data` on SMS/GG 4bpp (the
  run-time packed-to-planar conversion `bkg.set_data_native` skips).

### Colour

- **4** - colours per tile, the portable model (GB 2bpp, the universal
  interchange format every PNG converts to).
- **16** - colours per tile on the **4bpp tier**, entered automatically by a
  more-than-4-colour indexed PNG.
- **0** - sprite colour index that is transparent, every console.

| Console | Sprite tile | Background tile | Palette slots (bkg + spr) | On-screen colour |
|---|---|---|---|---|
| Game Boy, Mega Duck | 4 greys | 4 greys | 1 + 2 | 4 greys |
| Game Boy Color, Pocket | 4 | 4 | 8 + 8 | RGB555 |
| NES | 4 | 4 | 4 + 4 | 54-colour master |
| Sega Master System | 4 | **16** | 1 + 1 | 6-bit CRAM (64) |
| Game Gear | 4 | **16** | 1 + 1 | 12-bit CRAM (4096) |
| Atari Lynx | **16** | **16** opt-in | 1 + 4 | 12-bit (4096), 16 pens |
| PC Engine | **16** | **16** | 4 + 4 | 9-bit (512), 16 palettes |

Palette slots are the mosaik model's `graphics.palette` slots
(`PLATFORM_CAPS`), not the raw hardware register count. Slot 0 is the portable
guarantee. 4bpp backgrounds are always-on on PCE + SMS/GG and per-project on
the Lynx (`[world] lynx_bkg16`, because its strip buffers double); GB/GBC/NES
backgrounds stay 2bpp and down-tier by luma **rank**, not absolute threshold.

The **colour tier** (per-tile background palettes) is separate and composes
with everything: the attribute is per TILE, so a tile used under two palettes
simply exists twice.

**A per-FRAME sprite palette** (a clip's `pal` / `pal_<facing>` in
`studio.toml`) is one byte in GB Studio's own
metasprite-props encoding, owned by `mosaik_anim.py` -> the generated `F_PAL`:

- **bits 0-2** - the CGB OBJ palette (0-7).
- **bit 4 (0x10)** - draw through **OBP1** on a DMG build. Independent of
  bits 0-2.
- **255** - NO palette authored: the animator stands down and the room-load
  colour (the per-cell map or the kind's slot) survives. It cannot be 0 -
  0 is a real palette, and writing it flattened every coloured actor once.

The write is the portable `sprite.set_palette`, so every console shows what it
can: GBC/Pocket 0-7, DMG/Mega Duck the OBP pair, NES/Lynx/PCE their 4 slots
(the value folds `& 3` there - author 0-3 when they are targets), SMS/GG
nothing (one sprite palette). Where a kind also has a per-CELL layout, the
layout wins.

### Text and dialogue

- **lines + 2** - rows a dialogue box occupies (border + text + border), GB
  Studio's own geometry, bottom-anchored.
- **3** - lines `render_text` draws.
- **~15** - columns to keep a line within so it fits the narrowest screen.
- **256** - unique strings per project (u8 string id). Resident on the Lynx,
  which makes this the highest-value remaining Lynx fix.
- **96** - glyphs in a custom font (`text.set_font`).
- **192** - the SMS/GG bound on a relocated font (`set_font_at`:
  `base + 96 <= 192`).
- **0..95** - the SMS/GG resident console font's tiles: UNDER every scene
  tileset (which also loads from 0), so a VM world with a real tileset needs
  `[scenes] glyph_text` (`mosaik/codegen/gbdk_text.py`).
- **52** - `CC65_NAME_MAX`: a module-level C name longer than this is
  shortened + hashed on the cc65 consoles, whose compiler keeps 64 characters
  of an identifier (`mosaik/codegen/gen_modules.py`).
- **160 words / 340 B of RAM** - the PCE VCE shadow behind a real
  `palette.fade` (BG palettes 0-5, sprite palettes 0-3), only in a program
  that fades (`mosaik/codegen/cc65_palette.py`).

### Cartridge

- **16 KB** - one ROM bank.
- **16,384 B** - the RESIDENT (bank 0) image ceiling on the GB family, and the
  one budget a big game actually runs out of (`mosaik8_targets.gbdk_resident_end`
  reads it from the link map; the build refuses a ROM past it, because bank 1
  would silently overwrite the tail = a black-screen boot). **The GBC build of
  a project runs ~945 B heavier than its GB build**, so GBC is the meter.
  Read the live figure off the build's link map, never a remembered number.
  Reasoning + the levers: [`capacity-and-limits.md`](capacity-and-limits.md)
  Part 3.
- **A prelude helper is RESIDENT and cannot bank**, so the codegen emits the
  metasprite family PER TU, only into TUs that call it. Two consequences
  worth remembering: a helper only banked code calls costs bank 0 nothing now,
  and **`static inline` will not achieve that for a loop-shaped body** - sdcc
  emits an unused one as dead code in every TU (measured 236 B WORSE).
- **8 KB** - one cart-RAM bank.
- **32 KB, 64, 128, 256, 512 KB, 1, 2, 4, 8 MB** - selectable `rom_size`
  (auto-sized by default to the highest bank used; a too-small explicit value
  is a hard error, never a silent resize).
- **0, 8 KB, 32 KB, 128 KB** - selectable `ram_size` (auto-enables with save).
- **8192** - `save_bytes` on the GB family (MBC5 battery SRAM). Every other
  console is honest-off: calling `save.*` there is a compile error.
- **3 slots of 264 B = 792 B** - the VM8 save blob (`vm.sram`: an 8-byte header
  plus 128 i16 heap cells, per slot), so GB Studio's three slots fit the one
  8 KB bank with no bank switching. The stride is `OFF_HEAP + NCELLS * 2`, not
  a constant to copy.
- **16 KB** - one PC Engine ROM bank under `[build] code_banks`: ONE window
  at `$4000-$7FFF` (MPR2 + MPR3), logical bank k = physical banks 2k+2 /
  2k+3 behind the unchanged 32 KB resident image; the HuCard grows to the next
  power of two, **1 MB** at most without a mapper. **6 B** - the resident
  thunk per banked cc65 function.
- **32 KB** - the PC Engine HuCard we link (`-D__CARTSIZE__=$8000`); larger
  needs the image rotated or the cart white-screens.
- **512 B** - the Lynx C stack (`[build] lynx_stack_size`).

---

## Part 2 - VM8 (the event-script runtime)

Uniform on **every** console, pinned to the leanest target so one blob runs
everywhere (`docs/vm8-spec.md` §14, `lib/vm/core.mos`, `mosaik_vm/isa.py`).

| Number | What |
|---|---|
| **128** | heap variables (i16 cells). Also the `save` payload. |
| **8** | concurrent threads (`VM_CTXS`) |
| **16** | instructions per thread per frame (`QUANT`); **512** via `[build] vm_quant` |
| **8** | expression-stack cells per thread (`VM_STACK`), statically verified at compile |
| **4** | nested subroutine calls per thread (`CALL_DEPTH`) |
| **4** | arguments per thread (`NARGS`) |
| **4** | timers (`TIMER_SET` ids 0..3) |
| **4** | MUSIC ROUTINE slots (`MUSIC_ROUTINE`, GB Studio's own four). A `6xy` cell names one in the low two bits of its parameter's low nibble and hands the high nibble over as `arg(0)`. Both drivers since 2026-09-23: hUGEDriver's `6xy`, and vm.music's effect **15** on every console |
| **8** | concurrent input attachments (one per portable button). **Not every console has all eight**: the SMS pad has no Select at all and no Start bit (its Start is the console's PAUSE button, an NMI - and optionally pad button 1, `[build] sms_start_button`); the Game Gear has a real Start but no Select. A script bound to a button a target has not got simply never fires there |
| **65,535** | script blob bytes (u16 PC), roughly 9,000 to 16,000 authored events |
| **256** | unique strings (u8 id) |
| **255** | switch cases per `switch` |
| **1..255** | menu options |

### Pools

| Pool | Default | Knob | BSS cost |
|---|---|---|---|
| Actors per room | **8** | `[build] actor_pool` | 29 B/slot |
| Triggers per room | **8** | `[build] trigger_pool` | 9 B/slot |
| Projectiles in flight | **8** | fixed (`vm.projectile`) | |
| Combat enemies (`vm.combat`) | **4** | fixed | |

Actor ids in bytecode are **0..7** by default and validated by the assembler.
`generate_rooms` **refuses** a room with more placed objects than the pool
(per console, exactly, when objects carry `platforms` tags): both runtimes
drop extras silently, so that refusal is the only guard.

### Timing

- **1 VM frame is not 1 display frame**, and the ratio is **per room and per
  activity**, not per console. Frame-counted bytecode needs a shell-seeded
  per-console scale. Measured on the GB Studio sample conversion (DMG,
  2026-08-15, after the offscreen parking + `actor_scan` work):

  | room | idle | walking |
  |---|---|---|
  | a normal gameplay room (a parallax scroller) | **1.00** | **1.94** |
  | the worst case (255 tiles wide, 15 actors) | **2.12** | **2.18** |

  Elsewhere: **~1** on the PC Engine, **~3** on the Lynx (**2** with
  `[build] lynx_code_resident`).
- **A fade steps every 2^speed LCD frames** (`tools/projprobe/
  fade_style_probe.py`, 2026-09-15, on the reference ROM): GB Studio's
  `fade_speeds[] = {0, 1, 3, 7, 15, 31, 63}` is a frame MASK, so its default
  scene fade (speed 1) holds **2** frames per step and a fade event's default
  (speed 2) **4**; the ramp is **3 visible steps** (`E4 -> 90 -> 40 -> 00`
  towards WHITE, its default direction; `E4 -> F9 -> FE -> FF` towards black).
  Ours: `[scenes] fade` = `1 << autoFadeSpeed` LCD frames per step, a FADE op's
  `frames` = `vm_frames(4 << speed)` (one level per quarter), direction from
  `[scenes] fade_style` / state 32. Owner: `lib/vm/fx.mos`.
- **The window OVERLAY's cut line defaults to 150, which is OFF SCREEN**
  (gbvm's `LYC_SYNC_VALUE`; the display is 144 lines). So GB Studio's own
  default for `overlay_cut_scanline` means NO cut, and ours takes the same
  number for the same reason - a `[scenes] overlay_cut` or `set_state
  overlay_cut` of 144 or more DISARMS it. Its event clamps 0..150. **The cut
  takes hold on the line AFTER the one it names** (both engines spin for
  H-blank before writing LCDC), which is parity, not drift. Owner:
  `mosaik_vm/isa.py` (`OVERLAY_CUT_OFF`), `mosaik/codegen/gbdk_lyc.py`.
- **60 Hz is reached at idle in an ordinary room** and not while walking
  (~31 fps there). The old "not reachable by trimming" rule still holds for the
  heavy rooms: the target is total work under **70,224 cycles** (one LCD
  frame), and the worst-case room sits at **83,621** idle / **141,189** walking, so
  its remaining gap is the per-actor DESIGN cost (our dense WxH metasprite fan
  vs GB Studio's sparse descriptor, plus cross-bank accessor trampolines), not
  something trimming reaches.
- **96%** - our walk speed against GB Studio's own ROM, driven into the SAME
  room and sampled per second: **~90 px/s** vs **~94 px/s**. A conversion
  that assumes 2 VM frames per LCD frame is very nearly right, because walking
  measures 1.94 to 2.18. **Do not rescale movement** - it would break the
  common case to serve the outlier.
- **Two traps that produce confident wrong answers here**, both paid for twice:
  `emu/gb_profile.py` with no `--nav` measures the **"PRESS START" title
  screen** (it reads a meaningless 1.00 with 12 K cycles of work; the sample
  conversion needs `--nav start,a`, the reference ROM `start,a,a`) - always
  `--shot` and LOOK; and a player's world x is **u8**, so a hold longer than
  ~256 px WRAPS (a 240-frame walk read as 20 px/s instead of 90) - sample per
  second. Per-regime RATE figures also vary between runs by more than a single
  lever is worth reading into, because a faster build reaches different level
  content in the same frame count: compare the STAGE you changed.

### Combat, aiming, emotes

- **256** - angle units in a full turn. **0 = up, clockwise** (64 right,
  128 down), y in screen sense. GB Studio's convention exactly.
- **1/16 px** - the unit a projectile's velocity is expressed in
  (`launch16` / `launch_angle`). Whole pixels quantise a shot to about a dozen
  slopes; at speed 3 the worst gap between two aimable directions is
  **18 degrees**.
- **0xFE** - a projectile's default collision mask (every enemy group, not the
  player). **1** is `PLAYER_GROUP`, **2** is the default `GROUP_ENEMY`. A hit
  needs `group & mask`.
- **20** - PLAYER i-frames after a body-contact hit (`vm.entity.HURT_IFRAMES`,
  GB Studio's `PLAYER_HURT_IFRAMES`). ONE counter on the player, not per actor:
  standing between two enemies cannot take two hits on one frame. The per-actor
  `HIT_DEBOUNCE` (**8**) is a different job - it stops a burst of shots
  restarting one actor's death script.
- **ARG 0** - what hit an actor, read by its On Hit script: **0** = the
  PLAYER'S BODY, otherwise the SHOT's own collision group BIT. That is GB
  Studio's own encoding, and its compiler folds the per-group On Hit tabs into
  one script behind `if (ARG0 == n)`.
- **4 / 8 / 15** - the converted knockback: px per VM frame away from the
  facing, px upward, and frames the pad stays locked out
  (`studio.toml [player] knockback_x/_y/_frames`, from GB Studio's
  `plat_knockback_vel_x/_y` 8192/16384 and `plat_knockback_frames` 30).
- **4** - clip STATES per kind (`clips.mos`: idle / walk / jump / fall). An
  ACTOR's derived state only ever reaches idle or walk, so **2 and 3 are free**
  for a script to pin - which is where a GB Studio sprite's named STATES import
  to. The PLAYER uses all four.
- **1** - emote bubbles on screen at a time (GB Studio has a single
  `emote_actor`).
- **60** - frames an emote lives; **15** of them walk the bounce offset table;
  **16 px** the default lift above the actor.

---

## Part 3 - Scenes and worlds

| Cap | Value | Why |
|---|---|---|
| Scenes per world | **255** | u8 scene id |
| Placed objects per world | **255** | u8 `OBJ_COUNT` |
| Doors per world | **255** | u8 `DOOR_COUNT` |
| Triggers per world | **255** | u8 `TRIG_COUNT` |
| Background tiles per world | **255** | one shared tileset for all rooms |
| Distinct 2x2 metatiles | **255** | u8 metatile id |
| Replacement tiles per world (`[[replace_tile]]`) | **255** | u8 `src` operand of `BKG_TILE`; the bank is baked, not uploaded, so it costs no background VRAM |
| Concatenated map cells (`paint_table`) | **65,535** per array | u16 `MAP_OFF` |
| Map cells in ONE scene (`paint_table`) | **16,384** | a const array may not cross a ROM bank, and only the CONCATENATION chunks - one scene's block cannot |

All four `*_COUNT` caps are **enforced** (`_check_u8_count`). They were not
always: a world with 257 objects emitted `const OBJ_COUNT: u8 = 257`, which
survived into C as `uint8_t i; i < 257` - a silent infinite loop with no
warning and no link error.

Scene dimensions **widen to u16 past 255** per axis, so a 300-wide level is
fine. Rooms past **32 columns** column-stream automatically
(`engine.scroll`); a wide **and** tall topdown room 2D-streams
(`engine.scroll2d`). Wide/roam is derived per room from its own size, never a
flag, and a wide room must not be painted.

**Width costs 1,474 B of the resident image** (measured on the 17-scene
GB Studio sample conversion, the same figure on GB and GBC: GB 13,747 ->
15,221, GBC 14,692 -> 16,166) - `engine.scroll` links and the wide arm plus
`scenes.warm()` are seam-pinned resident. A world with no room wider than the
screen does not pay it.

At 65,535 concatenated cells that is roughly **73** rooms at 32x28, **182** at
20x18, **252** at 20x13.

### The three residency levers

| Lever | `world.toml` | What it buys |
|---|---|---|
| Streaming / banking | `stream` | map + collision leave the resident image (Lynx cart LRU, GB MBC5 / Sega / UNROM banks) |
| Paint interpreter | `paint_table` | removes **90 to 140 B of resident CODE per room**; adding a room adds table rows |
| Metatiles | `metatiles` | **map bytes / 4** (-68% measured); collision rides the metatile where no 2x2 block mixes types (-77% combined) |

They are separate, explicit opt-ins and they compose. Measured room ceilings
for a 20x18 world with a rich feature set (`projects/bigworld-paint`):

| Console | Mechanism | Rooms |
|---|---|---|
| Atari Lynx | cart archive window, no per-bank cap | **63** |
| GB / GBC / SMS / GG | concatenated arrays bank; the concatenation CHUNKS at 16 KB | **> 45**, not re-measured |
| PC Engine | resident 32 KB HuCard (`[build] code_banks` banks it, not re-measured) | **~24** |
| NES | `printf` overflows NROM | excluded |

A **bare** walk-with-doors loop on the Lynx goes from **~70** rooms
(resident-code-bound) to the cell / cart limit with `stream + paint_table`;
120 rooms builds and boots (87 KB cart archive, 103 KB `.lnx`).

**The GB-family row is stale on purpose, not guessed.** Its **45** was the old
one-symbol bank cap read backwards (45 x 360 cells = 16,200, just under
16,384), and that cap is gone: the concatenation now chunks into
`MAPS`/`MAPS2`/... with a per-scene `MAP_BLK` (2026-08-14). The measured
proof it no longer binds is the 17-scene GB Studio sample conversion at
**18,288 concatenated cells** linking on
gameboy, gameboy_color, sms and gamegear - past the old ceiling. What replaces
45 is whichever binds first of the 65,535-cell u16 cap, the cart size and the
resident image; nobody has re-run the `bigworld-paint` campaign to find it, so
no number is quoted here rather than an invented one.

Plain `stream` without `paint_table` is no longer the escape hatch for a big
GB-family world - it was only ever needed because of that bank cap.

---

## Part 4 - The Atari Lynx, where the real ceiling is

The Lynx has **no tilemap**. The background is composited into Suzy literal
buffers in RAM, and those buffers, not the cart and not the tile count, cap a
Lynx game.

- **48,184 B** - MAIN, the ONE area holding CODE + RODATA + DATA + BSS.
  `lynx.cfg` defines it as `$BE38 - __STACKSIZE__`, and **mosaik8 always passes
  the stack explicitly** (`-D__STACKSIZE__`, from `[build] lynx_stack_size`,
  default **512 B**) - so the figure follows that knob and is 48,184 B by
  default. cc65's own cfg default of $0800 would give 46,648 B, which is what
  this line used to quote; no mosaik8 build has ever linked that way.
- **~21 to 24 KB** - resident VM8 engine code before any game content.
- **~19 KB** - `graphics.bkg` RAM when imported.

| Background engine | When | Buffer cost |
|---|---|---|
| ROW strips | every room 32 tiles wide or less | **8,720 B** at `STRIP_W` 33 |
| WIDE columns | any room past 32 tiles | **14,112 B** (32 columns x 110 lines x 4 + 1) |

**4bpp doubles the per-line bytes**, so the row engine goes 8,720 to
**17,168 B**. That single number decides whether a room can be 16-colour.

Measured on the 3-scene GB Studio Lynx slice (32x18 parallax room, 187 tiles)
sitting at 47,474 B of MAIN with **529 B spare**:

| Ask | Cost | Fits? |
|---|---|---|
| one more scene | ~1.4 KB | no |
| the same room at its full 80-tile width | +8,046 B | no |
| the same room in 16 colours | +10,520 B | no |

**The practical rule for Lynx colour: a ~20-tile non-scrolling room can be
16-colour; a 32-wide scrolling one cannot.** `projects/vm-bkg16` proves it
works (12 colours on screen, 35,777 B used, 12,407 B spare) precisely because
its room is screen-width with a 4-tile tileset. **Budget a Lynx room's width
before its art.**

Other Lynx figures worth knowing: a cart page **miss** costs **~98,000 ticks**
(about one per thread slice), which is why `lynx_code_resident` exists; a
homebrew cart is **256 KB**, the size a Lynx cart archive is budgeted against.

**Code overlays** (`[build] code_banks` on the Lynx): a cart read runs at
**~13.3 ms a KB** plus **~5 ms** a load (Beetle), so an 8 KB overlay is ~6-7
frames: only cold code goes there, per-frame code is `hot` or `bank(0)`. The
window is the largest overlay and comes off MAIN; the loader is **+195 B**
over the `lseek` / `read` a streamed build already links (cc65's own
`lynx_load` is **+675 B**: its `open` pulls in `atoi`, the ctype table and
errno). Measured on a four-room VM8 RPG: **13.1 KB** of its engine code runs
per frame (7.5 KB of it in field, dialogue AND battle alike), so a full VM8
game keeps ~21 KB of resident code plus the ~8.5 KB gbs runtime whatever it
overlays. **Sprite sheets** streamed from the cart under `[world] stream`
free their whole RODATA (16 sheets of a 56x64 foe: **14,336 B**).

---

## Part 5 - Audio

Songs are DATA. The driver speaks hUGETracker semantics: **15** effects (+ effect
**15** CALL ROUTINE, hUGE `6xy`, since 2026-09-23), a u16 row counter, per-song
voicing flags, custom wavetables and instrument SUBPATTERNS (GB family; Lynx /
SMS / GG on their melodic slots).

**Audio MODE is per song.** The default (**game**) keeps one physical channel
free so a sound effect never interrupts music. **Music-only** hands that
channel to the music.

| Console | Chip | Game mode | Music-only |
|---|---|---|---|
| Game Boy family | APU, 4 channels | pulse 1 + wave + noise; pulse 2 = SFX | + pulse 2 |
| Atari Lynx | Mikey, 4 channels | B + C melodic, D noise; A = SFX | + A (3 melodic) |
| SMS / Game Gear | SN76489, 3 tone + noise | tone 1 + 2 melodic, noise; tone 0 = SFX | + tone 0 |
| NES / PC Engine | | degrade | degrade |

- **64 Hz** - the hUGEDriver tick rate (`[audio] huge_hz`), GB Studio's own.
  It must be a **timer interrupt**, not a game-loop call: ticking per VM frame
  collapsed tempo to **38%** while walking.
- **~1.8 to 2 KB** - resident bank-0 cost of hUGEDriver. It is a **fidelity**
  feature, not a capacity one: our own driver banks down to 367 B resident.
  GB family only.
- **15** - hUGE instruments per kind, **per song**.
- **32** - rows in an instrument SUBPATTERN (hUGE v6 table), one per driver
  tick; row **31** returns to row 0 unless it jumps (hUGETracker's codegen).
  Pitch offset **-36..+35**. **5 B** a row in `instruments.mos` (only the rows
  up to the last used one), **96 B** a table on the hUGEDriver path (32 `DN`
  cells). The vm.music table machinery costs **1,222 B** (GB, measured on a
  32 KB fixture) and exists only when an instrument plays a table
  (`VM_MUSIC_SUBPAT`); `mosaik_vm/instruments.py`, `lib/vm/music.mos`.
  On the pooled consoles (melodic slots; since 2026-09-23) a tabled song costs
  **+849 B of Lynx MAIN** (~653 B machinery + accessors and table data) and
  **+699 B** of the SMS / GG image (unbanked `vm-music`), **+593 B** in vm.music's
  code bank on the banked SMS/GG sample conversion (bank 0 unchanged).
- **Lynx MAIN headroom for music**, measured 2026-09-23 over all 67 Lynx-targeting
  projects: median **7,873 B** free. vm.music itself with `vm-music`'s small songs
  is **4,140 B**, so a game WITHOUT music needs **~5 KB** free to add a tabled
  song: 13 of the 67 have less; `vm-combat` (682 B) and `bigworld-paint` (787 B)
  could not even take the table code. Ruler: re-link with the build's cl65 line
  plus `-m` and sum the MAIN segments (exact to the byte: +1 B of stack = the
  overflow).
- **4 / 3** - vm.music's CALL ROUTINE queue: a 4-slot single-producer ring, 3
  usable; a FULL ring drops the NEWEST byte (the hUGE pack's C ring drops the
  oldest). Queued once a ROW. `lib/vm/music.mos` `m_rtq`.
- **6** - the current `.uge` version (hUGETracker's `UGE_FORMAT_VERSION`;
  the reader takes v1..v6); a v6 instrument is **1,385 B** (TInstrumentV3,
  with a FIXED 64-cell subpattern), a v1/v2 file has ONE 15-slot bank and
  33-byte waves, and only v5+ store a pattern KEY. `mosaik_vm/uge.py`.
- **16,384 B** - the HARD ceiling on ONE song's cell data
  (`mosaik_vm/songs.py` `CELL_CHUNK`). A song's interleaved block is a
  const array read IN PLACE while its single ROM bank is mapped, so it can
  never cross a bank; past this the whole library refuses to generate, on
  EVERY console (the Lynx does not escape it). One row costs
  `1 + 5 x channels` bytes, so a 4-channel song caps at **780 rows**. The
  LIBRARY has no such limit - it is chunked one `CELLS<n>` symbol per
  16 KB. Any per-cart song BUDGET a tool applies on top is a choice about
  how much cart to spend; this one is not negotiable.
- **vm.music is ticked by the GAME LOOP**, catching up against a free-running
  display-frame counter (cap **8** frames, `MUSIC_CATCHUP_MAX`). So anything
  that BLOCKS for many display frames inside one VM frame - a room load, a
  box-close repaint - stops the song for its duration unless it PUMPS
  (`core.music_pump()`, emitted through the generated room load). Measured on
  the converted sample: longest silence **39 -> 18** display frames. hUGEDriver
  does not have this at all, because its tick is an interrupt.

---

## Part 6 - The knobs (`mosaik.toml`)

The `[build]` keys below are the ones the build applies (the full reference,
with `[project]`, `[source]`, `[assets]` and `[lib]`, is §5.1 of
`mosaik_lang_spec.md`); anything else warns per build.

| Key | Default | Effect |
|---|---|---|
| `[build] shake_exports` | off | declaration-level tree-shaking |
| `[build] code_banks` | none | listed modules' code leaves the resident image, one bank each. `scripts` is refused; `vm.core` banks its cold half only (it `bank(0)`-pins its own hot path) |
| `[build] bank_bytecode` | off | banks the bytecode blob: **+2,728 B** of bank 0 |
| `[build] rom_size` / `ram_size` | auto | cart geometry |
| `[build] obj_8x16` | off | 8x16 OBJ mode, GB family |
| `[build] actor_pool` / `trigger_pool` | 8 / 8 | VM8 slot pools |
| `[build] vm_quant` | 16 | raise to 512 for a critical section spanning a CALL chain |
| `[build] park_updates` | off | GB Studio's actor lifecycle: an offscreen actor's On Update thread is killed and RESTARTS FROM THE TOP when the camera reaches it. A SEMANTIC change (an NPC patrolling off screen freezes), so opt-in; every GB Studio conversion sets it. Also what lets a room hold more scripted actors than the 8 thread contexts |
| `[build] actor_deactivate` | off | GB Studio's offscreen DEACTIVATION: an actor the camera left behind leaves the list every per-frame pass walks, so it does not auto-move, collide or get hit until the camera brings it back. Semantic, so opt-in |
| `[build] frame_lock` | 1 (off) | hold one game frame to at least N display frames (2..8): a uniform game SPEED at the cost of the rate of rooms that were faster. A FLOOR, not a ceiling |
| `[build] proj_scan` | 1 | collide a shot 1 frame in N (1/2/4/8, GB Studio uses 4); movement and lifetime still run every frame, so a fast shot can pass through a thin target |
| `[build] proj_under_lock` | off | projectiles keep flying under a cutscene LOCK, as GB Studio's do |
| `[build] move_lcd` | off | pace a native auto-move by display frames; kept for parity, and OFF for good (partial pacing broke the balance) |
| `[build] actor_scan` | 1 | re-test a PARKED actor against the visible window one frame in N (2/4/8; GB Studio uses 4). A VISIBLE actor is always tested, so nothing is drawn stale - it only decides how late a parked one wakes |
| `[build] sms_start_button` | off | on the MASTER SYSTEM, pad button 1 also answers `J_START` (the pad labels it "1 START"). A per-project CHOICE, not a console fact: the two are one bit to a program, so a script attached to both `a` and `start` fires TWICE on one press. The console's PAUSE button is a real Start either way (an NMI, always latched) |
| `[build] lynx_stack_size` | 512 | Lynx C stack, traded against MAIN |
| `[build] lynx_bkg16` | off | 4bpp background for a hand-written Lynx game |
| `[build] lynx_code_resident` | off | pin the bytecode blob in MAIN |
| `[build] bkg_max_tiles` / `bkg_strip_w` / `sprite_max_tiles` | auto for VM8 games | Lynx BSS budgets |
| `[build] sprite_max_slots` | 40 | Lynx / PCE sprite SLOT budget (1..40); a game with no projectiles needs 9. Never auto-derived |

The `[world]` flags (`stream`, `paint_table`, `metatiles`, `lynx_bkg16`) live
in `world.toml`; `[scenes] glyph_text` and the other room-load knobs in the
project's `studio.toml`, which the room generator reads. So does the music
driver: `studio.toml [audio] gb = "huge"` wires hUGEDriver on the GB family
(default: our own driver).

---

## Part 7 - Where each number lives

| Numbers | Source of truth |
|---|---|
| Per-console capabilities, palette slots, bpp, metasprite cap, save | `mosaik/platforms.py` (`PLATFORM_CAPS`) |
| Toolchain flags, ROM extensions, cart geometry | `mosaik8_targets.py` |
| The RESIDENT (bank 0) image, per target | `mosaik8_targets.py` (`gbdk_resident_end`, the link-map read the build's own overlap check uses) |
| Which prelude helpers a TU carries | `codegen/gbdk_metasprite.py` (`_meta_helper_needs`, the per-TU use scan) |
| Screen sizes | GBDK `DEVICE_SCREEN_*` per port; `CC65_PROFILES` in `mosaik/codegen/cc65.py`; the room-fit table in `mosaik_vm/rooms/config.py` |
| OAM slot counts, sprite tile tables | `codegen/gbdk_metasprite.py` (`GBS_META_SLOTS`), `codegen/cc65.py` (`CC65_MAX_TILES` / `CC65_MAX_SPRITES`) |
| The parked-sprite y | `codegen/gbdk_metasprite.py` (`GBS_SPR_PARK_Y`) |
| Raster table geometry and handler timing | `codegen/gbdk_raster.py` (`RASTER_GEOMETRY`, the GB / SMS handlers and fills) |
| Projectile pool + its OAM block | `lib/vm/projectile.mos` (`NPROJ`, `OAM_MAX`, `set_cell`/`set_top`) |
| A shot's own GROUP vs its MASK | `lib/vm/projectile.mos` (`p_group` / `p_mask`, the `PROJ_GROUP` latch) |
| Contact-hit i-frames + the On Hit debounce | `lib/vm/entity.mos` (`HURT_IFRAMES`, `HIT_DEBOUNCE`) |
| Knockback tuning | `mosaik_vm/rooms/config.py` (`knockback_x/_y/_frames`) -> `lib/vm/player.mos` (`set_knockback`) |
| Clip STATES per kind, and which are free | `lib/vm/canim.mos` + the generated `clips.mos` (`ST_IDLE`..`ST_FALL`) |
| SMS Start (pad button 1 + the PAUSE NMI) | `codegen/gbdk.py` (`GBS_PAD_MASK`, `NMI_ISR`, `GBS_SMS_PAUSE_FRAMES`) |
| VM8 sizing | `lib/vm/core.mos` + `mosaik_vm/isa.py` + `docs/vm8-spec.md` |
| Heap / string caps | `mosaik_vm/compiler.py` (`HEAP_CAP`, `STRING_CAP`) |
| One song's HARD cell-block ceiling | `mosaik_vm/songs.py` (`CELL_CHUNK`) |
| Scene / world caps | `mosaik_scenes/transpile/context.py` (`_check_u8_count`) |
| Lynx buffer maths | `mosaik/codegen/cc65_bkg.py` |

**Two numbers that are easy to conflate**, because they are both "about 32 to
40" and both about Lynx sprites, but they measure different axes:

- **40** - entries in the sprite TILE table (`CC65_MAX_TILES`). How much sprite
  ART a build can address. Past it, `sprite.set_data` drops tiles and the
  compiler warns.
- **~32** - sprite SLOTS that can be LIVE in one frame before the bundled cores
  start blanking (the table holds 40, `CC65_MAX_SPRITES`). A rendering
  throughput limit, nothing to do with how much art fits.

A tool that meters either one should import `CC65_MAX_TILES` /
`CC65_MAX_SPRITES` from `mosaik/codegen/cc65.py` rather than copy the number:
the tile table was once raised from 32 to 40, and a copied literal stayed at 32.
