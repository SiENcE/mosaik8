"""mosaik_scenes.autotile - a scene's AUTO-TILE rule, and baking it into the map.

A scene may carry `[scene.autotile]`: the author paints a LOGICAL map (say 0
sea, 1 ground) and the rule says how a material cell LOOKS - the edge tile its
four neighbours pick, a variant on some fully surrounded cells, a picture
behind the rest. Editors draw through it; with `bake = true` the transpiler
also WRITES the picked tiles into the map, so a game whose runtime does not
auto-tile shows the same picture.

    [scene.autotile]
    material = [1, 2]                  # the cell values that are the material
    tiles = "tiles/edges.png"          # 16 edge tiles first, then any variants
    variants = { first = 16, count = 4, chance = 4, hash = [7, 13] }
    other = "tiles/water.png"          # what a non-material cell shows (tiled)
    row_from = "bottom"                # what the hash counts rows from ("top")
    edge_x = "extend"                  # beyond the map: the edge cell itself ...
    edge_y = "other"                   # ... or not material, or "wrap" (a loop)
    bake = true                        # write the tiles into the built map

The rule:

- a material cell shows tile `m` of the `tiles` sheet, `m` = the bits of its
  material neighbours (1 N, 2 E, 4 S, 8 W);
- a fully surrounded cell (`m == 15`) shows `first + h % count` when
  `h = (x * hash[0] + row * hash[1]) & 15` is below `chance`;
- `row` counts from the scene's top or its bottom (`row_from`), and the
  `other` picture is anchored the same way (its bottom row on the scene's
  bottom row for "bottom");
- beyond the map a neighbour is the edge cell itself ("extend", the default),
  not material ("other"), or the cell on the far side ("wrap": a map that
  loops, like an endless shmup background), per axis.

A sheet's tiles are numbered in the ENGINE's sprite order: a `.sprites.toml`
manifest's order, each sprite row-major, or column-major under `[build]
obj_8x16` (`mosaik_assets.reorder_tiles_8x16`); a plain picture row-major.

BAKING maps each picked tile to its index in the scene's TILESET: the
scene's own `tileset`, else the world's `[tileset] png(s)` concatenated. A
`tiles` / `other` picture the tileset LISTS maps to its own place there (its
image's first index + its row-major place); one it does not list maps to the
first tileset tile with the SAME PIXELS, so a scene whose tileset is one
combined image of that art (a per-scene tileset is a single picture) bakes
too, and a tile found nowhere is an error that names it. Nothing is composed
or copied: the tile data, the per-tile palettes (`tile_palette`) and the
metatile table see an ordinary map of real tile ids. `[collision] solid`
then matches BAKED ids; a painted `collision` layer is unaffected.
A world with no baked rule is untouched (the dict passes through).
"""
import copy
import os

TILE = 8
EDGES = ("extend", "other", "wrap")
ROW_FROM = ("top", "bottom")


class RuleError(ValueError):
    """A `[scene.autotile]` table that cannot be applied."""


