"""Cc65Backend <concern> methods, mixed into Cc65Backend.

Split out of the former monolithic cc65.py; a plain mixin class - methods run
against the full CodeGenerator instance (self.emit, self.caps, ...)."""


class Cc65TextMixin:
    def _emit_cc65_text_tgi(self):
        """Text helpers for TGI consoles (pixel-addressed, e.g. Lynx)."""
        prof = self.cc65_profile
        if self.palette_imported:
            # Pen-partition mode: text follows the bkg palette slot (fg = bkg
            # color 3 = pen 15, bg = bkg color 0 = pen 0), and the helpers
            # apply the grey-ramp pen defaults so a text-only program is
            # deterministic before any palette call.
            fg, bg, init = '15', '0', 'gbs_pal_init();'
        else:
            fg = prof.get('text_fg', 'COLOR_WHITE')
            bg = prof.get('text_bg', 'COLOR_BLACK')
            init = 'gbs_video_init();'
        self.emit("/* TGI text draws transparently (pixels OR onto the screen), but the")
        self.emit("   Game Boy's tile text *replaces* the cell -- so clear the covered")
        self.emit("   cells first to keep reprinting (counters, scores) portable.")
        self.emit("   Once the sprite engine engages double-buffering, gbs_present()")
        self.emit("   draws the scene + flips, leaving the DRAW page = the one NOT on")
        self.emit("   screen; text drawn after present (a dialogue box redrawn each frame)")
        self.emit("   would land on the hidden page and show only every other frame ->")
        self.emit("   flicker. So draw to BOTH pages while double-buffered (each is a")
        self.emit("   complete frame the flip can show), then restore the draw page the")
        self.emit("   present expects. Single-buffered (text-only) programs draw once. */")
        self.emit("extern uint8_t gbs_draw_page;")
        self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s) {")
        self.emit("    int px = (int)x * GBS_CELL_W, py = (int)y * GBS_CELL_H;")
        self.emit("    uint8_t pass;")
        self.emit("    %s" % init)
        self.emit("    for (pass = 0; ; ++pass) {")
        self.emit("        tgi_setcolor(%s);" % bg)
        self.emit("        tgi_bar(px, py, px + (int)strlen(s) * GBS_CELL_W - 1, py + GBS_CELL_H - 1);")
        self.emit("        tgi_setcolor(%s);" % fg)
        self.emit("        tgi_outtextxy(px, py, s);")
        self.emit("        if (!gbs_spr_db || pass) break;")
        self.emit("        tgi_setdrawpage(gbs_draw_page ^ 1);")
        self.emit("    }")
        self.emit("    if (gbs_spr_db) tgi_setdrawpage(gbs_draw_page);")
        self.emit("}")
        self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n) {")
        self.emit("    char buf[7];")
        self.emit("    utoa(n, buf, 10);")
        self.emit("    gbs_print_string(x, y, buf);")
        self.emit("}")
        self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {")
        self.emit("    uint8_t pass;")
        self.emit("    %s" % init)
        self.emit("    for (pass = 0; ; ++pass) {")
        self.emit("        tgi_setcolor(%s);" % bg)
        self.emit("        tgi_bar((int)x * GBS_CELL_W, (int)y * GBS_CELL_H,")
        self.emit("                (int)(x + w) * GBS_CELL_W - 1, (int)(y + h) * GBS_CELL_H - 1);")
        self.emit("        if (!gbs_spr_db || pass) break;")
        self.emit("        tgi_setdrawpage(gbs_draw_page ^ 1);")
        self.emit("    }")
        self.emit("    tgi_setcolor(%s);" % fg)
        self.emit("    if (gbs_spr_db) tgi_setdrawpage(gbs_draw_page);")
        self.emit("}")
        if self.fill_box_used:
            self.emit("/* text.fill_box: a filled, bordered box drawn as a real framebuffer")
            self.emit("   OVERLAY (an opaque paper bar + a crisp 1px ink border via the TGI")
            self.emit("   line primitive) -- nicer + pixel-precise vs. the character-cell +--+")
            self.emit("   frame, and opaque over a frozen scrolled background. Cell coords like")
            self.emit("   clear_area; drawn to BOTH pages so it survives the double-buffer flip")
            self.emit("   (the same freeze idiom as the text helpers). */")
            self.emit("void gbs_fill_box(uint8_t c, uint8_t r, uint8_t w, uint8_t h) {")
            self.emit("    int x0 = (int)c * GBS_CELL_W, y0 = (int)r * GBS_CELL_H;")
            self.emit("    int x1 = (int)(c + w) * GBS_CELL_W - 1, y1 = (int)(r + h) * GBS_CELL_H - 1;")
            self.emit("    uint8_t pass;")
            self.emit("    %s" % init)
            self.emit("    /* The big opaque fill is drawn to the DISPLAYED page FIRST (pass 0 ->")
            self.emit("       gbs_draw_page ^ 1). The caller presents just before (the box freeze")
            self.emit("       idiom), so drawing the shown page first gives Suzy the whole frame")
            self.emit("       to lay the ~box-sized bar before the beam reaches it -- otherwise")
            self.emit("       the shown-page fill (drawn last, mid-scan) tears and the frozen")
            self.emit("       background bleeds through. The hidden page follows (pass 1). */")
            self.emit("    for (pass = 0; ; ++pass) {")
            self.emit("        if (gbs_spr_db) tgi_setdrawpage(pass ? gbs_draw_page : (gbs_draw_page ^ 1));")
            self.emit("        tgi_setcolor(%s);        /* opaque fill over the frozen map */" % bg)
            self.emit("        tgi_bar(x0, y0, x1, y1);")
            self.emit("        tgi_setcolor(%s);        /* crisp 1px border rectangle */" % fg)
            self.emit("        tgi_line(x0, y0, x1, y0); tgi_line(x0, y1, x1, y1);")
            self.emit("        tgi_line(x0, y0, x0, y1); tgi_line(x1, y0, x1, y1);")
            self.emit("        if (!gbs_spr_db || pass) break;")
            self.emit("    }")
            self.emit("    if (gbs_spr_db) tgi_setdrawpage(gbs_draw_page);")
            self.emit("}")
        # Custom font: no glyph-tile swap on the Lynx TGI backend -> no-ops.
        if self.font_set_used:
            self.emit("void gbs_set_font(const uint8_t *data) { (void)data; }")
        if self.font_at_used:
            self.emit("void gbs_set_font_at(uint8_t base, const uint8_t *data) { (void)base; (void)data; }")
        # text.plot_tile: no tile table behind TGI text -> a graceful no-op
        # (a custom frame on the Lynx is the text.fill_box pixel overlay).
        if self.plot_tile_used:
            self.emit("void gbs_plot_tile(uint8_t x, uint8_t y, uint8_t t) { (void)x; (void)y; (void)t; }")
        # text.glyph_buffer: the mode exists to hand a TILE table back to scene
        # art. TGI text draws pixels and reserves no glyph tiles, so there is
        # nothing to reclaim -> a no-op.
        if self.glyph_buffer_used:
            self.emit("void gbs_text_glyph_buffer(uint8_t base, uint8_t count) { (void)base; (void)count; }")
        # text.win_sprite_cut: no window layer, so nothing draws over it.
        if self.win_cut_used:
            self.emit("void gbs_text_win_cut(uint8_t on) { (void)on; }")
        # text.win_overlay_cut: and no window overlay to cut short either.
        if self.overlay_cut_used:
            self.emit("void gbs_text_win_overlay_cut(uint8_t y) { (void)y; }")

    @property
    def _conio_ui_space(self):
        """True when the conio plotters must map a UI cell into SCREEN space.

        Only for a program that both calls `text.to_window` (i.e. asks for a box
        HELD on screen) and has the scrolling background engine behind it -- a
        static-screen PCE program has nothing to correct for, and must keep the
        plain conio plotters byte-identical."""
        return (self.text_window_used and self.caps.get("has_bkg")
                and self.cc65_bkg_imported and self.platform == "pce")

    @property
    def _conio_bat_replicas(self):
        """True when a background-space conio clear must reach the BAT replicas.

        The PCE background engine writes every map cell into EIGHT BAT entries
        (columns +0/+32/+64/+96, rows +0/+32) so the u8 BXR/BYR scroll wraps
        like the Game Boy's 32x32 map. conio's `cclearxy` writes only the
        primary cell, so on a scrolled screen a cleared cell still showed the
        replica's old content: the room-load clear of a small room left the
        previous (taller) room in the columns past 32. A program without the
        background engine has no replicas and keeps the plain clear."""
        return (self.caps.get("has_bkg") and self.cc65_bkg_imported
                and self.platform == "pce")

    def _emit_conio_bat_replicate(self):
        """`gbs_bat_replicate`: copy a cleared rect's entry to every replica."""
        self.emit("/* Copy a cleared background rect into all eight BAT replicas (see")
        self.emit("   gbs_set_bkg_tiles). The entry is the one cclearxy just wrote, read")
        self.emit("   back in ASSEMBLY: cc65 re-reads $0202 after $0203 in C. */")
        self.emit("static uint8_t gbs_clr_lo, gbs_clr_hi;")
        self.emit("static void gbs_bat_replicate(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {")
        self.emit("    uint8_t cx, cy, xx, yy, rep;")
        self.emit("    uint16_t bat, entry;")
        self.emit("    if (!w || !h) return;")
        self.emit("    gbs_vreg(1, ((uint16_t)y << 7) + x);  /* MARR */")
        self.emit("    GBS_VDC_AR = 2;                       /* VRR */")
        self.emit("    __asm__(\"lda $0202\");")
        self.emit("    __asm__(\"sta %v\", gbs_clr_lo);")
        self.emit("    __asm__(\"lda $0203\");")
        self.emit("    __asm__(\"sta %v\", gbs_clr_hi);")
        self.emit("    entry = (uint16_t)(((uint16_t)gbs_clr_hi << 8) | gbs_clr_lo);")
        self.emit("    for (cy = 0; cy < h; ++cy) {")
        self.emit("        for (cx = 0; cx < w; ++cx) {")
        self.emit("            xx = (uint8_t)((x + cx) & 31); yy = (uint8_t)((y + cy) & 31);")
        self.emit("            bat = ((uint16_t)yy << 7) + xx;")
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

    def _emit_conio_ui_space(self):
        """UI text in SCREEN space on the PCE.

        The same fault the SMS/GG half fixed, on the console nobody had built a
        scrolling UI box for. `gbs_print_string` is `gotoxy` + `cputs`, i.e.
        ABSOLUTE cells of the BAT -- and the BAT is the very table the VDC
        hardware-scrolls through, so a box drawn at its authored screen cell
        slides away with the level. Measured on `vm-uiscroll`: standing still
        the box reads "SCROLL HOLD TEST"; after scrolling two tiles right it
        reads "ROLL HOLD TEST", the head gone off the left edge. The Lynx is
        correct for a different reason (its `draw_open_ui` redraws per present
        into a framebuffer), which is why `text.to_window` could stay a no-op
        for both cc65 consoles and only the PCE was wrong.

        The mapping is the SMS/GG one (`gbdk_text._emit_smsgg_ui_space`): while
        a box is routed to the screen, add the scroll's TILE offset to every
        plot. Two limits, one of them bigger here than on the SMS:

          * the cells belong to the scene, so `gbs_text_window_active()` stays 0
            and the caller still repaints the room on close (as on SMS/GG);
          * a cell cannot express a SUB-TILE scroll, so the box still sits up to
            7 px off its authored screen position. The SMS closes that by
            ROUNDING the hardware scroll down to a tile in its own `to_window`
            -- which it can only do because `ui_snap_sprites` re-places every
            sprite against the same rounding in the same display
            frame. `vm.core` calls that on the SMS/GG only, so the
            same snap here was measured to change NOTHING (472 differing pixels
            with and without it: the engine re-publishes the unsnapped scroll
            every frame and there is no PCE arm of the freeze to stop it) while
            risking sprites 0..7 px off the ground for as long as a box is up.
            So the snap is deliberately absent. Closing this is a PCE draw-camera
            (`cam_draw_x/y` + `ui_snap_sprites`), not a text change.

        What that leaves is the difference between the reported bug and a
        cosmetic residual: before, the box slid away with the level and lost its
        head entirely (16 characters down to 6 at the verify's scroll); now it is
        on the right cells at any scroll, within half a tile.

        It reads the engine's own PENDING scroll (`gbs_bkg_x/y`), not the
        BXR/BYR registers: `bkg.move` only stores the scroll and
        `gbs_bkg_scroll_flush` writes the registers in vblank, so the live
        registers are the PREVIOUS frame's and a box mapped through them lands a
        tile out. The same reasoning the SMS half records for its own shadow.

        BAT GEOMETRY, measured rather than assumed (the reason this stage was
        deferred): the PCE bkg engine writes each cell at `(row << 7) + col` and
        replicates it at +32/+64/+96 columns and +32 rows, i.e. a 128x64-entry
        virtual screen filling the whole $0000-$1FFF BAT block -- and conio own
        text lands correctly on that same table, so both share the stride. The
        scroll is a u8, so its tile offset is 0..31 and a mapped cell can reach
        column 62 of 128 and row 58 of 64: inside the table either way. The
        wraps below are therefore defensive, not load-bearing.
        """
        self.emit("/* ---- UI text in SCREEN space (text.to_window on the scrolling BAT) ---- */")
        self.emit("static uint8_t gbs_text_ui = 0;   /* a box/menu is routed to the screen */")
        self.emit("/* The scroll this frame will COMMIT: bkg.move only stores it and the")
        self.emit("   BXR/BYR write happens in vblank, so the live registers are last")
        self.emit("   frame's. The BAT is 128x64 entries (see the note above). */")
        self.emit("static uint8_t gbs_ui_cx(uint8_t x) {")
        self.emit("    if (gbs_text_ui) x = (uint8_t)((x + (gbs_bkg_x >> 3)) & 31);")
        self.emit("    return x;")
        self.emit("}")
        self.emit("static uint8_t gbs_ui_cy(uint8_t y) {")
        self.emit("    if (gbs_text_ui) y = (uint8_t)((y + (gbs_bkg_y >> 3)) & 31);")
        self.emit("    return y;")
        self.emit("}")

    def _cx(self, e, plain):
        """A text COLUMN -> the cell to write.

        Screen space while the UI mapping is live, otherwise `plain` - the
        caller's HISTORICAL text, passed verbatim rather than re-derived. The
        same rule `gbdk_text._ui_cell_x` records: re-deriving turns `r + j`
        into `(uint8_t)(r + j)`, which is the same behaviour and pure churn,
        and `fill_box_test` correctly fails it."""
        return "gbs_ui_cx(%s)" % e if self._conio_ui_space else plain

    def _cy(self, e, plain):
        """A text ROW -> the cell to write (see `_cx`)."""
        return "gbs_ui_cy(%s)" % e if self._conio_ui_space else plain

    def _emit_cc65_text_conio(self):
        """Text helpers for conio consoles (character-cell, e.g. PC Engine)."""
        if self._conio_ui_space:
            self._emit_conio_ui_space()
        cx, cy = self._cx, self._cy
        self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s) {")
        self.emit("    gbs_video_init();")
        self.emit("    gotoxy(%s, %s); cputs(s);" % (cx("x", "x"), cy("y", "y")))
        self.emit("}")
        self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n) {")
        self.emit("    char buf[7];")
        self.emit("    gbs_video_init();")
        self.emit("    utoa(n, buf, 10);")
        self.emit("    gotoxy(%s, %s); cputs(buf);" % (cx("x", "x"), cy("y", "y")))
        self.emit("}")
        replicate = self._conio_bat_replicas
        if replicate:
            self._emit_conio_bat_replicate()
        self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {")
        self.emit("    uint8_t j;")
        self.emit("    gbs_video_init();")
        if replicate and getattr(self, 'view_used', False):
            # video.set_view (the letterbox): the margin clear reaches the BAT's
            # rows 28..31, because a centred room's TOP margin shows their
            # replicas. conio addresses only the 28 screen rows - a cclearxy
            # below that wrote over row 0 (measured: the room lost its first
            # row) - so those rows are left to gbs_bat_replicate, which copies
            # the rect's first (cleared) cell into every cell and replica.
            self.emit("    for (j = 0; j < h; j++)")
            self.emit("        if ((uint8_t)(y + j) < SCREEN_ROWS) cclearxy(%s, %s, w);"
                      % (cx("x", "x"), cy("(uint8_t)(y + j)", "y + j")))
        else:
            self.emit("    for (j = 0; j < h; j++) cclearxy(%s, %s, w);"
                      % (cx("x", "x"), cy("(uint8_t)(y + j)", "y + j")))
        if replicate:
            guard = "if (!gbs_text_ui) " if self._conio_ui_space else ""
            self.emit("    %sgbs_bat_replicate(x, y, w, h);" % guard)
        self.emit("}")
        if self.fill_box_used:
            self.emit("/* text.fill_box: the conio backend has no pixel primitives, so the box is")
            self.emit("   character cells -- a cleared interior with an ASCII +--+ border (the")
            self.emit("   plain borderless clear was INVISIBLE on the PCE's black background:")
            self.emit("   a game framing its box through fill_box showed no frame at all). */")
            self.emit("void gbs_fill_box(uint8_t c, uint8_t r, uint8_t w, uint8_t h) {")
            self.emit("    uint8_t i, j;")
            self.emit("    gbs_video_init();")
            self.emit("    for (j = 0; j < h; j++) cclearxy(%s, %s, w);"
                      % (cx("c", "c"), cy("(uint8_t)(r + j)", "r + j")))
            self.emit("    for (i = 0; i < w; i++) { cputcxy(%s, %s, '-'); cputcxy(%s, %s, '-'); }"
                      % (cx("(uint8_t)(c + i)", "c + i"), cy("r", "r"),
                         cx("(uint8_t)(c + i)", "c + i"),
                         cy("(uint8_t)(r + h - 1)", "r + h - 1")))
            self.emit("    for (j = 1; j + 1 < h; j++) { cputcxy(%s, %s, '|'); cputcxy(%s, %s, '|'); }"
                      % (cx("c", "c"), cy("(uint8_t)(r + j)", "r + j"),
                         cx("(uint8_t)(c + w - 1)", "c + w - 1"),
                         cy("(uint8_t)(r + j)", "r + j")))
            self.emit("    cputcxy(%s, %s, '+'); cputcxy(%s, %s, '+');"
                      % (cx("c", "c"), cy("r", "r"),
                         cx("(uint8_t)(c + w - 1)", "c + w - 1"), cy("r", "r")))
            self.emit("    cputcxy(%s, %s, '+'); cputcxy(%s, %s, '+');"
                      % (cx("c", "c"), cy("(uint8_t)(r + h - 1)", "r + h - 1"),
                         cx("(uint8_t)(c + w - 1)", "c + w - 1"),
                         cy("(uint8_t)(r + h - 1)", "r + h - 1")))
            self.emit("}")
        # Custom font: no glyph-tile swap on the PCE conio backend -> no-ops.
        if self.font_set_used:
            self.emit("void gbs_set_font(const uint8_t *data) { (void)data; }")
        if self.font_at_used:
            self.emit("void gbs_set_font_at(uint8_t base, const uint8_t *data) { (void)base; (void)data; }")
        # text.plot_tile: conio text has no text-layer tile table -> a no-op
        # (a PCE custom frame degrades to the fill_box ASCII frame).
        if self.plot_tile_used:
            self.emit("void gbs_plot_tile(uint8_t x, uint8_t y, uint8_t t) { (void)x; (void)y; (void)t; }")
        # text.glyph_buffer: conio owns its own font tiles and reserves nothing
        # from the scene's tile table, so there is nothing to reclaim -> a no-op.
        if self.glyph_buffer_used:
            self.emit("void gbs_text_glyph_buffer(uint8_t base, uint8_t count) { (void)base; (void)count; }")
        # text.win_sprite_cut: no window layer, so nothing draws over it.
        if self.win_cut_used:
            self.emit("void gbs_text_win_cut(uint8_t on) { (void)on; }")
        # text.win_overlay_cut: and no window overlay to cut short either.
        if self.overlay_cut_used:
            self.emit("void gbs_text_win_overlay_cut(uint8_t y) { (void)y; }")
