# mosaik Language Specification

## Project Overview

**Language Name:** mosaik
**Target Platforms:** Nine retro consoles across two C backends:
- **GBDK backend**: Game Boy (DMG), Game Boy Color, Analogue Pocket, Mega Duck,
  Sega Master System, Game Gear, NES/Famicom
- **cc65 backend**: Atari Lynx, PC Engine / TurboGrafx-16

**Compilation Model:** mosaik emits readable C — GBDK C for GBDK consoles
(linked by `lcc`/`sdcc`), cc65 C for cc65 consoles (linked by `cl65`). The
lexer, parser, type-checker, and all logic codegen are shared; only the prelude
and standard-library lowering differ per backend.
**Philosophy:** Modern, expressive syntax (inspired by Lua/Python) that lowers
cleanly onto the target C runtime, with per-console capability gating enforced at
compile time.

> **File extensions**
> - `.mos` — mosaik **source** files
> - `.c` — generated C (intermediate build output; GBDK C or cc65 C)
> - `.gb` / `.gbc` / `.pocket` / `.duck` / `.sms` / `.gg` / `.nes` — GBDK ROM output
> - `.lnx` / `.pce` — cc65 ROM output (Atari Lynx / PC Engine)

## 1. Language Design Goals

### Core Principles
- **Two C backends, one language**: mosaik is a thin, expressive layer over GBDK C (Game Boy family / SMS / GG / NES) and cc65 C (Atari Lynx / PC Engine); generated C is human-readable.
- **Platform reach**: One source set targets nine consoles. Programs that stay within the portable stdlib subset (text, input, timing, sprites) build for all of them unchanged.
- **Capability gating**: Calling a stdlib function a console lacks is a clear compile-time error, not a silent no-op or link failure.
- **Familiar syntax**: Lua/Python-flavored surface syntax over an 8-bit/16-bit type system.
- **Expressiveness**: Structs, enums, modules, and a standard library for video/input/sprites/text/draw.
- 🔭 **Memory safety & optimization**: Automatic bounds checking and register-aware codegen are design goals, not yet implemented.

### Target Audience
- Retro game developers who want modern syntax without hand-writing GBDK C or cc65 C.
- Developers exploring multi-platform retro development with a single portable language.

## 2. Language Syntax and Semantics

### 2.1 Basic Syntax

```mosaik
-- Comments use double dashes (like Lua).
# Lines starting with '#' are directives/comments (e.g. the build tool injects
# "# platform gameboy"); they are currently ignored by the compiler.

-- A program is one or more module blocks.
module "player" {
    import "platform.input"
    import "graphics.text"

    -- Type definitions
    type Position = struct {
        x: u8,
        y: u8
    }

    -- Global variables and constants
    var player_pos: Position = {x: 80, y: 72}
    var health: u8 = 100
    const MAX_X: u8 = 152

    -- Functions
    function update_player() {
        if input.pressed(INPUT_LEFT) and player_pos.x > 0 {
            player_pos.x -= 1
        }
        if input.pressed(INPUT_RIGHT) and player_pos.x < MAX_X {
            player_pos.x += 1
        }
        text.print_number(0, 0, player_pos.x)
    }

    -- Export symbols (comma list or braced list both work)
    export update_player, player_pos
}
```

### 2.2 Type System

```mosaik
-- Primitive types (mapped to C fixed-width integer types)
u8      -- Unsigned 8-bit (0-255)        -> uint8_t
i8      -- Signed 8-bit (-128 to 127)    -> int8_t
u16     -- Unsigned 16-bit (0-65535)     -> uint16_t
i16     -- Signed 16-bit (-32768..32767) -> int16_t
bool    -- Boolean                       -> uint8_t
addr    -- Memory address (16-bit)       -> uint16_t
void    -- No value                      -> void

-- Array types with a compile-time size
array[u8, 160]      -- Array of 160 bytes
array[Position, 32] -- Array of 32 structs

-- Struct types
type Sprite = struct {
    x: u8,
    y: u8,
    tile: u8,
    flags: u8
}

-- Enum types (lowered to #define constants, backed by uint8_t)
enum Direction {
    UP = 0,
    DOWN = 1,
    LEFT = 2,
    RIGHT = 3
}
-- Enums may also be written `type Direction = enum { ... }`.
```

**Type checking notes (current behavior)**

- Type checking is **best-effort**: it produces diagnostics for simple programs, but if
  it raises on a more advanced construct the compiler prints a warning and proceeds to
  code generation anyway. It never blocks a build.
- Integer types interoperate freely (the target promotes/truncates like C).
- Numeric literals are inferred to the smallest fitting type (`u8`, then `i8`, `u16`, `i16`).
  They may be written in decimal, hexadecimal (`0xE4`), or binary (`0b1010`).
- 🔭 Bounds checking on array access and stricter type enforcement are planned.

### 2.3 Functions, Control Flow & Expressions

```mosaik
-- Functions: parameters are `name: type`, optional `-> returntype`.
-- `local function` marks a module-private function.
local function clamp(value: u8, hi: u8) -> u8 {
    if value > hi {
        return hi
    }
    return value
}

function demo() {
    -- Local variable declarations
    var i: u8 = 0
    var total: u8 = 0

    -- if / else if / else
    if i == 0 {
        total = 1
    } else if i == 1 {
        total = 2
    } else {
        total = 3
    }

    -- Infinite loop (compiles to `while (1)`)
    loop {
        i += 1
        if i >= 10 {
            return
        }
    }

    -- Conditional loop with break / continue
    while i < 20 {
        i += 1
        if i == 15 { continue }
        if i == 18 { break }
    }

    -- switch: multi-value case labels share a body, each case auto-breaks,
    -- and `default` is optional.
    switch i {
        case 0 { total = 1 }
        case 1, 2, 3 { total = 2 }
        default { total = 9 }
    }

    -- Numeric range for-loop: `for <var> in <start>..<end>` (end exclusive)
    for n in 0..8 {
        total += n
    }
}
```

**Operators**

| Category    | Operators |
| ----------- | --------- |
| Arithmetic  | `+  -  *  /  %` |
| Comparison  | `==  !=  <  >  <=  >=` |
| Logical     | `and  or  not` |
| Assignment  | `=  +=  -=` |
| Access      | `.` (field), `[]` (index), `()` (call) |

Aggregate literals are supported: struct literals `{x: 1, y: 2}` and array literals
`[0, 1, 2, 3]`. Assigning a struct literal to an existing variable is expanded into
per-field stores in the generated C.

**Implemented**: `loop`, `while <cond>`, `break`, `continue`, `switch`/`case`/`default`,
`if`/`else if`/`else`, and the numeric range `for`.

🔭 **Not yet implemented** at the statement/expression level: iterating over arrays in `for`,
pointer/reference types (`*T`), and `%=`/`*=`/`/=` compound assignment.

### 2.4 Conditional Compilation

A module may contain a top-level `if … { … } else { … }` block used for
per-platform conditional compilation. The condition is **evaluated against the
build target** (`--platform`), so each ROM links only the matching branch:

```mosaik
module "graphics" {
    if platform == "gameboy_color" {
        function set_palette() { -- GBC version
        }
    } else if platform == "nes" {
        function set_palette() { -- NES version
        }
    } else {
        function set_palette() { -- DMG fallback
        }
    }
    export set_palette
}
```

