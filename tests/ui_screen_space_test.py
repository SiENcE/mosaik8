#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""UI text in SCREEN space on a console with no window layer (SMS / Game Gear).

`text.to_window` is how a dialogue box says "I am UI, not scene". On the GB
family that moves it onto the window map, which never scrolls and never touches
the tilemap. SMS/GG have no window layer, so the call was a NO-OP and the box
was plotted into the NAME TABLE at its authored cells - and a name-table cell is
a MAP cell. In a scrolling room the box therefore landed wherever the camera
happened to be (off-screen entirely once the level had scrolled a screenful) and
slid away with the level. Measured on the SMS/GG sample conversion, whose the reference engine
platform rooms column-stream.

The fix keeps the plotting where it is and moves the COORDINATES: while
to_window is live, every plot adds the hardware scroll (`shadow_VDP_RSCX` holds
-x and `shadow_VDP_RSCY` holds y, per sms/sms.h `move_bkg`), so the box holds a
fixed SCREEN position. Columns wrap at 32 and rows at 28, because the SMS name
table is 32x28.

Gated on the call, so a program that never routes text to a window keeps the
historical absolute plotting byte-identical - which is what every hand-written
SMS sample does.
"""

from mosaik import MosaikCompiler

UI = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    function main() {
        video.enable_lcd()
        text.glyph_buffer(0, 0)
        text.to_window(14, 4)
        text.print_string(1, 15, "HELLO")
        text.print_number(1, 16, 42)
        text.clear_area(0, 14, 20, 4)
        text.to_bkg()
        loop { video.wait_vblank() }
    }
    export main
}
'''

# The same program with no UI routing: the box IS the scene here, so the
# historical absolute plotting is correct and must not change.
NO_UI = (UI.replace('        text.to_window(14, 4)\n', '')
           .replace('        text.to_bkg()\n', ''))


def compile_for(src, platform):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("UI text in screen space (text.to_window without a window layer)")
    print("=" * 50)
    ok = True

    for plat in ("sms", "gamegear"):
        c = compile_for(UI, plat)
        ok &= check("%s: to_window arms the screen-space mode" % plat,
                    "static uint8_t gbs_text_ui = 0;" in c
                    and "void gbs_text_to_window(uint8_t origin_row, uint8_t box_rows) {" in c
                    and "gbs_text_ui = 1;" in c
                    and "void gbs_text_to_bkg(void) {" in c
                    and "gbs_text_ui = 0;" in c)
        # A box is built out of TILES, so its border ring can only sit on the
        # tile grid -- and the screen edge does not, once the camera is a few
        # pixels into a tile, which insets the border and shows a sliver of the
        # wrapped cell beyond it ("the box is one tile too far left"). The
        # scroll is snapped to the tile below while the UI is up and restored
        # exactly on close.
        ok &= check("%s: the scroll SNAPS to a tile while UI is up, and restores"
                    % plat,
                    "gbs_ui_scx = shadow_VDP_RSCX;" in c
                    and "move_bkg((uint8_t)(cx & 0xF8), (uint8_t)(cy & 0xF8));" in c
                    and "move_bkg((uint8_t)(0u - gbs_ui_scx), gbs_ui_scy);" in c)
        ok &= check("%s: the cell transform reads the hardware scroll" % plat,
                    "0u - shadow_VDP_RSCX" in c and "shadow_VDP_RSCY >> 3" in c)
        ok &= check("%s: columns wrap at 32, ROWS at 28 (the name table is 32x28)"
                    % plat,
                    "return (uint8_t)(x & 31);" in c
                    and "while (y >= 28) y -= 28;" in c)
        ok &= check("%s: every plotter routes through it" % plat,
                    c.count("gbs_ui_cx(") >= 4 and c.count("gbs_ui_cy(") >= 4)
        ok &= check("%s: window_active() still reports 0, so the caller repaints"
                    " the room on close" % plat,
                    "uint8_t gbs_text_window_active(void) { return 0; }" in c)
        # ... and the no-op definition must NOT also be emitted (a duplicate
        # definition would not compile).
        ok &= check("%s: the non-window NO-OP to_window is not emitted too" % plat,
                    "void gbs_text_to_bkg(void) { }" not in c)

        none = compile_for(NO_UI, plat)
        ok &= check("%s: byte-identical without to_window (no transform)" % plat,
                    "gbs_ui_cx" not in none and "gbs_text_ui" not in none)

    # The GB family keeps the real window overlay -- the screen-space transform
    # is for consoles that have no such layer and must not appear there.
    gb = compile_for(UI, "gameboy")
    ok &= check("gameboy: keeps the window map, no screen-space transform",
                "gbs_ui_cx" not in gb and "gbs_text_win = 1" in gb)

    # The NES plots into its name table but shows the whole map, and it has no
    # VDP scroll shadow to read -- it keeps the honest no-op.
    nes = compile_for(UI, "nes")
    ok &= check("nes: keeps the no-op to_window",
                "gbs_ui_cx" not in nes
                and "void gbs_text_to_bkg(void) { }" in nes)

    print("=" * 50)
    print("All checks passed" if ok else "Some tests failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
