#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Per-glyph font WIDTHS (`mosaik_assets.png_to_font_widths`), stage V1 of
the variable-width text work.

Widths are trimmed out of the font PNG against the reference engine's marker colour, not
read from a sidecar - its `.json` carries only a recode `mapping`. Every sheet
here is built by the test itself. The byte-for-byte oracle against the reference engine's
own compiled `font_gbs_variable_width_widths[]` needs the reference engine's sources, so
it lives with them, in the studio's suite.
"""

import tempfile

import mosaik_assets as ga

failures = 0


def check(name, cond, detail=""):
    global failures
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f"  ({detail})" if detail and not cond else ""))
    if not cond:
        failures += 1


# The reference engine's own art colours. PAPER is deliberately NOT pure white: (255,255,255)
# satisfies `r > 249 && b > 249` and would itself read as the marker, which is a
# live trap for anyone hand-making a sheet.
PAPER = (224, 248, 207, 255)
INK = (7, 24, 33, 255)
MARK = (255, 0, 255, 255)

TMP = tempfile.mkdtemp(prefix="fontw_")


def sheet(cells, rows=1):
    """A 16-cell-wide sheet. `cells` is one (lo, hi) span per cell, or None for
    a wholly transparent cell; pixels outside the span are the marker."""
    px = [[MARK] * (16 * 8) for _ in range(rows * 8)]
    for i, span in enumerate(cells):
        if span is None:
            continue
        lo, hi = span
        gx, gy = (i % 16) * 8, (i // 16) * 8
        for x in range(lo, hi + 1):
            for y in range(8):
                px[gy + y][gx + x] = INK if (x + y) % 2 else PAPER
    path = os.path.join(TMP, "f%d.png" % len(os.listdir(TMP)))
    ga.write_png_rgba(path, 16 * 8, rows * 8, px)
    return path


# --- the span is what is measured, and it is two-sided ---------------------
spans = [(0, 3), (0, 0), (0, 7), (2, 5), (5, 7), (1, 6),
         (0, 4), (3, 3), (0, 6), (4, 7), (0, 1), (6, 7),
         (2, 2), (0, 5), (1, 1), (0, 7)]
got = ga.png_to_font_widths(sheet(spans))
want = [hi - lo + 1 for lo, hi in spans]
check("width is the span of non-marker pixels", got == want,
      "got %r want %r" % (got, want))

# Leading marker columns SHIFT the glyph, they are not left bearing: a glyph
# drawn at x 5..7 advances 3, exactly as one drawn at 0..2 does.
a = ga.png_to_font_widths(sheet([(5, 7)] + [(0, 7)] * 15))
b = ga.png_to_font_widths(sheet([(0, 2)] + [(0, 7)] * 15))
check("the trim is TWO-SIDED (leading marker is not bearing)",
      a[0] == b[0] == 3, "got %r and %r" % (a[0], b[0]))

# A marker pixel INSIDE the span is drawn (the reference engine renders it black), so it
# must not shorten the advance.
px = [[MARK] * 128 for _ in range(8)]
for x in range(0, 8):
    for y in range(8):
        px[y][x] = PAPER
for y in range(8):          # punch a marker column through the middle
    px[y][4] = MARK
p = os.path.join(TMP, "hole.png")
ga.write_png_rgba(p, 128, 8, px)
check("a marker pixel inside the span does not shorten it",
      ga.png_to_font_widths(p)[0] == 8, ga.png_to_font_widths(p)[0])

# A wholly transparent cell is a dead glyph: width 0, never advances.
check("a fully transparent cell measures 0",
      ga.png_to_font_widths(sheet([None] + [(0, 7)] * 15))[0] == 0)

# --- a sheet with NO marker at all is fixed-width --------------------------
check("a sheet with no marker returns None (fixed-width)",
      ga.png_to_font_widths(sheet([(0, 7)] * 16)) is None)

# --- geometry is validated -------------------------------------------------
bad = os.path.join(TMP, "narrow.png")
ga.write_png_rgba(bad, 64, 8, [[PAPER] * 64 for _ in range(8)])
try:
    ga.png_to_font_widths(bad)
    check("a sheet that is not 16 cells wide is refused", False)
except ga.AssetError:
    check("a sheet that is not 16 cells wide is refused", True)

tall = os.path.join(TMP, "tall.png")
ga.write_png_rgba(tall, 128, 12, [[PAPER] * 128 for _ in range(12)])
try:
    ga.png_to_font_widths(tall)
    check("a height that is not a multiple of 8 is refused", False)
except ga.AssetError:
    check("a height that is not a multiple of 8 is refused", True)

# --- multi-row sheets are read row-major, like the bitmaps -----------------
two = ga.png_to_font_widths(sheet([(0, 1)] * 16 + [(0, 5)] * 16, rows=2))
check("cells are read row-major across the whole sheet",
      len(two) == 32 and two[:16] == [2] * 16 and two[16:] == [6] * 16,
      "%r" % (two,))

# --- a full variable-width sheet: 224 cells, spacers, proportional -------
# A reference-engine-style variable sheet is 16 x 14 cells (ASCII 32..255), and its
# author pads lines with SPACERS: high cells that draw only paper for an exact
# width. Built here rather than read from a real project, so the rule is pinned
# on a clean clone too. (The byte-for-byte oracle against the reference engine's own
# compiled font lives in the studio's suite, which has its sources.)
full = [(0, 5)] * 224
full[ord('I') - 32] = (0, 2)            # a narrow glyph
full[ord('W') - 32] = (0, 7)            # a wide one
full[239 - 32] = (0, 0)                 # the 1 px spacer
full[255 - 32] = (0, 3)                 # the 4 px spacer
w = ga.png_to_font_widths(sheet(full, rows=14))
check("a 16 x 14 variable sheet covers ASCII 32..255", len(w) == 224, len(w))
check("its 1 px spacer (char 239) measures 1", w[239 - 32] == 1, w[239 - 32])
check("its 4 px spacer (char 255) measures 4", w[255 - 32] == 4, w[255 - 32])
check("it is proportional ('I' narrower than 'W')",
      w[ord('I') - 32] < w[ord('W') - 32],
      "I=%r W=%r" % (w[ord('I') - 32], w[ord('W') - 32]))

# ...and the same geometry with NO marker is a FIXED-width sheet, which is
# what keeps a 224-cell mono sheet at 96 glyphs (the trigger is the marker,
# not the cell count).
check("a 16 x 14 sheet with no marker is FIXED-width",
      ga.png_to_font_widths(sheet([(0, 7)] * 224, rows=14)) is None)

# The SPACE glyph must be a UNIFORM paper cell on a VWF sheet. `clear_area`
# blanks by plotting it raw at `gbs_font_base`, and nothing masks a raw plot -
# so a sheet whose space is "2 drawn columns + 6 marker columns" painted every
# blanked cell as a vertical bar, and a cleared region came out STRIPED at an
# 8 px period (reported from play on a converted platformer). Both polarities:
# a light-on-dark sheet's space is ink, an ordinary sheet's is paper.


def space_sheet(space_px, width):
    """A 16-cell variable sheet whose space draws `space_px` for `width`
    columns (marker after); every other cell is an ordinary 6 px glyph."""
    px = [[MARK] * 128 for _ in range(8)]
    for y in range(8):
        for x in range(width):
            px[y][x] = space_px
        for c in range(1, 16):
            for x in range(6):
                px[y][c * 8 + x] = INK if (x + y) % 2 else PAPER
    path = os.path.join(TMP, "space%d.png" % len(os.listdir(TMP)))
    ga.write_png_rgba(path, 128, 8, px)
    return path


g, w = ga.png_to_font_sheet(space_sheet(INK, 2))
check("vwf: an INVERTED sheet's space is a uniform cell (clear_area blanks raw)",
      g[0] == [0xFF] * 8, "%r" % (g[0],))
check("vwf: normalising the space did not disturb its width", w[0] == 2, w[0])
g, w = ga.png_to_font_sheet(space_sheet(PAPER, 4))
check("vwf: an ordinary sheet's space is a uniform paper cell",
      g[0] == [0x00] * 8, "%r" % (g[0],))
check("vwf: ...keeping its 4 px width", w[0] == 4, w[0])

print()
if failures:
    print("%d check(s) failed" % failures)
    sys.exit(1)
print("all font-width checks passed")
