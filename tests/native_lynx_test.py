#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Native-extension escape hatch (`native.lynx`) -- Phase 4 of the
native-feature pipeline (docs/running-knight-port-plan.md).

A `native.<console>` module exposes hardware features only that console has;
the calls lower to real hardware on their console and to a no-op everywhere
else, so one source still builds on all nine. This covers native.lynx
(fade_in / fade_out / screen_shake): real Mikey-palette fades + Suzy screen
shake on the Lynx, no-ops on the GB family and the PC Engine. Emitted only
when imported, so non-users stay byte-identical.
"""

from mosaik import MosaikCompiler, stdlib_module_names

SRC = '''
module "f" {
    import "platform.video"
    import "native.lynx"
    const PAL: array[u16, 16] = [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15]
    function main() {
        video.enable_lcd()
        lynx.fade_in(PAL, 30)
        loop {
            lynx.screen_shake(2)
            lynx.fade_out(PAL, 20)
            video.wait_vblank()
        }
    }
    export main
}
'''

NO_IMPORT = '''
module "g" {
    import "platform.video"
    function main() { video.enable_lcd() loop { video.wait_vblank() } }
    export main
}
'''


def compile_for(src, platform):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("Native-extension escape hatch (native.lynx)")
    print("=" * 50)
    ok = True

    ok &= check("native.lynx is a recognised stdlib module",
                "native.lynx" in stdlib_module_names())

    ly = compile_for(SRC, "lynx")
    gb = compile_for(SRC, "gameboy")
    pce = compile_for(SRC, "pce")

    # Calls lower to the same gbs_lynx_* names on every console.
    for label, out in (("lynx", ly), ("gameboy", gb), ("pce", pce)):
        ok &= check("%s: lynx.* calls lower to gbs_lynx_*" % label,
                    not out.startswith("Compilation error:")
                    and "gbs_lynx_fade_in(PAL, 30)" in out
                    and "gbs_lynx_screen_shake(2)" in out
                    and "gbs_lynx_fade_out(PAL, 20)" in out)

    # The Lynx emits real hardware; the others emit no-ops.
    ok &= check("lynx: real Mikey fade + Suzy screen shake",
                "MIKEY.palette[p] = g;" in ly
                and "SUZY.voff = yoff;" in ly
                and "void gbs_lynx_fade_in(const uint16_t *pal, uint8_t frames) {" in ly)
    ok &= check("gameboy: native.lynx is a no-op (no SUZY/MIKEY)",
                "SUZY.voff" not in gb and "MIKEY" not in gb
                and "void gbs_lynx_screen_shake(uint8_t yoff) { (void)yoff; }" in gb)
    ok &= check("pce: native.lynx is a no-op (no SUZY)",
                "SUZY.voff" not in pce
                and "void gbs_lynx_screen_shake(uint8_t yoff) { (void)yoff; }" in pce)

    # Gating: a program that doesn't import native.lynx emits none of it.
    for plat in ("lynx", "gameboy", "pce"):
        out = compile_for(NO_IMPORT, plat)
        ok &= check("%s: no import -> no gbs_lynx_* emitted" % plat,
                    "gbs_lynx_" not in out)

    print()
    if ok:
        print("All native.lynx checks passed")
        return 0
    print("native.lynx checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
