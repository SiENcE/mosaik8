#!/usr/bin/env python3
"""Frontend semantics pinned by the 2026-09-02 review:

  L-5  string escape sequences reach the generated C as the bytes they name
  L-3  `-<number>` is a negative literal (typed by value; refused into unsigned)
  L-4  `alias.CONST` from another module infers as its declared type
  L-2  literals 32768..65535 emit with a U suffix (unsigned int, not long)
  L-6  a float literal is a positioned syntax error

Pure Python: compiles to C and inspects the text; no toolchain needed."""
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik.compiler import MosaikCompiler  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok: " if cond else "  FAIL: ") + msg)
    if not cond:
        FAILS.append(msg)


def compile_src(src, platform="gameboy", extra=()):
    comp = MosaikCompiler()
    sources = [("main.mos", src)] + list(extra)
    out = comp.compile_program(sources, platform=platform)
    diags = list(getattr(comp.type_checker, "diagnostics", []) or [])
    return out, diags


MAIN = '''module "main" {
    import "platform.video"
%s
    function main() {
%s
        loop { video.wait_vblank() }
    }
    export main
}
'''


def test_string_escapes():
    print("[L-5: string escapes]")
    out, _ = compile_src(MAIN % ('    const GREETING: array[u8, 6] = "a\\nb\\x41\\"q"', ""))
    check("Compilation error" not in out, "a string with \\n \\xNN \\\" compiles")
    check('"a\\012bA\\"q"' in out,
          "...and reaches C as the bytes it names (newline as octal, A, quote)")
    out, _ = compile_src(MAIN % ('    const T: array[u8, 3] = "a\\qb"', ""))
    check("unknown escape sequence '\\q'" in out and "line 3" in out,
          "an unknown escape is a positioned error")


def test_negative_literals():
    print("[L-3: negative literals]")
    out, diags = compile_src(MAIN % ("    var dx: i8 = -2\n    var big: i16 = -300", ""))
    check("int8_t dx = (-2);" in out and "int16_t big = (-300);" in out,
          "negative initializers emit parenthesised, as before")
    check(not [d for d in diags if "negative" in d], "...and are not diagnosed on signed types")
    out, diags = compile_src(MAIN % ("    var q: u8 = -1", ""))
    check(any("negative literal -1" in d and "q: u8" in d for d in diags),
          "`var q: u8 = -1` is diagnosed (it would be 255 in C)")
    out, diags = compile_src(MAIN % ("    var s: u8 = 3", "        s = 10 - -1"))
    check("(10 - (-1))" in out, "a negative literal inside an expression keeps its parentheses")


def test_cross_module_const_type():
    print("[L-4: alias.CONST from another module infers as its declared type]")
    lim = '''module "lim" {
    const LIMIT: u16 = 300
    const SMALL = 7
    export LIMIT, SMALL
}
'''
    src = MAIN % ('    import "lim"\n    var acc: u16 = 0',
                  "        for i in 0..lim.LIMIT { acc += 1 }\n"
                  "        for j in 0..lim.SMALL { acc += 1 }")
    out, diags = compile_src(src, extra=[("lim.mos", lim)])
    check("Compilation error" not in out, "the program compiles (%s)" % out[:80].strip())
    check(re.search(r"uint16_t i;", out) is not None,
          "the loop over a u16 module const gets a 16-bit loop variable (no endless loop)")
    check(re.search(r"uint8_t j;", out) is not None,
          "the loop over an inferred small const stays 8-bit")


def test_wide_literals():
    print("[L-2: literals 32768..65535 carry a U suffix]")
    out, _ = compile_src(MAIN % ("    var m: u16 = 0xFFFF\n    var n: u16 = 40000\n    var k: u16 = 32767",
                                 "        m = m ^ 0xFFFF"))
    check("uint16_t m = 65535U;" in out and "uint16_t n = 40000U;" in out,
          "a literal above INT16_MAX is unsigned int, not long")
    check("uint16_t k = 32767;" in out, "a literal that fits int stays bare")
    check("(m ^ 65535U)" in out, "...also inside expressions")


