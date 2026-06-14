#!/usr/bin/env python3
"""Layer-3 declarative scene format -> mosaik transpiler (game-framework Phase 4).

Reads a world description (TOML) -- a shared tileset + several scenes (a tilemap
+ object placements) + door edges -- and emits a mosaik module holding:

  * the tileset and each scene's 32x32 map as `const` arrays,
  * `map_tile(scene, idx)` -- selects the right map by scene id (arrays cannot be
    passed by value in mosaik, so the picker is generated here),
  * the object table (parallel `const` arrays, flattened across scenes),
  * the door table: (from-scene, trigger cell) -> (to-scene, entry pixel).

The game `import`s the module and composes the framework (game.pad / camera /
collision) around it; cross-module `const`-array indexing makes the data usable
directly. The tileset reuses mosaik_assets' PNG->2bpp pipeline. This is the
GB-Studio `.gbsres` analogue from docs/game-framework-plan.md.

Usage:
    python mosaik_scenes.py world.toml -o src/scenes.mos
"""

import argparse
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

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

from mosaik_assets import png_to_gb_tiles, AssetError


class SceneError(Exception):
    pass


def _ident(name):
    """Uppercase C-ish identifier stem from a scene/kind name."""
    out = "".join(c if c.isalnum() else "_" for c in str(name)).upper()
    if not out or out[0].isdigit():
        out = "S_" + out
    return out


def _flatten_map(raw, w, h, scene):
    """Accept `map` as a flat list of w*h ints, or a list of h rows of w."""
    if raw and isinstance(raw[0], list):
        rows = raw
        if len(rows) != h or any(len(r) != w for r in rows):
            raise SceneError("scene '%s': map must be %d rows of %d" % (scene, h, w))
        flat = [v for row in rows for v in row]
    else:
        flat = list(raw)
    if len(flat) != w * h:
        raise SceneError("scene '%s': map has %d cells, expected %d"
                         % (scene, len(flat), w * h))
    return [int(v) & 0xFF for v in flat]


def _emit_array(lines, c_type_name, name, n, values, per_line=16):
    lines.append("    const %s: array[%s, %d] = [" % (name, c_type_name, n))
    for i in range(0, len(values), per_line):
        chunk = ", ".join(str(v) for v in values[i:i + per_line])
        tail = "," if i + per_line < len(values) else ""
        lines.append("        %s%s" % (chunk, tail))
    lines.append("    ]")


