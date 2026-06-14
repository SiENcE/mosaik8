# Using MosaiK8 — developer guide

MosaiK8 is a retro-console dev framework built around **mosaik**, a high-level
language that compiles (via C) to nine consoles from one source: Game Boy, Game
Boy Color, Analogue Pocket, Mega Duck, Sega Master System, Game Gear, NES, Atari
Lynx, and PC Engine.

> Naming: the framework/CLI is **MosaiK8**; the language is **mosaik**.
> `.mos` = source, `.c` = generated intermediate, `.gb`/`.gbc`/`.lnx`/… = ROM.

## 1. One-time setup

Fetch the toolchains + emulators (all gitignored):

```
python setup_tools.py            # GBDK-2020, cc65, libretro cores, pip deps
python setup_tools.py --check    # report what's installed
python setup_tools.py --only gbdk,cc65,cores,python
```

This installs GBDK-2020 → `gbdk/`, the cc65 snapshot → `cc65/`, the libretro
cores (Handy/Beetle Lynx, Beetle PCE, Genesis Plus GX, FCEUmm) → `emu/libretro/`,
and the pip packages (`pyboy`, `libretro.py`, `pillow`, `toml`). (`lynxboot.img`
is copyrighted and must be supplied by hand; Handy runs Lynx homebrew without it.)

Toolchain discovery: GBDK via `GBDK_HOME` → a `gbdk/` next to the tool → `PATH`;
cc65 via `CC65_HOME` → a `cc65/` next to the tool → `cl65` on `PATH`.

## 2. Build a program

There are exactly **two build modes**. There is no "scan the tree" mode and no
`run` command (verify ROMs with an emulator — §6).

### Single file

```
python mosaik8.py build samples/bounce.mos                 # default platform
python mosaik8.py build --platform lynx samples/hello.mos  # pick a console
python mosaik8.py build --platform gameboy --asset tiles.png game.mos
```

Output goes to a `build/<platform>/` next to the source. Single-file mode also
pulls in the file's transitive non-stdlib imports, resolved next to it with dots
as subfolders (`import "helpers.math"` → `helpers/math.mos`).

### Project (reads `mosaik.toml`)

```
python mosaik8.py build projects/box-pusher    # a dir, or a path to mosaik.toml
```

Project mode compiles **every `.mos` under `[source] folder`** into one program
and builds each `target_platforms` console.

Other CLI commands: `clean`, `init`, `version`.

## 3. `mosaik.toml`

Only these keys are applied (everything else prints a ⚠️ warning so typos don't
pass silently):

```toml
[project]
name = "my-game"
target_platforms = ["gameboy", "lynx", "pce"]   # any of the nine

[source]
folder = "src/"

[build]
output_dir = "build"
rom_size = "64KB"     # GB family only: cart geometry (banking); optional
ram_size = "8KB"      # GB family only; optional

[assets]
sprites = ["assets/sprites.png"]   # PNG -> tiles, injected into the build
```

Platform names accept aliases (`atari_lynx` → `lynx`, `pc_engine` → `pce`, …).

## 4. Write a program

A minimal portable program (builds on all nine consoles):

```mosaik
module "main" {
    import "platform.video"
    import "graphics.text"
    function main() {
        video.enable_lcd()
        text.print_string(2, 3, "HELLO")
        loop { video.wait_vblank() }
    }
    export main
}
```

Key language facts (full spec: [`mosaik_lang_spec.md`](mosaik_lang_spec.md)):

- **Modules**: `module "name" { … export a, b }`; `import "other"` then call
  `alias.fn(...)` (alias = the last dotted segment). Multi-module programs mangle
  symbols to `<module>_<name>`; `main()` is the entry point.
- **Types**: `u8`/`i8`/`u16`/`i16`/`bool`, `struct`, `enum`, `array[T, N]`.
  Params (incl. structs) pass **by value**; **arrays can't be passed by value**.
- **Control**: `if/else if/else`, `loop`, `while`, `for x in a..b` (end
  exclusive), `switch/case/default`, `break`, `continue`, `return`.
