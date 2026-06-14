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


# ---------------------------------------------------------------------------
# Named-sprite sheets (a PNG + a sidecar manifest cut into named sub-sprites)
# ---------------------------------------------------------------------------
#
# A sheet PNG `foo.png` may carry a sidecar `foo.sprites.json` next to it,
# mapping sprite name -> [x, y, w, h] pixel rectangle (w, h multiples of 8).
# The pipeline then concatenates each named sub-sprite's 8x8 tiles (row-major
# within its rect, manifest order) into the asset's `<name>_tiles` array, and
# emits per-sprite `#define <sprite>_tile <first-tile-index>` / `_w` / `_h`
# (tile units) so a program can `sprite.set_meta(slot, knight_tile, knight_w,
# knight_h)`. Without a sidecar, the PNG keeps the flat 8x8-grid behaviour.

def manifest_path(png_path):
    """Sidecar manifest path for a sheet PNG (`foo.png` -> `foo.sprites.json`)."""
    stem = os.path.splitext(png_path)[0]
    return stem + ".sprites.json"


def load_sprite_manifest(png_path):
    """Ordered [(name, x, y, w, h)] from a sheet's sidecar manifest, or None.

    The JSON is an object mapping sprite name -> [x, y, w, h] (pixels). Object
    insertion order is preserved (the tile layout order)."""
    side = manifest_path(png_path)
    if not os.path.exists(side):
        return None
    import json
    try:
        with open(side, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise AssetError("%s: bad sprite manifest: %s" % (side, e))
    out = []
    for name, rect in raw.items():
        if not (isinstance(rect, (list, tuple)) and len(rect) == 4):
            raise AssetError("%s: sprite '%s' must be [x, y, w, h]" % (side, name))
        out.append((re.sub(r"[^A-Za-z0-9_]", "_", name), *(int(v) for v in rect)))
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


def sheet_to_tiles(png_path, sprite_bpp):
    """Concatenate a manifested sheet's named sub-sprites into one tile stream
    at the target depth (4bpp packed-nibble if sprite_bpp==4 and the PNG is a
    >4-colour indexed image, else GB 2bpp). Returns the tile bytes."""
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
        out += encode(w, h, _slice_rows(rows, x, y, w, h))
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


def load_assets(paths, sprite_bpp=2):
    """Convert asset PNGs to [(c_name, data, bpp)], checking name clashes.

    `sprite_bpp` is the target console's native sprite depth (PLATFORM_CAPS).
    When the build qualifies for the 4bpp tier (see build_is_4bpp) every sprite
    asset is encoded packed-nibble 4bpp; otherwise GB 2bpp. The depth is
    uniform across the build so the sprite engine has a single source format.
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
        sheet = sheet_to_tiles(path, 4 if four else 2)
        if sheet is not None:
            # Named-sprite sheet: tiles already sliced + concatenated.
            assets.append((name, sheet, 4 if four else 2))
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
