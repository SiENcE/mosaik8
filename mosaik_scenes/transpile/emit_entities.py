"""mosaik_scenes.transpile.emit_entities - the object / door / trigger tables and their
selectors, forked per console when a world tags its content.

One section of the generated `scenes` module. `emit(c, L)` appends its
lines to `L`, reading the analysed world off the SceneCtx `c`
(see transpile.context)."""
from ..base import _emit_array, _emit_reader, _script_ident
from ..filtering import _platform_buckets


def emit(c, L):
    """The entity tables, the behaviour-slot selectors and the platform fork."""
    scenes, obj_scene, obj_kind, obj_x = c.scenes, c.obj_scene, c.obj_kind, c.obj_x
    obj_y, obj_init, obj_update, obj_interact = c.obj_y, c.obj_init, c.obj_update, c.obj_interact
    obj_hit, obj_pin, obj_plat, scene_init = c.obj_hit, c.obj_pin, c.obj_plat, c.scene_init
    d_from, d_tx, d_ty, d_to = c.d_from, c.d_tx, c.d_ty, c.d_to
    d_ex, d_ey, door_plat, trig_from = c.d_ex, c.d_ey, c.door_plat, c.trig_from
    trig_tx, trig_ty, trig_tw, trig_th = c.trig_tx, c.trig_ty, c.trig_tw, c.trig_th
    trig_enter, trig_plat, n_trig, is_vm = c.trig_enter, c.trig_plat, c.n_trig, c.is_vm
    trig_leave, has_trig_leave = c.trig_leave, c.has_trig_leave
    slots_used, has_obj_init, has_obj_update, has_obj_interact = c.slots_used, c.has_obj_init, c.has_obj_update, c.has_obj_interact
    has_obj_hit, has_obj_pin, has_scene_init, trig_present = c.has_obj_hit, c.has_obj_pin, c.has_scene_init, c.trig_present
    kinds, _check_u8_count, scene_id, _dim_type = c.kinds, c._check_u8_count, c.scene_id, c._dim_type

    def pad(vals):
        return vals if vals else [0]
    # Position tables widen to u16 ONLY when a (wide) world places an object/door
    # entry past 255 px; otherwise they stay u8, byte-identical to a narrow world.
    # Computed from the FULL view so a filtered bucket keeps the same column type.
    def _pos_type(vals):
        return "u16" if any(int(v) > 255 for v in vals) else "u8"
    n_obj = len(obj_scene)
    _check_u8_count("placed objects", n_obj, "fewer objects per room, or "
                    "split into more worlds")
    n_door = len(d_from)
    _check_u8_count("doors", n_door, "fewer connections, or split into more worlds")
    if slots_used and n_trig > 0:
        _check_u8_count("triggers", n_trig, "fewer [[trigger]] rects")
    ty_ox, ty_oy = _pos_type(obj_x), _pos_type(obj_y)
    ty_dtx, ty_dty, ty_dex, ty_dey = (_pos_type(d_tx), _pos_type(d_ty),
                                      _pos_type(d_ex), _pos_type(d_ey))
    ty_ttx, ty_tty = _pos_type(trig_tx), _pos_type(trig_ty)

    # The module `export` list (below) references these; the order matches the
    # historical emission order. Built ONCE from the full-view gating so it is
    # valid on whichever platform branch the compiler keeps.
    slot_exports = []
    if slots_used:
        slot_exports.append("NO_SCRIPT")
        if has_obj_init:
            slot_exports.append("obj_init")
        if has_obj_update:
            slot_exports.append("obj_update")
        if has_obj_interact:
            slot_exports.append("obj_interact")
        if has_obj_hit:
            slot_exports.append("obj_hit")
        if has_scene_init:
            slot_exports.append("scene_init")
        if trig_present:
            slot_exports += ["TRIG_COUNT", "TRIG_FROM", "TRIG_TX", "TRIG_TY",
                             "TRIG_TW", "TRIG_TH", "trigger_enter",
                             "trig_from_at", "trig_tx_at", "trig_ty_at",
                             "trig_tw_at", "trig_th_at"]
            # ON LEAVE rides beside the on-enter selector and only when a world
            # binds one: its PRESENCE is what the build reads to select
            # `vm.trigger`'s falling-edge arm, so emitting an always-empty
            # `trigger_leave` would arm the whole feature for every world.
            if has_trig_leave:
                slot_exports.append("trigger_leave")

    def _slot_selector(out, fn_name, arg_name, arg_type, pairs):
        # pairs: [(index_int, script_name), ...] with a script_name set. Emits
        # `function fn(arg: T) -> u16 { if arg == i { return scripts.ENTRY_x } .. }`.
        out.append("    function %s(%s: %s) -> u16 {" % (fn_name, arg_name, arg_type))
        for idx, nm in pairs:
            out.append("        if %s == %d {" % (arg_name, idx))
            out.append("            return scripts.ENTRY_%s" % _script_ident(nm))
            out.append("        }")
        out.append("        return NO_SCRIPT")
        out.append("    }")
        out.append("")

    def emit_entities(out, keep_obj, keep_door, keep_trig):
        """Append the OBJ/DOOR/SLOT lines for ONE bucket: the entities at the given
        kept flatten indices. Selectors re-index by the bucket's new 0..k-1 order."""
        def take(vals, keep):
            return [vals[i] for i in keep]
        o_scene, o_kind = take(obj_scene, keep_obj), take(obj_kind, keep_obj)
        o_x, o_y = take(obj_x, keep_obj), take(obj_y, keep_obj)
        out.append("    const OBJ_COUNT: u8 = %d" % len(keep_obj))
        _emit_array(out, "u8", "OBJ_SCENE", len(pad(o_scene)), pad(o_scene))
        _emit_array(out, "u8", "OBJ_KIND", len(pad(o_kind)), pad(o_kind))
        _emit_array(out, ty_ox, "OBJ_X", len(pad(o_x)), pad(o_x))
        _emit_array(out, ty_oy, "OBJ_Y", len(pad(o_y)), pad(o_y))
        if is_vm:
            # Accessors, so the generated rooms.mos does not read these arrays
            # from its own bank and pin them resident. See _emit_reader.
            _emit_reader(out, "obj_scene_at", "OBJ_SCENE", "u8")
            _emit_reader(out, "obj_kind_at", "OBJ_KIND", "u8")
            _emit_reader(out, "obj_x_at", "OBJ_X", ty_ox)
            _emit_reader(out, "obj_y_at", "OBJ_Y", ty_oy)
        if has_obj_pin:
            # SCREEN-SPACE actors, re-indexed to this bucket's flatten order.
            o_pin = take(obj_pin, keep_obj)
            out.append("    function obj_pinned(i: u8) -> u8 {")
            for j, v in enumerate(o_pin):
                if v:
                    out.append("        if i == %d {" % j)
                    out.append("            return 1")
                    out.append("        }")
            out.append("        return 0")
            out.append("    }")
        out.append("")

        df, dtx, dty = take(d_from, keep_door), take(d_tx, keep_door), take(d_ty, keep_door)
        dto, dex, dey = take(d_to, keep_door), take(d_ex, keep_door), take(d_ey, keep_door)
        out.append("    const DOOR_COUNT: u8 = %d" % len(keep_door))
        _emit_array(out, "u8", "DOOR_FROM", len(pad(df)), pad(df))
        _emit_array(out, ty_dtx, "DOOR_TX", len(pad(dtx)), pad(dtx))
        _emit_array(out, ty_dty, "DOOR_TY", len(pad(dty)), pad(dty))
        _emit_array(out, "u8", "DOOR_TO", len(pad(dto)), pad(dto))
        _emit_array(out, ty_dex, "DOOR_EX", len(pad(dex)), pad(dex))
        _emit_array(out, ty_dey, "DOOR_EY", len(pad(dey)), pad(dey))
        if is_vm:
            _emit_reader(out, "door_from_at", "DOOR_FROM", "u8")
            _emit_reader(out, "door_tx_at", "DOOR_TX", ty_dtx)
            _emit_reader(out, "door_ty_at", "DOOR_TY", ty_dty)
            _emit_reader(out, "door_to_at", "DOOR_TO", "u8")
            _emit_reader(out, "door_ex_at", "DOOR_EX", ty_dex)
            _emit_reader(out, "door_ey_at", "DOOR_EY", ty_dey)
        out.append("")

        if not slots_used:
            return
        out.append("    -- VM8 entity script slots: a selector maps an entity to its")
        out.append("    -- event script entry (scripts.ENTRY_*), NO_SCRIPT = unbound.")
        out.append("    const NO_SCRIPT: u16 = 0xFFFF")
        # Per-instance actor slots, RE-INDEXED to the bucket's OBJ flatten order.
        o_init = take(obj_init, keep_obj)
        o_update = take(obj_update, keep_obj)
        o_interact = take(obj_interact, keep_obj)
        if has_obj_init:
            _slot_selector(out, "obj_init", "i", "u16",
                           [(i, nm) for i, nm in enumerate(o_init) if nm])
        if has_obj_update:
            _slot_selector(out, "obj_update", "i", "u16",
                           [(i, nm) for i, nm in enumerate(o_update) if nm])
        if has_obj_interact:
            _slot_selector(out, "obj_interact", "i", "u16",
                           [(i, nm) for i, nm in enumerate(o_interact) if nm])
        if has_obj_hit:
            o_hit = take(obj_hit, keep_obj)
            _slot_selector(out, "obj_hit", "i", "u16",
                           [(i, nm) for i, nm in enumerate(o_hit) if nm])
        # Per-scene On Init, indexed by scene id (scenes are not per-console filtered).
        if has_scene_init:
            _slot_selector(out, "scene_init", "scene", "u8",
                           [(i, nm) for i, nm in enumerate(scene_init) if nm])
        # Trigger table (rects) + the on-enter selector, RE-INDEXED per bucket.
        if trig_present:
            tf, ttx, tty = take(trig_from, keep_trig), take(trig_tx, keep_trig), take(trig_ty, keep_trig)
            ttw, tth = take(trig_tw, keep_trig), take(trig_th, keep_trig)
            tenter = take(trig_enter, keep_trig)
            out.append("    const TRIG_COUNT: u8 = %d" % len(keep_trig))
            _emit_array(out, "u8", "TRIG_FROM", len(pad(tf)), pad(tf))
            _emit_array(out, ty_ttx, "TRIG_TX", len(pad(ttx)), pad(ttx))
            _emit_array(out, ty_tty, "TRIG_TY", len(pad(tty)), pad(tty))
            _emit_array(out, "u8", "TRIG_TW", len(pad(ttw)), pad(ttw))
            _emit_array(out, "u8", "TRIG_TH", len(pad(tth)), pad(tth))
            _emit_reader(out, "trig_from_at", "TRIG_FROM", "u8")
            _emit_reader(out, "trig_tx_at", "TRIG_TX", ty_ttx)
            _emit_reader(out, "trig_ty_at", "TRIG_TY", ty_tty)
            _emit_reader(out, "trig_tw_at", "TRIG_TW", "u8")
            _emit_reader(out, "trig_th_at", "TRIG_TH", "u8")
            _slot_selector(out, "trigger_enter", "i", "u16",
                           [(i, nm) for i, nm in enumerate(tenter) if nm])
            if has_trig_leave:
                tleave = take(trig_leave, keep_trig)
                _slot_selector(out, "trigger_leave", "i", "u16",
                               [(i, nm) for i, nm in enumerate(tleave) if nm])

    _e_buckets = _platform_buckets([obj_plat, door_plat, trig_plat])
    if _e_buckets is None:
        emit_entities(L, list(range(n_obj)), list(range(n_door)), list(range(n_trig)))
    elif len(_e_buckets) == 1:
        _ko, _kd, _kt = _e_buckets[0][1]
        emit_entities(L, _ko, _kd, _kt)
    else:
        for _bi, (_ids, (_ko, _kd, _kt)) in enumerate(_e_buckets):
            if _bi == 0:
                cond = " or ".join('platform == "%s"' % p for p in _ids)
                L.append("    if %s {" % cond)
            elif _ids is None:
                L.append("    } else {")
            else:
                cond = " or ".join('platform == "%s"' % p for p in _ids)
                L.append("    } else if %s {" % cond)
            emit_entities(L, _ko, _kd, _kt)
        L.append("    }")
        L.append("")

    # Background tile animations. `anim_tick()` is always emitted (a no-op when
    # there are no animated tiles) and always exported, so a hand-written or
    # composed loop can call `scenes.anim_tick()` unconditionally and keeps
    # compiling when the last animated tile is removed.
    # PUBLISHED for emit_tail: the slot selectors join the export list.
    c.slot_exports = slot_exports

