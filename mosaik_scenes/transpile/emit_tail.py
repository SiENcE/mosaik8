"""mosaik_scenes.transpile.emit_tail - the animated background tiles and the module
export list.

One section of the generated `scenes` module. `emit(c, L)` appends its
lines to `L`, reading the analysed world off the SceneCtx `c`
(see transpile.context)."""
from ..base import (_emit_animated, _emit_replace_tiles, _emit_scene_animated,
                    _ident)
from ..filtering import _SCENE_FORK_MARK


def emit(c, L):
    """anim_tick() and the export list that closes the module."""
    world, tiles, tile_count, _mark = c.world, c.tiles, c.tile_count, c._mark
    module, scenes, uniform, is_vm = c.module, c.scenes, c.uniform, c.is_vm
    has_collision, solid_set, has_palettes, has_tile_pal = c.has_collision, c.solid_set, c.has_palettes, c.has_tile_pal
    pal_write = c.pal_write
    # The LADDER type is emitted only where a scene paints one (emit_maps), so
    # its EXPORT must be decided by the same condition - a generated call and
    # its generated definition cannot disagree, and here the disagreement is a
    # compile error naming a symbol the reader has no reason to expect.
    has_ladder = bool(has_collision and any(3 in f for f in (c.col_flats or [])
                                            if f))
    kind_pal, kind_tpal, metatiles, paint_table = c.kind_pal, c.kind_tpal, c.metatiles, c.paint_table
    stream, per_scene_ts, ts_table, warm_fn = c.stream, c.per_scene_ts, c.ts_table, c.warm_fn
    has_parallax, bkg4_tiles, map_syms, col_syms = c.has_parallax, c.bkg4_tiles, c.map_syms, c.col_syms
    map_names, col_names, slot_exports = c.map_names, c.col_names, c.slot_exports
    SCENE_TYPES, has_obj_pin, kinds = c.SCENE_TYPES, c.has_obj_pin, c.kinds
    map_chunks, mt_collide = c.map_chunks, c.mt_collide

    _emit_animated(L, world.get("animated_tile", []), tiles, tile_count)
    # ...and the PER-SCENE ones, which need no shared tileset and so combine
    # with per-scene tilesets / stream / paint_table (the modes the world-global
    # form is refused in). Emitted only when a scene has one.
    has_scene_anim = _emit_scene_animated(L, getattr(c, "scene_anims", None),
                                          scenes, tiles,
                                          has_tile_pal=bool(has_tile_pal))
    # ...and the SCRIPT-driven cousin: a bank of replacement art the VM8
    # BKG_TILE ops write into a background tile (the reference engine's
    # EVENT_REPLACE_TILE_XY). Same tile-DATA mechanism as the animations
    # above - only the frame is chosen by a script, and usually computed.
    has_repl = _emit_replace_tiles(L, getattr(c, "repl_tiles", b"") or b"",
                                   has_tile_pal=bool(has_tile_pal))

    exports = (["TILE_COUNT", "MAP_W", "MAP_H", "SCENE_COUNT"]
               + (["SCENE_W", "SCENE_H"] if not uniform else [])
               + (["SCTYPE_%s" % t.upper() for t in SCENE_TYPES]
                  + ["SCENE_TYPE", "scene_type_at"] if is_vm else [])
               + (["PX_MAX", "PX_FIXED", "px_count", "px_row", "px_rows",
                   "px_shift"] if has_parallax else [])
               + ["TILESET", "map_tile", "paint"]
               + (["BKG_PALETTE16"] if bkg4_tiles is not None else [])
               + ["KIND_%s" % _ident(n) for n in kinds]
               # The chunk symbols + MAP_BLK + the shared readers exist only
               # past one chunk, so a world that fits a bank exports exactly
               # what it always did.
               + ((map_syms + ["MAP_OFF"]
                   + (["MAP_BLK", "map_cell"] if map_chunks > 1 else []))
                  if paint_table else map_names)
               + (["METATILE_COUNT", "METATILES", "map_meta"] if metatiles else [])
               + (["COLLIDE_NONE", "COLLIDE_SOLID", "COLLIDE_PLATFORM"]
                  + (["COLLIDE_LADDER"] if has_ladder else [])
                  + ["collision_at"]
                  + (["METATILE_COLLIDE"] if mt_collide is not None
                     else (["COLLISION", "COL_OFF"] if metatiles
                           else col_syms
                           + (["col_cell"] if map_chunks > 1 else []))
                     if paint_table else col_names)
                  if has_collision else [])
               + (["SOLID_COUNT", "SOLID_TILES", "is_solid"] if solid_set else [])
               + (["PAL_SLOTS", "PAL_STRIDE", "load_palettes"]
                  if has_palettes else [])
               + (["PAL_LIB", "set_palette"] if pal_write else [])
               + (["KIND_PAL"] + (["kind_pal_at"] if is_vm else [])
                  if kind_pal else [])
               + (["paint_actor"] if kind_tpal else [])
               + (["TILE_PAL", "TILE_PAL_OFF", "tile_pal", "paint_attrs"]
                  if has_tile_pal else [])
               + ["OBJ_COUNT", "OBJ_SCENE", "OBJ_KIND", "OBJ_X", "OBJ_Y",
                  "DOOR_COUNT", "DOOR_FROM", "DOOR_TX", "DOOR_TY", "DOOR_TO",
                  "DOOR_EX", "DOOR_EY"]
               + (["obj_scene_at", "obj_kind_at", "obj_x_at", "obj_y_at",
                   "door_from_at", "door_tx_at", "door_ty_at", "door_to_at",
                   "door_ex_at", "door_ey_at"] if is_vm else [])
               + (["obj_pinned"] if has_obj_pin else [])
               + slot_exports
               + (["warm"] if warm_fn else [])
               + ["anim_tick"]
               + (["anim_tick_at"] if has_scene_anim else [])
               # The generated CALL (rooms.mos's core.set_bkg_tile) and this
               # definition are decided by the same world fact, so they cannot
               # disagree - the split-compile rule.
               + (["RTILE_COUNT", "replace_tile"] if has_repl else []))
    if _mark:
        L.append(_SCENE_FORK_MARK)           # end of the per-console forkable body
    L.append("    export " + ", ".join(exports))
    L.append("}")

