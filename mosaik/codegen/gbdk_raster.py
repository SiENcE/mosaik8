"""The PER-SCANLINE SCROLL TABLE (`bkg.raster*`) and the Game Boy Color
double-speed switch (`system.cpu_fast`) for the GBDK backend.

A GbdkBackend concern mixin - methods run against the full CodeGenerator
instance (self.emit, self.caps, ...).

WHY IT EXISTS. `bkg.parallax*` gives a room up to three scroll BANDS. A
pseudo-3D road (OutRun, Sonic Drift, Space War 3D), a water ripple or a
"mode 7" floor needs a different scroll on EVERY line: a table with one entry
per screen line that an interrupt plays back as the beam goes down.

    bkg.raster(on, first)        arm (on != 0) / disarm the table. `first` is a
                                 promise: every line ABOVE it uses line 0's
                                 entry, so the console only has to work from
                                 `first` down (0 = the whole screen).
    bkg.raster_set(line, x, y)   that screen line shows the map scrolled to
                                 (x, y) - the same meaning as bkg.move(x, y).
    bkg.raster_copy(line, n, t)  n lines starting at `line`, from an array of
                                 (x, y) pairs - one copy instead of n calls.
    bkg.raster_show()            publish what was written; it goes live at the
                                 next v-blank, all lines at once.
    bkg.raster_get(line)         read back the x a line was given this frame.

THE FAST FILLS. A road rewrites every line of the table every frame, and a
loop of raster_set calls in compiled C costs about a fifth of a Game Boy frame
per 20 lines. Two fills do the two jobs a road needs in native code, walking
UP the screen from `line` (a road is built from its nearest line):

    bkg.raster_curve_start(x, dx)   8.8 fixed-point start value and slope
    bkg.raster_curve(line, n, ddx)  for n lines: x scroll = high byte of x;
                                    x += dx; dx += ddx. The state carries
                                    over, so a second call continues the same
                                    curve with another ddx (the next bend).
    bkg.raster_stripes(line, n, depth, phase, y)
                                    for n lines: y scroll = `y` when bit 7 of
                                    depth[k] + phase is set, else 0 - picks
                                    between two pictures drawn `y` lines apart
                                    (light / dark road stripes). A no-op on
                                    SMS / Game Gear, which have no per-line y.

THE TABLE IS DOUBLE-BUFFERED. Writes go to a back buffer and `raster_show`
swaps it in during v-blank, so a frame is never drawn from a half-written
table. The price of a swap (rather than a copy, which does not fit in v-blank)
is that the new back buffer holds the frame BEFORE last: rewrite every line you
animate each frame. While the table is disarmed a write lands in both buffers,
which is how static lines (a sky) are set up once.

PER CONSOLE:

  * GB family - a raw STAT interrupt, installed straight on the vector
    (`ISR_VECTOR`): GBDK's chained dispatcher costs more than the handler. Each
    H-blank it writes SCX and SCY for the NEXT line. With `first` > 1 the
    frame starts on the LYC source and the handler switches itself to the
    H-blank source, so the lines above `first` cost nothing. It is the ONLY
    user of the STAT vector: combining it with `bkg.parallax*` or the text
    window cuts is a compile error.
  * SMS / Game Gear - GBDK's own interrupt dispatcher is longer than a
    scanline (six pushes and six pops), so one line interrupt fires just above
    `first` and the handler STAYS in it to the bottom of the picture, writing
    the horizontal scroll register as the V counter ticks. The vertical scroll
    is latched once per frame by the VDP itself: `y` is ignored here.
    A VDP register write lands in the middle of whatever the main program was
    sending to the VDP, so the handler stands down for a frame when GBDK is
    inside a VRAM transfer (`_shadow_OAM_OFF`).
  * everything else - honest no-op stubs, so one source builds everywhere.
"""


