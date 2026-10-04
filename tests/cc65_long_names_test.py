"""cc65 keeps only 64 characters of an identifier; the generated C must not
depend on more.

A reference-engine conversion names its script entry points after the reference-engine
script path, so `scripts_ENTRY_space_battle_enemies_ship_mine_top_to_bottom_on_update`
and `..._on_update_2` (68 and 70 characters) became ONE macro to cc65 and the
PC Engine build stopped at "Macro redefinition is not identical". The cc65
backend shortens a module-level C name longer than `CC65_NAME_MAX` (52, which
leaves room for the `__bimpl__bk` a banked function adds) to a prefix plus a
hash of the whole name. Pinned here:

  * on the cc65 consoles no generated identifier reaches 64 characters, and two
    names that differ only past 64 stay two names;
  * the GBDK consoles keep the long names verbatim (sdcc has no such limit, so
    their C is unchanged);
  * a short name is untouched everywhere.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik import MosaikCompiler  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


LONG_A = "ENTRY_space_battle_enemies_ship_mine_top_to_bottom_on_update"
LONG_B = LONG_A + "_2"

LIB = '''
module "scripts" {
    const %s: u16 = 111
    const %s: u16 = 222
    const SHORT: u16 = 333
    export %s, %s, SHORT
}
''' % (LONG_A, LONG_B, LONG_A, LONG_B)

MAIN = '''
module "main" {
    import "scripts"
    import "graphics.text"
    function main() {
        text.print_number(0, 0, scripts.%s)
        text.print_number(0, 1, scripts.%s)
        text.print_number(0, 2, scripts.SHORT)
    }
}
''' % (LONG_A, LONG_B)


def compile_c(platform):
    return MosaikCompiler().compile_program(
        [("scripts.mos", LIB), ("main.mos", MAIN)], platform=platform)


def test_cc65():
    print("cc65: no identifier reaches 64 characters")
    for plat in ("pce", "lynx"):
        c = compile_c(plat)
        check("%s: compiles" % plat, not c.startswith("Compilation error"), c[:300])
        long_ids = sorted(set(re.findall(r"\b[A-Za-z_]\w{63,}\b", c)))
        check("%s: no identifier of 64+ characters" % plat, not long_ids,
              str(long_ids[:3]))
        defs = re.findall(r"#define (scripts_ENTRY\w*) \((\d+)\)", c)
        names = {n for n, _v in defs}
        check("%s: the two long consts stay two distinct names (%d)"
              % (plat, len(names)), len(names) == 2
              and sorted(v for _n, v in defs) == ["111", "222"], str(defs))
        check("%s: a short name is untouched" % plat,
              "scripts_SHORT" in c)


def test_gbdk_unchanged():
    print("GBDK: long names verbatim")
    c = compile_c("gameboy")
    check("gameboy: both long names are emitted as written",
          ("scripts_" + LONG_A) in c and ("scripts_" + LONG_B) in c)


if __name__ == "__main__":
    test_cc65()
    test_gbdk_unchanged()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
