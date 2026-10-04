"""mosaik_scenes.loaders - load a world (single-file world.toml or a split world/ dir)."""
import os

from .base import SceneError


try:
    import tomllib                       # Python 3.11+
    def _load_toml(path):
        with open(path, "rb") as f:
            return tomllib.load(f)
except ModuleNotFoundError:              # fall back to the `toml` package
    import toml
    def _load_toml(path):
        with open(path, "r", encoding="utf-8") as f:
            return toml.load(f)


def load_world(path):
    """Assemble a world dict from `path`, returning (world, base_dir).

    `path` is either a single world `.toml` (base_dir = its folder) or a
    split-per-resource directory (base_dir = the directory). Both yield the
    same dict shape `transpile` consumes, so the two layouts are equivalent.

    Tolerant of unknown keys: the transpiler reads only the keys it needs
    (`kind`/`x`/`y` on objects, `from`/`tx`/`ty`/`to`/`ex`/`ey` on doors, and the
    `[world]`/`[tileset]`/`[kinds]`/`[[scene]]` header), so extra keys pass through
    harmlessly. In particular MosaiK8 Studio writes **authoring-only metadata** the
    transpiler IGNORES: a stable instance `id` + an optional display `name` on each
    object/door, and `[world] next_door_id`/`next_object_id` id counters (the studio
    keys its per-instance side-tables -- a door's lock, an object's dialogue -- by
    that `id` instead of list position).
    Do NOT start rejecting unknown world/object/door keys -- it would break the studio
    round-trip. (The same rule already covers the studio's `scene_type`/`map_w`/`map_h`/
    `stream`/`start_scene` keys.)
    """
    if os.path.isdir(path):
        return load_world_dir(path), path
    return _load_toml(path), os.path.dirname(os.path.abspath(path))


def load_world_dir(d):
    """Assemble a world dict from a split-per-resource directory.

    Layout (the reference engine's scene-resource analogue):
      world.toml      -- the [world]/[tileset]/[kinds] header (+ optional doors)
      scenes/*.toml   -- one scene per file (flat: name, map, [[object]]); the
                         name defaults to the filename stem
      doors.toml      -- optional [[door]] table (merged with any in world.toml)

    Scene ids follow `[world] scene_order = [...]` (by name) when given, else
    the sorted filename order (deterministic).
    """
    root = os.path.join(d, "world.toml")
    if not os.path.isfile(root):
        raise SceneError("world directory '%s' needs a world.toml "
                         "(the [world]/[tileset]/[kinds] header)" % d)
    world = _load_toml(root)
    if world.get("scene"):
        raise SceneError("in a split world directory, scenes live in "
                         "scenes/*.toml, not [[scene]] in world.toml")

    scenes_dir = os.path.join(d, "scenes")
    if not os.path.isdir(scenes_dir):
        raise SceneError("world directory '%s' needs a scenes/ subdirectory" % d)
    by_name, file_order = {}, []
    for fn in sorted(f for f in os.listdir(scenes_dir) if f.endswith(".toml")):
        sc = _load_toml(os.path.join(scenes_dir, fn))
        # A scene file may optionally wrap its body in a [scene] table.
        if isinstance(sc.get("scene"), dict):
            sc = sc["scene"]
        sc.setdefault("name", os.path.splitext(fn)[0])
        nm = sc["name"]
        if nm in by_name:
            raise SceneError("duplicate scene name '%s' in scenes/" % nm)
        by_name[nm] = sc
        file_order.append(nm)
    if not file_order:
        raise SceneError("no scenes/*.toml files in '%s'" % scenes_dir)

    order = world.get("world", {}).get("scene_order")
    if order:
        unknown = [n for n in order if n not in by_name]
        if unknown:
            raise SceneError("[world] scene_order lists unknown scene(s): %s"
                             % ", ".join(unknown))
        ordered = list(order) + [n for n in file_order if n not in order]
    else:
        ordered = file_order
    world["scene"] = [by_name[n] for n in ordered]

    # Doors: an optional doors.toml, merged after any [[door]] in world.toml.
    doors = list(world.get("door", []))
    doors_path = os.path.join(d, "doors.toml")
    if os.path.isfile(doors_path):
        doors.extend(_load_toml(doors_path).get("door", []))
    if doors:
        world["door"] = doors
    return world
