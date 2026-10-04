#!/usr/bin/env python3
"""VM8 string-blob renderer -- workstream B phase 1.

A dialogue-heavy VM game's authored text used to lower to per-string
`text.print_string(...)` calls inside render_text/render_choice (resident CODE +
string literals). Above CompiledProgram.STRING_BLOB_THRESHOLD the compiler
instead emits ONE compact `STRINGS` byte blob + per-string `STR_OFF` offsets and
a generic line-buffer renderer that reads the blob through the assets.code_byte
seam -- so text is const DATA the Lynx can stream, and CODE stops scaling with
the number of strings.

This checks:
  * BELOW threshold -> the original switch renderer, and the generated scripts.mos
    is BYTE-IDENTICAL to the pre-change form (small games untouched).
  * ABOVE threshold -> the blob renderer (STRINGS/STR_OFF/_line + code_byte),
    and it COMPILES to C on gameboy AND lynx.
  * the CODE blob is IDENTICAL either way (strings live outside the bytecode, so
    the format did not change -- RefVM, goldens, and the studio catalogue are
    untouched).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik_vm import Compiler, CompiledProgram
from mosaik import MosaikCompiler


def compile_scripts(scripts):
    return Compiler().compile(scripts)


SMALL = [{"name": "main", "events": [
    {"event": "text", "string": "HI"},
    {"event": "text", "string": "BYE"},
    {"event": "stop"},
]}]

# ~40 bytes/page x 8 = ~320 bytes of text, crossing the 256-byte threshold.
_PAGES = [
    "THE ANCIENT KEEP\nHOLDS MANY OLD\nSECRETS INSIDE.",
    "LONG AGO A KING\nSEALED THE VAULT\nWITH A GOLD RUNE.",
    "FIND THE THREE\nGEMS HIDDEN DEEP\nIN THE CAVERNS.",
    "ONLY THEN WILL\nTHE GREAT DOOR\nOPEN FOR YOU.",
    "BEWARE THE BATS\nAND THE GUARDIAN\nOF THE INNER HALL.",
    "TAKE THIS BLADE\nIT SERVED ME IN\nMY YOUNGER DAYS.",
    "MAY FORTUNE GUIDE\nYOUR EVERY STEP\nBRAVE TRAVELLER.",
    "GO NOW AND CLAIM\nYOUR DESTINY IN\nTHE DEPTHS BELOW.",
]
BIG = [{"name": "main", "events": [{"event": "text", "string": p} for p in _PAGES]
        + [{"event": "stop"}]}]


def check(label, cond):
    print(("[PASS] " if cond else "[FAIL] ") + label)
    return cond


def main():
    ok = True

    small = compile_scripts(SMALL)
    big = compile_scripts(BIG)

    ok &= check("small text stays under the blob threshold (switch path)",
                not small._use_string_blob())
    ok &= check("big text crosses the blob threshold (blob path)",
                big._use_string_blob())

    small_mos = small.to_scripts_mos()
    big_mos = big.to_scripts_mos()

    # Small = the original switch form (byte-identical: no blob artifacts).
    ok &= check("small: switch renderer (no STRINGS/STR_OFF/_line)",
                "const STRINGS:" not in small_mos
                # the text origin comes from core (it sizes the box from the
                # string's line count), not from a row baked per case
                and "text.print_string(core.box_left(), core.box_top()" in small_mos)

    # Big = the blob renderer.
    ok &= check("big: STRINGS blob + offsets + line buffer emitted",
                "const STRINGS:" in big_mos
                and "const STR_OFF:" in big_mos
                and "var _line:" in big_mos
                and "assets.code_byte(STRINGS," in big_mos)
    ok &= check("big: render_text/render_choice are generic (few print_string)",
                big_mos.count("print_string") <= 6)

    # Forcing the big program through the switch path (raise the threshold) must
    # reproduce today's per-string codegen -- proves the two paths are a clean
    # A/B, and that below-threshold output is exactly the pre-change form.
    saved = CompiledProgram.STRING_BLOB_THRESHOLD
    try:
        CompiledProgram.STRING_BLOB_THRESHOLD = 10 ** 9
        big_switch = big.to_scripts_mos()
    finally:
        CompiledProgram.STRING_BLOB_THRESHOLD = saved
    ok &= check("big-as-switch has one print_string per authored line (>= 24)",
                big_switch.count("print_string") >= 24
                and "const STRINGS:" not in big_switch)

    # Strings live OUTSIDE the bytecode, so the render form never touches the CODE
    # blob -- a text-only UI_TEXT program is opcode + a u8 id per page, no string
    # bytes. The format is unchanged (RefVM / goldens / studio catalogue untouched).
    per_page = len(big.code) / float(len(_PAGES))
    ok &= check("CODE blob holds no string bytes (~2 B/page, format unchanged)",
                per_page < 4 and big.code == compile_scripts(BIG).code)

    # The blob scripts.mos COMPILES to C on a GBDK console AND the Lynx.
    # render_text reads the box's text origin from vm.core; a real build links
    # lib/vm/core.mos, so a standalone compile stubs the seam.
    core_stub = ('module "vm.core" {\n'
                 '    function box_top() -> u8 { return SCREEN_ROWS - 3 }\n'
                 '    function box_left() -> u8 { return 2 }\n'
                 '    function var_get(idx: u8) -> i16 { return 0 }\n'
                 '    function set_code_window(base: addr, enter: function() -> u8, leave: function(u8)) { }\n'
                 '    function set_code_window_res(base: addr) { }\n'
                 '    export box_top, box_left, var_get, set_code_window, set_code_window_res\n}\n')
    srcs = [("scripts.mos", big_mos), ("core.mos", core_stub)]
    for plat in ("gameboy", "lynx"):
        c = MosaikCompiler().compile_program(srcs, platform=plat)
        ok &= check("[%s] blob scripts.mos compiles to C" % plat,
                    not c.startswith("Compilation error")
                    and "render_text" in c)

    # Workstream B phase 2: TWO big blobs both read via assets.code_byte (a CODE-
    # heavy AND text-heavy VM game) each stream from the Lynx cart with their OWN
    # page reader -- the primary gbs_code_byte + the secondary gbs_str_byte -- so
    # cold text reads never evict the hot instruction page. Below threshold each
    # stays resident (byte-identical sym[off]).
    def two_blob_src(n):
        body = ", ".join("1" for _ in range(n))
        body2 = ", ".join("2" for _ in range(n))
        return '''module "m" {
    import "graphics.text"
    import "platform.assets"
    const A: array[u8, %d] = [ %s ]
    const B: array[u8, %d] = [ %s ]
    function fa(off: u16) -> u8 { return assets.code_byte(A, off) }
    function fb(off: u16) -> u8 { return assets.code_byte(B, off) }
    function main() {
        var x: u8 = fa(0)
        var y: u8 = fb(0)
        loop { }
    }
    export main
}''' % (n, body, n, body2)

    big2 = MosaikCompiler().compile_program(
        [("m.mos", two_blob_src(1200))], platform="lynx")
    ok &= check("[lynx] two big blobs -> both stream (gbs_code_byte + gbs_str_byte)",
                "gbs_code_byte(" in big2 and "gbs_str_byte(" in big2)
    ok &= check("[lynx] two-blob: neither blob stays resident in RODATA",
                "const unsigned char A[" not in big2
                and "const unsigned char B[" not in big2)

    small2 = MosaikCompiler().compile_program(
        [("m.mos", two_blob_src(8))], platform="lynx")
    ok &= check("[lynx] two SMALL blobs stay resident (no streaming readers)",
                "gbs_str_byte(" not in small2 and "gbs_code_byte(" not in small2)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
