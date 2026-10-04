#!/usr/bin/env python3
"""The AST walkers agree with each other, and the one-walk memo agrees with the walk.

Review L-10 lists "five separate AST walkers with different coverage" as
maintainability. It is not only that: **the divergence it names was a live
defect.** `compiler.py`'s reference walk (which decides what tree-shaking
keeps) skipped switch case LABELS where `gen_fnptr.py`'s visits them, so a
module referenced ONLY from a `case other.CONST` was pruned as unreferenced -
and the label then emitted

    case codes.RED: {

verbatim into the C, with no mosaik diagnostic at all, where the identical
reference in an EXPRESSION gives the clear, located
`main.mos:6: module "main" uses "codes.RED" but does not import "codes"`.

The second half is `generate()`'s ~40 `_used` flags. Each was its own whole-
program traversal: measured on the reference-engine sample conversion, **93 walks costing 18.1 s, 58%
of the compile**, which one traversal answers instead (31.2 s -> 14.2 s for
everything before the link). The risk a memo carries is answering about a
program that has since been rewritten, so it is keyed on the program OBJECT,
built at the top of `generate()` and dropped at the end - and this checks it
against the walk it replaces, verb for verb.
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
from mosaik.codegen import CodeGenerator

ok = True


def check(cond, label, detail=""):
    global ok
    ok = ok and bool(cond)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- " + detail) if detail else ""))


CODES = 'module "codes" {\n    const RED: u8 = 1\n    export RED\n}\n'
BODY = '''module "main" {
    import "platform.video"
    var g: u8 = 0
    function main() {
        video.enable_lcd()
%s
        loop { video.wait_vblank() }
    }
    export main
}
'''
LABEL_REF = BODY % ("        switch g {\n        case codes.RED {\n"
                    "            g = 9\n        }\n        }")
EXPR_REF = BODY % "        if g == codes.RED { g = 9 }"


def compile_two(src):
    return MosaikCompiler().compile_program(
        [("main.mos", src), ("codes.mos", CODES)], platform="gameboy")


def test_a_switch_label_is_a_reference():
    print("\n[a reference in a case LABEL counts like one in an expression]")
    lab = compile_two(LABEL_REF).splitlines()[0]
    exp = compile_two(EXPR_REF).splitlines()[0]
    check("does not import" in lab,
          "the label form reports the missing import", lab[:100])
    check("does not import" in exp,
          "the expression form does too (it always did)", exp[:100])
    check('"codes.RED"' in lab and '"codes.RED"' in exp,
          "and both name the same symbol")
    # The failure mode this replaces: pruned module, C emitted with a raw
    # `case codes.RED:` in it and no diagnostic at all.
    check("case codes.RED" not in compile_two(LABEL_REF),
          "no raw `case codes.RED:` reaches the C")


def test_every_walker_visits_switch_labels():
    """Source contract: neither walker may go back to skipping them."""
    print("\n[both walkers visit case labels]")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for path, needle in (
            ("mosaik/compiler.py", "for label in labels:"),
            ("mosaik/codegen/gen_fnptr.py", "for label in labels:")):
        src = open(os.path.join(root, path), encoding="utf-8").read()
        # inside the SwitchStmt arm, not merely somewhere in the file
        arm = src[src.index("isinstance(stmt, SwitchStmt)"):]
        arm = arm[:arm.index("ReturnStmt")]
        check(needle in arm, "%s walks the labels" % path)


def test_the_memo_agrees_with_the_walk():
    print("\n[the one-walk set answers exactly what the walk answers]")
    src = '''module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "graphics.bkg"
    var A: array[u8, 4]
    var g: u8 = 0
    function helper() {
        sprite.set_prop(0, 0)
        switch g {
        case 1 {
            bkg.set_tiles(0, 0, 1, 1, A)
        }
        }
    }
    function main() {
        video.enable_lcd()
        helper()
        loop { video.wait_vblank() }
    }
    export main
}
'''
    c = MosaikCompiler()
    c.compile_program([("main.mos", src)], platform="gameboy")
    g = c.code_generator
    # generate() drops the set when it finishes, which is itself the contract:
    # a stale set would answer about a rewritten AST.
    check(getattr(g, "_called_verbs", "missing") is None,
          "the set is dropped when generate() returns")

    # Rebuild it against a fresh parse and compare, verb for verb, with the
    # traversal it replaces.
    from mosaik.lexer import Lexer
    from mosaik.parser import Parser
    prog = Parser(Lexer(src).tokenize(), "gameboy", {}).parse()
    gen = CodeGenerator()
    verbs = gen._collect_called_verbs(prog)
    check(("sprite", "set_prop") in verbs, "it finds a call in a function body")
    check(("bkg", "set_tiles") in verbs, "...and one inside a switch case")
    check(("video", "enable_lcd") in verbs, "...and one in main")
    check(("sprite", "set_meta") not in verbs, "and does not invent one")
    probes = [("sprite", "set_prop"), ("bkg", "set_tiles"), ("video", "enable_lcd"),
              ("sprite", "set_meta"), ("bkg", "move"), ("video", "wait_vblank"),
              ("text", "print_string"), ("palette", "fade")]
    same = all(((a, f) in verbs) == gen._walk_uses_call(prog, a, f)
               for a, f in probes)
    check(same, "the set and the walk agree on every probe")


def main():
    print("Walker coverage, and the one-walk memo")
    print("=" * 50)
    test_a_switch_label_is_a_reference()
    test_every_walker_visits_switch_labels()
    test_the_memo_agrees_with_the_walk()
    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
