"""GBDK backend: prelude + stdlib lowering map (mixin for CodeGenerator)."""

from .gbdk_save import GbdkSaveMixin
from .gbdk_palette import GbdkPaletteMixin
from .gbdk_sprite import GbdkSpriteMixin
from .gbdk_metasprite import GbdkMetaspriteMixin
from .gbdk_bkg import GbdkBkgMixin
from .gbdk_lyc import GbdkLycMixin
from .gbdk_raster import GbdkRasterMixin
from .gbdk_sound import GbdkSoundMixin
from .gbdk_text import GbdkTextMixin


class GbdkBackend(GbdkSaveMixin, GbdkPaletteMixin, GbdkSpriteMixin,
                  GbdkMetaspriteMixin, GbdkBkgMixin, GbdkLycMixin,
                  GbdkRasterMixin,
                  GbdkSoundMixin, GbdkTextMixin):
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
        ('input', 'raw'): 'gbs_input_raw',
        ('text', 'print_string'): 'gbs_print_string',
        ('text', 'print_number'): 'gbs_print_number',
        ('text', 'clear_area'): 'gbs_clear_area',
        ('text', 'set_font'): 'gbs_set_font',
        # set_font_at(base, data): the font swap at a CALLER-chosen tile base --
        # the SMS/GG escape for a large tileset that would collide with the low
        # console font (opt-in, so set_font users stay byte-identical). Honored
        # on every tile-plotting console; a no-op where set_font is one (NES).
        ('text', 'set_font_at'): 'gbs_set_font_at',
        # glyph_buffer(base, count): the GLYPH-BUFFER text mode -- font in ROM,
        # each character rasterized on demand into the tile band. Real on the
        # tile-plotting consoles, a no-op on the NES (CHR glyphs).
        ('text', 'glyph_buffer'): 'gbs_text_glyph_buffer',
        # The VARIABLE-WIDTH renderer (vwf-text-plan V4): composite a
        # glyph at a sub-cell pen into a two-tile stage over a linear
        # tile ring. Real on the GB family, a no-op elsewhere.
        ('text', 'vwf_start'): 'gbs_vwf_start',
        ('text', 'vwf_nl'): 'gbs_vwf_nl',
        ('text', 'vwf_glyph'): 'gbs_vwf_glyph',
        ('text', 'vwf_number'): 'gbs_vwf_number',
        # to_window(origin_row, box_rows) / to_bkg(): route UI text onto the GB
        # window overlay (0x9C00), bottom-anchored. Real on the GB family, a no-op
        # on the other GBDK consoles (SMS/GG/NES have no GB-style window layer).
        ('text', 'to_window'): 'gbs_text_to_window',
        ('text', 'to_bkg'): 'gbs_text_to_bkg',
        ('text', 'window_active'): 'gbs_text_window_active',
        # win_sprite_cut(on): stop sprites drawing OVER the window overlay (an
        # LYC interrupt at the window's first scanline; the reference engine's model).
        ('text', 'win_sprite_cut'): 'gbs_text_win_cut',
        # win_reveal(): show the window layer to_window prepared, once the box
        # has been drawn into it (to_window no longer shows it itself).
        ('text', 'win_reveal'): 'gbs_text_win_reveal',
        # win_overlay_cut(y): stop the window OVERLAY at scanline `y` - the
        # window goes off there and sprites come back (the reference engine's
        # `overlay_cut_scanline`). The third tenant of LYC_REG; 144 and up
        # disarms it, as its own default 150 does.
        ('text', 'win_overlay_cut'): 'gbs_text_win_overlay_cut',
        # plot_tile(x, y, tile): ONE raw bkg tile id at a text cell, routed
        # through the TEXT layer (window map when to_window is live on the GB
        # family, else the bkg/name table) -- the custom tile-frame seam.
        ('text', 'plot_tile'): 'gbs_plot_tile',
        ('hw', 'write'): 'gbs_hw_write',
        ('hw', 'read'): 'gbs_hw_read',
        ('hw', 'peek'): '@hw_peek_is_inline',
        # SN76489 PSG data-port write (SMS / Game Gear): the PSG is a Z80 I/O port,
        # not memory, so hw.write can't reach it -- this lowers to `PSG = value`.
        ('hw', 'psg'): 'gbs_hw_psg',
        # Display visibility (macros, wrapped as helpers).
        ('video', 'show_sprites'): 'gbs_show_sprites',
        ('video', 'hide_sprites'): 'gbs_hide_sprites',
        ('video', 'show_background'): 'gbs_show_bkg',
        ('video', 'show_window'): 'gbs_show_win',
        ('video', 'hide_window'): 'gbs_hide_win',
        # video.set_overlay: the Lynx present-hook seam (cc65). Persistent
        # tilemaps compose drawn UI with the frame already -> a no-op here.
        ('video', 'set_overlay'): 'gbs_set_overlay',
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
        # set_meta_mask(base, tile, w, h, mask): set_meta with a per-COLUMN
        # BLANK mask (bit c = column c draws nothing). A masked column's OAM
        # objects are parked off-screen and consume NO tile, so a sparse
        # frame - the reference engine's metasprites place tiles at authored offsets and
        # leave gaps - costs neither a hardware sprite on the scanline nor a
        # tile in VRAM. Emitted only when called.
        ('sprite', 'set_meta_mask'): 'gbs_set_metasprite_mask',
        # move_world(base, x, y): the SIGNED-coordinate move a world-space
        # renderer needs, so the fan can park the columns that fall off the
        # screen instead of wrapping them onto the opposite edge.
        ('sprite', 'move_world'): 'gbs_move_sprite_world',
        # meta_cols(base): the metasprite's width in tile columns.
        ('sprite', 'meta_cols'): 'gbs_meta_cols',
        # set_meta_list(base, pw, tile, data, off, n): the per-OBJECT
        # descriptor form (the reference engine's metasprite_t). Each entry is
        # (dy, dx, dtile, props): rows may OVERLAP (its platform player's rows
        # are 12 px apart - a dense rectangle pads them to 16) and a tile may
        # REPEAT (a score sprite's 100 frames dedupe to ten digit cells).
        # `pw` = the frame's pixel width, what FLIP_X mirrors around.
        ('sprite', 'set_meta_list'): 'gbs_set_metasprite_list',
        ('sprite', 'move'): 'gbs_move_sprite',
        # sprite.vbl_hold(on): hold/release the shadow-OAM -> SAT copy so a
        # frame's sprite writes commit as a UNIT (see _emit_gbdk_vbl_hold).
        ('sprite', 'vbl_hold'): 'gbs_spr_vbl_hold',
        # sprite.cut_y(y): park sprite objects at/below screen row y (the
        # SMS/GG answer to text.win_sprite_cut -- see _emit_gbdk_spr_cut).
        ('sprite', 'cut_y'): 'gbs_spr_cut_set',
        # sprite.font_glyph(tile, ch): the console font's glyph for `ch` as one
        # sprite tile, read from the LINKED font_ibm (see gbdk_font.py).
        ('sprite', 'font_glyph'): 'gbs_sprite_font_glyph',
        ('sprite', 'set_palette'): 'gbs_sprite_palette',
        # set_meta_palettes(base, w, h, data, off): a palette PER CELL of a
        # metasprite, so one actor can wear several (reference-engine colours its
        # metasprite tiles individually - its player's hair, face and body
        # take three OBJ palettes). The map is in 8x8-TILE units, row-major,
        # like sprite.set_meta's own w/h; the runtime maps it onto whatever
        # the console's fan order is.
        ('sprite', 'set_meta_palettes'): 'gbs_set_meta_pal',
        # Background (graphics.bkg).
        ('bkg', 'set_data'): 'set_bkg_data',
        # set_data_pal(first, count, data, off, slot): upload 2bpp tiles from
        # `data + off*16` rendered through background palette SLOT -- the
        # per-TILE colour path on SMS/GG (CRAM entries slot*4..slot*4+3);
        # a plain upload ignoring the slot on every 2bpp-CRAM-free console.
        ('bkg', 'set_data_pal'): 'gbs_bkg_data_pal',
        ('bkg', 'set_tiles'): 'set_bkg_tiles',
        # set_data_native(first, count, data): tiles ALREADY in the console's
        # own tile format, uploaded without a conversion. It only differs from
        # set_data on SMS/GG under the 16-colour tier, where set_data turns
        # each packed-nibble tile into the VDP's planar layout at run time
        # (32 helper calls a tile - seconds for a whole screen's set). A
        # build-time generator can emit the planar bytes and skip that.
        ('bkg', 'set_data_native'): 'gbs_bkg_data_native',
        ('bkg', 'scroll'): 'scroll_bkg',
        ('bkg', 'move'): 'move_bkg',
        ('bkg', 'set_palette'): 'gbs_bkg_palette_fill',
        # set_attrs(x, y, w, h, data): the ATTRIBUTE mirror of bkg.set_tiles --
        # one palette-slot byte per map cell, uploaded as a rectangle. Real on
        # the per-tile-palette consoles, a graceful no-op elsewhere (so a
        # target-neutral room painter needs no `if platform` fork).
        ('bkg', 'set_attrs'): 'gbs_bkg_attrs',
        # edge_mask(on): blank the leftmost 8 px background column. Real on the
        # SMS only (see _emit_gbdk_edge_mask); a no-op stub elsewhere, so a
        # streamed level's setup stays target-neutral.
        ('bkg', 'edge_mask'): 'gbs_bkg_edge_mask',
        # Scanline PARALLAX bands (the reference engine's core/parallax.c model): an
        # LYC/STAT interrupt writes each band's SCX. Real on the GB register
        # model, no-op stubs elsewhere (see _emit_gbdk_parallax).
        ('bkg', 'parallax'): 'gbs_px_arm',
        ('bkg', 'parallax_band'): 'gbs_px_band',
        ('bkg', 'parallax_scx'): 'gbs_px_scx',
        ('bkg', 'parallax_scy'): 'gbs_px_scy_set',
        # The PER-SCANLINE scroll table (see gbdk_raster.py): one scroll per
        # screen line - a pseudo-3D road, a ripple, a "mode 7" floor. Real on
        # the GB family (a raw STAT interrupt) and on SMS/GG (a line interrupt
        # that walks the V counter), no-op stubs elsewhere.
        ('bkg', 'raster'): 'gbs_rs_arm',
        ('bkg', 'raster_set'): 'gbs_rs_set',
        ('bkg', 'raster_copy'): 'gbs_rs_copy',
        ('bkg', 'raster_show'): 'gbs_rs_show',
        ('bkg', 'raster_get'): 'gbs_rs_get',
        ('bkg', 'raster_curve_start'): 'gbs_rs_curve_start',
        ('bkg', 'raster_curve'): 'gbs_rs_curve',
        ('bkg', 'raster_stripes'): 'gbs_rs_stripes',
        # Palettes (graphics.palette): 4-color GB-model palette slots,
        # quantized to the console's native color format. Available on every
        # console -- 4-grey machines quantize to shades (see _emit_gbdk_palette).
        ('palette', 'rgb'): 'gbs_rgb',
        ('palette', 'set_bkg'): 'gbs_set_bkg_palette',
        ('palette', 'set_sprite'): 'gbs_set_spr_palette',
        ('palette', 'load_bkg'): 'gbs_load_bkg_palette',
        ('palette', 'load_sprite'): 'gbs_load_spr_palette',
        # load_bkg_set / load_sprite_set(slot, count, colors, off): load COUNT
        # consecutive 4-colour slots out of one table, starting at word index
        # `off` -- the per-scene palette set. Real where the multi-palette model
        # is (has_tile_palettes: GBC / Pocket / NES / PCE), an honest no-op on a
        # single-palette console, whose one fixed palette is what its art was
        # quantized for (the reference engine's DMG build works the same way).
        ('palette', 'load_bkg_set'): 'gbs_load_bkg_set',
        ('palette', 'load_sprite_set'): 'gbs_load_spr_set',
        # load_native(first, count, data): load `count` whole hardware
        # palettes already in the console's NATIVE colour format, lowering
        # STRAIGHT onto the z80 port's set_palette -- no prelude helper,
        # because its caller is the generated (BANKED) scenes.load_palettes
        # and every prelude byte is resident bank-0 rent. Real on SMS/GG
        # (16 entries a layer, so one call writes both banks); remapped to a
        # graceful no-op on the other GBDK consoles, whose palettes are not
        # a flat native run.
        ('palette', 'load_native'): 'set_palette',
        # set_2bpp(map): the nibble map the port's 2bpp -> native tile
        # expansion uses, i.e. which four CRAM entries a 2bpp tile's four
        # pixel values land on (`set_sprite_data` and friends read it). An
        # inline no-op on every non-z80 GBDK console, so it costs nothing to
        # call unconditionally.
        ('palette', 'set_2bpp'): 'set_2bpp_palette',
        # 16-colour sprite palette: a no-op on the GB family (2bpp sprites);
        # the asset was luma-quantized to greys (generalized-with-limits).
        ('palette', 'load_sprite16'): 'gbs_load_sprite_pal16',
        # 16-colour BACKGROUND palette: likewise a no-op on the 2bpp GB family
        # (bkg_bpp==2; the tileset luma-quantized to greys).
        ('palette', 'load_bkg16'): 'gbs_load_bkg_pal16',
        # fade(level): 0 normal .. 3 black, applied to every LOADED palette.
        # Real on the CGB class, where BGP/OBP0/OBP1 -- the registers vm.fx
        # ramps on a DMG -- are ignored by the hardware, so the only way to
        # darken a colour screen is to scale the palettes. An honest no-op on
        # the consoles whose palette is fixed art (see _emit_gbdk_palette).
        ('palette', 'fade'): 'gbs_pal_fade',
        # Window (graphics.window).
        ('window', 'set_tiles'): 'set_win_tiles',
        ('window', 'move'): 'move_win',
        # System utilities (platform.system).
        ('system', 'delay'): 'delay',
        ('system', 'random'): 'rand',
        ('system', 'seed_random'): 'initrand',
        ('system', 'frames'): 'gbs_frames',
        # cpu_fast(on): the Game Boy Color's double-speed CPU mode; a no-op on
        # every other console (see gbdk_raster.py).
        ('system', 'cpu_fast'): 'gbs_cpu_fast',
        # set_view(ox, oy): the letterbox offset (a room smaller than the
        # screen shown centred); real on SMS / Game Gear / PC Engine.
        ('video', 'set_view'): 'gbs_set_view',
        # vm.music's VBL-interrupt tick (6.6 stage 2): wire the driver's
        # update onto the add_VBL chain / raise the ISR's stand-down latch.
        ('system', 'music_isr'): 'gbs_music_isr_wire',
        ('system', 'music_hold'): 'gbs_music_hold',
        ('system', 'music_tick'): 'gbs_music_tick',
        # Sound (platform.sound): one square-wave beep channel.
        ('sound', 'beep'): 'gbs_sound_beep',
        ('sound', 'stop'): 'gbs_sound_stop',
        ('sound', 'sfx'): 'gbs_sound_sfx',
        ('sound', 'beep2'): 'gbs_sound_beep2',
        ('sound', 'stop2'): 'gbs_sound_stop2',
        ('save', 'enable'): 'gbs_save_enable',
        ('save', 'disable'): 'gbs_save_disable',
        ('save', 'write_u8'): 'gbs_save_write',
        ('save', 'read_u8'): 'gbs_save_read',
        # native.lynx escape hatch: no-ops on the GB family (the fade/shake are
        # Lynx hardware; one source still builds here -- generalized fallback).
        ('lynx', 'fade_in'): 'gbs_lynx_fade_in',
        ('lynx', 'fade_out'): 'gbs_lynx_fade_out',
        ('lynx', 'screen_shake'): 'gbs_lynx_screen_shake',
        ('lynx', 'jingle'): 'gbs_lynx_jingle',
        # native.huge -- hUGEDriver (GB family only; see stdlib.py). `play` is
        # the one verb that needs the SONG TABLE, so it is defined by the
        # generated song module rather than the prelude; the rest wrap the
        # driver object's own entry points.
        ('huge', 'play'): 'gbs_huge_play',
        ('huge', 'update'): 'gbs_huge_update',
        ('huge', 'stop'): 'gbs_huge_stop',
        ('huge', 'mute'): 'gbs_huge_mute',
        ('huge', 'set_position'): 'gbs_huge_setpos',
        ('huge', 'set_rate'): 'gbs_huge_set_rate',
        ('huge', 'routine_next'): 'gbs_huge_routine_next',
        # Asset residency seam (asset-streaming groundwork). Lowered SPECIALLY in
        # _gen_call, not via these names: use(id) -> nothing (the data is already
        # in ROM), ptr(id) -> the bare argument symbol (byte-identical to passing
        # the array straight to the setter). The sentinel values must never reach
        # the C output -- if one does, the build fails loudly (a missed intercept).
        ('assets', 'use'): '@assets_use_is_a_noop',
        ('assets', 'ptr'): '@assets_ptr_is_passthrough',
        ('assets', 'code_byte'): '@assets_code_byte_is_a_seam',
        ('assets', 'address'): '@assets_address_is_a_seam',
        ('assets', 'bank_enter'): '@assets_bank_enter_is_a_seam',
        ('assets', 'bank_leave'): '@assets_bank_leave_is_a_seam',
        # The range-windowed seam (paint_table + stream, item 33) -- also lowered
        # SPECIALLY in _gen_call; these sentinels must never reach the C output.
        ('assets', 'range_base'): '@assets_range_base_is_registration',
        ('assets', 'use_range'): '@assets_use_range_is_a_seam',
        ('assets', 'ptr_range'): '@assets_ptr_range_is_a_seam',
        ('assets', 'range_byte'): '@assets_range_byte_is_a_seam',
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
        elif self.spr_font_glyph_used:
            # sprite.font_glyph reads the linked font_ibm, declared here.
            self.emit("#include <gbdk/font.h>")
        self.emit("#include <rand.h>")
        if self.raster_used:
            # bkg.raster*: memcpy for raster_copy, and on the GB family the raw
            # STAT vector (ISR_VECTOR).
            self.emit("#include <string.h>")
            if self.caps.get('has_gb_regs'):
                self.emit("#include <gb/isr.h>")
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
        self._emit_gbdk_view_defines()
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
        if self.platform in ('gameboy_color', 'analogue_pocket'):
            # Lockstep with the definition in _emit_prelude_gbdk. Only main()
            # calls it and main() is never banked, but a gated prelude helper
            # without its prototype here is how bank TUs break.
            self.emit("void gbs_cgb_default_palettes(void);")
        self.emit("void gbs_enable_lcd(void);")
        self.emit("void gbs_disable_lcd(void);")
        self.emit("uint8_t gbs_input_pressed(uint8_t button);")
        self._emit_gbdk_text_decls()
        self.emit("void gbs_hw_write(uint16_t addr, uint8_t value);")
        self.emit("uint8_t gbs_hw_read(uint16_t addr);")
        self.emit("void gbs_show_sprites(void);")
        self.emit("void gbs_hide_sprites(void);")
        self.emit("void gbs_show_bkg(void);")
        if self.platform in ('sms', 'gamegear'):
            # bkg.set_tiles is rerouted through the 28-row clamp wrapper on
            # SMS/GG (see generator.generate), so banked code needs its
            # prototype too.
            self.emit("void gbs_set_bkg_tiles(uint8_t x, uint8_t y, uint8_t w, uint8_t h,")
            self.emit("                       const uint8_t *tiles);")
            self.emit("void gbs_hw_psg(uint8_t value);")
        # The metasprite layer / prop wrapper is `static inline` in EVERY TU
        # (the gbdk_save pattern): the bank TU gets the DEFINITIONS over
        # `extern` views of the one set of state tables, so a TU that calls a
        # helper carries its own copy in its own bank and bank 0 pays only
        # for what the MAIN TU itself calls.
        if self.metasprite_used:
            self._emit_gbdk_meta_tables(True)
            self._emit_gbdk_metasprite()
        else:
            self._emit_gbdk_prop_wrapper()
        # Before the mover: it redirects move_sprite for everything below.
        self._emit_gbdk_spr_cut(True)
        self._emit_gbdk_move_sprite()
        self._emit_gbdk_move_world()
        if self.spr_font_glyph_used:
            # Lockstep with _emit_gbdk_sprite_font_glyph: a HUD in a bank TU.
            self.emit("void gbs_sprite_font_glyph(uint8_t tile, uint8_t ch);")
        if self.bkg_data_pal_used:
            # Slot-rendered tile upload (SMS/GG per-tile colour); the same
            # lockstep rule as every gated helper -- scenes/rooms code calling
            # it may live in a bank TU.
            self.emit("void gbs_bkg_data_pal(uint8_t first, uint8_t count,")
            self.emit("                      const uint8_t *data, const uint8_t *slots);")
        if self.pal_native_used and self.platform not in ('sms', 'gamegear'):
            self.emit("void gbs_pal_native(uint8_t first, uint8_t count,")
            self.emit("                    const uint16_t *data);")
        elif self.pal_native_used and self.palette_fade_used:
            # SMS/GG WITH a fade: load_native is a real helper there (record +
            # scale per CRAM entry) and its caller, the generated
            # scenes.load_palettes, is BANKED - so the bank TU needs this. The
            # pointer is the console's own palette_color_t (1 B on the SMS,
            # 2 B on the Game Gear), which is the width trap the colour work
            # documents: a uint16_t here would misread the SMS staging buffer.
            self.emit("void gbs_pal_native(uint8_t first, uint8_t count,")
            self.emit("                    const palette_color_t *data);")
        if self.load_sprite16_used:
            self.emit("void gbs_load_sprite_pal16(const uint16_t *pal);")
        if self.load_bkg16_used:
            self.emit("void gbs_load_bkg_pal16(const uint16_t *pal);")
        if self.palette_fade_used:
            # (a banked rooms.mos / scripts path drives the fade seam)
            if self.prelude_bank_defs.get("gbs_pal_fade"):
                # LOCKSTEP with _cgb_fade_decls: on the CGB class the fade
                # engine itself may live in a ROM bank (review E-13), and then
                # its two entry points are BANKED - a plain prototype here
                # would make every bank TU call them as near functions.
                for line in self._cgb_fade_decls():
                    self.emit(line)
            else:
                self.emit("void gbs_pal_fade(uint8_t level);")
        if self.bkg_attrs_used and self.platform not in ('gameboy_color',
                                                         'analogue_pocket'):
            # (on the CGB class bkg.set_attrs lowers onto GBDK's own
            # set_bkg_attributes, an inline every TU already has)
            self.emit("void gbs_bkg_attrs(uint8_t x, uint8_t y, uint8_t w, uint8_t h,")
            self.emit("                   const uint8_t *data);")
        if self.edge_mask_used:
            # LOCKSTEP: a gated prelude helper needs its prototype here too,
            # or a banked rooms.mos / engine module fails to compile.
            self.emit("void gbs_bkg_edge_mask(uint8_t on);")
        if self.bkg_native_used:
            self.emit("void gbs_bkg_data_native(uint8_t first, uint8_t count,")
            self.emit("                         const uint8_t *data);")
        if self.raster_used:
            # LOCKSTEP: a banked module that calls bkg.raster* needs these.
            self._emit_gbdk_raster_protos()
        if self.cpu_fast_used:
            self.emit("void gbs_cpu_fast(uint8_t on);")
        if self.parallax_used:
            # The LOCKSTEP obligation for a gated prelude helper: without these
            # a banked rooms.mos / engine module fails to compile.
            self.emit("void gbs_px_arm(uint8_t n);")
            self.emit("void gbs_px_band(uint8_t i, uint8_t last);")
            self.emit("void gbs_px_scx(uint8_t i, uint8_t scx);")
            self.emit("void gbs_px_scy_set(uint8_t scy);")
        if self.bkg_move_used and (self.caps.get('has_gb_regs')
                                   or self.platform in ('sms', 'gamegear')):
            # bkg.move lowers onto the v-blank-committed shadow on the GB
            # register model AND on SMS/GG (see the swap in generator.py); a
            # bank TU calling it needs the prototype like every other gated
            # helper (the LOCKSTEP obligation).
            self.emit("void gbs_scroll_move(uint8_t x, uint8_t y);")
            if self.bkg_scroll_used:
                self.emit("void gbs_scroll_bkg(int8_t dx, int8_t dy);")
        if self.palette_set_used:
            self.emit("void gbs_load_bkg_set(uint8_t slot, uint8_t count,")
            self.emit("                      const uint16_t *colors, uint16_t off);")
            self.emit("void gbs_load_spr_set(uint8_t slot, uint8_t count,")
            self.emit("                      const uint16_t *colors, uint16_t off);")
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
        if self.sound_beep2_used:
            # The opt-in SECOND (music) voice, gated exactly like its
            # definition in gbdk_features (`mc = self.sound_beep2_used`).
            # Missing here, banking `vm.snd` failed with sdcc's "too many
            # parameters" - the same lockstep trap the text helpers hit.
            self.emit("void gbs_sound_stop2(void);")
            self.emit("void gbs_sound_beep2(uint16_t freq, uint16_t frames);")
        self.emit("void gbs_wait_vblank(void);")
        if self.asset_far_bank:
            # LOCKSTEP (the fourth time this trap has bitten): the generated
            # `rooms` module BANKS, and its loader is what calls this.
            self.emit("void gbs_spr_data_far(uint8_t first, uint8_t count, uint8_t bank, const uint8_t *data);")
        # LOCKSTEP: the hUGEDriver helpers are GATED on native.huge being
        # imported, so a TU that BANKS `vm.music_huge` needs their prototypes
        # under the SAME flag - without them sdcc reads each call as an
        # implicit int-returning function and rejects the arguments with
        # "too many parameters". That is the third time this trap has bitten
        # (the text helpers and vm.snd's second voice were the first two), and
        # it is what kept the driver bind resident on every hUGE conversion.
        if self.native_huge_imported:
            self.emit("void gbs_huge_play(uint8_t song);")
            self.emit("void gbs_huge_update(void);")
            self.emit("void gbs_huge_stop(void);")
            self.emit("void gbs_huge_mute(uint8_t mask);")
            self.emit("void gbs_huge_setpos(uint8_t order, uint8_t row);")
            self.emit("void gbs_huge_set_rate(uint8_t hz);")
        if self.huge_routines_used:
            # LOCKSTEP: vm.music_huge banks under [build] code_banks, and it is
            # the caller.
            self.emit("uint16_t gbs_huge_routine_next(void);")
        if self.frames_used:
            # LOCKSTEP: a gated prelude helper needs its prototype under the
            # SAME flag, or the bank TUs fail to compile.
            self.emit("uint8_t gbs_frames(void);")
        if self.music_isr_used:
            # Same lockstep rule: `core.set_music_driver` banks under
            # `[build] code_banks` (it is a cold seam setter), so its TU needs
            # the prototype.
            self.emit("void gbs_music_isr_wire(void (*fn)(void));")
        if self.music_hold_used:
            # ...and vm.music (the caller of the hold) banks too.
            self.emit("void gbs_music_hold(uint8_t on);")
        if self.music_tick_used:
            # ...and the main-loop tick is called from vm.core's music_pump,
            # which banks under [build] code_banks.
            self.emit("void gbs_music_tick(void);")
        if self.save_imported and self.caps['has_save']:
            # DEFINED here (static inline), not declared: see _emit_gbdk_save.
            self._emit_gbdk_save()
        if self.palette_imported:
            if self.platform == 'gameboy_color':
                _rgb_proto = self._gbs_rgb_needed()
            elif self.platform in ('sms', 'gamegear'):
                _rgb_proto = self._sms_rgb_needed()
            else:
                _rgb_proto = True
            if _rgb_proto:
                self.emit("uint16_t gbs_rgb(uint8_t r, uint8_t g, uint8_t b);")
            if self.palette_setter_used:
                self.emit("void gbs_set_bkg_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3);")
                self.emit("void gbs_set_spr_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3);")
            if self.palette_load_used:
                self.emit("void gbs_load_bkg_palette(uint8_t slot, const uint16_t *colors);")
                self.emit("void gbs_load_spr_palette(uint8_t slot, const uint16_t *colors);")
            if self.sprite_palette_used:
                self.emit("void gbs_sprite_palette(uint8_t nb, uint8_t slot);")
            if self.caps['has_tile_palettes'] and self.bkg_palette_fill_used:
                self.emit("void gbs_bkg_palette_fill(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t slot);")
        self.emit("")


    def _emit_clip_bounds(self):
        """Hoist sprite.move_world's column range into the fan's locals.

        `gbs_clip_hi == 0` is the NO-CLIP state (plain BSS, and what
        move_world restores on the way out), so a plain `sprite.move` keeps
        drawing every column. Hoisted because this loop IS the frame - the
        per-column cost is then two compares against a local."""
        if not self.meta_clip_used:
            return
        self.emit("        uint8_t clo = gbs_clip_lo;")
        self.emit("        uint8_t chi = gbs_clip_hi ? gbs_clip_hi : w;")

    def _emit_gbdk_move_world(self):
        """`sprite.move_world` - sprite.move in SIGNED screen coordinates, and
        `sprite.meta_cols` - how wide the metasprite at a base is.

        The pair is what a WORLD-space renderer needs to scroll a metasprite
        off an edge without ghosting: `meta_cols` tells it how far past the
        edge the actor may go before nothing of it is on screen (so it stops
        parking a 72 px actor that is still 7/9 visible), and `move_world`
        keeps the sign that `sprite.move`'s uint8_t destroys, so the fan can
        PARK the columns that really are outside instead of letting their u8
        position wrap onto the opposite edge.

        The sign is resolved HERE, once per metasprite, into a column range in
        FAN order - so FLIP_X costs the walk nothing and the walk itself stays
        8-bit. A column is drawable exactly while its hardware x is
        representable: `x + DEVICE_SPRITE_PX_OFFSET_X` in 0..255. That offset
        is the console's negative headroom and it differs wildly (8 on the GB,
        0 on the SMS, 48 on the Game Gear, whose viewport is a centre crop of
        the same plane), which is why this cannot be a constant margin in the
        caller - and why the SMS shows the bug worst.

        The bounds are a ONE-SHOT: cleared on the way out, so plain
        `sprite.move` is untouched and `gbs_clip_hi == 0` (plain BSS) is the
        no-clip state."""
        if not self.meta_clip_used:
            return
        if self._needs_meta('gbs_meta_cols'):
            self.emit("/* sprite.meta_cols: the metasprite width in 8x8 tile columns. */")
            self.emit("static uint8_t gbs_meta_cols(uint8_t nb) {")
            self.emit("    uint8_t w;")
            self.emit("    if (nb >= GBS_META_SLOTS) return 1;")
            self.emit("    w = gbs_meta_w[nb];")
            if self.meta_list_used:
                self.emit("    if (w == 0xFF) return (uint8_t)((gbs_meta_pw[nb] + 7) >> 3);")
            self.emit("    return w ? w : 1;")
            self.emit("}")
        if not self._needs_meta('gbs_move_sprite_world'):
            return
        self.emit("/* sprite.move_world: sprite.move in SIGNED screen coordinates -")
        self.emit("   see the emitter's note. Resolves the sign into a fan-order column")
        self.emit("   range, moves, and clears it again (a one-shot, so plain")
        self.emit("   sprite.move keeps its own behaviour). */")
        self.emit("static void gbs_move_sprite_world(uint8_t nb, int16_t x, int16_t y) {")
        self.emit("    uint8_t w, h, lo, hi, t;")
        self.emit("    int16_t n;")
        self.emit("    if (nb >= GBS_META_SLOTS) return;")
        self.emit("    w = gbs_meta_w[nb]; h = gbs_meta_h[nb];")
        if self.meta_list_used:
            # A LIST base's children sit at authored dx, not on a column grid,
            # so its bound is in dx units - resolved to two u8 thresholds here
            # so the per-object test in the fan stays 8-bit. dhi == 0 = none.
            self.emit("    if (w == 0xFF) {")
            self.emit("        n = (int16_t)(0 - DEVICE_SPRITE_PX_OFFSET_X) - x;")
            self.emit("        gbs_clip_dlo = (n > 0) ? ((n > 255) ? 255 : (uint8_t)n) : 0;")
            self.emit("        n = (int16_t)(255 - DEVICE_SPRITE_PX_OFFSET_X) - x;")
            self.emit("        gbs_clip_dhi = (n < 0) ? 1 : ((n > 254) ? 0 : (uint8_t)(n + 1));")
            self.emit("        gbs_move_sprite(nb, (uint8_t)x, (uint8_t)y);")
            self.emit("        gbs_clip_dhi = 0;")
            self.emit("        return;")
            self.emit("    }")
        # ONE hardware object: the fan's single-sprite path never consults the
        # column bounds, so it either fits or it is parked outright.
        self.emit("    if (w <= 1 && h <= 1) {")
        self.emit("        if (x < (int16_t)(0 - DEVICE_SPRITE_PX_OFFSET_X)")
        self.emit("            || x > (int16_t)(255 - DEVICE_SPRITE_PX_OFFSET_X))")
        self.emit("            move_sprite(nb, 0, GBS_SPR_PARK_Y);")
        self.emit("        else")
        self.emit("            gbs_move_sprite(nb, (uint8_t)x, (uint8_t)y);")
        self.emit("        return;")
        self.emit("    }")
        self.emit("    if (w == 0) w = 1;")
        # Column c spans hardware x = x + 8c + OFFSET. Drawable exactly while
        # that is representable in the u8 the hardware takes: `lo` counts the
        # columns below 0, `hi` is one past the last at or under 255. The
        # OFFSET is the console's negative headroom and differs wildly (8 on
        # the GB, 0 on the SMS, 48 on the Game Gear), which is why this cannot
        # be a constant margin in the caller.
        self.emit("    lo = 0; hi = w;")
        self.emit("    n = (int16_t)(0 - DEVICE_SPRITE_PX_OFFSET_X) - x;")
        self.emit("    if (n > 0) {")
        self.emit("        t = (uint8_t)((n + 7) >> 3);")
        self.emit("        lo = (t > w) ? w : t;")
        self.emit("    }")
        self.emit("    n = (int16_t)(255 - DEVICE_SPRITE_PX_OFFSET_X) - x;")
        self.emit("    if (n < 0) { lo = w; }")
        self.emit("    else if ((n >> 3) < (int16_t)w) { hi = (uint8_t)((n >> 3) + 1); }")
        self.emit("    if (lo > hi) lo = hi;")
        # THE COMMON CASE SETS NO CLIP AT ALL (2026-09-03): a metasprite wholly
        # on screen has lo == 0 and hi == w, and writing that into the clip
        # state - `gbs_clip_hi = w` - would make the plain fan take its clipped
        # walk for nothing. Left at the BSS no-clip state, the dispatcher in
        # gbs_move_sprite can hand it to the dense fast fan instead.
        self.emit("    if (lo == 0 && hi == w) {")
        self.emit("        gbs_move_sprite(nb, (uint8_t)x, (uint8_t)y);")
        self.emit("        return;")
        self.emit("    }")
        if self.caps['has_sprite_flip']:
            # FLIP_X reverses the fan, so authored column c is DRAWN at screen
            # column w-1-c. Mirroring the bounds here keeps the walk's test one
            # shape for both facings.
            self.emit("    if (gbs_meta_prop[nb] & FLIP_X) {")
            self.emit("        t = (uint8_t)(w - hi); hi = (uint8_t)(w - lo); lo = t;")
            self.emit("    }")
        self.emit("    gbs_clip_lo = lo;")
        self.emit("    gbs_clip_hi = hi ? hi : w;   /* 0 is the NO-CLIP sentinel */")
        self.emit("    gbs_move_sprite(nb, (uint8_t)x, (uint8_t)y);")
        self.emit("    gbs_clip_lo = 0;")
        self.emit("    gbs_clip_hi = 0;")
        self.emit("}")

    def _emit_gbdk_move_sprite(self):
        """sprite.move: the metasprite fan, a per-TU `static` definition
        emitted only into TUs whose code calls it (_tu_meta_needs) - so on a
        project whose renderers all bank, the fan leaves bank 0 entirely and
        each calling bank carries its own copy (an intra-bank direct call, no
        trampoline on the per-frame path). Must be emitted AFTER the meta
        tables (it reads them when metasprites are in use)."""
        if not self._needs_meta('gbs_move_sprite'):
            return
        if self.metasprite_used:
            self._emit_gbdk_move_fan_fast()
            self._emit_gbdk_move_list_fast()
        self.emit("/* sprite.move takes screen-pixel coordinates (origin = top-left of")
        self.emit("   the visible screen); the hardware offset differs per console. */")
        if self.metasprite_used:
            self.emit("static void gbs_move_sprite_full(uint8_t nb, uint8_t x, uint8_t y) {")
        else:
            self.emit("static void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y) {")
        # THE CHILD LOOP IS THE FRAME (measured 2026-08-11 on the reference-engine sample conversion: a
        # 7x6 actor is 21 OAM objects and `vm.actor.render` was 26.4k cycles a
        # frame for two actors, ~1,150 per child). So the per-child body is
        # WALKED, not computed: the flip decision and the cell pitch are
        # LOOP-INVARIANT, and hoisting them turns two ternaries, two shifts and
        # two adds per child into one add. The wrapping is identical - every
        # value is uint8 and the old form was `(uint8_t)(x + cc * 8 + OFF)`,
        # which is the same modulo 256 as accumulating `+= 8` from `x + OFF`.
        # A NEGATIVE step is `+= 0xF8` in uint8, so no signed arithmetic
        # appears (sm83 has none, and cc65's would cost a promotion).
        if self.metasprite_used and self._obj16():
            # 8x16 OBJ mode: a WxH TILE block is W*(H/2) objects laid out as
            # 8x16 cells, enumerated COLUMN-major to match the tile fan
            # (object k = column k/h2, pair k%h2 -- the loop order avoids the
            # sm83 division).
            self.emit("    uint8_t w = nb < GBS_META_SLOTS ? gbs_meta_w[nb] : 1;")
            self.emit("    uint8_t h = nb < GBS_META_SLOTS ? gbs_meta_h[nb] : 1;")
            self._emit_gbdk_move_list_arm()
            self.emit("    if (w > 1 || h > 1) {")
            self.emit("        /* 8x16 metasprite: W columns of H/2 objects, 16 px per row-pair,")
            self.emit("           column-major like the tile fan; flips reverse cells. The")
            self.emit("           flip and the pitch are hoisted -- see the emitter's note. */")
            self.emit("        uint8_t c, p, h2 = (uint8_t)(h >> 1), s = nb, prop = gbs_meta_prop[nb];")
            self.emit("        uint8_t cx = (uint8_t)(x + DEVICE_SPRITE_PX_OFFSET_X), xs = 8;")
            self.emit("        uint8_t y0 = (uint8_t)(y + DEVICE_SPRITE_PX_OFFSET_Y), ys = 16, cy;")
            self._emit_clip_bounds()
            if self.meta_mask_used:
                self.emit("        uint16_t m = gbs_meta_msk[nb];")
            if self.caps['has_sprite_flip']:
                self.emit("        if (prop & FLIP_X) { cx = (uint8_t)(cx + (w - 1) * 8); xs = (uint8_t)-8; }")
                self.emit("        if (prop & FLIP_Y) { y0 = (uint8_t)(y0 + (h2 - 1) * 16); ys = (uint8_t)-16; }")
            else:
                # SMS / Game Gear: 8x16 is a VDP sprite-SIZE bit and buys the
                # object halving, not a mirror -- the hardware has no flip at
                # all (HARDWARE_SPRITE_CAN_FLIP_X is 0 there). Reversing the
                # cell order without mirroring the pixels garbles the block,
                # so the layout never reverses and a left-facing pose is real
                # art (the soft-flip bake). Same rule as the 8x8 arm below.
                self.emit("        /* no hardware sprite flip on this console: never reverse cells */")
            if self.meta_mask_used:
                # The masked variant is its own loop rather than a per-child
                # test: this loop IS the frame (see the note above), and every
                # metasprite in a game that uses one sparse frame would
                # otherwise pay for it. A blank column's objects STAY parked --
                # set_meta_mask put them at (0,0) and moving them with the rest
                # would drop them back onto the scanline.
                self.emit("        if (m) {")
                self.emit("            for (c = 0; c < w; ++c) {")
                if self.meta_clip_used:
                    # A clipped column must be PARKED, not skipped: unlike a
                    # blank one (which set_meta_mask already parked and which
                    # must stay put), this one was DRAWN last frame and has
                    # just left the screen.
                    self.emit("                if (c < clo || c >= chi) {")
                    self.emit("                    for (p = 0; p < h2; ++p) "
                              "{ move_sprite(s, 0, GBS_SPR_PARK_Y); ++s; }")
                    self.emit("                } else if (m & 1) { s = (uint8_t)(s + h2); }")
                else:
                    self.emit("                if (m & 1) { s = (uint8_t)(s + h2); }")
                self.emit("                else {")
                self.emit("                    cy = y0;")
                self.emit("                    for (p = 0; p < h2; ++p) {")
                self.emit("                        move_sprite(s, cx, cy);")
                self.emit("                        cy = (uint8_t)(cy + ys);")
                self.emit("                        ++s;")
                self.emit("                    }")
                self.emit("                }")
                self.emit("                cx = (uint8_t)(cx + xs);")
                self.emit("                m >>= 1;")
                self.emit("            }")
                self.emit("            return;")
                self.emit("        }")
            self.emit("        for (c = 0; c < w; ++c) {")
            self.emit("            cy = y0;")
            if self.meta_clip_used:
                self.emit("            if (c < clo || c >= chi) {")
                self.emit("                for (p = 0; p < h2; ++p) "
                          "{ move_sprite(s, 0, GBS_SPR_PARK_Y); ++s; }")
                self.emit("                cx = (uint8_t)(cx + xs);")
                self.emit("                continue;")
                self.emit("            }")
            self.emit("            for (p = 0; p < h2; ++p) {")
            self.emit("                move_sprite(s, cx, cy);")
            self.emit("                cy = (uint8_t)(cy + ys);")
            self.emit("                ++s;")
            self.emit("            }")
            self.emit("            cx = (uint8_t)(cx + xs);")
            self.emit("        }")
            self.emit("        return;")
            self.emit("    }")
        elif self.metasprite_used:
            self.emit("    uint8_t w = nb < GBS_META_SLOTS ? gbs_meta_w[nb] : 1;")
            self.emit("    uint8_t h = nb < GBS_META_SLOTS ? gbs_meta_h[nb] : 1;")
            self._emit_gbdk_move_list_arm()
            self.emit("    if (w > 1 || h > 1) {")
            self.emit("        /* Metasprite: lay out the reserved child slots in an w*h")
            self.emit("           grid of 8x8 cells, reversing columns/rows when flipped. */")
            self.emit("        uint8_t r, c, s = nb, prop = gbs_meta_prop[nb];")
            self.emit("        uint8_t x0 = (uint8_t)(x + DEVICE_SPRITE_PX_OFFSET_X), xs = 8, cx;")
            self.emit("        uint8_t cy = (uint8_t)(y + DEVICE_SPRITE_PX_OFFSET_Y), ys = 8;")
            self._emit_clip_bounds()
            if self.caps['has_sprite_flip']:
                self.emit("        if (prop & FLIP_X) { x0 = (uint8_t)(x0 + (w - 1) * 8); xs = (uint8_t)-8; }")
                self.emit("        if (prop & FLIP_Y) { cy = (uint8_t)(cy + (h - 1) * 8); ys = (uint8_t)-8; }")
            else:
                # SMS / Game Gear have no hardware sprite flip: the per-cell tiles
                # can't be mirrored, so reversing the cell layout would just garble
                # the block. Keep the normal layout (the sprite shows unflipped --
                # use dedicated/pre-mirrored frames for facing here).
                self.emit("        /* no hardware sprite flip on this console: never reverse cells */")
            if self.meta_mask_used:
                # Its own loop, not a per-child test -- see the 8x16 note.
                self.emit("        {")
                self.emit("        uint16_t msk = gbs_meta_msk[nb], m;")
                self.emit("        if (msk) {")
                self.emit("            for (r = 0; r < h; ++r) {")
                self.emit("                cx = x0; m = msk;")
                self.emit("                for (c = 0; c < w; ++c) {")
                if self.meta_clip_used:
                    self.emit("                    if (c < clo || c >= chi) "
                              "move_sprite(s, 0, GBS_SPR_PARK_Y);")
                    self.emit("                    else if (!(m & 1)) move_sprite(s, cx, cy);")
                else:
                    self.emit("                    if (!(m & 1)) move_sprite(s, cx, cy);")
                self.emit("                    cx = (uint8_t)(cx + xs);")
                self.emit("                    m >>= 1;")
                self.emit("                    ++s;")
                self.emit("                }")
                self.emit("                cy = (uint8_t)(cy + ys);")
                self.emit("            }")
                self.emit("            return;")
                self.emit("        }")
                self.emit("        }")
            self.emit("        for (r = 0; r < h; ++r) {")
            self.emit("            cx = x0;")
            self.emit("            for (c = 0; c < w; ++c) {")
            if self.meta_clip_used:
                self.emit("                if (c < clo || c >= chi) "
                          "move_sprite(s, 0, GBS_SPR_PARK_Y);")
                self.emit("                else move_sprite(s, cx, cy);")
            else:
                self.emit("                move_sprite(s, cx, cy);")
            self.emit("                cx = (uint8_t)(cx + xs);")
            self.emit("                ++s;")
            self.emit("            }")
            self.emit("            cy = (uint8_t)(cy + ys);")
            self.emit("        }")
            self.emit("        return;")
            self.emit("    }")
        self.emit("    move_sprite(nb, (uint8_t)(x + DEVICE_SPRITE_PX_OFFSET_X),")
        self.emit("                    (uint8_t)(y + DEVICE_SPRITE_PX_OFFSET_Y));")
        self.emit("}")
        if self.metasprite_used:
            self._emit_gbdk_move_dispatch()

    def _emit_gbdk_move_fan_fast(self):
        """THE DENSE FAST FAN (2026-09-03). The general fan below handles
        lists, flips, masks and edge clips in ONE body, and sdcc compiles
        that body under `add sp, #-20` with 723 instructions: every array
        read goes through the stack frame. Measured on the reference-engine sample conversion's
        shooter room (per-position sequence spans): a 2x2 actor fan - two
        OAM objects - cost ~5,000 T-cycles a move through it, the largest
        single per-actor item left in `actor.render`.

        The frame size is a property of the FUNCTION, so the common case gets
        its own: a dense WxH block, no mask, no edge clip, no FLIP_Y - which
        is every actor and the player on every frame they are wholly on
        screen. FLIP_X is one compare and rides along (an actor facing left
        is as common as one facing right). Everything else takes the full
        body, unchanged."""
        self.emit("/* The dense fast fan: WxH block, no mask, no clip, no FLIP_Y.")
        self.emit("   Its own function so its frame stays small - see the emitter. */")
        self.emit("static void gbs_move_fan(uint8_t nb, uint8_t x, uint8_t y) {")
        if self._obj16():
            self.emit("    uint8_t w = gbs_meta_w[nb], n = (uint8_t)(gbs_meta_h[nb] >> 1), c, p, cy, xs = 8;")
        else:
            self.emit("    uint8_t w = gbs_meta_w[nb], n = gbs_meta_h[nb], c, p, cy, xs = 8;")
        self.emit("    x = (uint8_t)(x + DEVICE_SPRITE_PX_OFFSET_X);")
        self.emit("    y = (uint8_t)(y + DEVICE_SPRITE_PX_OFFSET_Y);")
        if self.caps['has_sprite_flip']:
            self.emit("    if (gbs_meta_prop[nb] & FLIP_X) { x = (uint8_t)(x + (w - 1) * 8); xs = (uint8_t)-8; }")
        if self._obj16():
            # column-major: W columns of H/2 objects, 16 px apart
            self.emit("    for (c = 0; c < w; ++c) {")
            self.emit("        cy = y;")
            self.emit("        for (p = 0; p < n; ++p) { move_sprite(nb, x, cy); cy = (uint8_t)(cy + 16); ++nb; }")
            self.emit("        x = (uint8_t)(x + xs);")
            self.emit("    }")
        else:
            # row-major: H rows of W cells, 8 px apart
            self.emit("    cy = y;")
            self.emit("    for (p = 0; p < n; ++p) {")
            self.emit("        uint8_t cx = x;")
            self.emit("        for (c = 0; c < w; ++c) { move_sprite(nb, cx, cy); cx = (uint8_t)(cx + xs); ++nb; }")
            self.emit("        cy = (uint8_t)(cy + 8);")
            self.emit("    }")
        self.emit("}")

    def _emit_gbdk_move_list_fast(self):
        """The LIST fast path (see _emit_gbdk_move_fan_fast): a descriptor
        base with no FLIP_X and no edge clip - every shot in flight and every
        descriptor HUD element, on every frame. The hide path is kept: a
        hidden base parks every child at the fixed off-screen y (see the
        list arm's note on why not the requested one)."""
        if not self.meta_list_used:
            return
        self.emit("/* The list fast path: a descriptor base, no FLIP_X, no clip. */")
        self.emit("static void gbs_move_list(uint8_t nb, uint8_t x, uint8_t y) {")
        # One cursor and an END, not a cursor plus a counter plus a limit:
        # `nb, x, y, n, k` is five live bytes and sdcc opened the body at
        # `add sp, #-11`, so the two array reads were addressed from a spilled
        # index (2,470 T-cycles for a two-object shot, measured).
        self.emit("    uint8_t e = (uint8_t)(nb + gbs_meta_h[nb]);")
        self.emit("    if (y >= SCREEN_HEIGHT) {")
        self.emit("        for (; nb < e; ++nb) move_sprite(nb, 0, GBS_SPR_PARK_Y);")
        self.emit("        return;")
        self.emit("    }")
        self.emit("    x = (uint8_t)(x + DEVICE_SPRITE_PX_OFFSET_X);")
        self.emit("    y = (uint8_t)(y + DEVICE_SPRITE_PX_OFFSET_Y);")
        self.emit("    for (; nb < e; ++nb)")
        self.emit("        move_sprite(nb, (uint8_t)(x + gbs_meta_dx[nb]), (uint8_t)(y + gbs_meta_dy[nb]));")
        self.emit("}")

    def _emit_gbdk_move_dispatch(self):
        """`sprite.move` itself: hand a dense unclipped block to the fast fan
        and an unflipped unclipped list to the list fast path; everything
        else to the full body (see _emit_gbdk_move_fan_fast). The clip tests
        read the ONE-SHOT state move_world sets, which is 0 - the BSS no-clip
        state - whenever the metasprite is wholly on screen."""
        self.emit("static void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y) {")
        self.emit("    if (nb < GBS_META_SLOTS) {")
        self.emit("        uint8_t w = gbs_meta_w[nb], h = gbs_meta_h[nb];")
        if self.meta_list_used:
            conds = ["w == 0xFF"]
            if self.meta_clip_used:
                conds.append("gbs_clip_dhi == 0")
            if self.caps['has_sprite_flip']:
                conds.append("!(gbs_meta_prop[nb] & FLIP_X)")
            self.emit("        if (%s) {" % " && ".join(conds))
            self.emit("            gbs_move_list(nb, x, y);")
            self.emit("            return;")
            self.emit("        }")
        conds = ["(w > 1 || h > 1)"]
        if self.meta_list_used:
            conds.append("w != 0xFF")
        if self.meta_mask_used:
            conds.append("gbs_meta_msk[nb] == 0")
        if self.meta_clip_used:
            conds.append("gbs_clip_hi == 0")
        if self.caps['has_sprite_flip']:
            conds.append("!(gbs_meta_prop[nb] & FLIP_Y)")
        self.emit("        if (%s) {" % " && ".join(conds))
        self.emit("            gbs_move_fan(nb, x, y);")
        self.emit("            return;")
        self.emit("        }")
        self.emit("    }")
        self.emit("    gbs_move_sprite_full(nb, x, y);")
        self.emit("}")

    def _emit_gbdk_move_list_arm(self):
        """The LIST arm of the move fan (sprite.set_meta_list frames).

        A list-shaped base is marked `gbs_meta_w[base] == 0xFF` and carries its
        object COUNT in gbs_meta_h[base]; each child's authored offset was
        stored per SLOT at set time (gbs_meta_dx/dy), so the move is one add
        per axis per child - the same walked shape as the dense fan. FLIP_X
        mirrors around the frame's pixel width (gbs_meta_pw); there is no list
        FLIP_Y (the importer bakes vertical flips into the cells). A hidden
        base (y >= SCREEN_HEIGHT, and park's 200) parks every child at a fixed
        OFF-SCREEN y rather than following the requested one: the dense fan's
        y+16+row*8 stays under 256, but an authored dy can be large enough that
        200+16+dy WRAPS back onto the screen.

        **That park is `(0, GBS_SPR_PARK_Y)`, not `(0, 0)`.** On the Game Boy
        OAM is biased by (8, 16), so y = 0 is above the screen and (0, 0) reads
        as hidden - which is why this was right for six months and wrong the
        moment a descriptor kind was drawn on a z80 port, where the SAT is
        written directly and (0, 0) is the VISIBLE top-left corner. Measured on
        the shooter conversion (SMS): every hidden descriptor actor stacked its children
        in the corner as a white block. The Game Gear hid it by accident (its
        160x144 viewport is a centre crop of the 256x192 plane)."""
        if not self.meta_list_used:
            return
        self.emit("    if (w == 0xFF) {   /* list-shaped: authored per-object offsets */")
        self.emit("        uint8_t k, n = h, s = nb, bx, by;")
        if self.meta_clip_used:
            # A list child sits at an authored dx, not on a column grid, so
            # move_world resolves its bound into dx units. dhi == 0 = no clip.
            self.emit("        uint8_t dlo = gbs_clip_dlo, dhi = gbs_clip_dhi;")
        self.emit("        if (y >= SCREEN_HEIGHT) {")
        self.emit("            for (k = 0; k < n; ++k) "
                      "{ move_sprite(s, 0, GBS_SPR_PARK_Y); ++s; }")
        self.emit("            return;")
        self.emit("        }")
        self.emit("        bx = (uint8_t)(x + DEVICE_SPRITE_PX_OFFSET_X);")
        self.emit("        by = (uint8_t)(y + DEVICE_SPRITE_PX_OFFSET_Y);")
        if self.caps['has_sprite_flip']:
            self.emit("        if (gbs_meta_prop[nb] & FLIP_X) {")
            self.emit("            uint8_t fw = (uint8_t)(bx + gbs_meta_pw[nb] - 8);")
            self.emit("            for (k = 0; k < n; ++k) {")
            if self.meta_clip_used:
                # Mirrored, a child's SCREEN offset is `pw - 8 - dx`, so the
                # bounds move_world resolved in dx terms mirror with it.
                self.emit("                uint8_t dm = (uint8_t)(gbs_meta_pw[nb] - 8 - gbs_meta_dx[s]);")
                self.emit("                if (dm < dlo || (dhi && dm >= dhi))")
                self.emit("                    move_sprite(s, 0, GBS_SPR_PARK_Y);")
                self.emit("                else")
                self.emit("                move_sprite(s, (uint8_t)(fw - gbs_meta_dx[s]),")
                self.emit("                            (uint8_t)(by + gbs_meta_dy[s]));")
            else:
                self.emit("                move_sprite(s, (uint8_t)(fw - gbs_meta_dx[s]),")
                self.emit("                            (uint8_t)(by + gbs_meta_dy[s]));")
            self.emit("                ++s;")
            self.emit("            }")
            self.emit("            return;")
            self.emit("        }")
        else:
            self.emit("        /* no hardware sprite flip on this console: never mirror */")
        self.emit("        for (k = 0; k < n; ++k) {")
        if self.meta_clip_used:
            self.emit("            if (gbs_meta_dx[s] < dlo || (dhi && gbs_meta_dx[s] >= dhi))")
            self.emit("                move_sprite(s, 0, GBS_SPR_PARK_Y);")
            self.emit("            else")
        self.emit("            move_sprite(s, (uint8_t)(bx + gbs_meta_dx[s]),")
        self.emit("                        (uint8_t)(by + gbs_meta_dy[s]));")
        self.emit("            ++s;")
        self.emit("        }")
        self.emit("        return;")
        self.emit("    }")

    def _emit_prelude_gbdk(self):
        cgb_class = self.platform in ('gameboy_color', 'analogue_pocket')
        self.emit("/* Generated by mosaik -> GBDK C backend */")
        self.emit("/* Target console: %s */" % self.platform)
        self._emit_gbdk_includes()
        self._emit_gbdk_defines()
        self.emit("/* mosaik standard library helpers */")
        if cgb_class:
            # A CGB-flagged ROM (-Wm-yc) gets no boot-ROM compatibility
            # palette, so bkg/sprite palette RAM starts UNINITIALISED -- and in
            # CGB mode the hardware IGNORES BGP/OBP0/OBP1, the registers a DMG
            # build renders through. A program that never loads a colour
            # therefore drew its (correct) 2bpp art through whatever the boot
            # ROM left behind: the pixels were right and the palette was
            # garbage. Seed the three DMG-equivalent slots with the standard
            # grey ramp so a colourless program looks EXACTLY like its Game Boy
            # build, which is the portability contract.
            #
            # Called FIRST THING IN MAIN (gen_fnptr._emit_function), not from
            # gbs_enable_lcd, and never gated on whether graphics.palette is
            # imported. Both of those were wrong:
            #   - at enable_lcd it would clobber colours a program loads before
            #     it (samples/colors.mos, projects/background), which is why it
            #     carried a `not palette_imported` guard in the first place;
            #   - and that guard then disabled the seed for every program whose
            #     palette calls do not actually reach a CGB slot -- one inside
            #     an `if platform` fork for another console (projects/vm-shmup,
            #     Lynx-only), or one that is a GB-family NO-OP by design
            #     (palette.load_bkg16 / load_sprite16: projects/bkg16-demo,
            #     pal-lab, vm-bkg16). All four rendered through garbage.
            # At main entry there is nothing to clobber: every palette a
            # program loads, before or after enable_lcd, is written later and
            # wins.
            # (the body is emitted after the palette engine, below, so it can
            # go THROUGH the fade shadow when there is one)
            pass
        self.emit("void gbs_enable_lcd(void) { %sDISPLAY_ON; SHOW_BKG; }"
                  % ("SPRITES_8x16; " if self._obj16() else ""))
        self.emit("void gbs_disable_lcd(void) { DISPLAY_OFF; }")
        if self.platform not in ('sms', 'gamegear'):
            # The slot-rendered upload and the native-format palette load are
            # real on SMS/GG only (their VDP stores tiles at 4bpp and their
            # CRAM is a flat 16-entry run per layer); elsewhere the slot has
            # nowhere to go and the data uploads plain -- graceful
            # degradation, like the other palette verbs. Normally dead: the
            # scene transpiler emits these inside an `if platform ==
            # "sms"/"gamegear"` fork, which conditional compilation drops on
            # every other console.
            if self.bkg_data_pal_used:
                self.emit("void gbs_bkg_data_pal(uint8_t first, uint8_t count,")
                self.emit("                      const uint8_t *data, const uint8_t *slots) {")
                self.emit("    (void)slots; set_bkg_data(first, count, data);")
                self.emit("}")
            if self.pal_native_used:
                self.emit("void gbs_pal_native(uint8_t first, uint8_t count,")
                self.emit("                    const uint16_t *data) {")
                self.emit("    (void)first; (void)count; (void)data;")
                self.emit("}")
        if self.platform in ('sms', 'gamegear'):
            # The GB crt0 blanks its background map to tile 0 before main(); the
            # z80 SMS/GG port leaves the VDP name table uninitialised, so every
            # cell a program never paints shows VRAM garbage (random tile indices
            # that, once a font/tileset is loaded, render as stray glyphs). Blank
            # the full 32x28 name table at the top of main() (see _emit_function)
            # so unpainted cells match the Game Boy's blank tile 0.
            if self.text_used:
                self.emit("void gbs_text_init(void);")
            # Clamp bkg-tilemap writes to the 32x28 SMS/GG name table: a 32-row
            # map (GB geometry) otherwise overruns into the SAT and corrupts the
            # displayed background. See generator._build_stdlib for the routing.
            self.emit("/* SMS/GG name table is 32x28; clamp tilemap writes so a 32-row")
            self.emit("   GB-geometry map can't overrun the name table into the SAT. */")
            self.emit("void gbs_set_bkg_tiles(uint8_t x, uint8_t y, uint8_t w, uint8_t h,")
            self.emit("                       const uint8_t *tiles) {")
            self.emit("    if (y >= 28) return;")
            self.emit("    if ((uint8_t)(y + h) > 28) h = (uint8_t)(28 - y);")
            self.emit("    set_bkg_tiles(x, y, w, h, tiles);")
            self.emit("}")
            if self.load_bkg16_used:
                # 4bpp BACKGROUND tier: convert each packed-nibble tile (32 B =
                # 8 rows x 4 bytes, two pixels/byte, leftmost in the high nibble)
                # into the SMS/GG VDP native planar tile (8 rows x 4 bitplane
                # bytes, bit7 = leftmost pixel) and upload verbatim via
                # set_bkg_native_data (skips GBDK's _current_2bpp_palette expand).
                self.emit("/* packed-nibble 4bpp tile row -> one VDP bitplane byte (bit k of")
                self.emit("   each of the 8 pixels, bit7 = leftmost pixel). */")
                self.emit("static uint8_t gbs_bkg_plane(const uint8_t *r, uint8_t k) {")
                self.emit("    uint8_t out = 0, i, px;")
                self.emit("    for (i = 0; i < 8; ++i) {")
                self.emit("        px = (uint8_t)((i & 1) ? (r[i >> 1] & 0x0F) : (r[i >> 1] >> 4));")
                self.emit("        out = (uint8_t)(out | (uint8_t)(((px >> k) & 1) << (7 - i)));")
                self.emit("    }")
                self.emit("    return out;")
                self.emit("}")
                self.emit("void gbs_set_bkg_data(uint8_t first, uint8_t count, const uint8_t *data) {")
                self.emit("    static uint8_t buf[32];  /* one native planar tile */")
                self.emit("    uint8_t t, row, k;")
                self.emit("    const uint8_t *tile;")
                self.emit("    for (t = 0; t < count; ++t) {")
                self.emit("        tile = data + (uint16_t)t * 32;")
                self.emit("        for (row = 0; row < 8; ++row)")
                self.emit("            for (k = 0; k < 4; ++k)")
                self.emit("                buf[row * 4 + k] = gbs_bkg_plane(tile + (uint16_t)row * 4, k);")
                self.emit("        set_bkg_native_data((uint16_t)first + t, 1, buf);")
                self.emit("    }")
                self.emit("}")
            if self.bkg_data_pal_used:
                # bkg.set_data_pal: the SMS/GG per-TILE background COLOUR
                # path. Their VDP stores every tile at 4bpp, so the port's
                # 2bpp expansion (set_tile_2bpp_data's nibble map) can target
                # CRAM entries slot*4..slot*4+3 instead of the fixed 0..3 --
                # the same 2bpp art every other console uploads, rendered
                # through any of the 4 palettes a scene loaded.
                #
                # `slots` is one slot byte per tile, a RAM buffer the (banked)
                # scenes code fills from TILE_PAL: passing TWO banked const
                # arrays into one helper would leave the bank mapping to
                # argument evaluation order. The four nibble maps are a TABLE
                # rather than `slot*4 * 0x1111 + 0x3210` -- this runs per TILE
                # and a 16-bit multiply in z80 software is the most expensive
                # thing on the path, in a function that is bank-0 rent.
                # (Sprites need no helper: a sheet uploads whole through one
                # slot, so the generated code sets the port's own
                # `_current_2bpp_palette` with palette.set_2bpp and calls the
                # ordinary sprite.set_data.)
                self.emit("/* bkg.set_data_pal: upload 2bpp tiles, tile t rendered through BG")
                self.emit("   palette slot slots[t] (CRAM entries slot*4..slot*4+3). */")
                self.emit("static const uint16_t gbs_pal2bpp[4] = "
                          "{ 0x3210, 0x7654, 0xBA98, 0xFEDC };")
                self.emit("void gbs_bkg_data_pal(uint8_t first, uint8_t count,")
                self.emit("                      const uint8_t *data, const uint8_t *slots) {")
                self.emit("    uint16_t tile = first;")
                self.emit("    while (count--) {")
                self.emit("        set_tile_2bpp_data(tile++, 1, data, gbs_pal2bpp[*slots++ & 3]);")
                self.emit("        data += 16;")
                self.emit("    }")
                self.emit("}")
            self.emit("/* SMS/GG: blank the uninitialised name table (the GB crt0 does this")
            self.emit("   before main; the z80 port does not). Called first thing in main. */")
            self.emit("void gbs_sms_clear_bkg(void) {")
            if self.text_used:
                # The SMS stdio console (printf/gotoxy) must be set up BEFORE the
                # name table is wiped: if font_init()/the console initialises
                # AFTER fill_bkg_rect, its first printf garbles the whole map (the
                # clear lands on a name table the console then re-lays-out). So
                # run the lazy text init eagerly here, ahead of the clear -- the
                # later first-text-call gbs_text_init() is then a no-op.
                self.emit("    gbs_text_init();")
            self.emit("    fill_bkg_rect(0, 0, 32, 28, 0);")
            self.emit("}")
        if self.platform in ('sms', 'gamegear'):
            # THE MASK IS PER CONSOLE, because the two pads differ by exactly
            # one real button. Read it off GBDK's own joypad() routines rather
            # than off the shared J_* names, which are GB-compat aliases:
            #
            #   SMS (lib/sms):  in a,($DC) ... and $3F, then the port $3F
            #     TH-line dance. Bits 6/7 of $DC are PLAYER-2 up/down, and
            #     they do not read as "not pressed" for a 1-player pad -
            #     measured on genesis_plus_gx, pressing button 2 makes
            #     `joypad() & J_SELECT` true as well. Start there is the
            #     console's Pause, a separate NMI line, not a joypad bit.
            #   GAME GEAR (lib/gg):  in a,($DC), cpl, and $3F, then
            #     `and $20; rlca; rlca` - which MIRRORS button 2 (J_A) onto
            #     bit 7, so J_SELECT is the phantom here - and then
            #     `in a,($00); cpl; and $80; rrca`: port $00 bit 7 IS the
            #     Game Gear's own Start button (active low), rotated into
            #     bit 6 = J_START. So on this console Start is REAL and only
            #     Select is fake, which is why the mask keeps 0x40 and drops
            #     0x80 rather than being removed altogether.
            #
            # A phantom button is not merely cosmetic: a VM8 `input_attach`
            # fires on a rising edge, so a script attached to BOTH a and
            # select (what a reference-engine "await any input" converts to) ran
            # TWICE on one press and spawned two copies of itself - in the GB
            # Studio sample that opened the title menu twice, once on the
            # title screen and again inside the first room, because the second
            # copy sat parked on the UI latch and resumed after the scene
            # change.
            #
            # So mask each console to the bits it actually has. Masking the
            # Game Gear to 0x3F as well (which this did until 2026-08-12) does
            # the opposite harm: it throws away a button the hardware has, and
            # a reference-engine conversion attaches its Start menu to exactly that
            # one, so those rooms were unreachable there.
            if self.platform == 'gamegear':
                self.emit("#define GBS_PAD_MASK 0x7F   "
                          "/* d-pad + buttons 1/2 + START (port $00 bit 7); no Select */")
                self.emit("uint8_t gbs_input_pressed(uint8_t button) "
                          "{ return (uint8_t)(joypad() & button & GBS_PAD_MASK); }")
            else:
                # ...AND THEN GIVE THE SMS A START, because masking the phantom
                # bit off is only half the truth. The console HAS a Start: it is
                # the PAUSE button, wired to the z80's non-maskable interrupt
                # rather than to a pad bit, so `joypad()` can never report it. GB
                # Studio binds its title screens to Start by construction, so
                # every converted SMS ROM sat on its own title screen forever -
                # measured, 600 frames with Start held, the SMS/GG sample conversion's ROM
                # still showing PRESS START while the SAME project's `.gg`
                # reached the menu. (That is also the whole of the "no actor
                # animation on SMS" report: you never reach a scene with an
                # actor in it.)
                #
                # TWO ways in, because the console offers two. The pad's
                # button 1 is LABELLED "1 START" and is what an SMS title
                # screen is started with, so it also answers J_START; and the
                # console's PAUSE button is a real Start that no pad bit can
                # report, so it is latched below. A program that attaches a
                # script to BOTH `a` and `start` therefore sees one press of
                # button 1 twice - the phantom-button hazard J_SELECT already
                # documents - but the alternative is a console whose titles
                # cannot be started with a button at all.
                #
                # For the NMI half, GBDK's own override point is the SYMBOL
                # NAME: `lib/sms/nmi.o` defines
                # `_NMI_ISR` as a two-byte RETN stub, and the linker drops that
                # module the moment something else defines it, which is why the
                # name must be exactly this (gbdk/examples/sms/pause_button).
                #
                # The latch is HELD for a few display frames rather than being
                # cleared on read, and that is the load-bearing part: an NMI is
                # an edge and our readers want a level. A VM8 `input_attach`
                # fires on a rising edge it computes from two consecutive polls,
                # and several readers poll within one frame, so a clear-on-read
                # latch would be consumed by whoever asked first and the edge
                # would never be seen. Holding it for GBS_SMS_PAUSE_FRAMES makes
                # one pause press look exactly like one short button press to
                # every reader, and pressing pause again re-latches.
                self.emit("#define GBS_PAD_MASK 0x3F   "
                          "/* d-pad + buttons 1/2; no Select, and no Start in "
                          "the pad - it is the PAUSE NMI, latched below */")
                self.emit("#define GBS_SMS_PAUSE_FRAMES 8   "
                          "/* display frames one pause press reads as held */")
                self.emit("volatile uint8_t gbs_sms_pause;")
                self.emit("volatile uint8_t gbs_sms_pause_t0;")
                self.emit("/* The console PAUSE button. The name MUST be "
                          "NMI_ISR - it replaces GBDK's RETN stub. */")
                self.emit("void NMI_ISR(void) CRITICAL INTERRUPT {")
                self.emit("    gbs_sms_pause = 1;")
                self.emit("    gbs_sms_pause_t0 = (uint8_t)sys_time;")
                self.emit("}")
                self.emit("uint8_t gbs_input_pressed(uint8_t button) {")
                self.emit("    uint8_t p = (uint8_t)(joypad() & GBS_PAD_MASK);")
                if self.sms_start_button:
                    self.emit("    /* [build] sms_start_button: button 1 is "
                              "labelled \"1 START\" on the pad. */")
                    self.emit("    if (p & J_A) p |= J_START;")
                self.emit("    if (gbs_sms_pause) {")
                self.emit("        if ((uint8_t)((uint8_t)sys_time - "
                          "gbs_sms_pause_t0) >= GBS_SMS_PAUSE_FRAMES) "
                          "gbs_sms_pause = 0;")
                self.emit("        else p |= J_START;")
                self.emit("    }")
                self.emit("    return (uint8_t)(p & button);")
                self.emit("}")
        else:
            self.emit("uint8_t gbs_input_pressed(uint8_t button) { return (uint8_t)(joypad() & button); }")
        if self.input_raw_used:
            # The whole (console-mapped) pad word in one read: the same body
            # as `pressed`, unmasked, so the SMS START synthesis and pause
            # latch above apply to it too.
            self.emit("uint8_t gbs_input_raw(void) { return gbs_input_pressed(0xFF); }")
        # BEFORE the text block and before the parallax verbs: both call
        # into it, and `gbs_lyc_wire` / `gbs_lyc_rebuild` are file-static.
        # It forward-declares `gbs_spr_want`, which is defined further down
        # with the show/hide verbs that own the program's own sprite state.
        self._emit_gbdk_lyc()
        self._emit_gbdk_text()
        self.emit("/* Raw hardware register access (sound, palette, timer, ...) */")
        self.emit("void gbs_hw_write(uint16_t addr, uint8_t value) { *(volatile uint8_t *)addr = value; }")
        self.emit("uint8_t gbs_hw_read(uint16_t addr) { return *(volatile uint8_t *)addr; }")
        if self.bank_enter_used:
            self.emit("/* assets.bank_enter: map a data blob's ROM bank for a WINDOW of")
            self.emit("   direct reads (the VM8 code window), returning the bank to restore. */")
            self.emit("static uint8_t gbs_bank_enter(uint8_t b) { uint8_t o = CURRENT_BANK; SWITCH_ROM(b); return o; }")
        if self.asset_far_bank:
            # A SPRITE SHEET LIVES IN A DATA BANK (see
            # _collect_bank_local_consts): map it, upload, restore. RESIDENT
            # and bank-neutral, because the caller is normally the generated
            # `rooms.load_room`, which is itself BANKED - its own window is
            # switched away for the duration of the upload, which is safe
            # only because this helper is in the always-mapped home bank.
            self.emit("/* sprite.set_data of a banked sheet: map the sheet's bank, upload,")
            self.emit("   restore the caller's (the caller may be banked itself). */")
            self.emit("void gbs_spr_data_far(uint8_t first, uint8_t count, uint8_t bank, const uint8_t *data) {")
            self.emit("    uint8_t save = CURRENT_BANK;")
            self.emit("    SWITCH_ROM(bank);")
            self.emit("    set_sprite_data(first, count, data);")
            self.emit("    SWITCH_ROM(save);")
            self.emit("}")
        if self.platform in ('sms', 'gamegear'):
            # The SN76489 PSG is a Z80 I/O port ($7F), not memory -- hw.write can't
            # reach it, so `hw.psg(v)` writes the port (GBDK's PSG SFR = an `out`).
            # Game Gear: also open the stereo pan so both ears hear all channels.
            self.emit("/* SN76489 PSG data-port write (hw.psg): a Z80 `out`, not a memory store. */")
            if self.platform == 'gamegear':
                self.emit("void gbs_hw_psg(uint8_t value) { GG_SOUND_PAN = 0xFF; PSG = value; }")
            else:
                self.emit("void gbs_hw_psg(uint8_t value) { PSG = value; }")
        # video.set_overlay (the Lynx present-hook seam): a graceful no-op on
        # the tilemap consoles -- normally conditional-compiled away (vm.core
        # registers it under `if platform == "lynx"`), emitted only if a
        # program calls it unconditionally.
        if self.overlay_used:
            self.emit("void gbs_set_overlay(void (*cb)(void)) { (void)cb; }")
        self.emit("/* Display visibility helpers (GBDK macros wrapped as functions) */")
        if self.win_cut_used or self.overlay_cut_used:
            # The window sprite-cut restores sprites every VBL, so it has to
            # know what the PROGRAM asked for -- otherwise it would turn them
            # back on for a game that deliberately hid them. The OVERLAY cut
            # (W7d phase 2) gives them back at its own scanline and needs the
            # same ceiling, so it states this flag too.
            self.emit("uint8_t gbs_spr_want = 0;  /* the program's own show/hide state */")
            self.emit("void gbs_show_sprites(void) { gbs_spr_want = 1; SHOW_SPRITES; }")
            self.emit("void gbs_hide_sprites(void) { gbs_spr_want = 0; HIDE_SPRITES; }")
        else:
            self.emit("void gbs_show_sprites(void) { SHOW_SPRITES; }")
            self.emit("void gbs_hide_sprites(void) { HIDE_SPRITES; }")
        self.emit("void gbs_show_bkg(void) { SHOW_BKG; }")
        if self.bkg_attrs_used and self.platform not in ('gameboy_color',
                                                         'analogue_pocket'):
            self._emit_gbdk_bkg_attrs()
        if self.edge_mask_used:
            self._emit_gbdk_edge_mask()
        if self.vbl_hold_used:
            self._emit_gbdk_vbl_hold()
        if self.parallax_used:
            self._emit_gbdk_parallax()
        if self.raster_used:
            self._emit_gbdk_raster()
        if self.bkg_native_used:
            self._emit_gbdk_bkg_native()
        if self.cpu_fast_used:
            self._emit_gbdk_cpu_fast()
        if self.view_used:
            self._emit_gbdk_view_setter()
        if self.bkg_move_used and (self.caps.get('has_gb_regs')
                                   or self.platform in ('sms', 'gamegear')):
            # After the parallax block: gbs_wait_vblank's commit reads
            # gbs_px_n when both are in the program.
            self._emit_gbdk_scroll_move()
        self._emit_gbdk_prop_wrapper()
        if self.metasprite_used:
            self._emit_gbdk_meta_tables(False)
            self._emit_gbdk_metasprite()
        self._emit_gbdk_spr_cut()
        if self.spr_font_glyph_used:
            self._emit_gbdk_sprite_font_glyph()
        if self.load_sprite16_used:
            self.emit("/* 16-colour sprite palette: no-op on the 2bpp GB family (the 4bpp")
            self.emit("   asset was luma-quantized to greys at build time). */")
            self.emit("void gbs_load_sprite_pal16(const uint16_t *pal) { (void)pal; }")
        # gbs_load_bkg_pal16 (palette.load_bkg16) is emitted with the palette
        # engine (_emit_gbdk_palette), where gbs_rgb is in scope -- the SMS/GG
        # body needs it. The GB-family/NES no-op lives there too.
        if self.native_huge_imported:
            self._emit_gbdk_huge()
        if self.music_isr_used or self.music_hold_used or self.music_tick_used:
            self._emit_gbdk_music_isr()
        if self.native_lynx_imported:
            self.emit("/* native.lynx escape hatch: no-ops on the GB family (Lynx-only). */")
            self.emit("void gbs_lynx_fade_in(const uint16_t *pal, uint8_t frames) { (void)pal; (void)frames; }")
            self.emit("void gbs_lynx_fade_out(const uint16_t *pal, uint8_t frames) { (void)pal; (void)frames; }")
            self.emit("void gbs_lynx_screen_shake(uint8_t yoff) { (void)yoff; }")
            self.emit("void gbs_lynx_jingle(const uint16_t *notes, uint8_t count) { (void)notes; (void)count; }")
        self._emit_gbdk_move_sprite()
        self._emit_gbdk_move_world()
        if self.caps['has_window']:
            self.emit("/* The window layer only exists on Game Boy-family consoles. */")
            if self.overlay_cut_used:
                # The OVERLAY CUT turns the window off at its scanline and the
                # V-blank half turns it back on, so it has to know what the
                # PROGRAM asked for -- the gbs_spr_want shape exactly, and for
                # the same reason: a restore must never put a layer back that
                # the game itself took down. NOT initialised: BSS is free and an
                # initialised global is resident image.
                self.emit("uint8_t gbs_win_want;  /* the program's own window show/hide state */")
                self.emit("void gbs_show_win(void) { gbs_win_want = 1; SHOW_WIN; }")
                self.emit("void gbs_hide_win(void) { gbs_win_want = 0; HIDE_WIN; }")
            else:
                self.emit("void gbs_show_win(void) { SHOW_WIN; }")
                self.emit("void gbs_hide_win(void) { HIDE_WIN; }")
        if self.palette_imported:
            self._emit_gbdk_palette()
        if cgb_class:
            # The DMG-default palette seed (see the note at the top of this
            # method). Emitted HERE, after the palette engine, so that when the
            # program has a colour fade it can write through gbs_pal_hw -- the
            # RAM shadow that fade scales. Seeded raw, slot 0 would stay
            # unrecorded and a room-load fade on a colourless CGB game would
            # dim nothing.
            self.emit("/* CGB palette RAM is uninitialised for a CGB-flagged cart and BGP/")
            self.emit("   OBP are ignored in CGB mode: seed the DMG grey ramp so a program")
            self.emit("   that loads no colour renders like its Game Boy build. Slot 0 bkg +")
            self.emit("   slots 0/1 sprite are the three DMG registers' equivalents. */")
            self.emit("void gbs_cgb_default_palettes(void) {")
            if self.palette_fade_used:
                # gbs_pal_hw records into the shadow and then DIMS the caller's
                # buffer in place, so each call gets a fresh copy. The three
                # seeds run from a table rather than unrolled: this is
                # boot-once code and the unrolled form cost ~40 B of RESIDENT
                # image (measured 2026-09-06 on a project over the ceiling).
                self.emit("    static const palette_color_t src[4] = {")
                self.emit("        RGB_WHITE, RGB_LIGHTGRAY, RGB_DARKGRAY, RGB_BLACK };")
                self.emit("    static const uint8_t seed[3] = { 0x00, 0x10, 0x11 };")
                self.emit("    palette_color_t buf[4];")
                self.emit("    uint8_t i, k;")
                self.emit("    for (k = 0; k < 3; ++k) {")
                self.emit("        for (i = 0; i < 4; ++i) buf[i] = src[i];")
                self.emit("        gbs_pal_hw((uint8_t)(seed[k] >> 4), "
                          "(uint8_t)(seed[k] & 15), 1, buf);")
                self.emit("    }")
            else:
                self.emit("    static const palette_color_t greys[4] = {")
                self.emit("        RGB_WHITE, RGB_LIGHTGRAY, RGB_DARKGRAY, RGB_BLACK };")
                self.emit("    set_bkg_palette(0, 1, greys);")
                self.emit("    set_sprite_palette(0, 1, greys);")
                self.emit("    set_sprite_palette(1, 1, greys);")
            self.emit("}")
        self._emit_gbdk_sound()
        if self.sound_sfx_used:
            self._emit_sound_sfx()
        if self.save_imported and self.caps['has_save']:
            # `static inline`, so the MAIN TU and every bank TU each define
            # them and only the TUs that actually save emit any code - see
            # _emit_gbdk_save for why they are not four functions in bank 0.
            self._emit_gbdk_save()
        self.emit("")