- Conditions may use `==`, `!=`, `and`, `or`, `not` over the special `platform`
  identifier and string literals. The literal is matched against the canonical
  target name **or any alias** (e.g. `"gbc"` ≡ `"gameboy_color"`, `"gg"` ≡
  `"gamegear"`).
- `else if` chains are supported (keep the `}` and `else` on the same line).
- **Both branches are always parsed**, so syntax errors surface on every target.
- If a condition cannot be resolved to a constant (e.g. it references a runtime
  variable), the compiler keeps the `then` branch — the previous behaviour.

Canonical platform names (and a few aliases):

| Canonical name      | Aliases              |
| ------------------- | -------------------- |
| `gameboy`           | `gb`, `dmg`          |
| `gameboy_color`     | `gbc`, `cgb`         |
| `analogue_pocket`   | `ap`, `pocket`       |
| `megaduck`          | `duck`               |
| `sms`               | —                    |
| `gamegear`          | `gg`                 |
| `nes`               | —                    |
| `lynx`              | —                    |
| `pce`               | `turbografx`         |

See `samples/cross_platform.mos` for a program that builds for every console, and
`samples/hello.mos` for a program portable across both backends.

### 2.5 Inline Assembly 🔭 (planned, not implemented)

```mosaik
function critical_timing() {
    asm {
        di
        ei
    }
}
```

An `asm { ... }` escape hatch is part of the design vision but is **not** recognized by
the current lexer/parser.

### 2.6 Memory Regions & Stack Allocation 🔭 (planned, not implemented)

The `region wram { ... }`, `region hram { ... }`, `region vram bank(n) { ... }`, and
`stack var` constructs described in earlier drafts are **not implemented**. Global
variables are emitted as ordinary C globals and placed by the GBDK toolchain.

### 2.7 ROM Banking (`bank(N)`) ✅

```mosaik
bank(2) function spawn_wave() { ... }          -- placed in ROM bank 2
bank(2) local function helper() -> u8 { ... }  -- bank() always comes first
```

