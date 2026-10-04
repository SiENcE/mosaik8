#!/usr/bin/env python3
"""Parser/typechecker error-handling regression test.

Asserts that invalid source is REJECTED with a real diagnostic and that the
diagnostics stay advisory (they must never change the generated C). Replaces
the old test_fixes2.py, which printed its findings without ever failing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler  # noqa: E402


def compile_src(src, platform="gameboy"):
    return MosaikCompiler().compile_program([("<test>", src)],
                                            platform=platform)


def check_rejected(name, src, expect_in_error=None):
    result = compile_src(src)
    assert result.startswith("Compilation error:"), \
        "%s: should have been rejected but compiled" % name
    if expect_in_error:
        first = result.splitlines()[0]
        assert expect_in_error in first, \
            "%s: error %r does not mention %r" % (name, first, expect_in_error)
    print("  ok (rejected): %s" % name)


def check_compiles(name, src):
    result = compile_src(src)
    assert not result.startswith("Compilation error:"), \
        "%s: should compile but failed: %s" % (name, result.splitlines()[0])
    print("  ok (compiles): %s" % name)
    return result


def main():
    print("parser/typechecker error handling")

    # -- invalid source is rejected with a diagnostic --------------------
    check_rejected("missing closing brace", '''
module "bad1" {
    function test() {
        return 42
''')
    check_rejected("export trailing comma", '''
module "bad2" {
    function test() { }
    export test,
}
''')
    # The `=` vs `==` typo is a parse error in condition position.
    check_rejected("assignment in if condition", '''
module "bad3" {
    function main() {
        var x: u8 = 0
        if x = 1 { }
    }
}
''', expect_in_error="use '=='")
    check_rejected("assignment in while condition", '''
module "bad4" {
    function main() {
        var x: u8 = 0
        while x = 1 { }
    }
}
''', expect_in_error="use '=='")
    # A non-lvalue assignment target is a parse error.
    check_rejected("literal assignment target", '''
module "bad5" {
    function main() { 1 = 2 }
}
''', expect_in_error="Invalid assignment target")
    check_rejected("call assignment target", '''
module "bad6" {
    function f() -> u8 { return 0 }
    function main() { f() = 2 }
}
''', expect_in_error="Invalid assignment target")

    # -- valid source still compiles -------------------------------------
    check_compiles("export list styles", '''
module "ok1" {
    function test() { }
    var a: u8 = 0
    export test, a
}
''')
    check_compiles("assignments and compound assignment", '''
module "ok2" {
    var g: u8 = 0
    function main() {
        g = 1
        g += 2
        while g < 9 { g -= 1 }
    }
}
''')

    # -- diagnostics are advisory and deterministic -----------------------
    # `%` infers cleanly (it used to abort the whole typecheck pass).
    c = check_compiles("modulo in an initializer", '''
module "ok3" {
    function main() {
        var x: u8 = 6 % 2
        var a: u16 = 300
        var w = a + a
    }
}
''')
    assert "uint16_t w" in c, "w should infer u16, got: %s" % [
        l for l in c.splitlines() if " w " in l]
    print("  ok: % infers, w is u16")

    # A diagnostic elsewhere must not change unrelated inference: the same
    # `w` stays u16 with an undefined-variable diagnostic in the program
    # (the typechecker writes inferred types into the AST, so an aborting
    # check used to flip w to u8 -- silent truncation).
    c2 = check_compiles("diagnostic does not change codegen", '''
module "ok4" {
    function main() {
        var bad: u8 = nope
        var a: u16 = 300
        var w = a + a
    }
}
''')
    assert "uint16_t w" in c2, \
        "a diagnostic changed unrelated inference (w != u16)"
    print("  ok: diagnostics do not change generated C")

    # The unknown-type diagnostic is recorded (collected, not raised).
    from mosaik import MosaikCompiler as MC
    comp = MC()
    result = comp.compile_program(
        [("<test>", 'module "d" { function main() { '
                    'var x: not_a_type = 0 } }')], platform="gameboy")
    assert not result.startswith("Compilation error:")
    assert any("Unknown type" in d for d in comp.type_checker.diagnostics), \
        "unknown-type diagnostic missing: %r" % comp.type_checker.diagnostics
    print("  ok: unknown type is a collected diagnostic")

    print("All parser/typechecker error-handling checks passed")


if __name__ == "__main__":
    main()
