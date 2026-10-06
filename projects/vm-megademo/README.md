# VM8 MEGADEMO

A seven-part demoscene production for **five 8-bit machines from one source
tree** — Game Boy, Game Boy Color, Game Gear, Master System and PC Engine —
directed entirely by **VM8 event scripts**.

<div align="center">
<table>
<tr>
<td align="center"><img src="docs/shots/gb.gif" alt="Game Boy"><br><sub>Game Boy</sub></td>
<td align="center"><img src="docs/shots/gbc.gif" alt="Game Boy Color"><br><sub>Game Boy Color</sub></td>
<td align="center"><img src="docs/shots/gg.gif" alt="Game Gear"><br><sub>Game Gear</sub></td>
</tr>
</table>
<table>
<tr>
<td align="center"><img src="docs/shots/sms.gif" alt="Master System"><br><sub>Master System</sub></td>
<td align="center"><img src="docs/shots/pce.gif" alt="PC Engine"><br><sub>PC Engine</sub></td>
</tr>
</table>
</div>

It tips its hat to the PC and Amiga classics (*Second Reality*, *Unreal*,
*Dope*): a boot screen, a logo, a vortex, a plasma with a sine scroller, vector
balls over a checkerboard, a fire, fireworks and credits, with a soundtrack
that drives the picture.

| # | Part | What you see | How it is done |
|---|------|--------------|----------------|
| 0 | **Boot** | a terminal that types itself | VM8 text boxes: typewriter, blips, self-closing boxes, `$var$` |
| 1 | **Title** | the logo, full of flowing colour, in a starfield | palette rotation; hardware scroll sway; the stars are VM8 projectiles |
| 2 | **Vortex** | a log-spiral tunnel, three colour schemes | pure palette rotation; four VM8 actors dance over it |
| 3 | **Plasma** | a drifting plasma under a sine scroller | palette rotation + VM8 `scroll_bg`; one sprite per letter; the text plays once and the part ends with it |
| 4 | **Grid runner** | 24 vector balls morphing over a moving checkerboard | real-time 3D with no multiplications; the floor is four rotating colours |
| 5 | **Fire** | flames that jump on every kick drum | a real per-cell fire; the music calls a VM8 script that stokes it |
| 6 | **Fireworks** | rockets, bursts, the moon crossing the sky, twinkling stars and the credits (with where to get MosaiK8: github.com/SiENcE/mosaik8) | every shell is a VM8 thread that rolls the burst's colour and kind; the kernel draws 16 particles with gravity |

