# MOSAIK KART

A pseudo-3D kart racer in **plain mosaik8 — no VM8** — for four 8-bit
consoles from one source tree: Game Boy Color and Game Gear first, Game Boy
and Master System along for the ride.

Three laps against three rivals on a track of seventeen bends, with boost
pads, roadside trees and arrow signs, a panning mountain backdrop, music, an
engine and a HUD.

| Console | Screen | Game frames per second |
|---|---|---|
| Game Boy Color | 160×144 | 60 |
| Game Gear | 160×144 | 30 |
| Game Boy | 160×144 | 30 |
| Master System | 256×192 | 30 |

The road itself is redrawn by the interrupt on every display
frame on all four; the table above is how often the game logic moves it.

## Controls

| | Game Boy / Color | Game Gear / Master System |
|---|---|---|
| accelerate | A | button 2 |
| brake | B | button 1 |
| steer | left / right | left / right |
| start, pause | START | START (the Master System's PAUSE button) |

Grass slows the kart to a crawl, a tree stops it dead, a rival bumped from
behind costs speed, and a yellow boost pad gives a second and a half of turbo.

## How the road works

None of these machines can rotate or scale a background, so this is the
technique the commercial racers of the time used (Sonic Drift on the Game
Gear, the Game Boy road engines): **the road is one picture, already drawn in
perspective, and the game only scrolls it — every scanline by a different
amount.** Nothing is redrawn while you drive.

mosaik8's `bkg.raster*` verbs are that table. Each frame `src/race.mos`:

1. **Bends and steers the road.** Starting at the bottom line, each line is
   shifted a little more than the one below it. The kart's sideways position
   is a shift that is full size at the bottom and zero at the horizon (a
   straight ramp); a bend adds its curve to the slope on every line (a
   parabola); and when the next bend comes into view the lines beyond it use
   its curve instead. That is two `bkg.raster_curve` calls.
2. **Moves the stripes.**
   - *Game Boy / Color:* the road is drawn twice, a light version and a dark
     one 80 lines further down the tilemap, and each line's **vertical**
     scroll picks one by its depth plus the distance driven
     (`bkg.raster_stripes`).
   - *Game Gear / Master System:* their video chip latches the vertical
     scroll once per frame, so every road pixel stores a depth phase (0–3)
     and the palette rotates — the same trick as the checkerboard in
     `vm-megademo`.
3. **Pans the backdrop.** The lines above the horizon share table entry 0,
   which moves sideways as the road turns.
4. **Places the sprites.** A distance becomes a screen line through a table,
   and `bkg.raster_get` says where the bent road is on that line, so karts
   and trees stand on the road wherever it goes. They come in three drawn
   sizes.

The track is data: a list of `(length, curve)` segments in
`tools/gen_assets.py`, which also paints the backgrounds and writes the
tables.

## Editing the sprites

The sprite art is `assets/sprites.png`, an indexed 4-colour PNG, with
`assets/sprites.sprites.toml` naming a rectangle per sprite. The build reads
both (`mosaik.toml [assets] sprites`): it cuts every rectangle into 8x8 tiles
in manifest order and defines `<name>_tile` for each, which the upload in
`src/d_spr.mos` names. Edit the PNG in any paint program or the studio's
image editor and rebuild.

- The palette INDEX is the colour: 0 is transparent and 1..3 go through the
  group's palette (Game Boy Color) or colour mapping (SMS / Game Gear). The
  PNG's preview colours are the player kart's, so the trees look red and blue
  there.
- Each drawn size is its own sprite (`tree`, `tree_m`, `tree_s`), and the
  mirrored copies (`kart_left`, `sign_left*`) are real pixels because the
  SMS / Game Gear cannot flip a sprite: retouch each one.
- Keep each sprite's size and the manifest order. `src/race.mos` places
  them as fixed metasprite shapes, and `tools/gen_assets.py` uploads runs
  of consecutive sprites as groups (`GROUPS`, with their palettes).
- `tools/paint_sprites.py` is how the sheet was first drawn. It is not part
  of the build and refuses to overwrite the PNG without `--force`.

## Build and test

```sh
projects/mosaik-kart/build.sh                 # art -> data modules -> four ROMs
python projects/mosaik-kart/tools/verify.py   # boots all four, a bot drives, checks picture, speed, sound
python projects/mosaik-kart/tools/play.py gameboy_color --out race.png   # a whole race as a contact sheet
python projects/mosaik-kart/tools/fps.py      # frame rate per console
python projects/mosaik-kart/tools/gbprof.py gameboy   # where a frame's time goes
```

ROMs land in `build/<console>/kart.{gbc,gg,gb,sms}`.

## Layout

```
src/main.mos        title < - > race
src/race.mos        the race: road table, physics, rivals, objects, HUD
src/snd.mos         sound driver: two songs, engine, effects (GB APU / SN76489)
src/d_*.mos         art, tables, track, songs       (generated)
assets/sprites.png  the sprite art (+ sprites.sprites.toml, the named rects)
tools/gen_assets.py paints the backgrounds, writes src/d_*.mos
```

## What it needed from the framework

| Verb | Why |
|---|---|
| `bkg.raster`, `raster_set`, `raster_copy`, `raster_show`, `raster_get` | the per-scanline scroll table (mosaik8 had three scroll bands, Game Boy only) |
| `bkg.raster_curve`, `raster_stripes` | fill the table in native code: on the Game Boy the same loop in compiled C cost 84,000 cycles a frame, these cost 25,000 (a display frame is 70,224) |
| `system.cpu_fast` | the Game Boy Color's double-speed CPU: the difference between 30 and 60 fps here |
| `bkg.set_data_native` | upload SMS / Game Gear tiles without the run-time format conversion: a screen loads in under a second instead of about four |

## Notes for the next person

- **On SMS / Game Gear, write to video memory only right after
  `video.wait_vblank()`.** The scanline table holds the CPU from the horizon
  to the bottom of the picture and writes a video register; it skips a frame
  rather than corrupt an upload it interrupted. The palette rotation is the
  only per-frame write and it sits first in `race.frame`.
- **The HUD is sprites**, not text: anything in the tilemap above the horizon
  pans with the backdrop.
- **SMS / Game Gear sprites cannot flip**, so the left-leaning kart and the
  left-pointing arrow are drawn mirrored in the sheet.
- **A 2bpp sprite reaches 3 of the 16 sprite colours on SMS / Game Gear, and
  which 3 is chosen when the tiles are uploaded** (`palette.set_2bpp`). The
  rival kart is uploaded three times through three mappings — that is how
  one drawing becomes a blue, a green and a yellow kart there. On the Game
  Boy Color the same tiles take a palette per sprite.
- **Avoid signed division and 16-bit multiplies in the frame loop.** The
  kart's sideways position is stored as the road's shift *per line*, which is
  what the table wants; `v * dt` is three additions; an object's sideways
  offset is a table. The profiler shows the difference.
- **sdcc for the Z80 choked on a call that passed three constant zeros**
  (it emitted `ld hl, a`, which does not assemble). The centre-screen message
  goes through a small array instead.
- **Everything that moves is scaled by `dt`**, the display frames that passed,
  so a race takes the same two minutes at 30 and at 60 frames a second.
