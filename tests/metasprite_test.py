#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Metasprites (graphics.sprite sprite.set_meta) -- Phase 1 of the
native-feature pipeline (docs/endless-runner-plan.md).

A metasprite is a W*H block of 8x8 tiles moved/flipped/re-tiled as one unit.
This verifies:

* set_meta lowers to gbs_set_metasprite on every backend (GB family, Lynx,
  PCE), with a meta-aware fan-out in gbs_move_sprite;
* the layer is emitted ONLY when a program actually calls set_meta, so
  ordinary sprite programs stay byte-identical (gating, like the palette /
  Lynx-bkg engines);
* on GBDK, set_tile/set_prop are routed through the gbs_ wrappers only while
  metasprites are in use;
* the capability flag max_metasprite_tiles exists for all consoles.
"""

from mosaik import MosaikCompiler, PLATFORM_CAPS

CONSOLES = ['gameboy', 'gameboy_color', 'analogue_pocket', 'megaduck',
            'sms', 'gamegear', 'nes', 'lynx', 'pce']

META = '''
module "m" {
    import "platform.video"
    import "graphics.sprite"
    const T: array[u8, 64] = [
        255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
        255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
        255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
        255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255]
    function main() {
        video.enable_lcd()
        sprite.set_data(0, 4, T)
        sprite.set_meta(0, 0, 2, 2)
        sprite.set_prop(0, FLIP_X)
        sprite.set_tile(0, 0)
        sprite.move(0, 40, 40)
        video.show_sprites()
        loop { video.wait_vblank() }
    }
    export main
}
'''

# Same program with no set_meta -- must stay on the plain (non-meta) path.
PLAIN = '''
module "m" {
    import "platform.video"
    import "graphics.sprite"
    const T: array[u8, 16] = [255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255]
    function main() {
        video.enable_lcd()
        sprite.set_data(0, 1, T)
        sprite.set_tile(0, 0)
        sprite.set_prop(0, FLIP_X)
        sprite.move(0, 40, 40)
        video.show_sprites()
        loop { video.wait_vblank() }
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
    print("Metasprites (sprite.set_meta)")
    print("=" * 50)
    ok = True

    # The capability flag is present on every console (it drives the
    # oversize-metasprite warning and documents the per-console ceiling).
    ok &= check("max_metasprite_tiles defined for all consoles",
                all('max_metasprite_tiles' in PLATFORM_CAPS[c] for c in CONSOLES))
    ok &= check("Lynx/PCE ceilings exceed the GB-family ceiling",
                PLATFORM_CAPS['lynx']['max_metasprite_tiles'] >
                PLATFORM_CAPS['gameboy']['max_metasprite_tiles'])

    # set_meta lowers + emits the layer on every backend.
    for c in CONSOLES:
        out = compile_for(META, c)
        good = (not out.startswith("Compilation error:")
                and "gbs_set_metasprite(0, 0, 2, 2)" in out   # call lowered
                and "void gbs_set_metasprite(" in out         # helper defined
                and "gbs_meta_w" in out                       # per-slot state
                # meta-aware fan-out in move (flip-aware grid layout)
                and "(prop & FLIP_X)" in out and "* 8" in out)
        ok &= check("%s: set_meta lowers + emits the meta layer" % c, good)

    # GBDK routes set_tile/set_prop through the gbs_ wrappers ONLY when
    # metasprites are used (so they fan out to the reserved child slots).
    gb_meta = compile_for(META, "gameboy")
    ok &= check("gameboy meta: set_tile/set_prop use gbs_ wrappers",
                "gbs_set_sprite_tile(0, 0)" in gb_meta
                and "gbs_set_sprite_prop(0, FLIP_X)" in gb_meta
                and "void gbs_set_sprite_tile(" in gb_meta)

    # Gating: a sprite program without set_meta keeps the plain path and emits
    # none of the metasprite machinery (byte-identical to before this feature).
    for c in ('gameboy', 'lynx', 'pce'):
        plain = compile_for(PLAIN, c)
        clean = ("gbs_set_metasprite" not in plain
                 and "gbs_meta_w" not in plain)
        ok &= check("%s: non-meta program omits the meta layer" % c, clean)
    # ...and on GBDK the plain program keeps the direct macro lowering.
    gb_plain = compile_for(PLAIN, "gameboy")
    ok &= check("gameboy non-meta: set_tile/set_prop stay direct (no wrappers)",
                "set_sprite_tile(0, 0)" in gb_plain
                and "void gbs_set_sprite_tile(" not in gb_plain)

    print()
    if ok:
        print("All metasprite checks passed")
        return 0
    print("Metasprite checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
