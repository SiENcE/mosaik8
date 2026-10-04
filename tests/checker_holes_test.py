#!/usr/bin/env python3
"""The type checker's holes, closed (review L-8) - and its diagnostics located (L-9).

L-8 listed seven things the checker let through. Each is pinned here by the
program that used to compile silently.

**Every one of them was swept over all 95 build targets before it was kept.**
That is the discipline the item needs: a stricter checker fails by being NOISY,
and a flood of warnings on working code gets all warnings ignored, which is
worse than the silence it replaces. The whole repo produced **8 warning
instances at one site** (`lib/vm/trig.mos:98`, the pre-existing i16/u16
comparison) with all of this on - the same count as before. Since 2026-10-03
it produces NONE: that site compared against a bare-`int` literal, which the
check now exempts (`frontend_semantics_test.py`). The one check that
did flood, argument typing, was flooding because of a bug in the CHECK: `bool`
lowers to `uint8_t`, so it interoperates with the integer types, and leaving it
out invented 44 warnings on `topdown.facing4(input.held(...), ...)`.

L-9's remaining half is here too, because it is what makes the rest readable:
a codegen failure now says `file:line:` instead of carrying a Python traceback
that names `gen_expr.py`. `compile_program` still RETURNS the string (59 test
files and the build tool read that contract, several asserting a bad program
produces it) - what changed is what the string says.
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


HEAD = 'module "main" {\n    import "platform.video"\n'
TAIL = '\n    function main() { video.enable_lcd() }\n}\n'


def diags(body, extra=()):
    c = MosaikCompiler()
    src = HEAD + body + TAIL
    c.compile_program([("m.mos", src)] + list(extra), platform="gameboy")
    return " | ".join(c.type_checker.diagnostics)


def says(body, needle, label, extra=()):
    d = diags(body, extra)
    check(needle in d, label, d[:120] or "(silent)")


def silent(body, label, extra=()):
    d = diags(body, extra)
    check(not d, label, d[:120])


def test_literal_ranges():
    print("\n[a literal that does not fit]")
    says("    var q: u8 = 300", "does not fit 'q: u8'", "var q: u8 = 300")
    says("    var q: i8 = 200", "does not fit 'q: i8'", "var q: i8 = 200")
    says("    var q: u16 = 70000", "does not fit 'q: u16'", "var q: u16 = 70000")
    # `-1`, not `0 - 1`: L-3 folds a negative NUMBER into a Literal, and only
    # a literal has a knowable width. `0 - 1` is a BinaryOp and goes through
    # C's promotion like any other arithmetic.
    says("    var q: u8 = -1", "Cannot assign negative literal",
         "the negative-into-unsigned wording is kept")
    silent("    var q: u8 = 255\n    var r: i16 = 0 - 32768",
           "the edges of each range are accepted")
    # bool has no range here on purpose: it lowers to uint8_t and `= 5` is
    # still truthy, so inventing 0..1 would be inventing a rule.
    silent("    var b: bool = true", "a bool initialiser is left alone")


def test_returns():
    print("\n[what a function returns]")
    says("    function f() -> u8 { video.wait_vblank() }",
         "never returns a value", "declared -> u8, returns nothing")
    says("    function f() -> u8 { return }",
         "Bare `return` in a function", "a bare return in a value function")
    says("    function f() { return 1 }",
         "Returning a value from a function declared without",
         "a value from a void function")
    says("    function f() -> u8 { return 400 }",
         "does not fit the return type", "an out-of-range return literal")
    silent("    function f() -> u8 { return 7 }", "a correct one is silent")
    # Deliberately NOT a path analysis: only "no `return <value>` anywhere".
    silent("    function f() -> u8 { if 1 == 1 { return 1 } return 2 }",
           "a return inside a branch counts")


def test_call_arguments():
    print("\n[call arguments are visited and typed]")
    says("    function f(a: u8) { video.wait_vblank() }\n"
         "    function g() { f(nosuchthing) }",
         "Undefined variable: nosuchthing",
         "an undefined name INSIDE an argument is found")
    says("    function f(a: u8) { video.wait_vblank() }\n"
         "    function g() { f(300) }",
         "does not fit parameter 1", "a literal that cannot fit a parameter")
    silent("    function f(a: u8, b: u16) { video.wait_vblank() }\n"
           "    function g() { f(1, 2) }", "a correct call is silent")
    # bool IS an integer type here (it lowers to uint8_t), so this must not
    # warn - it is the shape that flooded the first sweep.
    silent("    function f(a: u8) { video.wait_vblank() }\n"
           "    function g() { f(true) }",
           "a bool argument into a u8 parameter is fine")


def test_assignment():
    print("\n[assignment]")
    says("    const LIM = 5\n    function g() { LIM = 3 }",
         "Cannot assign to constant 'LIM'", "a const is not a target")
    says("    var g: u8 = 0\n    function h() { g = 400 }",
         "does not fit the assignment target", "an out-of-range literal")
    silent("    var g: u8 = 0\n    function h() { g = 200 }",
           "an in-range assignment is silent")


def test_scope_is_per_module():
    print("\n[the scope does not leak between modules]")
    a = 'module "a" {\n    var secret: u8 = 1\n    export secret\n}\n'
    says("    function g() -> u8 { return secret }",
         "Undefined variable: secret",
         "a BARE reference to another module's global is caught here, not in C",
         extra=[("a.mos", a)])


def test_types_are_program_wide():
    print("\n[a type declared later resolves earlier]")
    b = 'module "b" {\n    type Pt = struct {\n        x: u8\n        y: u8\n    }\n    export Pt\n}\n'
    c = MosaikCompiler()
    src = ('module "main" {\n    import "platform.video"\n    import "b"\n'
           '    var p: Pt\n    function main() { video.enable_lcd() }\n}\n')
    c.compile_program([("m.mos", src), ("b.mos", b)], platform="gameboy")
    d = " | ".join(c.type_checker.diagnostics)
    check("Unknown type" not in d, "declaration ORDER no longer decides it", d[:110])


def test_string_carries_its_nul():
    print("\n[an inferred string is long enough to be one]")
    c = MosaikCompiler()
    src = HEAD + '    function g() { var s = "hi" }' + TAIL
    out = c.compile_program([("m.mos", src)], platform="gameboy")
    line = next((l.strip() for l in out.splitlines() if " s[" in l), "(absent)")
    check("uint8_t s[3]" in out, "var s = \"hi\" is 3 bytes, not 2", line)


def test_a_codegen_failure_says_where():
    print("\n[L-9: a codegen failure carries its source line]")
    src = ('module "main" {\n    import "platform.video"\n'
           '    type Pt = struct {\n        x: u8\n        y: u8\n    }\n'
           '    function mk() -> Pt {\n        video.enable_lcd()\n'
           '        return { x: 1, y: 2 }\n    }\n'
           '    function main() { video.enable_lcd() }\n}\n')
    c = MosaikCompiler()
    out = c.compile_program([("m.mos", src)], platform="gameboy")
    first = out.splitlines()[0]
    check(out.startswith("Compilation error:"),
          "the STRING contract is unchanged (59 test files read it)")
    check("m.mos:9:" in first, "it names the mosaik file and line", first[:110])
    check("Traceback" not in out and "gen_expr.py" not in out,
          "and not a Python traceback (MOSAIK_TRACEBACK=1 brings it back)")
    err = getattr(c, "last_error", None)
    check(err is not None and getattr(err, "line", None) == 9,
          "the structured form is on compiler.last_error",
          repr(getattr(err, "line", None)))


def main():
    print("Checker holes (L-8) and located diagnostics (L-9)")
    print("=" * 55)
    test_literal_ranges()
    test_returns()
    test_call_arguments()
    test_assignment()
    test_scope_is_per_module()
    test_types_are_program_wide()
    test_string_carries_its_nul()
    test_a_codegen_failure_says_where()
    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
