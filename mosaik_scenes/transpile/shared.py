"""mosaik_scenes.transpile.shared - the chunk-size constants and the colour
conversions the analysis phase and the emitters share."""
from mosaik_assets import rgb555_words

from ..base import SceneError


PAL_SLOTS = 8       # hardware palette slots per layer the colour tier addresses

# The largest a single const array may be. A banked array is read in place
# while its one ROM bank is mapped, so it can never cross a bank boundary --
# 16 KB is the Game Boy's, the tightest of any target. Only the concatenated
# per-scene tileset table gets near it, and it is chunked to stay under.
_TS_CHUNK = 16384
#: Cells per chunk of the concatenated MAPS/COLLISION tables (paint_table).
#: One GB ROM bank, the tightest per-symbol ceiling of any target; a cell is
#: one byte in both tables, so the count IS the byte count.
_MAP_CHUNK = 16384


def _rgb888(color):
    """One authored palette colour -> an (r, g, b) triple, 0..255.

    Accepts "RRGGBB" / "#RRGGBB" hex or an [r, g, b] triple, the two spellings
    the studio and the reference-engine importer produce."""
    if isinstance(color, str):
        s = color.strip().lstrip("#")
        if len(s) != 6:
            raise SceneError("palette colour %r is not RRGGBB hex" % color)
        try:
            return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            raise SceneError("palette colour %r is not RRGGBB hex" % color)
    try:
        return tuple(int(v) & 0xFF for v in list(color)[:3])
    except (TypeError, ValueError):
        raise SceneError("palette colour %r is not [r, g, b]" % (color,))


def _rgb555(color):
    """One authored palette colour -> a portable 5-5-5 RGB word.

    The word is the SAME encoding BKG_PALETTE16 uses (the 4bpp tier), so
    `scenes.mos` stays target-neutral and each backend rounds to its native
    depth at runtime."""
    return rgb555_words([_rgb888(color)])[0]


