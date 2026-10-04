# vm-snake

**A complete game as bytecode.** `scripts/snake.v8s` is VM8 text assembly: the
body, the food, the walls, the score, the speed and game over are all VM8
instructions, and the board lives in heap cells 0..19, one 10-bit row mask
each. It is the first-party sample for the `.v8s` front end and the `.board`
convention: open it in the studio and the VM8 playground runs it, draws its
board, and steps it in the debugger.

The native side is only a **renderer** (`src/board.mos`). That is the split the
reference playground makes (its `.board` directive turns on a JavaScript
`renderBoard()` that reads the VM heap), with mosaik in place of the
JavaScript. Nothing in the mosaik source knows a rule of Snake.

```
python mosaik8.py build --platform gameboy projects/vm-snake
python projects/vm-snake/verify.py        # 25 headless checks on the real ROM
```

Regenerate the two generated modules (neither needs the studio):

```
python assets/gen_scripts.py      # scripts/snake.v8s -> src/scripts.mos
python assets/gen_tiles.py        # the computed tile art -> src/tiles.mos
```

## Controls

| Button | What it does |
|---|---|
| d-pad | turn (a reversal onto the snake's own neck is ignored) |
| A or START | start from the title, restart after game over |

The program reads those six buttons ITSELF, which is what the playground's
"This program reads: ..." line reports (`VmAssembly.buttons_read`): the four
directions are `INPUT_ATTACH`ments, and the title / game-over wait polls A and
START held (engine states 16 + button).

## The heap

```
 0..19  row masks: a set bit is a segment or the food   20/21  food x, y
 22/23  head x, y      24  head mask (1 while a snake is on the board)
 25     NEXT (255: a snake has no preview)     26  score (food eaten, shown x10)
 27     length         28  level (0..4)        29  note (0 / 1 title / 2 over)
 30     heading        31  wanted heading (button ids 4 up .. 7 right)
 32/33  ring head / tail index                  34..41  scratch for one move
 42     countdown      43  best score (survives a restart: the heap is not
                                       cleared)
 44     board height (seeded by the host)      64..127  the body ring
```

Cells 20..28 sit where the `.board` convention puts a falling piece (22 PX, 23
PY, 24 MASK) and the panel values (26 SCORE, 27 LINES, 28 LEVEL), so the
reference playground's own board view draws this game sensibly: the rows are
the body and the food, and the head is a one-cell "piece" drawn brighter.

A position is `y * 16 + x`. The body is a **ring of 64 positions** in cells
64..127: a move pushes the new head and pops the tail. VM8 has no computed heap
addressing (spec R1), so the ring and the rows are reached through SWITCH
tables (`qread`, `qwrite`, `readrow`, `writerow`), the VM8 idiom for an indexed
table. Past 64 segments the snake still eats and
scores; it just stops getting longer.

## QUANT: the spec minimum, and why that is enough

This project declares **no** `[build] vm_quant`. Only `main` writes game state:
the four direction attachments store the wanted heading in cell 31 and nothing
else, and `main` checks it for a reversal when it takes its next move. With one
writer there is no critical section a thread slice could split, so the program
is correct at QUANT 16.

What QUANT 16 does cost is time. A move is ~75 instructions, so it spreads over
five VM frames, and the playground's Threads pane marks thread 0 as cut off by
the slice. That mark is accurate and harmless here; it is also what sets the
snake's top speed (below).

## The board height is the host's

The program plays on 10 columns and **cell 44** rows. The shell seeds that
before boot (`board.seed_height`, from the console's text grid, with a wall
above and below), and the program reads it once:

| Console | Rows | Board on screen |
|---|---|---|
| SMS, PC Engine | 20 | the full `.board`, closed well |
| Game Boy / Color, Game Gear | 16 | closed well, key hints |
| Atari Lynx | 10 | closed well, no key hints |

A host that seeds nothing (the reference playground, the studio's simulator)
gets 20. Unlike a falling-block well, nothing here can be folded or hidden: the
snake goes everywhere, so the board must fit.

## Speed

Level 1 is a 5-VM-frame countdown plus the move's own ~5 VM frames; each level
(one per five food, up to level 5) takes a frame off the countdown. Measured
2026-09-22 on the real ROMs at level 1:

| Console | LCD frames per cell | Instrument |
|---|---|---|
| Game Boy | 15 | PyBoy (`verify.py` asserts 12..19) |
| SMS / Game Gear | 16.2 | `emu/libretro/lynx_probe.py --rate` on the head's x cell |
| Atari Lynx | ~18 (its own LCD rate) | the same probe, `.lbl` via `lynx_relabel.py` |
| PC Engine | not measured | runs and plays |

The level 5 floor is about 6 VM frames a cell (the move itself). A faster snake
would need the move to fit one slice, i.e. `vm_quant = 512`; that is a pace
choice this sample does not make.

## Everything is background tiles

The well, the walls, the snake, the food, the panel and its text are all
background tiles. The 5x7 glyph set is baked into the tile table by
`assets/gen_tiles.py`, because the console font is a resource `vm.core`'s
dialogue box owns (GBDK loads it over background tile data), and on the Lynx
text goes straight into a framebuffer a re-blitting present wipes.

**The two notes are drawn IN the well**, not in a dialogue box: on the GB
family `vm.core` answers `UI_TEXT` by moving the box onto the WINDOW layer,
which runs from its first row to the bottom of the screen, so a box in the
middle of the well would black out everything under it. Cell 29 raises the
note, `board.mos` draws it, and the program has no `UI_TEXT` at all (dispatch
pruning then drops the whole dialogue-box path from the ROM).

`core.font_preload()` runs **before** the tiles are uploaded, or GBDK's first
text call would land its 96 glyphs over them and clear the tilemap.

## Colour: one art, three hardware tiers

The art is COMPUTED (a rounded square, a disc, a brick bond) in 16-pen index
space, and lowered per console by the engine's own tiers:

| Console | Background colour | How |
|---|---|---|
| SMS / Game Gear / PC Engine | 16 | 4bpp tiles + `palette.load_bkg16` |
| Atari Lynx | 16 | the same, via `[build] lynx_bkg16` |
| Game Boy Color / Pocket | 3 palettes x 4 | 2bpp tiles + a per-cell palette slot (UI, snake, food) |
| Game Boy / Mega Duck | 4 greys | the same 2bpp tiles; snake and food differ in SHAPE |

`load_bkg16` and `load_bkg_set` are alternatives, not a pair: the PC Engine has
both and they write the same palettes, so `board.upload_tiles` calls one per
console.

## The Lynx build

It links with about 2 KB of the Lynx MAIN area to spare (ld65 map,
2026-09-22), holding the bytecode blob resident (`lynx_code_resident`) and the
16-colour strips (`lynx_bkg16`). `bkg_strip_w = 20` because the background
never scrolls, and the sprite tables are capped at one entry because the game
places no sprite.

## The renderer's rules

`update` (the `core.set_bkg_anim` seam, which fires every frame on every
console; `set_hud` would freeze the Lynx, whose present is skipped when nothing
changed) re-reads the row masks every frame and rebuilds only the rows whose
mask changed or that the head or the food entered or left. A row goes out in
one `bkg.set_tiles` behind a `shown` cache, so a frame where nothing moved
writes no tile. The head and the food are drawn by POSITION as well as by row
bit, so a head the program has moved one instruction before setting its row
bit is not drawn a frame late.
