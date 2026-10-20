#!/usr/bin/env python3
import os, re, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""The scenes module's per-room GEOMETRY tables leave the resident image (E7,
2026-10-20).

SCENE_W / SCENE_H / MAP_OFF / MAP_BLK / TS_OFF / TS_TC / TS_BLK were the last
per-room tables in bank 0, measured 7 B a room on vm-shardlings, because
RESIDENT functions read them (map_tile / collision_at / paint / warm /
ts_upload, pinned by their seam reads, and rooms.mos's bank(0) cell_at /
solid_at). Under the cache every resident reader reads the CURRENT room's row
from RAM (`G_*`, filled by the BANKED `geom_set`), so code banking puts the
tables in the scenes bank. Emitted only where it SAVES (`geom_far`: a
code-banked paint_table world whose tables' floor beats the cache's measured
fixed cost); everywhere else the module is byte for byte what it was.
"""

import importlib

import mosaik_assets as M
import mosaik_scenes.loaders as L
from mosaik_scenes.transpile import transpile
from mosaik import MosaikCompiler
import mosaik_vm

EM = importlib.import_module("mosaik_scenes.transpile.emit_maps")

FAILS = []

#: The geometry tables, by their scenes-module names.
GEOM = ("SCENE_W", "SCENE_H", "MAP_OFF", "MAP_BLK", "TS_OFF", "TS_TC", "TS_BLK")


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def _world(n_rooms=8, banked=True, metatiles=False, uniform=False, own_ts=True):
    """A paint_table + stream world of `n_rooms`: sizes alternate (unless
    `uniform`), odd rooms bring their own tileset (when `own_ts`), every room
    has a collision row. `banked` writes a mosaik.toml listing scenes in
    [build] code_banks next to it."""
    d = tempfile.mkdtemp()
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    for nm, sh in (("t.png", 0), ("s.png", 1), ("u.png", 2)):
        idx = [[((x // 8) + sh) % 4 for x in range(32)] for _ in range(8)]
        M.write_png_indexed(os.path.join(d, nm), 32, 8, idx, pal)
    if banked:
        with open(os.path.join(d, "mosaik.toml"), "w", encoding="utf-8") as f:
            f.write('[project]\nname = "geom"\n\n[build]\ncode_banks = ["scenes"]\n')
    body = ['[world]', 'module = "scenes"', 'map_w = 4', 'map_h = 2', 'vm = true',
            'stream = true', 'paint_table = true']
    if metatiles:
        body.append('metatiles = true')
    body += ['', '[tileset]', 'png = "t.png"', '', '[kinds]', 'player = 0', '']
    for i in range(n_rooms):
        wide = (i % 2 == 1) and not uniform
        w, h = (6, 4) if wide else (4, 2)
        rows = [[(x + y + i) % 4 for x in range(w)] for y in range(h)]
        col = [[1 if y == h - 1 else 0 for x in range(w)] for y in range(h)]
        body += ['[[scene]]', 'name = "r%d"' % i,
                 'map = %s' % rows, 'collision = %s' % col]
        if wide:
            body += ['map_w = %d' % w, 'map_h = %d' % h]
        if own_ts and i % 2 == 1:
            # two DIFFERENT own tilesets, so a small ts chunk splits them
            body.append('tileset = "%s"' % ("s.png" if i % 4 == 1 else "u.png"))
        body.append('')
    with open(os.path.join(d, "world.toml"), "w", encoding="utf-8") as f:
        f.write("\n".join(body))
    world, base = L.load_world(os.path.join(d, "world.toml"))
    return world, base


def _fn(src, name):
    """The body of `function name(` in the generated module (to its closing
    brace at the function's own indent), or "" when absent."""
    m = re.search(r"(?m)^( *)(?:bank\(\d+\) )?function %s\(" % name, src)
    if not m:
        return ""
    ind = m.group(1)
    rest = src[m.start():]
    end = re.search(r"(?m)^%s\}" % ind, rest)
    return rest[:end.end()] if end else rest


def _readers(src):
    """{function: [geometry tables it INDEXES]} over every function."""
    out = {}
    for m in re.finditer(r"(?m)^ *(?:bank\(\d+\) )?function (\w+)\(", src):
        body = _fn(src, m.group(1))
        hits = sorted({t for t in GEOM if re.search(r"\b%s\[" % t, body)})
        if hits:
            out[m.group(1)] = hits
    return out


def test_rule():
    print("[the rule: emitted only where it saves]")
    w, b = _world()
    check(EM.geom_far(w, b), "a banked 8-room world (floor %d B > %d) takes the cache"
          % (EM._geom_table_floor(w), EM.GEOM_FAR_MIN))
    w2, b2 = _world(banked=False)
    check(not EM.geom_far(w2, b2), "unbanked: off (every table is resident anyway)")
    w3, b3 = _world(metatiles=True)
    check(not EM.geom_far(w3, b3), "metatiles: off (its own offsets)")
    w4, b4 = _world(n_rooms=8, uniform=True, own_ts=False)
    check(EM._geom_table_floor(w4) == 8 and not EM.geom_far(w4, b4),
          "uniform, no own tileset, 8 rooms: floor 8 B, under the line: off")
    w5, b5 = _world(n_rooms=27, uniform=True, own_ts=False)
    check(EM._geom_table_floor(w5) == 27 and EM.geom_far(w5, b5),
          "the same shape at 27 rooms: floor 27 B, over the line: on")


def test_off_is_byte_identical():
    print("[off: byte for byte]")
    keep = EM.GEOM_FAR_MIN
    try:
        for kw in ({"banked": False}, {"metatiles": True},
                   {"n_rooms": 8, "uniform": True, "own_ts": False}):
            w, b = _world(**kw)
            src = transpile(w, b)
            EM.GEOM_FAR_MIN = 10 ** 6
            ref = transpile(w, b)
            EM.GEOM_FAR_MIN = keep
            check(src == ref and "geom_set" not in src,
                  "%s: the module is the near module" % kw)
    finally:
        EM.GEOM_FAR_MIN = keep


def test_readers():
    print("[on: no resident reader indexes a geometry table]")
    w, b = _world()
    far = transpile(w, b)
    keep = EM.GEOM_FAR_MIN
    EM.GEOM_FAR_MIN = 10 ** 6
    try:
        near = transpile(w, b)
    finally:
        EM.GEOM_FAR_MIN = keep
    rn, rf = _readers(near), _readers(far)
    # (ts_upload exists in a COLOURED world only; vm-shardlings' A/B covers it)
    hot = ("map_tile", "collision_at", "paint")
    check(all(f in rn for f in hot),
          "near: the resident readers index the tables (the check can fail): %s"
          % {f: rn.get(f) for f in hot})
    allowed = {"geom_set", "map_off_of", "map_blk_of", "paint_attrs"}
    check(set(rf) <= allowed and "geom_set" in rf,
          "far: only the BANKED geom_set / lookups / paint_attrs read them: %s" % rf)
    for f in ("map_tile", "collision_at"):
        body = _fn(far, f)
        check("var off: u16 = G_OFF" in body and "if G_S != scene {" in body
              and "off = map_off_of(scene)" in body,
              "%s answers from the cache, or asks for another scene" % f)
    for f in ("paint", "warm"):
        body = _fn(far, f)
        if body:
            first = [l.strip() for l in body.splitlines()[1:] if l.strip()][0]
            check(first == "geom_set(scene)", "%s sets the cache first" % f)
    gs = _fn(far, "geom_set")
    check("G_S = scene" in gs and "G_OFF = MAP_OFF[scene]" in gs
          and "G_W = SCENE_W[scene]" in gs and "G_TSC = TS_TC[scene]" in gs,
          "geom_set fills every field and the tag")
    offs = [int(v) for v in re.search(r"const MAP_OFF: array\[\w+, \d+\] = \[(.*?)\]",
                                      far, re.S).group(1).replace("\n", " ").split(",")
            if v.strip()]
    check(offs[0] == 0, "scene 0 starts at offset 0 (the cache's BSS state is its row)")


def test_placement():
    print("[on: the tables compile into the scenes bank, not the main TU]")
    w, b = _world()
    far = transpile(w, b)
    keep = EM.GEOM_FAR_MIN
    EM.GEOM_FAR_MIN = 10 ** 6
    try:
        near = transpile(w, b)
    finally:
        EM.GEOM_FAR_MIN = keep
    main = ('module "main" {\n    import "platform.video"\n    import "scenes"\n'
            '    function main() {\n        video.enable_lcd()\n'
            '        scenes.paint(1)\n'
            '        var t: u8 = scenes.map_tile(2, 3)\n'
            '        var k: u8 = scenes.collision_at(1, 0)\n'
            '        loop { video.wait_vblank() }\n    }\n}\n')

    def resident(src, plat):
        comp = MosaikCompiler()
        out = comp.compile_program([("scenes.mos", src), ("main.mos", main)],
                                   platform=plat, code_banks=["scenes"])
        if out.startswith("Compilation error:"):
            return None, out[:200]
        defs = {t for t in GEOM
                if re.search(r"scenes_%s\[\d+\] = \{" % t, out)}
        banked = {t for t in GEOM for u in (comp.code_generator.bank_units or {}).values()
                  if re.search(r"scenes_%s\[\d+\] = \{" % t, u)}
        return defs, banked

    for plat in ("gameboy", "gameboy_color", "sms", "gamegear"):
        dn, _bn = resident(near, plat)
        df, bf = resident(far, plat)
        check(dn is not None and {"SCENE_W", "MAP_OFF", "TS_OFF"} <= dn,
              "%s near: the tables are resident (the check can fail): %s" % (plat, dn))
        check(df is not None and not df and {"SCENE_W", "MAP_OFF", "TS_OFF"} <= bf,
              "%s far: none resident, all in a bank TU (%s)" % (plat, sorted(bf or [])))
    comp = MosaikCompiler()
    out = comp.compile_program([("scenes.mos", far), ("main.mos", main)],
                               platform="pce", code_banks=["scenes"])
    check(not out.startswith("Compilation error:"), "pce far, code-banked: compiles")


def test_chunked():
    print("[on, CHUNKED maps and tilesets: the chunk rides the cache too]")
    CX = importlib.import_module("mosaik_scenes.transpile.context")
    EH = importlib.import_module("mosaik_scenes.transpile.emit_head")
    keep = CX._MAP_CHUNK, EH._TS_CHUNK
    try:
        CX._MAP_CHUNK, EH._TS_CHUNK = 40, 64      # 1-4 rooms a map chunk, 1 tileset a ts chunk
        w, b = _world()
        far = transpile(w, b)
    finally:
        CX._MAP_CHUNK, EH._TS_CHUNK = keep
    check("const MAP_BLK:" in far and "const TS_BLK:" in far,
          "the fixture chunks both (MAP_BLK and TS_BLK emitted)")
    rf = _readers(far)
    check(set(rf) <= {"geom_set", "map_off_of", "map_blk_of", "paint_attrs"}
          and "map_blk_of" in rf, "only the banked functions index them: %s" % rf)
    gs = _fn(far, "geom_set")
    check("G_BLK = MAP_BLK[scene]" in gs and "G_TSB = TS_BLK[scene]" in gs,
          "geom_set fills the map chunk and the tileset chunk")
    mt = _fn(far, "map_tile")
    check("blk = map_blk_of(scene)" in mt and "map_cell(blk, off, idx)" in mt,
          "map_tile reads its chunk from the cache, or asks for another scene")
    check("if G_TSB == 0 {" in _fn(far, "paint"), "paint forks on the cached tileset chunk")
    main = ('module "main" {\n    import "platform.video"\n    import "scenes"\n'
            '    function main() {\n        video.enable_lcd()\n        scenes.paint(1)\n'
            '        var t: u8 = scenes.map_tile(5, 3)\n'
            '        loop { video.wait_vblank() }\n    }\n}\n')
    for plat in ("gameboy", "sms"):
        out = MosaikCompiler().compile_program([("scenes.mos", far), ("main.mos", main)],
                                               platform=plat, code_banks=["scenes"])
        check(not out.startswith("Compilation error:")
              and not re.search(r"scenes_(MAP_BLK|TS_BLK)\[\d+\] = \{", out),
              "%s: compiles, no chunk table resident" % plat)


def test_rooms_side():
    print("[rooms.mos reads the cache and writes it with `room`]")
    base = dict(types=["topdown"], uniform=False, has_collision=True)
    near = mosaik_vm.emit_rooms_mos(dict(base))
    far = mosaik_vm.emit_rooms_mos(dict(base, geom_far=True))
    check("scenes.SCENE_W[room]" in near and "scenes.geom_set(" not in near,
          "near: cell_at indexes SCENE_W (the check can fail)")
    check("scenes.SCENE_W[" not in far and "scenes.SCENE_H[" not in far
          and "var w16: u16 = scenes.G_W" in far,
          "far: no table index, cell_at reads scenes.G_W")
    lr = _fn(far, "load_room").splitlines()
    check(len(lr) > 2 and lr[1].strip() == "room = rm"
          and lr[2].strip() == "scenes.geom_set(rm)",
          "load_room sets the cache right after `room = rm`")


def main():
    print("scenes: the per-room geometry tables leave bank 0 (E7)")
    print("=" * 50)
    test_rule()
    test_off_is_byte_identical()
    test_readers()
    test_placement()
    test_chunked()
    test_rooms_side()
    print()
    if FAILS:
        print("scene geometry cache FAILED (%d): %s" % (len(FAILS), FAILS))
        return 1
    print("All scene-geometry-cache checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
