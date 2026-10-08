"""A cc65 build keeps only the prelude helpers something uses.

The cc65 prelude defined its general helpers (`gbs_delay`, `gbs_print_string`,
`gbs_clear_area`, `gbs_hw_read`, ...) in every program, and ld65 links a
translation unit whole, so a Lynx program paid for them called or not - and
for the cc65 library modules only they referenced. `cc65_prune` cuts the
definition of every `gbs_` function nothing names, to a fixed point
(measured on the Lynx shooter: 12 helpers, MAIN spare 93 -> ~2,450 B).
Pinned here:

  * the pass on a synthetic unit: an unused helper goes, a called one stays,
    a chain goes as a whole, a name in a string (inline assembly), in a
    preprocessor line or in an assembly unit keeps its helper, a comment does
    not, a prototype stays, a definition sharing its line is left alone;
  * on the Lynx and the PC Engine a program that never calls `system.delay`
    has no `gbs_delay` and one that does keeps it; `text.print_number` keeps
    the `gbs_print_string` it calls;
  * the GBDK consoles are untouched (the pass is cc65-only).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik import MosaikCompiler  # noqa: E402
from mosaik.codegen.cc65_prune import prune_unused_helpers  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


UNIT = r'''
#define GBS_MACRO_USES gbs_macro_named
void gbs_proto_only(uint8_t x);
/* gbs_comment_only is mentioned here, which is not a use */
void gbs_comment_only(void) { }
void gbs_unused(uint8_t x) {
    if (x) { gbs_unused(x - 1); }
}
void gbs_leaf(void) { }
void gbs_chain_top(void) {
    gbs_chain_mid();
}
static void gbs_chain_mid(void) { gbs_leaf_of_chain(); }
static uint8_t
gbs_split_line(void) { return 0; }
void gbs_leaf_of_chain(void) { }
void gbs_asm_named(void) { }
void gbs_unit_named(void) { }
void gbs_macro_named(void) { }
void gbs_called(void) { gbs_leaf(); }
uint8_t gbs_proto_only(uint8_t x) { return x; }
int main(void) {
    gbs_called();
    __asm__ ("jsr _gbs_asm_named");
    return 0;
}
'''


def test_unit():
    print("the pass on a synthetic unit")
    out, removed = prune_unused_helpers(UNIT, ["\tjsr _gbs_unit_named\n"])
    gone = ["gbs_unused", "gbs_chain_top", "gbs_chain_mid", "gbs_leaf_of_chain",
            "gbs_comment_only", "gbs_proto_only"]
    kept = ["gbs_called", "gbs_leaf", "gbs_asm_named", "gbs_unit_named",
            "gbs_macro_named"]
    check("the unused, the chain, the self-recursive and the comment-only go",
          removed == sorted(gone), str(removed))
    for name in kept:
        check("%s keeps its definition" % name, "void %s(void) {" % name in out)
    check("the definition of a prototype-only helper goes, its prototype stays",
          "uint8_t gbs_proto_only(uint8_t x) {" not in out
          and "void gbs_proto_only(uint8_t x);" in out)
    check("a definition whose type is on the line before is left alone",
          "gbs_split_line(void) { return 0; }" in out)
    check("main survives whole", "int main(void) {\n    gbs_called();" in out)
    again, removed2 = prune_unused_helpers(out, ["\tjsr _gbs_unit_named\n"])
    check("a second pass finds nothing (fixed point)", again == out and not removed2)


DELAY = '''
module "main" {
    import "platform.system"
    function main() {
        system.delay(100)
    }
}
'''
PLAIN = '''
module "main" {
    import "graphics.text"
    function main() {
        text.print_number(0, 0, 7)
    }
}
'''


def compile_c(src, platform):
    return MosaikCompiler().compile_program([("main.mos", src)], platform=platform)


def test_consoles():
    print("cc65 consoles: a helper is emitted only when used")
    for plat in ("lynx", "pce"):
        d = compile_c(DELAY, plat)
        p = compile_c(PLAIN, plat)
        check("%s: both compile" % plat,
              not d.startswith("Compilation error") and not p.startswith("Compilation error"),
              (d if d.startswith("Compilation") else p)[:300])
        check("%s: system.delay keeps gbs_delay" % plat, "void gbs_delay(uint16_t ms) {" in d)
        check("%s: no delay call, no gbs_delay" % plat, "void gbs_delay(" not in p)
        # The Lynx's print_number formats and calls print_string; the PCE's
        # writes through conio itself, so its print_string goes too.
        check("%s: print_number stays, print_string iff it calls it" % plat,
              "void gbs_print_number(" in p
              and ("void gbs_print_string(" in p) == (plat == "lynx"))
        check("%s: no print call, no gbs_print_string" % plat,
              "void gbs_print_string(" not in d)
        check("%s: gbs_hw_read is gone from both" % plat,
              "gbs_hw_read(uint16_t" not in d and "gbs_hw_read(uint16_t" not in p)


def test_gbdk_untouched():
    print("GBDK: not pruned")
    c = compile_c(PLAIN, "gameboy")
    check("gameboy: compiles", not c.startswith("Compilation error"), c[:300])
    check("gameboy: the generator records no cc65 pruning",
          "gbs_print_number" in c)


if __name__ == "__main__":
    test_unit()
    test_consoles()
    test_gbdk_untouched()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
