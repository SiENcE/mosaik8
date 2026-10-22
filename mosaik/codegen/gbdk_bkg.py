"""Background emitters for the GBDK backend: the per-tile attribute map
and the SCANLINE PARALLAX engine (the LYC/STAT band chain).

A GbdkBackend concern mixin - methods run against the full
CodeGenerator instance (self.emit, self.caps, ...)."""


class GbdkBkgMixin:
    def _emit_gbdk_parallax(self):
        """`bkg.parallax*` -- the reference engine's scanline parallax bands.

        The background splits into up to three horizontal BANDS, each showing
        the map at its own horizontal scroll, so a distant skyline drifts while
        the playfield tracks the camera. There is no hardware for it: an
        LYC/STAT interrupt fires at each band's first scanline and writes that
        band's `SCX` (and `SCY`) - which is exactly what `core/parallax.c` does
        in the reference engine's engine.

        Four rules this copies, each of which is a defect if dropped:

        * **The write must land in H-blank.** Changing SCX while the LCD is
          reading the line tears it, so the ISR spins on `STATF_BUSY` first -
          the reference engine's asm does the same.
        * **The scroll values are DOUBLE-BUFFERED.** The game publishes into a
          shadow and the VBL handler commits it, so a band's SCX cannot change
          between the top and bottom of the frame it is being drawn in.
        * **Upper bands are pinned to `SCY = 0`**; only the LAST band gets the
          vertical scroll. The reference engine does this because an upper band shows the
          top of the map whatever the camera's y is.
        * **It shares `LYC_REG` with the box's sprite cut.** One register,
          several consumers: the STOPS a band contributes are merged with
          everyone else's by `gbs_lyc_rebuild` and walked by the one emitted
          LCD interrupt (see `gbdk_lyc.py`, which also holds the band state
          this reads and writes). A band used to TAKE the register over and
          the cut used to stand down for it, which cost a box in a parallax
          room its sprite cut and produced two whole-screen defects.

        Emitted only when called, and only on a console with the GB register
        model - everything else gets no-op stubs so a target-neutral room
        loader needs no `if platform` fork."""
        if self.caps.get('has_gb_regs') and not self._lyc_used():
            # Sole tenant: no arbitration to do, so keep the original
            # emitter whole and stay byte-identical to before W7d.
            self._emit_gbdk_parallax_solo()
            return
        if not self.caps.get('has_gb_regs'):
            self.emit("/* bkg.parallax*: scanline parallax needs the GB's LYC/STAT")
            self.emit("   interrupt and its SCX/SCY registers; an honest no-op here so")
            self.emit("   a target-neutral room loader can call it unconditionally. */")
            self.emit("void gbs_px_arm(uint8_t n) { (void)n; }")
            self.emit("void gbs_px_band(uint8_t i, uint8_t last) { (void)i; (void)last; }")
            self.emit("void gbs_px_scx(uint8_t i, uint8_t scx) { (void)i; (void)scx; }")
            self.emit("void gbs_px_scy_set(uint8_t scy) { (void)scy; }")
            return
        self.emit("/* ---- Scanline PARALLAX bands: the four verbs the game calls ----")
        self.emit("   The band STATE and the interrupt that reads it live with the LYC")
        self.emit("   owner above (gbs_lyc_*), because LYC_REG has more than one tenant")
        self.emit("   and exactly one of them may write it. Declare the bands, publish")
        self.emit("   their scroll into the shadow, then arm: arming is what rebuilds")
        self.emit("   the stop list, so every band must be declared before it. */")
        self.emit("void gbs_px_band(uint8_t i, uint8_t last) {")
        self.emit("    if (i < GBS_PX_BANDS) gbs_px_last[i] = last;")
        self.emit("}")
        self.emit("void gbs_px_scx(uint8_t i, uint8_t scx) {")
        self.emit("    if (i < GBS_PX_BANDS) gbs_px_shx[i] = scx;")
        self.emit("}")
        self.emit("/* The LAST scroll write of a frame: publishes the whole shadow. */")
        self.emit("void gbs_px_scy_set(uint8_t scy) { gbs_px_shy = scy; gbs_px_ready = 1; }")
        self.emit("void gbs_px_arm(uint8_t n) {")
        self.emit("    if (n > GBS_PX_BANDS) n = GBS_PX_BANDS;")
        self.emit("    gbs_px_n = n;")
        self.emit("    if (n) gbs_lyc_wire();")
        self.emit("    /* Disarming does NOT reach for STAT_REG: it contributes no stops")
        self.emit("       and the rebuild decides whether anything is left to fire. That")
        self.emit("       is what stopped a room change from killing an open box's cut. */")
        self.emit("    gbs_lyc_rebuild();")
        self.emit("}")

    def _emit_gbdk_parallax_solo(self):
        """`bkg.parallax*` when parallax is the ONLY tenant of `LYC_REG`.

        What shipped before the W7d merge (`parallax-spike` is the worked
        program), except that it writes STAT ONCE, at wire time
        (`_emit_solo_lyc_source`): arming and disarming used to set and clear
        `STATF_LYC`, and on a MONOCHROME Game Boy each write can raise a
        spurious LCD interrupt that runs the band chain from whatever line the
        beam is on. The count `gbs_px_n` is the whole disarm now; both
        handlers already stood down on it. With a box's sprite cut in the
        program too, `gbdk_lyc.py` owns the register instead.

        The reference engine's scanline parallax bands.

        The background splits into up to three horizontal BANDS, each showing
        the map at its own horizontal scroll, so a distant skyline drifts while
        the playfield tracks the camera. There is no hardware for it: an
        LYC/STAT interrupt fires at each band's first scanline, writes that
        band's `SCX` (and `SCY`) and re-arms `LYC` for the next one - which is
        exactly what `core/parallax.c` does in the reference engine's engine.

        Four rules this copies, each of which is a defect if dropped:

        * **The write must land in H-blank.** Changing SCX while the LCD is
          reading the line tears it, so the ISR spins on `STATF_BUSY` first -
          the reference engine's asm does the same.
        * **The scroll values are DOUBLE-BUFFERED.** The game publishes into a
          shadow and the VBL handler commits it, so a band's SCX cannot change
          between the top and bottom of the frame it is being drawn in.
        * **Upper bands are pinned to `SCY = 0`**; only the LAST band gets the
          vertical scroll. The reference engine does this because an upper band shows the
          top of the map whatever the camera's y is.
        * **It cannot share `LYC_REG` with `text.win_sprite_cut`.** One
          register, two consumers - which is why a program with both never
          reaches this emitter: `gbdk_lyc.py` merges their stops (`_lyc_used`).

        Emitted only when called, and only on a console with the GB register
        model - everything else gets no-op stubs so a target-neutral room
        loader needs no `if platform` fork."""
        n = self.PARALLAX_BANDS
        if not self.caps.get('has_gb_regs'):
            self.emit("/* bkg.parallax*: scanline parallax needs the GB's LYC/STAT")
            self.emit("   interrupt and its SCX/SCY registers; an honest no-op here so")
            self.emit("   a target-neutral room loader can call it unconditionally. */")
            self.emit("void gbs_px_arm(uint8_t n) { (void)n; }")
            self.emit("void gbs_px_band(uint8_t i, uint8_t last) { (void)i; (void)last; }")
            self.emit("void gbs_px_scx(uint8_t i, uint8_t scx) { (void)i; (void)scx; }")
            self.emit("void gbs_px_scy_set(uint8_t scy) { (void)scy; }")
            return
        self.emit("/* ---- Scanline PARALLAX bands (bkg.parallax*) ----")
        self.emit("   One LYC interrupt per band: write that band's SCX/SCY in H-blank,")
        self.emit("   re-arm LYC for the next, and restart the chain at the last band.")
        self.emit("   the reference engine's core/parallax.c model. */")
        self.emit("#define GBS_PX_BANDS %d" % n)
        self.emit("uint8_t gbs_px_n = 0;              /* bands armed; 0 = off */")
        self.emit("static uint8_t gbs_px_last[GBS_PX_BANDS];   /* last scanline of band i */")
        self.emit("static uint8_t gbs_px_shx[GBS_PX_BANDS];    /* SCX shadow (game writes) */")
        self.emit("static uint8_t gbs_px_livex[GBS_PX_BANDS];  /* SCX live (ISR reads) */")
        self.emit("static uint8_t gbs_px_shy = 0, gbs_px_livey = 0;")
        self.emit("static uint8_t gbs_px_i = 0;       /* which band the ISR is on */")
        self.emit("static uint8_t gbs_px_wired = 0;")
        self.emit("/* 1 once the game loop has written EVERY band's scroll for this frame")
        self.emit("   (parallax_scy is the last write, see engine.scrollpx.update). The")
        self.emit("   V-blank commit below copies the shadow only then: a V-blank landing")
        self.emit("   between two bands' writes used to commit band 0's NEW camera beside")
        self.emit("   band 1's OLD one - a torn frame whenever the loop ran long. */")
        self.emit("static uint8_t gbs_px_ready = 0;")
        self.emit("")
        self.emit("/* The chain RESTARTS ITSELF: each band stores its OWN last scanline")
        self.emit("   and the final band stores 0, so the last interrupt of a frame arms")
        self.emit("   LYC at line 0 - which is the interrupt that writes band 0. That is")
        self.emit("   the reference engine's encoding (parallax.h: next_y = (end << 3) - 1, 0 last)")
        self.emit("   and it is why arming only has to set LYC = 0. */")
        self.emit("void gbs_px_lcd_isr(void) NONBANKED {")
        self.emit("    uint8_t i, ny;")
        self.emit("    /* GBDK CHAINS LCD handlers and add_LCD cannot be undone, so this")
        self.emit("       ISR outlives the room that armed it. Once disarmed it must stand")
        self.emit("       down completely: text.win_sprite_cut owns LYC then, and replaying")
        self.emit("       the stale band chain would scroll a NON-parallax room with the")
        self.emit("       previous room's band SCX values. */")
        self.emit("    if (!gbs_px_n) return;")
        self.emit("    i = gbs_px_i;")
        self.emit("    ny = gbs_px_last[i];")
        self.emit("    LYC_REG = ny;")
        self.emit("    gbs_px_i = ny ? (uint8_t)(i + 1) : 0;")
        self.emit("    /* In H-BLANK, or the write tears the line it lands on. */")
        self.emit("    while (STAT_REG & STATF_BUSY) ;")
        self.emit("    SCX_REG = gbs_px_livex[i];")
        self.emit("    SCY_REG = ny ? 0 : gbs_px_livey;")
        self.emit("}")
        self.emit("void gbs_px_vbl_isr(void) NONBANKED {")
        self.emit("    uint8_t i;")
        self.emit("    /* Disarmed = LYC belongs to text.win_sprite_cut. Resetting LYC to 0")
        self.emit("       here dragged the box's WY-1 cut line down to line 0 every frame,")
        self.emit("       so the first dialogue after a parallax room hid EVERY sprite for")
        self.emit("       the whole visible frame (and woke the stale band chain above). */")
        self.emit("    if (!gbs_px_n) return;")
        self.emit("    /* Commit the shadow ONCE per frame, so a band's scroll cannot")
        self.emit("       change between the top and bottom of the frame it draws in -")
        self.emit("       and only a COMPLETE shadow (gbs_px_ready): a half-written one")
        self.emit("       keeps last frame's live values, which are at least consistent. */")
        self.emit("    if (gbs_px_ready) {")
        self.emit("        for (i = 0; i < GBS_PX_BANDS; ++i) gbs_px_livex[i] = gbs_px_shx[i];")
        self.emit("        gbs_px_livey = gbs_px_shy;")
        self.emit("        gbs_px_ready = 0;")
        self.emit("    }")
        self.emit("    /* Re-seed the walk at band 0. The chain heals itself anyway (it")
        self.emit("       always reaches the 0 terminator), but an interrupt missed under")
        self.emit("       a long CRITICAL section would otherwise show one wrong frame. */")
        self.emit("    gbs_px_i = 0;")
        self.emit("    LYC_REG = 0;")
        self.emit("    /* Band 0's own interrupt is armed at LY = 0, and its write has to")
        self.emit("       wait for H-blank - which is the END of line 0, one line too late.")
        self.emit("       Hardware hides that (LY reads 0 for most of line 153, so the")
        self.emit("       coincidence fires in V-blank), but nothing else does: on a")
        self.emit("       scanline emulator the top row of the screen kept the LAST band's")
        self.emit("       scroll and flickered along with the playfield. Commit band 0 here,")
        self.emit("       in V-blank, where no line is being drawn. Same rule as the ISR:")
        self.emit("       only the LAST band carries the vertical scroll. */")
        self.emit("    SCX_REG = gbs_px_livex[0];")
        self.emit("    SCY_REG = gbs_px_last[0] ? 0 : gbs_px_livey;")
        self.emit("}")
        self.emit("void gbs_px_band(uint8_t i, uint8_t last) {")
        self.emit("    if (i < GBS_PX_BANDS) gbs_px_last[i] = last;")
        self.emit("}")
        self.emit("void gbs_px_scx(uint8_t i, uint8_t scx) {")
        self.emit("    if (i < GBS_PX_BANDS) gbs_px_shx[i] = scx;")
        self.emit("}")
        self.emit("/* The LAST scroll write of a frame: publishes the whole shadow. */")
        self.emit("void gbs_px_scy_set(uint8_t scy) { gbs_px_shy = scy; gbs_px_ready = 1; }")
        self.emit("void gbs_px_arm(uint8_t n) {")
        self.emit("    if (n > GBS_PX_BANDS) n = GBS_PX_BANDS;")
        self.emit("    if (n == 0) {")
        self.emit("        /* The count IS the disarm: both handlers stand down on it. */")
        self.emit("        gbs_px_n = 0;")
        self.emit("        return;")
        self.emit("    }")
        self.emit("    if (!gbs_px_wired) {")
        self.emit("        CRITICAL {")
        self.emit("            add_LCD(gbs_px_lcd_isr);")
        self.emit("            add_VBL(gbs_px_vbl_isr);")
        self._emit_solo_lyc_source(" " * 12)
        self.emit("        }")
        # OR into the LIVE mask: another feature may already own an interrupt
        # (native.huge drives hUGEDriver off the TIMER), and assigning would
        # turn it off.
        self.emit("        set_interrupts(IE_REG | VBL_IFLAG | LCD_IFLAG);")
        self.emit("        gbs_px_wired = 1;")
        self.emit("    }")
        self.emit("    /* The chain's start BEFORE the count: the handler may fire in")
        self.emit("       between, and must find band 0 armed when it finds bands. */")
        self.emit("    gbs_px_i = 0;")
        self.emit("    LYC_REG = 0;")
        self.emit("    gbs_px_n = n;")
        self.emit("}")

    # ------------------------------------------------------------ set_view
    def _view_real(self):
        """`video.set_view` moves the picture only where the screen can be
        bigger than a room: the SMS (32x24 cells) and, for a room under 20x18,
        the Game Gear. The GB family's screen is the smallest a room can be."""
        return (getattr(self, 'view_used', False)
                and self.platform in ('sms', 'gamegear'))

    def _emit_gbdk_view_defines(self):
        """The LETTERBOX offset's per-TU half (`video.set_view`), emitted in
        EVERY translation unit because the sprite helpers are per TU.

        A room smaller than the screen is shown CENTRED: everything the
        program computes stays in room-view space, and two hardware commits
        add the offset - the scroll commit subtracts it (GBS_VIEW_SCX/SCY) and
        every on-screen sprite placement adds it, by redefining GBDK's
        DEVICE_SPRITE_PX_OFFSET_X/Y (the one term all ~19 placement sites
        already add). A PARK writes fixed coordinates without that term, so a
        parked sprite stays parked. Nothing here is emitted unless the program
        calls video.set_view, so every other program is byte-identical."""
        if getattr(self, 'view_blank_used', False):
            self.emit("/* video.view_blank: the letterbox bands blanked (main TU). */")
            self.emit("void gbs_view_blank(uint8_t on);")
        if not getattr(self, 'view_used', False):
            return
        self.emit("/* video.set_view: the letterbox offset (defined in the main TU). */")
        self.emit("void gbs_set_view(uint8_t x, uint8_t y);")
        if not self._view_real():
            return
        self.emit("extern uint8_t gbs_view_ox, gbs_view_oy;")
        self.emit("enum { GBS_DSPX = DEVICE_SPRITE_PX_OFFSET_X, "
                  "GBS_DSPY = DEVICE_SPRITE_PX_OFFSET_Y };")
        self.emit("#undef DEVICE_SPRITE_PX_OFFSET_X")
        self.emit("#undef DEVICE_SPRITE_PX_OFFSET_Y")
        self.emit("#define DEVICE_SPRITE_PX_OFFSET_X (GBS_DSPX + gbs_view_ox)")
        self.emit("#define DEVICE_SPRITE_PX_OFFSET_Y (GBS_DSPY + gbs_view_oy)")
        self.emit("/* The scroll commit in view space. The vertical register wraps at")
        self.emit("   224 (the 28-row name table), and a shake can leave the shadow")
        self.emit("   at 252..255, which means a few pixels ABOVE 0. */")
        self.emit("#define GBS_VIEW_SCX(x) ((uint8_t)((x) - gbs_view_ox))")
        self.emit("#define GBS_VIEW_SCY(y) ((uint8_t)((((y) >= 224u ? (int16_t)(y) - 256"
                  " : (int16_t)(y)) - (int16_t)gbs_view_oy + 224) % 224))")

    def _emit_gbdk_view_setter(self):
        """The main TU's half of `video.set_view`: the offset itself."""
        if not self._view_real():
            self.emit("/* video.set_view: a no-op where the screen is never bigger")
            self.emit("   than a room. */")
            self.emit("void gbs_set_view(uint8_t x, uint8_t y) { (void)x; (void)y; }")
            return
        self.emit("/* video.set_view: the letterbox offset in pixels (tile-aligned,")
        self.emit("   set per room by the generated rooms module). */")
        self.emit("uint8_t gbs_view_ox = 0, gbs_view_oy = 0;")
        self.emit("void gbs_set_view(uint8_t x, uint8_t y) {")
        self.emit("    gbs_view_ox = x;")
        self.emit("    gbs_view_oy = y;")
        self.emit("}")

    # ---------------------------------------------------------- view_blank
    VIEW_BLANK_LEAD = 2     # lines between the interrupt and its edge
    VIEW_BLANK_RETRY = 8    # lines to the next try after a blocked bottom edge

    def _emit_gbdk_view_blank(self):
        """`video.view_blank(on)`: the letterbox's top and bottom BANDS blanked.

        The side margins of a letterboxed picture are name-table columns the
        program never writes, so a clear keeps them black. The bands above
        and below are not: on a vertically scrolling screen they are rows of
        the ring that scroll THROUGH the view, and sprites cross them too. So
        on the Master System the display is switched off for those lines by
        the VDP's line interrupt (register 1 bit 6; a blanked line shows the
        border colour, sprite palette entry 0 by default, and no sprites).

        The rules it has to keep (the raster handler's, gbdk_raster.py):

        * GBDK's dispatcher takes longer than a scanline, so each interrupt
          is asked for LEAD lines early and the handler waits on the V
          counter; a write made while the counter reads v takes effect on
          line v + 1.
        * A VDP register write from an interrupt corrupts a VRAM transfer the
          main program is in the middle of (`_shadow_OAM_OFF`). A blocked
          write is not lost: every handler compares the state the line wants
          with the state last WRITTEN and fixes it when it can, so a blocked
          edge comes late (a ragged band for one frame), never a black frame.
        * Register 10 is reloaded at each underflow with the value it holds
          THEN, so a write steers the interval after next. Three interrupts a
          frame: the top edge, a relay that aims the next one at the bottom
          edge, and the bottom edge; v-blank puts register 10 back for the top
          of the next frame. The schedule tracks only what reached the
          register, so a blocked write cannot strand it.
        * The program's own display switch wins: the handlers write GBDK's
          shadow of register 1 with bit 6 kept or cleared, never the shadow.

        A no-op everywhere but the SMS with a view: the Game Gear's LCD shows
        only its 160x144 window, and no other console letterboxes this way."""
        if not (self.platform == 'sms' and self._view_real()):
            self.emit("/* video.view_blank: a no-op on this console. */")
            self.emit("void gbs_view_blank(uint8_t on) { (void)on; }")
            return
        lead = self.VIEW_BLANK_LEAD
        retry = self.VIEW_BLANK_RETRY
        e = self.emit
        e("/* video.view_blank: the letterbox's top and bottom bands blanked by")
        e("   the line interrupt (display off above and below the view). */")
        # BSS only (zeroed by crt0, so `on` starts 0): an initialised global
        # costs resident image. The arming call sets the rest.
        e("uint8_t gbs_vb_on, gbs_vb_wired;")
        e("uint8_t gbs_vb_lit;        /* the display bit last WRITTEN */")
        e("uint8_t gbs_vb_r10;        /* what register 10 holds */")
        e("uint8_t gbs_vb_nxt;        /* the next interrupt's line, 0xFF unknown */")
        e("uint8_t gbs_vb_a, gbs_vb_b;/* the top / bottom edge, LEAD lines early */")
        e("static void gbs_vb_r1(uint8_t lit) NONBANKED {")
        e("    VDP_CMD = lit ? shadow_VDP_R1 : (uint8_t)(shadow_VDP_R1 & (uint8_t)~R1_DISP_ON);")
        e("    VDP_CMD = VDP_R1;")
        e("    gbs_vb_lit = lit;")
        e("}")
        e("static void gbs_vb_r10w(uint8_t r) NONBANKED {")
        e("    VDP_CMD = r;")
        e("    VDP_CMD = VDP_R10;")
        e("    gbs_vb_r10 = r;")
        e("}")
        e("/* Start of v-blank: blank the top band of the next frame and aim")
        e("   register 10 at its top edge (lit from line oy to 192 - oy). */")
        e("void gbs_vb_vbl(void) NONBANKED {")
        e("    if (!gbs_vb_on) return;")
        e("    if (!_shadow_OAM_OFF) {")
        e("        if (gbs_view_oy <= %d) {             /* no band to blank */" % lead)
        e("            if (!gbs_vb_lit) gbs_vb_r1(1);")
        e("            if (gbs_vb_r10 != 0xFF) gbs_vb_r10w(0xFF);")
        e("        } else {")
        e("            gbs_vb_a = (uint8_t)(gbs_view_oy - %d);" % (1 + lead))
        e("            gbs_vb_b = (uint8_t)(%d - gbs_view_oy);" % (191 - lead))
        e("            if (gbs_vb_lit) gbs_vb_r1(0);")
        e("            if (gbs_vb_r10 != gbs_vb_a) gbs_vb_r10w(gbs_vb_a);")
        e("        }")
        e("    }")
        e("    gbs_vb_nxt = gbs_vb_r10;")
        e("}")
        e("/* The line interrupt (GBDK's dispatcher has saved every register).")
        e("   v = the line it was aimed at; the reload there used r10, so the")
        e("   next one comes on u = v + r10 + 1 (none this frame past 192). */")
        e("void gbs_vb_isr(void) NONBANKED NAKED {")
        e("__asm")
        e("        ld a, (#_gbs_vb_on)")
        e("        or a")
        e("        ret z")
        e("        ld a, (#_gbs_vb_r10)")
        e("        ld c, a                 ; c = r10, the value the reload used")
        e("        inc a")
        e("        ret z                   ; 0xFF: nothing aimed this frame")
        e("        ld a, (#_gbs_vb_nxt)")
        e("        cp #0xFF")
        e("        jr nz, 1$")
        e("        in a, (#0x7E)           ; armed mid-frame: resync on the V counter")
        e("1$:")
        e("        ld b, a                 ; b = v")
        e("        ld l, a")
        e("        ld h, #0")
        e("        ld e, c")
        e("        ld d, h")
        e("        add hl, de")
        e("        inc hl                  ; hl = u")
        e("        ld a, h")
        e("        or a")
        e("        jr nz, 2$")
        e("        ld a, l")
        e("        cp #193")
        e("        jr c, 3$")
        e("2$:")
        e("        ld a, #0xFF             ; none this frame")
        e("3$:")
        e("        ld (#_gbs_vb_nxt), a")
        e("        ld e, a                 ; e = u, or 0xFF")
        e("        ld a, (#__shadow_OAM_OFF)")
        e("        or a")
        e("        ret nz                  ; mid-transfer: a later interrupt retries")
        e("        ld d, #1")
        e("        ld a, (#_gbs_vb_a)")
        e("        cp b")
        e("        jr z, 6$                ; the top edge: light it")
        e("        dec d")
        e("        ld a, (#_gbs_vb_b)")
        e("        cp b")
        e("        jr z, 6$                ; the bottom edge: blank it")
        e("        ; a retry: what the next line wants (lit from a+LEAD to b+LEAD)")
        e("        in a, (#0x7E)")
        e("        ld h, a")
        e("        ld a, (#_gbs_vb_a)")
        e("        add a, #%d" % lead)
        e("        ld l, a")
        e("        ld a, h")
        e("        cp l")
        e("        jr c, 8$                ; above the top edge: blank")
        e("        ld a, (#_gbs_vb_b)")
        e("        add a, #%d" % lead)
        e("        ld l, a")
        e("        ld a, h")
        e("        cp l")
        e("        jr nc, 8$               ; past the bottom edge: blank")
        e("        inc d                   ; lit")
        e("        jr 8$")
        e("6$:")
        e("        add a, #%d               ; the edge line: write while V reads it" % lead)
        e("        ld l, a")
        e("7$:")
        e("        in a, (#0x7E)")
        e("        cp l")
        e("        jr c, 7$")
        e("        jr 9$")
        e("8$:")
        e("        ld a, (#_gbs_vb_lit)")
        e("        cp d")
        e("        jr z, 10$")
        e("9$:")
        e("        ld a, (#_shadow_VDP_R1) ; the program's own register 1...")
        e("        bit 0, d")
        e("        jr nz, 11$")
        e("        and #0xBF               ; ...with the display bit cleared")
        e("11$:")
        e("        out (#0xBF), a")
        e("        ld a, #0x81")
        e("        out (#0xBF), a")
        e("        ld a, d")
        e("        ld (#_gbs_vb_lit), a")
        e("10$:")
        e("        ; register 10 for the reload AT u: the bottom edge after it;")
        e("        ; at or past the bottom edge, a RETRY %d lines on while the" % retry)
        e("        ; band is still lit (a blocked edge), else the next frame's top")
        e("        ; edge, as past the last line")
        e("        ld a, e")
        e("        cp #0xFF")
        e("        jr nz, 12$")
        e("        ld a, (#_gbs_vb_a)")
        e("        jr 14$")
        e("12$:")
        e("        ld l, a")
        e("        ld a, (#_gbs_vb_b)")
        e("        cp l")
        e("        jr z, 13$")
        e("        jr c, 13$")
        e("        sub l")
        e("        dec a                   ; b - u - 1")
        e("        jr 14$")
        e("13$:")
        e("        ld a, (#_gbs_vb_lit)")
        e("        or a")
        e("        ld a, #%d                ; still lit: retry (ld keeps the flags)" % (retry - 1))
        e("        jr nz, 14$")
        e("        ld a, (#_gbs_vb_a)")
        e("14$:")
        e("        cp c")
        e("        ret z")
        e("        ld (#_gbs_vb_r10), a")
        e("        out (#0xBF), a")
        e("        ld a, #0x8A             ; register 10")
        e("        out (#0xBF), a")
        e("        ret")
        e("__endasm;")
        e("}")
        e("void gbs_view_blank(uint8_t on) {")
        e("    if (!on) {")
        e("        if (gbs_vb_on) {")
        e("            gbs_vb_on = 0;")
        e("            __WRITE_VDP_REG(VDP_R0, __READ_VDP_REG(VDP_R0) & 0xEFu);")
        e("            __WRITE_VDP_REG(VDP_R1, __READ_VDP_REG(VDP_R1));  /* lit as the program wants */")
        e("            gbs_vb_lit = 1;")
        e("        }")
        e("        return;")
        e("    }")
        e("    if (gbs_vb_on) return;")
        e("    if (!gbs_vb_wired) {")
        e("        add_LCD(gbs_vb_isr);")
        e("        add_VBL(gbs_vb_vbl);")
        e("        gbs_vb_wired = 1;")
        e("    }")
        e("    gbs_vb_lit = 1;")
        e("    gbs_vb_nxt = 0xFF;")
        e("    __WRITE_VDP_REG(VDP_R10, 0xFF);")
        e("    gbs_vb_r10 = 0xFF;                     /* v-blank aims it */")
        e("    gbs_vb_on = 1;")
        e("    __WRITE_VDP_REG(VDP_R0, __READ_VDP_REG(VDP_R0) | R0_IE1);")
        e("}")

    def _emit_gbdk_scroll_move(self):
        """`bkg.move` on the GB register model: a SHADOW committed in v-blank.

        A direct `move_bkg` from the game loop lands wherever the loop has
        reached - which is ~25-30k cycles after the present returned, well past
        the 4,560-cycle v-blank window - so the scroll register changed
        MID-FRAME and the picture sheared at that scanline: the top at the old
        camera, the rest at the new. Measured on the converted town room
        (per-scanline SCX out of PyBoy, the shake probe's instrument): 76 of
        300 walking frames torn at LY ~56-66, which reads as shimmer at every
        high-contrast vertical edge. The parallax bands solved the same
        problem the same way (shadow + v-blank commit); this is that rule
        applied to the PLAIN scroll path.

        The commit lives in `gbs_wait_vblank`, right after `vsync()` returns -
        the start of v-blank, exactly where the reference engine's own loop writes its
        scroll - rather than in a third chained VBL ISR (GBDK's add_VBL chain
        is small, and the reference-engine sample conversion already adds two).

        Three rules:
        * **While parallax bands are armed the COMMIT stands down** (they own
          SCX/SCY through the LYC chain), but the shadow still records the
          camera - it is what the next plain room's first present resumes
          from. This subsumes the old `gbs_px_move` arbitration wrapper.
        * **With the LCD OFF the write is applied immediately** as well: no
          frame is being drawn (nothing can tear) and no present will run
          before the room load turns the screen back on, so the first visible
          frame must not render through a stale register.
        * Emitted only when the program calls `bkg.move` at all, and only on
          `has_gb_regs` - SMS/GG/NES keep their direct lowering (different
          scroll hardware; the SMS latches its vertical scroll per frame in
          hardware and its horizontal artefacts are the edge-mask story)."""
        self.emit("/* ---- bkg.move: the v-blank-committed scroll shadow ----")
        self.emit("   The game loop reaches its scroll write mid-frame, so a direct")
        self.emit("   register write shears the picture at that scanline. Write a")
        self.emit("   shadow here; gbs_wait_vblank commits it at the start of v-blank")
        self.emit("   (the parallax bands' own double-buffer rule, applied to the")
        self.emit("   plain scroll path). */")
        if self.platform in ('sms', 'gamegear'):
            # SMS/GG: the same shadow, for a second reason. Their sprite table
            # reaches the hardware once per frame (sprite.vbl_hold releases the
            # copy at the present), so a scroll written the moment the streamer
            # computes it is a frame AHEAD of the sprites whenever a game frame
            # spans more than one display frame - every actor appears displaced
            # while the camera moves and snaps into place when it stops
            # (reported from play). Committing the scroll at the same v-blank
            # keeps the two in phase. There is no LCD-off arm: move_bkg here is
            # a plain VDP register write and a room load ends in a present.
            self.emit("uint8_t gbs_scr_shx = 0, gbs_scr_shy = 0;")
            self.emit("void gbs_scroll_move(uint8_t x, uint8_t y) {")
            self.emit("    gbs_scr_shx = x;")
            self.emit("    gbs_scr_shy = y;")
            self.emit("}")
            if self.bkg_scroll_used:
                self._emit_gbdk_scroll_delta(lcd_off_arm=False)
            return
        self.emit("uint8_t gbs_scr_shx = 0, gbs_scr_shy = 0;")
        self.emit("void gbs_scroll_move(uint8_t x, uint8_t y) {")
        self.emit("    gbs_scr_shx = x;")
        self.emit("    gbs_scr_shy = y;")
        self.emit("    /* LCD off = a room load: nothing is drawing (no tear is possible)")
        self.emit("       and no present runs before the screen comes back, so the first")
        self.emit("       visible frame must not render through the stale register. */")
        self.emit("    if (!(LCDC_REG & LCDCF_ON)) {")
        self.emit("        SCX_REG = x;")
        self.emit("        SCY_REG = y;")
        self.emit("    }")
        self.emit("}")
        if self.bkg_scroll_used:
            self._emit_gbdk_scroll_delta(lcd_off_arm=True)

    def _emit_gbdk_scroll_delta(self, lcd_off_arm):
        """`bkg.scroll` once `bkg.move` has put the scroll on a SHADOW.

        THE SHADOW HAS TO BE THE ONLY SOURCE OF TRUTH FOR THE REGISTER. GBDK's
        `scroll_bkg` adds straight to SCX/SCY, so a program that calls BOTH
        verbs has two writers for one register and the commit wins: every
        `bkg.scroll` delta is overwritten by the next `gbs_wait_vblank`, which
        re-asserts the shadow the last `bkg.move` left. Measured on a
        free-running register-driven sample whose loop is
        `update() -> video.wait_vblank()` and which calls `bkg.move(0, 0)` once
        at room start: SCY read 0 on EVERY frame of a 900-frame run (the
        original ROM sweeps 0..126), so the starfield never scrolled at all.

        Accumulating into the shadow instead keeps the two verbs on one clock
        and keeps `bkg.scroll` tear-free for free. Emitted under exactly the
        gate that swaps `bkg.move`, so a program that never calls `bkg.move`
        keeps GBDK's direct `scroll_bkg` and stays byte-identical."""
        self.emit("/* bkg.scroll on the deferred path: accumulate into the SHADOW.")
        self.emit("   A direct scroll_bkg would be a SECOND writer of the same")
        self.emit("   register, and the v-blank commit would wipe it every frame. */")
        self.emit("void gbs_scroll_bkg(int8_t dx, int8_t dy) {")
        self.emit("    gbs_scr_shx += (uint8_t)dx;")
        self.emit("    gbs_scr_shy += (uint8_t)dy;")
        if lcd_off_arm:
            self.emit("    if (!(LCDC_REG & LCDCF_ON)) {   /* as gbs_scroll_move */")
            self.emit("        SCX_REG = gbs_scr_shx;")
            self.emit("        SCY_REG = gbs_scr_shy;")
            self.emit("    }")
        self.emit("}")

    def _emit_gbdk_edge_mask(self):
        """`bkg.edge_mask(on)`: blank the leftmost 8 px column of the
        background. Real on the SEGA MASTER SYSTEM, an honest no-op elsewhere.

        The column streamer keeps a 32-column hardware ring and writes each
        newly revealed column into the slot the scrolled-off one vacated. That
        works because the visible area is NARROWER than the ring, so the column
        being rewritten is off screen while it is written -- 20 of 32 columns on
        the Game Boy and the Game Gear, which leaves 12 columns of margin.

        **The SMS is the one console where the display is exactly as wide as the
        ring** (256 px = 32 columns), so there is NO margin: with a non-zero fine
        scroll the wrap column is on screen at both edges, and the streamer's
        write lands in a column the beam is still showing. Measured on the
        auto-scrolling `wide-scroll-spike`: the leftmost 8 px changed on 119 of
        119 frame transitions while the column immediately inward changed NONE,
        and the same ROM on the Game Gear (160 px = 20 columns, the same ring)
        changed nothing at either edge.

        The VDP has a bit for exactly this -- R0 bit 5, which GBDK's SMS port
        spells `HIDE_LEFT_COLUMN` and documents as "so it is not garbaged when
        you use horizontal scroll". Masking that column costs 8 px of the 256 and
        is what every scrolling SMS title does.

        The Game Gear is deliberately EXCLUDED even though it shares the VDP: its
        viewport is a 160 px window into the centre of the same plane, so the
        masked column is not visible there and arming it would only be a way to
        get the two builds out of step. Emitted only when called, so a program
        that never streams a wide level is byte-identical."""
        if self.platform != 'sms':
            self.emit("/* bkg.edge_mask: only the SMS shows the whole 32-column ring,")
            self.emit("   so only it can see the column being streamed. A no-op here. */")
            self.emit("void gbs_bkg_edge_mask(uint8_t on) { (void)on; }")
            return
        self.emit("/* bkg.edge_mask: blank the leftmost 8 px (VDP R0 bit 5). The SMS")
        self.emit("   display is exactly as wide as the 32-column tilemap ring, so a")
        self.emit("   column-streamed level has no off-screen column to write into and")
        self.emit("   the wrap column flickers at the edge. Masking it is the hardware's")
        self.emit("   own remedy (GBDK: HIDE_LEFT_COLUMN). */")
        self.emit("void gbs_bkg_edge_mask(uint8_t on) {")
        self.emit("    if (on) { HIDE_LEFT_COLUMN; } else { SHOW_LEFT_COLUMN; }")
        self.emit("}")

    def _emit_gbdk_vbl_hold(self):
        """`sprite.vbl_hold(on)`: commit a frame's sprite writes as a UNIT.

        On the SMS and the Game Gear the shadow OAM is copied to the VDP's
        sprite attribute table by GBDK's VBlank handler, EVERY VBlank, and a
        metasprite is written to that shadow one COLUMN AT A TIME (the fan loop
        in `gbs_move_sprite`). So a VBlank landing between two columns copies a
        table that is half at the new position and half at the old, and the
        actor is drawn split by a vertical seam - reported from play on
        the SMS/GG sample conversion for a 9-column animated actor and for a tall NPC, both
        with no dialogue box anywhere near.

        The reference engine has the same hazard and solves it by DOUBLE-BUFFERING the
        shadow: `toggle_shadow_OAM` writes into the inactive page and
        `activate_shadow_OAM` flips the pointer once, at the end of the frame,
        so a VBlank can never see a half-written table. GBDK's z80 port gives
        the same guarantee with one byte: `_shadow_OAM_base` is the MSB the
        copier reads, and zeroing it stands the copy down
        (`DISABLE_VBL_TRANSFER` / `ENABLE_VBL_TRANSFER`). vm.core holds it
        across the whole game frame and releases it immediately before the
        present, so exactly one COMPLETE table is copied per frame.

        The cost of the hold is that a frame whose sprite pass straddles a
        VBlank shows the previous frame's (complete) sprites rather than a torn
        mixture, which is the trade the reference engine makes too.

        The GB family's OAM DMA copies the whole shadow in one transfer, so it
        cannot tear - but it copies it at EVERY v-blank, so a game frame that
        spans several display frames shows its sprite writes as they happen,
        and the scroll (committed by gbs_wait_vblank) on another frame. The
        one GB-family caller is vm.core's FRAME SYNC (`[build] frame_sync`),
        which holds both to the frame lock's own v-blank: the same byte
        (`_shadow_OAM_base`, DISABLE_OAM_DMA) stands the ISR's copy down, and
        `gbs_vbl_held` stands gbs_wait_vblank's scroll commit down with it.

        The RELEASE does not turn the ISR's copy back on: it marks the present
        PENDING (2), and the next gbs_wait_vblank copies the table itself
        right after vsync() returns, in the same v-blank as the scroll commit,
        then hands the copy back to the ISR. Re-enabling at the release left a
        race: a v-blank landing between the release and the wait's vsync()
        copied the sprites there while the wait committed the scroll at the
        NEXT one (measured: 1 frame of 1,200 on vm-tallshmup). Nothing else on
        the GB family calls it, so every other program is byte-identical.

        A no-op everywhere else, so a target-neutral renderer calls it
        unconditionally. Emitted only when called."""
        if self.caps.get('has_gb_regs'):
            self.emit("/* sprite.vbl_hold(on), the GB family: hold the OAM DMA every")
            self.emit("   v-blank does AND the scroll commit gbs_wait_vblank does, so a")
            self.emit("   game frame reaches the screen at the first v-blank WAITED for")
            self.emit("   after the release, its sprites and its scroll together (vm.core's")
            self.emit("   frame sync). 0 free, 1 held, 2 released: the next wait copies the")
            self.emit("   table itself, in its own v-blank, and gives the copy back to the")
            self.emit("   ISR (re-enabling it here raced a v-blank before that wait). */")
            self.emit("uint8_t gbs_vbl_held = 0;")
            self.emit("void gbs_spr_vbl_hold(uint8_t on) {")
            self.emit("    if (on) { DISABLE_OAM_DMA; gbs_vbl_held = 1; }")
            self.emit("    else if (gbs_vbl_held) gbs_vbl_held = 2;")
            self.emit("}")
            return
        if self.platform not in ('sms', 'gamegear'):
            self.emit("/* sprite.vbl_hold: only the SMS/GG copy their shadow OAM")
            self.emit("   byte-by-byte on a VBlank ISR and can tear mid-metasprite;")
            self.emit("   an honest no-op here. */")
            self.emit("void gbs_spr_vbl_hold(uint8_t on) { (void)on; }")
            return
        self.emit("/* sprite.vbl_hold(on): hold the shadow-OAM -> SAT copy while the")
        self.emit("   frame writes sprites, release it when the frame is complete, so")
        self.emit("   a VBlank cannot copy a metasprite that is half-moved (the split")
        self.emit("   wide actor). The reference engine's toggle/activate_shadow_OAM,")
        self.emit("   spelled with the one byte GBDK's z80 port exposes. */")
        self.emit("void gbs_spr_vbl_hold(uint8_t on) {")
        self.emit("    if (on) { DISABLE_VBL_TRANSFER; } else { ENABLE_VBL_TRANSFER; }")
        self.emit("}")

    def _emit_gbdk_spr_cut(self, extern=False):
        """`sprite.cut_y(y)`: park every sprite OBJECT at or below screen row
        `y` (255 = off) -- the SMS/GG stand-in for `text.win_sprite_cut`.

        Those two consoles have no window layer, so a dialogue box is plotted
        into the scene's OWN background map -- and on this VDP sprites are
        unconditionally in front of the background. An actor standing where the
        box will be therefore draws straight through the text: reported from
        play on the SMS/GG sample conversion in the town room, where an NPC and a
        sign sit on top of a line of dialogue. The GB family
        never sees it because its box is on the WINDOW layer and
        `text.win_sprite_cut` hides sprites from the window's first scanline
        down; that verb is an honest no-op here (there is no window to cut).

        There is no per-scanline sprite disable on this hardware, so the cut is
        applied per OBJECT at the one place every sprite is positioned: rather
        than edit the twelve `move_sprite` call sites inside the metasprite fan
        (each of which is on the per-frame path and easy to get subtly wrong),
        the real `move_sprite` is wrapped once and the name is redirected for
        everything emitted BELOW this point. The wrapper's own body is above
        the `#define`, so it still calls the library function.

        Granularity is one 8x8/8x16 object: a sprite whose top is above the cut
        keeps drawing and may overhang the box's top edge by up to its own
        height, where the reference engine's scanline cut would clip it exactly. That is
        the known price of having no raster cut, and it is invisible for the
        case that produced the report (actors standing well inside the band).

        Emitted only when `sprite.cut_y` is called, and only on SMS/GG, so
        every other console is byte-identical."""
        if not self.spr_cut_used:
            return
        if self.platform not in ('sms', 'gamegear'):
            if extern:
                return
            self.emit("/* sprite.cut_y: the GB family cuts sprites off its dialogue")
            self.emit("   box with text.win_sprite_cut on the WINDOW layer; nothing")
            self.emit("   to do here. */")
            self.emit("void gbs_spr_cut_set(uint8_t y) { (void)y; }")
            return
        self.emit("/* ---- sprite.cut_y: no sprites over a background-plotted box ----")
        self.emit("   These consoles draw the dialogue box INTO the scene map and their")
        self.emit("   sprites are always in front of it, so an actor in the box's band")
        self.emit("   covers the text. Park every object at/below the cut row instead.")
        self.emit("   The wrapper is defined first and the name redirected after, so")
        self.emit("   every move_sprite emitted below goes through the cut while this")
        self.emit("   body still reaches the real one. */")
        self.emit("/* ONE threshold for every TU's wrapper: the main TU defines it, a")
        self.emit("   bank TU declares it (the meta-table rule). A per-TU `static` copy")
        self.emit("   would leave the banks reading a value the setter never wrote. */")
        if extern:
            self.emit("extern uint8_t gbs_spr_cut_y;")
        else:
            self.emit("uint8_t gbs_spr_cut_y = 255;   /* 255 = no cut */")
        self.emit("static void gbs_spr_put(uint8_t nb, uint8_t x, uint8_t y) {")
        self.emit("    if (y >= gbs_spr_cut_y) { move_sprite(nb, 0, GBS_SPR_PARK_Y); return; }")
        self.emit("    move_sprite(nb, x, y);")
        self.emit("}")
        self.emit("/* The cut arrives in SCREEN rows, the same space sprite.move takes;")
        self.emit("   sprites reach the SAT with the console offset already added, so")
        self.emit("   the threshold takes it too or the two are a few px apart. */")
        if extern:
            self.emit("void gbs_spr_cut_set(uint8_t y);")
        else:
            self.emit("void gbs_spr_cut_set(uint8_t y) {")
            self.emit("    gbs_spr_cut_y = (y == 255) ? 255")
            self.emit("                  : (uint8_t)(y + DEVICE_SPRITE_PX_OFFSET_Y);")
            self.emit("}")
        self.emit("#define move_sprite gbs_spr_put")

    def _emit_gbdk_bkg_attrs(self):
        """bkg.set_attrs(x, y, w, h, data): the ATTRIBUTE mirror of
        bkg.set_tiles -- one background palette-slot byte per map cell.

        This is the DATA half of the per-tile palette model; bkg.set_palette
        fills a rectangle with ONE slot, this uploads a whole rectangle of
        them, so a room painter can colour a map row in a single call instead
        of one call per cell. Emitted only when called.

        Per console: the CGB class writes the attribute map in VRAM bank 1
        (GBDK's set_bkg_attributes); the NES has 16x16 px attribute
        granularity, so a row's cells are downsampled two at a time and an odd
        row folds into the even one above it (the hardware has no finer
        resolution). Consoles without per-tile palette hardware are an honest
        no-op -- SMS/GG, the Lynx and the PCE reach 16 colours through the 4bpp
        background tier instead, and a 4-grey console has one palette. That
        no-op is what lets a target-neutral room painter call this with no
        `if platform` fork."""
        if not self.caps['has_tile_palettes']:
            self.emit("/* bkg.set_attrs: no per-tile background palette hardware here")
            self.emit("   (a single background palette, or the 4bpp 16-colour tier). */")
            self.emit("void gbs_bkg_attrs(uint8_t x, uint8_t y, uint8_t w, uint8_t h,")
            self.emit("                   const uint8_t *data) {")
            self.emit("    (void)x; (void)y; (void)w; (void)h; (void)data;")
            self.emit("}")
        elif self.platform == 'nes':
            self.emit("/* bkg.set_attrs: NES attributes are 16x16 px (2x2 tiles), so each")
            self.emit("   pair of cells collapses to one entry and an ODD map row folds")
            self.emit("   into the even row above it. 0x55 spreads the palette over all")
            self.emit("   four quadrants of the PPU attribute byte. */")
            self.emit("void gbs_bkg_attrs(uint8_t x, uint8_t y, uint8_t w, uint8_t h,")
            self.emit("                   const uint8_t *data) {")
            self.emit("    static uint8_t buf[16];")
            self.emit("    uint8_t i, j, n = (uint8_t)((w + 1) >> 1);")
            self.emit("    if (n > 16) n = 16;")
            self.emit("    for (j = 0; j < h; ++j) {")
            self.emit("        if ((uint8_t)((y + j) & 1) != 0) continue;")
            self.emit("        for (i = 0; i < n; ++i)")
            self.emit("            buf[i] = (uint8_t)((data[(uint16_t)j * w + (uint16_t)(i << 1)] & 3) * 0x55);")
            self.emit("        set_bkg_attributes_nes16x16((uint8_t)(x >> 1),")
            self.emit("                                    (uint8_t)((y + j) >> 1), n, 1, buf);")
            self.emit("    }")
            self.emit("}")
        # The CGB class needs no helper at all: GBDK's own set_bkg_attributes
        # already has this exact signature (VBK bank 1 + set_bkg_tiles), so
        # bkg.set_attrs lowers straight onto it (see _build_stdlib) and the
        # wrapper would be pure resident overhead.
