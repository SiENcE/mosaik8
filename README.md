# MosaiK8

**MosaiK8** is a framework for retro console development built around **mosaik**,
a modern, high-level programming language. The mosaik compiler has **two C
backends** — GBDK and cc65 — that together target nine consoles from one
language and one set of source files.

- **GBDK backend** (default) — emits GBDK C, linked by the GBDK-2020
  toolchain (`lcc`/`sdcc`) into Game Boy, Game Boy Color, Analogue Pocket,
  Mega Duck, Sega Master System, Game Gear, and NES ROMs.
- **cc65 backend** — emits cc65 C, linked by cc65 (`cl65`) into
  Atari Lynx (`.lnx`) and PC Engine (`.pce`) ROMs.

The lexer, parser, type-checker, and all logic codegen are shared; only the
prelude and standard-library lowering differ per backend. Programs that stay
within the portable stdlib subset (text, input, timing, sound, sprites) build for
every console unchanged.

> **File extensions**
> - `.mos` — mosaik **source** files
> - `.c` — generated C (intermediate build output)
> - `.gb` / `.gbc` / `.pocket` / `.duck` / `.sms` / `.gg` / `.nes` — GBDK ROM output
> - `.lnx` / `.pce` — cc65 ROM output (Atari Lynx / PC Engine)

## 🎮 Features

- **Modern Syntax**: Familiar syntax inspired by Lua/Python for retro console development
- **Nine Consoles**: Game Boy / Color / Pocket / Mega Duck, SMS, GG, NES (GBDK) and Atari Lynx, PC Engine (cc65) — one language, two backends
- **Portable Core**: Structs, enums, modules, control flow, and the portable stdlib subset compile identically on every target
- **Capability Gating**: Calling a stdlib function a console lacks is a clear compile-time error — not a silent no-op or link failure
- **Standard Library**: Built-in helpers for video, input, sprites, text, sound (a portable beep channel on all nine consoles), scrollable background tilemap (all nine consoles — hardware tilemap on GBDK targets, VDC BAT on the PCE, a composited Suzy background sprite on the Lynx), window (GB family), and draw primitives (Lynx)
- **Asset Pipeline**: PNGs listed in `mosaik.toml` (or passed with `--asset`) become tile data on every console — no hex arrays in source
- **One-shot Setup**: `setup_tools.py` downloads the toolchains and test emulators for you

> For a precise, status-tagged list of what the language implements today versus
> what is still planned, see [`docs/mosaik_lang_spec.md`](docs/mosaik_lang_spec.md).

## 📋 Requirements

- **Python 3.7+** with the `toml` package
- **GBDK-2020** — installed by `setup_tools.py` into `gbdk/`; also auto-detected via `GBDK_HOME` or `PATH`
- **cc65** — installed by `setup_tools.py` into `cc65/`; also auto-detected via `CC65_HOME` or `PATH` (needed only for Lynx/PCE targets)
- **PyBoy** — Python Game Boy emulator, optional, for running GBDK ROMs (see below)
- **libretro.py + Lynx cores** — optional, for headless Atari Lynx testing

### One-shot setup

`setup_tools.py` downloads and installs everything into the folders the build
tool expects (all of them gitignored):

```bash
python setup_tools.py            # install everything that is missing
python setup_tools.py --check    # report what is installed, change nothing
python setup_tools.py --only gbdk,cc65     # just the toolchains
python setup_tools.py --only cores,python  # just the test emulators
python setup_tools.py --force    # reinstall even if already present
```

| Component | Installs | Used for |
| --- | --- | --- |
| `gbdk` | GBDK-2020 (latest GitHub release) → `gbdk/` | building all GBDK-backend consoles |
| `cc65` | cc65 Windows snapshot → `cc65/` | building Atari Lynx / PC Engine |
| `cores` | Handy + Beetle Lynx libretro cores → `emu/libretro/` | headless Lynx testing |
| `python` | `pyboy`, `libretro.py`, `pillow`, `toml` (pip) | build tool + emulator harnesses |

