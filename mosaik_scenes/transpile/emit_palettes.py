"""mosaik_scenes.transpile.emit_palettes - the COLOUR tier: the palette library, the
per-scene sets and the per-tile attribute map.

One section of the generated `scenes` module. `emit(c, L)` appends its
lines to `L`, reading the analysed world off the SceneCtx `c`
(see transpile.context)."""
from .shared import PAL_SLOTS
from ..base import _emit_array, _emit_reader


def emit(c, L):
    """The palette tables and load_palettes / paint_attrs / paint_actor."""
    scenes, pal_lib, pal_words, pal_rgb = c.scenes, c.pal_lib, c.pal_words, c.pal_rgb
    pal_sms, scene_bpal, scene_spal, scene_tpal = c.pal_sms, c.scene_bpal, c.scene_spal, c.scene_tpal
    has_palettes, has_tile_pal, kind_pal, kind_tpal = c.has_palettes, c.has_tile_pal, c.kind_pal, c.kind_tpal
    kind_tpal_off, _pal_set, _dim_type = c.kind_tpal_off, c._pal_set, c._dim_type
    tile_count, stream, paint_table, metatiles = c.tile_count, c.stream, c.paint_table, c.metatiles
    uniform, scene_w, scene_h, map_w = c.uniform, c.scene_w, c.scene_h, c.map_w
    map_h, per_scene_ts, ts_table, is_vm = c.map_h, c.per_scene_ts, c.ts_table, c.is_vm
    _check_u8_count, wt, ht, _ts_arm = c._check_u8_count, c.wt, c.ht, c._ts_arm
    max_scene_w, scene_tiles = c.max_scene_w, c.scene_tiles
    pal_write = c.pal_write
    bkg_fold_rows, spr_fold_data = c.bkg_fold_rows, c.spr_fold_data
    # The 4-slot consoles' folded selections (palette_fold). Identical to the
    # authored rows when nothing folds, and then no fork is emitted at all.
    fold_bsel = ([f[1] for f in bkg_fold_rows] if bkg_fold_rows
                 else scene_bpal)
    fold_ssel = ([r if r is not None else scene_spal[i]
                  for i, r in enumerate(spr_fold_data[1])]
                 if spr_fold_data else scene_spal)
    folding = bool(bkg_fold_rows or spr_fold_data)

    if pal_write:
        # The [[palette]] LIBRARY, addressable by index, for the RUNTIME write
        # (vm.core's OP_PAL_SET -> core.set_pal_write -> this). The per-scene
        # tables above cannot serve it: they hold each scene's RESOLVED 8-slot
        # SET, so there is no way to name library palette 12 from a script.
        #
        # Portable 5-5-5 words on every console, like BKG_PALETTE16 - one
        # table, and graphics.palette rounds to the target's depth at the
        # write. That is deliberately NOT what the per-scene SMS table does
        # (it is FITTED at build time, because 2 bits a channel is too shallow
        # to round well at runtime); a runtime write has no build-time moment
        # to be fitted in, so the z80 pair takes the runtime rounding here. No
        # conversion uses this on SMS/GG, and a per-slot fitted table is the
        # follow-up if one ever does.
        lib = [v for entry in pal_words for v in entry]
        L.append("    -- The world's [[palette]] LIBRARY (`[world] pal_write`):")
        L.append("    -- 4 portable 5-5-5 colour words per entry, indexed by")
        L.append("    -- the library index a script names. Only a world that")
        L.append("    -- declares pal_write carries it.")
        _emit_array(L, "u16", "PAL_LIB", len(lib), lib, per_line=8)
        L.append("")
        L.append("    -- Write library palette `pal` into hardware slot `slot`")
        L.append("    -- of the SPRITE (layer 0) or BACKGROUND (layer 1)")
        L.append("    -- table. Registered as core.set_pal_write by the")
        L.append("    -- generated rooms.mos. It goes through graphics.palette")
        L.append("    -- rather than the hardware, so the CGB/SMS fade SHADOW")
        L.append("    -- records the write and a later ramp cannot undo it.")
        L.append("    -- A console with one palette per layer (DMG, NES) makes")
        L.append("    -- load_*_set an honest no-op, so a script's recolour is")
        L.append("    -- silently nothing there - the same degradation the rest")
        L.append("    -- of the colour tier has.")
        L.append("    function set_palette(layer: u8, slot: u8, pal: u8) {")
        L.append("        var off: u16 = pal")
        L.append("        off = off * 4")
        L.append("        if layer == 0 {")
        L.append("            palette.load_sprite_set(slot, 1, PAL_LIB, off)")
        L.append("        } else {")
        L.append("            palette.load_bkg_set(slot, 1, PAL_LIB, off)")
        L.append("        }")
        L.append("    }")
        L.append("")

    if has_palettes:
        bkg_pal_all = [w for i in range(len(scenes))
                       for w in _pal_set(scene_bpal[i])]
        spr_pal_all = [w for i in range(len(scenes))
                       for w in _pal_set(scene_spal[i])]
        L.append("    -- Per-scene hardware palettes: %d slots x 4 colours per"
                 % PAL_SLOTS)
        L.append("    -- scene, concatenated. Colours are PORTABLE 5-5-5 RGB")
        L.append("    -- words (like BKG_PALETTE16) -- scenes.mos is one source")
        L.append("    -- compiled per console, so palette.load_bkg_set rounds")
        L.append("    -- each to the target's real depth at runtime. (The SMS")
        L.append("    -- is too shallow to round well at runtime and takes a")
        L.append("    -- build-time-fitted native table instead; see below.)")
        L.append("    -- A room loads its set with an offset of `room * %d`."
                 % (PAL_SLOTS * 4))
        L.append("    const PAL_SLOTS: u8 = %d" % PAL_SLOTS)
        L.append("    const PAL_STRIDE: u8 = %d" % (PAL_SLOTS * 4))
        # The LOADER lives here rather than in the caller so the two tables are
        # read only by this module's own code. That is what lets engine CODE
        # banking co-locate them into the scenes bank instead of pinning ~2 KB
        # of palette words in the GB's resident image (measured on the 17-room
        # reference-engine import). A graceful no-op on a console with one palette
        # per layer, so the caller needs no `if platform` fork.
        # SMS/GG load their CRAM from HERE, in banked scenes code, instead of
        # through the palette.load_*_set prelude helpers: a prelude helper is
        # RESIDENT (it cannot bank), and the 5-5-5 -> native conversion via
        # gbs_rgb costs ~275 B of bank 0 -- on a console whose spare resident
        # bytes are counted in tens.
        # The tables THEMSELVES fork on the target, because how deep a console
        # is decides where the rounding can happen:
        #   * SMS (BGR-222, 2 bits a channel = 64 colours) is too shallow to
        #     round at runtime. Per-channel nearest is Euclidean-nearest in
        #     RGB and still greys out every desaturated mid-tone, which is
        #     what turned the reference-engine sample's pink parallax band into a
        #     flat grey one. Its entries are FITTED at build time instead, a
        #     whole 4-colour palette at a time (mosaik_assets.fit_palette_levels
        #     -- CIE94, entries kept distinct and in lightness order, and a
        #     chromatic entry never allowed to land on grey). One BYTE per
        #     entry, so the table is also half the size and the load is a
        #     plain copy.
        #   * Game Gear (BGR-444, 4096 colours) is deep enough that
        #     per-channel nearest is within a few units of ideal, so it keeps
        #     the portable 5-5-5 table and the runtime `cram()`.
        # The staging buffer's WIDTH must follow the console's own
        # palette_color_t: SMS CRAM is ONE byte per entry, the Game Gear's is
        # TWO, and set_palette reads the native type. A u16 buffer on the SMS
        # hands the hardware a zero high byte as every SECOND colour -- which
        # renders as black every other entry (it turned that same room's sky
        # black while the bands between stayed right).
        bkg_pal_sms = [w for i in range(len(scenes))
                       for w in _pal_set(fold_bsel[i], pal_sms)]
        spr_pal_sms = [w for i in range(len(scenes))
                       for w in _pal_set(fold_ssel[i], pal_sms)]
        L.append('    if platform == "sms" {')
        L.append("        -- One CRAM entry per BYTE here, fitted at build")
        L.append("        -- time into the 64-colour BGR-222 gamut.")
        L.append("        var CRAM_BUF: array[u8, 32]")
        _emit_array(L, "u8", "BKG_PAL", len(bkg_pal_sms), bkg_pal_sms,
                    per_line=16)
        L.append("")
        _emit_array(L, "u8", "SPR_PAL", len(spr_pal_sms), spr_pal_sms,
                    per_line=16)
        L.append("    } else {")
        if folding:
            # The Game Gear, NES and PC Engine address 4 slots a layer: the
            # FOLDED selection (palette_fold) in folded slot order.
            bkg_pal_f = [w for i in range(len(scenes))
                         for w in _pal_set(fold_bsel[i])]
            spr_pal_f = [w for i in range(len(scenes))
                         for w in _pal_set(fold_ssel[i])]
            L.append('    if platform == "gamegear" or platform == "nes" '
                     'or platform == "pce" {')
            L.append("        -- Folded to the 4 hardware slots (palette_fold).")
            _emit_array(L, "u16", "BKG_PAL", len(bkg_pal_f), bkg_pal_f,
                        per_line=8)
            L.append("")
            _emit_array(L, "u16", "SPR_PAL", len(spr_pal_f), spr_pal_f,
                        per_line=8)
            L.append("    } else {")
        _emit_array(L, "u16", "BKG_PAL", len(bkg_pal_all), bkg_pal_all,
                    per_line=8)
        L.append("")
        _emit_array(L, "u16", "SPR_PAL", len(spr_pal_all), spr_pal_all,
                    per_line=8)
        if folding:
            L.append("    }")
        L.append('        if platform == "gamegear" {')
        L.append("            -- ... and one per WORD here (BGR-444).")
        L.append("            var CRAM_BUF: array[u16, 32]")
        _emit_array(L, "u8", "CRAM5", 32,
                    [((v << 3) * 15 + 127) // 255 for v in range(32)])
        L.append("        } else {")
        L.append("        }")
        L.append("    }")
        L.append('    if platform == "gamegear" {')
        L.append("        -- One authored 5-5-5 word -> a native CRAM entry.")
        L.append("        function cram(w: u16) -> u16 {")
        L.append("            var r: u16 = CRAM5[(w >> 10) & 31]")
        L.append("            var g: u16 = CRAM5[(w >> 5) & 31]")
        L.append("            var b: u16 = CRAM5[w & 31]")
        L.append("            return r | (g << 4) | (b << 8)")
        L.append("        }")
        L.append("    } else {")
        L.append("    }")
        L.append("")
        L.append("    -- Load `scene`'s hardware palette set (background +")
        L.append("    -- sprite). Call it on room load, before painting.")
        L.append("    function load_palettes(scene: u8) {")
        L.append("        var off: u16 = scene")
        L.append("        off = off * PAL_STRIDE")
        L.append('        if platform == "sms" {')
        L.append("            -- Stage both banks (16 entries a layer = the")
        L.append("            -- 4 folded slots; bank 1 is the sprites') and")
        L.append("            -- load them in ONE native call. The entries are")
        L.append("            -- already native, so this is a plain copy.")
        L.append("            var i: u8 = 0")
        L.append("            while i < 16 {")
        L.append("                CRAM_BUF[i] = BKG_PAL[off + i]")
        L.append("                CRAM_BUF[i + 16] = SPR_PAL[off + i]")
        L.append("                i += 1")
        L.append("            }")
        L.append("            palette.load_native(0, 2, CRAM_BUF)")
        L.append("        } else {")
        L.append('            if platform == "gamegear" {')
        L.append("                var i: u8 = 0")
        L.append("                while i < 16 {")
        L.append("                    CRAM_BUF[i] = cram(BKG_PAL[off + i])")
        L.append("                    CRAM_BUF[i + 16] = cram(SPR_PAL[off + i])")
        L.append("                    i += 1")
        L.append("                }")
        L.append("                palette.load_native(0, 2, CRAM_BUF)")
        L.append("            } else {")
        # NES / PC Engine have FOUR slots and their setters mask the slot with
        # `& 3`, so loading all 8 wrote slots 4..7 back over 0..3 (every tile
        # then drew through slot 0's colours). They load 4. Emitted only for a
        # project that builds for one of them, so every other world's module
        # is byte-identical.
        from ..palette_fold import project_targets
        if {"nes", "pce"} & set(project_targets(c.base_dir)):
            L.append('                if platform == "nes" or platform == "pce" {')
            L.append("                    palette.load_bkg_set(0, 4, BKG_PAL, off)")
            L.append("                    palette.load_sprite_set(0, 4, SPR_PAL, off)")
            L.append("                } else {")
            L.append("                    palette.load_bkg_set(0, PAL_SLOTS, BKG_PAL, off)")
            L.append("                    palette.load_sprite_set(0, PAL_SLOTS, SPR_PAL, off)")
            L.append("                }")
        else:
            L.append("                palette.load_bkg_set(0, PAL_SLOTS, BKG_PAL, off)")
            L.append("                palette.load_sprite_set(0, PAL_SLOTS, SPR_PAL, off)")
        L.append("            }")
        L.append("        }")
        L.append("    }")
        L.append("")
    if kind_pal:
        # Per-KIND sprite palette SLOT ([kind_palettes] in the world). An actor
        # is drawn as one metasprite, so the slot is per KIND, not per OAM
        # object: the reference engine can colour the tiles WITHIN one actor differently
        # (its player's hair and body take OBJ palettes 0 and 1), which this
        # cannot express - a converter should report that as a drop.
        L.append("    -- Sprite palette SLOT per object kind ([kind_palettes]).")
        L.append("    -- Applied at room load with sprite.set_palette on the")
        L.append("    -- actor's OAM base, which fans it over the metasprite.")
        if spr_fold_data:
            remap = spr_fold_data[0]
            L.append('    if platform == "sms" or platform == "gamegear" or '
                     'platform == "nes" or platform == "pce" {')
            L.append("        -- Folded to the 4 hardware sprite slots.")
            _emit_array(L, "u8", "KIND_PAL", len(kind_pal),
                        [remap[s & 7] for s in kind_pal])
            L.append("    } else {")
        _emit_array(L, "u8", "KIND_PAL", len(kind_pal), kind_pal)
        if spr_fold_data:
            L.append("    }")
        if is_vm:
            _emit_reader(L, "kind_pal_at", "KIND_PAL", "u8")
        L.append("")
    if kind_tpal:
        L.append("    -- A palette per 8x8 CELL of each kind's metasprite")
        L.append("    -- ([kind_tile_palettes]), row-major -- so ONE actor can")
        L.append("    -- wear several. Applied at room load with")
        L.append("    -- sprite.set_meta_palettes; KIND_TPAL_OFF[kind] is the")
        L.append("    -- start of that kind's row.")
        if spr_fold_data:
            remap = spr_fold_data[0]
            L.append('    if platform == "sms" or platform == "gamegear" or '
                     'platform == "nes" or platform == "pce" {')
            L.append("        -- Folded to the 4 hardware sprite slots.")
            _emit_array(L, "u8", "KIND_TPAL", len(kind_tpal),
                        [remap[s & 7] for s in kind_tpal])
            L.append("    } else {")
        _emit_array(L, "u8", "KIND_TPAL", len(kind_tpal), kind_tpal)
        if spr_fold_data:
            L.append("    }")
        L.append("")
        _emit_array(L, _dim_type(*kind_tpal_off), "KIND_TPAL_OFF",
                    len(kind_tpal_off), kind_tpal_off)
        L.append("")
        L.append("    -- Colour the actor whose metasprite starts at OAM `base`.")
        L.append("    -- Lives HERE, not in the caller, so the two tables above")
        L.append("    -- are read only by this module's own code and can")
        L.append("    -- co-locate into its ROM bank instead of staying")
        L.append("    -- resident. `w`/`h` are the kind's metasprite size in")
        L.append("    -- 8x8 tiles (the caller knows it; the clips module owns it).")
        L.append("    function paint_actor(base: u8, kind: u8, w: u8, h: u8) {")
        # SMS/GG have no per-sprite palette select, so a per-CELL map has
        # nowhere to go there - a kind's slot rides its sheet UPLOAD instead
        # (sprite.set_data_pal). Folding the body away also keeps the
        # gbs_set_meta_pal fan machinery out of their RESIDENT prelude.
        L.append('        if platform == "sms" or platform == "gamegear" {')
        L.append("        } else {")
        L.append("            sprite.set_meta_palettes(base, w, h, KIND_TPAL, KIND_TPAL_OFF[kind])")
        L.append("        }")
        L.append("    }")
        L.append("")
    if has_tile_pal:
        # Per TILE index -> background palette slot, per scene. Concatenated
        # with a per-scene offset for the same reason the maps are: one symbol,
        # O(1) lookup, and adding a room adds table rows rather than code. Each
        # scene's row is as long as ITS resolved tileset (a per-scene tileset
        # world has one table per room; a shared-tileset world repeats the
        # world tile count).
        tpal_blob, tpal_off = [], []
        tpal_fold = []
        for i in range(len(scenes)):
            tpal_off.append(len(tpal_blob))
            n = scene_tiles[i][1] if (scene_tiles and scene_tiles[i]) \
                else tile_count
            row = list(scene_tpal[i] or [])
            cells = [(int(row[t]) & 7) if t < len(row) else 0
                     for t in range(n)]
            tpal_blob += cells
            if bkg_fold_rows:
                remap = bkg_fold_rows[i][0]
                tpal_fold += [remap[t] for t in cells]
        L.append("    -- Per-TILE background palette slot (0..%d), per scene."
                 % (PAL_SLOTS - 1))
        L.append("    -- A map cell's attribute is the slot of the TILE it")
        L.append("    -- holds, so this composes with stream / paint_table /")
        L.append("    -- metatiles unchanged: every reader goes through the")
        L.append("    -- map_tile it already used.")
        if bkg_fold_rows:
            L.append('    if platform == "sms" or platform == "gamegear" or '
                     'platform == "nes" or platform == "pce" {')
            L.append("        -- Folded to the 4 hardware slots (palette_fold).")
            _emit_array(L, "u8", "TILE_PAL", len(tpal_fold) or 1,
                        tpal_fold or [0], per_line=16)
            L.append("    } else {")
        _emit_array(L, "u8", "TILE_PAL", len(tpal_blob) or 1,
                    tpal_blob or [0], per_line=16)
        if bkg_fold_rows:
            L.append("    }")
        L.append("")
        _emit_array(L, _dim_type(*(tpal_off or [0])), "TILE_PAL_OFF",
                    len(tpal_off) or 1, tpal_off or [0])
        L.append("")
        L.append("    -- The background palette slot tile `t` renders with in")
        L.append("    -- `scene` -- the one lookup every colour path shares.")
        L.append("    function tile_pal(scene: u8, t: u8) -> u8 {")
        L.append("        return TILE_PAL[TILE_PAL_OFF[scene] + t]")
        L.append("    }")
        L.append("")
        shared_slotted = (not per_scene_ts) and c.bkg4_tiles is None
        if per_scene_ts or shared_slotted:
            # SMS/GG slot-rendered upload support: ts_slots copies the
            # scene's TILE_PAL row into a small RAM buffer (this function
            # BANKS with the scenes module and reads the table in place),
            # and the resident paint()/warm() then hand the buffer to ONE
            # bkg.set_data_pal call. Folded away on every other console.
            _tcs = [st[1] for st in (scene_tiles or []) if st]
            if not scene_tiles or any(st is None for st in scene_tiles):
                _tcs.append(tile_count)     # a shared-tileset scene uploads it
            n_buf = max(_tcs or [1])
            L.append('    if platform == "sms" or platform == "gamegear" {')
            L.append("        -- The current scene's per-tile palette slots,")
            L.append("        -- copied out for the slot-rendered tile upload.")
            L.append("        var TS_PAL_BUF: array[u8, %d]" % n_buf)
            L.append("")
            L.append("        function ts_slots(scene: u8, n: u8) {")
            L.append("            var t: u8 = 0")
            L.append("            while t < n {")
            L.append("                TS_PAL_BUF[t] = tile_pal(scene, t)")
            L.append("                t += 1")
            L.append("            }")
            L.append("        }")
            L.append("")
            L.append("        -- The slot-rendered tileset upload, in ONE place:")
            L.append("        -- it reads the residency seam, so it is pinned")
            L.append("        -- RESIDENT and paint()/warm() call it instead of")
            L.append("        -- each carrying a copy.")
            L.append("        function ts_upload(scene: u8) {")
            if per_scene_ts:
                _ts_arm("            ", True)
            else:
                L.append("            ts_slots(scene, TILE_COUNT)")
                L.append("            bkg.set_data_pal(0, TILE_COUNT, TILESET, "
                         "TS_PAL_BUF)")
            L.append("        }")
            L.append("    } else {")
            L.append("    }")
            L.append("")
        # paint_attrs is the PAINTED-room half: one attribute row at a time out
        # of a scratch buffer, mirroring how paint() uploads tiles. It is a
        # SEPARATE function rather than a tail of paint() so paint() stays
        # byte-identical and so a streamed (wide / roam) room -- which is never
        # painted -- can colour its columns through tile_pal instead.
        arow = max(1, min(max_scene_w, 32))
        L.append("    -- Scratch attribute row paint_attrs fills, one row at a time.")
        L.append("    var ATTR_ROW: array[u8, %d]" % arow)
        L.append("")
        L.append("    -- Upload `scene`'s background attribute map (the per-cell")
        L.append("    -- palette slots). Call it right after paint(); a graceful")
        L.append("    -- no-op on a console without per-tile palette hardware,")
        L.append("    -- which is why the caller needs no `if platform` fork.")
        L.append("    function paint_attrs(scene: u8) {")
        # SMS/GG have no attribute layer - their colour is baked into the
        # tile UPLOAD (bkg.set_data_pal) - so the whole per-cell walk folds
        # away there rather than running for a no-op set_attrs.
        L.append('        if platform == "sms" or platform == "gamegear" {')
        L.append("        } else {")
        _wh_a = ("MAP_W", "MAP_H") if uniform \
            else ("SCENE_W[scene]", "SCENE_H[scene]")
        L.append("            var w: %s = %s" % (wt, _wh_a[0]))
        L.append("            var h: %s = %s" % (ht, _wh_a[1]))
        L.append("            var aw: u8 = %d" % arow)
        L.append("            var y: u8 = 0")
        L.append("            var base: u16 = 0")
        L.append("            if w < aw { aw = w }")
        L.append("            while y < h {")
        L.append("                for x in 0..aw {")
        L.append("                    ATTR_ROW[x] = tile_pal(scene, map_tile(scene, base + x))")
        L.append("                }")
        L.append("                bkg.set_attrs(0, y, aw, 1, ATTR_ROW)")
        L.append("                base = base + w")
        L.append("                y = y + 1")
        L.append("            }")
        L.append("        }")
        L.append("    }")
        L.append("")

    # Object / door / trigger tables + the VM8 entity script slots
    # These are the packed parallel-array entity
    # tables the running game indexes by flatten position, so per-console CONTENT
    # FILTERING forks the WHOLE
    # region into `if platform` guards (one per target BUCKET) rather than editing
    # array elements. GATING (which symbols/types exist) is fixed by the FULL
    # unfiltered view so every branch declares the same exported symbols with the
    # same widths; only the array CONTENTS + re-indexed selector bodies differ per
    # bucket. An untagged world has a single bucket and is emitted guard-free =
    # BYTE-IDENTICAL to before (the additive rule).

