#!/usr/bin/env python3
"""VM8 bytecode-blob PAGE streaming.

The interpreter reads its CODE blob only through `fetch(off)`, which mosaik_vm.py
now lowers via the `assets.code_byte(CODE, off)` seam. This checks that seam:

  * gameboy / small blob  -- lowers to the byte-identical `CODE[off]` (resident);
                             the seam is a true no-op below the threshold.
  * lynx / big blob        -- the blob leaves the resident RODATA into the cart
                             archive and fetch routes through a current-page cache
                             (`gbs_code_byte` + lseek/read), freeing MAIN.
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
from mosaik.codegen.generator import CodeGenerator

THRESH = CodeGenerator.LYNX_CODE_STREAM_THRESHOLD


def scripts_mod(n, fetch_body):
    blob = ", ".join(str(i % 256) for i in range(n))
    return (
        'module "scripts" {\n'
        '    import "platform.assets"\n'
        '    const CODE: array[u8, %d] = [%s]\n'
        '    function fetch(off: u16) -> u8 {\n'
        '        %s\n'
        '    }\n'
        '    export fetch\n'
        '}\n' % (n, blob, fetch_body)
    )


MAIN = '''
module "main" {
    import "platform.video"
    import "scripts"
    var pc: u16 = 0
    function main() {
        var b: u8 = scripts.fetch(pc)
        video.wait_vblank()
    }
    export main
}
'''


def compile_for(platform, n, body):
    src = [("main.mos", MAIN), ("scripts.mos", scripts_mod(n, body))]
    return MosaikCompiler().compile_program(src, platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("VM8 bytecode page streaming (7.1 #3)")
    print("=" * 50)
    ok = True
    big = THRESH + 2000            # comfortably over the streaming threshold
    small = THRESH - 100           # under it -> stays resident
    seam = "return assets.code_byte(CODE, off)"
    plain = "return CODE[off]"

    # gameboy: the seam is transparent on a directly-mapped console -- a big blob
    # stays resident and the emitted C is byte-identical to a plain CODE[off].
    gb_seam = compile_for("gameboy", big, seam)
    gb_plain = compile_for("gameboy", big, plain)
    ok &= check("[gameboy] big-blob seam compiles", not gb_seam.startswith("Compilation error:"))
    ok &= check("[gameboy] seam C is BYTE-IDENTICAL to plain CODE[off]", gb_seam == gb_plain)
    ok &= check("[gameboy] no page cache emitted", "gbs_code_byte" not in gb_seam)

    # lynx, small blob: below the threshold, byte-identical to plain (resident).
    lx_small_seam = compile_for("lynx", small, seam)
    lx_small_plain = compile_for("lynx", small, plain)
    ok &= check("[lynx] small-blob seam is BYTE-IDENTICAL to plain (resident)",
                lx_small_seam == lx_small_plain and "gbs_code_byte" not in lx_small_seam)

    # lynx, big blob: streams -- page cache emitted, blob left the resident image,
    # fetch routes through gbs_code_byte, and the build can recover the archive.
    comp = MosaikCompiler()
    lx_big = comp.compile_program(
        [("main.mos", MAIN), ("scripts.mos", scripts_mod(big, seam))], platform="lynx")
    ok &= check("[lynx] big-blob compiles", not lx_big.startswith("Compilation error:"))
    ok &= check("[lynx] page cache + cart read emitted",
                "gbs_code_byte" in lx_big and "lseek(1" in lx_big)
    ok &= check("[lynx] fetch routes through the page cache",
                "return gbs_code_byte(off);" in lx_big)
    # The reader is the VM8 interpreter's hottest routine on the Lynx -- it runs
    # for every byte of every instruction. It once derived the page with
    # `(long)off / GBS_CODE_PAGE` and indexed with `off % GBS_CODE_PAGE`, a 32-bit
    # division plus a modulo in cc65 software per byte, which cost 5 of the 7 LCD
    # frames a VM frame of the falling-block assembly sample took (7.00 -> 3.53
    # when fixed). Keep the hot path division-free: a subtract, an unsigned
    # compare, and a mask.
    ok &= check("[lynx] the page-cache hot path has no division or modulo",
                "/ GBS_CODE_PAGE" not in lx_big and "% GBS_CODE_PAGE" not in lx_big)
    ok &= check("[lynx] the hot path is a subtract + unsigned window compare",
                "unsigned int rel = off - gbs_code_lo;" in lx_big
                and "rel >= GBS_CODE_PAGE" in lx_big)
    ok &= check("[lynx] the page base is a mask, not a multiply",
                "off & (unsigned int)~(GBS_CODE_PAGE - 1)" in lx_big)
    ok &= check("[lynx] the page size stays a power of two (the mask needs it)",
                CodeGenerator.LYNX_CODE_PAGE
                and not (CodeGenerator.LYNX_CODE_PAGE
                         & (CodeGenerator.LYNX_CODE_PAGE - 1)))
    ok &= check("[lynx] the big blob left the resident image",
                ("const uint8_t scripts_CODE[%d]" % big) not in lx_big)
    ok &= check("[lynx] the archive blob is recoverable + page-aligned",
                len(comp.code_generator.streamed_archive) > 0
                and len(comp.code_generator.streamed_archive) % CodeGenerator.LYNX_CODE_PAGE == 0)

    # lynx + `[build] lynx_code_resident`: the BYTECODE blob (the first, the one
    # the interpreter fetches a byte at a time) is pinned in RAM instead. A page
    # refill is ~98,000 ticks and the round-robin scheduler causes about one per
    # thread slice, so this is the speed/MAIN trade a project makes explicitly.
    comp_res = MosaikCompiler()
    lx_res = comp_res.compile_program(
        [("main.mos", MAIN), ("scripts.mos", scripts_mod(big, seam))],
        platform="lynx", lynx_code_resident=True)
    ok &= check("[lynx] lynx_code_resident compiles", not lx_res.startswith("Compilation error:"))
    ok &= check("[lynx] lynx_code_resident keeps the bytecode blob RESIDENT",
                ("const uint8_t scripts_CODE[%d]" % big) in lx_res
                and "return scripts_CODE[off];" in lx_res)
    ok &= check("[lynx] ...and emits no page cache for it at all",
                "gbs_code_byte" not in lx_res)
    ok &= check("[lynx] ...and nothing is archived to the cart",
                len(comp_res.code_generator.streamed_archive) == 0)
    # It is a knob, so OFF must stay exactly what it was.
    ok &= check("[lynx] lynx_code_resident OFF is byte-identical to the default",
                compile_for("lynx", big, seam) == lx_big)

    # Only the FIRST blob is pinned: a STRINGS / song blob is read once per box or
    # per row, so streaming those stays free and keeps their RODATA out of MAIN.
    NL0 = chr(10)
    two_main = NL0.join([
        'module "main" {',
        '    import "platform.video"',
        '    import "scripts"',
        '    import "strings"',
        '    var pc: u16 = 0',
        '    function main() {',
        '        var v: u8 = scripts.fetch(pc) + strings.get(pc)',
        '        video.wait_vblank()',
        '    }',
        '    export main',
        '}',
    ])
    strings_mod = NL0.join([
        'module "strings" {',
        '    import "platform.assets"',
        '    const S: array[u8, %d] = [%s]' % (big, ", ".join(str(i % 256) for i in range(big))),
        '    function get(off: u16) -> u8 {',
        '        return assets.code_byte(S, off)',
        '    }',
        '    export get',
        '}',
    ])
    lx_two = MosaikCompiler().compile_program(
        [("main.mos", two_main), ("scripts.mos", scripts_mod(big, seam)),
         ("strings.mos", strings_mod)],
        platform="lynx", lynx_code_resident=True)
    ok &= check("[lynx] ...the SECOND blob still streams (only the bytecode is pinned)",
                ("const uint8_t strings_S[%d]" % big) not in lx_two
                and ("const uint8_t scripts_CODE[%d]" % big) in lx_two)

    # lynx, THREE big blobs (audio plan Tier D): big SCRIPTS + big STRINGS + a big
    # imported SONG all stream at once, each through its own page reader (slot 0
    # gbs_code_byte / 1 gbs_str_byte / 2 gbs_dat_byte); a 4th stays resident.
    NL = chr(10)
    def blob_mod(name, n):
        vals = ", ".join(str(i % 256) for i in range(n))
        return NL.join([
            'module "%s" {' % name,
            '    import "platform.assets"',
            '    const B: array[u8, %d] = [%s]' % (n, vals),
            '    function get(off: u16) -> u8 {',
            '        return assets.code_byte(B, off)',
            '    }',
            '    export get',
            '}',
        ])
    main4 = NL.join([
        'module "main" {',
        '    import "platform.video"',
        '    import "b1"',
        '    import "b2"',
        '    import "b3"',
        '    import "b4"',
        '    var pc: u16 = 0',
        '    function main() {',
        '        var v: u8 = b1.get(pc) + b2.get(pc) + b3.get(pc) + b4.get(pc)',
        '        video.wait_vblank()',
        '    }',
        '    export main',
        '}',
    ])
    comp3 = MosaikCompiler()
    lx3 = comp3.compile_program(
        [("main.mos", main4)] + [("b%d.mos" % i, blob_mod("b%d" % i, big)) for i in (1, 2, 3, 4)],
        platform="lynx")
    ok &= check("[lynx] 3 big blobs compile", not lx3.startswith("Compilation error:"))
    ok &= check("[lynx] three page readers emitted (code / str / dat)",
                "gbs_code_byte" in lx3 and "gbs_str_byte" in lx3 and "gbs_dat_byte" in lx3)
    ok &= check("[lynx] all three streamed blobs left the resident image",
                all(("const uint8_t b%d_B[%d]" % (i, big)) not in lx3 for i in (1, 2, 3)))
    ok &= check("[lynx] the 4th big blob stays RESIDENT (slots exhausted, degrade-safe)",
                ("const uint8_t b4_B[%d]" % big) in lx3
                and "return b4_B[off];" in lx3)
    ok &= check("[lynx] each reader gets its own archive base",
                comp3.code_generator.streamed_code_base
                != comp3.code_generator.streamed_code2_base
                != comp3.code_generator.streamed_code3_base)

    print("=" * 50)
    print("All bytecode-streaming checks passed" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
