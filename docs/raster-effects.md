# Raster effects: the scanline table, double speed, native tiles

Three additions to the standard library, made for `projects/mosaik-kart` (a
pseudo-3D kart racer) and usable by any program. The reference for every verb
is `mosaik_lang_spec.md` §6; this page is the why, the how and the cost.

| Addition | Verbs | Real on |
|---|---|---|
| Per-scanline scroll table | `bkg.raster`, `raster_set`, `raster_copy`, `raster_show`, `raster_get`, `raster_curve_start`, `raster_curve`, `raster_stripes` | Game Boy family (x and y), SMS / Game Gear (x only); no-op elsewhere |
| Double-speed CPU | `system.cpu_fast(on)` | Game Boy Color; no-op elsewhere |
| Native-format tile upload | `bkg.set_data_native(first, count, data)` | everywhere; differs from `set_data` only on SMS / Game Gear |

Nothing is emitted for a program that does not call them, so every existing
project builds byte-identical.

## 1. The scanline table

`bkg.parallax*` gives a room up to three scroll bands. A pseudo-3D road, a
water ripple or a "mode 7" style floor needs a different scroll on **every**
line. The table holds one entry per screen line and an interrupt plays it
back as the picture is drawn.

```mosaik
bkg.move(SCX0, 0)                 -- the plain scroll first (see "rules")
bkg.raster_set(0, SCX0, 0)        -- line 0's entry: every line above `first` uses it
bkg.raster(1, HORIZON)            -- arm: work from the horizon down
loop {
    video.wait_vblank()
    -- a road: steer with a straight ramp, bend it with a parabola
    bkg.raster_curve_start(x0, dx0)                 -- 8.8 fixed point
    bkg.raster_curve(BOTTOM, LINES, 0 - curve)      -- bottom line first, going up
    bkg.raster_stripes(BOTTOM, LINES, DEPTH, phase, LINES)   -- Game Boy: light / dark road
    bkg.raster_show()                               -- live at the next v-blank
}
```

### Rules

- **`first` is a promise**: every line above it uses line 0's entry. The
  console only works from `first` down.
- **Double-buffered by swapping.** After `raster_show()` the buffer you write
  next holds the frame before last, so rewrite every line you animate, every
  frame. While disarmed, a write lands in both buffers.
- **Read back before you publish.** `raster_get(line)` reads the buffer being
  written; call it before `raster_show()`.
- **Vertical scroll is Game Boy only.** The SMS / Game Gear VDP latches it
  once per frame. Curves are portable, hills and `raster_stripes` are not.
- **It owns the scroll while armed.** `bkg.move` / `bkg.scroll` stand down.
  On SMS / Game Gear arming takes the pending `bkg.move` for the vertical
  scroll, so call `bkg.move` before `bkg.raster(1, ...)`.
- **Game Boy: it owns the STAT interrupt vector.** Combining it with
  `bkg.parallax*`, `text.win_sprite_cut` or `text.win_overlay_cut` is a
  compile error. A long TIMER handler (`native.huge`) can delay a line.
- **SMS / Game Gear: write video memory right after `video.wait_vblank()`.**
  The handler writes a VDP register, which would redirect an upload it
  interrupted, so it skips the frame when GBDK is inside a VRAM transfer.

### How it is played back

**Game Boy family** - a raw handler on the STAT vector (`ISR_VECTOR`), not
GBDK's `add_LCD` chain: the chained dispatcher costs more than the handler.
Each H-blank it writes SCX and SCY for the next line, 53 machine cycles in
all, with both writes done 31 cycles in - before the next line starts
drawing. The frame starts on the LYC source one line above `first` and the
handler switches itself to the H-blank source, so the lines above cost
nothing. V-blank masks the STAT interrupt over its own STAT write, because a
monochrome Game Boy raises a spurious one there.

**SMS / Game Gear** - GBDK's interrupt dispatcher (six pushes, six pops) is
longer than a scanline, so an interrupt per line is not possible through it.
One line interrupt fires just above `first` and the handler **stays in it** to
the bottom of the picture, writing VDP register 8 as the V counter ticks. The
main program gets the rest of the frame.

### Cost

| | Game Boy | Game Boy Color, double speed | SMS / Game Gear |
|---|---|---|---|
| playback, table on the lower 80 lines | 24 % of a frame | 12 % | the lines themselves: about 30 % on Game Gear |
| `raster_curve`, per line | 41 machine cycles | same, at twice the clock | 142 T-states |
| `raster_stripes`, per line | 24 machine cycles | same | no-op |
| the same road loop in compiled C | about 210 machine cycles a line | | |

## 2. `system.cpu_fast(on)`

The Game Boy Color's double-speed mode (`KEY1` + `STOP`, through GBDK's
`cpu_fast` / `cpu_slow`). Guarded on the hardware: the same ROM on a
monochrome Game Boy carries on at single speed.

What changes with it: anything clocked by the CPU - the TIMER interrupt
(`native.huge`, `system.music_isr`) and `system.delay` - runs twice as fast.
Anything clocked by v-blank (`video.wait_vblank`, `system.frames`) does not.

## 3. `bkg.set_data_native`

On SMS / Game Gear under the 16-colour tier, `bkg.set_data` takes
packed-nibble tiles and converts each to the VDP's planar layout at run time:
32 helper calls a tile, about four seconds for a screen's worth.
`set_data_native` takes the planar bytes (8 rows × 4 bitplane bytes, bit 7 =
leftmost pixel) and uploads them as they are; a build-time generator can emit
them directly. It also needs no `palette.load_bkg16` call to switch the tier
on. Everywhere else the native format is what `set_data` already takes.

## Where it lives

| File | What |
|---|---|
| `mosaik/codegen/gbdk_raster.py` | new: all three emitters (GB, SMS/GG, stubs) |
| `mosaik/codegen/gbdk.py` | verb map, prototypes, includes, emission calls |
| `mosaik/codegen/cc65.py` | verb map and no-op stubs (Lynx, PCE) |
| `mosaik/codegen/generator.py` | by-use flags; the STAT-vector exclusivity error |
| `mosaik/codegen/gbdk_sound.py` | `bkg.move`'s v-blank commit stands down while armed |
| `mosaik/typechecker.py` | the new verb names; `raster_get` returns u8 |
| `tests/raster_test.py` | new: what is emitted per console, gating, the clash error |
| `projects/raster-lab/` | new: the table proved on a rendered frame (`verify.py`) |
| `tests/project_verify_test.py` | runs that `verify.py`, rebuilding all four consoles' ROMs |
| `docs/mosaik_lang_spec.md`, `README.md` | the reference entries |
| `docs/engine-rules.md`, `docs/cheat-sheet.md`, `CLAUDE.md` | the full rule, the numbers, the headline |

## Tested

- `tests/raster_test.py` and the whole of `tests/run_all.py`.
- `projects/raster-lab/verify.py`: reads the scroll of every scanline off a
  screenshot on Game Boy, Game Boy Color (PyBoy), Game Gear and Master System
  (Genesis Plus GX), for both the table writes and the native fills. The Game
  Boy builds were also soaked for 600 frames on Gambatte, a cycle-accurate
  core, without a wrong line. It runs in the default suite
  (`tests/project_verify_test.py`, which rebuilds all four ROMs first; SMS /
  Game Gear skip without the core). Mutation-checked: arming the GB table's
  LYC one line late fails it.
- `projects/mosaik-kart`: a whole game on it, with its own acceptance test.

**Not tested on real hardware.**
