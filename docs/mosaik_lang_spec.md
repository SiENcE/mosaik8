# mosaik Language Specification

## Project Overview

**Language Name:** mosaik
**Target Platforms:** Nine retro consoles across two C backends:
- **GBDK backend**: Game Boy (DMG), Game Boy Color, Analogue Pocket, Mega Duck,
  Sega Master System, Game Gear, NES/Famicom
- **cc65 backend**: Atari Lynx, PC Engine / TurboGrafx-16

**Compilation Model:** mosaik emits readable C - GBDK C for GBDK consoles
(linked by `lcc`/`sdcc`), cc65 C for cc65 consoles (linked by `cl65`). The
lexer, parser, type-checker, and all logic codegen are shared; only the prelude
and standard-library lowering differ per backend.
**Philosophy:** Modern, expressive syntax (inspired by Lua/Python) that lowers
cleanly onto the target C runtime, with per-console capability gating enforced at
compile time.

> **File extensions**
> - `.mos` - mosaik **source** files
> - `.c` - generated C (intermediate build output; GBDK C or cc65 C)
> - `.gb` / `.gbc` / `.pocket` / `.duck` / `.sms` / `.gg` / `.nes` - GBDK ROM output
> - `.lnx` / `.pce` - cc65 ROM output (Atari Lynx / PC Engine)

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
# Lines starting with '#' are comments too; the compiler ignores them.

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

-- Function types (callbacks): a first-class function value.
function(u8, u8)         -- a callback taking (u8, u8), returning nothing
function(u8) -> bool     -- ...returning bool
var cb: function(u8, u8) = handler          -- a bare function name IS the value
var table: array[function(u8, u8), 8]       -- an array of callbacks
type Anim = struct { period: u8, cb: function(u8, u8) }   -- as a struct field
function run(f: function(u8, u8), x: u8) { f(x, 0) }      -- as a parameter
```

**Function pointers / callbacks**

- A `function(T...) -> R` type holds a function value. A bare function name
  (`handler`) or a qualified one (`mod.handler`) used where a value is expected
  *is* the pointer - mosaik has no `&`/pointer operators and needs none. Call
  through one like any function: `cb(x)`, `table[i](x)`, `rec.cb(x)`.
- There is no captured environment (no closures). A callback reads game state
  through shared module-level `var`s (the way `engine.camera` shares `camx`).
- **Portability rule:** a callback may only target a **home-bank** function.
  Referencing a `bank(N)` function is a compile error on every console where
  banking is real (`has_banking`: the GB family, SMS/GG, NES; sdcc's banked
  far-call is generated at the call site, so the address alone cannot reach a
  switched-out ROM bank); on consoles where `bank()` is ignored (Mega Duck,
  Lynx, PCE) the function lives in the linear image and the reference is
  unrestricted. A *direct call* to a banked function (the trampoline path) is
  unaffected.
- **NES limit:** a callback TYPE whose arguments total more than 2 bytes is a
  compile error on `nes` (SDCC's mos6502 backend calls through a pointer with
  a non-reentrant convention capped at 2 argument bytes; `function(u16, u16)`
  is 4). A direct call is fine; only the pointer type is limited
  (`gen_fnptr._check_fnptr_arg_bytes`).
- Lowering: each distinct signature becomes one C function-pointer `typedef`,
  emitted only when callbacks are used (callback-free programs are byte-identical).
- Worked use: the `engine.anim` framework module drives per-sprite animation
  through an "apply" callback (see `docs/game-framework.md`).

See **§2.8** for the full how-to (declaring, passing, calling through, and the
`engine.anim` animation pattern).

**Type checking notes (current behavior)**

- Type checking is **best-effort**: it produces diagnostics for simple programs, but if
  it raises on a more advanced construct the compiler prints a warning and proceeds to
  code generation anyway. It never blocks a build.
- **Integer arithmetic is C's** (the generated `(a op b)` carries no casts):
  the checker's VALUE model promotes anything narrower than `int` to a 16-bit
  `int`, so `u8 - u8` is `i16` and never wraps, and `u16` beats `i16` (an
  `i16`/`u16` pair is unsigned, which is why that comparison warns). A `var`'s
  inferred STORAGE type is the other rule: it keeps the rank order
  `u8 < i8 < u16 < i16`, so `var d = a - b` on two `u8` is a `uint8_t` and the
  assignment truncates, exactly as C does at an assignment
  (`typechecker.promote_arithmetic_type` / `_storage_type`, pinned by
  `tests/integer_semantics_test.py`; 63 engine sites depend on the C model).
- Numeric literals are inferred to the smallest fitting type (`u8`, then `i8`, `u16`, `i16`).
  They may be written in decimal, hexadecimal (`0xE4`), or binary (`0b1010`).
- A leading `-` on a number is folded into a NEGATIVE literal (typed `i8`/`i16`
  by value); a negative initializer into `u8`/`u16`/`addr` is a diagnostic. A
  float literal (`1.5`) is a positioned syntax error.
- String literals take C's common escape subset: `\n \t \r \0 \\ \" \' \xNN`
  (an unknown escape is a positioned error). An inferred string type carries its
  NUL (`var s = "hi"` is `array[u8, 3]`).
- Argument and parameter lists REQUIRE commas (`f(1 2)` is refused with its
  line) and may span lines inside their parentheses.
- What the checker does enforce (2026-09-06): call arity and argument types,
  literal ranges at a `var`, an assignment, a call parameter and a `return`;
  the four return checks (never returns a value / bare `return` / a value from
  a void function / out-of-range return literal); `const` targets; a scope
  that resets per module. Every diagnostic is `<file>:<line>: ...` on stderr;
  a codegen failure is a located `CompileError` (`MOSAIK_TRACEBACK=1` for the
  Python traceback). Not enforced: argument typing through a FUNCTION POINTER.
- 🔭 Bounds checking on array access is planned.

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
| Bitwise     | `&  \|  ^  <<  >>` |
| Comparison  | `==  !=  <  >  <=  >=` |
| Logical     | `and  or  not` |
| Assignment  | `=  +=  -=  |=  &=  ^=` |
| Access      | `.` (field), `[]` (index), `()` (call) |

**Bitwise operators** bind TIGHTER than comparison (the Rust/Go ordering,
deliberately not C's): `a & b == c` means `(a & b) == c`. Precedence, loosest
to tightest: `or` → `and` → `==`/`!=` → `<`/`>`/`<=`/`>=` → `|` → `^` → `&`
→ `<<`/`>>` → `+`/`-` → `*`/`/`/`%` → unary. A shift's result keeps its left
operand's width. There is no unary `~`; use `x ^ 255` (u8) / `x ^ 65535`
(u16). The emitted C is fully parenthesized, so C's own precedence never
applies. Prefer `& 31` / `& 255` over `% 32` / `% 256` for power-of-two wraps
(same semantics on unsigned values, cheaper code on cc65).

Aggregate literals are supported: struct literals `{x: 1, y: 2}` and array literals
`[0, 1, 2, 3]`. Assigning a struct literal to an existing variable is expanded into
per-field stores in the generated C. An aggregate literal is valid ONLY as a
declaration initializer or an assignment right-hand side: returning one or passing
one as a call argument is a compile error (assign it to a local first).

**Implemented**: `loop`, `while <cond>`, `break`, `continue`, `switch`/`case`/`default`,
`if`/`else if`/`else`, and the numeric range `for`.

Statement rules the compiler enforces (each is a clear compile error):

- **`break` directly inside a `switch` case is rejected.** Cases auto-break, so a
  user `break` there almost always means "break the enclosing loop" -- which a C
  `break` would NOT do. Set a flag in the case and break outside the switch. A
  `break` inside a loop nested in a case exits that loop, as usual.
- **Assignment is a statement-level operation with a real target**: `=` in an
  `if`/`while` condition is rejected (use `==`), and the target must be a
  variable, struct field, or array element (`1 = 2` is rejected).
- **A module-level name may not be referenced before a same-named local's
  declaration inside a function** (the reference would bind to the not-yet-
  declared local); rename the local or move its declaration up.
- **The `for` loop variable follows its bounds' width**: a 16-bit bound widens
  the variable to u16, so `for i in 0..256` iterates all 256 values (it used to
  loop forever on a u8 variable).

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
  variable), the compiler keeps the `then` branch - the previous behaviour.
- **Statement level too.** The same folding runs on an `if` / `else if` chain
  INSIDE a function body (`parser._fold_conditional_stmt`): a branch that
  cannot be taken is dropped instead of emitting a runtime test, and a chain
  is pruned one link at a time. A MIXED condition is partially evaluated
  (`_simplify_cond`): `FLAG and tk == 3` becomes `tk == 3` when `FLAG` is on
  and plain false when it is off; a condition with no compile-time part stays
  an ordinary runtime `if`.
