#!/usr/bin/env python3
"""Baked Lynx sprite sheets that TAKE TURNS in one upload slot stream from the
cart (`gen_streaming._pack_lynx_baked`).

With `[build] lynx_sprites` a sheet is build-time Suzy images, resident in
MAIN for good. Sheets uploaded to the SAME constant first tile can never be on
screen together (the later upload replaces the earlier one's tiles), so when
two or more share a slot their images are archived on the cart, one blob each
(a u16 offset per tile, then the images, on a 1 KB block), and the slot gets
ONE RAM buffer the size of its biggest blob. The Lynx shooter's three bosses
went from three resident image sets to one 673-byte buffer.

Pinned here:
  * codegen: two sheets at one constant slot stream into one buffer; a sheet
    alone in its slot, sheets at different slots, a non-constant first, a
    sheet also uploaded elsewhere, and a sheet named outside set_data all stay
    resident; no other program changes (no buffer, no loader);
  * the ROM (cc65 + a Lynx core): a fixture that swaps two sheets in one slot
    draws exactly the pictures of the same fixture with the sheets resident
    (forced by a non-constant first), at both sheets.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def _tile(seed):
    out = []
    for r in range(8):
        lo = (0x81 | (1 << (r % 8)) | seed) & 255
        hi = (0x3C ^ (seed << r)) & 255
        out += [lo, hi]
    return out


def _assets():
    """Three 16x16 sheets of one named sprite each (4 tiles)."""
    out = []
    for k, name in enumerate(("aa", "bb", "cc")):
        data = []
        for t in range(4):
            data += _tile(k * 7 + t * 3 + 1)
        out.append((name, bytes(data), 2))
    return out


def _compile(body, extra="", mode="whole", assets=None):
    src = '''
module "main" {
    import "graphics.sprite"
    import "platform.video"
    const SLOT: u8 = 8
    var where: u8 = 8
%s
    function main() {
%s
        video.enable_lcd()
        loop { video.wait_vblank() }
    }
    export main
}
''' % (extra, body)
    rects = {n: [(0, 2, 2)] for n in ("aa", "bb", "cc")}   # (first tile, w, h)
    return MosaikCompiler().compile_program([("main.mos", src)], platform="lynx",
                                            assets=assets or _assets(), lynx_sprites=mode,
                                            sheet_rects=rects)


def test_codegen():
    print("codegen: which sheets stream")
    two = _compile("        sprite.set_data(SLOT, 4, aa_tiles)\n"
                   "        sprite.set_data(SLOT, 4, bb_tiles)\n")
    check("two sheets at one constant slot compile",
          not two.startswith("Compilation error"), two[:400])
    check("... stream into ONE buffer through the loader",
          "gbs_spr_data_bstream(SLOT, 4," in two and "gbs_bimg_0" in two
          and "gbs_bimg_1" not in two)
    check("... and their images are not resident",
          "aa_tiles: baked images" in two and "bb_tiles: baked images" in two
          and "gbs_lt_aa" not in two and "gbs_lt_bb" not in two)
    # sheets with see-through pixels (dense noise packs to the literal size)
    holes = [(n, bytes(b & 0xF0 for b in d), bpp) for n, d, bpp in _assets()]
    pair = ("        sprite.set_data(SLOT, 4, aa_tiles)\n"
            "        sprite.set_data(SLOT, 4, bb_tiles)\n")
    two_w = _compile(pair, assets=holes)
    two_p = _compile(pair, mode="packed", assets=holes)
    size = lambda c: int(re.search(r"static uint8_t gbs_bimg_0\[(\d+)\];", c).group(1))
    check("lynx_sprites = \"packed\": the streamed sheets are packed (smaller buffer)",
          "gbs_spr_data_bstream(SLOT, 4," in two_p and size(two_p) < size(two_w),
          "%d vs %d" % (size(two_p), size(two_w)))
    cases = {
        "a sheet alone in its slot": "        sprite.set_data(SLOT, 4, aa_tiles)\n",
        "sheets at two different slots": "        sprite.set_data(SLOT, 4, aa_tiles)\n"
                                         "        sprite.set_data(0, 4, bb_tiles)\n",
        "a non-constant first": "        sprite.set_data(where, 4, aa_tiles)\n"
                                "        sprite.set_data(where, 4, bb_tiles)\n",
    }
    for label, body in cases.items():
        c = _compile(body)
        check("%s: resident, no loader" % label,
              not c.startswith("Compilation error") and "gbs_spr_data_bstream" not in c
              and "gbs_bimg_" not in c, c[:300])
    moved = _compile("        sprite.set_data(SLOT, 4, aa_tiles)\n"
                     "        sprite.set_data(SLOT, 4, bb_tiles)\n"
                     "        sprite.set_data(0, 4, bb_tiles)\n")
    check("a sheet also uploaded elsewhere stays resident (and its partner, now alone)",
          "gbs_spr_data_bstream" not in moved)
    named = _compile("        sprite.set_data(SLOT, 4, aa_tiles)\n"
                     "        sprite.set_data(SLOT, 4, bb_tiles)\n"
                     "        var b: u8 = bb_tiles[0]\n")
    check("a sheet read outside set_data stays resident",
          "bb_tiles: baked images" not in named)


# ------------------------------------------------------------------ the ROM
PROG = '''module "main" {
    import "graphics.sprite"
    import "platform.video"
    const SLOT: u8 = 8
    var where: u8 = 8
    function main() {
        sprite.set_data(%(f)s, aa_tile_count, aa_tiles)
        sprite.set_tile(0, %(f)s + aa_a_tile)
        sprite.move(0, 40, 30)
        sprite.set_tile(1, %(f)s + aa_a_tile)
        sprite.move(1, 90, 50)
        video.enable_lcd()
        video.show_sprites()
        var n: u16 = 0
        loop {
            if n == 240 {
                sprite.set_data(%(f)s, bb_tile_count, bb_tiles)
            }
            n = n + 1
            video.wait_vblank()
        }
    }
    export main
}
'''


def _sheet(path, seed):
    from PIL import Image
    im = Image.new("P", (16, 16), 0)
    im.putpalette([255, 255, 255, 170, 170, 170, 85, 85, 85, 0, 0, 0] + [0] * 756)
    px = im.load()
    for y in range(16):
        for x in range(16):
            v = ((x * (seed + 1) + y * 3 + (x ^ y)) // 3) % 4
            if (x, y) == (seed, 15 - seed):
                v = 3
            px[x, y] = v
    im.save(path)
    with open(path[:-4] + ".sprites.toml", "w", encoding="utf-8") as f:
        f.write('[[sprite]]\nname = "%s_a"\nrect = [0, 0, 16, 16]\n'
                % os.path.basename(path)[:-4])


def _project(tmp, name, first):
    proj = os.path.join(tmp, name)
    os.makedirs(os.path.join(proj, "src"))
    os.makedirs(os.path.join(proj, "assets"))
    _sheet(os.path.join(proj, "assets", "aa.png"), 2)
    _sheet(os.path.join(proj, "assets", "bb.png"), 5)
    with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
        f.write(PROG % {"f": first})
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "bst"\nversion = "0.1.0"\n'
                'target_platforms = ["lynx"]\n\n[source]\nfolder = "src/"\n\n'
                '[assets]\nsprites = ["assets/aa.png", "assets/bb.png"]\n\n'
                '[build]\noutput_dir = "build"\nlynx_sprites = "whole"\n')
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "lynx", proj], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    rom = os.path.join(proj, "build", "lynx", "bst.lnx")
    c = os.path.join(proj, "build", "lynx", "bst.c")
    text = open(c, encoding="utf-8").read() if os.path.isfile(c) else ""
    return (rom if r.returncode == 0 and os.path.isfile(rom) else None), text, r.stdout + r.stderr


CAPTURE = r'''
import sys, hashlib, json
sys.path.insert(0, sys.argv[1])
import run_lynx as R
rom, spans = sys.argv[2], json.loads(sys.argv[3])
b = (R.SessionBuilder.defaults(R.CORE).with_content(rom)
     .with_paths(R.ExplicitPathDriver(corepath=R.CORE, system=R.SYSTEM_DIR, save=R.SYSTEM_DIR,
                                      assets=R.SYSTEM_DIR, playlist=R.SYSTEM_DIR)).with_perf(None))
out = [{} for _ in spans]
with b.build() as s:
    for f in range(1, max(e for _a, e in spans) + 1):
        s.run()
        for k, (a, e) in enumerate(spans):
            if a <= f <= e:
                im = R.frame_image(s)
                bg = im.getpixel((0, 0))
                ink = sum(1 for p in im.getdata() if p != bg)
                out[k][hashlib.md5(im.tobytes()).hexdigest()] = ink
print(json.dumps(out))
'''


def _pictures(rom, spans):
    """For each (first, last) frame span: {picture hash: ink pixels} shown.
    A SET, not one frame: a static sprite-only Lynx program shows its sprites
    on one flip page only (a known engine fault), so one frame is a coin flip,
    and the two ROMs boot a few frames apart."""
    import json
    r = subprocess.run([sys.executable, "-c", CAPTURE,
                        os.path.join(ROOT, "emu", "libretro"), rom, json.dumps(spans)],
                       capture_output=True, text=True, cwd=ROOT)
    for line in reversed(r.stdout.splitlines()):
        if line.startswith("["):
            return json.loads(line)
    return None


def _lynx_core():
    return any(os.path.isfile(os.path.join(ROOT, "emu", "libretro", s + "_libretro" + e))
               for s in ("mednafen_lynx", "handy") for e in (".dll", ".so"))


def test_rom():
    print("ROM: streamed == resident")
    from mosaik8_build import cc65_available
    if not cc65_available() or not _lynx_core():
        print("  [SKIP] cc65 or a Lynx core not installed")
        return
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("  [SKIP] Pillow not installed")
        return
    tmp = tempfile.mkdtemp(prefix="lynxbst_")
    try:
        st, stc, out1 = _project(tmp, "st", "SLOT")
        rs, rsc, out2 = _project(tmp, "rs", "where")
        check("both fixtures build", st and rs, (out1 + out2)[-1200:])
        if not (st and rs):
            return
        check("the SLOT fixture streams, the `where` one does not",
              "gbs_spr_data_bstream(" in stc and "gbs_spr_data_bstream(" not in rsc)
        spans = [(150, 230), (380, 460)]          # the first sheet, then the second
        a, b = _pictures(st, spans), _pictures(rs, spans)
        check("both ROMs were captured", a is not None and b is not None)
        if a is None or b is None:
            return
        for k, label in enumerate(("before the swap", "after the swap")):
            drawn_a = {h for h, ink in a[k].items() if ink > 100}
            drawn_b = {h for h, ink in b[k].items() if ink > 100}
            check("%s: the same sprite pictures (%d / %d)" % (label, len(drawn_a), len(drawn_b)),
                  drawn_a and drawn_a == drawn_b)
        check("the second sheet really replaced the first",
              not ({h for h, i in a[0].items() if i > 100}
                   & {h for h, i in a[1].items() if i > 100}))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_codegen()
    test_rom()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