def test_float_literal():
    print("[L-6: a float literal is a positioned syntax error]")
    out, _ = compile_src(MAIN % ("", "        var f: u8 = 1.5"))
    check("Invalid number literal '1.5'" in out and "line 5" in out,
          "1.5 is refused with its line, not a bare int() traceback")


def test_separators_and_newlines():
    print("[L-6: argument lists need commas; a call may span lines]")
    out, _ = compile_src(MAIN % ("    var s: u8 = 0\n    function f(a: u8, b: u8) -> u8 { return a + b }",
                                 "        s = f(1 2)"))
    # LINE 6, and it used to be asserted as 5: the check passed on the
    # PYTHON TRACEBACK the error string carried, not on the diagnostic (this
    # substitution puts two lines into the first %s, so the call really is on
    # line 6). L-9 dropped the traceback and the accident showed.
    check("Expected ',' or ')' in argument list" in out and "line 6" in out,
          "`f(1 2)` is a positioned syntax error, not `f(1, 2)`")
    out, _ = compile_src(MAIN % ("    var s: u8 = 0\n    function f(a: u8, b: u8) -> u8 { return a + b }",
                                 "        s = f(\n            1,\n            2\n        )"))
    check("Compilation error" not in out and "f(1, 2)" in out,
          "a call split over lines inside its parentheses parses")
    out, _ = compile_src(MAIN % ("    function g(a: u8\n               b: u8) { }", ""))
    check("Expected ',' or ')' in parameter list" in out,
          "a parameter list without commas is refused")


def test_call_arity():
    print("[L-8: user-function calls are arity-checked]")
    out, diags = compile_src(MAIN % ("    var s: u8 = 0\n    function f(a: u8, b: u8) -> u8 { return a + b }",
                                     "        s = f(1)\n        s = f(1, 2, 3)\n        s = f(1, 2)"))
    arity = [d for d in diags if "Call to 'f'" in d]
    check(len(arity) == 2 and "passes 1 argument(s), it takes 2" in arity[0]
          and "passes 3 argument(s), it takes 2" in arity[1],
          "one diagnostic per wrong-arity call, none for the right one (%r)" % arity)
    out, diags = compile_src(MAIN % ("", "        video.enable_lcd()\n        video.wait_vblank()"))
    check(not [d for d in diags if "Call to" in d], "stdlib calls are not arity-checked (they register without parameters)")


def test_input_raw():
    print("[E-1: input.raw() reads the whole pad once]")
    for plat, marker in (("gameboy", "joypad()"), ("sms", "GBS_PAD_MASK"), ("lynx", "joy_read(0)")):
        out, diags = compile_src(MAIN % ("    var p: u8 = 0", "        p = input.raw()\n        p = p & INPUT_A"), platform=plat)
        check("Compilation error" not in out and "uint8_t gbs_input_raw(void)" in out
              and "gbs_input_pressed(0xFF)" in out and marker in out,
              "%s: input.raw lowers to the console's pad read, unmasked" % plat)
    out, _ = compile_src(MAIN % ("", "        if input.held(INPUT_A) { }"))
    check("gbs_input_raw" not in out, "a program that never calls input.raw emits no helper (byte-identical)")
    pad = open(os.path.join(ROOT, "lib", "engine", "pad.mos"), encoding="utf-8").read()
    body = pad.split("function update()")[1].split("\n    }")[0]
    check(body.count("input.raw()") == 1 and "input.held(" not in body,
          "engine.pad.update reads the pad once")


def test_diagnostic_lines_and_signed_compare():
    print("[L-9: diagnostics carry the source line; L-1: i16 vs u16 comparison warns]")
    out, diags = compile_src(MAIN % ("    var a: i16 = -1\n    var b: u16 = 5\n    var q: u8 = 0",
                                     "        if a < b { q = 1 }\n        q = undefined_name"))
    check(any(d.startswith("main.mos:7: ") and "i16 with u16" in d for d in diags),
          "the mixed-sign comparison is diagnosed with its line (%r)" % [d[:40] for d in diags])
    check(any(d.startswith("main.mos:8: ") and "Undefined variable: undefined_name" in d for d in diags),
          "an undefined name is diagnosed with its line")
    out, diags = compile_src(MAIN % ("    var q: u8 = -1", ""))
    check(any(d.startswith("main.mos:3: ") for d in diags),
          "a module-level declaration diagnostic carries its line")


