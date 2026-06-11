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


def load_assets(paths):
    """Convert asset PNGs to [(c_name, gb_2bpp_bytes)], checking name clashes."""
    assets = []
    seen = {}
    for path in paths:
        name = asset_c_name(path)
        if name in seen:
            raise AssetError(
                "asset name clash: %s and %s both map to '%s_tiles'"
                % (seen[name], path, name))
        seen[name] = path
        assets.append((name, png_to_gb_tiles(path)))
    return assets


# ---------------------------------------------------------------------------
# PNG writing (used by asset-generator scripts and tests; filter 0 only)
# ---------------------------------------------------------------------------

def _png_chunk(ctype, payload):
    return (struct.pack(">I", len(payload)) + ctype + payload
            + struct.pack(">I", zlib.crc32(ctype + payload) & 0xFFFFFFFF))


def write_png_indexed(path, width, height, indices, palette, trns=None):
    """Write an 8-bit indexed PNG. `indices` = rows of palette indices."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)
    plte = b"".join(bytes(c) for c in palette)
    raw = b"".join(b"\x00" + bytes(row) for row in indices)
    chunks = [_png_chunk(b"IHDR", ihdr), _png_chunk(b"PLTE", plte)]
    if trns is not None:
        chunks.append(_png_chunk(b"tRNS", bytes(trns)))
    chunks.append(_png_chunk(b"IDAT", zlib.compress(raw)))
    chunks.append(_png_chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(_PNG_SIGNATURE + b"".join(chunks))


def write_png_rgba(path, width, height, pixels):
    """Write an 8-bit RGBA PNG. `pixels` = rows of (r, g, b, a) tuples."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(
        b"\x00" + bytes(v for px in row for v in px) for row in pixels)
    with open(path, "wb") as f:
        f.write(_PNG_SIGNATURE
                + _png_chunk(b"IHDR", ihdr)
                + _png_chunk(b"IDAT", zlib.compress(raw))
                + _png_chunk(b"IEND", b""))
