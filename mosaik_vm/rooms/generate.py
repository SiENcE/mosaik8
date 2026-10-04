"""mosaik_vm.rooms.generate - `generate_rooms`: read the project's world
+ studio.toml, work out the world SHAPE (wide/roam rooms, pool limits,
which packs and seams it needs), and write `src/rooms.mos`."""
import os

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None

from ..loader import load_scripts
from ..songs import load_song_defs
from ..projio import _find_world, _project_sprite_pngs
from .config import (BKG_ROWS_SMSGG, BKG_TILES, _cells, _load_studio,
                     _targets_pce, _targets_smsgg, _target_screens, load_player_config,
                     load_pool_config)
from .emit import emit_rooms_mos


def _cc65_sprite_need(root, world, kmap, kt, stems, pkinds):
    """(tiles, slots) the busiest single room uploads / draws under per-room
    residency, or None when the project targets neither cc65 console.

    Tiles: the player's sheet (always uploaded) plus each distinct placed
    kind's sheet; a `[kind_variants]` placeholder counts as its LARGEST
    variant (the room load picks one at run time). Slots: the sum of the
    drawn fans (w x h 8x8 tiles each - one hardware sprite per tile on the
    PC Engine, one SCB on the Lynx), the player's included."""
    import toml as _toml
    mp = os.path.join(root, "mosaik.toml")
    try:
        targets = {str(p).strip().lower() for p in
                   (_toml.load(mp).get("project", {}) or {})
                   .get("target_platforms", [])}
    except Exception:  # noqa: BLE001 - an unreadable toml is the build's error
        return None
    if not targets & {"lynx", "pce"}:
        return None
    from mosaik_assets import sheet_sprite_defs
    fan = {}
    for kid, stem in stems.items():
        defs = sheet_sprite_defs(os.path.join(root, "assets", stem + ".png"))
        if defs:
            fan[kid] = defs[0][2] * defs[0][3]
    variants = {}
    for pname, spec in (world.get("kind_variants") or {}).items():
        if pname in kmap:
            variants[kmap[pname]] = [kmap[k] for k in (spec.get("kinds") or [])
                                     if k in kmap]
    pkid = kmap.get("player")
    ptiles = max([kt[k] for k in set(pkinds or [])] or [kt[pkid] if pkid is not None else 0])
    pfan = max([fan.get(k, 0) for k in set(pkinds or [])]
               or [fan.get(pkid, 0) if pkid is not None else 0])
    worst_t, worst_s = 0, 0
    for sc in world.get("scene", []) or []:
        kinds, slots = set(), pfan
        for obj in sc.get("object", []) or []:
            kid = kmap.get(obj.get("kind"))
            if kid is None or kid == pkid:
                continue
            alts = variants.get(kid, [kid])
            big = max(alts, key=lambda k: kt[k])
            kinds.add(big)
            slots += max(fan.get(k, 0) for k in alts)
        worst_t = max(worst_t, ptiles + sum(kt[k] for k in kinds))
        worst_s = max(worst_s, slots)
    return (worst_t, worst_s)


