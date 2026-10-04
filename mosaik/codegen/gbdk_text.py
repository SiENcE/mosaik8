"""GBDK backend: the TEXT layer (mixin for the GBDK backend).

Split out of `gbdk.py` (2026-08-07, the G16 glyph-buffer work) so the second,
glyph-buffer implementation of the same four plotters has somewhere to live
beside the resident-font one. The mirror of `cc65_text.py`. Pure reorg at the
split: every emitted line moved verbatim and the generated C is
byte-identical.

The text layer is the ONE place that decides how a glyph reaches the screen:

* **GB family** (`has_window`) -- plot glyph tiles straight into the 32-col
  bkg map, or the WINDOW map (0x9C00) while `text.to_window` is live.
* **Game Gear, and the SMS when a font SWAP is used** -- plot into the 32-col
  name table with the 28-row SAT clamp.
* **SMS (no swap) / NES** -- the plain GBDK printf console.

All three read `gbs_font_base`, the active font's first glyph tile, so the
plotters follow a font relocated by `text.set_font_at`.

**`text.glyph_buffer(base, count)` swaps the glyph SOURCE under all of them**
(the G16 mode): instead of 96 glyph tiles sitting in VRAM for the whole run,
the font stays in ROM and a character is rasterized on demand into a small
reserved band. Only the tile EXPRESSION in the plotters changes, so the
window/name-table routing above is shared by both modes.
"""

from .gbdk_font import FONT_IBM_GLYPHS, FONT_IBM_TILES_OFFSET

#: The variable-width renderer (`_emit_gbdk_vwf`) is derived from CrossZGB's
#: `vwf_print_render`, so every program that emits it carries CrossZGB's MIT
#: notice in its generated C: the ROM contains derived code, and the notice
#: has to be able to travel with it. Kept verbatim (THIRD_PARTY_NOTICES.md).
CROSSZGB_NOTICE = """\
/* The variable-width text renderer below is derived from CrossZGB's
   vwf_print_render (https://github.com/gbdk-2020/CrossZGB, examples/vwf),
   under the following licence. A ROM built from this code contains it, so
   distribute this notice with the ROM (credits, manual or a text file).

   The MIT License (MIT)

   Copyright (c) 2016 - 2024 Gonzalo de Santos Garcia, GBDK-2020 organization

   Permission is hereby granted, free of charge, to any person obtaining a copy
   of this software and associated documentation files (the "Software"), to deal
   in the Software without restriction, including without limitation the rights
   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
   copies of the Software, and to permit persons to whom the Software is
   furnished to do so, subject to the following conditions:

   The above copyright notice and this permission notice shall be included in all
   copies or substantial portions of the Software.

   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
   SOFTWARE. */"""


