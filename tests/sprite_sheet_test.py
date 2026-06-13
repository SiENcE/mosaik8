#!/usr/bin/env python3
import os, sys, json, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Named-sprite PNG sheets -- Phase 3 of the native-feature pipeline
(docs/endless-runner-plan.md).

A sheet PNG + a sidecar `<stem>.sprites.json` (name -> [x, y, w, h] pixels) is
sliced into named sub-sprites: their tiles are concatenated into the asset's
`<name>_tiles` array, and per-sprite `<s>_tile` / `_w` / `_h` defines feed
sprite.set_meta. Verifies the manifest parse, the slicing/offsets, and the
codegen emission; PNG-only (no PCX), the same on every console.
"""

import mosaik_assets as M
from mosaik import MosaikCompiler


def make_sheet():
    d = tempfile.mkdtemp()
    png = os.path.join(d, "sheet.png")
    pal = [(i * 16, 255 - i * 16, (i * 32) & 255) for i in range(16)]
    rows = [[1 + ((x // 8) + (y // 8)) % 15 for x in range(32)] for y in range(24)]
    M.write_png_indexed(png, 32, 24, rows, pal)
    json.dump({"knight": [0, 0, 16, 24], "gem": [16, 0, 16, 16]},
              open(M.manifest_path(png), "w"))
    return png

SRC = '''
module "s" {
    import "platform.video"
    import "graphics.sprite"
    function main() {
        video.enable_lcd()
        sprite.set_data(0, sheet_tile_count, sheet_tiles)
        sprite.set_meta(0, knight_tile, knight_w, knight_h)
        sprite.set_meta(8, gem_tile, gem_w, gem_h)
        sprite.move(0, 30, 30)
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
    print("Named-sprite PNG sheets (sprite.set_meta manifest)")
    print("=" * 50)
    ok = True
    png = make_sheet()

    # Manifest parse + per-sprite defs (cumulative tile offsets, manifest order).
    ok &= check("manifest parses in order with [x,y,w,h] rects",
                M.load_sprite_manifest(png) ==
                [('knight', 0, 0, 16, 24), ('gem', 16, 0, 16, 16)])
    ok &= check("sprite defs: knight=2x3@0, gem=2x2@6 (after knight's 6 tiles)",
                M.sheet_sprite_defs(png) ==
                [('knight', 0, 2, 3), ('gem', 6, 2, 2)])

    # Sliced + concatenated tile stream: 6 + 4 = 10 tiles.
    a2 = M.load_assets([png], sprite_bpp=2)
    a4 = M.load_assets([png], sprite_bpp=4)
    ok &= check("2bpp sheet = 10 tiles * 16 bytes; 4bpp = 10 * 32",
                len(a2[0][1]) == 10 * 16 and len(a4[0][1]) == 10 * 32)

    # Codegen emits the per-sprite defines + registers the symbols.
    name = M.asset_c_name(png)
    src = SRC.replace("sheet", name)
    defs = M.load_asset_sprite_defs([png])
    out = MosaikCompiler().compile(src, platform="gameboy",
                                   assets=a2, asset_sprites=defs)
    ok &= check("per-sprite defines emitted",
                "#define knight_tile 0" in out and "#define knight_w 2" in out
                and "#define knight_h 3" in out and "#define gem_tile 6" in out
                and "#define gem_w 2" in out and "#define gem_h 2" in out)
    ok &= check("set_meta lowers using the sheet defines (no compile error)",
                not out.startswith("Compilation error:")
                and "gbs_set_metasprite(0, knight_tile, knight_w, knight_h)" in out)

    # No manifest -> flat grid (unchanged behaviour).
    flat = tempfile.mktemp(suffix=".png")
    M.write_png_indexed(flat, 8, 8, [[1] * 8 for _ in range(8)],
                        [(0, 0, 0), (255, 255, 255)])
    ok &= check("a PNG with no sidecar stays a flat grid (no sprite defs)",
                M.sheet_sprite_defs(flat) == []
                and M.load_asset_sprite_defs([flat]) == [])

    print()
    if ok:
        print("All named-sprite sheet checks passed")
        return 0
    print("Named-sprite sheet checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
