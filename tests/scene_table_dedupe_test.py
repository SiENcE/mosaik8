#!/usr/bin/env python3
import os, re, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""The scenes module stores a repeated per-room row ONCE (E8 part A, the
studio's docs/shardlings-gen1-scale-plan.md, 2026-10-19).

A growing world filled the `scenes` CODE bank first: every room paid its
palette set (128 B), its per-tile palette row and an `if scene == N` arm of
the On Init dispatch, measured ~216 B a room on vm-shardlings (full at about
+30 rooms). Three changes, each emitted only where it SAVES, so a world with
no repeats keeps its module byte for byte:

- identical palette SETS are stored once and PAL_SET names each room's set;
- identical TILE_PAL rows share one TILE_PAL_OFF offset;
- a dense script-slot selector (6+ bound entries, smaller as a table) is a
  u16 table instead of an `if` chain.

Each check decodes what a room actually LOADS, so a wrong index fails here
rather than passing on a string match.
"""

import mosaik_assets as M
import mosaik_scenes.loaders as L
from mosaik_scenes.transpile import transpile
from mosaik import MosaikCompiler

FAILS = []

PALS = ['[[palette]]', 'name = "a"', 'colors = ["F8E8C8", "D89048", "A82820", "082048"]', '',
        '[[palette]]', 'name = "b"', 'colors = ["E0F8D0", "88C070", "346856", "081820"]', '',
        '[[palette]]', 'name = "c"', 'colors = ["FFFFFF", "AAAAAA", "555555", "000000"]', '']


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def _world(rooms, inits=None):
    """rooms: [(bkg_palettes, tile_palette), ...]; inits: on_init per room."""
    d = tempfile.mkdtemp()
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    idx = [[(x // 8) % 4 for x in range(32)] for _ in range(8)]
    M.write_png_indexed(os.path.join(d, "t.png"), 32, 8, idx, pal)
    body = ['[world]', 'module = "scenes"', 'map_w = 4', 'map_h = 2', 'vm = true', '',
            '[tileset]', 'png = "t.png"', '', '[kinds]', 'player = 0', ''] + PALS
    for i, (bp, tp) in enumerate(rooms):
        body += ['[[scene]]', 'name = "r%d"' % i, 'map = [[0,1,2,3],[3,2,1,0]]',
                 'collision = [[0,0,0,0],[1,1,1,1]]',
                 'bkg_palettes = %s' % bp, 'spr_palettes = [0, 1]',
                 'tile_palette = %s' % tp]
        if inits and inits[i]:
            body.append('on_init = "%s"' % inits[i])
        body.append('')
    with open(os.path.join(d, "world.toml"), "w", encoding="utf-8") as f:
        f.write("\n".join(body))
    world, base = L.load_world(os.path.join(d, "world.toml"))
    return transpile(world, base)


def _arr(src, name):
    """The LAST (default-console) definition of a const array, as ints/names."""
    ms = list(re.finditer(r"const %s: array\[\w+, (\d+)\] = \[(.*?)\]" % name, src, re.S))
    if not ms:
        return None
    return [v.strip() for v in ms[-1].group(2).replace("\n", " ").split(",") if v.strip()]


def _room_sets(src, n):
    """Each room's loaded BKG set, decoded the way load_palettes reads it."""
    bkg = _arr(src, "BKG_PAL")
    sel = _arr(src, "PAL_SET")
    out = []
    for r in range(n):
        k = int(sel[r]) if sel else r
        out.append(tuple(bkg[k * 32:(k + 1) * 32]))
    return out


def test_palette_sets():
    print("[palette sets: a repeat is stored once, distinct rooms are unchanged]")
    distinct = _world([("[0, 1]", "[0, 0, 1, 1]"), ("[1, 2]", "[0, 0, 1, 1]")])
    check("PAL_SET" not in distinct and "var off: u16 = scene" in distinct
          and len(_arr(distinct, "BKG_PAL")) == 64,
          "rooms with different sets keep `room * PAL_STRIDE` (old layout, byte for byte)")
    rep = _world([("[0, 1]", "[0, 0, 1, 1]"), ("[1, 2]", "[0, 0, 1, 1]"),
                  ("[0, 1]", "[0, 0, 1, 1]")])
    check(len(_arr(rep, "BKG_PAL")) == 64 and _arr(rep, "PAL_SET") == ["0", "1", "0"],
          "three rooms, two distinct sets: 2 sets stored, PAL_SET = 0, 1, 0")
    check("var off: u16 = PAL_SET[scene]" in rep, "load_palettes reads PAL_SET")
    a, b = _room_sets(distinct, 2), _room_sets(rep, 3)
    check(b[0] == a[0] and b[1] == a[1] and b[2] == a[0],
          "every room LOADS the same colours it did before the dedupe")


def test_tile_pal_rows():
    print("[tile palette rows: a repeated row shares one offset]")
    distinct = _world([("[0, 1]", "[0, 0, 1, 1]"), ("[0, 1]", "[1, 1, 0, 0]")])
    check(_arr(distinct, "TILE_PAL_OFF") == ["0", "4"] and len(_arr(distinct, "TILE_PAL")) == 8,
          "different rows keep their own offsets (old layout)")
    rep = _world([("[0, 1]", "[0, 0, 1, 1]"), ("[0, 1]", "[1, 1, 0, 0]"),
                  ("[0, 1]", "[0, 0, 1, 1]")])
    tp, off = _arr(rep, "TILE_PAL"), [int(v) for v in _arr(rep, "TILE_PAL_OFF")]
    check(len(tp) == 8 and off == [0, 4, 0],
          "the third room shares the first room's row: TILE_PAL_OFF = 0, 4, 0")
    want = {0: ["0", "0", "1", "1"], 1: ["1", "1", "0", "0"], 2: ["0", "0", "1", "1"]}
    check(all(tp[off[r]:off[r] + 4] == want[r] for r in range(3)),
          "every room READS its own slots through tile_pal")


def test_selector_table():
    print("[script-slot selectors: a dense one is a table, a short one a chain]")
    few = _world([("[0, 1]", "[0, 0, 1, 1]")] * 2, inits=["a", "b"])
    check("SCENE_INIT_TAB" not in few and "if scene == 0 {" in few,
          "2 bound rooms keep the `if` chain (byte for byte)")
    names = ["s%d" % i for i in range(8)]
    many = _world([("[0, 1]", "[0, 0, 1, 1]")] * 8, inits=names[:7] + [None])
    tab = _arr(many, "SCENE_INIT_TAB")
    check(tab == ["scripts.ENTRY_s%d" % i for i in range(7)] + ["NO_SCRIPT"],
          "8 rooms, 7 bound: SCENE_INIT_TAB in room order, NO_SCRIPT for the unbound one")
    fn = many.split("function scene_init(scene: u8) -> u16 {", 1)[1].split("\n    }", 1)[0]
    check("return SCENE_INIT_TAB[scene]" in fn and "if scene ==" not in fn,
          "scene_init reads the table instead of the chain")
    scripts = 'module "scripts" {\n%s\n    export %s\n}\n' % (
        "\n".join("    const ENTRY_%s: u16 = %d" % (n, 10 * i) for i, n in enumerate(names)),
        ", ".join("ENTRY_%s" % n for n in names))
    # SCENES FIRST: the table's initializer names `scripts` consts, and a
    # module emitted before `scripts` used to define the array above their
    # #defines (cc65: "Undeclared identifier"; a GBDK bank TU the same).
    for plat in ("gameboy", "gameboy_color", "sms", "pce"):
        out = MosaikCompiler().compile_program(
            [("scenes.mos", many), ("scripts.mos", scripts)], platform=plat)
        d = out.find("#define scripts_ENTRY_s0 ")
        t = out.find("scenes_SCENE_INIT_TAB[")
        check(not out.startswith("Compilation error:") and 0 <= d < t,
              "%s: the table compiles with `scenes` before `scripts`, the #define "
              "above the array (%s)" % (plat, out[:120] if out.startswith("Compilation") else "ok"))


def _far_world(n_rooms):
    """n rooms, every set and every tile row DIFFERENT (nothing dedupes)."""
    rooms = []
    for i in range(n_rooms):
        bp = "[%d, %d]" % (i % 3, (i // 3) % 3)
        tp = "[%d, %d, %d, %d]" % (i % 8, (i // 8) % 8, (i + 1) % 8, 3)
        rooms.append((bp, tp))
    return rooms


def test_far_mode():
    print("[far mode: past PAL_FAR_MIN the tables are read through copy_far]")
    import importlib
    EP = importlib.import_module("mosaik_scenes.transpile.emit_palettes")
    keep_min, keep_chunk = EP.PAL_FAR_MIN, EP.TP_CHUNK
    try:
        near = _world(_far_world(6))
        check("copy_far" not in near and "TP_ROW" not in near,
              "under the line: no far tables (byte for byte)")
        EP.PAL_FAR_MIN = 0
        EP.TP_CHUNK = 16          # 4-byte rows + a 4-byte pad: three rows a chunk
        far = _world(_far_world(6))
        check('import "platform.assets"' in far,
              "the module imports platform.assets for copy_far")
        check("assets.copy_far(PAL_BUF_B, BKG_PAL, off, 64)" in far
              and "palette.load_bkg_set(0, PAL_SLOTS, PAL_BUF_B, 0)" in far,
              "load_palettes copies the room's set into RAM, then loads it")
        fn = far.split("function tile_pal(scene: u8, t: u8) -> u8 {", 1)[1].split("\n    }", 1)[0]
        check("tp_load(scene)" in fn and "return TP_ROW[t]" in fn
              and "TILE_PAL[" not in fn,
              "tile_pal answers from the RAM row, refilled per scene")
        # Decode what tp_load copies for every room: its chunk, its offset,
        # the first 4 bytes of a 4-byte copy.
        names = ["TILE_PAL"] + ["TILE_PAL%d" % k for k in range(2, 9)]
        chunks = [_arr(far, nm) for nm in names]
        chunks = [ch for ch in chunks if ch is not None]
        off = [int(v) for v in _arr(far, "TILE_PAL_OFF")]
        blk = [int(v) for v in (_arr(far, "TILE_PAL_BLK") or ["0"] * 6)]
        check(len(chunks) == 2 and all(len(ch) <= 16 for ch in chunks)
              and _arr(far, "TILE_PAL_BLK") is not None,
              "the rows are CHUNKED (%d chunks of at most 16 B)" % len(chunks))
        rooms = _far_world(6)
        got = [chunks[blk[r]][off[r]:off[r] + 4] for r in range(6)]
        want = [[v.strip() for v in rooms[r][1].strip("[]").split(",")] for r in range(6)]
        check(got == want, "every room's tp_load copies ITS row: %s" % got)
        for plat in ("gameboy_color", "sms", "gamegear", "pce"):
            for banks in ([], ["scenes"]):
                comp = MosaikCompiler()
                main = ('module "main" {\n    import "platform.video"\n    import "scenes"\n'
                        '    function main() {\n        video.enable_lcd()\n'
                        '        scenes.load_palettes(1)\n'
                        '        var p: u8 = scenes.tile_pal(2, 1)\n'
                        '        loop { video.wait_vblank() }\n    }\n}\n')
                out = comp.compile_program([("scenes.mos", far), ("main.mos", main)],
                                           platform=plat, code_banks=banks)
                ok = not out.startswith("Compilation error:")
                streamed = dict(getattr(comp.code_generator, "streamed", {}) or {})
                moved = [s for s in ("scenes_BKG_PAL", "scenes_SPR_PAL", "scenes_TILE_PAL")
                         if s in streamed]
                if banks:
                    check(ok and len(moved) == 3,
                          "%s, code-banked: compiles, BKG_PAL / SPR_PAL / TILE_PAL in "
                          "DATA banks %s" % (plat, sorted(set(streamed[s] for s in moved))))
                else:
                    check(ok, "%s, unbanked: compiles (plain copy)%s" % (
                        plat, "" if ok else " " + out[:160]))
    finally:
        EP.PAL_FAR_MIN, EP.TP_CHUNK = keep_min, keep_chunk


def main():
    print("scenes: repeated per-room rows stored once (E8 part A)")
    print("=" * 50)
    test_palette_sets()
    test_tile_pal_rows()
    test_selector_table()
    test_far_mode()
    print()
    if FAILS:
        print("scene table dedupe FAILED (%d): %s" % (len(FAILS), FAILS))
        return 1
    print("All scene-table dedupe checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
