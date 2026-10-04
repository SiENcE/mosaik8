#!/usr/bin/env python3
"""Compound bitwise assignment: `|=`, `&=`, `^=` (added 2026-08-26).

The natural completion of the existing `+= -=` pair, added for the flag-
packing readers the R2 frame-budget work introduced (`actor.anim_in`,
`player.anim_flags`). The rules this pins:

* all three lex, parse, typecheck and lower to the same C operator (C has
  them natively, so no expansion is needed);
* an assignment target is still validated (a literal on the left is a parse
  error, same as `=`);
* a condition still refuses them (`if x |= 1` is the `if x = 1` typo class);
* programs that do not use them are untouched (the token is new).
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik.compiler import MosaikCompiler  # noqa: E402


def compile_program(sources, platform):
    return MosaikCompiler().compile_program(sources, platform=platform)

FAILS = []


def check(label, cond):
    print(("  [ok] " if cond else "  FAIL: ") + label)
    if not cond:
        FAILS.append(label)
    return cond


PROG = '''
module "main" {
    var flags: u8
    var wide: u16
    var tab: array[u8, 4]

    function main() {
        flags |= 0x10
        flags &= 0x7F
        flags ^= 3
        wide |= flags << 4
        tab[1] |= 1
        while flags > 0 {
            flags &= flags - 1      -- classic popcount step
        }
    }
}
'''


def test_compiles_and_lowers():
    print("[compile + lowering]")
    c = compile_program([("main.mos", PROG)], platform="gameboy")
    check("the program compiles", bool(c))
    check("|= lowers to C's own operator", "|=" in c)
    check("&= lowers to C's own operator", "&=" in c)
    check("^= lowers to C's own operator", "^=" in c)
    check("an array element is a legal target", "main_tab[1] |= 1" in c
          or "tab[1] |= 1" in c)


def _parses(src):
    """True if the PARSER accepts `src` (the compiler driver reports parse
    errors as diagnostics rather than raising, so rejection is asserted at
    the parser, where the error is born)."""
    from mosaik.lexer import Lexer
    from mosaik.parser import Parser
    try:
        Parser(Lexer(src).tokenize(), platform="gameboy").parse()
        return True
    except SyntaxError:
        return False


def test_rejections():
    print("[rejections]")
    check("a literal target is a parse error",
          not _parses('module "main" { function main() { 5 |= 1 } }'))
    check("a condition refuses |= (the `if x = 1` typo class)",
          not _parses('module "main" { var x: u8\n'
                      '    function main() { if x |= 1 { x = 0 } } }'))


def main():
    print("compound bitwise assignment (|= &= ^=)")
    print("=" * 50)
    test_compiles_and_lowers()
    test_rejections()
    print()
    if FAILS:
        print("FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        return 1
    print("All bitwise-assign checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
