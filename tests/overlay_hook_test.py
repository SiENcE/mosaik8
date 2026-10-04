#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""video.set_overlay(cb): the Lynx PRESENT-time UI overlay hook.

On the double-buffered Lynx the present recomposites the frame (bkg strips +
sprites) whenever anything moved -- including the strip ring's amortized
rebuild after a room paint -- and the freshly-blitted page SCANS OUT before any
post-present drawing lands on it. So a dialogue/menu drawn only after present
strobed or vanished over a moving background (vm-showcase's title menu over a
scroll_bg). The hook is called INSIDE gbs_present, after the blits and before
the flip, with the text helpers forced single-page (gbs_spr_db = 0), so the
open UI is part of EVERY composed frame. vm.core registers its draw_open_ui
here (Lynx only, via the NEEDS_PRESENT_REDRAW profile const). A graceful no-op
on the PCE (persistent BAT) and the GBDK consoles (persistent tilemap / the GB
window overlay). Emitted only when CALLED (byte-identical off).
"""

from mosaik import MosaikCompiler

PROG = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "graphics.text"
    function draw_ui() {
        text.print_string(2, 10, "HI")
    }
    function main() {
        video.enable_lcd()
        video.set_overlay(draw_ui)
        video.show_sprites()
        text.print_string(0, 0, "X")
        loop { video.wait_vblank() }
    }
    export main
}
'''

NO_OVERLAY = PROG.replace("        video.set_overlay(draw_ui)\n", "")


def compile_for(src, platform):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("video.set_overlay (the Lynx present-time UI overlay hook)")
    print("=" * 50)
    ok = True

    lynx = compile_for(PROG, "lynx")
    ok &= check("lynx: registration + callback slot emitted",
                "static void (*gbs_overlay_cb)(void) = 0;" in lynx
                and "void gbs_set_overlay(void (*cb)(void)) { gbs_overlay_cb = cb; }" in lynx)
    ok &= check("lynx: the hook runs inside gbs_present, single-page",
                "if (gbs_overlay_cb) {" in lynx
                and "uint8_t db = gbs_spr_db; gbs_spr_db = 0;" in lynx
                and "gbs_overlay_cb();" in lynx)
    ok &= check("lynx: the call lowers to gbs_set_overlay(...)",
                "gbs_set_overlay(main_draw_ui)" in lynx or "gbs_set_overlay(draw_ui)" in lynx)

    lynx_none = compile_for(NO_OVERLAY, "lynx")
    ok &= check("lynx: NOT emitted when unused (byte-identical)",
                "gbs_overlay_cb" not in lynx_none and "gbs_set_overlay" not in lynx_none)

    pce = compile_for(PROG, "pce")
    ok &= check("pce: graceful no-op (persistent BAT)",
                "void gbs_set_overlay(void (*cb)(void)) { (void)cb; }" in pce
                and "gbs_overlay_cb" not in pce)

    gb = compile_for(PROG, "gameboy")
    ok &= check("gameboy: graceful no-op (persistent tilemap / window overlay)",
                "void gbs_set_overlay(void (*cb)(void)) { (void)cb; }" in gb
                and "gbs_overlay_cb" not in gb)

    print("=" * 50)
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
