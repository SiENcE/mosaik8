#!/usr/bin/env python3
"""mosaik asset pipeline: PNG -> Game Boy 2bpp tile data.

The build tool converts each asset PNG into GB 2bpp tile bytes and hands them
to the compiler, which emits them into the translation unit as a
`const uint8_t <name>_tiles[]` array plus a `<name>_tile_count` define.

GB 2bpp is deliberately the *only* output format: it is the universal
interchange format across every supported console. GBDK-2020's
`set_sprite_data` accepts it natively on the GB family, converts it to CHR
layout on NES, and expands it to 4bpp through the compat layer
(`set_tile_2bpp_data` + `_current_2bpp_palette`) on SMS/Game Gear; the cc65
Lynx sprite engine converts it to Suzy literal sprites at runtime. So one
encoder serves both backends and all nine consoles (`png2asset`/`sp65` remain
available for native-format needs beyond this pipeline).

The PNG reader is self-contained (zlib + struct only, no PIL) so the build
tool keeps zero binary dependencies. Supported: non-interlaced PNGs,
greyscale / RGB / palette / greyscale+alpha / RGBA, bit depths 1/2/4/8.

Pixel -> GB colour value (0..3) mapping rules, in order:

* Indexed PNG with a palette of <= 4 entries: the palette index *is* the
  pixel value (0..3), giving artists exact control. Index 0 is the GB
  "colour 0" (transparent for sprites).
* Otherwise (true-colour, greyscale, or larger palettes):
  - alpha < 128            -> 0 (transparent)
  - luma >= 240 (white)    -> 0 (GB colour-0 convention: white = transparent)
  - luma >= 160            -> 1 (light)
  - luma >=  80            -> 2 (dark)
  - else                   -> 3 (black / full ink)

The image must be a multiple of 8 pixels in both dimensions; tiles are cut
left-to-right, top-to-bottom (tile index = (y/8)*tiles_per_row + x/8).
"""

import os
import re
import struct
import zlib

GB_TILE_BYTES = 16  # 8 rows x 2 bitplane bytes


class AssetError(Exception):
    """A problem with an asset file, reported with build-friendly context."""


