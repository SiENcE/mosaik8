#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""text.glyph_buffer(base, count): the GLYPH-BUFFER text mode (G16).

The problem it solves, measured on the reference-engine sample import: a scene with
191 background tiles plus a 96-tile resident console font needs 287 of the
256 available, so the scene art lands on the glyphs and text renders as
garbage -- and no arrangement of a resident font fixes it (moving the font
leaves 65 free tiles for a 96-glyph set).

The answer is the reference engine's: keep NO font in VRAM. The font is ROM data and each
character is rasterized on demand into a small reserved tile band, so glyphs
cost VRAM only for the distinct characters actually on screen. That ROM data is
the project's own font when it has one, else GBDK's font_ibm LINKED from the
console library (never a copy in our source; 946 B resident against the old
pasted 768 B table, +178 B, measured on GB and SMS 2026-09-26).

**The cache is keyed by GLYPH, not by screen CELL** (the reference engine indexes per
cell because its variable-width glyphs straddle tile boundaries; ours is
fixed-width, so one tile IS one character). Two consequences this test pins:
repeated letters share ONE tile, and re-printing the same text is a pure cache
HIT that returns the same tiles and writes no VRAM -- which is exactly what a
menu cursor move and a HUD value change do every time they redraw.

Slot 0 of the band is reserved for the SPACE glyph and never evicted, because
gbs_clear_area and the window-band clear blank a cell by plotting
gbs_font_base -- so the plotters need no glyph-mode fork.

Reach: real on the tile-plotting GBDK consoles (GB family + SMS/GG, where it
also retires the set_font_at relocation escape); a graceful no-op on the NES
(glyphs come from CHR, not from writable background tile data) and on
Lynx/PCE (TGI/conio text reserves no tiles from the scene, so there is
nothing to hand back). Opt-in and byte-identical off.

