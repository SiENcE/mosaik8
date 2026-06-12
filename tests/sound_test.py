#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""platform.sound (Audio Tier-1): sound.beep / sound.stop on every backend.

Verifies that the portable beep lowers to the right tone generator per
console family (GB-family APU incl. the Mega Duck envelope nibble swap,
SMS/GG PSG, NES APU, Lynx Mikey, PCE PSG), that the duration countdown is
hooked into wait_vblank/present on both backends, that `import
"platform.sound"` resolves, and that the registry gates the calls.
"""

from mosaik import MosaikCompiler, PLATFORM_CAPS

BEEP = '''
module "main" {
    import "platform.video"
    import "platform.sound"
    function main() {
        sound.beep(440, 30)
        loop {
            video.wait_vblank()
        }
        sound.stop()
    }
    export main
}
'''


def compile_for(src, platform):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("platform.sound (sound.beep / sound.stop)")
    print("=" * 50)
    ok = True

    ok &= check("every console declares has_sound",
                all('has_sound' in caps for caps in PLATFORM_CAPS.values()))

    outputs = {p: compile_for(BEEP, p) for p in PLATFORM_CAPS}
    ok &= check("beep compiles on every console",
                all(not o.startswith("Compilation error:")
                    for o in outputs.values()))
    ok &= check("calls lower to the gbs_ helpers everywhere",
                all("gbs_sound_beep(440, 30)" in o and "gbs_sound_stop()" in o
                    for o in outputs.values()))

    # GB family: APU pulse channel 2 via the NR*_REG symbols (the library
    # maps them per console, Mega Duck included).
    gb = outputs['gameboy']
    ok &= check("gameboy uses APU channel 2 (NR2x) with trigger",
                "NR52_REG = 0x80" in gb and "NR22_REG = 0xF0" in gb
                and "NR23_REG = (uint8_t)period" in gb
                and "131072UL" in gb)
    ok &= check("megaduck swaps the envelope nibbles (NR22 = 0x0F)",
                "NR22_REG = 0x0F" in outputs['megaduck']
                and "NR22_REG = 0xF0" not in outputs['megaduck'])

    # SMS / Game Gear: SN76489 PSG latch/data writes; GG also opens the
    # stereo pan register.
    sms = outputs['sms']
    ok &= check("sms uses the PSG port (latch + volume)",
                "PSG = (uint8_t)(PSG_LATCH | PSG_CH0" in sms
                and "111861UL" in sms)
    ok &= check("gamegear additionally sets GG_SOUND_PAN",
                "GG_SOUND_PAN = 0xFF" in outputs['gamegear']
                and "GG_SOUND_PAN" not in sms)

    # NES: APU pulse 1 registers.
    nes = outputs['nes']
    ok &= check("nes pokes the APU pulse-1 registers",
                "0x4015) = 0x01" in nes and "0x4000) = 0xBF" in nes)

    # Lynx: Mikey audio channel A square wave.
    lynx = outputs['lynx']
    ok &= check("lynx programs Mikey channel A",
                "MIKEY.channel_a.feedback = 0x01" in lynx
                and "MIKEY.channel_a.control = (uint8_t)(ENABLE_RELOAD | ENABLE_COUNT | sel)" in lynx)

    # PCE: PSG channel 0 with a square waveform.
    pce = outputs['pce']
    ok &= check("pce loads a square waveform into PSG channel 0",
                "GBS_PSG(6) = (i < 16) ? 0x00 : 0x1F" in pce
                and "GBS_PSG(4) = 0x9F" in pce)

    # Duration contract: the countdown runs in wait_vblank (GBDK wraps
    # vsync; cc65 hooks gbs_present).
    ok &= check("GBDK wait_vblank wraps vsync with the countdown",
                "gbs_wait_vblank()" in gb and "vsync();" in gb
                and "--gbs_snd_frames == 0) gbs_sound_stop();" in gb)
    ok &= check("cc65 present hooks the countdown on lynx and pce",
                all("--gbs_snd_frames == 0) gbs_sound_stop();" in o
                    for o in (lynx, pce)))

    print()
    if ok:
        print("All sound checks passed")
        return 0
    print("Sound checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