A placement annotation on module-level functions (design:
`docs/banking-plan.md`). On the **Game Boy family** (`gameboy`,
`gameboy_color`, `analogue_pocket` — `has_banking` in the capability
registry) the build links an MBC5 cartridge: each used bank becomes its own
generated C file (SDCC's `#pragma bank` is file-scoped) and every call to a
banked function goes through sdcc's `__banked` far-call trampoline — calls
look and behave exactly like ordinary calls, so `bank(N)` changes placement,
never semantics. The cart auto-sizes to the highest bank used (or honours an
explicit `[build] rom_size`, erroring if it is too small). On every other
console the annotation is **accepted and ignored** (one note per build), so
a source with banked GB code still builds for all nine consoles.

- `N` is 1..511; `bank(0)` is an error (bank 0 *is* the home bank), and
  `main()` cannot be banked.
- `bank` is contextual — only `bank(N)` before `function` is the annotation;
  variables named `bank` keep working.
- Module-level `var`/`const` data always stays in the home bank (banked
  `const` data is 🔭 — see the design doc).

## 3. Module System

### 3.1 Module Structure

```mosaik
module "graphics.sprites" {
    import "platform.video"

    -- Module-private function
    local function upload_sprite_data(data: addr, size: u16) {
    }

    -- Public function, declared and exported
    function set_position(id: u8, x: u8, y: u8) {
    }

    export set_position
}
```

Notes on the current implementation:
- `module "name" { ... }` is the only top-level construct. A file may contain multiple modules.
- `export` accepts both `export a, b, c` and `export { a, b, c }` forms.

#### Cross-file module linking ✅

`import "name"` either names a stdlib module (resolved by the compiler, see §3.2)
or another module of the program. All `.mos` files of a build are compiled
**together into one C translation unit** (whole-program compilation — the natural
model for sdcc/cc65, which optimize poorly across translation units):

- **Project mode** compiles every `.mos` under `[source] folder`. **Single-file
  mode** follows imports transitively: `import "helpers.math"` loads
  `helpers/math.mos` (dots map to subfolders, falling back to
  `helpers.math.mos`) relative to the importing file.
- A module is referenced by the **last segment** of its name:
  `import "helpers.math"` … `math.double(x)`. Both calls (`math.double(x)`) and
  module-level consts/vars (`math.MAX`) resolve cross-module.
- Only names on the exporting module's `export` list are visible; referencing
  anything else, using a module without importing it, importing a module no
  source defines, defining the same module twice, or defining `main()` in two
  modules are all clear compile errors. Two modules may not share a last name
  segment, and stdlib aliases (`video`, `input`, `hw`, `system`, `sprite`,
  `bkg`, `window`, `text`, `draw`) are reserved.
- C-level naming: with more than one module in the program, every module-level
  symbol is emitted as `<module>_<name>` (e.g. `counter_tick`; `main()` keeps
  its name as the entry point); parameters and locals shadow module symbols.
  A single-module program keeps plain C names, and a `module.func(...)` call
  that matches no stdlib or program module still lowers to `module_func(...)`.
- Struct/enum **type names** (and enum variant names) are program-global —
  two modules must not declare the same one.
- **Tree-shaking**: when a module defines `main()`, only it and the modules it
  reaches (through `import` *or* a qualified `other.foo` reference, transitively)
  are emitted — so adding an unused module to a project doesn't bloat the ROM.
  A library build with no `main()` keeps every module.
- Worked example: `projects/multifile` (main.mos + counter.mos).

### 3.2 Standard Library (built-in modules)

The standard library is built into the compiler. Each call's per-backend
lowering lives in the codegen `STDLIB_CALLS_*` maps (`mosaik/codegen/`), its
public signature is registered with the `TypeChecker`, and
`mosaik/stdlib.py`'s `STDLIB_MODULE_NAMES` is the set of module names `import`
recognises as stdlib (so `import "platform.video"` resolves while an unknown
import is an error). The modules:

```
platform.video    -- enable_lcd, disable_lcd, wait_vblank,
                     show_sprites/hide_sprites, show_background, show_window/hide_window,
                     SCREEN_WIDTH/SCREEN_HEIGHT/SCREEN_COLS/SCREEN_ROWS (per target)
platform.input    -- pressed, held, INPUT_A/B/SELECT/START/RIGHT/LEFT/UP/DOWN
platform.hardware -- write, read, REG_DIV/REG_NR10/REG_BGP/REG_OBP0/REG_OBP1
platform.system   -- delay, random, seed_random
platform.sound    -- beep, stop, sfx, SFX_COIN/HURT/JUMP/POINT/SELECT  (one beep channel; every console)
graphics.sprite   -- set_data, set_tile, get_tile, set_prop, set_meta, move, set_palette, FLIP_X, FLIP_Y
graphics.bkg      -- set_data, set_tiles, scroll, move, set_palette (has_tile_palettes consoles)
graphics.window   -- set_tiles, move
graphics.text     -- print_string, print_number, clear_area  (set_font is declared but unmapped)
graphics.draw     -- clear, set_color, pixel, line, bar, circle, present  (has_draw consoles, e.g. Lynx)
graphics.palette  -- rgb, set_bkg, set_sprite, load_bkg, load_sprite, load_sprite16  (every console; see §6)
native.lynx       -- fade_in, fade_out, screen_shake, jingle  (real on Lynx, no-op elsewhere -- the escape hatch)
```

These map to the matching GBDK functions (`set_sprite_data`, `set_bkg_tiles`,
`scroll_bkg`, `set_win_tiles`, `rand`, `delay`, ...); the display-visibility calls,
`sprite.move` (screen-pixel coords, see §6) and `hw.read`/`hw.write` are emitted
as small `gbs_*` wrappers in the prelude. Which calls exist on which console is
recorded in the `PLATFORM_CAPS` registry; calling one a console lacks is a clear
compile-time error.

🔭 The remaining stdlib tree (audio/sound, timers, math/fixed-point, collections,
strings, scenes) outlined in earlier drafts is a design goal and is not implemented.

## 4. Compiler Architecture

### 4.1 Compilation Pipeline

```
mosaik Sources (.mos, one or more)
    ↓
[Lexer + Parser]     -> AST per file, merged into one Program
    ↓
[Import resolution]  -> stdlib names vs. program modules (clear errors)
    ↓
[TypeChecker]        -> best-effort diagnostics (non-blocking)
    ↓
[CodeGenerator]      -> ONE C translation unit   ← backend selected by --platform
    ↓                                   ↙               ↘
[GBDK lcc/sdcc]                GBDK C              cc65 C
    -> .gb / .gbc / .sms / …   [cl65 -t <target>]
                                    -> .lnx / .pce
```

This pipeline lives in the `mosaik/` package
(`MosaikCompiler.compile_program(sources, platform=...) → C string`, with
`compile(source, ...)` as the single-source convenience wrapper). The package
is split by stage — `lexer`, `ast_nodes`, `platforms`, `parser`,
`typechecker`, `compiler`, and a `codegen/` subpackage whose `generator.py`
holds the shared `CodeGenerator` and `gbdk.py`/`cc65.py` the per-backend
prelude emitters + stdlib maps. `CodeGenerator` picks the backend in
`generate()` based on the `framework` entry in `PLATFORM_CAPS`. The build tool
in `mosaik8.py` writes the `.c` file and invokes `lcc` (GBDK) or `cl65` (cc65)
to produce the ROM. All sources of a build are emitted into a single C
translation unit (see §3.1 for the cross-module naming scheme).

### 4.2 Code Generation Details

- A **per-backend prelude** is emitted. GBDK: `#include <gb/gb.h>`, `<gbdk/platform.h>`,
  `<stdio.h>`, `<stdint.h>`, the `INPUT_*` `#define`s, and `gbs_*` helpers. cc65:
  profile-specific headers (`<lynx.h>`/`<pce.h>`), the TGI or conio init block, and the
  same `gbs_*` helper names (different bodies). Both backends emit `SCREEN_WIDTH/HEIGHT/
  COLS/ROWS` `#define`s; GB-family-only `REG_*` constants appear only on `has_gb_regs`
  consoles.
- Enums and scalar `const`s become `#define`s; structs become `typedef struct`s; module
  globals become C globals; functions get forward declarations so call order is irrelevant.
- **Array `const`s** become real C `const` tables (`const uint8_t name[N] = { ... };`) — a
  `#define` cannot hold aggregate data — which is how tile/map/music tables are emitted.
- `loop` → `while (1)`, `while c` → `while (c)`, `for x in a..b` → `for (uint8_t x = a; x < b; x++)`;
  `break`/`continue` pass through; `switch` → a C `switch` where each `case` body is wrapped
  in a block and auto-`break`s (multi-value labels emit consecutive `case` labels).
- Stdlib calls are selected from `STDLIB_CALLS_GBDK` or `STDLIB_CALLS_CC65_*` into
  `self.stdlib_calls` by the backend; calling anything absent from the active map — but
  present in the global `ALL_STDLIB_CALLS` set — is a clear compile-time error.

### 4.3 Optimization & Register Allocation 🔭 (planned)

There is a placeholder `RegisterAllocator` class, but **no optimization passes run today** —
code generation targets straightforward C and relies on SDCC for optimization. The
register-allocation and zero-cost-abstraction goals from earlier drafts remain 🔭 planned.

## 5. Build System

### 5.1 Project Configuration (`mosaik.toml`)

```toml
[project]
name = "my_game"                               # names the output .c and ROM
version = "1.0.0"
target_platforms = ["gameboy", "gameboy_color"]

[source]
folder = "src/"                                # where .mos sources live (relative to this file)

[assets]
sprites = ["assets/sprites.png"]               # PNGs -> tile data (relative to this file)

[build]
rom_size = "64KB"                              # cart ROM (GB family): 32KB..8MB; >32KB links an MBC5 cart
ram_size = "8KB"                               # battery-backed cart RAM (GB family): 0/8KB/32KB/128KB
output_dir = "build"                           # build output (relative to this file)
```

> `name`, `target_platforms`, `source.folder`, `build.output_dir`,
> `assets.sprites`, `build.rom_size` and `build.ram_size` are the keys that
> affect output (`rom_size`/`ram_size` pick the cartridge geometry on the
> Game Boy family — both default to a plain 32 KB/no-RAM cart, and `rom_size`
> grows automatically when `bank(N)` places code in higher banks; see §2.7).
> Every other key is reported, not silently ignored: recognised-but-unapplied
> keys (`optimization_level`, `debug_symbols`, `platforms.*`, `dependencies`)
> and unknown keys/sections each print a `⚠️` warning at build time.
> `--debug` enables `lcc` debug flags.

#### Asset pipeline ✅

Each PNG listed in `[assets] sprites` (project mode) or passed via a repeatable
`--asset file.png` flag (single-file mode) is converted to Game Boy 2bpp tile
data at build time and injected into the program as two constants named after
the file (`assets/sprites.png` → `sprites_tiles`, an array of 16 bytes per
8x8 tile, and `sprites_tile_count`):

```mosaik
sprite.set_data(0, sprites_tile_count, sprites_tiles)
```

GB 2bpp is the interchange format on **every** console (uploaded directly on
the GB family, converted by GBDK's `set_sprite_data` compatibility layer on
NES and SMS/Game Gear, converted for the Suzy blitter by the Lynx sprite
engine). Authoring rules: image dimensions must be multiples of 8 (tiles are
cut left-to-right, top-to-bottom); an indexed PNG with a palette of at most 4
entries maps palette indices literally to GB colour values 0..3 (index 0 =
transparent for sprites); any other PNG maps per pixel — transparent
(alpha < 128) or near-white → 0, then light → 1, dark → 2, black → 3 by
luminance. The converter (`mosaik_assets.py`) is dependency-free. The
`projects/shmup` project is the worked example. On the Lynx, the sprite engine's tile table
holds 32 tiles; the build warns when assets exceed that.

For programs that `import "graphics.palette"`, a ≤ 4-entry indexed PNG also
emits its authored palette as `const u16 <name>_palette[4]`, converted to the
console's native color format at build time (see §6 `graphics.palette`) —
`palette.load_sprite(0, sprites_palette)` then recolors the asset with its
authored colors on every console.

**Named-sprite sheets ✅** — a sheet `foo.png` may carry a sidecar
`foo.sprites.json` mapping sprite name → `[x, y, w, h]` (pixels). The pipeline
then cuts each named sub-sprite from its rect, concatenates their 8×8 tiles
(row-major, manifest order) into `<name>_tiles`, and emits per-sprite
`<sprite>_tile` / `<sprite>_w` / `<sprite>_h` defines (tile units) — exactly
the shape `sprite.set_meta(slot, knight_tile, knight_w, knight_h)` wants. The
generator script that authors the sheet + manifest is project-local (e.g.
`projects/endless-runner/assets/gen_sprites.py`).

**4bpp / 16-colour tier ✅** — when the target's `PLATFORM_CAPS.sprite_bpp` is 4
(the Atari Lynx) and an asset is a >4-colour indexed PNG, the whole build's
sprite tiles are encoded **packed-nibble 4bpp** instead of 2bpp, and the
16-entry authored palette is emitted as `const u16 <name>_palette16[16]` for
`palette.load_sprite16` (see the 4bpp tier in §5.4 / `graphics.palette` in §6).
The same source still builds on the 2bpp consoles (luma-quantized,
`load_sprite16` a no-op).

### 5.2 Build Commands

The build tool has **two modes**, selected by the `build` argument (there is no
"scan the whole tree" mode):

```bash
# Single-file mode: build a .mos; output goes to a build/ folder next to the file
python mosaik8.py build samples/bounce.mos
python mosaik8.py build --platform gameboy samples/pong.mos

# Project mode: build a mosaik.toml (or a directory containing one, or ./mosaik.toml)
python mosaik8.py build projects/game/mosaik.toml
python mosaik8.py build projects/game
python mosaik8.py build                       # uses ./mosaik.toml

# Other commands
python mosaik8.py build --debug projects/game # add debug symbols
python mosaik8.py clean                        # remove ./build
python mosaik8.py init my_game                 # scaffold a new project
python mosaik8.py version

# There is no `run` command; open a built ROM in an emulator directly, e.g.
#   pyboy samples/build/gameboy/bounce.gb
```

GBDK is located automatically: `GBDK_HOME`, then a `gbdk-2020/`/`gbdk/` folder next to the
tool (this repo bundles one), then `PATH`.

Both modes compile all the sources of a build (project mode: everything under
`[source] folder`; single-file mode: the file plus its transitive imports, see
§3.1) into **one** generated `.c` named after the output, then link it.

### 5.3 Target Consoles

`--platform` (and `target_platforms` in `mosaik.toml`) selects the console.
GBDK consoles map to a GBDK-2020 `lcc` port; their generated C includes
`<gbdk/platform.h>`, so one program compiles for every GBDK target. The Atari
Lynx uses the **cc65 backend** instead — a separate prelude and standard-library
lowering linked by cc65's `cl65` (see §5.4).

| `--platform`       | Console                | Backend / port    | ROM     |
| ------------------ | ---------------------- | ----------------- | ------- |
| `gameboy`          | Game Boy (DMG)         | GBDK `sm83:gb`    | `.gb`   |
| `gameboy_color`    | Game Boy Color         | GBDK `sm83:gb`+CGB| `.gbc`  |
| `analogue_pocket`  | Analogue Pocket        | GBDK `sm83:ap`    | `.pocket` |
| `megaduck`         | Mega Duck / Cougar Boy | GBDK `sm83:duck`  | `.duck` |
| `sms`              | Sega Master System     | GBDK `z80:sms`    | `.sms`  |
| `gamegear`         | Sega Game Gear         | GBDK `z80:gg`     | `.gg`   |
| `nes`              | Nintendo NES / Famicom | GBDK `mos6502:nes`| `.nes`  |
| `lynx`             | Atari Lynx             | cc65 `cl65 -t lynx` | `.lnx` |
| `pce`              | PC Engine / TurboGrafx | cc65 `cl65 -t pce`  | `.pce` |

```bash
python mosaik8.py build --platform sms  samples/cross_platform.mos
python mosaik8.py build --platform nes  samples/cross_platform.mos
python mosaik8.py build --platform lynx samples/hello.mos
python mosaik8.py build --platform pce  samples/hello.mos
```

### 5.4 cc65 backend (Atari Lynx, PC Engine)

cc65 consoles are driven by the cc65 toolchain rather than GBDK. The language is
identical; only the standard-library surface differs, because the hardware model
does. The cc65 backend is **data-driven across consoles** via per-console
profiles (`CodeGenerator.CC65_PROFILES`): each profile gives the headers, the
text backend, the driver init/teardown, and the present call. Two text backends
exist today:

- **TGI profile** (`lynx`) — pixel-addressed graphics; `graphics.draw` works.
- **conio profile** (`pce`) — character-cell text via `gotoxy`/`cputs`; no
  `graphics.draw` (the PC Engine is tile-based, no TGI driver). Sprites use
  the VDC hardware engine instead (see below).

Adding another cc65 console is a new `CC65_PROFILES` entry plus a `PLATFORM_*`
registration — no new code path. Stdlib portability tiers:

- **Portable (build for GBDK *and* every cc65 console unchanged):**
  `platform.video` (`enable_lcd`/`disable_lcd`/`wait_vblank`), `platform.input`
  (`pressed`/`held`), `platform.system` (`delay`/`random`/`seed_random`),
  `platform.hardware` (`read`/`write`), and `graphics.text`
  (`print_string`/`print_number`/`clear_area`). Text coordinates stay in
  character cells on every target; TGI profiles scale cells to pixels and
  clear the covered cells before drawing (TGI text is otherwise transparent),
  so reprinting a counter *replaces* the old value, exactly as on the GB.
- **Sprites on TGI profiles (Tier-2, Suzy hardware):** `graphics.sprite`
  (`set_data`/`set_tile`/`get_tile`/`set_prop`/`move`) plus the
  `video.show_sprites`/`hide_sprites`/`show_background` toggles. On the Lynx the
  GBDK OAM model (a shared 8×8 2bpp tile table + sprite slots) is kept as the
  *API*, but rendering uses the **Suzy blitter**: each tile is converted once into
  a totally-literal Lynx sprite-data stream (byte-exact with the bundled `sp65`),
  each slot owns a Suzy SCB (`SCB_REHV_PAL`), and `gbs_present()` clears the back
  buffer and fires one `tgi_sprite()` per visible slot. Coordinates are screen
  pixels (the same `sprite.move` contract as every other console); GB pixel value 0 → pen 0
  (`COLOR_TRANSPARENT`) so it is transparent, and `set_prop` `FLIP_X`/`FLIP_Y`
  lower to Suzy `HFLIP`/`VFLIP`. The GBDK sprite samples `bounce.mos`/`pong.mos`
  build and run on the Lynx unchanged. *Limitation:* a sprite program owns the
  frame (present repaints the background), so immediate `graphics.text` and
  sprites shouldn't be mixed in the same frame.
- **Sprites on the PC Engine (Tier-2, VDC hardware):** the same
  `graphics.sprite` API backed by the HuC6270 VDC. Each GB 8×8 2bpp tile is
  converted once into the top-left quarter of a 16×16 4bpp VDC sprite pattern
  in VRAM (above the conio screen + font), each sprite slot owns one entry in
  a RAM mirror of the Sprite Attribute Table, and `wait_vblank` flushes the
  mirror to VRAM where the VDC's auto-repeat SATB DMA picks it up at the next
  vblank — tear-free. GB pixel value 0 maps to sprite-palette colour 0, which
  the VDC never draws (the GB transparency model); `FLIP_X`/`FLIP_Y` lower to
  the SATB X/Y-invert bits; coordinates are screen pixels like everywhere
  else. Sprites are an independent plane in front of the background, so —
  unlike the Lynx — **text and sprites mix freely** on the PC Engine.
  `bounce.mos`/`pong.mos` build and run on `pce` unchanged.
- **4bpp / 16-colour sprites (`sprite_bpp` tier, native on Lynx):** ✅ The
  asset pipeline encodes sprite tiles at the **target's native depth**
  (`PLATFORM_CAPS.sprite_bpp`): a >4-colour indexed PNG becomes packed-nibble
  **4bpp (16-colour)** on the Atari Lynx, and the Lynx sprite engine widens its
  literal rows to `BPP_4` and loads the asset's authored palette into the 16
  Mikey pens with `palette.load_sprite16(<name>_palette16)`. On the 2bpp
  consoles (Game Boy family / SMS / Game Gear / NES — and the PC Engine until
  its VDC 4bpp sprite path lands) the **same source** still builds: the PNG is
  luma-quantized to the 2bpp grey ramp and `load_sprite16` is a no-op
  (generalized-with-limits). Hand-authored 2bpp tiles and ≤4-colour assets are
  unaffected. *(PC Engine native 4bpp sprites: planned.)*
- **Metasprites (`sprite.set_meta`, every console):** ✅
  `sprite.set_meta(base, tile, w, h)` declares a **W×H block of 8×8 tiles**
  moved/flipped/re-tiled as one logical sprite — it reserves the sprite slots
  `base..base+w*h-1` and assigns them tiles row-major from `tile`. `move`/
  `set_prop`/`set_tile` on the base then act on the whole block (flips reverse
  the cell layout *and* mirror each tile, for a true whole-sprite mirror). The
  GB family fans the block out across consecutive OAM objects (bounded by the
  `max_metasprite_tiles` capability / the 10-per-line budget); the Lynx and
  PCE — far more generous ceilings — composite it. The layer is emitted only
  when a program calls `set_meta`, so ordinary sprite programs are
  byte-identical. Worked example: `samples/metasprite.mos`; the integrated
  showcase (metasprites + 4bpp/16-colour + named-sprite sheets + `native.lynx`,
  one source on all nine consoles) is `projects/endless-runner`.
- **Sound (Tier-1, every console):** `sound.beep(freq, frames)`/`sound.stop()`
  — see §6 `platform.sound`. On cc65 consoles the tone comes from the Lynx
  Mikey / PC Engine PSG.
- **TGI-only 🔭→ `graphics.draw`:** `draw.clear`, `draw.set_color`,
  `draw.pixel`, `draw.line`, `draw.bar`, `draw.circle`, `draw.present`. Available
  on TGI-profile consoles (Lynx); guard with `if platform == "lynx"` (see
  `samples/lynx_draw.mos`).
- **Background tilemap ✅ `graphics.bkg`:** supported on the cc65 consoles
  too. The PC Engine has real tilemap hardware (the VDC BAT plus the BXR/BYR
  scroll registers; the 32×32 map is replicated across the BAT so the u8
  scroll wraps mod 256 exactly like the Game Boy). The Lynx has *no* tilemap
  layer, so its engine composites the map once into one large literal Suzy
  background sprite and re-blits it with wrapped offsets each
  `wait_vblank` — the classic Lynx big-background-sprite scrolling technique;
  scrolling costs a handful of hardware blits per frame. Sprites layer on
  top on both (Lynx: painter's order; PCE: the sprite plane). Worked
  example: `projects/background`.
- **Not available on cc65 consoles:** the window APIs (`graphics.window`,
  `video.show_window`/`hide_window`), and `graphics.draw` on non-TGI
  profiles (PC Engine). Calling one when building for that console is a
  clear compile-time error, not a link failure.

cc65 provides `<stdint.h>`, so the `uN`/`iN` types lower to the same `uintN_t`
spellings on both backends. `cl65` locates its own cfg/lib/include relative to
its binary, so the bundled `cc65/` needs no extra flags. cc65 is found via
`CC65_HOME`, then a bundled `cc65/` next to the tool, then `cl65` on `PATH`.

The Lynx TGI is an interrupt-driven **dual-buffer** device, so `video.enable_lcd`
enables IRQs (`CLI()`) and sets the display refresh to **60 Hz**
(`tgi_setframerate(60)` — not the Lynx-classic 75, so `video.wait_vblank` paces
programs at the same rate as the Game Boy and "60 frames ≈ 1 second" holds), and
starts **single-buffered** (draw page == view page) so immediate text persists
without flipping. `video.wait_vblank` waits out the current display frame (the
Lynx `clock()` ticks once per frame) until the sprite or background engine is
used, then switches to true double-buffering and a VBL-synced page flip. (Calling
the flip repeatedly on content that lives in only one buffer — e.g. text plus a
`wait_vblank` loop — alternates with a blank buffer and flickers; that is why
non-sprite drawing stays single-buffered.)

> Which stdlib calls exist on which console is recorded once in the
> `PLATFORM_CAPS` registry (mosaik/platforms.py); calling anything a console
> lacks — on *either* backend — is a clear compile-time error. The `REG_*`
> hardware-register constants are Game Boy addresses and only exist on the
> Game Boy family; referencing one elsewhere is likewise a compile error.

### 5.5 Cross-platform support matrix

**The language core is fully portable.** Every non-hardware feature — `var`/
`const`, structs, enums, all control flow (`if`/`loop`/`while`/`for`/`switch`),
operators, functions, modules, conditional compilation — compiles identically on
all targets, because it is plain C codegen shared by both backends. Portability
therefore comes down to the **standard library**, summarised below.

Columns group the consoles that behave the same (per the `PLATFORM_CAPS`
capability registry):

- **GB family** = `gameboy`, `gameboy_color`, `analogue_pocket`, `megaduck`
  (full GBDK stdlib, window layer, GB hardware registers).
- **SMS/GG** = `sms`, `gamegear` (GBDK; no window layer, no GB registers).
- **NES** = `nes` (GBDK; no window layer, no GB registers).
- **Lynx** = `lynx` (cc65, TGI profile).
- **PCE** = `pce` (cc65, conio profile).

Legend: ✅ supported · ❌ unsupported. Calling a stdlib function a console lacks
is a **clear compile-time error** ("not supported on target ...") on every
backend — GBDK consoles included — driven by `PLATFORM_CAPS`. See footnotes.

| Stdlib call | GB family | SMS/GG | NES | Lynx | PCE |
| --- | :-: | :-: | :-: | :-: | :-: |
| `video.enable_lcd` / `disable_lcd` / `wait_vblank` | ✅ | ✅ | ✅ | ✅ ⁵ | ✅ |
| `video.show_sprites` / `hide_sprites` / `show_background` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `video.show_window` / `hide_window` | ✅ | ❌ ¹ | ❌ ¹ | ❌ | ❌ |
| `input.pressed` / `held` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `hw.read` / `hw.write` | ✅ ² | ✅ ² | ✅ ² | ✅ ² | ✅ ² |
| `REG_*` register constants | ✅ ² | ❌ ² | ❌ ² | ❌ ² | ❌ ² |
| `system.delay` / `random` / `seed_random` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `sound.beep` / `sound.stop` | ✅ ⁷ | ✅ ⁷ | ✅ ⁷ | ✅ ⁷ | ✅ ⁷ |
| `text.print_string` / `print_number` / `clear_area` | ✅ ³ | ✅ ³ | ✅ ³ | ✅ ³ | ✅ ³ |
| `SCREEN_WIDTH/HEIGHT/COLS/ROWS` constants | ✅ | ✅ | ✅ | ✅ | ✅ |
| `graphics.sprite.*` (`set_data`/`set_tile`/`get_tile`/`set_prop`/`move`) | ✅ | ✅ | ✅ | ✅ ⁴ | ✅ ⁸ |
| `graphics.bkg.*` (`set_data`/`set_tiles`/`scroll`/`move`) | ✅ | ✅ | ✅ | ✅ ⁹ | ✅ ⁹ |
| `graphics.window.*` (`set_tiles`/`move`) | ✅ | ❌ ¹ | ❌ ¹ | ❌ | ❌ |
| `graphics.draw.*` (TGI: `clear`/`set_color`/`pixel`/`line`/`bar`/`circle`/`present`) | ❌ | ❌ | ❌ | ✅ | ❌ |
| `graphics.palette.*` (`rgb`/`set_bkg`/`set_sprite`/`load_bkg`/`load_sprite`) | ✅ ¹⁰ | ✅ ¹⁰ | ✅ ¹⁰ | ✅ ¹⁰ | ✅ ¹⁰ |
| `sprite.set_palette` | ✅ ¹⁰ | ✅ ¹⁰ | ✅ ¹⁰ | ✅ ¹⁰ | ✅ ¹⁰ |
| `bkg.set_palette` (per-tile palettes) | GBC/AP ✅, DMG/Duck ❌ ¹¹ | ❌ ¹¹ | ✅ ¹¹ | ❌ ¹¹ | ✅ ¹¹ |
| `text.set_font` | ❌ ⁶ | ❌ ⁶ | ❌ ⁶ | ❌ | ❌ |

Footnotes:

1. Only the Game Boy family has a hardware window layer; the
   `show_window`/`hide_window` and `graphics.window` helpers are a compile
   error elsewhere (GBDK's SMS/GG `SHOW_WIN` macros are no-ops and the NES
   port has none, so this is honest-off rather than silently-broken).
2. `hw.read`/`hw.write` work mechanically everywhere, but register addresses
   are console-specific. The `REG_*` constants are **Game Boy addresses** and
   only exist on the GB family; referencing one elsewhere is a compile error —
   gate raw hardware access with `if platform == "..."`.
3. Text coordinates are character cells on every target (`SCREEN_COLS` ×
   `SCREEN_ROWS`). GBDK/NES render via the tile font; the Lynx scales cells to
   TGI pixels; the PC Engine uses conio cells. (GBDK text needs a font loaded —
   handled automatically by the prelude.)
4. Lynx sprites use the **Suzy hardware blitter** (the GB 8×8 2bpp tile model
   is kept as the API; tiles convert to literal Lynx sprite data + one SCB per
   slot, drawn via `tgi_sprite`). A sprite program owns the frame, so don't mix
   it with immediate `graphics.text`. See §5.4.
5. On the Lynx `wait_vblank` only paces the frame until the sprite or
   background engine is used, then it becomes a VBL-synced double-buffer flip
   (see the display-model note in §5.4).
6. `text.set_font` is declared but unmapped on every target — it will not link.
7. One portable square-wave channel; the tone generator is whatever the
   console has (GB-family APU, SMS/GG PSG, NES APU, Lynx Mikey, PCE PSG). The
   beep duration counts down in `video.wait_vblank` — see §6 `platform.sound`.
8. PCE sprites use the **VDC hardware** (GB tiles become 16×16 4bpp VDC
   patterns, a SATB mirror is flushed on `wait_vblank` and DMA'd at vblank).
   Text and sprites mix freely on the PCE. See §5.4.
9. The same GB background model (256-tile table, 32×32 map, u8 scroll wrap
   mod 256) on very different hardware: the PCE uses its real tilemap (VDC
   BAT + BXR/BYR scroll); the Lynx — which has no tilemap layer — composites
   the map into one large Suzy background sprite re-blitted with wrapped
   offsets each frame. On the Lynx a background program owns the frame like
   a sprite program does (don't mix with immediate `graphics.text`). See
   §5.4; worked example: `projects/background`.
10. `graphics.palette` exists on **every** console with graceful degradation
    instead of an error: on the 4-grey machines (DMG, Mega Duck) colors
    quantize to shades and apply through `BGP`/`OBP0`/`OBP1` — exactly what
    real GBC games do on a DMG — and consoles with fewer palette slots mask
    or ignore the extras (`sprite.set_palette` is an honest no-op on SMS/GG,
    whose sprites all share one palette). Slot 0 is the portable guarantee;
    the per-console slot counts live in `PLATFORM_CAPS`
    (`bkg_palettes`/`spr_palettes`). See §6 `graphics.palette`.
11. `bkg.set_palette` (per-tile background palette selection) requires
    per-tile palette hardware — the GBC attribute map, the PCE BAT bits, or
    the NES attribute table (16×16 px granularity: the rectangle is rounded
    outward) — recorded as `has_tile_palettes` in `PLATFORM_CAPS`. Elsewhere
    (DMG/Duck, SMS/GG, Lynx) it is a clear compile error.

> Portability rule of thumb: programs that stay within **text + input + timing +
> sound + sprites + background + palettes** (`graphics.text`, `platform.input`,
> `platform.system`, `platform.sound`, `graphics.sprite`, `graphics.bkg`,
> `graphics.palette`) are portable across **all nine consoles** — and size
> their world from `SCREEN_WIDTH`/`SCREEN_HEIGHT` instead of hardcoding
> 160×144. Add `graphics.window` and the program is Game Boy-family only; use
> `graphics.draw` and it is Lynx-only; `bkg.set_palette` needs per-tile
> palette hardware (GBC/Pocket, NES, PCE). Guard the non-portable parts with
> `if platform == "..."`.

## 6. Standard Library Reference

### platform.video
```mosaik
-- Screen geometry, set per build target by the prelude:
--   gameboy family 160x144 (20x18 cells) - sms 256x192 - nes 256x240
--   gamegear 160x144 - lynx 160x102 (20x12) - pce 256x224 (32x28)
const SCREEN_WIDTH: u16    -- visible width in pixels
const SCREEN_HEIGHT: u16   -- visible height in pixels
const SCREEN_COLS: u8      -- text width in character cells
const SCREEN_ROWS: u8      -- text height in character cells
function enable_lcd()      -- DISPLAY_ON; SHOW_BKG
function disable_lcd()     -- DISPLAY_OFF
function wait_vblank()     -- vsync()
function show_sprites()    -- SHOW_SPRITES
function hide_sprites()    -- HIDE_SPRITES
function show_background() -- SHOW_BKG
function show_window()     -- SHOW_WIN
function hide_window()     -- HIDE_WIN
```

### platform.input
```mosaik
-- Constants map to GBDK joypad bits (J_A, J_B, ...).
const INPUT_A; INPUT_B; INPUT_SELECT; INPUT_START
const INPUT_RIGHT; INPUT_LEFT; INPUT_UP; INPUT_DOWN
function pressed(button: u8) -> bool   -- joypad() & button
function held(button: u8) -> bool      -- (currently identical to pressed)
```

### platform.hardware
```mosaik
-- Raw memory-mapped I/O: register addresses and byte read/write. Use this for
-- hardware the higher-level modules don't cover yet (sound, palettes, timer).
-- The REG_* constants are Game Boy addresses and exist only on the GB family
-- (gameboy/gbc/pocket/megaduck); referencing one on any other console is a
-- compile error - gate raw access with `if platform == "..."`.
const REG_DIV; REG_NR10; REG_BGP; REG_OBP0; REG_OBP1   -- 0xFF04/10/47/48/49
function write(address: addr, value: u8)   -- *(volatile uint8_t *)address = value
function read(address: addr) -> u8         -- return *(volatile uint8_t *)address
```

### platform.system
```mosaik
function delay(ms: u16)         -- delay(ms)
function random() -> u8         -- rand()   (needs <rand.h>, emitted in the prelude)
function seed_random(seed: u16) -- initrand(seed)
```

### platform.sound
```mosaik
-- One portable square-wave channel (Audio Tier-1). beep() is non-blocking:
-- it starts the tone and returns; `frames` is the duration in wait_vblank
-- ticks (60 = 1 second on every console; 0 = play until stop()). The
-- countdown runs inside video.wait_vblank, so a program that never calls
-- wait_vblank keeps the tone until sound.stop(). The generator per console:
-- GB family = APU pulse channel 2 - sms/gamegear = SN76489 PSG channel 0 -
-- nes = APU pulse 1 - lynx = Mikey audio channel A - pce = PSG channel 0.
-- Useful freq range is about 110 Hz - 8 kHz on every target (each backend
-- clamps the low end to what its divider can express).
function beep(freq: u16, frames: u16)   -- gbs_sound_beep
function stop()                          -- gbs_sound_stop
```

### graphics.sprite
```mosaik
const FLIP_X; FLIP_Y    -- per-console bits (GBDK S_FLIPX/S_FLIPY, Suzy
                        -- HFLIP/VFLIP, PCE SATB X/Y-invert via set_prop)
function set_data(first: u8, count: u8, data: addr) -- set_sprite_data
function set_tile(id: u8, tile: u8)           -- set_sprite_tile
function get_tile(id: u8) -> u8               -- get_sprite_tile
function set_prop(id: u8, prop: u8)           -- set_sprite_prop
-- (x, y) are SCREEN-PIXEL coordinates: (0, 0) is the top-left of the visible
-- screen on every console (the prelude applies the hardware offset, e.g. GB
-- OAM +8/+16). Hide a sprite by moving it to y = SCREEN_HEIGHT.
function move(id: u8, x: u8, y: u8)           -- gbs_move_sprite
-- Which graphics.palette sprite slot this sprite renders with (OAM attr bits
-- on the GB family/NES, the SCB penpal on the Lynx, SATB bits on the PCE;
-- honest no-op on SMS/GG, whose sprites share one palette).
function set_palette(id: u8, slot: u8)        -- gbs_sprite_palette
```

### graphics.bkg
```mosaik
function set_data(first: u8, count: u8, data: addr)   -- set_bkg_data
function set_tiles(x: u8, y: u8, w: u8, h: u8, tiles: addr) -- set_bkg_tiles
function scroll(dx: i8, dy: i8)               -- scroll_bkg
function move(x: u8, y: u8)                    -- move_bkg
-- Per-tile palette selection (has_tile_palettes consoles only: GBC/Pocket
-- attribute map, NES attribute table at 16x16 granularity, PCE BAT bits;
-- a clear compile error elsewhere). Map coordinates wrap mod 32.
function set_palette(x: u8, y: u8, w: u8, h: u8, slot: u8) -- gbs_bkg_palette_fill
```
The comments give the GBDK lowering; on the cc65 consoles the same calls go to
the `gbs_set_bkg_*`/`gbs_move_bkg` engine helpers (PCE: VDC BAT + BXR/BYR
scroll; Lynx: the composited Suzy background sprite — see §5.4/§5.5).
Scroll offsets are u8 and wrap mod 256 (= the 32×32 map size) on every
console.

### graphics.window
```mosaik
function set_tiles(x: u8, y: u8, w: u8, h: u8, tiles: addr) -- set_win_tiles
function move(x: u8, y: u8)                    -- move_win
```

### graphics.text
```mosaik
function print_string(x: u8, y: u8, text: string)   -- gotoxy + printf("%s")
function print_number(x: u8, y: u8, number: u8)      -- gotoxy + printf("%d")
function clear_area(x: u8, y: u8, width: u8, height: u8)
-- set_font(font_data: addr) is declared in the stdlib module but is not mapped to a
-- helper, so calling it will not link. (🔭 planned)
```

### graphics.palette ✅

The portable color model: a **palette slot holds 4 colors**, matching the GB
2bpp tiles every console shares, with the GB transparency rules kept (sprite
color 0 is transparent everywhere; bkg color 0 is a real color — the
paper/backdrop). A color is an opaque `u16` made by `palette.rgb`, which
quantizes RGB888 to the console's native format: RGB555 on the Game Boy
Color/Analogue Pocket, RGB222 on the SMS, RGB444 on the Game Gear, the
nearest NES master-palette entry, 12-bit Mikey pens on the Lynx, 9-bit VCE
words on the PC Engine — and a plain DMG shade 0–3 on the 4-grey consoles
(Game Boy, Mega Duck), where `set_bkg`/`set_sprite` pack `BGP`/`OBP0`/`OBP1`.
So one colored source builds and stays meaningful on **all nine consoles**.

```mosaik
function rgb(r: u8, g: u8, b: u8) -> u16    -- quantize RGB888 to a native color
function set_bkg(slot: u8, c0: u16, c1: u16, c2: u16, c3: u16)
function set_sprite(slot: u8, c0: u16, c1: u16, c2: u16, c3: u16)
function load_bkg(slot: u8, colors: addr)    -- e.g. an asset's <name>_palette
function load_sprite(slot: u8, colors: addr)
```

- **Slot 0 is the portable guarantee.** The per-console slot counts are
  `PLATFORM_CAPS` (`bkg_palettes` / `spr_palettes`): GBC/Pocket 8+8, NES and
  PCE 4+4, Lynx 1 bkg + 4 sprite (the 16 Mikey pens partition exactly into
  pen 0 = backdrop/transparent, 4×3 sprite pens, 3 bkg pens), SMS/GG 1+1
  (CRAM), DMG/Duck 1 bkg + 2 sprite (`OBP0`/`OBP1`). Out-of-range slots are
  masked or ignored at run time.
- **Text follows bkg slot 0** — text lives in the background layer on every
  console — with paper = bkg color 0 and ink = bkg color 3 (GBDK font tiles,
  the Lynx pen partition, and the PCE text palette all reproduce this).
- On the **NES**, color 0 of bkg slot 0 is the shared PPU backdrop; on the
  **PCE**, bkg color 0 is the global backdrop (VCE `$000`) — both match the
  GB intuition that bkg color 0 is "the screen color".
- Per-sprite slot selection is `sprite.set_palette(id, slot)`; per-tile
  background selection is `bkg.set_palette(x, y, w, h, slot)` on
  `has_tile_palettes` consoles (see §5.5 footnotes 10–11).
- **Asset palettes:** an indexed PNG with ≤ 4 entries (the kind whose indices
  map literally to GB colour values, §5.1) also emits its authored palette as
  `const u16 <name>_palette[4]`, converted to the native format at build
  time — `palette.load_sprite(0, player_palette)` recolors the asset with
  its authored colors on every console.
- Programs that never `import "graphics.palette"` emit no palette code and
  keep today's grey-ramp defaults byte-for-byte.
```mosaik
import "graphics.palette"
palette.set_bkg(0, palette.rgb(16, 24, 64), palette.rgb(96, 110, 200), palette.rgb(48, 56, 120), palette.rgb(235, 240, 255))
palette.set_sprite(0, palette.rgb(0, 0, 0), palette.rgb(255, 200, 120), palette.rgb(220, 90, 30), palette.rgb(255, 240, 200))
sprite.set_palette(0, 0)
```
Worked example: `samples/colors.mos` (one source, all nine consoles).

## 7. Example Programs

### 7.1 Hello / Counter

```mosaik
module "main" {
    import "platform.video"
    import "graphics.text"

    var frame_count: u8 = 0

    function main() {
        video.enable_lcd()
        text.print_string(2, 4, "Hello, World!")

        loop {
            frame_count += 1
            text.print_number(2, 6, frame_count)
            video.wait_vblank()
        }
    }

    export main
}
```

### 7.2 Simple Movement Loop

```mosaik
module "game" {
    import "platform.input"
    import "platform.video"

    var player_x: u8 = 80
    var player_y: u8 = 72

    function update() {
        if input.pressed(INPUT_LEFT)  { player_x -= 1 }
        if input.pressed(INPUT_RIGHT) { player_x += 1 }
        if input.pressed(INPUT_UP)    { player_y -= 1 }
        if input.pressed(INPUT_DOWN)  { player_y += 1 }
    }

    function main() {
        video.enable_lcd()
        loop {
            update()
            video.wait_vblank()
        }
    }

    export main
}
```

See the `samples/` folder (`text_simple`, `text`, `text_complex`, `bounce`, `pong`,
`control_flow`, `graphics_showcase`, `cross_platform`, `metasprite`, `colors`) and the
full projects for runnable programs: `projects/game`, `projects/shmup` (asset
pipeline), `projects/background` (scrolling tilemap), `projects/colorlab`
(palettes), and `projects/endless-runner` (the native-feature pipeline —
metasprites, 4bpp/16-colour, `native.lynx`).

## 8. Roadmap

### Done
- Lexer, parser, best-effort type checker, shared C code generator with two backends.
- Structs, enums, modules, functions, control flow, expressions (see §2).
- `while` / `break` / `continue` and `switch` / `case` / `default`.
- Hex (`0xE4`) and binary (`0b1010`) integer literals.
- Array `const` data emitted as real C `const` tables.
- **GBDK backend**: stdlib mapped to GBDK helpers — video (sprite/window visibility),
  input, hardware, system (delay/random), sprite, background, window, text.
  Targets: Game Boy / Color, Analogue Pocket, Mega Duck, SMS, Game Gear, NES.
- **cc65 backend**: data-driven profiles for Atari Lynx (TGI) and PC Engine (conio).
  Tier-1 portable stdlib (`platform.*`, `graphics.text`) on both; Suzy hardware sprites
  (`graphics.sprite`) and TGI draw primitives (`graphics.draw`) on the Lynx.
- Uniform capability gating via `PLATFORM_CAPS` registry — clear compile-time errors on
  every backend when a console lacks a stdlib call.
- Two-mode build system (single file / project), `clean`, `init`; GBDK-2020 and cc65
  autodetection.
- Unit tests in `tests/` plus end-to-end sample builds (~90 ROM matrix via `--samples`).
- True per-platform conditional compilation (evaluate `platform == ...`).
- Cross-file module linking (whole-program compilation of all sources into one C
  translation unit, export-list enforcement, single-file import closure — see §3.1),
  with module-level tree-shaking (unreferenced modules pruned from the ROM).
- Compiler organised as the `mosaik/` package (lexer / ast / platforms / parser /
  typechecker / codegen split by backend); golden snapshot tests guard the codegen.
- ROM banking: `bank(N)` function placement → MBC5 carts up to 8 MB on the Game Boy
  family, with `rom_size`/`ram_size` cartridge geometry in `mosaik.toml` (see §2.7
  and `docs/banking-plan.md`); config honesty warnings for unapplied/unknown keys.
- Color & palettes: `graphics.palette` — 4-color GB-model palette slots on all
  nine consoles (RGB888 quantized per console; greyscale degradation on
  DMG/Mega Duck), `sprite.set_palette`, per-tile `bkg.set_palette` on
  GBC/Pocket/NES/PCE, asset-pipeline `<name>_palette` emission (see §6 and
  `docs/color-palette-plan.md`; sample: `samples/colors.mos`).

### Planned 🔭
- Inline `asm { ... }`, memory `region` blocks, `stack var`, pointers, function types.
- Array bounds checking and stricter type enforcement.
- Optimization passes / register allocation beyond what SDCC provides.
- Expanded standard library (audio, timer, backgrounds, math, collections, strings).
- Array iteration in `for`, more compound assignments (`%=`/`*=`/`/=`).
- Banked `const` data and `bank(auto)` placement via bankpack; banking beyond the
  Game Boy family (see `docs/banking-plan.md` §6).

## Technical Considerations

### Memory Constraints (target hardware)

| Console | RAM | VRAM | Notes |
| --- | --- | --- | --- |
| Game Boy (DMG) | 8 KB WRAM | 8 KB | ROM banking via GBDK |
| Game Boy Color | up to 32 KB WRAM | 16 KB | |
| SMS / GG / NES | varies | varies | GBDK port handles layout |
| Atari Lynx | 64 KB | (framebuffer) | Suzy blitter; cc65 linker cfg |
| PC Engine | varies | (VRAM tiles) | cc65 conio; no TGI sprite engine |

### Compatibility
- GBDK backend: standard GBDK-2020 C built with `lcc`/`sdcc`. GBC ROMs use the
  `sm83:gb` port with the makebin CGB-compat flag (`-Wm-yc`).
- cc65 backend: standard cc65 C built with `cl65 -t <target>`. Lynx and PCE use
  the bundled cc65 (located via `CC65_HOME`, bundled `cc65/`, or `cl65` on `PATH`).
