"""Generate src/tiles.mos -- vm-snake's background tile art, in COLOUR.

    python assets/gen_tiles.py            # rewrites src/tiles.mos

The renderer draws EVERYTHING as background tiles: the field, the walls, the
snake, the food and the whole side panel including its text. That is why a 5x7
glyph set is baked in here instead of using `text.print_string`:

  * the console font is a shared resource (GBDK relocates its 96 glyphs into
    the top of the bkg tile table and vm.core's dialogue box owns it), and
  * on the Atari Lynx there is no tilemap - TGI text is drawn straight into the
    framebuffer, so a re-blitting present WIPES it, while background tiles are
    recomposed from the strip engine and persist.

THE ART IS COMPUTED, not drawn: every tile below is a small function of its
pixel coordinates (a rounded square, a disc, a brick bond), so there is no
hand-placed pixel to maintain and a change of style is a change of formula.
Only the glyphs are a table, because a letter is not a formula.


COLOUR: ONE ART, THREE HARDWARE TIERS
-------------------------------------
The art is written once, in 16-PEN index space, and lowered to whatever each
console can show. The three tiers are the engine's, not this project's (the
engine's "4bpp BACKGROUND tier" and "PER-TILE BACKGROUND PALETTES" rules).

  * SMS / Game Gear / PC Engine / Atari Lynx -- `bkg_bpp == 4`. TILES is
    emitted as 32 B/tile packed nibbles behind an `if platform` fork and
    `palette.load_bkg16(BKG_PALETTE16)` loads all sixteen pens. The Lynx needs
    `[build] lynx_bkg16` (no tilemap, so 4bpp doubles its background RAM).

  * Game Boy Color / Analogue Pocket -- 2bpp tiles and EIGHT background
    palettes selected PER MAP CELL. Each tile belongs to one of three palette
    GROUPS (UI, snake, food), and a group's pens lower to the 2bpp values
    0..3 of that group's palette. board.mos writes the slot with
    `bkg.set_attrs` beside every `bkg.set_tiles`.

  * Game Boy / Mega Duck -- four greys, the console's maximum. The same 2bpp
    arm renders as shades; the snake and the food stay apart by SHAPE (a
    rounded square against a disc with a leaf), which is why neither relies on
    its colour.
"""

import os

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "src", "tiles.mos")

# ------------------------------------------------------------------ the pens
# Pen 0 is the field on every tier: what shows through a tile's empty pixels,
# the Lynx's transparent pen, and colour 0 of EVERY Game Boy Color palette (a
# rounded tile's corners have to blend into the field whatever its palette).
VOID, GRID, BRICK, INK = 0, 1, 2, 3
SKIN, SKIN_HI, SKIN_LO, EYE = 4, 5, 6, 7
FRUIT, FRUIT_HI, LEAF = 8, 9, 10
MORTAR = 11

PEN_RGB = {
    VOID:     (0x14, 0x1C, 0x24),    # the field, a deep slate
    GRID:     (0x2C, 0x38, 0x44),    # the field's faint grid dots
    BRICK:    (0xB0, 0x84, 0x58),    # the walls, a sandstone
    INK:      (0xF0, 0xEC, 0xD8),    # every glyph
    SKIN:     (0x58, 0xB8, 0x40),    # the snake
    SKIN_HI:  (0xB8, 0xE8, 0x70),    # its scale highlights
    SKIN_LO:  (0x20, 0x50, 0x18),    # its outline
    EYE:      (0xF8, 0xF8, 0xE0),    # the whites of its eyes
    FRUIT:    (0xC8, 0x30, 0x68),    # the food, a berry
    FRUIT_HI: (0xF8, 0xA8, 0xC8),    # its shine
    LEAF:     (0x30, 0x88, 0x38),    # its leaf and stem
    MORTAR:   (0x5C, 0x40, 0x2C),    # the walls' joints
}
for _spare in range(12, 16):
    PEN_RGB[_spare] = (0, 0, 0)

# The three palette GROUPS a tile can belong to, and how each group's pens
# lower to a 2bpp value. The same table is the Game Boy Color palette (a
# group's four colours in 2bpp order) and the DMG shade map.
GROUPS = {
    "ui":    {VOID: 0, GRID: 1, MORTAR: 1, BRICK: 2, INK: 3},
    "snake": {VOID: 0, SKIN_HI: 1, EYE: 1, SKIN: 2, SKIN_LO: 3},
    "food":  {VOID: 0, FRUIT_HI: 1, FRUIT: 2, LEAF: 3},
}
GROUP_SLOT = {"ui": 0, "snake": 1, "food": 2}


