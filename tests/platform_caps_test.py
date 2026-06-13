#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Platform capability registry (PLATFORM_CAPS).

Verifies that the per-console capability registry is complete and in sync with
the build tool's target registry, that capability-gated stdlib calls raise the
clear "not supported on target" diagnostic on consoles that lack them (on the
GBDK backend too, not just cc65), that the GB-only hardware-register constants
are gated, and that the portability contract (SCREEN_* geometry constants and
screen-pixel sprite coordinates) is emitted on every backend.
"""

from mosaik import (MosaikCompiler, PLATFORM_CAPS,
                               PLATFORM_FRAMEWORK, platform_caps)

WINDOW = '''
module "main" {
    import "graphics.window"
    function main() { window.move(7, 100) }
    export main
}
'''

REG = '''
module "main" {
    import "platform.hardware"
    function main() { hw.write(REG_BGP, 0xE4) }
    export main
}
'''

SPRITE = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
    const TILE: array[u8, 16] = [255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255]
    function main() {
        sprite.set_data(0, 1, TILE)
        sprite.move(0, 10, 20)
        video.show_sprites()
    }
    export main
}
'''

GEOMETRY = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    const MAX_X: u8 = SCREEN_WIDTH - 8
    function main() {
        text.print_number(0, 0, MAX_X)
        text.print_number(0, 1, SCREEN_ROWS)
    }
    export main
}
'''

CAP_KEYS = {'framework', 'has_sprites', 'has_bkg', 'has_window', 'has_draw',
            'has_gb_regs', 'has_sound', 'has_banking',
            'sprite_bpp', 'max_metasprite_tiles'}


def compile_for(src, platform):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def is_unsupported_error(out, platform):
    return (out.startswith("Compilation error:")
            and "not supported on target '%s'" % platform in out)


def main():
    print("Platform capability registry")
    print("=" * 50)
    ok = True

    # Registry shape: every console carries every capability key.
    ok &= check("every console has every capability key",
                all(CAP_KEYS <= set(caps) for caps in PLATFORM_CAPS.values()))
    ok &= check("PLATFORM_FRAMEWORK derives from the registry",
                PLATFORM_FRAMEWORK == {n: c['framework']
                                       for n, c in PLATFORM_CAPS.items()})
    ok &= check("aliases resolve to a caps entry",
                platform_caps('gbc')['has_window']
                and platform_caps('atari_lynx')['has_draw'])

    # Parity with the build tool's target registry (also asserted at import
    # in mosaik8.py; checked here so a broken assert is a test failure too).
    from mosaik8 import PLATFORM_TARGETS
    ok &= check("PLATFORM_TARGETS and PLATFORM_CAPS list the same consoles",
                set(PLATFORM_TARGETS) == set(PLATFORM_CAPS))
    ok &= check("both registries agree on each console's framework",
                all(PLATFORM_TARGETS[p]['framework'] == PLATFORM_CAPS[p]['framework']
                    for p in PLATFORM_TARGETS))

    # Window layer: GB family only. On NES/SMS this used to die as an SDCC
    # link error; now it is the same clear diagnostic cc65 consoles give.
    for plat in ('nes', 'sms', 'gamegear', 'lynx', 'pce'):
        ok &= check("window.* unsupported on %s gives clear error" % plat,
                    is_unsupported_error(compile_for(WINDOW, plat), plat))
    for plat in ('gameboy', 'gameboy_color', 'megaduck'):
        ok &= check("window.* still works on %s" % plat,
                    "move_win(7, 100)" in compile_for(WINDOW, plat))

    # GB hardware-register constants: gated to consoles that have them.
    for plat in ('sms', 'nes', 'lynx'):
        out = compile_for(REG, plat)
        ok &= check("REG_BGP on %s gives clear error" % plat,
                    out.startswith("Compilation error:")
                    and "Game Boy-specific" in out)
    gb = compile_for(REG, 'gameboy')
    ok &= check("REG_BGP works on gameboy",
                "#define REG_BGP" in gb and "gbs_hw_write(REG_BGP" in gb)
    sms_text = compile_for(GEOMETRY, 'sms')
    ok &= check("sms prelude omits the GB register defines",
                "#define REG_BGP" not in sms_text)

    # Screen geometry constants on every backend.
    gb_geo = compile_for(GEOMETRY, 'gameboy')
    ok &= check("GBDK prelude defines SCREEN_* from DEVICE_* macros",
                "#define SCREEN_WIDTH  DEVICE_SCREEN_PX_WIDTH" in gb_geo
                and "#define SCREEN_ROWS   DEVICE_SCREEN_HEIGHT" in gb_geo
                and "#define MAX_X ((SCREEN_WIDTH - 8))" in gb_geo)
    ly_geo = compile_for(GEOMETRY, 'lynx')
    ok &= check("lynx prelude defines SCREEN_* (160x102, 20x12 cells)",
                "#define SCREEN_WIDTH  160" in ly_geo
                and "#define SCREEN_HEIGHT 102" in ly_geo
                and "#define SCREEN_COLS   20" in ly_geo
                and "#define SCREEN_ROWS   12" in ly_geo)
    pce_geo = compile_for(GEOMETRY, 'pce')
    ok &= check("pce prelude defines SCREEN_* (256x224, 32x28 cells)",
                "#define SCREEN_WIDTH  256" in pce_geo
                and "#define SCREEN_COLS   32" in pce_geo)

    # Sprite coordinate contract: screen pixels on both backends.
    gb_spr = compile_for(SPRITE, 'gameboy')
    ok &= check("GBDK sprite.move adds the per-console hardware offset",
                "gbs_move_sprite(0, 10, 20)" in gb_spr
                and "DEVICE_SPRITE_PX_OFFSET_X" in gb_spr
                and "DEVICE_SPRITE_PX_OFFSET_Y" in gb_spr)
    ly_spr = compile_for(SPRITE, 'lynx')
    ok &= check("lynx sprite.move uses screen coords directly (no GB offset)",
                "gbs_scb[nb].hpos = x;" in ly_spr
                and "gbs_scb[nb].vpos = y;" in ly_spr)

    print()
    if ok:
        print("All platform capability checks passed")
        return 0
    print("Platform capability checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
