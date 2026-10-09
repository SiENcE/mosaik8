"""mosaik_scenes.transpile.context - the world dict -> everything the
emitters need: the tilesets, the flattened maps, the collision model,
the metatile table, the palettes and the entity tables.

`transpile()` used to open with ~750 lines of analysis and then emit
1,400 more against the ~170 locals it left behind. The analysis is
unchanged and lives here; `analyse()` captures its locals into a plain
namespace, which is what keeps the code above exactly what it was
rather than rewritten into 170 `self.x =` lines.

A few of the captured names are CALLABLES closing over that analysis
(`scene_id`, `_check_u8_count`, `_pal_set`, `_dim_type`) - they are part
of the context in the same way, and the emitters call them off it."""
import os

from mosaik_assets import (png_to_gb_tiles, png_to_4bpp_bkg_tiles,
                           png_palette_bkg16, bkg_build_is_4bpp,
                           rgb555_words, fit_palettes, AssetError)
from mosaik.platforms import BKG_4BPP_ENGINE
from ..base import SceneError, _flatten_map
from ..filtering import _entity_platforms
from .shared import PAL_SLOTS, _MAP_CHUNK, _rgb888
from ..palette_fold import (scene_palette_rows, bkg_fold, spr_fold,
                            targets_fold)


def _project_targets_smsgg(base_dir):
    """Whether the project owning a world at `base_dir` targets the SMS or
    Game Gear - `mosaik.toml [project] target_platforms`, the same fact
    `mosaik_vm.rooms.config._targets_smsgg` reads.

    A world sits at the project root, in `assets/`, or split into `world/` /
    `assets/world/`, so `mosaik.toml` is at most two levels up. A world with no
    project around it (a bare `python -m mosaik_scenes`) answers False, which is
    the GB-family behaviour every such world had before."""
    d = os.path.abspath(base_dir or ".")
    for _ in range(3):
        mp = os.path.join(d, "mosaik.toml")
        if os.path.isfile(mp):
            try:
                from ..loaders import _load_toml
                proj = _load_toml(mp).get("project", {}) or {}
            except Exception:  # noqa: BLE001
                return False
            return bool({"sms", "gamegear"}
                        & {str(p).strip().lower() for p in
                           (proj.get("target_platforms") or [])})
        d = os.path.dirname(d)
    return False