Notes:
- cc65 binary snapshots exist for Windows only; on Linux/macOS install cc65
  from your package manager and set `CC65_HOME`.
- The Beetle Lynx core needs the real Lynx boot ROM (`lynxboot.img`, 512
  bytes, copyrighted — not downloadable). Drop it into `emu/libretro/`
  yourself; without it the harness falls back to the Handy core, which boots
  homebrew BIOS-less.

### Pointing at your own toolchains

If you already have GBDK-2020 or cc65 installed elsewhere, set an environment
variable instead of running the installer:

```bash
# Windows (PowerShell)
$env:GBDK_HOME = "C:\path\to\gbdk-2020"   # for GBDK consoles
$env:CC65_HOME = "C:\path\to\cc65"        # for Lynx / PC Engine

# Linux / macOS
export GBDK_HOME=/path/to/gbdk-2020
export CC65_HOME=/path/to/cc65
```

Search order for each toolchain:
1. The `GBDK_HOME` / `CC65_HOME` environment variable
2. A `gbdk-2020/` or `gbdk/` / `cc65/` folder next to `mosaik8.py` (what `setup_tools.py` creates)
3. The system `PATH` (`lcc` / `cl65`)

### Running ROMs (optional, for verification)

MosaiK8 only builds ROMs — it has no `run` command, so it never depends on an
installed emulator. To see a build working, open the ROM in any emulator
directly.