# ---------------------------------------------------------------------------
# PNG reading (minimal, dependency-free)
# ---------------------------------------------------------------------------

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# PNG colour types -> samples per pixel.
_SAMPLES_PER_PIXEL = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _decode_png(data):
    """Decode a PNG into (width, height, colour_type, palette, trns, pixels).

    `pixels` is a list of rows; each row is a list of per-pixel sample tuples
    (one int per sample, channel range scaled to 0..255).
    """
    if not data.startswith(_PNG_SIGNATURE):
        raise AssetError("not a PNG file (bad signature)")

    width = height = None
    bit_depth = colour_type = None
    palette = None
    trns = None
    idat = bytearray()

    pos = len(_PNG_SIGNATURE)
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length  # length + type + data + CRC
        if ctype == b"IHDR":
            (width, height, bit_depth, colour_type,
             compression, filter_method, interlace) = struct.unpack(
                ">IIBBBBB", chunk)
            if compression != 0 or filter_method != 0:
                raise AssetError("unsupported PNG compression/filter method")
            if interlace != 0:
                raise AssetError("interlaced (Adam7) PNGs are not supported; "
                                 "re-export without interlacing")
            if colour_type not in _SAMPLES_PER_PIXEL:
                raise AssetError("unsupported PNG colour type %d" % colour_type)
            if colour_type in (2, 4, 6):
                if bit_depth != 8:
                    raise AssetError(
                        "only 8-bit channels are supported for "
                        "truecolour/alpha PNGs (got %d-bit)" % bit_depth)
            elif bit_depth not in (1, 2, 4, 8):
                raise AssetError(
                    "unsupported PNG bit depth %d (use 1/2/4/8)" % bit_depth)
        elif ctype == b"PLTE":
            palette = [tuple(chunk[i:i + 3]) for i in range(0, len(chunk), 3)]
        elif ctype == b"tRNS":
            trns = bytes(chunk)
        elif ctype == b"IDAT":
            idat.extend(chunk)
        elif ctype == b"IEND":
            break

    if width is None:
        raise AssetError("missing IHDR chunk")
    if colour_type == 3 and palette is None:
        raise AssetError("indexed PNG without a PLTE palette")

    raw = zlib.decompress(bytes(idat))

    samples = _SAMPLES_PER_PIXEL[colour_type]
    bits_per_pixel = bit_depth * samples
    stride = (width * bits_per_pixel + 7) // 8  # filtered bytes per scanline
    bpp = max(1, bits_per_pixel // 8)           # filter step, per PNG spec

    if len(raw) < (stride + 1) * height:
        raise AssetError("truncated PNG image data")

    # Undo per-scanline filters.
    rows_bytes = []
    prev = bytearray(stride)
    for y in range(height):
        offset = y * (stride + 1)
        ftype = raw[offset]
        line = bytearray(raw[offset + 1:offset + 1 + stride])
        if ftype == 1:    # Sub
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                up_left = prev[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + _paeth(left, prev[i], up_left)) & 0xFF
        elif ftype != 0:
            raise AssetError("unsupported PNG filter type %d" % ftype)
        rows_bytes.append(line)
        prev = line

    # Unpack scanline bytes into per-pixel sample tuples.
    max_val = (1 << bit_depth) - 1
    pixels = []
    for line in rows_bytes:
        row = []
        if bit_depth == 8:
            for x in range(width):
                row.append(tuple(line[x * samples:(x + 1) * samples]))
        else:
            # Sub-byte depths only occur with 1 sample/pixel (grey or indexed).
            per_byte = 8 // bit_depth
            for x in range(width):
                byte = line[x // per_byte]
                shift = 8 - bit_depth * (x % per_byte + 1)
                value = (byte >> shift) & max_val
                if colour_type == 0:
                    value = value * 255 // max_val  # scale grey to 0..255
                row.append((value,))
        pixels.append(row)

    return width, height, colour_type, palette, trns, pixels


def _luma(r, g, b):
    return (r * 299 + g * 587 + b * 114) // 1000


def _shade_from_rgba(r, g, b, a):
    """Map one RGBA pixel to a GB colour value 0..3 (see module docstring)."""
    if a < 128:
        return 0
    luma = _luma(r, g, b)
    if luma >= 240:
        return 0
    if luma >= 160:
        return 1
    if luma >= 80:
        return 2
    return 3


def png_to_shades(path):
    """Read a PNG and return (width, height, rows of GB colour values 0..3)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise AssetError("cannot read asset: %s" % e)

    width, height, colour_type, palette, trns, pixels = _decode_png(data)

    shades = []
    if colour_type == 3 and len(palette) <= 4:
        # Small palette: indices are literal GB colour values.
        for row in pixels:
            shades.append([px[0] for px in row])
    else:
        for row in pixels:
            out = []
            for px in row:
                if colour_type == 0:      # greyscale
                    g = px[0]
                    r, gg, b, a = g, g, g, 255
                elif colour_type == 2:    # RGB
                    r, gg, b, a = px[0], px[1], px[2], 255
                elif colour_type == 3:    # indexed (large palette)
                    r, gg, b = palette[px[0]]
                    a = trns[px[0]] if trns and px[0] < len(trns) else 255
                elif colour_type == 4:    # grey + alpha
                    r, gg, b, a = px[0], px[0], px[0], px[1]
                else:                     # RGBA
                    r, gg, b, a = px
                out.append(_shade_from_rgba(r, gg, b, a))
            shades.append(out)
    return width, height, shades


# ---------------------------------------------------------------------------
# GB 2bpp encoding
# ---------------------------------------------------------------------------

def shades_to_gb_tiles(width, height, shades):
    """Encode rows of GB colour values into GB 2bpp tile bytes.

    Tiles are cut left-to-right, top-to-bottom. Per tile: 8 rows x 2 bytes
    (low bitplane byte then high bitplane byte, leftmost pixel in the MSB) --
    the format `sprite.set_data` consumes on every console.
    """
    if width % 8 or height % 8:
        raise AssetError(
            "image is %dx%d; tile assets must be multiples of 8 pixels"
            % (width, height))
    out = bytearray()
    for tile_y in range(height // 8):
        for tile_x in range(width // 8):
            for row in range(8):
                line = shades[tile_y * 8 + row]
                lo = hi = 0
                for col in range(8):
                    value = line[tile_x * 8 + col] & 0x03
                    bit = 7 - col
                    lo |= (value & 1) << bit
                    hi |= (value >> 1) << bit
                out.append(lo)
                out.append(hi)
    return bytes(out)


def png_to_gb_tiles(path):
    """Convert a PNG asset to GB 2bpp tile bytes (the full pipeline)."""
    try:
        width, height, shades = png_to_shades(path)
        return shades_to_gb_tiles(width, height, shades)
    except AssetError as e:
        raise AssetError("%s: %s" % (path, e))


# ---------------------------------------------------------------------------
# 4bpp encoding (16-colour sprites, the native depth of the Lynx / PC Engine)
# ---------------------------------------------------------------------------
#
# The 4bpp interchange is "packed nibble": per 8-pixel row, 4 bytes, two
# pixels per byte, the leftmost pixel in the HIGH nibble -- exactly the pixel
# layout the Lynx Suzy blitter consumes for a 4bpp literal sprite, so the cc65
# engine copies the row verbatim. 32 bytes/tile (vs 16 for GB 2bpp).

MAX_4BPP_COLORS = 16


def png_indices(path):
    """(width, height, rows of palette indices, palette) for an indexed PNG
    with at most 16 entries, or None if the PNG is not such an image.

    Used by the 4bpp sprite tier: the palette index *is* the 4bpp pixel value
    (0..15), and index 0 stays the transparent / backdrop colour, mirroring
    the 2bpp `index == colour value` rule for <=4-entry PNGs."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise AssetError("cannot read asset: %s" % e)
    width, height, colour_type, palette, _trns, pixels = _decode_png(data)
    if colour_type != 3 or palette is None or len(palette) > MAX_4BPP_COLORS:
        return None
    index_rows = [[px[0] & 0x0F for px in row] for row in pixels]
    return width, height, index_rows, [tuple(c) for c in palette]


def indices_to_4bpp_tiles(width, height, index_rows):
    """Encode rows of 4-bit palette indices into packed-nibble 4bpp tiles.

    Tiles cut left-to-right, top-to-bottom; per tile 8 rows x 4 bytes, two
    pixels per byte (leftmost pixel = high nibble)."""
    if width % 8 or height % 8:
        raise AssetError(
            "image is %dx%d; tile assets must be multiples of 8 pixels"
            % (width, height))
    out = bytearray()
    for tile_y in range(height // 8):
        for tile_x in range(width // 8):
            for row in range(8):
                line = index_rows[tile_y * 8 + row]
                for pair in range(4):
                    left = line[tile_x * 8 + pair * 2] & 0x0F
                    right = line[tile_x * 8 + pair * 2 + 1] & 0x0F
                    out.append((left << 4) | right)
    return bytes(out)


def png_to_4bpp_tiles(path):
    """Convert an indexed (<=16 colour) PNG to packed-nibble 4bpp tile bytes."""
    info = png_indices(path)
    if info is None:
        return None
    width, height, index_rows, _palette = info
    try:
        return indices_to_4bpp_tiles(width, height, index_rows)
    except AssetError as e:
        raise AssetError("%s: %s" % (path, e))


def ranked_shades(palette):
    """GB shade (0..3, 0 = lightest) per palette entry, by luma RANK (quartiles
    over the distinct luma values) instead of the absolute `_shade_from_rgba`
    thresholds.

    The absolute thresholds suit full-range art but CRUSH a mid-range palette:
    a 16-colour scene whose lumas all sit in 80..160 quantizes to ONE shade --
    a flat, unreadable Game Boy image. Ranking spreads the palette across all
    four shades (relative brightness survives), which is what the 4bpp
    background tier's 2bpp down-tier arm uses (mosaik_scenes) -- and the
    studio's per-console preview mirrors THIS function, so editor and ROM
    agree. Duplicate colours share a rank (never straddle two shades)."""
    lumas = [_luma(*c[:3]) for c in palette]
    distinct = sorted(set(lumas), reverse=True)          # lightest first
    n = len(distinct)
    rank = {v: i for i, v in enumerate(distinct)}
    if n <= 1:
        return [0] * len(palette)
    if n <= 4:
        # few distinct lumas: spread them over the shade range ends-first
        spread = {v: (i * 3) // (n - 1) for i, v in enumerate(distinct)}
        return [spread[v] for v in lumas]
    return [(rank[v] * 4) // n for v in lumas]


def png_rgba_rows(path):
    """(width, height, rows of (r, g, b, a) tuples) for any PNG colour type."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise AssetError("cannot read asset: %s" % e)
    width, height, colour_type, palette, trns, pixels = _decode_png(data)
    rows = []
    for row in pixels:
        out = []
        for px in row:
            if colour_type == 0:
                g = px[0]
                out.append((g, g, g, 255))
            elif colour_type == 2:
                out.append((px[0], px[1], px[2], 255))
            elif colour_type == 3:
                r, g, b = palette[px[0]]
                a = trns[px[0]] if trns and px[0] < len(trns) else 255
                out.append((r, g, b, a))
            elif colour_type == 4:
                out.append((px[0], px[0], px[0], px[1]))
            else:
                out.append(tuple(px))
        rows.append(out)
    return width, height, rows


#: A non-indexed PNG with at most this many distinct opaque colours is treated
#: as small-palette art and RANKED (see `png_to_gb_tiles_ranked`); above it the
#: image is genuine full-range art the absolute thresholds are meant for.
MAX_RANKED_COLORS = 16


def png_to_gb_tiles_ranked(path):
    """A small-palette PNG as GB 2bpp tiles via RANKED luma quantization
    (`ranked_shades`) -- the 2bpp down-tier arm of the 4bpp background fork,
    and the reader every reference-engine background goes through.

    An INDEXED PNG ranks its PLTE. A non-indexed one (RGB / RGBA / greyscale)
    ranks the colours the IMAGE ITSELF uses, which is the same intent: it used
    to fall straight through to the absolute-threshold `png_to_gb_tiles`, and
    that CRUSHES the reference engine's own DMG palette. Its lightest green (224,248,207)
    has luma 236, just under the 240 "white" cut, so it merged with the second
    shade (165) - a converted background rendered with no white at all, one
    shade darker throughout, and two shades collapsed into one. It only shows
    on a project whose backgrounds are saved as RGB/RGBA rather than indexed
    (the reference-engine sample conversion's are all indexed, which is why this survived two
    conversions; the platformer conversion's title screen is RGBA and is where it was caught).

    GB art is 4-colour by construction, so when a non-indexed image carries
    more than 4 distinct opaque colours the ranking palette is its 4 most
    FREQUENT ones and every other colour snaps to the nearest of those by luma
    - an authoring stray (the platformer conversion's title screen has 39 pixels of pure black
    beside the palette's (7,24,33)) must not shift the ranks of the real
    shades. Past `MAX_RANKED_COLORS` distinct colours the image is not
    GB-shaped at all and keeps the absolute thresholds.

    A transparent pixel stays shade 0, as `_shade_from_rgba` has always had it.
    """
    info = png_indices(path)
    if info is not None:
        width, height, index_rows, palette = info
        shade = ranked_shades(palette)
        rows = [[shade[i] for i in row] for row in index_rows]
    else:
        width, height, rgba = png_rgba_rows(path)
        counts = {}
        for row in rgba:
            for px in row:
                if px[3] >= 128:
                    counts[px[:3]] = counts.get(px[:3], 0) + 1
        if not counts or len(counts) > MAX_RANKED_COLORS:
            return png_to_gb_tiles(path)
        # The ranking palette: at most 4 colours, the most used first (ties
        # broken by luma so the choice is deterministic across runs).
        pal = sorted(counts, key=lambda c: (-counts[c], -_luma(*c)))[:4]
        shade = ranked_shades(pal)
        by_colour = {c: shade[i] for i, c in enumerate(pal)}
        pal_luma = [_luma(*c) for c in pal]
        for c in counts:
            if c in by_colour:
                continue
            lu = _luma(*c)
            near = min(range(len(pal)), key=lambda i: abs(pal_luma[i] - lu))
            by_colour[c] = shade[near]
        rows = [[0 if px[3] < 128 else by_colour[px[:3]] for px in row]
                for row in rgba]
    try:
        return shades_to_gb_tiles(width, height, rows)
    except AssetError as e:
        raise AssetError("%s: %s" % (path, e))


#: Glyph cells a VARIABLE-width sheet may carry: ASCII 32..255, the full 16x14
#: grid. A FIXED-width sheet stays at the historical 96 (ASCII 32..127) because
#: that is all the resident-font path can address (it plots
#: `font_base + ch - 32` into a contiguous VRAM block) and all the built-in
#: table defines.
FONT_CELLS_VWF = 224
FONT_CELLS_MONO = 96


def png_to_font_sheet(path):
    """`(glyphs, widths)` for a font PNG: 1bpp bitmaps, plus one advance width
    per glyph when the sheet is VARIABLE-width (else None).

    A fixed-width sheet reads exactly as it always has - 96 glyphs, no widths -
    which is what keeps every project that bakes one byte-identical. That is
    not a hypothetical: `gbs-mono.png` carries 224 cells, so widening on CELL
    COUNT would have grown all four reference-engine conversions by ~1 KB for glyphs
    none of them can draw. The trigger is the MARKER, not the size.

    A variable-width sheet carries its whole range, because that is where its
    author put the spacers - the platformer conversion pads lines with characters 239 and 255, which
    is exactly the range a 96-glyph table cuts off.
    """
    spans, seen_marker = _font_spans(path)
    if not seen_marker:
        return png_to_font_1bpp(path), None
    widths = [0 if sp is None else sp[1] - sp[0] + 1 for sp in spans]
    shifts = [0 if sp is None else sp[0] for sp in spans]
    n = min(len(widths), FONT_CELLS_VWF)
    glyphs = png_to_font_1bpp(path, count=n, shifts=shifts[:n])
    # NORMALISE THE SPACE GLYPH TO A UNIFORM PAPER CELL. A VWF glyph's
    # meaningful content is only its first `width` columns - the rest are
    # marker columns the compositor masks away. Nothing masks a RAW plot,
    # though, and `gbs_clear_area` blanks a cell by plotting the space glyph
    # at `gbs_font_base`: on a pre-inverted sheet that is 2 dark columns then
    # 6 light ones, so every blanked cell drew a vertical bar and a cleared
    # region came out striped at an 8 px period. Take the sheet's own paper
    # from the space glyph's DRAWN part and fill the cell with it, which is
    # black for a light-on-dark font and blank for an ordinary one. Compositing
    # a real space is unaffected: only its first `width` columns are ever used,
    # and they keep the same value.
    if glyphs and widths:
        paper = 0xFF if (widths[0] and (glyphs[0][0] & 0x80)) else 0x00
        glyphs[0] = [paper] * 8
    return (glyphs, widths[:n])


def png_to_font_1bpp(path, count=FONT_CELLS_MONO, shifts=None):
    """Convert a font PNG to the glyph-buffer text mode's 1bpp table
    (`[assets] font`): `count` glyphs x 8 one-byte bitmap rows, from ASCII 32.

    `count` defaults to 96 (ASCII 32..127), which is the historical behaviour
    and what a fixed-width sheet gets. `png_to_font_sheet` raises it to the
    sheet's full range for a variable-width font.

    The PNG is a row-major grid of 8x8 glyph cells whose FIRST cell is the
    space (character 32) -- the reference engine's own font layout (its recode table maps
    char 32 to glyph 0, so cell i renders character 32+i; `gbs-mono.png`
    carries 224 cells, of which the first 96 are the ASCII set this reads).

    Ink detection goes through the ranked-luma reader, so a darkest-first PLTE
    (every reference-engine asset) converts correctly: after ranking, ink pixels are
    the DARK shades (2..3) and paper the light ones (0..1). A glyph whose two
    2bpp planes would disagree (a mid-shade font) still folds to that same
    ink test -- 1bpp is the format, dark-on-light is the contract.

    Returns a list of `count` rows of 8 ints. Raises AssetError when the image
    holds fewer than `count` cells or the space cell is not blank (a
    mis-laid-out grid would silently shift every character otherwise).
    """
    tiles = png_to_gb_tiles_ranked(path)
    if len(tiles) < count * 16:
        raise AssetError("%s: a font needs %d 8x8 glyph cells (ASCII 32..%d); "
                         "this image has %d"
                         % (path, count, 31 + count, len(tiles) // 16))
    glyphs = []
    for g in range(count):
        t = tiles[g * 16:(g + 1) * 16]
        # `shifts` RE-BASES a variable-width glyph at x = 0 (the reference engine's
        # two-sided trim): its width is the ink SPAN, so a centred glyph -- a
        # 2 px 'I' at x 3 -- must shift left by its span start or the
        # compositor draws the marker columns in front of it instead of the
        # letter. Columns pushed in from the right land beyond the width and
        # are masked out, so their value does not matter.
        sh = shifts[g] if shifts else 0
        rows = []
        for r in range(8):
            hi = t[r * 2 + 1]
            # ink = the DARK shades (2 and 3), which is exactly the high 2bpp
            # plane; a clean 2-colour font ranks to shades {0, 3}, where both
            # planes are equal and this reads either.
            rows.append((hi << sh) & 0xFF)
        glyphs.append(rows)
    # A MONO sheet's space must be blank (a mis-laid-out grid would silently
    # shift every character otherwise). A VWF sheet skips the check: its
    # marker presence already proves the layout, and its space may
    # legitimately carry ink (a variable-width font from a converted project
    # has a space of two dark columns on a light-on-dark sheet).
    if shifts is None and glyphs[0] != [0] * 8:
        raise AssetError("%s: the first glyph cell (character 32, the space) "
                         "is not blank -- the grid must start at the space "
                         "glyph, or every character shifts" % path)
    return glyphs


#: A font sheet's glyph grid is 16 cells wide. Not a guess and not a knob: GB
#: Studio's reader walks a fixed 16-column grid (`lib/fonts/fontData.ts`), and
#: every font PNG it ships is 128 px across.
FONT_GRID_COLS = 16


def _font_transparent(r, g, b, a):
    """The reference engine's own transparency test for a FONT sheet.

    `fontDataIndexFn` (`lib/fonts/fontData.ts`) is
    `g > 249 || (r > 249 && b > 249)` - which catches magenta (255, 0, 255) on
    the second clause and pure green (0, 255, 0) on the first. Note it just
    misses the reference engine's own white (224, 248, 207), whose g is 248: one below the
    cut, deliberately.

    We additionally treat a fully transparent pixel as transparent. The
    reference ignores alpha entirely, but the only images that differ are ones
    it would already misread, and an indexed PNG whose marker colour is also in
    `tRNS` is a shape a hand-made sheet can easily have.
    """
    if a == 0:
        return True
    return g > 249 or (r > 249 and b > 249)


def _font_spans(path):
    """`(spans, seen_marker)` for a font PNG: one `(lo, hi)` ink-column span
    per glyph cell (None for a wholly transparent cell), row-major, plus
    whether ANY marker pixel exists. The one scan `png_to_font_widths` and
    `png_to_font_sheet` both read, so the width and the re-basing shift can
    never disagree about where a glyph starts."""
    width, height, rows = png_rgba_rows(path)
    cell = 8
    if width != FONT_GRID_COLS * cell:
        raise AssetError("%s: a font sheet must be %d px wide (a %d-cell grid "
                         "of 8x8 glyphs); this image is %d"
                         % (path, FONT_GRID_COLS * cell, FONT_GRID_COLS, width))
    if height % cell:
        raise AssetError("%s: a font sheet's height must be a multiple of 8; "
                         "this image is %d" % (path, height))
    spans = []
    seen_marker = False
    for gy in range(height // cell):
        for gx in range(FONT_GRID_COLS):
            lo, hi = None, None
            for x in range(cell):
                col_x = gx * cell + x
                opaque = False
                for y in range(cell):
                    r, g, b, a = rows[gy * cell + y][col_x]
                    if _font_transparent(r, g, b, a):
                        seen_marker = True
                    else:
                        opaque = True
                if opaque:
                    if lo is None:
                        lo = x
                    hi = x
            spans.append(None if lo is None else (lo, hi))
    return spans, seen_marker


def png_to_font_widths(path):
    """Per-glyph ADVANCE WIDTHS for a variable-width font PNG, or None.

    Returns one width per glyph cell, in the same row-major order
    `png_to_font_1bpp` reads bitmaps in (cell i renders character 32 + i), or
    **None when the sheet is fixed-width** - i.e. when it carries no
    transparent pixel at all, which is exactly how the reference engine decides
    `isVariableWidth`. `gbs-mono.png` has none and yields None; `gbs-var.png`
    has 1,496 magenta pixels and yields widths.

    The width is the horizontal span of NON-TRANSPARENT pixels in the cell,
    `maxX - minX + 1` (`shared/lib/tiles/indexedImage.ts`). Three properties of
    that definition are easy to get wrong and are what this docstring is for:

    * **The trim is TWO-SIDED.** Leading transparent columns shift the glyph
      left rather than acting as left bearing, because `minX` moves. A sheet
      that pads on the left is drawn tight, not indented.
    * **Transparent is not the same as blank.** The span is measured against
      the MARKER colour, not against ink, so the space glyph - solid paper with
      a magenta tail - measures 4 px wide in `gbs-var`, not 0.
    * **A marker pixel INSIDE the span is not a hole**, it is drawn. The reference engine
      turns it into index 255, i.e. both bitplanes set, i.e. BLACK. So the
      marker colour is only safe in the leading and trailing columns, and a
      sheet that uses it as a see-through hole will render solid.

    A fully transparent cell measures 0 and never advances the pen - GB
    Studio's dead-glyph shape.

    This reads PIXELS only and is independent of `png_to_font_1bpp`, which
    reads the same sheet through the ranked-luma path for its bitmaps. Keep
    them that way: ranking folds the marker colour into a shade, so widths
    cannot be recovered after it.
    """
    spans, seen_marker = _font_spans(path)
    if not seen_marker:
        return None
    return [0 if sp is None else sp[1] - sp[0] + 1 for sp in spans]

def png_to_4bpp_bkg_tiles(path):
    """Convert an indexed (<=16 colour) tileset PNG to packed-nibble 4bpp tile
    bytes -- the BACKGROUND-tier sibling of png_to_4bpp_tiles.

    Same packed-nibble interchange as the 4bpp sprite tier (32 bytes/tile), so
    the per-console bkg engine (PCE VDC planar / SMS-GG VDP planar / Lynx Suzy
    strips) reads one format. Returns None when the PNG is not an indexed
    <=16-colour image (the caller then keeps the 2bpp png_to_gb_tiles path)."""
    return png_to_4bpp_tiles(path)


# ---------------------------------------------------------------------------
# Named-sprite sheets (a PNG + a sidecar manifest cut into named sub-sprites)
# ---------------------------------------------------------------------------
#
# A sheet PNG `foo.png` may carry a sidecar `foo.sprites.toml` next to it: an
# array of `[[sprite]]` tables, each a `name` plus a `rect = [x, y, w, h]` pixel
# rectangle (w, h multiples of 8). The pipeline then concatenates each named
# sub-sprite's 8x8 tiles (row-major within its rect, array order) into the
# asset's `<name>_tiles` array, and emits per-sprite
# `#define <sprite>_tile <first-tile-index>` / `_w` / `_h` (tile units) so a
# program can `sprite.set_meta(slot, knight_tile, knight_w, knight_h)`. Without a
# sidecar, the PNG keeps the flat 8x8-grid behaviour.

def manifest_path(png_path):
    """Sidecar manifest path for a sheet PNG (`foo.png` -> `foo.sprites.toml`)."""
    stem = os.path.splitext(png_path)[0]
    return stem + ".sprites.toml"


def load_sprite_manifest(png_path):
    """Ordered [(name, x, y, w, h)] from a sheet's sidecar manifest, or None.

    The sidecar is TOML: an array of `[[sprite]]` tables, each a `name` plus a
    `rect = [x, y, w, h]` (pixels). Array order is the tile layout order."""
    side = manifest_path(png_path)
    if not os.path.exists(side):
        return None
    import toml
    try:
        raw = toml.load(side)
    except (OSError, toml.TomlDecodeError) as e:
        raise AssetError("%s: bad sprite manifest: %s" % (side, e))
    out = []
    for entry in raw.get("sprite", []):
        name, rect = entry.get("name"), entry.get("rect")
        if name is None or not (isinstance(rect, (list, tuple)) and len(rect) == 4):
            raise AssetError("%s: each [[sprite]] needs a name and "
                             "rect = [x, y, w, h]" % side)
        out.append((re.sub(r"[^A-Za-z0-9_]", "_", str(name)),
                    *(int(v) for v in rect)))
    return out


def write_sprite_manifest(png_path, sprites):
    """Write a sheet's sidecar manifest (`<png>.sprites.toml`).

    `sprites` is an ordered mapping name -> [x, y, w, h] (px) or a sequence of
    (name, [x, y, w, h]) pairs; the written order is the tile-layout order. This
    is the one writer the IDE's slicer and the project gen_sprites.py scripts
    both call, so the on-disk format lives in exactly one place.

    A SPARSE entry adds two more fields, `(name, rect, cols, mask)`: `cols` is
    the frame's true width in TILES and `mask` a per-column blank bitmap (bit c
    = column c draws nothing). The rect then holds only the DRAWN columns,
    packed together - that is the point, a blank cell costs neither a tile nor
    one of the GB's 10 sprites per scanline. Written only when a mask is
    non-zero, so every existing sheet's manifest is byte-identical."""
    items = sprites.items() if hasattr(sprites, "items") else sprites
    lines = ["# MosaiK8 named-sprite sheet manifest -- order = tile-layout order.",
             "# Each sprite is a name + a [x, y, w, h] pixel rect (w, h x8).", ""]
    for entry in items:
        name, rect = entry[0], entry[1]
        cols = int(entry[2]) if len(entry) > 2 else 0
        mask = int(entry[3]) if len(entry) > 3 else 0
        x, y, w, h = (int(v) for v in rect)
        lines += ["[[sprite]]", 'name = "%s"' % name,
                  "rect = [%d, %d, %d, %d]" % (x, y, w, h)]
        if mask:
            lines += ["cols = %d" % cols, "mask = %d" % mask]
        lines.append("")
    _write_atomic(manifest_path(png_path), "\n".join(lines).encode("utf-8"))


def append_frame_descs(png_path, frames):
    """Append DESCRIPTOR FRAMES to a sheet's manifest (`[[frame]]` entries).

    A descriptor frame is the reference engine's metasprite model: a named frame that owns
    NO pixels of its own -- it composes the sheet's `[[sprite]]` entries (the
    tile POOL, deduped cells) at authored offsets. `frames` is a sequence of
    `(name, pw, ph, objs)` where `objs` is `[(dy, dx, cell, props), ...]`:
    offsets in px from the frame's top-left, `cell` the INDEX of a [[sprite]]
    pool entry, `props` extra OAM bits (0 normally -- flips are baked into the
    cells so SMS/GG render them too). Rows may OVERLAP and a cell may REPEAT,
    which is what the dense rect + column mask cannot say.

    Written as its own section so `sheet_sprite_defs`' cumulative tile offsets
    (which walk [[sprite]] rects) are untouched; a sheet with no [[frame]]
    entries is byte-identical."""
    lines = ["", "# Descriptor frames: composed from the pool cells above at",
             "# authored offsets (objs = [dy, dx, cell, props] per object).", ""]
    for name, pw, ph, objs in frames:
        lines += ["[[frame]]",
                  'name = "%s"' % re.sub(r"[^A-Za-z0-9_]", "_", str(name)),
                  "size = [%d, %d]" % (int(pw), int(ph)),
                  "objs = [%s]" % ", ".join(
                      "[%d, %d, %d, %d]" % (int(dy), int(dx), int(c), int(p))
                      for (dy, dx, c, p) in objs),
                  ""]
    with open(manifest_path(png_path), "ab") as f:
        f.write("\n".join(lines).encode("utf-8"))


def sprite_frame_descs(png_path):
    """{frame name: (pw, ph, [(dy, dx, cell NAME, props), ...])} for a sheet's
    `[[frame]]` descriptor entries, or {} for a sheet without any.

    The on-disk `objs` reference pool cells by their [[sprite]] INDEX (stable
    within the file); they resolve to the cell's NAME here so a consumer can
    look tiles up in the same name->tile map every other frame uses."""
    side = manifest_path(png_path)
    if not os.path.exists(side):
        return {}
    import toml
    try:
        raw = toml.load(side)
    except (OSError, toml.TomlDecodeError) as e:
        raise AssetError("%s: bad sprite manifest: %s" % (side, e))
    cells = [re.sub(r"[^A-Za-z0-9_]", "_", str(e.get("name")))
             for e in raw.get("sprite", [])]
    out = {}
    for entry in raw.get("frame", []):
        name = re.sub(r"[^A-Za-z0-9_]", "_", str(entry.get("name")))
        size = entry.get("size") or [0, 0]
        objs = []
        for o in (entry.get("objs") or []):
            dy, dx, cell = int(o[0]), int(o[1]), int(o[2])
            props = int(o[3]) if len(o) > 3 else 0
            if not (0 <= cell < len(cells)):
                raise AssetError("%s: [[frame]] %s references pool cell %d "
                                 "(sheet has %d)" % (side, name, cell,
                                                     len(cells)))
            objs.append((dy, dx, cells[cell], props))
        out[name] = (int(size[0]), int(size[1]), objs)
    return out


def sprite_frame_masks(png_path):
    """{sprite name: (cols, mask)} for a sheet's SPARSE entries, or {}.

    `cols` is the frame's true width in tiles (the rect holds only the drawn
    ones) and `mask` the per-column blank bitmap. A sheet with no sparse entry
    returns an empty map and everything downstream stays as it was."""
    side = manifest_path(png_path)
    if not os.path.exists(side):
        return {}
    import toml
    try:
        raw = toml.load(side)
    except (OSError, toml.TomlDecodeError) as e:
        raise AssetError("%s: bad sprite manifest: %s" % (side, e))
    out = {}
    for entry in raw.get("sprite", []):
        mask = int(entry.get("mask", 0) or 0)
        if not mask:
            continue
        name = re.sub(r"[^A-Za-z0-9_]", "_", str(entry.get("name")))
        out[name] = (int(entry.get("cols", 0) or 0), mask)
    return out


def _slice_rows(rows, x, y, w, h):
    """Extract a w*h pixel sub-rectangle (rows of pixel values) at (x, y)."""
    return [row[x:x + w] for row in rows[y:y + h]]


def sheet_sprite_defs(png_path):
    """[(sprite_name, tile_offset, w_tiles, h_tiles)] for a manifested sheet,
    or [] if it has no manifest. Offsets are cumulative in manifest order
    (independent of colour depth), so codegen can emit the per-sprite defines
    without re-reading the pixels."""
    manifest = load_sprite_manifest(png_path)
    if not manifest:
        return []
    defs = []
    offset = 0
    for name, _x, _y, w, h in manifest:
        if w % 8 or h % 8:
            raise AssetError("%s: sprite '%s' is %dx%d; w/h must be multiples "
                             "of 8 pixels" % (png_path, name, w, h))
        wt, ht = w // 8, h // 8
        defs.append((name, offset, wt, ht))
        offset += wt * ht
    return defs


def reorder_tiles_8x16(tiles, w, h):
    """Permute ONE sprite's tile block from row-major to COLUMN-major -- the
    8x16 OBJ mode's data order (G4).

    In 8x16 mode the hardware pairs tile N with N+1 as its VERTICAL neighbour,
    but a row-major frame stores the HORIZONTAL neighbour there. Column-major
    storage makes cell (c, r) land at index c*h + r, so vertical pairs are
    consecutive AND every object's tile index (c*h + 2p, h even) is even --
    exactly what the mode requires. The PNG stays readable; only these encoded
    bytes are re-ordered. `w`/`h` in 8x8 tiles; `h` must be even (the callers
    gate the mode on that)."""
    out = bytearray()
    for c in range(w):
        for r in range(h):
            i = (r * w + c) * 16
            out += tiles[i:i + 16]
    return bytes(out)


_BIT_REVERSE = bytes(int("{:08b}".format(b)[::-1], 2) for b in range(256))


def mirror_cell_tiles(tiles, src, w, h, col_major=False):
    """The w x h tile CELL starting at tile `src` of an ENCODED 2bpp stream,
    mirrored left-right: every tile row's bits reversed (leftmost pixel is the
    MSB of both plane bytes) and the cell's tile columns in reverse order. The
    result is in the SAME layout as the stream - row-major, or column-major
    under 8x16 OBJ mode (`reorder_tiles_8x16`) - so it can be appended to the
    sheet and addressed exactly like one of its own cells. The SMS/GG
    soft-flip bake under per-room residency (`mosaik_anim.residency_flip_bake`)."""
    def tile(i):
        at = (src + i) * GB_TILE_BYTES
        return tiles[at:at + GB_TILE_BYTES].translate(_BIT_REVERSE)
    out = bytearray()
    if col_major:
        for c in range(w):
            for r in range(h):
                out += tile((w - 1 - c) * h + r)
    else:
        for r in range(h):
            for c in range(w):
                out += tile(r * w + (w - 1 - c))
    return bytes(out)


def sheet_to_tiles(png_path, sprite_bpp, obj_8x16=False):
    """Concatenate a manifested sheet's named sub-sprites into one tile stream
    at the target depth (4bpp packed-nibble if sprite_bpp==4 and the PNG is a
    >4-colour indexed image, else GB 2bpp). Returns the tile bytes.

    With `obj_8x16` each sub-sprite's tiles are stored COLUMN-major (vertical
    pairs, see reorder_tiles_8x16); each entry's OFFSET in the stream is
    unchanged, so the manifest's `<name>_tile` defines and the clips module's
    frame bases stay valid -- only the metasprite fan (which changes in
    lockstep) reads the intra-frame order."""
    manifest = load_sprite_manifest(png_path)
    if not manifest:
        return None
    four = sprite_bpp == 4 and (png_indices(png_path) is not None
                                and len(png_indices(png_path)[3]) > 4)
    if four:
        _w, _h, rows, _pal = png_indices(png_path)
        encode = lambda sw, sh, sr: indices_to_4bpp_tiles(sw, sh, sr)
    else:
        _w, _h, rows = png_to_shades(png_path)
        encode = lambda sw, sh, sr: shades_to_gb_tiles(sw, sh, sr)
    out = bytearray()
    for name, x, y, w, h in manifest:
        block = encode(w, h, _slice_rows(rows, x, y, w, h))
        if obj_8x16 and not four:
            # manifest rects are PIXELS; the permutation works in TILES.
            tw, th = w // 8, h // 8
            if th % 2:
                raise AssetError(
                    "%s: sprite %r is %d tiles tall -- 8x16 OBJ mode "
                    "([build] obj_8x16) needs an EVEN tile height (pad the "
                    "frame or drop the flag)" % (png_path, name, th))
            block = reorder_tiles_8x16(block, tw, th)
        out += block
    return bytes(out)


def asset_c_name(path):
    """Derive the C-identifier base name for an asset from its filename.

    `assets/player-ship.png` -> `player_ship` (symbols `player_ship_tiles`
    and `player_ship_tile_count`).
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    name = re.sub(r"[^A-Za-z0-9_]", "_", stem)
    if not name or name[0].isdigit():
        name = "_" + name
    return name


def build_is_4bpp(paths, sprite_bpp):
    """Decide whether this build's sprite assets are encoded at 4bpp.

    A build uses the native 4bpp tier when the target console is 4bpp-capable
    (`sprite_bpp == 4`, i.e. Lynx / PC Engine) AND at least one asset is an
    indexed PNG that actually needs more than 4 colours. Otherwise it stays on
    the universal GB 2bpp path (so every existing program -- hand-authored
    tiles, <=4-colour assets, every GB-family build -- is byte-identical)."""
    if sprite_bpp != 4:
        return False
    for path in paths:
        info = png_indices(path)
        if info is not None and len(info[3]) > 4:
            return True
    return False


def bkg_build_is_4bpp(paths):
    """Decide whether a BACKGROUND (scene tileset) built from these PNG(s) uses
    the native 4bpp (16-colour) tier -- the bkg sibling of build_is_4bpp.

    True only when EVERY tileset image is an indexed <=16-colour PNG (so the
    packed-nibble 4bpp encode is defined for all of them) AND at least one
    carries more than 4 colours (else the 2bpp path is already the right,
    byte-identical answer). Unlike build_is_4bpp there is no sprite_bpp argument:
    the caller gates on mosaik.platforms.BKG_4BPP_ENGINE (which consoles have a
    4bpp bkg backend), and the transpiler bakes the per-platform TILESET fork."""
    any_rich = False
    for path in paths:
        info = png_indices(path)
        if info is None:
            return False
        if len(info[3]) > 4:
            any_rich = True
    return any_rich


def rgb555_words(colors):
    """Pack [(r,g,b)] (0..255 each) into 15-bit 0RRRRRGGGGGBBBBB u16 words.

    The portable palette encoding a scene BKG_PALETTE16 carries: platform-neutral
    (scenes.mos is one source compiled per console), so `palette.load_bkg16`
    unpacks and rounds each entry to the target's native colour depth at runtime
    (via gbs_rgb) rather than the codegen baking native words as the sprite
    <name>_palette16 does. 5-5-5 covers every target (GBC's 5-bit is the deepest)."""
    out = []
    for (r, g, b) in colors:
        out.append(((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3))
    return out


# ---------------------------------------------------------------------------
# Fitting an authored palette into a SHALLOW console gamut
# ---------------------------------------------------------------------------
# The SMS renders 2 bits a channel: 4 levels, 64 colours in total. Rounding
# each channel to its nearest level is Euclidean-nearest in RGB and therefore
# looks optimal per swatch -- but it sends every desaturated mid-tone onto the
# grey diagonal, because at that depth grey really is the nearest point. That
# is the right answer for one swatch in isolation and the wrong one for a
# 4-colour palette painting flat bands of sky: hue is what the eye reads
# there, and the reference-engine sample's pink parallax band came out grey.
#
# So the unit of work is the PALETTE, not the colour: pick all four at once,
# minimising perceptual error subject to the entries staying distinct and
# keeping their lightness order. Two rules earn their keep and are worth
# stating, since both were arrived at by looking at renders:
#
#   * the metric is CIE94, not plain CIELAB. Pure blue sits at a* +35, so an
#     unnormalised a* term reads the sample's dark navy ground as closer to
#     PLUM than to blue. CIE94 compares hue ANGLE and normalises chroma by the
#     source's own, which keeps navy navy.
#   * a chromatic source may not land on an INTERIOR grey (_FIT_CHROMA_MIN).
#     Every honest metric answers "grey" for a dusty pink in 64 colours; the
#     constraint is the deliberate part, not a fudged weight. Black and white
#     are exempt because they are the ends of the lightness axis rather than
#     a desaturation -- no gamut offers chroma there, and banning them sent
#     the sample's warm off-white clouds to yellow when white was both nearer
#     and what the reference renders.
#
# The Game Gear needs none of this (4 bits a channel = 4096 colours, where
# per-channel nearest is already within a few units), so only the SMS path
# calls it.

_FIT_CHROMA_MIN = 12.0    # below this the source counts as neutral
_FIT_L_TOL = 5.0          # lightness order is only enforced past this gap
_FIT_CANDIDATES = 10      # per-entry shortlist; the joint search is its product


def _srgb_linear(c):
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rgb_to_lab(rgb):
    """sRGB 0..255 -> CIE L*a*b* (D65)."""
    r, g, b = (_srgb_linear(v) for v in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b)
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t):
        return t ** (1.0 / 3.0) if t > 0.008856 else (7.787 * t + 16.0 / 116.0)

    fx, fy, fz = f(x), f(y), f(z)
    return (116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz))


def _chroma(lab):
    return (lab[1] * lab[1] + lab[2] * lab[2]) ** 0.5


def _de94_sq(src, dst):
    """Squared CIE94 difference (graphic-arts weights), src as the reference."""
    dl = src[0] - dst[0]
    c1, c2 = _chroma(src), _chroma(dst)
    dc = c1 - c2
    da, db = src[1] - dst[1], src[2] - dst[2]
    dh2 = da * da + db * db - dc * dc
    if dh2 < 0.0:
        dh2 = 0.0
    sc = 1.0 + 0.045 * c1
    sh = 1.0 + 0.015 * c1
    return dl * dl + (dc / sc) ** 2 + dh2 / (sh * sh)


def _level_cube(levels):
    step = 255.0 / (levels - 1)
    vals = [int(round(i * step)) for i in range(levels)]
    return [((r, g, b), (ri, gi, bi))
            for ri, r in enumerate(vals)
            for gi, g in enumerate(vals)
            for bi, b in enumerate(vals)]


_CUBE_CACHE = {}


def _cube(levels):
    got = _CUBE_CACHE.get(levels)
    if got is None:
        got = [(rgb, idx, rgb_to_lab(rgb)) for rgb, idx in _level_cube(levels)]
        _CUBE_CACHE[levels] = got
    return got


def _is_interior_grey(idx, levels):
    """A grey that is neither black nor white -- the only ones a chromatic
    source is forbidden to land on."""
    return idx[0] == idx[1] == idx[2] and 0 < idx[0] < levels - 1


def _shortlist(color, levels):
    """The candidates one source colour may be fitted to, best first."""
    cube = _cube(levels)
    lab = rgb_to_lab(color)
    pool = cube
    if _chroma(lab) >= _FIT_CHROMA_MIN:
        pool = [e for e in cube if not _is_interior_grey(e[1], levels)]
    ranked = sorted(pool, key=lambda e: (_de94_sq(lab, e[2]), e[0]))
    return ranked[:_FIT_CANDIDATES]


def _fit_lists(colors, labs, shortlists):
    """Best assignment of one palette given each entry's candidate list.

    Returns (choices, cost), or None when the constraints admit none."""
    n = len(colors)
    best = [None, None]
    chosen = [None] * n

    def walk(i, cost):
        if best[1] is not None and cost >= best[1]:
            return                                   # branch and bound
        if i == n:
            best[0], best[1] = list(chosen), cost
            return
        for entry in shortlists[i]:
            ok = True
            for j in range(i):
                if colors[j] == colors[i]:
                    continue
                if chosen[j][1] == entry[1]:         # distinct sources stay distinct
                    ok = False
                    break
                dsrc = labs[j][0] - labs[i][0]
                if abs(dsrc) >= _FIT_L_TOL and \
                        dsrc * (chosen[j][2][0] - entry[2][0]) < 0:
                    ok = False                       # lightness order inverted
                    break
            if not ok:
                continue
            chosen[i] = entry
            walk(i + 1, cost + _de94_sq(labs[i], entry[2]))
            chosen[i] = None

    walk(0, 0.0)
    return None if best[0] is None else (best[0], best[1])


def _fit_one(colors, levels, pins):
    """(choices, cost) for one palette, honouring `pins` where it can."""
    labs = [rgb_to_lab(c) for c in colors]
    free = [_shortlist(c, levels) for c in colors]
    if pins:
        pinned = list(free)
        hit = False
        for i, c in enumerate(colors):
            keep = [e for e in pinned[i] if e[1] == pins.get(c)]
            if keep:
                pinned[i] = keep
                hit = True
        if hit:
            got = _fit_lists(colors, labs, pinned)
            if got is not None:
                return got
            # The pins collide inside THIS palette; fit it freely instead.
    got = _fit_lists(colors, labs, free)
    if got is not None:
        return got
    # More entries than the constraints leave room for. Per-entry nearest
    # always answers.
    cube = _cube(levels)
    picks = [min(cube, key=lambda e: (_de94_sq(lab, e[2]), e[0])) for lab in labs]
    return picks, sum(_de94_sq(lab, e[2]) for lab, e in zip(labs, picks))


def fit_palette_levels(colors, levels=4, pins=None):
    """Fit one palette into a `levels`-per-channel gamut, jointly.

    `colors` is a list of (r, g, b) 0..255. Returns a matching list of
    (ri, gi, bi) channel INDEXES in 0..levels-1, which the caller packs into
    the console's native word. Deterministic."""
    return [e[1] for e in _fit_one(colors, levels, pins)[0]]


def fit_palettes(palettes, levels=4):
    """Fit a whole palette LIBRARY, keeping a shared colour consistent.

    Fitting each palette on its own is not enough: worlds share colours
    between palettes on purpose (the reference-engine sample paints one sky colour
    into four of them), and two palettes are free to resolve the same source
    differently under their own distinctness pressure. On screen that is a
    hard seam where two differently-palletted areas meet -- something the
    deeper consoles never show, because there the shared colour stays one
    colour.

    So a source colour used by more than one palette is decided GLOBALLY:
    each of its candidates is scored by re-fitting every palette that
    contains it, and the cheapest total wins. Colours are decided
    most-shared-first, and each decision pins the ones after it. That is
    what keeps the sample's sky one colour while still letting the palette
    that also holds a near-white cloud take white for it."""
    palettes = [list(p) for p in palettes]
    users = {}
    for i, pal in enumerate(palettes):
        for c in pal:
            users.setdefault(c, set()).add(i)
    shared = sorted((c for c, u in users.items() if len(u) > 1),
                    key=lambda c: (-len(users[c]), c))

    pins = {}
    for color in shared:
        idxs = sorted(users[color])
        best = None
        for entry in _shortlist(color, levels):
            trial = dict(pins)
            trial[color] = entry[1]
            fits = [_fit_one(palettes[i], levels, trial) for i in idxs]
            total = sum(cost for _, cost in fits)
            # A candidate only counts as shared if every palette actually
            # took it -- a pin one of them had to drop is not consistency.
            kept = all(choices[palettes[i].index(color)][1] == entry[1]
                       for i, (choices, _) in zip(idxs, fits))
            key = (0 if kept else 1, total, entry[0])
            if best is None or key < best[0]:
                best = (key, entry[1])
        pins[color] = best[1]

    return [[e[1] for e in _fit_one(pal, levels, pins)[0]] for pal in palettes]


def load_assets(paths, sprite_bpp=2, obj_8x16=False):
    """Convert asset PNGs to [(c_name, data, bpp)], checking name clashes.

    `sprite_bpp` is the target console's native sprite depth (PLATFORM_CAPS).
    When the build qualifies for the 4bpp tier (see build_is_4bpp) every sprite
    asset is encoded packed-nibble 4bpp; otherwise GB 2bpp. The depth is
    uniform across the build so the sprite engine has a single source format.

    `obj_8x16` (`[build] obj_8x16`, effective on the GB family and on
    SMS/GG) stores every manifested
    sub-sprite's tiles COLUMN-major (see reorder_tiles_8x16); it REFUSES a
    plain unmanifested sheet, because without per-frame rects there is no
    geometry to reorder by and the sprite would silently scramble on screen.
    """
    four = build_is_4bpp(paths, sprite_bpp)
    assets = []
    seen = {}
    for path in paths:
        name = asset_c_name(path)
        if name in seen:
            raise AssetError(
                "asset name clash: %s and %s both map to '%s_tiles'"
                % (seen[name], path, name))
        seen[name] = path
        sheet = sheet_to_tiles(path, 4 if four else 2, obj_8x16=obj_8x16)
        if sheet is not None:
            # Named-sprite sheet: tiles already sliced + concatenated.
            assets.append((name, sheet, 4 if four else 2))
        elif obj_8x16:
            raise AssetError(
                "%s: [build] obj_8x16 needs every sprite asset to be a "
                "MANIFESTED named sheet (a .sprites.toml beside the PNG) -- "
                "the 8x16 tile reorder needs each frame's rect" % path)
        elif four:
            data = png_to_4bpp_tiles(path)
            if data is None:
                raise AssetError(
                    "%s: a 4bpp (16-colour) build needs every sprite asset to "
                    "be an indexed PNG with <=16 colours" % path)
            assets.append((name, data, 4))
        else:
            assets.append((name, png_to_gb_tiles(path), 2))
    return assets


def load_asset_sprite_defs(paths):
    """[(sprite_name, tile_offset, w_tiles, h_tiles)] across all manifested
    sheets, for codegen to emit per-sprite set_meta defines."""
    defs = []
    seen = {}
    for path in paths:
        for entry in sheet_sprite_defs(path):
            name = entry[0]
            if name in seen:
                raise AssetError(
                    "sprite name clash: '%s' defined in %s and %s"
                    % (name, seen[name], path))
            seen[name] = path
            defs.append(entry)
    return defs


def png_palette(path):
    """The RGB palette of an indexed PNG with at most 4 entries, or None.

    Returns [(r, g, b)] x 4 (padded with black) for the PNGs whose indices
    map literally to GB colour values -- the same condition png_to_shades
    uses -- so graphics.palette programs can recolor an asset with its
    authored colors (`<name>_palette`). Larger palettes and true-colour
    images quantize through the luma path and carry no palette.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise AssetError("cannot read asset: %s" % e)
    _w, _h, colour_type, palette, _trns, _pixels = _decode_png(data)
    if colour_type != 3 or palette is None or len(palette) > 4:
        return None
    colors = [tuple(c) for c in palette]
    while len(colors) < 4:
        colors.append((0, 0, 0))
    return colors


def png_palette16(path):
    """The RGB palette of an indexed PNG with at most 16 entries, padded to 16
    (with black), or None. The authored 16-colour palette of a 4bpp sprite
    asset -- the Lynx engine loads it into Mikey's pens so 4bpp sprites show
    their real colours; emitted as `<name>_palette16`."""
    info = png_indices(path)
    if info is None:
        return None
    colors = list(info[3])
    while len(colors) < MAX_4BPP_COLORS:
        colors.append((0, 0, 0))
    return colors


def png_palette_bkg16(path):
    """The 16-entry RGB palette of an indexed (<=16 colour) tileset PNG, padded
    to 16 with black, or None -- the BACKGROUND-tier sibling of png_palette16.

    The authored 16-colour palette a 4bpp-background build loads with
    `palette.load_bkg16` (emitted as a scene `BKG_PALETTE16`). Identical
    encoding to the sprite variant; kept as a separate name so the two tiers
    read independently."""
    return png_palette16(path)


def load_asset_palettes(paths):
    """[(c_name, [(r,g,b)] x 4)] for the indexed-PNG assets that carry one."""
    palettes = []
    for path in paths:
        try:
            colors = png_palette(path)
        except AssetError as e:
            raise AssetError("%s: %s" % (path, e))
        if colors is not None:
            palettes.append((asset_c_name(path), colors))
    return palettes


def load_asset_palettes16(paths):
    """[(c_name, [(r,g,b)] x 16)] for indexed (<=16 colour) PNG assets -- the
    authored palettes carried by the 4bpp sprite tier (`<name>_palette16`)."""
    palettes = []
    for path in paths:
        colors = png_palette16(path)
        if colors is not None:
            palettes.append((asset_c_name(path), colors))
    return palettes


# ---------------------------------------------------------------------------
# PNG writing (used by asset-generator scripts and tests; filter 0 only)
# ---------------------------------------------------------------------------

def _png_chunk(ctype, payload):
    return (struct.pack(">I", len(payload)) + ctype + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))


def _write_atomic(path, data):
    """Write `data` to `path` via a temp-file + os.replace swap.

    These writers overwrite SOURCE assets in place (the Studio's pixel and
    palette editors call them on authored art), so a crash mid-write must
    never destroy the original -- either the old file or the complete new one
    survives."""
    tmp = "%s.tmp" % path
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def write_png_indexed(path, width, height, indices, palette, trns=None):
    """Write an 8-bit indexed PNG.

    `indices` is either rows of palette indices (a list of `height` rows, each
    `width` long) or a single flat list of `width * height` indices (reshaped
    into rows here). A flat list used to be written verbatim -- `bytes(int)`
    silently produced a corrupt, all-zero image -- so it is now reshaped and
    validated instead.
    """
    if indices and not isinstance(indices[0], (list, tuple, bytes, bytearray)):
        if len(indices) != width * height:
            raise AssetError(
                "write_png_indexed: flat indices length %d != width*height %d"
                % (len(indices), width * height))
        indices = [indices[y * width:(y + 1) * width] for y in range(height)]
    else:
        for y, row in enumerate(indices):
            if len(row) != width:
                raise AssetError(
                    "write_png_indexed: row %d has %d indices, expected width %d"
                    % (y, len(row), width))
        if len(indices) != height:
            raise AssetError(
                "write_png_indexed: %d rows, expected height %d"
                % (len(indices), height))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)
    plte = b"".join(bytes(c) for c in palette)
    raw = b"".join(b"\x00" + bytes(row) for row in indices)
    chunks = [_png_chunk(b"IHDR", ihdr), _png_chunk(b"PLTE", plte)]
    if trns is not None:
        chunks.append(_png_chunk(b"tRNS", bytes(trns)))
    chunks.append(_png_chunk(b"IDAT", zlib.compress(raw)))
    chunks.append(_png_chunk(b"IEND", b""))
    _write_atomic(path, _PNG_SIGNATURE + b"".join(chunks))


def rewrite_png_palette(path, new_palette):
    """Recolor an indexed PNG in place: keep its pixel indices (and any tRNS),
    replace only the PLTE entries.

    `new_palette` is a sequence of (r, g, b) with exactly as many entries as the
    image's current palette (<=16). The single writer the Studio's Palette
    designer calls, so a recolored asset round-trips through the very same
    PNG->2bpp/4bpp + `png_palette`/`png_palette16` pipeline the build, the
    composer's color pass-through, and the Scene-editor preview already consume --
    the indices are untouched, so only the colours change. Raises AssetError on a
    non-indexed PNG or an entry-count mismatch.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise AssetError("cannot read asset: %s" % e)
    width, height, colour_type, palette, trns, pixels = _decode_png(data)
    if colour_type != 3 or palette is None:
        raise AssetError(
            "%s is not an indexed PNG, so it has no palette to recolor" % path)
    new = [tuple(int(v) for v in c[:3]) for c in new_palette]
    if len(new) != len(palette):
        raise AssetError(
            "palette has %d entries but the image uses %d"
            % (len(new), len(palette)))
    index_rows = [[px[0] for px in row] for row in pixels]
    write_png_indexed(path, width, height, index_rows, new, trns)


def write_png_rgba(path, width, height, pixels):
    """Write an 8-bit RGBA PNG. `pixels` = rows of (r, g, b, a) tuples."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(
        b"\x00" + bytes(v for px in row for v in px) for row in pixels)
    _write_atomic(path, _PNG_SIGNATURE
                  + _png_chunk(b"IHDR", ihdr)
                  + _png_chunk(b"IDAT", zlib.compress(raw))
                  + _png_chunk(b"IEND", b""))
