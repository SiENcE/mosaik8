"""Cc65Backend <concern> methods, mixed into Cc65Backend.

Split out of the former monolithic cc65.py; a plain mixin class - methods run
against the full CodeGenerator instance (self.emit, self.caps, ...)."""


class Cc65NativeMixin:
    # ---- Metasprite layer (graphics.sprite sprite.set_meta), shared by the
    # Lynx and PCE sprite engines. A metasprite at base slot B with W*H tiles
    # reserves the slots B..B+W*H-1 and is moved/flipped/re-tiled as one unit;
    # the per-slot meta tables default to zero (== single sprite), so untouched
    # slots and non-metasprite programs are byte-identical. The public
    # move/set_tile/set_prop wrappers get a meta-aware early branch (injected
    # by _emit_cc65_meta_branch) that clears the base's meta size, iterates the
    # children through the same public wrappers (now single), then restores it
    # -- so the per-engine slot logic is never duplicated.

    def _emit_native_lynx(self, real):
        """native.lynx escape hatch: Mikey-palette fades + Suzy screen shake.

        `real` (the Lynx itself) emits the hardware implementations; the other
        cc65 console (PC Engine) gets no-ops, so a program that uses the Lynx
        extension still builds there (the fade is just an instant cut). The
        palette words are the native 0xGBR Lynx format (`<name>_palette16` /
        palette.rgb), so fade_in/out ramp each pen's G/B/R nibble in step."""
        if not real:
            self.emit("/* native.lynx: no-op on the PC Engine (Lynx-only hardware). */")
            self.emit("void gbs_lynx_fade_in(const uint16_t *pal, uint8_t frames) { (void)pal; (void)frames; }")
            self.emit("void gbs_lynx_fade_out(const uint16_t *pal, uint8_t frames) { (void)pal; (void)frames; }")
            self.emit("void gbs_lynx_screen_shake(uint8_t yoff) { (void)yoff; }")
            self.emit("void gbs_lynx_jingle(const uint16_t *notes, uint8_t count) { (void)notes; (void)count; }")
            self.emit("void gbs_lynx_sprite_camera(uint8_t first, uint8_t count, uint8_t x, uint8_t y) {")
            self.emit("    (void)first; (void)count; (void)x; (void)y;")
            self.emit("}")
            return
        self.emit("/* native.lynx: ramp the 16 Mikey pens to a fraction num/den of")
        self.emit("   their 0xGBR target (per-nibble), used by fade_in / fade_out. */")
        self.emit("static void gbs_lynx_pens(const uint16_t *pal, uint8_t num, uint8_t den) {")
        self.emit("    uint8_t p, g, b, r; uint16_t c;")
        self.emit("    for (p = 0; p < 16; ++p) {")
        self.emit("        c = pal[p];")
        self.emit("        g = (uint8_t)((((c >> 8) & 0x0F) * num) / den);")
        self.emit("        b = (uint8_t)((((c >> 4) & 0x0F) * num) / den);")
        self.emit("        r = (uint8_t)((( c        & 0x0F) * num) / den);")
        self.emit("        MIKEY.palette[p] = g;")
        self.emit("        MIKEY.palette[16 + p] = (uint8_t)((b << 4) | r);")
        self.emit("    }")
        self.emit("}")
        self.emit("static void gbs_lynx_wait1(void) { clock_t t = clock(); while (clock() == t) { } }")
        self.emit("void gbs_lynx_fade_in(const uint16_t *pal, uint8_t frames) {")
        self.emit("    uint8_t s;")
        self.emit("    if (frames == 0) frames = 1;")
        self.emit("    for (s = 0; s <= frames; ++s) { gbs_lynx_pens(pal, s, frames); gbs_lynx_wait1(); }")
        self.emit("}")
        self.emit("void gbs_lynx_fade_out(const uint16_t *pal, uint8_t frames) {")
        self.emit("    uint8_t s;")
        self.emit("    if (frames == 0) frames = 1;")
        self.emit("    for (s = frames; ; --s) {")
        self.emit("        gbs_lynx_pens(pal, s, frames); gbs_lynx_wait1();")
        self.emit("        if (s == 0) break;")
        self.emit("    }")
        self.emit("}")
        self.emit("/* Vertical screen offset (the Suzy display address shake). */")
        if getattr(self, 'lynx_camera_used', False):
            # the present puts VOFF back to the shake after the camera chain
            self.emit("void gbs_lynx_screen_shake(uint8_t yoff) { gbs_lynx_shk = yoff; SUZY.voff = yoff; }")
        else:
            self.emit("void gbs_lynx_screen_shake(uint8_t yoff) { SUZY.voff = yoff; }")
        self.emit("/* native multi-voice jingle: a melody on Mikey channel B, a second")
        self.emit("   voice independent of the sound.beep/sfx channel A (blocking, ~8")
        self.emit("   frames/note; freq 0 = rest). The portable layer has one channel;")
        self.emit("   this is the native Lynx audio tier. */")
        self.emit("static void gbs_lynx_chanb(uint16_t freq) {")
        self.emit("    uint32_t half; uint8_t sel = AUD_1;")
        self.emit("    if (freq == 0) { MIKEY.channel_b.control = 0; MIKEY.channel_b.volume = 0; return; }")
        self.emit("    half = 500000UL / freq;")
        self.emit("    while (half > 256 && sel < AUD_64) { half >>= 1; ++sel; }")
        self.emit("    if (half) --half;")
        self.emit("    MIKEY.channel_b.volume = 0x7F;")
        self.emit("    MIKEY.channel_b.feedback = 0x01;")
        self.emit("    MIKEY.channel_b.dac = 0;")
        self.emit("    MIKEY.channel_b.shiftlo = 0x01;")
        self.emit("    MIKEY.channel_b.other = 0;")
        self.emit("    MIKEY.channel_b.reload = (uint8_t)half;")
        self.emit("    MIKEY.channel_b.count = (uint8_t)half;")
        self.emit("    MIKEY.channel_b.control = (uint8_t)(ENABLE_RELOAD | ENABLE_COUNT | sel);")
        self.emit("}")
        self.emit("void gbs_lynx_jingle(const uint16_t *notes, uint8_t count) {")
        self.emit("    uint8_t i, f;")
        self.emit("    for (i = 0; i < count; ++i) {")
        self.emit("        gbs_lynx_chanb(notes[i]);")
        self.emit("        for (f = 0; f < 8; ++f) gbs_lynx_wait1();")
        self.emit("    }")
        self.emit("    gbs_lynx_chanb(0);")
        self.emit("}")
