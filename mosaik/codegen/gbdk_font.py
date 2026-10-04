"""The console font, as the GBDK consoles LINK it: GBDK's own `font_ibm`.

The engine carries NO copy of any font's pixels. Every path that needs the
console font reads the `font_ibm` object GBDK links out of the console library
(`gbdk/lib/<console>/<console>.lib`, declared in `<gbdk/font.h>`), exactly as
the ordinary text path's `font_load(font_ibm)` does:

- glyph-buffer text (`text.glyph_buffer`) with no project font rasterizes from
  `font_ibm + FONT_IBM_TILES_OFFSET` (`gbdk_text._emit_gbdk_glyph_font`);
- `sprite.font_glyph(tile, ch)` uploads one glyph of it as a sprite tile (the
  SMS / Game Gear HUD's sprite text, `mosaik_vm/hud.py`).

GBDK's library is GPLv2 with a LINKING exception (`gbdk/licenses/
LICENSE_GPLV2_LE`), which covers a program linking it; that is why the font is
linked and never pasted here. The cc65 PC Engine has no GBDK: its
`sprite.font_glyph` reads cc65's own linked console font (`pce_font`).

The layout below was read out of the library's `.rel` records
(`read_font_ibm`) and is the same in every GBDK console library we build for
(gb, ap, duck, sms, gg, nes): byte 0 is the font type, 0x05 =
FONT_128ENCODING | FONT_COMPRESSED; byte 1 the tile count, 102; then a
128-byte ASCII -> tile table, then 102 tiles of 8 bytes (1bpp, one row per
byte, bit 7 = leftmost pixel). ASCII 32 + i maps to tile i for i < 96, so the
printable range is simply `tiles + (ch - 32) * 8`. `tests/glyph_buffer_test.py`
pins all of that against the installed library.
"""

import os
import re

#: font_ibm's type byte: FONT_128ENCODING (1) | FONT_COMPRESSED (4).
FONT_IBM_TYPE = 0x05
#: Tiles in font_ibm (its byte 1).
FONT_IBM_TILES = 102
#: Where the tiles start: 2 header bytes + the 128-byte encoding table.
FONT_IBM_TILES_OFFSET = 2 + 128
#: The printable range the engine uses, ASCII 32..127.
FONT_IBM_GLYPHS = 96


def read_font_ibm(lib_path):
    """The bytes of `_font_ibm` as linked from a GBDK console library, read out
    of its SDCC `.rel` text records (`T` lines: two address bytes, two area
    bytes, then data). None when the library has no such object. For tests and
    measurement only; nothing in a build calls this."""
    with open(lib_path, "rb") as handle:
        text = handle.read().decode("latin-1")
    at = text.find("S _font_ibm Def")
    if at < 0:
        return None
    start = text.rfind("\nH ", 0, at)
    end = text.find("\nH ", at)
    module = text[start:end if end > 0 else len(text)]
    size = re.search(r"A _\w+ size ([0-9A-F]+) flags \d+ addr 0\s*\nS _font_ibm",
                     module)
    if not size:
        return None
    out = bytearray(int(size.group(1), 16))
    for line in module.split("\n"):
        if line.startswith("T "):
            fields = [int(x, 16) for x in line[2:].split()]
            addr = fields[0] | (fields[1] << 8)
            data = fields[4:]
            out[addr:addr + len(data)] = bytes(data)
    return bytes(out)


def gbdk_console_lib(gbdk_home, console):
    """Path of a GBDK console library (`lib/<console>/<console>.lib`)."""
    return os.path.join(gbdk_home, "lib", console, console + ".lib")
