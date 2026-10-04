#!/usr/bin/env python3
"""The CGB fade engine leaves the resident image when the build can bank it.

Review E-13: the reference-engine sample conversion linked for the Game Boy Color with **14 bytes** of
resident headroom, and the whole difference against the DMG build is the CGB
palette machinery - `gbs_pal_t3`, `gbs_pal_dim`, `gbs_pal_hw`, `gbs_pal_re`,
`gbs_pal_fade`. None of it runs per frame (the fade runs on a level CHANGE,
three times a transition; `gbs_pal_hw` on a palette LOAD), so it belongs in a
ROM bank. Measured on that project: resident **13 B spare -> 694 B**, and the
only visible price is that a room's ramp finishes 0-2 display frames later.

Two arms, and the test pins both, because getting the placement right is only
half of it - a prototype that disagrees with its definition about BANKED is a
wrong call through a trampoline that was never set up:

  * BANKING BUILD - the definitions live in a data-bank TU, `gbs_pal_hw` and
    `gbs_pal_fade` are BANKED there, the resident TU sees only prototypes, and
    the two private helpers (`gbs_pal_dim`, `gbs_pal_re`) plus the divide
    table do NOT appear resident at all.
  * NO BANKING - byte-for-byte the code that shipped before: everything
    resident, `gbs_pal_hw` static, no BANKED anywhere.

Also pinned: the state (`gbs_pal_sh` / `gbs_pal_msk` / `gbs_fade_lvl`) is ONE
copy wherever it lands - it is a shadow of what the program asked for, and a
second copy would fade against a different history.
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

ok = True


def check(cond, label, detail=""):
    global ok
    ok = ok and bool(cond)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- " + detail) if detail else ""))


PROG = '''
module "cold" {
    import "graphics.palette"
    function step(l: u8) {
        palette.fade(l)
    }
    export step
}

module "main" {
    import "platform.video"
    import "graphics.palette"
    import "cold"
    const PAL: array[u16, 8] = [0, 1, 2, 3, 4, 5, 6, 7]
    function main() {
        palette.load_bkg_set(0, 2, PAL, 0)
        palette.set_bkg(0, 1, 2, 3, 4)
        palette.fade(3)
        cold.step(0)
        video.wait_vblank()
    }
}
'''

# The DEFINITION of each, not its name: the resident TU still CALLS three of
# these, so a bare name search says "resident" for a function that is not.
DEFS = ("static const uint8_t gbs_pal_t3[64] = {",
        "static uint16_t gbs_pal_dim(uint16_t c) {",
        "static void gbs_pal_re(uint8_t spr, uint8_t s) {",
        "palette_color_t *buf) BANKED {",
        "void gbs_pal_fade(uint8_t level) BANKED {")
STATE = ("static palette_color_t gbs_pal_sh[64];",
         "static uint8_t gbs_pal_msk[2];",
         "static uint8_t gbs_fade_lvl;")


def gen(**kw):
    c = MosaikCompiler()
    out = c.compile_program([("m.mos", PROG)], platform="gameboy_color", **kw)
    assert not out.startswith("Compilation error"), out
    return out, c.code_generator


def test_banked():
    print("\n[a banking build: the engine is in a ROM bank]")
    # `code_banks` is the ordinary way a VM8 project turns banking on; any
    # bank(N) would do. What matters is that banking is ACTIVE.
    main, g = gen(code_banks=["cold"])
    banks = "\n".join(g.bank_units.values())
    check(bool(g.prelude_bank_defs.get("gbs_pal_fade")),
          "the fade engine is registered for a data bank")
    check("void gbs_pal_fade(uint8_t level) BANKED {" in banks
          and "void gbs_pal_hw(uint8_t spr, uint8_t slot, uint8_t count," in banks,
          "its two entry points are defined BANKED, in a bank TU")
    check("void gbs_pal_fade(uint8_t level) BANKED;" in main
          and "palette_color_t *buf) BANKED;" in main,
          "the resident TU sees BANKED prototypes")
    # The definitions must be GONE from the resident TU, not duplicated into
    # it: two copies of gbs_pal_sh is two fade histories.
    for d in DEFS:
        check(d in banks and d not in main,
              "defined in the bank, not resident: %s" % d.split("(")[0])
    for st in STATE:
        check(banks.count(st) == 1 and st not in main,
              "the shadow %s is one copy, in the bank" % st.split()[-1].rstrip(";"))
    check(banks.count("static const uint8_t gbs_pal_t3[64]") == 1,
          "the x/3 divide table is banked and single")
    # Everything that CALLS into it from the resident side still can: the two
    # setters, the palette-set path and the boot seed.
    check("gbs_pal_hw(0, slot, 1, buf);" in main
          and "gbs_pal_hw(spr, slot, count, gbs_pal_buf);" in main,
          "the resident setters and gbs_pal_set still route through it")
    check("gbs_cgb_default_palettes" in main and "gbs_pal_hw((uint8_t)" in main,
          "the boot seed still records its greys in the shadow")


def test_resident():
    print("\n[no banking: the pre-E-13 code, unchanged]")
    main, g = gen()
    check(not g.bank_units and not g.prelude_bank_defs,
          "nothing is banked in this build")
    check("BANKED" not in main.split("gbs_pal_fade")[0][-4000:],
          "no BANKED qualifier reaches the palette family")
    check("static void gbs_pal_hw(uint8_t spr, uint8_t slot, uint8_t count," in main,
          "gbs_pal_hw is static again (the original spelling)")
    check("void gbs_pal_fade(uint8_t level) {" in main,
          "gbs_pal_fade is defined resident")
    for d in ("static const uint8_t gbs_pal_t3[64] = {",
              "static uint16_t gbs_pal_dim(uint16_t c) {",
              "static void gbs_pal_re(uint8_t spr, uint8_t s) {"):
        check(d in main, "resident: %s" % d.split("(")[0])
    for st in STATE:
        check(main.count(st) == 1, "the shadow %s is one copy"
              % st.split()[-1].rstrip(";"))


def test_bank_tus_see_the_same_prototypes():
    """A bank TU calling `palette.fade` must call it the same way.

    This is the lockstep half: `_emit_prelude_gbdk_decls` hands every bank
    translation unit the prototypes, and if it hands out a NEAR one for a
    banked definition the call goes to the trampoline's address as if it were
    code. The `cold` module above exists to force such a TU."""
    print("\n[lockstep: a bank TU's view]")
    _main, g = gen(code_banks=["cold"])
    tus = [t for t in g.bank_units.values() if "cold_step" in t]
    check(bool(tus), "the cold module really did get its own bank TU")
    for t in tus:
        check("void gbs_pal_fade(uint8_t level) BANKED;" in t,
              "it declares gbs_pal_fade BANKED")
        check("gbs_pal_fade(l);" in t, "... and calls it")


def main():
    print("CGB fade: resident vs banked")
    print("=" * 50)
    test_banked()
    test_resident()
    test_bank_tus_see_the_same_prototypes()
    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