class Rule(object):
    """One scene's auto-tile rule, validated. Paths stay as written (relative
    to the world's base dir); `resolve` never touches the disk."""

    def __init__(self, material, tiles, first=16, count=0, chance=0,
                 hash_=(7, 13), other=None, row_from="top", edge_x="extend",
                 edge_y="extend", bake=False):
        self.material = frozenset(int(m) for m in material)
        self.tiles = tiles
        self.first = int(first)
        self.count = int(count)
        self.chance = int(chance)
        self.hash = (int(hash_[0]), int(hash_[1]))
        self.other = other or None
        self.row_from = row_from
        self.edge_x = edge_x
        self.edge_y = edge_y
        self.bake = bool(bake)

    # The messages are English here; the studio translates them by TEXT, so
    # it keeps the same literals in a marked table (its autotile.py), and its
    # test pins that every one of these is there.
    @classmethod
    def from_dict(cls, d):
        """The rule of a `[scene.autotile]` dict; RuleError says what is wrong."""
        if not isinstance(d, dict):
            raise RuleError("[scene.autotile] must be a table")
        mat = d.get("material")
        if not isinstance(mat, list) or not mat \
                or not all(isinstance(m, int) and 0 <= m < 256 for m in mat):
            raise RuleError("material must list the cell values (0..255) that are the material")
        tiles = d.get("tiles")
        if not isinstance(tiles, str) or not tiles:
            raise RuleError("tiles must name the sheet whose first 16 tiles are the edges")
        v = d.get("variants") or {}
        if not isinstance(v, dict):
            raise RuleError("variants must be a table")
        first, count, chance = v.get("first", 16), v.get("count", 0), v.get("chance", 0)
        hash_ = v.get("hash", [7, 13])
        if not all(isinstance(n, int) for n in (first, count, chance)) or first < 0:
            raise RuleError("variants: first, count and chance must be whole numbers")
        if count < 0 or not 0 <= chance <= 16:
            raise RuleError("variants: count must be >= 0 and chance 0..16 (out of 16)")
        if chance and not count:
            raise RuleError("variants: a chance needs a count of variant tiles")
        if not (isinstance(hash_, list) and len(hash_) == 2
                and all(isinstance(n, int) for n in hash_)):
            raise RuleError("variants: hash must be two whole numbers [x, row]")
        other = d.get("other")
        if other is not None and (not isinstance(other, str) or not other):
            raise RuleError("other must name a picture or be left out")
        row_from = d.get("row_from", "top")
        if row_from not in ROW_FROM:
            raise RuleError("row_from must be \"top\" or \"bottom\"")
        edge_x, edge_y = d.get("edge_x", "extend"), d.get("edge_y", "extend")
        if edge_x not in EDGES or edge_y not in EDGES:
            raise RuleError("edge_x / edge_y must be \"extend\", \"other\" or \"wrap\"")
        bake = d.get("bake", False)
        if not isinstance(bake, bool):
            raise RuleError("bake must be true or false")
        return cls(mat, tiles, first, count, chance, hash_, other, row_from,
                   edge_x, edge_y, bake)

    def to_dict(self):
        """The `[scene.autotile]` dict, defaults left out (the inverse of from_dict)."""
        d = {"material": sorted(self.material), "tiles": self.tiles}
        if self.count or self.chance:
            d["variants"] = {"first": self.first, "count": self.count,
                             "chance": self.chance, "hash": list(self.hash)}
        if self.other:
            d["other"] = self.other
        if self.row_from != "top":
            d["row_from"] = self.row_from
        if self.edge_x != "extend":
            d["edge_x"] = self.edge_x
        if self.edge_y != "extend":
            d["edge_y"] = self.edge_y
        if self.bake:
            d["bake"] = True
        return d

    # -- the rule ---------------------------------------------------------
    def _is(self, cells, w, h, x, y, cx, cy):
        if x < 0 or x >= w:
            if self.edge_x == "other":
                return 0
            x = x % w if self.edge_x == "wrap" else cx
        if y < 0 or y >= h:
            if self.edge_y == "other":
                return 0
            y = y % h if self.edge_y == "wrap" else cy
        return 1 if cells[y * w + x] in self.material else 0

    def mask(self, cells, w, h, x, y):
        """The 4-neighbour mask of material cell (x, y): 1 N, 2 E, 4 S, 8 W."""
        return (self._is(cells, w, h, x, y - 1, x, y)
                | self._is(cells, w, h, x + 1, y, x, y) << 1
                | self._is(cells, w, h, x, y + 1, x, y) << 2
                | self._is(cells, w, h, x - 1, y, x, y) << 3)

    def row(self, y, h):
        """The row number the hash and `other` count: from the top or the bottom."""
        return h - 1 - y if self.row_from == "bottom" else y

    def resolve(self, cells, w, h, x, y):
        """What cell (x, y) of a `w` x `h` flat map shows: ("tile", n) = tile n
        of the sheet, ("other", r) = the `other` picture's tile row r (the
        column is x), or None = the cell's own tile (no `other` picture)."""
        if cells[y * w + x] in self.material:
            m = self.mask(cells, w, h, x, y)
            if m == 15 and self.chance:
                hv = (x * self.hash[0] + self.row(y, h) * self.hash[1]) & 15
                if hv < self.chance:
                    return ("tile", self.first + hv % self.count)
            return ("tile", m)
        if self.other:
            return ("other", self.row(y, h))
        return None

    def other_tile(self, x, row, cols, rows):
        """Which tile (column, row) of the `other` picture (`cols` x `rows`
        tiles) a non-material cell in column `x` and rule row `row` shows."""
        cols, rows = max(1, cols), max(1, rows)
        ty = row % rows
        if self.row_from == "bottom":
            ty = rows - 1 - ty
        return (x % cols, ty)

    def cells_to_redraw(self, w, h, x, y):
        """The cells a paint of (x, y) can change: it and its four neighbours."""
        out = [(x, y)]
        for nx, ny in ((x, y - 1), (x + 1, y), (x, y + 1), (x - 1, y)):
            if 0 <= nx < w and 0 <= ny < h:
                out.append((nx, ny))
        return out


