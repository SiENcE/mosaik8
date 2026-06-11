#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Asset pipeline (mosaik_assets.py + compiler integration).

Verifies the PNG decoder (including non-zero scanline filters), the
pixel-to-GB-shade mapping rules, the GB 2bpp encoder against known tile
bytes, tile cutting order, the asset name/clash handling, and that
MosaikCompiler.compile(assets=...) emits the `<name>_tiles` array and
`<name>_tile_count` define into the C for both backends.
"""

import struct
import tempfile
import zlib

import mosaik_assets as ga
from mosaik_compiler import MosaikCompiler

failures = 0


def check(name, cond, detail=""):
    global failures
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures += 1


# The bounce sample's ball tile: both bitplanes identical, a known-good
# GB 2bpp reference (52 ink pixels in a circle).
BALL = bytes([0x3C, 0x3C, 0x7E, 0x7E, 0xFF, 0xFF, 0xFF, 0xFF,
              0xFF, 0xFF, 0xFF, 0xFF, 0x7E, 0x7E, 0x3C, 0x3C])


def ball_shades():
    rows = []
    for r in range(8):
        lo, hi = BALL[r * 2], BALL[r * 2 + 1]
        rows.append([(((hi >> (7 - c)) & 1) << 1) | ((lo >> (7 - c)) & 1)
                     for c in range(8)])
    return rows


PALETTE4 = [(255, 0, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]

tmp = tempfile.mkdtemp(prefix="mosaik_assets_")


def path(name):
    return os.path.join(tmp, name)


# --- encoder golden -------------------------------------------------------

enc = ga.shades_to_gb_tiles(8, 8, ball_shades())
check("encoder reproduces known GB 2bpp tile bytes", enc == BALL, enc.hex())

# --- indexed PNG: <=4-entry palette means literal pixel values ------------

ga.write_png_indexed(path("ball.png"), 8, 8, ball_shades(), PALETTE4)
check("indexed PNG (4-colour palette) roundtrips byte-exact",
      ga.png_to_gb_tiles(path("ball.png")) == BALL)

# --- RGBA mapping: alpha -> 0, luma bands -> 1..3 --------------------------

rgba = [[{0: (9, 9, 9, 0),        # transparent regardless of colour
          1: (200, 200, 200, 255),  # light
          2: (120, 120, 120, 255),  # dark
          3: (10, 10, 10, 255)}[v]  # ink
         for v in row] for row in ball_shades()]
ga.write_png_rgba(path("rgba.png"), 8, 8, rgba)
check("RGBA PNG maps alpha/luma to GB shades",
      ga.png_to_gb_tiles(path("rgba.png")) == BALL)

# Opaque white maps to colour 0 (the GB transparent-white convention).
white = [[(255, 255, 255, 255)] * 8] * 8
ga.write_png_rgba(path("white.png"), 8, 8, white)
check("opaque white maps to colour 0",
      ga.png_to_gb_tiles(path("white.png")) == bytes(16))

# --- multi-tile cutting: left-to-right, top-to-bottom ----------------------

grid = [row + [1] * 8 for row in ball_shades()]          # 16x8: ball + light
grid += [[2] * 8 + [3] * 8 for _ in range(8)]            # 16x16: dark + ink
ga.write_png_indexed(path("grid.png"), 16, 16, grid, PALETTE4)
data = ga.png_to_gb_tiles(path("grid.png"))
check("16x16 PNG yields 4 tiles", len(data) == 4 * 16)
check("tile order is row-major",
      data[0:16] == BALL
      and data[16:32] == bytes([0xFF, 0x00] * 8)    # all-1 pixels
      and data[32:48] == bytes([0x00, 0xFF] * 8)    # all-2 pixels
      and data[48:64] == bytes([0xFF, 0xFF] * 8))   # all-3 pixels

# --- decoder handles non-zero scanline filters -----------------------------
# Hand-build an 8x8 greyscale PNG using Sub on row 0 and Up on rows 1..7:
# a horizontal ramp repeated on every row (Sub deltas, then all-zero Ups).

ramp = [0, 36, 72, 108, 144, 180, 216, 252]
sub_row = bytes([1] + [ramp[0]] + [ramp[i] - ramp[i - 1] for i in range(1, 8)])
up_rows = bytes([2] + [0] * 8) * 7
ihdr = struct.pack(">IIBBBBB", 8, 8, 8, 0, 0, 0, 0)
png = (b"\x89PNG\r\n\x1a\n"
       + ga._png_chunk(b"IHDR", ihdr)
       + ga._png_chunk(b"IDAT", zlib.compress(sub_row + up_rows))
       + ga._png_chunk(b"IEND", b""))
with open(path("filters.png"), "wb") as f:
    f.write(png)
_, _, shades = ga.png_to_shades(path("filters.png"))
expect = [ga._shade_from_rgba(v, v, v, 255) for v in ramp]
check("Sub/Up filtered greyscale PNG decodes",
      all(row == expect for row in shades), str(shades[0]))

# --- names and clashes -----------------------------------------------------

check("asset names sanitize to C identifiers",
      ga.asset_c_name("assets/player-ship.png") == "player_ship"
      and ga.asset_c_name("8ball.png") == "_8ball")
try:
    ga.load_assets([path("ball.png"), path("ball.png")])
    check("duplicate asset names are rejected", False)
except ga.AssetError:
    check("duplicate asset names are rejected", True)

# --- size validation -------------------------------------------------------

try:
    ga.shades_to_gb_tiles(7, 8, [[0] * 7] * 8)
    check("non-multiple-of-8 image is rejected", False)
except ga.AssetError:
    check("non-multiple-of-8 image is rejected", True)

# --- compiler integration: both backends emit the asset symbols ------------

PROGRAM = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
    function main() {
        sprite.set_data(0, ball_tile_count, ball_tiles)
        sprite.set_tile(0, 0)
        sprite.move(0, 16, 16)
        video.enable_lcd()
        video.show_sprites()
        loop { video.wait_vblank() }
    }
    export main
}
'''

assets = [("ball", BALL + BALL)]  # 2 tiles
for platform in ("gameboy", "gamegear", "lynx"):
    c = MosaikCompiler().compile(PROGRAM, platform=platform, assets=assets)
    ok = (not c.startswith("Compilation error")
          and "#define ball_tile_count 2" in c
          and "const uint8_t ball_tiles[32]" in c
          and "0x3C, 0x3C," in c)
    check(f"compile(assets=...) emits tile data on {platform}", ok,
          c[:300])

# Without assets nothing asset-related is emitted.
c = MosaikCompiler().compile(PROGRAM.replace("ball_tile_count", "1")
                               .replace("ball_tiles", "0"), platform="gameboy")
check("no asset block without assets", "asset pipeline" not in c)

print()
if failures:
    print(f"{failures} test(s) FAILED")
    sys.exit(1)
print("All asset pipeline tests passed")