The loop is about two and a half minutes. **START** skips to the next part on every
console (the Master System's START is its PAUSE button).

## VM8 is the director

Every part is a VM8 scene with its own event script in `scripts/`. The scripts
decide *what happens and when*; a small native kernel (`src/`) pushes the
pixels they ask for. The two talk through VM8 heap variables: a script writes
`zoom`, `shape`, `cyc`, `flash`…, the kernel reads them every frame, and the
kernel writes back `t` (the part's clock) and `loaded`.

What the scripts use, and where to look:

| VM8 feature | Used for | Script |
|---|---|---|
| scenes (`change_scene`, computed room) | one scene per part; START computes `scene() + 1` | `00_main` `skip` |
| subroutines (`call` / `ret`) | shared `enter` / `leave` / `burst` | `00_main`, `60_sky` |
| threads with arguments (`start_thread args`, `arg(n)`) | one dancer body, four phases; fireworks shells | `30_tunnel`, `60_sky` |
| `set_self` + `self` actors | one script body bound to a different actor per thread | `30_tunnel` |
| join handles (`handle`, `handle_next`, `thread_stop`) | wait for a shell to finish; stop the cooling loop | `60_sky`, `55_fire` |
| actors, native moves, `actor_await_move` | the dancers | `30_tunnel` |
| projectiles: angle-aimed, animated | starfield, embers, rockets | `20_title`, `55_fire`, `60_sky` |
| timers | star and ember spawners | `20_title`, `55_fire` |
| input attachments | START = next part | `00_main` |
| expressions (`rand`, arithmetic, state reads) | everything that is placed or paced | all |
| `while` / `if` / `switch` / `wait_until` | ramps, the shape sequence, the timeline | all |
| `fade_in` / `fade_out` | every transition | `00_main` |
| `shake` | the beat, in the title only; the cycling parts answer the kick through the palette (`kick`: the colour cycle lurches ahead) and the fire stokes its flames. Nothing flashes on the beat: a white frame in time with the music read as a glitch below 60 fps | `00_main` `beat` |
| `palette_set` (PAL_SET) | swaps the colour ramp live | `30_tunnel`, `40_plasma` |
| `scroll_bg` | the plasma drift | `40_plasma` |
| `overlay_show` / `overlay_move_to` | the window curtain that unveils the title (Game Boy family) | `20_title` |
| text boxes, `text_speed`, `text_blip`, `hold`, `$var$` | boot terminal, credits | `10_boot`, `60_sky` |
| `music_song`, `music_mute` | the soundtrack; the lead drops out for the credits | `20_title`, `60_sky` |
| **`music_routine`** | the song's kick drum *calls a script* in time | `00_main` `beat` |

The bytecode is one blob. It adapts to each screen because the kernel
publishes `scrw`, `scrh`, `cx`, `cy` on the heap and the scripts place things
with them.

## The tricks

**Colour cycling as an engine** (`src/pal.mos`). The vortex, the plasma, the
logo and the checkerboard do not redraw anything: their tilemaps store a
*level* per 4×4-pixel block and the palette rotates underneath. Each console
gets the model it can do best:

| Console | Levels | How |
|---|---|---|
| Game Boy | 8 | 4 greys + their 50 % dithers; the BGP register rotates |
| Game Boy Color | 24 | 8 overlapping palettes of 4, written in the hardware's own word order |
| Game Gear / Master System / PC Engine | 10 | 16-colour tiles; the colour RAM rotates |

**The checkerboard** stores `(depth phase + 2 × column parity) & 3` per pixel,
so rotating four colours slides the whole perspective board forward.

**Vector balls without a multiply** (`src/balls.mos`). The rotation matrix
comes from the sine table through product-to-sum identities; the sine table is
kept pre-scaled by the current zoom by *adding* one sine per zoom step; model
coordinates are small integers, so each `k × axis` is a table of multiples
built by addition; and every shape is point-symmetric, so twelve points are
transformed and twelve are mirrored.

**The fire** (`src/fire.mos`) is computed for real. Heat 0–15 *is* the tile
number, so the heat buffer is the tilemap. The pass is time-sliced into bands
because compiled C costs these CPUs about 130 machine cycles per cell.

**Loading without a hitch.** A part's art is uploaded a few tiles per frame
while the screen is black, so the music never stalls.

**One song, three sound chips.** The song is data (`scripts/songs.toml`). `vm.music` plays it on the Game Boy APU, the
SN76489 and the PC Engine's HuC6280 PSG.

## Build and run

```sh
python setup_tools.py                                          # GBDK (and cc65 for the PC Engine)
python mosaik8.py build projects/vm-megademo                   # all five ROMs
python mosaik8.py build projects/vm-megademo --platform gameboy   # one console
```

ROMs land in `build/<console>/megademo.{gb,gbc,gg,sms,pce}`. The art, the
soundtrack and the compiled bytecode are checked in as generated sources, so
the build needs nothing else. On Linux, `setup_tools.py` cannot fetch cc65:
build it from source and point `CC65_HOME` at it.

After editing an event script, compile the bytecode again:

```sh
python -m mosaik_vm projects/vm-megademo/scripts -o projects/vm-megademo/src/scripts.mos \
       --map projects/vm-megademo/build/scripts.map.json
```

`src/vmv.mos` names the heap index of every script variable, for the kernel.
The compiler numbers variables in order of first appearance, and `00_main`
lists every variable the kernel reads first; the map's `variables` table holds
the indices to copy into `vmv.mos` when that list changes.

## Frame rates

Measured kernel frame rates (frames per second, emulated, 2026-10-04):

| | title | vortex | plasma | floor | fire | sky |
|---|---|---|---|---|---|---|
| Game Boy | 33 | 26 | 48 | 25 | 23 | 38 |
| Game Boy Color | 33 | 24 | 43 | 21 | 24 | 38 |
| Game Gear | 28 | 24 | 40 | 23 | 22 | 33 |
| Master System | 29 | 24 | 25 | 21 | 20 | 33 |
| PC Engine | 45 | 56 | 60 | 55 | 30 | 45 |

The fire and the fireworks vary with what is on screen. The floor paid for its
24 balls: it ran at 25 to 39 with 16.

All motion is time-based, so every console runs the show at the same speed;
the slower ones just take fewer steps. The numbers are honest about what a
bytecode VM plus compiled C leaves on a 4 MHz CPU: with music playing, the
VM8 frame itself is about three quarters of a Game Boy frame, and each VM8
actor or projectile costs roughly a tenth of one.

## Layout

```
scripts/*.evt.toml     the show: one event script per part (the source of truth)
scripts/songs.toml     the soundtrack           (generated)
src/main.mos           the shell: wires the VM8 packs
src/demo.mos           the kernel: part loader, effect dispatch, heap wiring
src/pal.mos            palette engine: cycling, fades, flashes, per console
src/balls.mos          vector balls        src/scroller.mos   sine scroller
src/sky.mos            firework bursts, the moon, twinkling stars
src/fire.mos           fire
src/gfx.mos            sine table, scroll  src/vram.mos       resident tile upload
src/d_*.mos            art per part        (generated)
src/scripts.mos, songs.mos, instruments.mos, vmv.mos          (generated)
docs/shots/            the clips above      docs/preview/      the art per console
```

## Notes for the next person

Things this project ran into in the framework, and how it works around them:

- **Banked code cannot call `bkg.set_data` on SMS / Game Gear when the
  16-colour tier is on**: the 4bpp uploader is only declared in the home
  translation unit, so the banked unit fails to compile ("too many
  parameters"). `src/vram.mos` is a resident wrapper; it is deliberately not
  in `code_banks`.
- **The 16-colour tile tier is switched on by `palette.load_bkg16` being
  called somewhere.** The palette engine writes native colours on SMS / GG, so
  `pal.init()` makes one `load_bkg16` call to keep the tier on.
- **`palette.load_bkg16` divides.** On SMS / GG it rounds each channel with a
  software division — 48 per palette, over half a Z80 frame. `pal.mos` shifts
  and calls `palette.load_native` instead.
- **`bkg.set_tiles` on the PC Engine mirrors every cell eight times** for
  scroll wrap, which is fine for loading and far too slow per frame; the fire
  writes the BAT directly through `hw.write`.
- **`sprite.move` goes through the metasprite layer** as soon as anything in
  the program uses metasprites (the VM8 actor pack does), which makes each
  move several hundred cycles.
- **`wait`, `actor_move_to` and a projectile's `life` take literals only;**
  `rand()` takes a literal bound. The scripts use `wait_until t >= …`,
  `actor_move` + `actor_await_move`, and scale `rand(128)`.
- **`set_self` takes a literal actor**, so a generic thread body is entered
  through one two-line stub per actor.
- **Arithmetic is C's: `u8 + u8` does not wrap inside an index.** `zs[d + 64]`
  promotes the sum to `int` and reads past the end of a 256-entry table when
  `d` is above 191. The vector balls did exactly that for a quarter of every
  turn, and the object collapsed into a vertical line. Store a wrapping angle
  in a `u8` first (`var dq: u8 = d + 64`), then index with it.
- **On the Game Boy, BGP has two writers.** VM8's `vm.fx` writes the register
  on every fade step and resets it to level 0 at every scene change; this
  demo's palette engine rotates the same register. Two different values in one
  frame made every fade flicker, and each part change flashed the old picture
  at full brightness. `src/pal.mos` now follows `vm.fx`'s fade level exactly,
  rewrites BGP right inside the fade callback, ignores the scene change's jump
  from black straight to level 0 (each part fades itself in), and `main.mos`
  gives `vm.fx` an all-black BGP base, so a write of its own can only be dark.
- **A DMG scene that rotates all four greys can only stay still as noise.**
  Anything that must keep one grey needs all four indices in equal parts, a
  checker. The floor therefore rotates three indices and keeps index 3 black:
  a clean black sky, scrolling sun stripes and moving floor bands.
- **The title's window curtain must be put away before a scene change.** If
  START skips the title while the curtain is still sliding, the window stays
  half-way down, and the window's sprite cut keeps hiding every sprite below it
  in every later part. The `skip` script runs `overlay_hide` first.
- **Deeply nested inline tables can be mis-read.** The `toml` package
  silently dropped a `switch` (and an `if` with an `else`) written as an
  inline table two levels inside a `while`. The scripts keep such blocks at
  the top level of a script and reach them with `call`. When a script
  misbehaves, dump what the loader actually parsed before blaming the VM.