- **Operators**: `+ - * / %`, comparisons, `and/or/not`, `= += -=`. **No bitwise
  operators.** The parser is **newline-terminated** (one statement/expression per
  line; a call's args stay on one line).
- **Conditional compilation**: `if platform == "lynx" { } else { }` keeps only
  the matching branch (evaluated against the build target).
- **`const` arrays** become real C tables; cross-module `const`-array indexing
  works (`data.MAP[idx]`). Exported `var`s/arrays are shared globals.

### The stdlib (per-console support: spec §5.5)

- `platform.video` (lcd, `wait_vblank`, show/hide sprites/window/background),
  `platform.input` (`held`/`pressed` + `INPUT_*`), `platform.system`
  (`delay`/`random`/`seed_random`), `platform.hardware` (raw `hw.read/write`),
  `platform.sound` (`beep`/`stop`).
- `graphics.sprite` (`set_data`/`set_tile`/`move`/`set_prop`/`set_meta`),
  `graphics.bkg` (32×32 scrollable tilemap), `graphics.window` (GB family only),
  `graphics.text` (GBDK `printf` — large; gated to actual use; **overflows the
  NES**), `graphics.palette` (the GB 4-colour-slot model on all nine; degrades to
  greys on DMG/Duck), `graphics.draw` (Lynx only).
- `native.lynx` (`fade_in/out`, `screen_shake`, `jingle` — no-ops elsewhere).

Calling a stdlib function a console lacks is a **clear compile error**, not a
link failure. `SCREEN_WIDTH`/`SCREEN_HEIGHT` (pixels) and `SCREEN_COLS`/`ROWS`
(text cells) are defined per console; `sprite.move` takes **screen-pixel** coords
everywhere.

## 5. Assets (PNG → tiles)

Put PNGs in `[assets] sprites = [...]` (project) or `--asset foo.png`
(single-file). `mosaik_assets.py` converts each to GB 2bpp (the universal
format) and injects `const uint8_t <stem>_tiles[]` + `#define <stem>_tile_count`.
A sheet `foo.png` with a `foo.sprites.json` sidecar is cut into named
sub-sprites. A ≤4-colour indexed PNG also emits a `<stem>_palette[4]`. See the
"Asset pipeline" notes in `CLAUDE.md` and `projects/shmup` (a worked example).

## 6. Testing ROMs

Building proves a ROM *links*. To check it *runs*:

- **Game Boy family** (`.gb`/`.gbc`/`.pocket`) — drive [PyBoy](https://docs.pyboy.dk/)
  headlessly and inspect the screen / VRAM / OAM:

  ```python
  from pyboy import PyBoy
  pb = PyBoy("build/gameboy/game.gb", window="null")
  for _ in range(200): pb.tick()
  img = pb.screen.image          # 160x144 PIL image
  tile = pb.memory[0x9800]       # BG tilemap; OAM at 0xFE00 (y,x,tile,attr)
  pb.button_press("up"); pb.tick(); pb.button_release("up")
  pb.stop()
  ```

  PyBoy caveat: WRAM (`0xC000-0xDFFF`) reads return zeros and registers aren't
  meaningful at frame boundaries — assert via the screen, VRAM, and OAM only.
  (And note: scripted `button_press("right")` can fire spurious repeated edges —
  verify edge logic with DOWN/UP/LEFT or on the Lynx.)

- **Lynx / PCE / SMS / GG / NES** — the libretro harness:

  ```
  python emu/libretro/run_lynx.py build/lynx/game.lnx 400 --png out.png
  python emu/libretro/run_lynx.py build/lynx/game.lnx 600 --press RIGHT@420-600 --png r.png
  python emu/libretro/run_lynx.py build/pce/game.pce 300 --core mednafen_pce_fast --png p.png
  #   --core genesis_plus_gx  (SMS .sms / Game Gear .gg)   --core fceumm  (NES .nes)
  ```

  It runs N frames, reports the final frame's distinct colours (exit 1 if blank),
  saves screenshots, and holds buttons over frame ranges. Handy boots homebrew
  BIOS-less but slowly — use **≥300–400 frames** before reading the screen. (Mega
  Duck and Analogue Pocket have no core; verify those via their GB/GBC-equivalent
  build in PyBoy.)

The repo's own gates: `python tests/run_all.py` (unit tests), `--samples` (build
every sample × console + the projects), and `python tests/verify_roms.py`
(behavioural checks; `--lynx`/`--pce`/`--sms`/`--gg`/`--nes` add those cores).

## 7. Building a game on the framework

For multi-room games, scenes, a follow camera, collision, and genre loops, use
the **game-framework** (`lib/game/`): see
[`game-framework.md`](game-framework.md), the
[how-to-add-a-genre](adding-a-genre.md) guide, and the worked examples
`projects/zelda-slice`, `box-pusher`, `scene-demo`, `platformer`.
