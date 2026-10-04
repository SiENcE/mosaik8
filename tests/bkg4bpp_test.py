#!/usr/bin/env python3
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""4bpp / 16-colour BACKGROUND tier.

The background mirror of the sprite 4bpp tier. Verifies:
- the `bkg_bpp` capability + BKG_4BPP_ENGINE gate (PCE enabled = Stage 1);
- the tileset encoders (packed-nibble 4bpp, 16-colour palette, 5-5-5 packing);
- the scene transpiler forks TILESET by platform when the tileset is >4-colour
  and emits BKG_PALETTE16, and stays byte-identical for a <=4-colour tileset;
- the PCE bkg engine fills all four VDC planes + loads the VCE bkg palette;
- the same source still compiles on a 2bpp console (load_bkg16 a no-op there).
"""

import mosaik_assets as M
import mosaik_scenes.loaders as L
from mosaik_scenes.transpile import transpile
from mosaik import MosaikCompiler, PLATFORM_CAPS
from mosaik8_targets import target_bkg_bpp


def _world(colors, lynx16=False):
    """A one-scene world whose tileset PNG has `colors` colours (optionally
    opting the Lynx into the 4bpp bkg tier); returns the scenes module text."""
    d = tempfile.mkdtemp()
    pal = [(i * 16, 255 - i * 16, (i * 8) & 0xFF) for i in range(colors)]
    idx = [[(x + y) % colors for x in range(16)] for y in range(16)]
    M.write_png_indexed(os.path.join(d, "t.png"), 16, 16, idx, pal)
    with open(os.path.join(d, "world.toml"), "w") as f:
        if lynx16:
            f.write('[world]\nlynx_bkg16 = true\n\n')
        f.write('[tileset]\npng = "t.png"\n\n[[scene]]\nname="room"\n'
                'map_w=2\nmap_h=2\nmap=[0,1,2,3]\n')
    world, base = L.load_world(os.path.join(d, "world.toml"))
    return transpile(world, base)


APP = '''
module "app" {
    import "scenes"
    import "graphics.bkg"
    import "graphics.palette"
    import "platform.video"
    function main() {
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        bkg.set_tiles(0, 0, scenes.MAP_W, scenes.MAP_H, scenes.TILESET)
        palette.load_bkg16(scenes.BKG_PALETTE16)
        video.wait_vblank()
    }
    export main
}
'''


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("4bpp / 16-colour background tier")
    print("=" * 50)
    ok = True

    # --- capabilities ---
    ok &= check("bkg_bpp==4 on sms/gamegear/lynx/pce",
                all(PLATFORM_CAPS[p]['bkg_bpp'] == 4
                    for p in ('sms', 'gamegear', 'lynx', 'pce')))
    ok &= check("bkg_bpp==2 on gameboy/nes",
                PLATFORM_CAPS['gameboy']['bkg_bpp'] == 2
                and PLATFORM_CAPS['nes']['bkg_bpp'] == 2)
    ok &= check("target_bkg_bpp: pce/sms/gamegear=4, gameboy/lynx=2",
                target_bkg_bpp('pce') == 4 and target_bkg_bpp('sms') == 4
                and target_bkg_bpp('gamegear') == 4
                and target_bkg_bpp('gameboy') == 2 and target_bkg_bpp('lynx') == 2)

    # --- encoders ---
    d = tempfile.mkdtemp()
    p16 = os.path.join(d, "c16.png")
    p4 = os.path.join(d, "c4.png")
    M.write_png_indexed(p16, 16, 16, [[(x) % 16 for x in range(16)] for _ in range(16)],
                        [(i * 16, i, 255 - i) for i in range(16)])
    M.write_png_indexed(p4, 16, 16, [[(x) % 4 for x in range(16)] for _ in range(16)],
                        [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)])
    ok &= check("bkg_build_is_4bpp true for >4-colour, false for <=4",
                M.bkg_build_is_4bpp([p16]) and not M.bkg_build_is_4bpp([p4]))
    tiles4 = M.png_to_4bpp_bkg_tiles(p16)
    ok &= check("png_to_4bpp_bkg_tiles = 32 B/tile (4 tiles -> 128 B)",
                tiles4 is not None and len(tiles4) == 128)
    ok &= check("png_palette_bkg16 returns 16 colours",
                len(M.png_palette_bkg16(p16)) == 16)
    ok &= check("rgb555_words packs 0RRRRRGGGGGBBBBB",
                M.rgb555_words([(255, 0, 0), (0, 255, 0), (0, 0, 255)])
                == [0x7C00, 0x03E0, 0x001F])
    # The fork's 2bpp DOWN-TIER arm quantizes by luma RANK (quartiles), not the
    # absolute thresholds: a MID-RANGE 16-colour palette (all lumas 80..160)
    # would otherwise crush to ONE shade -- a flat, unreadable GB/GBC/NES image.
    pmid = os.path.join(d, "mid.png")
    midpal = [(80 + i * 5, 100 + i * 3, 90 + i * 4) for i in range(16)]  # lumas ~93..160
    M.write_png_indexed(pmid, 16, 16,
                        [[(x) % 16 for x in range(16)] for _ in range(16)], midpal)
    ok &= check("ranked_shades spreads a mid-range palette over all 4 shades",
                len(set(M.ranked_shades(midpal))) == 4)
    ok &= check("ranked_shades: duplicates share a rank, lightest -> shade 0",
                M.ranked_shades([(200, 200, 200), (200, 200, 200),
                                 (50, 50, 50)]) == [0, 0, 3])
    t2 = M.png_to_gb_tiles_ranked(pmid)
    ok &= check("png_to_gb_tiles_ranked emits 2bpp (16 B/tile) with 4 shades",
                len(t2) == 4 * 16 and len({(b1, b2) for b1, b2 in
                                           zip(t2[::2], t2[1::2])}) > 1)

    # A NON-INDEXED png ranks the colours the IMAGE uses. It used to fall
    # straight through to the absolute-threshold reader, which CRUSHES GB
    # Studio's own DMG palette: its lightest green (224,248,207) has luma 236,
    # just under the 240 "white" cut, so it merged with the second shade (165)
    # and the whole background came out one shade dark with two shades
    # collapsed into one. Caught on the platformer conversion, whose backgrounds are
    # saved RGBA (the reference-engine sample's are all indexed, which is why two earlier
    # conversions never showed it).
    def _shade_hist(tiles):
        h = {}
        for i in range(0, len(tiles), 2):
            lo, hi = tiles[i], tiles[i + 1]
            for b in range(8):
                v = ((hi >> b) & 1) * 2 + ((lo >> b) & 1)
                h[v] = h.get(v, 0) + 1
        return h

    GBS4 = [(224, 248, 207), (134, 192, 108), (48, 104, 80), (7, 24, 33)]
    prgb = os.path.join(d, "gbs_rgb.png")
    # 8x8 rows of one shade each, in palette order, twice over (one tile).
    M.write_png_rgba(prgb, 8, 8, [[GBS4[y // 2] + (255,) for _ in range(8)]
                                  for y in range(8)])
    hist = _shade_hist(M.png_to_gb_tiles_ranked(prgb))
    ok &= check("a non-indexed reference-engine palette ranks over all 4 shades "
                "(was 3, with white never used)",
                sorted(hist) == [0, 1, 2, 3] and hist[0] == 16)
    # ... and an authoring STRAY (a few pixels of pure black beside the
    # palette's darkest) must not shift the ranks of the four real shades.
    pstray = os.path.join(d, "gbs_stray.png")
    rows = [[GBS4[y // 2] + (255,) for _ in range(8)] for y in range(8)]
    rows[7][7] = (0, 0, 0, 255)
    M.write_png_rgba(pstray, 8, 8, rows)
    hist = _shade_hist(M.png_to_gb_tiles_ranked(pstray))
    ok &= check("a stray 5th colour snaps to the nearest shade, ranks unmoved",
                sorted(hist) == [0, 1, 2, 3] and hist[0] == 16 and hist[3] == 16)

    # --- transpiler fork ---
    rich = _world(16)
    ok &= check(">4-colour tileset forks TILESET by platform + emits BKG_PALETTE16",
                'if platform ==' in rich and 'platform == "pce"' in rich
                and 'platform == "sms"' in rich and 'BKG_PALETTE16' in rich)
    plain = _world(4)
    ok &= check("<=4-colour tileset stays byte-identical (no fork, no palette)",
                'if platform ==' not in plain and 'BKG_PALETTE16' not in plain)

    # --- PCE codegen ---
    cp = MosaikCompiler().compile((rich + "\n" + APP).strip(), platform='pce')
    ok &= check("pce: TILESET is 4bpp (32 B/tile), 2bpp on gameboy",
                'scenes_TILESET[128]' in cp)
    ok &= check("pce: 4bpp planar upload (plane helper + *32 stride)",
                'gbs_bkg_plane(' in cp and 'data + (uint16_t)t * 32' in cp)
    ok &= check("pce: gbs_load_bkg_pal16 real VCE body (BG palette 2 + backdrop)",
                'gbs_vce((uint16_t)(0x20 + p)' in cp
                and 'gbs_vce(0x000, gbs_rgb(r, g, b))' in cp)
    ok &= check("pce: no leftover load_bkg16 no-op stub",
                'gbs_load_bkg_pal16(const uint16_t *pal) { (void)pal; }' not in cp)

    # --- SMS codegen (native 4bpp planar upload + CRAM palette) ---
    cs = MosaikCompiler().compile((rich + "\n" + APP).strip(), platform='sms')
    ok &= check("sms: bkg.set_data routed to the mosaik 4bpp wrapper",
                'void gbs_set_bkg_data(' in cs and 'set_bkg_native_data(' in cs)
    ok &= check("sms: packed-nibble -> VDP planar (plane helper)",
                'gbs_bkg_plane(' in cs)
    ok &= check("sms: gbs_load_bkg_pal16 loads 16 CRAM entries via gbs_rgb",
                'set_bkg_palette_entry(0, p, gbs_rgb(r, g, b))' in cs)

    # --- Lynx 16-colour bkg (configurable: [world] lynx_bkg16) ---
    lyn_off = MosaikCompiler().compile((rich + "\n" + APP).strip(), platform='lynx')
    ok &= check("lynx WITHOUT opt-in: 2bpp strips (BPP_2), load_bkg16 a no-op",
                'BPP_2 | TYPE_BACKNONCOLL' in lyn_off
                and 'gbs_load_bkg_pal16(const uint16_t *pal) { (void)pal; }' in lyn_off)
    ok &= check("lynx WITHOUT opt-in: TILESET stays 2bpp (lynx not in fork)",
                'platform == "lynx"' not in rich)
    rich_lyn = _world(16, lynx16=True)
    ok &= check("[world] lynx_bkg16: lynx joins the TILESET fork",
                'platform == "lynx"' in rich_lyn)
    lyn_on = MosaikCompiler().compile((rich_lyn + "\n" + APP).strip(), platform='lynx')
    ok &= check("lynx WITH opt-in: BPP_4 strips + 32 B/tile table + identity pen map",
                'BPP_4 | TYPE_BACKNONCOLL' in lyn_on
                and 'gbs_bkg_tileset[GBS_BKG_MAX_TILES][32]' in lyn_on
                and '(i << 5) | (i << 1) | 1' in lyn_on)
    ok &= check("lynx WITH opt-in: gbs_load_bkg_pal16 loads the 16 Mikey pens",
                'MIKEY.palette[p] = (uint8_t)(w >> 8)' in lyn_on
                and 'gbs_load_bkg_pal16(const uint16_t *pal) { (void)pal; }' not in lyn_on)

    # --- [build] lynx_bkg16 explicit knob (a hand-written game, no scenes) ---
    HAND = ('module "h" {\n'
            '    import "platform.video"\n    import "graphics.bkg"\n'
            '    import "graphics.palette"\n'
            '    const T: array[u8, 32] = [ %s ]\n'
            '    const P: array[u16, 16] = [ %s ]\n'
            '    function main() {\n'
            '        bkg.set_data(0, 1, T)\n        palette.load_bkg16(P)\n'
            '        video.enable_lcd() video.show_background()\n'
            '        loop { video.wait_vblank() }\n    }\n    export main\n}\n'
            % (", ".join(["0"] * 32), ", ".join(["0x100"] * 16)))
    kon = MosaikCompiler().compile_program([("h.mos", HAND)], platform="lynx",
                                           lynx_bkg16=True)
    ok &= check("[build] lynx_bkg16 forces the 4bpp Lynx engine (hand-written, no scenes)",
                'BPP_4 | TYPE_BACKNONCOLL' in kon
                and 'MIKEY.palette[p] = (uint8_t)(w >> 8)' in kon)
    koff = MosaikCompiler().compile_program([("h.mos", HAND)], platform="lynx",
                                            lynx_bkg16=False)
    ok &= check("without the knob a hand-written game stays 2bpp (byte-identical)",
                'BPP_2 | TYPE_BACKNONCOLL' in koff)

    # --- 2bpp console: same source compiles, load_bkg16 is a no-op ---
    cg = MosaikCompiler().compile((rich + "\n" + APP).strip(), platform='gameboy')
    ok &= check("gameboy: TILESET is 2bpp (16 B/tile)",
                'scenes_TILESET[64]' in cg)
    ok &= check("gameboy: load_bkg16 no-op stub, symbol still resolves",
                'gbs_load_bkg_pal16(const uint16_t *pal) { (void)pal; }' in cg)

    # --- VM rooms.mos auto-wiring (generated games call load_bkg16) ---
    import mosaik_vm

    def _vm_project(ncolors):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "src"), exist_ok=True)
        pal = [(i * 16, 255 - i * 16, (i * 8) & 0xFF) for i in range(ncolors)]
        M.write_png_indexed(
            os.path.join(d, "tiles.png"), 16, 16,
            [[(x + y) % ncolors for x in range(16)] for y in range(16)], pal)
        with open(os.path.join(d, "world.toml"), "w") as f:
            f.write('[world]\nvm = true\nstart_scene = "room"\n\n[kinds]\nplayer = 0\n\n')
            f.write('[tileset]\npng = "tiles.png"\n\n[[scene]]\nname = "room"\n')
            f.write('scene_type = "topdown"\nmap_w = 4\nmap_h = 4\nmap = [%s]\n'
                    % ", ".join(str(i % ncolors) for i in range(16)))
            f.write('\n[[scene.object]]\nkind = "player"\nx = 16\ny = 16\n')
        open(os.path.join(d, "src", "rooms.mos"), "w").close()
        return open(mosaik_vm.generate_rooms(d), encoding="utf-8").read()

    r16 = _vm_project(16)
    ok &= check("rooms.mos: 16-colour VM world auto-emits load_bkg16 + palette import",
                'palette.load_bkg16(scenes.BKG_PALETTE16)' in r16
                and 'import "graphics.palette"' in r16)
    r4 = _vm_project(4)
    ok &= check("rooms.mos: 4-colour VM world stays byte-identical (no load_bkg16)",
                'load_bkg16' not in r4)

    print()
    if ok:
        print("All 4bpp background-tier checks passed")
        return 0
    print("4bpp background-tier checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
