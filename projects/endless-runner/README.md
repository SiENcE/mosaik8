# Endless Runner

A mosaik reimagining of Dr. Ludos' *Running Knight* (LynxJam 2023) — one source
that builds and plays on **all nine consoles**, showcasing the native-feature
pipeline (`docs/endless-runner-plan.md`, Phases 1–5) and using the **original
artwork** straight from the game's `gfx_sprites.pcx`.

The original (`_samples/running-knight/`) is ~1600 lines of Lynx-only cc65 C
with 4bpp Suzy sprites, an SCB Z-sort, a 4-channel Chipper music driver,
hardware fades and EEPROM. This is a **gameplay reimagining** on portable
mosaik: the engine uses native hardware where it exists and a portable
fallback where it doesn't.

## Play

Dodge the stone **pillars**, grab the **chests**. D-pad up/down moves the
knight; hold **A** to run (the world scrolls faster). **A** starts / restarts.

```
python mosaik8.py build projects/endless-runner
```

Builds `endless-runner.{gb,gbc,pocket,duck,sms,gg,nes,lnx,pce}` under `build/`.

## Original artwork, all nine consoles

`assets/gen_sprites.py` reads the **original `gfx_sprites.pcx`** (Pillow) and
cuts out the knight / pillar / chest at the exact rectangles the game's
`game/Makefile` feeds to sp65, snapping each to the 8×8 tile grid (the knight
is a 16×32 = 2×4-tile metasprite) and packing them — with the original
16-colour palette — into `assets/sprites.png` + its manifest. Colour tiers
automatically:

| Console | Sprites | Ground |
|---|---|---|
| **Atari Lynx** | the **original 16 colours** (4bpp, `palette.load_sprite16` → Mikey pens) | scrolling sprite floor¹ |
| GBC / Pocket / SMS / GG / NES / PCE | 4-colour luma quantization, coloured via `graphics.palette` | scrolling hardware tilemap |
| Game Boy / Mega Duck | 4 greys | scrolling tilemap |

¹ *The Lynx can't run `graphics.bkg`: its 256×256 Suzy composite has no spare
RAM beside the 4bpp metasprites. Instead the ground is a row of scrolling
decoration sprites over the pen-0 field — the few-small-sprites floor of Dr.
Ludos' original. The Lynx engine paints one Suzy sprite per slot and the
libretro cores blank the frame past ~32 live slots, so the Lynx runs 2 foes
(not 3) to leave room for the 8 ground sprites (32 slots total).*

Regenerate the art (after editing the cut list or the bkg) from the repo root:

```
python projects/endless-runner/assets/gen_sprites.py
```

## Engine features used

- **Metasprites** (`sprite.set_meta`, P1) — the knight + obstacles are 2×4-tile
  blocks moved as one (GB-family fan-out over OAM, Lynx/PCE composite).
- **4bpp / 16-colour tier** (P2) — native original colours on the Lynx.
- **Named-sprite sheet** (P3) — the PCX is sliced via the sidecar manifest.
- **`native.lynx`** (P4) — screen **shake** on death + a two-voice **jingle**
  (Mikey channel B, P5) on the title / game-over; no-ops elsewhere.
- **`sound.sfx`** (P5) — portable coin / hurt / point one-shots.

## Notes / portability limits

- **Score HUD** is text, which lives in the (scrolling) background layer, so it
  is shown on the *static* title and game-over screens, not during play. The
  Lynx (sprite engine owns the frame) shows none and leans on the jingle / shake.
- **High score** is RAM-only (lost on power-off); the original used Lynx EEPROM.
- **Audio** is one portable beep channel + the Lynx's second jingle voice; the
  original's 4-channel Chipper music is out of scope.
