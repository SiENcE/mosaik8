#!/usr/bin/env python3
"""The SMS ring window is ONE window - the column path and the ROW path agree.

A column-streamed SMS level shifts its 32-column ring one column ahead
(`[s_base+1, s_base+32]`) because the SMS displays the whole plane and the
seam column is split across the two screen edges; `bkg.edge_mask` hides the
left half. `engine.scroll2d` streams BOTH axes, so the ring window has two
writers - and they must name the same window.

They did not. `put_row2d` kept writing logical columns `s_base .. s_base+31`
while the column path maintained `s_base+1 .. s_base+32`, so every row
streamed by a VERTICAL scroll wrote ONE cell with a column 32 places out of
place, into the slot belonging to `s_base+32`. It is invisible while it sits
under the left-column mask and is dragged into view the moment the camera
moves right, where it marches leftward across the screen. Measured on
the SMS/GG sample conversion's town room (56x56, roam): after scrolling up 4 rows and
right one column, screen column 31 held exactly 4 stale cells, and a
before/after frame diff at that camera state differs in exactly x 248..255,
rows 0..31 - the stale cells and nothing else.

The GB family keeps its own definition of both functions (its display is
narrower than the ring, so it needs no shift) and stays byte-identical.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler  # noqa: E402

_FAILED = []

SRC = '''
module "main" {
    import "engine.scroll2d"
    import "platform.video"

    const WC: u16 = 64
    const WR: u16 = 64

    function tile_at(c: u16, r: u16) -> u8 {
        var v: u16 = c + r
        var t: u8 = v
        return t
    }

    function main() {
        video.enable_lcd()
        scroll2d.fill2d(WC, WR, tile_at)
        var camx: u16 = 0
        var camy: u16 = 0
        loop {
            scroll2d.update2d(camx, camy, tile_at)
            scroll2d.refill2d(tile_at)
            camx = camx + 1
            video.wait_vblank()
        }
    }
    export main
}
'''


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def body_of(c, name):
    """The C body of `name`.

    Anchored on the signature line ending in `{`, so the earlier PROTOTYPE
    (same text, ending in `;`) cannot be matched instead - which silently
    returned another function's body and made every check here vacuous.
    """
    m = re.search(r"^void %s\([^;{]*\)\s*(?:BANKED\s*)?\{\n(.*?)\n\}"
                  % re.escape(name), c, re.S | re.M)
    return m.group(1) if m else ""


def compile_for(platform):
    """Compile the probe program together with the REAL lib module, the way
    the build tool resolves the import closure (a single-source compile does
    not pull `engine.scroll2d` in at all)."""
    lib = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "lib", "engine", "scroll2d.mos")
    with open(lib, encoding="utf-8") as f:
        srcs = [("scroll2d.mos", f.read()), ("main.mos", SRC.strip())]
    return MosaikCompiler().compile_program(srcs, platform=platform)


def main():
    print("The SMS 2D ring window has ONE definition")
    print("=" * 50)

    sms = compile_for("sms")
    gb = compile_for("gameboy")

    row_sms = body_of(sms, "engine_scroll2d_put_row2d")
    row_gb = body_of(gb, "engine_scroll2d_put_row2d")
    check(bool(row_sms) and bool(row_gb), "both targets emit put_row2d")

    # the ROW writer starts one column ahead on the SMS, and only there
    check("engine_scroll2d_s_base + 1" in row_sms,
          "sms: put_row2d starts at s_base + 1 (the shifted window)")
    check("engine_scroll2d_s_base + 1" not in row_gb,
          "gameboy: put_row2d starts at s_base (no shift, byte-identical)")

    # ... and it must not run off the map, the same guard the column path has
    check("engine_scroll2d_s_wcols" in row_sms,
          "sms: put_row2d skips columns past the map's right edge")

    # the COLUMN writer names the same window
    upd_sms = body_of(sms, "engine_scroll2d_update2d")
    upd_gb = body_of(gb, "engine_scroll2d_update2d")
    # (the SMS fork lowers as an initializer plus an override, so `+ 31`
    # survives as a dead store - assert the value actually STREAMED)
    check("nc = (engine_scroll2d_s_base + 32)" in upd_sms,
          "sms: update2d streams the column at s_base + 32")
    # THE GB FAMILY PREFETCHES + 30, NOT + 31, and the one-column difference
    # is a visible defect. The streamer runs before the frame's scroll reaches
    # the register (bkg.move publishes a shadow the present commits), so the
    # frame on screen while this writes is still at the PREVIOUS camera, whose
    # leftmost visible slot is exactly `(s_base + 31) & 31`. Measured on
    # the reference-engine sample conversion walking right: 608 of 608 streamed writes landed in the ring
    # slot displayed at the left edge - a full-height strip of content from 31
    # columns away - and 0 of 608 after. `+ 30` is off-screen under both the
    # old camera (slots b-1..b+20) and the new (b..b+21), and every column is
    # still written exactly once, one frame earlier.
    check("nc = (engine_scroll2d_s_base + 30)" in upd_gb
          and "+ 32" not in upd_gb,
          "gameboy: update2d prefetches s_base + 30 (off-screen under both "
          "the displayed and the pending camera)")
    check("put_col2d((engine_scroll2d_s_base + 1)" in upd_sms,
          "sms: scrolling LEFT re-streams s_base + 1, the same window")

    # the Game Gear shares the 28-row arm but NOT the shift (its viewport is a
    # 160 px window into the centre of the plane, so nothing is split)
    gg = compile_for("gamegear")
    row_gg = body_of(gg, "engine_scroll2d_put_row2d")
    check("engine_scroll2d_s_base + 1" not in row_gg,
          "gamegear: no shift (its viewport hides the seam already)")

    print("=" * 50)
    if _FAILED:
        print("Some tests failed:")
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
