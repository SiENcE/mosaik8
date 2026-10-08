"""Cc65Backend <concern> methods, mixed into Cc65Backend.

Split out of the former monolithic cc65.py; a plain mixin class - methods run
against the full CodeGenerator instance (self.emit, self.caps, ...)."""


class Cc65SpriteMixin:
    def _emit_cc65_sprite_engine(self, prof):
        """Hardware Suzy sprite engine for the Atari Lynx.

        Keeps the Game Boy sprite *model* (a shared 8x8 2bpp tile table + sprite
        slots referencing tiles by index) so the GBDK sprite samples build and
        run unchanged, but draws via the Lynx's Suzy blitter instead of a
        per-pixel software loop. Each GB tile is converted once into a
        totally-literal Lynx sprite-data stream; each sprite slot owns a Suzy
        Sprite Control Block (SCB) pointing at its tile's data. gbs_present()
        clears the back buffer, fires one tgi_sprite() per visible slot, and
        flips. A sprite program therefore owns the frame (present repaints the
        background), so mixing immediate text and sprites in the same frame is
        not supported. sprite.move takes screen-pixel coordinates (the same
        contract as the GBDK backend's gbs_move_sprite wrapper).

        Lynx literal sprite-data layout (verified against cc65's sp65), per
        8-pixel 2bpp row: [offset=0x04][2 packed pixel bytes][0x00 pad byte];
        an offset byte of 0x00 ends the sprite. Pixels are 2 bits each, packed
        MSB-first (leftmost pixel in the high bits). Pixel value 0 maps to pen 0
        (COLOR_TRANSPARENT), so it is transparent for TYPE_NORMAL sprites --
        exactly the Game Boy's colour-0-is-transparent object model.
        """
        sw, sh = prof.get('screen_w', 160), prof.get('screen_h', 102)
        # When the program imports graphics.bkg, present() also blits the
        # composited background (emitted just above by _emit_cc65_bkg_engine).
        with_bkg = self.caps['has_bkg'] and self.cc65_bkg_imported
        # In palette mode the per-frame clear paints pen 0 = bkg color 0
        # (GB semantics: the backdrop is background color 0).
        clear_pen = '0' if self.palette_imported else 'COLOR_BLACK'
        # 4bpp tier: 16-colour sprites (the Lynx's native depth) when the asset
        # pipeline fed packed-nibble 4bpp tiles. The literal row widens from
        # [0x04][2 data][pad] to [0x06][4 data][pad]; otherwise everything (and
        # the byte-for-byte output of every 2bpp program) is unchanged.
        bpp4 = (self.sprite_src_bpp == 4)
        bpp_macro = 'BPP_4' if bpp4 else 'BPP_2'
        src_stride = 32 if bpp4 else 16
        # Bytes per converted tile: 8 rows * (offset + data + pad) + terminator.
        tile_bytes = (8 * 6 + 1) if bpp4 else (8 * 4 + 1)
        # The tile table is the resident gbs_tiles[GBS_MAX_TILES][GBS_TILE_BYTES]
        # in the scarce Lynx MAIN. Shrink it to the tiles the program uploads
        # (generator._resolve_sprite_max_tiles, or an explicit [build]
        # sprite_max_tiles); None / 40 keeps the byte-identical full table.
        if self._lynx_baked:
            # [build] lynx_sprites = "whole" / lynx_orientation: build-time
            # images instead of the run-time 8x8 converter (cc65_lynx_native).
            self._emit_lynx_baked_engine(prof, with_bkg, sw, sh, clear_pen, bpp4)
            return
        max_tiles = self.sprite_max_tiles or self.CC65_MAX_TILES
        max_tiles = max(1, min(self.CC65_MAX_TILES, int(max_tiles)))
        self.emit("/* --- Suzy hardware sprite engine (Atari Lynx) --- */")
        if max_tiles < self.CC65_MAX_TILES:
            self.emit("#define GBS_MAX_TILES   %d  /* shrunk sprite_max_tiles: only the uploaded tiles */"
                      % max_tiles)
        else:
            self.emit("#define GBS_MAX_TILES   %d" % max_tiles)
        # The 40 hardware-independent sprite SLOTS cost ~27 B of MAIN each (an
        # SCB + the tile/meta side tables). A game whose highest slot is known
        # (the VM8 pool: actors 0..7, player 8, projectiles 16+) can lower it
        # with `[build] sprite_max_slots`; every slot op is already guarded by
        # `nb < GBS_MAX_SPRITES`, so an out-of-range slot is a no-op rather than
        # a stray write. Unset = the byte-identical 40.
        max_slots = self.sprite_max_slots or self.CC65_MAX_SPRITES
        max_slots = max(1, min(self.CC65_MAX_SPRITES, int(max_slots)))
        if max_slots < self.CC65_MAX_SPRITES:
            self.emit("#define GBS_MAX_SPRITES %d  /* shrunk sprite_max_slots */"
                      % max_slots)
        else:
            self.emit("#define GBS_MAX_SPRITES %d" % max_slots)
        self.emit("#define GBS_TILE_BYTES  %d  /* literal-encoded 8x8 %s tile */"
                  % (tile_bytes, bpp_macro.replace('BPP_', '') + 'bpp'))
        self.emit("static uint8_t gbs_tiles[GBS_MAX_TILES][GBS_TILE_BYTES];")
        # A POWER-OF-TWO SLOT STRIDE. An SCB is 23 bytes, and cc65 turns every
        # `gbs_scb[i]` subscript into `LDA #23 / JSR tosumula0` -- its GENERAL
        # software multiply, measured at 1,001 ticks on GearLynx against a
        # 195,601-tick display frame. Padding the slot to 32 makes the same
        # subscript five shifts (`aslax4` + `aslax1`, ~70 ticks): one 2x2
        # metasprite move went 19,704 -> 12,253 ticks, and vm-clipdemo's whole
        # per-frame work 198,859 -> 169,102. Suzy reads each SCB at its own
        # address and follows SCBNEXT, so it never requires them to be
        # contiguous -- the padding is invisible to the hardware. Cost is
        # 9 B per slot of MAIN (`[build] sprite_max_slots` caps it).
        self.emit("typedef struct { SCB_REHV_PAL s; uint8_t pad_[9]; } gbs_scb_t;")
        self.emit("static gbs_scb_t gbs_scb[GBS_MAX_SPRITES];")
        self.emit("static uint8_t gbs_spr_tile[GBS_MAX_SPRITES];")
        self.emit("static uint8_t gbs_spr_max = 0;     /* highest slot touched + 1 */")
        self.emit("static uint8_t gbs_spr_used = 0;    /* engine active this program */")
        self.emit("static uint8_t gbs_spr_visible = 1;")
        self.emit("static uint8_t gbs_spr_db = 0;      /* double-buffering engaged */")
        self.emit("uint8_t gbs_draw_page = 0;    /* page tgi_sprite/text draw to now (tracks the flip) */")
        self.emit("static uint8_t gbs_spr_inited = 0;")
        if self.metasprite_used:
            self._emit_cc65_meta_state()
        if bpp4:
            self.emit("/* 4bpp identity pen map: sprite pixel value v -> pen v (the 16")
            self.emit("   Mikey pens hold the colours; load them with palette.load_sprite16). */")
            self.emit("static const uint8_t gbs_spr_penpal[8] = {")
            self.emit("    0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF")
            self.emit("};")
        elif not self.palette_imported:
            self.emit("/* GB 2bpp pixel value 1..3 -> Lynx pens; value 0 -> pen 0 = transparent.")
            self.emit("   Packed two pens per byte, lower pixel value in the high nibble. */")
            self.emit("static const uint8_t gbs_spr_penpal[8] = {")
            self.emit("    (COLOR_TRANSPARENT << 4) | COLOR_LIGHTGREY,   /* pixels 0,1 */")
            self.emit("    (COLOR_GREY << 4) | COLOR_WHITE,              /* pixels 2,3 */")
            self.emit("    0, 0, 0, 0, 0, 0")
            self.emit("};")
        self.emit("static void gbs_spr_init(void) {")
        self.emit("    uint8_t s, i;")
        self.emit("    if (gbs_spr_inited) return;")
        if self.palette_imported and not bpp4:
            self.emit("    gbs_pal_init();  /* grey-ramp pen defaults */")
        self.emit("    for (s = 0; s < GBS_MAX_SPRITES; ++s) {")
        self.emit("        gbs_scb[s].s.sprctl0 = %s | TYPE_NORMAL;" % bpp_macro)
        self.emit("        gbs_scb[s].s.sprctl1 = LITERAL | REHV;  /* literal data, reload h/v size */")
        self.emit("        gbs_scb[s].s.sprcoll = 0;")
        self.emit("        gbs_scb[s].s.next = (char *)0;")
        self.emit("        gbs_scb[s].s.data = gbs_tiles[0];       /* default to tile 0 (as on GB) */")
        self.emit("        gbs_scb[s].s.hpos = -16; gbs_scb[s].s.vpos = -16;")
        self.emit("        gbs_scb[s].s.hsize = 0x100; gbs_scb[s].s.vsize = 0x100;  /* 1:1 scale */")
        if self.palette_imported and not bpp4:
            self.emit("        /* Default to sprite palette slot 0 (pens 1-3). */")
            self.emit("        gbs_scb[s].s.penpal[0] = gbs_pal_penpal[0][0];")
            self.emit("        gbs_scb[s].s.penpal[1] = gbs_pal_penpal[0][1];")
            self.emit("        for (i = 2; i < 8; ++i) gbs_scb[s].s.penpal[i] = 0;")
        else:
            self.emit("        for (i = 0; i < 8; ++i) gbs_scb[s].s.penpal[i] = gbs_spr_penpal[i];")
        self.emit("    }")
        self.emit("    gbs_spr_inited = 1;")
        self.emit("    gbs_force = 1;   /* every slot was just laid down */")
        self.emit("}")
        if bpp4:
            self.emit("/* Copy one 8x8 4bpp tile (32 packed-nibble bytes, 4/row) into a")
            self.emit("   literal Lynx sprite: per row [offset=0x06][4 pixel bytes][pad]. */")
            self.emit("static void gbs_conv_tile(uint8_t tile, const uint8_t *src) {")
            self.emit("    uint8_t *o = gbs_tiles[tile];")
            self.emit("    uint8_t row;")
            self.emit("    for (row = 0; row < 8; ++row) {")
            self.emit("        *o++ = 0x06;")
            self.emit("        *o++ = src[row * 4];     *o++ = src[row * 4 + 1];")
            self.emit("        *o++ = src[row * 4 + 2]; *o++ = src[row * 4 + 3];")
            self.emit("        *o++ = 0x00;")
            self.emit("    }")
            self.emit("    *o = 0x00;  /* end of sprite data */")
            self.emit("}")
            if self.load_sprite16_used:
                self._emit_lynx_load_sprite_pal16()
        else:
            self.emit("/* Convert one 8x8 GB 2bpp tile (16 bytes) into a literal Lynx sprite. */")
            self.emit("static void gbs_conv_tile(uint8_t tile, const uint8_t *gb) {")
            self.emit("    uint8_t *o = gbs_tiles[tile];")
            self.emit("    uint8_t row, col, lo, hi, bit, ci, a, b;")
            self.emit("    for (row = 0; row < 8; ++row) {")
            self.emit("        lo = gb[row * 2]; hi = gb[row * 2 + 1];")
            self.emit("        a = 0; b = 0;")
            self.emit("        for (col = 0; col < 8; ++col) {")
            self.emit("            bit = 7 - col;")
            self.emit("            ci = (uint8_t)((((hi >> bit) & 1) << 1) | ((lo >> bit) & 1));")
            self.emit("            if (col < 4) a = (uint8_t)(a | (ci << ((3 - col) * 2)));")
            self.emit("            else         b = (uint8_t)(b | (ci << ((3 - (col - 4)) * 2)));")
            self.emit("        }")
            self.emit("        *o++ = 0x04; *o++ = a; *o++ = b; *o++ = 0x00;")
            self.emit("    }")
            self.emit("    *o = 0x00;  /* end of sprite data */")
            self.emit("}")
            if self.load_sprite16_used:
                self.emit("/* load_sprite16 with no 4bpp asset present: nothing to load. */")
                self.emit("void gbs_load_sprite_pal16(const uint16_t *pal) { (void)pal; }")
        self._emit_cc65_present(prof, with_bkg, sw, sh, clear_pen)
        self.emit("void gbs_set_sprite_data(uint8_t first, uint8_t count, const uint8_t *data) {")
        self.emit("    uint8_t i;")
        self.emit("    gbs_spr_init();")
        self.emit("    for (i = 0; i < count; ++i)")
        self.emit("        if ((uint8_t)(first + i) < GBS_MAX_TILES)")
        self.emit("            gbs_conv_tile((uint8_t)(first + i), data + (uint16_t)i * %d);" % src_stride)
        self.emit("    gbs_spr_used = 1;")
        self.emit("    gbs_force = 1;   /* new sprite tile data -> present must re-blit */")
        self.emit("}")
        self.emit("void gbs_set_sprite_tile(uint8_t nb, uint8_t tile) {")
        self.emit("    gbs_spr_init();")
        if self.metasprite_used:
            self._emit_cc65_meta_branch('tile')
        self.emit("    if (nb < GBS_MAX_SPRITES && tile < GBS_MAX_TILES) {")
        self.emit("        gbs_spr_tile[nb] = tile;")
        self.emit("        if (gbs_scb[nb].s.data != gbs_tiles[tile]) {")
        self.emit("            gbs_scb[nb].s.data = gbs_tiles[tile]; gbs_force = 1;")
        self.emit("        }")
        self.emit("        if (nb >= gbs_spr_max) gbs_spr_max = nb + 1;")
        self.emit("    }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        self.emit("uint8_t gbs_get_sprite_tile(uint8_t nb) {")
        self.emit("    return nb < GBS_MAX_SPRITES ? gbs_spr_tile[nb] : 0;")
        self.emit("}")
        self.emit("/* prop carries FLIP_X (HFLIP) / FLIP_Y (VFLIP) for SPRCTL0. */")
        self.emit("void gbs_set_sprite_prop(uint8_t nb, uint8_t prop) {")
        self.emit("    gbs_spr_init();")
        if self.metasprite_used:
            self._emit_cc65_meta_branch('prop')
        self.emit("    if (nb < GBS_MAX_SPRITES) {")
        self.emit("        gbs_scb_t *q = &gbs_scb[nb];")
        self.emit("        uint8_t c0 = (uint8_t)((%s | TYPE_NORMAL) |" % bpp_macro)
        self.emit("            (prop & (HFLIP | VFLIP)));")
        self.emit("        uint8_t d = (uint8_t)(c0 ^ q->s.sprctl0);")
        self.emit("        if (d) {")
        self.emit("            /* A flipped Suzy sprite mirrors about its REFERENCE point, so")
        self.emit("               the reference moves to the far edge of the 8x8 and the")
        self.emit("               picture stays where sprite.move put it. */")
        self.emit("            if (d & HFLIP) q->s.hpos += (c0 & HFLIP) ? 7 : -7;")
        self.emit("            if (d & VFLIP) q->s.vpos += (c0 & VFLIP) ? 7 : -7;")
        self.emit("            q->s.sprctl0 = c0; gbs_force = 1;")
        self.emit("        }")
        self.emit("    }")
        self.emit("}")
        self.emit("/* sprite.move takes screen-pixel coordinates (top-left origin). */")
        self.emit("void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y) {")
        self.emit("    gbs_spr_init();")
        if self.metasprite_used:
            self._emit_cc65_meta_branch('move')
        self.emit("    if (nb < GBS_MAX_SPRITES) {")
        self.emit("        gbs_scb_t *q = &gbs_scb[nb];   /* one subscript, not two */")
        self.emit("        int hx = x, vy = y;")
        self.emit("        /* a flipped 8x8 is referenced at its far edge (see set_prop) */")
        self.emit("        if (q->s.sprctl0 & HFLIP) hx += 7;")
        self.emit("        if (q->s.sprctl0 & VFLIP) vy += 7;")
        self.emit("        if (q->s.hpos != hx) { q->s.hpos = hx; gbs_force = 1; }")
        self.emit("        if (q->s.vpos != vy) { q->s.vpos = vy; gbs_force = 1; }")
        self.emit("        if (nb >= gbs_spr_max) gbs_spr_max = nb + 1;")
        self.emit("    }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        self._emit_cc65_move_world()
        self.emit("void gbs_show_sprites(void) { gbs_spr_used = 1;")
        self.emit("    if (!gbs_spr_visible) { gbs_spr_visible = 1; gbs_force = 1; } }")
        self.emit("void gbs_hide_sprites(void) {")
        self.emit("    if (gbs_spr_visible) { gbs_spr_visible = 0; gbs_force = 1; } }")
        if with_bkg:
            self.emit("void gbs_show_bkg(void) {")
            self.emit("    if (!gbs_bkg_visible) { gbs_bkg_visible = 1; gbs_force = 1; } }")
        else:
            self.emit("void gbs_show_bkg(void) { }")
        if self.metasprite_used:
            self._emit_cc65_meta_func()

    def _emit_cc65_present(self, prof, with_bkg, sw, sh, clear_pen):
        """`gbs_present` for the Suzy engine: the background, then every
        visible sprite slot, then the flip. Shared by the 8x8-tile engine and
        the baked-image one (`_emit_lynx_baked_engine`); only the sprite chain
        differs (`_emit_cc65_present_sprites`)."""
        if getattr(self, 'lynx_camera_used', False):
            self._emit_lynx_camera_state()
        self.emit("void gbs_present(void) {")
        self.emit("    uint8_t s;")
        if self.caps['has_sound']:
            self.emit("    if (gbs_snd_frames && --gbs_snd_frames == 0) gbs_sound_stop();")
        self.emit("    /* No sprites in use: stay single-buffered so immediate")
        self.emit("       (text) drawing persists and does not flicker -- but still")
        self.emit("       wait out the frame so wait_vblank paces the main loop.")
        self.emit("       The Lynx clock() ticks once per display frame (Mikey")
        self.emit("       timer 2, the VBL timer), so CLOCKS_PER_SEC == framerate. */")
        if with_bkg:
            self.emit("    if (!gbs_spr_used && !gbs_bkg_used) {")
        else:
            self.emit("    if (!gbs_spr_used) {")
        self.emit("        clock_t t = clock();")
        self.emit("        while (clock() == t) { }")
        self.emit("        return;")
        self.emit("    }")
        self.emit("    /* First sprite frame: switch to true double-buffering so the")
        self.emit("       per-frame clear + redraw happens off-screen and the displayed")
        self.emit("       page is always a complete frame -- no tearing, and sprites that")
        self.emit("       do not move (a fixed HUD, a static-camera scene) stay visible.")
        self.emit("       With graphics.bkg this also repositions the row-strip ring each")
        self.emit("       frame; moving SCBs is cheap enough to do so every frame. */")
        self.emit("    if (!gbs_spr_db) { tgi_setdrawpage(1); gbs_draw_page = 1; gbs_spr_db = 1; }")
        # The wide column-strip engine (engine.scroll) draws EVERY frame -- it
        # cannot use the change-detection skip below. The skip assumes the two
        # buffers are identical once redraw reaches 0, so a static frame just
        # flips between them; that holds only if the last two draws produced
        # pixel-identical pages. The wide path reads the shared strip SCBs
        # (repositioned per frame from gbs_bkg_x) and an object/enemy/HUD cast
        # composed camera-relative, so the two draws can diverge (an enemy that
        # paced, a sprite that crossed the on-screen cull) -- then redraw drops
        # to 0 and the flip alternates the good page with a stale/blank one
        # FOREVER (platform-quest's whole-screen Lynx flicker). Present is light
        # here (compose is at set_tiles time; this only repositions ~21 column
        # SCBs, the same ~16-SCB/frame budget as the row ring), so redrawing
        # unconditionally is affordable and keeps both buffers current.
        wide_always = with_bkg and self.cc65_wide_scroll
        if not wide_always:
            # EXACT CHANGE DETECTION, SET BY THE WRITERS -- there is no
            # per-frame scan any more. This used to be a weighted u16 sum over
            # every slot's hpos/vpos/sprctl0/data, recomputed each frame. On
            # cc65 each of those `* 7u / * 13u / * 17u` is a call to the general
            # software multiply (1,001 ticks, measured on GearLynx), so a
            # 12-slot screen paid ~36,000 ticks a frame -- 11% of vm-clipdemo's
            # whole per-frame work -- to answer a question the writers already
            # know the answer to. `gbs_move_sprite` and friends compare before
            # they store and raise `gbs_force` only on a REAL change.
            #
            # It is also strictly more accurate: the sum was a hash, so two
            # compensating changes in one frame could collide (dx1*7 ==
            # -dx2*7 mod 65536) and hold a stale frame until the next change.
            # That documented trade-off is gone.
            #
            # Re-asserting the SAME value (the Lynx idiom -- engine.anim writes
            # every frame) does not dirty anything, which is what makes a
            # compare-then-store cheaper than a plain dirty flag would have been.
            self.emit("    {   /* Re-blit until BOTH buffers are current (2 frames after a")
            self.emit("       change); a static frame then just flips between the two")
            self.emit("       identical buffers. tgi_updatedisplay() still runs EVERY frame")
            self.emit("       (the Lynx display needs it -- skipping it blanks the screen),")
            self.emit("       but dropping the per-frame recomposite frees the Suzy load that")
            self.emit("       intermittently flickered a foreground sprite on Beetle. The")
            self.emit("       writers raise gbs_force on a real change (see gbs_move_sprite). */")
            self.emit("        if (gbs_force) gbs_redraw = 2;")
            self.emit("        gbs_force = 0;")
            self.emit("    }")
        if wide_always:
            self.emit("    {   /* wide column-strip engine: always redraw (see above) */")
        else:
            self.emit("    if (gbs_redraw) {")
            self.emit("        --gbs_redraw;")
        if with_bkg and self.cc65_wide_scroll:
            # Column-strip ring (wide streamed level): display column i shows map
            # column (tx0+i)&31 from its own strip slot, at hpos = i*8 - fx. The
            # strips were composited at set_tiles time (one per streamed column),
            # so present only repositions them -- horizontal scroll is pure SCB
            # hpos, no recomposite here. The strips are composited from the vertical
            # offset row (gbs_bkg_y>>3, see compose_col), and the fractional 0..7 px
            # is the strip vpos -- so a level taller than the screen shows from `voff`
            # down (its bottom on the short Lynx screen).
            self.emit("    if (gbs_bkg_used && gbs_bkg_visible) {")
            self.emit("        uint8_t i, c;")
            self.emit("        uint8_t tx0 = (uint8_t)(gbs_bkg_x >> 3);")
            self.emit("        uint8_t fx = (uint8_t)(gbs_bkg_x & 7);")
            self.emit("        int vy = -(int)(gbs_bkg_y & 7);   /* sub-tile vertical offset */")
            self.emit("        int hx;")
            self.emit("        for (i = 0; i < GBS_BKG_COL_VIS; ++i) {")
            self.emit("            hx = (int)((uint16_t)i * 8) - (int)fx;")
            self.emit("            if (hx <= -8 || hx >= %d) continue;  /* off-screen */" % sw)
            self.emit("            c = (uint8_t)((uint8_t)(tx0 + i) & 31);")
            self.emit("            gbs_bkg_scb[c].data = gbs_bkg_strip[c];")
            self.emit("            gbs_bkg_scb[c].hpos = hx;")
            self.emit("            gbs_bkg_scb[c].vpos = vy;")
            self.emit("            tgi_sprite(&gbs_bkg_scb[c]);")
            self.emit("        }")
            self.emit("    } else {")
            self.emit("        tgi_setcolor(%s);" % clear_pen)
            self.emit("        tgi_bar(0, 0, %d, %d);" % (sw - 1, sh - 1))
            self.emit("    }")
        elif with_bkg:
            self.emit("    if (gbs_bkg_used && gbs_bkg_visible) {")
            self.emit("        /* Row-strip ring: display row i shows map row (ty0+i)&31 from ring")
            self.emit("           slot (ty0+i)%%STRIPS at (hpos=-x, vpos=i*8-fy). hpos covers any")
            self.emit("           horizontal scroll (wide strip, no wrap copy). A row stays in the")
            self.emit("           same slot for its whole visible life; a slot is recomposited only")
            self.emit("           when a new row enters it (one slot per vertical tile cross). That")
            self.emit("           new row enters OFF-SCREEN at the bottom margin (~16 frames before")
            self.emit("           it scrolls into view), so it is composited a few columns per frame")
            self.emit("           -- the per-tile recomposite never lands whole on one frame. */")
            self.emit("        uint8_t i, p, mr, budget;")
            self.emit("        uint8_t ty0 = (uint8_t)(gbs_bkg_y >> 3);")
            self.emit("        uint8_t fy = (uint8_t)(gbs_bkg_y & 7);")
            if getattr(self, 'lynx_orient', None):
                # portrait: screen-width strips that slide with the camera
                self.emit("        int hx = -(int)(gbs_bkg_x & 7);")
                self.emit("        gbs_bkg_slide();")
            else:
                self.emit("        int hx = -(int)gbs_bkg_x;")
            self.emit("        if (gbs_bkg_built && gbs_bkg_x == gbs_bkg_px && gbs_bkg_y == gbs_bkg_py) {")
            self.emit("            /* Static camera + every strip composed: the strip SCBs stay")
            self.emit("               positioned for this (x,y); the chained blit below re-draws them")
            self.emit("               (skips the per-strip vp/offscreen recompute + SCB rewrites). */")
            self.emit("        } else {")
            self.emit("        budget = GBS_BKG_AMORT;")
            self.emit("        /* Assign each slot its target row (reset progress on change) and")
            self.emit("           advance the incremental composite of incomplete (off-screen) strips. */")
            self.emit("        for (i = 0; i < GBS_BKG_STRIPS; ++i) {")
            self.emit("            p = (uint8_t)((uint8_t)(ty0 + i) % GBS_BKG_STRIPS);")
            self.emit("            mr = (uint8_t)((uint8_t)(ty0 + i) & 31);")
            self.emit("            if (gbs_bkg_strip_row[p] != mr) { gbs_bkg_strip_row[p] = mr; gbs_bkg_strip_col[p] = 0; }")
            self.emit("            if (budget && gbs_bkg_strip_col[p] < GBS_BKG_STRIP_W) {")
            self.emit("                uint8_t c0 = gbs_bkg_strip_col[p];")
            self.emit("                uint8_t c1 = (uint8_t)(c0 + budget);")
            self.emit("                if (c1 > GBS_BKG_STRIP_W) c1 = GBS_BKG_STRIP_W;")
            self.emit("                gbs_bkg_compose_cols(p, mr, c0, c1);")
            self.emit("                budget = (uint8_t)(budget - (c1 - c0));")
            self.emit("                gbs_bkg_strip_col[p] = c1;")
            self.emit("            }")
            self.emit("        }")
            self.emit("        /* Place the on-screen strips; finish any not-yet-complete one first")
            self.emit("           (fast scroll / first load outran the amortizer -- a rare full hit).")
            self.emit("           Record the visible slot range [vlo,vhi) for the static-camera path. */")
            # By POINTER, for the reason the chain below gives: three
            # `gbs_bkg_scb[i]` subscripts (a 23-byte SCB) and `gbs_bkg_strip[p]`
            # (a 353-byte row) were four cc65 software multiplies a strip,
            # ~1,000 ticks each -- measured on the Lynx shooter, this loop was
            # most of the present's 117k-253k-tick background block. The strip
            # pointer starts once (one multiply) and steps a row, wrapping at
            # the end of the ring; `vp` steps 8.
            self.emit("        gbs_bkg_vlo = 255; gbs_bkg_vhi = 0;")
            self.emit("        { SCB_REHV_PAL *b = gbs_bkg_scb;")
            self.emit("          uint8_t *sr = gbs_bkg_strip[(uint8_t)(ty0 % GBS_BKG_STRIPS)];")
            self.emit("          int vp = -(int)fy;")
            self.emit("          for (i = 0; i < GBS_BKG_STRIPS; ++i, ++b, vp += 8) {")
            self.emit("            p = (uint8_t)((uint8_t)(ty0 + i) % GBS_BKG_STRIPS);")
            self.emit("            if (i) {")
            self.emit("                sr += sizeof gbs_bkg_strip[0];")
            self.emit("                if (sr == (uint8_t *)gbs_bkg_strip + sizeof gbs_bkg_strip)")
            self.emit("                    sr = gbs_bkg_strip[0];")
            self.emit("            }")
            self.emit("            if (vp <= -8 || vp >= %d) continue;  /* off-screen */" % sh)
            self.emit("            if (gbs_bkg_vlo == 255) gbs_bkg_vlo = i;")
            self.emit("            gbs_bkg_vhi = (uint8_t)(i + 1);")
            self.emit("            if (gbs_bkg_strip_col[p] < GBS_BKG_STRIP_W) {")
            self.emit("                gbs_bkg_compose_cols(p, gbs_bkg_strip_row[p], gbs_bkg_strip_col[p], GBS_BKG_STRIP_W);")
            self.emit("                gbs_bkg_strip_col[p] = GBS_BKG_STRIP_W;")
            self.emit("            }")
            self.emit("            b->data = sr;")
            self.emit("            b->vpos = vp;")
            self.emit("            b->hpos = hx;")
            self.emit("        } }")
            self.emit("        /* The strip ring composites amortized over many frames, so keep")
            self.emit("           re-blitting (both buffers) until every strip is fully built --")
            self.emit("           otherwise a static frame would freeze a half-built buffer. INSIDE")
            self.emit("           the gbs_bkg_used+visible block on purpose: a program that pulls in")
            self.emit("           the bkg engine (imports graphics.bkg, e.g. via vm.core) but never")
            self.emit("           PAINTS a map leaves every strip at column 0, so out here this")
            self.emit("           would pin gbs_redraw = 2 FOREVER -- the present then full-clears")
            self.emit("           (the else branch) + re-blits every frame, wiping a text box/menu")
            self.emit("           drawn after present = flicker (vm-menu / vm-ui, no scene). With no")
            self.emit("           bkg the strips are never shown, so the force is simply skipped. */")
            self.emit("        gbs_bkg_px = gbs_bkg_x; gbs_bkg_py = gbs_bkg_y;  /* strips positioned for this camera */")
            self.emit("        { uint8_t k; gbs_bkg_built = 1;")
            self.emit("          for (k = 0; k < GBS_BKG_STRIPS; ++k)")
            self.emit("            if (gbs_bkg_strip_col[k] < GBS_BKG_STRIP_W) { gbs_redraw = 2; gbs_bkg_built = 0; break; } }")
            self.emit("        }  /* end static-camera fast-path else */")
            self.emit("        /* Chain the visible strips into ONE Suzy walk (SCBNEXT): the whole")
            self.emit("           background paints on a single kick, not one tgi_sprite per strip. */")
            self.emit("        { SCB_REHV_PAL *h = 0, *pv = 0;")
            # ...by POINTER, for the reason the SCB stride comment gives: this
            # took THREE `gbs_bkg_scb[i]` subscripts per strip, and a bkg SCB is
            # the unpadded 23-byte type, so each was a 1,001-tick software
            # multiply -- ~39,000 ticks a frame on a 13-strip screen, to build a
            # chain of 13 pointers.
            self.emit("          SCB_REHV_PAL *b = gbs_bkg_scb +")
            self.emit("              (gbs_bkg_vlo < GBS_BKG_STRIPS ? gbs_bkg_vlo : 0);")
            self.emit("          for (i = gbs_bkg_vlo; i < gbs_bkg_vhi; ++i, ++b) {")
            self.emit("              if (pv) pv->next = (char *)b; else h = b;")
            self.emit("              pv = b; }")
            self.emit("          if (pv) { pv->next = (char *)0; tgi_sprite(h); } }")
            self.emit("    } else {")
            self.emit("        tgi_setcolor(%s);" % clear_pen)
            self.emit("        tgi_bar(0, 0, %d, %d);" % (sw - 1, sh - 1))
            self.emit("    }")
        else:
            self.emit("    tgi_setcolor(%s);" % clear_pen)
            self.emit("    tgi_bar(0, 0, %d, %d);" % (sw - 1, sh - 1))
        self._emit_cc65_present_sprites(sw, sh)
        if self.overlay_used:
            self.emit("        if (gbs_overlay_cb) {")
            self.emit("            /* Compose the UI overlay INTO this re-blitted frame, drawing")
            self.emit("               ONCE onto the page being composed: gbs_spr_db = 0 makes the")
            self.emit("               text helpers draw single-page (their dual-page pass loop is")
            self.emit("               for post-present callers seeding both flip buffers). */")
            self.emit("            uint8_t db = gbs_spr_db; gbs_spr_db = 0;")
            self.emit("            gbs_overlay_cb();")
            self.emit("            gbs_spr_db = db;")
            self.emit("        }")
        self.emit("        while (tgi_busy()) { }  /* let Suzy finish before the flip */")
        self.emit("    }")
        self.emit("    tgi_updatedisplay();    /* queue the VBL flip -- ALWAYS (keeps the display alive) */")
        self.emit("    /* Wait until the flip actually HAPPENED. tgi_updatedisplay() ==")
        self.emit("       tgi_ioctl(4, 1) only QUEUES the swap (sets the driver's")
        self.emit("       SWAPREQUEST; the display IRQ consumes it at the next VBL, and the")
        self.emit("       driver's draw-buffer pointer moves only then). Returning while it")
        self.emit("       is still pending is the double-buffer KILLER: the next frame's")
        self.emit("       Suzy blits land in the page just queued for DISPLAY, and that")
        self.emit("       frame's own updatedisplay COALESCES into the pending request --")
        self.emit("       two logical flips collapse into one physical flip, the page")
        self.emit("       parity inverts, and one page goes permanently stale, shown every")
        self.emit("       other flip (the whole-screen flicker platform-quest had; a clock()")
        self.emit("       tick is NOT a safe wait -- the C-library jiffy and the display")
        self.emit("       timer are different IRQ sources at different phases). Polling")
        self.emit("       tgi_ioctl(4, 0) reads SWAPREQUEST back, so this waits precisely")
        self.emit("       for the swap: page parity holds AND wait_vblank paces the loop at")
        self.emit("       the display rate (one flip per display frame), like the GB. */")
        self.emit("    while (tgi_ioctl(4, (void*)0)) { }")
        self.emit("    gbs_draw_page ^= 1;     /* the flip swapped draw <-> view */")
        self.emit("}")

    def _emit_cc65_present_sprites(self, sw, sh):
        """The present's sprite chain: every on-screen slot, one Suzy walk."""
        if self._lynx_baked:
            self._emit_lynx_baked_chain()
            return
        camera = getattr(self, 'lynx_camera_used', False)
        self.emit("    if (gbs_spr_visible) {")
        if camera:
            self.emit("        SCB_REHV_PAL *h = 0, *pv = 0, *wh = 0, *wv = 0;")
        else:
            self.emit("        SCB_REHV_PAL *h = 0, *pv = 0;")
        self.emit("        gbs_scb_t *q = gbs_scb;   /* walk by pointer (see the sum loop) */")
        self.emit("        for (s = 0; s < gbs_spr_max; ++s, ++q) {")
        if camera:
            # lynx.sprite_camera: a world slot culls and chains apart (see
            # _emit_lynx_camera_state); landscape only, so SCB = logical.
            self.emit("            if (s >= gbs_cam_first && s < gbs_cam_end) {")
            self.emit("                int lx = q->s.hpos - gbs_cam_x, ty = q->s.vpos - gbs_cam_y;")
            self.emit("                if (q->s.sprctl0 & HFLIP) lx -= 7;")
            self.emit("                if (q->s.sprctl0 & VFLIP) ty -= 7;")
            self.emit("                if (ty >= %d || lx >= %d || ty <= -8 || lx <= -8) continue;" % (sh, sw))
            self.emit("                if (wv) wv->next = (char *)&q->s; else wh = &q->s;")
            self.emit("                wv = &q->s;")
            self.emit("                continue;")
            self.emit("            }")
        self.emit("            /* Skip fully-offscreen slots: hidden/unused sprites parked")
        self.emit("               at the init (-16,-16) or the hide (y=SCREEN_HEIGHT) spot")
        self.emit("               would otherwise feed Suzy off-window literal blits, which")
        self.emit("               corrupt the frame (the rest of the chain drops out). */")
        self.emit("            {   /* cull on the drawn 8x8 (a flipped one is referenced")
        self.emit("                   at its far edge, see gbs_set_sprite_prop) */")
        self.emit("                int lx = q->s.hpos, ty = q->s.vpos;")
        self.emit("                if (q->s.sprctl0 & HFLIP) lx -= 7;")
        self.emit("                if (q->s.sprctl0 & VFLIP) ty -= 7;")
        self.emit("                if (ty >= %d || lx >= %d || ty <= -8 || lx <= -8) continue;" % (sh, sw))
        self.emit("            }")
        self.emit("            if (pv) pv->next = (char *)&q->s; else h = &q->s;")
        self.emit("            pv = &q->s;")
        self.emit("        }")
        if camera:
            self._emit_lynx_camera_draw("wh", "wv")
        self.emit("        /* One Suzy walk for all visible sprites (chained after the bkg). */")
        self.emit("        if (pv) { pv->next = (char *)0; tgi_sprite(h); }")
        self.emit("    }")

    def _emit_pce_sprite_engine(self, prof):
        """Hardware VDC sprite engine for the PC Engine.

        Keeps the Game Boy sprite model (a shared 8x8 2bpp tile table + sprite
        slots referencing tiles by index, same gbs_* API names): each GB tile
        is converted once into the top-left quarter of a 16x16 4bpp VDC sprite
        pattern in VRAM, each sprite slot owns one entry in a RAM mirror of
        the Sprite Attribute Table, and gbs_present() writes the mirror to the
        SATB area in VRAM and waits for vblank, where the VDC's auto-repeat
        SATB DMA (DCR bit 4) transfers it into the sprite hardware -- so
        updates are tear-free. Sprites are an independent plane in front of
        the background, so text and sprites mix freely (unlike the Lynx).

        VRAM map (word addresses; the whole of it is `docs/vram-layout.md`):
        the cc65 conio runtime owns $0000-$1FFF (the 64x32 BAT) and
        $2000-$2FFF (font); tile patterns go at $3000 (`CC65_MAX_TILES` x 64
        words) and the SATB at $7F00 (64 entries x 4 words; unused entries are
        zeroed once = parked off-screen at raster -64). Raising
        CC65_MAX_TILES walks the pattern area toward the background
        characters at $4000, and nothing checks that at build time.

        VDC access: register select at $0200, data at $0202/$0203 (the cc65
        runtime maps the hardware bank into the bottom 8 KB; its IRQ stub
        only *reads* the status port, so the select latch is safe to use
        outside vblank). The control register value extends the runtime's
        $0088 (background + vblank IRQ) with the sprite-enable bit.

        SATB entry format: word 0 = Y + 64, word 1 = X + 32, word 2 = pattern
        code (VRAM word address >> 5), word 3 = attributes (bit 15 Y-invert,
        bit 11 X-invert, bit 7 = in front of the background, bits 3-0 sprite
        palette). sprite.move takes screen-pixel coordinates; moving a sprite
        to y = SCREEN_HEIGHT parks it below the 224-line display, matching
        the portable hide idiom.
        """
        self.emit("/* --- VDC hardware sprite engine (PC Engine) --- */")
        # A residency world states its busiest room's need (rooms
        # SPR_TILE_NEED / SPR_SLOT_NEED, gen_budgets): past the default 40 the
        # table and the slot pool grow to it - slots up to the SATB's 64, and
        # the pattern area, which must stay below the background characters
        # at $4000 (64 tiles from $3000), moves to the free $5000-$7EFF
        # (188 tiles) when it needs more. No need stated = the 40 / $3000
        # layout every existing program has, byte for byte.
        need = getattr(self, 'cc65_spr_need', None)
        tiles, slots, vram = self.CC65_MAX_TILES, 40, None
        if need and (need[0] > tiles or need[1] > slots):
            tiles = max(tiles, min(self.PCE_MAX_PATTERNS, need[0]))
            slots = max(slots, min(64, need[1]))
            if tiles > 64:
                vram = 0x5000
        if tiles != self.CC65_MAX_TILES:
            self.emit("#define GBS_MAX_TILES   %d  /* the busiest room's need */" % tiles)
        else:
            self.emit("#define GBS_MAX_TILES   %d" % self.CC65_MAX_TILES)
        if slots != 40:
            self.emit("#define GBS_MAX_SPRITES %d  /* the busiest room's fans (SATB holds 64) */"
                      % slots)
        else:
            self.emit("#define GBS_MAX_SPRITES 40  /* the GB OAM model; SATB holds 64 */")
        self.emit("#define GBS_VDC_AR (*(volatile uint8_t *)0x0200)  /* register select */")
        self.emit("#define GBS_VDC_DL (*(volatile uint8_t *)0x0202)  /* data low */")
        self.emit("#define GBS_VDC_DH (*(volatile uint8_t *)0x0203)  /* data high (latches) */")
        if vram:
            self.emit("#define GBS_VRAM_TILES 0x%04Xu  /* past 64 tiles: the free VRAM above the bkg characters */"
                      % vram)
        else:
            self.emit("#define GBS_VRAM_TILES 0x3000u  /* above the conio BAT + font */")
        self.emit("#define GBS_VRAM_SATB  0x7F00u")
        self.emit("static uint16_t gbs_satb[GBS_MAX_SPRITES * 4];  /* SATB RAM mirror */")
        self.emit("static uint8_t gbs_spr_tile[GBS_MAX_SPRITES];")
        self.emit("static uint8_t gbs_spr_max = 0;   /* highest slot touched + 1 */")
        self.emit("static uint8_t gbs_spr_used = 0;  /* engine active this program */")
        self.emit("static uint8_t gbs_spr_inited = 0;")
        if self.metasprite_used:
            self._emit_cc65_meta_state()
        self.emit("static void gbs_vreg(uint8_t reg, uint16_t value) {")
        self.emit("    GBS_VDC_AR = reg;")
        self.emit("    GBS_VDC_DL = (uint8_t)value;")
        self.emit("    GBS_VDC_DH = (uint8_t)(value >> 8);")
        self.emit("}")
        self.emit("/* Point VRAM writes at `addr`; data writes then auto-increment. */")
        self.emit("static void gbs_vram_addr(uint16_t addr) {")
        self.emit("    gbs_vreg(0, addr);  /* MAWR */")
        self.emit("    GBS_VDC_AR = 2;     /* VWR */")
        self.emit("}")
        self.emit("static void gbs_spr_init(void) {")
        self.emit("    uint8_t s;")
        self.emit("    uint16_t i;")
        self.emit("    if (gbs_spr_inited) return;")
        self.emit("    gbs_video_init();")
        self.emit("    /* Sprite palette 0 (VCE colour index $100+): GB greys, 9-bit")
        self.emit("       GGGRRRBBB words. Entry 0 is hardware-transparent -- the Game")
        self.emit("       Boy colour-0-transparent object model. */")
        self.emit("    (*(volatile uint8_t *)0x0402) = 0x00;  /* VCE address low */")
        self.emit("    (*(volatile uint8_t *)0x0403) = 0x01;  /* VCE address high: $100 */")
        self.emit("    (*(volatile uint8_t *)0x0404) = 0x00;  /* 0: transparent */")
        self.emit("    (*(volatile uint8_t *)0x0405) = 0x00;")
        self.emit("    (*(volatile uint8_t *)0x0404) = 0xB6;  /* 1: light grey */")
        self.emit("    (*(volatile uint8_t *)0x0405) = 0x01;")
        self.emit("    (*(volatile uint8_t *)0x0404) = 0xDB;  /* 2: dark grey */")
        self.emit("    (*(volatile uint8_t *)0x0405) = 0x00;")
        self.emit("    (*(volatile uint8_t *)0x0404) = 0xFF;  /* 3: white */")
        self.emit("    (*(volatile uint8_t *)0x0405) = 0x01;")
        self.emit("    /* Park all 64 hardware SATB entries off-screen once. */")
        self.emit("    gbs_vram_addr(GBS_VRAM_SATB);")
        self.emit("    for (i = 0; i < 64 * 4; ++i) { GBS_VDC_DL = 0; GBS_VDC_DH = 0; }")
        self.emit("    gbs_vreg(19, GBS_VRAM_SATB);  /* SATB source address */")
        self.emit("    gbs_vreg(15, 0x0010);         /* DCR: repeat SATB DMA every vblank */")
        self.emit("    gbs_vreg(5, 0x00C8);          /* CR: BG + sprites + vblank IRQ */")
        self.emit("    for (s = 0; s < GBS_MAX_SPRITES; ++s) {")
        self.emit("        gbs_satb[(uint16_t)s * 4]     = 0;  /* y: off-screen */")
        self.emit("        gbs_satb[(uint16_t)s * 4 + 1] = 0;")
        self.emit("        gbs_satb[(uint16_t)s * 4 + 2] = GBS_VRAM_TILES >> 5;  /* tile 0 */")
        self.emit("        gbs_satb[(uint16_t)s * 4 + 3] = 0x0080;  /* in front of the BG */")
        self.emit("    }")
        self.emit("    gbs_spr_inited = 1;")
        self.emit("}")
        bpp4 = self.sprite_src_bpp == 4
        if bpp4:
            # 16-colour (4bpp) tier: the asset is packed-nibble (32 bytes/tile, two
            # pixels/byte, high nibble = left). Distribute each pixel's 4 bits across
            # the VDC's 4 bitplanes -- plane k = bit k of the pixel, exactly extending
            # the 2bpp layout below (which fills only planes 0 + 1), so a 4-colour tile
            # renders identically either way. The 8 source pixels are the LEFT half of
            # the 16x16 pattern (high byte of each plane word); the right half + rows
            # 8-15 stay 0. (review 2026-07-12 item 5.1: exploit the PCE's 16-colour VDC.)
            self.emit("/* Convert one 8x8 4bpp packed-nibble tile (32 bytes) into the top-")
            self.emit("   left quarter of a 16x16 VDC sprite pattern: plane k = bit k of")
            self.emit("   each pixel (extends the 2bpp path to all 4 VDC planes). */")
            self.emit("static void gbs_conv_tile(uint8_t tile, const uint8_t *px) {")
            self.emit("    uint8_t plane, row, col, b, p;")
            self.emit("    const uint8_t *r;")
            self.emit("    gbs_vram_addr(GBS_VRAM_TILES + (uint16_t)tile * 64);")
            self.emit("    for (plane = 0; plane < 4; ++plane) {")
            self.emit("        for (row = 0; row < 16; ++row) {")
            self.emit("            b = 0;")
            self.emit("            if (row < 8) {")
            self.emit("                r = px + (uint16_t)row * 4;")
            self.emit("                for (col = 0; col < 8; ++col) {")
            self.emit("                    p = (col & 1) ? (uint8_t)(r[col >> 1] & 0x0F)")
            self.emit("                                  : (uint8_t)(r[col >> 1] >> 4);")
            self.emit("                    if (p & (uint8_t)(1u << plane))")
            self.emit("                        b |= (uint8_t)(1u << (7 - col));")
            self.emit("                }")
            self.emit("            }")
            self.emit("            GBS_VDC_DL = 0; GBS_VDC_DH = b;")
            self.emit("        }")
            self.emit("    }")
            self.emit("}")
        else:
            self.emit("/* Convert one 8x8 GB 2bpp tile (16 bytes) into the top-left quarter")
            self.emit("   of a 16x16 VDC sprite pattern (4 planes x 16 rows; the GB byte is")
            self.emit("   the left half = the high byte of each plane word). */")
            self.emit("static void gbs_conv_tile(uint8_t tile, const uint8_t *gb) {")
            self.emit("    uint8_t row;")
            self.emit("    gbs_vram_addr(GBS_VRAM_TILES + (uint16_t)tile * 64);")
            self.emit("    for (row = 0; row < 16; ++row) {  /* plane 0 */")
            self.emit("        GBS_VDC_DL = 0; GBS_VDC_DH = row < 8 ? gb[row * 2] : 0;")
            self.emit("    }")
            self.emit("    for (row = 0; row < 16; ++row) {  /* plane 1 */")
            self.emit("        GBS_VDC_DL = 0; GBS_VDC_DH = row < 8 ? gb[row * 2 + 1] : 0;")
            self.emit("    }")
            self.emit("    for (row = 0; row < 32; ++row) {  /* planes 2 + 3 empty */")
            self.emit("        GBS_VDC_DL = 0; GBS_VDC_DH = 0;")
            self.emit("    }")
            self.emit("}")
        if self.caps['has_bkg'] and self.cc65_bkg_imported:
            self.emit("void gbs_bkg_scroll_flush(void);  /* latches BXR/BYR in vblank (defined below) */")
        self.emit("void gbs_present(void) {")
        self.emit("    uint16_t i, words;")
        if self.caps['has_sound']:
            self.emit("    if (gbs_snd_frames && --gbs_snd_frames == 0) gbs_sound_stop();")
        self.emit("    if (gbs_spr_used) {")
        self.emit("        /* Flush the SATB mirror to VRAM; the auto-repeat DMA picks it")
        self.emit("           up at the next vblank, so the update is tear-free. */")
        self.emit("        gbs_vram_addr(GBS_VRAM_SATB);")
        self.emit("        words = (uint16_t)gbs_spr_max << 2;")
        self.emit("        for (i = 0; i < words; ++i) {")
        self.emit("            GBS_VDC_DL = (uint8_t)gbs_satb[i];")
        self.emit("            GBS_VDC_DH = (uint8_t)(gbs_satb[i] >> 8);")
        self.emit("        }")
        self.emit("    }")
        self.emit("    waitvsync();")
        if self.caps['has_bkg'] and self.cc65_bkg_imported:
            self.emit("    gbs_bkg_scroll_flush();  /* latch BXR/BYR in vblank -- no scroll jitter */")
        self.emit("}")
        stride = 32 if bpp4 else 16
        self.emit("void gbs_set_sprite_data(uint8_t first, uint8_t count, const uint8_t *data) {")
        self.emit("    uint8_t i;")
        self.emit("    gbs_spr_init();")
        self.emit("    for (i = 0; i < count; ++i)")
        self.emit("        if ((uint8_t)(first + i) < GBS_MAX_TILES)")
        self.emit("            gbs_conv_tile((uint8_t)(first + i), data + (uint16_t)i * %d);"
                  % stride)
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        if self.spr_font_glyph_used:
            self._emit_cc65_pce_font_glyph(bpp4)
        self.emit("void gbs_set_sprite_tile(uint8_t nb, uint8_t tile) {")
        self.emit("    gbs_spr_init();")
        if self.metasprite_used:
            self._emit_cc65_meta_branch('tile')
        self.emit("    if (nb < GBS_MAX_SPRITES && tile < GBS_MAX_TILES) {")
        self.emit("        gbs_spr_tile[nb] = tile;")
        self.emit("        /* 16x16 pattern = 64 words = 2 pattern-code units per tile. */")
        self.emit("        gbs_satb[(uint16_t)nb * 4 + 2] =")
        self.emit("            (uint16_t)((GBS_VRAM_TILES >> 5) + ((uint16_t)tile << 1));")
        self.emit("        if (nb >= gbs_spr_max) gbs_spr_max = nb + 1;")
        self.emit("    }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        self.emit("uint8_t gbs_get_sprite_tile(uint8_t nb) {")
        self.emit("    return nb < GBS_MAX_SPRITES ? gbs_spr_tile[nb] : 0;")
        self.emit("}")
        self.emit("/* prop carries FLIP_X / FLIP_Y -> the SATB X/Y-invert bits. */")
        self.emit("void gbs_set_sprite_prop(uint8_t nb, uint8_t prop) {")
        self.emit("    gbs_spr_init();")
        if self.metasprite_used:
            self._emit_cc65_meta_branch('prop')
        self.emit("    if (nb < GBS_MAX_SPRITES)")
        if self.palette_imported:
            self.emit("        /* Keep the sprite.set_palette bits (SATB attr bits 0-3). */")
            self.emit("        gbs_satb[(uint16_t)nb * 4 + 3] = (uint16_t)(0x0080u")
            self.emit("            | (gbs_satb[(uint16_t)nb * 4 + 3] & 0x000Fu)")
        else:
            self.emit("        gbs_satb[(uint16_t)nb * 4 + 3] = (uint16_t)(0x0080u")
        self.emit("            | ((prop & FLIP_X) ? 0x0800u : 0u)")
        self.emit("            | ((prop & FLIP_Y) ? 0x8000u : 0u));")
        self.emit("}")
        self.emit("/* sprite.move takes screen-pixel coordinates (top-left origin); the")
        self.emit("   SATB origin is offset by (32, 64). */")
        self.emit("void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y) {")
        self.emit("    gbs_spr_init();")
        if self.metasprite_used:
            self._emit_cc65_meta_branch('move')
        self.emit("    if (nb < GBS_MAX_SPRITES) {")
        if getattr(self, 'view_used', False) and self.platform == 'pce':
            # video.set_view: the letterbox offset. Every placement (metasprite
            # children and parks included) reaches this leaf; a park plus the
            # offset is still below the 224-line screen.
            self.emit("        gbs_satb[(uint16_t)nb * 4]     = (uint16_t)(64u + y + gbs_view_oy);")
            self.emit("        gbs_satb[(uint16_t)nb * 4 + 1] = (uint16_t)(32u + x + gbs_view_ox);")
        else:
            self.emit("        gbs_satb[(uint16_t)nb * 4]     = (uint16_t)(64u + y);")
            self.emit("        gbs_satb[(uint16_t)nb * 4 + 1] = (uint16_t)(32u + x);")
        self.emit("        if (nb >= gbs_spr_max) gbs_spr_max = nb + 1;")
        self.emit("    }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        self.emit("/* Show/hide toggle the CR sprite-enable bit (text is unaffected). */")
        self._emit_cc65_move_world()
        self.emit("void gbs_show_sprites(void) { gbs_spr_init(); gbs_vreg(5, 0x00C8); gbs_spr_used = 1; }")
        self.emit("void gbs_hide_sprites(void) { gbs_spr_init(); gbs_vreg(5, 0x0088); }")
        self.emit("void gbs_show_bkg(void) { }")
        if self.load_sprite16_used:
            if bpp4 and self.palette_fade_used:
                # Through the fade's shadow (gbs_vce, defined with the palette
                # verbs below): a 16-colour palette loaded while the screen is
                # dark must load dimmed, like every other palette.
                self.emit("/* palette.load_sprite16: 16 colour words -> VCE sprite palette 0")
                self.emit("   ($100..$10F), through the fade's shadow. */")
                self.emit("static void gbs_vce(uint16_t index, uint16_t color);")
                self.emit("void gbs_load_sprite_pal16(const uint16_t *pal) {")
                self.emit("    uint8_t p;")
                self.emit("    gbs_spr_init();")
                self.emit("    for (p = 0; p < 16; ++p) gbs_vce((uint16_t)(0x100 + p), pal[p]);")
                self.emit("}")
            elif bpp4:
                self.emit("/* palette.load_sprite16: 16 colour words -> VCE sprite palette 0")
                self.emit("   ($100..$10F). Entry 0 is hardware-transparent (GB object model). */")
                self.emit("void gbs_load_sprite_pal16(const uint16_t *pal) {")
                self.emit("    uint8_t p;")
                self.emit("    gbs_spr_init();")
                self.emit("    (*(volatile uint8_t *)0x0402) = 0x00;  /* VCE address low */")
                self.emit("    (*(volatile uint8_t *)0x0403) = 0x01;  /* VCE address high: $100 */")
                self.emit("    for (p = 0; p < 16; ++p) {")
                self.emit("        (*(volatile uint8_t *)0x0404) = (uint8_t)pal[p];")
                self.emit("        (*(volatile uint8_t *)0x0405) = (uint8_t)(pal[p] >> 8);")
                self.emit("    }")
                self.emit("}")
            else:
                self.emit("/* 16-colour sprite palette: a no-op on the PCE 2bpp sprite tier. */")
                self.emit("void gbs_load_sprite_pal16(const uint16_t *pal) { (void)pal; }")
        if self.metasprite_used:
            self._emit_cc65_meta_func()

    def _emit_cc65_pce_font_glyph(self, bpp4):
        """`sprite.font_glyph(tile, ch)` on the PC Engine: the console font's
        glyph for `ch` as ONE sprite tile, read from cc65's LINKED console font
        `pce_font` (the conio font, cc65 `libsrc/pce/vga.s`: 8 bytes per
        character indexed by its code, bit 7 = leftmost pixel) - the font PCE
        text already draws with, and never a copy in our source.

        Built in whatever form this build's `gbs_set_sprite_data` takes, so the
        glyph renders as colour 3 on transparent 0 either way: 16 bytes of GB
        2bpp (both planes = the row), or 32 bytes of packed nibbles on the
        16-colour sprite tier."""
        self.emit("/* sprite.font_glyph(tile, ch): the PCE console font's glyph for ch, read")
        self.emit("   from cc65's LINKED pce_font (8 B per character code, bit 7 = left),")
        self.emit("   uploaded as one sprite tile in colour 3 on transparent 0. */")
        self.emit("extern unsigned char pce_font[];")
        self.emit("void gbs_sprite_font_glyph(uint8_t tile, uint8_t ch) {")
        if bpp4:
            self.emit("    uint8_t buf[32], i, k, r;")
        else:
            self.emit("    uint8_t buf[16], i;")
        self.emit("    const unsigned char *src;")
        self.emit("    if (ch < 32 || ch > 127) ch = 32;   /* unprintable -> space */")
        self.emit("    src = pce_font + (uint16_t)ch * 8;")
        if bpp4:
            self.emit("    for (i = 0; i < 8; ++i) {")
            self.emit("        r = src[i];")
            self.emit("        for (k = 0; k < 4; ++k) {  /* two pixels per byte, left high */")
            self.emit("            buf[i * 4 + k] = (uint8_t)(((r & 0x80) ? 0x30 : 0)")
            self.emit("                                     | ((r & 0x40) ? 0x03 : 0));")
            self.emit("            r = (uint8_t)(r << 2);")
            self.emit("        }")
            self.emit("    }")
        else:
            self.emit("    for (i = 0; i < 8; ++i) buf[i * 2] = buf[i * 2 + 1] = src[i];")
        self.emit("    gbs_set_sprite_data(tile, 1, buf);")
        self.emit("}")

    def _emit_cc65_meta_state(self):
        self.emit("/* --- Metasprite layer (sprite.set_meta) --- */")
        self.emit("static uint8_t gbs_meta_w[GBS_MAX_SPRITES];   /* 0/1 = single */")
        self.emit("static uint8_t gbs_meta_h[GBS_MAX_SPRITES];")
        self.emit("static uint8_t gbs_meta_prop[GBS_MAX_SPRITES];")
        if self._fan_pal:
            self.emit("/* A fan's sprite palette (slot + 1; 0 = none, or a per-CELL map):")
            self.emit("   sprite.set_palette on a base records it, set_meta re-applies it to")
            self.emit("   the cells, as the GB fan's recorded prop does. */")
            self.emit("static uint8_t gbs_meta_spal[GBS_MAX_SPRITES];")
        if self.meta_list_used:
            self.emit("/* Per-object descriptor state (sprite.set_meta_list): authored")
            self.emit("   offsets per child slot; gbs_meta_w[base] == 0xFF marks a list")
            self.emit("   base, its object count in gbs_meta_h[base]. */")
            self.emit("static uint8_t gbs_meta_dx[GBS_MAX_SPRITES];")
            self.emit("static uint8_t gbs_meta_dy[GBS_MAX_SPRITES];")
            self.emit("static uint8_t gbs_meta_pw[GBS_MAX_SPRITES];")
        if self.meta_mask_used:
            self.emit("/* Per-metasprite BLANK-COLUMN mask (sprite.set_meta_mask): bit c set")
            self.emit("   = column c draws nothing and consumes NO tile. There is no")
            self.emit("   sprites-per-scanline limit here, but the tile NUMBERING is the")
            self.emit("   same on every backend - a sparse frame's sheet holds only its")
            self.emit("   drawn columns - so the skip has to be honoured. */")
            self.emit("static uint16_t gbs_meta_msk[GBS_MAX_SPRITES];")
        if self.meta_clip_used:
            # The EDGE CLIP - see the gbdk mixin's _emit_meta_clip_table for
            # the whole story. Short version: `sprite.move`'s x is a uint8_t,
            # so a world-space renderer whose actor has scrolled past the left
            # edge hands the fan an already-WRAPPED origin and its leading
            # columns then draw at the far RIGHT. Both cc65 targets store a
            # screen x of 0..255 (the Lynx SCB hpos straight, the PCE offset
            # by the SATB's own 32), so neither has any negative headroom and
            # the sign has to come from the caller.
            self.emit("/* Edge clip for sprite.move_world: draw fan columns")
            self.emit("   [gbs_clip_lo, gbs_clip_hi) and PARK the rest. hi == 0 = no clip,")
            self.emit("   which is the BSS default - plain sprite.move is unchanged. */")
            self.emit("static uint8_t gbs_clip_lo;")
            self.emit("static uint8_t gbs_clip_hi;")
            if self.meta_list_used:
                self.emit("static uint8_t gbs_clip_dlo;")
                self.emit("static uint8_t gbs_clip_dhi;")

    @property
    def _fan_pal(self):
        """A cc65 program that recolours metasprites: the fan carries ONE
        palette across its cells (a 2x2 actor showed one coloured cell). Both
        cc65 engines give every 8x8 cell its own hardware sprite - the PCE a
        SATB entry, the Lynx an SCB - and both set the BASE cell only."""
        return (self.platform in ("pce", "lynx") and self.palette_imported
                and self.metasprite_used)

    def _fan_cell_pal(self, cell, slot):
        """The C statement giving sprite `cell` palette `slot`, per console."""
        if self.platform == "lynx":
            return ("{ gbs_scb[%s].s.penpal[0] = gbs_pal_penpal[%s][0]; "
                    "gbs_scb[%s].s.penpal[1] = gbs_pal_penpal[%s][1]; }"
                    % (cell, slot, cell, slot))
        return ("gbs_satb[(uint16_t)(%s) * 4 + 3] = (uint16_t)("
                "(gbs_satb[(uint16_t)(%s) * 4 + 3] & ~0x000Fu) | %s);"
                % (cell, cell, slot))

    def _emit_fan_pal_apply(self, n_expr):
        """Re-apply a base's recorded fan palette to its `n_expr` cells."""
        self.emit("    if (gbs_meta_spal[base]) {")
        self.emit("        uint8_t fk, fp = (uint8_t)(gbs_meta_spal[base] - 1);")
        self.emit("        for (fk = 0; fk < %s && (uint8_t)(base + fk) < GBS_MAX_SPRITES; ++fk)" % n_expr)
        self.emit("            " + self._fan_cell_pal("base + fk", "fp"))
        if self.platform == "lynx":
            self.emit("        gbs_force = 1;   /* penpal repoint -> present must re-blit */")
        self.emit("    }")

    def _emit_cc65_move_world(self):
        """`sprite.move_world` + `sprite.meta_cols` on cc65 - the signed-entry
        pair the gbdk backend documents. The representable screen x here is a
        flat 0..255 on both targets, so there is no per-console headroom term:
        a column is drawable exactly while `x + 8c` is in range."""
        if not self.meta_clip_used:
            return
        self.emit("/* sprite.meta_cols: the metasprite width in 8x8 tile columns. */")
        self.emit("uint8_t gbs_meta_cols(uint8_t nb) {")
        self.emit("    uint8_t w;")
        self.emit("    if (nb >= GBS_MAX_SPRITES) return 1;")
        self.emit("    w = gbs_meta_w[nb];")
        if self.meta_list_used:
            self.emit("    if (w == 0xFF) return (uint8_t)((gbs_meta_pw[nb] + 7) >> 3);")
        self.emit("    return w ? w : 1;")
        self.emit("}")
        self.emit("/* sprite.move_world: sprite.move in SIGNED screen coordinates, so a")
        self.emit("   column that has scrolled off an edge is PARKED rather than wrapped")
        self.emit("   onto the opposite one. A one-shot: cleared on the way out. */")
        self.emit("void gbs_move_sprite_world(uint8_t nb, int16_t x, int16_t y) {")
        self.emit("    uint8_t w, h, lo, hi, t;")
        self.emit("    int16_t n;")
        self.emit("    if (nb >= GBS_MAX_SPRITES) return;")
        self.emit("    w = gbs_meta_w[nb]; h = gbs_meta_h[nb];")
        if self.meta_list_used:
            self.emit("    if (w == 0xFF) {")
            self.emit("        gbs_clip_dlo = (x < 0) ? ((-x > 255) ? 255 : (uint8_t)(-x)) : 0;")
            self.emit("        n = 255 - x;")
            self.emit("        gbs_clip_dhi = (n < 0) ? 1 : ((n > 254) ? 0 : (uint8_t)(n + 1));")
            self.emit("        gbs_move_sprite(nb, (uint8_t)x, (uint8_t)y);")
            self.emit("        gbs_clip_dhi = 0;")
            self.emit("        return;")
            self.emit("    }")
        self.emit("    if (w <= 1 && h <= 1) {")
        self.emit("        if (x < 0 || x > 255) gbs_move_sprite(nb, 200, %s);" % self._spr_park_y("220"))
        self.emit("        else gbs_move_sprite(nb, (uint8_t)x, (uint8_t)y);")
        self.emit("        return;")
        self.emit("    }")
        self.emit("    if (w == 0) w = 1;")
        self.emit("    lo = 0; hi = w;")
        self.emit("    if (x < 0) {")
        self.emit("        t = (uint8_t)(((-x) + 7) >> 3);")
        self.emit("        lo = (t > w) ? w : t;")
        self.emit("    }")
        self.emit("    n = 255 - x;")
        self.emit("    if (n < 0) { lo = w; }")
        self.emit("    else if ((n >> 3) < (int16_t)w) { hi = (uint8_t)((n >> 3) + 1); }")
        self.emit("    if (lo > hi) lo = hi;")
        # The cc65 fan mirrors per child (`cc = w - 1 - c`) rather than
        # reversing the walk, so its loop index IS the screen column - no
        # mirroring of the bounds here, unlike the gbdk arm.
        self.emit("    gbs_clip_lo = lo;")
        self.emit("    gbs_clip_hi = hi ? hi : w;   /* 0 is the NO-CLIP sentinel */")
        self.emit("    gbs_move_sprite(nb, (uint8_t)x, (uint8_t)y);")
        self.emit("    gbs_clip_lo = 0;")
        self.emit("    gbs_clip_hi = 0;")
        self.emit("}")

    def _spr_park_y(self, other):
        """The y a PARK writes (and a hidden base is tested against).

        200 / 220 are off-screen on the Lynx's 102 lines, but the PC Engine
        shows 224 (`SCREEN_HEIGHT`), so a parked sprite sat on screen there."""
        return "SCREEN_HEIGHT" if self.platform == "pce" else other

    def _emit_list_child_move(self, xexpr, indent):
        """One LIST-shaped child's move; on the PCE a child whose row passes
        the bottom of the screen parks instead of wrapping to the top."""
        pad = " " * indent
        if self.platform == "pce":
            self.emit(pad + "if ((uint16_t)y + gbs_meta_dy[s] >= SCREEN_HEIGHT)")
            self.emit(pad + "    gbs_move_sprite(s, 200, SCREEN_HEIGHT);")
            self.emit(pad + "else")
            self.emit(pad + "gbs_move_sprite(s, %s, (uint8_t)(y + gbs_meta_dy[s]));" % xexpr)
        else:
            self.emit(pad + "gbs_move_sprite(s, %s," % xexpr)
            self.emit(pad + "                (uint8_t)(y + gbs_meta_dy[s]));")

    def _emit_cc65_meta_branch(self, op):
        """Early meta-dispatch branch for gbs_move_sprite / set_tile / set_prop
        (op in {'move','tile','prop'}). Placed right after gbs_spr_init()."""
        if op == 'prop':
            self.emit("    if (nb < GBS_MAX_SPRITES) gbs_meta_prop[nb] = prop;")
        if self.meta_list_used:
            # A LIST-shaped base (sprite.set_meta_list): children at their
            # authored offsets. FLIP_X mirrors around the frame's pixel width;
            # a hidden base (>= 200, park's convention) parks every child --
            # the present loop skips fully-offscreen slots per child anyway,
            # but a u8 wrap of 200 + a large dy could land back on screen.
            self.emit("    if (nb < GBS_MAX_SPRITES && gbs_meta_w[nb] == 0xFF) {")
            if op == 'move':
                self.emit("        uint8_t k, n = gbs_meta_h[nb], s = nb;")
                if self.meta_clip_used:
                    self.emit("        uint8_t dlo = gbs_clip_dlo, dhi = gbs_clip_dhi;")
                self.emit("        if (y >= %s) {" % self._spr_park_y("200"))
                self.emit("            gbs_meta_w[nb] = 0;")
                self.emit("            for (k = 0; k < n; ++k) { gbs_move_sprite(s, 200, %s); ++s; }" % self._spr_park_y("220"))
                self.emit("            gbs_meta_w[nb] = 0xFF;")
                self.emit("            return;")
                self.emit("        }")
                self.emit("        if (gbs_meta_prop[nb] & FLIP_X) {")
                self.emit("            uint8_t fw = (uint8_t)(x + gbs_meta_pw[nb] - 8);")
                self.emit("            gbs_meta_w[nb] = 0;")
                self.emit("            for (k = 0; k < n; ++k) {")
                if self.meta_clip_used:
                    self.emit("                {")
                    self.emit("                uint8_t dm = (uint8_t)(gbs_meta_pw[nb] - 8 - gbs_meta_dx[s]);")
                    self.emit("                if (dm < dlo || (dhi && dm >= dhi))")
                    self.emit("                    gbs_move_sprite(s, 200, %s);" % self._spr_park_y("220"))
                    self.emit("                else")
                    self._emit_list_child_move("(uint8_t)(fw - gbs_meta_dx[s])", 16)
                    self.emit("                }")
                else:
                    self._emit_list_child_move("(uint8_t)(fw - gbs_meta_dx[s])", 16)
                self.emit("                ++s;")
                self.emit("            }")
                self.emit("            gbs_meta_w[nb] = 0xFF;")
                self.emit("            return;")
                self.emit("        }")
                self.emit("        gbs_meta_w[nb] = 0;")
                self.emit("        for (k = 0; k < n; ++k) {")
                if self.meta_clip_used:
                    self.emit("            if (gbs_meta_dx[s] < dlo || (dhi && gbs_meta_dx[s] >= dhi))")
                    self.emit("                gbs_move_sprite(s, 200, %s);" % self._spr_park_y("220"))
                    self.emit("            else")
                self._emit_list_child_move("(uint8_t)(x + gbs_meta_dx[s])", 12)
                self.emit("            ++s;")
                self.emit("        }")
                self.emit("        gbs_meta_w[nb] = 0xFF;")
                self.emit("        return;")
            elif op == 'tile':
                self.emit("        return;  /* a LIST base re-tiles only via set_meta_list */")
            else:  # prop
                self.emit("        uint8_t k, n = gbs_meta_h[nb];")
                self.emit("        gbs_meta_w[nb] = 0;")
                self.emit("        for (k = 0; k < n; ++k) gbs_set_sprite_prop((uint8_t)(nb + k), prop);")
                self.emit("        gbs_meta_w[nb] = 0xFF;")
                self.emit("        return;")
            self.emit("    }")
        # The bounds check must come BEFORE the table reads (a slot >=
        # GBS_MAX_SPRITES would read past gbs_meta_w into its neighbours).
        self.emit("    if (nb < GBS_MAX_SPRITES && (gbs_meta_w[nb] > 1 || gbs_meta_h[nb] > 1)) {")
        self.emit("        uint8_t w = gbs_meta_w[nb], h = gbs_meta_h[nb];")
        if op == 'move':
            self.emit("        uint8_t r, c, s = nb, prop = gbs_meta_prop[nb], cc, rr;")
            if self.platform == "pce":
                self.emit("        uint16_t ry;")
            if self.meta_clip_used:
                self.emit("        uint8_t clo = gbs_clip_lo;")
                self.emit("        uint8_t chi = gbs_clip_hi ? gbs_clip_hi : w;")
        else:
            self.emit("        uint8_t r, c, idx = 0;")
        blank = self.meta_mask_used and op in ('move', 'tile')
        if blank:
            self.emit("        uint16_t msk = gbs_meta_msk[nb];")
            if op == 'tile':
                # Under a mask the CHILD index and the TILE index part company
                # (a blank column takes a slot but no tile), so the one shared
                # counter is not enough.
                self.emit("        uint8_t tc = 0;")
        self.emit("        gbs_meta_w[nb] = 0; gbs_meta_h[nb] = 0;  /* base acts single while iterating */")
        self.emit("        for (r = 0; r < h; ++r)")
        self.emit("            for (c = 0; c < w; ++c) {")
        if blank:
            # A blank column is parked off screen (the present loop skips a
            # fully-offscreen slot) and takes no tile from the frame's block.
            self.emit("                if (msk & ((uint16_t)1 << c)) {")
            if op == 'move':
                self.emit("                    gbs_move_sprite(s, 200, %s);" % self._spr_park_y("200"))
                self.emit("                    ++s;")
            else:
                self.emit("                    ++idx;")
            self.emit("                    continue;")
            self.emit("                }")
        if op == 'move':
            self.emit("                cc = (prop & FLIP_X) ? (uint8_t)(w - 1 - c) : c;")
            self.emit("                rr = (prop & FLIP_Y) ? (uint8_t)(h - 1 - r) : r;")
            if self.platform == "pce":
                # The PCE screen is 224 lines and its SATB y is 16-bit, so a
                # row past the bottom must PARK: the u8 `y + rr * 8` wrapped a
                # tall fan's lower rows (a parked 7x8 foe, an actor at the
                # bottom of a tall room) back onto the TOP of the screen.
                self.emit("                ry = (uint16_t)y + (uint16_t)(rr * 8);")
                cond = "ry >= SCREEN_HEIGHT"
                if self.meta_clip_used:
                    cond += " || cc < clo || cc >= chi"
                self.emit("                if (%s)" % cond)
                self.emit("                    gbs_move_sprite(s, 200, SCREEN_HEIGHT);")
                self.emit("                else")
                self.emit("                gbs_move_sprite(s, (uint8_t)(x + cc * 8), (uint8_t)ry);")
            else:
                if self.meta_clip_used:
                    # `cc` is the SCREEN column (this fan mirrors per child
                    # rather than reversing the walk), so the bounds apply to
                    # it directly. A clipped column must be PARKED, not
                    # skipped: unlike a blank one it was DRAWN last frame and
                    # has just left the screen.
                    self.emit("                if (cc < clo || cc >= chi)")
                    self.emit("                    gbs_move_sprite(s, 200, 220);")
                    self.emit("                else")
                self.emit("                gbs_move_sprite(s, (uint8_t)(x + cc * 8), (uint8_t)(y + rr * 8));")
            self.emit("                ++s;")
        elif op == 'tile':
            if blank:
                self.emit("                gbs_set_sprite_tile((uint8_t)(nb + idx), (uint8_t)(tile + tc));")
                self.emit("                ++tc;")
            else:
                self.emit("                gbs_set_sprite_tile((uint8_t)(nb + idx), (uint8_t)(tile + idx));")
            self.emit("                ++idx;")
        else:  # prop
            self.emit("                gbs_set_sprite_prop((uint8_t)(nb + idx), prop);")
            self.emit("                ++idx;")
        self.emit("            }")
        self.emit("        gbs_meta_w[nb] = w; gbs_meta_h[nb] = h;")
        self.emit("        return;")
        self.emit("    }")

    def _emit_cc65_meta_func(self, whole=False):
        """The metasprite fan, its masked form and the descriptor-list form.

        `whole` (Lynx `lynx_sprites = "whole"`): the plain fan is replaced by
        the one-SCB collapse (`_emit_lynx_whole_meta_func`), and the masked and
        list forms raise `gbs_spr_one` so each child draws its OWN tile."""
        one_on = "    gbs_spr_one = 1;   /* children draw single tiles */"
        one_off = "    gbs_spr_one = 0;"
        if not whole or self.meta_mask_used:
            self._emit_cc65_meta_fan(whole, one_on, one_off)
        if self.meta_list_used:
            self._emit_cc65_meta_list(whole, one_on, one_off)

    def _emit_cc65_meta_fan(self, whole, one_on, one_off):
        self.emit("/* Define a metasprite: reserve base..base+w*h-1, tiles row-major. */")
        if self.meta_mask_used:
            self.emit("void gbs_set_metasprite_mask(uint8_t base, uint8_t tile, uint8_t w,")
            self.emit("                             uint8_t h, uint16_t mask) {")
        else:
            self.emit("void gbs_set_metasprite(uint8_t base, uint8_t tile, uint8_t w, uint8_t h) {")
        self.emit("    uint8_t r, c, idx = 0, prop;")
        if self.meta_mask_used:
            self.emit("    uint8_t tc = 0;")
        self.emit("    if (base >= GBS_MAX_SPRITES) return;  /* no such sprite slot */")
        if whole:
            self.emit(one_on)
        self.emit("    prop = gbs_meta_prop[base];")
        self.emit("    gbs_meta_w[base] = 0; gbs_meta_h[base] = 0;  /* assign children as singles */")
        if self.meta_mask_used:
            self.emit("    gbs_meta_msk[base] = mask;")
        self.emit("    for (r = 0; r < h; ++r)")
        self.emit("        for (c = 0; c < w; ++c) {")
        if self.meta_mask_used:
            self.emit("            if (mask & ((uint16_t)1 << c)) {")
            self.emit("                gbs_move_sprite((uint8_t)(base + idx), 200, %s);" % self._spr_park_y("200"))
            self.emit("            } else {")
            self.emit("                gbs_set_sprite_tile((uint8_t)(base + idx), (uint8_t)(tile + tc));")
            self.emit("                gbs_set_sprite_prop((uint8_t)(base + idx), prop);")
            self.emit("                ++tc;")
            self.emit("            }")
        else:
            self.emit("            gbs_set_sprite_tile((uint8_t)(base + idx), (uint8_t)(tile + idx));")
            self.emit("            gbs_set_sprite_prop((uint8_t)(base + idx), prop);")
        self.emit("            if ((uint8_t)(base + idx) < GBS_MAX_SPRITES) {")
        self.emit("                gbs_meta_w[base + idx] = 1; gbs_meta_h[base + idx] = 1;")
        self.emit("            }")
        self.emit("            ++idx;")
        self.emit("        }")
        if self._fan_pal:
            self._emit_fan_pal_apply("idx")
        if whole:
            self.emit(one_off)
        self.emit("    gbs_meta_w[base] = w; gbs_meta_h[base] = h;")
        self.emit("}")
        if self.meta_mask_used and not whole:
            self.emit("/* sprite.set_meta: the masked form with an empty mask, so an")
            self.emit("   unmasked upload always CLEARS a stale mask. */")
            self.emit("void gbs_set_metasprite(uint8_t base, uint8_t tile, uint8_t w, uint8_t h) {")
            self.emit("    gbs_set_metasprite_mask(base, tile, w, h, 0);")
            self.emit("}")

    def _emit_cc65_meta_list(self, whole, one_on, one_off):
        """`sprite.set_meta_list`: the per-OBJECT descriptor form."""
        self.emit("/* The per-OBJECT descriptor form (sprite.set_meta_list): n entries of")
        self.emit("   (dy, dx, dtile, props) at d + off; offsets from the frame's top-left,")
        self.emit("   dtile added to `tile` and free to repeat (the tile dedupe). Children")
        self.emit("   the PREVIOUS frame used beyond n are parked. */")
        self.emit("void gbs_set_metasprite_list(uint8_t base, uint8_t pw, uint8_t tile,")
        self.emit("                             const uint8_t *d, uint16_t off, uint8_t n) {")
        self.emit("    uint8_t k, s = base, prev, prop;")
        self.emit("    if (base >= GBS_MAX_SPRITES || n > (uint8_t)(GBS_MAX_SPRITES - base)) return;")
        self.emit("    prop = gbs_meta_prop[base];")
        self.emit("    if (gbs_meta_w[base] == 0xFF) prev = gbs_meta_h[base];")
        self.emit("    else prev = (uint8_t)(gbs_meta_w[base] * gbs_meta_h[base]);")
        self.emit("    if (prev > (uint8_t)(GBS_MAX_SPRITES - base)) prev = (uint8_t)(GBS_MAX_SPRITES - base);")
        self.emit("    gbs_meta_w[base] = 0; gbs_meta_h[base] = 0;  /* assign children as singles */")
        if whole:
            self.emit(one_on)
        self.emit("    d += off;")
        self.emit("    for (k = 0; k < n; ++k) {")
        self.emit("        gbs_meta_dy[s] = *d++;")
        self.emit("        gbs_meta_dx[s] = *d++;")
        self.emit("        gbs_set_sprite_tile(s, (uint8_t)(tile + *d++));")
        self.emit("        gbs_set_sprite_prop(s, (uint8_t)(prop | *d++));")
        self.emit("        gbs_meta_w[s] = 1; gbs_meta_h[s] = 1;")
        self.emit("        ++s;")
        self.emit("    }")
        self.emit("    for (; s < (uint8_t)(base + prev); ++s) gbs_move_sprite(s, 200, %s);" % self._spr_park_y("220"))
        if self._fan_pal:
            self._emit_fan_pal_apply("n")
        if whole:
            self.emit(one_off)
        self.emit("    gbs_meta_w[base] = 0xFF;   /* list-shaped */")
        self.emit("    gbs_meta_h[base] = n;")
        self.emit("    gbs_meta_pw[base] = pw;")
        self.emit("}")