def gbc_palettes():
    """The Game Boy Color background palettes, one per group, in slot order:
    for each 2bpp value, the pen that lowers to it (the first one wins)."""
    out = []
    for group in sorted(GROUP_SLOT, key=GROUP_SLOT.get):
        by_value = {}
        for pen, value in GROUPS[group].items():
            by_value.setdefault(value, pen)
        out.append([PEN_RGB[by_value[v]] for v in range(4)])
    return out


# ------------------------------------------------------------------- the art
# Each maker returns 8 rows of 8 pens.

def tile(fn):
    return [[fn(x, y) for x in range(8)] for y in range(8)]


def field():
    """The well: empty, with one grid dot per cell so the lanes read."""
    return tile(lambda x, y: GRID if (x, y) == (0, 0) else VOID)


def panel():
    """The panel behind the text: a sparse diagonal dot pattern."""
    return tile(lambda x, y: GRID if (x * 3 + y * 5) % 13 == 0 else VOID)


def wall():
    """A running brick bond: joints every 4 rows, staggered by half a brick."""
    def px(x, y):
        if y % 4 == 3:
            return MORTAR
        shift = 0 if (y // 4) % 2 == 0 else 4
        if (x + shift) % 8 == 7:
            return MORTAR
        return BRICK
    return tile(px)


def _rounded(x, y, inset):
    """Inside a square inset by `inset` px with its four corners clipped."""
    lo, hi = inset, 7 - inset
    if not (lo <= x <= hi and lo <= y <= hi):
        return False
    corner = (x in (lo, hi)) and (y in (lo, hi))
    return not corner


def _edge(x, y, inset):
    """On the outline of that rounded square."""
    if not _rounded(x, y, inset):
        return False
    return any(not _rounded(x + dx, y + dy, inset)
               for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))


def body():
    """A segment: a rounded square with an outline and diagonal scales."""
    def px(x, y):
        if not _rounded(x, y, 0):
            return VOID
        if _edge(x, y, 0):
            return SKIN_LO
        return SKIN_HI if (x + y) % 4 == 0 else SKIN
    return tile(px)


# Eye positions per heading, in the order of the program's button ids minus 4:
# up, down, left, right. The eyes sit toward the way the head is going.
EYES = [((2, 2), (5, 2)), ((2, 5), (5, 5)), ((2, 2), (2, 5)), ((5, 2), (5, 5))]


def head(heading):
    """The head: the segment's shape, solid, with two eyes facing `heading`."""
    eyes = EYES[heading]

    def px(x, y):
        if not _rounded(x, y, 0):
            return VOID
        if _edge(x, y, 0):
            return SKIN_LO
        for ex, ey in eyes:
            if (x, y) == (ex, ey):
                return SKIN_LO                  # the pupil
            if abs(x - ex) + abs(y - ey) == 1 and not _edge(x, y, 0):
                return EYE
        return SKIN
    return tile(px)


def food():
    """A berry: a disc with a shine, and a leaf on a stem above it."""
    cx, cy, r2 = 3.5, 4.5, 3.2 ** 2

    def px(x, y):
        d2 = (x - cx) ** 2 + (y - cy) ** 2
        if y <= 1:
            if (x, y) in ((4, 0), (5, 0), (3, 1)):
                return LEAF
            return VOID
        if d2 <= r2:
            if (x - (cx - 1.2)) ** 2 + (y - (cy - 1.2)) ** 2 <= 1.0:
                return FRUIT_HI
            return FRUIT
        return VOID
    return tile(px)


