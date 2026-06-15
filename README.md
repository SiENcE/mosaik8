# MosaiK8

**MosaiK8** is a framework for retro console development built around **mosaik**,
a modern, high-level programming language. The mosaik compiler has **two C
backends** — GBDK and cc65 — that together target **nine consoles from one
language and one set of source files**.

- **GBDK backend** (default) — emits GBDK C, linked by the GBDK-2020
  toolchain (`lcc`/`sdcc`) into Game Boy, Game Boy Color, Analogue Pocket,
  Mega Duck, Sega Master System, Game Gear, and NES ROMs.
- **cc65 backend** — emits cc65 C, linked by cc65 (`cl65`) into
  Atari Lynx (`.lnx`) and PC Engine (`.pce`) ROMs.

The lexer, parser, type-checker, and all logic codegen are shared; only the
prelude and standard-library lowering differ per backend. Programs that stay
within the portable stdlib subset (text, input, timing, sound, sprites,
background, palettes) build for every console unchanged.

> Naming: the framework/CLI is **MosaiK8**; the language is **mosaik**.
> `.mos` = source · `.c` = generated intermediate · `.gb`/`.gbc`/`.pocket`/
> `.duck`/`.sms`/`.gg`/`.nes` = GBDK ROMs · `.lnx`/`.pce` = cc65 ROMs.

## 🎮 Features

- **Nine consoles, one source** — Game Boy / Color / Pocket / Mega Duck, SMS,
  Game Gear, NES (GBDK) and Atari Lynx, PC Engine (cc65).
- **Modern syntax** — Lua/Python-flavoured surface over an 8/16-bit type system:
  structs, enums, modules, control flow, and the portable stdlib compile
  identically everywhere.
- **Capability gating** — calling a stdlib function a console lacks is a clear
  compile-time error, not a silent no-op or link failure.
- **Standard library** — video, input, sound (a portable beep channel on all
  nine), sprites + metasprites, scrollable background tilemap, window (GB
  family), draw primitives (Lynx), and `graphics.palette` (the 4-colour GB
  palette model on every console, degrading to greys on the 4-grey machines).
- **Asset pipeline** — PNGs listed in `mosaik.toml` (or `--asset`) become tile
  data on every console; a sidecar `*.sprites.json` slices a sheet into named
  sub-sprites ready for `sprite.set_meta`.
- **ROM banking** — `bank(N)` places functions in MBC5 banks to grow past 32 KB
  on the Game Boy family (ignored elsewhere, so the source still builds).
- **Game framework** — reusable `lib/game/` modules (scenes, follow camera,
  collision, dialogue, HUD, genre kits) for building multi-room games.
- **One-shot setup** — `setup_tools.py` downloads the toolchains and test
  emulators for you.

## 📚 Documentation

- **[`docs/mosaik_lang_spec.md`](docs/mosaik_lang_spec.md)** — the language: syntax,
  types, modules, the build system, the full standard-library reference, and the
  per-console support matrix. *Start here to write mosaik.*
- **[`docs/game-framework.md`](docs/game-framework.md)** — the game framework that
  sits on top of mosaik: scenes, actors, collision, camera, dialogue, HUD, genre
  loops, and a how-to for adding a new genre. *Start here to build a game.*

## 📋 Requirements

- **Python 3.7+** with the `toml` package
- **GBDK-2020** — for the GBDK consoles (installed by `setup_tools.py`)
- **cc65** — for the Atari Lynx / PC Engine targets (installed by `setup_tools.py`)
- *(optional, for verification)* **PyBoy** (Game Boy ROMs) and **libretro.py + cores**
  (Lynx / PCE / SMS / GG / NES)

### One-shot setup

`setup_tools.py` downloads and installs everything into the folders the build
tool expects (all gitignored):

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
| `cores` | Handy/Beetle Lynx, Beetle PCE, Genesis Plus GX, FCEUmm cores → `emu/libretro/` | headless Lynx/PCE/SMS/GG/NES testing |
| `python` | `pyboy`, `libretro.py`, `pillow`, `toml` (pip) | build tool + emulator harnesses |

Notes:
- cc65 binary snapshots exist for Windows only; on Linux/macOS install cc65 from
  your package manager and set `CC65_HOME`.
- The Beetle Lynx core needs the real Lynx boot ROM (`lynxboot.img`, 512 bytes,
  copyrighted — not downloadable). Drop it into `emu/libretro/` yourself;
  without it the harness falls back to the Handy core, which boots homebrew
  BIOS-less.

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

Search order per toolchain: the `GBDK_HOME`/`CC65_HOME` env var → a
`gbdk-2020/`/`gbdk/` or `cc65/` folder next to `mosaik8.py` (what
`setup_tools.py` creates) → the system `PATH` (`lcc` / `cl65`).

## 🚀 Quick Start

### 1. Initialize a new project

```bash
python mosaik8.py init my_game
cd my_game
```

This creates `my_game/mosaik.toml` + `my_game/src/main.mos`.

### 2. Write your first game

A minimal portable program (builds on all nine consoles):

