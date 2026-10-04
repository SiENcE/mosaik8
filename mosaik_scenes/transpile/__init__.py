"""mosaik_scenes.transpile - the declarative world -> `scenes` mosaik module transpiler.

Split out of the former single-file transpile.py; `transpile(world, base_dir)`
is unchanged, and `mosaik_scenes` re-exports it exactly as before. The pieces:

  * `shared`         - the chunk-size constants + the colour conversions
  * `context`        - the world dict -> the analysed `SceneCtx` every emitter
                       reads (tilesets, flattened maps, collision, metatiles,
                       palettes, entity tables)
  * `forked`         - the per-console content-filtering driver
  * `emit_head`      - module header, consts, kind ids, tileset data
  * `emit_maps`      - the tilemaps, paint()/warm(), the collision model
  * `emit_palettes`  - the colour tier
  * `emit_entities`  - objects / doors / triggers + the slot selectors
  * `emit_tail`      - anim_tick() and the export list
  * `core`           - `transpile()`: analyse, then emit the five in order

The rule the whole file lives by is unchanged: **every optional feature is
emitted only when present**, so a world that does not use it stays
byte-identical (golden-pinned by tests/scene_transpile_test.py).
"""

from .shared import PAL_SLOTS, _MAP_CHUNK, _TS_CHUNK, _rgb555, _rgb888  # noqa: F401
from .context import SceneCtx, analyse  # noqa: F401
from .forked import _transpile_scene_forked  # noqa: F401
from .core import transpile  # noqa: F401
