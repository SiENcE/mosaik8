"""mosaik_scenes.transpile.emit_maps - the tilemaps and the collision model - the paint
interpreter, the chunked cell readers and the solid-tile set.

One section of the generated `scenes` module. `emit(c, L)` appends its
lines to `L`, reading the analysed world off the SceneCtx `c`
(see transpile.context)."""
from ..base import _emit_array, _ident


def emit(c, L):
    """The map/collision tables, paint()/warm(), map_tile/collision_at/is_solid."""
    map_w, map_h, scenes, scene_w = c.map_w, c.map_h, c.scenes, c.scene_w
    scene_h, uniform, flats, map_off = c.scene_h, c.uniform, c.flats, c.map_off
    map_blk, map_chunks, col_flats, has_collision = c.map_blk, c.map_chunks, c.col_flats, c.has_collision
    solid_set, paint_table, metatiles, stream = c.solid_set, c.paint_table, c.metatiles, c.stream
    mt_table, mt_flats, mt_w, mt_h = c.mt_table, c.mt_flats, c.mt_w, c.mt_h
    mt_off, mt_collide, per_scene_ts, ts_table = c.mt_off, c.mt_collide, c.per_scene_ts, c.ts_table
    scene_tiles, scene_ts_name, ts_off, ts_tc = c.scene_tiles, c.scene_ts_name, c.ts_off, c.ts_tc
    ts_maxlen, warm_fn, warm_ranges, wide_world = c.ts_maxlen, c.warm_fn, c.warm_ranges, c.wide_world
    max_scene_cells, _dim_type, tile_count = c.max_scene_cells, c._dim_type, c.tile_count
    has_palettes, has_tile_pal, is_vm, _emit_tileset_upload = c.has_palettes, c.has_tile_pal, c.is_vm, c._emit_tileset_upload
    max_scene_w = c.max_scene_w

    wt = _dim_type(map_w, *scene_w)
    ht = _dim_type(map_h, *scene_h)
    off_t = _dim_type(*map_off)
    # Chunk symbol names for the concatenated tables. One chunk = the single
    # historic symbol, so every world that fits a bank stays byte-identical.
    map_syms = ["MAPS" if k == 0 else "MAPS%d" % (k + 1) for k in range(map_chunks)]
    col_syms = ["COLLISION" if k == 0 else "COLLISION%d" % (k + 1)
                for k in range(map_chunks)]

    def _emit_chunked(syms, blocks, per_line):
        """Emit one const array per chunk, each holding the scenes MAP_BLK
        assigns to it. Scene blocks are contiguous and in scene order, so a
        chunk is a plain slice of the old concatenation."""
        for k, sym in enumerate(syms):
            blob = [v for i, f in enumerate(blocks) if map_blk[i] == k for v in f]
            if k:
                L.append("    -- Chunk %d: no const array may cross a ROM bank, so"
                         % (k + 1))
                L.append("    -- the concatenation continues in its own symbol.")
            _emit_array(L, "u8", sym, len(blob), blob, per_line=per_line)
            L.append("")

    def _emit_cell_reader(name, syms, read):
        """The ONE chunk reader every cell read routes through.

        The fork is written once here rather than inlined at each call site:
        duplicating a seam fork cost ~510 B of resident image the last time it
        was tried (the song CELLS blob), and there are four read sites between
        map_tile, collision_at and paint(). Emitted only past one chunk."""
        if len(syms) == 1:
            return
        L.append("    -- The ONE chunked-table reader: %s is split at a ROM bank"
                 % syms[0])
        L.append("    -- (no const array may cross one), so every read goes")
        L.append("    -- through here instead of inlining the fork per site.")
        L.append("    function %s(blk: u8, off: u16, idx: u16) -> u8 {" % name)
        for k, sym in enumerate(syms[:-1]):
            L.append("        if blk == %d { return %s }" % (k, read % sym))
        L.append("        return %s" % (read % syms[-1]))
        L.append("    }")
        L.append("")

    if paint_table and not metatiles:
        # Item 33: ONE concatenated MAPS table + a per-scene start-offset table,
        # so the three selectors below are O(1) instead of a per-scene dispatch.
        # CHUNKED past a ROM bank (see map_blk above), which adds MAP_BLK and
        # routes every read through map_cell; at one chunk none of that is
        # emitted and the output is what it always was.
        map_names = []                       # no per-scene map symbols in this mode
        _emit_chunked(map_syms, flats, max_scene_w)
        _emit_array(L, off_t, "MAP_OFF", len(map_off), map_off)
        L.append("")
        if map_chunks > 1:
            L.append("    -- Which chunk each scene's map block lives in. Shared")
            L.append("    -- with COLLISION: both split on the same scene")
            L.append("    -- boundaries, so one table indexes both.")
            _emit_array(L, "u8", "MAP_BLK", len(map_blk), map_blk)
            L.append("")
        _wh = ("MAP_W", "MAP_H") if uniform \
            else ("SCENE_W[scene]", "SCENE_H[scene]")
        if stream:
            # paint_table + [world] stream (item 33 stream-compose): the
            # concatenated MAPS is archived WHOLE, and the range seam streams the
            # CURRENT room's window (offset MAP_OFF[scene], length w*h) -- so
            # map_tile/paint stay O(1) CODE and the map DATA streams off-resident.
            # Byte-identical resident on the directly-mapped consoles (the seam is
            # a pointer/index into MAPS there).
            _emit_cell_reader("map_cell", map_syms,
                              "assets.range_byte(%s, off, idx)")
            L.append("    -- Tile at flat index `idx` of `scene` -- O(1) window read")
            L.append("    -- through the range seam (streams on the Lynx / banks on")
            L.append("    -- the GB family; a resident index elsewhere) (item 33).")
            L.append("    function map_tile(scene: u8, idx: u16) -> u8 {")
            if map_chunks > 1:
                L.append("        return map_cell(MAP_BLK[scene], MAP_OFF[scene], idx)")
            else:
                L.append("        return assets.range_byte(MAPS, MAP_OFF[scene], idx)")
            L.append("    }")
            L.append("")

            def _emit_range_setup(ind):
                """Register every chunk with the range seam and warm the room's
                collision window. EVERY chunk symbol needs its own range_base:
                the seam keys on the symbol, so an unregistered chunk reads
                cold - which on the Lynx is an empty level, not an error."""
                for sym in map_syms:
                    L.append("%sassets.range_base(%s, %d)"
                             % (ind, sym, max_scene_cells))
                if has_collision:
                    for sym in col_syms:
                        L.append("%sassets.range_base(%s, %d)"
                                 % (ind, sym, max_scene_cells))
                    # Warm the room's collision window too (collision_at samples
                    # it every frame), so both live in the 2-slot cache.
                    _emit_blk_fork(ind, col_syms, "assets.use_range(%s, off, n)")

            def _emit_blk_fork(ind, syms, stmt):
                """A statement that names a chunk SYMBOL cannot route through
                map_cell (it takes a pointer, or returns nothing), so it forks
                on MAP_BLK here. One chunk emits the bare statement."""
                if len(syms) == 1:
                    L.append(ind + stmt % syms[0])
                    return
                for k, sym in enumerate(syms):
                    L.append("%sif MAP_BLK[scene] == %d { %s }"
                             % (ind, k, stmt % sym))

            L.append("    -- Paint `scene`'s map -- ONE loop over the descriptor, the")
            L.append("    -- room's window streamed from the cart (item 33).")
            L.append("    function paint(scene: u8) {")
            _emit_tileset_upload()           # Gap 3: per-scene tileset (before the map)
            L.append("        var off: u16 = MAP_OFF[scene]")
            L.append("        var w: %s = %s" % (wt, _wh[0]))
            L.append("        var h: %s = %s" % (ht, _wh[1]))
            L.append("        var n: u16 = w * h")
            _emit_range_setup("        ")
            _emit_blk_fork("        ", map_syms,
                           "bkg.set_tiles(0, 0, w, h, assets.ptr_range(%s, off, n))")
            L.append("    }")
            L.append("")
            if warm_fn:
                # A WIDE room is never painted (paint's one bkg.set_tiles would
                # overrun the 32-tile tilemap), so it needs everything paint()
                # does EXCEPT that upload: its per-scene TILESET, and the range
                # windows map_tile/collision_at then read. Without the tileset
                # upload a wide room would also render with whatever tiles the
                # PREVIOUS room left in VRAM.
                L.append("    -- Wide-room counterpart of paint(): upload the scene's")
                L.append("    -- tileset and warm its map/collision windows, but do NOT")
                L.append("    -- upload the map (engine.scroll streams it by column).")
                L.append("    function warm(scene: u8) {")
                _emit_tileset_upload()
                L.append("        var off: u16 = MAP_OFF[scene]")
                L.append("        var w: %s = %s" % (wt, _wh[0]))
                L.append("        var h: %s = %s" % (ht, _wh[1]))
                L.append("        var n: u16 = w * h")
                _emit_range_setup("        ")
                _emit_blk_fork("        ", map_syms,
                               "assets.use_range(%s, off, n)")
                L.append("    }")
                L.append("")
        else:
            # paint_table alone (resident): mosaik has no array slice/pointer, so
            # paint() uploads one ROW strip at a time out of a boot-zero scratch
            # buffer (the engine.scroll column-strip idiom).
            L.append("    -- Scratch row buffer paint() fills from MAPS one row at a time")
            L.append("    -- (mosaik has no array slice, so a strip is copied then uploaded).")
            L.append("    var paint_buf: array[u8, %d]" % max_scene_w)
            L.append("")
            _emit_cell_reader("map_cell", map_syms, "%s[off + idx]")
            L.append("    -- Tile at flat index `idx` of `scene` -- O(1): index the")
            L.append("    -- concatenated MAPS at the scene's start offset (item 33).")
            L.append("    function map_tile(scene: u8, idx: u16) -> u8 {")
            if map_chunks > 1:
                L.append("        return map_cell(MAP_BLK[scene], MAP_OFF[scene], idx)")
            else:
                L.append("        return MAPS[MAP_OFF[scene] + idx]")
            L.append("    }")
            L.append("")
            L.append("    -- Paint `scene`'s map to the background (call on room load,")
            L.append("    -- after bkg.set_data(0, TILE_COUNT, TILESET)). ONE loop over")
            L.append("    -- the per-scene descriptor -- adding a room adds table rows,")
            L.append("    -- not code (item 33).")
            L.append("    function paint(scene: u8) {")
            _emit_tileset_upload()           # Gap 3: per-scene tileset (before the map)
            L.append("        var off: u16 = MAP_OFF[scene]")
            L.append("        var w: %s = %s" % (wt, _wh[0]))
            L.append("        var h: %s = %s" % (ht, _wh[1]))
            L.append("        var row: %s = 0" % ht)
            if map_chunks > 1:
                # Hoisted out of both loops: the chunk is a property of the
                # SCENE, so the fork is one table read per paint, not per cell.
                L.append("        var blk: u8 = MAP_BLK[scene]")
            L.append("        while row < h {")
            L.append("            var c: %s = 0" % wt)
            L.append("            while c < w {")
            if map_chunks > 1:
                L.append("                paint_buf[c] = map_cell(blk, off, c)")
            else:
                L.append("                paint_buf[c] = MAPS[off + c]")
            L.append("                c += 1")
            L.append("            }")
            L.append("            bkg.set_tiles(0, row, w, 1, paint_buf)")
            L.append("            off += w")
            L.append("            row += 1")
            L.append("        }")
            L.append("    }")
            L.append("")
            if warm_fn:
                # paint_table WITHOUT stream: the maps are resident, so a wide
                # room needs nothing warmed - only its per-scene tileset, which
                # paint() would have uploaded.
                L.append("    -- Wide-room counterpart of paint(): upload the")
                L.append("    -- scene's tileset, but not the map (engine.scroll")
                L.append("    -- streams that by column).")
                L.append("    function warm(scene: u8) {")
                _emit_tileset_upload()
                L.append("    }")
                L.append("")
    elif metatiles:
        # Item 32: the shared 2x2 metatile table + per-scene metatile maps.
        # `<name>_MAP` keeps its name but now holds (w/2)x(h/2) metatile ids, and
        # METATILES holds each distinct 2x2 block as 4 consecutive tiles
        # (TL, TR, BL, BR). map_tile/paint below hide that entirely, so callers
        # are unchanged.
        L.append("    -- 2x2 METATILES, content-deduplicated world-wide (item 32).")
        L.append("    -- 4 tiles each in row-major order: TL, TR, BL, BR.")
        L.append("    const METATILE_COUNT: u8 = %d" % len(mt_table))
        _emit_array(L, "u8", "METATILES", len(mt_table) * 4,
                    [v for quad in mt_table for v in quad], per_line=16)
        L.append("")
        if mt_collide is not None:
            L.append("    -- Collision as a METATILE ATTRIBUTE (the classic NES model):")
            L.append("    -- this world's painted collision never varies inside a 2x2")
            L.append("    -- block, so the cell type is a property of the metatile and")
            L.append("    -- ONE byte here replaces every per-scene collision array.")
            _emit_array(L, "u8", "METATILE_COLLIDE", len(mt_collide), mt_collide,
                        per_line=16)
            L.append("")
        elif has_collision:
            # Say WHY the bigger cut did not apply -- otherwise it is invisible.
            # The usual cause is a wall exactly ONE tile thick: the 2x2 block
            # straddling it mixes solid and empty, so collision is not a property
            # of the metatile and the per-cell arrays below have to stay.
            L.append("    -- NOTE: collision could NOT ride the metatile here -- some")
            L.append("    -- 2x2 block mixes cell types (a 1-tile-thick wall does it),")
            L.append("    -- so the per-cell arrays below are still needed. Painting")
            L.append("    -- collision in 2x2 blocks would drop them entirely.")
            L.append("")
        map_names = []
        mt_off_t = _dim_type(*mt_off)
        if paint_table:
            # + paint_table (item 33 composed): concatenate the COMPRESSED maps.
            # MAP_OFF is in METATILE cells here -- a quarter of the full-cell
            # offsets -- so the whole selector stack is one O(1) indexed read
            # over data a quarter the size.
            all_mmaps = [v for f in mt_flats for v in f]
            L.append("    -- Concatenated COMPRESSED maps (metatiles + paint_table):")
            L.append("    -- MAP_OFF is in METATILE cells, a quarter of the tile grid.")
            _emit_array(L, "u8", "MAPS", len(all_mmaps), all_mmaps,
                        per_line=max(mt_w))
            L.append("")
            _emit_array(L, mt_off_t, "MAP_OFF", len(mt_off), mt_off)
            L.append("")
        else:
            for i, sc in enumerate(scenes):
                nm = _ident(sc.get("name", "scene%d" % i)) + "_MAP"
                map_names.append(nm)
                L.append("    -- %d x %d metatiles (= %d x %d tiles), %d B instead of %d."
                         % (mt_w[i], mt_h[i], scene_w[i], scene_h[i],
                            mt_w[i] * mt_h[i], scene_w[i] * scene_h[i]))
                _emit_array(L, "u8", nm, mt_w[i] * mt_h[i], mt_flats[i],
                            per_line=mt_w[i])
                L.append("")
        # Scratch row buffer: mosaik has no array slice, so paint() expands one
        # ROW of tiles at a time and uploads it (the paint_table idiom).
        L.append("    -- Scratch row buffer paint() expands metatiles into, one")
        L.append("    -- tile row at a time (mosaik has no array slice).")
        L.append("    var paint_buf: array[u8, %d]" % max(scene_w))
        L.append("")
        # ONE selector on the METATILE grid; map_tile, collision_at (attribute
        # mode) and paint all go through it, so the residency mode is decided in
        # exactly one place:
        #   resident            -> a per-scene dispatch chain
        #   + stream            -> the same chain; paint() marks each map with
        #                          assets.use, and codegen rewrites the indexing
        #                          through the cart cache / ROM bank
        #   + paint_table       -> one O(1) indexed read of the compressed concat
        #   + both              -> the range seam's window read (warmed by paint)
        L.append("    -- Metatile id at flat index `midx` of `scene`'s metatile grid.")
        L.append("    function map_meta(scene: u8, midx: u16) -> u8 {")
        if paint_table and stream:
            L.append("        return assets.range_byte(MAPS, MAP_OFF[scene], midx)")
        elif paint_table:
            L.append("        return MAPS[MAP_OFF[scene] + midx]")
        else:
            for i, nm in enumerate(map_names):
                L.append("        if scene == %d {" % i)
                L.append("            return %s[midx]" % nm)
                L.append("        }")
            L.append("        return 0")
        L.append("    }")
        L.append("")
        L.append("    -- Tile at flat index `idx` of `scene` -- same signature as the")
        L.append("    -- uncompressed build: find the metatile covering (x,y), then the")
        L.append("    -- quadrant inside it (item 32).")
        L.append("    function map_tile(scene: u8, idx: u16) -> u8 {")
        L.append("        var w: u16 = %s" % ("MAP_W" if uniform else "SCENE_W[scene]"))
        L.append("        var x: u16 = idx % w")
        L.append("        var y: u16 = idx / w")
        L.append("        var mw: u16 = (w + 1) / 2")
        # u16, not u8: the TABLE index is m*4, which overflows a byte past
        # metatile 63 (the id itself is a u8, the offset into METATILES is not).
        L.append("        var m: u16 = map_meta(scene, (y / 2) * mw + (x / 2))")
        L.append("        return METATILES[m * 4 + (y % 2) * 2 + (x % 2)]")
        L.append("    }")
        L.append("")
        L.append("    -- Paint `scene`'s map to the background (call on room load,")
        L.append("    -- after bkg.set_data(0, TILE_COUNT, TILESET)). Expands ONE tile")
        L.append("    -- row at a time into the scratch buffer; the inner loop walks")
        L.append("    -- metatile COLUMNS, so it costs no division and no per-tile")
        L.append("    -- dispatch -- room load stays about as cheap as a flat map.")
        L.append("    function paint(scene: u8) {")
        _emit_tileset_upload()               # per-scene tileset (before the map)
        L.append("        var w: u16 = %s" % ("MAP_W" if uniform else "SCENE_W[scene]"))
        L.append("        var h: u16 = %s" % ("MAP_H" if uniform else "SCENE_H[scene]"))
        L.append("        var mw: u16 = (w + 1) / 2")
        if stream and paint_table:
            # Warm the CURRENT room's windows (range_byte reads an already-warm
            # window -- it never loads on a miss), exactly the item-33 contract:
            # map_meta/map_tile/collision_at answer for the PAINTED room.
            L.append("        var mh: u16 = (h + 1) / 2")
            L.append("        assets.range_base(MAPS, %d)"
                     % max(a * b for a, b in zip(mt_w, mt_h)))
            if has_collision and mt_collide is None:
                # Per-cell collision kept its full-size layout, so it windows
                # through its OWN offset table (COL_OFF), not the compressed
                # MAP_OFF.
                L.append("        assets.range_base(COLLISION, %d)" % max_scene_cells)
                L.append("        assets.use_range(COLLISION, COL_OFF[scene], w * h)")
            L.append("        assets.use_range(MAPS, MAP_OFF[scene], mw * mh)")
        elif stream:
            # Whole-asset residency: mark this room's map (and its per-cell
            # collision, when the attribute could not apply) resident; map_meta /
            # collision_at then read through the cart cache / ROM bank.
            for i, nm in enumerate(map_names):
                use = ["assets.use(%s)" % nm]
                if has_collision and mt_collide is None and any(col_flats[i]):
                    use.append("assets.use(%s_COLLISION)"
                               % _ident(scenes[i].get("name", "scene%d" % i)))
                L.append("        if scene == %d { %s }" % (i, " ".join(use)))
        L.append("        var row: u16 = 0")
        L.append("        while row < h {")
        L.append("            var mrow: u16 = row / 2")
        L.append("            var half: u16 = (row % 2) * 2")
        L.append("            var mc: u16 = 0")
        L.append("            while mc < mw {")
        L.append("                var m: u16 = map_meta(scene, mrow * mw + mc)")
        L.append("                var base: u16 = m * 4 + half")
        L.append("                var c: u16 = mc * 2")
        L.append("                paint_buf[c] = METATILES[base]")
        L.append("                if c + 1 < w {")          # odd width: no right half
        L.append("                    paint_buf[c + 1] = METATILES[base + 1]")
        L.append("                }")
        L.append("                mc += 1")
        L.append("            }")
        L.append("            bkg.set_tiles(0, row, w, 1, paint_buf)")
        L.append("            row += 1")
        L.append("        }")
        L.append("    }")
        L.append("")
        if warm_fn:
            # Metatile counterpart of the warm() above -- same contract (tileset
            # + windows, no map upload), reading the COMPRESSED map extents.
            L.append("    -- Wide-room counterpart of paint(): upload the scene's")
            L.append("    -- tileset and warm its map/collision windows, but do NOT")
            L.append("    -- upload the map (engine.scroll streams it by column).")
            L.append("    function warm(scene: u8) {")
            _emit_tileset_upload()
            if warm_ranges:
                L.append("        var w: u16 = %s" % ("MAP_W" if uniform else "SCENE_W[scene]"))
                L.append("        var h: u16 = %s" % ("MAP_H" if uniform else "SCENE_H[scene]"))
                L.append("        var mw: u16 = (w + 1) / 2")
                L.append("        var mh: u16 = (h + 1) / 2")
                L.append("        assets.range_base(MAPS, %d)"
                         % max(a * b for a, b in zip(mt_w, mt_h)))
                if has_collision and mt_collide is None:
                    L.append("        assets.range_base(COLLISION, %d)" % max_scene_cells)
                    L.append("        assets.use_range(COLLISION, COL_OFF[scene], w * h)")
                L.append("        assets.use_range(MAPS, MAP_OFF[scene], mw * mh)")
            elif stream:
                # Whole-asset residency: the wide room's own map (and per-cell
                # collision) marked resident, exactly what paint() does for a
                # painted one -- map_meta/collision_at then read a warm cache.
                for i, nm in enumerate(map_names):
                    use = ["assets.use(%s)" % nm]
                    if has_collision and mt_collide is None:
                        use.append("assets.use(%s_COLLISION)"
                                   % _ident(scenes[i].get("name", "scene%d" % i)))
                    L.append("        if scene == %d { %s }" % (i, " ".join(use)))
            L.append("    }")
            L.append("")
    else:
        # Per-scene maps.
        map_names = []
        for i, sc in enumerate(scenes):
            nm = _ident(sc.get("name", "scene%d" % i)) + "_MAP"
            map_names.append(nm)
            _emit_array(L, "u8", nm, scene_w[i] * scene_h[i], flats[i],
                        per_line=scene_w[i])
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
        if stream:
            L.append("    -- [world] stream: the per-scene map is fetched through the")
            L.append("    -- asset-residency seam (assets.use makes it resident, then")
            L.append("    -- assets.ptr hands its pointer to the setter) so the Lynx can")
            L.append("    -- load it from the cart here, at room load. Byte-identical on")
            L.append("    -- every directly-mapped console (use = no-op, ptr = the const).")
        L.append("    function paint(scene: u8) {")
        _emit_tileset_upload()               # Gap 3: per-scene tileset (before the map)
        for i, nm in enumerate(map_names):
            # Uniform world: MAP_W/MAP_H consts (byte-identical to before). Non-
            # uniform: this scene's literal dims (the branch is per scene id).
            dims = "MAP_W, MAP_H" if uniform else "%d, %d" % (scene_w[i], scene_h[i])
            L.append("        if scene == %d {" % i)
            if stream:
                L.append("            assets.use(%s)" % nm)
                # Preload this room's collision layer too (if any), so it streams
                # alongside the map: the use() marks it streamable (collision_at then
                # indexes it through the same cache) and warms the current room's slot.
                if has_collision and any(col_flats[i]):
                    cnm = _ident(scenes[i].get("name", "scene%d" % i)) + "_COLLISION"
                    L.append("            assets.use(%s)" % cnm)
                L.append("            bkg.set_tiles(0, 0, %s, assets.ptr(%s))"
                         % (dims, nm))
            else:
                L.append("            bkg.set_tiles(0, 0, %s, %s)" % (dims, nm))
            L.append("        }")
        L.append("    }")
        L.append("")
        if warm_fn:
            # Wide/tall counterpart of paint() on the PLAIN per-scene path.
            # Reached when a world has per-scene tilesets but no paint_table
            # (under 8 scenes, say): the streamed room is never painted, so
            # without this its tileset is never uploaded at all and the room
            # renders through the PREVIOUS room's tile data.
            L.append("    -- Wide-room counterpart of paint(): upload the scene's")
            L.append("    -- tileset (and mark its streamed map resident), but do")
            L.append("    -- NOT upload the map -- engine.scroll streams it.")
            L.append("    function warm(scene: u8) {")
            _emit_tileset_upload()
            if stream:
                for i, nm in enumerate(map_names):
                    use = ["assets.use(%s)" % nm]
                    if has_collision:
                        use.append("assets.use(%s_COLLISION)"
                                   % _ident(scenes[i].get("name", "scene%d" % i)))
                    L.append("        if scene == %d { %s }" % (i, " ".join(use)))
            L.append("    }")
            L.append("")

    # Collision layer (only when present -- collision-free worlds stay
    # byte-identical). A per-scene COLLISION_* array (cell TYPE, not tile slot)
    # + a `collision_at(scene, idx)` selector mirroring `map_tile`, plus the
    # named cell-type constants so games read by name (COLLIDE_SOLID, ...).
    col_names = []
    if has_collision:
        L.append("    -- Collision layer: per-cell collision TYPE, decoupled")
        L.append("    -- from the visual tile (0 none / 1 solid / 2 platform).")
        L.append("    const COLLIDE_NONE: u8 = 0")
        L.append("    const COLLIDE_SOLID: u8 = 1")
        L.append("    const COLLIDE_PLATFORM: u8 = 2")
        # ...and the LADDER type, emitted only where a scene paints one, so
        # every world that predates ladders stays byte-identical (the same
        # only-when-present rule the rest of this transpiler follows).
        if any(3 in f for f in col_flats if f):
            L.append("    const COLLIDE_LADDER: u8 = 3")
        L.append("")
        if mt_collide is not None:
            # Item 32: the per-cell arrays are GONE -- collision_at resolves the
            # metatile covering (x,y) and reads its attribute. Same signature and
            # same answers, so engine.collision / the generated solid_at are
            # unchanged; a 32x28 scene just stopped costing 896 B of collision.
            L.append("    -- Collision cell at flat index `idx` of `scene` -- read")
            L.append("    -- from the metatile's attribute (item 32), so no scene")
            L.append("    -- carries a per-cell collision array at all.")
            L.append("    function collision_at(scene: u8, idx: u16) -> u8 {")
            L.append("        var w: u16 = %s" % ("MAP_W" if uniform else "SCENE_W[scene]"))
            L.append("        var mw: u16 = (w + 1) / 2")
            L.append("        var m: u16 = map_meta(scene, (idx / w / 2) * mw + (idx % w) / 2)")
            L.append("        return METATILE_COLLIDE[m]")
            L.append("    }")
            L.append("")
        elif paint_table:
            # Item 33: one concatenated COLLISION table. WITHOUT metatiles it
            # shares MAP_OFF (map + collision have the same per-scene cell
            # layout); WITH metatiles the map is compressed to metatile cells
            # while collision stays w*h, so collision gets its OWN full-cell
            # offset table (COL_OFF). Either way collision_at is the same O(1)
            # read -- an indexed const resident, or (with stream) the range
            # seam's window read.
            if metatiles:
                # Metatiles keep ONE unchunked COLLISION (the map is compressed
                # and carries its own mt_off, so the two cannot share a split);
                # the size guard above is what keeps it inside a bank.
                all_col = [v for cf in col_flats for v in cf]
                _emit_array(L, "u8", "COLLISION", len(all_col), all_col,
                            per_line=max_scene_w)
                L.append("")
                col_off = "COL_OFF"
                _emit_array(L, off_t, "COL_OFF", len(map_off), map_off)
                L.append("")
            else:
                # Chunked on the SAME scene boundaries as MAPS, so MAP_BLK and
                # MAP_OFF index both tables (per-scene blocks are w*h either way).
                _emit_chunked(col_syms, col_flats, max_scene_w)
                col_off = "MAP_OFF"
            L.append("    -- Collision cell at flat index `idx` of `scene` -- O(1).")
            if not metatiles:
                _emit_cell_reader(
                    "col_cell", col_syms,
                    "assets.range_byte(%s, off, idx)" if stream else "%s[off + idx]")
            if map_chunks > 1 and not metatiles:
                L.append("    function collision_at(scene: u8, idx: u16) -> u8 {")
                L.append("        return col_cell(MAP_BLK[scene], MAP_OFF[scene], idx)")
            elif stream:
                L.append("    function collision_at(scene: u8, idx: u16) -> u8 {")
                L.append("        return assets.range_byte(COLLISION, %s[scene], idx)"
                         % col_off)
            else:
                L.append("    function collision_at(scene: u8, idx: u16) -> u8 {")
                L.append("        return COLLISION[%s[scene] + idx]" % col_off)
            L.append("    }")
            L.append("")
        else:
            # A scene whose collision layer is ALL CLEAR gets no array: the
            # selector's fall-through already answers 0 for it. One painted
            # scene (a walled hangar) used to cost every other scene a full
            # w*h array of zeros - 3,200 B per 20 x 160 shmup stage.
            col_scenes = []
            for i, sc in enumerate(scenes):
                if not any(col_flats[i]):
                    continue
                nm = _ident(sc.get("name", "scene%d" % i)) + "_COLLISION"
                col_names.append(nm)
                col_scenes.append(i)
                _emit_array(L, "u8", nm, scene_w[i] * scene_h[i], col_flats[i],
                            per_line=scene_w[i])
                L.append("")
            L.append("    -- Collision cell at flat index `idx` of `scene`.")
            L.append("    function collision_at(scene: u8, idx: u16) -> u8 {")
            for i, nm in zip(col_scenes, col_names):
                L.append("        if scene == %d {" % i)
                L.append("            return %s[idx]" % nm)
                L.append("        }")
            L.append("        return 0")
            L.append("    }")
            L.append("")

    # The solid tile-id SET ([collision] solid) exported as a readable list + an
    # is_solid() helper, so a TILE-BASED game (no per-cell layer -- e.g.
    # game-slice, which avoids per-cell arrays to fit the Lynx) can read which
    # tile ids collide instead of hardcoding them. Emitted ONLY when [collision]
    # solid is set (byte-identical otherwise); tree-shaken out if the game uses
    # the per-cell collision_at path instead. A game may still hardcode its own.
    if solid_set:
        ids = sorted(solid_set)
        L.append("    -- Solid tile ids ([collision] solid): the tile-based")
        L.append("    -- collision set -- a game reads SOLID_TILES / is_solid(t)")
        L.append("    -- instead of hardcoding which tiles are walls.")
        L.append("    const SOLID_COUNT: u8 = %d" % len(ids))
        _emit_array(L, "u8", "SOLID_TILES", len(ids), ids)
        L.append("    -- True if tile id `t` is in the solid set.")
        L.append("    function is_solid(t: u8) -> bool {")
        L.append("        for i in 0..SOLID_COUNT {")
        L.append("            if SOLID_TILES[i] == t {")
        L.append("                return true")
        L.append("            }")
        L.append("        }")
        L.append("        return false")
        L.append("    }")
        L.append("")

    # COLOUR tables (see the collection block above). Emitted only for a world
    # that authors [[palette]] + a per-scene assignment, so every existing
    # world is byte-identical.
    # PUBLISHED for emit_palettes (wt/ht are the dimension types) and for
    # emit_tail (the export list names every emitted symbol).
    c.wt, c.ht = wt, ht
    c.map_syms, c.col_syms = map_syms, col_syms
    c.map_names, c.col_names = map_names, col_names

