#!/usr/bin/env python3
"""bkg.move on the GB register model is a SHADOW committed in v-blank.

A direct move_bkg from the game loop lands mid-frame (the loop reaches its
scroll write ~25-30k cycles after the present returns; v-blank is 4,560
cycles), which SHEARS the picture at that scanline - measured on the
converted town room as 76 of 300 walking frames torn at LY ~56-66
(a local probe). The fix is the parallax bands' own
double-buffer rule applied to the plain scroll path: gbs_scroll_move writes
a shadow, gbs_wait_vblank commits it right after vsync() returns - the start
of v-blank, where the reference engine's own loop writes its scroll.

Pins:
  * the lowering swap + the commit ordering (vsync first, then the commit);
  * the LCD-off immediate arm (a room load's write must reach the register
    before the first visible frame renders through it);
  * the parallax stand-down is on the COMMIT, not the shadow write;
  * gated on USE and on has_gb_regs: a program that never calls bkg.move,
    and every SMS/GG/NES/Lynx build, is byte-identical.
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

FAILS = []


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    if not cond:
        FAILS.append(label)


SCROLLER = """
module "main" {
    import "graphics.bkg"
    function main() {
        var x: u8 = 0
        loop {
            x += 1
            bkg.move(x, 0)
            video.wait_vblank()
        }
    }
}
"""

STATIC = """
module "main" {
    import "graphics.bkg"
    function main() {
        loop {
            video.wait_vblank()
        }
    }
}
"""


# Calls BOTH verbs, which is where the shadow needs a second writer routed
# through it. A free-running register-driven sample's shape: bkg.move once at room start, then
# bkg.scroll per frame, and wait_vblank at the bottom of the loop.
BOTH = """
module "main" {
    import "graphics.bkg"
    function main() {
        bkg.move(0, 0)
        loop {
            bkg.scroll(0, 1)
            video.wait_vblank()
        }
    }
}
"""

# bkg.scroll with NO bkg.move: nothing commits, so GBDK's direct scroll_bkg
# is still right and must stay byte-identical.
SCROLL_ONLY = """
module "main" {
    import "graphics.bkg"
    function main() {
        loop {
            bkg.scroll(0, 1)
            video.wait_vblank()
        }
    }
}
"""


def wait_vblank_body(c):
    return c.split("void gbs_wait_vblank(void) {")[1].split("\n}")[0]


def main():
    c = MosaikCompiler().compile(SCROLLER, platform="gameboy")
    check("bkg.move lowers onto gbs_scroll_move on the GB",
          "gbs_scroll_move(x, 0)" in c and "move_bkg(x, 0)" not in c)
    body = c.split("void gbs_scroll_move(uint8_t x, uint8_t y) {")[1].split("\n}")[0]
    check("the helper writes the shadow, not the register",
          "gbs_scr_shx = x;" in body and "gbs_scr_shy = y;" in body)
    check("...with the LCD-off immediate arm (room load / boot)",
          "if (!(LCDC_REG & LCDCF_ON))" in body and "SCX_REG = x;" in body)
    w = wait_vblank_body(c)
    check("gbs_wait_vblank commits the shadow",
          "SCX_REG = gbs_scr_shx;" in w and "SCY_REG = gbs_scr_shy;" in w)
    check("...AFTER vsync() returns (the start of v-blank)",
          "vsync();" in w and w.index("vsync();") < w.index("SCX_REG = gbs_scr_shx;"))
    check("...unconditionally in a program with no parallax",
          "gbs_px_n" not in w)
    check("the old gbs_px_move arbitration wrapper is gone",
          "gbs_px_move" not in c)

    # A program that never calls bkg.move carries none of it.
    c2 = MosaikCompiler().compile(STATIC, platform="gameboy")
    check("no bkg.move = no shadow, no commit (byte-identical shape)",
          "gbs_scroll_move" not in c2 and "gbs_scr_shx" not in c2)

    # The NES keeps the direct lowering (different scroll hardware).
    cp = MosaikCompiler().compile(SCROLLER, platform="nes")
    check("nes keeps the direct scroll write",
          "gbs_scroll_move" not in cp and "gbs_scr_shx" not in cp)
    # SMS/GG TAKE THE SHADOW TOO, for a second reason: their sprite table
    # reaches the hardware once per frame (sprite.vbl_hold releases the copy at
    # the present), so a scroll written the moment the streamer computes it is
    # a frame AHEAD of the sprites whenever a game frame spans more than one
    # display frame - every actor slides against the background while the
    # camera moves and snaps back when it stops (reported from play; measured
    # on the SMS/GG sample conversion as the sprite-to-background offset wandering over
    # three values while walking, and constant on 130/130 frames after).
    for p in ("sms", "gamegear"):
        cp = MosaikCompiler().compile(SCROLLER, platform=p)
        check("%s defers the scroll to the v-blank commit" % p,
              "gbs_scroll_move(x, 0)" in cp and "gbs_scr_shx = x;" in cp)
        w = wait_vblank_body(cp)
        check("...and %s commits it there, after vsync()" % p,
              "move_bkg(gbs_scr_shx, gbs_scr_shy);" in w
              and w.index("vsync();") < w.index("move_bkg(gbs_scr_shx"))
        # There is no LCD-off arm here: move_bkg is a plain VDP register write
        # and a room load ends in a present.
        body = cp.split("void gbs_scroll_move(uint8_t x, uint8_t y) {")[1]
        body = body.split(chr(10) + "}")[0]
        check("...with no LCD-off arm (%s has no LCDC)" % p,
              "LCDC_REG" not in body)
    # ...and the whole GB register class gets the shadow.
    cm = MosaikCompiler().compile(SCROLLER, platform="megaduck")
    check("megaduck (GB register model) gets the shadow",
          "gbs_scroll_move(x, 0)" in cm)

    # ---- bkg.scroll must ride the SAME shadow, or the commit wipes it ----
    # GBDK's scroll_bkg adds straight to SCX/SCY. Left alone, a program that
    # calls both verbs has TWO writers for one register and the commit wins:
    # every delta is overwritten by the next gbs_wait_vblank, which re-asserts
    # the shadow the last bkg.move left. Measured on
    # a program whose loop writes the scroll registers directly: SCY read 0 on every frame of a 900-frame
    # run where the original ROM sweeps 0..126, so nothing scrolled at all.
    cb = MosaikCompiler().compile(BOTH, platform="gameboy")
    check("bkg.scroll lowers onto the shadow when bkg.move is also used",
          "gbs_scroll_bkg(0, 1)" in cb)
    # Split defensively: without the fix the helper does not exist at all, and
    # a test that raises reports nothing about the checks below it.
    parts = cb.split("void gbs_scroll_bkg(int8_t dx, int8_t dy) {")
    dbody = parts[1].split(chr(10) + "}")[0] if len(parts) > 1 else ""
    check("...and ACCUMULATES into the shadow (never assigns the register)",
          "gbs_scr_shx += (uint8_t)dx;" in dbody
          and "gbs_scr_shy += (uint8_t)dy;" in dbody)
    check("...with the same LCD-off arm as gbs_scroll_move",
          "LCDC_REG" in dbody)

    # Gated on USE both ways: neither verb alone pays for the other.
    conly = MosaikCompiler().compile(SCROLL_ONLY, platform="gameboy")
    check("bkg.scroll WITHOUT bkg.move keeps GBDK's direct scroll_bkg",
          "gbs_scroll_bkg" not in conly and "scroll_bkg(0, 1)" in conly)
    check("...and emits no commit to fight it",
          "gbs_scr_shy" not in wait_vblank_body(conly))
    check("a bkg.move-only program emits no scroll delta helper (dead bytes)",
          "gbs_scroll_bkg" not in c)

    print("\n" + ("All checks passed" if not FAILS else
                  "SOME CHECKS FAILED: %s" % FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
