"""Layer-3 declarative scene format -> mosaik transpiler (game-framework Phase 4).

Reads a world description (TOML) -- a shared tileset + several scenes (a tilemap
+ object placements) + door edges -- and emits a mosaik module holding:

  * the tileset and each scene's 32x32 map as `const` arrays,
  * `map_tile(scene, idx)` -- selects the right map by scene id (arrays cannot be
    passed by value in mosaik, so the picker is generated here),
  * the object table (parallel `const` arrays, flattened across scenes),
  * the door table: (from-scene, trigger cell) -> (to-scene, entry pixel),
  * background tile animations: each `[[animated_tile]]` (tile / frames /
    period / count -- the reference-engine tile-data swap) lowers into a self-contained
    `anim_tick()` the game loop calls once per frame (an inline per-tile timer +
    `bkg.set_data`, no `engine.anim` slot). `anim_tick()` is **always** emitted and
    exported -- a no-op when there are no animated tiles -- so a loop may call
    `scenes.anim_tick()` unconditionally and keeps compiling when the last
    animated tile is removed. The MosaiK Studio Background editor writes these to
    `world.toml`.

The game `import`s the module and composes the framework (engine.pad / camera /
collision) around it; cross-module `const`-array indexing makes the data usable
directly. The tileset reuses mosaik_assets' PNG->2bpp pipeline. This is the
reference engine's scene-resource analogue (see docs/game-framework.md).

The world is either a single `world.toml` OR a **split-per-resource directory**
(the reference engine's scene-resource analogue): a `world/` folder holding `world.toml` (the
`[world]`/`[tileset]`/`[kinds]` header), one `scenes/<name>.toml` per scene (its
map + objects), and an optional `doors.toml`. Both forms assemble to the same
world dict and emit byte-identical modules.

Usage:
    python -m mosaik_scenes world.toml  -o src/scenes.mos   # single file
    python -m mosaik_scenes world/      -o src/scenes.mos   # split directory"""

import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from . import (base, filtering, loaders, transpile, cli)  # noqa: E402,F401

_SUBMODULES = (base, filtering, loaders, transpile, cli)
for _m in _SUBMODULES:
    for _k, _v in vars(_m).items():
        if not _k.startswith('__'):
            globals()[_k] = _v
del _m, _k, _v