class SceneCtx(object):
    """The analysed world: an attribute per name `analyse()` computes.

    Carries no behaviour of its own. Emitters also PUBLISH onto it what a
    later section needs (`wt`/`ht`, the map/collision symbol lists, the
    tileset upload helpers) - each such hand-off is commented where it
    happens."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


def analyse(world, base_dir, _mark=False, _force=None):
    """The world dict -> a SceneCtx. Raises SceneError on a bad world."""
    # BAKED AUTO-TILE rules (`[scene.autotile] bake = true`, ../autotile.py):
    # the logical map cells become the real tile ids the rule picks, before
    # anything below reads a map, so the tile data, colour, metatiles and
    # streaming see an ordinary map. A world without one passes through AS IS
    # (the same dict), so it stays byte-identical.
    from ..autotile import bake_world
    world = bake_world(world, base_dir)
    w = world.get("world", {})
    module = w.get("module", "scenes")
    map_w = int(w.get("map_w", 32))
    map_h = int(w.get("map_h", 32))

    # Optional asset-streaming opt-in ([world] stream): route each per-scene map
    # upload in paint() through the asset-residency seam (assets.use makes it
    # resident, assets.ptr hands the pointer to the setter) so the Lynx can load
    # it from the cart per room instead of holding every scene resident. Additive
    # and OFF by default -- a world without `stream` is byte-identical (the same
    # rule as `pack_tiles` / `[[animated_tile]]`); and ON it is byte-identical in
    # the GENERATED C too (the seam lowers to a no-op + the const pointer on every
    # console today, the Stage A.2 transparency), until the Lynx streaming
    # lowering (Stage B) swaps in the cart loader. The shared tileset stays
    # resident (referenced by every scene) -- only the per-scene maps stream.
    stream = bool(w.get("stream"))

    # Item 33 -- the ONE generic paint interpreter ([world] paint_table): today
    # map_tile / collision_at / paint each dispatch PER SCENE (`if scene == i {
    # return SCENE_i_MAP[idx] }` x N), ~140 B of resident CODE per scene in the
    # Lynx MAIN / the GB home bank, so the room count is capped by resident code
    # even when the map DATA already streams/banks. With paint_table on, every
    # scene's map is CONCATENATED into one flat MAPS array + a per-scene start
    # offset table MAP_OFF[]: map_tile/collision_at become an O(1) indexed read
    # and paint() is one loop over the descriptor (dims + offset), so adding a
    # room adds table ROWS, not code. Additive + OFF by default -- a world without
    # `paint_table` is byte-identical (the same rule as `stream` / `pack_tiles`).
    # Composes with `stream`: paint_table concatenates the maps into ONE resident
    # MAPS array (byte-identical off), and when `stream` is ALSO set the seam
    # streams the CURRENT room's WINDOW of that array on the Lynx (a slot sized to
    # the widest scene) / banks it on the GB family -- so the room count stops
    # growing BOTH resident code (the O(1) interpreter) AND resident data
    # (streaming) at once. The range-windowed seam (assets.range_base/use_range/
    # ptr_range/range_byte) is what carries it (item 33 stream-compose).
    paint_table = bool(w.get("paint_table"))

    # Item 32 -- METATILE map compression ([world] metatiles). Classic consoles
    # never stored maps at 8x8 granularity: every 2x2 block of tiles is
    # content-deduplicated into ONE shared table (4 bytes per distinct metatile)
    # and each scene's map becomes a (w/2)x(h/2) array of u8 metatile ids, so map
    # bytes drop to a QUARTER (a 32x28 scene: 896 B -> 224 B) against a one-time
    # table cost. The editor keeps painting 8x8 tiles -- this is derived at
    # transpile, never re-authored -- and `map_tile(scene, idx)` / `paint(scene)`
    # keep their exact signatures, resolving through the table, so every caller
    # (rooms.mos, vm.player, engine.scroll, the collision helpers) is unchanged.
    # Off = byte-identical (the same additive rule as stream / paint_table).
    metatiles = bool(w.get("metatiles"))

    # One tileset (`png`) or several merged into one combined tile table
    # (`pngs = [...]`, concatenated in load order so image k's tiles occupy a
    # contiguous index range after the earlier images). The studio's multi-image
    # tilesets and Tiled `.tmx` import (whose maps reference several tilesets by
    # firstgid) both lower to this one combined table.
    ts = world.get("tileset", {})
    pngs = ts.get("pngs") or ([ts["png"]] if "png" in ts else None)
    if not pngs:
        raise SceneError("[tileset] needs a `png` or `pngs` path")
    tiles = []
    for rel in pngs:
        try:
            tiles += list(png_to_gb_tiles(os.path.join(base_dir, rel)))
        except AssetError as e:
            raise SceneError(str(e))
    tile_count = len(tiles) // 16

    kinds = world.get("kinds", {})            # name -> id

    scenes = world.get("scene", [])
    if not scenes:
        raise SceneError("a world needs at least one [[scene]]")

    # Per-scene tilesets (Gap 3): a scene may declare
    # its OWN tileset image (`[[scene]] tileset = "title.png"`), uploaded on room
    # load (paint) so a full-screen IMAGE scene (a title / menu, ~180 tiles) needn't
    # share the gameplay tileset's <=255-tile / <=132-tile-font budget. Scenes
    # WITHOUT an override keep the shared [tileset]. Additive: a world where no scene
    # sets `tileset` is byte-identical (this whole block is skipped, and paint() gains
    # no tileset upload). `scene_tiles[i]` = (tiles, count) for an override scene, else
    # None (uses the shared TILESET). Each scene's MAP indexes into ITS resolved
    # tileset (as authored), so no remapping is needed.
    scene_ts_rel = [sc.get("tileset") for sc in scenes]
    per_scene_ts = any(scene_ts_rel)
    scene_tiles = None
    if per_scene_ts:
        # [world] stream + paint_table DO combine: the per-scene tileset stays RESIDENT
        # (uploaded by paint() before the map) while the maps stream / concatenate as
        # usual. pack_tiles / [[animated_tile]] can't yet -- they REMAP or ANIMATE tile
        # indices in the ONE shared tileset (packing would corrupt an override scene's
        # own-tileset map indices; an animated tile swaps data an override scene's
        # upload clobbers), so a per-scene tileset with them is a clear error, not a
        # silent bug. (The image-title use case needs neither.)
        if world.get("animated_tile") or w.get("pack_tiles"):
            raise SceneError(
                "a per-scene [[scene]] tileset can't yet combine with [world] "
                "pack_tiles / [[animated_tile]] (they remap / animate indices in the "
                "one shared tileset); [world] stream / paint_table DO combine")
        scene_tiles = []
        for i, rel in enumerate(scene_ts_rel):
            if not rel:
                scene_tiles.append(None)
                continue
            try:
                st = list(png_to_gb_tiles(os.path.join(base_dir, rel)))
            except AssetError as e:
                raise SceneError(str(e))
            stc = len(st) // 16
            if stc > 255:
                raise SceneError(
                    "scene '%s' tileset '%s' has %d tiles; the u8 upload count "
                    "caps it at 255" % (scenes[i].get("name"), rel, stc))
            scene_tiles.append((st, stc))

    # PER-SCENE background tile animation (`[[scene.animated_tile]]`). The
    # world-global `[[animated_tile]]` animates the ONE shared tileset, which
    # is why it is refused above with per-scene tilesets; a per-SCENE entry
    # belongs to the scene whose tileset is loaded, so it combines with
    # per-scene tilesets / stream / paint_table freely.
    #
    # Its frames come from their OWN image (`png`, the reference engine's model - it keeps
    # each animation in a separate tileset asset), read here at transpile time
    # and BAKED into the module by the emitter. Nothing is added to the scene's
    # uploaded tileset, so an animation costs no background VRAM at all.
    scene_anims = []
    anim_pngs = {}
    for i, sc in enumerate(scenes):
        rows = sc.get("animated_tile") or []
        if not rows:
            scene_anims.append([])
            continue
        out = []
        for a in rows:
            rel = a.get("png")
            src = None
            if rel:
                if rel not in anim_pngs:
                    try:
                        anim_pngs[rel] = list(png_to_gb_tiles(
                            os.path.join(base_dir, rel)))
                    except AssetError as e:
                        raise SceneError(str(e))
                src = anim_pngs[rel]
            elif scene_tiles and scene_tiles[i]:
                src = scene_tiles[i][0]        # frames index the scene tileset
            else:
                src = None                     # ...or the shared one
            out.append((a, src))
        scene_anims.append(out)

    # THE REPLACEMENT TILE BANK (`[[replace_tile]]`): art a SCRIPT may write
    # into a background tile, the reference engine's EVENT_REPLACE_TILE_XY. Read here and
    # baked by the emitter, exactly like an animation's frames - nothing
    # uploads it, so it costs no background VRAM. The bank is the entries
    # CONCATENATED in world order and the VM's `src` operand indexes the whole
    # thing, so whoever authors a write (the reference-engine importer, or the studio)
    # resolves its own tileset's base once and the runtime needs no table.
    repl_tiles = bytearray()
    repl_banks = []
    for r in (world.get("replace_tile") or []):
        rel = r.get("png")
        if not rel:
            raise SceneError("[[replace_tile]] %r needs a `png`"
                             % (r.get("name") or "?"))
        try:
            got = list(png_to_gb_tiles(os.path.join(base_dir, rel)))
        except AssetError as e:
            raise SceneError(str(e))
        repl_banks.append((r.get("name") or rel, len(repl_tiles) // 16,
                           len(got) // 16))
        repl_tiles += bytes(got)
    if len(repl_tiles) // 16 > 255:
        raise SceneError("the [[replace_tile]] bank holds %d tiles; the u8 "
                         "`src` operand caps it at 255" % (len(repl_tiles) // 16))

    # 4bpp BACKGROUND colour tier.
    # When the shared tileset PNG(s) carry >4 colours AND the engine has a 4bpp
    # bkg backend (mosaik.platforms.BKG_4BPP_ENGINE), emit the TILESET forked by
    # `if platform`: the 4bpp consoles get native 16-colour packed-nibble tiles
    # (32 B/tile), everyone else keeps the 2bpp tiles. `bkg_pal16` (the tileset's
    # authored 16-colour palette, packed 5-5-5) is emitted unconditionally as
    # BKG_PALETTE16 so `palette.load_bkg16` always resolves (one portable source,
    # the sprite <name>_palette16 mirror). While BKG_4BPP_ENGINE is empty (Stage
    # 0) this whole tier is DORMANT and every world stays byte-identical.
    #
    # The LYNX joins the 4bpp fork only when the world opts in with
    # `[world] lynx_bkg16` -- its no-tilemap Suzy strip engine doubles its RAM at
    # 4bpp (~13 KB -> ~26 KB), so it is a per-project choice against the tight Lynx
    # MAIN budget (default off = the byte-identical 2bpp grey down-tier). The
    # generator detects the depth from the emitted TILESET and widens the engine.
    bkg4_engines = set(BKG_4BPP_ENGINE)
    if w.get("lynx_bkg16"):
        bkg4_engines.add("lynx")
    bkg4_tiles = None
    bkg_pal16 = None
    if bkg4_engines and bkg_build_is_4bpp(
            [os.path.join(base_dir, rel) for rel in pngs]):
        # These features REMAP / ANIMATE / STREAM the tileset bytes and don't yet
        # understand 4bpp; refuse the combination with a clear error rather than
        # emit something subtly wrong (the plain single-tileset case is the target).
        if stream or paint_table or per_scene_ts or world.get("animated_tile") \
                or w.get("pack_tiles"):
            raise SceneError(
                "a >4-colour (4bpp) background tileset can't yet combine with "
                "[world] stream / paint_table / per-scene [[scene]] tilesets / "
                "[[animated_tile]] / [world] pack_tiles")
        t4 = []
        for rel in pngs:
            t4 += list(png_to_4bpp_bkg_tiles(os.path.join(base_dir, rel)))
        bkg4_tiles = t4
        bkg_pal16 = rgb555_words(png_palette_bkg16(os.path.join(base_dir, pngs[0])))
        # The fork's 2bpp DOWN-TIER arm quantizes by luma RANK (quartiles over
        # the palette), not the absolute thresholds: a mid-range 16-colour
        # palette would otherwise crush to ONE shade -- a flat, unreadable GB /
        # GBC / NES image. Only the >4-colour fork changes; a <=4-colour world
        # keeps the literal-index path, byte-identical.
        from mosaik_assets import png_to_gb_tiles_ranked
        tiles = []
        for rel in pngs:
            tiles += list(png_to_gb_tiles_ranked(os.path.join(base_dir, rel)))

    # Per-scene map sizes: each scene may override the
    # world-level map_w/map_h -- a title / menu room needn't be a full scrolling
    # room, so it can be screen-sized and cost far less const data. The
    # world-level size is the default, so a UNIFORM world (every scene the world
    # size, the common case) stays byte-identical: it emits the single
    # MAP_W/MAP_H consts, fixed-length arrays, and the MAP_W/MAP_H paint()
    # exactly as before. Only a world with a differently-sized scene emits the
    # per-scene SCENE_W[]/SCENE_H[] tables + variable-length arrays (additive,
    # the same rule as `pack_tiles` / the collision layer / animated tiles).
    scene_w = [int(sc.get("map_w", map_w)) for sc in scenes]
    scene_h = [int(sc.get("map_h", map_h)) for sc in scenes]
    uniform = all(sw == map_w and sh == map_h
                  for sw, sh in zip(scene_w, scene_h))
    if _force is not None:
        # A forked build stubs some scenes to 1x1, so it is non-uniform; force EVERY
        # branch non-uniform (emit SCENE_W/SCENE_H) so the export list matches.
        uniform = _force["uniform"]

    # Item 33: per-scene start offsets into the concatenated MAPS/COLLISION
    # tables (paint_table). Each scene's map occupies `scene_w*scene_h` cells
    # laid out row-major, so scene i starts at the running sum. map_tile /
    # collision_at then index `MAPS[MAP_OFF[scene] + idx]` (O(1), no dispatch),
    # and paint() strides one row at a time from MAP_OFF[scene]. The offset is a
    # u16, so the whole world's map data caps at 65535 cells resident (a world
    # that big wants [world] stream, not paint_table).
    map_off = []
    _acc = 0
    for sw, sh in zip(scene_w, scene_h):
        map_off.append(_acc)
        _acc += sw * sh
    if paint_table and _acc > 0xFFFF:
        raise SceneError("[world] paint_table concatenates %d map cells; the u16 "
                         "offset caps it at 65535 cells resident (use [world] "
                         "stream for a world this large)" % _acc)
    # CHUNKED at a ROM bank, the same rule the per-scene tilesets (_TS_CHUNK)
    # and the song CELLS blob (songs.CELL_CHUNK) already follow: a const array
    # is read in place while its ONE bank is mapped, so no single symbol may
    # cross one (16 KB on the GB, the tightest per-symbol ceiling of any
    # target). The concatenation reached it as soon as the reference-engine sample
    # kept its 255-wide SHMUP room whole - 14,274 cells to 18,288 - and the
    # symptom was not a link error but the importer silently DROPPING
    # `paint_table`, which put the per-scene chains back in the resident image
    # and cropped the room instead.
    #
    # So each scene's block lives WHOLE inside one chunk, MAP_BLK says which,
    # and MAP_OFF is the offset WITHIN it. MAPS and COLLISION split on the same
    # boundaries because their per-scene blocks are the same length (w*h), which
    # is what lets one MAP_BLK serve both and keeps COLLISION sharing MAP_OFF.
    # A world that fits one chunk (every world before this) emits exactly the
    # one MAPS symbol and no MAP_BLK at all - byte-identical.
    #
    # Metatiles are deliberately NOT chunked: that path quarters the map bytes
    # and keeps its own mt_off, while COLLISION stays full-cell on map_off, so
    # chunking one would silently mis-offset the other. It gets a clear error
    # below instead - it would take a ~65,000-cell world to reach.
    map_blk = [0] * len(scenes)
    map_chunks = 1
    if paint_table and not metatiles:
        _cells = [sw * sh for sw, sh in zip(scene_w, scene_h)]
        _big = [i for i, n in enumerate(_cells) if n > _MAP_CHUNK]
        if _big:
            raise SceneError(
                "[world] paint_table: scene '%s' is %d map cells, more than the "
                "%d-cell ROM bank a single const array may occupy - split the "
                "scene (its map cannot be chunked, only the concatenation can)"
                % (scenes[_big[0]].get("name", "scene%d" % _big[0]),
                   _cells[_big[0]], _MAP_CHUNK))
        map_off, _acc = [], 0
        for i, n in enumerate(_cells):
            if _acc and _acc + n > _MAP_CHUNK:
                map_chunks += 1
                _acc = 0
            map_blk[i] = map_chunks - 1
            map_off.append(_acc)
            _acc += n
    elif paint_table and metatiles and _acc > _MAP_CHUNK:
        raise SceneError(
            "[world] paint_table + metatiles concatenates %d collision cells, "
            "past the %d-cell ROM bank a single const array may occupy; the "
            "chunked non-metatile path handles this size, metatiles do not yet"
            % (_acc, _MAP_CHUNK))
    max_scene_w = max(scene_w)
    # A WIDE world (a scene wider than the 32-tile hardware background) can't use
    # the two together: with `paint_table` the map is read through the RANGE
    # window (`assets.range_byte`), and that window is warmed by `paint()` -- but
    # a wide level is never painted in one go. Its shell column-streams through
    # `engine.scroll`, reading arbitrary cells via `map_tile`, so the window
    # stays cold (an empty level) and, once tree-shaking drops the unused
    # `paint()`, the range cache is not even emitted (a link error on the Lynx).
    # `stream` ALONE is the supported wide combination: `map_tile` then indexes
    # the whole-asset cache, which loads on a miss.
    # ... which is why `stream + paint_table` on a wide world emits `warm(scene)`
    # (below): everything paint() does EXCEPT the map upload -- the per-scene
    # tileset and the range_base/use_range pair that warms the room's window.
    # The wide room's map is ONE contiguous range in the concatenated MAPS, so
    # warming it at room load is exactly what a painted room already does; the
    # shell then column-streams arbitrary cells through map_tile and every read
    # hits the warm window. `generate_rooms`' wide arm calls it where the narrow
    # arm calls paint(). This is what lets a big world keep paint_table AND go
    # wide: without it, dropping paint_table put the per-scene map_tile/paint
    # chains back in the GB RESIDENT image (+2,734 B on the 17-scene the reference engine
    # import, over bank 0), because a seam read lowers to SWITCH_ROM and
    # bank-switching code cannot itself be banked.
    # A room past the background on EITHER axis is never painted: a wide one
    # column-streams (engine.scroll), a wide/tall TOPDOWN one streams both axes
    # (engine.scroll2d, vm.player's ROAM mode). Both need the same warm().
    # KEEP THIS IN LOCKSTEP with generate_rooms' `warm_fn` - calling an
    # unemitted warm() is an unexported-symbol error, and skipping an emitted
    # one streams an empty level.
    # A room past the background needs warm() for a SECOND, independent reason,
    # and this one bites on every console: a PER-SCENE TILESET is uploaded by
    # paint(), so a streamed room that skips paint renders its map against
    # whatever tile DATA the previous room left in VRAM. Found 2026-08-15 on the
    # the platformer conversion (6 scenes, so paint_table stays off): its 20x36 title
    # screen drew the correct map through the LOGO room's tiles - 182 of 182
    # tiles wrong in VRAM, a clean link, and verify.py green. This is the latent
    # bug once reported as "stream + wide without paint_table
    # renders the wrong background tiles on SMS/GG"; it was never SMS/GG-specific
    # and never about stream.
    max_scene_h = max(scene_h)
    wide_world = bool(max_scene_w > 32 or max_scene_h > 32)
    # The RANGE half (paint_table's windowed reads) only applies with both keys;
    # the tileset half applies whenever a scene brings its own image.
    warm_ranges = bool(paint_table and stream)
    # ...and the ROW limit is generate_rooms' own: a project that targets the
    # SMS / Game Gear (32x28 name table) streams a 29..32-row room through
    # scroll2d, so rooms.mos calls warm() for it (mosaik_vm/rooms
    # BKG_ROWS_SMSGG). Deciding on 32 here generated a rooms.mos that could not
    # compile - `module "scenes" has no module-level symbol "warm"` - on EVERY
    # console of such a project (the adventure check project with sms/gamegear added,
    # 2026-09-17). Only the warm decision moves: `wide_world` gates other
    # refusals that stay at 32, as generate_rooms' own do.
    roam_rows = 28 if _project_targets_smsgg(base_dir) else 32
    warm_fn = bool((max_scene_w > 32 or max_scene_h > roam_rows)
                   and (per_scene_ts or warm_ranges))
    # The widest scene's cell count = the range cache's window (Lynx slot) size
    # and the concatenation's row-buffer bound (item 33).
    max_scene_cells = max(sw * sh for sw, sh in zip(scene_w, scene_h))

    # VM8 scene TYPE: the per-scene
    # NATIVE player-handler selector (topdown / platform / adventure / shmup /
    # logo / menu / pointnclick). Only a VM world ([world] vm = true) emits the SCENE_TYPE
    # table + SCTYPE_* consts; a composer / hand-written world never sets `vm`, so
    # every existing world is byte-identical (the same additive rule as
    # per-scene sizes / the collision layer / streaming). vm.player (Stage 2)
    # dispatches on SCENE_TYPE[room]. The richer per-actor defs (anim states /
    # move speed / collision box) + script-slot refs land with the actor model
    # they feed, not here.
    is_vm = bool(w.get("vm"))
    # APPENDED, never inserted: the index IS the SCTYPE_* id a built ROM
    # carries, so a new type goes on the END or every existing world's
    # scene-type table renumbers under it (W7j).
    SCENE_TYPES = ["topdown", "platform", "adventure", "shmup", "logo", "menu",
                   "pointnclick"]
    scene_type_ids = []
    for sc in scenes:
        t = str(sc.get("scene_type", "topdown"))
        if t not in SCENE_TYPES:
            raise SceneError("scene '%s': unknown scene_type '%s' (one of %s)"
                             % (sc.get("name"), t, ", ".join(SCENE_TYPES)))
        scene_type_ids.append(SCENE_TYPES.index(t))

    # PARALLAX BANDS (`[[scene]] parallax = [{rows = N, speed = S}, ...]`, GB
    # Studio's model): the background splits into up to three horizontal bands,
    # each scrolled at `camera >> speed` by a scanline interrupt. `speed` 0 is
    # full speed, n is 1/2^n, and "fixed" is the sentinel below - NOT a shift,
    # because no shift zeroes a u16 (the reference engine writes 128 there and its runtime
    # only reaches 0 by shifting past the word width; don't copy the encoding).
    # The LAST band's `rows` may be 0 or absent, meaning "to the bottom".
    # Additive: a world where no scene sets `parallax` emits none of this.
    PX_MAX = 3
    px_fixed = 255
    px_bands = []                       # per scene: [(row, rows, shift), ...]
    for i, sc in enumerate(scenes):
        got = sc.get("parallax") or []
        if not got:
            px_bands.append([])
            continue
        if len(got) > PX_MAX:
            raise SceneError(
                "scene '%s': %d parallax bands; the scanline runtime carries "
                "at most %d (the reference engine's own limit)"
                % (sc.get("name"), len(got), PX_MAX))
        rows_h = scene_h[i]
        out, row = [], 0
        for k, band in enumerate(got):
            n = int(band.get("rows", 0) or 0)
            if k == len(got) - 1 or n <= 0:
                # The LAST band fills to the bottom of the MAP, not of the
                # screen. `rows` is doing double duty - `scrollpx.band` derives
                # the band's SCANLINE extent from `(row + rows) * 8` and reads
                # anything past 143 as the chain terminator, while `put()` uses
                # the same number as the count of map rows to stream - and for
                # the last band those two differ the moment a room is TALLER
                # than the screen. That band is the only one the parallax ISR
                # gives the vertical scroll to (`SCY = voff`, upper bands pinned
                # to 0), so the hardware reads tilemap rows `voff/8` further
                # down than `put()` wrote them: clamped to the screen, the platformer conversion's
                # 22-row `tutorial_0` drew 4 rows too high and its bottom 4 rows
                # came from tilemap rows nothing had ever written. Streaming to
                # the map's bottom keeps every row the band can scroll onto
                # resident, so SCY alone does the vertical scroll and no
                # re-stream is needed when the camera moves in y.
                # `map_h <= 32` and the upper bands sit above `row`, so
                # `row + n` never passes the 32-row tilemap.
                n = max(0, rows_h - row)
            if n <= 0:
                break
            sp = band.get("speed", 0)
            shift = px_fixed if str(sp).lower() == "fixed" else int(sp)
            if shift != px_fixed and not 0 <= shift <= 15:
                raise SceneError(
                    "scene '%s': parallax speed %r; expected 0..15 (1/2^n) or "
                    "\"fixed\"" % (sc.get("name"), sp))
            out.append((row, n, shift))
            row += n
        px_bands.append(out)
    has_parallax = any(px_bands)

    # Flatten every scene's map once (so optional tile-packing can remap them),
    # each using its own size.
    flats = [_flatten_map(sc.get("map", []), scene_w[i], scene_h[i],
                          sc.get("name"))
             for i, sc in enumerate(scenes)]

    # Two INDEPENDENT collision models, decoupled (a game uses one):
    #   * The per-cell collision LAYER (reference-engine style): a `collision` array
    #     parallel to `map` (0 none / 1 solid / 2 platform), the painted-layer
    #     path -> `collision_at`. Emitted ONLY when a scene PAINTS one, so
    #     collision-free worlds stay byte-identical (the additive rule, like
    #     `pack_tiles` / `[[animated_tile]]`).
    #   * The TILE-BASED solid set: `[collision] solid = [ids]` -> exported as
    #     `SOLID_TILES` / `is_solid(t)` (below) so a game reads which tile ids
    #     collide. It is NOT a per-cell layer and does NOT infer one (that
    #     doubled scene data for a tile-based game -- which overflowed the Lynx;
    #     paint a `collision` array if you want the per-cell layer).
    solid_set = set(int(v) & 0xFF
                    for v in world.get("collision", {}).get("solid", []))
    explicit_col = [sc.get("collision") for sc in scenes]
    has_collision = any(c is not None for c in explicit_col)
    col_flats = None
    if has_collision:
        col_flats = []
        for i, sc in enumerate(scenes):
            if explicit_col[i] is not None:
                col_flats.append(_flatten_map(explicit_col[i], scene_w[i],
                                              scene_h[i], sc.get("name"),
                                              what="collision"))
            else:
                col_flats.append([0] * (scene_w[i] * scene_h[i]))

    # Optional dead-tile packing ([world] pack_tiles): emit only the tiles the
    # maps actually reference, remapped to a compact 0..k-1 range -- so an
    # oversized tileset PNG costs only what's used (and the budget reflects it).
    # Opt-in (existing worlds stay byte-identical), a no-op when every tile is
    # used, and skipped when animated tiles are present (their dedicated
    # destination blocks need separate allocation -- a follow-up).
    if w.get("pack_tiles") and not world.get("animated_tile"):
        used = sorted({v for flat in flats for v in flat if v < tile_count})
        if 0 < len(used) < tile_count:
            remap = {old: new for new, old in enumerate(used)}
            tiles = [b for old in used for b in tiles[old * 16:(old + 1) * 16]]
            tile_count = len(used)
            flats = [[remap.get(v, v) for v in flat] for flat in flats]

    # Item 32: build the shared metatile table + the compressed per-scene maps.
    # Runs AFTER pack_tiles so it dedups the FINAL tile indices. Each metatile is
    # 4 bytes in row-major 2x2 order (TL, TR, BL, BR), which is also exactly the
    # layout `paint()` uploads a 2-wide strip from. Scenes with odd dimensions
    # pad the right/bottom edge with tile 0; the padding is never read back
    # because map_tile still bounds on the scene's REAL width.
    #
    # COLLISION can ride the metatile (the classic NES platformer model, the biggest single
    # data cut): when a world's painted collision never varies WITHIN a 2x2
    # block, the cell type is a property of the metatile, so one attribute byte
    # per metatile replaces the per-cell arrays entirely (a 32x28 scene: 896 B
    # -> 0, against 1 B per distinct metatile shared world-wide). It is
    # auto-detected, never configured: a world that paints 8x8-fine collision
    # (any block with mixed cells) keeps the per-cell arrays unchanged.
    # The collision type joins the dedup KEY when the attribute is used, so two
    # identical-looking blocks that collide differently stay distinct metatiles.
    # Metatiles COMPOSE with the residency features (they change what a map
    # CONTAINS; stream / paint_table change how it is READ, and both compose
    # through the one map_meta selector):
    #   * + paint_table: the compressed maps concatenate into MAPS + a MAP_OFF
    #     in METATILE cells -- map_meta is one O(1) indexed read.
    #   * + stream: the per-scene compressed maps ride the whole-asset seam
    #     (paint() marks them with assets.use; codegen rewrites map_meta's
    #     indexing through the cart cache / ROM bank automatically).
    #   * + both: the range seam windows the CURRENT room out of the compressed
    #     concat (range_base/use_range warmed by paint, range_byte reads).
    # The shared METATILES / METATILE_COLLIDE tables stay RESIDENT in every
    # mode: they are world-global (a window/bank per room makes no sense) and
    # small by construction (<= 255 * 4 + 255 B).
    mt_table, mt_flats, mt_w, mt_h = None, None, None, None
    mt_collide = None
    if metatiles:
        # Can collision ride the metatile? Only if no 2x2 block mixes cell types.
        def _collision_is_16x16():
            for i, cf in enumerate(col_flats or []):
                sw, sh = scene_w[i], scene_h[i]
                for my in range((sh + 1) // 2):
                    for mx in range((sw + 1) // 2):
                        seen = {cf[y * sw + x]
                                for dy in range(2) for dx in range(2)
                                for x, y in [(mx * 2 + dx, my * 2 + dy)]
                                if x < sw and y < sh}
                        if len(seen) > 1:
                            return False
            return True

        mt_attr = has_collision and _collision_is_16x16()
        mt_index, mt_table = {}, []
        mt_collide = [] if mt_attr else None
        mt_flats, mt_w, mt_h = [], [], []
        for i, flat in enumerate(flats):
            sw, sh = scene_w[i], scene_h[i]
            cf = col_flats[i] if mt_attr else None
            mw, mh = (sw + 1) // 2, (sh + 1) // 2
            mt_w.append(mw)
            mt_h.append(mh)
            out = []
            for my in range(mh):
                for mx in range(mw):
                    quad = []
                    for dy in range(2):
                        for dx in range(2):
                            x, y = mx * 2 + dx, my * 2 + dy
                            quad.append(flat[y * sw + x]
                                        if x < sw and y < sh else 0)
                    # The block's collision type (uniform by the check above, so
                    # the top-left in-bounds cell speaks for all four).
                    col = cf[my * 2 * sw + mx * 2] if cf is not None else None
                    # Collision joins the KEY: the same tiles colliding two
                    # different ways must stay two metatiles, or the attribute
                    # would be ambiguous.
                    key = (tuple(quad), col)
                    idx = mt_index.get(key)
                    if idx is None:
                        idx = len(mt_table)
                        mt_index[key] = idx
                        mt_table.append(quad)
                        if mt_attr:
                            mt_collide.append(col)
                    out.append(idx)
            mt_flats.append(out)
        # The map stores metatile ids as u8, so 255 is the hard ceiling (the same
        # cap-and-dedup shape as the 256-tile table).
        if len(mt_table) > 255:
            raise SceneError(
                "world has %d distinct 2x2 metatiles; the u8 map caps it at 255. "
                "Reuse 2x2 tile patterns across the world (a metatile is shared "
                "world-wide, so repeated scenery is free), split the world, or "
                "drop [world] metatiles" % len(mt_table))

    # Per-scene start offsets into the concatenated COMPRESSED maps (metatiles +
    # paint_table), in METATILE cells -- the compressed analogue of map_off. The
    # full-cell map_off (below) is still needed alongside it when per-cell
    # collision falls back: the collision layer stays w*h while the map is
    # (w/2)*(h/2), so the two can no longer share one offset table.
    mt_off = None
    if metatiles:
        mt_off = []
        _macc = 0
        for mw_i, mh_i in zip(mt_w, mt_h):
            mt_off.append(_macc)
            _macc += mw_i * mh_i

    # COLOUR: the per-tile background palette model (GBC / Analogue Pocket /
    # NES / PC Engine). Three additive inputs, all emitted only when present so
    # a colourless world is byte-identical:
    #
    #   [[palette]] name/colors  - the world's palette LIBRARY (4 colours each,
    #                              "RRGGBB" hex or [r,g,b]).
    #   [[scene]] bkg_palettes   - which library palette each of the 8 hardware
    #             spr_palettes     background / sprite slots holds in THIS room.
    #   [[scene]] tile_palette   - per TILE index, which of those 8 slots the
    #                              tile renders with.
    #
    # The per-TILE table (rather than a per-CELL attribute layer) is the whole
    # reason this composes with `stream` / `paint_table` / `metatiles` for
    # free: the attribute of a map cell is looked up from the tile the cell
    # already holds, so `paint_attrs` and the streamed-room colouring both go
    # through the existing `map_tile` read and no residency mode has to learn
    # anything new. It is also the small representation - one byte per tile
    # (<= 255 per tileset) instead of one per cell (w*h per scene). The cost is
    # that a tile used with two palettes must exist twice in the tileset; a
    # converter makes that exact by putting the palette in its dedup key.
    pal_lib = world.get("palette", [])
    # A scene without its own rows takes `[world] default_bkg_palettes` /
    # `default_spr_palettes` (the reference engine's project defaults). Absent = None, the
    # behaviour every world had before (palette_fold.scene_palette_rows, which
    # the rooms generator's colour gate reads too).
    scene_bpal, scene_spal = scene_palette_rows(world)
    scene_tpal = [sc.get("tile_palette") for sc in scenes]
    has_palettes = bool(pal_lib) and any(p is not None
                                         for p in scene_bpal + scene_spal)
    has_tile_pal = bool(pal_lib) and any(p is not None for p in scene_tpal)
    # [kind_palettes] = { <kind name> = <sprite palette slot 0..7> }: which of
    # the room's 8 hardware sprite palettes an actor of that kind renders with.
    # Independent of the background tables above (a world may colour sprites
    # without a per-tile background map), so it is gated on its own key.
    kp_src = world.get("kind_palettes") or {}
    kind_pal = None
    if kp_src:
        for k in kp_src:
            if k not in kinds:
                raise SceneError("[kind_palettes] names '%s', which is not in "
                                 "[kinds]" % k)
        n_kinds = max(int(v) for v in kinds.values()) + 1 if kinds else 1
        kind_pal = [0] * n_kinds
        for k, slot in kp_src.items():
            kind_pal[int(kinds[k]) & 0xFF] = int(slot) & 7
    # [kind_tile_palettes] = { <kind> = [slot per CELL] }: a palette per 8x8
    # CELL of the kind's metasprite (row-major, the same units sprite.set_meta
    # takes), so ONE actor can wear several -- the reference engine's platform player has
    # hair, face and body on three OBJ palettes. The per-kind rows are
    # concatenated with an offset table for the same reason the maps are: one
    # symbol, an O(1) lookup, and adding a kind adds rows rather than code.
    # The row LENGTH must match the kind's metasprite size, which lives in the
    # clips module -- the converter owns that, not this transpiler.
    ktp_src = world.get("kind_tile_palettes") or {}
    kind_tpal, kind_tpal_off = None, None
    if ktp_src:
        for k in ktp_src:
            if k not in kinds:
                raise SceneError("[kind_tile_palettes] names '%s', which is "
                                 "not in [kinds]" % k)
        n_kinds = max(int(v) for v in kinds.values()) + 1 if kinds else 1
        rows = [[] for _ in range(n_kinds)]
        for k, cells in ktp_src.items():
            rows[int(kinds[k]) & 0xFF] = [int(c) & 7 for c in (cells or [])]
        kind_tpal, kind_tpal_off = [], []
        # A kind that has a whole-sprite slot ([kind_palettes]) but no cell
        # row still goes through paint_actor, which reads w*h cells: without a
        # row of its own it drew on slot 0 AND read into the next kind's row.
        # Its row is its slot, as long as the longest authored row (the
        # transpiler never sees a metasprite size). A kind with neither keeps
        # the [0] it always had, so every existing world is byte-identical.
        widest = max([len(r) for r in rows] + [1])
        slot_of = {int(kinds[k]) & 0xFF: int(v) & 7 for k, v in kp_src.items()}
        for kid, row in enumerate(rows):
            kind_tpal_off.append(len(kind_tpal))
            # A kind with no map still needs a row: the generated room loader
            # colours every kind through the one call.
            if not row and kid in slot_of:
                row = [slot_of[kid]] * widest
            kind_tpal += row or [0]
    # `[world] pal_write`: a script writes a palette at RUNTIME (the reference engine's
    # EVENT_PALETTE_SET_*), so the LIBRARY itself has to be addressable by
    # index at run time, not just resolved per scene. Opt-in, because the
    # table and its writer are pure cost to a world that never recolours -
    # and it is a world KEY rather than something derived, for the reason
    # `replace_tile` is: this transpiler never sees the scripts. The generated
    # CALL (rooms.mos's core.set_pal_write) reads the same fact, so the two
    # cannot disagree.
    pal_write = bool(w.get("pal_write")) and bool(pal_lib)
    pal_words = []
    pal_rgb = []
    for p in pal_lib:
        cols = list(p.get("colors") or [])
        if len(cols) != 4:
            raise SceneError("[[palette]] '%s' has %d colours; a palette slot "
                             "holds exactly 4" % (p.get("name"), len(cols)))
        rgbs = [_rgb888(c) for c in cols]
        pal_rgb.append(rgbs)
        pal_words.append(rgb555_words(rgbs))

    # The SMS renders 2 bits a channel (64 colours), shallow enough that
    # rounding each channel independently at runtime greys out every
    # desaturated mid-tone. Its entries are therefore FITTED here -- the
    # whole LIBRARY at once, so a colour two palettes share stays one colour
    # (see mosaik_assets.fit_palettes for why neither the colour nor the
    # single palette is the right unit of work). The Game Gear's 4 bits a
    # channel need none of it and keep the runtime conversion.
    pal_sms = [[ri | (gi << 2) | (bi << 4) for (ri, gi, bi) in fit]
               for fit in fit_palettes(pal_rgb, 4)]

    # The 8 -> 4 slot FOLD for SMS / Game Gear / NES / PC Engine
    # (mosaik_scenes.palette_fold). Only when a slot >= 4 is used AND the
    # project builds for one of the four, so every other world - and a
    # conversion the importer already folded - is byte-identical.
    fold_on = targets_fold(base_dir)
    bkg_fold_rows = (bkg_fold(world) if fold_on and pal_lib
                     and any(p is not None for p in scene_tpal) else None)
    spr_fold_data = (spr_fold(world) if fold_on and (kp_src or ktp_src)
                     else None)

    def _pal_set(sel, table=None):
        """One scene's 8 hardware slots -> 32 colour entries (4 per slot).

        A missing / short list pads with palette 0, so a world that colours
        only some slots (the reference engine leaves unused scene palette ids blank) needs
        no placeholders. An out-of-range index is a clear error rather than a
        silently black room."""
        table = pal_words if table is None else table
        out = []
        sel = list(sel or [])
        for s in range(PAL_SLOTS):
            idx = int(sel[s]) if s < len(sel) and sel[s] is not None else 0
            if idx < 0 or idx >= len(pal_words):
                raise SceneError("palette index %d is not one of the %d "
                                 "[[palette]] entries" % (idx, len(pal_words)))
            out += table[idx]
        return out

    # The combined table is uploaded as one block whose count (TILE_COUNT, and
    # the count argument of bkg.set_data on both backends) is a u8, so 255 is
    # the hard ceiling: a 256-tile upload would truncate to a count of 0 and
    # silently blank the world (pack_tiles above can bring an oversized merge
    # back under the cap).
    if tile_count > 255:
        raise SceneError("combined tileset has %d tiles; the u8 upload count "
                         "caps it at 255 (trim images, or set [world] "
                         "pack_tiles to emit only the tiles the maps use)"
                         % tile_count)

    def _check_u8_count(label, n, remedy):
        # SCENE_COUNT/OBJ_COUNT/DOOR_COUNT/TRIG_COUNT are emitted as `const ...:
        # u8`. Without this guard a count > 255 silently emits an OUT-OF-RANGE u8
        # literal (e.g. `const OBJ_COUNT: u8 = 257`): the value survives into the
        # generated C as a plain `#define` (never truncated), so a `for i in
        # 0..scenes.OBJ_COUNT` loop compiles to `uint8_t i; i < 257`, which is
        # ALWAYS true for every u8 -- a SILENT INFINITE LOOP at runtime, no
        # compiler warning, no link error. Raise here instead, at generate time.
        if n > 255:
            raise SceneError("world has %d %s; the u8 %s count caps it at 255 "
                             "(%s)" % (n, label, label, remedy))

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

    # Flatten the object + door tables across scenes (parallel arrays). The flatten
    # ORDER is the table position the running game indexes by; a studio `id`/`name`
    # on an object/door is authoring metadata (see load_world) and is read NOWHERE
    # here -- only kind/x/y and from/tx/ty/to/ex/ey matter.
    obj_scene, obj_kind, obj_x, obj_y = [], [], [], []
    # VM8 PER-INSTANCE script slots: each placed
    # actor may carry On Init / On Update / On Interact script refs (a script NAME
    # in scripts/*.evt.toml). Collected in OBJ flatten order (= the runtime OBJ
    # table index the shell loops over), so the emitted selectors index by OBJ id.
    # None where a slot is unbound. Only a VM world reads these (byte-identical
    # elsewhere -- the same additive rule as SCENE_TYPE).
    obj_init, obj_update, obj_interact, obj_hit = [], [], [], []
    obj_pin = []
    # Per-console CONTENT FILTER:
    # each entity may carry a `platforms` allow-list of canonical target ids; the
    # entity tables are then FORKED per target-bucket into `if platform` guards
    # (see _platform_buckets / the emit_entities helper below). Empty = every
    # target, so an untagged world produces no guard and stays byte-identical.
    obj_plat = []
    for i, sc in enumerate(scenes):
        for ob in sc.get("object", []):
            k = ob["kind"]
            if k not in kinds:
                raise SceneError("scene '%s': object kind '%s' not in [kinds]"
                                 % (sc.get("name"), k))
            obj_scene.append(i)
            obj_kind.append(int(kinds[k]) & 0xFF)
            # OBJ_X/OBJ_Y widen to u16 below when a WIDE/TALL level places an object
            # past 255 px (u16-masked here so the literal fits); narrow worlds stay u8.
            obj_x.append(int(ob["x"]) & 0xFFFF)
            obj_y.append(int(ob["y"]) & 0xFFFF)
            obj_init.append(ob.get("on_init") or None)
            obj_update.append(ob.get("on_update") or None)
            obj_interact.append(ob.get("on_interact") or None)
            obj_hit.append(ob.get("on_hit") or None)
            # SCREEN-SPACE (the reference engine's isPinned): drawn without the camera
            # subtraction. Rare and boolean, so it is emitted as a SELECTOR
            # rather than a full u8 column - a world with no pinned object
            # emits nothing at all and stays byte-identical.
            obj_pin.append(1 if ob.get("pinned") else 0)
            obj_plat.append(_entity_platforms(ob))

    # Per-scene On Init script (one-shot on room load), by scene id.
    scene_init = [sc.get("on_init") or None for sc in scenes]

    doors = world.get("door", [])
    d_from, d_tx, d_ty, d_to, d_ex, d_ey = [], [], [], [], [], []
    door_plat = []
    for dr in doors:
        d_from.append(scene_id(dr["from"]))
        door_plat.append(_entity_platforms(dr))
        # Trigger CELL (tile col/row). Widened to u16 below when a WIDE/TALL level
        # puts the trigger past cell 255 (u16-masked here so the literal fits);
        # narrow worlds stay u8. Masking to u8 here silently aliased a far door
        # (col 298 -> 42) so it never matched the u16 player column.
        d_tx.append(int(dr["tx"]) & 0xFFFF)
        d_ty.append(int(dr["ty"]) & 0xFFFF)
        d_to.append(scene_id(dr["to"]))
        d_ex.append(int(dr["ex"]) & 0xFFFF)   # u16 entry pixel on a wide/tall room
        d_ey.append(int(dr["ey"]) & 0xFFFF)

    # Scriptable tile-rect TRIGGERS (VM8): a door is a specialised trigger kept
    # native (DOOR_*); a [[trigger]] is the general on-enter rect. Each: from-scene
    # + a tile rect (tx/ty, tw/th default 1) + on_enter (a script name). VM-only +
    # additive -- a non-VM world has no [[trigger]] and stays byte-identical.
    triggers = world.get("trigger", []) if is_vm else []
    trig_from, trig_tx, trig_ty, trig_tw, trig_th, trig_enter = [], [], [], [], [], []
    trig_leave = []
    trig_plat = []
    for tr in triggers:
        trig_from.append(scene_id(tr["from"]))
        trig_plat.append(_entity_platforms(tr))
        trig_tx.append(int(tr["tx"]) & 0xFFFF)
        trig_ty.append(int(tr["ty"]) & 0xFFFF)
        trig_tw.append(max(1, int(tr.get("tw", 1))) & 0xFF)
        trig_th.append(max(1, int(tr.get("th", 1))) & 0xFF)
        trig_enter.append(tr.get("on_enter") or None)
        # ON LEAVE, the falling edge of the same latch (the reference engine's
        # TRIGGER_HAS_LEAVE_SCRIPT arm). Additive: a world that binds none emits
        # no `trigger_leave` selector, which is what keeps `vm.trigger`'s
        # enter-only arm - and its byte-identical output - selected.
        trig_leave.append(tr.get("on_leave") or None)

    # Any per-instance / scene / trigger slot bound? Gates the whole slot-selector
    # block (+ the `import "scripts"` it needs), so a VM world with no scripts
    # attached is byte-identical to today (SCENE_TYPE only).
    n_trig = len(triggers)
    has_obj_init = any(obj_init)
    has_obj_update = any(obj_update)
    has_obj_interact = any(obj_interact)
    has_obj_hit = any(obj_hit)
    has_obj_pin = is_vm and any(obj_pin)
    has_scene_init = any(scene_init)
    trig_present = n_trig > 0
    has_trig_leave = any(trig_leave)
    if _force is not None:
        # Per-console SCENE fork: a bucket that drops the only slotted entity would
        # emit fewer slot selectors than another, breaking the uniform export. Force
        # the slot-kind set to the FULL world's so every branch emits the same
        # selectors (an empty one just returns NO_SCRIPT).
        has_obj_init = has_obj_init or _force["has_obj_init"]
        has_obj_update = has_obj_update or _force["has_obj_update"]
        has_obj_interact = has_obj_interact or _force["has_obj_interact"]
        has_obj_hit = has_obj_hit or _force["has_obj_hit"]
        has_obj_pin = has_obj_pin or _force["has_obj_pin"]
        has_scene_init = has_scene_init or _force["has_scene_init"]
        trig_present = trig_present or _force["has_triggers"]
        has_trig_leave = has_trig_leave or _force["has_trig_leave"]
    slots_used = is_vm and (has_obj_init or has_obj_update or has_obj_interact
                            or has_obj_hit or has_scene_init or trig_present)

    # A WIDE level (map_w > 255 tiles, the u16-column-streamed path) needs the
    # dimension consts/arrays to be u16, else a width like 300 wraps to a u8 44
    # and the column-streamer (`gather` walks the map with += SCENE_W[room]) reads
    # garbage -- no background. Each axis picks its own width so narrow/short
    # worlds stay byte-identical u8 (every existing wide sample is <= 255 per axis).
    def _dim_type(*vals):
        return "u16" if max(vals) > 255 else "u8"
    # Everything above is the context, captured as it stands (see the
    # module docstring): a new derived value is just a new local.
    return SceneCtx(**{k: v for k, v in locals().items()
                       if not k.startswith("__")})
