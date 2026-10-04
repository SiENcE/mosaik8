#!/usr/bin/env python3
"""THE PER-SCANLINE SCROLL TABLE (`bkg.raster*`) and `system.cpu_fast`.

`bkg.parallax*` splits a room into three scroll bands. A pseudo-3D road, a
ripple or a "mode 7" floor needs a different scroll on every line: a table
with one entry per screen line that an interrupt plays back.

Pinned here:
  * the four verbs compile on every console - a raw STAT handler on the GB
    family, a V-counter walk inside one line interrupt on SMS/GG, honest no-op
    stubs elsewhere - and a program that never calls them is byte-identical;
  * the GB handler is on the VECTOR (GBDK's chained dispatcher costs more than
    the handler), writes both scroll registers before it does anything else,
    and v-blank masks the STAT source over its own STAT write (a monochrome
    Game Boy raises a spurious interrupt there otherwise);
  * it cannot be combined with the other users of that vector;
  * the SMS/GG handler stands down while GBDK is inside a VRAM transfer;
  * `bkg.move`'s v-blank commit stands down while the table is armed;
  * the native FILLS (`raster_curve`, `raster_stripes`) and `raster_get`:
    assembly on both CPUs, called through parameterless routines so they do
    not depend on which registers this sdcc passes arguments in;
  * `system.cpu_fast` is real on the Game Boy Color only;
  * `bkg.set_data_native` uploads SMS/GG tiles without the run-time
    conversion, and is plain `set_bkg_data` elsewhere.

The end-to-end proof is a ROM: `projects/raster-lab/verify.py` reads the scroll
of every scanline off a rendered frame.
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
    import "graphics.bkg"
    import "platform.video"
    import "platform.system"
    var tab: array[u8, 16]
    function main() {
        system.cpu_fast(1)
        bkg.set_data_native(0, 1, tab)
        bkg.raster_set(0, 3, 0)
        bkg.raster(1, 40)
        loop {
            bkg.raster_copy(40, 8, tab)
            bkg.raster_curve_start(0x3000, 0xFF80)
            bkg.raster_curve(143, 100, 2)
            bkg.raster_stripes(143, 100, tab, 0, 80)
            tab[0] = bkg.raster_get(100)
            bkg.raster_show()
            bkg.move(0, 0)
            video.wait_vblank()
        }
    }
    export main
}
'''

_PLAIN = '''
module "app" {
    import "graphics.bkg"
    import "platform.video"
    function main() {
        loop { video.wait_vblank() }
    }
    export main
}
'''

_CLASH = '''
module "app" {
    import "graphics.bkg"
    import "platform.video"
    function main() {
        bkg.parallax(2)
        bkg.raster(1, 40)
        loop { video.wait_vblank() }
    }
    export main
}
'''


def test_gb_family():
    print("\n[GB family: a raw STAT handler]")
    for p in ("gameboy", "gameboy_color"):
        c = MosaikCompiler().compile(_APP, platform=p)
        check("ISR_VECTOR(VECTOR_STAT, gbs_rs_isr)" in c and "add_LCD" not in c,
              "%s: the handler is on the vector, not GBDK's chained dispatcher" % p)
        check("#include <gb/isr.h>" in c, "%s: the vector macro's header" % p)
        i = c.index("void gbs_rs_isr(void)")
        body = c[i:c.index("__endasm", i)]
        check(body.index("_SCX_REG") < body.index("_SCY_REG")
              < body.index("_gbs_rs_ptr), a"),
              "%s: both scroll writes come before the cursor is stored" % p)
        check("reti" in body and "NAKED" in body, "%s: naked, returns with reti" % p)
        v = c[c.index("void gbs_rs_vbl(void)"):c.index("void gbs_rs_set")]
        check(v.index("IE_REG &= 0xFDu") < v.index("STAT_REG = 0x40")
              < v.index("IF_REG &= 0xFDu") < v.index("IE_REG |= LCD_IFLAG"),
              "%s: v-blank masks the STAT source over its own STAT write" % p)
        check("LYC_REG = gbs_rs_first - 1;" in v,
              "%s: `first` starts the frame on the LYC source, a line early" % p)
        check("if (!gbs_rs_on) {\n        SCX_REG = gbs_scr_shx;" in c,
              "%s: bkg.move's v-blank commit stands down while armed" % p)


def test_sms_gg():
    print("\n[SMS / Game Gear: one line interrupt, a V-counter walk]")
    for p, top in (("sms", 0), ("gamegear", 24)):
        c = MosaikCompiler().compile(_APP, platform=p)
        check("#define GBS_RS_TOP %d " % top in c,
              "%s: screen line 0 is VDP line %d" % (p, top))
        i = c.index("void gbs_rs_isr(void)")
        body = c[i:c.index("__endasm", i)]
        check(body.index("__shadow_OAM_OFF") < body.index("out (c), a"),
              "%s: stands down while GBDK is inside a VRAM transfer" % p)
        check("ld a, #0x88" in body and "in a, (#0x7E)" in body,
              "%s: writes VDP register 8 as the V counter ticks" % p)
        check("add_LCD(gbs_rs_isr);" in c and "VDP_R10" in c,
              "%s: armed through the line counter" % p)
        check("gbs_rs_live[gbs_rs_first - 1] = gbs_rs_live[0];" in c,
              "%s: the entry above `first` carries line 0's scroll" % p)
        check("if (!gbs_rs_on)\n    move_bkg(gbs_scr_shx, gbs_scr_shy);" in c,
              "%s: bkg.move's v-blank commit stands down while armed" % p)


def test_fills():
    print("\n[the native fills]")
    for p in ("gameboy", "gameboy_color"):
        c = MosaikCompiler().compile(_APP, platform=p)
        i = c.index("static void gbs_rs_curve_asm(void)")
        body = c[i:c.index("__endasm", i)]
        check("push bc" in body and "pop bc" in body and "dec (hl)" in body,
              "%s: raster_curve is a naked loop that preserves BC" % p)
        check(body.count("dec hl") == 2,
              "%s: it walks UP the table two bytes (x, y) at a time" % p)
        i = c.index("static void gbs_rs_stripes_asm(void)")
        body = c[i:c.index("__endasm", i)]
        check("rla" in body, "%s: raster_stripes tests bit 7 of depth + phase" % p)
        check("+ ((uint16_t)line << 1) + 1;" in c,
              "%s: ... and writes the Y half of each entry" % p)
        check("return gbs_rs_buf[gbs_rs_back][(uint16_t)line << 1];" in c,
              "%s: raster_get reads the buffer being written" % p)
        check("set_bkg_data(first, count, data);" in c,
              "%s: set_data_native is the plain upload" % p)
    for p in ("sms", "gamegear"):
        c = MosaikCompiler().compile(_APP, platform=p)
        i = c.index("static void gbs_rs_curve_asm(void)")
        body = c[i:c.index("__endasm", i)]
        check("neg" in body and body.count("dec hl") == 1,
              "%s: raster_curve stores the negated x, one byte a line" % p)
        check("(void)line; (void)n; (void)depth; (void)phase; (void)y;" in c,
              "%s: raster_stripes is a no-op (no per-line vertical scroll)" % p)
        check("return (uint8_t)(0 - gbs_rs_buf[gbs_rs_back][line]);" in c,
              "%s: raster_get undoes the negation" % p)
        check("set_bkg_native_data(first, count, data);" in c,
              "%s: set_data_native skips the packed-nibble conversion" % p)


def test_stubs_and_gating():
    print("\n[stubs and gating]")
    for p in ("nes", "lynx", "pce"):
        c = MosaikCompiler().compile(_APP, platform=p)
        check("void gbs_rs_show(void) { }" in c and "gbs_rs_isr" not in c
              and "uint8_t gbs_rs_get(uint8_t line) { (void)line; return 0; }" in c,
              "%s: honest no-op stubs" % p)
        check("void gbs_cpu_fast(uint8_t on) { (void)on; }" in c,
              "%s: cpu_fast is a no-op" % p)
    for p in ("gameboy", "gameboy_color", "sms", "gamegear", "nes", "lynx", "pce"):
        c = MosaikCompiler().compile(_PLAIN, platform=p)
        check("gbs_rs_" not in c and "gbs_cpu_fast" not in c
              and "gbs_bkg_data_native" not in c,
              "%s: nothing is emitted for a program that does not call them" % p)
    c = MosaikCompiler().compile(_APP, platform="gameboy_color")
    check("if (on) cpu_fast(); else cpu_slow();" in c and "_cpu != CGB_TYPE" in c,
          "gameboy_color: cpu_fast is real, and guarded on the hardware")
    c = MosaikCompiler().compile(_APP, platform="gameboy")
    check("void gbs_cpu_fast(uint8_t on) { (void)on; }" in c,
          "gameboy: cpu_fast is a no-op")


def test_vector_is_exclusive():
    print("\n[the STAT vector has one owner]")
    for p in ("gameboy", "gameboy_color"):
        c = MosaikCompiler().compile(_CLASH, platform=p)
        check(c.startswith("Compilation error")
              and "owns the STAT interrupt vector" in c,
              "%s: raster + parallax is a clear compile error" % p)
    c = MosaikCompiler().compile(_CLASH, platform="sms")
    check("gbs_rs_isr" in c and not c.startswith("Compilation error"),
          "sms: the same source builds (parallax is a no-op there)")

if __name__ == "__main__":
    test_gb_family()
    test_sms_gg()
    test_fills()
    test_stubs_and_gating()
    test_vector_is_exclusive()
    if _FAILED:
        print("\n%d check(s) FAILED" % len(_FAILED))
        sys.exit(1)
    print("\nall checks passed")