```mosaik
module "main" {
    import "platform.video"
    import "platform.input"

    var player_x: u8 = 80
    var player_y: u8 = 72

    function main() {
        video.enable_lcd()
        loop {
            if input.pressed(INPUT_LEFT)  and player_x > 0   { player_x -= 1 }
            if input.pressed(INPUT_RIGHT) and player_x < 152 { player_x += 1 }
            if input.pressed(INPUT_UP)    and player_y > 0   { player_y -= 1 }
            if input.pressed(INPUT_DOWN)  and player_y < 136 { player_y += 1 }
            video.wait_vblank()
        }
    }

    export main
}
```

The full language is documented in
[`docs/mosaik_lang_spec.md`](docs/mosaik_lang_spec.md).

### 3. Build and run

```bash
# Build for Game Boy (uses mosaik.toml in the current directory)
python mosaik8.py build

# ...or any other supported console
python mosaik8.py build --platform gameboy_color
python mosaik8.py build --platform nes
python mosaik8.py build --platform lynx samples/hello.mos

# MosaiK8 has no `run` command — open the built ROM in an emulator directly:
pyboy samples/build/gameboy/bounce.gb                        # Game Boy
python emu/libretro/run_lynx.py samples/build/lynx/hello.lnx # Atari Lynx (headless)
```

Supported `--platform` values: `gameboy`, `gameboy_color`, `analogue_pocket`,
`megaduck`, `sms`, `gamegear`, `nes` (GBDK backend), and `lynx`, `pce` (cc65
backend). A program that stays within the portable stdlib subset builds for all
nine unchanged — size the world with the per-target `SCREEN_WIDTH`/`SCREEN_HEIGHT`
constants and it *behaves* right everywhere too. Gate non-portable code with
`if platform == "..."`.

## 🏗️ Build Modes

The build tool has exactly two modes — there is **no "scan the whole tree" mode**.

- **Single-file mode** — `build <file.mos>`: a `build/` folder is created next to
  the file, and the generated `.c`/ROM are named after the source. Transitive
  non-stdlib imports are pulled in automatically.
  ```bash
  python mosaik8.py build samples/bounce.mos
  python mosaik8.py build --platform gameboy samples/pong.mos
  ```
- **Project mode** — `build <mosaik.toml>` (or a directory containing one, or
  nothing for `./mosaik.toml`): compiles every `.mos` under `[source] folder`
  into one program and builds each `target_platforms` console.
  ```bash
  python mosaik8.py build projects/game        # directory containing mosaik.toml
  ```

Other commands: `clean` (removes the project-mode `build/`), `init`, `version`.
Add `--debug` for `lcc` debug symbols. See the build-system section of the
[language spec](docs/mosaik_lang_spec.md) for the full CLI and `mosaik.toml`
reference.

## ⚙️ Configuration (`mosaik.toml`)

```toml
[project]
name = "my_game"                              # names the output .c and ROM
target_platforms = ["gameboy", "lynx", "pce"] # any of the nine

[source]
folder = "src/"                               # where .mos sources live

[assets]
sprites = ["assets/sprites.png"]              # PNGs → tile data at build time

[build]
output_dir = "build"
rom_size = "64KB"                             # GB family only: cart geometry (banking)
ram_size = "8KB"                              # GB family only
```

These are the keys the build acts on (plus `project.version` as metadata).
Anything else prints a `⚠️` warning so typos don't pass silently. Platform
names accept aliases (`atari_lynx` → `lynx`, `pc_engine` → `pce`, …).

## 🧪 Samples & Tests

The `samples/` folder holds ready-to-build programs (`hello`, `bounce`, `pong`,
`beep`, `banked`, `colors`, `metasprite`, `cross_platform`, …); full projects
live in `projects/` (`game`, `shmup`, `background`, `colorlab`, `endless-runner`,
`zelda-slice`, …). Build any sample in single-file mode and any project in
project mode:

```bash
python mosaik8.py build samples/bounce.mos   # → samples/build/<console>/bounce.*
python mosaik8.py build projects/shmup       # → projects/shmup/build/<console>/starfall.*
```

Run the test suite:

```bash
python tests/run_all.py            # unit tests
python tests/run_all.py --samples  # also build every sample × console + the projects
python tests/verify_roms.py        # behavioural checks (PyBoy; --lynx/--pce/--sms/--gg/--nes add cores)
```

`run_all.py` exits non-zero on any failure, so it is suitable for CI. ROM
verification (PyBoy + the libretro harness) is documented in the
[language spec](docs/mosaik_lang_spec.md#56-testing-roms).

## 🔍 Troubleshooting

- **`GBDK tool 'lcc' not found`** — run `python setup_tools.py --only gbdk`, or
  set `GBDK_HOME` to an existing install, or add GBDK's `bin/` to your `PATH`.
- **`'charmap' codec can't decode byte ...`** — a `.gb` ROM was treated as a
  source file. Source files must use the `.mos` extension.
- **ROM too large** — on the Game Boy family, move functions into ROM banks with
  `bank(N)` and/or set `[build] rom_size` (see the spec's ROM-banking section).

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📄 License

This project is licensed under the MIT License — see the LICENSE file for details.

## 🙏 Acknowledgments

- **GBDK-2020 Team** — the multi-console development kit
- **cc65 Team** — the toolchain powering the Atari Lynx and PC Engine backend
- **libretro / Handy / Beetle** — the cores powering headless testing
- **Retro Development Community** — resources and inspiration across all platforms
- **Lua and Python Communities** — syntax inspiration

---

**Happy Retro Console Development!** 🎮✨
