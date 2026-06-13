#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""graphics.palette: the 4-color GB-model palette slots on every console.

Verifies that palette.rgb / set_bkg / set_sprite lower to the right color
hardware per console (DMG shade registers, CGB palette RAM with the
Analogue Pocket's DMG mirror, SMS/GG CRAM, NES PPU palettes, the Lynx
16-pen partition, PCE VCE color RAM), that sprite.set_palette routes to
OAM attributes / SCB penpals / SATB bits, that bkg.set_palette is gated by
has_tile_palettes, that the registry carries the new capability fields, and
that programs which do not import graphics.palette keep palette-free output.
"""

from mosaik import MosaikCompiler, PLATFORM_CAPS

PALETTE = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "graphics.palette"
    const RING: array[u8, 16] = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]
    function main() {
        sprite.set_data(0, 1, RING)
        sprite.set_tile(0, 0)
        sprite.set_palette(0, 1)
        palette.set_bkg(0, palette.rgb(16, 24, 64), palette.rgb(96, 110, 200), palette.rgb(48, 56, 120), palette.rgb(235, 240, 255))
        palette.set_sprite(1, palette.rgb(0, 0, 0), palette.rgb(255, 200, 120), palette.rgb(220, 90, 30), palette.rgb(255, 240, 200))
        video.enable_lcd()
        loop {
            video.wait_vblank()
        }
    }
    export main
}
'''

BKG_PALETTE = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    import "graphics.palette"
    const T: array[u8, 16] = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]
    const MAP: array[u8, 4] = [0, 0, 0, 0]
    function main() {
        bkg.set_data(0, 1, T)
        bkg.set_tiles(0, 0, 2, 2, MAP)
        palette.set_bkg(1, palette.rgb(10, 20, 30), palette.rgb(40, 50, 60), palette.rgb(70, 80, 90), palette.rgb(100, 110, 120))
        bkg.set_palette(0, 0, 2, 2, 1)
        video.enable_lcd()
        loop {
            video.wait_vblank()
        }
    }
    export main
}
'''

NO_PALETTE = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
    const RING: array[u8, 16] = [1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]
    function main() {
        sprite.set_data(0, 1, RING)
        sprite.set_tile(0, 0)
        video.enable_lcd()
        loop {
            video.wait_vblank()
        }
    }
    export main
}
'''


ASSET_SRC = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "graphics.palette"
    function main() {
        sprite.set_data(0, ball_tile_count, ball_tiles)
        sprite.set_tile(0, 0)
        palette.load_sprite(0, ball_palette)
        palette.load_bkg(0, ball_palette)
        video.enable_lcd()
        loop {
            video.wait_vblank()
        }
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
    print("graphics.palette (palette.rgb / set_bkg / set_sprite / set_palette)")
    print("=" * 50)
    ok = True

    ok &= check("every console declares the color capability fields",
                all('has_color' in c and 'bkg_palettes' in c
                    and 'spr_palettes' in c and 'has_tile_palettes' in c
                    for c in PLATFORM_CAPS.values()))
    ok &= check("slot 0 is the portable guarantee on every console",
                all(c['bkg_palettes'] >= 1 and c['spr_palettes'] >= 1
                    for c in PLATFORM_CAPS.values()))

    outputs = {p: compile_for(PALETTE, p) for p in PLATFORM_CAPS}
    ok &= check("the palette program compiles on every console",
                all(not o.startswith("Compilation error:")
                    for o in outputs.values()))
    ok &= check("calls lower to the gbs_ helpers everywhere",
                all("gbs_set_bkg_palette(0, gbs_rgb(16, 24, 64)" in o
                    and "gbs_set_spr_palette(1, gbs_rgb(0, 0, 0)" in o
                    and "gbs_sprite_palette(0, 1)" in o
                    for o in outputs.values()))

    # 4-grey consoles: luma quantization + the DMG shade registers.
    gb = outputs['gameboy']
    ok &= check("gameboy quantizes to shades and packs BGP/OBP0/OBP1",
                "* 3 + (uint16_t)g * 6 + b) / 10" in gb
                and "BGP_REG = DMG_PALETTE(c0, c1, c2, c3)" in gb
                and "OBP1_REG = DMG_PALETTE" in gb
                and "RGB8" not in gb)
    ok &= check("megaduck uses the same DMG path (port-remapped registers)",
                "BGP_REG = DMG_PALETTE" in outputs['megaduck'])

    # CGB class: palette RAM via cgb.h; the Pocket adds the DMG mirror.
    gbc = outputs['gameboy_color']
    ok &= check("gameboy_color writes CGB palette RAM via RGB8",
                "return RGB8(r, g, b)" in gbc
                and "set_bkg_palette(slot & 7, 1, buf)" in gbc
                and "set_sprite_palette(slot & 7, 1, buf)" in gbc
                and "gbs_pal_shade" not in gbc)
    ap = outputs['analogue_pocket']
    ok &= check("analogue_pocket mirrors slot 0/1 into the DMG registers",
                "set_bkg_palette(slot & 7, 1, buf)" in ap
                and "gbs_pal_shade" in ap and "OBP1_REG = DMG_PALETTE" in ap)

    # SMS / Game Gear: CRAM entries 0-3 where the 2bpp compat layer maps
    # GB pixel values; no per-sprite palette select.
    sms = outputs['sms']
    ok &= check("sms writes CRAM entries 0-3 (slot 0 only)",
                "set_bkg_palette_entry(0, 0, c0)" in sms
                and "set_sprite_palette_entry(0, 3, c3)" in sms
                and "if (slot != 0) return;" in sms)
    ok &= check("sms/gg sprite.set_palette is an honest no-op",
                "(void)nb; (void)slot;" in sms
                and "(void)nb; (void)slot;" in outputs['gamegear'])

    # NES: PPU palettes; RGB8 (the RGB_TO_NES chain) inside gbs_rgb only.
    nes = outputs['nes']
    ok &= check("nes writes PPU palettes through one gbs_rgb function",
                "return RGB8(r, g, b)" in nes and nes.count("RGB8") == 2
                and "set_bkg_palette_entry(slot, 0, (palette_color_t)c0)" in nes
                and "& ~0x03) | (slot & 0x03)" in nes)

    # Lynx: the 16-pen partition (pens 1-12 sprites, 13-15 + 0 background).
    lynx = outputs['lynx']
    ok &= check("lynx partitions the Mikey palette into pen groups",
                "MIKEY.palette[pen] = (uint8_t)(c >> 8)" in lynx
                and "{0x01, 0x23}, {0x04, 0x56}, {0x07, 0x89}, {0x0A, 0xBC}" in lynx
                and "gbs_pal_set_pen((uint8_t)(3 * slot + 1), c1)" in lynx)
    ok &= check("lynx sprite slots default to the slot-0 pens",
                "gbs_scb[s].penpal[0] = gbs_pal_penpal[0][0]" in lynx
                and "gbs_scb[nb].penpal[0] = gbs_pal_penpal[slot][0]" in lynx)
    ok &= check("lynx text + clear follow the bkg palette pens (15/0)",
                "tgi_setcolor(15);" in lynx and "tgi_setcolor(0);" in lynx
                and "gbs_pal_init();" in lynx.split("gbs_print_string")[1][:200])

    # PCE: VCE color RAM + SATB attribute bits.
    pce = outputs['pce']
    ok &= check("pce writes VCE sprite palettes, the backdrop and the text ink",
                "gbs_vce((uint16_t)(0x100 + ((uint16_t)slot << 4)) + 1, c1)" in pce
                and "if (slot == 0) { gbs_vce(0x000, c0); gbs_vce(0x011, c3); }" in pce
                and "(uint16_t)((slot + 2) << 4) + 1, c1" in pce)
    ok &= check("pce sprite.set_palette sets SATB bits 0-3 and set_prop keeps them",
                "& ~0x000Fu) | (slot & 3)" in pce
                and "(gbs_satb[(uint16_t)nb * 4 + 3] & 0x000Fu)" in pce)

    # bkg.set_palette: per-tile palettes only where the hardware has them.
    bkg_out = {p: compile_for(BKG_PALETTE, p) for p in PLATFORM_CAPS}
    with_tiles = [p for p, c in PLATFORM_CAPS.items() if c['has_tile_palettes']]
    without = [p for p, c in PLATFORM_CAPS.items() if not c['has_tile_palettes']]
    ok &= check("bkg.set_palette compiles on %s" % ", ".join(sorted(with_tiles)),
                all(not bkg_out[p].startswith("Compilation error:")
                    for p in with_tiles))
    ok &= check("bkg.set_palette is a clear error elsewhere",
                all(bkg_out[p].startswith("Compilation error:")
                    and "not supported on target" in bkg_out[p]
                    for p in without))
    ok &= check("gbc fills the VBK attribute map",
                "VBK_REG = VBK_ATTRIBUTES;" in bkg_out['gameboy_color']
                and "set_bkg_tile_xy" in bkg_out['gameboy_color'])
    ok &= check("nes fills 16x16 attribute cells",
                "set_bkg_attributes_nes16x16(cx, cy, 1, 1, &a)" in bkg_out['nes'])
    ok &= check("pce rewrites BAT palette bits and set_tiles preserves them",
                "gbs_bkg_pal[idx >> 1]" in bkg_out['pce']
                and "(uint16_t)(pal + 2) << 12" in bkg_out['pce'])

    # Asset pipeline: an indexed PNG's authored palette is emitted as a
    # native-color `<name>_palette[4]` array (per-console conversion at
    # build time), loadable with palette.load_bkg / load_sprite.
    import tempfile
    from mosaik_assets import (write_png_indexed, load_assets,
                               load_asset_palettes)
    tmp = tempfile.mkdtemp()
    png = os.path.join(tmp, "ball.png")
    authored = [(16, 24, 64), (255, 200, 120), (220, 90, 30), (255, 240, 200)]
    write_png_indexed(png, 8, 8,
                      [[(x + y) % 4 for x in range(8)] for y in range(8)],
                      authored)
    assets = load_assets([png])
    palettes = load_asset_palettes([png])
    ok &= check("indexed PNGs carry their authored palette",
                palettes == [("ball", authored)])
    asset_out = {p: MosaikCompiler().compile(
                     ASSET_SRC.strip(), platform=p,
                     assets=assets, asset_palettes=palettes)
                 for p in PLATFORM_CAPS}
    ok &= check("the asset-palette program compiles on every console",
                all(not o.startswith("Compilation error:")
                    for o in asset_out.values()))
    ok &= check("color consoles fold the palette through the port's RGB8",
                "const uint16_t ball_palette[4] = { RGB8(16, 24, 64)," in
                asset_out['gameboy_color']
                and "RGB8(16, 24, 64)" in asset_out['nes'])
    ok &= check("4-grey consoles precompute shades, cc65 packs native words",
                "ball_palette[4] = { 3, 1, 2, 0 }" in asset_out['gameboy']
                and "ball_palette[4] = { 0x141, 0xC7F" in asset_out['lynx']
                and "ball_palette[4] = { 0x002, 0x1BB" in asset_out['pce'])
    ok &= check("the palette loads through gbs_load_*_palette",
                "gbs_load_spr_palette(0, ball_palette)" in asset_out['lynx']
                and "gbs_load_bkg_palette(0, ball_palette)" in asset_out['sms'])

    # No graphics.palette import -> no palette prelude, byte-identical output.
    ok &= check("programs without the import stay palette-free",
                all("gbs_rgb" not in compile_for(NO_PALETTE, p)
                    for p in PLATFORM_CAPS))

    print()
    if ok:
        print("All palette checks passed")
        return 0
    print("Palette checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
