"""cc65 backend: profiles, preludes, text + Suzy sprite engine (mixin)."""

from .cc65_text import Cc65TextMixin
from .cc65_sound import Cc65SoundMixin
from .cc65_palette import Cc65PaletteMixin
from .cc65_bkg import Cc65BkgMixin
from .cc65_sprite import Cc65SpriteMixin
from .cc65_native import Cc65NativeMixin
from .cc65_bank import Cc65BankMixin


class Cc65Backend(Cc65TextMixin, Cc65SoundMixin, Cc65PaletteMixin, Cc65BkgMixin, Cc65SpriteMixin, Cc65NativeMixin, Cc65BankMixin):
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
        ('input', 'raw'): 'gbs_input_raw',
        # Text (character-cell coords; TGI profiles scale to pixels).
        ('text', 'print_string'): 'gbs_print_string',
        ('text', 'print_number'): 'gbs_print_number',
        ('text', 'clear_area'): 'gbs_clear_area',
        # text.fill_box: a filled, bordered box drawn as a real framebuffer
        # overlay -- the TGI pixel primitives (Lynx) give a crisp border + an
        # opaque fill, unlike the character-cell +--+ frame. cc65-only (no GBDK
        # framebuffer); engine.box routes its Lynx branch here.
        ('text', 'fill_box'): 'gbs_fill_box',
        # text.plot_tile: a raw tile write through the TEXT layer (the GBDK
        # custom-frame seam). The cc65 consoles draw text OFF the tile table
        # (Lynx TGI framebuffer, PCE conio chars), so there is no text-layer
        # tile to plot -- a graceful no-op (frame via text.fill_box instead),
        # matching the set_font degradation model.
        ('text', 'plot_tile'): 'gbs_plot_tile',
        # video.set_overlay(cb): the Lynx PRESENT-time UI overlay hook -- the
        # callback draws an open dialogue/menu INSIDE gbs_present (after the
        # bkg/sprite blits, before the flip), so the UI is part of every
        # composed frame and survives any recomposite. No-op on the PCE
        # (persistent BAT -- drawn text composes with the frame already).
        ('video', 'set_overlay'): 'gbs_set_overlay',
        # Custom font (MosaiK Studio): the cc65 text backends (Lynx TGI bitmap
        # font, PCE conio font tiles) don't wire a live glyph swap, so this is a
        # graceful no-op -- the default font shows. Matches platform.sound's
        # degrade-don't-error model, so a composed set_font call builds here.
        ('text', 'set_font'): 'gbs_set_font',
        ('text', 'set_font_at'): 'gbs_set_font_at',
        # glyph_buffer(base, count): the GLYPH-BUFFER text mode exists to free a
        # TILE table for scene art. The cc65 consoles draw text off the
        # framebuffer/conio and reserve no glyph tiles at all, so there is
        # nothing to free -- a graceful no-op, like set_font above.
        ('text', 'glyph_buffer'): 'gbs_text_glyph_buffer',
        # The VARIABLE-WIDTH renderer (vwf-text-plan V4): composite a
        # glyph at a sub-cell pen into a two-tile stage over a linear
        # tile ring. Real on the GB family, a no-op elsewhere.
        ('text', 'vwf_start'): 'gbs_vwf_start',
        ('text', 'vwf_nl'): 'gbs_vwf_nl',
        ('text', 'vwf_glyph'): 'gbs_vwf_glyph',
        ('text', 'vwf_number'): 'gbs_vwf_number',
        # to_window/to_bkg: the GB window-overlay text seam -- a no-op on the cc65
        # consoles (Lynx/PCE have no GB-style window layer; text draws through the
        # framebuffer/conio). Emitted only when used (byte-identical otherwise).
        ('text', 'to_window'): 'gbs_text_to_window',
        ('text', 'to_bkg'): 'gbs_text_to_bkg',
        ('text', 'window_active'): 'gbs_text_window_active',
        # win_sprite_cut: there is no window layer (and no OBJ-over-window
        # problem) on the cc65 consoles -> a graceful no-op.
        ('text', 'win_sprite_cut'): 'gbs_text_win_cut',
        # win_reveal: no window layer to reveal -> a graceful no-op.
        ('text', 'win_reveal'): 'gbs_text_win_reveal',
        # win_overlay_cut: no window layer either, so there is no overlay
        # to cut short -> a graceful no-op.
        ('text', 'win_overlay_cut'): 'gbs_text_win_overlay_cut',
        # Raw hardware access.
        ('hw', 'write'): 'gbs_hw_write',
        ('hw', 'read'): 'gbs_hw_read',
        ('hw', 'peek'): '@hw_peek_is_inline',
        # System utilities.
        ('system', 'delay'): 'gbs_delay',
        ('system', 'random'): 'rand',
        ('system', 'seed_random'): 'gbs_seed_random',
        ('system', 'frames'): 'gbs_frames',
        # cpu_fast(on): only the Game Boy Color has a second CPU speed.
        ('system', 'cpu_fast'): 'gbs_cpu_fast',
        # set_view(ox, oy): the letterbox offset (a room smaller than the
        # screen shown centred); real on SMS / Game Gear / PC Engine.
        ('video', 'set_view'): 'gbs_set_view',
        # batch sprite verbs: the portable C loops (see gbdk_batch.py).
        ('sprite', 'plot'): 'gbs_spr_plot',
        ('sprite', 'drift'): 'gbs_spr_drift',
        ('sprite', 'hit_box'): 'gbs_spr_hit_box',
        ('sprite', 'hit'): 'gbs_spr_hit',
        # Sound (platform.sound): one square-wave beep channel.
        ('sound', 'beep'): 'gbs_sound_beep',
        ('sound', 'stop'): 'gbs_sound_stop',
        ('sound', 'sfx'): 'gbs_sound_sfx',
        ('sound', 'beep2'): 'gbs_sound_beep2',
        ('sound', 'stop2'): 'gbs_sound_stop2',
        # native.lynx escape hatch: real Mikey-palette fades + Suzy screen
        # shake on the Lynx; a no-op on the PC Engine (the other cc65 console).
        ('lynx', 'fade_in'): 'gbs_lynx_fade_in',
        ('lynx', 'fade_out'): 'gbs_lynx_fade_out',
        ('lynx', 'screen_shake'): 'gbs_lynx_screen_shake',
        ('lynx', 'jingle'): 'gbs_lynx_jingle',
        # Asset residency seam (asset-streaming groundwork). Lowered SPECIALLY in
        # _gen_call, not via these names: use(id) -> nothing, ptr(id) -> the bare
        # argument symbol (byte-identical to passing the const array to a setter).
        # Today the Lynx still converts from that resident const, exactly as now;
        # Stage B swaps these two lowerings to a cart loader + a cache pointer.
        # The sentinels must never reach the C output (a missed intercept fails).
        ('assets', 'use'): '@assets_use_is_a_noop',
        ('assets', 'ptr'): '@assets_ptr_is_passthrough',
        ('assets', 'code_byte'): '@assets_code_byte_is_a_seam',
        ('assets', 'address'): '@assets_address_is_a_seam',
        ('assets', 'bank_enter'): '@assets_bank_enter_is_a_seam',
        ('assets', 'bank_leave'): '@assets_bank_leave_is_a_seam',
        # The range-windowed seam (paint_table + stream, item 33), lowered
        # SPECIALLY in _gen_call; sentinels must never reach the C output.
        ('assets', 'range_base'): '@assets_range_base_is_registration',
        ('assets', 'use_range'): '@assets_use_range_is_a_seam',
        ('assets', 'ptr_range'): '@assets_ptr_range_is_a_seam',
        ('assets', 'range_byte'): '@assets_range_byte_is_a_seam',
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
        # set_meta_mask(base, tile, w, h, mask): set_meta with a per-COLUMN
        # blank mask (a SPARSE frame; see the GBDK map). Honoured here for the
        # TILE numbering - a sparse frame's sheet holds only its drawn
        # columns - and the blank cells park off screen.
        ('sprite', 'set_meta_mask'): 'gbs_set_metasprite_mask',
        ('sprite', 'move_world'): 'gbs_move_sprite_world',
        ('sprite', 'meta_cols'): 'gbs_meta_cols',
        # set_meta_list: the per-OBJECT descriptor form (see the GBDK map).
        # Real here too - one slot per object, authored offsets stored per
        # slot, tiles repeating freely.
        ('sprite', 'set_meta_list'): 'gbs_set_metasprite_list',
        ('sprite', 'move'): 'gbs_move_sprite',
        ('video', 'show_sprites'): 'gbs_show_sprites',
        ('video', 'hide_sprites'): 'gbs_hide_sprites',
        ('video', 'show_background'): 'gbs_show_bkg',
    }

    # Scrollable background tilemap (graphics.bkg), for has_bkg consoles.
    # The PC Engine has a real one (the VDC BAT plus the BXR/BYR scroll
    # registers); the Lynx has no tilemap hardware, so its engine draws the
    # 32x32 map as a ring of screen-spanning Suzy row-strip sprites (one per
    # visible row) that gbs_present() repositions -- the SPRDEMO4 scrolling
    # technique (cf. the side-scroller sample's scrolling floor). See
    # _emit_cc65_bkg_engine for the full design. The engines are emitted only
    # when the program imports graphics.bkg (the Lynx one costs ~18 KB of RAM).
    STDLIB_CALLS_CC65_BKG = {
        ('bkg', 'set_data'): 'gbs_set_bkg_data',
        # set_data_native: the native format is what set_data takes here.
        ('bkg', 'set_data_native'): 'gbs_set_bkg_data',
        ('bkg', 'set_tiles'): 'gbs_set_bkg_tiles',
        ('bkg', 'scroll'): 'gbs_scroll_bkg',
        ('bkg', 'move'): 'gbs_move_bkg',
        # set_attrs: the per-cell background palette-slot upload. The cc65
        # consoles reach 16 colours through the 4bpp background tier
        # (palette.load_bkg16) rather than per-tile 4-colour palettes, so this
        # is a graceful no-op here -- which is what lets a target-neutral room
        # painter call it with no `if platform` fork.
        ('bkg', 'set_attrs'): 'gbs_bkg_attrs',
        # parallax*: scanline bands need the GB's LYC/STAT interrupt and its
        # SCX/SCY registers. The Lynx has no tilemap at all (its background is
        # composited strips) and the PCE's scroll registers are written once
        # per frame at vblank -- a mid-frame write is what its own engine note
        # says makes the background jump. Honest no-ops, so a target-neutral
        # room loader calls them unconditionally.
        ('bkg', 'parallax'): 'gbs_px_arm',
        ('bkg', 'parallax_band'): 'gbs_px_band',
        ('bkg', 'parallax_scx'): 'gbs_px_scx',
        ('bkg', 'parallax_scy'): 'gbs_px_scy_set',
        # raster*: the per-scanline scroll table. No-op stubs here for the
        # same reason as parallax (see the prelude).
        ('bkg', 'raster'): 'gbs_rs_arm',
        ('bkg', 'raster_set'): 'gbs_rs_set',
        ('bkg', 'raster_copy'): 'gbs_rs_copy',
        ('bkg', 'raster_show'): 'gbs_rs_show',
        ('bkg', 'raster_get'): 'gbs_rs_get',
        ('bkg', 'raster_curve_start'): 'gbs_rs_curve_start',
        ('bkg', 'raster_curve'): 'gbs_rs_curve',
        ('bkg', 'raster_stripes'): 'gbs_rs_stripes',
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
        # A per-scene palette SET (see the GBDK map): real on the PCE's four
        # VCE background palettes, an honest no-op on the Lynx, whose single
        # 16-pen hardware palette is partitioned once.
        ('palette', 'load_bkg_set'): 'gbs_load_bkg_set',
        ('palette', 'load_sprite_set'): 'gbs_load_spr_set',
        ('palette', 'load_sprite16'): 'gbs_load_sprite_pal16',
        ('palette', 'load_bkg16'): 'gbs_load_bkg_pal16',
        # fade(level): the CGB colour fade (see the GBDK map). No-op here --
        # the Lynx fades natively through native.lynx instead.
        ('palette', 'fade'): 'gbs_pal_fade',
        ('sprite', 'set_palette'): 'gbs_sprite_palette',
        # set_meta_palettes: a palette PER CELL of a metasprite. Real here
        # too - the cc65 meta layer reserves one real sprite slot per cell,
        # so this is a loop over the existing per-slot setter.
        ('sprite', 'set_meta_palettes'): 'gbs_set_meta_pal',
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
            # The rate `video_init` PROGRAMS, named so `gbs_delay` can convert
            # against it. It is not CLOCKS_PER_SEC: on this console that macro
            # is a runtime call (`__clocks_per_sec()`) and it answers 50, while
            # `clock()` demonstrably advances once per PRESENTED frame - see
            # the delay note below. Keep it equal to the tgi_setframerate line
            # (pinned by tests/lynx_delay_clock_test.py).
            'frame_hz': 60,
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
    # addressed by sprite.set_data on cc65 sprite consoles. 40 (not 32) so a
    # full named sheet PLUS a small extra metasprite (e.g. game-slice's intro
    # logo uploaded above the 30-tile sheet) both fit; the table is BSS, so the
    # extra costs only a few hundred bytes of RAM.
    CC65_MAX_TILES = 40
    # Sprite SLOTS the Suzy engine allocates (the GB OAM object count, kept so
    # a slot id means the same thing on every console). `[build]
    # sprite_max_slots` lowers it for a tight Lynx build.
    CC65_MAX_SPRITES = 40
    # PC Engine sprite patterns a residency world may grow the table to:
    # 64 words each in the free VRAM $5000-$7EFF (below the SATB at $7F00).
    PCE_MAX_PATTERNS = (0x7F00 - 0x5000) // 64

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
        # ROM banking (the PC Engine under [build] code_banks): the names the
        # GB lowering text uses, before anything can need them. Nothing when
        # the program does not bank.
        self._emit_cc65_bank_prelude()
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
        if getattr(self, 'view_used', False):
            if self.platform == 'pce':
                # video.set_view: the letterbox offset, applied by the scroll
                # flush (gbs_bkg_scroll_flush) and the sprite leaf
                # (gbs_move_sprite). Emitted only when called.
                self.emit("/* video.set_view: a room smaller than the screen is shown centred. */")
                self.emit("static uint8_t gbs_view_ox = 0, gbs_view_oy = 0;")
                self.emit("void gbs_set_view(uint8_t x, uint8_t y) { gbs_view_ox = x; gbs_view_oy = y; }")
            else:
                self.emit("/* video.set_view: a no-op on this console. */")
                self.emit("void gbs_set_view(uint8_t x, uint8_t y) { (void)x; (void)y; }")
        self.emit("static uint8_t gbs_video_ready = 0;")
        self.emit("void gbs_video_init(void) {")
        self.emit("    if (gbs_video_ready) return;")
        for stmt in prof['video_init']:
            self.emit("    %s" % stmt)
        self.emit("    gbs_video_ready = 1;")
        self.emit("}")
        self.emit("void gbs_video_done(void) { %s }" % prof['video_done'])
        self._emit_cc65_sound(prof)
        if self.sound_sfx_used:
            self._emit_sound_sfx()
        emit_bkg = self.caps['has_bkg'] and self.cc65_bkg_imported
        if self.bkg_attrs_used and self._pce_attrs_real():
            # bkg.set_attrs on the PC Engine: each cell's slot goes through
            # the per-cell palette writer the bkg engine already has (the BAT
            # entry's palette bits, VCE BG palette slot+2). It used to be the
            # (void) no-op below, so a coloured world drew every tile through
            # slot 0 there although PLATFORM_CAPS says it has tile palettes.
            # Defined after the engine; declared here for the call order.
            self.emit("void gbs_bkg_palette_fill(uint8_t x, uint8_t y, uint8_t w,")
            self.emit("                          uint8_t h, uint8_t slot);")
            self.emit("/* bkg.set_attrs: one palette slot per map cell -> the BAT. */")
            self.emit("void gbs_bkg_attrs(uint8_t x, uint8_t y, uint8_t w, uint8_t h,")
            self.emit("                   const uint8_t *data) {")
            self.emit("    uint8_t cx, cy;")
            self.emit("    for (cy = 0; cy < h; ++cy)")
            self.emit("        for (cx = 0; cx < w; ++cx)")
            self.emit("            gbs_bkg_palette_fill((uint8_t)(x + cx), (uint8_t)(y + cy),")
            self.emit("                                 1, 1, data[(uint16_t)cy * w + cx]);")
            self.emit("}")
        elif self.bkg_attrs_used:
            # bkg.set_attrs (per-cell background palette slots): the cc65
            # consoles get their colour from the 4bpp 16-colour background tier
            # (palette.load_bkg16), not from per-tile 4-colour palettes, so
            # this is an honest no-op -- and that is what lets the generated,
            # target-neutral room painter call it unconditionally.
            self.emit("/* bkg.set_attrs: no per-tile background palette here; this console")
            self.emit("   colours its background through the 4bpp 16-colour tier. */")
            self.emit("void gbs_bkg_attrs(uint8_t x, uint8_t y, uint8_t w, uint8_t h,")
            self.emit("                   const uint8_t *data) {")
            self.emit("    (void)x; (void)y; (void)w; (void)h; (void)data;")
            self.emit("}")
        if self.edge_mask_used:
            # bkg.edge_mask: the SMS left-column blank (its display is exactly
            # as wide as the tilemap ring, so a streamed column is written
            # while it is on screen). Neither cc65 console has that problem --
            # the Lynx composites strips and the PCE's BAT is 128 columns wide
            # against a 32-column view -- so this is an honest no-op.
            self.emit("/* bkg.edge_mask: no 32-column tilemap ring to run out of;")
            self.emit("   nothing to mask here. */")
            self.emit("void gbs_bkg_edge_mask(uint8_t on) { (void)on; }")
        if self.parallax_used:
            # bkg.parallax*: scanline bands are an LYC/STAT interrupt writing
            # SCX per band. The Lynx has no tilemap (its background is
            # composited strips, so "scroll" is a strip position, not a
            # register read per line) and the PCE defers its scroll write to
            # vblank on purpose - a mid-frame BXR write is what makes its
            # background jump. Honest no-ops.
            self.emit("/* bkg.parallax*: scanline parallax needs a per-line scroll register;")
            self.emit("   this console has no tilemap scroll to change mid-frame. */")
            self.emit("void gbs_px_arm(uint8_t n) { (void)n; }")
            self.emit("void gbs_px_band(uint8_t i, uint8_t last) { (void)i; (void)last; }")
            self.emit("void gbs_px_scx(uint8_t i, uint8_t scx) { (void)i; (void)scx; }")
            self.emit("void gbs_px_scy_set(uint8_t scy) { (void)scy; }")
        if self.raster_used:
            # bkg.raster*: the same story as parallax, one entry per line.
            self.emit("/* bkg.raster*: no per-line scroll register driven here;")
            self.emit("   honest no-ops so a target-neutral program still builds. */")
            self.emit("void gbs_rs_arm(uint8_t on, uint8_t first) { (void)on; (void)first; }")
            self.emit("void gbs_rs_set(uint8_t line, uint8_t x, uint8_t y) {")
            self.emit("    (void)line; (void)x; (void)y;")
            self.emit("}")
            self.emit("void gbs_rs_copy(uint8_t line, uint8_t n, const uint8_t *t) {")
            self.emit("    (void)line; (void)n; (void)t;")
            self.emit("}")
            self.emit("void gbs_rs_show(void) { }")
            self.emit("uint8_t gbs_rs_get(uint8_t line) { (void)line; return 0; }")
            self.emit("void gbs_rs_curve_start(uint16_t x, uint16_t dx) { (void)x; (void)dx; }")
            self.emit("void gbs_rs_curve(uint8_t line, uint8_t n, uint16_t ddx) {")
            self.emit("    (void)line; (void)n; (void)ddx;")
            self.emit("}")
            self.emit("void gbs_rs_stripes(uint8_t line, uint8_t n, const uint8_t *depth,")
            self.emit("                    uint8_t phase, uint8_t y) {")
            self.emit("    (void)line; (void)n; (void)depth; (void)phase; (void)y;")
            self.emit("}")
        if self.batch_used:
            from .gbdk_batch import GbdkBatchMixin
            # the sprite mover is defined further down this file
            self.emit("void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y);")
            GbdkBatchMixin._emit_batch_c(self, mover="gbs_move_sprite", offs=False)
        if self.cpu_fast_used:
            self.emit("/* system.cpu_fast: only the Game Boy Color has a second CPU speed. */")
            self.emit("void gbs_cpu_fast(uint8_t on) { (void)on; }")
        if sprite_engine == 'suzy':
            # Static-frame skip state (used by both engines + the present): the
            # present checksums the drawn state (sprite slots + bkg scroll) and
            # SKIPs the heavy recomposite + flip when unchanged, so a static
            # frame holds the last complete buffer instead of re-blitting the
            # wide bkg strips + sprites every frame -- that per-frame Suzy load
            # intermittently drops a foreground sprite on the Beetle core.
            # gbs_force is now the WHOLE mechanism: the per-frame weighted-sum
            # checksum it used to back up is gone (it cost ~36,000 ticks a frame
            # in cc65 software multiplies -- see cc65_sprite's present). Every
            # writer, of pixels or of position, raises it on a real change.
            self.emit("static uint8_t gbs_force = 1;   /* force the next present (init: first frame) */")
            self.emit("static uint8_t gbs_redraw = 0;  /* full re-blits still owed (2 after a change) */")
            if self.overlay_used:
                self.emit("/* PRESENT-time UI overlay hook (video.set_overlay): called after the bkg")
                self.emit("   strip + sprite blits and before the flip, so an open dialogue/menu is")
                self.emit("   part of EVERY composed frame. Post-present drawing alone cannot")
                self.emit("   survive a recomposite: the freshly-blitted page scans out before the")
                self.emit("   drawing lands, so UI over a moving background (a scroll_bg title, the")
                self.emit("   shmup auto-scroll, the strip ring's amortized rebuild) strobed. */")
                self.emit("static void (*gbs_overlay_cb)(void) = 0;")
                self.emit("void gbs_set_overlay(void (*cb)(void)) { gbs_overlay_cb = cb; }")
            self.emit("")
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
                self._emit_cc65_meta_pal()
                self._emit_cc65_fade()
        elif sprite_engine == 'vdc':
            self._emit_pce_sprite_engine(prof)
            if emit_bkg:
                # After the sprite engine: reuses its gbs_vreg/gbs_vram_addr.
                self._emit_pce_bkg_engine(prof)
            if self.palette_imported:
                # After both engines: the setters init them first so a later
                # engine init cannot clobber user colors with the grey ramp.
                self._emit_pce_palette(emit_bkg)
                self._emit_cc65_meta_pal()
                self._emit_cc65_fade()
        else:
            self.emit("void gbs_present(void) {")
            if self.caps['has_sound']:
                self.emit("    if (gbs_snd_frames && --gbs_snd_frames == 0) gbs_sound_stop();")
                if self.sound_beep2_used:
                    self.emit("    if (gbs_snd_frames2 && --gbs_snd_frames2 == 0) gbs_sound_stop2();")
            if prof['present']:
                self.emit("    %s" % prof['present'])
            self.emit("}")
        # video.set_overlay off the Suzy engine (PCE VDC / text-only profiles):
        # a persistent tilemap composes drawn text with the frame already, so
        # the present hook is a graceful no-op (the Suzy branch emitted the
        # real registration above).
        if self.overlay_used and sprite_engine != 'suzy':
            self.emit("void gbs_set_overlay(void (*cb)(void)) { (void)cb; }")
        self.emit("uint8_t gbs_input_pressed(uint8_t button) {")
        self.emit("    gbs_video_init();")
        if is_tgi:
            # Lynx: the cc65 stdjoy driver mis-reads UP/DOWN on accurate emulators
            # (GearLynx / Holani / real hardware) -- joy_read returns 0 for the two
            # vertical directions while LEFT/RIGHT and the buttons work. The
            # JOYSTICK register ($FCB0 = SUZY.joystick) bits match the JOY_*_MASK
            # layout exactly (up 0x80, down 0x40), so OR the vertical directions in
            # straight from the hardware. Landscape play (no display rotation).
            self.emit("    return (uint8_t)((joy_read(0) |")
            self.emit("        (SUZY.joystick & (JOY_UP_MASK | JOY_DOWN_MASK))) & button);")
        else:
            self.emit("    return (uint8_t)(joy_read(0) & button);")
        self.emit("}")
        if self.input_raw_used:
            self.emit("uint8_t gbs_input_raw(void) { return gbs_input_pressed(0xFF); }")
        if is_tgi:
            self._emit_cc65_text_tgi()
        else:
            self._emit_cc65_text_conio()
        # text.to_window/to_bkg: the GB window-overlay seam. Neither cc65 console
        # has a window LAYER, but that is not the same as having nothing to do.
        #
        #   * LYNX: genuinely a no-op. Text draws into the framebuffer and the
        #     present hook redraws the open box every frame (`draw_open_ui`), so
        #     a scrolled background never moves it.
        #   * PCE: NOT a no-op. conio writes
        #     ABSOLUTE cells of the BAT, which is the table the VDC scrolls
        #     through, so the box slid with the level (measured on vm-uiscroll:
        #     "SCROLL HOLD TEST" standing still, "ROLL HOLD TEST" after two
        #     tiles of scroll). The latch below puts the conio plotters into
        #     screen space; `_emit_conio_ui_space` is where the mapping lives.
        #
        # `gbs_text_window_active()` stays 0 on BOTH: the cells belong to the
        # scene either way, so the caller must still repaint the room on close.
        # Emitted only when the program uses the verb.
        if self.text_window_used:
            if self._conio_ui_space:
                # ...AND THE SCROLL SNAPS TO A TILE while the box is up, the SMS
                # half's other move. Mapping the box into screen space puts it on
                # the right CELL, but a cell cannot land at a sub-tile offset, so
                # with `x & 7 != 0` the whole box still shows shifted by that many
                # pixels.
                #
                # This only works because vm.player learned the PCE arm of the
                # draw camera at the same time: the
                # `in_block` stand-down stops `follow_and_render` re-publishing
                # the UNSNAPPED scroll every frame - which is why an earlier cut
                # of this snap, made alone, measured as changing literally
                # nothing - and `cam_draw_x/y` rounds every sprite the same way
                # so nothing floats off the ground while the box is up.
                self.emit("static uint8_t gbs_ui_scx = 0;   /* the pre-snap scroll, restored on close */")
                self.emit("static uint8_t gbs_ui_scy = 0;")
                self.emit("void gbs_text_to_window(uint8_t origin_row, uint8_t box_rows) {")
                self.emit("    (void)origin_row; (void)box_rows;   /* the box keeps its authored rows */")
                self.emit("    if (!gbs_text_ui) {")
                self.emit("        gbs_ui_scx = gbs_bkg_x;   /* the pending scroll, saved */")
                self.emit("        gbs_ui_scy = gbs_bkg_y;")
                self.emit("        if ((gbs_ui_scx & 7) || (gbs_ui_scy & 7))")
                self.emit("            gbs_move_bkg((uint8_t)(gbs_ui_scx & 0xF8),")
                self.emit("                         (uint8_t)(gbs_ui_scy & 0xF8));")
                self.emit("    }")
                self.emit("    gbs_text_ui = 1;")
                self.emit("}")
                self.emit("void gbs_text_to_bkg(void) {")
                self.emit("    if (gbs_text_ui) {")
                self.emit("        gbs_text_ui = 0;")
                self.emit("        gbs_move_bkg(gbs_ui_scx, gbs_ui_scy);   /* the exact scroll back */")
                self.emit("    }")
                self.emit("}")
            else:
                self.emit("void gbs_text_to_window(uint8_t origin_row, uint8_t box_rows) { (void)origin_row; (void)box_rows; }")
                self.emit("void gbs_text_to_bkg(void) { }")
            # No window LAYER on either cc65 console - the box is drawn into
            # the framebuffer / BAT and is visible as it is written, so there
            # is nothing to hold back and nothing to reveal.
            self.emit("void gbs_text_win_reveal(void) { }")
            self.emit("uint8_t gbs_text_window_active(void) { return 0; }")
        hz = prof.get('frame_hz')
        self.emit("/* delay(ms) busy-waits using the system clock, so the granularity is a")
        self.emit("   FRAME TICK, not a millisecond. Rounded UP with a 1-tick floor:")
        self.emit("   delay(1..16) used to truncate to 0 ticks and return immediately")
        self.emit("   (GBDK's delay() is true milliseconds). */")
        if hz:
            # MEASURED, 2026-09-06 (review E-8), on the Lynx: `clock()` advances
            # exactly once per PRESENTED frame (120 ticks across 120
            # `wait_vblank`s), but cc65 resolves CLOCKS_PER_SEC there through a
            # RUNTIME call that answers 50 - so every delay ran 50/60 of its
            # length. delay(2000) took 100 display frames where 120 is a second
            # and a half of them, delay(1000) took 50; linear, 20% short, in
            # every Lynx program ever built. Convert against the rate the
            # prelude PROGRAMS instead. The consoles whose header states a
            # constant that matches their display (the PC Engine's 60) keep
            # CLOCKS_PER_SEC and are byte-identical.
            self.emit("/* THE TICK IS A PRESENTED FRAME, and CLOCKS_PER_SEC does not say so")
            self.emit("   on this console: cc65 resolves it at RUNTIME (__clocks_per_sec)")
            self.emit("   and it answers 50, while clock() advances once per frame at the")
            self.emit("   %d Hz video_init programs -- measured, review E-8. Converting" % hz)
            self.emit("   against the macro made every delay 20% short. */")
            self.emit("void gbs_delay(uint16_t ms) {")
            self.emit("    clock_t ticks = ((clock_t)ms * %d + 999) / 1000;" % hz)
        else:
            self.emit("void gbs_delay(uint16_t ms) {")
            self.emit("    clock_t ticks = ((clock_t)ms * CLOCKS_PER_SEC + 999) / 1000;")
        self.emit("    clock_t target;")
        self.emit("    if (ms > 0 && ticks == 0) ticks = 1;")
        self.emit("    target = clock() + ticks;")
        self.emit("    while (clock() < target) { }")
        self.emit("}")
        self.emit("void gbs_seed_random(uint16_t seed) { srand(seed); }")
        if self.frames_used:
            self.emit("/* system.frames(): the DISPLAY-frame counter. cc65's clock() is")
            self.emit("   driven by a hardware timer at CLOCKS_PER_SEC (~60 Hz here), so")
            self.emit("   like GBDK's sys_time it keeps counting whatever the game loop")
            self.emit("   is doing - which is the whole point. u8 is enough: it is read")
            self.emit("   for single-digit deltas and the wrap is exact unsigned. */")
            self.emit("uint8_t gbs_frames(void) { return (uint8_t)clock(); }")
        self.emit("/* Raw hardware register access (addresses are console-specific). */")
        self.emit("void gbs_hw_write(uint16_t addr, uint8_t value) { *(volatile uint8_t *)addr = value; }")
        self.emit("uint8_t gbs_hw_read(uint16_t addr) { return *(volatile uint8_t *)addr; }")
        if self.native_lynx_imported:
            # self.platform is canonicalised by the time codegen runs.
            self._emit_native_lynx(real=(self.platform == 'lynx'))
        # After the sprite engine: the far sheet upload calls into it.
        self._emit_cc65_bank_late()
        self.emit("")
