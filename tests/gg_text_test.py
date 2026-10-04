#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Game Gear text plots glyphs into the 32-col name table (not printf).

The GG VDP name table is 32 cols (like the SMS), but the LCD shows only the
CENTRE 20x18. GBDK's printf wraps its cursor at DEVICE_SCREEN_WIDTH (= 20), so a
camera-scrolled dialogue/shop box (col = camx/8 + n) had its tail past col 19
wrapped to col 0 of the next row -- the box drifted + tore on the Game Gear while
the SMS (32 visible cols = the full name table) rendered fine. (Confirmed in the
genesis_plus_gx core: a scrolled box split; the SMS one did not.)

Fix (codegen/gbdk.py): the GG plots each glyph with set_bkg_tile_xy(x & 31, y, t)
-- exactly the GB-family fix -- so the hardware scroll wraps the cells on-screen
and nothing clips. It can't probe gbs_font_base via get_bkg_tile_xy (absent on the
z80 port), so it reads the font's first_tile from its handle instead. SMS and NES
keep the printf path (32-col screens never clip) and stay byte-identical.
"""

from mosaik import MosaikCompiler

TEXT = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    function main() {
        video.enable_lcd()
        text.print_string(5, 5, "HELLO")
        text.print_number(5, 6, 42)
        text.clear_area(2, 2, 4, 4)
        loop { video.wait_vblank() }
    }
    export main
}
'''


def compile_for(platform):
    return MosaikCompiler().compile(TEXT.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("Game Gear text -> 32-col name table (not printf)")
    print("=" * 50)
    ok = True

    gg = compile_for("gamegear")
    # The three text helpers plot tiles, never printf/gotoxy.
    ok &= check("gamegear: print_string plots via set_bkg_tile_xy",
                "set_bkg_tile_xy((uint8_t)((x + i) & 31)" in gg)
    ok &= check("gamegear: text helpers issue no printf/gotoxy calls",
                'printf("%s", s)' not in gg and "gotoxy(x, y)" not in gg)
    # The glyph base comes from the font handle's first_tile (no probe available).
    ok &= check("gamegear: font base read from the font handle (first_tile)",
                "((pmfont_handle)fh)->first_tile" in gg)
    ok &= check("gamegear: still loads the font once (no throwaway pads)",
                gg.count("font_load(") == 1 and "font_load(font_min)" not in gg)

    # SMS keeps the printf path (32 visible cols never clip) -- byte-identical.
    sms = compile_for("sms")
    ok &= check("sms: keeps gotoxy()+printf() text path",
                'gotoxy(x, y); printf("%s", s)' in sms)
    ok &= check("sms: does NOT plot glyphs with set_bkg_tile_xy in text",
                "set_bkg_tile_xy((uint8_t)((x + i) & 31)" not in sms)

    # NES likewise keeps printf (32-col screen, lazy-init padded font path).
    nes = compile_for("nes")
    ok &= check("nes: keeps gotoxy()+printf() text path",
                'gotoxy(x, y); printf("%s", s)' in nes)

    # The Game Boy family is unaffected (its own probed set_bkg_tiles path).
    gb = compile_for("gameboy")
    ok &= check("gameboy: unchanged (probes gbs_font_base via get_bkg_tile_xy)",
                "gbs_font_base = get_bkg_tile_xy(0, 0)" in gb
                and "((pmfont_handle)fh)->first_tile" not in gb)

    print("=" * 50)
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
