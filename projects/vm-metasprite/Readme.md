# vm-metasprite

What the **per-object metasprite descriptor** (`sprite.set_meta_list`) buys,
what it costs, and what VM8 can already do without it. One room, four
readouts, one 28-tile sheet.

```
python projects/vm-metasprite/assets/gen.py          # regenerate everything
python mosaik8.py build --platform gameboy projects/vm-metasprite
python projects/vm-metasprite/verify.py              # PyBoy behaviour checks
```

## What a metasprite is, and what "dedupe" means

Hardware sprites on these consoles are **8x8 pixels** (8x16 with
`[build] obj_8x16`). Anything bigger is several hardware objects moved
together: a **metasprite**. Our model has always been a dense `w x h`
rectangle whose cells take **consecutive** tiles from a base - `set_meta(base,
tile, w, h)` means "tile, tile+1, tile+2, ...".

That is compact and fast, but it fixes two things:

* every cell of every frame needs **its own tile**, even if two frames are
  99% the same picture;
* the rows are **exactly 8 px apart**, because that is what a grid is.

Sprite VRAM is the scarce resource here - the Game Boy has **128 OBJ tiles**
that do not collide with background data, and a whole room's cast shares them.

## The four readouts

| | what it is | tiles |
|---|---|---|
| **A** (left) | **DESCRIPTOR** counter: ONE actor, 100 frames (`00`..`99`), each frame two objects pointing at ten pooled digit cells | **20** |
| **B** (right) | the **NATIVE VM8 alternative**: two actors of one `digit` kind, 10 frames each. One sheet upload serves both | **20** |
| **C** (left, below) | **DESCRIPTOR** stack: head + body **11 px** apart - rows overlap | **4** (shared) |
| **D** (right, below) | the same picture as a **DENSE** rectangle: rows **16 px** apart, spacing baked into its own art | **4 more** |

A and B count in lockstep from the same variable, so you can see they draw the
identical readout. `verify.py` asserts they agree on all 400 sampled frames.

**A dense version of A would be 100 frames x 4 tiles = 400 tiles** - over
three times the GB's entire OBJ table. That is the arithmetic that made
the shooter conversion's score display stop counting at 7.

## So do we need it? Honestly: for authoring, usually not

**B is the better answer for a score you are writing yourself.** Two actors of
a ten-frame digit kind cost the same 20 tiles, need no descriptor, and are
plainer to read. VM8 has a third answer that is better still: the **HUD var
field** (`scripts/hud.toml` -> `generate_hud`), which draws a number as
background/window text and costs no sprite VRAM at all - that is what
`projects/vm-hud` shows and what a new project should reach for first.

The descriptor earns its place in exactly two situations:

1. **Fidelity conversion.** A reference-engine project's author already drew the
   two-digit sprite with 100 frames; the importer has to convert what is
   there, not redesign it. That is the whole reason this exists.
2. **Row overlap** (C vs D). *This one has no alternative.* A metasprite's
   rows are 8 px apart and a second actor is a separate entity with its own
   slot, position and animation state. When a character's head sits 11 px
   above its body, only a per-object list can say so - which is why GB
   Studio's platform player has a 4 px gap under its feet in a dense port.

## What it costs

The per-object machinery is about **1.1 KB of RESIDENT image** (bank 0),
measured on the reference-engine conversion: `gbs_move` +537 B, `gbs_set_` +532 B.
It cannot bank - a prelude helper never can. On a project with room to spare
that is nothing; on the 17-scene reference-engine sample conversion, whose GBC build had **94 B**
spare, it was fatal. So it is **opt-in twice over**:

* the C prelude is emitted only if a program calls `sprite.set_meta_list`;
* `vm.canim`'s desc path folds away behind the build-stated `VM_META_LIST`;
* and the reference-engine importer probes the dense trim first, building pools only
  for kinds that would otherwise be **capped**.

A world with no descriptors is byte-identical to before.

## How the data is shaped

`assets/gen.py` writes it by hand, which is also the smallest worked example
of the format:

```python
# the POOL: ordinary [[sprite]] cells, ten 8x16 digits + a head + a body
write_sprite_manifest(png, [("d0", [0, 0, 8, 16]), ...])

# the FRAMES: named [[frame]] entries, objs = (dy, dx, pool cell, props)
append_frame_descs(png, [
    ("counter_f42", 16, 16, [(0, 0, 4, 0), (0, 8, 2, 0)]),   # "42"
    ("stack_f0",     8, 27, [(0, 0, 10, 0), (11, 0, 11, 0)]),  # 11 px apart
])
```

`mosaik_anim` turns those into `DESC`/`D_OFF`/`D_CNT` plus `draw()`,
`is_desc()` and `fan()`; `main.mos` registers the seam with
`canim.set_clip_draw(clips.draw, clips.is_desc)`. Note `main.mos` also assigns
each actor's OAM base by hand - this world mixes sprite shapes, which is
exactly what a generated `rooms.mos` handles at room load.

Builds on gameboy / gameboy_color / sms / gamegear / lynx / pce.
