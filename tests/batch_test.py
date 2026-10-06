#!/usr/bin/env python3
"""THE BATCH SPRITE VERBS: `sprite.plot`, `sprite.drift`, `sprite.hit_box`,
`sprite.hit`.

A shooter keeps pools - bullets, shots, swarms - and does the same three
things to every entry every frame: move it, draw it, test it. These verbs do
a whole pool in one native loop over plain byte arrays.

Pinned here:
  * they compile on every console: assembly on the Game Boy family and on
    SMS / Game Gear, a portable C loop on the cc65 consoles;
  * on the assembly consoles the verbs are MACROS that store their arguments
    straight into statics - as a C function the hand-over cost more than the
    loop it was calling - and the macros reach every translation unit, so a
    module in a ROM bank can use them;
  * the assembly takes no arguments in registers and treats a count of 0 as
    "nothing to do";
  * the Game Gear adds its screen offset and parks what the sprite table
    would take for its end marker;
  * a program that never calls them carries none of it;
  * `input.raw` is prototyped in banked translation units (a call without one
    read the pad out of the wrong register).

The end-to-end proof is a ROM: `projects/batch-lab/verify.py`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


_APP = '''
module "app" {
    import "graphics.sprite"
    import "platform.video"
    var xs: array[u8, 8]
    var ys: array[u8, 8]
    var vx: array[u8, 8]
    var r: u8
    function main() {
        loop {
            sprite.drift(xs, vx, 8)
            sprite.plot(0, 8, xs, ys, 1)
            sprite.plot(8, 4, xs, ys, 2)
            sprite.hit_box(10, 10, 16, 16)
            r = sprite.hit(xs, ys, 8)
            video.wait_vblank()
        }
    }
    export main
}
'''

_PLAIN = '''
module "app" {
    import "graphics.sprite"
    import "platform.video"
    function main() {
        loop { video.wait_vblank() }
    }
    export main
}
'''


def test_gb_family():
    print("\n[GB family: assembly behind macros]")
    for p in ("gameboy", "gameboy_color"):
        c = MosaikCompiler().compile(_APP, platform=p)
        check("#define gbs_spr_plot(first, n, xs, ys, cols)" in c
              and "#define gbs_spr_drift(pos, vel, n)" in c
              and "#define gbs_spr_hit(xs, ys, n)" in c
              and "#define gbs_spr_hit_box(x, y, w, h)" in c,
              "%s: the four verbs are macros" % p)
        check("gbs_bt_d = (uint8_t *)&shadow_OAM[first]" in c,
              "%s: plot writes the shadow sprite table directly" % p)
        check("void gbs_spr_plot(" not in c and "void gbs_spr_drift(" not in c,
              "%s: no C wrapper function is left between the call and the loop" % p)
        for name in ("plot1", "plot2", "drift", "hit"):
            i = c.index("void gbs_bt_%s(void) NAKED {" % name)
            body = c[i:c.index("__endasm", i)]
            check("ld a, (#_gbs_bt_n)" in body and "ret z" in body
                  and body.index("ret z") < body.index("push bc"),
                  "%s: gbs_bt_%s returns at once for a count of 0" % (p, name))
        i = c.index("void gbs_bt_hit(void) NAKED {")
        body = c[i:c.index("__endasm", i)]
        check(body.index("ld a, #255") < body.index("ret z"),
              "%s: ... and an empty scan answers 255" % p)
        i = c.index("void gbs_bt_plot1(void) NAKED {")
        body = c[i:c.index("__endasm", i)]
        check("add a, #16" in body and "add a, #8" in body,
              "%s: plot adds the sprite origin (8, 16)" % p)
        check("static void gbs_bt_" not in c,
              "%s: the routines are visible to banked translation units" % p)


def test_sms_gg():
    print("\n[SMS / Game Gear: assembly behind macros]")
    for p, offx, offy in (("gamegear", 48, 23), ("sms", 0, 255)):
        c = MosaikCompiler().compile(_APP, platform=p)
        check("#define gbs_spr_plot(first, n, xs, ys, cols)" in c
              and "gbs_bt_e = (uint8_t *)shadow_OAM + 0x40" in c,
              "%s: plot is a macro over the split Y / (X, tile) sprite table" % p)
        i = c.index("void gbs_bt_plot1(void) NAKED {")
        body = c[i:c.index("__endasm", i)]
        check("add a, #%d " % offy in body, "%s: the vertical screen offset is %d" % (p, offy))
        check(("add a, #%d " % offx in body) == (offx != 0),
              "%s: the horizontal screen offset is %d" % (p, offx))
        check("cp #0xD0" in body and "ld a, #0xC0" in body,
              "%s: a Y the table would read as its end marker is parked instead" % p)
        check("exx" in c[c.index("void gbs_bt_hit(void) NAKED {"):],
              "%s: the scan keeps its box in the alternate registers" % p)


def test_cc65():
    print("\n[cc65 consoles: the portable loops]")
    for p in ("pce", "lynx"):
        c = MosaikCompiler().compile(_APP, platform=p)
        check("void gbs_spr_plot(uint8_t first, uint8_t n, const uint8_t *xs," in c
              and "uint8_t gbs_spr_hit(const uint8_t *xs, const uint8_t *ys, uint8_t n)" in c,
              "%s: plain C functions" % p)
        check("__asm" not in c[c.index("batch sprite verbs"):c.index("uint8_t gbs_spr_hit(")],
              "%s: no assembly" % p)
        check(c.index("void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y);")
              < c.index("gbs_move_sprite(first++, x, y)"),
              "%s: the sprite mover is declared before the loop that calls it" % p)


def test_by_use():
    print("\n[nothing is emitted for a program that does not call them]")
    for p in ("gameboy", "gameboy_color", "gamegear", "sms", "pce", "lynx"):
        c = MosaikCompiler().compile(_PLAIN, platform=p)
        check("gbs_bt_" not in c and "gbs_spr_plot" not in c, "%s: no batch code" % p)


def test_banked_input_raw():
    print("\n[input.raw in a banked translation unit]")
    from mosaik.codegen import gbdk
    src = open(gbdk.__file__, encoding="utf-8").read()
    i = src.index('self.emit("uint8_t gbs_input_pressed(uint8_t button);")')
    near = src[i:i + 500]
    check("if self.input_raw_used:" in near and 'self.emit("uint8_t gbs_input_raw(void);")' in near,
          "the shared prototype block declares gbs_input_raw when the program uses it")


def main():
    test_gb_family()
    test_sms_gg()
    test_cc65()
    test_by_use()
    test_banked_input_raw()
    if _FAILED:
        print("\n%d check(s) FAILED:" % len(_FAILED))
        for f in _FAILED:
            print("   - " + f)
        return 1
    print("\nALL BATCH-SPRITE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
