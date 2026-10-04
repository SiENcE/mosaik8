"""Generate the bkg16-demo tileset + world.

Writes a 16-colour indexed tileset (16 tiles, tile k = a solid colour index k)
and a 32x28 rainbow map, so the screen shows all sixteen colours at once. On a
bkg_bpp==4 console (PC Engine today) the tileset renders at native 16-colour
depth via the 4bpp background tier + palette.load_bkg16; on a 2bpp console the
same source luma-quantizes to the four-grey ramp.

    python projects/bkg16-demo/assets/gen_world.py
    python -m mosaik_scenes projects/bkg16-demo/world.toml \
        -o projects/bkg16-demo/src/scenes.mos
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)

from mosaik_assets import write_png_indexed  # noqa: E402

# Sixteen vivid, well-separated colours (index 0 = the backdrop).
PALETTE = [
    (16, 16, 24),    (230, 60, 60),   (245, 140, 40),  (245, 225, 60),
    (120, 220, 70),  (60, 200, 90),   (60, 210, 200),  (60, 150, 240),
    (80, 90, 240),   (150, 70, 230),  (230, 80, 220),  (245, 130, 180),
    (150, 90, 60),   (200, 200, 210), (120, 125, 135), (250, 250, 250),
]

TILE = 8
NTILES = 16


def main():
    # Tileset: NTILES tiles in a row, tile k a solid block of colour index k.
    w, h = TILE * NTILES, TILE
    idx = [[(x // TILE) for x in range(w)] for _ in range(h)]
    write_png_indexed(os.path.join(HERE, "tiles.png"), w, h, idx, PALETTE)

    # A 32x28 rainbow map: 2-wide vertical colour bands cycling through 0..15.
    mw, mh = 32, 28
    rows = []
    for r in range(mh):
        row = [((c // 2) + r) % NTILES for c in range(mw)]
        rows.append(row)
    flat = [t for row in rows for t in row]

    world = os.path.join(HERE, "..", "world.toml")
    with open(world, "w") as f:
        # Opt the Lynx into the 4bpp 16-colour background (it doubles the Suzy
        # strip RAM; a per-project choice against the tight Lynx MAIN budget).
        f.write('[world]\nlynx_bkg16 = true\n\n')
        f.write('[tileset]\npng = "assets/tiles.png"\n\n')
        f.write('[[scene]]\nname = "rainbow"\n')
        f.write("map_w = %d\nmap_h = %d\n" % (mw, mh))
        f.write("map = [\n")
        for r in range(mh):
            f.write("    " + ", ".join(str(v) for v in flat[r * mw:(r + 1) * mw]) + ",\n")
        f.write("]\n")
    print("wrote tiles.png (%d colours) + world.toml (%dx%d)" % (NTILES, mw, mh))


if __name__ == "__main__":
    main()