def test_signed_compare_literals():
    print("[L-1: a literal the codegen writes as a bare int does not force unsigned]")
    # vm.trig's `while a > 1023 or b > 1023` warned twice on every build that
    # linked it: 1023 is typed u16, but C sees an `int` and compares signed.
    out, diags = compile_src(MAIN % ("    var a: i16 = -1\n    var q: u8 = 0",
                                     "        if a > 1023 { q = 1 }\n        if 32767 >= a { q = 2 }"))
    check("a > 1023" in out and "32767 >= a" in out,
          "the literals reach C bare (signed int)")
    check(not any("i16 with u16" in d for d in diags),
          "i16 against a literal 256..32767 is not diagnosed (%r)" % diags)
    out, diags = compile_src(MAIN % ("    var a: i16 = -1\n    var q: u8 = 0",
                                     "        if a < 40000 { q = 1 }"))
    check("a < 40000U" in out and any("i16 with u16" in d for d in diags),
          "a literal 32768..65535 is U-suffixed in C, so it still warns")
    # The megademo's `x > SCREEN_WIDTH - 8` warned on every console: int - int
    # stays an `int` in C, so arithmetic over only bare constants compares
    # signed exactly as the bare constant does.
    out, diags = compile_src(MAIN % ("    var a: i16 = -1\n    var q: u8 = 0",
                                     "        if a > SCREEN_WIDTH - 8 { q = 1 }\n"
                                     "        if a >= (SCREEN_HEIGHT - 4) * 2 { q = 2 }"))
    check(not any("i16 with u16" in d for d in diags),
          "i16 against constant arithmetic (SCREEN_WIDTH - 8) is not diagnosed (%r)" % diags)
    out, diags = compile_src(MAIN % ("    var a: i16 = -1\n    var b: u16 = 8\n    var q: u8 = 0",
                                     "        if a > SCREEN_WIDTH - b { q = 1 }"))
    check(any("i16 with u16" in d for d in diags),
          "a u16 VARIABLE in the arithmetic makes it unsigned again: it still warns")
    out, diags = compile_src(MAIN % ("    var a: i16 = -1\n    var q: u8 = 0",
                                     "        if a < 40000 - 8 { q = 1 }"))
    check(any("i16 with u16" in d for d in diags),
          "a U-suffixed literal in the arithmetic still warns")


def test_build_diagnostic_lines():
    print("[L-9: a diagnostic from a real BUILD names the file's own line]")
    # The build used to prepend a two-line `# platform <console>` header to
    # every source, so each build diagnostic pointed 2 lines below the fault
    # (`vm.trig:98` for the comparison on line 96).
    import tempfile
    import mosaik8_build
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "t"\ntarget_platforms = ["gameboy"]\n'
                '[source]\nfolder = "src/"\n')
    os.makedirs(os.path.join(d, "src"))
    os.makedirs(os.path.join(d, "build"))
    path = os.path.join(d, "src", "main.mos")
    with open(path, "w", encoding="utf-8") as f:
        f.write(MAIN % ("", "        var a: i16 = 0\n        var b: u16 = 3\n"
                            "        if a < b { a = 1 }"))
    builder = mosaik8_build.MosaikBuilder(os.path.join(d, "mosaik.toml"))
    builder.compiler.code_generator.platform = "gameboy"
    builder.compile_sources([path], os.path.join(d, "build"), "gameboy", "t")
    diags = list(builder.compiler.type_checker.diagnostics)
    check(any(x.startswith(path + ":7: ") and "i16 with u16" in x for x in diags),
          "the comparison on line 7 is reported at line 7 (%r)" % diags)


def main():
    test_diagnostic_lines_and_signed_compare()
    test_signed_compare_literals()
    test_build_diagnostic_lines()
    test_input_raw()
    test_separators_and_newlines()
    test_call_arity()
    test_string_escapes()
    test_negative_literals()
    test_cross_module_const_type()
    test_wide_literals()
    test_float_literal()
    if FAILS:
        print("\nfrontend semantics FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("\nAll frontend semantics checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
