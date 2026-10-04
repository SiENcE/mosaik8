# vm-palanim - a sprite that changes PALETTE as its animation runs

The worked example for **per-FRAME sprite palettes**. The reference engine keeps a
palette on every metasprite TILE; read at the FRAME level that one field
recolours a whole sprite while its clip plays, and on a 4-grey Game Boy it
flashes one. Both lower to ONE number per frame, and this project is what
proves the number reaches OAM on real hardware.

Three actors, three authored shapes, plus a control - and the whole feature is
the `pal` lists in `assets/gen.py`:

| kind | authored | what it does |
|---|---|---|
| `blinker` | `pal = [1, 2]` | alternates sprite palettes 1 and 2 frame by frame over ONE piece of art (the reference engine's savepoint shimmer) |
| `flasher` | `pal = [0, 16]` | alternates the **DMG select** (bit 4 = OBP1) with the colour palette left at 0 - how a two-colour screen flashes a sprite, its only way to |
| `stately` | idle `[3]`, walk `[2]` | recolours per **STATE**: green standing, red walking - the exploding-mine shape, driven by movement with no bytecode |
| `hero` (the player) | *nothing* | the **CONTROL**: its `F_PAL` entries are the 255 no-palette sentinel, so the animator must leave its colour alone while the others recolour around it |

That last row is the point as much as the first three. An uncoloured default of
**0** is a real palette, and writing it flattened every coloured actor in a room
the first time this shipped (a big animated actor in a parallax room and the
player both drew palette-0 flat). 255 says *nothing authored*; `vm.canim` stands down on it.

## Running it

```bash
python projects/vm-palanim/assets/gen.py                 # art + world + studio.toml + clips.mos
python -m mosaik_vm projects/vm-palanim/scripts -o projects/vm-palanim/src/scripts.mos
python mosaik8.py build --platform gameboy_color projects/vm-palanim
python projects/vm-palanim/verify.py                     # PyBoy, reads OAM
```

Walk the player with the D-pad; it animates from its own (uncoloured) clips and
must stay one colour throughout.

## Per console

The write is the portable `sprite.set_palette`, so each console shows what it
can - and the two that can show **nothing** are targeted deliberately, because
one project building everywhere is half of what is being proven:

| Console | What you see |
|---|---|
| Game Boy Color, Analogue Pocket | everything: OBJ palettes 0-7 |
| Game Boy, Mega Duck | the **flasher** (OBP0/OBP1 are the only two sprite palettes); the colour-palette numbers are in OAM but the hardware ignores them, so `blinker` and `stately` hold still |
| Atari Lynx, PC Engine | the recolours on their 4 sprite slots (the value folds `& 3`, which is why this project authors 0-3) |
| Master System, Game Gear | **nothing** - one sprite palette, so every write is an honest no-op and all four orbs render in slot 0's colours. The frames still STEP (the orb's highlight moves), so you can see the clip running under a palette that cannot change |

The NES is excluded for the reason `vm-danim` excludes it: `vm.canim`'s clip
callbacks carry 4 argument bytes and a 6502 function-pointer call takes 2.

## What the ROM says

`verify.py` reads the OAM **attribute byte**, never the screen - the palette
lives there, and a screenshot would only say the picture changed, not which
mechanism changed it. It is keyed on OAM **slot** (actor *i* fans 4 objects
from base 4*i*, the player above the 8-slot pool), because two actors' screen
x ranges can overlap while the slots are deterministic.

Measured on the GBC build: blinker `{1, 2}`, flasher DMG-select `{clear, set}`
on colour palette `{0}` throughout, stately `3` standing and `2` walking, and
the player `{0}` for the whole run.

**One trap the probe documents:** `vm.core.run()` places actors
(`actor.render`) BEFORE it ticks the animator, so a freshly spawned fan wears
its base property for exactly ONE frame before its first `apply` writes the
clip's palette - measured here as the blinker appearing on frame 69 with
attribute 0 and being on palette 1 from frame 70. That is the documented frame
order, so `verify.py` warms up past it and asserts on the steady state.

## Files

- `assets/gen.py` - regenerates everything: the 2-tile scene, the orb sheet
  (two frames whose highlight moves, so the frame step shows even where the
  palette write is ignored), `studio.toml [animations]` and `src/clips.mos`.
- `scripts/main.evt.toml` - places the three actors and assigns their clips;
  `pace` walks `stately` up and down. No per-frame bytecode: every recolour
  after placement is `F_PAL` driving `vm.canim`.
- `src/main.mos` - the `vm-danim` data-driven shell plus exactly one new
  registration, `canim.set_clip_pal(clips.frame_pal)`, and four tellable sprite
  palettes to recolour between.