[PyBoy](https://github.com/Baekalfen/PyBoy) is the recommended way to *verify*
Game Boy ROMs: it's a Python-based emulator that can run headless and expose the
rendered screen and memory, so you can assert that something actually drew (see
[Testing](#-samples--tests)).

```bash
pip install pyboy
pyboy samples/build/gameboy/bounce.gb            # GBDK ROMs
```

Atari Lynx and PC Engine ROMs run headlessly through the bundled libretro
harness:

```bash
python emu/libretro/run_lynx.py samples/build/lynx/pong.lnx 300 --png out.png
python emu/libretro/run_lynx.py samples/build/pce/pong.pce 300 --core mednafen_pce_fast --png out.png
```

Any GUI emulator that accepts a ROM path works too (BGB/mGBA for `.gb`/`.gbc`,
Mednafen for `.lnx`/`.pce`/`.sms`/`.gg`/`.nes`, …).

## 🚀 Quick Start

### 1. Initialize a New Project

```bash
python mosaik8.py init my_game
cd my_game
```

This creates:
```
my_game/
├── mosaik.toml       # Project configuration
└── src/
    └── main.mos      # Main source file
```

### 2. Write Your First Game

Edit `src/main.mos`:

```mosaik
module "main" {
    import "platform.video"
    import "platform.input"
    
    var player_x: u8 = 80
    var player_y: u8 = 72
    var frame_count: u8 = 0
    
    function update_player() {
        if input.pressed(INPUT_LEFT) and player_x > 0 {
            player_x -= 1
        }
        if input.pressed(INPUT_RIGHT) and player_x < 152 {
            player_x += 1
        }
        if input.pressed(INPUT_UP) and player_y > 0 {
            player_y -= 1
        }
        if input.pressed(INPUT_DOWN) and player_y < 136 {
            player_y += 1
        }
    }
    
    function main() {
        video.enable_lcd()
        
        loop {
            frame_count += 1
            update_player()
            video.wait_vblank()
        }
    }
    
    export main
}
```

### 3. Build and Run

```bash
# Build for Game Boy (uses mosaik.toml in the current directory)
python mosaik8.py build

# Build for Game Boy Color
python mosaik8.py build --platform gameboy_color

# ...or any other supported console
python mosaik8.py build --platform nes
python mosaik8.py build --platform sms
python mosaik8.py build --platform lynx samples/hello.mos

# To see a build run, open the ROM in an emulator directly (no `run` command):
pyboy samples/build/gameboy/bounce.gb                       # Game Boy
python emu/libretro/run_lynx.py samples/build/lynx/hello.lnx # Atari Lynx (headless)
```

Supported `--platform` values: `gameboy`, `gameboy_color`, `analogue_pocket`,
`megaduck`, `sms`, `gamegear`, `nes` (GBDK backend), and `lynx`, `pce` (cc65
backend). Each links a native ROM (`.gb`, `.gbc`, `.pocket`, `.duck`, `.sms`,
`.gg`, `.nes`, `.lnx`, `.pce`).

Two backends, one language: GBDK consoles are linked by GBDK's `lcc`; the cc65
consoles (Atari Lynx, PC Engine) are linked by cc65's `cl65`. The
lexer/parser/typechecker and all logic codegen are shared — only the
standard-library lowering differs. A program that stays within the portable
stdlib subset (text, input, timing, sound, sprites) builds for **all nine
consoles** unchanged; size the world with the per-target
`SCREEN_WIDTH`/`SCREEN_HEIGHT` constants and it *behaves* right everywhere too
(see `samples/bounce.mos`). What each console supports is recorded in a
capability registry; calling something a console lacks (the window layer
outside the Game Boy family, `graphics.draw` outside the Lynx, GB `REG_*`
registers elsewhere, ...) is a clear compile-time error — gate such code with
`if platform == "..."`. See `docs/cc65-backend-plan.md` and
`docs/platform-support-plan.md`.

```bash
# Build the same source for a GBDK console and both cc65 consoles
python mosaik8.py build --platform gameboy samples/hello.mos
python mosaik8.py build --platform lynx    samples/hello.mos
python mosaik8.py build --platform pce     samples/hello.mos
```

## 🏗️ Build Modes

The build tool has exactly two modes, chosen by what you pass to `build`. There
is **no "scan the whole tree" mode** — you always point it at one source file or
one project.

### Single-file mode — `build <file.mos>`

Pass a single `.mos` file. A `build/` folder is created **next to that file**,
and the generated `.c` and ROM are named after the source file.

```bash
python mosaik8.py build samples/text_simple.mos
# -> samples/build/gameboy/text_simple.c
#    samples/build/gameboy/text_simple.gb
#    samples/build/gameboy_color/text_simple.c
#    samples/build/gameboy_color/text_simple.gb

# Limit to a single platform:
python mosaik8.py build --platform gameboy samples/bounce.mos
```

### Project mode — `build <mosaik.toml>`

Pass a `mosaik.toml` (or a directory containing one, or nothing to use
`./mosaik.toml`). The tool reads the project file, compiles the `.mos` files
in the `[source] folder`, writes output to the `[build] output_dir`, and names
the `.c` and ROM after `[project] name`. Target platforms come from
`[project] target_platforms`.

```bash
# Any of these build the projects/game project:
python mosaik8.py build projects/game/mosaik.toml
python mosaik8.py build projects/game        # directory containing mosaik.toml
cd projects/game && python ../../mosaik8.py build   # uses ./mosaik.toml
# -> projects/game/build/gameboy/game.c
#    projects/game/build/gameboy/game.gb
```

There is no `run` command — open the built ROM in an emulator directly (PyBoy
for GBDK consoles, the libretro harness for the Lynx).

> Note: TOML string values must be quoted. Use `folder = "src/"`, not
> `folder = src/`. A malformed project file is reported as an error rather than
> silently ignored.

## 📁 Example Project Structure

```
my_project/
├── mosaik.toml              # Project configuration
├── src/                     # Source files (.mos)
│   ├── main.mos             # Main module
│   ├── player.mos           # Game modules
│   └── enemies.mos
├── assets/                  # Graphics, audio, data
│   ├── sprites/
│   └── backgrounds/
└── build/                   # Build output
    ├── gameboy/
    │   └── my_project.gb   # Game Boy ROM
    └── gameboy_color/
        └── my_project.gb   # Game Boy Color ROM
```

### Multi-file programs (cross-file module linking)

All `.mos` files of a build are compiled **together into one program**: in
project mode every file under `[source] folder`, in single-file mode the given
file plus everything it (transitively) imports — `import "player"` loads
`player.mos` next to the importing file (`import "game.utils"` →
`game/utils.mos`). Use the last segment of a module's name to reference it,
and remember only exported names are visible:

```mosaik
-- src/main.mos                          -- src/player.mos
module "main" {                          module "player" {
    import "player"                          var x: u8 = 80
                                             function update() { ... }
    function main() {                        export update, x
        loop { player.update() }         }
    }
    export main
}
```

Calling a function a module doesn't export, importing a module no file
defines, or defining the same module twice are clear compile-time errors. See
`projects/multifile` for a working example.

## ⚙️ Configuration

### Project Configuration (`mosaik.toml`)

```toml
[project]
name = "my_game"                              # names the output .c and ROM
version = "1.0.0"
target_platforms = ["gameboy", "gameboy_color"]

[source]
folder = "src/"                               # where .mos sources live (relative to this file)

[assets]
sprites = ["assets/sprites.png"]              # PNGs converted to tile data at build time

[build]
optimization_level = 2
debug_symbols = true
rom_size = "32KB"
ram_size = "8KB"
output_dir = "build"                          # build output (relative to this file)

[platforms.gameboy]
features = ["save_support"]
memory_layout = "standard"

[platforms.gameboy_color]
features = ["save_support", "color", "speed_switch"]
memory_layout = "expanded"

[dependencies]
stdlib = "1.0"
```

## 🎨 Assets (PNG → tiles)

Instead of embedding hex tile arrays in source, list PNGs under `[assets]`
(project mode) or pass `--asset file.png` (single-file mode). The build
converts each PNG to Game Boy 2bpp tile data and injects it into the program
as two ready-to-use constants, named after the file:

```text
assets/sprites.png  ->  sprites_tiles        (const u8 array, 16 bytes/tile)
                        sprites_tile_count   (number of 8x8 tiles)
```

```mosaik
sprite.set_data(0, sprites_tile_count, sprites_tiles)
```

GB 2bpp is the interchange format on **every** console: the GB family uploads
it directly, the NES and Game Gear/SMS convert it in GBDK's `set_sprite_data`
compatibility layer, and the Lynx sprite engine converts it for the Suzy
blitter — so one PNG serves all targets.

Authoring rules (see `mosaik_assets.py` for details):

- The image must be a multiple of 8 pixels each way; it is cut into 8×8 tiles
  left-to-right, top-to-bottom.
- An indexed PNG with a palette of **≤ 4 entries** maps each palette index
  straight to the GB colour value 0–3 (exact control; index 0 = transparent
  for sprites).
- Anything else maps per pixel: transparent (alpha < 128) or near-white → 0,
  then light → 1, dark → 2, black → 3 by luminance.

The `projects/shmup` project is the worked example:
`projects/shmup/assets/sprites.png` is the pregenerated sheet (regenerated by
`projects/shmup/assets/gen_sprites.py`), and `projects/shmup/src/shmup.mos`
ships a complete game on top of it.

## 🔧 Build Commands

### Basic Commands

```bash
# Build a single source file (build/ created next to it)
python mosaik8.py build samples/text_simple.mos

# Build a project (reads mosaik.toml)
python mosaik8.py build projects/game/mosaik.toml
python mosaik8.py build              # uses ./mosaik.toml

# Restrict to one platform / add debug symbols
python mosaik8.py build --platform gameboy_color samples/bounce.mos
python mosaik8.py build --debug projects/game

# Clean build artifacts (project-mode build/ in the current directory)
python mosaik8.py clean

# Show version
python mosaik8.py version
```

> Note: `clean` removes the project-mode `build/` directory. In single-file
> mode the output lives next to the source, e.g. `samples/build/`.

## 🧪 Samples & Tests

### Building the sample programs

The `samples/` folder contains ready-to-build mosaik programs:

| Sample | What it shows |
| --- | --- |
| `text_simple.mos` | Static text rendering |
| `text.mos` | Variables, counters, `text.print_number` |
| `text_complex.mos` | Nested conditions, multiple counters |
| `bounce.mos` | Structs, signed math, input handling; sized by `SCREEN_WIDTH`/`SCREEN_HEIGHT`, so it uses the whole screen on every sprite-capable console |
| `pong.mos` | A small game loop, same screen-geometry portability |
| `beep.mos` | `platform.sound`: the portable beep channel (A/B play tones; builds for all 9 consoles) |
| `cross_platform.mos` | Per-platform conditional compilation + the `SCREEN_*` constants; builds for all 9 consoles |
| `hello.mos` | The portable Tier-1 subset (text/input/timing), one source for both backends |
| `draw.mos` | The Lynx-only `graphics.draw` TGI primitives, platform-gated |
| `graphics_showcase.mos` | Sprites, background, window HUD, palette registers (Game Boy family) |
| `novascape.mos` | A complete game port (Game Boy family) |

Build any one of them in single-file mode (output goes to `samples/build/`):

```bash
python mosaik8.py build samples/bounce.mos
```

Full project examples live in `projects/` and are built in project mode:

```bash
python mosaik8.py build projects/game        # Box Runner     -> projects/game/build/<console>/game.*
python mosaik8.py build projects/shmup       # Starfall shmup -> projects/shmup/build/<console>/starfall.*
python mosaik8.py build projects/background  # scrolling bkg  -> projects/background/build/<console>/background.*
```

`projects/shmup` (Starfall) is a vertical shoot'em up for Game Boy, Game Gear
and Atari Lynx from one source, with all graphics pregenerated from a PNG via
the asset pipeline (see **Assets** above). `projects/background` scrolls a
32×32-tile world with `graphics.bkg` and walks an animated sprite on top —
one source for all nine consoles, including the Lynx (no tilemap hardware;
the backend composites the map into one big Suzy background sprite) and the
PC Engine (real VDC tilemap + scroll registers).

See a built ROM run by opening it in an emulator directly:

```bash
pyboy samples/build/gameboy/bounce.gb                        # Game Boy
python emu/libretro/run_lynx.py samples/build/lynx/hello.lnx # Atari Lynx (headless)
```

### Running the tests

Unit tests live in `tests/`. Run the whole suite (and optionally rebuild every
sample end-to-end) with the test runner:

```bash
# Run all unit tests
python tests/run_all.py

# Run all unit tests AND compile every sample to a ROM, for every console it
# supports (derived from the capability registry — ~90 builds), plus the
# projects/game and projects/shmup projects
python tests/run_all.py --samples

# Optional behavioral checks: drive the built ROMs in emulators
# (PyBoy for Game Boy; --lynx also screen-diffs the Lynx shmup through the
# libretro harness)
python tests/verify_roms.py --lynx
```

You can also run an individual test directly:

```bash
python tests/enum_test.py
python tests/test_fixes.py
```

Each test prints a `PASS`/`FAIL` line; `run_all.py` exits non-zero if anything
fails, so it is suitable for CI.

## 📚 Language Guide

### Basic Syntax

```mosaik
module "example" {
    import "platform.video"
    
    -- Comments use double dashes
    var health: u8 = 100
    const MAX_SPEED: u8 = 5
    
    type Position = struct {
        x: u8,
        y: u8
    }
    
    enum Direction {
        UP = 0,
        DOWN = 1,
        LEFT = 2,
        RIGHT = 3
    }
    
    function move_player(pos: Position, dir: Direction) -> Position {
        if dir == UP and pos.y > 0 {
            pos.y -= 1
        }
        return pos
    }
    
    export move_player, Direction
}
```

### Control Flow

```mosaik
function control_flow_demo() {
    var i: u8 = 0

    -- if / else if / else
    if i == 0 {
        i = 1
    } else if i == 1 {
        i = 2
    } else {
        i = 3
    }

    -- Infinite loop (compiles to `while (1)`); use `return` to leave it
    loop {
        i += 1
        if i >= 10 { return }
    }

    -- Conditional loop with break / continue
    while i < 20 {
        i += 1
        if i == 15 { continue }
        if i == 18 { break }
    }

    -- switch with multi-value labels and an optional default arm.
    -- Each case auto-breaks; list values to share one body.
    switch i {
        case 0 { i = 1 }
        case 1, 2, 3 { i = 2 }
        default { i = 9 }
    }

    -- Numeric range for-loop: `for <var> in <start>..<end>` (end is exclusive)
    for n in 0..8 {
        i += n
    }
}

-- `local function` marks a function private to its module.
local function helper(x: u8) -> u8 {
    return x + 1
}
```

> Operators: arithmetic `+ - * / %`, comparison `== != < > <= >=`, logical
> `and or not`, and assignment `= += -=`. Integer literals may be written in
> decimal, hex (`0xE4`), or binary (`0b1010`). `for` only iterates numeric
> ranges (not arrays).
>
> Parameters — including structs and arrays — are passed **by value** (there are
> no pointer/reference types yet), so a function mutating a struct argument
> changes only its local copy.

### Type System

```mosaik
-- Primitive types
u8      -- Unsigned 8-bit (0-255)
i8      -- Signed 8-bit (-128 to 127) 
u16     -- Unsigned 16-bit (0-65535)
i16     -- Signed 16-bit (-32768 to 32767)
bool    -- Boolean
addr    -- Memory address

-- Array types
array[u8, 160]     -- Array of 160 bytes
array[Position, 32] -- Array of 32 positions

-- `const` arrays become real C `const` tables (great for tile/map/music data):
const TILE: array[u8, 8] = [0x3C, 0x42, 0x42, 0x42, 0x42, 0x42, 0x3C, 0x00]

-- Struct types
type Sprite = struct {
    x: u8,
    y: u8,
    tile: u8,
    flags: u8
}
```

### Platform-Specific Code

```mosaik
module "graphics" {
    import "platform.video"
    
    -- Conditional compilation
    if platform == "gameboy_color" {
        function set_palette(colors: array[u16, 4]) {
            -- GBC-specific palette code
        }
    } else {
        function set_palette(colors: array[u16, 4]) {
            -- DMG grayscale fallback
        }
    }
    
    export set_palette
}
```

The `platform == ...` condition is **evaluated against the build target**, so
each ROM links only the matching branch. Conditions may combine `==`, `!=`,
`and`, `or`, `not` over `platform` and string literals (including `else if`
chains); the literal matches the canonical name or an alias (`"gbc"` ≡
`"gameboy_color"`). Both branches are always parsed, so syntax errors surface on
every target, and an unresolvable condition falls back to the `then` branch.

mosaik targets all nine supported consoles via `--platform`:

```bash
# GBDK consoles
python mosaik8.py build --platform sms  samples/cross_platform.mos
python mosaik8.py build --platform nes  samples/cross_platform.mos
# cc65 consoles
python mosaik8.py build --platform lynx samples/hello.mos
python mosaik8.py build --platform pce  samples/hello.mos
```

See `samples/cross_platform.mos` for one program that builds for all nine consoles.
See `samples/hello.mos` for a program portable across both backends.

### Standard Library

These built-in modules resolve at compile time and map to platform C helpers.
Which calls are available on which console is enforced by the capability
registry (a clear compile-time error is raised if you call something a target
doesn't support — no silent failures).

| Import | Provides |
| --- | --- |
| `platform.video` | `enable_lcd()`, `disable_lcd()`, `wait_vblank()`, `show_sprites()`, `hide_sprites()`, `show_background()`, `show_window()`, `hide_window()`, and the per-target screen geometry constants `SCREEN_WIDTH`/`SCREEN_HEIGHT` (pixels) and `SCREEN_COLS`/`SCREEN_ROWS` (text cells) |
| `platform.input` | `pressed(button)`, `held(button)`, and `INPUT_A/B/SELECT/START/RIGHT/LEFT/UP/DOWN` |
| `platform.hardware` | `write(address, value)`, `read(address)`, and `REG_DIV/REG_NR10/REG_BGP/REG_OBP0/REG_OBP1` (the `REG_*` constants are Game Boy addresses and exist only on the GB family) |
| `platform.system` | `delay(ms)`, `random()`, `seed_random(seed)` |
| `platform.sound` | `beep(freq, frames)`, `stop()` — one portable square-wave channel on every console (GB-family APU, SMS/GG PSG, NES APU, Lynx Mikey, PCE PSG). `beep` is non-blocking; the duration counts down in `wait_vblank` ticks (60 ≈ 1 s; 0 = until `stop()`) |
| `graphics.sprite` | `set_data(first, count, data)`, `set_tile(id, tile)`, `get_tile(id)`, `set_prop(id, prop)`, `move(id, x, y)` — screen-pixel coordinates, `(0, 0)` = top-left of the visible screen on every console — `FLIP_X`, `FLIP_Y` |
| `graphics.bkg` | `set_data(first, count, data)`, `set_tiles(x, y, w, h, tiles)`, `scroll(dx, dy)`, `move(x, y)` — a 32×32-tile scrollable background with u8 wrap-around on **every** console: the GBDK targets and the PCE scroll their tilemap hardware; the Lynx (no tilemap layer) composites the map into one large Suzy background sprite re-blitted with wrapped offsets each frame (see `projects/background`) |
| `graphics.window` | `set_tiles(x, y, w, h, tiles)`, `move(x, y)` |
| `graphics.text` | `print_string(x, y, text)`, `print_number(x, y, n)`, `clear_area(x, y, w, h)` |

```mosaik
video.enable_lcd()
if input.pressed(INPUT_A) { text.print_string(2, 2, "A pressed") }
text.print_number(2, 4, score)
video.wait_vblank()
```

`platform.hardware` is the escape hatch for memory-mapped I/O the higher-level
modules don't cover yet — sound registers, palettes, the divider, etc. It maps
to raw `volatile` byte access at a 16-bit address:

```mosaik
import "platform.hardware"

hw.write(REG_BGP, 0xE4)       -- set the background palette
var seed: u8 = hw.read(REG_DIV) -- the divider makes a cheap RNG source
```

The `graphics.*` and `platform.system` modules wrap GBDK's sprite, background,
window, and utility calls:

```mosaik
import "graphics.sprite"
import "graphics.bkg"

sprite.set_data(0, 2, SHIP_TILES)   -- upload two 8x8 tiles
sprite.set_tile(0, 0)
sprite.set_prop(0, FLIP_X)          -- mirror it horizontally
sprite.move(0, 84, 78)

bkg.set_data(0, 3, BKG_TILES)
bkg.set_tiles(0, 0, 20, 18, screen) -- fill a region of the tilemap
bkg.scroll(1, 0)                    -- nudge the background each frame
```

See `samples/graphics_showcase.mos` for a runnable demo (scrolling starfield,
an animated ship you can fly around, a window HUD, and palette cycling).

## 🎯 Examples

### Simple Pong Game

```mosaik
module "pong" {
    import "platform.video"
    import "platform.input"
    
    var paddle_y: u8 = 72
    var ball_x: u8 = 80
    var ball_y: u8 = 72
    var ball_dx: i8 = 1
    var ball_dy: i8 = 1
    
    function update_paddle() {
        if input.pressed(INPUT_UP) and paddle_y > 0 {
            paddle_y -= 2
        }
        if input.pressed(INPUT_DOWN) and paddle_y < 128 {
            paddle_y += 2
        }
    }
    
    function update_ball() {
        ball_x += ball_dx
        ball_y += ball_dy
        
        -- Bounce off walls (SCREEN_* adapt to the build target)
        if ball_y <= 0 or ball_y >= SCREEN_HEIGHT {
            ball_dy = -ball_dy
        }
        
        -- Reset if ball goes off screen
        if ball_x <= 0 or ball_x >= SCREEN_WIDTH {
            ball_x = SCREEN_WIDTH / 2
            ball_y = SCREEN_HEIGHT / 2
        }
    }
    
    function main() {
        video.enable_lcd()
        
        loop {
            update_paddle()
            update_ball()
            video.wait_vblank()
        }
    }
    
    export main
}
```

### Sprite Animation

```mosaik
module "animation" {
    import "platform.video"
    
    type Animation = struct {
        frames: array[u8, 8],
        frame_count: u8,
        current_frame: u8,
        timer: u8,
        speed: u8
    }
    
    function create_animation(speed: u8) -> Animation {
        var anim: Animation = {
            frames: [0, 1, 2, 3, 4, 5, 6, 7],
            frame_count: 8,
            current_frame: 0,
            timer: 0,
            speed: speed
        }
        return anim
    }
    
    function update_animation(anim: Animation) {
        anim.timer += 1
        if anim.timer >= anim.speed {
            anim.timer = 0
            anim.current_frame += 1
            if anim.current_frame >= anim.frame_count {
                anim.current_frame = 0
            }
        }
    }
    
    export Animation, create_animation, update_animation
}
```

## 🔍 Troubleshooting

### Common Issues

**GBDK Not Found**
```bash
Error: GBDK tool 'lcc' not found
```
- Run `python setup_tools.py --only gbdk` to download and install GBDK-2020 into `gbdk/`
- Or set `GBDK_HOME` to an existing install: `$env:GBDK_HOME = "C:\path\to\gbdk-2020"` (PowerShell) or `export GBDK_HOME=/path/to/gbdk-2020` (bash)
- Or add the GBDK `bin/` directory to your `PATH`

**A `.gb` ROM was treated as a source file**
```bash
Error: 'charmap' codec can't decode byte 0x90 ...
```
- Source files must use the `.mos` extension; `.gb` is reserved for compiled ROMs
- The discovery step only picks up `.mos` files and skips `build/` folders

**Compilation Errors**
```bash
Compilation failed: Unknown type: 'MyType'
```
- Check type definitions are declared before use
- Verify imports are correct
- Review syntax for typos

**ROM Size Issues**

If a program is too large, the error comes from the GBDK toolchain (`sdcc`/`makebin`)
during linking, not from mosaik — the `rom_size`/`ram_size` keys in
`mosaik.toml` are accepted but not currently enforced by the build tool.
- Split work into smaller functions / reuse buffers
- Let SDCC optimize; keep data tables compact
- For larger programs, GBDK ROM banking applies (handled by the toolchain)

### Debug Mode

```bash
# Build with debug symbols
python mosaik8.py build --debug

# This passes -debug to lcc and generates:
# - .map files with memory layout
# - .sym files with symbol information
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- **GBDK-2020 Team** - For the excellent multi-console development kit
- **cc65 Team** - For the cc65 toolchain powering the Atari Lynx and PC Engine backend
- **libretro / Handy / Beetle Lynx** - For the cores powering headless Lynx testing
- **Retro Development Community** - For resources and inspiration across all platforms
- **Lua and Python Communities** - For syntax inspiration

## 📞 Support

- **Issues**: Report bugs and request features on GitHub Issues
- **Documentation**: See the `docs/` directory for detailed guides
- **Community**: Join the retro console development Discord/forums

---

**Happy Retro Console Development!** 🎮✨
