"""mosaik_scenes.transpile.forked - the per-console CONTENT FILTERING
driver: a world whose entities/scenes carry `platforms` tags is
transpiled once per target bucket and the forkable bodies are spliced
into one `if platform` chain."""
from ..base import SceneError
from ..filtering import (_clear_plat, _entity_platforms, _platform_buckets,
                         _split_marked, _stub_scene)


def _transpile_scene_forked(world, base_dir):
    """If any `[[scene]]` carries a `platforms` allow-list, emit the scenes module with
    its body FORKED per target console -- excluded scenes STUBBED to a 1x1 room (their
    tile data + entities dropped), everything else kept. Returns the module text, or
    None when no scene is tagged (the caller transpiles normally = byte-identical)."""
    scenes = world.get("scene", [])
    scene_plats = [_entity_platforms(sc) for sc in scenes]
    if not any(scene_plats):
        return None

    w = world.get("world", {})
    # A few big features interact with the whole-body fork in ways not yet worked out;
    # refuse them with a clear error rather than emit something subtly wrong.
    if w.get("stream") or w.get("paint_table"):
        raise SceneError("per-scene `platforms` filtering can't yet combine with "
                         "[world] stream / paint_table")
    if any(sc.get("tileset") for sc in scenes):
        raise SceneError("per-scene `platforms` filtering can't yet combine with "
                         "per-scene [[scene]] tilesets")

    is_vm = bool(w.get("vm"))
    has_collision = any(sc.get("collision") is not None for sc in scenes)
    all_objs = [o for sc in scenes for o in sc.get("object", [])]
    # The slot-kind set + trigger presence of the FULL world, so every branch emits the
    # SAME slot selectors + SCENE_W/H (the export list can't be conditional).
    force = {
        "uniform": False,
        "has_obj_init": any(o.get("on_init") for o in all_objs),
        "has_obj_update": any(o.get("on_update") for o in all_objs),
        "has_obj_interact": any(o.get("on_interact") for o in all_objs),
        "has_obj_hit": any(o.get("on_hit") for o in all_objs),
        "has_obj_pin": any(o.get("pinned") for o in all_objs),
        "has_scene_init": any(sc.get("on_init") for sc in scenes),
        "has_triggers": is_vm and bool(world.get("trigger")),
        "has_trig_leave": is_vm and any(t.get("on_leave")
                                        for t in (world.get("trigger") or [])),
    }

    obj_map = [(i, ob) for i, sc in enumerate(scenes)
               for ob in sc.get("object", [])]      # global obj index -> (scene, obj)
    obj_plats = [_entity_platforms(ob) for _, ob in obj_map]
    doors = world.get("door", [])
    door_plats = [_entity_platforms(d) for d in doors]
    triggers = world.get("trigger", []) if is_vm else []
    trig_plats = [_entity_platforms(t) for t in triggers]

    buckets = _platform_buckets([scene_plats, obj_plats, door_plats, trig_plats])
    bodies, prefix, suffix = [], None, None
    for ids, (keep_sc, keep_obj, keep_door, keep_trig) in buckets:
        keep_sc, keep_obj = set(keep_sc), set(keep_obj)
        keep_door, keep_trig = set(keep_door), set(keep_trig)
        # Kept objects per scene (an object in a STUBBED scene is dropped with it).
        objs_by_scene = {}
        for g, (si, ob) in enumerate(obj_map):
            if g in keep_obj and si in keep_sc:
                objs_by_scene.setdefault(si, []).append(_clear_plat(ob))
        bscenes = []
        for i, sc in enumerate(scenes):
            if i in keep_sc:
                nsc = _clear_plat(sc)
                nsc["object"] = objs_by_scene.get(i, [])
                bscenes.append(nsc)
            else:
                bscenes.append(_stub_scene(sc, has_collision))
        bworld = dict(world)
        bworld["scene"] = bscenes
        bworld["door"] = [_clear_plat(doors[i]) for i in sorted(keep_door)]
        if triggers:
            bworld["trigger"] = [_clear_plat(triggers[i]) for i in sorted(keep_trig)]
        # imported HERE, not at module scope: `core` imports this module for
        # the fork check at the top of transpile(), so a top-level import
        # would close the cycle.
        from .core import transpile
        head, body, tail = _split_marked(
            transpile(bworld, base_dir, _mark=True, _force=force))
        bodies.append((ids, body))
        if prefix is None:
            prefix, suffix = head, tail

    L = list(prefix)
    for bi, (ids, body) in enumerate(bodies):
        if bi == 0:
            L.append('    if %s {' % " or ".join('platform == "%s"' % p for p in ids))
        elif ids is None:
            L.append("    } else {")
        else:
            L.append('    } else if %s {'
                     % " or ".join('platform == "%s"' % p for p in ids))
        L.extend(body)
    L.append("    }")
    L.extend(suffix)
    return "\n".join(L) + "\n"