- **Build-supplied `defines` fold as well.** The build passes a dict of
  defines; a bool flag is tested as a bare identifier (`if VM_OP_FADE { }`),
  and an INTEGER define may size an array (`array[u8, VM_ACTOR_POOL]`,
  `parser.int_define`; a bool never sizes an array, and an unknown identifier
  in a size position is a clear error, never a silent default). Only names the
  build actually passed resolve, so a runtime `if some_var` is untouched. This
  is how `lib/vm/core.mos` guards each opcode arm at zero bytes when unused.

Canonical platform names and their aliases (`platforms.PLATFORM_ALIASES`):

| Canonical name      | Aliases              |
| ------------------- | -------------------- |
| `gameboy`           | `gb`, `dmg`          |
| `gameboy_color`     | `gbc`, `cgb`, `color` |
| `analogue_pocket`   | `ap`, `pocket`, `analogue` |
| `megaduck`          | `duck`, `mega_duck`  |
| `sms`               | `master_system`, `sega_master_system` |
| `gamegear`          | `gg`, `game_gear`    |
| `nes`               | `famicom`            |
| `lynx`              | `atari_lynx`, `atarilynx` |
| `pce`               | `turbografx`, `turbografx16`, `tg16`, `pc_engine`, `pcengine` |

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

### 2.7 ROM Banking (`bank(N)`)

```mosaik
bank(2) function spawn_wave() { ... }          -- placed in ROM bank 2
bank(2) local function helper() -> u8 { ... }  -- bank() always comes first
```