class GbdkRasterMixin:
    # Screen lines the table covers, and the VDP line of screen line 0.
    RASTER_GEOMETRY = {
        'sms': (192, 0),
        'gamegear': (144, 24),
    }

    def _raster_kind(self):
        if self.caps.get('has_gb_regs'):
            return 'gb'
        if self.platform in ('sms', 'gamegear'):
            return 'sms'
        return None

    def _emit_gbdk_raster_protos(self):
        self.emit("void gbs_rs_arm(uint8_t on, uint8_t first);")
        self.emit("void gbs_rs_set(uint8_t line, uint8_t x, uint8_t y);")
        self.emit("void gbs_rs_copy(uint8_t line, uint8_t n, const uint8_t *t);")
        self.emit("void gbs_rs_show(void);")
        self.emit("uint8_t gbs_rs_get(uint8_t line);")
        self.emit("void gbs_rs_curve_start(uint16_t x, uint16_t dx);")
        self.emit("void gbs_rs_curve(uint8_t line, uint8_t n, uint16_t ddx);")
        self.emit("void gbs_rs_stripes(uint8_t line, uint8_t n, const uint8_t *depth,")
        self.emit("                    uint8_t phase, uint8_t y);")
        if self._raster_kind():
            self.emit("extern uint8_t gbs_rs_on;")

    def _emit_gbdk_raster(self):
        kind = self._raster_kind()
        if kind == 'gb':
            self._emit_gbdk_raster_gb()
        elif kind == 'sms':
            self._emit_gbdk_raster_sms()
        else:
            self.emit("/* bkg.raster*: no per-line scroll register on this console;")
            self.emit("   honest no-ops so a target-neutral program still builds. */")
            self.emit("void gbs_rs_arm(uint8_t on, uint8_t first) { (void)on; (void)first; }")
            self.emit("void gbs_rs_set(uint8_t line, uint8_t x, uint8_t y) {")
            self.emit("    (void)line; (void)x; (void)y;")
            self.emit("}")
            self.emit("void gbs_rs_copy(uint8_t line, uint8_t n, const uint8_t *t) {")
            self.emit("    (void)line; (void)n; (void)t;")
            self.emit("}")
            self.emit("void gbs_rs_show(void) { }")
            self._emit_raster_fill_stubs()

    def _emit_raster_fill_stubs(self):
        self.emit("uint8_t gbs_rs_get(uint8_t line) { (void)line; return 0; }")
        self.emit("void gbs_rs_curve_start(uint16_t x, uint16_t dx) { (void)x; (void)dx; }")
        self.emit("void gbs_rs_curve(uint8_t line, uint8_t n, uint16_t ddx) {")
        self.emit("    (void)line; (void)n; (void)ddx;")
        self.emit("}")
        self.emit("void gbs_rs_stripes(uint8_t line, uint8_t n, const uint8_t *depth,")
        self.emit("                    uint8_t phase, uint8_t y) {")
        self.emit("    (void)line; (void)n; (void)depth; (void)phase; (void)y;")
        self.emit("}")

    def _emit_raster_fill_state(self):
        """What the native fills read: C stores the arguments here and calls a
        parameterless NAKED routine, so the assembly never depends on which
        registers this sdcc passes arguments in."""
        emit = self.emit
        emit("/* ---- the fast fills (raster_curve / raster_stripes) ---- */")
        emit("uint16_t gbs_rs_cx, gbs_rs_cdx, gbs_rs_cdd;   /* x, dx, ddx (8.8) */")
        emit("uint8_t *gbs_rs_cp;                 /* the entry being written */")
        emit("uint8_t gbs_rs_cn;                  /* lines left */")
        emit("void gbs_rs_curve_start(uint16_t x, uint16_t dx) {")
        emit("    gbs_rs_cx = x;")
        emit("    gbs_rs_cdx = dx;")
        emit("}")

    # ------------------------------------------------------------------ GB
    def _emit_gbdk_raster_gb(self):
        emit = self.emit
        emit("/* ---- The per-scanline scroll table (bkg.raster*) ----")
        emit("   One (SCX, SCY) pair per screen line, played back by a raw STAT")
        emit("   interrupt: each H-blank writes the NEXT line's scroll. Two")
        emit("   buffers; raster_show swaps them in v-blank. */")
        emit("#define GBS_RS_LINES 144")
        emit("static uint8_t gbs_rs_buf[2][(GBS_RS_LINES + 2) * 2];")
        emit("uint8_t gbs_rs_on = 0;")
        emit("static uint8_t gbs_rs_first = 0;")
        emit("static uint8_t gbs_rs_back = 0;      /* the buffer the game writes */")
        emit("static uint8_t gbs_rs_ready = 0;     /* raster_show: swap at v-blank */")
        emit("static uint8_t gbs_rs_wired = 0;")
        emit("uint8_t *gbs_rs_ptr;                /* the handler's read cursor */")
        emit("")
        emit("/* NAKED and on the vector itself: 53 machine cycles a line, of which")
        emit("   the two scroll writes are done 31 in - inside H-blank plus the OAM")
        emit("   scan that follows it, i.e. before the next line starts drawing.")
        emit("   The STAT write turns the LYC source that started the frame into")
        emit("   the H-blank source; it is harmless on every later line (the")
        emit("   source is already selected and the interrupt line already high,")
        emit("   so even a monochrome Game Boy's STAT-write glitch raises nothing). */")
        emit("void gbs_rs_isr(void) NONBANKED NAKED {")
        emit("__asm")
        emit("        push af")
        emit("        push hl")
        emit("        ld hl, #_gbs_rs_ptr")
        emit("        ld a, (hl+)")
        emit("        ld h, (hl)")
        emit("        ld l, a")
        emit("        ld a, (hl+)")
        emit("        ldh (#_SCX_REG), a")
        emit("        ld a, (hl+)")
        emit("        ldh (#_SCY_REG), a")
        emit("        ld a, l")
        emit("        ld (#_gbs_rs_ptr), a")
        emit("        ld a, h")
        emit("        ld (#_gbs_rs_ptr + 1), a")
        emit("        ld a, #0x08             ; STATF_MODE00: every H-blank from here")
        emit("        ldh (#_STAT_REG), a")
        emit("        pop hl")
        emit("        pop af")
        emit("        reti")
        emit("__endasm;")
        emit("}")
        emit("ISR_VECTOR(VECTOR_STAT, gbs_rs_isr)")
        emit("")
        emit("void gbs_rs_vbl(void) NONBANKED {")
        emit("    uint8_t *p, *q;")
        emit("    if (!gbs_rs_on) return;")
        emit("    if (gbs_rs_ready) {")
        emit("        gbs_rs_back ^= 1;")
        emit("        gbs_rs_ready = 0;")
        emit("    }")
        emit("    p = gbs_rs_buf[gbs_rs_back ^ 1];")
        emit("    /* Line 0 has no H-blank before it: its scroll is set here. */")
        emit("    SCX_REG = p[0];")
        emit("    SCY_REG = p[1];")
        emit("    /* A STAT write in v-blank can raise a spurious interrupt on a")
        emit("       monochrome Game Boy; mask the source over the write and drop")
        emit("       whatever it latched. */")
        emit("    IE_REG &= 0xFDu;            /* ~LCD_IFLAG */")
        emit("    if (gbs_rs_first < 2) {")
        emit("        gbs_rs_ptr = p + 2;")
        emit("        STAT_REG = 0x08;        /* H-blank source from line 0 */")
        emit("    } else {")
        emit("        /* Start at the line ABOVE `first` on the LYC source. That")
        emit("           first call lands while its own line is already being")
        emit("           set up, so it is given line 0's entry: no visible")
        emit("           change, and the H-blank that follows it writes `first`. */")
        emit("        q = p + ((uint16_t)(gbs_rs_first - 1) << 1);")
        emit("        q[0] = p[0];")
        emit("        q[1] = p[1];")
        emit("        gbs_rs_ptr = q;")
        emit("        LYC_REG = gbs_rs_first - 1;")
        emit("        STAT_REG = 0x40;        /* STATF_LYC */")
        emit("    }")
        emit("    IF_REG &= 0xFDu;")
        emit("    IE_REG |= LCD_IFLAG;")
        emit("}")
        emit("void gbs_rs_set(uint8_t line, uint8_t x, uint8_t y) {")
        emit("    uint8_t *p;")
        emit("    if (line >= GBS_RS_LINES) return;")
        emit("    p = gbs_rs_buf[gbs_rs_back] + ((uint16_t)line << 1);")
        emit("    p[0] = x;")
        emit("    p[1] = y;")
        emit("    if (!gbs_rs_on) {           /* disarmed: both buffers */")
        emit("        p = gbs_rs_buf[gbs_rs_back ^ 1] + ((uint16_t)line << 1);")
        emit("        p[0] = x;")
        emit("        p[1] = y;")
        emit("    }")
        emit("}")
        emit("void gbs_rs_copy(uint8_t line, uint8_t n, const uint8_t *t) {")
        emit("    if (line >= GBS_RS_LINES) return;")
        emit("    if (n > GBS_RS_LINES - line) n = GBS_RS_LINES - line;")
        emit("    memcpy(gbs_rs_buf[gbs_rs_back] + ((uint16_t)line << 1), t,")
        emit("           (uint16_t)n << 1);")
        emit("    if (!gbs_rs_on)")
        emit("        memcpy(gbs_rs_buf[gbs_rs_back ^ 1] + ((uint16_t)line << 1), t,")
        emit("               (uint16_t)n << 1);")
        emit("}")
        emit("void gbs_rs_show(void) { gbs_rs_ready = 1; }")
        emit("uint8_t gbs_rs_get(uint8_t line) {")
        emit("    if (line >= GBS_RS_LINES) return 0;")
        emit("    return gbs_rs_buf[gbs_rs_back][(uint16_t)line << 1];")
        emit("}")
        self._emit_raster_fill_state()
        emit("/* 41 machine cycles a line (the C loop it replaces took ~210). */")
        emit("static void gbs_rs_curve_asm(void) NAKED {")
        emit("__asm")
        emit("        push bc")
        emit("        ld hl, #_gbs_rs_cx")
        emit("        ld a, (hl+)")
        emit("        ld e, a")
        emit("        ld d, (hl)              ; de = x")
        emit("        ld hl, #_gbs_rs_cdx")
        emit("        ld a, (hl+)")
        emit("        ld c, a")
        emit("        ld b, (hl)              ; bc = dx")
        emit("        ld hl, #_gbs_rs_cp")
        emit("        ld a, (hl+)")
        emit("        ld h, (hl)")
        emit("        ld l, a                 ; hl = the entry")
        emit("1$:")
        emit("        ld (hl), d              ; x scroll = high byte")
        emit("        dec hl")
        emit("        dec hl                  ; the line above")
        emit("        ld a, e")
        emit("        add a, c")
        emit("        ld e, a")
        emit("        ld a, d")
        emit("        adc a, b")
        emit("        ld d, a                 ; x += dx")
        emit("        push hl")
        emit("        ld hl, #_gbs_rs_cdd")
        emit("        ld a, c")
        emit("        add a, (hl)")
        emit("        ld c, a")
        emit("        inc hl")
        emit("        ld a, b")
        emit("        adc a, (hl)")
        emit("        ld b, a                 ; dx += ddx")
        emit("        ld hl, #_gbs_rs_cn")
        emit("        dec (hl)")
        emit("        pop hl                  ; (flags survive the pop)")
        emit("        jr nz, 1$")
        emit("        ld hl, #_gbs_rs_cx")
        emit("        ld a, e")
        emit("        ld (hl+), a")
        emit("        ld (hl), d")
        emit("        ld hl, #_gbs_rs_cdx")
        emit("        ld a, c")
        emit("        ld (hl+), a")
        emit("        ld (hl), b")
        emit("        pop bc")
        emit("        ret")
        emit("__endasm;")
        emit("}")
        emit("void gbs_rs_curve(uint8_t line, uint8_t n, uint16_t ddx) {")
        emit("    if (line >= GBS_RS_LINES || !n) return;")
        emit("    if (n > line + 1) n = line + 1;")
        emit("    gbs_rs_cp = gbs_rs_buf[gbs_rs_back] + ((uint16_t)line << 1);")
        emit("    gbs_rs_cn = n;")
        emit("    gbs_rs_cdd = ddx;")
        emit("    gbs_rs_curve_asm();")
        emit("}")
        emit("const uint8_t *gbs_rs_sd;           /* the depth table */")
        emit("uint8_t gbs_rs_sph, gbs_rs_sy;")
        emit("static void gbs_rs_stripes_asm(void) NAKED {")
        emit("__asm")
        emit("        push bc")
        emit("        ld hl, #_gbs_rs_sd")
        emit("        ld a, (hl+)")
        emit("        ld d, (hl)")
        emit("        ld e, a                 ; de = depth")
        emit("        ld a, (#_gbs_rs_cn)")
        emit("        ld b, a")
        emit("        ld a, (#_gbs_rs_sph)")
        emit("        ld c, a")
        emit("        ld hl, #_gbs_rs_cp")
        emit("        ld a, (hl+)")
        emit("        ld h, (hl)")
        emit("        ld l, a")
        emit("1$:")
        emit("        ld a, (de)")
        emit("        inc de")
        emit("        add a, c")
        emit("        rla                     ; bit 7 -> carry")
        emit("        jr c, 2$")
        emit("        xor a")
        emit("        jr 3$")
        emit("2$:")
        emit("        ld a, (#_gbs_rs_sy)")
        emit("3$:")
        emit("        ld (hl), a")
        emit("        dec hl")
        emit("        dec hl")
        emit("        dec b")
        emit("        jr nz, 1$")
        emit("        pop bc")
        emit("        ret")
        emit("__endasm;")
        emit("}")
        emit("void gbs_rs_stripes(uint8_t line, uint8_t n, const uint8_t *depth,")
        emit("                    uint8_t phase, uint8_t y) {")
        emit("    if (line >= GBS_RS_LINES || !n) return;")
        emit("    if (n > line + 1) n = line + 1;")
        emit("    gbs_rs_cp = gbs_rs_buf[gbs_rs_back] + ((uint16_t)line << 1) + 1;")
        emit("    gbs_rs_cn = n;")
        emit("    gbs_rs_sd = depth;")
        emit("    gbs_rs_sph = phase;")
        emit("    gbs_rs_sy = y;")
        emit("    gbs_rs_stripes_asm();")
        emit("}")
        emit("void gbs_rs_arm(uint8_t on, uint8_t first) {")
        emit("    if (!on) {")
        emit("        if (gbs_rs_on) {")
        emit("            CRITICAL {")
        emit("                gbs_rs_on = 0;")
        emit("                STAT_REG = 0;   /* no source: the handler goes quiet */")
        emit("            }")
        emit("        }")
        emit("        return;")
        emit("    }")
        emit("    if (first >= GBS_RS_LINES) first = GBS_RS_LINES - 1;")
        emit("    gbs_rs_first = first;")
        emit("    if (!gbs_rs_wired) {")
        emit("        CRITICAL {")
        emit("            add_VBL(gbs_rs_vbl);")
        emit("        }")
        emit("        gbs_rs_wired = 1;")
        emit("    }")
        emit("    /* The v-blank handler selects the STAT source and seeds the")
        emit("       cursor, so nothing fires before the first whole frame. */")
        emit("    gbs_rs_on = 1;")
        emit("    set_interrupts(IE_REG | VBL_IFLAG | LCD_IFLAG);")
        emit("}")

    # ------------------------------------------------------------- SMS / GG
    def _emit_gbdk_raster_sms(self):
        emit = self.emit
        lines, top = self.RASTER_GEOMETRY[self.platform]
        emit("/* ---- The per-scanline scroll table (bkg.raster*) ----")
        emit("   One horizontal scroll per screen line. GBDK's interrupt")
        emit("   dispatcher alone is longer than a scanline, so ONE line")
        emit("   interrupt fires just above `first` and the handler stays in it")
        emit("   to the bottom of the picture, writing VDP register 8 as the V")
        emit("   counter ticks. The VDP latches the vertical scroll once per")
        emit("   frame: `y` is ignored on this console. */")
        emit("#define GBS_RS_LINES %d" % lines)
        emit("#define GBS_RS_TOP %d       /* VDP line of screen line 0 */" % top)
        emit("static uint8_t gbs_rs_buf[2][GBS_RS_LINES + 2];")
        emit("uint8_t gbs_rs_on = 0;")
        emit("static uint8_t gbs_rs_back = 0;")
        emit("static uint8_t gbs_rs_ready = 0;")
        emit("static uint8_t gbs_rs_wired = 0;")
        emit("static uint8_t gbs_rs_first = 0;")
        emit("uint8_t *gbs_rs_live;")
        if self.bkg_move_used:
            emit("extern uint8_t gbs_scr_shx, gbs_scr_shy;   /* bkg.move's shadow */")
        emit("")
        emit("/* After the last line: take a published table, then put line 0's")
        emit("   scroll back for the top of the next frame. */")
        emit("void gbs_rs_tail(void) NONBANKED {")
        emit("    while (VCOUNTER == (uint8_t)(GBS_RS_TOP + GBS_RS_LINES - 2)) ;")
        emit("    if (gbs_rs_ready) {")
        emit("        gbs_rs_live = gbs_rs_buf[gbs_rs_back];")
        emit("        gbs_rs_back ^= 1;")
        emit("        gbs_rs_ready = 0;")
        emit("    }")
        emit("    /* The interrupt is asked for a line early (its latency varies by")
        emit("       a line), so the handler may write the entry ABOVE `first`:")
        emit("       keep line 0's scroll in it, which is what that line shows. */")
        emit("    if (gbs_rs_first) gbs_rs_live[gbs_rs_first - 1] = gbs_rs_live[0];")
        emit("    VDP_CMD = gbs_rs_live[0];")
        emit("    VDP_CMD = VDP_RSCX;")
        emit("}")
        emit("/* Called by GBDK's dispatcher with every register already saved.")
        emit("   A write made while the V counter reads `v` is latched for line")
        emit("   v + 1, so the cursor starts two entries past the line it woke on. */")
        emit("void gbs_rs_isr(void) NONBANKED NAKED {")
        emit("__asm")
        emit("        ld a, (#_gbs_rs_on)")
        emit("        or a")
        emit("        ret z")
        emit("        ; GBDK is inside a VRAM transfer: a register write now would")
        emit("        ; redirect the rest of it. Skip this frame.")
        emit("        ld a, (#__shadow_OAM_OFF)")
        emit("        or a")
        emit("        ret nz")
        emit("        in a, (#0x7E)           ; the V counter")
        emit("        cp #%d                  ; already past the last line" % (top + lines - 2))
        emit("        ret nc")
        emit("        ld b, a")
        emit("        sub #%d" % ((top - 2) & 0xFF))
        emit("        ld e, a")
        emit("        ld d, #0")
        emit("        ld hl, (#_gbs_rs_live)")
        emit("        add hl, de              ; entry (v + 2 - top)")
        emit("        ld c, #0xBF             ; the VDP control port")
        emit("1$:")
        emit("        in a, (#0x7E)")
        emit("        cp b")
        emit("        jr z, 1$")
        emit("        ld b, a")
        emit("        ld a, (hl)")
        emit("        inc hl")
        emit("        out (c), a")
        emit("        ld a, #0x88             ; register 8: horizontal scroll")
        emit("        out (c), a")
        emit("        ld a, b")
        emit("        cp #%d" % (top + lines - 2))
        emit("        jr c, 1$")
        emit("        jp _gbs_rs_tail")
        emit("__endasm;")
        emit("}")
        emit("void gbs_rs_set(uint8_t line, uint8_t x, uint8_t y) {")
        emit("    (void)y;")
        emit("    if (line >= GBS_RS_LINES) return;")
        emit("    x = (uint8_t)(0 - x);       /* the VDP scrolls the other way */")
        emit("    gbs_rs_buf[gbs_rs_back][line] = x;")
        emit("    if (!gbs_rs_on) gbs_rs_buf[gbs_rs_back ^ 1][line] = x;")
        emit("}")
        emit("void gbs_rs_copy(uint8_t line, uint8_t n, const uint8_t *t) {")
        emit("    uint8_t *p, *q;")
        emit("    if (line >= GBS_RS_LINES) return;")
        emit("    if (n > GBS_RS_LINES - line) n = GBS_RS_LINES - line;")
        emit("    p = gbs_rs_buf[gbs_rs_back] + line;")
        emit("    q = gbs_rs_buf[gbs_rs_back ^ 1] + line;")
        emit("    while (n--) {")
        emit("        *p = (uint8_t)(0 - *t);")
        emit("        if (!gbs_rs_on) *q = *p;")
        emit("        ++p; ++q; t += 2;")
        emit("    }")
        emit("}")
        emit("void gbs_rs_show(void) { gbs_rs_ready = 1; }")
        emit("uint8_t gbs_rs_get(uint8_t line) {")
        emit("    if (line >= GBS_RS_LINES) return 0;")
        emit("    return (uint8_t)(0 - gbs_rs_buf[gbs_rs_back][line]);")
        emit("}")
        self._emit_raster_fill_state()
        emit("static void gbs_rs_curve_asm(void) NAKED {")
        emit("__asm")
        emit("        push bc")
        emit("        ld de, (#_gbs_rs_cx)")
        emit("        ld bc, (#_gbs_rs_cdx)")
        emit("        ld hl, (#_gbs_rs_cp)")
        emit("1$:")
        emit("        ld a, d")
        emit("        neg                     ; the VDP scrolls the other way")
        emit("        ld (hl), a")
        emit("        dec hl                  ; the line above")
        emit("        ex de, hl")
        emit("        add hl, bc")
        emit("        ex de, hl               ; x += dx")
        emit("        push hl")
        emit("        ld hl, (#_gbs_rs_cdd)")
        emit("        add hl, bc")
        emit("        ld b, h")
        emit("        ld c, l                 ; dx += ddx")
        emit("        pop hl")
        emit("        ld a, (#_gbs_rs_cn)")
        emit("        dec a")
        emit("        ld (#_gbs_rs_cn), a")
        emit("        jr nz, 1$")
        emit("        ld (#_gbs_rs_cx), de")
        emit("        ld (#_gbs_rs_cdx), bc")
        emit("        pop bc")
        emit("        ret")
        emit("__endasm;")
        emit("}")
        emit("void gbs_rs_curve(uint8_t line, uint8_t n, uint16_t ddx) {")
        emit("    if (line >= GBS_RS_LINES || !n) return;")
        emit("    if (n > line + 1) n = line + 1;")
        emit("    gbs_rs_cp = gbs_rs_buf[gbs_rs_back] + line;")
        emit("    gbs_rs_cn = n;")
        emit("    gbs_rs_cdd = ddx;")
        emit("    gbs_rs_curve_asm();")
        emit("}")
        emit("/* No per-line vertical scroll on this VDP: nothing to pick between. */")
        emit("void gbs_rs_stripes(uint8_t line, uint8_t n, const uint8_t *depth,")
        emit("                    uint8_t phase, uint8_t y) {")
        emit("    (void)line; (void)n; (void)depth; (void)phase; (void)y;")
        emit("}")
        emit("void gbs_rs_arm(uint8_t on, uint8_t first) {")
        emit("    uint8_t irq;")
        emit("    if (!on) {")
        emit("        if (gbs_rs_on) {")
        emit("            gbs_rs_on = 0;")
        emit("            __WRITE_VDP_REG(VDP_R0, __READ_VDP_REG(VDP_R0) & 0xEFu);")
        emit("        }")
        emit("        return;")
        emit("    }")
        emit("    if (first >= GBS_RS_LINES) first = GBS_RS_LINES - 1;")
        emit("    /* The line counter underflows `R10` lines into the frame. */")
        emit("    irq = (uint8_t)(GBS_RS_TOP + first);")
        emit("    irq = (irq > 3) ? (uint8_t)(irq - 3) : 0;")
        emit("    gbs_rs_first = first;")
        emit("    if (!gbs_rs_wired) {")
        emit("        gbs_rs_live = gbs_rs_buf[gbs_rs_back ^ 1];")
        emit("        add_LCD(gbs_rs_isr);")
        emit("        gbs_rs_wired = 1;")
        emit("    }")
        emit("    if (first) gbs_rs_live[first - 1] = gbs_rs_live[0];")
        if self.bkg_move_used:
            # bkg.move is a shadow that video.wait_vblank commits, and that
            # commit stands down while the table is armed: take the pending
            # scroll now, or the VERTICAL scroll (which the table never
            # writes on this console) stays wherever the last screen left it.
            emit("    move_bkg(gbs_scr_shx, gbs_scr_shy);")
        emit("    __WRITE_VDP_REG(VDP_R10, irq);")
        emit("    __WRITE_VDP_REG(VDP_RSCX, gbs_rs_live[0]);")
        emit("    gbs_rs_on = 1;")
        emit("    __WRITE_VDP_REG(VDP_R0, __READ_VDP_REG(VDP_R0) | R0_IE1);")
        emit("}")

    # ------------------------------------------------------- set_data_native
    def _emit_gbdk_bkg_native(self):
        """`bkg.set_data_native` - tiles already in the console's own format.

        On SMS / Game Gear the VDP stores a tile as 8 rows x 4 bitplane bytes.
        Under the 16-colour tier `bkg.set_data` takes PACKED-NIBBLE tiles (the
        portable 4bpp form the asset pipeline emits) and converts each one at
        run time; a program whose build step already knows the target can
        emit the planar bytes and upload them as they are. Everywhere else the
        native format IS what `bkg.set_data` takes, so this is the same call."""
        self.emit("/* bkg.set_data_native: tiles in the console's own format, no conversion. */")
        self.emit("void gbs_bkg_data_native(uint8_t first, uint8_t count,")
        self.emit("                         const uint8_t *data) {")
        if self.platform in ('sms', 'gamegear'):
            self.emit("    set_bkg_native_data(first, count, data);")
        else:
            self.emit("    set_bkg_data(first, count, data);")
        self.emit("}")

    # ------------------------------------------------------------ cpu_fast
    def _emit_gbdk_cpu_fast(self):
        """`system.cpu_fast(on)` - the Game Boy Color's double-speed CPU mode.

        Twice the instructions per frame for the cost of battery life. Real on
        the Game Boy Color target (and only when the cartridge is running on
        Color hardware); a no-op on every other console.

        It changes what a CPU-clocked rate means: the TIMER interrupt and
        `system.delay` run twice as fast. Anything paced by v-blank
        (`video.wait_vblank`, `system.frames`) is unaffected - the LCD keeps
        its speed."""
        if self.platform == 'gameboy_color':
            self.emit("/* system.cpu_fast: the Color's double-speed mode (KEY1 + STOP).")
            self.emit("   Guarded on the hardware, so the same ROM on a monochrome Game")
            self.emit("   Boy just carries on at single speed. */")
            self.emit("void gbs_cpu_fast(uint8_t on) {")
            self.emit("    if (_cpu != CGB_TYPE) return;")
            self.emit("    if (on) cpu_fast(); else cpu_slow();")
            self.emit("}")
        else:
            self.emit("/* system.cpu_fast: only the Game Boy Color has a second CPU speed. */")
            self.emit("void gbs_cpu_fast(uint8_t on) { (void)on; }")
