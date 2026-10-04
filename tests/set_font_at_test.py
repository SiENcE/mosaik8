#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""text.set_font_at(base, data): the font swap at a caller-chosen tile base.

The SMS/GG background pattern table is FLAT (no GB-style addressing blocks:
one run of tiles shared by the tileset and the console font), and GBDK's
console font loads low (tiles 0..95) -- exactly where a game tileset grows
up from 0. So a LARGE tileset (bigworld-paint) overwrites the font and the
labels render as tileset garbage. Relocating the font inside the shared
gbs_set_font was
tried and reverted (it regressed ui-quest's small-tileset SMS render), so the
relocation is an OPT-IN verb: set_font_at(96, FONT_TILES) uploads the 96
glyphs at tile `base` and repoints gbs_font_base. (On SMS/GG the background
only addresses tiles 0..191 -- the GB-compat name table lives at VRAM 0x1800
= tiles 192..247 and the SAT at 0x1F00 = 248..255 -- so base + 96 must stay
<= 192: base 96 over a <= 96-tile tileset is the full-range layout.)

Because printf only draws the console font at its own base, an SMS program
that swaps the font (set_font OR set_font_at) switches its text helpers from
gotoxy()+printf() to the Game Gear's set_bkg_tile_xy tile-plotting path (which
reads gbs_font_base, so it follows a relocation; the SMS 32-col screen = the
full name table, so plotting never clips). A no-swap SMS program keeps the
printf path BYTE-IDENTICAL (pinned at the time by the golden snapshots of colors and the side-scroller
sample).

Elsewhere: the GB family + Game Gear honor the base too (their plotters read
gbs_font_base); the NES and the cc65 consoles (Lynx/PCE) no-op it, exactly
like set_font.
"""

from mosaik import MosaikCompiler

FONT_AT = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    const FONT: array[u8, 16] = [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    function main() {
        video.enable_lcd()
        text.set_font_at(96, FONT)
        text.print_string(5, 5, "HELLO")
        loop { video.wait_vblank() }
    }
    export main
}
'''

FONT_SET = FONT_AT.replace("text.set_font_at(96, FONT)", "text.set_font(FONT)")
NO_SWAP = FONT_AT.replace("        text.set_font_at(96, FONT)\n", "")


def compile_for(src, platform):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("text.set_font_at (caller-chosen font tile base)")
    print("=" * 50)
    ok = True

    AT_BODY = ("void gbs_set_font_at(uint8_t base, const uint8_t *data) "
               "{ gbs_text_init(); set_bkg_data(base, 96, data); gbs_font_base = base; }")
    PLOT = "set_bkg_tile_xy((uint8_t)((x + i) & 31)"
    PRINTF = 'gotoxy(x, y); printf("%s", s)'

    # SMS with set_font_at: tile-plotting text + the relocating helper.
    sms_at = compile_for(FONT_AT, "sms")
    ok &= check("sms(set_font_at): emits the relocating gbs_set_font_at",
                AT_BODY in sms_at)
    ok &= check("sms(set_font_at): text switches to tile plotting",
                PLOT in sms_at and PRINTF not in sms_at)
    ok &= check("sms(set_font_at): font base read from the font handle",
                "((pmfont_handle)fh)->first_tile" in sms_at)
    ok &= check("sms(set_font_at): plain gbs_set_font not emitted (unused)",
                "void gbs_set_font(" not in sms_at)

    # SMS with plain set_font: same tile-plotting unification (the swapped
    # glyphs live at the console font's base, but the plotters must exist so
    # a swap and a relocation share one text path); no set_font_at helper.
    sms_set = compile_for(FONT_SET, "sms")
    ok &= check("sms(set_font): text switches to tile plotting too",
                PLOT in sms_set and PRINTF not in sms_set)
    ok &= check("sms(set_font): overwrites the font at its own base",
                "set_bkg_data(gbs_font_base, 96, data)" in sms_set)
    ok &= check("sms(set_font): gbs_set_font_at not emitted (unused)",
                "gbs_set_font_at" not in sms_set)

    # SMS without any swap: the printf path, untouched (byte-identical class).
    sms_plain = compile_for(NO_SWAP, "sms")
    ok &= check("sms(no swap): keeps gotoxy()+printf() text path",
                PRINTF in sms_plain and PLOT not in sms_plain)
    ok &= check("sms(no swap): no font-base variable, no swap helpers",
                "gbs_font_base" not in sms_plain
                and "gbs_set_font" not in sms_plain)

    # Game Gear honors the base on its existing tile-plotting path.
    gg = compile_for(FONT_AT, "gamegear")
    ok &= check("gamegear: emits the relocating gbs_set_font_at",
                AT_BODY in gg)

    # GB family: honored too (the probed plotters read gbs_font_base).
    gb = compile_for(FONT_AT, "gameboy")
    ok &= check("gameboy: emits the relocating gbs_set_font_at",
                AT_BODY in gb)
    ok &= check("gameboy: keeps the probed font-base init",
                "gbs_font_base = get_bkg_tile_xy(0, 0)" in gb)

    # NES / Lynx: graceful no-ops, like set_font.
    nes = compile_for(FONT_AT, "nes")
    ok &= check("nes: gbs_set_font_at is a no-op",
                "void gbs_set_font_at(uint8_t base, const uint8_t *data) "
                "{ (void)base; (void)data; }" in nes)
    lynx = compile_for(FONT_AT, "lynx")
    ok &= check("lynx: gbs_set_font_at is a no-op",
                "void gbs_set_font_at(uint8_t base, const uint8_t *data) "
                "{ (void)base; (void)data; }" in lynx)

    print("=" * 50)
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
