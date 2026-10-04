#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""text.plot_tile(x, y, tile): ONE raw background tile id at a text cell,
routed through the TEXT layer.

The custom tile-frame seam. On the GB family the vm.core dialogue/menu boxes
ride the WINDOW overlay (text.to_window, the reference engine's model) -- a 9-slice frame
put down with bkg.set_tiles lands on the bkg map UNDERNEATH the opaque window
and is invisible. plot_tile follows the text router instead: the window map
while text is routed there (exactly how the reference engine's ui_draw_frame writes its
frame), the bkg map otherwise. On SMS/GG it is a name-table write with the SAT
row clamp; on NES a plain name-table write; on the cc65 consoles (Lynx/PCE --
text is drawn OFF the tile table) a graceful no-op, the set_font degradation
model (frame via text.fill_box there). Emitted only when CALLED, so every
non-user stays byte-identical (golden-pinned). Proof: projects/vm-boxtest
style 2 (the custom 9-slice frame) on gb/sms/gg.
"""

from mosaik import MosaikCompiler

# A window-routing program (the vm.core shape): plot_tile must follow to_window.
WIN = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    function main() {
        video.enable_lcd()
        text.to_window(14, 4)
        text.plot_tile(1, 14, 247)
        text.print_string(2, 15, "HI")
        text.to_bkg()
        loop { video.wait_vblank() }
    }
    export main
}
'''

# A plain program (no window routing): plot_tile is a bare bkg-map write.
PLAIN = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    function main() {
        video.enable_lcd()
        text.plot_tile(1, 14, 247)
        text.print_string(2, 15, "HI")
        loop { video.wait_vblank() }
    }
    export main
}
'''

NO_PLOT = PLAIN.replace("        text.plot_tile(1, 14, 247)\n", "")


def compile_for(src, platform):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("text.plot_tile (raw tile through the text-layer router)")
    print("=" * 50)
    ok = True

    # GB family + to_window: dual-branch router (window map when live).
    gb_win = compile_for(WIN, "gameboy")
    # One map byte as ONE byte (2026-09-07): the
    # 1x1 rectangle copier
    # cost ~1,200 T-cycles a cell and a 9-slice box frame is 80 of them.
    ok &= check("gameboy(+to_window): window-aware router",
                "void gbs_plot_tile(uint8_t x, uint8_t y, uint8_t t)" in gb_win
                and "if (gbs_text_win) set_vram_byte(get_win_xy_addr((uint8_t)(x & 31), "
                    "(uint8_t)((uint8_t)(y - gbs_win_row) & 31)), t);" in gb_win
                and "else set_vram_byte(get_bkg_xy_addr((uint8_t)(x & 31), (uint8_t)(y & 31)), t);" in gb_win)
    ok &= check("gameboy: the call lowers to gbs_plot_tile(...)",
                "gbs_plot_tile(1, 14, 247)" in gb_win)

    # GB family without to_window: the plain bkg one-liner (no window vars).
    gb_plain = compile_for(PLAIN, "gameboy")
    ok &= check("gameboy(plain): bare bkg-map write, no window branch",
                "void gbs_plot_tile(uint8_t x, uint8_t y, uint8_t t)" in gb_plain
                and "    set_bkg_tiles((uint8_t)(x & 31), (uint8_t)(y & 31), 1, 1, &t);" in gb_plain
                and "gbs_text_win" not in gb_plain)

    # Byte-identical when unused: the helper is NOT emitted without a call.
    gb_none = compile_for(NO_PLOT, "gameboy")
    ok &= check("gameboy: gbs_plot_tile NOT emitted when unused (byte-identical)",
                "gbs_plot_tile" not in gb_none)

    # SMS/GG: a name-table write with the SAT row clamp. PLAIN keeps the SMS on
    # its printf console path (no font swap), so this also covers that branch.
    for plat in ("sms", "gamegear"):
        out = compile_for(PLAIN, plat)
        ok &= check(f"{plat}: name-table write with the SAT clamp",
                    "void gbs_plot_tile(uint8_t x, uint8_t y, uint8_t t)" in out
                    and "if (y >= 28) return;" in out
                    and "set_bkg_tile_xy((uint8_t)(x & 31), y, t);" in out)

    # cc65 consoles: a graceful no-op (text is drawn off the tile table).
    for plat in ("lynx", "pce"):
        out = compile_for(PLAIN, plat)
        ok &= check(f"{plat}: graceful no-op",
                    "void gbs_plot_tile(uint8_t x, uint8_t y, uint8_t t) "
                    "{ (void)x; (void)y; (void)t; }" in out)

    print("=" * 50)
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