def transpile(world, base_dir):
    """world: parsed TOML dict; base_dir: dir paths in the TOML are relative to.
    Returns the mosaik module source string (deterministic)."""
    w = world.get("world", {})
    module = w.get("module", "scenes")
    map_w = int(w.get("map_w", 32))
    map_h = int(w.get("map_h", 32))

    ts = world.get("tileset", {})
    if "png" not in ts:
        raise SceneError("[tileset] needs a `png` path")
    png = os.path.join(base_dir, ts["png"])
    try:
        tiles = png_to_gb_tiles(png)
    except AssetError as e:
        raise SceneError(str(e))
    tile_count = len(tiles) // 16

    kinds = world.get("kinds", {})            # name -> id

    scenes = world.get("scene", [])
    if not scenes:
        raise SceneError("a world needs at least one [[scene]]")
    # Scene ids are the declaration order; names index into them.
    name_to_id = {}
    for i, sc in enumerate(scenes):
        nm = sc.get("name", "scene%d" % i)
        if nm in name_to_id:
            raise SceneError("duplicate scene name '%s'" % nm)
        name_to_id[nm] = i

    def scene_id(ref):
        if isinstance(ref, int):
            return ref
        if ref not in name_to_id:
            raise SceneError("unknown scene '%s'" % ref)
        return name_to_id[ref]

    # Flatten the object + door tables across scenes (parallel arrays).
    obj_scene, obj_kind, obj_x, obj_y = [], [], [], []
    for i, sc in enumerate(scenes):
        for ob in sc.get("object", []):
            k = ob["kind"]
            if k not in kinds:
                raise SceneError("scene '%s': object kind '%s' not in [kinds]"
                                 % (sc.get("name"), k))
            obj_scene.append(i)
            obj_kind.append(int(kinds[k]) & 0xFF)
            obj_x.append(int(ob["x"]) & 0xFF)
            obj_y.append(int(ob["y"]) & 0xFF)

    doors = world.get("door", [])
    d_from, d_tx, d_ty, d_to, d_ex, d_ey = [], [], [], [], [], []
    for dr in doors:
        d_from.append(scene_id(dr["from"]))
        d_tx.append(int(dr["tx"]) & 0xFF)
        d_ty.append(int(dr["ty"]) & 0xFF)
        d_to.append(scene_id(dr["to"]))
        d_ex.append(int(dr["ex"]) & 0xFF)
        d_ey.append(int(dr["ey"]) & 0xFF)

    L = []
    L.append("-- %s.mos -- GENERATED by mosaik_scenes.py; do not edit by hand." % module)
    L.append("-- Edit the world TOML and re-run the transpiler (game-framework")
    L.append("-- Layer 3 / Phase 4). The game imports this module and composes the")
    L.append("-- framework (game.pad / game.camera / game.collision) around it.")
    L.append("")
    L.append('module "%s" {' % module)
    L.append('    import "graphics.bkg"')
    L.append("    const TILE_COUNT: u8 = %d" % tile_count)
    L.append("    const MAP_W: u8 = %d" % map_w)
    L.append("    const MAP_H: u8 = %d" % map_h)
    L.append("    const SCENE_COUNT: u8 = %d" % len(scenes))
    L.append("")
    # Object-kind ids as named constants, so games compare OBJ_KIND[i] == KIND_*.
    for name, kid in kinds.items():
        L.append("    const KIND_%s: u8 = %d" % (_ident(name), int(kid) & 0xFF))
    if kinds:
        L.append("")
    _emit_array(L, "u8", "TILESET", tile_count * 16, list(tiles))
    L.append("")
    # Per-scene maps.
    map_names = []
    for i, sc in enumerate(scenes):
        nm = _ident(sc.get("name", "scene%d" % i)) + "_MAP"
        map_names.append(nm)
        flat = _flatten_map(sc.get("map", []), map_w, map_h, sc.get("name"))
        _emit_array(L, "u8", nm, map_w * map_h, flat, per_line=map_w)
        L.append("")
    # The map selector (arrays can't be passed by value -> pick by scene id).
    L.append("    -- Tile at flat index `idx` of `scene` (picks the map by id).")
    L.append("    function map_tile(scene: u8, idx: u16) -> u8 {")
    for i, nm in enumerate(map_names):
        L.append("        if scene == %d {" % i)
        L.append("            return %s[idx]" % nm)
        L.append("        }")
    L.append("        return 0")
    L.append("    }")
    L.append("")
    # Upload a scene's whole map to the background (one set_tiles per room load).
    L.append("    -- Paint `scene`'s map to the background (call on room load,")
    L.append("    -- after bkg.set_data(0, TILE_COUNT, TILESET)).")
    L.append("    function paint(scene: u8) {")
    for i, nm in enumerate(map_names):
        L.append("        if scene == %d {" % i)
        L.append("            bkg.set_tiles(0, 0, MAP_W, MAP_H, %s)" % nm)
        L.append("        }")
    L.append("    }")
    L.append("")

    # Object table (size >= 1 so the array type is always valid; OBJ_COUNT is the
    # real length the game loops over).
    def pad(vals):
        return vals if vals else [0]
    n_obj = len(obj_scene)
    L.append("    const OBJ_COUNT: u8 = %d" % n_obj)
    _emit_array(L, "u8", "OBJ_SCENE", len(pad(obj_scene)), pad(obj_scene))
    _emit_array(L, "u8", "OBJ_KIND", len(pad(obj_kind)), pad(obj_kind))
    _emit_array(L, "u8", "OBJ_X", len(pad(obj_x)), pad(obj_x))
    _emit_array(L, "u8", "OBJ_Y", len(pad(obj_y)), pad(obj_y))
    L.append("")

    # Door table: (from-scene, trigger cell) -> (to-scene, entry pixel).
    n_door = len(d_from)
    L.append("    const DOOR_COUNT: u8 = %d" % n_door)
    _emit_array(L, "u8", "DOOR_FROM", len(pad(d_from)), pad(d_from))
    _emit_array(L, "u8", "DOOR_TX", len(pad(d_tx)), pad(d_tx))
    _emit_array(L, "u8", "DOOR_TY", len(pad(d_ty)), pad(d_ty))
    _emit_array(L, "u8", "DOOR_TO", len(pad(d_to)), pad(d_to))
    _emit_array(L, "u8", "DOOR_EX", len(pad(d_ex)), pad(d_ex))
    _emit_array(L, "u8", "DOOR_EY", len(pad(d_ey)), pad(d_ey))
    L.append("")

    exports = (["TILE_COUNT", "MAP_W", "MAP_H", "SCENE_COUNT", "TILESET",
                "map_tile", "paint"]
               + ["KIND_%s" % _ident(n) for n in kinds]
               + map_names
               + ["OBJ_COUNT", "OBJ_SCENE", "OBJ_KIND", "OBJ_X", "OBJ_Y",
                  "DOOR_COUNT", "DOOR_FROM", "DOOR_TX", "DOOR_TY", "DOOR_TO",
                  "DOOR_EX", "DOOR_EY"])
    L.append("    export " + ", ".join(exports))
    L.append("}")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Transpile a world TOML to a mosaik scenes module.")
    ap.add_argument("world", help="the world .toml")
    ap.add_argument("-o", "--output", help="output .mos (default: stdout)")
    args = ap.parse_args(argv)
    try:
        world = _load_toml(args.world)
        src = transpile(world, os.path.dirname(os.path.abspath(args.world)))
    except (SceneError, KeyError, FileNotFoundError) as e:
        print("scene transpile error: %s" % e, file=sys.stderr)
        return 1
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(src)
        print("wrote %s (%d scenes)" % (args.output, len(world.get("scene", []))))
    else:
        sys.stdout.write(src)
    return 0


if __name__ == "__main__":
    sys.exit(main())
