"""Cc65Backend <concern> methods, mixed into Cc65Backend.

Split out of the former monolithic cc65.py; a plain mixin class - methods run
against the full CodeGenerator instance (self.emit, self.caps, ...)."""


class Cc65PaletteMixin:
    def _emit_cc65_palette_set(self):
        """palette.load_bkg_set / load_sprite_set on the cc65 consoles.

        COUNT consecutive 4-colour slots out of one table, starting at word
        index `off` -- the per-scene palette set a coloured world loads at room
        load. Real on the PCE (four VCE background palettes) and on the Lynx
        for what its 16-pen partition holds (bkg slot 0, sprite slots 0..3).
        Emitted only when called."""
        if not self.palette_set_used:
            return
        if self.caps['has_tile_palettes']:
            self._emit_palette_set_real()
        elif self.platform == 'lynx':
            # The Lynx's 16 pens ARE partitioned into one background palette
            # (slot 0) and four sprite slots, and its single-slot setters
            # already honour that. A coloured world's set used to be dropped
            # here, so its room drew through the boot pens (an inverted grey
            # ramp). Load what the partition holds and STOP there: the sprite
            # setter masks the slot with `& 3`, so slots 4..7 would overwrite
            # 0..3.
            self.emit("/* palette.load_bkg_set / load_sprite_set on the Lynx: the pen")
            self.emit("   partition holds bkg slot 0 and sprite slots 0..3; the rest of")
            self.emit("   a set has nowhere to go. Portable 5-5-5 words, rounded here. */")
            self.emit("static uint16_t gbs_pal_word(uint16_t c) {")
            self.emit("    return gbs_rgb((uint8_t)(((c >> 10) & 0x1F) << 3),")
            self.emit("                   (uint8_t)(((c >> 5) & 0x1F) << 3),")
            self.emit("                   (uint8_t)((c & 0x1F) << 3));")
            self.emit("}")
            for name, setter, top in (("gbs_load_bkg_set", "gbs_set_bkg_palette", 1),
                                      ("gbs_load_spr_set", "gbs_set_spr_palette", 4)):
                self.emit("void %s(uint8_t slot, uint8_t count," % name)
                self.emit("%s const uint16_t *colors, uint16_t off) {" % (" " * len(name)))
                self.emit("    uint8_t i;")
                self.emit("    const uint16_t *p;")
                self.emit("    for (i = 0; i < count; ++i) {")
                self.emit("        if ((uint8_t)(slot + i) >= %d) break;" % top)
                self.emit("        p = colors + off + (uint16_t)i * 4;")
                self.emit("        %s((uint8_t)(slot + i), gbs_pal_word(p[0])," % setter)
                self.emit("            gbs_pal_word(p[1]), gbs_pal_word(p[2]),")
                self.emit("            gbs_pal_word(p[3]));")
                self.emit("    }")
                self.emit("}")
        else:
            self.emit("/* One partitioned hardware palette on this console: a per-scene")
            self.emit("   colour SET has nowhere to go. An honest no-op. */")
            self.emit("void gbs_load_bkg_set(uint8_t slot, uint8_t count,")
            self.emit("                      const uint16_t *colors, uint16_t off) {")
            self.emit("    (void)slot; (void)count; (void)colors; (void)off;")
            self.emit("}")
            self.emit("void gbs_load_spr_set(uint8_t slot, uint8_t count,")
            self.emit("                      const uint16_t *colors, uint16_t off) {")
            self.emit("    (void)slot; (void)count; (void)colors; (void)off;")
            self.emit("}")

    def _emit_cc65_fade(self):
        """`palette.fade` on the cc65 consoles: an honest no-op.

        The verb exists for the CGB, where the DMG palette REGISTERS the fade
        normally ramps are ignored by the hardware. The Lynx has its own native
        fade (`native.lynx.fade_out`, real Mikey pen ramping) and the PCE's VCE
        would need the same shadow the CGB path keeps, so neither is wired yet;
        `vm.fx` calls the seam unconditionally, so a stub here keeps the
        generated `rooms.mos` target-neutral. Emitted only when called."""
        if not self.palette_fade_used:
            return
        if self._pce_fade:
            self._emit_pce_fade()
            return
        self.emit("/* palette.fade: not wired on this console (the Lynx fades through")
        self.emit("   native.lynx instead). An honest no-op. */")
        self.emit("void gbs_pal_fade(uint8_t level) { (void)level; }")

    @property
    def _pce_fade(self):
        """A PCE program that calls `palette.fade`: its VCE writes go through a
        shadow and the fade is real (it was a no-op stub)."""
        return self.platform == "pce" and self.palette_fade_used

    def _emit_pce_fade_shadow(self):
        """`gbs_vce` with a SHADOW, for `palette.fade` on the PC Engine.

        The SMS/GG fade (`gbdk_palette._emit_smsgg_fade`) against the VCE:
        there is no palette register to darken, so the fade scales the colour
        RAM, and the shadow is there for the same ordering reason - load_room
        blacks out, loads the new room's palettes, paints, and only then ramps,
        so a palette written while the screen is dark must be written DIMMED
        and the ramp back must be exact. Every colour the engine writes goes
        through `gbs_vce`; shadowed are the BG palettes 0-5 (the backdrop $000,
        the text ink $011, bkg slots 0-3 = palettes 2-5) and the sprite
        palettes 0-3 ($100-$13F): 160 words. The engines' grey defaults are
        written before any palette and stay raw."""
        white = bool(self.defines.get('VM_FADE_STYLE'))
        self.emit("/* palette.fade scales the VCE through this shadow (160 words: BG")
        self.emit("   palettes 0-5, sprite palettes 0-3), so a palette LOADED while the")
        self.emit("   screen is dark is written dimmed and the ramp back is exact. */")
        self.emit("static uint16_t gbs_vsh[160];")
        self.emit("static uint8_t gbs_vmsk[20];        /* entries actually written */")
        self.emit("static uint8_t gbs_fade_lvl;        /* 0 normal .. 3 black */")
        if white:
            self.emit("static uint8_t gbs_fade_white;      /* 1 = the ramp goes to WHITE */")
        self.emit("static void gbs_vce_raw(uint16_t index, uint16_t color) {")
        self.emit("    (*(volatile uint8_t *)0x0402) = (uint8_t)index;")
        self.emit("    (*(volatile uint8_t *)0x0403) = (uint8_t)(index >> 8);")
        self.emit("    (*(volatile uint8_t *)0x0404) = (uint8_t)color;")
        self.emit("    (*(volatile uint8_t *)0x0405) = (uint8_t)(color >> 8);")
        self.emit("}")
        self.emit("/* 9-bit GGGRRRBBB, 3 bits a channel, scaled by the fade level. */")
        self.emit("static uint16_t gbs_vce_dim(uint16_t c) {")
        self.emit("    uint8_t k, x, n = (uint8_t)(3 - gbs_fade_lvl);")
        self.emit("    uint16_t o = 0;")
        self.emit("    if (n == 3) return c;")
        if white:
            self.emit("    if (gbs_fade_white) {")
            self.emit("        if (n == 0) return 0x1FF;   /* the whole white load */")
            self.emit("        for (k = 0; k < 9; k += 3) {")
            self.emit("            x = (uint8_t)((c >> k) & 7);")
            self.emit("            o |= (uint16_t)((uint8_t)(x + (7 - x) * gbs_fade_lvl / 3)) << k;")
            self.emit("        }")
            self.emit("        return o;")
            self.emit("    }")
        self.emit("    if (n == 0) return 0;")
        self.emit("    for (k = 0; k < 9; k += 3) {")
        self.emit("        x = (uint8_t)((c >> k) & 7);")
        self.emit("        o |= (uint16_t)((uint8_t)(x * n / 3)) << k;")
        self.emit("    }")
        self.emit("    return o;")
        self.emit("}")
        self.emit("static uint8_t gbs_vce_slot(uint16_t index) {")
        self.emit("    if (index < 0x60) return (uint8_t)index;")
        self.emit("    if (index >= 0x100 && index < 0x140) return (uint8_t)(index - 0xA0);")
        self.emit("    return 0xFF;")
        self.emit("}")
        self.emit("static void gbs_vce(uint16_t index, uint16_t color) {")
        self.emit("    uint8_t s = gbs_vce_slot(index);")
        self.emit("    if (s != 0xFF) {")
        self.emit("        gbs_vsh[s] = color;")
        self.emit("        gbs_vmsk[s >> 3] |= (uint8_t)(1 << (s & 7));")
        self.emit("        color = gbs_vce_dim(color);")
        self.emit("    }")
        self.emit("    gbs_vce_raw(index, color);")
        self.emit("}")

    def _emit_pce_fade(self):
        """`palette.fade(level)` on the PC Engine: rewrite every shadowed VCE
        entry at the new level (bit 7 of `level` = towards white, as on the
        CGB and SMS/GG, only on a build that states VM_FADE_STYLE)."""
        white = bool(self.defines.get('VM_FADE_STYLE'))
        self.emit("void gbs_pal_fade(uint8_t level) {")
        self.emit("    uint8_t s;")
        if white:
            self.emit("    uint8_t w = (uint8_t)(level >> 7);   /* bit 7: towards white */")
            self.emit("    level &= 0x7F;")
        self.emit("    if (level > 3) level = 3;")
        if white:
            self.emit("    if (level == gbs_fade_lvl && w == gbs_fade_white) return;")
            self.emit("    gbs_fade_white = w;")
        else:
            self.emit("    if (level == gbs_fade_lvl) return;   /* the ramp holds each step */")
        self.emit("    gbs_fade_lvl = level;")
        self.emit("    for (s = 0; s < 160; ++s)")
        self.emit("        if (gbs_vmsk[s >> 3] & (uint8_t)(1 << (s & 7)))")
        self.emit("            gbs_vce_raw(s < 0x60 ? (uint16_t)s : (uint16_t)(s + 0xA0),")
        self.emit("                        gbs_vce_dim(gbs_vsh[s]));")
        self.emit("}")

    def _emit_cc65_meta_pal(self):
        """`sprite.set_meta_palettes` on the cc65 consoles.

        The cc65 metasprite layer reserves ONE real sprite slot per 8x8 cell
        (`gbs_set_metasprite` walks base..base+w*h-1), so a per-cell palette is
        just a loop over the per-slot setter that already exists -- no
        remembered map is needed, because neither the Lynx nor the PCE re-fans
        the palette: the Lynx keeps it in the SCB penpal (a different field
        from the flip) and the PCE's set_prop already preserves the SATB
        palette nibble. The map is ROW-MAJOR in 8x8-tile units, matching
        `sprite.set_meta`'s own w/h, so the authored data is target-neutral.
        Emitted only when called."""
        if not self.meta_palettes_used:
            return
        self.emit("/* sprite.set_meta_palettes: one palette per CELL of a metasprite")
        self.emit("   (row-major, 8x8-tile units) -- here one real sprite slot each. */")
        self.emit("void gbs_set_meta_pal(uint8_t nb, uint8_t w, uint8_t h,")
        self.emit("                      const uint8_t *data, uint16_t off) {")
        self.emit("    uint8_t k, n = (uint8_t)(w * h);")
        self.emit("    for (k = 0; k < n; ++k)")
        self.emit("        gbs_sprite_palette((uint8_t)(nb + k), data[off + k]);")
        if self._fan_pal:
            # A per-CELL map: no fan palette for set_meta to re-apply over it.
            self.emit("    for (k = 0; k < n && (uint8_t)(nb + k) < GBS_MAX_SPRITES; ++k)")
            self.emit("        gbs_meta_spal[(uint8_t)(nb + k)] = 0;")
        self.emit("}")

    def _emit_lynx_palette_core(self):
        """graphics.palette on the Lynx: partition the 16-pen hardware palette.

        The Lynx has one global 16-pen palette (Mikey GREEN/BLUERED register
        pairs); every Suzy sprite maps its 2-bit pixel values onto pens via
        its SCB penpal. The partition budgets the 16 pens exactly into the
        GB palette model:

          pen 0       sprite-transparent + bkg color 0 (the backdrop color
                      the present-clear paints -- GB semantics: the screen
                      outside drawn tiles shows bkg color 0)
          pens 1-12   sprite slots 0-3, colors 1-3 (slot s -> pens 3s+1..3s+3)
          pens 13-15  bkg colors 1-3 (pen 15 doubles as the text foreground)

        Defaults reproduce the engines' grey ramp, so a program that imports
        graphics.palette but never sets colors looks unchanged. Once colors
        are set, TGI text follows the bkg slot (fg = bkg color 3, bg = bkg
        color 0) -- the same coupling as on the Game Boy, where text tiles
        live in the background layer. Emitted before the engines, whose init
        code applies the defaults via gbs_pal_init().
        """
        self.emit("/* --- graphics.palette: the Lynx 16-pen partition --- */")
        self.emit("/* pen 0 = sprite-transparent + bkg color 0; pens 3s+1..3s+3 = sprite")
        self.emit("   slot s colors 1-3; pens 13-15 = bkg colors 1-3 (15 = text fg). */")
        self.emit("static const uint8_t gbs_pal_penpal[4][2] = {")
        self.emit("    {0x01, 0x23}, {0x04, 0x56}, {0x07, 0x89}, {0x0A, 0xBC}")
        self.emit("};")
        self.emit("static uint8_t gbs_pal_inited = 0;")
        self.emit("/* Color words are 12-bit 0x0GBR: Mikey GREEN ($FDA0+) gets the high")
        self.emit("   byte, BLUERED ($FDB0+) the low byte (blue high nibble, red low). */")
        self.emit("static void gbs_pal_set_pen(uint8_t pen, uint16_t c) {")
        self.emit("    MIKEY.palette[pen] = (uint8_t)(c >> 8);")
        self.emit("    MIKEY.palette[16 + pen] = (uint8_t)c;")
        self.emit("}")
        self.emit("static void gbs_pal_init(void) {")
        self.emit("    uint8_t s;")
        self.emit("    if (gbs_pal_inited) return;")
        self.emit("    gbs_video_init();  /* tgi_init's default palette runs first */")
        self.emit("    /* Grey-ramp defaults in every pen group: the non-palette look. */")
        self.emit("    gbs_pal_set_pen(0, 0x0000);")
        self.emit("    for (s = 0; s < 4; ++s) {")
        self.emit("        gbs_pal_set_pen((uint8_t)(3 * s + 1), 0x0AAA);  /* light grey */")
        self.emit("        gbs_pal_set_pen((uint8_t)(3 * s + 2), 0x0555);  /* dark grey */")
        self.emit("        gbs_pal_set_pen((uint8_t)(3 * s + 3), 0x0FFF);  /* white */")
        self.emit("    }")
        self.emit("    gbs_pal_set_pen(13, 0x0AAA);")
        self.emit("    gbs_pal_set_pen(14, 0x0555);")
        self.emit("    gbs_pal_set_pen(15, 0x0FFF);")
        self.emit("    gbs_pal_inited = 1;")
        self.emit("}")
        self.emit("uint16_t gbs_rgb(uint8_t r, uint8_t g, uint8_t b) {")
        self.emit("    return (uint16_t)(((uint16_t)(g >> 4) << 8)")
        self.emit("                      | ((uint16_t)(b >> 4) << 4) | (r >> 4));")
        self.emit("}")
        self.emit("void gbs_set_bkg_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
        self.emit("    if (slot != 0) return;  /* one bkg palette in the pen partition */")
        self.emit("    gbs_pal_init();")
        self.emit("    gbs_pal_set_pen(0, c0);")
        self.emit("    gbs_pal_set_pen(13, c1);")
        self.emit("    gbs_pal_set_pen(14, c2);")
        self.emit("    gbs_pal_set_pen(15, c3);")
        self.emit("}")
        self.emit("void gbs_set_spr_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
        self.emit("    (void)c0;  /* color 0 stays pen 0 = transparent */")
        self.emit("    slot &= 3;")
        self.emit("    gbs_pal_init();")
        self.emit("    gbs_pal_set_pen((uint8_t)(3 * slot + 1), c1);")
        self.emit("    gbs_pal_set_pen((uint8_t)(3 * slot + 2), c2);")
        self.emit("    gbs_pal_set_pen((uint8_t)(3 * slot + 3), c3);")
        self.emit("}")
        self.emit("void gbs_load_bkg_palette(uint8_t slot, const uint16_t *colors) {")
        self.emit("    gbs_set_bkg_palette(slot, colors[0], colors[1], colors[2], colors[3]);")
        self.emit("}")
        self.emit("void gbs_load_spr_palette(uint8_t slot, const uint16_t *colors) {")
        self.emit("    gbs_set_spr_palette(slot, colors[0], colors[1], colors[2], colors[3]);")
        self.emit("}")
        self._emit_cc65_palette_set()
        if self.load_bkg16_used:
            if getattr(self, 'lynx_bkg16', False):
                # 4bpp (16-colour) Lynx background ([world] lynx_bkg16): the strips
                # are BPP_4 with an identity pen map, so load the tileset's 16
                # colours into the Mikey pens. BKG_PALETTE16 is portable 5-5-5 RGB;
                # gbs_rgb rounds each to the Lynx 12-bit 0xGBR word (as the sprite
                # 4bpp path does). Run AFTER bkg.set_data (which grey-ramps the pens).
                self.emit("/* palette.load_bkg16: 16 colours (5-5-5 RGB) -> the Mikey pens")
                self.emit("   (the 4bpp bkg strips index them 1:1 via the identity pen map). */")
                self.emit("void gbs_load_bkg_pal16(const uint16_t *pal) {")
                self.emit("    uint8_t p, r, g, b; uint16_t w;")
                self.emit("    for (p = 0; p < 16; ++p) {")
                self.emit("        r = (uint8_t)(((pal[p] >> 10) & 0x1F) << 3);")
                self.emit("        g = (uint8_t)(((pal[p] >> 5) & 0x1F) << 3);")
                self.emit("        b = (uint8_t)((pal[p] & 0x1F) << 3);")
                self.emit("        w = gbs_rgb(r, g, b);")
                self.emit("        MIKEY.palette[p] = (uint8_t)(w >> 8);")
                self.emit("        MIKEY.palette[16 + p] = (uint8_t)w;")
                self.emit("    }")
                self.emit("}")
            else:
                # 2bpp Lynx background (opt-in off): the tileset luma-quantized to
                # greys, so the pen load is a no-op (the symbol still resolves).
                self.emit("/* palette.load_bkg16: Lynx 2bpp bkg (no [world] lynx_bkg16). */")
                self.emit("void gbs_load_bkg_pal16(const uint16_t *pal) { (void)pal; }")

    def _emit_lynx_sprite_palette(self):
        """sprite.set_palette on the Lynx: repoint a slot's SCB pen triple."""
        self.emit("/* sprite.set_palette: repoint the sprite's SCB at the slot's pens. */")
        self.emit("void gbs_sprite_palette(uint8_t nb, uint8_t slot) {")
        if self._fan_pal:
            # A metasprite is w x h SCBs: recolour the whole FAN and record
            # the slot for a set_meta that grows it (see the PCE twin below).
            self._emit_fan_sprite_palette()
        else:
            self.emit("    gbs_spr_init();")
            self.emit("    slot &= 3;")
            self.emit("    if (nb < GBS_MAX_SPRITES) {")
            self.emit("        gbs_scb[nb].s.penpal[0] = gbs_pal_penpal[slot][0];")
            self.emit("        gbs_scb[nb].s.penpal[1] = gbs_pal_penpal[slot][1];")
            self.emit("    }")
        self.emit("    gbs_force = 1;   /* penpal repoint -> present must re-blit the slot */")
        self.emit("}")

    def _emit_fan_sprite_palette(self):
        """The body of a FAN-aware `gbs_sprite_palette` (PCE and Lynx)."""
        self.emit("    uint8_t k, n = 1;")
        self.emit("    gbs_spr_init();")
        self.emit("    if (nb >= GBS_MAX_SPRITES) return;")
        self.emit("    slot &= 3;")
        self.emit("    gbs_meta_spal[nb] = (uint8_t)(slot + 1);")
        if self.meta_list_used:
            self.emit("    if (gbs_meta_w[nb] == 0xFF) n = gbs_meta_h[nb];")
            self.emit("    else")
        self.emit("    if (gbs_meta_w[nb] > 1 || gbs_meta_h[nb] > 1)")
        self.emit("        n = (uint8_t)(gbs_meta_w[nb] * gbs_meta_h[nb]);")
        self.emit("    for (k = 0; k < n && (uint8_t)(nb + k) < GBS_MAX_SPRITES; ++k)")
        self.emit("        " + self._fan_cell_pal("nb + k", "slot"))

    def _emit_pce_palette(self, with_bkg):
        """graphics.palette on the PC Engine: VCE color RAM + SATB bits.

        Sprite slots 0-3 map to VCE sprite palettes 0-3 ($100+; entry 0 is
        hardware-transparent). conio text glyphs render through BG palette 1
        entry 1 (verified against Beetle PCE Fast: recoloring palette 1
        recolors the text -- the runtime initialises that entry to white),
        so palette mode reserves palette 1 as the *text* palette and maps
        bkg slots 0-3 to VCE BG palettes 2-5. Setting bkg slot 0 writes the
        backdrop (VCE $000 -- BG color 0 of every palette displays it) and
        the text ink (palette 1 entry 1 = bkg color 3), which reproduces
        the Game Boy text model: paper = bkg color 0, ink = bkg color 3,
        exactly as on the GB family and the Lynx pen partition. The setters
        init the engines first so a later lazy engine init cannot clobber
        user colors with its grey-ramp defaults. Emitted after the engines.
        """
        self.emit("/* --- graphics.palette (PC Engine VCE color RAM) --- */")
        if self._pce_fade:
            self._emit_pce_fade_shadow()
        else:
            self.emit("static void gbs_vce(uint16_t index, uint16_t color) {")
            self.emit("    (*(volatile uint8_t *)0x0402) = (uint8_t)index;")
            self.emit("    (*(volatile uint8_t *)0x0403) = (uint8_t)(index >> 8);")
            self.emit("    (*(volatile uint8_t *)0x0404) = (uint8_t)color;")
            self.emit("    (*(volatile uint8_t *)0x0405) = (uint8_t)(color >> 8);")
            self.emit("}")
        self.emit("/* Color words are 9-bit VCE GGGRRRBBB. */")
        self.emit("uint16_t gbs_rgb(uint8_t r, uint8_t g, uint8_t b) {")
        self.emit("    return (uint16_t)(((uint16_t)(g >> 5) << 6)")
        self.emit("                      | ((uint16_t)(r >> 5) << 3) | (b >> 5));")
        self.emit("}")
        self.emit("void gbs_set_bkg_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
        self.emit("    slot &= 3;")
        if with_bkg:
            self.emit("    gbs_bkg_init();  /* grey defaults first; cannot clobber later */")
        else:
            self.emit("    gbs_video_init();")
        self.emit("    /* bkg slots 0-3 -> VCE BG palettes 2-5 (palette 1 entry 1 is the")
        self.emit("       conio text ink). BG color 0 always shows VCE $000, so slot 0's")
        self.emit("       color 0 recolors the global backdrop (= the text paper), and")
        self.emit("       its color 3 becomes the text ink -- the Game Boy text model. */")
        self.emit("    if (slot == 0) { gbs_vce(0x000, c0); gbs_vce(0x011, c3); }")
        self.emit("    gbs_vce((uint16_t)((slot + 2) << 4) + 1, c1);")
        self.emit("    gbs_vce((uint16_t)((slot + 2) << 4) + 2, c2);")
        self.emit("    gbs_vce((uint16_t)((slot + 2) << 4) + 3, c3);")
        self.emit("}")
        self.emit("void gbs_set_spr_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
        self.emit("    (void)c0;  /* entry 0 is hardware-transparent */")
        self.emit("    slot &= 3;")
        self.emit("    gbs_spr_init();  /* grey defaults + SATB parking first */")
        self.emit("    gbs_vce((uint16_t)(0x100 + ((uint16_t)slot << 4)) + 1, c1);")
        self.emit("    gbs_vce((uint16_t)(0x100 + ((uint16_t)slot << 4)) + 2, c2);")
        self.emit("    gbs_vce((uint16_t)(0x100 + ((uint16_t)slot << 4)) + 3, c3);")
        self.emit("}")
        self.emit("void gbs_load_bkg_palette(uint8_t slot, const uint16_t *colors) {")
        self.emit("    gbs_set_bkg_palette(slot, colors[0], colors[1], colors[2], colors[3]);")
        self.emit("}")
        self.emit("void gbs_load_spr_palette(uint8_t slot, const uint16_t *colors) {")
        self.emit("    gbs_set_spr_palette(slot, colors[0], colors[1], colors[2], colors[3]);")
        self.emit("}")
        self._emit_cc65_palette_set()
        self.emit("/* sprite.set_palette -> SATB attribute bits 0-3 (sprite palette). */")
        self.emit("void gbs_sprite_palette(uint8_t nb, uint8_t slot) {")
        if self._fan_pal:
            # A metasprite is a FAN of hardware sprites: recolouring only the
            # base left every other cell on palette 0 (a 2x2 NPC drew three
            # quarters in the player's colours). Recolour the whole fan and
            # record the slot, so a set_meta that grows the fan later (the
            # room load sets the kind palette before the clip first tiles it)
            # carries it to the new cells too.
            self._emit_fan_sprite_palette()
        else:
            self.emit("    gbs_spr_init();")
            self.emit("    if (nb < GBS_MAX_SPRITES)")
            self.emit("        gbs_satb[(uint16_t)nb * 4 + 3] = (uint16_t)(")
            self.emit("            (gbs_satb[(uint16_t)nb * 4 + 3] & ~0x000Fu) | (slot & 3));")
        self.emit("}")
        if self.load_bkg16_used:
            # palette.load_bkg16 on the PCE: the scene BKG_PALETTE16 is 16 words
            # of portable 5-5-5 RGB (0RRRRRGGGGGBBBBB); unpack each, round to the
            # VCE's 9-bit depth via gbs_rgb, and load it into BG palette 2 -- the
            # palette bkg slot 0 selects (the 4bpp tileset chars reference it).
            # Entry 0 is never displayed (BG color 0 shows the global VCE $000),
            # so the tileset's colour 0 recolours that backdrop instead.
            self.emit("/* palette.load_bkg16: 16 colours (5-5-5 RGB words) -> VCE BG")
            self.emit("   palette 2 ($20..$2F); colour 0 recolours the global backdrop. */")
            self.emit("void gbs_load_bkg_pal16(const uint16_t *pal) {")
            self.emit("    uint8_t p, r, g, b;")
            if with_bkg:
                self.emit("    gbs_bkg_init();")
            else:
                self.emit("    gbs_video_init();")
            self.emit("    for (p = 0; p < 16; ++p) {")
            self.emit("        r = (uint8_t)(((pal[p] >> 10) & 0x1F) << 3);")
            self.emit("        g = (uint8_t)(((pal[p] >> 5) & 0x1F) << 3);")
            self.emit("        b = (uint8_t)((pal[p] & 0x1F) << 3);")
            self.emit("        if (p == 0) gbs_vce(0x000, gbs_rgb(r, g, b));")
            self.emit("        else gbs_vce((uint16_t)(0x20 + p), gbs_rgb(r, g, b));")
            self.emit("    }")
            self.emit("}")

    def _emit_lynx_load_sprite_pal16(self):
        """palette.load_sprite16 on the Lynx: load a 16-colour word array into
        the Mikey pens (the 4bpp sprite palette). Emitted in the 4bpp sprite
        engine when the program calls load_sprite16."""
        self.emit("/* palette.load_sprite16: 16 colour words -> the Mikey pens. */")
        self.emit("void gbs_load_sprite_pal16(const uint16_t *pal) {")
        self.emit("    uint8_t p;")
        self.emit("    gbs_spr_init();")
        self.emit("    for (p = 0; p < 16; ++p) {")
        self.emit("        MIKEY.palette[p] = (uint8_t)(pal[p] >> 8);")
        self.emit("        MIKEY.palette[16 + p] = (uint8_t)pal[p];")
        self.emit("    }")
        self.emit("}")