class GbdkTextMixin:
    """The `gbs_print_*` / `gbs_clear_area` / font / window-router emitters.

    Mixed into `GbdkBackend`; every method runs against the full generator
    instance (`self.emit`, `self.caps`, `self.platform`, the `*_used` flags).
    """

    def _emit_gbdk_text_decls(self):
        """Text-layer prototypes for a bank translation unit.

        Banked code calls these helpers, whose definitions live once in the
        main TU's home bank. **Keep each gate identical to the definition's
        gate in `_emit_gbdk_text`** -- a helper emitted under one flag and
        declared under another gets no prototype inside a bank, and sdcc
        rejects the call with "too many parameters". That has bitten twice
        (the shell's 9-slice frame drawer, then `vm.snd`).
        """
        if self.text_used:
            self.emit("void gbs_text_init(void);")
            self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s);")
            self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n);")
            self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h);")
        # The GATED text-layer helpers. Each is emitted in the main TU only
        # when its verb is CALLED, so a bank TU must declare it under the SAME
        # flag -- otherwise banked code calling one gets no prototype and sdcc
        # rejects the call ("too many parameters"). This bit the shell's own
        # 9-slice frame drawer the moment module code started banking.
        if self.plot_tile_used:
            self.emit("void gbs_plot_tile(uint8_t x, uint8_t y, uint8_t t);")
        if self.font_swap_used:
            self.emit("void gbs_set_font(const uint8_t *data);")
            self.emit("void gbs_set_font_at(uint8_t base, const uint8_t *data);")
        if self.glyph_buffer_used:
            self.emit("void gbs_text_glyph_buffer(uint8_t base, uint8_t count);")
        if self.vwf_used:
            self.emit("void gbs_vwf_start(uint8_t col, uint8_t row);")
            self.emit("void gbs_vwf_nl(uint8_t col, uint8_t row);")
            self.emit("uint8_t gbs_vwf_glyph(uint8_t ch);")
            self.emit("void gbs_vwf_number(uint16_t v);")
        if self.win_cut_used:
            self.emit("void gbs_text_win_cut(uint8_t on);")
        if self.overlay_cut_used:
            self.emit("void gbs_text_win_overlay_cut(uint8_t y);")
        if ((self.text_used and self.caps['has_window'])
                or (self.text_window_used and self._smsgg_plot_path)):
            # The SMS/GG arm matters for the same reason the gated helpers do:
            # these are real functions there now (screen-space UI), so a BANK TU
            # that calls one needs the prototype or sdcc rejects the call.
            self.emit("void gbs_text_to_window(uint8_t origin_row, uint8_t box_rows);")
            self.emit("void gbs_text_win_reveal(void);")
            self.emit("void gbs_text_to_bkg(void);")
            self.emit("uint8_t gbs_text_window_active(void);")

    # The highest background tile id + 1, per console. The GB family owns the
    # whole 0..255 table for the background (sprites live in OBJ 0..127); the
    # SMS/GG GB-compat layout puts the name table at VRAM 0x1800 and the SAT at
    # 0x1F00, so its background is tiles 0..191 only (docs/vram-layout.md).
    GLYPH_TOP_TILE = {'sms': 192, 'gamegear': 192}

    def _glyph_top_tile(self):
        return self.GLYPH_TOP_TILE.get(self.platform, 256)

    def _glyph_tile(self, cexpr):
        """The C expression giving the VRAM tile that renders character `cexpr`.

        The ONE difference between the two text modes. Off (the default), a
        glyph is at a fixed offset from the resident font's base; on, it is
        resolved through the on-demand glyph cache. Returning an expression
        rather than forking the plotters keeps the window / name-table routing
        (which is the fiddly part) shared by both, and keeps the mode
        byte-identical off.
        """
        if self.glyph_buffer_used:
            return "gbs_glyph_tile((uint8_t)%s)" % cexpr
        return "(uint8_t)(gbs_font_base + (uint8_t)(%s - 32))" % cexpr

    @property
    def _smsgg_plot_path(self):
        """True when this build plots glyphs into the SMS/GG NAME TABLE.

        The Game Gear always does (printf wraps at its 20-col window and split a
        camera-scrolled box); the SMS does once a font swap or the glyph buffer
        moves the glyphs off printf's own base. Both the screen-space UI
        transform below and the `to_window` definitions key off this, so the two
        cannot disagree about which console owns the name-table path.

        ...AND SO DOES ANY SMS PROGRAM THAT CALLS `text.to_window`. That verb
        asks for a box held in SCREEN space, and only the plotting path can
        answer it: printf writes at name-table cells, which on the SMS ARE the
        scrolling scene (its 32-col name table is the full screen width), so a
        box over a scrolling room slid away with the level and its `to_window`
        compiled to a no-op that said nothing. That was silent - the box looks
        right at scroll 0, which is where a still screenshot takes it (measured
        on vm-uiscroll: correct standing still, truncated to 12 of 30 columns
        with RIGHT held). A no-`to_window` SMS program keeps printf and stays
        byte-identical.
        """
        return (self.platform == 'gamegear'
                or (self.platform == 'sms'
                    and (self.font_swap_used or self.glyph_buffer_used
                         or self.text_window_used)))

    def _ui_cell_x(self, xexpr, plain):
        """A text COLUMN -> the name-table column to write.

        Screen space while `to_window` is live (see `_emit_smsgg_ui_space`),
        otherwise `plain` - the caller's HISTORICAL text, passed verbatim rather
        than re-derived, so a program that never routes text to a window emits
        the identical C it always did. (Re-deriving it produced
        `(uint8_t)((x) & 31)` for `(uint8_t)(x & 31)`: same behaviour, and
        `plot_tile_test` correctly failed it as churn.)
        """
        if self._smsgg_plot_path and self.text_window_used:
            return "gbs_ui_cx((uint8_t)(%s))" % xexpr
        return plain

    def _ui_cell_y(self, yexpr, plain):
        """A text ROW -> the name-table row to write (see `_ui_cell_x`)."""
        if self._smsgg_plot_path and self.text_window_used:
            return "gbs_ui_cy((uint8_t)(%s))" % yexpr
        return plain

    def _emit_smsgg_ui_space(self):
        """UI text in SCREEN space on a console with no window layer.

        The GB family answers `text.to_window` by moving the box onto the window
        map, which never scrolls and never touches the scene. SMS/GG have no
        such layer, so the box is plotted into the NAME TABLE - and a name-table
        cell is a MAP cell. A box drawn at its authored screen row therefore
        landed wherever the camera happened to be (off-screen entirely once the
        level had scrolled a screenful) and then slid away with the level, which
        is exactly what a reference-engine import does in a scrolling platform room.

        While `to_window` is live every plot adds the hardware scroll, so the box
        holds a fixed SCREEN position. Two honest limits: a cell cannot express
        sub-tile scroll, so the box snaps to the tile grid (up to 7 px), and the
        cells belong to the scene, so the caller still repaints the room on close
        (`gbs_text_window_active()` stays 0 here, which is what asks it to).
        """
        self.emit("/* ---- UI text in SCREEN space (text.to_window with no window layer) ---- */")
        self.emit("static uint8_t gbs_text_ui = 0;   /* a box/menu is routed to the screen */")
        # THE SCROLL THIS FRAME WILL SHOW, not the one on the register. When
        # bkg.move is deferred (gbs_scroll_move + the v-blank commit, which
        # SMS/GG take so the background and the sprite table change on the one
        # clock), the live VDP shadow is still the PREVIOUS frame's scroll --
        # so a box plotted through it lands a tile out, and a snap written
        # straight to the register is undone by the next commit. Both halves
        # therefore read and write the pending shadow instead.
        deferred = self.bkg_move_used
        if deferred:
            self.emit("/* gbs_scr_shx/shy are the scroll this frame will COMMIT (bkg.move is")
            self.emit("   deferred to v-blank here); the live VDP shadow is last frame's, so")
            self.emit("   every UI mapping below reads the pending value. The name table is")
            self.emit("   32x28 - columns wrap at 32 and rows at 28, not 32. */")
            self.emit("extern uint8_t gbs_scr_shx, gbs_scr_shy;")
            # ...and the setter, which is DEFINED further down this file.
            self.emit("void gbs_scroll_move(uint8_t x, uint8_t y);")
            cur_x, cur_y = "gbs_scr_shx", "gbs_scr_shy"
        else:
            self.emit("/* move_bkg() stores -x in RSCX and y in RSCY (sms/sms.h), and the name")
            self.emit("   table is 32x28 - so columns wrap at 32 and rows at 28, not 32. */")
            cur_x = "(uint8_t)(0u - shadow_VDP_RSCX)"
            cur_y = "shadow_VDP_RSCY"
        self.emit("static uint8_t gbs_ui_cx(uint8_t x) {")
        self.emit("    if (gbs_text_ui) x += (uint8_t)(%s >> 3);" % cur_x)
        self.emit("    return (uint8_t)(x & 31);")
        self.emit("}")
        self.emit("static uint8_t gbs_ui_cy(uint8_t y) {")
        self.emit("    if (gbs_text_ui) {")
        self.emit("        y += (uint8_t)(%s >> 3);" % cur_y)
        self.emit("        while (y >= 28) y -= 28;")
        self.emit("    }")
        self.emit("    return y;")
        self.emit("}")
        self.emit("/* SNAP the scroll to a tile boundary while UI is up. A box is built out")
        self.emit("   of TILES, so its border ring can only land on the tile grid -- and the")
        self.emit("   screen edge does not, once the camera is a few pixels into a tile. The")
        self.emit("   box then looks inset by up to 7 px with a sliver of the wrapped-round")
        self.emit("   cell beyond it, which reads as 'the box is one tile too far left'. There")
        self.emit("   is no way to place a tile at a sub-tile offset, so the SCROLL moves")
        self.emit("   instead: round it down to the tile below and restore the exact value on")
        self.emit("   close. The camera is held while the box is up (vm.player), so nothing")
        self.emit("   fights this, and vm.player positions sprites against the SAME rounding")
        self.emit("   so they stay glued to the scene. */")
        self.emit("static uint8_t gbs_ui_scx = 0;   /* the pre-snap scroll, restored on close */")
        self.emit("static uint8_t gbs_ui_scy = 0;")
        self.emit("void gbs_text_to_window(uint8_t origin_row, uint8_t box_rows) {")
        if not deferred:
            self.emit("    uint8_t cx, cy;")
        self.emit("    (void)origin_row; (void)box_rows;   /* the box keeps its authored rows */")
        self.emit("    if (!gbs_text_ui) {")
        if deferred:
            # gbs_ui_scx/scy hold the CAMERA here (not the negated register),
            # because that is what the deferred setter takes back on close.
            self.emit("        gbs_ui_scx = gbs_scr_shx;   /* the pending camera, saved */")
            self.emit("        gbs_ui_scy = gbs_scr_shy;")
            self.emit("        if ((gbs_ui_scx & 7) || (gbs_ui_scy & 7))")
            self.emit("            gbs_scroll_move((uint8_t)(gbs_ui_scx & 0xF8),")
            self.emit("                            (uint8_t)(gbs_ui_scy & 0xF8));")
        else:
            self.emit("        gbs_ui_scx = shadow_VDP_RSCX;   /* move_bkg stored -x here */")
            self.emit("        gbs_ui_scy = shadow_VDP_RSCY;   /* ... and y here, verbatim */")
            self.emit("        cx = (uint8_t)(0u - gbs_ui_scx);")
            self.emit("        cy = gbs_ui_scy;")
            self.emit("        if ((cx & 7) || (cy & 7))")
            self.emit("            move_bkg((uint8_t)(cx & 0xF8), (uint8_t)(cy & 0xF8));")
        self.emit("    }")
        self.emit("    gbs_text_ui = 1;")
        self.emit("}")
        self.emit("/* No layer to reveal: the box is plotted into the name table itself,")
        self.emit("   so it is visible as it is written. See gbs_text_win_reveal on the GB")
        self.emit("   family, where the band is held back until it is whole. */")
        self.emit("void gbs_text_win_reveal(void) { }")
        self.emit("void gbs_text_to_bkg(void) {")
        self.emit("    if (gbs_text_ui) {")
        self.emit("        gbs_text_ui = 0;")
        if deferred:
            self.emit("        gbs_scroll_move(gbs_ui_scx, gbs_ui_scy);")
        else:
            self.emit("        move_bkg((uint8_t)(0u - gbs_ui_scx), gbs_ui_scy);")
        self.emit("    }")
        self.emit("}")
        self.emit("/* 0 = the overlay DID touch the scene map, so the caller must repaint the")
        self.emit("   room on close. Only a real window layer can answer 1. */")
        self.emit("uint8_t gbs_text_window_active(void) { return 0; }")

    def _glyph_digit(self, dexpr):
        """As `_glyph_tile`, for a DIGIT VALUE 0..9 rather than a character.

        The resident path indexes the font directly (glyph 16 is '0'); the
        glyph cache is keyed by character, so the digit becomes one first.
        """
        if self.glyph_buffer_used:
            return "gbs_glyph_tile((uint8_t)(48 + %s))" % dexpr
        return "(uint8_t)(gbs_font_base + 16 + %s)" % dexpr

    def _emit_gbdk_glyph_font(self):
        """The ROM font + the on-demand glyph cache (`text.glyph_buffer`).

        Emitted only when the verb is CALLED, so every existing program keeps
        the resident-font path and stays byte-identical.

        Why a cache keyed by GLYPH and not, like the reference engine, by screen CELL:
        this font is fixed-width, so one tile IS one character, and then
        repeated letters share a tile (a band holds distinct characters, ~30
        for English text, not the ~54 cells of a full box) and a REDRAW is a
        cache hit with no VRAM write at all -- which is what a menu cursor move
        and a HUD value change both do. The reference engine has no choice because its
        variable-width glyphs straddle tile boundaries. A variable-width font
        here would need the per-cell ring as its own mode.
        """
        top = self._glyph_top_tile()
        # The sheet's real glyph count, needed by the defines below. 96
        # (ASCII 32..127) for every fixed-width font, which is all of them
        # today; a VARIABLE-width sheet brings its whole ASCII 32..255 range,
        # because that is where its author put the spacers. Identical at 96.
        n_glyphs = (len(self.glyph_font_override)
                    if self.glyph_font_override else FONT_IBM_GLYPHS)
        # The band is DERIVED (gen_budgets._resolve_glyph_band): one past the
        # highest background tile the whole program uploads, so an author never
        # has to hand-pick it and a growing tileset moves it automatically. The
        # runtime call can still override, and passing 0 asks for these.
        default_base, default_count = self.glyph_band or (top - 48, 48)
        self.emit("/* ---- GLYPH-BUFFER text (text.glyph_buffer) ----")
        self.emit("   The font stays in ROM and each character is rasterized on demand into")
        self.emit("   a small reserved tile band, instead of 96 glyph tiles occupying VRAM")
        self.emit("   for the whole run. That is what lets a scene use nearly the whole")
        self.emit("   background tile table -- the reference engine's model (art 0..190, frame, then")
        self.emit("   glyphs 204..255) -- and it retires the set_font_at relocation escape.")
        self.emit("   The cache is keyed by GLYPH, not by screen cell: repeated letters")
        self.emit("   share one tile, and re-printing the same text is a pure cache hit with")
        self.emit("   no VRAM write, so a menu cursor move or a HUD value change is free.")
        self.emit("   `count` therefore bounds the DISTINCT characters on screen at once. */")
        self.emit("#define GBS_GLYPH_MAX   64   /* ceiling on the WRAM slot table */")
        self.emit("/* The DERIVED band: one past the highest bkg tile this program uploads")
        self.emit("   (scene tileset, per-scene tilesets, the shell's 9-slice frame), up to")
        self.emit("   the top of the table. text.glyph_buffer(0, 0) asks for exactly this. */")
        self.emit("#define GBS_GLYPH_BASE  %d" % default_base)
        self.emit("#define GBS_GLYPH_COUNT %d" % default_count)
        self.emit("#define GBS_GLYPH_TOP   %d   /* one past the highest bkg tile id */" % top)
        self.emit("#define GBS_GLYPH_N     %d   /* glyphs in the sheet, from ASCII 32 */"
                  % n_glyphs)
        font_rows = self.glyph_font_override
        font_bank = 0
        if font_rows is not None:
            self.emit("/* The PROJECT'S OWN font ([assets] font), converted to 1bpp at build")
            self.emit("   time (8 B/glyph, ASCII 32..127) and baked here in place of the")
            self.emit("   console font -- so a custom font costs nothing at runtime and")
            self.emit("   no VRAM (glyphs rasterize from this table on demand). */")
            table = ["const uint8_t gbs_glyph_font[%d * 8] = {" % n_glyphs]
            for i, row in enumerate(font_rows):
                if not isinstance(row, str):
                    # a custom font arrives as 8 ints per glyph (png_to_font_1bpp)
                    row = ','.join('0x%02X' % b for b in row)
                ch = chr(32 + i)
                label = "' '" if ch == ' ' else ("'%s'" % ch)
                if ch == '"':
                    label = "'\\\"'"
                if ch == '\\':
                    label = "'\\\\'"
                table.append("    %s,  /* %s */" % (row, label))
            table.append("};")
            # BANK the table when this build already has banks (bank0 plan
            # O5). 768 B of the resident image for data the rasterizer
            # touches only on a cache MISS -- about 30 distinct glyphs per
            # screen, and a cold box already costs ~6 LCD frames, so a bank
            # switch around the read is affordable where one per fetched byte
            # would not be. 0 = stay resident (no banking in this build).
            font_bank = self._prelude_data_bank("gbs_glyph_font", n_glyphs * 8,
                                                "\n".join(table))
            if font_bank:
                self.emit("/* The table itself lives in a switchable ROM bank (bank0 plan")
                self.emit("   O5); the rasterizer maps it around the read -- gbs_glyph_load. */")
                self.emit("extern const uint8_t gbs_glyph_font[%d * 8];" % n_glyphs)
            else:
                self.emit("static " + "\n".join(table))
            glyph_src = "gbs_glyph_font"
        else:
            # No project font: the CONSOLE font, LINKED out of GBDK's library
            # like the ordinary text path's font_load(font_ibm) -- never a copy
            # in our source (GBDK's linking exception covers exactly this).
            self.emit("/* No project font: the glyphs are GBDK's own font_ibm, LINKED from the")
            self.emit("   console library (the same font the ordinary text path loads), so")
            self.emit("   switching modes does not change how text looks. font_ibm is")
            self.emit("   FONT_128ENCODING | FONT_COMPRESSED: 2 header bytes, a 128-byte")
            self.emit("   ASCII -> tile table that maps ASCII 32 + i to tile i, then 1bpp tiles")
            self.emit("   of 8 B, which GBDK's set_bkg_1bpp_data expands. It is resident. */")
            glyph_src = "font_ibm + %d" % FONT_IBM_TILES_OFFSET
        # Per-glyph ADVANCE widths, for a VARIABLE-width sheet only. Trimmed out
        # of the PNG at build time against the reference engine's marker colour
        # (`png_to_font_widths`) -- its own fonts carry no width table either.
        # Emitted beside the bitmaps and banked with them; absent for every
        # fixed-width font, which is what keeps this byte-identical today.
        widths = self.glyph_widths_override
        if widths:
            wtable = ["const uint8_t gbs_glyph_widths[%d] = {" % len(widths)]
            for i in range(0, len(widths), 16):
                wtable.append("    " + ",".join("%d" % w for w in widths[i:i + 16])
                              + ("," if i + 16 < len(widths) else ""))
            wtable.append("};")
            self.emit("/* Advance width per glyph, 0..8 px. A VARIABLE-width font only. */")
            wbank = self._prelude_data_bank("gbs_glyph_widths", len(widths),
                                            "\n".join(wtable))
            if wbank:
                self.emit("extern const uint8_t gbs_glyph_widths[%d];" % len(widths))
                # Its own bank variable: _prelude_data_bank places each symbol
                # independently, so the widths need not share the bitmaps' bank.
                self.emit("static uint8_t gbs_glyph_widths_bank = %d;" % wbank)
            else:
                self.emit("static " + "\n".join(wtable))
        if self.vwf_used:
            # The width read is its own accessor because the widths table may
            # bank INDEPENDENTLY of the bitmaps (_prelude_data_bank decides per
            # symbol), so the compositor cannot fold it into its own switch.
            # A sheet with no widths answers 8, i.e. a fixed-width font drawn
            # through the VWF path renders exactly as the cell path would.
            self.emit("/* Advance width of one glyph, 8 when the sheet is fixed-width. */")
            self.emit("static uint8_t gbs_vwf_width(uint8_t g) {")
            if widths:
                if self.prelude_bank_defs.get("gbs_glyph_widths"):
                    self.emit("    uint8_t w, __entry = CURRENT_BANK;")
                    self.emit("    SWITCH_ROM(gbs_glyph_widths_bank);")
                    self.emit("    w = gbs_glyph_widths[g];")
                    self.emit("    SWITCH_ROM(__entry);")
                    self.emit("    return w;")
                else:
                    self.emit("    return gbs_glyph_widths[g];")
            else:
                self.emit("    (void)g; return 8;")
            self.emit("}")
        self.emit("/* The band's base doubles as the SPACE tile (slot 0), which is what")
        self.emit("   gbs_clear_area and the window-band clear plot to blank a cell -- so the")
        self.emit("   plotters keep reading gbs_font_base and need no glyph-mode fork. */")
        self.emit("uint8_t gbs_font_base = 0;")
        self.emit("static const uint8_t *gbs_glyph_src = %s;" % glyph_src)
        if font_bank:
            self.emit("/* The BANK gbs_glyph_src lives in, 0 = resident (no switch). It")
            self.emit("   travels WITH the pointer, because the rasterizer reads it LAZILY,")
            self.emit("   long after the source was chosen: text.set_font repoints it at a")
            self.emit("   caller's sheet, which is home data, so a swap must also stop")
            self.emit("   switching. One variable beside the pointer is what makes that")
            self.emit("   safe -- this table was flagged for exactly that dangling shape. */")
            self.emit("static uint8_t gbs_glyph_bank = %d;" % font_bank)
        if self.vwf_used:
            self.emit("/* ---- The VWF band PARTITION (vwf-text-plan V4b) ----")
            self.emit("   The fixed-width glyph cache and the VWF ring allocate from the SAME")
            self.emit("   derived band, and un-partitioned they hand the same tiles to")
            self.emit("   different text (measured: every menu row rendered identical to the")
            self.emit("   dialogue box). The cache keeps the TOP GBS_VWF_RESERVE tiles --")
            self.emit("   menus and HUD stay fixed-width, and clear_area's space tile lives")
            self.emit("   there -- and the ring below gets everything else. */")
            self.emit("#define GBS_VWF_RESERVE 8")
            self.emit("static uint8_t gbs_vwf_base = 0;   /* ring start (the band's bottom) */")
            self.emit("static uint8_t gbs_vwf_count = 0;  /* ring size; 0 = no room, refused */")
        self.emit("static uint8_t gbs_glyph_2bpp = 0;   /* 1 once set_font swapped in a 2bpp sheet */")
        self.emit("static uint8_t gbs_glyph_base = 0;")
        self.emit("static uint8_t gbs_glyph_count = 0;  /* 0 = the band is not set up yet */")
        self.emit("static uint8_t gbs_glyph_next = 1;   /* round-robin cursor; slot 0 is ' ' */")
        self.emit("static uint8_t gbs_glyph_slot[%d];   /* char-32 -> slot+1 (0 = not resident) */"
                  % n_glyphs)
        self.emit("static uint8_t gbs_glyph_char[GBS_GLYPH_MAX];  /* slot -> char-32 (0xFF free) */")
        self.emit("/* Rasterize glyph g into band slot s. A custom sheet (text.set_font) is")
        self.emit("   2bpp, 16 B/glyph; the built-in font is 1bpp, 8 B/glyph. */")
        self.emit("static void gbs_glyph_load(uint8_t s, uint8_t g) {")
        if self.prelude_bank_defs.get("gbs_glyph_font"):
            # Map the font's bank for the copy, then put back whatever the
            # CALLER had mapped: this runs under banked code (a dialogue box
            # opened from a banked script path), and returning under a switched
            # window makes the caller execute garbage. Same contract as the
            # bank-neutrality wrapper the code-banking pass applies.
            self.emit("    uint8_t __entry = CURRENT_BANK;")
            self.emit("    if (gbs_glyph_bank) SWITCH_ROM(gbs_glyph_bank);")
        self.emit("    if (gbs_glyph_2bpp) {")
        self.emit("        set_bkg_data((uint8_t)(gbs_glyph_base + s), 1,")
        self.emit("                     gbs_glyph_src + (uint16_t)g * 16);")
        self.emit("    } else {")
        self.emit("        set_1bpp_colors(3, 0);  /* the console font's ink on paper */")
        self.emit("        set_bkg_1bpp_data((uint8_t)(gbs_glyph_base + s), 1,")
        self.emit("                          gbs_glyph_src + (uint16_t)g * 8);")
        self.emit("    }")
        if self.prelude_bank_defs.get("gbs_glyph_font"):
            self.emit("    SWITCH_ROM(__entry);")
        self.emit("}")
        self.emit("void gbs_text_glyph_buffer(uint8_t base, uint8_t count) {")
        self.emit("    uint8_t i;")
        self.emit("    /* 0 means 'the derived band': base = above everything this program")
        self.emit("       uploads as tile data, count = from there to the top of the table.")
        self.emit("       A generated shell passes 0, 0 and never carries the numbers. */")
        self.emit("    if (base == 0) base = GBS_GLYPH_BASE;")
        self.emit("    if (count == 0) count = (uint8_t)(GBS_GLYPH_TOP - base);")
        self.emit("    if (count > GBS_GLYPH_MAX) count = GBS_GLYPH_MAX;")
        self.emit("    /* Never let the band run off the end of the background tile table. */")
        self.emit("    if ((uint16_t)base + count > GBS_GLYPH_TOP)")
        self.emit("        count = (uint8_t)(GBS_GLYPH_TOP - base);")
        self.emit("    if (count < 2) return;  /* slot 0 is ' '; a 1-tile band is unusable */")
        if self.vwf_used:
            self.emit("    /* Partition: ring below, cache's GBS_VWF_RESERVE on top. A band too")
            self.emit("       small to split leaves the ring EMPTY (vwf_glyph then draws nothing)")
            self.emit("       rather than letting the two fight over tiles. */")
            self.emit("    if (count > (uint8_t)(GBS_VWF_RESERVE + 4)) {")
            self.emit("        gbs_vwf_base = base;")
            self.emit("        gbs_vwf_count = (uint8_t)(count - GBS_VWF_RESERVE);")
            self.emit("        base = (uint8_t)(base + gbs_vwf_count);")
            self.emit("        count = GBS_VWF_RESERVE;")
            self.emit("    } else {")
            self.emit("        gbs_vwf_count = 0;")
            self.emit("    }")
        self.emit("    gbs_glyph_base = base;")
        self.emit("    gbs_glyph_count = count;")
        self.emit("    for (i = 0; i < 96; i++) gbs_glyph_slot[i] = 0;")
        self.emit("    for (i = 0; i < count; i++) gbs_glyph_char[i] = 0xFF;")
        self.emit("    /* Slot 0 holds ' ' PERMANENTLY: gbs_clear_area and the window-band")
        self.emit("       clear blank a cell by plotting gbs_font_base, so the space glyph")
        self.emit("       must never be evicted out from under them. */")
        self.emit("    gbs_glyph_char[0] = 0;")
        self.emit("    gbs_glyph_slot[0] = 1;")
        self.emit("    gbs_glyph_next = 1;")
        self.emit("    gbs_font_base = base;")
        self.emit("    gbs_glyph_load(0, 0);")
        self.emit("}")
        self.emit("/* Resolve a character to its VRAM tile, rasterizing on a cache MISS. */")
        self.emit("static uint8_t gbs_glyph_tile(uint8_t c) {")
        self.emit("    uint8_t g, s, old;")
        self.emit("    if (gbs_glyph_count == 0)")
        self.emit("        gbs_text_glyph_buffer(GBS_GLYPH_BASE, GBS_GLYPH_COUNT);")
        self.emit("    g = (uint8_t)(c - 32);  /* wraps for a control char -> caught below */")
        self.emit("    if (g > %d) g = 0;      /* anything unprintable renders as a space */"
                  % (n_glyphs - 1))
        self.emit("    s = gbs_glyph_slot[g];")
        self.emit("    if (s) return (uint8_t)(gbs_glyph_base + s - 1);")
        self.emit("    s = gbs_glyph_next;")
        self.emit("    old = gbs_glyph_char[s];")
        self.emit("    if (old != 0xFF) gbs_glyph_slot[old] = 0;  /* evict the round-robin victim */")
        self.emit("    gbs_glyph_char[s] = g;")
        self.emit("    gbs_glyph_slot[g] = (uint8_t)(s + 1);")
        self.emit("    if (++gbs_glyph_next >= gbs_glyph_count) gbs_glyph_next = 1;")
        self.emit("    gbs_glyph_load(s, g);")
        self.emit("    return (uint8_t)(gbs_glyph_base + s);")
        self.emit("}")

    def _emit_gbdk_sprite_font_glyph(self):
        """`sprite.font_glyph(tile, ch)`: the console font's glyph for `ch` as
        ONE sprite tile, read from GBDK's LINKED `font_ibm` (never a copy; see
        `gbdk_font.py`).

        It expands the 1bpp row into the SAME 16-byte 2bpp tile the overlay
        HUD used to bake at build time (both planes = the glyph: colour 3 on
        transparent 0) and uploads it through the ordinary `set_sprite_data`,
        so every console's own 2bpp expansion (the SMS/GG nibble map included)
        treats it exactly as it treated the baked tile. Emitted only when the
        verb is called."""
        self.emit("/* sprite.font_glyph(tile, ch): the console font's glyph for ch as one")
        self.emit("   sprite tile, read from GBDK's LINKED font_ibm (FONT_128ENCODING |")
        self.emit("   FONT_COMPRESSED: 2 header bytes, the ASCII -> tile table, 1bpp tiles).")
        self.emit("   Expanded to 2bpp with both planes = the glyph (colour 3 on 0). */")
        self.emit("void gbs_sprite_font_glyph(uint8_t tile, uint8_t ch) {")
        self.emit("    uint8_t buf[16], i;")
        self.emit("    const uint8_t *src;")
        self.emit("    if (ch < 32 || ch > 127) ch = 32;   /* unprintable -> space */")
        self.emit("    src = font_ibm + %d + (uint16_t)font_ibm[2 + ch] * 8;"
                  % FONT_IBM_TILES_OFFSET)
        self.emit("    for (i = 0; i < 8; ++i) buf[i * 2] = buf[i * 2 + 1] = src[i];")
        self.emit("    set_sprite_data(tile, 1, buf);")
        self.emit("}")

    def _emit_gbdk_vwf(self, win):
        """`text.vwf_start` / `vwf_nl` / `vwf_glyph` -- VARIABLE-WIDTH text.

        The second text MODE. The glyph buffer caches one tile per CHARACTER,
        which is only possible because a fixed-width glyph IS a cell; a
        variable-width glyph straddles cell boundaries, so there is nothing to
        dedupe and the band is walked as a linear RING instead. Ported from
        CrossZGB's `vwf_print_render` (https://github.com/gbdk-2020/CrossZGB,
        `examples/vwf/`; Copyright (c) 2016 - 2024 Gonzalo de Santos Garcia,
        GBDK-2020 organization; MIT), which composites in 1bpp - the format
        `png_to_font_1bpp` already emits - and leaves the per-console tile
        expansion to `set_bkg_1bpp_data`.

        Because this code is DERIVED from an MIT work and lands in every ROM
        that draws variable-width text, the emitted C carries CrossZGB's full
        notice (`CROSSZGB_NOTICE`), and whoever distributes such a ROM must
        pass it on (README, THIRD_PARTY_NOTICES.md).

        Three properties copied from the reference on purpose:

        * **The in-progress cell is mapped immediately**, so a half-drawn glyph
          is already on screen. That is what makes the typewriter compose with
          this for free rather than needing a second code path.
        * **A partial cell is abandoned at a line break, never packed.** Every
          line starts at pen 0.
        * **The pen lives here, not in the caller.** The caller feeds
          characters and counts completed cells.

        The ring is BOUNDED by the band and simply stops when it runs out
        (`vwf-text-plan` "band exhaustion"): the reference engine's own ring wraps
        silently and overwrites its earlier tiles, which corrupts text already
        on screen. Stopping loses the tail of an over-long line instead, which
        is the same failure the fixed-width path already has and is at least
        the one the author can see.
        """
        for line in CROSSZGB_NOTICE.splitlines():
            self.emit(line)
        self.emit("/* ---- VARIABLE-WIDTH text (text.vwf_*) ----")
        self.emit("   Characters are COMPOSITED at a sub-cell pen into a two-tile stage and")
        self.emit("   the glyph band is walked as a linear ring, because a variable-width")
        self.emit("   glyph straddles cell boundaries and cannot be cached per character.")
        self.emit("   1bpp throughout; gbs_vwf_upload expands to the tile format at upload. */")
        self.emit("static uint8_t gbs_vwf_stage[16];  /* current cell + spill */")
        self.emit("static uint8_t gbs_vwf_pen;        /* 0..7 px inside the current cell */")
        self.emit("static uint8_t gbs_vwf_tile;       /* band tile being composed */")
        self.emit("static uint8_t gbs_vwf_col, gbs_vwf_row;")
        # The in-progress cell's tile ID does not change between the glyph
        # that opens it and the one that spills out of it, so it is MAPPED
        # once, at its first glyph; only its DATA is re-uploaded per glyph
        # (which is what keeps a half-drawn glyph on screen). Re-mapping it
        # on every glyph was one map write per glyph for nothing (2026-09-07).
        self.emit("static uint8_t gbs_vwf_mapped;      /* the in-progress cell is on the map */")
        # The band's end. gbs_glyph_base/count are set up by the glyph buffer,
        # which a VWF program always uses (the font it composites from is that
        # mode's ROM table).
        self.emit("/* Room left in the ring. Measured as a DISTANCE from the base, never as")
        self.emit("   `tile < base + count`: the band runs to the top of the tile table, so on")
        self.emit("   the GB family that sum is exactly 256 and truncates to 0 in uint8_t --")
        self.emit("   which reads as 'no room' for every glyph and silently draws nothing. */")
        # A macro, not a static function: sdcc did not inline the one-liner
        # and it measured ~300 T-cycles a call, 1.75 calls a glyph (2026-09-07).
        self.emit("#define gbs_vwf_room() ((uint8_t)((uint8_t)(gbs_vwf_tile - gbs_vwf_base) < gbs_vwf_count))")
        self.emit("static void gbs_vwf_blank(uint8_t *p) {")
        self.emit("    /* 0x00 = paper (colour 0, white) -- the reference engine's text_bkg_fill default,")
        self.emit("       even for a pre-inverted sheet: its own docs call the resulting white")
        self.emit("       trailing columns out, and the platformer conversion's authored 1 px / 4 px spacer glyphs")
        self.emit("       (chars 239 / 255) exist precisely to blacken them. Fidelity keeps")
        self.emit("       the wart AND the workaround. Unrolled: the loop was ~500 cycles. */")
        self.emit("    p[0] = 0; p[1] = 0; p[2] = 0; p[3] = 0; p[4] = 0; p[5] = 0; p[6] = 0; p[7] = 0;")
        self.emit("}")
        self.emit("/* Write one composed tile id at a text cell, through the SAME router the")
        self.emit("   glyph plotters use -- the window map while text is routed there, else")
        self.emit("   the bkg map -- so a VWF box lands on the layer a fixed-width one would. */")
        # ONE MAP BYTE, WRITTEN AS ONE BYTE (2026-09-07). This is called two or
        # three times per glyph (the in-progress cell every time, the completed
        # cell on a spill) and `set_win_tiles(..., 1, 1, &t)` is GBDK's
        # rectangle copier: measured ~1,220 T-cycles a call on the entry
        # profile (`set_xy_tt` + `set_win_tiles` + `set_xy_wtt`) for a single
        # byte. The reference engine's `ui_set_tile` is `set_vram_byte` on a computed
        # address; `get_win_xy_addr` reads the live LCDC map select, so the
        # byte lands where the rectangle copier would have put it.
        self.emit("static void gbs_vwf_put(uint8_t col, uint8_t row, uint8_t t) {")
        if win:
            self.emit("    if (gbs_text_win) set_vram_byte(get_win_xy_addr((uint8_t)(col & 31), "
                      "(uint8_t)((uint8_t)(row - gbs_win_row) & 31)), t);")
            self.emit("    else set_vram_byte(get_bkg_xy_addr((uint8_t)(col & 31), (uint8_t)(row & 31)), t);")
        else:
            # `win` is text_window_used, not a console: this arm is a GB-family
            # program whose text never leaves the bkg map, so the same direct
            # write applies (the emitter as a whole is under has_window).
            self.emit("    set_vram_byte(get_bkg_xy_addr((uint8_t)(col & 31), (uint8_t)(row & 31)), t);")
        self.emit("}")
        # THE UPLOAD IS THE PEN'S BIGGEST SINGLE ITEM, AND set_bkg_1bpp_data
        # IS THE EXPENSIVE WAY TO DO IT (measured 2026-09-07 on
        # the DMG-palette check conversion's room 0, autofire: suppressing just this
        # call took vm_core_run_scripts 60,520 -> 40,821 T-cycles a game frame,
        # i.e. ~13,600 cycles per glyph, where the reference engine's own per-char upload
        # - a plain 16-byte set_bkg_data of its 2bpp staging buffer - measures
        # 4,120 on the reference ROM). GBDK's 1bpp entry point is general: it
        # expands through the __1bpp_colors PAIR, per BIT. The pen fixes that
        # pair at (3, 0) - ink 3 on paper 0 - and at that pair the expansion is
        # one stage byte written into BOTH planes, an 8-step copy. The same 16
        # bytes reach VRAM either way, so the box is pixel-identical (checked
        # against the pre-change build).
        #
        # `b, b` is the GB's byte-interleaved 2bpp row format, and this whole
        # emitter is GB-FAMILY ONLY - its one call site sits under
        # `caps['has_window']` (the SMS/GG background is 4bpp planar and would
        # need the port's own expander), so there is no second arm to write.
        self.emit("/* Upload `n` composed cells. The 1bpp stage is expanded HERE rather")
        self.emit("   than by set_bkg_1bpp_data, which walks the colour pair BIT by bit:")
        self.emit("   the pen's pair is fixed at (3, 0), so a set bit is colour 3 = both")
        self.emit("   planes, i.e. every stage byte written twice. Same bytes, same tiles. */")
        self.emit("static uint8_t gbs_vwf_out[32];")
        # The expansion loop in C measured ~1,100 T-cycles an upload; the same
        # eight-per-row copy in sm83 is a fifth of that (2026-09-07). Stage and
        # output are fixed globals, so the core takes only the byte count.
        self.emit("static void gbs_vwf_expand(uint8_t n8) OLDCALL NAKED {")
        self.emit("    n8;")
        self.emit("    __asm")
        self.emit("        ldhl sp, #2")
        self.emit("        ld b, (hl)")
        self.emit("        ld de, #_gbs_vwf_stage")
        self.emit("        ld hl, #_gbs_vwf_out")
        self.emit("    1$:")
        self.emit("        ld a, (de)")
        self.emit("        inc de")
        self.emit("        ld (hl+), a")
        self.emit("        ld (hl+), a")
        self.emit("        dec b")
        self.emit("        jr nz, 1$")
        self.emit("        ret")
        self.emit("    __endasm;")
        self.emit("}")
        self.emit("static void gbs_vwf_upload(uint8_t n) {")
        self.emit("    gbs_vwf_expand((uint8_t)(n << 3));")
        self.emit("    set_bkg_data(gbs_vwf_tile, n, gbs_vwf_out);")
        self.emit("}")
        banked = bool(self.prelude_bank_defs.get("gbs_glyph_font"))
        # THE SHIFT IS HAND-WRITTEN sm83, LIKE THE REFERENCE'S (2026-09-07).
        # The C loop cost ~6,800 T-cycles a glyph: sdcc has no variable shift
        # on this CPU and emits a library loop per row, and the obvious C
        # rewrite (two direction-specific loops) measured WORSE. The reference engine's
        # `ui_print_shift_char` (`core/gb/ui_a.s`) is the same loop in
        # assembly with the rotate and mask in globals; this is that loop for
        # a 1bpp stage. OLDCALL puts both pointers on the stack at sp+2 /
        # sp+4 behind the return address, which is the one layout a naked
        # function can rely on. The emitter is GB-family only (see
        # gbs_vwf_upload above), so the sm83 mnemonics are not a second arm.
        self.emit("/* Shift a glyph by the pen and merge it under `mask` (1 bits keep the")
        self.emit("   destination). rotate bit 7 = shift LEFT by the low bits, else RIGHT.")
        self.emit("   The rotate and mask travel in globals so the asm core takes only the")
        self.emit("   two pointers (the reference's shape). */")
        self.emit("uint8_t gbs_vwf_rot, gbs_vwf_msk;")
        self.emit("static void gbs_vwf_shift8(uint8_t *dest, const uint8_t *src) OLDCALL NAKED {")
        self.emit("    dest; src;")
        self.emit("    __asm")
        self.emit("        ldhl sp, #2")
        self.emit("        ld a, (hl+)")
        self.emit("        ld e, a")
        self.emit("        ld a, (hl+)")
        self.emit("        ld d, a          ; de = dest")
        self.emit("        ld a, (hl+)")
        self.emit("        ld h, (hl)")
        self.emit("        ld l, a          ; hl = src")
        self.emit("        push hl")
        self.emit("        ld h, d")
        self.emit("        ld l, e")
        self.emit("        pop de           ; hl = dest, de = src")
        self.emit("        ld b, #8")
        self.emit("    1$:")
        self.emit("        ld a, (de)")
        self.emit("        inc de")
        self.emit("        ld c, a")
        self.emit("        ld a, (_gbs_vwf_rot)")
        self.emit("        bit 7, a")
        self.emit("        jr nz, 3$")
        self.emit("        or a")
        self.emit("        jr z, 5$")
        self.emit("    2$:")
        self.emit("        srl c")
        self.emit("        dec a")
        self.emit("        jr nz, 2$")
        self.emit("        jr 5$")
        self.emit("    3$:")
        self.emit("        and #0x7F")
        self.emit("        jr z, 5$")
        self.emit("    4$:")
        self.emit("        sla c")
        self.emit("        dec a")
        self.emit("        jr nz, 4$")
        self.emit("    5$:")
        self.emit("        ld a, (_gbs_vwf_msk)")
        self.emit("        and (hl)")
        self.emit("        ld (hl), a")
        self.emit("        ld a, (_gbs_vwf_msk)")
        self.emit("        cpl")
        self.emit("        and c")
        self.emit("        or (hl)")
        self.emit("        ld (hl+), a")
        self.emit("        dec b")
        self.emit("        jr nz, 1$")
        self.emit("        ret")
        self.emit("    __endasm;")
        self.emit("}")
        self.emit("static void gbs_vwf_shift(uint8_t *dest, const uint8_t *src,")
        self.emit("                          uint8_t rotate, uint8_t mask) {")
        if banked:
            self.emit("    uint8_t __entry = CURRENT_BANK;")
            self.emit("    if (gbs_glyph_bank) SWITCH_ROM(gbs_glyph_bank);")
        self.emit("    gbs_vwf_rot = rotate; gbs_vwf_msk = mask;")
        self.emit("    gbs_vwf_shift8(dest, src);")
        if banked:
            self.emit("    SWITCH_ROM(__entry);")
        self.emit("}")
        # The two masks were variable shifts too (`0xFF << dx`, `0xFF >> k`):
        # two more library loops per glyph. Nine and seventeen constant bytes.
        self.emit("static const uint8_t gbs_vwf_lm[9] = {0xFF, 0xFE, 0xFC, 0xF8, 0xF0, 0xE0, 0xC0, 0x80, 0x00};")
        self.emit("static const uint8_t gbs_vwf_rm[17] = {0xFF, 0x7F, 0x3F, 0x1F, 0x0F, 0x07, 0x03, 0x01,")
        self.emit("                                       0, 0, 0, 0, 0, 0, 0, 0, 0};")
        self.emit("void gbs_vwf_start(uint8_t col, uint8_t row) {")
        self.emit("    gbs_text_init();")
        self.emit("    if (gbs_glyph_count == 0) gbs_text_glyph_buffer(GBS_GLYPH_BASE, GBS_GLYPH_COUNT);")
        # There used to be a `set_1bpp_colors(3, 0)` here, because
        # set_bkg_1bpp_data expands through a GLOBAL pair and the glyph cache
        # was the only other thing to set it. gbs_vwf_upload does its own
        # expansion now, so the pair is nobody's business but
        # gbs_glyph_load's - which sets it for itself at every cache upload.
        self.emit("    gbs_vwf_tile = gbs_vwf_base;")
        self.emit("    gbs_vwf_col = col; gbs_vwf_row = row; gbs_vwf_pen = 0; gbs_vwf_mapped = 0;")
        self.emit("    gbs_vwf_blank(gbs_vwf_stage); gbs_vwf_blank(gbs_vwf_stage + 8);")
        self.emit("}")
        self.emit("void gbs_vwf_nl(uint8_t col, uint8_t row) {")
        self.emit("    /* A part-filled cell is ABANDONED, never packed (the reference does the")
        self.emit("       same): give up its tile and start the next line at pen 0. */")
        self.emit("    if (gbs_vwf_pen) { gbs_vwf_tile++; gbs_vwf_pen = 0; }")
        self.emit("    gbs_vwf_col = col; gbs_vwf_row = row; gbs_vwf_mapped = 0;")
        self.emit("    gbs_vwf_blank(gbs_vwf_stage); gbs_vwf_blank(gbs_vwf_stage + 8);")
        self.emit("}")
        self.emit("uint8_t gbs_vwf_glyph(uint8_t ch) {")
        self.emit("    uint8_t letter, width, dx, mask, done = 0;")
        self.emit("    const uint8_t *bitmap;")
        self.emit("    if (!gbs_vwf_room()) return 0;   /* band exhausted: drop the tail */")
        self.emit("    letter = (uint8_t)(ch - 32);")
        self.emit("    if (letter > (uint8_t)(GBS_GLYPH_N - 1)) letter = 0;")
        self.emit("    bitmap = gbs_glyph_src + (uint16_t)letter * 8;")
        self.emit("    width = gbs_vwf_width(letter);")
        self.emit("    if (width > 8) width = 8;")
        self.emit("    dx = (uint8_t)(8u - gbs_vwf_pen);")
        self.emit("    mask = (uint8_t)(gbs_vwf_lm[dx] | gbs_vwf_rm[(uint8_t)(gbs_vwf_pen + width)]);")
        self.emit("    gbs_vwf_shift(gbs_vwf_stage, bitmap, gbs_vwf_pen, mask);")
        self.emit("    if ((uint8_t)(gbs_vwf_pen + width) > 8u) {")
        self.emit("        mask = gbs_vwf_rm[(uint8_t)(width - dx)];")
        self.emit("        gbs_vwf_shift(gbs_vwf_stage + 8, bitmap, (uint8_t)(dx | 0x80u), mask);")
        self.emit("    }")
        self.emit("    gbs_vwf_pen = (uint8_t)(gbs_vwf_pen + width);")
        self.emit("    if (gbs_vwf_pen > 7u) {")
        self.emit("        gbs_vwf_pen = (uint8_t)(gbs_vwf_pen - 8u);")
        self.emit("        gbs_vwf_upload(gbs_vwf_pen ? 2 : 1);")
        self.emit("        gbs_vwf_put(gbs_vwf_col, gbs_vwf_row, gbs_vwf_tile);")
        self.emit("        gbs_vwf_col++;")
        self.emit("        gbs_vwf_tile++;")
        self.emit("        gbs_vwf_mapped = 0;          /* a new in-progress cell */")
        self.emit("        /* carry the spill down and blank the new spill (unrolled: fixed")
        self.emit("           addresses, so each move is one load and one store) */")
        self.emit("        gbs_vwf_stage[0] = gbs_vwf_stage[8];  gbs_vwf_stage[1] = gbs_vwf_stage[9];")
        self.emit("        gbs_vwf_stage[2] = gbs_vwf_stage[10]; gbs_vwf_stage[3] = gbs_vwf_stage[11];")
        self.emit("        gbs_vwf_stage[4] = gbs_vwf_stage[12]; gbs_vwf_stage[5] = gbs_vwf_stage[13];")
        self.emit("        gbs_vwf_stage[6] = gbs_vwf_stage[14]; gbs_vwf_stage[7] = gbs_vwf_stage[15];")
        self.emit("        gbs_vwf_blank(gbs_vwf_stage + 8);")
        self.emit("        done = 1;")
        self.emit("    } else {")
        self.emit("        gbs_vwf_upload(1);")
        self.emit("    }")
        self.emit("    /* Map the IN-PROGRESS cell too, so a half-drawn glyph is already on")
        self.emit("       screen -- which is what lets the typewriter reveal ride this. Once")
        self.emit("       per cell: its id is fixed until it spills. */")
        self.emit("    if (gbs_vwf_pen && !gbs_vwf_mapped && gbs_vwf_room()) {")
        self.emit("        gbs_vwf_put(gbs_vwf_col, gbs_vwf_row, gbs_vwf_tile);")
        self.emit("        gbs_vwf_mapped = 1;")
        self.emit("    }")
        self.emit("    return done;")
        self.emit("}")
        self.emit("/* The decimal digits of `v` through the SAME pen. A $var$ token cannot go")
        self.emit("   through gbs_print_number here: that plots whole CELLS and would stamp")
        self.emit("   over the composited tiles either side of it. */")
        self.emit("void gbs_vwf_number(uint16_t v) {")
        self.emit("    uint8_t d[5], k = 0, i;")
        self.emit("    if (v == 0) { d[k++] = 0; }")
        self.emit("    while (v > 0) { d[k++] = (uint8_t)(v % 10); v /= 10; }")
        self.emit("    for (i = 0; i < k; i++) gbs_vwf_glyph((uint8_t)(48 + d[k - 1 - i]));")
        self.emit("}")

    def _emit_win_sprite_cut(self):
        """`text.win_sprite_cut(on)` -- keep sprites OFF the window overlay.

        On DMG hardware OBJ draws ABOVE the window, so an actor standing low in
        the room pokes through an open dialogue box (measured on the reference-engine
        import: the player's lower objects intruded 4 px into the box). GB
        Studio solves it with an LCD interrupt - `interrupts.c` sets LYC to the
        scanline before the window and does `HIDE_SPRITES` there, and its VBL
        handler restores them - and this is that, scoped to the UI box.

        The interrupt itself is NOT here. `LYC_REG` has more than one tenant
        (the parallax bands are the other), so the cut contributes a STOP to
        the one emitted LCD handler and lets it dispatch - see `gbdk_lyc.py`,
        which owns `gbs_cut_on` / `gbs_cut_line` and the restore. This verb is
        therefore just "publish my stop and rebuild the list", which is what
        lets a box in a parallax room have its sprite cut at last: the two used
        to stand down for each other and the box simply went without.
        """
        if not self._lyc_used():
            # Sole tenant: no arbitration to do, so keep the original
            # emitter whole and stay byte-identical to before W7d.
            self._emit_win_sprite_cut_solo()
            return
        self.emit("/* ---- Sprites OFF the window overlay (text.win_sprite_cut) ----")
        self.emit("   Publish the cut's scanline and rebuild the LYC stop list; the one")
        self.emit("   emitted LCD interrupt does the hiding and its V-blank half the")
        self.emit("   restoring (see gbs_lyc_isr / gbs_lyc_vbl above). */")
        self.emit("void gbs_text_win_cut(uint8_t on) {")
        self.emit("    gbs_cut_on = on;")
        self.emit("    if (on) {")
        self.emit("        /* fire one line BEFORE the window's first, so the hide lands in")
        self.emit("           that H-blank rather than mid-scanline (the reference engine: WY_REG - 1). */")
        self.emit("        gbs_cut_line = WY_REG ? (uint8_t)(WY_REG - 1) : 0;")
        self.emit("        gbs_lyc_wire();")
        self.emit("    }")
        self.emit("    gbs_lyc_rebuild();")
        self.emit("    /* Closing restores at once rather than waiting for the next")
        self.emit("       V-blank, and only to what the PROGRAM asked for. */")
        self.emit("    if (!on && gbs_spr_want) SHOW_SPRITES;")
        self.emit("}")

    def _emit_win_overlay_cut(self):
        """`text.win_overlay_cut(y)` -- stop the window OVERLAY at scanline y.

        The reference engine's `overlay_cut_scanline` (W7d phase 2). At that line the
        window layer goes off and the sprites come back, so an overlay - the
        curtain, a full-width box - covers only the TOP of the screen with the
        room playing below it. Its default is 150, which is off-screen on a
        144-line display, so anything from SCREEN_HEIGHT up DISARMS the cut and
        a project that never writes the state pays nothing.

        The interrupt is NOT here when `LYC_REG` has another tenant: the cut
        contributes a STOP to the one emitted handler and lets it dispatch
        (`gbdk_lyc.py`, which owns `gbs_ocut_on` / `gbs_ocut_line` and the
        restore). Sole tenant, it owns the register itself - see
        `_emit_win_overlay_cut_solo`.
        """
        if not self._lyc_used():
            self._emit_win_overlay_cut_solo()
            return
        self.emit("/* ---- The window OVERLAY CUT (text.win_overlay_cut) ----")
        self.emit("   Publish the cut's scanline and rebuild the LYC stop list; the one")
        self.emit("   emitted LCD interrupt puts the window away there and gives the")
        self.emit("   sprites back, and its V-blank half restores the layer. */")
        self.emit("void gbs_text_win_overlay_cut(uint8_t y) {")
        self.emit("    if (y < SCREEN_HEIGHT) {")
        self.emit("        gbs_ocut_on = 1;")
        self.emit("        gbs_ocut_line = y;")
        self.emit("        gbs_lyc_wire();")
        self.emit("    } else {")
        self.emit("        /* 150 is the reference engine's own default and is off the bottom of the")
        self.emit("           screen, so any line at or past it means NO cut. */")
        self.emit("        gbs_ocut_on = 0;")
        self.emit("    }")
        self.emit("    gbs_lyc_rebuild();")
        self.emit("    /* Disarming gives the layer back at once rather than waiting for")
        self.emit("       the next V-blank, and only as far as the program wanted it. */")
        self.emit("    if (!gbs_ocut_on && gbs_win_want) SHOW_WIN;")
        self.emit("}")

    def _emit_win_overlay_cut_solo(self):
        """`text.win_overlay_cut(y)` when the cut is the ONLY tenant of `LYC_REG`.

        One tenant has nothing to arbitrate: its stop list would be its own
        chain, and the merge machinery is ~145 B of resident image (MEASURED)
        that could never merge anything. So it owns the register directly, the
        same rule `_emit_win_sprite_cut_solo` follows.

        It writes STAT ONCE, at wire time (`_emit_solo_lyc_source`, the DMG
        STAT-write bug); arming moves `LYC_REG` and disarming only drops
        `gbs_ocut_on`, which the handler already stood down on.
        """
        self.emit("/* ---- The window OVERLAY CUT (text.win_overlay_cut) ----")
        self.emit("   the reference engine's overlay_cut_scanline: at this line the WINDOW goes off")
        self.emit("   and the sprites come back, so an overlay covers only the TOP of the")
        self.emit("   screen with the room playing below it (the second half of its own")
        self.emit("   simple_LCD_isr). Sole tenant of LYC_REG here, so this arms the")
        self.emit("   register itself; with a second tenant gbdk_lyc.py owns it and this")
        self.emit("   verb becomes a stop in that walk.")
        self.emit("")
        self.emit("   `gbs_spr_want` / `gbs_win_want` mirror what the PROGRAM asked for")
        self.emit("   (video.show_sprites / hide_sprites, video.show_window /")
        self.emit("   hide_window, text.to_window / to_bkg), so neither restore can give")
        self.emit("   back something the game itself took away. Both are defined with")
        self.emit("   those verbs further down the prelude. */")
        self.emit("extern uint8_t gbs_spr_want;")
        if not self.text_window_used:
            # ...declared with the window router when there IS one (it writes
            # the flag), so only a program with no `to_window` needs it here.
            self.emit("extern uint8_t gbs_win_want;")
        self.emit("static uint8_t gbs_ocut_wired = 0;")
        self.emit("uint8_t gbs_ocut_on;     /* a program wants the overlay cut */")
        self.emit("uint8_t gbs_ocut_line;   /* ...at this scanline */")
        self.emit("void gbs_ocut_lcd_isr(void) NONBANKED {")
        self.emit("    /* This handler outlives whatever armed it (add_LCD cannot be")
        self.emit("       undone), so a disarmed cut must stand down completely. */")
        self.emit("    if (!gbs_ocut_on) return;")
        self.emit("    /* In H-BLANK, or the LCDC write tears the line it lands on. */")
        self.emit("    while (STAT_REG & STATF_BUSY) ;")
        self.emit("    HIDE_WIN;")
        self.emit("    if (gbs_spr_want) SHOW_SPRITES;")
        self.emit("}")
        self.emit("void gbs_ocut_vbl_isr(void) NONBANKED {")
        self.emit("    if (gbs_win_want) SHOW_WIN;")
        self.emit("}")
        self.emit("void gbs_text_win_overlay_cut(uint8_t y) {")
        self.emit("    if (y < SCREEN_HEIGHT) {")
        self.emit("        if (!gbs_ocut_wired) {")
        self.emit("            CRITICAL {")
        self.emit("                add_LCD(gbs_ocut_lcd_isr);")
        self.emit("                add_VBL(gbs_ocut_vbl_isr);")
        self._emit_solo_lyc_source(" " * 16)
        self.emit("            }")
        # OR into the LIVE mask rather than assigning it: another feature may
        # already own an interrupt (native.huge drives hUGEDriver off the
        # TIMER), and assigning would turn it off the moment the cut was armed.
        self.emit("            set_interrupts(IE_REG | VBL_IFLAG | LCD_IFLAG);")
        self.emit("            gbs_ocut_wired = 1;")
        self.emit("        }")
        self.emit("        /* The line BEFORE the flag: the handler may fire in between, and")
        self.emit("           must not find the cut armed at the previous line. */")
        self.emit("        gbs_ocut_line = y;")
        self.emit("        LYC_REG = y;")
        self.emit("        gbs_ocut_on = 1;")
        self.emit("    } else {")
        self.emit("        /* 150 is the reference engine's own default and is off the bottom of the")
        self.emit("           screen, so any line at or past it means NO cut. The flag is")
        self.emit("           the whole disarm: the handler stands down on it. */")
        self.emit("        gbs_ocut_on = 0;")
        self.emit("        if (gbs_win_want) SHOW_WIN;")
        self.emit("    }")
        self.emit("}")

    def _emit_win_sprite_cut_solo(self):
        """`text.win_sprite_cut(on)` when the cut is the ONLY tenant of `LYC_REG`.

        Every VM project with a dialogue box and no parallax bands is in this
        shape (only the ones with `box_hides_sprites`, the reference-engine
        conversions, ever ARM it). With bands in the program too,
        `gbdk_lyc.py` owns the register and the verb becomes two lines; see
        `_emit_win_sprite_cut`.

        STAT is written ONCE, at wire time (`_emit_solo_lyc_source`). It used
        to be set at every box open and cleared at every box close, and on a
        MONOCHROME Game Boy each of those writes can raise a spurious LCD
        interrupt: this handler then did HIDE_SPRITES at whatever line the
        beam was on, and the sprites vanished from there to the bottom of the
        frame - on box open and on box close. The handler stands down on
        `gbs_cut_on` now, so it is entered once a frame even with no box up.

        The `if (gbs_px_n)` stand-downs this used to carry for a program with
        parallax bands are gone: such a program has two LYC tenants and never
        reaches this emitter (`_lyc_used`).

        Keep sprites OFF the window overlay.

        On DMG hardware OBJ draws ABOVE the window, so an actor standing low in
        the room pokes through an open dialogue box (measured on the reference-engine
        import: the player's lower objects intruded 4 px into the box). GB
        Studio solves it with an LCD interrupt - `interrupts.c` sets LYC to the
        scanline before the window and does `HIDE_SPRITES` there, and its VBL
        handler restores them - and this is that, scoped to the UI box.

        `gbs_spr_want` mirrors what the PROGRAM asked for (video.show_sprites /
        hide_sprites), so the per-frame restore can never turn sprites back on
        for a game that wanted them off.
        """
        self.emit("/* ---- Sprites OFF the window overlay (text.win_sprite_cut) ----")
        self.emit("   OBJ draws above the WINDOW on this hardware, so an actor low in")
        self.emit("   the room would show through an open dialogue box. Cut them at the")
        self.emit("   window's first scanline with an LYC interrupt and restore them")
        self.emit("   each VBL -- the reference engine's own interrupts.c model. */")
        # Defined with gbs_show_sprites/gbs_hide_sprites further down the
        # prelude (they own the program's own show/hide state); the ISRs here
        # only READ it, so a forward declaration is enough.
        self.emit("extern uint8_t gbs_spr_want;")
        self.emit("static uint8_t gbs_cut_wired = 0;")
        self.emit("uint8_t gbs_cut_on;     /* a box wants the cut; the handler's stand-down */")
        self.emit("void gbs_win_lcd_isr(void) NONBANKED {")
        self.emit("    /* Entered once a frame whether or not a box is up: the LYC source")
        self.emit("       stays enabled (see gbs_text_win_cut), so THIS is the disarm. */")
        self.emit("    if (!gbs_cut_on) return;")
        self.emit("    HIDE_SPRITES;")
        self.emit("}")
        self.emit("void gbs_win_vbl_isr(void) NONBANKED {")
        self.emit("    if (gbs_spr_want) SHOW_SPRITES;")
        self.emit("}")
        self.emit("void gbs_text_win_cut(uint8_t on) {")
        self.emit("    if (on) {")
        self.emit("        if (!gbs_cut_wired) {")
        self.emit("            CRITICAL {")
        self.emit("                add_LCD(gbs_win_lcd_isr);")
        self.emit("                add_VBL(gbs_win_vbl_isr);")
        self._emit_solo_lyc_source(" " * 16)
        self.emit("            }")
        # OR into the LIVE mask rather than assigning it: another feature may
        # already own an interrupt (native.huge drives hUGEDriver from the
        # TIMER), and assigning would turn it off the moment a box opened -
        # which for the music driver means the song stops dead mid-dialogue.
        self.emit("            set_interrupts(IE_REG | VBL_IFLAG | LCD_IFLAG);")
        self.emit("            gbs_cut_wired = 1;")
        self.emit("        }")
        self.emit("        /* fire one line BEFORE the window's first, so the hide lands in")
        self.emit("           that H-blank rather than mid-scanline (the reference engine: WY_REG - 1).")
        self.emit("           The line BEFORE the flag: the handler may fire in between, and")
        self.emit("           must not find the cut armed at the previous box's line. */")
        self.emit("        LYC_REG = WY_REG ? (uint8_t)(WY_REG - 1) : 0;")
        self.emit("        gbs_cut_on = 1;")
        self.emit("    } else {")
        self.emit("        gbs_cut_on = 0;")
        self.emit("        if (gbs_spr_want) SHOW_SPRITES;")
        self.emit("    }")
        self.emit("}")

    def _emit_glyph_text_init(self):
        """`gbs_text_init` in glyph-buffer mode: set the band up, nothing else.

        This is where most of the mode's ROM saving comes from - there is no
        `font_init` / `font_load` / tile probe and no GBDK printf console, so
        the whole font allocator drops out of the link.
        """
        self.emit("/* Glyph-buffer mode: no font is loaded into VRAM at all, so init is just")
        self.emit("   'make sure the band exists'. A program that calls text.glyph_buffer()")
        self.emit("   before printing never takes the default. */")
        self.emit("void gbs_text_init(void) {")
        self.emit("    if (gbs_glyph_count == 0)")
        self.emit("        gbs_text_glyph_buffer(GBS_GLYPH_BASE, GBS_GLYPH_COUNT);")
        self.emit("}")

    def _emit_glyph_font_swap(self):
        """`text.set_font` / `set_font_at` in glyph-buffer mode.

        A custom sheet is the same 96-glyph 2bpp PNG-derived data the resident
        path uploads, except here it is never uploaded wholesale - it becomes
        the ROM source the rasterizer reads, so a swap costs no VRAM at all.
        `set_font_at`'s tile base RELOCATES THE BAND, which is the honest
        reading of what its callers want (get the glyphs out of the tileset's
        way) now that there is no 96-tile block to place.
        """
        # The font table's own bank (O5). A custom sheet is HOME data, so a
        # swap must also stop the rasterizer switching -- emitted only when the
        # built-in table actually banked, so the non-banking path is unchanged.
        banked_font = bool(self.prelude_bank_defs.get("gbs_glyph_font"))
        if self.font_set_used:
            self.emit("/* Custom font: point the rasterizer at the caller's 2bpp sheet and")
            self.emit("   re-seed the cache, so glyphs already on screen are redrawn from it. */")
            self.emit("void gbs_set_font(const uint8_t *data) {")
            self.emit("    gbs_glyph_src = data;")
            if banked_font:
                self.emit("    gbs_glyph_bank = 0;   /* caller data is home; do not switch */")
            self.emit("    gbs_glyph_2bpp = 1;")
            self.emit("    if (gbs_glyph_count == 0)")
            self.emit("        gbs_text_glyph_buffer(GBS_GLYPH_BASE, GBS_GLYPH_COUNT);")
            self.emit("    else gbs_text_glyph_buffer(gbs_glyph_base, gbs_glyph_count);")
            self.emit("}")
        if self.font_at_used:
            self.emit("/* Custom font at a caller-chosen base: in glyph-buffer mode there is no")
            self.emit("   96-tile block to place, so the base MOVES THE BAND -- which is what")
            self.emit("   the large-tileset callers were asking for in the first place. */")
            self.emit("void gbs_set_font_at(uint8_t base, const uint8_t *data) {")
            self.emit("    gbs_glyph_src = data;")
            if banked_font:
                self.emit("    gbs_glyph_bank = 0;   /* caller data is home; do not switch */")
            self.emit("    gbs_glyph_2bpp = 1;")
            self.emit("    gbs_text_glyph_buffer(base, gbs_glyph_count ? gbs_glyph_count")
            self.emit("                                                : GBS_GLYPH_COUNT);")
            self.emit("}")

    def _emit_gbdk_text(self):
        """The text helpers themselves (only when text is actually CALLED).

        Emitted post-conditional-compilation, so a program whose text is
        compiled out pays nothing -- printf alone overflows NES NROM.
        """
        # The GLYPH-BUFFER mode replaces the resident font entirely: no
        # font_init / font_load / probe, no GBDK console, no 96 reserved tiles.
        # It needs a tile-plotting text path, so it is only offered where one
        # exists (the GB family and SMS/GG); the NES keeps printf and the verb
        # is a no-op there.
        glyphs = self.glyph_buffer_used and (self.caps['has_window']
                                             or self.platform in ('sms', 'gamegear'))
        if self.text_used:
            if glyphs:
                self._emit_gbdk_glyph_font()
            else:
                self.emit("/* Text needs a font loaded before any glyph is drawn; do it lazily on")
                self.emit("   first use (once). The font glyphs occupy background tile-DATA slots")
                self.emit("   (tile = first_tile + ASCII-32), which collides with a game's")
                self.emit("   background tileset -- that also grows up from tile 0. GBDK's font")
                self.emit("   allocator hands out tiles sequentially from font_init(), so two")
                self.emit("   throwaway loads (font_ibm = 96 tiles, then font_min) push the REAL")
                self.emit("   font up to tile 139 (measured), reserving 0..138 for the game's tileset and")
                self.emit("   leaving the top for animated/extra tiles (the reference-engine 'reserve")
                self.emit("   tiles for the font' rule, inverted because GBDK pins the font low). */")
            if self.caps['has_window']:
                # GB family (gameboy/gbc/pocket/megaduck): a 20-col visible window
                # over a 32-col bkg map, and the sm83 tile-xy primitives. printf
                # clamps the cursor to cols 0..19, so a camera-scrolled dialogue
                # box (col = camx/8 + n) past col 19 lost its tail off-screen.
                # Plot glyphs straight into the 32-col map instead; the hardware
                # scroll wraps them on-screen, so the box survives any scroll.
                if not glyphs:
                    self.emit("uint8_t gbs_font_base = 132;  /* pre-probe default; the probe below finds 139 */")
                win = self.text_window_used
                if win:
                    # WINDOW-LAYER text (the vm.core UI path, the reference engine's overlay
                    # model): when gbs_text_win is set, glyphs go to the WINDOW map
                    # (0x9C00) instead of the scrolling bkg map, at row `y -
                    # gbs_win_row` -- so a box drawn at its authored SCREEN row lands
                    # on a bottom-anchored window (positioned by gbs_text_to_window).
                    # The window never scrolls with the camera and never touches the
                    # scene tilemap, so a box no longer clobbers game tiles, drifts
                    # with the scroll, or needs a blank-band clear on close.
                    if self.overlay_cut_used and not self._lyc_used():
                        # The merged form declares this in the LYC block above;
                        # the SOLO overlay cut is emitted after the window
                        # router, which writes it, so it needs the extern here.
                        self.emit("extern uint8_t gbs_win_want;  /* the program's own window state */")
                    self.emit("static uint8_t gbs_text_win = 0;  /* 0 = bkg map, 1 = window map */")
                    self.emit("static uint8_t gbs_win_row = 0;   /* screen row mapped to window row 0 */")
                if glyphs:
                    self._emit_glyph_text_init()
                else:
                    self.emit("void gbs_text_init(void) {")
                    self.emit("    static uint8_t gbs_font_ready = 0;")
                    self.emit("    if (!gbs_font_ready) {")
                    self.emit("        uint8_t saved;")
                    self.emit("        font_init();")
                    self.emit("        font_load(font_ibm);  /* pad: reserve bkg tiles 0..95 */")
                    self.emit("        font_load(font_min);   /* pad: reserve bkg tiles 96..138 */")
                    self.emit("        font_set(font_load(font_ibm));  /* active font -> tiles 139..234 */")
                    self.emit("        /* Learn the active font's first tile rather than assuming it:")
                    self.emit("           write the ' ' glyph (font index 0), read the tile id back,")
                    self.emit("           then restore the probed cell. */")
                    self.emit("        saved = get_bkg_tile_xy(0, 0);")
                    self.emit("        gotoxy(0, 0); putchar(' ');")
                    self.emit("        gbs_font_base = get_bkg_tile_xy(0, 0);")
                    self.emit("        set_bkg_tile_xy(0, 0, saved);")
                    self.emit("        gbs_font_ready = 1;")
                    self.emit("    }")
                    self.emit("}")

                def _plot(xexpr, yexpr, tref):
                    """Emit a 1-tile write of `tref` (a &t-style pointer) at screen
                    (xexpr, yexpr) -- to the window map when gbs_text_win, else the
                    bkg map. xexpr/yexpr are emitted verbatim (already parenthesized
                    where needed), so the non-window (win False) form is BYTE-
                    IDENTICAL to the original single set_bkg_tiles call."""
                    if win:
                        self.emit("        if (gbs_text_win) set_win_tiles((uint8_t)(%s & 31), "
                                  "(uint8_t)((uint8_t)(%s - gbs_win_row) & 31), 1, 1, %s);"
                                  % (xexpr, yexpr, tref))
                        self.emit("        else set_bkg_tiles((uint8_t)(%s & 31), (uint8_t)(%s & 31), 1, 1, %s);"
                                  % (xexpr, yexpr, tref))
                    else:
                        self.emit("        set_bkg_tiles((uint8_t)(%s & 31), (uint8_t)(%s & 31), 1, 1, %s);"
                                  % (xexpr, yexpr, tref))

                self.emit("/* Plot glyphs into the 32x32 bkg map (all cols/rows, & 31) rather than")
                self.emit("   gotoxy()+printf() -- printf clamps to the 20-col window and wrapped a")
                if win:
                    self.emit("   scrolled box's tail off-screen. (Window map when gbs_text_win.) */")
                else:
                    self.emit("   scrolled box's tail off-screen. */")
                # AFTER the plotters: gbs_vwf_put reuses the same window/bkg
                # router, and gbs_text_win / gbs_win_row / gbs_text_init are all
                # `static` in this TU, so this block cannot be hoisted above them
                # (a forward `extern` would clash with the static definition).
                if self.vwf_used:
                    self._emit_gbdk_vwf(win)
                # ROW-BUFFERED PLOTTING (review E-3, 2026-09-06). Every GBDK
                # tile-map call waits for VRAM access itself, and the plotters
                # made one call per CELL - a 20-character line paid that wait
                # twenty times, a box clear once per cell. Glyphs are staged
                # in a 32-cell row and flushed in RUNS, one call each; a run
                # never crosses column 31, because the map wraps there and a
                # linear write does not (the `& 31` the per-cell form relied
                # on). print_number extracts its digits by repeated
                # subtraction instead of two u16 library divisions per digit.
                # Same cells, same order, same tiles as the per-cell form.
                self.emit("/* Row-buffered plotting: a 32-cell stage flushed in runs, one tile-map")
                self.emit("   call per run instead of per cell (each call waits for VRAM itself).")
                self.emit("   A run stops at column 31 - the map wraps there, a write does not. */")
                self.emit("static uint8_t gbs_text_row[32];")
                self.emit("static void gbs_text_flush(uint8_t x, uint8_t y, uint8_t n) {")
                self.emit("    const uint8_t *p = gbs_text_row;")
                self.emit("    uint8_t x0 = (uint8_t)(x & 31), n0;")
                if win:
                    self.emit("    y = gbs_text_win ? (uint8_t)((uint8_t)(y - gbs_win_row) & 31) : (uint8_t)(y & 31);")
                else:
                    self.emit("    y = (uint8_t)(y & 31);")
                self.emit("    while (n) {")
                self.emit("        n0 = (uint8_t)(32 - x0);")
                self.emit("        if (n0 > n) n0 = n;")
                if win:
                    self.emit("        if (gbs_text_win) set_win_tiles(x0, y, n0, 1, p);")
                    self.emit("        else set_bkg_tiles(x0, y, n0, 1, p);")
                else:
                    self.emit("        set_bkg_tiles(x0, y, n0, 1, p);")
                self.emit("        p += n0; n = (uint8_t)(n - n0); x0 = 0;")
                self.emit("    }")
                self.emit("}")
                self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s) {")
                self.emit("    uint8_t n;")
                self.emit("    gbs_text_init();")
                self.emit("    while (*s) {")
                self.emit("        n = 0;")
                self.emit("        while (*s && n < 32) { gbs_text_row[n++] = %s; s++; }" % self._glyph_tile("*s"))
                self.emit("        gbs_text_flush(x, y, n);")
                self.emit("        x = (uint8_t)(x + n);")
                self.emit("    }")
                self.emit("}")
                # The digits come from repeated SUBTRACTION (two u16 library
                # divisions per digit is what this replaced), but from a LOOP
                # over the powers rather than four unrolled blocks: measured
                # 2026-09-06, unrolling cost ~100 B of RESIDENT image on a
                # project already over the bank-0 ceiling, and the loop keeps
                # the divisions gone for one index and one table read a digit.
                self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n) {")
                self.emit("    static const uint16_t pw[4] = { 10000, 1000, 100, 10 };")
                self.emit("    uint8_t k = 0, d, i;")
                self.emit("    gbs_text_init();")
                self.emit("    for (i = 0; i < 4; ++i) {")
                self.emit("        d = 0;")
                self.emit("        while (n >= pw[i]) { n -= pw[i]; ++d; }")
                self.emit("        if (k || d) gbs_text_row[k++] = %s;" % self._glyph_digit("d"))
                self.emit("    }")
                self.emit("    gbs_text_row[k++] = %s;" % self._glyph_digit("(uint8_t)n"))
                self.emit("    gbs_text_flush(x, y, k);")
                self.emit("}")
                self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {")
                self.emit("    uint8_t i, j;")
                self.emit("    gbs_text_init();")
                self.emit("    if (w > 32) w = 32;  /* the map is 32 wide: wider only re-clears */")
                self.emit("    for (i = 0; i < w; i++) gbs_text_row[i] = gbs_font_base;")
                self.emit("    for (j = 0; j < h; j++) gbs_text_flush(x, (uint8_t)(y + j), w);")
                self.emit("}")
                if self.plot_tile_used:
                    self.emit("/* Plot ONE raw tile id at a text cell through the same router as the")
                    self.emit("   glyph plotters -- the window map while text is routed there, else")
                    self.emit("   the bkg map. The custom-frame seam: the reference engine draws its 9-slice")
                    self.emit("   dialogue frame into the WINDOW map; a frame plotted here composes")
                    self.emit("   with the overlay instead of being covered by it (as a frame put")
                    self.emit("   down with bkg.set_tiles is -- the opaque window sits above it). */")
                    self.emit("void gbs_plot_tile(uint8_t x, uint8_t y, uint8_t t) {")
                    if win:
                        # One map byte as one byte (the gbs_vwf_put rule,
                        # 2026-09-07): a 9-slice box frame is 80 of these per
                        # open, and the rectangle copier cost ~1,200 T-cycles
                        # each for a single cell.
                        self.emit("    if (gbs_text_win) set_vram_byte(get_win_xy_addr((uint8_t)(x & 31), (uint8_t)((uint8_t)(y - gbs_win_row) & 31)), t);")
                        self.emit("    else set_vram_byte(get_bkg_xy_addr((uint8_t)(x & 31), (uint8_t)(y & 31)), t);")
                    else:
                        self.emit("    set_bkg_tiles((uint8_t)(x & 31), (uint8_t)(y & 31), 1, 1, &t);")
                    self.emit("}")
                if win:
                    self.emit("/* Route text to the WINDOW layer, bottom-anchored (the reference engine's overlay):")
                    self.emit("   the box authored at screen rows [origin_row .. origin_row+box_rows) is")
                    self.emit("   relocated to the bottom box_rows rows via WY, so it overlays the scene")
                    self.emit("   without touching the bkg map or scrolling with the camera. */")
                    self.emit("void gbs_text_to_window(uint8_t origin_row, uint8_t box_rows) {")
                    self.emit("    uint8_t i, j;")
                    self.emit("    gbs_text_init();")
                    self.emit("    gbs_win_row = origin_row;")
                    self.emit("    gbs_text_win = 1;")
                    self.emit("    /* clear the visible window band to the space glyph (no stale tiles):")
                    self.emit("       one row-wide write per row (see gbs_text_row) */")
                    self.emit("    for (i = 0; i < 20; i++) gbs_text_row[i] = gbs_font_base;")
                    self.emit("    for (j = 0; j < box_rows; j++) set_win_tiles(0, j, 20, 1, gbs_text_row);")
                    self.emit("    move_win(7, (uint8_t)(SCREEN_HEIGHT - (uint16_t)box_rows * 8));")
                    self.emit("}")
                    self.emit("/* SHOW the band this prepared, once the caller has DRAWN into it.")
                    self.emit("   Split from to_window because a box is ASSEMBLED - blank the band,")
                    self.emit("   draw the 9-slice frame, then the glyphs - and all of that happens")
                    self.emit("   inside one VM frame that spans more than one LCD frame. Switching")
                    self.emit("   the layer on at the START of it displays the half-built states:")
                    self.emit("   measured on the reference-engine sample conversion, blank paper, then the border, then the")
                    self.emit("   text, one display frame each. */")
                    self.emit("void gbs_text_win_reveal(void) {")
                    if self.overlay_cut_used:
                        # The OVERLAY CUT's V-blank half turns the window back
                        # on, and may only give back what the program asked
                        # for - so every place that shows or hides the layer
                        # records the wish, exactly as gbs_spr_want is recorded
                        # by video.show_sprites / hide_sprites.
                        self.emit("    gbs_win_want = 1;")
                    self.emit("    SHOW_WIN;")
                    self.emit("}")
                    self.emit("/* Hide the overlay window + route text back to the bkg map. The scene")
                    self.emit("   tilemap was never touched, so no clear/redraw is needed. */")
                    if self.overlay_cut_used:
                        self.emit("void gbs_text_to_bkg(void) { gbs_text_win = 0; gbs_win_want = 0; HIDE_WIN; }")
                    else:
                        self.emit("void gbs_text_to_bkg(void) { gbs_text_win = 0; HIDE_WIN; }")
                    if self.win_cut_used:
                        self._emit_win_sprite_cut()
                    self.emit("/* True while a UI box is live on the window overlay -- the caller skips")
                    self.emit("   the bkg clear + room repaint (the window leaves the scene untouched). */")
                    self.emit("uint8_t gbs_text_window_active(void) { return gbs_text_win; }")
                # ...but the OVERLAY CUT is emitted whether or not TEXT routes
                # to the window. `text.to_window` is only one way to put the
                # layer up - vm.fx's curtain raises it with window.move +
                # video.show_window and no text at all, and cutting THAT short
                # is the feature's first use. So it is gated on the window
                # LAYER existing, not on the box path using it.
                if self.overlay_cut_used:
                    self._emit_win_overlay_cut()
                if glyphs:
                    self._emit_glyph_font_swap()
                else:
                    if self.font_set_used:
                        self.emit("/* Custom font: overwrite the loaded font's 96 glyph tiles (ASCII 32..127)")
                        self.emit("   with a user sheet. The glyph tiles are ordinary bkg tile DATA, so a")
                        self.emit("   font swap is a pure set_bkg_data over the font base -- zero extra tiles. */")
                        self.emit("void gbs_set_font(const uint8_t *data) { gbs_text_init(); set_bkg_data(gbs_font_base, 96, data); }")
                    if self.font_at_used:
                        self.emit("/* Custom font at a caller-chosen tile base (the SMS/GG large-tileset")
                        self.emit("   escape; honored here too -- the plotters read gbs_font_base). */")
                        self.emit("void gbs_set_font_at(uint8_t base, const uint8_t *data) { gbs_text_init(); set_bkg_data(base, 96, data); gbs_font_base = base; }")
            elif self._smsgg_plot_path:
                # THE one gate (it was spelled out here a second time, and a
                # second copy of a condition is a second chance to disagree
                # with `to_window` about who owns the name-table path).
                # Game Gear, and the SMS when a FONT SWAP is used: plot glyphs
                # straight into the 32-col name table with set_bkg_tile_xy (& 31).
                # - Game Gear (always): its name table is 32 cols (like the SMS),
                #   but the LCD only shows the CENTRE 20x18 -- so printf, which
                #   wraps its cursor at DEVICE_SCREEN_WIDTH (20), garbled any box
                #   drawn at a camera-scrolled column (col = camx/8 + n): the tail
                #   past col 19 wrapped to col 0 of the next row (a dialogue/shop
                #   box drifted + split). Plotting lets the hardware scroll bring
                #   the cells on-screen -- exactly the GB-family fix, minus the
                #   get_bkg_tile_xy font-base probe the z80 port lacks (read the
                #   font's first_tile from its handle instead).
                # - SMS (font-swap programs only): printf only draws the console
                #   font at its own base, so a font RELOCATED by set_font_at
                #   (the large-tileset escape -- the SMS pattern table is FLAT,
                #   and GBDK's console font at tiles 0..95 is overwritten by a
                #   large game tileset) would be unreadable; the tile plotters
                #   read gbs_font_base, so they follow the relocation. The SMS
                #   32-col screen = the full name table, so nothing ever clips.
                #   NO-swap SMS programs keep the printf path (byte-identical).
                # - The SMS also takes this branch in GLYPH-BUFFER mode, for the
                #   same reason: the band sits above the tileset and printf can
                #   only draw the console font at its own base.
                if not glyphs:
                    self.emit("uint8_t gbs_font_base = 0;  /* active font's first glyph tile */")
                if glyphs:
                    self._emit_glyph_text_init()
                else:
                    self.emit("void gbs_text_init(void) {")
                    self.emit("    static uint8_t gbs_font_ready = 0;")
                    self.emit("    if (!gbs_font_ready) {")
                    self.emit("        font_t fh;")
                    self.emit("        font_init();")
                    # Load the font ONCE (tiles 0..95): the GB-family
                    # two-throwaway-load trick corrupts set_bkg_data on the z80
                    # port (see the SMS note below), so it is not used here.
                    self.emit("        fh = font_load(font_ibm);")
                    self.emit("        font_set(fh);")
                    self.emit("        gbs_font_base = ((pmfont_handle)fh)->first_tile;")
                    self.emit("        gbs_font_ready = 1;")
                    self.emit("    }")
                    self.emit("}")
                if self.text_window_used:
                    self._emit_smsgg_ui_space()
                self.emit("/* Plot glyphs into the 32-col name table (& 31) rather than")
                self.emit("   gotoxy()+printf(): printf wraps at the GG's 20-col window and split")
                self.emit("   a scrolled box. The hardware scroll wraps the cells on-screen. */")
                self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s) {")
                self.emit("    uint8_t i = 0, t;")
                self.emit("    if (y >= 28) return;  /* rows >= 28 overrun the name table into the SAT */")
                self.emit("    gbs_text_init();")
                self.emit("    while (s[i]) {")
                self.emit("        t = %s;" % self._glyph_tile("s[i]"))
                self.emit("        set_bkg_tile_xy(%s, %s, t);"
                          % (self._ui_cell_x("x + i", "(uint8_t)((x + i) & 31)"),
                             self._ui_cell_y("y", "y")))
                self.emit("        i++;")
                self.emit("    }")
                self.emit("}")
                self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n) {")
                self.emit("    uint8_t digits[5]; uint8_t k = 0; uint8_t i, t;")
                self.emit("    if (y >= 28) return;  /* rows >= 28 overrun the name table into the SAT */")
                self.emit("    gbs_text_init();")
                self.emit("    if (n == 0) { digits[k++] = 0; }")
                self.emit("    while (n > 0) { digits[k++] = (uint8_t)(n % 10); n /= 10; }")
                self.emit("    for (i = 0; i < k; i++) {")
                self.emit("        t = %s;" % self._glyph_digit("digits[k - 1 - i]"))
                self.emit("        set_bkg_tile_xy(%s, %s, t);"
                          % (self._ui_cell_x("x + i", "(uint8_t)((x + i) & 31)"),
                             self._ui_cell_y("y", "y")))
                self.emit("    }")
                self.emit("}")
                self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {")
                self.emit("    uint8_t i, j; uint8_t blank;")
                self.emit("    gbs_text_init();")
                self.emit("    blank = gbs_font_base;  /* the space glyph */")
                self.emit("    for (j = 0; j < h; j++) {")
                self.emit("        if ((uint8_t)(y + j) >= 28) break;  /* SAT clamp, like gbs_set_bkg_tiles */")
                self.emit("        for (i = 0; i < w; i++) { set_bkg_tile_xy(%s, %s, blank); }"
                          % (self._ui_cell_x("x + i", "(uint8_t)((x + i) & 31)"),
                             self._ui_cell_y("y + j", "(uint8_t)(y + j)")))
                self.emit("    }")
                self.emit("}")
                if self.plot_tile_used:
                    self.emit("/* Raw tile plot at a text cell (the custom-frame seam). No GB-style")
                    self.emit("   window layer here -- the name table, with the SAT row clamp. */")
                    self.emit("void gbs_plot_tile(uint8_t x, uint8_t y, uint8_t t) {")
                    self.emit("    if (y >= 28) return;  /* rows >= 28 overrun the name table into the SAT */")
                    self.emit("    set_bkg_tile_xy(%s, %s, t);"
                              % (self._ui_cell_x("x", "(uint8_t)(x & 31)"),
                                 self._ui_cell_y("y", "y")))
                    self.emit("}")
                if glyphs:
                    self._emit_glyph_font_swap()
                if self.font_set_used and not glyphs:
                    self.emit("/* Custom font: overwrite the loaded font's 96 glyph tiles (ASCII 32..127). */")
                    self.emit("void gbs_set_font(const uint8_t *data) { gbs_text_init(); set_bkg_data(gbs_font_base, 96, data); }")
                if self.font_at_used and not glyphs:
                    self.emit("/* Custom font at a caller-chosen tile base: the SMS/GG bkg pattern")
                    self.emit("   table has no reserved font block, so a large tileset overwrites the")
                    self.emit("   low console font (tiles 0..95) -- relocate the swapped font above the")
                    self.emit("   tileset. NOTE the GB-compat layout gives the background only tiles")
                    self.emit("   0..191: the name table lives at VRAM 0x1800 (= tiles 192..247) and")
                    self.emit("   the SAT at 0x1F00 (= 248..255), so base + 96 must stay <= 192 --")
                    self.emit("   base 96 over a <= 96-tile tileset is the full-range layout. */")
                    self.emit("void gbs_set_font_at(uint8_t base, const uint8_t *data) { gbs_text_init(); set_bkg_data(base, 96, data); gbs_font_base = base; }")
            else:
                # SMS (no font swap) / NES: their screens are 32 cols wide (= the
                # map), so printf does not clip -- keep the simple console path,
                # byte-identical. (The Game Gear shares the SMS name table but
                # shows only a 20-col window, so it needs the plotting path above.)
                # (An SMS program that SWAPS the font -- set_font / set_font_at --
                # takes the tile-plotting branch above instead, so this branch is
                # SMS-no-swap + NES only; the NES draws glyphs from CHR, so a
                # font swap is a graceful no-op there.)
                self.emit("void gbs_text_init(void) {")
                self.emit("    static uint8_t gbs_font_ready = 0;")
                self.emit("    if (!gbs_font_ready) {")
                self.emit("        font_init();")
                if self.platform == 'sms':
                    # The GB-family trick of two throwaway font_loads (to push the
                    # active font up to tile 139, clear of a game's tileset)
                    # CORRUPTS set_bkg_data on the SMS z80 port: after the extra
                    # loads, a subsequent bkg.set_data lands the game's tiles in the
                    # wrong VRAM and the font glyphs bleed across the whole
                    # background (every text+tilemap program -- the side-scroller sample,
                    # colors -- showed scattered glyphs). And here text init runs
                    # EAGERLY (gbs_sms_clear_bkg, before any bkg.set_data), which is
                    # exactly the order that triggers it. So load the font once: it
                    # sits at tiles 0..95; a game tileset loaded over it wins for the
                    # tiles it overwrites (the GB collision the throwaways guarded
                    # against is far rarer here -- it needs a >~32-tile tileset AND
                    # text sharing the low glyph range).
                    self.emit("        font_set(font_load(font_ibm));")
                else:
                    self.emit("        font_load(font_ibm);  /* pad: reserve bkg tiles 0..95 */")
                    self.emit("        font_load(font_min);   /* pad: reserve bkg tiles 96..138 */")
                    self.emit("        font_set(font_load(font_ibm));  /* active font -> tiles 139..234 */")
                self.emit("        gbs_font_ready = 1;")
                self.emit("    }")
                self.emit("}")
                self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s) { gbs_text_init(); gotoxy(x, y); printf(\"%s\", s); }")
                # %u, not %d: n is u16 and SDCC's %d is signed 16-bit, so %d
                # would print 40000 as -25536 (every other console prints
                # unsigned via the digit loop / utoa).
                self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n) { gbs_text_init(); gotoxy(x, y); printf(\"%u\", n); }")
                self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {")
                self.emit("    uint8_t i, j;")
                self.emit("    gbs_text_init();")
                self.emit("    for (j = 0; j < h; j++) { gotoxy(x, y + j); for (i = 0; i < w; i++) printf(\" \"); }")
                self.emit("}")
                if self.plot_tile_used:
                    # A raw tile write bypasses the printf console entirely --
                    # set_bkg_tile_xy exists on every GBDK console (incl. NES).
                    self.emit("/* Raw tile plot at a text cell (the custom-frame seam). */")
                    if self.platform == 'sms':
                        self.emit("void gbs_plot_tile(uint8_t x, uint8_t y, uint8_t t) {")
                        self.emit("    if (y >= 28) return;  /* rows >= 28 overrun the name table into the SAT */")
                        self.emit("    set_bkg_tile_xy((uint8_t)(x & 31), y, t);")
                        self.emit("}")
                    else:
                        self.emit("void gbs_plot_tile(uint8_t x, uint8_t y, uint8_t t) { set_bkg_tile_xy((uint8_t)(x & 31), y, t); }")
                # NES only here (an SMS font-swap program plots tiles, above):
                # the NES draws glyphs from CHR, so a font swap is a graceful
                # no-op (the default font shows).
                if self.font_set_used:
                    self.emit("void gbs_set_font(const uint8_t *data) { (void)data; }")
                if self.font_at_used:
                    self.emit("void gbs_set_font_at(uint8_t base, const uint8_t *data) { (void)base; (void)data; }")
                # text.glyph_buffer on the NES: its glyphs come out of CHR, not
                # from background tile DATA the program can write, so there is no
                # band to rasterize into and nothing to hand back to the tileset
                # -- a graceful no-op, like the font swap above. (This branch is
                # NES-only once glyph_buffer is called: an SMS program using it
                # takes the tile-plotting branch.)
                if self.glyph_buffer_used:
                    self.emit("void gbs_text_glyph_buffer(uint8_t base, uint8_t count) { (void)base; (void)count; }")
            # to_window/to_bkg on a non-window GBDK console: a no-op, so the
            # shared vm.core UI code links. The GB family emitted the real
            # overlay funcs in the has_window branch above, and the SMS/GG
            # name-table path emits the SCREEN-SPACE ones
            # (_emit_smsgg_ui_space) - which, since calling `to_window` now
            # selects that path on the SMS too, leaves this the NES only.
            if (self.text_window_used and not self.caps['has_window']
                    and not self._smsgg_plot_path):
                self.emit("void gbs_text_to_window(uint8_t origin_row, uint8_t box_rows) { (void)origin_row; (void)box_rows; }")
                self.emit("void gbs_text_win_reveal(void) { }")
                self.emit("void gbs_text_to_bkg(void) { }")
                self.emit("uint8_t gbs_text_window_active(void) { return 0; }")
            if self.win_cut_used and not self.caps['has_window']:
                # No GB-style window layer here, so nothing draws over it.
                self.emit("void gbs_text_win_cut(uint8_t on) { (void)on; }")
            if self.overlay_cut_used and not self.caps['has_window']:
                # ...and no window overlay to cut short either. The SMS, Game
                # Gear and NES draw their UI into the name table itself, which
                # no scanline interrupt can put away, so this is an honest
                # no-op and the importer says so on the report.
                self.emit("void gbs_text_win_overlay_cut(uint8_t y) { (void)y; }")
