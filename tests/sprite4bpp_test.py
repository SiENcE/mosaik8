#!/usr/bin/env python3
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""4bpp / 16-colour sprite tier -- Phase 2 of the native-feature pipeline
(docs/running-knight-port-plan.md).

Verifies the asset pipeline encodes a >4-colour PNG at packed-nibble 4bpp for
a 4bpp-capable console (Lynx) and luma-quantizes it to 2bpp elsewhere; that
the Lynx sprite engine widens to BPP_4 literals and loads the asset's
16-colour palette into the Mikey pens; and that the same source still compiles
on the 2bpp consoles (generalized-with-limits: load_sprite16 is a no-op there).
"""

import mosaik_assets as M
from mosaik import MosaikCompiler, PLATFORM_CAPS

# A 16x16, 16-colour indexed PNG written to a temp file.
def make_png():
    pal = [(i * 16, 255 - i * 16, (i * 32) & 255) for i in range(16)]
    rows = [[((x // 2) + (y // 2)) % 16 for x in range(16)] for y in range(16)]
    path = os.path.join(tempfile.gettempdir(), "t4bpp16.png")
    M.write_png_indexed(path, 16, 16, rows, pal)
    return path

SRC = '''
module "s" {
    import "platform.video"
    import "graphics.sprite"
    function main() {
        video.enable_lcd()
        sprite.set_data(0, gem_tile_count, gem_tiles)
        palette.load_sprite16(gem_palette16)
        sprite.move(0, 40, 40)
        video.show_sprites()
        loop { video.wait_vblank() }
    }
    export main
}
'''


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("4bpp / 16-colour sprite tier")
    print("=" * 50)
    ok = True
    png = make_png()

    # Capability registry.
    ok &= check("Lynx/PCE native sprite depth is 4bpp, GB family 2bpp",
                PLATFORM_CAPS['lynx']['sprite_bpp'] == 4
                and PLATFORM_CAPS['pce']['sprite_bpp'] == 4
                and PLATFORM_CAPS['gameboy']['sprite_bpp'] == 2
                and PLATFORM_CAPS['nes']['sprite_bpp'] == 2)

    # Asset encoder: 4bpp on a 4bpp target with >4 colours; 2bpp otherwise.
    ok &= check("build_is_4bpp: yes on a 4bpp target with a >4-colour PNG",
                M.build_is_4bpp([png], 4) and not M.build_is_4bpp([png], 2))
    a4 = M.load_assets([png], sprite_bpp=4)
    a2 = M.load_assets([png], sprite_bpp=2)
    ok &= check("4bpp encoding = 32 bytes/tile, 2bpp = 16 bytes/tile",
                a4[0][2] == 4 and len(a4[0][1]) == 4 * 32
                and a2[0][2] == 2 and len(a2[0][1]) == 4 * 16)
    ok &= check("png_palette16 returns the 16 authored colours",
                len(M.png_palette16(png)) == 16)

    # Codegen on the Lynx: native 4bpp engine + Mikey-pen palette load.
    name = M.asset_c_name(png)
    pal16 = M.load_asset_palettes16([png])
    ly = MosaikCompiler().compile(
        SRC.replace("gem", name), platform="lynx",
        assets=a4, asset_palettes16=pal16)
    ok &= check("lynx: sprite engine widens to BPP_4 literals",
                "BPP_4 | TYPE_NORMAL" in ly
                and "8x8 4bpp tile" in ly
                and "*o++ = 0x06;" in ly)
    ok &= check("lynx: 16-colour asset palette emitted + loaded into pens",
                ("const uint16_t %s_palette16[16]" % name) in ly
                and "MIKEY.palette[p] = (uint8_t)(pal[p] >> 8);" in ly
                and "gbs_load_sprite_pal16(%s_palette16)" % name in ly)
    ok &= check("lynx: 4bpp source stride is 32 bytes/tile",
                "data + (uint16_t)i * 32" in ly)

    # Codegen on the Game Boy: same source, 2bpp grey down-tier + no-op load.
    gb = MosaikCompiler().compile(
        SRC.replace("gem", name), platform="gameboy",
        assets=a2, asset_palettes16=pal16)
    ok &= check("gameboy: stays GB 2bpp (no BPP_4 / 4bpp engine)",
                "BPP_4" not in gb and "GB 2bpp" in gb)
    ok &= check("gameboy: load_sprite16 is a no-op but the symbol resolves",
                ("const uint16_t %s_palette16[16]" % name) in gb
                and "void gbs_load_sprite_pal16(const uint16_t *pal) { (void)pal; }" in gb)

    # A program with no 4bpp asset is unaffected on the Lynx (2bpp engine).
    plain = MosaikCompiler().compile(
        '''module "p" { import "platform.video"
           import "graphics.sprite"
           const T: array[u8,16] = [255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255]
           function main() { video.enable_lcd() sprite.set_data(0,1,T)
               sprite.set_tile(0,0) sprite.move(0,8,8) video.show_sprites()
               loop { video.wait_vblank() } }
           export main }''', platform="lynx")
    ok &= check("lynx without a 4bpp asset keeps the 2bpp engine",
                "BPP_2 | TYPE_NORMAL" in plain and "BPP_4" not in plain)

    print()
    if ok:
        print("All 4bpp sprite-tier checks passed")
        return 0
    print("4bpp sprite-tier checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
