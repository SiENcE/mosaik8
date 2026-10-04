"""mosaik_vm.rooms - src/rooms.mos generation (room load, doors/triggers/slots, player dispatch).

Split out of the former single-file rooms.py; the public API is unchanged
(`mosaik_vm.rooms.generate_rooms` / `.emit_rooms_mos` and the config readers
are all re-exported here, and `mosaik_vm`'s flat re-export sees them exactly
as before). The pieces:

  * `config`        - constants, the studio.toml / mosaik.toml readers, the
                      small shape helpers (`_dim_w`, `_rowlim`, `_cells`, ...)
  * `context`       - the world-shape dict -> the ~50 derived flags every
                      emitter reads (`derive(info) -> RoomsCtx`)
  * `emit_prelude`  - module header, imports, tuning consts, residency
                      tables, the collision probes, the per-type ticks
  * `emit_load`     - `load_room`
  * `emit_dispatch` - the per-scene-type handler pick, the fade, the hook
  * `emit_start`    - `start()`
  * `emit`          - `emit_rooms_mos`: the four sections in order
  * `generate`      - `generate_rooms`: the world analysis + the file write
"""

from .config import (BKG_ROWS_SMSGG, BKG_TILES, DEFAULT_ACTOR_POOL,  # noqa: F401
                     DEFAULT_TRIGGER_POOL, HOT_PIN, _CLEAR_CONSOLES,
                     _PLAYER_DEFAULTS, _PLAYER_TYPES, _SCREEN_CELLS,
                     _SHMUP_DEFAULT_PACE, _cells, _dim_h, _dim_w, _load_studio,
                     _meta_h, _meta_w, _pylim, _rowlim, _target_screens,
                     _targets_pce, _targets_smsgg, _wants_boxes, load_player_config,
                     load_pool_config)
from .context import RoomsCtx, derive  # noqa: F401
from .emit import emit_rooms_mos  # noqa: F401
from .generate import generate_rooms  # noqa: F401
