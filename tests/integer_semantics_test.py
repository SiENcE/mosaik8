#!/usr/bin/env python3
"""The checker's arithmetic model is C's, and storage inference is not.

Review L-1. `gen_expression` emits `(a op b)` with no casts, so C's usual
arithmetic conversions govern the generated code: on sdcc and cc65 `int` is
16 bits, both 8-bit operands widen, and the result does NOT wrap. The checker
ranked `u8 < i8 < u16 < i16` and called `u8 - u8` a u8, i.e. it modelled a
wrap the emitted C never had.

**Which model to change was decided by measuring, not by preference.** A sweep
of all 95 build targets found 63 distinct sites where the two disagree, and
every one of them depends on C's behaviour:

    lib/engine/camera.mos:56   if target - cur < step { return target }
    lib/vm/combat.mos:49       return b - a < aw          (an axis overlap)
    lib/vm/player.mos:1126     while scr_ax <= 0 - 4      (a negative literal)

Making the old model true would mean emitting `(uint8_t)` narrowing casts -
which is also L-7's first bullet - and that would break all 63. So the MODEL
moved and the emission did not: the eight ROMs of the reference-engine sample conversion (GB,
GBC), the SMS/GG sample, platformer and shooter conversions, `vm-shmup`
(Lynx, PCE) and
`vm-combat` (Lynx) are byte-identical across the change, and the four goldens
match.

The one place the narrow model still belongs is a `var`'s inferred type:
`var d = a - b` is a `uint8_t` and the C assignment truncates into it, exactly
as C itself does at an assignment. That is `_storage_type`, and keeping the
two apart is the whole of the fix.
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
from mosaik.typechecker import TypeChecker
from mosaik.ast_nodes import PrimitiveType

ok = True


def check(cond, label, detail=""):
    global ok
    ok = ok and bool(cond)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- " + detail) if detail else ""))


def promote(a, b, storage=False):
    t = TypeChecker().promote_arithmetic_type(PrimitiveType(a), PrimitiveType(b),
                                              storage=storage)
    return getattr(t, "name", None)


def compile_gb(src):
    c = MosaikCompiler()
    out = c.compile_program([("m.mos", src)], platform="gameboy")
    assert not out.startswith("Compilation error"), out
    return out, c.code_generator


PROG = '''
module "main" {
    import "platform.video"
    var a: u8 = 3
    var b: u8 = 200
    var w: u16 = 9
    var s: i16 = 0 - 5
    function main() {
        video.enable_lcd()
        var d = a - b
        var e = a + w
        var f = a & b
        var g = s + w
        video.wait_vblank()
    }
}
'''


def test_the_value_model_is_c():
    print("\n[the expression's type is what C computes]")
    # Anything narrower than int is promoted TO int, so a pair of u8 is int.
    # There is no same-name shortcut here, and writing one was this fix's own
    # first bug (it sent u8+u8 straight back to u8).
    check(promote("u8", "u8") == "i16", "u8 op u8 is int", promote("u8", "u8"))
    check(promote("i8", "u8") == "i16", "i8 op u8 is int", promote("i8", "u8"))
    check(promote("u8", "u16") == "u16", "u8 op u16 is unsigned int")
    # The old rank order said i16, because it ranked i16 above u16. C converts
    # the int to unsigned, which is exactly why the ordering comparison of the
    # two is worth a warning.
    check(promote("i16", "u16") == "u16", "i16 op u16 is UNSIGNED int",
          promote("i16", "u16"))
    check(promote("i16", "i16") == "i16", "i16 op i16 is int")
    check(promote("u16", "u16") == "u16", "u16 op u16 is unsigned int")


def test_storage_inference_is_unchanged():
    print("\n[a var's inferred type is the narrow one, as before]")
    check(promote("u8", "u8", storage=True) == "u8", "storage: u8 op u8 is u8")
    check(promote("i16", "u16", storage=True) == "i16",
          "storage: the old rank order survives for storage")
    c, _g = compile_gb(PROG)
    body = c[c.index("main(void) {"):]
    body = body[:body.index("\n}")]
    # `s + w` is i16 + u16: C says UNSIGNED int, the storage rule deliberately
    # keeps the old rank order (i16 above u16) so the emitted declaration is
    # unchanged. That divergence is the point of having two rules, and pinning
    # it here is what stops someone "tidying" them back together.
    for decl in ("uint8_t d = (a - b);", "uint16_t e = (a + w);",
                 "uint8_t f = (a & b);", "int16_t g = (s + w);"):
        check(decl in body, "emitted: %s" % decl, body.strip()[:110])


def test_no_casts_are_emitted():
    print("\n[and no narrowing cast appears - L-7's first bullet, refused]")
    c, _g = compile_gb(PROG)
    body = c[c.index("main(void) {"):]
    body = body[:body.index("\n}")]
    check("(uint8_t)(a - b)" not in body and "(uint8_t)((a - b))" not in body,
          "the subtraction is not narrowed in place")
    # The measurement behind the refusal: these three shapes exist in the
    # engine and read the C value, not a wrapped one.
    for path, needle in (
            ("lib/engine/camera.mos", "if target - cur < step {"),
            ("lib/vm/combat.mos", "return b - a < aw"),
            ("lib/vm/player.mos", "while scr_ax <= 0 - 4 {")):
        full = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), path)
        src = open(full, encoding="utf-8").read()
        check(needle in src, "still relies on C's width: %s" % path, needle)


def test_the_unsigned_comparison_warning_now_covers_8_bit():
    print("\n[the i16-vs-u16 warning reaches the 8-bit case]")
    src = '''
module "main" {
    import "platform.video"
    var a: u8 = 3
    var b: u8 = 200
    var w: u16 = 9
    function main() {
        video.enable_lcd()
        if a - b < w { }
    }
}
'''
    c = MosaikCompiler()
    c.compile_program([("m.mos", src)], platform="gameboy")
    warns = " ".join(c.type_checker.diagnostics)
    check("Comparison of i16 with u16" in warns,
          "a u8 difference against a u16 is diagnosed", warns[:100])


def main():
    print("Integer semantics: the model is C's, the storage rule is not")
    print("=" * 60)
    test_the_value_model_is_c()
    test_storage_inference_is_unchanged()
    test_no_casts_are_emitted()
    test_the_unsigned_comparison_warning_now_covers_8_bit()
    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
