"""GBDK backend: prelude + stdlib lowering map (mixin for CodeGenerator)."""


class GbdkBackend:
    """GBDK-specific codegen: the stdlib call map and C prelude.

    Mixed into CodeGenerator; all methods run against the full generator
    instance (self.emit, self.caps, self.platform, ...).
    """

    # mosaik stdlib calls -> C helper / GBDK function names.
    STDLIB_CALLS_GBDK = {
        ('video', 'enable_lcd'): 'gbs_enable_lcd',
        ('video', 'disable_lcd'): 'gbs_disable_lcd',
        # wait_vblank is a vsync() wrapper so it can also count down the
        # platform.sound beep duration (60 ticks/s on every console).
        ('video', 'wait_vblank'): 'gbs_wait_vblank',
        ('input', 'pressed'): 'gbs_input_pressed',
        ('input', 'held'): 'gbs_input_pressed',
        ('text', 'print_string'): 'gbs_print_string',
        ('text', 'print_number'): 'gbs_print_number',
        ('text', 'clear_area'): 'gbs_clear_area',
        ('hw', 'write'): 'gbs_hw_write',
        ('hw', 'read'): 'gbs_hw_read',
        # Display visibility (macros, wrapped as helpers).
        ('video', 'show_sprites'): 'gbs_show_sprites',
        ('video', 'hide_sprites'): 'gbs_hide_sprites',
        ('video', 'show_background'): 'gbs_show_bkg',
        ('video', 'show_window'): 'gbs_show_win',
        ('video', 'hide_window'): 'gbs_hide_win',
        # Sprites (graphics.sprite). sprite.move takes screen-pixel coords;
        # the gbs_ wrapper adds the per-console hardware offset.
        ('sprite', 'set_data'): 'set_sprite_data',
        ('sprite', 'set_tile'): 'set_sprite_tile',
        ('sprite', 'get_tile'): 'get_sprite_tile',
        ('sprite', 'set_prop'): 'set_sprite_prop',
        # set_meta lowers to a fan-out helper that reserves w*h consecutive
        # OAM slots (set_tile/set_prop are swapped to gbs_ wrappers when
        # metasprites are used; see CodeGenerator.generate).
        ('sprite', 'set_meta'): 'gbs_set_metasprite',
        ('sprite', 'move'): 'gbs_move_sprite',
        ('sprite', 'set_palette'): 'gbs_sprite_palette',
        # Background (graphics.bkg).
        ('bkg', 'set_data'): 'set_bkg_data',
        ('bkg', 'set_tiles'): 'set_bkg_tiles',
        ('bkg', 'scroll'): 'scroll_bkg',
        ('bkg', 'move'): 'move_bkg',
        ('bkg', 'set_palette'): 'gbs_bkg_palette_fill',
        # Palettes (graphics.palette): 4-color GB-model palette slots,
        # quantized to the console's native color format. Available on every
        # console -- 4-grey machines quantize to shades (see _emit_gbdk_palette).
        ('palette', 'rgb'): 'gbs_rgb',
        ('palette', 'set_bkg'): 'gbs_set_bkg_palette',
        ('palette', 'set_sprite'): 'gbs_set_spr_palette',
        ('palette', 'load_bkg'): 'gbs_load_bkg_palette',
        ('palette', 'load_sprite'): 'gbs_load_spr_palette',
        # 16-colour sprite palette: a no-op on the GB family (2bpp sprites);
        # the asset was luma-quantized to greys (generalized-with-limits).
        ('palette', 'load_sprite16'): 'gbs_load_sprite_pal16',
        # Window (graphics.window).
        ('window', 'set_tiles'): 'set_win_tiles',
        ('window', 'move'): 'move_win',
        # System utilities (platform.system).
        ('system', 'delay'): 'delay',
        ('system', 'random'): 'rand',
        ('system', 'seed_random'): 'initrand',
        # Sound (platform.sound): one square-wave beep channel.
        ('sound', 'beep'): 'gbs_sound_beep',
        ('sound', 'stop'): 'gbs_sound_stop',
        ('sound', 'sfx'): 'gbs_sound_sfx',
        # native.lynx escape hatch: no-ops on the GB family (the fade/shake are
        # Lynx hardware; one source still builds here -- generalized fallback).
        ('lynx', 'fade_in'): 'gbs_lynx_fade_in',
        ('lynx', 'fade_out'): 'gbs_lynx_fade_out',
        ('lynx', 'screen_shake'): 'gbs_lynx_screen_shake',
        ('lynx', 'jingle'): 'gbs_lynx_jingle',
    }

    def _emit_gbdk_includes(self):
        # <gbdk/platform.h> pulls in the correct console header for the build
        # target (Game Boy, Pocket, Mega Duck, SMS/GG, NES), so the same
        # generated C compiles for every supported platform.
        self.emit("#include <gbdk/platform.h>")
        if self.text_used:
            # console/font/stdio are only needed by graphics.text (printf-based,
            # large -- esp. on the NES). Omit them when text is unused.
            self.emit("#include <gbdk/console.h>")
            self.emit("#include <gbdk/font.h>")
        self.emit("#include <rand.h>")
        if self.text_used:
            self.emit("#include <stdio.h>")
        self.emit("#include <stdint.h>")
        self.emit("")

    def _emit_gbdk_defines(self):
        self.emit("/* Input button constants mapped to GBDK joypad bits */")
        self.emit("#define INPUT_A      J_A")
        self.emit("#define INPUT_B      J_B")
        self.emit("#define INPUT_SELECT J_SELECT")
        self.emit("#define INPUT_START  J_START")
        self.emit("#define INPUT_RIGHT  J_RIGHT")
        self.emit("#define INPUT_LEFT   J_LEFT")
        self.emit("#define INPUT_UP     J_UP")
        self.emit("#define INPUT_DOWN   J_DOWN")
        self.emit("")
        self.emit("/* Screen geometry for the build target (GBDK resolves the")
        self.emit("   DEVICE_* macros per console at C compile time). */")
        self.emit("#define SCREEN_WIDTH  DEVICE_SCREEN_PX_WIDTH")
        self.emit("#define SCREEN_HEIGHT DEVICE_SCREEN_PX_HEIGHT")
        self.emit("#define SCREEN_COLS   DEVICE_SCREEN_WIDTH")
        self.emit("#define SCREEN_ROWS   DEVICE_SCREEN_HEIGHT")
        self.emit("")
        if self.caps['has_gb_regs']:
            self.emit("/* Hardware register addresses (for hw.read / hw.write) */")
            self.emit("#define REG_DIV  0xFF04")
            self.emit("#define REG_NR10 0xFF10")
            self.emit("#define REG_BGP  0xFF47")
            self.emit("#define REG_OBP0 0xFF48")
            self.emit("#define REG_OBP1 0xFF49")
            self.emit("")
        self.emit("/* Sprite property flags (graphics.sprite) */")
        self.emit("#define FLIP_X S_FLIPX")
        self.emit("#define FLIP_Y S_FLIPY")
        self.emit("")

    def _emit_prelude_gbdk_decls(self):
        """Declarations-only prelude for a bank translation unit.

        Banked code (`bank(N)` functions) is emitted into its own C file --
        SDCC's `#pragma bank` is file-scoped -- so it needs the same includes
        and #defines as the main TU plus *prototypes* for the gbs_ helpers.
        The helper definitions live once in the main TU's home bank, which is
        always mapped, so banked code can call them at any time. Keep this
        list in sync with the definitions in _emit_prelude_gbdk.
        """
        self._emit_gbdk_includes()
        self._emit_gbdk_defines()
        self.emit("/* mosaik standard library helpers (defined in the main TU) */")
        self.emit("void gbs_enable_lcd(void);")
        self.emit("void gbs_disable_lcd(void);")
        self.emit("uint8_t gbs_input_pressed(uint8_t button);")
        if self.text_used:
            self.emit("void gbs_text_init(void);")
            self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s);")
            self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n);")
            self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h);")
        self.emit("void gbs_hw_write(uint16_t addr, uint8_t value);")
        self.emit("uint8_t gbs_hw_read(uint16_t addr);")
        self.emit("void gbs_show_sprites(void);")
        self.emit("void gbs_hide_sprites(void);")
        self.emit("void gbs_show_bkg(void);")
        self.emit("void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y);")
        if self.metasprite_used:
            self.emit("void gbs_set_metasprite(uint8_t base, uint8_t tile, uint8_t w, uint8_t h);")
            self.emit("void gbs_set_sprite_tile(uint8_t nb, uint8_t tile);")
            self.emit("void gbs_set_sprite_prop(uint8_t nb, uint8_t prop);")
        if self.load_sprite16_used:
            self.emit("void gbs_load_sprite_pal16(const uint16_t *pal);")
        if self.native_lynx_imported:
            self.emit("void gbs_lynx_fade_in(const uint16_t *pal, uint8_t frames);")
            self.emit("void gbs_lynx_fade_out(const uint16_t *pal, uint8_t frames);")
            self.emit("void gbs_lynx_screen_shake(uint8_t yoff);")
            self.emit("void gbs_lynx_jingle(const uint16_t *notes, uint8_t count);")
        if self.caps['has_window']:
            self.emit("void gbs_show_win(void);")
            self.emit("void gbs_hide_win(void);")
        self.emit("void gbs_sound_stop(void);")
        self.emit("void gbs_sound_beep(uint16_t freq, uint16_t frames);")
        if self.sound_sfx_used:
            self.emit("void gbs_sound_sfx(uint8_t id);")
        self.emit("void gbs_wait_vblank(void);")
        if self.palette_imported:
            self.emit("uint16_t gbs_rgb(uint8_t r, uint8_t g, uint8_t b);")
            self.emit("void gbs_set_bkg_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3);")
            self.emit("void gbs_set_spr_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3);")
            self.emit("void gbs_load_bkg_palette(uint8_t slot, const uint16_t *colors);")
            self.emit("void gbs_load_spr_palette(uint8_t slot, const uint16_t *colors);")
            self.emit("void gbs_sprite_palette(uint8_t nb, uint8_t slot);")
            if self.caps['has_tile_palettes']:
                self.emit("void gbs_bkg_palette_fill(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t slot);")
        self.emit("")

    def _emit_prelude_gbdk(self):
        cgb_class = self.platform in ('gameboy_color', 'analogue_pocket')
        # A CGB-flagged ROM (-Wm-yc) gets no boot-ROM compatibility palette, so
        # the hardware bkg/sprite palette 0 starts uninitialised and text (BGP
        # writes are ignored in CGB mode) renders in garbage colours. Seed
        # palette 0 with the standard DMG greys at LCD-enable -- but ONLY when
        # the program does not import graphics.palette, otherwise this would
        # clobber the colours a program sets before video.enable_lcd() (e.g.
        # samples/colors.mos, projects/background). Palette-managing programs
        # are responsible for their own slot 0.
        cgb_default_pal = cgb_class and not self.palette_imported
        self.emit("/* Generated by mosaik -> GBDK C backend */")
        self.emit("/* Target console: %s */" % self.platform)
        self._emit_gbdk_includes()
        self._emit_gbdk_defines()
        self.emit("/* mosaik standard library helpers */")
        if cgb_default_pal:
            self.emit("void gbs_cgb_default_palettes(void) {")
            self.emit("    static const palette_color_t greys[4] = {")
            self.emit("        RGB_WHITE, RGB_LIGHTGRAY, RGB_DARKGRAY, RGB_BLACK };")
            self.emit("    set_bkg_palette(0, 1, greys);")
            self.emit("    set_sprite_palette(0, 1, greys);")
            self.emit("}")
            self.emit("void gbs_enable_lcd(void) { gbs_cgb_default_palettes(); DISPLAY_ON; SHOW_BKG; }")
        else:
            self.emit("void gbs_enable_lcd(void) { DISPLAY_ON; SHOW_BKG; }")
        self.emit("void gbs_disable_lcd(void) { DISPLAY_OFF; }")
        self.emit("uint8_t gbs_input_pressed(uint8_t button) { return (uint8_t)(joypad() & button); }")
        if self.text_used:
            self.emit("/* GBDK-2020's console printf needs a font loaded before any text")
            self.emit("   tiles are drawn; do it lazily on first use (once). */")
            self.emit("void gbs_text_init(void) {")
            self.emit("    static uint8_t gbs_font_ready = 0;")
            self.emit("    if (!gbs_font_ready) { font_init(); font_set(font_load(font_ibm)); gbs_font_ready = 1; }")
            self.emit("}")
            self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s) { gbs_text_init(); gotoxy(x, y); printf(\"%s\", s); }")
            self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n) { gbs_text_init(); gotoxy(x, y); printf(\"%d\", n); }")
            self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {")
            self.emit("    uint8_t i, j;")
            self.emit("    gbs_text_init();")
            self.emit("    for (j = 0; j < h; j++) { gotoxy(x, y + j); for (i = 0; i < w; i++) printf(\" \"); }")
            self.emit("}")
        self.emit("/* Raw hardware register access (sound, palette, timer, ...) */")
        self.emit("void gbs_hw_write(uint16_t addr, uint8_t value) { *(volatile uint8_t *)addr = value; }")
        self.emit("uint8_t gbs_hw_read(uint16_t addr) { return *(volatile uint8_t *)addr; }")
        self.emit("/* Display visibility helpers (GBDK macros wrapped as functions) */")
        self.emit("void gbs_show_sprites(void) { SHOW_SPRITES; }")
        self.emit("void gbs_hide_sprites(void) { HIDE_SPRITES; }")
        self.emit("void gbs_show_bkg(void) { SHOW_BKG; }")
        if self.metasprite_used:
            self._emit_gbdk_metasprite()
        if self.load_sprite16_used:
            self.emit("/* 16-colour sprite palette: no-op on the 2bpp GB family (the 4bpp")
            self.emit("   asset was luma-quantized to greys at build time). */")
            self.emit("void gbs_load_sprite_pal16(const uint16_t *pal) { (void)pal; }")
        if self.native_lynx_imported:
            self.emit("/* native.lynx escape hatch: no-ops on the GB family (Lynx-only). */")
            self.emit("void gbs_lynx_fade_in(const uint16_t *pal, uint8_t frames) { (void)pal; (void)frames; }")
            self.emit("void gbs_lynx_fade_out(const uint16_t *pal, uint8_t frames) { (void)pal; (void)frames; }")
            self.emit("void gbs_lynx_screen_shake(uint8_t yoff) { (void)yoff; }")
            self.emit("void gbs_lynx_jingle(const uint16_t *notes, uint8_t count) { (void)notes; (void)count; }")
        self.emit("/* sprite.move takes screen-pixel coordinates (origin = top-left of")
        self.emit("   the visible screen); the hardware offset differs per console. */")
        self.emit("void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y) {")
        if self.metasprite_used:
            self.emit("    uint8_t w = gbs_meta_w[nb], h = gbs_meta_h[nb];")
            self.emit("    if (w > 1 || h > 1) {")
            self.emit("        /* Metasprite: lay out the reserved child slots in an w*h")
            self.emit("           grid of 8x8 cells, reversing columns/rows when flipped. */")
            self.emit("        uint8_t r, c, s = nb, prop = gbs_meta_prop[nb], cc, rr;")
            self.emit("        for (r = 0; r < h; ++r)")
            self.emit("            for (c = 0; c < w; ++c) {")
            if self.caps['has_sprite_flip']:
                self.emit("                cc = (prop & FLIP_X) ? (uint8_t)(w - 1 - c) : c;")
                self.emit("                rr = (prop & FLIP_Y) ? (uint8_t)(h - 1 - r) : r;")
            else:
                # SMS / Game Gear have no hardware sprite flip: the per-cell tiles
                # can't be mirrored, so reversing the cell layout would just garble
                # the block. Keep the normal layout (the sprite shows unflipped --
                # use dedicated/pre-mirrored frames for facing here).
                self.emit("                cc = c;  /* no hardware sprite flip on this console */")
                self.emit("                rr = r;")
            self.emit("                move_sprite(s, (uint8_t)(x + cc * 8 + DEVICE_SPRITE_PX_OFFSET_X),")
            self.emit("                               (uint8_t)(y + rr * 8 + DEVICE_SPRITE_PX_OFFSET_Y));")
            self.emit("                ++s;")
            self.emit("            }")
            self.emit("        return;")
            self.emit("    }")
        self.emit("    move_sprite(nb, (uint8_t)(x + DEVICE_SPRITE_PX_OFFSET_X),")
        self.emit("                    (uint8_t)(y + DEVICE_SPRITE_PX_OFFSET_Y));")
        self.emit("}")
        if self.caps['has_window']:
            self.emit("/* The window layer only exists on Game Boy-family consoles. */")
            self.emit("void gbs_show_win(void) { SHOW_WIN; }")
            self.emit("void gbs_hide_win(void) { HIDE_WIN; }")
        if self.palette_imported:
            self._emit_gbdk_palette()
        self._emit_gbdk_sound()
        if self.sound_sfx_used:
            self._emit_sound_sfx()
        self.emit("")

    def _emit_gbdk_metasprite(self):
        """Metasprite layer for the GBDK consoles (emitted only when
        sprite.set_meta is used).

        A metasprite at base slot B with W*H tiles reserves the OAM slots
        B..B+W*H-1; set_meta assigns them tiles row-major from the base tile,
        and gbs_move_sprite lays them out as an 8x8 grid (flip-aware). The
        meta tables default to zero (== single sprite) so untouched slots and
        non-metasprite programs behave exactly as before. set_tile/set_prop on
        a metasprite base fan out to the children (the stdlib map routes them
        to these wrappers only while metasprites are in use)."""
        self.emit("/* --- Metasprite layer (graphics.sprite sprite.set_meta) --- */")
        self.emit("static uint8_t gbs_meta_w[40];   /* 0/1 = single sprite */")
        self.emit("static uint8_t gbs_meta_h[40];")
        self.emit("static uint8_t gbs_meta_prop[40];")
        self.emit("void gbs_set_metasprite(uint8_t base, uint8_t tile, uint8_t w, uint8_t h) {")
        self.emit("    uint8_t r, c, s = base, t = tile, prop = gbs_meta_prop[base];")
        self.emit("    for (r = 0; r < h; ++r)")
        self.emit("        for (c = 0; c < w; ++c) {")
        self.emit("            set_sprite_tile(s, t);")
        self.emit("            set_sprite_prop(s, prop);")
        self.emit("            gbs_meta_w[s] = 1; gbs_meta_h[s] = 1;")
        self.emit("            ++s; ++t;")
        self.emit("        }")
        self.emit("    gbs_meta_w[base] = w; gbs_meta_h[base] = h;")
        self.emit("}")
        self.emit("/* set_tile on a metasprite base re-tiles all its children (row-major). */")
        self.emit("void gbs_set_sprite_tile(uint8_t nb, uint8_t tile) {")
        self.emit("    uint8_t w = gbs_meta_w[nb], h = gbs_meta_h[nb];")
        self.emit("    if (w > 1 || h > 1) {")
        self.emit("        uint8_t r, c, s = nb, t = tile;")
        self.emit("        for (r = 0; r < h; ++r)")
        self.emit("            for (c = 0; c < w; ++c) { set_sprite_tile(s, t); ++s; ++t; }")
        self.emit("        return;")
        self.emit("    }")
        self.emit("    set_sprite_tile(nb, tile);")
        self.emit("}")
        self.emit("/* set_prop on a metasprite base flips every child (move reorders cells). */")
        self.emit("void gbs_set_sprite_prop(uint8_t nb, uint8_t prop) {")
        self.emit("    uint8_t w = gbs_meta_w[nb], h = gbs_meta_h[nb], s, n;")
        self.emit("    gbs_meta_prop[nb] = prop;")
        self.emit("    if (w > 1 || h > 1) {")
        self.emit("        n = (uint8_t)(w * h);")
        self.emit("        for (s = 0; s < n; ++s) set_sprite_prop((uint8_t)(nb + s), prop);")
        self.emit("        return;")
        self.emit("    }")
        self.emit("    set_sprite_prop(nb, prop);")
        self.emit("}")

    def _emit_gbdk_palette(self):
        """graphics.palette for GBDK consoles (emitted only when imported).

        The portable model: a palette slot holds 4 colors; gbs_rgb quantizes
        RGB888 to the console's native format inside one function (on the NES
        the RGB8 macro is a 64-way constant-folding chain -- one copy here
        beats inlining it per call site). Per console class:
        - gameboy / megaduck (4 greys): gbs_rgb yields a DMG shade 0-3; bkg
          slot 0 packs into BGP, sprite slots 0/1 into OBP0/OBP1 (the *_REG
          symbols resolve per console -- the Mega Duck register remap
          included). Other slots are honest no-ops (caps: 1 bkg / 2 spr).
        - gameboy_color: cgb.h set_bkg_palette / set_sprite_palette, RGB555.
        - analogue_pocket: the CGB path plus a DMG-register mirror of slot
          0/1, so a Pocket core running the ROM in DMG mode still shows
          quantized shades instead of ignoring the palette entirely.
        - sms / gamegear: CRAM entries 0-3 (BG) and sprite-bank entries 0-3 --
          exactly where the port's 2bpp compat layer puts GB pixel values
          (its default _current_2bpp_palette is the identity, 0x3210).
        - nes: PPU palettes via set_bkg/sprite_palette_entry; entry 0 of BG
          palettes is the shared backdrop, so only slot 0's color 0 shows.
        """
        cgb_class = self.platform in ('gameboy_color', 'analogue_pocket')
        ap_mirror = self.platform == 'analogue_pocket'
        self.emit("/* graphics.palette: 4-color GB-model palette slots. */")
        if not self.caps['has_color']:
            self.emit("/* 4-grey console: colors quantize to DMG shades (white=0 .. black=3),")
            self.emit("   the same thresholds as the asset pipeline's PNG luma mapping. */")
            self.emit("uint16_t gbs_rgb(uint8_t r, uint8_t g, uint8_t b) {")
            self.emit("    uint16_t luma = ((uint16_t)r * 3 + (uint16_t)g * 6 + b) / 10;")
            self.emit("    if (luma >= 240) return 0;")
            self.emit("    if (luma >= 160) return 1;")
            self.emit("    if (luma >= 80) return 2;")
            self.emit("    return 3;")
            self.emit("}")
            self.emit("void gbs_set_bkg_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    if (slot != 0) return;  /* one BG palette on this console */")
            self.emit("    BGP_REG = DMG_PALETTE(c0, c1, c2, c3);")
            self.emit("}")
            self.emit("void gbs_set_spr_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    if (slot == 0) OBP0_REG = DMG_PALETTE(c0, c1, c2, c3);")
            self.emit("    else if (slot == 1) OBP1_REG = DMG_PALETTE(c0, c1, c2, c3);")
            self.emit("}")
        elif cgb_class:
            self.emit("uint16_t gbs_rgb(uint8_t r, uint8_t g, uint8_t b) { return RGB8(r, g, b); }")
            if ap_mirror:
                self.emit("/* DMG-register mirror: quantize an RGB555 word to a DMG shade so a")
                self.emit("   Pocket core running this ROM in DMG mode still shows the palette")
                self.emit("   (in CGB mode the BGP/OBP writes are ignored and harmless). */")
                self.emit("static uint8_t gbs_pal_shade(uint16_t c) {")
                self.emit("    uint16_t luma = ((c & 0x1F) * 3 + ((c >> 5) & 0x1F) * 6 + ((c >> 10) & 0x1F)) / 10;")
                self.emit("    if (luma >= 30) return 0;")
                self.emit("    if (luma >= 20) return 1;")
                self.emit("    if (luma >= 10) return 2;")
                self.emit("    return 3;")
                self.emit("}")
            self.emit("void gbs_set_bkg_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    palette_color_t buf[4];")
            self.emit("    buf[0] = c0; buf[1] = c1; buf[2] = c2; buf[3] = c3;")
            self.emit("    set_bkg_palette(slot & 7, 1, buf);")
            if ap_mirror:
                self.emit("    if (slot == 0)")
                self.emit("        BGP_REG = DMG_PALETTE(gbs_pal_shade(c0), gbs_pal_shade(c1),")
                self.emit("                              gbs_pal_shade(c2), gbs_pal_shade(c3));")
            self.emit("}")
            self.emit("void gbs_set_spr_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    palette_color_t buf[4];")
            self.emit("    buf[0] = c0; buf[1] = c1; buf[2] = c2; buf[3] = c3;")
            self.emit("    set_sprite_palette(slot & 7, 1, buf);")
            if ap_mirror:
                self.emit("    if (slot == 0)")
                self.emit("        OBP0_REG = DMG_PALETTE(gbs_pal_shade(c0), gbs_pal_shade(c1),")
                self.emit("                               gbs_pal_shade(c2), gbs_pal_shade(c3));")
                self.emit("    else if (slot == 1)")
                self.emit("        OBP1_REG = DMG_PALETTE(gbs_pal_shade(c0), gbs_pal_shade(c1),")
                self.emit("                               gbs_pal_shade(c2), gbs_pal_shade(c3));")
            self.emit("}")
        elif self.platform in ('sms', 'gamegear'):
            self.emit("uint16_t gbs_rgb(uint8_t r, uint8_t g, uint8_t b) { return RGB8(r, g, b); }")
            self.emit("/* The 2bpp compat layer loads GB pixel values into CRAM entries 0-3")
            self.emit("   (BG bank) / sprite-bank entries 0-3, so those are the slot-0 colors. */")
            self.emit("void gbs_set_bkg_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    if (slot != 0) return;  /* one BG palette on this console */")
            self.emit("    set_bkg_palette_entry(0, 0, c0);")
            self.emit("    set_bkg_palette_entry(0, 1, c1);")
            self.emit("    set_bkg_palette_entry(0, 2, c2);")
            self.emit("    set_bkg_palette_entry(0, 3, c3);")
            self.emit("}")
            self.emit("void gbs_set_spr_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    if (slot != 0) return;  /* sprites share the second CRAM bank */")
            self.emit("    set_sprite_palette_entry(0, 0, c0);  /* entry 0: transparent on screen,")
            self.emit("                                            but the SMS border shows it */")
            self.emit("    set_sprite_palette_entry(0, 1, c1);")
            self.emit("    set_sprite_palette_entry(0, 2, c2);")
            self.emit("    set_sprite_palette_entry(0, 3, c3);")
            self.emit("}")
        else:  # nes
            self.emit("/* RGB8 maps to the nearest NES master-palette index through a 64-way")
            self.emit("   constant-folding chain; keep the one copy inside this function. */")
            self.emit("uint16_t gbs_rgb(uint8_t r, uint8_t g, uint8_t b) { return RGB8(r, g, b); }")
            self.emit("void gbs_set_bkg_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    slot &= 3;")
            self.emit("    /* Entry 0 is the shared PPU backdrop ($3F00): the hardware only")
            self.emit("       displays slot 0's color 0. */")
            self.emit("    set_bkg_palette_entry(slot, 0, (palette_color_t)c0);")
            self.emit("    set_bkg_palette_entry(slot, 1, (palette_color_t)c1);")
            self.emit("    set_bkg_palette_entry(slot, 2, (palette_color_t)c2);")
            self.emit("    set_bkg_palette_entry(slot, 3, (palette_color_t)c3);")
            self.emit("}")
            self.emit("void gbs_set_spr_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    slot &= 3;")
            self.emit("    set_sprite_palette_entry(slot, 0, (palette_color_t)c0);  /* transparent */")
            self.emit("    set_sprite_palette_entry(slot, 1, (palette_color_t)c1);")
            self.emit("    set_sprite_palette_entry(slot, 2, (palette_color_t)c2);")
            self.emit("    set_sprite_palette_entry(slot, 3, (palette_color_t)c3);")
            self.emit("}")
        self.emit("void gbs_load_bkg_palette(uint8_t slot, const uint16_t *colors) {")
        self.emit("    gbs_set_bkg_palette(slot, colors[0], colors[1], colors[2], colors[3]);")
        self.emit("}")
        self.emit("void gbs_load_spr_palette(uint8_t slot, const uint16_t *colors) {")
        self.emit("    gbs_set_spr_palette(slot, colors[0], colors[1], colors[2], colors[3]);")
        self.emit("}")
        # sprite.set_palette: which palette slot a sprite uses.
        if self.platform in ('sms', 'gamegear'):
            self.emit("/* SMS/GG sprites always use the sprite CRAM bank; there is no")
            self.emit("   per-sprite palette select, so this is an honest no-op. */")
            self.emit("void gbs_sprite_palette(uint8_t nb, uint8_t slot) { (void)nb; (void)slot; }")
        elif self.platform == 'nes':
            self.emit("/* NES OAM attribute bits 0-1 select the sprite palette. */")
            self.emit("void gbs_sprite_palette(uint8_t nb, uint8_t slot) {")
            self.emit("    set_sprite_prop(nb, (uint8_t)((get_sprite_prop(nb) & ~0x03) | (slot & 0x03)));")
            self.emit("}")
        else:
            self.emit("/* The OAM attribute carries both the CGB palette number (bits 0-2)")
            self.emit("   and the DMG OBP0/OBP1 select (S_PALETTE); writing both keeps one")
            self.emit("   implementation correct in either mode. */")
            self.emit("void gbs_sprite_palette(uint8_t nb, uint8_t slot) {")
            self.emit("    uint8_t prop = (uint8_t)(get_sprite_prop(nb) & ~(S_PALETTE | 0x07));")
            self.emit("    prop |= (uint8_t)(slot & 0x07);")
            self.emit("    if (slot & 1) prop |= S_PALETTE;")
            self.emit("    set_sprite_prop(nb, prop);")
            self.emit("}")
        # bkg.set_palette: per-tile background palette (has_tile_palettes only).
        if self.caps['has_tile_palettes']:
            if cgb_class:
                self.emit("/* bkg.set_palette: fill a map rectangle in the CGB attribute map")
                self.emit("   (VRAM bank 1). Coordinates wrap mod 32 like set_bkg_tiles. */")
                self.emit("void gbs_bkg_palette_fill(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t slot) {")
                self.emit("    uint8_t i, j;")
                self.emit("    slot &= 7;")
                self.emit("    VBK_REG = VBK_ATTRIBUTES;")
                self.emit("    for (j = 0; j < h; ++j)")
                self.emit("        for (i = 0; i < w; ++i)")
                self.emit("            set_bkg_tile_xy((uint8_t)((x + i) & 31), (uint8_t)((y + j) & 31), slot);")
                self.emit("    VBK_REG = VBK_TILES;")
                self.emit("}")
            else:  # nes
                self.emit("/* bkg.set_palette: NES attribute granularity is 16x16 px (2x2 tiles);")
                self.emit("   the rectangle is rounded outward to whole attribute cells. */")
                self.emit("void gbs_bkg_palette_fill(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t slot) {")
                self.emit("    uint8_t a = (uint8_t)((slot & 3) * 0x55);  /* all four quadrants */")
                self.emit("    uint8_t cx, cy;")
                self.emit("    if (w == 0 || h == 0) return;")
                self.emit("    for (cy = (uint8_t)(y >> 1); cy <= (uint8_t)((y + h - 1) >> 1); ++cy)")
                self.emit("        for (cx = (uint8_t)(x >> 1); cx <= (uint8_t)((x + w - 1) >> 1); ++cx)")
                self.emit("            set_bkg_attributes_nes16x16(cx, cy, 1, 1, &a);")
                self.emit("}")

    def _emit_gbdk_sound(self):
        """platform.sound for GBDK consoles: one square-wave beep channel.

        sound.beep(freq_hz, frames) starts a tone; the duration counts down in
        gbs_wait_vblank (60 ticks/s, so `frames` matches the wait_vblank pacing
        on every console; 0 = play until sound.stop()). The tone generator
        differs per family: Game Boy APU pulse channel 2 (the NR*_REG symbols
        resolve per console in GBDK's library -- the Mega Duck remap included),
        the SN76489 PSG on SMS/Game Gear, and APU pulse 1 on the NES.
        """
        self.emit("/* platform.sound: one square-wave beep channel. */")
        self.emit("static uint16_t gbs_snd_frames = 0;")
        if self.caps['has_gb_regs']:
            # Mega Duck quirk: the volume-envelope registers (NR12/NR22/NR42)
            # have their nibbles swapped relative to the Game Boy.
            envelope = '0x0F' if self.platform == 'megaduck' else '0xF0'
            self.emit("/* Game Boy APU, pulse channel 2 (no sweep). Powering the APU off")
            self.emit("   clears every register, so stop() is a single write. */")
            self.emit("void gbs_sound_stop(void) { NR52_REG = 0x00; gbs_snd_frames = 0; }")
            self.emit("void gbs_sound_beep(uint16_t freq, uint16_t frames) {")
            self.emit("    uint16_t period;")
            self.emit("    if (freq < 64) freq = 64;  /* APU floor: period must be >= 0 */")
            self.emit("    period = (uint16_t)(2048 - (uint16_t)(131072UL / freq));")
            self.emit("    NR52_REG = 0x80;  /* APU on */")
            self.emit("    NR51_REG = 0xFF;  /* route every channel left + right */")
            self.emit("    NR50_REG = 0x77;  /* master volume max */")
            self.emit("    NR21_REG = 0x80;  /* 50% duty */")
            self.emit("    NR22_REG = %s;  /* full volume, no envelope */" % envelope)
            self.emit("    NR23_REG = (uint8_t)period;")
            self.emit("    NR24_REG = 0x80 | (uint8_t)(period >> 8);  /* trigger */")
            self.emit("    gbs_snd_frames = frames;")
            self.emit("}")
        elif self.platform in ('sms', 'gamegear'):
            self.emit("/* SN76489 PSG, tone channel 0 (latch/data bytes on the PSG port). */")
            self.emit("void gbs_sound_stop(void) {")
            self.emit("    PSG = PSG_LATCH | PSG_CH0 | PSG_VOLUME | 0x0F;  /* attenuation max */")
            self.emit("    gbs_snd_frames = 0;")
            self.emit("}")
            self.emit("void gbs_sound_beep(uint16_t freq, uint16_t frames) {")
            self.emit("    uint16_t divider;")
            self.emit("    if (freq < 110) freq = 110;  /* divider must fit 10 bits */")
            self.emit("    divider = (uint16_t)(111861UL / freq);  /* 3.579545 MHz / 32 / f */")
            if self.platform == 'gamegear':
                self.emit("    GG_SOUND_PAN = 0xFF;  /* all channels to both ears */")
            self.emit("    PSG = (uint8_t)(PSG_LATCH | PSG_CH0 | (divider & 0x0F));")
            self.emit("    PSG = (uint8_t)((divider >> 4) & 0x3F);")
            self.emit("    PSG = PSG_LATCH | PSG_CH0 | PSG_VOLUME | 0x00;  /* attenuation 0 = loudest */")
            self.emit("    gbs_snd_frames = frames;")
            self.emit("}")
        else:
            self.emit("/* NES APU, pulse channel 1. */")
            self.emit("void gbs_sound_stop(void) {")
            self.emit("    (*(volatile uint8_t *)0x4015) = 0x00;  /* silence all channels */")
            self.emit("    gbs_snd_frames = 0;")
            self.emit("}")
            self.emit("void gbs_sound_beep(uint16_t freq, uint16_t frames) {")
            self.emit("    uint16_t timer;")
            self.emit("    if (freq < 55) freq = 55;  /* timer must fit 11 bits */")
            self.emit("    timer = (uint16_t)(111861UL / freq) - 1;  /* 1.789773 MHz / 16 / f */")
            self.emit("    (*(volatile uint8_t *)0x4015) = 0x01;  /* enable pulse 1 */")
            self.emit("    (*(volatile uint8_t *)0x4000) = 0xBF;  /* 50% duty, no length, vol 15 */")
            self.emit("    (*(volatile uint8_t *)0x4001) = 0x08;  /* sweep off (negate: no mute) */")
            self.emit("    (*(volatile uint8_t *)0x4002) = (uint8_t)timer;")
            self.emit("    (*(volatile uint8_t *)0x4003) = (uint8_t)((timer >> 8) & 0x07);")
            self.emit("    gbs_snd_frames = frames;")
            self.emit("}")
        self.emit("/* wait_vblank with the beep-duration countdown (60 ticks/s). */")
        self.emit("void gbs_wait_vblank(void) {")
        self.emit("    vsync();")
        self.emit("    if (gbs_snd_frames && --gbs_snd_frames == 0) gbs_sound_stop();")
        self.emit("}")
