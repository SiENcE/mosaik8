# Box-Pusher — a second game on the mosaik game-framework

A Sokoban-style grid puzzle: push every crate onto a goal. One source, all nine
consoles. It is the proof the framework generalises to a genre *deliberately
unlike* the game slice (a turn-based grid puzzle, not free-roam action).

## What it demonstrates

The framework is **à la carte source you compose** — a game imports only the
pieces its genre needs, and *owns its loop* (the engine kits don't drive your
game logic; the lone framework callback is `engine.anim`, which Box-Pusher's
static tokens don't need). Box-Pusher composes two Tier-A engine modules:

- **`engine.pad`** — d-pad **edge** detection (one cell-step per tap, not held).
  Grid movement is what made this game *add* d-pad edges to `engine.pad`
  (previously only the action buttons were edged) — the framework grew to fit a
  new genre, touching nothing else.
- **`engine.camera`** — the follow camera over the 256×256 warehouse, with its
  exported `camx`/`camy` read directly to place the sprites.

It intentionally does **not** use `engine.collision` (pixel box-vs-corner
collision) or `genre.topdown` (free-movement facing / chase AI): a grid puzzle
checks cell types directly and moves a whole cell at a time. Those modules are
exercised by `lib/genre/topdown_template.mos` and the unit test instead — which is
the point: different genres pull different framework pieces.

## Framework source comes from the shared `lib/` path

`import "engine.pad"` / `import "engine.camera"` resolve to `lib/*.mos` via the
shared `lib/` search path — this project carries **no** `src/game/` copies. The
build reports the pulled-in modules under "Library modules", and tree-shaking
keeps only the ones actually imported (this grid genre uses just `engine.pad` +
`engine.camera`). To override a module locally instead, drop a modified copy in
`src/game/` — see `projects/vendor-override` for that pattern.

## Controls

D-pad to move/push (one cell per tap); push every crate onto the diamond marks.
When all crates are home, **A** resets for another round.

## Build & run

```
python mosaik8.py build projects/box-pusher
# then e.g. PyBoy for the .gb, or emu/libretro/run_lynx.py for the .lnx
```
