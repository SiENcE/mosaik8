#!/usr/bin/env python3
"""The PC Engine keeps sprites off an open text box (`VM_BOX_CUT`).

Its box is drawn into the screen like the SMS/GG one and every sprite is in
front of it, so an actor standing in the box's band covered the text
(measured on projects/vm-shardlings' START menu, 2026-10-19: the strolling
kid over the menu). `text.win_sprite_cut` is a no-op on the cc65 consoles
(there is no window layer), so the PCE now takes the SMS/GG road: vm.core
calls `sprite.cut_y(row * 8)` when a box or menu opens and `255` when it
closes, and the PCE's one placement leaf (`gbs_move_sprite`) puts every object
whose top is at or below the cut off screen.

Opt-in like the knob it serves: the build states `VM_BOX_CUT` only off a
`core.set_box_cut(1)` line (rooms.mos emits it for `[scenes]
box_hides_sprites`), so every other PCE build is byte-identical.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik import MosaikCompiler  # noqa: E402

FAILS = []


def check(cond, msg):
    print("  [%s] %s" % ("ok" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def test_build_flag():
    import mosaik8_build as mb
    check(mb._wants_box_cut([("rooms.mos", "        core.set_box_cut(1)")]),
          "the build states VM_BOX_CUT off rooms.mos' core.set_box_cut(1)")
    check(not mb._wants_box_cut([("rooms.mos", "    -- core.set_box_cut(1)")])
          and not mb._wants_box_cut([("rooms.mos", "core.set_box_cut(0)")])
          and not mb._wants_box_cut([("rooms.mos", "core.set_box(draw_frame)")]),
          "...and only off it (a comment, a 0, set_box do not count)")


def test_core_sites():
    core = open(os.path.join(ROOT, "lib", "vm", "core.mos"), encoding="utf-8").read()
    smsgg = re.findall(r'if platform == "sms" or platform == "gamegear" \{\s*\n\s*ui_sprite_cut\(([^)]*\)?)\)',
                       core)
    pce = re.findall(r'if platform == "pce" \{\s*\n\s*if VM_BOX_CUT \{\s*\n\s*ui_sprite_cut\(([^)]*\)?)\)',
                     core)
    check(len(smsgg) >= 6, "vm.core has its SMS/GG cut sites (%d)" % len(smsgg))
    check(sorted(smsgg) == sorted(pce),
          "every SMS/GG cut site has a PCE twin with the same row, under VM_BOX_CUT")
    m = re.search(r'if platform == "pce" \{\s*\n\s*if VM_BOX_CUT \{\s*\n\s*local function '
                  r'ui_sprite_cut\(y: u8\) \{\s*\n\s*sprite\.cut_y\(y\)', core)
    check(m is not None, "the PCE defines ui_sprite_cut (sprite.cut_y) under VM_BOX_CUT")


PROG = '''
module "main" {
    import "graphics.sprite"
    function main() {
        sprite.cut_y(96)
        sprite.move(0, 40, 120)
    }
}
'''
PLAIN = '''
module "main" {
    import "graphics.sprite"
    function main() {
        sprite.move(0, 40, 120)
    }
}
'''


def _c(src, platform):
    c = MosaikCompiler().compile_program([("main.mos", src)], platform=platform)
    assert not c.startswith("Compilation error"), c
    return c


def test_pce_codegen():
    c = _c(PROG, "pce")
    leaf = c.split("void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y) {", 1)[1].split("\n}\n", 1)[0]
    check("void gbs_spr_cut_set(uint8_t y) { gbs_spr_cut_y = y; }" in c,
          "PCE: sprite.cut_y sets one threshold (255 = off)")
    check(re.search(r"if \(y >= gbs_spr_cut_y\)[^\n]*\n\s*gbs_satb\[\(uint16_t\)nb \* 4\] = 0;\s*\n\s*else",
                    leaf) is not None,
          "PCE: the placement leaf puts an object at/below the cut off screen")
    plain = _c(PLAIN, "pce")
    check("gbs_spr_cut" not in plain, "PCE: no cut_y call, no cut code (byte-identical)")
    lynx = _c(PROG, "lynx")
    check("void gbs_spr_cut_set(uint8_t y) { (void)y; }" in lynx and "gbs_spr_cut_y" not in lynx,
          "Lynx: the verb links as a no-op")


def main():
    print("pce_box_cut_test")
    test_build_flag()
    test_core_sites()
    test_pce_codegen()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