A placement annotation on module-level functions. Banking is real on every console whose
`has_banking` capability is set: the **Game Boy family** (`gameboy`,
`gameboy_color`, `analogue_pocket`: an MBC5 cartridge), **SMS / Game Gear**
(the Sega mapper, slot 1 via `MAP_FRAME1`) and the **NES** (UNROM / iNES
mapper 30, `_switch_prg0`). Each used bank becomes its own generated C file
(SDCC's `#pragma bank` is file-scoped) and every call to a banked function
goes through sdcc's `__banked` far-call trampoline - calls look and behave
exactly like ordinary calls, so `bank(N)` changes placement, never semantics.
The cart auto-sizes to the highest bank used (or honours an explicit `[build]
rom_size`, erroring if it is too small). On the **Mega Duck** (its mapper is
unverified) the annotation is **accepted and ignored** (one note per build),
so a source with banked code still builds for all nine consoles. The two cc65
consoles bank only when the program asks for code banking (`[build]
code_banks`, below): the **PC Engine** then maps real 16 KB ROM banks and the
**Lynx** loads cart OVERLAYS; without it a bare `bank(N)` keeps their linear
image, byte for byte.

- `N` is 1..511, and `main()` cannot be banked.
- **`bank(0)` PINS a function to the home bank.** ✅ Without `[build]
  code_banks` it is a no-op (an unannotated function is already resident, and
  the output is byte-identical), which is why it used to be rejected. With
  code banking it is the only way to say *this one is hot, keep it resident*:
  that feature banks a whole MODULE, while hotness is per FUNCTION. The
  generated collision probes in `src/rooms.mos` use it - they run 30+ times a
  frame and are ~40 bytes each, while the rest of that module is cold
  room-load code that must bank or bank 0 overflows.
- `bank` is contextual - only `bank(N)` before `function` is the annotation;
  variables named `bank` keep working.
- **`hot` marks a function the frame calls** (`hot function f()`, `hot local
  function g()`, `hot bank(0) function h()`; contextual like `bank`). It keeps
  the function resident where a cold call is EXPENSIVE - the Lynx, whose code
  banks are cart overlays loaded on a miss (~13 ms a KB) - and changes nothing
  anywhere else (a GB / Sega / NES / PCE bank switch is cheap; `bank(0)`
  stays the pin there). The runtime under `lib/` carries the measured
  per-frame set; the generated `rooms` / `scenes` / `clips` modules get theirs
  from the generators, for a project that targets the Lynx with
  `code_banks` only.
- Module-level `var` data always stays in the home bank. A `const` array
  leaves it in two ways: an array handed to `platform.assets` (`use` / `ptr`
  / `range_base`) is STREAMED (see below), and under `[build] code_banks` a
  const array read only by one module's banked code is defined in that
  module's bank TU, while `[assets]` sprite sheets read only by
  `sprite.set_data` go into a DATA bank and upload through a far read
  (`gbs_spr_data_far`). A const read from the home bank stays home
  (`tests/code_banking_test.py`).

**Streamed `const` data (`platform.assets`).** Two residency strategies,
picked by the target (`gen_streaming._collect_streamed`): on the **Lynx** the
cart is not CPU-addressable, so the streamed arrays are archived and copied
into an LRU cache through a cart read; on a **banking console** the array
stays in ROM, packed greedily into 16 KB data banks above the highest
`bank(N)` function (only once the streamed total exceeds one bank, or as soon
as `code_banks` is on), and `assets.ptr` bank-switches to read it. **An array
can never span banks**: one larger than 16,384 bytes is a clear compile error
(split the data). Every other console keeps the data resident and the seam is
a no-op / passthrough, byte-identical.

- **A streamed POINTER is valid only while its bank is mapped**
  (`_check_streamed_args`, a compile-time error): `assets.ptr` of a banked
  const passed to a callee in ANOTHER bank (the trampoline maps the callee's
  bank first), or two such pointers of different banks in one call (the second
  unmaps the first), is refused. Read the array in the home bank and pass
  VALUES. The function that dereferences a streamed pointer itself stays home.

**`[build] code_banks` (cold-code banking).** A list of module names whose
function BODIES leave the resident image, one ROM bank per module, on a
`has_banking` console, the **PC Engine** and the **Lynx** (ignored with a note
elsewhere; absent = byte-identical). The two cc65 consoles lower it their own
way, under the same rules:

- **PC Engine**: ONE 16 KB window at `$4000-$7FFF` (MPR2 + MPR3, which cc65's
  crt0 leaves free); logical bank k is physical banks 2k+2 / 2k+3 behind the
  unchanged 32 KB resident image, and the HuCard grows to the next power of
  two (up to 1 MB). Streamed data banks exactly as on the GB family.
- **Lynx**: each listed module's functions are one cart OVERLAY, linked at a
  RAM window at the bottom of RAM (MAIN moves up above it; the window is the
  largest overlay) and read from the cart when a call finds another one
  loaded; the caller's overlay is reloaded on the way back. Data still
  streams through the cart archive; string literals and `--static-locals`
  locals stay in MAIN. `bank(0)` and `hot` functions stay resident; a cold
  call in a steady frame is a load per frame, so the build counts loads
  (`gbs_ovl_loads`).
- Both: a banked function's NAME is a 6-byte resident thunk (its body is
  `<name>__bk` in the bank's segment), so a call is an ordinary `jsr` and a
  pointer to the name is a resident address. Tests `pce_banking_test.py`,
  `lynx_overlay_test.py`.

The rules, each pinned by `tests/code_banking_test.py`:

- An address-taken function (a callback registered through a seam) is SPLIT:
  a resident stub keeps the address, the body moves to the bank as
  `<name>__bimpl BANKED`.
- A function whose body switches the ROM window (a streamed-seam read) can
  never bank; it stays home with a bank-neutrality wrapper. A
  window-switching function is what pins the generated `scripts` renderers
  home on a project with a streamed strings blob.
- `scripts` is REFUSED outright (its `fetch()` runs per instruction byte).
  `vm.core` is accepted because it pins its own hot path with `bank(0)`.
- **A per-frame function in a banked module MUST be pinned with `bank(0)`**
  (every call into a bank is a trampoline, ~164 cycles); measure with
  `emu/gb_profile.py calls`. A module-private `local function` whose callers
  all share its bank is emitted without `BANKED` (a direct near call).
- The streamed-data threshold drops to 0 with code banking on; the first-seen
  `assets.code_byte` blob (the bytecode fetch) stays resident, later blobs
  bank.

**`[build] bank_bytecode`** puts the VM8 bytecode blob (`_scripts_CODE`, the
biggest resident symbol of a VM8 game) into a ROM bank instead of bank 0, at
the cost of a `SWITCH_ROM` per fetched byte (~0.5 % of an LCD frame for an
event-script game, ~28 % at peak for the falling-block assembly sample). It needs `code_banks`;
without it there is no bank to switch to and the blob stays resident.

### 2.8 Callbacks (function pointers) & animation

A **callback** is a function passed around as a value. mosaik has first-class
function pointers: a `function(T...) -> R` type holds a function, and a function
*name* used where a value is expected *is* that pointer - there is no `&`
operator (mosaik has none).

**Declaring the type and taking a reference.** A function name (`handler`) or a
module-qualified one (`mod.handler`) used in a value position is the pointer:

```mosaik
function on_hit(slot: u8, dmg: u8) { ... }

var cb: function(u8, u8) = on_hit        -- store it (the name is the value)
var table: array[function(u8, u8), 4]    -- an array of callbacks
type Timer = struct { left: u8, fire: function(u8, u8) }   -- a struct field
```

**Passing and calling through.** A callback is an ordinary parameter; call
through it like any function (`cb(x)`, `table[i](x)`, `rec.fire(x)` - no special
syntax, no dereference):

```mosaik
function for_each(f: function(u8, u8), n: u8) {
    for i in 0..n {
        f(i, 0)                          -- call through the pointer
    }
}

function main() {
    table[0] = on_hit
    table[0](3, 1)                       -- through an array element
    for_each(on_hit, 4)                  -- passed by name
}
```

**Rules and limits.**

- **No closures.** A callback carries no captured environment; it reads game
  state through shared module-level `var`s (the way `engine.camera` shares
  `camx`). This matches the no-heap model.
- **Home-bank only.** A callback must target a function in the home bank.
  Referencing a `bank(N)` function as a value is a compile error wherever
  banking is real (GB family, SMS/GG, NES; sdcc's banked far-call is generated
  at the *call site*, so a bare address cannot reach a switched-out ROM bank -
  the same hazard as a streamed `const` pointer). A *direct call* to a banked
  function is unaffected. Where `bank()` is ignored (the Mega Duck, and the
  Lynx / PCE without `[build] code_banks`) the function is in the linear image
  and the reference is unrestricted. Under
  `[build] code_banks` an address-taken function is split into a resident stub
  plus a banked body, so registering it stays legal (§2.7).
- **NES argument bytes.** On `nes` a callback type may carry at most 2 bytes
  of arguments (SDCC mos6502's non-reentrant pointer-call convention); a
  `function(u16, u16)` type is a compile error there, a direct call is not.
- **Lowering.** Each distinct signature becomes one C function-pointer `typedef`,
  emitted only when callbacks are used, so callback-free programs are
  byte-identical.

**Animation - the canonical use.** The framework module `engine.anim` turns the
inline "tick counter + `%` frame-select + `set_tile`/`set_meta` swap" into a
data-driven animator driven by an *apply callback*. You write the
frame→graphic mapping; the module owns the clock:

```mosaik
import "engine.anim"

-- apply callback: frame -> graphic. `slot` is the sprite the animator drives.
function hero_frame(slot: u8, frame: u8) {
    sprite.set_tile(slot, frame)         -- or set_meta for a metasprite
}

function main() {
    sprite.set_data(0, 2, HERO_TILES)
    anim.set(0, 8, 2, hero_frame)        -- animator 0: 2 frames, step every 8 ticks
    -- ...
    loop {
        anim.tick()                      -- advance + re-assert the current frame
        video.wait_vblank()
    }
}
```

`anim.tick()` re-asserts the current frame every frame (the Lynx rebuilds its
sprite composite each present, so a metasprite must be re-asserted or it drops
out; harmless elsewhere). Other verbs: `anim.set_period(i, period)` (change the
rate, e.g. faster while running), `anim.reset(i, frame)` (snap to a frame, e.g.
standing), `anim.clear(i)` (stop). `engine.anim` fits a **fixed-period frame
cycle** on one sprite; facing selection, one-shot effects and free-running
register flicker stay inline. Full guide:
[game-framework.md → Animation](game-framework.md#animation--gameanim-callback-driven).

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

#### Cross-file module linking

`import "name"` either names a stdlib module (resolved by the compiler, see §3.2)
or another module of the program. All `.mos` files of a build are compiled
**together into one C translation unit** (whole-program compilation - the natural
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
  segment, and stdlib aliases (`video`, `input`, `hw`, `system`, `sound`,
  `save`, `assets`, `sprite`, `bkg`, `window`, `text`, `draw`, `palette`,
  `lynx`, `huge`; the module names of `CodeGenerator.ALL_STDLIB_CALLS`) are
  reserved.
- C-level naming: with more than one module in the program, every module-level
  symbol is emitted as `<module>_<name>` (e.g. `counter_tick`; `main()` keeps
  its name as the entry point); parameters and locals shadow module symbols.
  A single-module program keeps plain C names, and a `module.func(...)` call
  that matches no stdlib or program module still lowers to `module_func(...)`.
- Struct/enum **type names** (and enum variant names) are program-global -
  two modules must not declare the same one.
- **Tree-shaking**: when a module defines `main()`, only it and the modules it
  reaches (through `import` *or* a qualified `other.foo` reference, transitively)
  are emitted - so adding an unused module to a project doesn't bloat the ROM.
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
                     set_overlay (the Lynx present-time UI overlay hook; no-op elsewhere),
                     SCREEN_WIDTH/SCREEN_HEIGHT/SCREEN_COLS/SCREEN_ROWS (per target)
platform.input    -- pressed, held, raw, INPUT_A/B/SELECT/START/RIGHT/LEFT/UP/DOWN  (pressed IS held: level, no edge detector)
platform.hardware -- write, read, REG_DIV/REG_NR10/REG_BGP/REG_OBP0/REG_OBP1
platform.system   -- delay, random, seed_random
platform.sound    -- beep, stop, sfx, SFX_COIN/HURT/JUMP/POINT/SELECT  (one beep channel; every console)
platform.save     -- enable, disable, write_u8, read_u8  (battery SRAM; GB family only -- has_save, honest-off elsewhere)
platform.assets   -- use, ptr, address, code_byte, bank_enter, bank_leave, range_base, use_range, ptr_range, range_byte  (the asset residency seam: a const array named here STREAMS - Lynx cart archive + LRU cache, GB-family/SMS/GG/NES data banks - and the seam bank-switches or copies on read; a no-op/passthrough, byte-identical, where nothing streams; see §2.7)
graphics.sprite   -- set_data, set_tile, get_tile, set_prop, set_meta, set_meta_mask, move, set_palette, FLIP_X, FLIP_Y
graphics.bkg      -- set_data, set_tiles, scroll, move, set_palette (has_tile_palettes consoles)
graphics.window   -- set_tiles, move
graphics.text     -- print_string, print_number, clear_area, set_font, set_font_at, glyph_buffer, fill_box, to_window, to_bkg, window_active, win_sprite_cut, win_overlay_cut, plot_tile  (font swap: GB family + SMS/GG; glyph_buffer: the ROM-font text mode, GB family + SMS/GG; fill_box overlay box: cc65 only; to_window/win_sprite_cut/win_overlay_cut/plot_tile: the GB-family UI overlay + raw-tile router, no-op degradation elsewhere)
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

**Degraded lowerings are announced, once per build.** Some verbs are honest
but weaker than their name on a given console, and the build prints a note for
each one the program actually uses (`platforms.degraded_uses`, keyed off the
same condition the emitter uses; `tests/degraded_verbs_test.py` checks both
directions): `input.pressed` is `input.held` on EVERY console (the runtime has
no edge detector, so a held button reads as pressed every frame; latch the
previous state yourself); `FLIP_X` / `FLIP_Y` are ignored where the sprite
hardware has no flip (`has_sprite_flip` is off on SMS/GG; the art has to be
pre-mirrored); `bkg.set_attrs` is a no-op where there is no per-tile palette
map; and a button the console does not have is a dead constant (`INPUT_START`
and `INPUT_SELECT` are 0 on the Lynx, so every test against them is false).

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
is split by stage - `lexer`, `ast_nodes`, `platforms`, `parser`,
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
- **Array `const`s** become real C `const` tables (`const uint8_t name[N] = { ... };`) - a
  `#define` cannot hold aggregate data - which is how tile/map/music tables are emitted.
- `loop` → `while (1)`, `while c` → `while (c)`, `for x in a..b` → `for (uint8_t x = a; x < b; x++)`;
  `break`/`continue` pass through; `switch` → a C `switch` where each `case` body is wrapped
  in a block and auto-`break`s (multi-value labels emit consecutive `case` labels).
- Stdlib calls are selected from `STDLIB_CALLS_GBDK` or `STDLIB_CALLS_CC65_*` into
  `self.stdlib_calls` by the backend; calling anything absent from the active map - but
  present in the global `ALL_STDLIB_CALLS` set - is a clear compile-time error.

### 4.3 Optimization & Register Allocation 🔭 (planned)

There is a placeholder `RegisterAllocator` class, but **no optimization passes run today** -
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
shake_exports = true                           # opt-in: drop module-level functions/vars unreachable from main()
```

> The keys that affect output are exactly `mosaik8_build.APPLIED_KEYS`:
> `project.name`, `project.target_platforms`, `source.folder`,
> `assets.sprites`, `assets.font` (a glyph-grid PNG baked as the
> glyph-buffer console font, see §5.5 note 14), `lib.paths` (extra library
> roots joining `MOSAIK_LIB` and the bundled `lib/`), and under `[build]`:
>
> - `output_dir`; `rom_size` / `ram_size` (cartridge geometry on the Game
>   Boy family; both default to a plain 32 KB / no-RAM cart, and `rom_size`
>   grows automatically when `bank(N)` places code in higher banks, §2.7);
>   `shake_exports` (below); `code_banks` / `bank_bytecode` (§2.7).
> - Lynx / cc65: `lynx_stack_size` (the C stack in bytes, default 512),
>   `bkg_max_tiles` (the Lynx bkg engine's resident tile-table budget, default
>   256), `bkg_strip_w` (the row-strip engine's strip width in tiles),
>   `lynx_bkg16` (force the 4bpp background engine for a hand-written game),
>   `lynx_code_resident` (keep the VM8 blob in Lynx RAM instead of streaming
>   it), `sprite_max_tiles` / `sprite_max_slots` (the Suzy tile-table and
>   SCB slot budgets, default 40 each).
> - Sprites / input: `obj_8x16` (8x16 hardware sprite mode on the six
>   `OBJ16_CONSOLES`), `sms_start_button` (map SMS pad button 1 onto
>   `INPUT_START` as well as the PAUSE NMI).
> - VM8 runtime (each a `VM_*` define the fixed runtime in `lib/vm/` folds
>   on): `vm_quant` (instructions per thread per frame, default 16),
>   `actor_pool` / `trigger_pool` (per-room slot pools), `frame_lock` (hold a
>   game frame to N display frames), `park_updates` (kill the On Update of an
>   actor parked off screen), `actor_deactivate` (GB Studio's offscreen
>   deactivation, takes the actor out of the walked list), `actor_scan` /
>   `proj_scan` (re-test a parked actor / collide a shot 1-in-N frames),
>   `proj_under_lock` (shots keep flying under a cutscene lock), `move_lcd`
>   (pace a native auto-move by display frames; kept for parity, off).
>
> `shake_exports` (default false) enables declaration-level tree-shaking:
> module-level functions and vars unreachable from `main()` are dropped
> before codegen, so an exported-but-unused library surface stops costing
> ROM (and, on cc65's `--static-locals` link, BSS). Any reference keeps a
> declaration - a call, an address-taken callback, a `const` table entry, a
> switch case label - and type declarations are always kept. Off by default
> because shaking changes the generated C (default output stays
> byte-identical).
> Every other key is reported, not silently ignored: recognised-but-unapplied
> keys (`build.optimization_level`, `build.debug_symbols`,
> `platforms.<console>.features` / `.memory_layout`, the whole
> `[dependencies]` table; `NOT_YET_APPLIED_KEYS`), the `[project]` metadata
> (`version`, `author`, `description`) aside, and unknown keys/sections each
> print a `⚠️` warning at build time.
> `--debug` enables `lcc` debug flags.

#### Asset pipeline

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
transparent for sprites); any other PNG maps per pixel - transparent
(alpha < 128) or near-white → 0, then light → 1, dark → 2, black → 3 by
luminance. The converter (`mosaik_assets.py`) is dependency-free. The
`projects/shmup` project is the worked example. On the Lynx, the sprite engine's tile table
holds 32 tiles; the build warns when assets exceed that.

For programs that `import "graphics.palette"`, a ≤ 4-entry indexed PNG also
emits its authored palette as `const u16 <name>_palette[4]`, converted to the
console's native color format at build time (see §6 `graphics.palette`) -
`palette.load_sprite(0, sprites_palette)` then recolors the asset with its
authored colors on every console.

**Named-sprite sheets** - a sheet `foo.png` may carry a sidecar
`foo.sprites.toml`: an array of `[[sprite]]` tables, each a `name` plus a
`rect = [x, y, w, h]` (pixels). The pipeline
then cuts each named sub-sprite from its rect, concatenates their 8×8 tiles
(row-major, array order) into `<name>_tiles`, and emits per-sprite
`<sprite>_tile` / `<sprite>_w` / `<sprite>_h` defines (tile units) - exactly
the shape `sprite.set_meta(slot, knight_tile, knight_w, knight_h)` wants. The
generator script that authors the sheet + manifest is project-local (e.g.
an `assets/gen_sprites.py`).

**4bpp / 16-colour tier** - when the target's `PLATFORM_CAPS.sprite_bpp` is 4
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
Lynx uses the **cc65 backend** instead - a separate prelude and standard-library
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

- **TGI profile** (`lynx`) - pixel-addressed graphics; `graphics.draw` works.
- **conio profile** (`pce`) - character-cell text via `gotoxy`/`cputs`; no
  `graphics.draw` (the PC Engine is tile-based, no TGI driver). Sprites use
  the VDC hardware engine instead (see below).

Adding another cc65 console is a new `CC65_PROFILES` entry plus a `PLATFORM_*`
registration - no new code path. Stdlib portability tiers:

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
  vblank - tear-free. GB pixel value 0 maps to sprite-palette colour 0, which
  the VDC never draws (the GB transparency model); `FLIP_X`/`FLIP_Y` lower to
  the SATB X/Y-invert bits; coordinates are screen pixels like everywhere
  else. Sprites are an independent plane in front of the background, so -
  unlike the Lynx - **text and sprites mix freely** on the PC Engine.
  `bounce.mos`/`pong.mos` build and run on `pce` unchanged.
- **4bpp / 16-colour sprites (`sprite_bpp` tier, native on Lynx):** The
  asset pipeline encodes sprite tiles at the **target's native depth**
  (`PLATFORM_CAPS.sprite_bpp`): a >4-colour indexed PNG becomes packed-nibble
  **4bpp (16-colour)** on the Atari Lynx, and the Lynx sprite engine widens its
  literal rows to `BPP_4` and loads the asset's authored palette into the 16
  Mikey pens with `palette.load_sprite16(<name>_palette16)`. On the 2bpp
  consoles (Game Boy family / SMS / Game Gear / NES - and the PC Engine until
  its VDC 4bpp sprite path lands) the **same source** still builds: the PNG is
  luma-quantized to the 2bpp grey ramp and `load_sprite16` is a no-op
  (generalized-with-limits). Hand-authored 2bpp tiles and ≤4-colour assets are
  unaffected. *(PC Engine native 4bpp sprites: planned.)*
- **Metasprites (`sprite.set_meta`, every console):**
  `sprite.set_meta(base, tile, w, h)` declares a **W×H block of 8×8 tiles**
  moved/flipped/re-tiled as one logical sprite - it reserves the sprite slots
  `base..base+w*h-1` and assigns them tiles row-major from `tile`. `move`/
  `set_prop`/`set_tile` on the base then act on the whole block (flips reverse
  the cell layout *and* mirror each tile, for a true whole-sprite mirror). The
  GB family fans the block out across consecutive OAM objects (bounded by the
  `max_metasprite_tiles` capability / the 10-per-line budget); the Lynx and
  PCE - far more generous ceilings - composite it. The layer is emitted only
  when a program calls `set_meta`, so ordinary sprite programs are
  byte-identical. Worked example: `samples/metasprite.mos`.
- **Sparse metasprite frames (`sprite.set_meta_mask`, every console):**
  `sprite.set_meta_mask(base, tile, w, h, mask)` is `set_meta` with a per-COLUMN
  BLANK bitmap (bit *c* = column *c* draws nothing). A masked column's objects
  park off screen and consume **no tile**, so a frame with holes in it costs
  neither VRAM nor one of the GB's 10 sprites per scanline - a converted GB
  Studio title banner is ten 8×16 tiles with an 8 px gap, and dense that is
  eleven objects on one line. The sheet holds only the drawn columns, which is
  why every backend honours the mask even where there is no per-line limit.
  Emitted only when called, so a program of solid rectangles is byte-identical.
- **Sound (Tier-1, every console):** `sound.beep(freq, frames)`/`sound.stop()`
  - see §6 `platform.sound`. On cc65 consoles the tone comes from the Lynx
  Mikey / PC Engine PSG.
- **TGI-only 🔭→ `graphics.draw`:** `draw.clear`, `draw.set_color`,
  `draw.pixel`, `draw.line`, `draw.bar`, `draw.circle`, `draw.present`. Available
  on TGI-profile consoles (Lynx); guard with `if platform == "lynx"` (see
  `samples/lynx_draw.mos`).
- **Background tilemap `graphics.bkg`:** supported on the cc65 consoles
  too. The PC Engine has real tilemap hardware (the VDC BAT plus the BXR/BYR
  scroll registers; the 32×32 map is replicated across the BAT so the u8
  scroll wraps mod 256 exactly like the Game Boy). The Lynx has *no* tilemap
  layer, so its engine draws the map as a ring of screen-spanning literal Suzy
  row-strip sprites (one per visible row) - horizontal scroll is pure SCB
  position, vertical recomposites one strip per tile crossing (amortized over
  the frames the entering row is off-screen) - the SPRDEMO4 scrolling technique;
  scrolling costs ~16 hardware blits per frame. Sprites layer on
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
(`tgi_setframerate(60)` - not the Lynx-classic 75, so `video.wait_vblank` paces
programs at the same rate as the Game Boy and "60 frames ≈ 1 second" holds), and
starts **single-buffered** (draw page == view page) so immediate text persists
without flipping. `video.wait_vblank` waits out the current display frame (the
Lynx `clock()` ticks once per frame) until the sprite or background engine is
used, then switches to true double-buffering and a VBL-synced page flip. (Calling
the flip repeatedly on content that lives in only one buffer - e.g. text plus a
`wait_vblank` loop - alternates with a blank buffer and flickers; that is why
non-sprite drawing stays single-buffered.)

> Which stdlib calls exist on which console is recorded once in the
> `PLATFORM_CAPS` registry (mosaik/platforms.py); calling anything a console
> lacks - on *either* backend - is a clear compile-time error. The `REG_*`
> hardware-register constants are Game Boy addresses and only exist on the
> Game Boy family; referencing one elsewhere is likewise a compile error.

### 5.5 Cross-platform support matrix

**The language core is fully portable.** Every non-hardware feature - `var`/
`const`, structs, enums, all control flow (`if`/`loop`/`while`/`for`/`switch`),
operators, functions, modules, conditional compilation - compiles identically on
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
backend - GBDK consoles included - driven by `PLATFORM_CAPS`. See footnotes.

| Stdlib call | GB family | SMS/GG | NES | Lynx | PCE |
| --- | :-: | :-: | :-: | :-: | :-: |
| `video.enable_lcd` / `disable_lcd` / `wait_vblank` | ✅ | ✅ | ✅ | ✅ ⁵ | ✅ |
| `video.show_sprites` / `hide_sprites` / `show_background` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `video.show_window` / `hide_window` | ✅ | ❌ ¹ | ❌ ¹ | ❌ | ❌ |
| `video.set_overlay` (present-time UI overlay hook; a graceful no-op where ❌ - see §6) | ❌ | ❌ | ❌ | ✅ | ❌ |
| `input.pressed` / `held` / `raw` | ✅ | ✅ | ✅ | ✅ | ✅ |
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
| `text.set_font` / `set_font_at` | ✅ ⁶ | ✅ ⁶ | ✅ ⁶ | ❌ ⁶ | ❌ ⁶ |
| `text.glyph_buffer` (ROM font, rasterized on demand) | ✅ ¹⁴ | ✅ ¹⁴ | ✅ ¹⁴ | ❌ ¹⁴ | ❌ ¹⁴ |
| `text.fill_box` (overlay box) | ❌ | ❌ | ❌ | ✅ ¹² | ✅ ¹² |
| `text.to_window` / `to_bkg` / `window_active` (UI overlay) | ✅ ¹³ | ❌ ¹³ | ❌ ¹³ | ❌ ¹³ | ❌ ¹³ |
| `text.win_sprite_cut` (keep sprites off the overlay) | ✅ ¹³ | ❌ ¹³ | ❌ ¹³ | ❌ ¹³ | ❌ ¹³ |
| `text.win_overlay_cut` (stop the overlay at a scanline) | ✅ ¹³ | ❌ ¹³ | ❌ ¹³ | ❌ ¹³ | ❌ ¹³ |
| `text.plot_tile` (raw tile via the text router) | ✅ ¹³ | ✅ ¹³ | ✅ ¹³ | ❌ ¹³ | ❌ ¹³ |

Footnotes:

1. Only the Game Boy family has a hardware window layer; the
   `show_window`/`hide_window` and `graphics.window` helpers are a compile
   error elsewhere (GBDK's SMS/GG `SHOW_WIN` macros are no-ops and the NES
   port has none, so this is honest-off rather than silently-broken).
2. `hw.read`/`hw.write` work mechanically everywhere, but register addresses
   are console-specific. The `REG_*` constants are **Game Boy addresses** and
   only exist on the GB family; referencing one elsewhere is a compile error -
   gate raw hardware access with `if platform == "..."`.
3. Text coordinates are character cells on every target (`SCREEN_COLS` ×
   `SCREEN_ROWS`). GBDK/NES render via the tile font; the Lynx scales cells to
   TGI pixels; the PC Engine uses conio cells. (GBDK text needs a font loaded -
   handled automatically by the prelude.)
4. Lynx sprites use the **Suzy hardware blitter** (the GB 8×8 2bpp tile model
   is kept as the API; tiles convert to literal Lynx sprite data + one SCB per
   slot, drawn via `tgi_sprite`). A sprite program owns the frame, so don't mix
   it with immediate `graphics.text`. See §5.4.
5. On the Lynx `wait_vblank` only paces the frame until the sprite or
   background engine is used, then it becomes a VBL-synced double-buffer flip
   (see the display-model note in §5.4).
6. `text.set_font(data)` swaps the console font's 96 glyph tiles for a custom
   sheet on the tile-font consoles (GB family + SMS/Game Gear - glyphs are
   ordinary bkg tile data there); `text.set_font_at(base, data)` is the same
   swap at a caller-chosen tile base (the SMS/GG escape for a large tileset
   that overwrites the low console font; on SMS/GG `base + 96` must stay
   ≤ 192 - see `docs/vram-layout.md`). A font swap also switches SMS text
   from printf to tile plotting. Both are graceful no-ops on the NES
   (CHR font) and the cc65 Lynx/PCE (their own fonts).
7. One portable square-wave channel; the tone generator is whatever the
   console has (GB-family APU, SMS/GG PSG, NES APU, Lynx Mikey, PCE PSG). The
   beep duration counts down in `video.wait_vblank` - see §6 `platform.sound`.
8. PCE sprites use the **VDC hardware** (GB tiles become 16×16 4bpp VDC
   patterns, a SATB mirror is flushed on `wait_vblank` and DMA'd at vblank).
   Text and sprites mix freely on the PCE. See §5.4.
9. The same GB background model (256-tile table, 32×32 map, u8 scroll wrap
   mod 256) on very different hardware: the PCE uses its real tilemap (VDC
   BAT + BXR/BYR scroll); the Lynx - which has no tilemap layer - draws the
   map as a ring of screen-spanning Suzy row-strip sprites (one per visible
   row). On the Lynx a background program owns the frame like
   a sprite program does (don't mix with immediate `graphics.text`). See
   §5.4; worked example: `projects/background`.
10. `graphics.palette` exists on **every** console with graceful degradation
    instead of an error: on the 4-grey machines (DMG, Mega Duck) colors
    quantize to shades and apply through `BGP`/`OBP0`/`OBP1` - exactly what
    real GBC games do on a DMG - and consoles with fewer palette slots mask
    or ignore the extras (`sprite.set_palette` is an honest no-op on SMS/GG,
    whose sprites all share one palette). Slot 0 is the portable guarantee;
    the per-console slot counts live in `PLATFORM_CAPS`
    (`bkg_palettes`/`spr_palettes`). See §6 `graphics.palette`.
11. `bkg.set_palette` (per-tile background palette selection) requires
    per-tile palette hardware - the GBC attribute map, the PCE BAT bits, or
    the NES attribute table (16×16 px granularity: the rectangle is rounded
    outward) - recorded as `has_tile_palettes` in `PLATFORM_CAPS`. Elsewhere
    (DMG/Duck, SMS/GG, Lynx) it is a clear compile error.
12. `text.fill_box(c, r, w, h)` draws a filled, bordered box as a real
    framebuffer OVERLAY - cc65-only (the framebuffer consoles). On the **Lynx**
    it uses the TGI primitives (an opaque `tgi_bar` fill + a crisp 1px border of
    four `tgi_line`), drawn to both double-buffer pages so it survives the
    box-freeze flip: nicer and pixel-precise vs. the character-cell `+--+` frame,
    and opaque over a frozen scrolled background. On the **PCE** conio (no pixel
    primitives) it is a cleared cell box with an ASCII `+--+` border (a plain
    borderless clear was invisible on the PCE's black background). On the GBDK
    consoles (no framebuffer, tile-based text) it is a clear compile error -
    frame boxes there with tiles / `clear_area` (`engine.box`).
    `engine.box.draw_box` routes here on the Lynx (its TGI font has no `|`
    glyph, so the character frame garbled its verticals) and keeps the `+--+`
    glyph frame everywhere else.
13. The GB-family UI overlay (GB Studio's model): `text.to_window(origin_row,
    box_rows)` routes subsequent text onto the hardware WINDOW map (0x9C00),
    bottom-anchored via `WY`, so a dialogue/menu box never touches the
    scrolling scene tilemap; `to_bkg()` hides it, `window_active()` reports it.
    `text.plot_tile(x, y, tile)` plots ONE raw bkg tile id at a text cell
    through the same router - the seam a CUSTOM tile frame needs to compose
    with the overlay (a `bkg.set_tiles` frame lands UNDER the opaque window;
    GB Studio's `ui_draw_frame` writes the window map the same way). All four
    are graceful no-ops where marked ❌ (SMS/GG/NES keep their name-table
    text path; `plot_tile` is a real name-table write there - mind the SMS/GG
    0..191 tile-base cap - and a no-op on Lynx/PCE, where a custom frame is
    `text.fill_box`). `vm.core` wraps its dialogue/menu boxes in these, so
    every VM8 game gets the overlay for free on the GB family.
    `text.win_sprite_cut(on)` belongs to the same group: sprites draw ABOVE
    the window on GB hardware, so an actor standing low in the room shows
    THROUGH an open box. It hides them from the overlay's first scanline down
    (an LYC interrupt) and restores them each frame, honouring the program's
    own `video.show_sprites`/`hide_sprites` - GB Studio's `interrupts.c` model.
    `text.win_overlay_cut(y)` is the other half of that same ISR: at scanline
    `y` the WINDOW layer goes off and the sprites come back, so an overlay
    covers only the TOP of the screen with the room playing below it. GB
    Studio's `overlay_cut_scanline`, whose default 150 is off the bottom of a
    144-line screen - so any `y` from `SCREEN_HEIGHT` up DISARMS the cut, and a
    program that never calls it pays nothing. It honours
    `video.show_window`/`hide_window` the same way the sprite cut honours the
    sprite pair: a restore only ever gives back what the program asked for.
14. **GLYPH-BUFFER text** (`text.glyph_buffer(base, count)`): the font stays in
    ROM (the project's `[assets] font` when it has one, else GBDK's `font_ibm`
    as the console library LINKS it) and each character is rasterized ON
    DEMAND into the tile band
    `[base, base+count)`, instead of 96 glyph tiles occupying VRAM for the
    whole run. That is what lets a scene use nearly the whole background tile
    table - GB Studio's model (art 0..190, frame, glyphs 204..255) - and it
    retires `set_font_at` as the SMS/GG large-tileset escape. **Pass 0 for
    either argument to take the DERIVED band**: one past the highest bkg tile
    the program uploads, up to the top of the table, so a generated shell
    carries no tile numbers and a growing tileset moves the band on its own;
    no room left is a clear compile error naming the numbers. The cache is
    keyed by GLYPH (this font is fixed-width, so one tile is one character):
    repeated letters share a tile and a redraw writes no VRAM, so `count`
    bounds the DISTINCT characters on screen at once, not the cells. A custom
    font is the BUILD-TIME `mosaik.toml [assets] font` (a glyph-grid PNG baked
    into the ROM), not a runtime pointer. ❌ = a graceful no-op: the NES draws
    glyphs from CHR and Lynx/PCE from a framebuffer, so neither reserves tiles
    to reclaim.

> Portability rule of thumb: programs that stay within **text + input + timing +
> sound + sprites + background + palettes** (`graphics.text`, `platform.input`,
> `platform.system`, `platform.sound`, `graphics.sprite`, `graphics.bkg`,
> `graphics.palette`) are portable across **all nine consoles** - and size
> their world from `SCREEN_WIDTH`/`SCREEN_HEIGHT` instead of hardcoding
> 160×144. Add `graphics.window` and the program is Game Boy-family only; use
> `graphics.draw` and it is Lynx-only; `bkg.set_palette` needs per-tile
> palette hardware (GBC/Pocket, NES, PCE). Guard the non-portable parts with
> `if platform == "..."`.

### 5.6 Testing ROMs

Building a ROM only proves it *links*. To check that it actually runs, drive it
headlessly:

- **Game Boy family** (`.gb`/`.gbc`/`.pocket`) - [PyBoy](https://docs.pyboy.dk/),
  a Python Game Boy emulator that exposes the rendered screen and memory:

  ```python
  from pyboy import PyBoy
  pb = PyBoy("build/gameboy/game.gb", window="null")   # headless
  for _ in range(200): pb.tick()
  img  = pb.screen.image          # 160x144 PIL image
  tile = pb.memory[0x9800]        # BG tilemap; OAM at 0xFE00 (y, x, tile, attr)
  pb.button_press("up"); pb.tick(); pb.button_release("up")
  pb.stop()
  ```

  Assert via the screen, VRAM, OAM, WRAM (`0xC000-0xDFFF`), HRAM and the IO
  registers; all read live between ticks. (An APU register reads 0x00 unless
  PyBoy runs with `sound_emulated=True`, and NR13 / NR23 / NR33 are write-only.)
  (Note: scripted `button_press("right")` can fire spurious repeated
  edges - verify edge logic with DOWN/UP/LEFT or on the Lynx.)

- **Lynx / PCE / SMS / GG / NES** - the bundled libretro harness:

  ```bash
  python emu/libretro/run_lynx.py build/lynx/game.lnx 400 --png out.png
  python emu/libretro/run_lynx.py build/lynx/game.lnx 600 --press RIGHT@420-600 --png r.png
  python emu/libretro/run_lynx.py build/pce/game.pce  300 --core mednafen_pce_fast --png p.png
  #   --core genesis_plus_gx  (SMS .sms / Game Gear .gg)   --core fceumm  (NES .nes)
  ```

  It runs N frames, reports the final frame's distinct colours (exit 1 if blank),
  saves screenshots, and holds buttons over frame ranges. Handy boots homebrew
  BIOS-less but slowly - allow **≥300–400 frames** before reading the screen.
  (Mega Duck and Analogue Pocket have no core; verify those via their
  GB/GBC-equivalent build in PyBoy.)

The repo's own gates: `python tests/run_all.py` (unit tests), `--samples` (build
every sample × console + the projects), and `python tests/verify_roms.py`
(behavioural checks; `--lynx`/`--pce`/`--sms`/`--gg`/`--nes` add those cores).

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
function set_overlay(cb: function()) -- LYNX: register a PRESENT-time UI overlay
                           -- drawer, called inside gbs_present after the
                           -- bkg/sprite blits and before the flip (text
                           -- helpers forced single-page) -- so an open
                           -- dialogue/menu is part of every composed frame
                           -- and survives a recomposite (a moving background
                           -- otherwise scans out before post-present drawing
                           -- lands = the UI strobes). vm.core registers its
                           -- box/menu drawer here. A graceful no-op on every
                           -- persistent-tilemap console (GBDK families, PCE).
                           -- Emitted only when called (byte-identical off).
```

### platform.input
```mosaik
-- Constants map to GBDK joypad bits (J_A, J_B, ...).
const INPUT_A; INPUT_B; INPUT_SELECT; INPUT_START
const INPUT_RIGHT; INPUT_LEFT; INPUT_UP; INPUT_DOWN
function pressed(button: u8) -> bool   -- joypad() & button
function held(button: u8) -> bool      -- (currently identical to pressed)
function raw() -> u8                   -- the whole pad in ONE read, the console's
                                       -- own bit layout: mask it with INPUT_*
```

`raw()` is what a per-frame pad latch wants (`engine.pad.update` reads it once
and masks eight buttons out of it); eight `held` calls are eight hardware reads.
It carries the same console mapping as `pressed` (the SMS START synthesis, the
Lynx vertical fix), and a program that never calls it emits no helper.

⚠️ **Not every console has all eight buttons.** The read is masked to the bits
the target's own `joypad()` can produce, so asking for one it has not got
answers NO rather than a phantom press:

| Console | Missing | Note |
|---|---|---|
| SMS | `INPUT_SELECT`, and `INPUT_START` as a pad bit | Its Start is the console's **PAUSE** button, an NMI. The prelude claims `NMI_ISR` and presents the press as `INPUT_START` for a few display frames, so Start works. `[build] sms_start_button` (default off) additionally maps pad **button 1** onto it - the pad labels it "1 START" - at the cost that button 1 and Start become one bit, so a program bound to both fires twice on one press. |
| Game Gear | `INPUT_SELECT` | Start is real (port `$00` bit 7). |

⚠️ **`pressed` is LEVEL-triggered - it is identical to `held`** (both return the
current pad state; there is no rising-edge detection). For "one press = one
action" (menus, dialogue paging, toggles) track the previous frame yourself:
`var prev; edge = held(BTN) and not prev; prev = held(BTN)`. A real edge-`pressed`
is 🔭.

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
function peek(address: addr) -> u8         -- *(const uint8_t *)address, INLINE (no
                                           -- helper call, not volatile): a plain
                                           -- data read the compiler may schedule
```

`peek` is for DATA (a ROM blob, a RAM table) reached by address - the VM8
interpreter's fetch fast path reads its bytecode this way (see `assets.address`).
`read` stays the volatile access a hardware register needs.

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

### platform.save
```mosaik
-- Battery-backed persistent storage (Stage 0: the Game Boy family only, via
-- MBC5 cart SRAM at 0xA000). enable()/disable() map/unmap the RAM window --
-- keep it disabled except around a block of accesses (battery-safety hygiene).
-- write_u8/read_u8 do per-byte access at `off` (0..capacity-1); a u16 value is
-- two calls (lo/hi) at the call site. The MBC5+RAM+BATTERY cart header is
-- selected by `[build] ram_size` (see §Build system), so an emulator persists a
-- .sav. HONEST-OFF: has_save is True only on the GB family; save.* on any other
-- console (SMS/GG/NES/Lynx/PCE/Mega Duck) is a clear "not supported on target"
-- error -- guard it with `if platform == "gameboy" or ...` (SMS/GG cart SRAM +
-- Lynx EEPROM are staged follow-ups). Emitted only when imported.
function enable()                        -- gbs_save_enable  (ENABLE_RAM; SWITCH_RAM(0))
function disable()                       -- gbs_save_disable (DISABLE_RAM)
function write_u8(off: u16, v: u8)       -- gbs_save_write   (0xA000[off] = v)
function read_u8(off: u16) -> u8         -- gbs_save_read    (return 0xA000[off])
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
-- One palette per 8x8 CELL of a metasprite (row-major, `w`/`h` in the same
-- 8x8-tile units set_meta takes; `off` is the start of this sprite's row in a
-- shared table). Where set_palette colours a whole actor, this colours it cell
-- by cell -- a character whose hair, face and body take three palettes. Real
-- wherever sprites have a per-sprite palette select, an honest no-op on SMS/GG.
function set_meta_palettes(base: u8, w: u8, h: u8, data: addr, off: u16)
-- set_meta with a per-COLUMN BLANK mask (bit c = column c draws nothing): its
-- objects park and it takes NO tile from the frame's block, so a SPARSE frame
-- costs neither VRAM nor a hardware sprite on the scanline. An unmasked
-- set_meta clears a stale mask. Emitted only when called.
function set_meta_mask(base: u8, tile: u8, w: u8, h: u8, mask: u16)
-- The CONSOLE font's glyph for character `ch` (ASCII 32..127; anything else
-- is a space) as sprite tile `tile`, colour 3 on transparent 0, read from the
-- font the toolchain LINKS: GBDK's font_ibm on the GBDK consoles, cc65's
-- pce_font on the PC Engine. Refused on the Lynx (no console font is linked
-- there). The overlay HUD's sprite text uses it. Emitted only when called.
function font_glyph(tile: u8, ch: u8)         -- gbs_sprite_font_glyph
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
-- The ATTRIBUTE mirror of set_tiles: one background palette-slot byte per map
-- cell, uploaded as a rectangle. Real on the per-tile-palette consoles (on the
-- NES at its own 16x16 px granularity), a graceful NO-OP elsewhere -- SMS/GG,
-- the Lynx and the PCE reach 16 colours through the 4bpp background tier
-- instead (§5.4), and a 4-grey console has one palette. That no-op is what lets
-- one target-neutral room painter call it with no `if platform` fork.
function set_attrs(x: u8, y: u8, w: u8, h: u8, data: addr)  -- gbs_bkg_attrs
```
The comments give the GBDK lowering; on the cc65 consoles the same calls go to
the `gbs_set_bkg_*`/`gbs_move_bkg` engine helpers (PCE: VDC BAT + BXR/BYR
scroll; Lynx: the Suzy row-strip background engine - see §5.4/§5.5).
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
function set_font(font_data: addr)             -- swap the console font's 96 glyph
                                               -- tiles (GB family + SMS/GG; no-op
                                               -- on NES/Lynx/PCE)
function set_font_at(base: u8, font_data: addr) -- the swap at a caller-chosen tile
                                               -- base (SMS/GG large-tileset escape;
                                               -- base + 96 <= 192 there -- see
                                               -- docs/vram-layout.md)
function glyph_buffer(base: u8, count: u8)     -- GLYPH-BUFFER text: keep NO font in
                                               -- VRAM, rasterizing each character on
                                               -- demand into tiles [base, base+count)
                                               -- so a scene may use nearly the whole
                                               -- tile table (GB Studio's model).
                                               -- (0, 0) = the DERIVED band, above
                                               -- everything the program uploads.
                                               -- `count` bounds the DISTINCT
                                               -- characters on screen (repeats share
                                               -- a tile). GB family + SMS/GG; a
                                               -- no-op on NES/Lynx/PCE. A custom
                                               -- font is `[assets] font`, baked at
                                               -- build time.
function fill_box(c: u8, r: u8, w: u8, h: u8)  -- a filled, bordered box as a real
                                               -- framebuffer OVERLAY (cc65 only):
                                               -- an opaque fill + a crisp 1px pixel
                                               -- border via the TGI primitives on
                                               -- the Lynx (nicer + pixel-precise vs.
                                               -- an ASCII cell frame), a cleared
                                               -- cell box with an ASCII +--+ border
                                               -- on the PCE conio. A clear "not
                                               -- supported" error on the GBDK
                                               -- consoles (tile-based, no
                                               -- framebuffer -- frame boxes there
                                               -- with tiles / clear_area).
function to_window(origin_row: u8, box_rows: u8) -- route subsequent text onto the GB
                                               -- WINDOW overlay, bottom-anchored (GB
                                               -- Studio's UI model): screen rows
                                               -- [origin_row..origin_row+box_rows)
                                               -- relocate to the bottom box_rows
                                               -- rows. GB family only; a graceful
                                               -- no-op everywhere else.
function to_bkg()                              -- hide the overlay + text back to the
                                               -- bkg map (no-op off the GB family)
function window_active() -> u8                 -- 1 while text rides the overlay (the
                                               -- caller can skip its bkg clear /
                                               -- room repaint on close)
function win_sprite_cut(on: u8)                -- keep SPRITES off the overlay: OBJ
                                               -- draws above the window on GB
                                               -- hardware, so an actor low in the
                                               -- room shows through an open box.
                                               -- Hides them from the overlay's first
                                               -- scanline down (an LYC interrupt),
                                               -- restoring them each frame. GB
                                               -- family only; a no-op elsewhere.
function win_overlay_cut(y: u8)                -- STOP the overlay at scanline y: the
                                               -- window layer goes off there and the
                                               -- sprites come back, so an overlay
                                               -- covers only the TOP of the screen
                                               -- with the room playing below it. GB
                                               -- Studio's overlay_cut_scanline; its
                                               -- default 150 is off the bottom of a
                                               -- 144-line screen, so any y from
                                               -- SCREEN_HEIGHT up DISARMS the cut.
                                               -- GB family only; a no-op elsewhere.
function plot_tile(x: u8, y: u8, tile: u8)     -- ONE raw bkg tile id at a text cell,
                                               -- THROUGH the text router: follows
                                               -- to_window onto the window map (how
                                               -- GB Studio draws its 9-slice frame);
                                               -- a name-table write on SMS/GG (SAT
                                               -- row clamp; bkg tiles are 0..191
                                               -- there) and NES; a graceful no-op on
                                               -- Lynx/PCE (frame via fill_box).
```

### graphics.palette

The portable color model: a **palette slot holds 4 colors**, matching the GB
2bpp tiles every console shares, with the GB transparency rules kept (sprite
color 0 is transparent everywhere; bkg color 0 is a real color - the
paper/backdrop). A color is an opaque `u16` made by `palette.rgb`, which
quantizes RGB888 to the console's native format: RGB555 on the Game Boy
Color/Analogue Pocket, RGB222 on the SMS, RGB444 on the Game Gear, the
nearest NES master-palette entry, 12-bit Mikey pens on the Lynx, 9-bit VCE
words on the PC Engine - and a plain DMG shade 0–3 on the 4-grey consoles
(Game Boy, Mega Duck), where `set_bkg`/`set_sprite` pack `BGP`/`OBP0`/`OBP1`.
So one colored source builds and stays meaningful on **all nine consoles**.

```mosaik
function rgb(r: u8, g: u8, b: u8) -> u16    -- quantize RGB888 to a native color
function set_bkg(slot: u8, c0: u16, c1: u16, c2: u16, c3: u16)
function set_sprite(slot: u8, c0: u16, c1: u16, c2: u16, c3: u16)
function load_bkg(slot: u8, colors: addr)    -- e.g. an asset's <name>_palette
function load_sprite(slot: u8, colors: addr)
-- Load COUNT consecutive slots out of ONE table, starting at WORD index `off`
-- -- a whole scene's palette SET in one call, indexable per room without
-- pointer arithmetic (which mosaik does not have). Real where the
-- multi-palette model is (`has_tile_palettes`: GBC/Pocket, NES, PCE); an
-- honest no-op on a console with one palette per layer, whose fixed palette is
-- what its art was quantized for.
-- NOTE the colour ENCODING: unlike load_bkg/load_sprite (native words), these
-- take PORTABLE 5-5-5 RGB words, like `load_bkg16` and for the same reason --
-- a per-scene palette table is GENERATED data in a target-neutral module,
-- which cannot bake per-console words, so the rounding happens at run time.
function load_bkg_set(slot: u8, count: u8, colors: addr, off: u16)
function load_sprite_set(slot: u8, count: u8, colors: addr, off: u16)
-- Darken every LOADED palette towards black: 0 normal .. 3 black, each channel
-- scaled by (3 - level)/3. The COLOUR fade, and the counterpart of the DMG
-- BGP/OBP ramp -- which the hardware IGNORES in CGB mode, and which the SMS and
-- Game Gear do not have at all, so on those three consoles this is the only way
-- to fade. Real on the CGB class (scaling the loaded palettes) and on SMS/GG
-- (scaling CRAM); an honest no-op elsewhere -- the DMG registers are real on
-- GB/Mega Duck, and the Lynx fades natively through `native.lynx`. An entry the
-- program never loaded is never touched. BIT 7 of `level` asks for the ramp
-- towards WHITE instead (each channel scaled towards full: GB Studio's
-- `fade_style` 0, its default) - honoured only on a build that states the
-- VM_FADE_STYLE define (a VM8 game with a `[scenes] fade_style` or a script
-- writing the `fade_style` state; `vm.fx` is the caller); elsewhere the bit
-- reads as "above 3" and clamps to black, so a program that never asks for a
-- direction is byte-identical.
function fade(level: u8)
```

- **Slot 0 is the portable guarantee.** The per-console slot counts are
  `PLATFORM_CAPS` (`bkg_palettes` / `spr_palettes`): GBC/Pocket 8+8, NES and
  PCE 4+4, Lynx 1 bkg + 4 sprite (the 16 Mikey pens partition exactly into
  pen 0 = backdrop/transparent, 4×3 sprite pens, 3 bkg pens), SMS/GG 1+1
  (CRAM), DMG/Duck 1 bkg + 2 sprite (`OBP0`/`OBP1`). Out-of-range slots are
  masked or ignored at run time.
- **Text follows bkg slot 0** - text lives in the background layer on every
  console - with paper = bkg color 0 and ink = bkg color 3 (GBDK font tiles,
  the Lynx pen partition, and the PCE text palette all reproduce this).
- On the **NES**, color 0 of bkg slot 0 is the shared PPU backdrop; on the
  **PCE**, bkg color 0 is the global backdrop (VCE `$000`) - both match the
  GB intuition that bkg color 0 is "the screen color".
- Per-sprite slot selection is `sprite.set_palette(id, slot)`; per-tile
  background selection is `bkg.set_palette(x, y, w, h, slot)` (one slot over a
  rectangle) or `bkg.set_attrs(x, y, w, h, data)` (a rectangle OF slots) on
  `has_tile_palettes` consoles (see §5.5 footnotes 10–11).
- **`set_prop` and `set_palette` own different OAM bits.** `sprite.set_prop`
  writes the flip/priority flags and PRESERVES the palette a
  `sprite.set_palette` / `set_meta_palettes` set; those write the palette and
  preserve the flip. Without that split a facing flip reset every coloured
  sprite to palette 0. The preserved bits are read back PER SLOT, so a
  metasprite coloured cell by cell keeps each cell's own palette; `set_palette`
  applies one palette to the whole fan, `set_meta_palettes` one per cell.
- **Asset palettes:** an indexed PNG with ≤ 4 entries (the kind whose indices
  map literally to GB colour values, §5.1) also emits its authored palette as
  `const u16 <name>_palette[4]`, converted to the native format at build
  time - `palette.load_sprite(0, player_palette)` recolors the asset with
  its authored colors on every console.
- Programs that never `import "graphics.palette"` emit no palette code and
  keep today's grey-ramp defaults byte-for-byte. Each verb beyond `rgb` and the
  two slot setters is likewise emitted only when it is CALLED (a prelude helper
  is resident and cannot bank).
- **The guide to colouring a whole game** - the portable model, the per-console
  table, per-tile background palettes and per-cell sprite palettes - is
  `docs/game-framework.md`, section "Colour". This section is the normative
  call surface.
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
pipeline), `projects/background` (scrolling tilemap) and `projects/colorlab`
(palettes).

## 8. Roadmap

### Done
- Lexer, parser, best-effort type checker, shared C code generator with two backends.
- Structs, enums, modules, functions, control flow, expressions (see §2).
- `while` / `break` / `continue` and `switch` / `case` / `default`.
- Bitwise operators `&` `|` `^` `<<` `>>` (Rust-style precedence, see §2.3).
- Hex (`0xE4`) and binary (`0b1010`) integer literals.
- Array `const` data emitted as real C `const` tables.
- **GBDK backend**: stdlib mapped to GBDK helpers - video (sprite/window visibility),
  input, hardware, system (delay/random), sprite, background, window, text.
  Targets: Game Boy / Color, Analogue Pocket, Mega Duck, SMS, Game Gear, NES.
- **cc65 backend**: data-driven profiles for Atari Lynx (TGI) and PC Engine (conio).
  Tier-1 portable stdlib (`platform.*`, `graphics.text`) on both; Suzy hardware sprites
  (`graphics.sprite`) and TGI draw primitives (`graphics.draw`) on the Lynx.
- Uniform capability gating via `PLATFORM_CAPS` registry - clear compile-time errors on
  every backend when a console lacks a stdlib call.
- Two-mode build system (single file / project), `clean`, `init`; GBDK-2020 and cc65
  autodetection.
- Unit tests in `tests/` plus end-to-end sample builds (~90 ROM matrix via `--samples`).
- True per-platform conditional compilation (evaluate `platform == ...`).
- Cross-file module linking (whole-program compilation of all sources into one C
  translation unit, export-list enforcement, single-file import closure - see §3.1),
  with module-level tree-shaking (unreferenced modules pruned from the ROM).
- Compiler organised as the `mosaik/` package (lexer / ast / platforms / parser /
  typechecker / codegen split by backend); golden snapshot tests guard the codegen.
- ROM banking: `bank(N)` function placement → MBC5 carts up to 8 MB on the Game Boy
  family, the Sega mapper on SMS/Game Gear and UNROM on the NES, with
  `rom_size`/`ram_size` cartridge geometry in `mosaik.toml` (see §2.7);
  config honesty warnings for unapplied/unknown keys.
- Banked `const` data: streamed arrays (`platform.assets`) packed into 16 KB data
  banks on the banking consoles and cart-archived on the Lynx; `[build]
  code_banks` (cold-code banking with stub splits and seam pinning, bank-local
  consts, sprite sheets in data banks) and `[build] bank_bytecode` (§2.7).
- Color & palettes: `graphics.palette` - 4-color GB-model palette slots on all
  nine consoles (RGB888 quantized per console; greyscale degradation on
  DMG/Mega Duck), `sprite.set_palette`, per-tile `bkg.set_palette` on
  GBC/Pocket/NES/PCE, asset-pipeline `<name>_palette` emission (see §6;
  sample: `samples/colors.mos`).
- First-class function pointers (callbacks): a `function(T...) -> R` type that
  holds a function value, stored in `var`/`const`, passed as a parameter, kept
  in a struct field or `array`, and called through (`cb(x)`, `table[i](x)`). A
  bare function name (`handler`) or a qualified one (`mod.handler`) used in a
  value position *is* the pointer - no `&` operator. Portability rule: a
  callback may only target a **home-bank** function; referencing a `bank(N)`
  function is a compile error on every banking console (GB family, SMS/GG,
  NES; sdcc's banked far-call is generated at the call site, so a raw address
  cannot reach a switched-out ROM bank), and unrestricted where `bank()` is
  ignored (the Mega Duck; the Lynx / PCE without `code_banks`). No captured environment -
  a handler reads game state through shared module-level `var`s. Built on this:
  the `engine.anim` framework module (callback-driven sprite animation).

### Planned 🔭
- Inline `asm { ... }`, memory `region` blocks, `stack var`, pointers.
- Array bounds checking and stricter type enforcement.
- Optimization passes / register allocation beyond what SDCC provides.
- Expanded standard library (audio, timer, backgrounds, math, collections, strings).
- Array iteration in `for`, more compound assignments (`%=`/`*=`/`/=`).
- `bank(auto)` placement via bankpack; banking on the Mega Duck (its mapper is
  unverified) and the cc65 consoles.

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
