#!/usr/bin/env python3
import os, re, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""An indexed sheet's `<name>_palette[4]` is emitted only when the program
NAMES it (E9, 2026-10-20).

The array used to be emitted for EVERY indexed PNG whenever the program
imported graphics.palette: vm-shardlings carried 39 of them in bank 0 and no
code read one (312 B on every console, 16 B a new species), raid-chrome 28 on
the Game Boy, where its palette code folds away. Now one filtered list
(`CodeGenerator.asset_palettes_named`, built in `generate()` from the
identifier set of the same ONE traversal as the `_used` flags) feeds both the
definitions (gen_modules) and the bank units' externs (gen_decls), so a
declaration and its definition cannot disagree. The walk sees the program
after the parser's platform fold and after tree-shaking, so a name in a folded
`if platform` arm or a shaken-out function keeps nothing.

A NAMED palette the sheet cannot carry (a 32-bit RGBA PNG, an indexed one
whose palette is padded to 256 entries: what an image editor saves after an
edit) is a compile error naming the PNG and the reason
(`mosaik_assets.asset_palette_problems`, `CodeGenerator._check_named_palettes`).
It used to reach sdcc as "Undefined identifier 'hudbg_palette'" in a generated
bank file, on the colour consoles only (the Game Boy folds the palette code
away), with a hint about banked arrays (2026-10-21).
"""

from mosaik import MosaikCompiler, PLATFORM_CAPS
import struct
import zlib
from mosaik_assets import (write_png_indexed, load_assets, load_asset_palettes,
                           asset_palette_problems, png_palette_problem)
import mosaik8_build

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(cond, label, detail=""):
    print("  %s %s" % ("ok:  " if cond else "FAIL:", label))
    if not cond:
        FAILS.append(label + (" -- " + detail if detail else ""))
    return cond


AUTH = {"ball": [(16, 24, 64), (255, 200, 120), (220, 90, 30), (255, 240, 200)],
        "wall": [(0, 0, 0), (90, 90, 90), (170, 170, 170), (255, 255, 255)],
        "door": [(40, 0, 0), (120, 40, 0), (200, 120, 40), (250, 230, 200)]}


def sheets(tmp):
    paths = []
    for name, pal in AUTH.items():
        p = os.path.join(tmp, name + ".png")
        write_png_indexed(p, 8, 8, [[(x + y) % 4 for x in range(8)]
                                    for y in range(8)], pal)
        paths.append(p)
    return paths


def program(body, extra=""):
    return '''module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "graphics.palette"
%s
    function main() {
%s
        video.enable_lcd()
        loop { video.wait_vblank() }
    }
    export main
}
''' % (extra, body)


#: ball: a call argument; wall: an indexed read inside a switch case; door:
#: never named.
NAMED = program('''        palette.load_sprite(0, ball_palette)
        var g: u8 = 1
        var w: u16 = 0
        switch g {
        case 1 {
            w = wall_palette[2]
        }
        }''')
NONE = program('''        sprite.set_tile(0, 0)''')
#: wall named ONLY in a function nothing calls: kept without shake_exports
#: (as before), dropped with it.
SHAKEN = program('''        palette.load_sprite(0, ball_palette)''', '''
    function unused() {
        palette.load_bkg(0, wall_palette)
    }''')
#: wall named ONLY in an `if platform` arm for another console.
FOLDED = program('''        palette.load_sprite(0, ball_palette)
        if platform == "lynx" {
            palette.load_bkg(0, wall_palette)
        }''')
#: a bank(1) function names wall; main names ball.
BANKED = program('''        palette.load_sprite(0, ball_palette)
        far()''', '''
    bank(1) function far() {
        palette.load_bkg(0, wall_palette)
    }''')


def defined(c, name):
    return re.search(r"const uint16_t %s_palette\[4\] = \{" % name, c) is not None


def compile_all(tmp):
    paths = sheets(tmp)
    assets, pals = load_assets(paths), load_asset_palettes(paths)
    assert [n for n, _c in pals] == list(AUTH), pals

    def comp(src, plat, **kw):
        c = MosaikCompiler()
        out = c.compile_program([("main.mos", src)], platform=plat, assets=assets,
                                asset_palettes=pals, **kw)
        return out, c.code_generator

    print("\n[named palettes only, on every console]")
    for plat in PLATFORM_CAPS:
        out, _g = comp(NAMED, plat)
        if not check(not out.startswith("Compilation error"), "%s compiles" % plat,
                     out[:300]):
            continue
        check(defined(out, "ball") and defined(out, "wall"),
              "%s: the two named palettes are defined (a call argument, an "
              "indexed read in a switch case)" % plat)
        check(not defined(out, "door") and "door_palette" not in out,
              "%s: the unnamed one is not emitted at all" % plat)

    print("\n[graphics.palette imported, nothing named]")
    for plat in ("gameboy", "gameboy_color", "sms", "lynx", "pce"):
        out, _g = comp(NONE, plat)
        check("_palette[4]" not in out
              and "Authored palettes of indexed-PNG assets" not in out,
              "%s: no asset palette and no header comment" % plat)

    print("\n[tree-shaking and the platform fold keep nothing]")
    out, _g = comp(SHAKEN, "gameboy")
    check(defined(out, "wall"), "a name in an uncalled function keeps the "
          "palette without shake_exports (unchanged behaviour)")
    out, _g = comp(SHAKEN, "gameboy", shake_exports=True)
    check(defined(out, "ball") and not defined(out, "wall"),
          "with shake_exports the shaken function's palette is dropped")
    out, _g = comp(FOLDED, "gameboy")
    check(defined(out, "ball") and not defined(out, "wall"),
          "a name in another console's `if platform` arm keeps nothing on GB")
    out, _g = comp(FOLDED, "lynx")
    check(defined(out, "wall"), "...and keeps it on the console it names")

    print("\n[a bank unit declares exactly what the main unit defines]")
    for plat in ("gameboy", "sms"):
        out, g = comp(BANKED, plat)
        units = getattr(g, "bank_units", {}) or {}
        check(1 in units, "%s: the bank(1) function has its own unit" % plat)
        u = units.get(1, "")
        check(defined(out, "ball") and defined(out, "wall")
              and not defined(out, "door"),
              "%s: the main unit defines ball and wall only" % plat)
        check("extern const uint16_t wall_palette[4];" in u
              and "extern const uint16_t ball_palette[4];" in u
              and "door_palette" not in u,
              "%s: the bank unit declares the same two and not door" % plat)
    return paths


def write_png_rgba(path, w, h):
    """A 32-bit RGBA PNG (colour type 6), what an editor saves an edit as."""
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    rows = b"".join(bytes([0]) + b"".join(bytes([68, 85, 136, 255] if (x + y) % 2 else [0, 0, 0, 0])
                                         for x in range(w)) for y in range(h))
    with open(path, "wb") as f:
        f.write(bytes([0x89]) + b"PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def unusable_sheets():
    """A named palette whose sheet cannot carry one stops the compile with
    the PNG's path and the reason; unnamed, the same sheets are harmless."""
    print("\n[a named palette the PNG cannot carry is a clear error]")
    tmp = tempfile.mkdtemp(prefix="asset_pal_bad_")
    try:
        good = os.path.join(tmp, "ball.png")
        write_png_indexed(good, 8, 8, [[(x + y) % 4 for x in range(8)] for y in range(8)], AUTH["ball"])
        rgba = os.path.join(tmp, "hudbg.png")
        write_png_rgba(rgba, 8, 8)
        wide = os.path.join(tmp, "wide.png")
        write_png_indexed(wide, 8, 8, [[(x + y) % 4 for x in range(8)] for y in range(8)],
                          AUTH["wall"] + [(0, 0, 0)] * 252)
        paths = [good, rgba, wide]
        check(png_palette_problem(good) is None, "a 4-entry indexed sheet has no problem")
        check("RGBA" in (png_palette_problem(rgba) or ""), "an RGBA sheet is named as RGBA",
              str(png_palette_problem(rgba)))
        check("256 entries" in (png_palette_problem(wide) or ""),
              "a padded palette is named with its entry count", str(png_palette_problem(wide)))
        probs = asset_palette_problems(paths)
        check(sorted(probs) == ["hudbg", "wide"], "the problems are keyed by C name", str(probs))
        assets, pals = load_assets(paths), load_asset_palettes(paths)

        def comp(src, plat):
            return MosaikCompiler().compile_program(
                [("main.mos", src)], platform=plat, assets=assets, asset_palettes=pals,
                asset_palette_problems=probs)
        for plat in ("gameboy_color", "gamegear", "sms", "lynx"):
            out = comp(program("        palette.load_bkg(0, hudbg_palette)"), plat)
            check(out.startswith("Compilation error") and rgba in out and "hudbg_palette" in out
                  and "RGBA" in out and "indexed PNG with at most 4 palette entries" in out,
                  "%s: naming an RGBA sheet's palette names the file and the fix" % plat, out[:400])
            out = comp(program("        palette.load_bkg(0, wide_palette)"), plat)
            check(out.startswith("Compilation error") and wide in out and "256 entries" in out,
                  "%s: naming a padded sheet's palette names the entry count" % plat, out[:400])
        out = comp(program("        palette.load_sprite(0, ball_palette)"), "gameboy_color")
        check(not out.startswith("Compilation error") and defined(out, "ball"),
              "the same unusable sheets, unnamed, change nothing", out[:300])
        out = comp(FOLDED.replace("wall_palette", "hudbg_palette"), "gameboy")
        check(not out.startswith("Compilation error"),
              "a name folded away for this console (the Game Boy's greys) is no error", out[:300])
        out = comp(program("        palette.load_bkg(0, hudbg_palette)",
                           "    const hudbg_palette: array[u16, 4] = [0, 1, 2, 3]"), "gameboy_color")
        check(not out.startswith("Compilation error"),
              "a `<sheet>_palette` the program declares itself is its own", out[:300])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def link(paths):
    print("\n[linked: only the named palettes reach the ROM's symbols]")
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    tmp = tempfile.mkdtemp(prefix="asset_pal_link_")
    try:
        src = os.path.join(tmp, "t.mos")
        with open(src, "w", encoding="utf-8") as f:
            f.write(NAMED)
        for plat, ext in (("gameboy", ".gb"), ("sms", ".sms")):
            cmd = [sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                   "--platform", plat, "--debug", src]
            for p in paths:
                cmd += ["--asset", p]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            bdir = os.path.join(tmp, "build", plat)
            if not check(os.path.exists(os.path.join(bdir, "t" + ext)),
                         "%s links" % plat, (r.stdout + r.stderr)[-600:]):
                continue
            noi = os.path.join(bdir, "t.noi")
            syms = open(noi, encoding="utf-8", errors="replace").read() \
                if os.path.exists(noi) else ""
            check(" _ball_palette " in syms and " _wall_palette " in syms
                  and "_door_palette" not in syms,
                  "%s: _ball_palette and _wall_palette are linked, "
                  "_door_palette is not" % plat)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("Asset palettes are emitted only when the program names them")
    print("=" * 60)
    tmp = tempfile.mkdtemp(prefix="asset_pal_")
    try:
        link(compile_all(tmp))
        unusable_sheets()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("asset_palette_by_use_test: all checks passed")
