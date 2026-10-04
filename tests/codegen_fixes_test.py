#!/usr/bin/env python3
"""Regression tests for the codegen-review fixes.

Each check pins one verified finding: G1/G2/G3/G4/G5/G9/G10 in the shared
generator, B1/B3/B4/B8 in the backends. Assertion-based; exits nonzero on
failure.
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


def compile_src(src, platform="gameboy", **kw):
    return MosaikCompiler().compile_program([("<test>", src)],
                                            platform=platform, **kw)


def ok(src, platform="gameboy", **kw):
    out = compile_src(src, platform, **kw)
    assert not out.startswith("Compilation error"), out.splitlines()[0]
    return out


def rejected(src, needle, platform="gameboy"):
    out = compile_src(src, platform)
    assert out.startswith("Compilation error") and needle in out, \
        "expected rejection mentioning %r, got: %s" % (needle,
                                                       out.splitlines()[0])


def main():
    print("codegen-review regression checks")

    # G1: nested assignment is parenthesized; statement position is not.
    out = ok('module "m" { function main() { '
             'var a: u8 = 0 var b: u8 = 0 var x: u8 = 0 '
             'a = (b = x) + 1 } }')
    assert "a = ((b = x) + 1);" in out, \
        [l for l in out.splitlines() if "a = " in l]
    out = ok('module "m" { var g: u8 = 0 function main() { g = 1 g += 2 } }')
    assert "\ng = 1;" in out.replace("    ", "\n") or "g = 1;" in out
    assert "(g = 1)" not in out
    print("  ok G1: assignment parenthesized when nested only")

    # G2: for-loop variable width follows the bounds.
    out = ok('module "m" { var t: u16 = 0 function main() { '
             'for i in 0..256 { t += 1 } } }')
    assert "{ uint16_t i;" in out
    out = ok('module "m" { var n: u16 = 300 var t: u16 = 0 function main() { '
             'for i in 0..n { t += 1 } } }')
    assert "{ uint16_t i;" in out
    out = ok('module "m" { var t: u8 = 0 function main() { '
             'for i in 0..8 { t += 1 } } }')
    assert "{ uint8_t i;" in out
    print("  ok G2: 16-bit bounds widen the loop variable")

    # G3: a hoisted initializer evaluates IN PLACE on cc65 (split decl).
    src = ('module "m" { function f(a: u8) -> u8 { '
           'a = a + 1 var b: u8 = a return b } '
           'function main() { var r: u8 = f(1) } }')
    lynx = [l.strip() for l in ok(src, "lynx").splitlines()]
    i = lynx.index("uint8_t f(uint8_t a) {")
    assert lynx[i + 1:i + 5] == ["uint8_t b;", "a = (a + 1);", "b = a;",
                                 "return b;"], lynx[i:i + 5]
    # A literal initializer still hoists whole.
    lynx2 = ok('module "m" { function main() { var a: u8 = 0 a = 1 '
               'var b: u8 = 5 a = b } }', "lynx")
    assert "uint8_t b = 5;" in lynx2
    print("  ok G3: cc65 hoist splits non-literal initializers")

    # G9: for-body and switch-case decls after statements are hoisted on cc65.
    lynx3 = ok('''module "m" {
        function main() {
            var x: u8 = 0
            for i in 0..4 { x = i var t: u8 = x x = t }
            switch x {
                case 1 { x = 2 var u: u8 = x x = u }
                default { x = 9 var w: u8 = x x = w }
            }
        }
    }''', "lynx")
    for v in ("t", "u", "w"):
        assert ("uint8_t %s;" % v) in lynx3 and ("%s = x;" % v) in lynx3, v
    print("  ok G9: for/case/default bodies hoist on cc65")

    # G4: bank(N) + assets emits a valid bank TU with the asset symbols.
    comp = MosaikCompiler()
    out = comp.compile_program(
        [("<test>", 'module "m" { import "graphics.sprite" '
                    'bank(2) function h() { sprite.set_tile(0, hero_tile) } '
                    'function main() { h() loop { } } }')],
        platform="gameboy", assets=[("hero", bytes(16))],
        asset_sprites=[("hero", 0, 1, 1)])
    assert not out.startswith("Compilation error"), out.splitlines()[0]
    bank2 = comp.code_generator.bank_units[2]
    assert "#define hero_tile_count 1" in bank2
    assert "extern const uint8_t hero_tiles[16];" in bank2
    assert "#define hero_tile 0" in bank2
    print("  ok G4: bank TU carries asset + named-sprite symbols")

    # G5: struct-literal assignment through a side-effecting target rejected.
    rejected('''module "m" {
        type P = struct { x: u8, y: u8 }
        var pts: array[P, 4]
        var n: u8 = 0
        function nxt() -> u8 { n += 1 return n }
        function main() { pts[nxt()] = {x: 1, y: 2} }
    }''', "function call in it")
    out = ok('module "m" { type P = struct { x: u8, y: u8 } var p: P '
             'function main() { p = {x: 1, y: 2} } }')
    assert "p.x = 1;" in out and "p.y = 2;" in out
    print("  ok G5: side-effecting struct-literal target rejected")

    # G10: cross-module enum-variant collision rejected.
    r = MosaikCompiler().compile_program(
        [("a.mos", 'module "a" { type C = enum { RED } '
                   'function f() -> u8 { return RED } export f }'),
         ("b.mos", 'module "b" { import "a" type H = enum { RED } '
                   'function main() { var x: u8 = a.f() } }')],
        platform="gameboy")
    assert r.startswith("Compilation error") and "enum constant 'RED'" in r
    print("  ok G10: cross-module enum variant collision rejected")

    # B1: set_palette without the graphics.palette import emits its helper.
    src = ('module "m" { import "graphics.sprite" '
           'function main() { sprite.set_palette(0, 1) loop { } } }')
    for plat in ("gameboy_color", "lynx"):
        out = ok(src, plat)
        assert "gbs_sprite_palette" in out, plat
    print("  ok B1: set_palette emits the palette helpers")

    # B3: cc65 rand() cast to u8; GBDK stays bare.
    src = ('module "m" { import "platform.system" var r: u8 = 0 '
           'function main() { r = system.random() } }')
    assert "((uint8_t)rand())" in ok(src, "lynx")
    gb = ok(src, "gameboy")
    assert "(uint8_t)rand" not in gb and "rand()" in gb
    print("  ok B3: cc65 random cast to u8")

    # B4: SMS/NES print_number is unsigned.
    out = ok('module "m" { import "graphics.text" '
             'function main() { text.print_number(0, 0, 40000) } }', "sms")
    assert 'printf("%u", n)' in out
    print("  ok B4: sms print_number uses %u")

    # B8: GG text plotters clamp rows >= 28 (SAT protection); GB unchanged.
    src = ('module "m" { import "graphics.text" '
           'function main() { text.print_string(0, 30, "X") '
           'text.clear_area(0, 26, 4, 6) } }')
    gg = ok(src, "gamegear")
    i = gg.index("void gbs_print_string(")
    assert "y >= 28" in gg[i:i + 300]
    assert "SAT clamp" in gg
    gb = ok(src, "gameboy")
    i = gb.index("void gbs_print_string(")
    assert "y >= 28" not in gb[i:i + 300]
    print("  ok B8: GG text plotters clamp at row 28")

    # ---- second batch: the accepted-recommendation fixes ------------------

    # G6: ambiguous mangled names are rejected with a source-level error.
    r = MosaikCompiler().compile_program(
        [("a.mos", 'module "a" { import "a_b" var b_c: u8 = 1 '
                   'function main() { b_c = a_b.c } }'),
         ("ab.mos", 'module "a_b" { var c: u8 = 2 export c }')],
        platform="gameboy")
    assert r.startswith("Compilation error") and \
        "share the C name 'a_b_c'" in r, r.splitlines()[0]
    print("  ok G6: mangling collision detected")

    # G7: referencing a module symbol before the local that shadows it.
    rejected('module "m" { var g: u8 = 0 function main() { '
             'g = 5 var g: u8 = 1 g += 1 } }',
             "before the local declaration")
    ok('module "m" { var g: u8 = 0 function main() { var g: u8 = 1 g += 1 '
       'f() } function f() { g = 2 } }')
    print("  ok G7: use-before-decl of a shadowed symbol rejected")

    # G8: aggregate literals only work as initializers / assignment RHS.
    rejected('module "m" { type P = struct { x: u8, y: u8 } '
             'function f() -> u8 { return {x: 1, y: 2} } '
             'function main() { var a: u8 = f() } }',
             "returning a struct/array literal")
    rejected('module "m" { type P = struct { x: u8, y: u8 } '
             'function g(p: P) { } function main() { g({x: 1, y: 2}) } }',
             "cannot be passed as an argument")
    print("  ok G8: aggregate literal in return/argument rejected")

    # G11: a streamed const array bigger than one ROM bank errors clearly.
    big = "[" + ", ".join(["1"] * 20000) + "]"
    rejected('module "m" { import "platform.assets" '
             'const BIG: array[u8, 20000] = %s var x: u8 = 0 '
             'function main() { assets.use(BIG) x = BIG[0] loop { } } }'
             % big, "more than one 16384-byte ROM bank")
    print("  ok G11: oversized banked array rejected")

    # G12: break directly inside a switch case is forbidden; loop breaks
    # (including a loop nested in a case) still work.
    rejected('module "m" { function main() { var x: u8 = 1 loop { '
             'switch x { case 1 { break } } } } }', "auto-break")
    ok('module "m" { function main() { var x: u8 = 1 '
       'switch x { case 1 { while x < 9 { x += 1 break } } } } }')
    ok('module "m" { function main() { loop { break } } }')
    print("  ok G12: break-in-case rejected, loop breaks intact")

    # B6: cc65 delay rounds ticks UP (never 0 for ms > 0).
    out = ok('module "m" { import "platform.system" '
             'function main() { system.delay(16) } }', "lynx")
    assert "+ 999) / 1000" in out and "ticks = 1" in out, \
        [l for l in out.splitlines() if "gbs_delay" in l or "ticks" in l]
    print("  ok B6: cc65 delay never truncates to 0 ticks")

    # B7: metasprite tables are bounds-guarded and sized per console.
    src = ('module "m" { import "graphics.sprite" '
           'function main() { sprite.set_meta(0, 0, 2, 2) } }')
    gb = ok(src, "gameboy")
    assert "#define GBS_META_SLOTS 40" in gb and \
        "base >= GBS_META_SLOTS" in gb, "gb meta guard missing"
    nes = ok(src, "nes")
    assert "#define GBS_META_SLOTS 64" in nes, "nes should have 64 slots"
    lynx = ok(src, "lynx")
    assert "base >= GBS_MAX_SPRITES) return;" in lynx, "cc65 meta guard missing"
    assert "nb < GBS_MAX_SPRITES && (gbs_meta_w[nb]" in lynx, \
        "cc65 meta branch must bounds-check before reading"
    print("  ok B7: metasprite tables guarded (40 GB / 64 NES, cc65 checked)")

    print("All codegen-review regression checks passed")


if __name__ == "__main__":
    main()
