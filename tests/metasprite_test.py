#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Metasprites (graphics.sprite sprite.set_meta) -- Phase 1 of the
native-feature pipeline.

A metasprite is a W*H block of 8x8 tiles moved/flipped/re-tiled as one unit.
This verifies:

* set_meta lowers to gbs_set_metasprite on every backend (GB family, Lynx,
  PCE), with a meta-aware fan-out in gbs_move_sprite;
* the layer is emitted ONLY when a program actually calls set_meta, so
  ordinary sprite programs stay byte-identical (gating, like the palette /
  Lynx-bkg engines);
* on GBDK, set_tile/set_prop are routed through the gbs_ wrappers only while
  metasprites are in use;
* the capability flag max_metasprite_tiles exists for all consoles;
* SPARSE frames (sprite.set_meta_mask): a per-column BLANK mask parks a
  column's objects and consumes NO tile, and the whole masked path is emitted
  only when a program calls it, so every other program stays byte-identical.
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


# A SPARSE metasprite: set_meta_mask's blank columns park their objects and
# take no tile from the frame's block. Keep a plain set_meta call TOO: the
# helpers are emitted per TU by USE now, and the "set_meta clears a stale
# mask" wrapper only exists in a TU that calls set_meta at all.
MASKED = META.replace("sprite.set_meta(0, 0, 2, 2)",
                      "sprite.set_meta_mask(0, 0, 3, 2, 2)\n"
                      "        sprite.set_meta(0, 0, 2, 2)")


def check_meta_mask(check):
    """sprite.set_meta_mask on every backend + its gating."""
    ok = True
    for c in CONSOLES:
        out = compile_for(MASKED, c)
        good = ("gbs_set_metasprite_mask(0, 0, 3, 2, 2)" in out
                and "void gbs_set_metasprite_mask(" in out
                and "gbs_meta_msk" in out)
        # set_meta itself must CLEAR a stale mask: on the cc65 consoles it
        # is the masked call with an empty mask; on the GBDK consoles it is
        # the dense set fan, a flat body that clears the base's mask itself
        # (2026-09-05, the empty-mask upload priced like a sparse one).
        if c in ("lynx", "pce"):
            good = good and "gbs_set_metasprite_mask(base, tile, w, h, 0)" in out
        else:
            i = out.index("static void gbs_set_metasprite(uint8_t base, uint8_t tile, uint8_t w, uint8_t h) {")
            body = out[i:out.index("\n}", i)]
            good = good and ("gbs_meta_msk[base] = 0;" in body
                             and "gbs_set_metasprite_mask(" not in body
                             and "1 << c" not in body)
        ok &= check("%s: set_meta_mask lowers + emits the mask table" % c, good)
    # Gating: a metasprite program that never masks keeps the old prelude and
    # never allocates the per-base mask table (byte-identical).
    for c in CONSOLES:
        plain = compile_for(META, c)
        ok &= check("%s: unmasked metasprite program omits the mask table" % c,
                    "gbs_meta_msk" not in plain
                    and "gbs_set_metasprite_mask" not in plain)
    # The GB move loop must keep the masked children PARKED - re-laying them
    # with the rest is what put a blank column back on the scanline.
    gb = compile_for(MASKED, "gameboy")
    ok &= check("gameboy: the move fan has a masked variant that skips columns",
                "gbs_meta_msk[nb]" in gb and "(m & 1)" in gb)
    return ok


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
                # Grid layout. On GBDK the child loop WALKS the cells at a
                # hoisted 8 px pitch (that loop is the frame's biggest single
                # cost -- see the emitter's note); the cc65 backends still
                # compute the offset per cell.
                and ("xs = 8" if PLATFORM_CAPS[c]['framework'] == 'gbdk'
                     else "* 8") in out)
        # The grid layout reverses cells for a flip ONLY where the hardware can
        # mirror the tiles; SMS / Game Gear (no sprite flip) must keep the
        # normal layout (reversing without mirroring garbles the block).
        if PLATFORM_CAPS[c].get('has_sprite_flip', True):
            good = good and "(prop & FLIP_X)" in out
        else:
            good = (good and "(prop & FLIP_X)" not in out
                    and "no hardware sprite flip on this console" in out)
        ok &= check("%s: set_meta lowers + emits the meta layer" % c, good)

    # GBDK routes set_tile/set_prop through the gbs_ wrappers ONLY when
    # metasprites are used (so they fan out to the reserved child slots).
    gb_meta = compile_for(META, "gameboy")
    ok &= check("gameboy meta: set_tile/set_prop use gbs_ wrappers",
                "gbs_set_sprite_tile(0, 0)" in gb_meta
                and "gbs_set_sprite_prop(0, FLIP_X)" in gb_meta
                and "void gbs_set_sprite_tile(" in gb_meta)

    ok &= check_meta_mask(check)

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