def sheet_tile_origins(png_path, width, height, obj16=False):
    """The (x, y) pixel origin of every 8x8 tile of a sheet, in the ENGINE's
    sprite order (manifest order; row-major per sprite, column-major under
    obj_8x16; a plain picture row-major). A manifest rect that is not a
    multiple of 8 or leaves the picture is skipped."""
    from mosaik_assets import load_sprite_manifest
    try:
        manifest = load_sprite_manifest(png_path)
    except Exception:  # noqa: BLE001 - a broken manifest reads as a plain picture
        manifest = None
    out = []
    if not manifest:
        for ty in range(height // TILE):
            for tx in range(width // TILE):
                out.append((tx * TILE, ty * TILE))
        return out
    for _name, x, y, w, h in manifest:
        if w % TILE or h % TILE or x + w > width or y + h > height:
            continue
        tw, th = w // TILE, h // TILE
        if obj16 and th % 2 == 0:
            order = [(c, r) for c in range(tw) for r in range(th)]
        else:
            order = [(c, r) for r in range(th) for c in range(tw)]
        out.extend((x + c * TILE, y + r * TILE) for c, r in order)
    return out


def _project_obj16(base_dir):
    """`[build] obj_8x16` of the project around a world (mosaik.toml at most
    two levels up, as `context._project_targets_smsgg` looks)."""
    d = os.path.abspath(base_dir or ".")
    for _ in range(3):
        mp = os.path.join(d, "mosaik.toml")
        if os.path.isfile(mp):
            try:
                from .loaders import _load_toml
                return bool((_load_toml(mp).get("build", {}) or {}).get("obj_8x16"))
            except Exception:  # noqa: BLE001
                return False
        d = os.path.dirname(d)
    return False


def _png_size(path):
    from mosaik_assets import png_to_shades
    w, h, _rows = png_to_shades(path)
    return w, h


def _norm(rel):
    return os.path.normpath(rel).replace("\\", "/").lower()


def bake_world(world, base_dir):
    """The world with every `bake = true` scene's map resolved to real tile
    ids (a copy; the input is not touched). A world with no baked rule is
    returned AS IS. Raises SceneError for a rule that cannot be baked."""
    from .base import SceneError, _flatten_map
    scenes = world.get("scene", []) or []
    todo = [i for i, sc in enumerate(scenes)
            if isinstance(sc.get("autotile"), dict) and sc["autotile"].get("bake")]
    if not todo:
        return world
    w = world.get("world", {}) or {}
    map_w, map_h = int(w.get("map_w", 32)), int(w.get("map_h", 32))
    ts = world.get("tileset", {}) or {}
    world_pngs = ts.get("pngs") or ([ts["png"]] if "png" in ts else [])
    obj16 = _project_obj16(base_dir)
    sizes = {}

    tiles_cache = {}

    def tiles_of(rel):
        """A picture's 2bpp tile bytes, row-major (the tileset's own reading)."""
        if rel not in tiles_cache:
            from mosaik_assets import png_to_gb_tiles
            try:
                tiles_cache[rel] = bytes(png_to_gb_tiles(os.path.join(base_dir, rel)))
            except Exception as e:  # noqa: BLE001
                raise SceneError("autotile: cannot read %r: %s" % (rel, e))
        return tiles_cache[rel]

    def size(rel):
        if rel not in sizes:
            try:
                sizes[rel] = _png_size(os.path.join(base_dir, rel))
            except Exception as e:  # noqa: BLE001
                raise SceneError("autotile: cannot read %r: %s" % (rel, e))
        return sizes[rel]

    out = dict(world)
    out["scene"] = list(scenes)
    for i in todo:
        sc = scenes[i]
        name = sc.get("name", "#%d" % i)
        try:
            rule = Rule.from_dict(sc["autotile"])
        except RuleError as e:
            raise SceneError("scene '%s' [scene.autotile]: %s" % (name, e))
        pngs = [sc["tileset"]] if sc.get("tileset") else list(world_pngs)
        base, at = {}, 0
        for rel in pngs:
            pw, ph = size(rel)
            base.setdefault(_norm(rel), (at, pw // TILE))
            at += (pw // TILE) * (ph // TILE)

        content = {}

        def by_content():
            """tile bytes -> the FIRST id in the scene's tileset with them."""
            if not content:
                at2 = 0
                for rel in pngs:
                    data = tiles_of(rel)
                    for k in range(len(data) // 16):
                        content.setdefault(data[k * 16:(k + 1) * 16], at2 + k)
                    at2 += len(data) // 16
            return content

        def locator(rel, what):
            """(col, row) of a tile of picture `rel` -> its id in the scene's
            tileset: the picture's own place when the tileset lists it, else
            the first tileset tile with the SAME PIXELS (a scene whose tileset
            is one combined image of the art the rule names)."""
            got = base.get(_norm(rel))
            if got is not None:
                b, cols = got
                return lambda c, r: b + r * cols + c
            pw, _ph = size(rel)
            data, cols = tiles_of(rel), pw // TILE

            def find(c, r):
                k = r * cols + c
                tid = by_content().get(data[k * 16:(k + 1) * 16])
                if tid is None:
                    raise SceneError(
                        "scene '%s' [scene.autotile] bake: tile (%d, %d) of the %s "
                        "picture %r is not in the scene's tileset (%s) - list the "
                        "picture there, or a tileset with the same tile"
                        % (name, c, r, what, rel, ", ".join(pngs) or "none"))
                return tid
            return find
        t_at = locator(rule.tiles, "tiles")
        tw, th = size(rule.tiles)
        origins = sheet_tile_origins(os.path.join(base_dir, rule.tiles), tw, th, obj16)
        if rule.other:
            o_at = locator(rule.other, "other")
            ow, oh = size(rule.other)
            o_cols, o_rows = ow // TILE, oh // TILE
        sw = int(sc.get("map_w", 0)) or map_w
        sh = int(sc.get("map_h", 0)) or map_h
        cells = _flatten_map(sc.get("map", []), sw, sh, name)
        baked = list(cells)
        for y in range(sh):
            for x in range(sw):
                got = rule.resolve(cells, sw, sh, x, y)
                if got is None:
                    continue
                if got[0] == "tile":
                    n = got[1]
                    if n >= len(origins):
                        raise SceneError(
                            "scene '%s' [scene.autotile] bake: tile %d is past the end "
                            "of %r (%d tiles)" % (name, n, rule.tiles, len(origins)))
                    ox, oy = origins[n]
                    baked[y * sw + x] = t_at(ox // TILE, oy // TILE)
                else:
                    cx, cy = rule.other_tile(x, got[1], o_cols, o_rows)
                    baked[y * sw + x] = o_at(cx, cy)
        if any(v > 255 for v in baked):
            raise SceneError("scene '%s' [scene.autotile] bake: a tile id passes 255"
                             % name)
        nsc = copy.copy(sc)
        nsc["map"] = [baked[r * sw:(r + 1) * sw] for r in range(sh)]
        out["scene"][i] = nsc
    return out
