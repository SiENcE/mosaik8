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
        # Sound (platform.sound): one square-wave beep channel.
        ('sound', 'beep'): 'gbs_sound_beep',
        ('sound', 'stop'): 'gbs_sound_stop',
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
        ('sprite', 'set_meta'): 'gbs_set_metasprite',
        ('sprite', 'move'): 'gbs_move_sprite',
        ('video', 'show_sprites'): 'gbs_show_sprites',
        ('video', 'hide_sprites'): 'gbs_hide_sprites',
        ('video', 'show_background'): 'gbs_show_bkg',
    }

    # Scrollable background tilemap (graphics.bkg), for has_bkg consoles.
    # The PC Engine has a real one (the VDC BAT plus the BXR/BYR scroll
    # registers); the Lynx has no tilemap hardware, so its engine composites
    # the 32x32 map into one large literal Suzy background sprite that
    # gbs_present() re-blits with wrapped offsets -- the classic Lynx
    # big-background-sprite technique (cf. Running Knight's scrolling floor).
    # The engines are emitted only when the program imports graphics.bkg
    # (the Lynx one costs ~21 KB of RAM).
    STDLIB_CALLS_CC65_BKG = {
        ('bkg', 'set_data'): 'gbs_set_bkg_data',
        ('bkg', 'set_tiles'): 'gbs_set_bkg_tiles',
        ('bkg', 'scroll'): 'gbs_scroll_bkg',
        ('bkg', 'move'): 'gbs_move_bkg',
    }

    # Palettes (graphics.palette): the 4-color GB-model palette slots on the
    # cc65 consoles -- the Lynx partitions its single 16-pen hardware palette
    # (see _emit_lynx_palette_core), the PCE writes VCE color RAM. Same call
    # names as the GBDK backend; the helpers are emitted only when the
    # program imports graphics.palette. sprite.set_palette is gated by
    # has_sprites, bkg.set_palette by has_tile_palettes (PCE only -- the
    # Lynx composite background has a single penpal).
    STDLIB_CALLS_CC65_PALETTE = {
        ('palette', 'rgb'): 'gbs_rgb',
        ('palette', 'set_bkg'): 'gbs_set_bkg_palette',
        ('palette', 'set_sprite'): 'gbs_set_spr_palette',
        ('palette', 'load_bkg'): 'gbs_load_bkg_palette',
        ('palette', 'load_sprite'): 'gbs_load_spr_palette',
        ('sprite', 'set_palette'): 'gbs_sprite_palette',
        ('bkg', 'set_palette'): 'gbs_bkg_palette_fill',
    }

    # Per-console cc65 profile. Describes how the shared cc65 prelude specialises
    # for a target: headers, text backend ('tgi' = pixel coords via
    # tgi_outtextxy, 'conio' = character cells via gotoxy/cputs), the driver
    # init/teardown sequence, the frame-present call, the screen geometry
    # (screen_w/h in pixels, screen_cols/rows in text cells -> the SCREEN_*
    # prelude constants), and which hardware blocks back the sprite engine
    # ('sprites': 'suzy' = Lynx blitter, 'vdc' = PC Engine VDC/SATB) and the
    # beep channel ('sound': 'mikey' or 'pce_psg'). Whether a console *has*
    # sprites/draw/sound at all lives in PLATFORM_CAPS, not here. Adding a
    # cc65 console is a new entry here plus a PLATFORM_CAPS row and a
    # mosaik8.py PLATFORM_TARGETS row.
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
            'sprites': 'suzy', 'sound': 'mikey',
        },
        'pce': {
            'headers': ['pce.h', 'conio.h', 'joystick.h', 'time.h', 'stdlib.h', 'stdint.h'],
            'text': 'conio',
            # The cc65 conio runtime brings the VDC/VCE up in 512-px-wide
            # display mode (VCE 10.7 MHz dot clock + a 64-tile VDC display
            # window), so 256-px-period content -- a SCREEN_WIDTH=256 portable
            # program, or the replicated bkg map -- showed twice side by side.
            # Switch to the standard PC Engine 256-px mode: VCE dot clock to
            # 5.37 MHz and the VDC Horizontal Display Register (R11) to 32
            # tiles, centred by the matching Horizontal Sync Register (R10).
            # Now one 256-px scene fills the whole raster (no doubling, no
            # overscan border). The 64-wide BAT (VDC R9/MWR) is left alone:
            # the bkg engine still uses the off-screen half for seamless u8
            # scroll wrap.
            'video_init': ['joy_install(joy_static_stddrv);', 'clrscr();',
                           '*(volatile unsigned char *)0x0400 = 0x00;  /* VCE: 5.37 MHz dot clock (256px) */',
                           '*(volatile unsigned char *)0x0200 = 10;    /* VDC R10 (HSR) */',
                           '*(volatile unsigned char *)0x0202 = 0x02;',
                           '*(volatile unsigned char *)0x0203 = 0x02;',
                           '*(volatile unsigned char *)0x0200 = 11;    /* VDC R11 (HDR): 32 tiles = 256px */',
                           '*(volatile unsigned char *)0x0202 = 0x1F;',
                           '*(volatile unsigned char *)0x0203 = 0x04;'],
            'video_done': 'joy_uninstall();',
            'present': 'waitvsync();',
            # The conio map is 64x32 virtual; this is the visible safe area a
            # portable program should target (256x224 px display).
            'screen_w': 256, 'screen_h': 224,
            'screen_cols': 32, 'screen_rows': 28,
            'input_start': 'JOY_RUN_MASK', 'input_select': 'JOY_SELECT_MASK',
            'sprites': 'vdc', 'sound': 'pce_psg',
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
        sprite_engine = prof.get('sprites') if self.caps['has_sprites'] else None
        if sprite_engine == 'suzy':
            self.emit("/* Sprite flip flags -> Suzy SPRCTL0 bits (graphics.sprite). */")
            self.emit("#define FLIP_X HFLIP")
            self.emit("#define FLIP_Y VFLIP")
        elif sprite_engine == 'vdc':
            self.emit("/* Sprite flip flags; gbs_set_sprite_prop translates them to the")
            self.emit("   VDC sprite-attribute X/Y-invert bits. */")
            self.emit("#define FLIP_X 0x01")
            self.emit("#define FLIP_Y 0x02")
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
        self._emit_cc65_sound(prof)
        emit_bkg = self.caps['has_bkg'] and self.cc65_bkg_imported
        if sprite_engine == 'suzy':
            if self.palette_imported:
                # Pen partition + setters first: the engines' init code
                # applies the grey-ramp pen defaults via gbs_pal_init().
                self._emit_lynx_palette_core()
            if emit_bkg:
                # State + builders first: the sprite engine's gbs_present()
                # blits the composited background before the sprite slots.
                self._emit_cc65_bkg_engine(prof)
            self._emit_cc65_sprite_engine(prof)
            if self.palette_imported:
                # sprite.set_palette needs the engine's SCBs, so it comes last.
                self._emit_lynx_sprite_palette()
        elif sprite_engine == 'vdc':
            self._emit_pce_sprite_engine(prof)
            if emit_bkg:
                # After the sprite engine: reuses its gbs_vreg/gbs_vram_addr.
                self._emit_pce_bkg_engine(prof)
            if self.palette_imported:
                # After both engines: the setters init them first so a later
                # engine init cannot clobber user colors with the grey ramp.
                self._emit_pce_palette(emit_bkg)
        else:
            self.emit("void gbs_present(void) {")
            if self.caps['has_sound']:
                self.emit("    if (gbs_snd_frames && --gbs_snd_frames == 0) gbs_sound_stop();")
            if prof['present']:
                self.emit("    %s" % prof['present'])
            self.emit("}")
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
        self.emit("   cells first to keep reprinting (counters, scores) portable. */")
        self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s) {")
        self.emit("    int px = (int)x * GBS_CELL_W, py = (int)y * GBS_CELL_H;")
        self.emit("    %s" % init)
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
        self.emit("    %s" % init)
        self.emit("    tgi_setcolor(%s);" % bg)
        self.emit("    tgi_bar((int)x * GBS_CELL_W, (int)y * GBS_CELL_H,")
        self.emit("            (int)(x + w) * GBS_CELL_W - 1, (int)(y + h) * GBS_CELL_H - 1);")
        self.emit("    tgi_setcolor(%s);" % fg)
        self.emit("}")

    def _emit_cc65_sound(self, prof):
        """platform.sound for cc65 consoles: one square-wave beep channel.

        Same contract as the GBDK backend: sound.beep(freq_hz, frames) starts
        a tone and the duration counts down in gbs_present (wait_vblank, 60
        ticks/s; 0 = play until sound.stop()). The generator is the Mikey
        audio chip on the Lynx and the PSG on the PC Engine.
        """
        if not self.caps['has_sound']:
            return
        self.emit("/* platform.sound: one square-wave beep channel (counted down")
        self.emit("   in gbs_present, 60 ticks/s). */")
        self.emit("static uint16_t gbs_snd_frames = 0;")
        if prof.get('sound') == 'mikey':
            self.emit("/* Mikey audio channel A. Feedback tap 0 through the inverting XOR")
            self.emit("   makes the shift register alternate on every timer underflow ->")
            self.emit("   a square wave at clock / (2 * (backup + 1)). */")
            self.emit("void gbs_sound_stop(void) {")
            self.emit("    MIKEY.channel_a.control = 0;")
            self.emit("    MIKEY.channel_a.volume = 0;")
            self.emit("    gbs_snd_frames = 0;")
            self.emit("}")
            self.emit("void gbs_sound_beep(uint16_t freq, uint16_t frames) {")
            self.emit("    uint32_t half;       /* half-period in 1 MHz base-clock ticks */")
            self.emit("    uint8_t sel = AUD_1;")
            self.emit("    if (freq == 0) freq = 1;")
            self.emit("    half = 500000UL / freq;")
            self.emit("    while (half > 256 && sel < AUD_64) { half >>= 1; ++sel; }")
            self.emit("    if (half) --half;    /* timer counts backup+1 ticks */")
            self.emit("    MIKEY.channel_a.volume = 0x7F;")
            self.emit("    MIKEY.channel_a.feedback = 0x01;  /* tap 0 only */")
            self.emit("    MIKEY.channel_a.dac = 0;")
            self.emit("    MIKEY.channel_a.shiftlo = 0x01;   /* seed the shift register */")
            self.emit("    MIKEY.channel_a.other = 0;")
            self.emit("    MIKEY.channel_a.reload = (uint8_t)half;")
            self.emit("    MIKEY.channel_a.count = (uint8_t)half;")
            self.emit("    MIKEY.channel_a.control = (uint8_t)(ENABLE_RELOAD | ENABLE_COUNT | sel);")
            self.emit("    MIKEY.mstereo = 0;   /* Lynx II: all channels to both ears */")
            self.emit("    gbs_snd_frames = frames;")
            self.emit("}")
        else:  # 'pce_psg'
            self.emit("/* PC Engine PSG channel 0: load a square waveform once, then key on")
            self.emit("   with a 12-bit frequency divider (3.579545 MHz / 32 / f). The cc65")
            self.emit("   startup silences the PSG master balance; beep restores it. */")
            self.emit("#define GBS_PSG(reg) (*(volatile uint8_t *)(0x0800u + (reg)))")
            self.emit("static uint8_t gbs_psg_wave_loaded = 0;")
            self.emit("void gbs_sound_stop(void) {")
            self.emit("    GBS_PSG(0) = 0;  /* select channel 0 */")
            self.emit("    GBS_PSG(4) = 0;  /* key off, volume 0 */")
            self.emit("    gbs_snd_frames = 0;")
            self.emit("}")
            self.emit("void gbs_sound_beep(uint16_t freq, uint16_t frames) {")
            self.emit("    uint16_t divider;")
            self.emit("    uint8_t i;")
            self.emit("    if (freq < 28) freq = 28;  /* divider must fit 12 bits */")
            self.emit("    divider = (uint16_t)(111861UL / freq);")
            self.emit("    GBS_PSG(1) = 0xFF;  /* main volume left + right */")
            self.emit("    GBS_PSG(0) = 0;     /* select channel 0 */")
            self.emit("    if (!gbs_psg_wave_loaded) {")
            self.emit("        GBS_PSG(4) = 0x40;  /* DDA on... */")
            self.emit("        GBS_PSG(4) = 0x00;  /* ...and off: reset the waveform index */")
            self.emit("        for (i = 0; i < 32; ++i) GBS_PSG(6) = (i < 16) ? 0x00 : 0x1F;")
            self.emit("        gbs_psg_wave_loaded = 1;")
            self.emit("    }")
            self.emit("    GBS_PSG(5) = 0xFF;  /* channel balance left + right */")
            self.emit("    GBS_PSG(2) = (uint8_t)divider;")
            self.emit("    GBS_PSG(3) = (uint8_t)((divider >> 8) & 0x0F);")
            self.emit("    GBS_PSG(4) = 0x9F;  /* key on, volume max */")
            self.emit("    gbs_snd_frames = frames;")
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

    def _emit_lynx_sprite_palette(self):
        """sprite.set_palette on the Lynx: repoint a slot's SCB pen triple."""
        self.emit("/* sprite.set_palette: repoint the sprite's SCB at the slot's pens. */")
        self.emit("void gbs_sprite_palette(uint8_t nb, uint8_t slot) {")
        self.emit("    gbs_spr_init();")
        self.emit("    slot &= 3;")
        self.emit("    if (nb < GBS_MAX_SPRITES) {")
        self.emit("        gbs_scb[nb].penpal[0] = gbs_pal_penpal[slot][0];")
        self.emit("        gbs_scb[nb].penpal[1] = gbs_pal_penpal[slot][1];")
        self.emit("    }")
        self.emit("}")

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
        self.emit("/* sprite.set_palette -> SATB attribute bits 0-3 (sprite palette). */")
        self.emit("void gbs_sprite_palette(uint8_t nb, uint8_t slot) {")
        self.emit("    gbs_spr_init();")
        self.emit("    if (nb < GBS_MAX_SPRITES)")
        self.emit("        gbs_satb[(uint16_t)nb * 4 + 3] = (uint16_t)(")
        self.emit("            (gbs_satb[(uint16_t)nb * 4 + 3] & ~0x000Fu) | (slot & 3));")
        self.emit("}")

    def _emit_cc65_bkg_engine(self, prof):
        """Suzy background-tilemap engine for the Atari Lynx (graphics.bkg).

        The Lynx has no tilemap layer -- its video model is a framebuffer plus
        the Suzy sprite blitter, so "everything on screen is a sprite". The
        engine therefore keeps the Game Boy background *model* (a 256-entry
        8x8 2bpp tile table + a 32x32 tile map = a 256x256 px world that
        scrolls with u8 wrap-around) and implements it the way real Lynx games
        scroll (e.g. Running Knight's floor): the whole map is composited once
        into a single large *literal* Suzy sprite, and gbs_present() re-blits
        that one sprite with up to four wrapped offsets per frame, so the
        per-frame cost is a handful of large hardware blits instead of
        hundreds of tile blits. Suzy clips off-window lines/pixels in
        hardware, so the oversized sprite costs little.

        The composite is TYPE_BACKNONCOLL (a "background" sprite: pen 0 is
        painted, not transparent -- GB bkg colour 0 is a real colour) and maps
        GB colours 0..3 to black / light-grey / grey / white, matching the
        sprite engine's palette so sprites layered on top read consistently.
        Because the four wrapped copies tile the whole window, present skips
        its full-screen clear while the background is visible.

        Memory: the composite is 256 rows x (1 offset byte + 64 literal data
        bytes + 1 pad) + 1 terminator = 16897 bytes, plus a 4 KB packed tile
        table -- which is why this engine is only emitted for programs that
        import graphics.bkg.
        """
        self.emit("/* --- Suzy background-tilemap engine (Atari Lynx) --- */")
        self.emit("/* The Lynx has no tilemap layer; the 32x32 GB map is composited into")
        self.emit("   ONE large literal Suzy background sprite (256x256 px) that present")
        self.emit("   re-blits with wrapped offsets -- a few big hardware blits per frame")
        self.emit("   (the classic Lynx scrolling technique), clipped free by Suzy. */")
        self.emit("#define GBS_BKG_MAX_TILES 256")
        self.emit("#define GBS_BKG_ROW_BYTES 66  /* offset + 64 literal 2bpp bytes + pad */")
        self.emit("static uint8_t gbs_bkg_tileset[GBS_BKG_MAX_TILES][16];  /* packed rows */")
        self.emit("static uint8_t gbs_bkg_sprite[256u * GBS_BKG_ROW_BYTES + 1];")
        self.emit("static SCB_REHV_PAL gbs_bkg_scb[4];  /* up to 4 wrapped copies/frame */")
        self.emit("static uint8_t gbs_bkg_used = 0;     /* engine active this program */")
        self.emit("static uint8_t gbs_bkg_visible = 1;")
        self.emit("static uint8_t gbs_bkg_x = 0, gbs_bkg_y = 0;  /* scroll, wraps mod 256 */")
        self.emit("static uint8_t gbs_bkg_inited = 0;")
        self.emit("static void gbs_bkg_init(void) {")
        self.emit("    uint8_t *o = gbs_bkg_sprite;")
        self.emit("    uint16_t y;")
        self.emit("    uint8_t c, i;")
        self.emit("    if (gbs_bkg_inited) return;")
        if self.palette_imported:
            self.emit("    gbs_pal_init();  /* grey-ramp pen defaults */")
        self.emit("    /* Pre-write all 256 line records; set_tiles only fills the data. */")
        self.emit("    for (y = 0; y < 256; ++y) {")
        self.emit("        o[0] = GBS_BKG_ROW_BYTES;          /* offset to the next line */")
        self.emit("        o[GBS_BKG_ROW_BYTES - 1] = 0x00;   /* pad closes the line */")
        self.emit("        o += GBS_BKG_ROW_BYTES;")
        self.emit("    }")
        self.emit("    *o = 0x00;  /* end of sprite data */")
        self.emit("    for (c = 0; c < 4; ++c) {")
        self.emit("        /* A *background* sprite: pen 0 paints (GB bkg colour 0 is a")
        self.emit("           real colour, not transparent), no collision. */")
        self.emit("        gbs_bkg_scb[c].sprctl0 = BPP_2 | TYPE_BACKNONCOLL;")
        self.emit("        gbs_bkg_scb[c].sprctl1 = LITERAL | REHV;")
        self.emit("        gbs_bkg_scb[c].sprcoll = 0;")
        self.emit("        gbs_bkg_scb[c].next = (char *)0;")
        self.emit("        gbs_bkg_scb[c].data = gbs_bkg_sprite;")
        self.emit("        gbs_bkg_scb[c].hpos = 0; gbs_bkg_scb[c].vpos = 0;")
        self.emit("        gbs_bkg_scb[c].hsize = 0x100; gbs_bkg_scb[c].vsize = 0x100;")
        if self.palette_imported:
            self.emit("        /* Pen partition: pixel 0 -> pen 0 (bkg color 0, painted --")
            self.emit("           background type), pixels 1-3 -> pens 13-15. */")
            self.emit("        gbs_bkg_scb[c].penpal[0] = 0x0D;")
            self.emit("        gbs_bkg_scb[c].penpal[1] = 0xEF;")
        else:
            self.emit("        /* GB colours 0..3 -> black, light grey, grey, white (the same")
            self.emit("           ramp as the sprite engine, plus an opaque pen 0). */")
            self.emit("        gbs_bkg_scb[c].penpal[0] = (COLOR_BLACK << 4) | COLOR_LIGHTGREY;")
            self.emit("        gbs_bkg_scb[c].penpal[1] = (COLOR_GREY << 4) | COLOR_WHITE;")
        self.emit("        for (i = 2; i < 8; ++i) gbs_bkg_scb[c].penpal[i] = 0;")
        self.emit("    }")
        self.emit("    gbs_bkg_inited = 1;")
        self.emit("}")
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
        self.emit("    uint16_t t;")
        self.emit("    uint8_t row;")
        self.emit("    gbs_bkg_init();")
        self.emit("    for (t = 0; t < count; ++t) {")
        self.emit("        if ((uint16_t)first + t >= GBS_BKG_MAX_TILES) break;")
        self.emit("        for (row = 0; row < 8; ++row)")
        self.emit("            gbs_bkg_pack_row(data[t * 16 + row * 2],")
        self.emit("                             data[t * 16 + row * 2 + 1],")
        self.emit("                             &gbs_bkg_tileset[first + t][row * 2]);")
        self.emit("    }")
        self.emit("}")
        self.emit("/* GB semantics: map coords wrap mod 32; `tiles` is row-major w x h. */")
        self.emit("void gbs_set_bkg_tiles(uint8_t x, uint8_t y, uint8_t w, uint8_t h,")
        self.emit("                       const uint8_t *tiles) {")
        self.emit("    uint8_t cx, cy, row;")
        self.emit("    const uint8_t *src;")
        self.emit("    uint8_t *dst;")
        self.emit("    gbs_bkg_init();")
        self.emit("    for (cy = 0; cy < h; ++cy) {")
        self.emit("        for (cx = 0; cx < w; ++cx) {")
        self.emit("            src = gbs_bkg_tileset[tiles[(uint16_t)cy * w + cx]];")
        self.emit("            dst = gbs_bkg_sprite")
        self.emit("                + (uint16_t)((uint8_t)(y + cy) & 31) * (8u * GBS_BKG_ROW_BYTES)")
        self.emit("                + 1 + (((uint8_t)(x + cx) & 31) << 1);")
        self.emit("            for (row = 0; row < 8; ++row) {")
        self.emit("                dst[0] = src[0]; dst[1] = src[1];")
        self.emit("                src += 2; dst += GBS_BKG_ROW_BYTES;")
        self.emit("            }")
        self.emit("        }")
        self.emit("    }")
        self.emit("    gbs_bkg_used = 1;")
        self.emit("}")
        self.emit("void gbs_move_bkg(uint8_t x, uint8_t y) {")
        self.emit("    gbs_bkg_x = x; gbs_bkg_y = y;")
        self.emit("    gbs_bkg_used = 1;")
        self.emit("}")
        self.emit("void gbs_scroll_bkg(int8_t dx, int8_t dy) {")
        self.emit("    gbs_bkg_x = (uint8_t)(gbs_bkg_x + dx);")
        self.emit("    gbs_bkg_y = (uint8_t)(gbs_bkg_y + dy);")
        self.emit("    gbs_bkg_used = 1;")
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
        # When the program imports graphics.bkg, present() also blits the
        # composited background (emitted just above by _emit_cc65_bkg_engine).
        with_bkg = self.caps['has_bkg'] and self.cc65_bkg_imported
        # In palette mode the per-frame clear paints pen 0 = bkg color 0
        # (GB semantics: the backdrop is background color 0).
        clear_pen = '0' if self.palette_imported else 'COLOR_BLACK'
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
        if self.metasprite_used:
            self._emit_cc65_meta_state()
        if not self.palette_imported:
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
        if self.palette_imported:
            self.emit("    gbs_pal_init();  /* grey-ramp pen defaults */")
        self.emit("    for (s = 0; s < GBS_MAX_SPRITES; ++s) {")
        self.emit("        gbs_scb[s].sprctl0 = BPP_2 | TYPE_NORMAL;")
        self.emit("        gbs_scb[s].sprctl1 = LITERAL | REHV;  /* literal data, reload h/v size */")
        self.emit("        gbs_scb[s].sprcoll = 0;")
        self.emit("        gbs_scb[s].next = (char *)0;")
        self.emit("        gbs_scb[s].data = gbs_tiles[0];       /* default to tile 0 (as on GB) */")
        self.emit("        gbs_scb[s].hpos = -16; gbs_scb[s].vpos = -16;")
        self.emit("        gbs_scb[s].hsize = 0x100; gbs_scb[s].vsize = 0x100;  /* 1:1 scale */")
        if self.palette_imported:
            self.emit("        /* Default to sprite palette slot 0 (pens 1-3). */")
            self.emit("        gbs_scb[s].penpal[0] = gbs_pal_penpal[0][0];")
            self.emit("        gbs_scb[s].penpal[1] = gbs_pal_penpal[0][1];")
            self.emit("        for (i = 2; i < 8; ++i) gbs_scb[s].penpal[i] = 0;")
        else:
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
        self.emit("       per-frame clear+redraw happens off-screen. */")
        self.emit("    if (!gbs_spr_db) { tgi_setdrawpage(1); gbs_spr_db = 1; }")
        if with_bkg:
            self.emit("    if (gbs_bkg_used && gbs_bkg_visible) {")
            self.emit("        /* Blit the composited 256x256 background sprite; up to four")
            self.emit("           wrapped copies tile the whole window, and pen 0 paints")
            self.emit("           (background type), so no full-screen clear is needed.")
            self.emit("           Suzy clips the off-window parts in hardware. */")
            self.emit("        int px = -(int)gbs_bkg_x, py = -(int)gbs_bkg_y;")
            self.emit("        uint8_t n = 0;")
            self.emit("        gbs_bkg_scb[n].hpos = px;       gbs_bkg_scb[n].vpos = py; ++n;")
            self.emit("        if (px + 256 < %d) {" % sw)
            self.emit("            gbs_bkg_scb[n].hpos = px + 256; gbs_bkg_scb[n].vpos = py; ++n;")
            self.emit("        }")
            self.emit("        if (py + 256 < %d) {" % sh)
            self.emit("            gbs_bkg_scb[n].hpos = px;   gbs_bkg_scb[n].vpos = py + 256; ++n;")
            self.emit("            if (px + 256 < %d) {" % sw)
            self.emit("                gbs_bkg_scb[n].hpos = px + 256;")
            self.emit("                gbs_bkg_scb[n].vpos = py + 256; ++n;")
            self.emit("            }")
            self.emit("        }")
            self.emit("        for (s = 0; s < n; ++s) tgi_sprite(&gbs_bkg_scb[s]);")
            self.emit("    } else {")
            self.emit("        tgi_setcolor(%s);" % clear_pen)
            self.emit("        tgi_bar(0, 0, %d, %d);" % (sw - 1, sh - 1))
            self.emit("    }")
        else:
            self.emit("    tgi_setcolor(%s);" % clear_pen)
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
        if self.metasprite_used:
            self._emit_cc65_meta_branch('tile')
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
        if self.metasprite_used:
            self._emit_cc65_meta_branch('prop')
        self.emit("    if (nb < GBS_MAX_SPRITES)")
        self.emit("        gbs_scb[nb].sprctl0 = (uint8_t)((BPP_2 | TYPE_NORMAL) |")
        self.emit("            (prop & (HFLIP | VFLIP)));")
        self.emit("}")
        self.emit("/* sprite.move takes screen-pixel coordinates (top-left origin). */")
        self.emit("void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y) {")
        self.emit("    gbs_spr_init();")
        if self.metasprite_used:
            self._emit_cc65_meta_branch('move')
        self.emit("    if (nb < GBS_MAX_SPRITES) {")
        self.emit("        gbs_scb[nb].hpos = x;")
        self.emit("        gbs_scb[nb].vpos = y;")
        self.emit("        if (nb >= gbs_spr_max) gbs_spr_max = nb + 1;")
        self.emit("    }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        self.emit("void gbs_show_sprites(void) { gbs_spr_used = 1; gbs_spr_visible = 1; }")
        self.emit("void gbs_hide_sprites(void) { gbs_spr_visible = 0; }")
        if with_bkg:
            self.emit("void gbs_show_bkg(void) { gbs_bkg_visible = 1; }")
        else:
            self.emit("void gbs_show_bkg(void) { }")
        if self.metasprite_used:
            self._emit_cc65_meta_func()

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

        VRAM map: the cc65 conio runtime owns $0000-$1FFF (128x64 BAT) and
        $2000-$2FFF (font); tile patterns go at $3000 (32 tiles x 64 words)
        and the SATB at $7F00 (64 entries x 4 words; unused entries are
        zeroed once = parked off-screen at raster -64).

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
        self.emit("#define GBS_MAX_TILES   %d" % self.CC65_MAX_TILES)
        self.emit("#define GBS_MAX_SPRITES 40  /* the GB OAM model; SATB holds 64 */")
        self.emit("#define GBS_VDC_AR (*(volatile uint8_t *)0x0200)  /* register select */")
        self.emit("#define GBS_VDC_DL (*(volatile uint8_t *)0x0202)  /* data low */")
        self.emit("#define GBS_VDC_DH (*(volatile uint8_t *)0x0203)  /* data high (latches) */")
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
        self.emit("        gbs_satb[(uint16_t)nb * 4]     = (uint16_t)(64u + y);")
        self.emit("        gbs_satb[(uint16_t)nb * 4 + 1] = (uint16_t)(32u + x);")
        self.emit("        if (nb >= gbs_spr_max) gbs_spr_max = nb + 1;")
        self.emit("    }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        self.emit("/* Show/hide toggle the CR sprite-enable bit (text is unaffected). */")
        self.emit("void gbs_show_sprites(void) { gbs_spr_init(); gbs_vreg(5, 0x00C8); gbs_spr_used = 1; }")
        self.emit("void gbs_hide_sprites(void) { gbs_spr_init(); gbs_vreg(5, 0x0088); }")
        self.emit("void gbs_show_bkg(void) { }")
        if self.metasprite_used:
            self._emit_cc65_meta_func()

    # ---- Metasprite layer (graphics.sprite sprite.set_meta), shared by the
    # Lynx and PCE sprite engines. A metasprite at base slot B with W*H tiles
    # reserves the slots B..B+W*H-1 and is moved/flipped/re-tiled as one unit;
    # the per-slot meta tables default to zero (== single sprite), so untouched
    # slots and non-metasprite programs are byte-identical. The public
    # move/set_tile/set_prop wrappers get a meta-aware early branch (injected
    # by _emit_cc65_meta_branch) that clears the base's meta size, iterates the
    # children through the same public wrappers (now single), then restores it
    # -- so the per-engine slot logic is never duplicated.

    def _emit_cc65_meta_state(self):
        self.emit("/* --- Metasprite layer (sprite.set_meta) --- */")
        self.emit("static uint8_t gbs_meta_w[GBS_MAX_SPRITES];   /* 0/1 = single */")
        self.emit("static uint8_t gbs_meta_h[GBS_MAX_SPRITES];")
        self.emit("static uint8_t gbs_meta_prop[GBS_MAX_SPRITES];")

    def _emit_cc65_meta_branch(self, op):
        """Early meta-dispatch branch for gbs_move_sprite / set_tile / set_prop
        (op in {'move','tile','prop'}). Placed right after gbs_spr_init()."""
        if op == 'prop':
            self.emit("    if (nb < GBS_MAX_SPRITES) gbs_meta_prop[nb] = prop;")
        self.emit("    if (gbs_meta_w[nb] > 1 || gbs_meta_h[nb] > 1) {")
        self.emit("        uint8_t w = gbs_meta_w[nb], h = gbs_meta_h[nb];")
        if op == 'move':
            self.emit("        uint8_t r, c, s = nb, prop = gbs_meta_prop[nb], cc, rr;")
        else:
            self.emit("        uint8_t r, c, idx = 0;")
        self.emit("        gbs_meta_w[nb] = 0; gbs_meta_h[nb] = 0;  /* base acts single while iterating */")
        self.emit("        for (r = 0; r < h; ++r)")
        self.emit("            for (c = 0; c < w; ++c) {")
        if op == 'move':
            self.emit("                cc = (prop & FLIP_X) ? (uint8_t)(w - 1 - c) : c;")
            self.emit("                rr = (prop & FLIP_Y) ? (uint8_t)(h - 1 - r) : r;")
            self.emit("                gbs_move_sprite(s, (uint8_t)(x + cc * 8), (uint8_t)(y + rr * 8));")
            self.emit("                ++s;")
        elif op == 'tile':
            self.emit("                gbs_set_sprite_tile((uint8_t)(nb + idx), (uint8_t)(tile + idx));")
            self.emit("                ++idx;")
        else:  # prop
            self.emit("                gbs_set_sprite_prop((uint8_t)(nb + idx), prop);")
            self.emit("                ++idx;")
        self.emit("            }")
        self.emit("        gbs_meta_w[nb] = w; gbs_meta_h[nb] = h;")
        self.emit("        return;")
        self.emit("    }")

    def _emit_cc65_meta_func(self):
        self.emit("/* Define a metasprite: reserve base..base+w*h-1, tiles row-major. */")
        self.emit("void gbs_set_metasprite(uint8_t base, uint8_t tile, uint8_t w, uint8_t h) {")
        self.emit("    uint8_t r, c, idx = 0, prop = gbs_meta_prop[base];")
        self.emit("    gbs_meta_w[base] = 0; gbs_meta_h[base] = 0;  /* assign children as singles */")
        self.emit("    for (r = 0; r < h; ++r)")
        self.emit("        for (c = 0; c < w; ++c) {")
        self.emit("            gbs_set_sprite_tile((uint8_t)(base + idx), (uint8_t)(tile + idx));")
        self.emit("            gbs_set_sprite_prop((uint8_t)(base + idx), prop);")
        self.emit("            if ((uint8_t)(base + idx) < GBS_MAX_SPRITES) {")
        self.emit("                gbs_meta_w[base + idx] = 1; gbs_meta_h[base + idx] = 1;")
        self.emit("            }")
        self.emit("            ++idx;")
        self.emit("        }")
        self.emit("    gbs_meta_w[base] = w; gbs_meta_h[base] = h;")
        self.emit("}")

    def _emit_pce_bkg_engine(self, prof):
        """VDC background-tilemap engine for the PC Engine (graphics.bkg).

        Unlike the Lynx, the PCE has a *real* scrollable tilemap: the VDC's
        Background Attribute Table (BAT) plus the BXR/BYR scroll registers.
        The cc65 conio runtime configures a 64x32-entry BAT (512x256 px
        virtual screen), so the 32x32 GB map is written twice side by side --
        the content then repeats every 256 px and a u8 BXR scroll (0..255)
        wraps seamlessly, exactly the Game Boy contract. Vertically the BAT
        is 32 rows = 256 px, so BYR wraps mod 256 natively.

        Tiles are converted once from GB 2bpp into 4bpp planar background
        characters at VRAM $4000 (the conio runtime owns $0000-$1FFF BAT +
        $2000-$2FFF font; the sprite engine owns $3000-$37FF), planes 2/3
        left empty. BAT entries select BG palette 1 (palette bits 12-15),
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
        self.emit("void gbs_move_bkg(uint8_t x, uint8_t y) {")
        self.emit("    gbs_bkg_init();")
        self.emit("    gbs_bkg_x = x; gbs_bkg_y = y;")
        self.emit("    gbs_vreg(7, x);  /* BXR */")
        self.emit("    gbs_vreg(8, y);  /* BYR */")
        self.emit("}")
        self.emit("void gbs_scroll_bkg(int8_t dx, int8_t dy) {")
        self.emit("    gbs_move_bkg((uint8_t)(gbs_bkg_x + dx), (uint8_t)(gbs_bkg_y + dy));")
        self.emit("}")
        if self.palette_imported:
            self.emit("/* bkg.set_palette: remember the slot per cell and rewrite the BAT")
            self.emit("   entries' palette bits in place (the char code is read back from")
            self.emit("   VRAM through MARR/VRR, then written to all eight replicas). */")
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
            self.emit("            entry = (uint16_t)(*(volatile uint8_t *)0x0202);")
            self.emit("            entry |= (uint16_t)(*(volatile uint8_t *)0x0203) << 8;")
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
