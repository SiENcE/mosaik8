"""cc65 backend: profiles, preludes, text + Suzy sprite engine (mixin)."""


class Cc65Backend:
    """cc65-specific codegen: stdlib maps, per-console profiles, and the
    prelude/text/sprite-engine emitters. Mixed into CodeGenerator.
    """

    # mosaik stdlib calls -> cc65 helper / library function names. The
    # portable Tier-1 surface (text, input, timing, raw hardware) maps to `gbs_`
    # helpers emitted by _emit_prelude_cc65; the helper *bodies* vary per console
    # profile (e.g. TGI vs conio text) but the call names do not. Tile/sprite/
    # window calls have no cc65 equivalent and are reported as unsupported (see
    # _gen_call).
    STDLIB_CALLS_CC65_CORE = {
        # Display lifecycle / frame presentation.
        ('video', 'enable_lcd'): 'gbs_video_init',
        ('video', 'disable_lcd'): 'gbs_video_done',
        ('video', 'wait_vblank'): 'gbs_present',
        # Input.
        ('input', 'pressed'): 'gbs_input_pressed',
        ('input', 'held'): 'gbs_input_pressed',
        # Text (character-cell coords; TGI profiles scale to pixels).
        ('text', 'print_string'): 'gbs_print_string',
        ('text', 'print_number'): 'gbs_print_number',
        ('text', 'clear_area'): 'gbs_clear_area',
        # Raw hardware access.
        ('hw', 'write'): 'gbs_hw_write',
        ('hw', 'read'): 'gbs_hw_read',
        # System utilities.
        ('system', 'delay'): 'gbs_delay',
        ('system', 'random'): 'rand',
        ('system', 'seed_random'): 'gbs_seed_random',
    }

    # Vector/framebuffer drawing (graphics.draw, TGI). Only available on cc65
    # consoles whose profile has a TGI driver (e.g. Lynx, not the tile-based
    # PC Engine).
    STDLIB_CALLS_CC65_DRAW = {
        ('draw', 'clear'): 'tgi_clear',
        ('draw', 'set_color'): 'tgi_setcolor',
        ('draw', 'pixel'): 'tgi_setpixel',
        ('draw', 'line'): 'tgi_line',
        ('draw', 'bar'): 'tgi_bar',
        ('draw', 'circle'): 'tgi_circle',
        ('draw', 'present'): 'tgi_updatedisplay',
    }

    # Hardware-style sprites (graphics.sprite + the sprite-visibility video
    # toggles). On framebuffer consoles these are backed by a software OAM
    # engine (8x8, Game Boy 2bpp tile data) that is re-blitted on each
    # gbs_present(); only available on profiles with `has_sprites`.
    STDLIB_CALLS_CC65_SPRITE = {
        ('sprite', 'set_data'): 'gbs_set_sprite_data',
        ('sprite', 'set_tile'): 'gbs_set_sprite_tile',
        ('sprite', 'get_tile'): 'gbs_get_sprite_tile',
        ('sprite', 'set_prop'): 'gbs_set_sprite_prop',
        ('sprite', 'move'): 'gbs_move_sprite',
        ('video', 'show_sprites'): 'gbs_show_sprites',
        ('video', 'hide_sprites'): 'gbs_hide_sprites',
        ('video', 'show_background'): 'gbs_show_bkg',
    }

    # Per-console cc65 profile. Describes how the shared cc65 prelude specialises
    # for a target: headers, text backend ('tgi' = pixel coords via
    # tgi_outtextxy, 'conio' = character cells via gotoxy/cputs), the driver
    # init/teardown sequence, the frame-present call, and the screen geometry
    # (screen_w/h in pixels, screen_cols/rows in text cells -> the SCREEN_*
    # prelude constants). Which stdlib *capabilities* a console has (sprites,
    # draw, ...) lives in PLATFORM_CAPS, not here. Adding a cc65 console is a
    # new entry here plus a PLATFORM_CAPS row and a mosaik8.py
    # PLATFORM_TARGETS row.
    CC65_PROFILES = {
        'lynx': {
            'headers': ['tgi.h', 'lynx.h', 'joystick.h', '6502.h', 'time.h',
                        'stdlib.h', 'string.h', 'stdint.h'],
            'text': 'tgi',
            'cell_w': 8, 'cell_h': 8,
            # The Lynx TGI is an interrupt-driven dual-buffer device: CLI()
            # enables the IRQs it needs and tgi_setframerate() programs the
            # display refresh that tgi_updatedisplay() syncs to. We start in
            # single-buffer mode (draw page == view page) so immediate drawing
            # (text) is shown and persists without flipping; the sprite engine
            # switches to true double-buffering lazily (see the present helper).
            # 60 Hz (not the Lynx-classic 75) so wait_vblank paces programs at
            # the same rate as the Game Boy and "60 frames = 1 second" holds.
            'video_init': ['tgi_install(tgi_static_stddrv);', 'tgi_init();',
                           'CLI();',
                           'joy_install(joy_static_stddrv);',
                           'tgi_setpalette(tgi_getdefpalette());',
                           'tgi_setframerate(60);',
                           'tgi_setviewpage(0);', 'tgi_setdrawpage(0);',
                           'tgi_setcolor(COLOR_WHITE);', 'tgi_clear();'],
            'video_done': 'joy_uninstall(); tgi_uninstall();',
            'present': 'tgi_updatedisplay();',
            'text_fg': 'COLOR_WHITE', 'text_bg': 'COLOR_BLACK',
            'screen_w': 160, 'screen_h': 102,
            'screen_cols': 20, 'screen_rows': 12,
            'input_start': '0', 'input_select': '0',
        },
        'pce': {
            'headers': ['pce.h', 'conio.h', 'joystick.h', 'time.h', 'stdlib.h', 'stdint.h'],
            'text': 'conio',
            'video_init': ['joy_install(joy_static_stddrv);', 'clrscr();'],
            'video_done': 'joy_uninstall();',
            'present': '',
            # The conio map is 64x32 virtual; this is the visible safe area a
            # portable program should target (256x224 px display).
            'screen_w': 256, 'screen_h': 224,
            'screen_cols': 32, 'screen_rows': 28,
            'input_start': 'JOY_RUN_MASK', 'input_select': 'JOY_SELECT_MASK',
        },
    }

    # Capacity of the cc65 sprite engine's converted-tile table (see
    # _emit_cc65_sprite_engine). Asset tile data beyond this cannot be
    # addressed by sprite.set_data on cc65 sprite consoles.
    CC65_MAX_TILES = 32

    def _emit_prelude_cc65(self):
        """Prelude for cc65 consoles, specialised by the active CC65 profile.

        Two text backends are supported: 'tgi' (pixel-addressed, e.g. Atari
        Lynx, following the bundled samples/lynx idiom) and 'conio'
        (character-cell, e.g. PC Engine). cc65 provides <stdint.h>, so the
        uintN_t spellings used by the shared codegen are valid here too.
        """
        prof = self.cc65_profile or self.CC65_PROFILES['lynx']
        is_tgi = prof['text'] == 'tgi'

        self.emit("/* Generated by mosaik -> cc65 C backend */")
        self.emit("/* Target console: %s (cc65 %s-text profile) */"
                  % (self.platform, prof['text']))
        for header in prof['headers']:
            self.emit("#include <%s>" % header)
        self.emit("")
        if is_tgi:
            self.emit("/* Text is addressed in character cells (as on Game Boy);")
            self.emit("   TGI profiles scale cell coords to pixels. */")
            self.emit("#define GBS_CELL_W %d" % prof.get('cell_w', 8))
            self.emit("#define GBS_CELL_H %d" % prof.get('cell_h', 8))
            self.emit("")
        self.emit("/* Screen geometry for the build target. */")
        self.emit("#define SCREEN_WIDTH  %d" % prof.get('screen_w', 160))
        self.emit("#define SCREEN_HEIGHT %d" % prof.get('screen_h', 102))
        self.emit("#define SCREEN_COLS   %d" % prof.get('screen_cols', 20))
        self.emit("#define SCREEN_ROWS   %d" % prof.get('screen_rows', 12))
        self.emit("")
        self.emit("/* Input button constants mapped to this console's joypad bits. */")
        self.emit("#define INPUT_A      JOY_BTN_1_MASK")
        self.emit("#define INPUT_B      JOY_BTN_2_MASK")
        self.emit("#define INPUT_SELECT %s" % prof.get('input_select', '0'))
        self.emit("#define INPUT_START  %s" % prof.get('input_start', '0'))
        self.emit("#define INPUT_RIGHT  JOY_RIGHT_MASK")
        self.emit("#define INPUT_LEFT   JOY_LEFT_MASK")
        self.emit("#define INPUT_UP     JOY_UP_MASK")
        self.emit("#define INPUT_DOWN   JOY_DOWN_MASK")
        self.emit("")
        if self.caps['has_sprites']:
            self.emit("/* Sprite flip flags -> Suzy SPRCTL0 bits (graphics.sprite). */")
            self.emit("#define FLIP_X HFLIP")
            self.emit("#define FLIP_Y VFLIP")
        else:
            self.emit("/* Sprite flip flags are unused here; defined so shared code links. */")
            self.emit("#define FLIP_X 0")
            self.emit("#define FLIP_Y 0")
        self.emit("")
        self.emit("/* mosaik standard library helpers (cc65) */")
        self.emit("static uint8_t gbs_video_ready = 0;")
        self.emit("void gbs_video_init(void) {")
        self.emit("    if (gbs_video_ready) return;")
        for stmt in prof['video_init']:
            self.emit("    %s" % stmt)
        self.emit("    gbs_video_ready = 1;")
        self.emit("}")
        self.emit("void gbs_video_done(void) { %s }" % prof['video_done'])
        if self.caps['has_sprites']:
            self._emit_cc65_sprite_engine(prof)
        else:
            self.emit("void gbs_present(void) { %s }" % prof['present'])
        self.emit("uint8_t gbs_input_pressed(uint8_t button) {")
        self.emit("    gbs_video_init();")
        self.emit("    return (uint8_t)(joy_read(0) & button);")
        self.emit("}")
        if is_tgi:
            self._emit_cc65_text_tgi()
        else:
            self._emit_cc65_text_conio()
        self.emit("/* delay(ms) busy-waits using the system clock (CLOCKS_PER_SEC ticks/s). */")
        self.emit("void gbs_delay(uint16_t ms) {")
        self.emit("    clock_t target = clock() + (clock_t)ms * CLOCKS_PER_SEC / 1000;")
        self.emit("    while (clock() < target) { }")
        self.emit("}")
        self.emit("void gbs_seed_random(uint16_t seed) { srand(seed); }")
        self.emit("/* Raw hardware register access (addresses are console-specific). */")
        self.emit("void gbs_hw_write(uint16_t addr, uint8_t value) { *(volatile uint8_t *)addr = value; }")
        self.emit("uint8_t gbs_hw_read(uint16_t addr) { return *(volatile uint8_t *)addr; }")
        self.emit("")

    def _emit_cc65_text_tgi(self):
        """Text helpers for TGI consoles (pixel-addressed, e.g. Lynx)."""
        prof = self.cc65_profile
        fg, bg = prof.get('text_fg', 'COLOR_WHITE'), prof.get('text_bg', 'COLOR_BLACK')
        self.emit("/* TGI text draws transparently (pixels OR onto the screen), but the")
        self.emit("   Game Boy's tile text *replaces* the cell -- so clear the covered")
        self.emit("   cells first to keep reprinting (counters, scores) portable. */")
        self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s) {")
        self.emit("    int px = (int)x * GBS_CELL_W, py = (int)y * GBS_CELL_H;")
        self.emit("    gbs_video_init();")
        self.emit("    tgi_setcolor(%s);" % bg)
        self.emit("    tgi_bar(px, py, px + (int)strlen(s) * GBS_CELL_W - 1, py + GBS_CELL_H - 1);")
        self.emit("    tgi_setcolor(%s);" % fg)
        self.emit("    tgi_outtextxy(px, py, s);")
        self.emit("}")
        self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n) {")
        self.emit("    char buf[7];")
        self.emit("    utoa(n, buf, 10);")
        self.emit("    gbs_print_string(x, y, buf);")
        self.emit("}")
        self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {")
        self.emit("    gbs_video_init();")
        self.emit("    tgi_setcolor(%s);" % bg)
        self.emit("    tgi_bar((int)x * GBS_CELL_W, (int)y * GBS_CELL_H,")
        self.emit("            (int)(x + w) * GBS_CELL_W - 1, (int)(y + h) * GBS_CELL_H - 1);")
        self.emit("    tgi_setcolor(%s);" % fg)
        self.emit("}")

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
        # Bytes per converted tile: 8 rows * (offset + 2 data + pad) + terminator.
        tile_bytes = 8 * 4 + 1
        self.emit("/* --- Suzy hardware sprite engine (Atari Lynx) --- */")
        self.emit("#define GBS_MAX_TILES   %d" % self.CC65_MAX_TILES)
        self.emit("#define GBS_MAX_SPRITES 40")
        self.emit("#define GBS_TILE_BYTES  %d  /* literal-encoded 8x8 2bpp tile */" % tile_bytes)
        self.emit("static uint8_t gbs_tiles[GBS_MAX_TILES][GBS_TILE_BYTES];")
        self.emit("static SCB_REHV_PAL gbs_scb[GBS_MAX_SPRITES];")
        self.emit("static uint8_t gbs_spr_tile[GBS_MAX_SPRITES];")
        self.emit("static uint8_t gbs_spr_max = 0;     /* highest slot touched + 1 */")
        self.emit("static uint8_t gbs_spr_used = 0;    /* engine active this program */")
        self.emit("static uint8_t gbs_spr_visible = 1;")
        self.emit("static uint8_t gbs_spr_db = 0;      /* double-buffering engaged */")
        self.emit("static uint8_t gbs_spr_inited = 0;")
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
        self.emit("    for (s = 0; s < GBS_MAX_SPRITES; ++s) {")
        self.emit("        gbs_scb[s].sprctl0 = BPP_2 | TYPE_NORMAL;")
        self.emit("        gbs_scb[s].sprctl1 = LITERAL | REHV;  /* literal data, reload h/v size */")
        self.emit("        gbs_scb[s].sprcoll = 0;")
        self.emit("        gbs_scb[s].next = (char *)0;")
        self.emit("        gbs_scb[s].data = gbs_tiles[0];       /* default to tile 0 (as on GB) */")
        self.emit("        gbs_scb[s].hpos = -16; gbs_scb[s].vpos = -16;")
        self.emit("        gbs_scb[s].hsize = 0x100; gbs_scb[s].vsize = 0x100;  /* 1:1 scale */")
        self.emit("        for (i = 0; i < 8; ++i) gbs_scb[s].penpal[i] = gbs_spr_penpal[i];")
        self.emit("    }")
        self.emit("    gbs_spr_inited = 1;")
        self.emit("}")
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
        self.emit("void gbs_present(void) {")
        self.emit("    uint8_t s;")
        self.emit("    /* No sprites in use: stay single-buffered so immediate")
        self.emit("       (text) drawing persists and does not flicker -- but still")
        self.emit("       wait out the frame so wait_vblank paces the main loop.")
        self.emit("       The Lynx clock() ticks once per display frame (Mikey")
        self.emit("       timer 2, the VBL timer), so CLOCKS_PER_SEC == framerate. */")
        self.emit("    if (!gbs_spr_used) {")
        self.emit("        clock_t t = clock();")
        self.emit("        while (clock() == t) { }")
        self.emit("        return;")
        self.emit("    }")
        self.emit("    /* First sprite frame: switch to true double-buffering so the")
        self.emit("       per-frame clear+redraw happens off-screen. */")
        self.emit("    if (!gbs_spr_db) { tgi_setdrawpage(1); gbs_spr_db = 1; }")
        self.emit("    tgi_setcolor(COLOR_BLACK);")
        self.emit("    tgi_bar(0, 0, %d, %d);" % (sw - 1, sh - 1))
        self.emit("    if (gbs_spr_visible)")
        self.emit("        for (s = 0; s < gbs_spr_max; ++s) tgi_sprite(&gbs_scb[s]);")
        self.emit("    while (tgi_busy()) { }  /* let Suzy finish before the flip */")
        self.emit("    tgi_updatedisplay();    /* VBL-synced flip (draw <-> view) */")
        self.emit("}")
        self.emit("void gbs_set_sprite_data(uint8_t first, uint8_t count, const uint8_t *data) {")
        self.emit("    uint8_t i;")
        self.emit("    gbs_spr_init();")
        self.emit("    for (i = 0; i < count; ++i)")
        self.emit("        if ((uint8_t)(first + i) < GBS_MAX_TILES)")
        self.emit("            gbs_conv_tile((uint8_t)(first + i), data + (uint16_t)i * 16);")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        self.emit("void gbs_set_sprite_tile(uint8_t nb, uint8_t tile) {")
        self.emit("    gbs_spr_init();")
        self.emit("    if (nb < GBS_MAX_SPRITES && tile < GBS_MAX_TILES) {")
        self.emit("        gbs_spr_tile[nb] = tile;")
        self.emit("        gbs_scb[nb].data = gbs_tiles[tile];")
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
        self.emit("    if (nb < GBS_MAX_SPRITES)")
        self.emit("        gbs_scb[nb].sprctl0 = (uint8_t)((BPP_2 | TYPE_NORMAL) |")
        self.emit("            (prop & (HFLIP | VFLIP)));")
        self.emit("}")
        self.emit("/* sprite.move takes screen-pixel coordinates (top-left origin). */")
        self.emit("void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y) {")
        self.emit("    gbs_spr_init();")
        self.emit("    if (nb < GBS_MAX_SPRITES) {")
        self.emit("        gbs_scb[nb].hpos = x;")
        self.emit("        gbs_scb[nb].vpos = y;")
        self.emit("        if (nb >= gbs_spr_max) gbs_spr_max = nb + 1;")
        self.emit("    }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        self.emit("void gbs_show_sprites(void) { gbs_spr_used = 1; gbs_spr_visible = 1; }")
        self.emit("void gbs_hide_sprites(void) { gbs_spr_visible = 0; }")
        self.emit("void gbs_show_bkg(void) { }")

    def _emit_cc65_text_conio(self):
        """Text helpers for conio consoles (character-cell, e.g. PC Engine)."""
        self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s) {")
        self.emit("    gbs_video_init();")
        self.emit("    gotoxy(x, y); cputs(s);")
        self.emit("}")
        self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n) {")
        self.emit("    char buf[7];")
        self.emit("    gbs_video_init();")
        self.emit("    utoa(n, buf, 10);")
        self.emit("    gotoxy(x, y); cputs(buf);")
        self.emit("}")
        self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {")
        self.emit("    uint8_t j;")
        self.emit("    gbs_video_init();")
        self.emit("    for (j = 0; j < h; j++) cclearxy(x, y + j, w);")
        self.emit("}")