def generate_rooms(root, out_path=None, world=None, base=None):
    """Generate `src/rooms.mos` from the project's world + studio.toml [player].

    Returns the path, or None when the project has no world, or when
    `[world] wide` marks the shell as HAND-WRITTEN (the escape hatch -- see
    the platformer port). Written by the studio scaffold + regenerated on
    Save & Transpile (only when a rooms.mos already exists, so hand-wired
    samples are untouched).

    WIDE rooms are otherwise generated: wideness is DERIVED per room from the
    scene's own dimensions (a room wider than the 32-tile hardware background
    column-streams through engine.scroll), so a world may MIX wide and narrow
    rooms and each takes its own path. What is not generated yet raises a
    clear error naming the room, rather than silently dropping the whole
    world's wiring.

    `world`/`base` override the on-disk world with an ALREADY-LOADED dict. The
    caller that needs this is the studio's colour bridge: it folds the Palettes
    dock's per-CELL palette map into the world dict on the way to the scene
    transpiler, never onto disk, so rooms.mos has to be generated from the SAME
    dict or its colour flags (which decide whether load_room calls
    `scenes.load_palettes` / `paint_attrs`) would disagree with what the
    transpiler actually emitted. Unset = load the project's world as before,
    which is every other caller."""
    import mosaik_scenes
    _base = base
    if world is None:
        wp = _find_world(root)
        if not wp:
            return None
        world, _base = mosaik_scenes.load_world(wp)
    if world.get("world", {}).get("wide"):
        # The HAND-WRITTEN-shell escape hatch, kept for the projects built on
        # it. It says "this project owns its own rooms wiring", not "this
        # world contains a wide scene" -- that is derived below.
        return None
    w = world.get("world", {})
    scenes = world.get("scene", []) or []
    kinds = dict(world.get("kinds", {}) or {})
    doors = world.get("door", []) or []
    types, seen = [], set()
    for sc in scenes:
        t = str(sc.get("scene_type", "topdown"))
        if t not in seen:
            seen.add(t)
            types.append(t)
    def_w, def_h = int(w.get("map_w", 32)), int(w.get("map_h", 32))
    eff = [(int(sc.get("map_w", 0) or def_w), int(sc.get("map_h", 0) or def_h))
           for sc in scenes]
    all_objs = [ob for sc in scenes for ob in (sc.get("object", []) or [])]
    all_trig = world.get("trigger", []) or []      # [[trigger]] is world-level
    has_pk = "player" in kinds
    # WIDE rooms, DERIVED from each scene's own size (see BKG_TILES). Only
    # COLUMNS stream, so a room taller than the hardware tilemap needs the 2D
    # streamer (engine.scroll2d), which is not generated yet -- and a wide
    # room's handler must be one vm.player supports wide (setup_wide is the
    # PLATFORM variant). Refuse precisely, naming the room and the reason,
    # instead of dropping the whole world's wiring.
    wide_rooms = False
    # ... and whether a SHMUP room in particular is wide. The wide-shmup fork
    # is emitted only then: a world with wide PLATFORM rooms and a NARROW
    # shmup would otherwise carry a branch it can never take (the reference-engine
    # conversions are exactly that shape).
    wide_shmup = False
    roam_rooms = False
    smsgg = _targets_smsgg(root)
    row_lim = BKG_ROWS_SMSGG if smsgg else BKG_TILES
    for sc, (sw, sh) in zip(scenes, eff):
        if sw <= BKG_TILES and sh <= row_lim:
            continue
        stype = str(sc.get("scene_type", "topdown"))
        from ..isa import VmError
        if stype in ("topdown", "adventure", "pointnclick"):
            # ROAM: a wide AND/OR tall free-roam room streams BOTH axes
            # ... and a POINT-AND-CLICK room is one (W7j): its dispatch arm IS
            # the topdown one, setup_roam included, with only the per-frame
            # move swapped - so a cursor room streams both axes for free.
            # through engine.scroll2d (the reference engine's model - the 32x32 tilemap
            # is a sliding window and crossing a tile boundary queues just
            # the new column or row). vm.player drives it with setup_roam.
            roam_rooms = True
            continue
        if sw <= BKG_TILES and sh <= BKG_TILES:
            # Only the SMS/GG row limit put this room in the loop, and its type
            # has no 2D handler. It is not a NEW failure (a 29..32-row shmup or
            # platform room has always drawn its bottom rows wrong there), so
            # refusing it would break projects that build today; it stays out of
            # scope and is reported by the converter instead.
            continue
        if sh > BKG_TILES:
            raise VmError(
                "scene %r is %dx%d, taller than the %d-tile hardware "
                "background, and its type is %r: only a TOPDOWN or "
                "POINT-AND-CLICK room streams both axes (engine.scroll2d via "
                "setup_roam); a %s room streams "
                "COLUMNS only. Shrink the room, change its type, or write the "
                "shell by hand and set [world] wide."
                % (sc.get("name"), sw, sh, BKG_TILES, stype, stype))
        if stype not in ("platform", "shmup"):
            raise VmError(
                "scene %r is %d tiles wide (past the %d-tile hardware "
                "background) but its type is %r: vm.player supports a wide "
                "PLATFORM room (setup_wide), a wide SHMUP room "
                "(setup_wide_shmup) and a wide/tall TOPDOWN room (setup_roam), "
                "not this one. Shrink the room, change its type, or write the "
                "shell by hand and set [world] wide."
                % (sc.get("name"), sw, BKG_TILES, stype))
        wide_rooms = True
        if stype == "shmup":
            wide_shmup = True
    # A room can hold at most `[build] actor_pool` objects (vm.actor's ACTORS)
    # and `[build] trigger_pool` triggers (vm.trigger's MAXT): the emitted
    # load_room loop assigns pool slots in flatten order, so more entries than
    # slots would run the pool over -- and both runtimes DROP the extras
    # silently (`actor.activate`'s bounds guard, `trigger.add`'s `tn < MAXT`),
    # so on console the 9th entity simply never appears. Refuse the world with
    # a clear error instead. Per-console filtering counts exactly: an entry
    # tagged `platforms` only ships on those consoles, so the bound is untagged
    # + the busiest single console's tagged count.
    _pools = load_pool_config(root)

    def _worst_per_console(entries):
        untagged = sum(1 for e in entries if not e.get("platforms"))
        tagged = {}
        for e in entries:
            for con in (e.get("platforms") or []):
                tagged[con] = tagged.get(con, 0) + 1
        return untagged + (max(tagged.values()) if tagged else 0)

    for sc in scenes:
        objs = [ob for ob in (sc.get("object", []) or [])
                if not (has_pk and ob.get("kind") == "player")]
        for entries, cap, what, knob, pack in (
                (objs, _pools["actor"], "objects", "actor_pool", "vm.actor"),
                (sc.get("trigger", []) or [], _pools["trigger"], "triggers",
                 "trigger_pool", "vm.trigger")):
            worst = _worst_per_console(entries)
            if worst > cap:
                from ..isa import VmError
                raise VmError(
                    "scene %r places %d %s on one console but the pool has %d "
                    "slots (%s) - raise [build] %s in mosaik.toml, split the "
                    "room, or drop %s"
                    % (sc.get("name"), worst, what, cap, pack, knob, what))
    start = 0
    ss = w.get("start_scene")
    if ss:
        for i, sc in enumerate(scenes):
            if sc.get("name") == ss:
                start = i
    # 4bpp BACKGROUND tier: a >4-colour indexed tileset renders native 16-colour
    # on a bkg_bpp==4 console (PCE/SMS/GG). When one is present, rooms.mos loads
    # the tileset's authored palette (scenes.BKG_PALETTE16, emitted by the scene
    # transpiler's platform fork) on room load; that call also flips the PCE/SMS
    # bkg upload to the 4bpp path (the MUST-call contract). Byte-identical for a
    # <=4-colour tileset.
    from mosaik.platforms import BKG_4BPP_ENGINE
    from mosaik_assets import bkg_build_is_4bpp, AssetError
    ts = world.get("tileset", {}) or {}
    ts_pngs = ts.get("pngs") or ([ts["png"]] if "png" in ts else [])
    bkg4 = False
    if BKG_4BPP_ENGINE and ts_pngs and _base:
        try:
            bkg4 = bkg_build_is_4bpp([os.path.join(_base, p) for p in ts_pngs])
        except AssetError:
            bkg4 = False
    # COLOUR (the per-tile background palette tier). Read straight off the
    # world, in lockstep with the scene transpiler's own two flags: `colour`
    # means it emitted BKG_PAL/SPR_PAL (a room loads its palette SET at room
    # load), `tile_pal` that it also emitted TILE_PAL + paint_attrs (a painted
    # room uploads its attribute map, a streamed one colours per column).
    # Calling either without the transpiler emitting it is an unexported-symbol
    # error, so keep these two conditions matching transpile.py's
    # `has_palettes` / `has_tile_pal`.
    _pal_lib = bool(world.get("palette"))
    # The rows WITH `[world] default_*_palettes` applied - the transpiler's own
    # reading (mosaik_scenes.palette_fold.scene_palette_rows).
    from mosaik_scenes.palette_fold import scene_palette_rows
    _bpal, _spal = scene_palette_rows(world)
    colour = _pal_lib and any(b is not None or sp is not None
                              for b, sp in zip(_bpal, _spal))
    tile_pal = _pal_lib and any(sc.get("tile_palette") is not None
                                for sc in scenes)
    # [kind_palettes] -> scenes.KIND_PAL: which sprite palette slot an actor of
    # each kind renders with. Independent of the background tables.
    kind_pal = bool(world.get("kind_palettes"))
    # `[world] pal_write` -> wire core.set_pal_write(scenes.set_palette) so the
    # PAL_SET opcode reaches the library. The transpiler emits `set_palette`
    # under the SAME world fact, so the generated call and the generated
    # definition cannot disagree (the `replace_tile` rule).
    pal_write = bool(world.get("world", {}).get("pal_write")) and _pal_lib
    # [kind_tile_palettes]: a palette per CELL of a kind's metasprite, so ONE
    # actor can wear several. Supersedes the uniform per-kind slot where both
    # are present -- the two engine verbs are exclusive by design.
    kind_tpal = bool(world.get("kind_tile_palettes"))
    info = {
        "start": start,
        "types": types,
        "bkg4": bkg4,
        "colour": colour,
        "tile_pal": tile_pal,
        "kind_pal": kind_pal,
        "has_pal_write": pal_write,
        "kind_tpal": kind_tpal,
        "uniform": all(e == (def_w, def_h) for e in eff),
        "wide_rooms": wide_rooms,
        "wide_shmup": wide_shmup,
        "roam_rooms": roam_rooms,
        # The project targets a console whose background is 28 rows tall, so
        # the room-load / redraw forks compare HEIGHT against a per-console
        # const instead of the flat 32. See BKG_ROWS_SMSGG.
        "smsgg_rows": bool(smsgg and roam_rooms),
        # ...and whether the project targets them AT ALL, which is what decides
        # the per-console sprite-tile ceiling fork (their 192-slot pattern area
        # against the GB's 128). A project that will never build for those two
        # carries no fork and stays byte-identical.
        "targets_smsgg": bool(smsgg),
        # ...and the PC Engine, whose 224-line screen shows the GB park y
        # (200) and whose conio clear is a BAT write: its forks (the park, the
        # small-room clear) are carried only by a project that targets it.
        "targets_pce": _targets_pce(root),
        # A room SMALLER than some target console's screen leaves the cells it
        # does not cover holding the previous room. Derived from the project's
        # OWN targets, so a GB-only project (whose 20x18 screen is the smallest
        # a scene can be) emits nothing and stays byte-identical, while an
        # SMS one -- 32x24 against imported 20x18 rooms -- gets the clear.
        # ANDed with has_text/has_menu below, once those are known.
        "small_scenes": any(w < sw or h < sh
                            for (w, h) in eff
                            for (sw, sh) in _target_screens(root)),
        "has_collision": any(sc.get("collision") for sc in scenes),
        # a painted cell of TYPE 2 (one-way platform) anywhere in the world.
        # A scene's collision is a list of ROWS in world.toml but may arrive
        # flat, so flatten before testing - a membership test on the nested
        # form silently never matches (it compares against row LISTS).
        "has_platform_cells": any(2 in _cells(sc.get("collision"))
                                  for sc in scenes),
        # ...and a painted cell of TYPE 3 (a climbable LADDER), same rule.
        "has_ladder_cells": any(3 in _cells(sc.get("collision"))
                                for sc in scenes),
        "has_solid_tiles": bool((world.get("collision") or {}).get("solid")),
        "has_doors": bool(doors),
        "has_triggers": bool(all_trig),
        # ON LEAVE: emitted only when a world binds one, because that is what
        # picks `vm.trigger`'s falling-edge arm - and the 6-arg `trigger.add`
        # that arm defines. The generated CALL and its generated DEFINITION are
        # decided by this one fact.
        "has_trig_leave": any(tr.get("on_leave") for tr in all_trig),
        "has_obj_init": any(ob.get("on_init") for ob in all_objs),
        "has_obj_update": any(ob.get("on_update") for ob in all_objs),
        "has_obj_interact": any(ob.get("on_interact") for ob in all_objs),
        "has_obj_hit": any(ob.get("on_hit") for ob in all_objs),
        # SCREEN-SPACE actors (the reference engine's isPinned): drawn without the camera
        # subtraction, so a foreground cutscene character or a hearts readout
        # holds its place while the level scrolls under it. Emitted only when a
        # world places one, so every other world is byte-identical.
        "has_obj_pin": any(ob.get("pinned") for ob in all_objs),
        "has_scene_init": any(sc.get("on_init") for sc in scenes),
        # Animated background tiles ([[animated_tile]]) -> wire
        # core.set_bkg_anim(scenes.anim_tick) so run() drives the tile-data swap
        # each frame. False -> scenes.anim_tick (a no-op) is never referenced.
        "has_bkg_anim": bool(world.get("animated_tile")),
        # ...and the PER-SCENE form, which needs the room passed in (the
        # world-global `anim_tick()` takes none). A world with both wires both.
        "has_scene_bkg_anim": any(sc.get("animated_tile") for sc in scenes),
        # A REPLACEMENT TILE BANK ([[replace_tile]]) -> wire
        # core.set_bkg_tile(scenes.replace_tile) so the BKG_TILE opcodes reach
        # it. The transpiler emits `replace_tile` under the SAME world fact, so
        # the generated call and the generated definition cannot disagree.
        "has_repl_tile": bool(world.get("replace_tile")),
        # The transpiler emits `scenes.warm(scene)` for a room past the
        # background (mosaik_scenes/transpile.py `warm_fn`), which cannot be
        # painted but still needs everything paint() does APART from the map
        # upload. Two independent reasons, either is enough:
        #   * a PER-SCENE TILESET - paint() uploads it, so without warm() the
        #     room renders through the previous room's tile data (every
        #     console; found on the platformer conversion, whose 6 scenes keep
        #     paint_table off);
        #   * stream + paint_table - the range windows map_tile/collision_at
        #     read.
        # Keep the two conditions in lockstep: calling a warm() that was not
        # emitted is an unexported-symbol error, and NOT calling one that was
        # leaves the room streaming an empty level.
        "warm_fn": bool((wide_rooms or roam_rooms)
                        and (any(sc.get("tileset") for sc in scenes)
                             or (world.get("world", {}).get("stream")
                                 and world.get("world", {}).get("paint_table")))),
        # SCANLINE PARALLAX: any scene declaring bands. The transpiler emits
        # the PX_* tables for exactly this condition; keep the two in lockstep,
        # since reading px_count() from a world that emitted none is an
        # unexported-symbol error.
        "parallax": any(sc.get("parallax") for sc in scenes),
        "has_player_kind": has_pk,
        "has_objects": any(ob.get("kind") != "player" for ob in all_objs)
                       if has_pk else bool(all_objs),
        "player": load_player_config(root),
    }
    info["has_entity"] = (info["has_obj_init"] or info["has_obj_update"]
                          or info["has_obj_interact"] or info["has_obj_hit"])
    # A script using a modal menu/choice needs core.set_choice(render_choice)
    # wired; scan the authored events so the wiring links ONLY when used.
    def _uses_menu(evs):
        for ev in evs or []:
            e = ev.get("event")
            if e in ("menu", "say_choose"):
                return True
            if e == "if" and (_uses_menu(ev.get("then")) or _uses_menu(ev.get("else"))):
                return True
            if e == "switch":
                for c in ev.get("cases", []) or []:
                    if _uses_menu(c.get("do")):
                        return True
                if _uses_menu(ev.get("default")):
                    return True
        return False
    has_menu = False
    try:
        for s in load_scripts(os.path.join(root, "scripts")):
            if _uses_menu(s.get("events")):
                has_menu = True
    except Exception:
        has_menu = False
    info["has_menu"] = has_menu
    # A script that fires a `projectile` needs the vm.projectile pool wired
    # (core.set_projectiles); scan events so it links ONLY when used. On Hit slots
    # then add the native hit dispatch (projectile.set_hit).
    def _build_id(scripts):
        """An 8-bit digest of the scripts as authored (names + events), never
        0 so a shell that set one is distinguishable from one that did not."""
        import hashlib
        import json
        blob = json.dumps(scripts, sort_keys=True, default=str).encode("utf-8")
        return (hashlib.sha1(blob).digest()[0] % 255) + 1

    def _uses(evs, names):
        for ev in evs or []:
            e = ev.get("event")
            if e in names:
                return True
            if e == "if" and (_uses(ev.get("then"), names)
                              or _uses(ev.get("else"), names)):
                return True
            if e == "switch":
                for c in ev.get("cases", []) or []:
                    if _uses(c.get("then"), names):
                        return True
                if _uses(ev.get("default"), names):
                    return True
        return False
    def _any_event(evs, pred):
        """True when `pred` says yes about any event, at any nesting depth."""
        for ev in evs or []:
            if pred(ev):
                return True
            for kids in (ev.get("then"), ev.get("else"), ev.get("default")):
                if _any_event(kids, pred):
                    return True
            for c in ev.get("cases", []) or []:
                if _any_event(c.get("then"), pred):
                    return True
        return False

    def _atan2_in(ev):
        # `atan2(` can appear in ANY expression-valued parameter (a set_var
        # expr, an if condition, a projectile angle), so this looks at the
        # values rather than at a fixed field list.
        return any("atan2(" in v for v in ev.values() if isinstance(v, str))

    uses_proj = uses_player_hit = uses_stop_update = uses_emote = False
    uses_start_update = False
    uses_atan2 = uses_proj_angle = uses_proj_anim = uses_consume = False
    uses_proj_group = False
    uses_pvis = uses_curtain = uses_save = False
    uses_text_speed = False
    save_build_id = 0
    try:
        scripts_seen = []
        for s in load_scripts(os.path.join(root, "scripts")):
            scripts_seen.append(s)
            evs = s.get("events")
            if _any_event(evs, _atan2_in):
                uses_atan2 = True
            if _any_event(evs, lambda ev: (ev.get("event") == "projectile"
                                           and ev.get("angle") is not None)):
                uses_proj_angle = True
            if _any_event(evs, lambda ev: (ev.get("event") == "projectile"
                                           and int(ev.get("frames") or 0) > 1)):
                uses_proj_anim = True
            if _any_event(evs, lambda ev: (ev.get("event") == "projectile"
                                           and int(ev.get("group") or 0) > 0)):
                uses_proj_group = True
            if _uses(evs, ("projectile",)):
                uses_proj = True
            if _uses(evs, ("set_player_hit",)):
                uses_player_hit = True
            if _uses(evs, ("actor_stop_update",)):
                uses_stop_update = True
            if _uses(evs, ("actor_start_update",)):
                uses_start_update = True
            if _uses(evs, ("actor_emote",)):
                uses_emote = True
            # An OVERRIDING input attachment takes its button away from the
            # native player handler (the reference engine's `joy ^= key`). Wire the seam
            # only when a script really overrides, or every VM8 project would
            # link the consume path and stop being byte-identical.
            if _any_event(evs, lambda ev: (ev.get("event") == "input_attach"
                                           and bool(ev.get("override")))):
                uses_consume = True
            if _uses(evs, ("player_visible",)):
                uses_pvis = True
            if _uses(evs, ("text_speed",)):
                uses_text_speed = True
            if _uses(evs, ("overlay_show", "overlay_move_to", "overlay_hide")):
                uses_curtain = True
            # The persistent-SAVE pack (`vm.sram`) is OPT-IN: vm.core's
            # SAVE/LOAD ops and the `save_exists()` state all go through the
            # `core.set_save` seam, and a shell that never registers it leaves
            # them no-ops - a game that saves, silently doesn't. Wire it when a
            # script really saves or loads, or reads whether a save exists, and
            # never otherwise (a project with no save links none of it and
            # stays byte-identical).
            if _uses(evs, ("save", "load", "save_clear", "save_peek")):
                uses_save = True
            if _any_event(evs, lambda ev: any(
                    "save_exists(" in v
                    for v in ev.values() if isinstance(v, str))):
                uses_save = True
        # The SAVE BUILD ID (vm.sram.set_build): one byte derived from the
        # authored scripts, so a save written by a build whose scripts have
        # changed - variable order, scene ids, what a cell means - reads as
        # "no save" instead of restoring into a room that moved. Stable
        # across regenerations of unchanged scripts, which is what makes a
        # save survive a rebuild of the same game.
        save_build_id = _build_id(scripts_seen)
    except Exception:
        uses_proj = uses_player_hit = uses_stop_update = uses_emote = False
        uses_start_update = False
        uses_atan2 = uses_proj_angle = uses_proj_anim = False
        uses_proj_group = False
        uses_consume = uses_pvis = uses_curtain = uses_save = False
        uses_text_speed = False
    # OP_A_STOP_UPDATE reaches vm.entity's thread handles through a seam. Wire it
    # only when a script actually stops an update AND the world has On Update
    # slots for it to stop - otherwise the seam stays unregistered and the op's
    # arm is pruned away anyway.
    info["uses_save"] = uses_save
    info["save_build_id"] = save_build_id
    info["uses_stop_update"] = uses_stop_update
    info["uses_start_update"] = uses_start_update
    info["uses_input_consume"] = uses_consume
    info["uses_player_visible"] = uses_pvis
    info["uses_text_speed"] = uses_text_speed
    # The OVERLAY CURTAIN (vm.fx.overlay): the window-layer cover the reference engine
    # slides to reveal a scene. Wired only when a script raises one, so a
    # project that does not is byte-identical (the ops are pruned too).
    info["uses_curtain"] = uses_curtain
    # The EMOTE pack needs BOTH halves: a script that emotes and art to show.
    # Wiring it with no art would reserve OAM + 4 VRAM tiles to display nothing,
    # and a project with the art but no `actor_emote` should link none of it.
    from ..emotes import emote_sheets
    info["uses_emote"] = bool(uses_emote and emote_sheets(_load_studio(root)))
    # a player-hit script needs projectiles wired to reach it
    info["uses_projectile"] = uses_proj or uses_player_hit
    info["uses_player_hit"] = uses_player_hit
    # PROJECTILE ART (studio.toml [projectiles]): a shared sheet the per-room
    # allocator uploads LAST, so a shot draws its own sprite. Only under
    # per-room residency - with the boot-upload layout the art is already
    # resident and the bytecode's tile is absolute.
    proj = _load_studio(root).get("projectiles", {}) or {}
    if uses_proj and proj.get("sheet") and proj.get("tiles"):
        info["proj_stem"] = str(proj["sheet"])
        info["proj_tiles"] = int(proj["tiles"])
    # The sheet's CELL, in 8x8 tiles. A shot is ONE hardware object only where
    # `obj_8x16` makes that object 8x16 - off it, an 8x16 cell needs two, and
    # drawing one showed the top half of every frame (the SMS/GG "wrong
    # particle animations"). Absent = 1x1, byte-identical.
    if uses_proj and int(proj.get("cell_w", 1) or 1) * int(proj.get("cell_h", 1) or 1) > 1:
        info["proj_cell"] = (int(proj["cell_w"]), int(proj["cell_h"]))
    # ...and the shot's art may be a per-object DESCRIPTOR instead of that
    # dense cell (`[projectiles] desc_kind`, the clips KIND the importer gave
    # the shot sheet). The reference engine's `projectiles_render` draws through the same
    # metasprite table an actor does, so its frames dedupe to a tile pool -
    # measured on the shooter conversion, 44 dense tiles against the reference ROM's 14, on a
    # console with 128. `set_cell` STAYS wired beside it: it is what sizes the
    # OAM fan, and the descriptor draws the same number of objects.
    if uses_proj and proj.get("desc_kind") is not None:
        info["proj_desc_kind"] = int(proj["desc_kind"])
    # THE SHOT'S OWN COLLISION BOX (studio.toml [projectiles] box_*), GB
    # Studio's `projectile->def.bounds`. Offsets are inside the drawn cell.
    # Absent = vm.projectile's 8x8 at the cell's top-left, byte-identical -
    # which is only right for a 1x1 cell, and the cell is derived now.
    if uses_proj and int(proj.get("box_w", 0) or 0) > 0:
        info["proj_box"] = (int(proj["box_w"]), int(proj["box_h"]),
                            int(proj.get("box_x", 0) or 0),
                            int(proj.get("box_y", 0) or 0))
    # The two vm.trig consumers, each wired only where it is used: an atan2()
    # expression anywhere, and a projectile fired along an angle.
    info["uses_atan2"] = uses_atan2
    info["uses_proj_angle"] = uses_proj_angle
    # an animated launch (frames > 1) wires the PROJ_ANIM latch seam
    info["uses_proj_anim"] = uses_proj_anim
    # ...and a launch that names its own collision group wires PROJ_GROUP
    info["uses_proj_group"] = uses_proj_group
    # DATA-DRIVEN clips (Option X): wire vm.canim when the project has a clips
    # module. Meta size = the LARGEST animated sprite cell (the pool is uniform).
    clips_path = os.path.join(root, "src", "clips.mos")
    sp = os.path.join(root, "studio.toml")
    anims = _load_studio(root).get("animations", {}) or {}
    if anims and os.path.isfile(clips_path):
        mw = mh = 1
        try:
            from mosaik_assets import sheet_sprite_defs
            for png in _project_sprite_pngs(root):
                if os.path.isfile(png):
                    for _n, _off, cw, ch in sheet_sprite_defs(png):
                        mw = max(mw, int(cw))
                        mh = max(mh, int(ch))
        except Exception:
            pass
        with open(clips_path, "r", encoding="utf-8") as f:
            ctext = f.read()
        info["clips"] = {"meta_w": mw, "meta_h": mh,
                         # mosaik_anim emits meta_w/meta_h only when the world
                         # MIXES sprite sizes; the scalars above stay the
                         # fallback for a uniform world (byte-identical).
                         "per_kind_size": "function meta_w(" in ctext,
                         # mosaik_anim emits frame_mask only when some frame is
                         # SPARSE (a reference-engine metasprite with gaps); a world of
                         # solid rectangles never links the masked draw path.
                         "frame_mask": "function frame_mask(" in ctext,
                         # mosaik_anim emits is_desc/fan/draw only when some
                         # frame is a DESCRIPTOR (per-object offsets + tile
                         # dedupe); every other world never links the list path.
                         "desc": "function is_desc(" in ctext,
                         # mosaik_anim emits frame_pal only when some frame
                         # carries a sprite PALETTE (the reference engine's per-state /
                         # per-frame recolour); every other world never links
                         # the palette latch or the sprite-palette prelude.
                         "frame_pal": "function frame_pal(" in ctext,
                         # the BATCHED selector (frame | flip | is_desc in one
                         # call); a clips.mos generated before it exists keeps
                         # the three-read arm and stays byte-identical.
                         "sel": "function sel(" in ctext,
                         "flip": "upload_flip" in ctext,
                         "player": "player" in anims}
    # PER-ROOM SPRITE RESIDENCY (studio.toml [sprites] residency = "room"):
    # every kind has its OWN sheet asset, and the generated load_room uploads
    # only the kinds the room actually places, at a cumulative VRAM base --
    # so the WORLD's sprite art is unbounded and only the busiest single ROOM
    # must fit the GB's 128 exclusive OBJ tiles (0x8000..0x87FF; 128+ collides
    # with BG data in 0x8800 mode). Collect per-KIND-ID tile counts + asset
    # stems from studio.toml [kind_sprites] + the sheet manifests.
    residency = None
    sdata = _load_studio(root)
    if (sdata.get("sprites", {}) or {}).get("residency") == "room" \
            and info.get("clips"):
        from mosaik_assets import sheet_sprite_defs
        kmap = dict(world.get("kinds", {}) or {})
        ksheets = sdata.get("kind_sprites", {}) or {}
        nk = (max(kmap.values()) + 1) if kmap else 1
        kt = [0] * nk
        stems = {}
        for kname, kid in kmap.items():
            stem = ksheets.get(kname)
            if not stem:
                continue
            png = os.path.join(root, "assets", stem + ".png")
            if not os.path.isfile(png):
                continue
            defs = sheet_sprite_defs(png)
            if not defs:
                continue
            _n, off, w, h = defs[-1]
            kt[kid] = off + w * h          # the sheet's total tiles
            stems[kid] = stem
        player_stem = ksheets.get("player") if "player" in kmap else None
        # PER-SCENE PLAYER SPRITE: `studio.toml [player] sprite_by_type`
        # maps a scene TYPE to the kind whose art the player wears there
        # (the reference engine's `defaultPlayerSprites`). Emitted only when it actually
        # selects more than one kind, so a single-sprite world stays
        # byte-identical.
        pkinds, pboxes = None, None
        pl = sdata.get("player", {}) or {}
        by_type = pl.get("sprite_by_type") or {}
        box_by_type = pl.get("box_by_type") or {}
        # ... and the PER-SCENE override on top of it. The reference engine resolves the
        # pair the other way round from ours: a scene's own
        # `playerSpriteSheetId` WINS and `defaultPlayerSprites` is only the
        # fallback, so a project that dresses its player per SCENE (a title
        # screen whose player is the menu CURSOR, cutscenes whose player is a
        # BLANK sheet - the standard reference-engine way to hide it) cannot be
        # expressed by type alone. Keyed by scene NAME, absent = the type
        # answer, so a world with no overrides is byte-identical.
        sprite_by_scene = pl.get("sprite_by_scene") or {}
        box_by_scene = pl.get("box_by_scene") or {}
        if (by_type or sprite_by_scene) and "player" in kmap:
            sel, boxes = [], []
            dw, dh = int(pl.get("width", 8)), int(pl.get("height", 8))
            for sc in scenes:
                stype = str(sc.get("scene_type", "topdown"))
                sname = str(sc.get("name", ""))
                kname = sprite_by_scene.get(sname) or by_type.get(stype)
                kid = kmap.get(kname) if kname else None
                # an unknown/unmapped type falls back to the default player
                sel.append(kmap["player"] if kid is None else kid)
                # The COLLISION box per scene. It is authored beside the
                # sprite map rather than derived from the metasprite size,
                # because a frame's rect is padded up to whole 8 px tiles and
                # the box must stop at the FEET: the reference engine's platform player
                # is 28 px of character in a 32 px frame, and counting the
                # padding floats it 4 px above the floor.
                b = box_by_scene.get(sname) or box_by_type.get(stype) or []
                boxes.append((int(b[0]), int(b[1])) if len(b) == 2 else (dw, dh))
            if len(set(sel)) > 1:
                pkinds = sel
                if len(set(boxes)) > 1:
                    pboxes = boxes

        # [kind_palettes] -> the slot each kind's sheet is RENDERED through on
        # SMS/GG (those consoles have no per-sprite palette select, so the
        # slot has to ride the tile data at upload time). Gated on the
        # PROJECT actually targeting one of them: rooms.mos is target-neutral,
        # so the calls sit behind an `if platform` fork and cost the other
        # consoles nothing -- but a project that will never build for SMS/GG
        # should not carry them at all (byte-identical). Kinds without an
        # entry keep slot 0, the reference-engine default.
        kpal = world.get("kind_palettes") or {}
        kslot = ({kmap[k]: int(v) & 7 for k, v in kpal.items() if k in kmap}
                 if _targets_smsgg(root) else {})
        if kslot:
            # Four CRAM sprite slots there: the transpiler's own fold, so the
            # sheet uploads onto the slot scenes.KIND_PAL names on SMS/GG.
            from mosaik_scenes.palette_fold import spr_fold
            _sf = spr_fold(world)
            if _sf:
                kslot = {k: _sf[0][v] for k, v in kslot.items()}
        # The SMS/GG SOFT FLIP under residency: the build appends a flip_left
        # kind's mirror tiles to the end of its own sheet on those two consoles
        # (mosaik8_build._bake_soft_flip), so the kind occupies more tiles
        # there and the allocator's KT must say so. The SAME planner the build
        # and generate_clips use, so the counts, the appended tiles and the
        # SMS/GG frame indices cannot disagree. Absent = byte-identical.
        import mosaik_anim
        kpngs = {k: os.path.join(root, "assets", str(s) + ".png")
                 for k, s in ksheets.items()}
        fplan = mosaik_anim.residency_flip_bake(
            anims, {k: p for k, p in kpngs.items() if os.path.isfile(p)})
        kt_smsgg = None
        if fplan:
            kt_smsgg = list(kt)
            for kid, stem in stems.items():
                got = fplan["by_stem"].get(stem)
                if got:
                    kt_smsgg[kid] += got["extra"]
        residency = {"nk": nk, "kt": kt, "stems": stems,
                     "player_stem": player_stem, "pkinds": pkinds,
                     "pboxes": pboxes, "kslot": kslot, "kt_smsgg": kt_smsgg}
        # The cc65 consoles size their sprite tile TABLE and SLOT pool at
        # build time (the GB's OBJ RAM is fixed hardware), and a residency
        # upload's base is a runtime value no compile-time scan can bound -
        # so the generator, which knows every room's kinds, states the worst
        # room's need. Read by the compiler's budget pass, kept by the shaker.
        # Only for a project that targets the Lynx or the PC Engine.
        need = _cc65_sprite_need(root, world, kmap, kt, stems, pkinds)
        if need:
            residency["spr_need"] = need
    # THE BOX OFFSET (`studio.toml [player] box_offset_by_type`): where the
    # collision box sits INSIDE the drawn sprite.
    #
    # They coincide for a hand-authored game. The reference engine's model splits them:
    # an actor carries ONE authored `bounds` that walls, triggers and interact
    # all test, and for its topdown player that box is the 16x8 FEET ROW of a
    # 16x16 character - so the head passes in front of the wall behind it.
    # Per scene TYPE, because the player's art is. Absent = 0, i.e.
    # byte-identical.
    pl = sdata.get("player", {}) or {}
    off_by_type = pl.get("box_offset_by_type") or {}
    off_by_scene = pl.get("box_offset_by_scene") or {}
    soffs = None
    if off_by_type or off_by_scene:
        rows = []
        for sc in scenes:
            b = (off_by_scene.get(str(sc.get("name", "")))
                 or off_by_type.get(str(sc.get("scene_type", "topdown"))) or [])
            rows.append((int(b[0]), int(b[1])) if len(b) == 2 else (0, 0))
        if any(r != (0, 0) for r in rows):
            soffs = rows
    # THE CAMERA FOCUS (`studio.toml [player] camera_focus_by_type`): the
    # point the follow camera centres on, offset from the player's sprite
    # top-left. Absent = the collision box's centre (byte-identical). GB
    # Studio's rule is a FIXED `PLAYER.pos + 8` and cannot be derived from
    # the box, so it is its own knob.
    focus_by_type = pl.get("camera_focus_by_type") or {}
    focus_by_scene = pl.get("camera_focus_by_scene") or {}
    cfocus = None
    if focus_by_type or focus_by_scene:
        rows = []
        for sc in scenes:
            f = (focus_by_scene.get(str(sc.get("name", "")))
                 or focus_by_type.get(str(sc.get("scene_type", "topdown"))) or [])
            rows.append((int(f[0]), int(f[1])) if len(f) == 2 else None)
        if any(r is not None for r in rows):
            dflt = next(r for r in rows if r is not None)
            cfocus = [r or dflt for r in rows]
    # TRIGGER GRID SNAP (`studio.toml [scenes] trigger_snap`, a list of scene
    # NAMES): the reference engine's TOPDOWN state moves the player on an 8 px grid, so
    # its trigger test only ever samples grid stops and the player walks a
    # full step INTO a trigger before it fires. Per scene, because its
    # ADVENTURE state runs the same test at pixel positions (the shooter conversion's aiming
    # quadrants) and both convert to our `topdown`. Absent or empty = every
    # room pixel-exact, byte-identical.
    snap_names = set((sdata.get("scenes", {}) or {}).get("trigger_snap") or [])
    tsnap = None
    if snap_names and info.get("has_triggers"):
        rows = [8 if str(sc.get("name", "")) in snap_names else 0
                for sc in scenes]
        if any(rows):
            tsnap = rows
    info["tsnap"] = tsnap
    # TRIGGER FORCE RE-FIRE (`studio.toml [scenes] trigger_force`, a list of
    # scene NAMES + `trigger_force_button`, a portable BTN_* id, default 4 =
    # UP): the reference engine's PLATFORM state passes INPUT_PLATFORM_FORCE_TRIGGER
    # (default INPUT_UP_PRESSED) as its trigger scan's `force` argument, which
    # re-fires the STANDING trigger's enter script on the button's rising
    # edge - the only way a platformer door (`if held_up() { change_scene }`)
    # can work, since the walk in spends the one latch. Per scene because the
    # topdown/shmup states pass FALSE. Absent or empty = no set_force call,
    # byte-identical (and the VM_TRIG_NO_FORCE define keeps vm.trigger's
    # plain arms).
    force_names = set((sdata.get("scenes", {}) or {}).get("trigger_force") or [])
    try:
        force_btn = int((sdata.get("scenes", {}) or {})
                        .get("trigger_force_button", 4))
    except (TypeError, ValueError):
        force_btn = 4
    tforce = None
    if force_names and info.get("has_triggers"):
        rows = [force_btn if str(sc.get("name", "")) in force_names else 255
                for sc in scenes]
        if any(v != 255 for v in rows):
            tforce = rows
    info["tforce"] = tforce
    info["boffs"] = soffs
    info["cfocus"] = cfocus
    info["residency"] = residency
    info["variants"] = _kind_variants(root, world, residency)
    # Opt-in shell callback at the end of every room load (see emit_rooms_mos).
    info["on_load_hook"] = bool((sdata.get("sprites", {}) or {})
                                .get("on_load_hook"))
    # auto-fade on room load: LCD frames held per darkness step (see
    # emit_rooms_mos). Absent/0 keeps the instant cut, byte-identical.
    try:
        info["fade"] = int((sdata.get("scenes", {}) or {}).get("fade", 0) or 0)
    except (TypeError, ValueError):
        info["fade"] = 0
    # ... and which WAY it goes (`[scenes] fade_style`, "white" or "black"):
    # The reference engine's `fade_style` field, whose default is WHITE where the engine's
    # is black. Absent = black = byte-identical; declared, start() hands it to
    # vm.fx before the first room load so the boot fade already goes that way,
    # and a script's `set_state fade_style` may flip it later.
    style = str((sdata.get("scenes", {}) or {}).get("fade_style") or "").lower()
    if style and style not in ("white", "black"):
        raise ValueError("studio.toml [scenes] fade_style must be "
                         "\"white\" or \"black\", not %r" % style)
    info["fade_style"] = style
    # THE OVERLAY CUT (`[scenes] overlay_cut`, W7d phase 2): the scanline at
    # which the window overlay stops - at that line the layer goes off and the
    # sprites come back, so an overlay covers only the TOP of the screen with
    # the room playing below it. The reference engine's `overlay_cut_scanline` and its
    # numbering, whose default 150 is off the bottom of a 144-line screen and
    # therefore means NO cut; absent/0/150 emits nothing and is byte-identical.
    # A script's `set_state overlay_cut` may move it at run time.
    try:
        cut = int((sdata.get("scenes", {}) or {}).get("overlay_cut", 0) or 0)
    except (TypeError, ValueError):
        cut = 0
    if cut and not 0 < cut < 144:
        raise ValueError("studio.toml [scenes] overlay_cut must be a scanline "
                         "on screen (1..143); %d is off the bottom, which is "
                         "what NO cut already means" % cut)
    info["overlay_cut"] = cut
    # Freeze the player's INPUT while a text box or menu is open (the reference engine
    # parity). Opt-in + byte-identical off, like every other seam here.
    info["ui_blocks_player"] = bool((sdata.get("scenes", {}) or {})
                                    .get("ui_blocks_player"))
    # GLYPH-BUFFER text: keep NO font in VRAM, rasterizing glyphs on demand into
    # a band above the scene art, so a room may use nearly the whole tile table.
    # Opt-in + byte-identical off, like every other seam here. The band itself is
    # DERIVED in codegen, so nothing here carries tile numbers.
    info["glyph_text"] = bool((sdata.get("scenes", {}) or {}).get("glyph_text"))
    # The dialogue box's full-WIDTH look (the reference engine spans the screen; ours
    # defaults to an inset box). Opt-in, byte-identical off.
    info["box_full_width"] = bool((sdata.get("scenes", {}) or {})
                                  .get("box_full_width"))
    info["box_hides_sprites"] = bool((sdata.get("scenes", {}) or {})
                                     .get("box_hides_sprites"))
    # The box's MINIMUM height in rows (the reference engine's per-text `minHeight`,
    # whose own default is 4). 0 = no floor = the historical `lines + 2`.
    # Clamped to what render_text can hold + its two border rows; a conversion
    # writes the value its own texts agree on.
    try:
        _bmin = int((sdata.get("scenes", {}) or {}).get("box_min_rows") or 0)
    except (TypeError, ValueError):
        _bmin = 0
    info["box_min_rows"] = max(0, min(7, _bmin))
    # The scripted camera PAN's clock: on, `camera_pan`'s `step` is
    # QUARTER-pixels per DISPLAY frame and the pan is paced on the display
    # clock instead of moving whole pixels per VM frame. The reference engine's camera
    # moves subpixels per LCD frame, so this is what makes a converted pan
    # exact at any frame rate; off (the default) is the historical behaviour,
    # byte-identical.
    info["camera_pan_lcd"] = bool((sdata.get("scenes", {}) or {})
                                  .get("camera_pan_lcd"))
    # The authored DMG palette REGISTERS (`studio.toml [dmg]`), which is what a
    # Game Boy sprite's three visible colours are mapped by. The reference engine's own
    # defaults are not the identity (OBP0 0xD0, OBP1 0xE0), so a conversion
    # that leaves them alone draws every sprite one shade dark. Absent, or all
    # three at the hardware identity 0xE4, emits nothing and fades as before.
    _dmg = sdata.get("dmg", {}) or {}
    _regs = []
    for _k in ("bgp", "obp0", "obp1"):
        try:
            _regs.append(max(0, min(255, int(_dmg.get(_k, 0xE4)))))
        except (TypeError, ValueError):
            _regs.append(0xE4)
    info["dmg_palette"] = tuple(_regs) if _regs != [0xE4] * 3 else None
    # SOLID ACTORS (reference-engine parity): an actor BLOCKS the player's movement
    # unless its ACTOR_FLAG_COLLISION is cleared. Opt-in, because turning it on
    # changes how every existing world plays - a hand-authored game may well
    # mean its decorative actors to be walked over. On, load_room registers each
    # placed actor's box and start() wires player.set_actor_block.
    info["solid_actors"] = bool((sdata.get("scenes", {}) or {})
                                .get("solid_actors"))
    # ... over each kind's authored HITBOX (`studio.toml [hitbox.<kind>]`, the
    # per-kind rect inside the sprite frame the studio already edits and draws
    # on the scene canvas). That box - not the drawn rectangle - is what GB
    # Studio blocks over: its big animated actor is a 7x6-TILE drawing with a 47x39 box
    # and its sign a 16x16 drawing whose box straddles the tile the post
    # stands on, so blocking over the metasprite makes scenery far more solid
    # than the reference. `solid = false` in a kind's section is how it opts
    # out as DATA (the importer writes it for a kind whose own On Init clears
    # ACTOR_FLAG_COLLISION), which keeps the A_SET_COLLISION opcode for the
    # genuinely dynamic uses. A kind with NO section falls back to its
    # metasprite rect, so a hand-authored world gets something sensible
    # without authoring 30 boxes.
    kboxes = {}
    kmap = dict((world.get("kinds", {}) or {}))
    for kname, spec in (sdata.get("hitbox", {}) or {}).items():
        if kname not in kmap or not isinstance(spec, dict):
            continue
        try:
            box = (int(spec.get("w", 0) or 0), int(spec.get("h", 0) or 0),
                   int(spec.get("x", 0) or 0), int(spec.get("y", 0) or 0))
        except (TypeError, ValueError):
            continue
        if spec.get("solid") is False:
            box = (0, 0, 0, 0)          # authored NON-blocking
        kboxes[kmap[kname]] = tuple(max(0, min(255, v)) for v in box)
    info["kind_boxes"] = kboxes
    info["nkinds"] = (max(kmap.values()) + 1) if kmap else 0
    # Register the box-HEIGHT seam only when the generated scripts module
    # actually provides it. Read from the emitted file rather than inferred, so
    # the registration and the selector cannot get out of step - a rooms.mos
    # calling a text_lines that an older scripts.mos does not export would be
    # an unexported-symbol build error.
    # MUSIC PUMP: a room load blocks for many DISPLAY frames inside one VM
    # frame, and vm.core advances the song only from run()'s per-frame arm - so
    # without pumping it, the music stops for the whole load (the reported
    # "vm.music stops when a new scene loads"). Gated on the project actually
    # having songs, and on the SAME condition `generate_glue` wires a driver on,
    # so a music-free world stays byte-identical. A hUGEDriver project emits the
    # pumps too and they cost nothing: its tick is a 64 Hz timer interrupt and
    # its `update` seam is deliberately empty.
    info["music"] = bool(load_song_defs(os.path.join(root, "scripts")))
    info["has_text"] = False
    sp = os.path.join(root, "src", "scripts.mos")
    if os.path.isfile(sp):
        try:
            with open(sp, encoding="utf-8") as f:
                info["has_text"] = "function text_lines(" in f.read()
        except Exception:
            info["has_text"] = False
    # The room-load clear of the cells a small scene does not cover. It blanks
    # with the font's SPACE glyph, which is the only tile guaranteed blank on
    # every console (a scene tileset's own tile 0 is blank only by luck -- 8 of
    # the reference-engine import's 17 have no blank tile at all), so it is emitted
    # only for a world that already draws TEXT and therefore already carries
    # the glyph. A text-free world with small scenes keeps the stale cells;
    # blanking those needs a reserved blank tile of its own, which costs a tile
    # out of the SMS/GG budget and is not worth spending unasked.
    info["clear_outside"] = bool(info.pop("small_scenes", False)
                                 and (info["has_text"] or info["has_menu"]))
    # 8x16 OBJ mode: read from mosaik.toml (a BUILD knob, beside the pools) so
    # the emitted OAM packing counts w*(h/2) objects per fan on the GB family.
    mp = os.path.join(root, "mosaik.toml")
    if toml is not None and os.path.isfile(mp):
        try:
            info["obj_8x16"] = bool(
                (toml.load(mp).get("build", {}) or {}).get("obj_8x16"))
        except Exception:
            info["obj_8x16"] = False
    out_path = out_path or os.path.join(root, "src", "rooms.mos")
    # Generate BEFORE opening - see `songs.generate_songs`: `open(..., "w")`
    # truncates, so a raise here used to leave a zero-byte rooms.mos behind.
    text = emit_rooms_mos(info)
    # A Lynx overlay build keeps the per-frame handlers resident (`hot`);
    # every other project's text is unchanged.
    from mosaik.hotmark import mark_hot
    text = mark_hot(text, "rooms", root)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path