# ---------------------------------------------------------------- the glyphs
# 5x7 cells, '#' = ink, placed at columns 1..5 / rows 0..6 of the 8x8 tile, so
# adjacent glyphs keep a gutter and share a baseline.
FONT = {
    "0": (".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."),
    "1": ("..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."),
    "2": (".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"),
    "3": ("####.", "....#", "....#", ".###.", "....#", "....#", "####."),
    "4": ("#...#", "#...#", "#...#", "#####", "....#", "....#", "....#"),
    "5": ("#####", "#....", "#....", "####.", "....#", "....#", "####."),
    "6": (".###.", "#....", "#....", "####.", "#...#", "#...#", ".###."),
    "7": ("#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."),
    "8": (".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."),
    "9": (".###.", "#...#", "#...#", ".####", "....#", "....#", ".###."),
    "A": (".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "B": ("####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."),
    "C": (".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."),
    "D": ("####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."),
    "E": ("#####", "#....", "#....", "####.", "#....", "#....", "#####"),
    "G": (".###.", "#...#", "#....", "#.###", "#...#", "#...#", ".###."),
    "H": ("#...#", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"),
    "K": ("#...#", "#..#.", "#.#..", "##...", "#.#..", "#..#.", "#...#"),
    "L": ("#....", "#....", "#....", "#....", "#....", "#....", "#####"),
    "M": ("#...#", "##.##", "#.#.#", "#...#", "#...#", "#...#", "#...#"),
    "N": ("#...#", "##..#", "##..#", "#.#.#", "#..##", "#..##", "#...#"),
    "O": (".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "P": ("####.", "#...#", "#...#", "####.", "#....", "#....", "#...."),
    "R": ("####.", "#...#", "#...#", "####.", "#.#..", "#..#.", "#...#"),
    "S": (".####", "#....", "#....", ".###.", "....#", "....#", "####."),
    "T": ("#####", "..#..", "..#..", "..#..", "..#..", "..#..", "..#.."),
    "U": ("#...#", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."),
    "V": ("#...#", "#...#", "#...#", "#...#", "#...#", ".#.#.", "..#.."),
}
DIGITS = "0123456789"
LETTERS = "ABCDEGHKLMNOPRSTUV"


def glyph(ch):
    src = FONT[ch]
    return tile(lambda x, y: INK if (y < 7 and 1 <= x <= 5
                                     and src[y][x - 1] == "#") else VOID)


def tile_table():
    """[(const_name, comment, group, pens)] in emission order - the tile ids."""
    tiles = [
        ("T_EMPTY", "the well's field", "ui", field()),
        ("T_WALL", "the well's walls", "ui", wall()),
        ("T_BG", "the panel's field", "ui", panel()),
        ("T_BODY", "a snake segment", "snake", body()),
    ]
    for i, name in enumerate(("up", "down", "left", "right")):
        tiles.append(("T_HEAD%d" % i, "the head, facing %s" % name, "snake",
                      head(i)))
    tiles.append(("T_FOOD", "the food", "food", food()))
    for ch in DIGITS + LETTERS:
        tiles.append(("G_%s" % ch, "glyph '%s'" % ch, "ui", glyph(ch)))
    return tiles


# The words the renderer spells, as ONE blob of tile ids plus an offset per
# word (a space is the field tile it sits on). Built here rather than in
# board.mos because a mosaik array initialiser has to be a compile-time
# constant, and a glyph id imported from another module is a C symbol.
# The note words are ten columns at most: they are drawn INSIDE the well.
LABELS = [("L_SCORE", "SCORE", "T_BG"), ("L_LENGTH", "LENGTH", "T_BG"),
          ("L_LEVEL", "LEVEL", "T_BG"), ("L_BEST", "BEST", "T_BG"),
          ("L_PAD", "PAD TURN", "T_BG"), ("L_ASTART", "A START", "T_BG"),
          ("L_SNAKE", "SNAKE", "T_EMPTY"), ("L_PRESSA", "PRESS A", "T_EMPTY"),
          ("L_GAMEOVER", "GAME OVER", "T_EMPTY")]


def encode_2bpp(pens, group):
    """8 rows -> 16 bytes of GB 2bpp (low plane byte, high plane byte)."""
    lower = GROUPS[group]
    out = []
    for line in pens:
        lo = hi = 0
        for col, pen in enumerate(line):
            v = lower[pen]
            lo |= (v & 1) << (7 - col)
            hi |= ((v >> 1) & 1) << (7 - col)
        out += [lo, hi]
    return out


def encode_4bpp(pens):
    """8 rows -> 32 bytes of packed nibbles, leftmost pixel = high nibble
    (what the 4bpp background tier's `bkg.set_data` expects)."""
    out = []
    for line in pens:
        for pair in range(4):
            out.append((line[pair * 2] << 4) | line[pair * 2 + 1])
    return out


def rgb555(rgb):
    r, g, b = rgb
    return ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3)


def fmt_rows(values, per_row, indent, fmt="%d"):
    lines = []
    for i in range(0, len(values), per_row):
        lines.append(indent + ", ".join(fmt % v for v in values[i:i + per_row]))
    return ",\n".join(lines)


def main():
    tiles = tile_table()
    n = len(tiles)
    ids = {name: i for i, (name, _c, _g, _p) in enumerate(tiles)}
    data2 = [b for _n, _c, g, pens in tiles for b in encode_2bpp(pens, g)]
    data4 = [b for _n, _c, _g, pens in tiles for b in encode_4bpp(pens)]
    pal16 = [rgb555(PEN_RGB[i]) for i in range(16)]
    gbc = [rgb555(c) for slot in gbc_palettes() for c in slot]

    out = []
    w = out.append
    w("-- tiles.mos -- GENERATED by assets/gen_tiles.py; do not edit by hand.")
    w("--")
    w("-- vm-snake's whole background tile set: the field, the walls, the")
    w("-- snake (a segment and a head for each heading), the food and a 5x7")
    w("-- glyph set for the panel. Regenerate with:  python assets/gen_tiles.py")
    w("--")
    w("-- ONE art, three hardware tiers (the generator's docstring has the whole")
    w("-- story): 4bpp 16-colour tiles on SMS / Game Gear / PC Engine / Lynx;")
    w("-- 2bpp tiles plus a per-cell palette on the Game Boy Color; four greys")
    w("-- on a DMG, where the snake and the food differ in SHAPE.")
    w('module "tiles" {')
    w("    const TILE_COUNT: u8 = %d" % n)
    w("")
    w("    -- Tile ids, in the order this table emits them.")
    for name, comment, _g, _p in tiles:
        w("    const %-10s u8 = %-3d -- %s" % (name + ":", ids[name], comment))
    w("")
    w("    -- The Game Boy Color palette slot of the three tile groups. The")
    w("    -- groups are contiguous runs of the table, so board.mos picks a slot")
    w("    -- with two range tests.")
    w("    const SLOT_UI: u8 = %d" % GROUP_SLOT["ui"])
    w("    const SLOT_SNAKE: u8 = %d" % GROUP_SLOT["snake"])
    w("    const SLOT_FOOD: u8 = %d" % GROUP_SLOT["food"])
    w("")
    blob, offs = [], []
    for _const, word, space in LABELS:
        offs.append(len(blob))
        blob += [ids[space] if ch == " " else ids["G_%s" % ch] for ch in word]
    w("    -- The words the renderer spells, as tile runs in one blob.")
    w("    const LABELS: array[u8, %d] = [" % len(blob))
    rows = []
    for (_const, word, _s), off in zip(LABELS, offs):
        rows.append("        " + ", ".join("%d" % b for b in blob[off:off + len(word)]))
    w(",\n".join(rows))
    w("    ]")
    for (const, word, _s), off in zip(LABELS, offs):
        w("    const %-11s u8 = %-3d -- %s" % (const + ":", off, word))
    w("")
    w("    -- The tile DATA, forked on the console's background depth. The 4bpp")
    w("    -- arm is 32 B/tile packed nibbles; the 2bpp arm is 16 B/tile GB")
    w("    -- planar. Only the target's arm survives conditional compilation.")
    w('    if platform == "sms" or platform == "gamegear" or platform == "pce" or platform == "lynx" {')
    w("    const TILES: array[u8, %d] = [" % len(data4))
    w(fmt_rows(data4, 16, "        ", "0x%02X"))
    w("    ]")
    w("    } else {")
    w("    const TILES: array[u8, %d] = [" % len(data2))
    w(fmt_rows(data2, 16, "        ", "0x%02X"))
    w("    ]")
    w("    }")
    w("")
    w("    -- All sixteen pens as PORTABLE 5-5-5 RGB words: palette.load_bkg16")
    w("    -- rounds each to the target's real depth. A no-op on a 2bpp console.")
    w("    const BKG_PALETTE16: array[u16, 16] = [")
    w(fmt_rows(pal16, 8, "        "))
    w("    ]")
    w("")
    w("    -- The Game Boy Color background palettes, 4 colours each, for")
    w("    -- palette.load_bkg_set: slot 0 the UI, 1 the snake, 2 the food.")
    w("    const PAL_SLOTS: u8 = %d" % len(GROUP_SLOT))
    w("    const BKG_PAL: array[u16, %d] = [" % len(gbc))
    w(fmt_rows(gbc, 4, "        "))
    w("    ]")
    w("")
    w("    export TILE_COUNT, TILES, BKG_PALETTE16, BKG_PAL, PAL_SLOTS,")
    w("           SLOT_UI, SLOT_SNAKE, SLOT_FOOD, LABELS,")
    w("           " + ", ".join(c for c, _w, _s in LABELS) + ",")
    names = [name for name, _c, _g, _p in tiles]
    for i in range(0, len(names), 8):
        tail = "," if i + 8 < len(names) else ""
        w("           " + ", ".join(names[i:i + 8]) + tail)
    w("}")

    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    print("wrote %s (%d tiles, %d B at 4bpp / %d B at 2bpp)"
          % (OUT, n, len(data4), len(data2)))


if __name__ == "__main__":
    main()
