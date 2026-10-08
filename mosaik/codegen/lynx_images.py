"""Build-time Suzy sprite images for the Atari Lynx (Qt-free, pure Python).

`[build] lynx_sprites = "whole"` draws every named sprite of a sheet (one
`.sprites.toml` rectangle) as ONE literal Suzy sprite instead of one SCB per
8x8 tile. Suzy pays per sprite LINE (~6 us, measured), so a 16x16 drawn
whole is 16 lines where four 8x8 tiles are 32, and nothing has to be
converted at run time.

`[build] lynx_orientation = "portrait_*"` turns the picture a quarter turn on
the way to Suzy. The program works on a LOGICAL 102x160 screen; an image is
baked already rotated into PHYSICAL pixels (160x102 screen), so drawing it
costs nothing extra.

An image blob is `[lw, lh, <literal sprite data>]`: the LOGICAL width and
height in pixels (what the engine needs to place, flip and cull it), then
the Suzy data the SCB points at (blob + 2). Literal data is one record per
physical line, `[n + 2][n pixel bytes][0x00 pad]`, and a 0x00 offset byte
ends the sprite -- the same layout the run-time 8x8 converter writes.
"""

ORIENTATIONS = (None, 'portrait_left', 'portrait_right')


def tile_pixel(data, bpp, tile, x, y):
    """Colour index of pixel (x, y) of 8x8 tile `tile` in a sheet's data.

    2bpp is the GB planar encoding (16 B a tile, a lo/hi byte pair a row);
    4bpp is packed nibbles (32 B a tile, 4 B a row, high nibble first)."""
    if bpp == 4:
        b = data[tile * 32 + y * 4 + (x >> 1)]
        return (b >> 4) if (x & 1) == 0 else (b & 0x0F)
    lo = data[tile * 16 + y * 2]
    hi = data[tile * 16 + y * 2 + 1]
    bit = 7 - x
    return (((hi >> bit) & 1) << 1) | ((lo >> bit) & 1)


def logical_pixels(data, bpp, off, w, h):
    """The w x h-tile rectangle starting at tile `off` (row-major tiles, as
    the asset pipeline stores a named sprite) as rows of colour indices."""
    rows = []
    for py in range(h * 8):
        row = []
        for px in range(w * 8):
            t = off + (py >> 3) * w + (px >> 3)
            row.append(tile_pixel(data, bpp, t, px & 7, py & 7))
        rows.append(row)
    return rows


def rotate(rows, orient):
    """Logical pixel rows -> physical pixel rows for an orientation.

    portrait_left (the console turned counter-clockwise): physical
    P[py][px] = L[lh - 1 - px][py]. portrait_right (clockwise):
    P[py][px] = L[px][lw - 1 - py]. Derived from the screen maps
    px = 159 - ly, py = lx (left) and px = ly, py = 101 - lx (right)."""
    if orient is None:
        return [list(r) for r in rows]
    lh = len(rows)
    lw = len(rows[0]) if rows else 0
    if orient == 'portrait_left':
        return [[rows[lh - 1 - px][py] for px in range(lh)] for py in range(lw)]
    if orient == 'portrait_right':
        return [[rows[px][lw - 1 - py] for px in range(lh)] for py in range(lw)]
    raise ValueError("unknown orientation %r" % (orient,))


def literal_data(phys_rows, bpp):
    """Suzy literal sprite data for physical pixel rows (MSB-first)."""
    out = bytearray()
    per = 8 // bpp                       # pixels per byte
    for row in phys_rows:
        n = (len(row) + per - 1) // per
        line = bytearray(n)
        for i, v in enumerate(row):
            shift = 8 - bpp * (i % per + 1)
            line[i // per] |= (v & ((1 << bpp) - 1)) << shift
        out.append(n + 2)
        out += line
        out.append(0x00)
    out.append(0x00)
    return bytes(out)


def bake(data, bpp, off, w, h, orient=None):
    """One image blob: [lw, lh] + literal data of the rotated rectangle."""
    rows = logical_pixels(data, bpp, off, w, h)
    return bytes([w * 8, h * 8]) + literal_data(rotate(rows, orient), bpp)


def sheet_images(data, bpp, rects, orient=None, singles=False):
    """Every image of one sheet, and the per-tile tables that address them.

    Returns (blobs, table, table1): `blobs` is a list of image blobs;
    `table[t]` is the blob index tile `t` draws (the whole sprite for the
    FIRST tile of a named rectangle, its own 8x8 for a tile outside every
    rectangle) or None for a tile INSIDE a rectangle past its first (drawn as
    part of it). With `singles`, `table1[t]` is tile t's own 8x8 image for
    EVERY tile (what a descriptor-list or masked-fan child draws); otherwise
    table1 is None."""
    tsize = 32 if bpp == 4 else 16
    count = len(data) // tsize
    table = [None] * count
    covered = [False] * count
    blobs = []
    for off, w, h in rects:
        if off + w * h > count:
            continue                     # a manifest past the pixels: skip
        table[off] = len(blobs)
        blobs.append(bake(data, bpp, off, w, h, orient))
        for t in range(off, off + w * h):
            covered[t] = True
    one = [None] * count
    for t in range(count):
        if not covered[t]:
            table[t] = len(blobs)
            one[t] = len(blobs)
            blobs.append(bake(data, bpp, t, 1, 1, orient))
    if not singles:
        return blobs, table, None
    for t in range(count):
        if one[t] is None:
            one[t] = len(blobs)
            blobs.append(bake(data, bpp, t, 1, 1, orient))
    return blobs, table, one