def _kind_variants(root, world, residency):
    """`world.toml [kind_variants]`: a placed PLACEHOLDER kind the room load
    swaps for `kinds[var]`, e.g. one battle room whose foe is whichever
    species the script picked:

        [kind_variants]
        foe = { var = "foe_species", kinds = ["mon_a", "mon_b", "mon_c"] }

    Wherever `foe` is placed, load_room reads heap variable `foe_species`
    and places that kind instead (its art, clips, palette and box); a value
    outside the list keeps the placeholder. One room then serves every
    variant, and per-room residency uploads only the chosen kind's sheet, so
    the ROM carries one room rather than one room per variant.

    Returns {placeholder_kind_id: (heap_index, [kind ids])} or None (absent =
    nothing emitted, byte-identical). Needs per-room residency: without it
    every sheet sits at a fixed VRAM base and swapping the kind would draw
    the wrong tiles."""
    kv = world.get("kind_variants") or {}
    if not kv:
        return None
    from ..isa import VmError
    if residency is None:
        raise VmError(
            "world.toml [kind_variants] needs studio.toml [sprites] "
            "residency = \"room\" (the room load uploads the chosen kind's "
            "sheet) and at least one [animations] clip.")
    kmap = dict(world.get("kinds", {}) or {})
    from ..loader import compile_path
    variables = dict(compile_path(os.path.join(root, "scripts")).variables)
    out = {}
    for ph, spec in sorted(kv.items()):
        spec = spec or {}
        if ph not in kmap:
            raise VmError("[kind_variants] %r is not a kind in [kinds]" % ph)
        var = spec.get("var")
        if var not in variables:
            raise VmError(
                "[kind_variants] %r reads variable %r, which no script uses"
                % (ph, var))
        names = list(spec.get("kinds") or [])
        if not names or len(names) > 255:
            raise VmError("[kind_variants] %r needs 1..255 kinds" % ph)
        bad = [n for n in names if n not in kmap]
        if bad:
            raise VmError("[kind_variants] %r lists unknown kinds %s"
                          % (ph, ", ".join(map(repr, bad))))
        out[kmap[ph]] = (int(variables[var]), [kmap[n] for n in names])
    return out
