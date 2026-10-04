#!/usr/bin/env python3
"""Bitwise operators (& | ^ << >>): lexing, precedence, inference, and both
backends' emitted C plus a real toolchain build. See the spec 2.3."""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler  # noqa: E402
from mosaik8_build import cc65_available, gbdk_available  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ok(src, platform="gameboy"):
    out = MosaikCompiler().compile_program([("<test>", src)],
                                           platform=platform)
    assert not out.startswith("Compilation error"), out.splitlines()[0]
    return out


def _toolchain_available(plat):
    """The ROM build needs the real toolchain; the pure-Python checks above do
    not. **The builder's own probe, not a lookalike** - a directory named
    `gbdk` is not a toolchain (mosaik8_build.gbdk_available)."""
    return cc65_available() if plat == "lynx" else gbdk_available()


def main():
    print("bitwise operator checks")

    # Every operator lowers to its C twin, fully parenthesized.
    out = ok('''module "m" {
        var w: u16 = 0
        function main() {
            var x: u8 = 0xF0
            var y: u8 = x & 0x0F
            y = x | 3
            y = x ^ 255
            y = x >> 4
            y = x << 1
            w = w & 255
        }
    }''')
    for frag in ("(x & 15)", "(x | 3)", "(x ^ 255)", "(x >> 4)", "(x << 1)",
                 "(w & 255)"):
        assert frag in out, frag
    print("  ok: all five operators emit")

    # Precedence: bitwise binds tighter than comparison (Rust/Go, not C);
    # shifts bind tighter than & ; + binds tighter than shifts.
    out = ok('''module "m" {
        var b: u8 = 0
        function main() {
            var x: u8 = 5
            b = x & 3 == 1
            if x & 8 == 8 { b = 1 }
            b = x & 1 << 2
            b = x << 1 + 1
            b = x | 2 ^ 3 & 1
        }
    }''')
    for frag in (
        "((x & 3) == 1)",          # & before ==
        "if (((x & 8) == 8))",     # the C-footgun case, sane here
        "(x & (1 << 2))",          # << before &
        "(x << (1 + 1))",          # + before <<
        "(x | (2 ^ (3 & 1)))",     # | loosest, then ^, then &
    ):
        assert frag in out, (frag,
                             [l for l in out.splitlines() if "b = " in l])
    print("  ok: precedence (comparison > | > ^ > & > shifts > additive)")

    # << vs <= / >> vs >= lex correctly side by side.
    out = ok('module "m" { function main() { var a: u8 = 4 '
             'if a <= 4 { a = a << 1 } if a >= 8 { a = a >> 2 } } }')
    assert "(a << 1)" in out and "(a >> 2)" in out
    print("  ok: shift vs comparison lexing")

    # Inference: & | ^ promote like arithmetic; shifts keep the left width.
    out = ok('module "m" { var n: u16 = 300 function main() { '
             'var w = n & 255 var s = n >> 4 var b = 3 & 1 } }')
    lines = {l.strip() for l in out.splitlines()}
    assert "uint16_t w = (n & 255);" in lines, sorted(
        l for l in lines if " w " in l)
    assert "uint16_t s = (n >> 4);" in lines
    assert "uint8_t b = (3 & 1);" in lines
    print("  ok: inference (promote for & | ^, left width for shifts)")

    # Both backends build the emitted C for real.
    src = '''module "m" {
        import "graphics.sprite"
        function main() {
            var flags: u8 = 0
            flags = flags | 0x80
            flags = flags & 0x7F
            var t: u16 = 300
            t = t >> 1 ^ 5
            sprite.move(0, flags, t & 255)
            loop { }
        }
    }
    '''
    tmp = tempfile.mkdtemp()
    try:
        p = os.path.join(tmp, "bw.mos")
        with open(p, "w", encoding="utf-8") as f:
            f.write(src)
        for plat in ("gameboy", "lynx"):
            if not _toolchain_available(plat):
                print("  skip: %s ROM build (toolchain not installed)" % plat)
                continue
            r = subprocess.run(
                [sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                 "--platform", plat, p],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace")
            out = (r.stdout or "") + (r.stderr or "")
            assert "ROM created" in out, "%s build failed:\n%s" % (
                plat, out[-800:])
            print("  ok: %s ROM builds" % plat)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("All bitwise checks passed")


if __name__ == "__main__":
    main()
