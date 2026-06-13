#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Sound: portable SFX set + native Lynx jingle -- Phase 5 of the
native-feature pipeline (docs/running-knight-port-plan.md).

`sound.sfx(id)` is a small fixed bank of one-shot effects (SFX_COIN/HURT/...)
over the single beep channel -- portable on every console, replacing per-game
named SFX banks. The native Lynx jingle (`lynx.jingle`, via native.lynx) adds
a *second* voice on Mikey channel B -- real on the Lynx, a no-op elsewhere.
Audio is not screen-verifiable, so this checks lowering + gating; the build
matrix (run_all --samples) proves the emitted C links.
"""

from mosaik import MosaikCompiler

CONSOLES = ['gameboy', 'gameboy_color', 'analogue_pocket', 'megaduck',
            'sms', 'gamegear', 'nes', 'lynx', 'pce']

SRC = '''
module "s" {
    import "platform.video"
    import "platform.sound"
    import "native.lynx"
    const TUNE: array[u16, 4] = [880, 988, 1047, 1319]
    function main() {
        video.enable_lcd()
        sound.sfx(SFX_COIN)
        sound.sfx(SFX_HURT)
        lynx.jingle(TUNE, 4)
        loop { video.wait_vblank() }
    }
    export main
}
'''

NO_SFX = '''
module "n" {
    import "platform.video"
    import "platform.sound"
    function main() { video.enable_lcd() sound.beep(440, 8)
        loop { video.wait_vblank() } }
    export main
}
'''


def compile_for(src, platform):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("Sound: SFX set + native Lynx jingle")
    print("=" * 50)
    ok = True

    # sound.sfx + the SFX_* constants lower on every console.
    for c in CONSOLES:
        out = compile_for(SRC, c)
        good = (not out.startswith("Compilation error:")
                and "gbs_sound_sfx(SFX_COIN)" in out
                and "#define SFX_COIN 0" in out
                and "#define SFX_HURT 1" in out
                and "void gbs_sound_sfx(uint8_t id) {" in out
                and "gbs_sound_beep(gbs_sfx_freq[id], gbs_sfx_frames[id])" in out)
        ok &= check("%s: sound.sfx + SFX_* ids lower" % c, good)

    # The native Lynx jingle is a real second voice on the Lynx, no-op elsewhere.
    ly = compile_for(SRC, "lynx")
    ok &= check("lynx: jingle drives Mikey channel B (a second voice)",
                "gbs_lynx_jingle(TUNE, 4)" in ly
                and "MIKEY.channel_b.control = (uint8_t)(ENABLE_RELOAD" in ly)
    for c in ("gameboy", "pce"):
        out = compile_for(SRC, c)
        ok &= check("%s: jingle is a no-op (no channel B)" % c,
                    "gbs_lynx_jingle(TUNE, 4)" in out
                    and "MIKEY.channel_b" not in out)

    # Gating: a program that doesn't use sound.sfx emits none of the bank.
    for c in ("gameboy", "lynx"):
        out = compile_for(NO_SFX, c)
        ok &= check("%s: no sound.sfx -> no SFX bank emitted" % c,
                    "gbs_sound_sfx" not in out and "SFX_COIN" not in out)

    print()
    if ok:
        print("All sound SFX / jingle checks passed")
        return 0
    print("Sound SFX / jingle checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
