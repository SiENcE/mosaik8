# CLAUDE.md

Guidance for working in this repository.

## What this is

**MosaiK8** is a framework for retro console development built around **mosaik**,
a high-level language. The mosaik compiler has **two C backends** (not assembly,
despite some stale docstrings):

- **GBDK backend** (default) — emits GBDK C, linked by the GBDK-2020
  toolchain (`lcc`/`sdcc`) into a `.gb`/`.gbc`/`.sms`/… ROM.
- **cc65 backend** — emits cc65 C for the Atari Lynx (`.lnx`) and PC Engine
  (`.pce`), linked by cc65's `cl65`. See `docs/cc65-backend-plan.md`.

The lexer/parser/typechecker and all logic codegen are shared; only the prelude
and stdlib lowering differ per backend. **`PLATFORM_CAPS`
(mosaik_compiler.py) is the per-console capability registry and single source
of truth**: each console's `framework` (from which `PLATFORM_FRAMEWORK` is
derived) plus `has_sprites`/`has_bkg`/`has_window`/`has_draw`/`has_gb_regs`. It
drives stdlib-call availability and the "not supported on target" diagnostics
on *both* backends, which prelude blocks are emitted, and the sample-build
matrix. mosaik8.py's `PLATFORM_TARGETS` (build flags per console) must list
the same consoles — asserted at import.

- `.mos` = mosaik source · `.c` = generated intermediate · `.gb`/`.gbc`/`.lnx`/… = ROM output.
- Naming: the framework/build tool is **MosaiK8**, the language is **mosaik**.
  The `gbs_`/`GBS_` prefix on generated-C helpers (`gbs_present`,
  `gbs_move_sprite`, `GBS_MAX_TILES`, …) is the internal C ABI and was kept
  through the rename — don't churn it.

## Layout

- `mosaik_compiler.py` — the compiler: `Lexer` → `Parser` (hand-written
  recursive descent → AST) → `TypeChecker` → `CodeGenerator` (emits GBDK *or*
  cc65 C). `MosaikCompiler.compile(source, platform=...) -> C string` is the
  entry point. `CodeGenerator` picks the backend in `generate()`:
  `_emit_prelude_{gbdk,cc65}` + the stdlib lowering map (`STDLIB_CALLS_GBDK`, or
  for cc65 `STDLIB_CALLS_CC65_CORE` plus `_DRAW`/`_SPRITE` per the console
  profile) selected into `self.stdlib_calls`. Built-in stdlib modules are the
  `STDLIB_*` string constants near the bottom (not compiled).
- `mosaik8.py` — the build tool / CLI: locates GBDK (`GBDKInterface`) and cc65
  (`Cc65Interface`), reads `mosaik.toml` (`BuildConfig`), compiles sources and
  invokes `lcc` or `cl65` (`MosaikBuilder.link_rom` dispatches on the target's
  `framework`). CLI commands: `build`, `clean`, `init`, `version`. (There is no
  `run` command — verify ROMs by driving an emulator directly; see below.)
- `mosaik_assets.py` — the asset pipeline: a dependency-free PNG reader (all
  scanline filters, grey/RGB/RGBA/indexed, bit depths 1/2/4/8, no interlace) +
  GB 2bpp encoder, plus PNG *writers* used by asset-generator scripts and
  tests. See "Asset pipeline" under behavior below.
- `setup_tools.py` — downloader/installer for everything external (all
  gitignored): GBDK-2020 (latest GitHub release) → `gbdk/`, the cc65 Windows
  snapshot (SourceForge) → `cc65/`, the Handy + Beetle Lynx libretro cores
  (libretro buildbot) → `emu/libretro/`, and the pip packages (`pyboy`,
  `libretro.py`, `pillow`, `toml`). `--check` reports, `--only a,b` selects,
  `--force` reinstalls. `lynxboot.img` is copyrighted and must be supplied by
  hand (Handy works without it).
- `docs/mosaik_lang_spec.md` — language spec; uses ✅ implemented / 🔭 planned
  tags. The source of truth for what the language actually does (§5.5 has the
  per-console stdlib support matrix).
