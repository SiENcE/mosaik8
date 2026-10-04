#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""text.fill_box(c, r, w, h): a filled, bordered box drawn as a real framebuffer
OVERLAY on the cc65 pixel consoles.

The character-cell +--+ frame (engine.box) is crude on the Lynx and locked to the
8px text grid. text.fill_box instead uses the TGI primitives -- an opaque paper
bar (tgi_bar) + a crisp 1px ink border (four tgi_line) -- so the box is
pixel-precise, opaque over a frozen scrolled background, and nicer looking. It is
drawn to BOTH double-buffer pages (the same freeze idiom as the text helpers), so
it survives the Lynx page flip while a box is open.

The big fill bar is drawn to the DISPLAYED page FIRST (pass 0 -> gbs_draw_page ^
1): the caller presents just before fill_box (the box-freeze idiom), so drawing
the shown page right after that vblank gives Suzy the whole frame to lay the bar
before the beam reaches it. Drawing the shown page LAST (the plain both-pages
order) tears the big bar and the frozen background bleeds through. Callers must
present-then-draw and redraw only on change (wait_vblank -> fill_box+text, idle
with system.delay) -- else the box reaches only one flip buffer and does not show
until the next flip (a button press).

It is cc65-only (the GBDK consoles are tile-based, no framebuffer; engine.box
keeps its tile/ASCII frame there and stays byte-identical). On the PCE conio
backend (no pixel primitives) it degrades to a cleared cell box with an ASCII
+--+ border (a plain borderless clear was INVISIBLE on the PCE's black
background -- a game framing its dialogue through fill_box showed no frame at
all). Emitted only when actually CALLED, so a program that never uses it is
byte-identical (golden-pinned). On the Lynx,
engine.box.draw_box now routes here too (the TGI font has no '|' glyph, so the
character frame garbled its verticals).
"""

from mosaik import MosaikCompiler

BOX = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    function main() {
        video.enable_lcd()
        text.fill_box(2, 3, 16, 6)
        text.print_string(4, 4, "SHOP")
        loop { video.wait_vblank() }
    }
    export main
}
'''

NO_BOX = BOX.replace("        text.fill_box(2, 3, 16, 6)\n", "")


def compile_for(src, platform):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("text.fill_box (TGI overlay box on the cc65 pixel consoles)")
    print("=" * 50)
    ok = True

    lynx = compile_for(BOX, "lynx")
    ok &= check("lynx: emits gbs_fill_box",
                "void gbs_fill_box(uint8_t c, uint8_t r, uint8_t w, uint8_t h)" in lynx)
    ok &= check("lynx: opaque fill via tgi_bar + a 1px border via 4 tgi_line",
                "tgi_bar(x0, y0, x1, y1);" in lynx
                and lynx.count("tgi_line(x0, y0, x1, y0)") == 1
                and "tgi_line(x1, y0, x1, y1)" in lynx)
    ok &= check("lynx: draws to BOTH double-buffer pages, DISPLAYED page first",
                "if (!gbs_spr_db || pass) break;" in lynx
                and "tgi_setdrawpage(pass ? gbs_draw_page : (gbs_draw_page ^ 1));"
                in lynx)
    ok &= check("lynx: the call lowers to gbs_fill_box(...)",
                "gbs_fill_box(2, 3, 16, 6)" in lynx)

    # Byte-identical when unused: the helper is NOT emitted without a call.
    lynx_none = compile_for(NO_BOX, "lynx")
    ok &= check("lynx: gbs_fill_box NOT emitted when unused (byte-identical)",
                "gbs_fill_box" not in lynx_none)

    # PCE conio fallback: a cleared cell box with an ASCII +--+ border (the old
    # borderless clear was invisible on the PCE's black background).
    pce = compile_for(BOX, "pce")
    ok &= check("pce: gbs_fill_box is a cleared cell box with an ASCII border",
                "void gbs_fill_box(uint8_t c, uint8_t r, uint8_t w, uint8_t h)" in pce
                and "cclearxy(c, r + j, w);" in pce
                and "cputcxy(c, r, '+');" in pce
                and "cputcxy(c + i, r, '-');" in pce
                and "cputcxy(c, r + j, '|');" in pce
                and "tgi_bar" not in pce)

    # cc65-only: calling it on a GBDK console is a clear "not supported on
    # target" compile error (emitted in-band, so the C build fails loudly), NOT
    # a silent lowering to text_fill_box(...).
    try:
        gb = compile_for(BOX, "gameboy")
    except Exception as e:
        gb = str(e)
    ok &= check("gameboy: text.fill_box is rejected (cc65-only verb)",
                "not supported on target 'gameboy'" in gb
                and "text.fill_box" in gb and "text_fill_box(" not in gb)

    print("=" * 50)
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
