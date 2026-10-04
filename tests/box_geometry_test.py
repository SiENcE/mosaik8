#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""The DIALOGUE BOX's geometry (reference-engine parity, 2026-08-07).

Three defects, all reported by playing the converted reference-engine sample and all
fixed against the reference engine's OWN numbers, read from its generated scripts:

    2-line text: VM_OVERLAY_CLEAR 0,0,20,4 + VM_OVERLAY_MOVE_TO 0,14
    3-line text: VM_OVERLAY_CLEAR 0,0,20,5 + VM_OVERLAY_MOVE_TO 0,13

i.e. the box is `lines + 2` rows tall, BOTTOM-anchored, spans the FULL 20-col
width, and its text starts one cell in (its `ui_dest_base` is the window origin
+ 32 + 1 = row 1, col 1).

1. HEIGHT. Ours was a fixed 4 rows with render_text printing at rows 15/16/17,
   so a 3-LINE text had nowhere to put its bottom border and overwrote it.
   Core now sizes the box from `text_lines(id)` BEFORE drawing the frame.
2. WIDTH. Ours drew at col 1 with SCREEN_COLS-2, leaving a column of scene
   showing down each side.
3. SPRITES. OBJ draws ABOVE the window on GB hardware, so an actor standing low
   in the room shows through an open box. The reference engine cuts them with an LCD (LYC)
   interrupt at the window's first scanline and restores them each VBL.

The pairing is the subtle part: the frame is drawn by CORE and the text by the
GENERATED render_text, so both now read the same core-owned origin
(box_top/box_left) instead of each computing rows. A shell that never registers
the seams keeps the historical fixed box, which is why an un-regenerated
project cannot end up with a resized frame and un-moved text.

Measured on the ROM (the reference-engine sample conversion, PyBoy): a 2-line box lands at rows 14..17 and
a 3-line box at 13..17 (WY 112 -> 104), both full width with text at col 1 and
both borders intact; with the cut on, LYC is armed at the line before the box.
"""

from mosaik import MosaikCompiler
from mosaik_vm import Compiler

CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "lib", "vm", "core.mos")


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("dialogue box geometry (reference-engine parity)")
    print("=" * 50)
    ok = True
    core = open(CORE, encoding="utf-8").read()

    # -- core owns the geometry, and it is the reference engine's ------------------------
    ok &= check("core sizes the box as lines + 2, bottom-anchored",
                "box_h = n + 2" in core
                and "return SCREEN_ROWS - box_h" in core)
    ok &= check("core exposes the text origin (box_top / box_left)",
                "function box_top() -> u8" in core
                and "function box_left() -> u8" in core
                and "return box_col + 1" in core)
    ok &= check("the frame is drawn at the DYNAMIC geometry, not constants",
                "g_box_draw(box_col, box_row(), box_w, box_h)" in core
                and "g_box_draw(BOX_COL, BOX_ROW" not in core)
    ok &= check("both knobs default to the historical inset box",
                "var box_col: u8 = BOX_COL" in core
                and "var box_w: u8 = SCREEN_COLS - 2" in core
                and "var box_h: u8 = BOX_H" in core)
    ok &= check("the sprite cut is scoped to the BOX (opt-in), not to to_window",
                "text.win_sprite_cut(1)" in core and "var box_cut: u8" in core)

    # -- the generated render_text FOLLOWS core rather than baking rows -------
    prog = Compiler().compile([{"name": "main", "events": [
        {"event": "text", "string": "one"},
        {"event": "text", "string": "two\nlines"},
        {"event": "text", "string": "three\nwhole\nlines"}]}])
    mos = prog.to_scripts_mos()
    ok &= check("render_text prints at core.box_left() / core.box_top()",
                "text.print_string(core.box_left(), core.box_top()," in mos
                and "core.box_top() + 1" in mos
                and "SCREEN_ROWS - 3" not in mos)
    ok &= check("text_lines reports 1 / 2 / 3 for the three strings",
                "const STR_LINES: array[u8, 3] = [" in mos
                and "1, 2, 3" in mos.replace("\n", " "))

    # -- the win_sprite_cut verb: real on the GB family, no-op elsewhere ------
    CUT = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    function main() {
        video.enable_lcd()
        text.to_window(14, 4)
        text.win_sprite_cut(1)
        text.print_string(1, 15, "HI")
        text.win_sprite_cut(0)
        loop { video.wait_vblank() }
    }
    export main
}
'''
    gb = MosaikCompiler().compile_program([("m.mos", CUT.strip())],
                                          platform="gameboy")
    ok &= check("gameboy: an LYC interrupt hides sprites at the box's first line",
                "LYC_REG = WY_REG ? (uint8_t)(WY_REG - 1) : 0;" in gb
                and "STAT_REG |= STATF_LYC;" in gb
                and "HIDE_SPRITES;" in gb
                and "add_LCD(gbs_win_lcd_isr)" in gb)
    ok &= check("gameboy: the VBL restore honours the program's own show/hide",
                "if (gbs_spr_want) SHOW_SPRITES;" in gb
                and "void gbs_show_sprites(void) { gbs_spr_want = 1; SHOW_SPRITES; }" in gb)
    for plat in ("sms", "nes", "lynx", "pce"):
        c = MosaikCompiler().compile_program([("m.mos", CUT.strip())],
                                             platform=plat)
        ok &= check(f"{plat}: win_sprite_cut is a graceful no-op",
                    "void gbs_text_win_cut(uint8_t on) { (void)on; }" in c)

    # -- unused = byte-identical: no cut code, no sprite-state tracking -------
    PLAIN = CUT.replace("        text.win_sprite_cut(1)\n", "").replace(
        "        text.win_sprite_cut(0)\n", "")
    off = MosaikCompiler().compile_program([("m.mos", PLAIN.strip())],
                                            platform="gameboy")
    ok &= check("unused: no ISR, no LYC, no gbs_spr_want (byte-identical)",
                "gbs_win_lcd_isr" not in off and "LYC_REG" not in off
                and "void gbs_show_sprites(void) { SHOW_SPRITES; }" in off)

    print("=" * 50)
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
