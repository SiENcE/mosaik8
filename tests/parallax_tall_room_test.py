#!/usr/bin/env python3
"""A parallax room TALLER than the screen streams its last band to the MAP bottom.

The defect (found A/B-ing the platformer conversion's first tutorial room
against its source project's own ROM,
2026-08-21): `rows` on a parallax band does DOUBLE DUTY.

  * `engine.scrollpx.band()` derives the band's SCANLINE extent from
    `(row + rows) * 8`, and reads anything past 143 as the chain terminator -
    so for the last band any value that reaches the bottom of the screen is
    equivalent.
  * `engine.scrollpx.put()` uses the SAME number as the count of map rows to
    gather and write into the tilemap.

Those two are the same number only while the room is exactly as tall as the
screen. The last band is also the only one the parallax ISR gives the vertical
scroll to - `SCY_REG = ny ? 0 : gbs_px_livey`, the reference engine's own model, upper
bands pinned to 0 - so on a taller room the hardware reads tilemap rows
`voff / 8` further down than `put()` ever wrote them.

Measured on that room (22 rows, camera y 32 = 4 rows): the tilemap held map
rows 0..17 at tilemap rows 0..17 and rows 18..22 had never been written at all
(they held one uniform tile each). The level drew 4 rows too high, and its
ground band plus the numbered strip along the bottom - map rows 20 and 21 -
could not be reached from any camera position.

The fix is to stream the last band to the bottom of the MAP: every row it can
scroll onto is then resident and `SCY` alone does the vertical scroll, with no
re-stream when the camera moves in y. `map_h <= 32` and the upper bands sit
above `row`, so `row + rows` never passes the 32-row tilemap.

Why it hid: the only other parallax rooms in the conversions are exactly 18
rows tall (the reference-engine sample conversion's parallax room and the platformer
conversion's cutscene), where `voff` is
always 0 and the two meanings of `rows` coincide. Which is the standing rule
about reference-engine scene-resource data one more time - a second EXAMPLE, here a second scene
shape, is what makes an assumption fail.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik_scenes import transpile  # noqa: E402
from mosaik_assets import write_png_indexed  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def _world(height, bands):
    return {"world": {"module": "scenes", "map_w": 40, "map_h": height,
                      "vm": True},
            "tileset": {"png": "tiles.png"},
            "kinds": {"player": 0},
            "scene": [{"name": "room",
                       "map": [[0] * 40 for _ in range(height)],
                       "parallax": bands}]}


def _px_rows(src):
    """The emitted PX_ROWS table, as ints."""
    i = src.index("const PX_ROWS")
    # `const PX_ROWS: array[u8, N] = [ ... ]` - skip the TYPE's brackets
    body = src[src.index("= [", i) + 3:src.index("]", src.index("= [", i))]
    return [int(v) for v in body.replace("\n", "").split(",") if v.strip()]


#: the platformer conversion's tutorial room: a 22-row PLATFORM room, sky at 1/4, mid at 1/2, the rest
#: at full speed - the reference engine's `[{height:1,speed:2},{height:3,speed:1},
#: {height:0,speed:0}]`, whose last layer's height it ignores.
TALL = [{"rows": 1, "speed": 2}, {"rows": 3, "speed": 1}, {"rows": 0, "speed": 0}]


def test_tall_room_streams_to_the_map_bottom(tmpdir):
    src = transpile(_world(22, TALL), tmpdir)
    rows = _px_rows(src)[:3]
    check("the last band streams every row the camera can scroll onto",
          rows == [1, 3, 18],
          "got %s; 18 = 22 map rows - the 4 the upper bands hold, NOT the 14 "
          "screen rows it covers" % rows)
    # ...and the band still terminates the ISR chain: scrollpx.band() reads
    # (row + rows) * 8 >= 144 as "last", so over-running the screen is exactly
    # how the last band is spelled.
    check("...and (row + rows) * 8 still reaches the screen bottom",
          (1 + 3 + 18) * 8 >= 144)
    # Every written row has to fit the 32-row hardware tilemap.
    check("...and stays inside the 32-row tilemap", 4 + 18 <= 32)


def test_screen_tall_room_is_unchanged(tmpdir):
    """The rooms that already worked must stay byte-identical: at 18 rows the
    map bottom and the screen bottom are the same row."""
    src = transpile(_world(18, TALL), tmpdir)
    check("an 18-row room is unaffected", _px_rows(src)[:3] == [1, 3, 14],
          str(_px_rows(src)[:3]))
    # The platformer conversion's cutscene shape, two bands on an 18-row room.
    src = transpile(_world(18, [{"rows": 12, "speed": 1},
                                {"rows": 6, "speed": "fixed"}]), tmpdir)
    check("...and so is a two-band 18-row room", _px_rows(src)[:2] == [12, 6],
          str(_px_rows(src)[:2]))


def test_the_last_bands_authored_rows_are_ignored(tmpdir):
    """As the reference engine ignores its last layer's `height`: whatever is authored,
    the last band runs to the bottom. Otherwise the value that decides how much
    is resident could disagree with the one that decides the scanline extent."""
    a = _px_rows(transpile(_world(22, TALL), tmpdir))[:3]
    b = [{"rows": 1, "speed": 2}, {"rows": 3, "speed": 1},
         {"rows": 99, "speed": 0}]
    c = _px_rows(transpile(_world(22, b), tmpdir))[:3]
    check("an authored last-band height does not change it", a == c,
          "%s vs %s" % (a, c))


def main():
    import tempfile
    print("Parallax on a room taller than the screen")
    print("=" * 50)
    with tempfile.TemporaryDirectory() as tmp:
        pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
        write_png_indexed(os.path.join(tmp, "tiles.png"), 8, 8, [0] * 64, pal)
        print("[a 22-row room]")
        test_tall_room_streams_to_the_map_bottom(tmp)
        print("[the 18-row rooms that already worked]")
        test_screen_tall_room_is_unchanged(tmp)
        print("[the last band's authored height is ignored]")
        test_the_last_bands_authored_rows_are_ignored(tmp)
    print("=" * 50)
    if failed:
        print("%d check(s) FAILED" % failed)
        return 1
    print("All parallax tall-room checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
