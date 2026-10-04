#!/usr/bin/env python3
"""An animated background tile carries its own PALETTE on SMS/GG.

Those two consoles store every background tile at 4bpp, and the port's 2bpp
upload decides which 4 CRAM entries a tile lands on. The scene TILESET upload
passes a slot PER TILE (`bkg.set_data_pal`); an animated FRAME goes through
the plain `bkg.set_data`, which uses the port's `_current_2bpp_palette` - so
without setting it, every frame landed on palette 0 and the animation
rendered in the wrong colours while the static art around it was right (the
converted sample's waterfall, flowers and cave mouth on the Game Gear).

Same mechanism the SPRITE path already uses: a whole upload shares one slot,
so set the port's map and upload. RESTORED afterwards, because the map is
global state and the next plain background upload would otherwise inherit an
animated tile's palette.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik_scenes.base import _emit_scene_animated  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def _emit(has_tile_pal):
    """One scene with one 2-frame animation on tile 7."""
    src = bytes(range(16)) * 4
    anims = [[({"tile": 7, "count": 1, "period": 12, "frames": [0, 1]}, src)]]
    L = []
    got = _emit_scene_animated(L, anims, [{"name": "room"}], tiles=None,
                               has_tile_pal=has_tile_pal)
    return got, "\n".join(L)


def main():
    print("Animated background tile palettes (SMS/GG)")
    print("=" * 50)
    on, src = _emit(True)
    check("the animation is emitted", on and "SAN0_TILE" in src)
    check("...behind an SMS/GG fork (every other console folds it away)",
          'if platform == "sms" or platform == "gamegear" {' in src, src[:400])
    check("...selecting the tile's own slot through the port's 2bpp map",
          "palette.set_2bpp(SAN_PAL2BPP[tile_pal(san_room, t) & 3])" in src,
          src[:600])
    check("...over the four nibble maps the tileset upload uses",
          "[ 0x3210, 0x7654, 0xBA98, 0xFEDC ]" in src)
    # EVERY upload site: the seed and each switch arm.
    n_up = src.count("bkg.set_data(SAN0_TILE")
    n_sel = src.count("san_pal(SAN0_TILE)")
    check("EVERY upload selects the palette first (%d uploads, %d selects)"
          % (n_up, n_sel), n_up > 0 and n_sel == n_up, src)
    # count CALL sites only - the fork emits a DEFINITION in each arm too
    n_off = sum(1 for ln in src.splitlines()
                if "san_pal_off()" in ln and "function" not in ln)
    check("...and RESTORES it after, so the next plain upload does not "
          "inherit an animated tile's palette (%d restores)" % n_off,
          n_off == n_up, src)
    # A world with no per-tile palettes has no `tile_pal` to call at all.
    off, src2 = _emit(False)
    check("a world with NO per-tile palettes emits the no-op pair instead "
          "(tile_pal does not exist there)",
          "tile_pal(" not in src2 and "local function san_pal(t: u8) {" in src2,
          src2[:400])
    print("=" * 50)
    if failed:
        print("%d check(s) FAILED" % failed)
        return 1
    print("All scene-anim palette checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
