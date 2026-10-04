# VRAM tile layout (GB family / SMS / Game Gear / PC Engine)

The single source that describes *where* each kind of tile lives in VRAM, and how
much of each a game can afford at once. This is MosaiK8's answer to GB Studio's
per-scene tile budget (https://www.gbstudio.dev/docs/project-editor/scenes/limits):
GB Studio reserves fixed ranges and caps a scene at 192 background tiles. MosaiK8
used not to partition VRAM at all, so a project could silently over-subscribe it
(the symptom: `bigworld-paint`'s 255-tile background overwrote the font, so dialogue
and menus showed no letters). Today the compiler enforces the parts it can read:
it refuses a resolvable `bkg.set_data` past tile 191 on SMS/GG
(`mosaik/codegen/gen_budgets.py::_check_smsgg_bkg_range`), derives the glyph band
from the program's own uploads (`_resolve_glyph_band`, see "The glyph band"), and
the VM8 rooms generator caps sprite tiles per console (see the ceilings below).
The studio's budget view meters the same numbers per target before a build. This doc
is the reference all three read from.

## Game Boy (DMG) — one 8 KB bank

Tile RAM is `0x8000-0x97FF` = **384 tile slots** (16 bytes each), in three 128-tile
blocks (https://gbdev.io/pandocs/Tile_Data.html):

| Block | Address       | Sprites (OBJ)      | Background / Window       |
|-------|---------------|--------------------|---------------------------|
| 0     | `0x8000-0x87FF` | ids 0..127 (base 0x8000) | ids 0..127 *(only in 8000 mode)* |
| 1     | `0x8800-0x8FFF` | ids 128..255       | ids 128..255 (shared)     |
| 2     | `0x9000-0x97FF` | —                  | ids 0..127 *(8800/signed mode)* |

Sprites (OBJ) **always** use unsigned addressing (base `0x8000`, blocks 0+1).
The background uses one mode via `LCDC.4`:
- **8000 (unsigned)**: BG ids 0..255 in blocks 0+1 — aliases the sprite tiles.
- **8800 (signed, GBDK default)**: BG ids 0..127 in block 2, ids 128..255 in block 1.

The productive layout is **8800 mode with sprites confined to block 0**: sprites get
block 0 (128), the background gets blocks 1+2 (256), and all 384 slots are usable at
once. Whatever font/frame the game uses lives *inside* that background 256.

> **Probing a background tile in an emulator: the address is not `0x8000 + id *
> 16`.** In 8800 mode — which is what this engine runs — background id *n* is at
> `0x9000 + n * 16` for 0..127 and `0x8800 + (n - 128) * 16` for 128..255.
> Reading `0x8000 + n * 16` samples SPRITE slots, so a test that checks whether
> a tile-data write landed (`[[animated_tile]]`, `[[replace_tile]]`) sees
> unrelated bytes and reports "no change" for a write that worked. Cost real
> time twice; the probes in `tools/bganimprobe/` get it right.

### The GBDK console font

> **This whole section is the RESIDENT-font path, and `text.glyph_buffer`
> retires it.** In glyph-buffer mode the font leaves VRAM entirely (it lives in
> ROM and each character is rasterized on demand into a small band above the
> scene art), so none of the budgets or ordering rules below apply: a scene may
> use nearly the whole tile table, and `font_preload` has nothing to preload.
> That is the mode a GB Studio import and any big-tileset game wants - see
> the `text.glyph_buffer` footnote in `mosaik_lang_spec.md`. Read on for the default (resident-font) path.

`graphics.text` lowers to GBDK's console font, which is **background tile data**.
`gbs_text_init` (fired on the first `text.*` call, or by `text.set_font`) does three
`font_load`s that occupy bkg tiles **0..138 as throwaway padding** and settle the
active glyphs at **ids 139..234** (probed at runtime; MEASURED 2026-09-24 in PyBoy on
three VM samples - the codegen's `gbs_font_base = 132` is only the pre-probe
default). It also
reloads that padding over the low tiles and re-inits the tilemap. Consequences:

- **A text-using GB program must keep its background tileset out of 139..234.**
  In practice cap the referenced/uploaded tileset to **ids 0..127 (block 2)** and let
  the font live in block 1. `bigworld-paint` does this: `BG_TILES = 128`.
- **Upload order matters.** `set_font`/the first `text.*` must run *before*, or the
  tileset must be re-uploaded *after* (the `font_preload` idiom), because `font_init`
  reloads padding over the low tiles and clears the tilemap.

### A worked GB budget (`bigworld-paint`)

| Region                | ids       | VRAM              | Count |
|-----------------------|-----------|-------------------|-------|
| Sprites (block 0)     | 0..39     | `0x8000-0x8280`   | 40    |
| Background (block 2)  | 0..127    | `0x9000-0x97FF`   | 128   |
| Custom font (block 1) | ~139..234 | `0x88B0-0x8FA0`   | 96    |
| 9-slice frame (block 1)| 247..255 | `0x8F70-0x8FFF`   | 9     |

So on the **DMG** 255 distinct background tiles + text cannot coexist — the real
simultaneous ceiling with a font + frame is ~128 background tiles. (255 works on
the Lynx, which has its own font and no aliasing.)

## Game Boy Color / Analogue Pocket — two 8 KB banks (16 KB)

The GBC doubles VRAM to **two banks** (https://gbdev.io/pandocs/Tile_Maps.html,
https://jsgroth.dev/blog/posts/game-boy-color/). The BG map's **attribute byte**
lives in VRAM bank 1 and carries a **bank-select bit per cell** (`Bit 3`), and each
sprite's OAM attribute carries one per sprite. So:

- **Background** can address **~512 tiles** (256 per bank), not 256 — a per-cell
  bank pick.
- **Sprites** reach both banks (a per-sprite bank pick).
- Tile *data* is still `0x8000-0x97FF` **in each bank** (384 slots × 2 = 768
  physical), and the font lives in the bg region as on DMG — but there's now
  ample room, so **a 255-tile background + a font + a frame fits** where the DMG
  can't. (Using bank 1 needs `VBK_REG` + attribute maps; GB Studio-style tooling
  hides this.)

The engine does not use bank 1 for tile data today; the per-room sprite ceiling
below is the same 128 on every GB-family target, GBC included.
`docs/mosaik_lang_spec.md` §color notes GBC's palette side.

### The sprite tile ceiling (VM8 rooms)

The VM8 rooms generator packs each room's sprite sheets upward from OBJ tile 0
and refuses (hides) a kind that would cross the top. The ceiling is per console
(`mosaik_vm/rooms/context.py`: `OBJ_TILE_CAP`, `SMSGG_OBJ_TILES`; emitted as
`OBJ_TILE_TOP` / `EMOTE_TILE` by `rooms/emit_prelude.py`):

| Target set        | Sprite tiles | With an emote bubble |
|-------------------|--------------|----------------------|
| GB family (and every non-SMS/GG target) | 128 (block 0) | 124 (`EMOTE_TILE` = 124) |
| SMS / Game Gear   | 192 (the whole pattern area) | 188 (`EMOTE_TILE` = 188) |

The emote's four tiles come off the TOP of the table, so the allocator never
reaches them. The SMS/GG number used to be a flat 128 inherited from the GB;
measured on the SMS/GG sample conversion, its long walk-in room wants 156 and
the parallax room 132, which is why the 192-slot area is now used in full.

## SMS / Game Gear

GBDK's GB-compat layout **mirrors the GB's VRAM 1:1** (verified empirically via a
glyph-corruption map, 2026-07; see `tests/set_font_at_test.py`):

| Region                  | VRAM            | Tiles |
|-------------------------|-----------------|-------|
| Background patterns     | `0x0000-0x17FF` | 0..191 |
| Name table (32×28×2 B)  | `0x1800-0x1EFF` | *(= tile ids 192..247 — off-limits)* |
| SAT (sprite attributes) | `0x1F00-0x1FFF` | *(= tile ids 248..255 — off-limits)* |
| Sprite patterns         | `0x2000-0x37FF` | 256..447 (`set_sprite_data` slot n = 256+n) |

Sprite tiles: the VM8 rooms generator may use **all 192** of the pattern slots
(**188** with an emote bubble, whose four sit at the top) - `SMSGG_OBJ_TILES` in
`mosaik_vm/rooms/context.py`, against the GB family's 128 / 124. See "The sprite
tile ceiling" above.

So the **background only addresses tiles 0..191**: "tile 192..255" pattern data IS
the name table + SAT (every map repaint corrupts a font parked there — garbled
glyphs + colour specks), and the compat tilemap writers use 8-bit ids, so the
256..447 sprite region is unreachable for the background anyway. Sprites never
compete with the background lanes.

The GBDK z80 port inits the console font **eagerly at boot** into tiles **0..95**,
then a large tileset upload overwrites it (tileset wins, text garbles) — the mirror
of the GB problem. The fix is the **opt-in `text.set_font_at(base, data)`** verb (a
font swap at a caller-chosen base; a font swap also switches SMS text from printf
to tile plotting, which follows the relocated `gbs_font_base`). `base + 96` must
stay ≤ 192; the full-UI packing is **tileset 0..86 + frame 87..95 + font 96..191**
— exactly the 192 budget (`bigworld-paint`). A small-tileset game keeps plain
`set_font` (overwrite the console font in place — `ui-quest`).

**Hiding a sprite is a Y, and 0 is not it here.** On the GB family OAM is
biased by (8, 16), so writing y = 0 puts an object 16 px above the screen and a
GB-family idiom parks there. The z80 ports write the SAT **directly**: y = 0 is
the *visible* top-left corner, and a sprite parked that way sits in the corner
of the screen wearing whatever tile it last held. Park at **200**
(`GBS_SPR_PARK_Y`) - off-screen on both a 144- and a 192-line display.

And do not reach for 208 as "further off screen": **SAT y = `0xD0` (208)
TERMINATES the sprite list** on the SMS in 192-line mode, so every object after
it stops drawing. 200 is chosen to sit below the display and above that
sentinel. (Found 2026-08-18 on the shooter conversion: a hidden per-object
descriptor actor parked its children at (0, 0) and they showed as a white block.
The Game Gear hid the same bug by accident - its 160×144 viewport is a centre
crop of the same 256×192 plane, so plane (0, 0) is outside the window.)

**Nothing may be parked at the TOP of a 256-tile table.** That GB-family idiom
is invalid here and fails *silently*: the write lands in the name table / SAT,
the tile never draws, and sprites flicker. It cost `projects/vm-bganim` its whole
animated water tile on both consoles (the studio used to allocate an animated
clip's hidden index from 255 downward) while the GB build looked correct. The
studio now allocates that index **just above the real tileset**, and the compiler
refuses a resolvable `bkg.set_data` past tile 191 on these two consoles
(`mosaik/codegen/gen_budgets.py::_check_smsgg_bkg_range`,
`tests/sms_bkg_clear_test.py`).

## The glyph band (`text.glyph_buffer`, GB family + SMS/GG)

In glyph-buffer mode the font stays in ROM (the project's `[assets] font`, else
GBDK's `font_ibm` as the console library links it, never a copy in the engine)
and characters are rasterized on demand into a band of background tiles ABOVE
the program's art. The compiler
places it (`gen_budgets.py::_resolve_glyph_band`), nobody hand-picks it:

- `top` = the console's highest background tile + 1: **192 on SMS/GG, 256
  everywhere else** (`gbdk_text.py::GLYPH_TOP_TILE`).
- `base` = `max(first + count)` over every compile-time resolvable
  `bkg.set_data` in the final program (scene tilesets, the transpiler's
  `TS_MAX_TC`, a hand-written shell's 9-slice frame). Nothing readable means
  the fallback `base = top - 48` (`GLYPH_FALLBACK_COUNT`).
- `band = (base, min(top - base, 64))`: **at most 64 tiles**. Fewer than 2 is a
  hard error (slot 0 is the space glyph), never a silently blank text box.

## PC Engine — one 64 KWord VDC VRAM (review E-10)

The PCE addresses VRAM in **words**, not bytes, and the cc65 conio runtime has
already claimed the bottom of it before any mosaik code runs. The map the two
engines lay over that (`cc65_sprite.py`, `cc65_bkg.py`):

| range (word addr) | contents | owner |
|---|---|---|
| `$0000-$1FFF` | BAT, 128x64 entries (8192 words; a 1024x512 px virtual screen) | cc65 conio |
| `$2000-$2FFF` | the conio font | cc65 conio |
| `$3000-$39FF` | sprite patterns — `GBS_VRAM_TILES`, `CC65_MAX_TILES` (40) x 64 words | `cc65_sprite.py` |
| `$3A00-$3FFF` | free (room for ~24 more sprite tiles) | — |
| `$5000-$7EFF` | sprite patterns INSTEAD of `$3000`, when a residency world's busiest room needs more than 64 tiles (`SPR_TILE_NEED`, up to 188); free otherwise | `cc65_sprite.py` |
| `$4000-$4FFF` | background characters — `GBS_VRAM_BKG`, up to `GBS_BKG_MAX_TILES` (256) x 16 words | `cc65_bkg.py` |
| `$7F00-$7F FF` | SATB — 64 entries x 4 words, DMA'd from the RAM mirror each vblank | `cc65_sprite.py` |

Notes that matter when changing any of it:

- **The BAT is at `$0000`, not `$4000`.** `$4000` is background CHARACTER data;
  the map itself is the conio BAT the runtime set up, and it is **128 entries
  wide** (the full `$0000-$1FFF` block: `bat = (y << 7) + x` in
  `cc65_bkg.py::_emit_pce_bkg_engine`). `gbs_set_bkg_tiles` wraps GB map
  coordinates mod 32 and writes each cell **eight times**: at columns +0, +32,
  +64, +96 and again at +32 rows, so the 32x32 GB map tiles the whole 128x64
  BAT and a u8 `BXR`/`BYR` scroll wraps mod 256 both ways (the Game Boy
  contract). The writes go straight through, not via a vblank queue (the queue
  made a streamed level shake; the real flicker was the mid-frame scroll write,
  now deferred in `gbs_bkg_scroll_flush`). Entries select BG palette 1 (a
  per-cell slot + 2 under `graphics.palette`) while conio text keeps palette 0.
- **A GB tile becomes the top-left quarter of a 16x16 4bpp VDC sprite pattern**,
  hence 64 words per sprite tile against 16 per background tile.
- The sprite pattern area sizes itself from `CC65_MAX_TILES`, so raising that
  constant walks `$3000` upward toward `$4000`: 40 tiles end at `$3A00` and
  there is room for about 24 more before the background characters are hit.
  Nothing checks this at build time. `[build] sprite_max_tiles` can only ever
  LOWER the table (`cc65_sprite.py`: clamped to `CC65_MAX_TILES`), and only the
  Lynx Suzy engine reads it (the auto-derive is Lynx + VM8 only); the PCE
  sprite engine always emits AT LEAST the full 40, so its `$3000-$39FF` never
  shrinks. It GROWS for a per-room residency world: the rooms generator
  states the busiest room's `SPR_TILE_NEED` / `SPR_SLOT_NEED`, the table takes
  that many tiles (still at `$3000` up to 64, which ends exactly at `$4000`;
  moved to `$5000` past that, 188 at most before the SATB) and the slots that
  many SATB entries (64 at most). No need stated = this layout, byte for byte.
- VDC access is register-select at `$0200`, data at `$0202/$0203`; the SATB's
  auto-repeat DMA (DCR bit 4) is what makes a sprite update tear-free.

## Lynx / NES

- **Lynx** composites from RAM with its own bitmap font; there is no tile aliasing, so
  the full 255-tile background is fine. Custom-font/9-slice tile data is *unused* and
  should be tree-shaken away (it otherwise eats the tight cc65 MAIN area) — emit stubs
  under `if platform == "lynx" or platform == "pce"`.
- **PC Engine** has its own conio font too, so the same stub rule applies to it (its
  VRAM map is the section above).
- **NES** has no `printf`/box (NROM overflow); text and frames are no-ops.

## The rule of thumb

> On the GB family, budget **384** tile slots across sprites (block 0), background
> (blocks 1+2), the font (~96, block 1) and any 9-slice frame (~9, block 1). A
> text-using game gets **~128** clean background tiles. On SMS/GG the background
> gets only **192** slots (tiles 0..191 — 192..255 are the name table + SAT), shared
> with the font + frame; a full font + frame leaves **~87** background tiles
> (relocate the font with `text.set_font_at`). Everywhere else the font is separate.

Sprite tiles are a separate ceiling the VM8 rooms generator enforces: **128 on
the GB family (124 with an emote), 192 on SMS/GG (188 with an emote)**. The
SMS/GG background range and the glyph band are checked by the compiler; the
studio's Budget dock meters all of it per target before a build.