- `Readme.md` — user-facing docs. `docs/` — design docs (cc65 backend plan,
  platform-support plan).
- `samples/*.mos` — runnable example programs (`hello.mos` is portable
  across both backends; `draw.mos` shows the Lynx-only `draw.*`).
  `projects/game` — a full project example (Box Runner). `projects/shmup` —
  the asset-pipeline showcase project (Starfall, a vertical shoot'em up; one
  source for gameboy/gamegear/lynx, tiles pregenerated from
  `assets/sprites.png`, which `assets/gen_sprites.py` regenerates).
- `tests/` — unit tests + `run_all.py` test runner.
- `gbdk/` — GBDK-2020, `cc65/` — cc65, `emu/libretro/` — emulator harness
  (`run_lynx.py` is tracked; the core DLLs and `lynxboot.img` are not). The
  toolchains and DLLs are gitignored and installed by `setup_tools.py`.

## Commands

```bash
# One-shot setup on a fresh clone (toolchains + emulators, all gitignored)
python setup_tools.py              # or --check / --only gbdk,cc65,cores,python

# Build a single file (output -> build/ next to the source)
python mosaik8.py build samples/bounce.mos
python mosaik8.py build --platform gameboy samples/pong.mos

# Build a project (reads mosaik.toml; dir or ./mosaik.toml also work)
python mosaik8.py build projects/game
python mosaik8.py build projects/shmup   # asset-pipeline project (PNG -> tiles)

# Tests
python tests/run_all.py            # unit tests
python tests/run_all.py --samples  # also build every sample x every console it
                                   # supports (derived from PLATFORM_CAPS, ~90
                                   # ROMs) + the projects/game and
                                   # projects/shmup projects
python tests/verify_roms.py        # behavioral checks (PyBoy GB asserts;
                                   # --lynx screen-diffs the shmup via the
                                   # libretro harness)
python emu/libretro/run_lynx.py samples/build/lynx/pong.lnx 300 --png out.png
                                   # headless Lynx run (see "Testing ROMs")
```

There are exactly **two build modes** (single-file vs project) — no "scan the
tree" mode. GBDK is found via `GBDK_HOME`, then a `gbdk-2020/`/`gbdk/` folder next
to the tool (what `setup_tools.py` installs), then `PATH`. cc65 is found via
`CC65_HOME`, then a `cc65/` next to the tool, then `cl65` on `PATH`.

```bash
# Build the same source for both backends (GBDK + cc65/Lynx)
python mosaik8.py build --platform gameboy samples/hello.mos
python mosaik8.py build --platform lynx    samples/hello.mos
```

## Testing ROMs (PyBoy for GBDK, libretro for the Lynx)

Building a ROM only proves it *links*. To check it actually runs:

