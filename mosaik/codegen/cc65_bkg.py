"""Cc65Backend <concern> methods, mixed into Cc65Backend.

Split out of the former monolithic cc65.py; a plain mixin class - methods run
against the full CodeGenerator instance (self.emit, self.caps, ...)."""


class Cc65BkgMixin:
    def _emit_cc65_bkg_engine(self, prof):
        """Suzy background-tilemap engine for the Atari Lynx (graphics.bkg).

        The Lynx has no tilemap layer -- its video model is a framebuffer plus
        the Suzy sprite blitter, so "everything on screen is a sprite". The
        engine keeps the Game Boy background *model* (a 256-entry 8x8 2bpp tile
        table + a 32x32 tile map = a 256x256 px world that scrolls with u8 wrap),
        and draws it as a **vertical stack of full-map-width row strips**: one
        literal Suzy sprite per visible map row (256 px wide = the whole 32-tile
        map width), positioned independently each frame. This is the structure
        the SPRDEMO4 reference uses -- it makes scrolling a matter of *moving*
        SCBs, never of re-laying-out pixels:

          * Horizontal scroll is **pure SCB position** (`hpos = -x`): the strip
            already holds all 32 columns, so there is *no* recomposite when the
            camera crosses a vertical tile boundary -- only an extra wrap copy at
            `hpos + 256` to cover the seam.
          * Vertical scroll recomposites **exactly one strip** per horizontal
            tile boundary (the row newly scrolled in at top/bottom); the strips
            form a ring so the other rows keep their pixels and just move by
            `vpos`. The fractional 0..7 px scroll is the strips' position.

        So the per-frame cost is constant and small (set ~2*STRIPS SCB fields +
        blit), with at most a single 256-byte strip recomposite when crossing a
        tile -- replacing the old single-windowed-sprite engine's full-window
        recomposite spike (the whole win_w x win_h x 8 raster, ~5 KB of copies)
        that landed on one frame every 8 px of scroll and stuttered. The strip
        count is rounded up to a power of two that divides the 32-row map so the
        ring rotates by exactly +1 slot per tile step even across the map's
        vertical wrap (no recomposite burst at the wrap seam).

        Strips are TYPE_BACKNONCOLL (a "background" sprite: pen 0 is painted,
        not transparent -- GB bkg colour 0 is a real colour) and map GB colours
        0..3 to black / light-grey / grey / white, matching the sprite engine's
        palette so sprites layered on top read consistently. Because the strips
        cover the whole screen, present skips its full-screen clear while the
        background is visible.

        Memory: the logical 32x32 map (1 KB) + a 4 KB packed tile table + the
        strip buffers (STRIPS x 8 rows x (1 offset + 64 data + 1 pad) + 1
        terminator) -- ~13 KB total on the Lynx, which is why this engine is
        only emitted for programs that import graphics.bkg.

        A WIDE streamed level (engine.scroll) instead uses the transposed
        per-COLUMN layout (`_emit_cc65_bkg_engine_wide`), because the row-strip
        model thrashes on horizontal streaming (every set_tiles stales all rows).
        """
        if self.cc65_wide_scroll:
            self._emit_cc65_bkg_engine_wide(prof)
            return
        sw = prof.get('screen_w', 160)
        sh = prof.get('screen_h', 102)
        win_h = (sh + 7) // 8 + 1   # visible rows + one tile of margin
        # A ring of `strips` row strips (a power of two dividing the 32-row map,
        # so the ring rotates +1 per tile step cleanly across the vertical wrap).
        # Each strip spans the whole 256 px scroll period PLUS the screen width,
        # so a SINGLE strip at hpos = -x covers the screen at any horizontal
        # scroll (no wrap copy, which had doubled the SCB count and flickered
        # foreground sprites on real hardware). Vertical scroll recomposites one
        # strip per tile crossing; the off-screen row entering at the bottom is
        # pre-composed over the frames before it scrolls in (amortized), so the
        # per-tile recomposite never lands as a spike on a single frame.
        strips = next(n for n in (4, 8, 16, 32) if n >= win_h)
        full_strip_w = (255 + sw + 7) // 8  # the full scroll-period width (tiles)
        screen_tiles = (sw + 7) // 8
        # 4bpp (16-colour) background tier ([world] lynx_bkg16, detected from the
        # scenes TILESET depth in generator._resolve_lynx_bkg_budgets): the strips
        # + packed tile table widen from 2 bytes/tile-column to 4 (all 16 Mikey
        # pens reachable, set by palette.load_bkg16), doubling the bkg BSS. Off =
        # the byte-identical 2bpp grey path.
        bpp4 = bool(getattr(self, 'lynx_bkg16', False))
        col_bytes = 4 if bpp4 else 2
        tile_bytes = 32 if bpp4 else 16
        bpp_macro = "BPP_4" if bpp4 else "BPP_2"
        # self.bkg_strip_w (an explicit [build] bkg_strip_w, or a value auto-
        # derived from the widest scene for a VM8 game -- see
        # generator._resolve_lynx_bkg_budgets) shrinks the ~13.5 KB strip BSS
        # when the world can't scroll a full period. Clamped to a coverable
        # range; None keeps the byte-identical full default.
        if self.bkg_strip_w:
            strip_w = max(screen_tiles + 1, min(full_strip_w, int(self.bkg_strip_w)))
        else:
            strip_w = full_strip_w
        strip_bytes = strip_w * col_bytes + 2   # offset byte + strip_w cols * col_bytes + pad
        self.emit("/* --- Suzy background row-strip engine (Atari Lynx) --- */")
        self.emit("/* The Lynx has no tilemap layer; the 32x32 GB map is drawn as a vertical")
        self.emit("   ring of literal row strips, each spanning the full scroll period + the")
        self.emit("   screen so ONE strip per row covers any horizontal scroll (no wrap copy).")
        self.emit("   Horizontal scroll is pure SCB position (hpos). Vertical scroll")
        self.emit("   recomposites one strip per tile crossing, pre-composed incrementally")
        self.emit("   while it is still off-screen so no single frame takes the whole hit. */")
        if self.bkg_max_tiles < 256:
            self.emit("#define GBS_BKG_MAX_TILES %d  /* [build] bkg_max_tiles: shrunk tile table */"
                      % self.bkg_max_tiles)
        else:
            self.emit("#define GBS_BKG_MAX_TILES 256")
        self.emit("#define GBS_BKG_MAP_W     32  /* full map width in tiles = scroll period */")
        if strip_w < full_strip_w:
            self.emit("#define GBS_BKG_STRIP_W   %d  /* shrunk bkg_strip_w: widest scene bounds the scroll */"
                      % strip_w)
        else:
            self.emit("#define GBS_BKG_STRIP_W   %d  /* strip width in tiles (scroll + screen) */"
                      % strip_w)
        self.emit("#define GBS_BKG_STRIPS    %d  /* visible rows + margin, divides 32 */"
                  % strips)
        self.emit("#define GBS_BKG_STRIP_BYTES %d  /* offset + STRIP_W*2 literal bytes + pad */"
                  % strip_bytes)
        self.emit("#define GBS_BKG_AMORT   16  /* strip columns composited per frame (amortize) */")
        self.emit("static uint8_t gbs_bkg_tileset[GBS_BKG_MAX_TILES][%d];  /* packed rows */"
                  % tile_bytes)
        self.emit("static uint8_t gbs_bkg_map[1024];  /* the logical 32x32 tile-index map */")
        self.emit("static uint8_t gbs_bkg_strip[GBS_BKG_STRIPS][8 * GBS_BKG_STRIP_BYTES + 1];")
        self.emit("static uint8_t gbs_bkg_strip_row[GBS_BKG_STRIPS];  /* target map row of each slot */")
        self.emit("static uint8_t gbs_bkg_strip_col[GBS_BKG_STRIPS]; /* columns composited so far (==STRIP_W: done) */")
        # Static-camera fast path: once every strip is composed AND the camera has
        # not moved, the strip SCBs are already positioned, so present skips the
        # per-strip vp/offscreen recompute + SCB rewrites and just re-blits the
        # cached visible range (the double buffer still needs both pages drawn).
        # A map/tile change clears gbs_bkg_built; a scroll mismatches px,py.
        self.emit("static uint8_t gbs_bkg_px = 0xFF, gbs_bkg_py = 0xFF;  /* camera the strips are positioned for */")
        self.emit("static uint8_t gbs_bkg_built = 0;   /* all strips composed: the static-camera fast path is valid */")
        self.emit("static uint8_t gbs_bkg_vlo = 0, gbs_bkg_vhi = 0;  /* cached visible strip range [vlo,vhi) */")
        self.emit("/* One screen-spanning strip per visible row -- no wrap copies. */")
        self.emit("static SCB_REHV_PAL gbs_bkg_scb[GBS_BKG_STRIPS];")
        self.emit("static uint8_t gbs_bkg_used = 0;     /* engine active this program */")
        self.emit("static uint8_t gbs_bkg_visible = 1;")
        self.emit("static uint8_t gbs_bkg_x = 0, gbs_bkg_y = 0;  /* scroll, wraps mod 256 */")
        self.emit("static uint8_t gbs_bkg_inited = 0;")
        self.emit("static void gbs_bkg_init(void) {")
        self.emit("    uint8_t s, r, i;")
        self.emit("    uint8_t *o;")
        self.emit("    if (gbs_bkg_inited) return;")
        if self.palette_imported:
            self.emit("    gbs_pal_init();  /* grey-ramp pen defaults */")
        self.emit("    /* Pre-write each strip's line records; compose only fills the data. */")
        self.emit("    for (s = 0; s < GBS_BKG_STRIPS; ++s) {")
        self.emit("        o = gbs_bkg_strip[s];")
        self.emit("        for (r = 0; r < 8; ++r) {")
        self.emit("            o[0] = GBS_BKG_STRIP_BYTES;            /* offset to next line */")
        self.emit("            o[GBS_BKG_STRIP_BYTES - 1] = 0x00;     /* pad closes the line */")
        self.emit("            o += GBS_BKG_STRIP_BYTES;")
        self.emit("        }")
        self.emit("        *o = 0x00;  /* end of this strip's sprite data */")
        self.emit("        gbs_bkg_strip_row[s] = 0xFF;  /* stale */")
        self.emit("        gbs_bkg_strip_col[s] = 0;")
        self.emit("    }")
        self.emit("    /* A *background* sprite: pen 0 paints (GB bkg colour 0 is a real")
        self.emit("       colour, not transparent), no collision. */")
        self.emit("    for (s = 0; s < GBS_BKG_STRIPS; ++s) {")
        self.emit("        gbs_bkg_scb[s].sprctl0 = %s | TYPE_BACKNONCOLL;" % bpp_macro)
        self.emit("        gbs_bkg_scb[s].sprctl1 = LITERAL | REHV;")
        self.emit("        gbs_bkg_scb[s].sprcoll = 0;")
        self.emit("        gbs_bkg_scb[s].next = (char *)0;")
        self.emit("        gbs_bkg_scb[s].data = gbs_bkg_strip[0];")
        self.emit("        gbs_bkg_scb[s].hpos = 0; gbs_bkg_scb[s].vpos = 0;")
        self.emit("        gbs_bkg_scb[s].hsize = 0x100; gbs_bkg_scb[s].vsize = 0x100;")
        if bpp4:
            self.emit("        /* 4bpp identity pen map: pixel index i -> pen i (all 16 Mikey")
            self.emit("           pens hold the tileset's colours, set by palette.load_bkg16). */")
            self.emit("        for (i = 0; i < 8; ++i)")
            self.emit("            gbs_bkg_scb[s].penpal[i] = (uint8_t)((i << 5) | (i << 1) | 1);")
        elif self.palette_imported:
            self.emit("        /* Pen partition: pixel 0 -> pen 0 (bkg color 0, painted --")
            self.emit("           background type), pixels 1-3 -> pens 13-15. */")
            self.emit("        gbs_bkg_scb[s].penpal[0] = 0x0D;")
            self.emit("        gbs_bkg_scb[s].penpal[1] = 0xEF;")
            self.emit("        for (i = 2; i < 8; ++i) gbs_bkg_scb[s].penpal[i] = 0;")
        else:
            self.emit("        /* GB colours 0..3 -> black, light grey, grey, white (the same")
            self.emit("           ramp as the sprite engine, plus an opaque pen 0). */")
            self.emit("        gbs_bkg_scb[s].penpal[0] = (COLOR_BLACK << 4) | COLOR_LIGHTGREY;")
            self.emit("        gbs_bkg_scb[s].penpal[1] = (COLOR_GREY << 4) | COLOR_WHITE;")
            self.emit("        for (i = 2; i < 8; ++i) gbs_bkg_scb[s].penpal[i] = 0;")
        self.emit("    }")
        self.emit("    gbs_bkg_inited = 1;")
        self.emit("}")
        if not bpp4:
            self.emit("/* Pack one GB 2bpp tile row (bitplane bytes lo/hi) into 2 literal")
            self.emit("   bytes, pixels MSB-first (same packing as the sprite engine). */")
            self.emit("static void gbs_bkg_pack_row(uint8_t lo, uint8_t hi, uint8_t *out) {")
            self.emit("    uint8_t col, bit, ci, a = 0, b = 0;")
            self.emit("    for (col = 0; col < 8; ++col) {")
            self.emit("        bit = 7 - col;")
            self.emit("        ci = (uint8_t)((((hi >> bit) & 1) << 1) | ((lo >> bit) & 1));")
            self.emit("        if (col < 4) a = (uint8_t)(a | (ci << ((3 - col) * 2)));")
            self.emit("        else         b = (uint8_t)(b | (ci << ((3 - (col - 4)) * 2)));")
            self.emit("    }")
            self.emit("    out[0] = a; out[1] = b;")
            self.emit("}")
        self.emit("void gbs_set_bkg_data(uint8_t first, uint8_t count, const uint8_t *data) {")
        self.emit("    uint16_t t; uint8_t row, s, c, hit; const uint8_t *mrow;")
        self.emit("    gbs_bkg_init();")
        if bpp4:
            # 4bpp: the source is packed-nibble (32 B/tile), which IS the Suzy
            # 4bpp literal format, so copy each tile's 32 bytes verbatim.
            if self.bkg_max_tiles < 256:
                self.emit("    for (t = 0; t < count; ++t) {")
                self.emit("        if ((uint8_t)(first + t) >= GBS_BKG_MAX_TILES) continue;  /* shrunk bkg_max_tiles: skip an out-of-budget upload */")
                self.emit("        for (row = 0; row < 32; ++row)")
                self.emit("            gbs_bkg_tileset[(uint8_t)(first+t)][row] = data[t*32+row];")
                self.emit("    }")
            else:
                self.emit("    for (t = 0; t < count; ++t)")
                self.emit("        for (row = 0; row < 32; ++row)")
                self.emit("            gbs_bkg_tileset[(uint8_t)(first+t)][row] = data[t*32+row];  /* u8 wrap = GB tile-index semantics */")
        elif self.bkg_max_tiles < 256:
            self.emit("    for (t = 0; t < count; ++t) {")
            self.emit("        if ((uint8_t)(first + t) >= GBS_BKG_MAX_TILES) continue;  /* shrunk bkg_max_tiles: skip an out-of-budget upload */")
            self.emit("        for (row = 0; row < 8; ++row)")
            self.emit("            gbs_bkg_pack_row(data[t*16+row*2], data[t*16+row*2+1], &gbs_bkg_tileset[(uint8_t)(first+t)][row*2]);")
            self.emit("    }")
        else:
            self.emit("    for (t = 0; t < count; ++t)")
            self.emit("        for (row = 0; row < 8; ++row)")
            self.emit("            gbs_bkg_pack_row(data[t*16+row*2], data[t*16+row*2+1], &gbs_bkg_tileset[(uint8_t)(first+t)][row*2]);  /* u8 wrap = GB tile-index semantics; no table overrun */")
        self.emit("    /* Targeted strip invalidation: a tile-DATA change only affects the row")
        self.emit("       strips whose CURRENT map row uses one of the changed tile indices")
        self.emit("       [first, first+count). Mark JUST those for recompose (strip_col = 0 ->")
        self.emit("       the present place loop recomposes them, keeping their assigned row),")
        self.emit("       instead of blowing away the whole 16-strip ring. An animated bkg tile")
        self.emit("       (a few map cells) then recomposes a handful of strips, not all 16 --")
        self.emit("       and the strips NOT touched keep strip_col == STRIP_W, so gbs_bkg_built")
        self.emit("       recovers to 1 and the static-camera fast path re-engages on the next")
        self.emit("       unchanged frame. A strip already stale (row 0xFF: fresh init / pending")
        self.emit("       scroll) stays stale. Wrap-safe range test: (uint8_t)(tile-first)<count. */")
        self.emit("    hit = 0;")
        self.emit("    for (s = 0; s < GBS_BKG_STRIPS; ++s) {")
        self.emit("        row = gbs_bkg_strip_row[s];")
        self.emit("        if (row == 0xFF) { hit = 1; continue; }  /* already recomposing */")
        self.emit("        mrow = &gbs_bkg_map[(uint16_t)row * 32];")
        self.emit("        for (c = 0; c < 32; ++c) {")
        self.emit("            if ((uint8_t)(mrow[c] - first) < count) {")
        self.emit("                gbs_bkg_strip_col[s] = 0;  /* force this strip to recompose */")
        self.emit("                hit = 1;")
        self.emit("                break;")
        self.emit("            }")
        self.emit("        }")
        self.emit("    }")
        self.emit("    if (hit) {")
        self.emit("        gbs_bkg_built = 0;   /* a shown strip changed -> fast path invalid until rebuilt */")
        self.emit("        gbs_force = 1;       /* present must recomposite */")
        self.emit("    }")
        self.emit("}")
        self.emit("/* GB semantics: map coords wrap mod 32; `tiles` is row-major w x h.")
        self.emit("   Writes the logical tile map and marks the affected strips stale so")
        self.emit("   they recomposite from the new map. */")
        self.emit("void gbs_set_bkg_tiles(uint8_t x, uint8_t y, uint8_t w, uint8_t h,")
        self.emit("                       const uint8_t *tiles) {")
        self.emit("    uint8_t cx, cy, s, r, hit;")
        self.emit("    gbs_bkg_init();")
        self.emit("    for (cy = 0; cy < h; ++cy)")
        self.emit("        for (cx = 0; cx < w; ++cx)")
        self.emit("            gbs_bkg_map[(uint16_t)((uint8_t)(y + cy) & 31) * 32")
        self.emit("                        + ((uint8_t)(x + cx) & 31)] = tiles[(uint16_t)cy * w + cx];")
        self.emit("    /* Targeted invalidation: a MAP change only affects the strips whose")
        self.emit("       current map row falls in the written row range [y, y+h) (wrap mod 32).")
        self.emit("       Mark just those for recompose (strip_col = 0); strips outside the")
        self.emit("       written rows keep their cached buffers + strip_col == STRIP_W, so")
        self.emit("       gbs_bkg_built recovers and the fast path re-engages. At room load the")
        self.emit("       strips are all stale (row 0xFF), so this still fully recomposes.")
        self.emit("       Wrap-safe row test: (uint8_t)(row - y) & 31 < h. */")
        self.emit("    hit = 0;")
        self.emit("    for (s = 0; s < GBS_BKG_STRIPS; ++s) {")
        self.emit("        r = gbs_bkg_strip_row[s];")
        self.emit("        if (r == 0xFF) { hit = 1; continue; }  /* already recomposing */")
        self.emit("        if ((uint8_t)((r - y) & 31) < h) {")
        self.emit("            gbs_bkg_strip_col[s] = 0;  /* force this strip to recompose */")
        self.emit("            hit = 1;")
        self.emit("        }")
        self.emit("    }")
        self.emit("    gbs_bkg_used = 1;")
        self.emit("    if (hit) {")
        self.emit("        gbs_bkg_built = 0;   /* a shown strip changed -> fast path invalid until rebuilt */")
        self.emit("        gbs_force = 1;       /* new map -> present must recomposite */")
        self.emit("    }")
        self.emit("}")
        self.emit("/* Composite columns [c0, c1) of row strip `p` from logical map row")
        self.emit("   `map_row` (copying pre-packed tile rows). The strip is STRIP_W tiles wide")
        self.emit("   (> the 32-tile map), so map columns repeat (col c shows map col c & 31)")
        self.emit("   -- that built-in horizontal wrap is what lets a single strip cover any")
        self.emit("   scroll with no wrap copy. Cells are %d bytes wide so they land on byte"
                  % col_bytes)
        self.emit("   boundaries. Composing by column range lets a strip be built")
        self.emit("   incrementally over the frames it is still off-screen (no per-tile spike). */")
        self.emit("static void gbs_bkg_compose_cols(uint8_t p, uint8_t map_row, uint8_t c0, uint8_t c1) {")
        self.emit("    const uint8_t *map_r = &gbs_bkg_map[(uint16_t)map_row * 32];")
        self.emit("    uint8_t *base = gbs_bkg_strip[p] + 1;  /* first line's data byte */")
        self.emit("    const uint8_t *gb;")
        self.emit("    uint8_t *dst;")
        self.emit("    uint8_t cx, row;")
        self.emit("    for (cx = c0; cx < c1; ++cx) {")
        self.emit("        gb = gbs_bkg_tileset[map_r[cx & 31]];")
        self.emit("        dst = base + (cx << %d);" % (2 if bpp4 else 1))
        self.emit("        for (row = 0; row < 8; ++row) {")
        if bpp4:
            self.emit("            dst[0] = gb[0]; dst[1] = gb[1]; dst[2] = gb[2]; dst[3] = gb[3];")
            self.emit("            gb += 4; dst += GBS_BKG_STRIP_BYTES;")
        else:
            self.emit("            dst[0] = gb[0]; dst[1] = gb[1];")
            self.emit("            gb += 2; dst += GBS_BKG_STRIP_BYTES;")
        self.emit("        }")
        self.emit("    }")
        self.emit("}")
        # The scroll is CHANGE-DETECTED here, not scanned in present: the
        # weighted-sum checksum that used to notice it is gone (cc65 software
        # multiplies, ~36,000 ticks a frame -- see cc65_sprite's present).
        self.emit("void gbs_move_bkg(uint8_t x, uint8_t y) {")
        self.emit("    if (gbs_bkg_x != x || gbs_bkg_y != y) {")
        self.emit("        gbs_bkg_x = x; gbs_bkg_y = y; gbs_force = 1;")
        self.emit("    }")
        self.emit("    gbs_bkg_used = 1;")
        self.emit("}")
        self.emit("void gbs_scroll_bkg(int8_t dx, int8_t dy) {")
        self.emit("    if (dx || dy) {")
        self.emit("        gbs_bkg_x = (uint8_t)(gbs_bkg_x + dx);")
        self.emit("        gbs_bkg_y = (uint8_t)(gbs_bkg_y + dy);")
        self.emit("        gbs_force = 1;")
        self.emit("    }")
        self.emit("    gbs_bkg_used = 1;")
        self.emit("}")

    def _emit_cc65_bkg_engine_wide(self, prof):
        """Suzy background engine, COLUMN-strip variant for a wide streamed level.

        The default Lynx bkg engine (above) draws the 32x32 GB map as a ring of
        screen-spanning ROW strips, tuned for vertical scroll: `bkg.set_tiles`
        marks EVERY strip stale (any map write can touch any row), and the
        recompositor amortizes a strip in over the ~16 frames it is off-screen.
        That is the wrong axis for a wide horizontally-STREAMED level
        (`engine.scroll`), which rewrites ONE column every 8 px of scroll -- every
        frame all the row strips go stale and the amortizer never catches up, so
        the screen stays near-blank (a thrash, not a logic bug).

        Transpose the layout: draw the visible window as per-COLUMN strips -- one
        Suzy sprite per map column (8 px wide, screen-tall), positioned
        horizontally by the scroll. There are exactly GBS_BKG_MAP_W (32) of them,
        so strip slot c always holds map column c (no ring rotation needed).
        `engine.scroll`'s single-column `set_tiles` then recomposites EXACTLY
        that one column strip (the revealed column), and horizontal scroll is
        pure SCB hpos (the ring's cheap axis, just transposed). The ~21 visible
        column strips blitted per frame is the same Suzy budget as the 16 row
        strips, so the known Lynx flicker tradeoff is unchanged.

        Vertical scroll is NOT supported here (the wide "h" path is screen-tall,
        camy == 0); a both-axes wide Lynx level would need both layouts or a 2D
        tile cache -- a separate follow-up.
        """
        sw = prof.get('screen_w', 160)
        sh = prof.get('screen_h', 102)
        col_h = (sh + 7) // 8 + 1    # visible rows + one for the sub-tile vertical offset
        col_vis = (sw + 7) // 8 + 1  # visible columns + one for the sub-tile pan
        # 4bpp (16-colour) background tier ([world] lynx_bkg16): the per-column
        # literal strips + tile table widen from 2 to 4 literal bytes per 8px row.
        # A wide 16-colour level is bounded by bkg_max_tiles (the tile table) since
        # the column strips are fixed at the 32-column map width. Off = 2bpp.
        bpp4 = bool(getattr(self, 'lynx_bkg16', False))
        data_bytes = 4 if bpp4 else 2   # literal bytes per 8px tile row
        tile_bytes = 32 if bpp4 else 16
        bpp_macro = "BPP_4" if bpp4 else "BPP_2"
        col_bytes = data_bytes + 2      # line record: offset + literal data + pad
        self.emit("/* --- Suzy background COLUMN-strip engine (Atari Lynx, wide stream) --- */")
        self.emit("/* The Lynx has no tilemap layer. For a WIDE level streamed by")
        self.emit("   engine.scroll (one column rewritten per 8 px of horizontal scroll),")
        self.emit("   the 32 GB map columns are drawn as 32 per-column literal strips (8 px")
        self.emit("   wide, screen-tall). Strip slot c holds map column c, so a streamed")
        self.emit("   single-column set_tiles recomposites exactly ONE strip; horizontal")
        self.emit("   scroll is pure SCB hpos. Screen-tall only (no vertical scroll). */")
        if self.bkg_max_tiles < 256:
            self.emit("#define GBS_BKG_MAX_TILES %d  /* [build] bkg_max_tiles: shrunk tile table */"
                      % self.bkg_max_tiles)
        else:
            self.emit("#define GBS_BKG_MAX_TILES 256")
        self.emit("#define GBS_BKG_MAP_W     32  /* map width in tiles = column count */")
        self.emit("#define GBS_BKG_COL_H     %d  /* visible rows in tiles (strip height) */"
                  % col_h)
        self.emit("#define GBS_BKG_COL_VIS   %d  /* visible columns drawn per frame */"
                  % col_vis)
        self.emit("#define GBS_BKG_COL_BYTES %d  /* offset + %d literal bytes + pad */"
                  % (col_bytes, data_bytes))
        self.emit("#define GBS_BKG_COL_LINES (GBS_BKG_COL_H * 8)  /* scanlines per strip */")
        self.emit("static uint8_t gbs_bkg_tileset[GBS_BKG_MAX_TILES][%d];  /* packed rows */"
                  % tile_bytes)
        self.emit("static uint8_t gbs_bkg_map[1024];  /* the logical 32x32 tile-index map */")
        self.emit("static uint8_t gbs_bkg_strip[GBS_BKG_MAP_W][GBS_BKG_COL_LINES * GBS_BKG_COL_BYTES + 1];")
        self.emit("/* One screen-tall strip per map column. */")
        self.emit("static SCB_REHV_PAL gbs_bkg_scb[GBS_BKG_MAP_W];")
        self.emit("static uint8_t gbs_bkg_used = 0;     /* engine active this program */")
        self.emit("static uint8_t gbs_bkg_visible = 1;")
        self.emit("static uint8_t gbs_bkg_x = 0, gbs_bkg_y = 0;  /* scroll, wraps mod 256 */")
        self.emit("static uint8_t gbs_bkg_inited = 0;")
        self.emit("static void gbs_bkg_init(void) {")
        self.emit("    uint8_t s, r, i;")
        self.emit("    uint8_t *o;")
        self.emit("    if (gbs_bkg_inited) return;")
        if self.palette_imported:
            self.emit("    gbs_pal_init();  /* grey-ramp pen defaults */")
        self.emit("    /* Pre-write each strip's line records; compose only fills the data. */")
        self.emit("    for (s = 0; s < GBS_BKG_MAP_W; ++s) {")
        self.emit("        o = gbs_bkg_strip[s];")
        self.emit("        for (r = 0; r < GBS_BKG_COL_LINES; ++r) {")
        self.emit("            o[0] = GBS_BKG_COL_BYTES;            /* offset to next line */")
        self.emit("            o[GBS_BKG_COL_BYTES - 1] = 0x00;     /* pad closes the line */")
        self.emit("            o += GBS_BKG_COL_BYTES;")
        self.emit("        }")
        self.emit("        *o = 0x00;  /* end of this strip's sprite data */")
        self.emit("    }")
        self.emit("    /* A *background* sprite: pen 0 paints (GB bkg colour 0 is a real")
        self.emit("       colour, not transparent), no collision. */")
        self.emit("    for (s = 0; s < GBS_BKG_MAP_W; ++s) {")
        self.emit("        gbs_bkg_scb[s].sprctl0 = %s | TYPE_BACKNONCOLL;" % bpp_macro)
        self.emit("        gbs_bkg_scb[s].sprctl1 = LITERAL | REHV;")
        self.emit("        gbs_bkg_scb[s].sprcoll = 0;")
        self.emit("        gbs_bkg_scb[s].next = (char *)0;")
        self.emit("        gbs_bkg_scb[s].data = gbs_bkg_strip[0];")
        self.emit("        gbs_bkg_scb[s].hpos = 0; gbs_bkg_scb[s].vpos = 0;")
        self.emit("        gbs_bkg_scb[s].hsize = 0x100; gbs_bkg_scb[s].vsize = 0x100;")
        if bpp4:
            self.emit("        /* 4bpp identity pen map: pixel index i -> pen i (all 16 Mikey")
            self.emit("           pens hold the tileset's colours, set by palette.load_bkg16). */")
            self.emit("        for (i = 0; i < 8; ++i)")
            self.emit("            gbs_bkg_scb[s].penpal[i] = (uint8_t)((i << 5) | (i << 1) | 1);")
        elif self.palette_imported:
            self.emit("        /* Pen partition: pixel 0 -> pen 0 (bkg color 0, painted --")
            self.emit("           background type), pixels 1-3 -> pens 13-15. */")
            self.emit("        gbs_bkg_scb[s].penpal[0] = 0x0D;")
            self.emit("        gbs_bkg_scb[s].penpal[1] = 0xEF;")
            self.emit("        for (i = 2; i < 8; ++i) gbs_bkg_scb[s].penpal[i] = 0;")
        else:
            self.emit("        /* GB colours 0..3 -> black, light grey, grey, white (the same")
            self.emit("           ramp as the sprite engine, plus an opaque pen 0). */")
            self.emit("        gbs_bkg_scb[s].penpal[0] = (COLOR_BLACK << 4) | COLOR_LIGHTGREY;")
            self.emit("        gbs_bkg_scb[s].penpal[1] = (COLOR_GREY << 4) | COLOR_WHITE;")
            self.emit("        for (i = 2; i < 8; ++i) gbs_bkg_scb[s].penpal[i] = 0;")
        self.emit("    }")
        self.emit("    gbs_bkg_inited = 1;")
        self.emit("}")
        if not bpp4:
            self.emit("/* Pack one GB 2bpp tile row (bitplane bytes lo/hi) into 2 literal")
            self.emit("   bytes, pixels MSB-first (same packing as the sprite engine). */")
            self.emit("static void gbs_bkg_pack_row(uint8_t lo, uint8_t hi, uint8_t *out) {")
            self.emit("    uint8_t col, bit, ci, a = 0, b = 0;")
            self.emit("    for (col = 0; col < 8; ++col) {")
            self.emit("        bit = 7 - col;")
            self.emit("        ci = (uint8_t)((((hi >> bit) & 1) << 1) | ((lo >> bit) & 1));")
            self.emit("        if (col < 4) a = (uint8_t)(a | (ci << ((3 - col) * 2)));")
            self.emit("        else         b = (uint8_t)(b | (ci << ((3 - (col - 4)) * 2)));")
            self.emit("    }")
            self.emit("    out[0] = a; out[1] = b;")
            self.emit("}")
        self.emit("/* Composite one column strip from logical map column `col`, starting at")
        self.emit("   the row of the vertical scroll offset (gbs_bkg_y>>3): stack GBS_BKG_COL_H")
        self.emit("   map cells top-to-bottom, copying each tile's 8 pre-packed 2-byte rows.")
        self.emit("   The fractional 0..7 px of gbs_bkg_y is applied as the strip's vpos in")
        self.emit("   present, so a level taller than the screen shows from `voff` down (e.g.")
        self.emit("   its bottom on the short Lynx screen). gbs_bkg_y is constant in the h")
        self.emit("   path (a fixed offset, no vertical scroll), so composing at set_tiles is")
        self.emit("   correct. */")
        self.emit("static void gbs_bkg_compose_col(uint8_t col) {")
        self.emit("    uint8_t *line = gbs_bkg_strip[col] + 1;  /* first line's data byte */")
        self.emit("    uint8_t r0 = (uint8_t)(gbs_bkg_y >> 3);   /* top map row to show */")
        self.emit("    const uint8_t *gb;")
        self.emit("    uint8_t tr, row;")
        self.emit("    for (tr = 0; tr < GBS_BKG_COL_H; ++tr) {")
        self.emit("        gb = gbs_bkg_tileset[gbs_bkg_map[(uint16_t)((uint8_t)(r0 + tr) & 31)")
        self.emit("                                          * GBS_BKG_MAP_W + col]];")
        self.emit("        for (row = 0; row < 8; ++row) {")
        if bpp4:
            self.emit("            line[0] = gb[0]; line[1] = gb[1]; line[2] = gb[2]; line[3] = gb[3];")
            self.emit("            gb += 4; line += GBS_BKG_COL_BYTES;")
        else:
            self.emit("            line[0] = gb[0]; line[1] = gb[1];")
            self.emit("            gb += 2; line += GBS_BKG_COL_BYTES;")
        self.emit("        }")
        self.emit("    }")
        self.emit("}")
        self.emit("void gbs_set_bkg_data(uint8_t first, uint8_t count, const uint8_t *data) {")
        self.emit("    uint16_t t; uint8_t row, c;")
        self.emit("    gbs_bkg_init();")
        if bpp4:
            # 4bpp: the packed-nibble source (32 B/tile) is the Suzy literal format.
            if self.bkg_max_tiles < 256:
                self.emit("    for (t = 0; t < count; ++t) {")
                self.emit("        if ((uint8_t)(first + t) >= GBS_BKG_MAX_TILES) continue;  /* shrunk bkg_max_tiles: skip an out-of-budget upload */")
                self.emit("        for (row = 0; row < 32; ++row)")
                self.emit("            gbs_bkg_tileset[(uint8_t)(first+t)][row] = data[t*32+row];")
                self.emit("    }")
            else:
                self.emit("    for (t = 0; t < count; ++t)")
                self.emit("        for (row = 0; row < 32; ++row)")
                self.emit("            gbs_bkg_tileset[(uint8_t)(first+t)][row] = data[t*32+row];  /* u8 wrap = GB tile-index semantics */")
        elif self.bkg_max_tiles < 256:
            self.emit("    for (t = 0; t < count; ++t) {")
            self.emit("        if ((uint8_t)(first + t) >= GBS_BKG_MAX_TILES) continue;  /* shrunk bkg_max_tiles: skip an out-of-budget upload */")
            self.emit("        for (row = 0; row < 8; ++row)")
            self.emit("            gbs_bkg_pack_row(data[t*16+row*2], data[t*16+row*2+1], &gbs_bkg_tileset[(uint8_t)(first+t)][row*2]);")
            self.emit("    }")
        else:
            self.emit("    for (t = 0; t < count; ++t)")
            self.emit("        for (row = 0; row < 8; ++row)")
            self.emit("            gbs_bkg_pack_row(data[t*16+row*2], data[t*16+row*2+1], &gbs_bkg_tileset[(uint8_t)(first+t)][row*2]);  /* u8 wrap = GB tile-index semantics; no table overrun */")
        self.emit("    for (c = 0; c < GBS_BKG_MAP_W; ++c) gbs_bkg_compose_col(c);  /* tiles changed */")
        self.emit("    gbs_force = 1;   /* new tile data -> present must recomposite */")
        self.emit("}")
        self.emit("/* GB semantics: map coords wrap mod 32; `tiles` is row-major w x h.")
        self.emit("   Writes the logical map, then recomposites only the columns it touched")
        self.emit("   -- the streaming shape is w == 1, so one streamed column = one strip. */")
        self.emit("void gbs_set_bkg_tiles(uint8_t x, uint8_t y, uint8_t w, uint8_t h,")
        self.emit("                       const uint8_t *tiles) {")
        self.emit("    uint8_t cx, cy;")
        self.emit("    gbs_bkg_init();")
        self.emit("    for (cy = 0; cy < h; ++cy)")
        self.emit("        for (cx = 0; cx < w; ++cx)")
        self.emit("            gbs_bkg_map[(uint16_t)((uint8_t)(y + cy) & 31) * GBS_BKG_MAP_W")
        self.emit("                        + ((uint8_t)(x + cx) & 31)] = tiles[(uint16_t)cy * w + cx];")
        self.emit("    for (cx = 0; cx < w; ++cx) gbs_bkg_compose_col((uint8_t)((uint8_t)(x + cx) & 31));")
        self.emit("    gbs_bkg_used = 1;")
        self.emit("    gbs_force = 1;   /* new map -> present must recomposite */")
        self.emit("}")
        # The scroll is CHANGE-DETECTED here, not scanned in present: the
        # weighted-sum checksum that used to notice it is gone (cc65 software
        # multiplies, ~36,000 ticks a frame -- see cc65_sprite's present).
        self.emit("void gbs_move_bkg(uint8_t x, uint8_t y) {")
        self.emit("    if (gbs_bkg_x != x || gbs_bkg_y != y) {")
        self.emit("        gbs_bkg_x = x; gbs_bkg_y = y; gbs_force = 1;")
        self.emit("    }")
        self.emit("    gbs_bkg_used = 1;")
        self.emit("}")
        self.emit("void gbs_scroll_bkg(int8_t dx, int8_t dy) {")
        self.emit("    if (dx || dy) {")
        self.emit("        gbs_bkg_x = (uint8_t)(gbs_bkg_x + dx);")
        self.emit("        gbs_bkg_y = (uint8_t)(gbs_bkg_y + dy);")
        self.emit("        gbs_force = 1;")
        self.emit("    }")
        self.emit("    gbs_bkg_used = 1;")
        self.emit("}")

    def _emit_pce_bkg_engine(self, prof):
        """VDC background-tilemap engine for the PC Engine (graphics.bkg).

        Unlike the Lynx, the PCE has a *real* scrollable tilemap: the VDC's
        Background Attribute Table (BAT) plus the BXR/BYR scroll registers.
        The cc65 conio runtime's BAT is 128 entries wide (the full $0000-$1FFF
        block, 128x64 entries), so each cell of the 32x32 GB map is written
        eight times (columns +0/+32/+64/+96, and again at +32 rows): the
        content then repeats every 256 px both ways and a u8 BXR/BYR scroll
        (0..255) wraps seamlessly, exactly the Game Boy contract.

        Tiles are converted once from GB 2bpp into 4bpp planar background
        characters at VRAM $4000 (word addresses: the conio runtime owns
        $0000-$1FFF BAT + $2000-$2FFF font, and the sprite engine owns
        $3000 upward - `CC65_MAX_TILES` x 64 words, so $3000-$39FF at the
        current 40, NOT the $37FF this note used to say from when it was 32).
        The whole map is `docs/vram-layout.md`. Planes 2/3 are left empty. BAT entries select BG palette 1 (palette bits 12-15),
        whose entries 1..3 are set to the same GB grey ramp as the sprite
        engine -- conio text keeps using palette 0, so text and a scrolled
        background coexist (they share the layer, exactly as on the GB,
        where text prints into the background tilemap). BG colour 0 of every
        palette displays VCE entry $000 (the global background colour),
        which is left as the runtime set it (black).

        Emitted after the sprite engine: reuses gbs_vreg/gbs_vram_addr.
        """
        self.emit("/* --- VDC background-tilemap engine (PC Engine) --- */")
        self.emit("/* Real tilemap hardware: BAT entries + the BXR/BYR scroll registers.")
        self.emit("   The 32x32 GB map is written twice into the 64-wide conio BAT so a")
        self.emit("   u8 BXR scroll wraps seamlessly mod 256 (the Game Boy contract). */")
        self.emit("#define GBS_VRAM_BKG 0x4000u  /* above the sprite patterns */")
        self.emit("#define GBS_BKG_MAX_TILES 256")
        self.emit("static uint8_t gbs_bkg_x = 0, gbs_bkg_y = 0;  /* scroll, wraps mod 256 */")
        self.emit("static uint8_t gbs_bkg_inited = 0;")
        self.emit("static uint8_t gbs_bkg_scroll_dirty = 0;  /* a bkg.move awaits a vblank write */")
        if self.palette_imported:
            self.emit("/* Per-map-cell bkg palette slot (bkg.set_palette), two cells per")
            self.emit("   byte; set_tiles consults it so retiling keeps the palette. */")
            self.emit("static uint8_t gbs_bkg_pal[512];")
        self.emit("static void gbs_bkg_init(void) {")
        self.emit("    if (gbs_bkg_inited) return;")
        self.emit("    gbs_video_init();")
        if self.palette_imported:
            self.emit("    /* BG palette 2 (VCE $21-$23): the GB grey ramp -- bkg slot 0 in")
            self.emit("       the palette-mode mapping (palette 1 entry 1 is the conio text")
            self.emit("       ink). Entry 0 of a BG palette is never displayed (BG colour 0")
            self.emit("       shows VCE $000). */")
            self.emit("    (*(volatile uint8_t *)0x0402) = 0x21;  /* VCE address low */")
        else:
            self.emit("    /* BG palette 1 (VCE $11-$13): the GB grey ramp, same colours as")
            self.emit("       the sprite engine. Entry 0 of a BG palette is never displayed")
            self.emit("       (BG colour 0 shows VCE $000), so start at $11. */")
            self.emit("    (*(volatile uint8_t *)0x0402) = 0x11;  /* VCE address low */")
        self.emit("    (*(volatile uint8_t *)0x0403) = 0x00;  /* VCE address high */")
        self.emit("    (*(volatile uint8_t *)0x0404) = 0xB6;  /* 1: light grey */")
        self.emit("    (*(volatile uint8_t *)0x0405) = 0x01;  /* (autoincrements) */")
        self.emit("    (*(volatile uint8_t *)0x0404) = 0xDB;  /* 2: dark grey */")
        self.emit("    (*(volatile uint8_t *)0x0405) = 0x00;")
        self.emit("    (*(volatile uint8_t *)0x0404) = 0xFF;  /* 3: white */")
        self.emit("    (*(volatile uint8_t *)0x0405) = 0x01;")
        self.emit("    gbs_bkg_inited = 1;")
        self.emit("}")
        if self.load_bkg16_used:
            # 4bpp BACKGROUND tier.
            # A load_bkg16 program feeds a 32 B/tile packed-nibble tileset (the
            # scenes.mos `if platform == "pce"` fork), so fill ALL FOUR VDC planes
            # from the 16-colour source instead of the 2bpp two-plane path. The
            # 16 colours are loaded into VCE BG palette 2 by gbs_load_bkg_pal16.
            self.emit("/* One packed-nibble 4bpp tile (32 bytes = 8 rows x 4 bytes, two")
            self.emit("   pixels per byte, leftmost pixel in the high nibble) -> a VDC 4bpp")
            self.emit("   planar character: 16 words, rows 0-7 as plane0(lo)/plane1(hi)")
            self.emit("   then rows 0-7 as plane2(lo)/plane3(hi). */")
            self.emit("static uint8_t gbs_bkg_plane(const uint8_t *r, uint8_t k) {")
            self.emit("    uint8_t out = 0, i, px;")
            self.emit("    for (i = 0; i < 8; ++i) {")
            self.emit("        px = (uint8_t)((i & 1) ? (r[i >> 1] & 0x0F) : (r[i >> 1] >> 4));")
            self.emit("        out = (uint8_t)(out | (uint8_t)(((px >> k) & 1) << (7 - i)));")
            self.emit("    }")
            self.emit("    return out;")
            self.emit("}")
            self.emit("void gbs_set_bkg_data(uint8_t first, uint8_t count, const uint8_t *data) {")
            self.emit("    uint16_t t;")
            self.emit("    uint8_t row;")
            self.emit("    const uint8_t *tile;")
            self.emit("    gbs_bkg_init();")
            self.emit("    for (t = 0; t < count; ++t) {")
            self.emit("        if ((uint16_t)first + t >= GBS_BKG_MAX_TILES) break;")
            self.emit("        tile = data + (uint16_t)t * 32;")
            self.emit("        gbs_vram_addr(GBS_VRAM_BKG + (((uint16_t)first + t) << 4));")
            self.emit("        for (row = 0; row < 8; ++row) {")
            self.emit("            GBS_VDC_DL = gbs_bkg_plane(tile + (uint16_t)row * 4, 0);")
            self.emit("            GBS_VDC_DH = gbs_bkg_plane(tile + (uint16_t)row * 4, 1);")
            self.emit("        }")
            self.emit("        for (row = 0; row < 8; ++row) {")
            self.emit("            GBS_VDC_DL = gbs_bkg_plane(tile + (uint16_t)row * 4, 2);")
            self.emit("            GBS_VDC_DH = gbs_bkg_plane(tile + (uint16_t)row * 4, 3);")
            self.emit("        }")
            self.emit("    }")
            self.emit("}")
        else:
            self.emit("/* Convert one GB 2bpp tile into a 4bpp planar background character:")
            self.emit("   16 words = rows 0-7 as plane0(lo)/plane1(hi), rows 8-15 planes 2+3")
            self.emit("   (empty). The GB row bytes are exactly the two planes. */")
            self.emit("void gbs_set_bkg_data(uint8_t first, uint8_t count, const uint8_t *data) {")
            self.emit("    uint16_t t;")
            self.emit("    uint8_t row;")
            self.emit("    gbs_bkg_init();")
            self.emit("    for (t = 0; t < count; ++t) {")
            self.emit("        if ((uint16_t)first + t >= GBS_BKG_MAX_TILES) break;")
            self.emit("        gbs_vram_addr(GBS_VRAM_BKG + (((uint16_t)first + t) << 4));")
            self.emit("        for (row = 0; row < 8; ++row) {")
            self.emit("            GBS_VDC_DL = data[t * 16 + row * 2];      /* plane 0 */")
            self.emit("            GBS_VDC_DH = data[t * 16 + row * 2 + 1];  /* plane 1 */")
            self.emit("        }")
            self.emit("        for (row = 0; row < 8; ++row) { GBS_VDC_DL = 0; GBS_VDC_DH = 0; }")
            self.emit("    }")
            self.emit("}")
        self.emit("/* GB semantics: map coords wrap mod 32. The conio runtime's BAT is")
        self.emit("   128 entries wide (the full $0000-$1FFF VRAM block), so each entry")
        self.emit("   is replicated at +32/+64/+96 columns and +32 rows -- the content")
        self.emit("   then repeats every 256 px both ways and the u8 BXR/BYR scroll")
        self.emit("   wraps seamlessly, exactly the Game Boy contract. */")
        self.emit("void gbs_set_bkg_tiles(uint8_t x, uint8_t y, uint8_t w, uint8_t h,")
        self.emit("                       const uint8_t *tiles) {")
        if self.palette_imported:
            self.emit("    uint8_t cx, cy, rep, xx, yy, pal;")
            self.emit("    uint16_t bat, entry, idx;")
        else:
            self.emit("    uint8_t cx, cy, rep;")
            self.emit("    uint16_t bat, entry;")
        self.emit("    gbs_bkg_init();")
        self.emit("    for (cy = 0; cy < h; ++cy) {")
        self.emit("        for (cx = 0; cx < w; ++cx) {")
        if self.palette_imported:
            self.emit("            /* The cell's palette slot (default 0 -> VCE BG palette 2;")
            self.emit("               palette 1 is the conio text ink) in bits 12-15;")
            self.emit("               character code = word addr / 16. */")
            self.emit("            xx = (uint8_t)((x + cx) & 31); yy = (uint8_t)((y + cy) & 31);")
            self.emit("            idx = ((uint16_t)yy << 5) | xx;")
            self.emit("            pal = (uint8_t)((gbs_bkg_pal[idx >> 1] >> ((idx & 1) << 2)) & 3);")
            self.emit("            entry = (uint16_t)(((uint16_t)(pal + 2) << 12)")
            self.emit("                    | ((GBS_VRAM_BKG >> 4) + tiles[(uint16_t)cy * w + cx]));")
            self.emit("            bat = ((uint16_t)yy << 7) + xx;  /* 128-wide BAT */")
        else:
            self.emit("            /* Palette 1 in bits 12-15; character code = word addr / 16. */")
            self.emit("            entry = (uint16_t)(0x1000u | ((GBS_VRAM_BKG >> 4)")
            self.emit("                    + tiles[(uint16_t)cy * w + cx]));")
            self.emit("            bat = ((uint16_t)((uint8_t)(y + cy) & 31) << 7)  /* 128-wide */")
            self.emit("                + (uint16_t)((uint8_t)(x + cx) & 31);")
        self.emit("            /* Write the cell IMMEDIATELY (4 column copies + the +32-row")
        self.emit("               replica), the same path a 2D streamed level (engine.scroll2d)")
        self.emit("               uses -- which scrolls cleanly on the PCE. A wide (engine.scroll)")
        self.emit("               level used to queue these writes for a vblank drain, on the")
        self.emit("               theory that mid-frame BAT writes flicker; but the real flicker")
        self.emit("               was the mid-frame SCROLL-register write (now deferred to vblank")
        self.emit("               in gbs_bkg_scroll_flush), and the deferred BAT queue itself made")
        self.emit("               the streamed background SHAKE. So write straight through here,")
        self.emit("               like the working 2D path. */")
        self.emit("            for (rep = 0; rep < 4; ++rep) {  /* columns 0/32/64/96 */")
        self.emit("                gbs_vram_addr(bat + ((uint16_t)rep << 5));")
        self.emit("                GBS_VDC_DL = (uint8_t)entry;")
        self.emit("                GBS_VDC_DH = (uint8_t)(entry >> 8);")
        self.emit("                gbs_vram_addr(bat + ((uint16_t)rep << 5) + (32u << 7));")
        self.emit("                GBS_VDC_DL = (uint8_t)entry;")
        self.emit("                GBS_VDC_DH = (uint8_t)(entry >> 8);")
        self.emit("            }")
        self.emit("        }")
        self.emit("    }")
        self.emit("}")
        self.emit("/* bkg.move only STORES the scroll; the BXR/BYR registers are written in")
        self.emit("   vblank by gbs_bkg_scroll_flush (called from gbs_present). The VDC reloads")
        self.emit("   BYR into its internal vertical counter once per frame at the top of the")
        self.emit("   display, and reads BXR per scanline, so writing them mid-frame (from the")
        self.emit("   game loop, at a phase that drifts with how much work runs before the")
        self.emit("   move) applies the scroll this frame or one frame late depending on")
        self.emit("   timing -- the background JUMPS up/down while scrolling. Deferring the")
        self.emit("   write to vblank pins it to the safe point, so the scroll is rock-steady")
        self.emit("   no matter where in the frame bkg.move is called. */")
        self.emit("void gbs_move_bkg(uint8_t x, uint8_t y) {")
        self.emit("    gbs_bkg_init();")
        self.emit("    gbs_bkg_x = x; gbs_bkg_y = y;")
        self.emit("    gbs_bkg_scroll_dirty = 1;")
        self.emit("}")
        self.emit("/* Write the pending scroll to BXR/BYR. Called from gbs_present AFTER")
        self.emit("   waitvsync(), i.e. inside vblank where the write is latched cleanly. */")
        self.emit("void gbs_bkg_scroll_flush(void) {")
        self.emit("    if (!gbs_bkg_scroll_dirty) return;")
        self.emit("    gbs_vreg(7, gbs_bkg_x);  /* BXR */")
        self.emit("    gbs_vreg(8, gbs_bkg_y);  /* BYR */")
        self.emit("    gbs_bkg_scroll_dirty = 0;")
        self.emit("}")
        self.emit("void gbs_scroll_bkg(int8_t dx, int8_t dy) {")
        self.emit("    gbs_move_bkg((uint8_t)(gbs_bkg_x + dx), (uint8_t)(gbs_bkg_y + dy));")
        self.emit("}")
        if self.palette_imported:
            self.emit("/* bkg.set_palette: remember the slot per cell and rewrite the BAT")
            self.emit("   entries' palette bits in place (the char code is read back from")
            self.emit("   VRAM through MARR/VRR, then written to all eight replicas).")
            self.emit("   The read is ASSEMBLY: reading VRR high advances MARR, and cc65's")
            self.emit("   optimizer re-reads $0202 AFTER $0203 for `lo | hi << 8`, which")
            self.emit("   returned the NEXT cell's low byte (every repaint copied codes). */")
            self.emit("static uint8_t gbs_vrr_lo, gbs_vrr_hi;")
            self.emit("void gbs_bkg_palette_fill(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t slot) {")
            self.emit("    uint8_t cx, cy, xx, yy, rep, shift;")
            self.emit("    uint16_t idx, bat, entry;")
            self.emit("    slot &= 3;")
            self.emit("    gbs_bkg_init();")
            self.emit("    for (cy = 0; cy < h; ++cy) {")
            self.emit("        for (cx = 0; cx < w; ++cx) {")
            self.emit("            xx = (uint8_t)((x + cx) & 31); yy = (uint8_t)((y + cy) & 31);")
            self.emit("            idx = ((uint16_t)yy << 5) | xx;")
            self.emit("            shift = (uint8_t)((idx & 1) << 2);")
            self.emit("            gbs_bkg_pal[idx >> 1] = (uint8_t)(")
            self.emit("                (gbs_bkg_pal[idx >> 1] & (uint8_t)~(0x0F << shift))")
            self.emit("                | (slot << shift));")
            self.emit("            bat = ((uint16_t)yy << 7) + xx;")
            self.emit("            gbs_vreg(1, bat);  /* MARR */")
            self.emit("            GBS_VDC_AR = 2;    /* VRR */")
            self.emit("            __asm__(\"lda $0202\");")
            self.emit("            __asm__(\"sta %v\", gbs_vrr_lo);")
            self.emit("            __asm__(\"lda $0203\");")
            self.emit("            __asm__(\"sta %v\", gbs_vrr_hi);")
            self.emit("            entry = (uint16_t)(((uint16_t)gbs_vrr_hi << 8) | gbs_vrr_lo);")
            self.emit("            entry = (uint16_t)((entry & 0x0FFF)")
            self.emit("                    | ((uint16_t)(slot + 2) << 12));")
            self.emit("            for (rep = 0; rep < 4; ++rep) {")
            self.emit("                gbs_vram_addr(bat + ((uint16_t)rep << 5));")
            self.emit("                GBS_VDC_DL = (uint8_t)entry;")
            self.emit("                GBS_VDC_DH = (uint8_t)(entry >> 8);")
            self.emit("                gbs_vram_addr(bat + ((uint16_t)rep << 5) + (32u << 7));")
            self.emit("                GBS_VDC_DL = (uint8_t)entry;")
            self.emit("                GBS_VDC_DH = (uint8_t)(entry >> 8);")
            self.emit("            }")
            self.emit("        }")
            self.emit("    }")
            self.emit("}")