ROM-verified separately under PyBoy on a 200-tile-background program running
the real shell sequence (font first, then the room's tileset): the resident
path renders 8 of 13 cells as the wrong pixels, the glyph buffer renders
13 of 13, and the ROM is 1,107 B SMALLER because GBDK's font allocator and
printf console drop out of the link entirely.
"""

from mosaik import MosaikCompiler

GLYPH = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    function main() {
        video.enable_lcd()
        text.glyph_buffer(200, 48)
        text.print_string(5, 5, "HELLO")
        text.print_number(5, 6, 42)
        loop { video.wait_vblank() }
    }
    export main
}
'''

# The same program on the resident-font path -- the byte-identical-off case.
PLAIN = GLYPH.replace("        text.glyph_buffer(200, 48)\n", "")

GLYPH_FONT = GLYPH.replace(
    "        text.print_string(5, 5, \"HELLO\")",
    "        text.set_font(FONT)\n        text.print_string(5, 5, \"HELLO\")"
).replace(
    "    function main() {",
    "    const FONT: array[u8, 16] = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]\n"
    "    function main() {")


# A program that uploads `tiles` background tiles and asks for the DERIVED
# band (0, 0). The band must land above the art, wherever the art ends.
DERIVED = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    import "graphics.bkg"
    const T: array[u8, %d] = [0]
    function main() {
        video.enable_lcd()
        text.glyph_buffer(0, 0)
        bkg.set_data(0, %d, T)
        text.print_string(1, 1, "HI")
        loop { video.wait_vblank() }
    }
    export main
}
'''


def compile_for(src, platform):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def band_of(tiles, platform):
    """(base, count) of the derived band, or None when the build refuses."""
    c = compile_for(DERIVED % (tiles * 16, tiles), platform)
    if c.startswith('Compilation error'):
        return None
    vals = [int(l.split()[-1]) for l in c.splitlines()
            if l.startswith(('#define GBS_GLYPH_BASE',
                             '#define GBS_GLYPH_COUNT'))]
    return tuple(vals) if len(vals) == 2 else None


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("text.glyph_buffer (ROM font + on-demand glyph band)")
    print("=" * 50)
    ok = True

    RESOLVE = "gbs_text_row[n++] = gbs_glyph_tile((uint8_t)*s);"
    DIGIT = "gbs_text_row[k++] = gbs_glyph_tile((uint8_t)(48 + d));"
    FONT_LOAD = "font_load(font_ibm)"

    gb = compile_for(GLYPH, "gameboy")
    # No project font: the glyphs are GBDK's LINKED font_ibm (past its 2-byte
    # header and 128-byte encoding table), never a table in our source.
    LINKED = "static const uint8_t *gbs_glyph_src = font_ibm + 130;"
    ok &= check("gameboy: the rasterizer reads GBDK's linked font_ibm, no table",
                LINKED in gb and "gbs_glyph_font" not in gb)
    ok &= check("gameboy: uploads via GBDK's 1bpp expander, not raw 2bpp",
                "set_bkg_1bpp_data" in gb)
    ok &= check("gameboy: the plotters resolve a character through the cache",
                RESOLVE in gb and DIGIT in gb)
    ok &= check("gameboy: NO font is loaded into VRAM (the whole point)",
                FONT_LOAD not in gb)
    # Where the ROM saving comes from: with no font in VRAM there is no reason
    # to link GBDK's font allocator or its printf console at all. (Match real
    # CALLS -- the surviving comments mention gotoxy()/printf() by name.)
    ok &= check("gameboy: GBDK's font allocator + printf console drop out",
                "font_init();" not in gb and 'printf("' not in gb)
    ok &= check("gameboy: slot 0 is pinned to the space glyph",
                "gbs_glyph_slot[0] = 1;" in gb
                and "gbs_glyph_next = 1;" in gb)
    ok &= check("gameboy: the band is clamped to the top of the tile table",
                "#define GBS_GLYPH_TOP   256" in gb
                and "count = (uint8_t)(GBS_GLYPH_TOP - base);" in gb)
    ok &= check("gameboy: an unprintable character degrades to a space",
                "if (g > 95) g = 0;" in gb)

    # SMS/GG: the same renderer, and the band is capped at the 192-tile
    # background (the name table lives at VRAM 0x1800 = tiles 192..247).
    # This is what retires set_font_at as the large-tileset escape.
    for plat in ("sms", "gamegear"):
        c = compile_for(GLYPH, plat)
        ok &= check(f"{plat}: same renderer, band capped at the 192-tile bkg",
                    LINKED in c
                    and "#define GBS_GLYPH_TOP   192" in c)
        ok &= check(f"{plat}: takes the tile-plotting path (printf can't follow"
                    " a moved band)",
                    "set_bkg_tile_xy((uint8_t)((x + i) & 31)" in c
                    and 'gotoxy(x, y); printf("%s", s)' not in c)

    # A custom sheet becomes the ROM source the rasterizer reads, so a font
    # swap costs no VRAM at all and needs no 96-tile block to place.
    gbf = compile_for(GLYPH_FONT, "gameboy")
    ok &= check("gameboy: set_font repoints the rasterizer instead of uploading 96 tiles",
                "gbs_glyph_src = data;" in gbf
                and "set_bkg_data(gbs_font_base, 96, data)" not in gbf)

    # NES: glyphs come from CHR, so there is no band to rasterize into.
    nes = compile_for(GLYPH, "nes")
    ok &= check("nes: glyph_buffer is a graceful no-op, printf path kept",
                "void gbs_text_glyph_buffer(uint8_t base, uint8_t count) "
                "{ (void)base; (void)count; }" in nes
                and 'gotoxy(x, y); printf("%s", s)' in nes)

    # Lynx / PCE: text draws off the framebuffer/conio and reserves no tiles
    # from the scene, so there is nothing to reclaim.
    for plat in ("lynx", "pce"):
        c = compile_for(GLYPH, plat)
        ok &= check(f"{plat}: glyph_buffer is a graceful no-op",
                    "void gbs_text_glyph_buffer(uint8_t base, uint8_t count) "
                    "{ (void)base; (void)count; }" in c
                    and "gbs_glyph_font" not in c)

    # The band is DERIVED, not hand-picked: its base is one past the highest
    # background tile the program uploads, so a growing tileset moves it and an
    # author never carries the numbers (a generated shell passes 0, 0).
    ok &= check("gameboy: the band tracks the tileset (16/100/240 tiles)",
                band_of(16, "gameboy")[0] == 16
                and band_of(100, "gameboy")[0] == 100
                and band_of(240, "gameboy") == (240, 16))
    ok &= check("gameboy: no readable upload -> the top-of-table fallback",
                compile_for(GLYPH, "gameboy").count("#define GBS_GLYPH_BASE  208") == 1)

    # No room left is a LOUD refusal naming the numbers, never a silent
    # not-drawing-text. On SMS/GG the background is only 192 tiles, so a
    # 191-tile tileset genuinely cannot host a band -- and says so.
    ok &= check("gameboy: a 255-tile tileset is refused, not silently squeezed",
                band_of(255, "gameboy") is None)
    ok &= check("sms: 191 tiles refused (its background ends at 192)",
                band_of(191, "sms") is None and band_of(100, "sms")[0] == 100)

    # `[assets] font` -> compile_program(glyph_font=...): a custom 96-glyph
    # 1bpp table bakes into the prelude in PLACE of the built-in, so a custom
    # font costs nothing at runtime; unset keeps the built-in byte-identical.
    fake = [[0] * 8] + [[i & 0xFF] * 8 for i in range(1, 96)]
    gf = MosaikCompiler().compile_program(
        [("m.mos", GLYPH.strip())], platform="gameboy", glyph_font=fake)
    ok &= check("gameboy: glyph_font override replaces the linked console font",
                "The PROJECT'S OWN font" in gf
                and "0x21,0x21,0x21,0x21,0x21,0x21,0x21,0x21" in gf
                and "gbs_glyph_src = gbs_glyph_font;" in gf
                and "font_ibm + 130" not in gf)

    # --- VARIABLE-width sheets widen the table; fixed-width ones do NOT ----
    # The trigger is the marker colour, never the
    # cell count: gbs-mono.png carries all 224 cells too, so widening on size
    # would have grown all four reference-engine conversions by ~1 KB for glyphs none
    # of them can draw.
    ok &= check("mono font: the historical 96-glyph table, no widths",
                "gbs_glyph_font[96 * 8]" in gf
                and "gbs_glyph_slot[96]" in gf
                and "if (g > 95)" in gf
                and "gbs_glyph_widths" not in gf)

    vwf_rows = [[0] * 8] + [[i & 0xFF] * 8 for i in range(1, 224)]
    vwf_w = [4] + [(i % 8) + 1 for i in range(1, 224)]
    vf = MosaikCompiler().compile_program(
        [("m.mos", GLYPH.strip())], platform="gameboy",
        glyph_font=vwf_rows, glyph_widths=vwf_w)
    ok &= check("vwf font: the table widens to the sheet's full ASCII 32..255",
                "gbs_glyph_font[224 * 8]" in vf
                and "gbs_glyph_slot[224]" in vf
                and "if (g > 223)" in vf)
    ok &= check("vwf font: the advance widths are emitted beside the bitmaps",
                "gbs_glyph_widths[224]" in vf
                and "Advance width per glyph" in vf)
    # The two tables must agree on the count, or the renderer indexes one past
    # the end of the other -- the shape a silent wrong-glyph bug takes.
    import re as _re
    _fn = _re.search(r"gbs_glyph_font\[(\d+) \* 8\]", vf)
    _wn = _re.search(r"gbs_glyph_widths\[(\d+)\]", vf)
    ok &= check("vwf font: bitmap and width tables are the same length",
                bool(_fn and _wn) and _fn.group(1) == _wn.group(1))

    # --- the VARIABLE-WIDTH renderer (text.vwf_*), stage V4a ----------------
    VWF = GLYPH.replace("        text.glyph_buffer(200, 48)\n",
                        "        text.glyph_buffer(0, 0)\n"
                        "        text.vwf_start(1, 2)\n"
                        "        text.vwf_glyph(65)\n"
                        "        text.vwf_nl(1, 3)\n")
    vc = compile_for(VWF, "gameboy")
    ok &= check("vwf: the compositor is emitted when the verbs are used",
                "gbs_vwf_glyph" in vc and "gbs_vwf_start" in vc
                and "gbs_vwf_nl" in vc)
    # The compositor is derived from CrossZGB's vwf_print_render (MIT): its
    # full notice travels in the C of every program that emits it, and only
    # there (THIRD_PARTY_NOTICES.md).
    from mosaik.codegen.gbdk_text import CROSSZGB_NOTICE
    ok &= check("vwf: the C carries CrossZGB's full MIT notice",
                CROSSZGB_NOTICE in vc
                and "Copyright (c) 2016 - 2024 Gonzalo de Santos Garcia" in vc
                and "The above copyright notice and this permission notice" in vc)
    ok &= check("...and a program without variable-width text does not",
                "CrossZGB" not in compile_for(GLYPH, "gameboy"))

    # BOTH of these were real bugs caught by looking at a frame, not by any
    # codegen assertion -- each drew NOTHING and neither failed the build.
    #
    # 1. The band runs to the top of the tile table, so `base + count` is
    #    exactly 256 on the GB family and truncates to 0 in uint8_t: the room
    #    test then reads "no room" for every glyph. Measure the DISTANCE.
    ok &= check("vwf: ring room is a distance, never base + count (u8 wrap)",
                "(uint8_t)(gbs_vwf_tile - gbs_vwf_base) < gbs_vwf_count" in vc
                and "gbs_vwf_tile < (uint8_t)(gbs_vwf_base + gbs_vwf_count)"
                not in vc)
    # The PARTITION (V4b): the fixed-width glyph cache keeps the top
    # GBS_VWF_RESERVE tiles of the band and the ring gets the rest -- measured
    # un-partitioned, every menu row rendered identical to the dialogue box,
    # because the two allocators handed the same tiles to different text.
    ok &= check("vwf: the band is PARTITIONED between the ring and the cache",
                "#define GBS_VWF_RESERVE" in vc
                and "gbs_vwf_count = (uint8_t)(count - GBS_VWF_RESERVE);" in vc
                and "count = GBS_VWF_RESERVE;" in vc)
    # 2. The pen's INK-ON-PAPER pair is (3, 0), and each console's upload has
    #    to establish it its own way. On the GB family the pen expands its own
    #    1bpp stage into the 2bpp staging buffer - a set bit is colour 3, i.e.
    #    the stage byte written into BOTH planes - and uploads with
    #    set_bkg_data. That replaced set_bkg_1bpp_data, GBDK's general entry
    #    point, which walks the colour pair BIT by bit: measured 2026-09-07 on
    #    the DMG-palette check conversion, ~13,600 T-cycles a glyph against ~4,120
    #    for the reference ROM's plain 16-byte set_bkg_data, and dropping it
    #    took the room's box regime from 1.88 to 1.71 LCD frames per game
    #    frame with the rendered box PIXEL-IDENTICAL (whole-VRAM md5).
    vwf_body = vc.split("static void gbs_vwf_upload")[1].split(chr(10) + "}")[0]
    ok &= check("vwf/gb: the pen expands its own stage and uploads 2bpp",
                "gbs_vwf_expand((uint8_t)(n << 3));" in vwf_body
                and "set_bkg_data(gbs_vwf_tile, n, gbs_vwf_out)" in vwf_body
                and "set_bkg_1bpp_data" not in vwf_body)
    # 2026-09-07, the second pass on the pen: the per-glyph loops that
    # sdcc could not compile well are hand-written sm83 behind the C wrappers
    # - the shift (the reference's `ui_print_shift_char` shape, rotate + mask
    # in globals) and the 2bpp expansion - both OLDCALL NAKED so the stack
    # layout is the one thing a naked function can rely on; a map cell is
    # written as ONE byte (`set_vram_byte` on the LCDC-derived address, the
    # reference's `ui_set_tile`), and the in-progress cell is mapped once per
    # cell, not once per glyph.
    ok &= check("vwf/gb: the shift and the expansion are naked sm83 cores",
                "static void gbs_vwf_shift8(uint8_t *dest, const uint8_t *src) OLDCALL NAKED {" in vc
                and "static void gbs_vwf_expand(uint8_t n8) OLDCALL NAKED {" in vc
                and "ld a, (_gbs_vwf_rot)" in vc and "ld a, (_gbs_vwf_msk)" in vc
                and "gbs_vwf_rot = rotate; gbs_vwf_msk = mask;" in vc)
    put_body = vc.split("static void gbs_vwf_put")[1].split(chr(10) + "}")[0]
    # (This program never routes text to the window, so only the bkg arm is
    # emitted; the window arm is the same call on get_win_xy_addr.)
    ok &= check("vwf/gb: a map cell is one VRAM byte, never a 1x1 rectangle",
                "set_vram_byte(get_bkg_xy_addr(" in put_body
                and "set_win_tiles" not in put_body and "set_bkg_tiles" not in put_body)
    ok &= check("vwf/gb: the in-progress cell is mapped once per cell",
                "if (gbs_vwf_pen && !gbs_vwf_mapped && gbs_vwf_room()) {" in vc
                and "gbs_vwf_mapped = 0;          /* a new in-progress cell */" in vc)
    ok &= check("vwf: ring room is a macro, not a call sdcc will not inline",
                "#define gbs_vwf_room() " in vc and "static uint8_t gbs_vwf_room" not in vc)
    # ... and the now-dead colour-pair setter goes with it: only the SMS/GG
    # arm still expands through the global pair, and the glyph CACHE sets it
    # for itself (a VWF-only GB program never touches it).
    ok &= check("vwf/gb: the 1bpp colour pair is not set on a path that never expands",
                "set_1bpp_colors" not in
                vc.split("void gbs_vwf_start")[1].split("}")[0])
    # The GLYPH CACHE still expands through GBDK's 1bpp entry point, and still
    # sets the pair itself: it uploads once per NEW character and is not on the
    # per-frame path, so the general expander is the right call there.
    ok &= check("vwf: the glyph cache keeps GBDK's 1bpp expander + its own pair",
                "set_1bpp_colors(3, 0);" in vc
                and "set_bkg_1bpp_data((uint8_t)(gbs_glyph_base + s), 1," in vc)

    # The pen and the ring live in the RUNTIME, not in the caller: the caller
    # feeds characters and is told when a cell completed.
    ok &= check("vwf: the pen is engine state, not a caller argument",
                "uint8_t gbs_vwf_glyph(uint8_t ch)" in vc
                and "gbs_vwf_pen" in vc)
    # A partial cell is ABANDONED at a line break, never packed (the reference
    # does the same); every line starts at pen 0.
    ok &= check("vwf: a newline abandons the partial cell",
                "if (gbs_vwf_pen) { gbs_vwf_tile++; gbs_vwf_pen = 0; }" in vc)

    for plat in ("sms", "gamegear", "nes", "lynx", "pce"):
        c = compile_for(VWF, plat)
        ok &= check(f"{plat}: vwf verbs build (GB family first, honest elsewhere)",
                    "gbs_vwf" in c or "gbs_text_glyph_buffer" in c)

    # --- The console font is LINKED, never copied into our source ------------
    # (public-release review D4.) Every consumer indexes GBDK's font_ibm past
    # its header + encoding table and assumes ASCII 32 + i is tile i, so the
    # layout is pinned against the libraries GBDK actually ships (skipped when
    # no GBDK is installed; the codegen checks above still run).
    from mosaik.codegen import gbdk_font
    ok &= check("the engine source carries no font pixels (no pasted table)",
                not hasattr(gbdk_font, "GLYPH_FONT_1BPP")
                and gbdk_font.FONT_IBM_TILES_OFFSET == 130)
    import mosaik8_build
    home = mosaik8_build.GBDKInterface().find_gbdk()   # the one "is GBDK here"
    libs = {c: gbdk_font.gbdk_console_lib(home, c)
            for c in ("gb", "ap", "duck", "sms", "gg", "nes")} if home else {}
    if libs and all(os.path.isfile(p) for p in libs.values()):
        for console, path in libs.items():
            f = gbdk_font.read_font_ibm(path)
            ok &= check(f"{console}.lib: font_ibm is 128-encoded 1bpp, 102 tiles,"
                        " ASCII 32 + i -> tile i",
                        f is not None
                        and f[0] == gbdk_font.FONT_IBM_TYPE
                        and f[1] == gbdk_font.FONT_IBM_TILES
                        and len(f) == gbdk_font.FONT_IBM_TILES_OFFSET
                        + gbdk_font.FONT_IBM_TILES * 8
                        and all(f[2 + 32 + i] == i
                                for i in range(gbdk_font.FONT_IBM_GLYPHS)))
    else:
        print("  [SKIP] no GBDK install: the font_ibm layout is not re-read")

    # sprite.font_glyph(tile, ch): the HUD's sprite text, from the linked font.
    FG = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
    function main() {
        video.enable_lcd()
        sprite.font_glyph(10, 72)
        loop { video.wait_vblank() }
    }
    export main
}
'''
    for plat in ("gameboy", "sms", "gamegear", "nes"):
        c = compile_for(FG, plat)
        ok &= check(f"{plat}: font_glyph reads the linked font_ibm through set_sprite_data",
                    "#include <gbdk/font.h>" in c
                    and "src = font_ibm + 130 + (uint16_t)font_ibm[2 + ch] * 8;" in c
                    and "set_sprite_data(tile, 1, buf);" in c
                    and "gbs_sprite_font_glyph(10, 72)" in c)
    pce = compile_for(FG, "pce")
    ok &= check("pce: font_glyph reads cc65's linked pce_font",
                "extern unsigned char pce_font[];" in pce
                and "src = pce_font + (uint16_t)ch * 8;" in pce
                and "gbs_set_sprite_data(tile, 1, buf);" in pce)
    ok &= check("lynx: font_glyph is refused (no console font is linked there)",
                compile_for(FG, "lynx").startswith("Compilation error"))
    for plat in ("gameboy", "sms", "pce"):
        c = compile_for(PLAIN, plat)
        ok &= check(f"{plat}: font_glyph unused -> no helper, no font_ibm reference",
                    "gbs_sprite_font_glyph" not in c and "pce_font" not in c
                    and "font_ibm + " not in c)

    # Byte-identical off: a program that never calls the verb keeps the
    # resident-font path exactly as it was.
    for plat in ("gameboy", "gameboy_color", "sms", "gamegear", "nes",
                 "lynx", "pce"):
        c = compile_for(PLAIN, plat)
        ok &= check(f"{plat}: unused -> no glyph code at all",
                    "gbs_glyph" not in c and "GBS_GLYPH" not in c)

    print("=" * 50)
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