- **GBDK consoles** — drive the `.gb` headlessly with
  [PyBoy](https://docs.pyboy.dk/) (a CLI emulator *and* a Python module), which
  lets you inspect the rendered screen and memory (example below).
- **Lynx** — headless via [libretro.py](https://pypi.org/project/libretro.py/)
  + the Handy core: `python emu/libretro/run_lynx.py
  samples/build/lynx/<rom>.lnx [frames] [--png out.png] [--press RIGHT@80-280]`.
  It runs N emulated frames (~75 frames of boot before `main` draws), reports
  the final frame's distinct colors (exit 1 if blank), saves screenshots, and
  can hold buttons over frame ranges — so screen-diff checks (did the sprite
  move?) work like the PyBoy ones. The harness prefers the Beetle Lynx core
  (`mednafen_lynx_libretro.dll`) when `emu/libretro/lynxboot.img` is present
  (gitignored; Beetle silently renders a blank screen without it) and falls
  back to `handy_libretro.dll`, which boots homebrew BIOS-less. It also
  monkeypatches a libretro.py NULL-frame bug — keep that if you rewrite it.
  (A standalone Mednafen bundle was removed — Mednafen is GUI-only with no
  scripting API; the libretro cores replaced it.)

PyBoy example (GBDK):

```python
from pyboy import PyBoy
pb = PyBoy("samples/build/gameboy/pong.gb", window="null")  # headless
for _ in range(200):
    pb.tick()
# Inspect what rendered:
img = pb.screen.image                 # PIL image of the 160x144 screen
pb.button_press("up"); pb.tick(); pb.button_release("up")
tile = pb.memory[0x9800]              # BG tilemap; OAM is at 0xFE00 (y,x,tile,attr)
pb.stop()
```

Useful checks: count distinct gray levels in `pb.screen.image` (a blank screen =
nothing drawn), read the BG tilemap at `0x9800` for text, and read OAM at
`0xFE00` (4 bytes/sprite: y, x, tile, attr) to confirm sprites are positioned and
moving. Note `gotoxy`/text coords are **character cells** (20x18), not pixels.
mosaik's `sprite.move` takes **screen-pixel** coords, but what PyBoy reads at
`0xFE00` is raw OAM — on GB, OAM = screen pixel + (8,16). `tests/verify_roms.py`
scripts these checks for the sprite samples.

## Behavior to keep in mind

- **Type checking is best-effort and non-blocking**: if it raises, the compiler
  prints a warning and still generates code. Don't make it block builds.
- **Each `.mos` is its own translation unit**; there is no cross-module linking.
  Calls like `module.func(...)` lower to `module_func(...)` unless they match a
  known stdlib call (the active `self.stdlib_calls` map; a call known to *some*
  backend but not this console errors via `ALL_STDLIB_CALLS`).
- **Conditional compilation is evaluated against the build target**: a
  module-level `if platform == "..." { } else if ... { } else { }` keeps only the
  matching branch (all branches are still parsed). Platform is threaded through
  `MosaikCompiler.compile(source, platform=...)` → `Parser(platform=...)`;
  `Parser._eval_platform_cond` resolves `==`/`!=`/`and`/`or`/`not` over `platform`
  and string literals (canonicalised via `PLATFORM_ALIASES`). Unresolvable
  conditions fall back to the `then` branch. Keep `} else` on one line.
- **Multi-console targets**: `--platform` / `target_platforms` selects the
  console via `PLATFORM_TARGETS` (mosaik8.py). GBDK consoles — `gameboy`,
  `gameboy_color`, `analogue_pocket`, `megaduck`, `sms`, `gamegear`, `nes` —
  carry `lcc` port flags; the GBDK prelude includes `<gbdk/platform.h>`
  (cross-platform). The cc65 consoles `lynx` and `pce` carry a `cc65_target`
  instead and are linked by `cl65 -t <target>`. Adding another console is a
  `PLATFORM_CAPS` row (capabilities + framework; `PLATFORM_FRAMEWORK` is
  derived), a `PLATFORM_TARGETS` row, an alias entry, and — for cc65 — a
  `CC65_PROFILES` entry; no new code path.
- **Capability gating (PLATFORM_CAPS) is uniform across backends**: calling a
  stdlib function a console lacks is a clear `_gen_call` compile error on GBDK
  consoles too (e.g. `window.*` on `nes`/`sms`/`gamegear` — only the GB family
  has a window layer), selected by subtracting `CALLS_NEEDING_{WINDOW,BKG,
  SPRITES}` from the active stdlib map. The GB-only `REG_*` register constants
  are prelude `#define`s only on `has_gb_regs` consoles; referencing one
  elsewhere raises a "Game Boy-specific" error in `gen_expression`.
- **Portability contract**: every prelude `#define`s `SCREEN_WIDTH`/
  `SCREEN_HEIGHT` (pixels) and `SCREEN_COLS`/`SCREEN_ROWS` (text cells) — GBDK
  lowers them to the `DEVICE_SCREEN_*` macros, cc65 takes them from the
  profile. `sprite.move(id, x, y)` takes **screen-pixel coords** on every
  console: the GBDK `gbs_move_sprite` wrapper adds
  `DEVICE_SPRITE_PX_OFFSET_X/Y`, the Lynx engine uses them directly; hide a
  sprite with `y = SCREEN_HEIGHT`. Samples size their world from `SCREEN_*`
  (bounce/pong use a 4px wall inset ≥ top speed so clamped `u8` positions
  can't wrap). `novascape.mos` keeps its logic in GB OAM space via a local
  `moveOAM()` shim (`sprite.move(id, x-8, y-16)` — u8 wrap makes it byte-exact,
  including hide-at-(0,0)).
- **Asset pipeline (PNG → tiles)**: `[assets] sprites = ["assets/foo.png"]` in
  `mosaik.toml` (paths relative to the project file) or repeatable
  `--asset foo.png` flags (single-file mode). `mosaik_assets.py` converts each
  PNG to **GB 2bpp** — the universal interchange format: GBDK's
  `set_sprite_data` takes it natively on the GB family, converts to CHR on
  NES, and expands 2bpp→4bpp via the compat layer (`set_tile_2bpp_data`) on
  SMS/GG; the Lynx engine converts it to Suzy literals. So conversion happens
  once per build, not per console, and no `png2asset` call is needed.
  `MosaikCompiler.compile(source, platform=..., assets=[(name, bytes)])`
  injects `const uint8_t <name>_tiles[]` + `#define <name>_tile_count N` into
  the generated TU (name = sanitized filename stem; clashes are an
  `AssetError`). Mapping: a ≤4-entry-palette indexed PNG uses indices as
  literal GB colour values (note: PIL pads palettes to 256 entries, so
  PIL-saved indexed PNGs fall through to the luma path — `mosaik_assets`'
  own `write_png_indexed` keeps them small); otherwise alpha<128 or
  near-white → 0, luma → 1..3. Images must be multiples of 8 px, cut into
  tiles row-major. Codegen warns when assets exceed the Lynx engine's
  `GBS_MAX_TILES` (`CodeGenerator.CC65_MAX_TILES`, 32). Tests:
  `tests/asset_pipeline_test.py`; worked example: `projects/shmup`.
- **cc65 stdlib / profiles**: the cc65 backend is data-driven across consoles
  via `CodeGenerator.CC65_PROFILES` — each profile gives the headers, text
  backend (`'tgi'` = pixel coords via `tgi_outtextxy`, e.g. Lynx; `'conio'` =
  character cells via `gotoxy`/`cputs`, e.g. PC Engine), driver init/teardown,
  and present call. Portable Tier-1 (`platform.video`/`input`/`system`/
  `hardware`, `graphics.text`) lowers via `STDLIB_CALLS_CC65_CORE` to `gbs_`
  helpers whose *bodies* vary per profile but whose *names* don't.
  `graphics.draw` (`STDLIB_CALLS_CC65_DRAW`) is added only for `has_draw`
  consoles; `graphics.sprite` + sprite-visibility toggles
  (`STDLIB_CALLS_CC65_SPRITE`) only for `has_sprites` consoles (both flags live
  in `PLATFORM_CAPS`, not the profile). Sprites on the
  Lynx use the **Suzy hardware blitter** (`_emit_cc65_sprite_engine`): it keeps
  the Game Boy sprite *model* (a shared 8×8 2bpp tile table + sprite slots
  referencing tiles by index, same `gbs_*` API names) but each tile is converted
  once into a totally-literal Lynx sprite-data stream, each slot owns a Suzy
  Sprite Control Block (`SCB_REHV_PAL` from `<lynx.h>`/`_suzy.h`), and
  `gbs_present()` clears the back buffer and fires one `tgi_sprite()` per visible
  slot — so a sprite program owns the frame (present repaints the bg; don't mix
  with immediate text). The literal tile format (verified byte-exact against the
  bundled `sp65`) is, per 8px 2bpp row, `[offset=0x04][2 MSB-first pixel bytes]
  [0x00 pad]`, with a trailing `0x00` ending the sprite; GB pixel value 0 maps to
  pen 0 (`COLOR_TRANSPARENT`) so it is transparent for `TYPE_NORMAL` sprites,
  matching the GB colour-0-transparent object model. `FLIP_X`/`FLIP_Y` lower to
  Suzy `HFLIP`/`VFLIP`. `bounce.mos`/`pong.mos` build & run on the Lynx.
  **Lynx display model
  (matters / flicker-sensitive):** the Lynx TGI is an interrupt-driven
  dual-buffer device, so `video_init` must `CLI()` (enable IRQs) +
  `tgi_setframerate(60)` (60 not 75, so `wait_vblank` paces programs at GB
  speed and "60 frames ≈ 1 s" holds), and it starts single-buffered
  (`tgi_setdrawpage(0)` ==
  `tgi_setviewpage(0)`) so immediate text persists without flipping. `gbs_present`
  waits out one display frame (the Lynx `clock()` ticks per frame — Mikey VBL
  timer) until the sprite engine is used, then it switches to true double
  buffering (`tgi_setdrawpage(1)` + `tgi_updatedisplay()` flip). Calling
  `tgi_updatedisplay()` on content that lives in only one buffer (e.g. text +
  repeated `wait_vblank`) flips to a blank buffer every other frame = flicker —
  that's why text stays single-buffered. TGI text glyphs draw transparently, so
  `gbs_print_string`/`number` first clear the covered cells (GB tile-replace
  semantics: reprinted counters don't smear). Background/window
  tilemap calls (`graphics.bkg`/`window`, `video.show_window`), and
  sprite/draw on a conio profile (PC Engine), are unsupported — calling one is a
  clear compile error (raised in `_gen_call` via `ALL_STDLIB_CALLS`), not a link
  failure. cc65 has `<stdint.h>`, so `uintN_t`
  spellings work on both backends. Lynx ROMs are checked headlessly with the
  `emu/libretro/run_lynx.py` harness — see "Testing ROMs".
- **Implemented**: modules/import/export, `var`/`const`, struct & enum types,
  functions (`local`, `-> type`), `if/else if/else`, `loop`, `while <cond>`,
  `break`, `continue`, `switch`/`case`/`default`, `for x in a..b`, `return`;
  operators `+ - * / %`, comparisons, `and/or/not`, `= += -=`; integer literals
  in decimal/hex (`0xE4`)/binary (`0b1010`); struct/array literals. `const`
  arrays emit as real C `const` tables (not `#define`). Params (incl. structs)
  pass **by value**.
- **Not implemented** (don't assume these exist): array iteration in `for`,
  pointers, inline `asm`, `region`/`stack` memory blocks, function types,
  `%=`/`*=`/`/=`. Stdlib: `platform.video` (incl. `show_sprites`/`hide_sprites`/
  `show_window`/`hide_window`/`show_background`), `platform.input`,
  `platform.hardware` (raw `hw.read`/`hw.write`), `platform.system`
  (`delay`/`random`/`seed_random`), `graphics.sprite`, `graphics.bkg`,
  `graphics.window`, and `graphics.text` (`print_string`/`print_number`/
  `clear_area` work — the prelude's `gbs_print_*` helpers lazily `font_init()` +
  `font_set(font_load(font_ibm))` on first use, since GBDK-2020's `printf` draws
  nothing until a font is loaded; `text.set_font` is declared but unmapped, so it
  won't link). Stdlib calls map via the active stdlib map (`self.stdlib_calls`,
  selected from `STDLIB_CALLS_*` by framework + capabilities);
  stdlib constants (`INPUT_*`, `FLIP_X/Y`, `SCREEN_*`, and — GB family only —
  `REG_*`) are emitted as prelude `#define`s (the `STDLIB_*` module strings are
  **not** compiled).
- Several `mosaik.toml` keys (`rom_size`, `ram_size`, `optimization_level`,
  `features`, etc.) are parsed but **not acted on**. Only `name`,
  `target_platforms`, `source.folder`, `build.output_dir` affect output.

## Conventions

- Platform is Windows; use PowerShell syntax for shell work. Console output uses
  status emoji — streams are reconfigured to UTF-8 to survive cp1252 consoles.
- When adding a language feature, touch all four stages as needed (Lexer →
  Parser/AST → TypeChecker → CodeGenerator) and add a test under `tests/` plus a
  sample if it's user-visible. Keep `mosaik_lang_spec.md` and `Readme.md` in
  sync, using the ✅/🔭 tags.
