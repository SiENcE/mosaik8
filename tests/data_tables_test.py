#!/usr/bin/env python3
import os, re, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""E5 DATA TABLES (2026-10-20): rows x named columns of constants, read one
cell at a time by TBL_GET instead of a SWITCH case per row.

`scripts/tables.toml` -> one TABLES byte blob in scripts.mos (a 6-byte header
per table, then the cells) read through the one-line resident
`scripts.table_byte` (the assets.code_byte seam, like STRINGS: a DATA bank on a
banked build). The header and cell arithmetic live in vm.core's tbl_read,
which BANKS, because a whole reader in the scripts module (which can never
bank) measured 377 B of Game Boy bank 0; the split costs vm-shardlings 79 B (GB) / 75 B (SMS).
The reader is registered by the generated rooms.mos (core.set_tables); a
program that reads no table emits none of it.
"""

import io
import contextlib

import mosaik_vm as m
import mosaik8_build

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
FAILS = []


def check(cond, msg, detail=""):
    print("  %s %s%s" % ("ok:  " if cond else "FAIL:", msg, ("  -- %s" % detail) if detail and not cond else ""))
    if not cond:
        FAILS.append(msg)
    return cond


TABLES = [{"name": "species", "columns": ["el", "hp"], "cell": "u8",
           "rows": [[0, 20], [1, 24], [2, 255]]},
          {"name": "moves", "columns": ["pow", "acc"], "cell": "i16",
           "rows": [[-5, 90], [300, -32768]]}]
READS = [  # (var, table, row expression, column, expected)
    ("ta", "species", "ts", "hp", 255),
    ("tb", "moves", "0", "pow", -5),
    ("tc", "species", "ts + 5", "el", 0),
    ("td", "moves", "ts - 1", "acc", -32768)]


def _read_events():
    evs = [{"event": "set_var", "var": "ts", "value": 2},
           {"event": "set_var", "var": "tc", "value": 7}]
    for var, t, row, col, _want in READS:
        evs.append({"event": "table_get", "table": t, "row": row, "column": col, "var": var})
    return evs


def model():
    print("\n[the tables model]")
    blob = m.encode_tables(m.validate_tables(TABLES))
    check(list(blob[:12]) == [12, 0, 3, 0, 2, 1, 18, 0, 2, 0, 4, 2],
          "the header: cells offset, rows, row stride, cell width per table",
          list(blob[:12]))
    check(len(blob) == 12 + 3 * 2 + 2 * 4, "then the cells, row-major", len(blob))
    check(m.table_cell(blob, 0, 2, 1) == 255 and m.table_cell(blob, 1, 1, 1) == -32768,
          "table_cell reads u8 unsigned and i16 signed")
    check(m.table_cell(blob, 0, 3, 0) == 0 and m.table_cell(blob, 0, -1, 0) == 0,
          "a row past the end or negative reads 0")
    for bad, why in (({"name": "x y", "columns": ["a"]}, "a name that is no identifier"),
                     ({"name": "t", "columns": ["a", "a"]}, "two columns with one name"),
                     ({"name": "t", "columns": ["a"], "rows": [[1, 2]]}, "a row of the wrong length"),
                     ({"name": "t", "columns": ["a"], "rows": [[256]]}, "a u8 cell over 255"),
                     ({"name": "t", "columns": ["a"], "cell": "i16", "rows": [[40000]]}, "an i16 cell out of range"),
                     ({"name": "t", "columns": ["a"], "cell": "u32"}, "an unknown cell type"),
                     ({"name": "t", "columns": ["a"], "rows": [[1]], "row_names": ["x", "y"]},
                      "row names that do not match the rows")):
        try:
            m.validate_tables([bad])
            check(False, "%s is refused" % why)
        except m.VmError:
            check(True, "%s is refused" % why)
    try:
        m.encode_tables(m.validate_tables([{"name": "big", "columns": ["a", "b"], "cell": "i16",
                                             "rows": [[0, 0]] * 4100}]))
        check(False, "a blob past one ROM bank is refused")
    except m.VmError:
        check(True, "a blob past one ROM bank is refused")


def emission():
    print("\n[scripts.mos: emitted only when a script reads a table]")
    plain = [{"name": "main", "events": [{"event": "set_var", "var": "a", "value": 1}]}]
    a = m.Compiler().compile(plain).to_scripts_mos()
    b = m.Compiler(TABLES).compile(plain).to_scripts_mos()
    check(a == b and "TABLES" not in b, "tables.toml present but unread: byte-identical")
    used = [{"name": "main", "events": _read_events()}]
    src = m.Compiler(TABLES).compile(used).to_scripts_mos()
    check("const TABLES: array[u8, 26]" in src and "function table_byte(off: u16) -> u8" in src,
          "read: the blob and the one-line reader")
    check(re.search(r"export [^\n]*\btable_byte\b", src) is not None, "...exported")
    fn = src[src.index("function table_byte"):]
    fn = fn[:fn.index("\n    }") + 6]
    check(fn.count("\n") == 2 and "assets.code_byte(TABLES, off)" in fn,
          "the reader is ONE code_byte line (the arithmetic is vm.core's)", fn)
    tagged = [{"name": "main", "events": [{"event": "set_var", "var": "a", "value": 1}]},
              {"name": "lynx_only", "platforms": ["lynx"], "events": _read_events()}]
    src = m.emit_scripts_module(tagged, tables=TABLES)
    check(src.count("function table_byte(") >= 2 and "if platform" in src,
          "per-console buckets: every bucket defines table_byte (the export list is uniform)")


def _copy(tmp, name="vm-bkg16"):
    dst = os.path.join(tmp, name)
    shutil.copytree(os.path.join(ROOT, "projects", name), dst,
                    ignore=shutil.ignore_patterns("build"))
    return dst


def _add_tables(root, banked=False):
    import toml
    sd = os.path.join(root, "scripts")
    with open(os.path.join(sd, "tables.toml"), "w", encoding="utf-8") as f:
        f.write(toml.dumps({"table": TABLES}))
    p = os.path.join(sd, "main.evt.toml")
    doc = toml.load(p)
    main = next(s for s in doc["script"] if s["name"] == "main")
    main["events"] = _read_events() + main["events"]
    with open(p, "w", encoding="utf-8") as f:
        f.write(toml.dumps(doc))
    if banked:
        mp = os.path.join(root, "mosaik.toml")
        with open(mp, encoding="utf-8") as f:
            t = f.read()
        t = t.replace("[build]\n", '[build]\ncode_banks = ["vm.core", "vm.actor", "vm.player", "vm.snd", "vm.entity", "vm.canim", '
                      '"engine.camera", "engine.anim", "scenes", "rooms", "glue", "main"]\n'
                      'bank_bytecode = true\n', 1)
        with open(mp, "w", encoding="utf-8") as f:
            f.write(t)
    _regen(root)


def _regen(root):
    import regen_generated
    for mod in ("scenes", "scripts", "rooms", "glue"):
        out = os.path.join(root, "src", mod + ".mos")
        if not os.path.exists(out):
            continue
        text = regen_generated._new_text(root, out, mod)
        if text is not None:
            with io.open(out, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)


def wiring(tmp):
    print("\n[rooms.mos registers the reader; the build warns when nothing does]")
    root = _copy(tmp, "vm-bkg16")
    rooms = open(os.path.join(root, "src", "rooms.mos"), encoding="utf-8").read()
    check("set_tables" not in rooms, "no table read: rooms.mos untouched")
    _add_tables(root)
    rooms = open(os.path.join(root, "src", "rooms.mos"), encoding="utf-8").read()
    check("core.set_tables(scripts.table_byte)" in rooms, "a table read: rooms.mos registers table_byte")
    srcs = [(fn, open(os.path.join(root, "src", fn), encoding="utf-8").read())
            for fn in os.listdir(os.path.join(root, "src")) if fn.endswith(".mos")]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        d = mosaik8_build._vm_dispatch_defines(srcs)
    check(d.get("VM_OP_TBL_GET") is True and "core.set_tables" not in buf.getvalue(),
          "the build turns the arm on and stays quiet when it is registered")
    stripped = [(fn, t.replace("core.set_tables(", "core.set_tables_GONE(")) for fn, t in srcs]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        mosaik8_build._vm_dispatch_defines(stripped)
    check("Warning" in buf.getvalue() and "core.set_tables" in buf.getvalue(),
          "...and WARNS when no source registers it (a hand shell reads 0s)", buf.getvalue())
    return root


def source_contract():
    print("\n[vm.core: the arithmetic banks, the operands are fetched resident]")
    src = open(os.path.join(ROOT, "lib", "vm", "core.mos"), encoding="utf-8").read()
    fold = src[src.index("    if VM_OP_TBL_GET {\n        var g_tbyte"):]
    fold = fold[:fold.index("\n    }\n") + 6]
    for fn in ("tbl_read", "tbl_op"):
        check(re.search(r"\n        local function %s\(" % fn, fold) is not None,
              "%s is declared inside the op's fold" % fn)
        check(not re.search(r"bank\(0\)[^\n]*function %s\(" % fn, src),
              "%s is NOT bank(0), so code_banks banks it" % fn)
    check("case OP_TBL_GET" not in src,
          "TBL_GET has NO case label (an empty folded case cost SMS / GG non-users 2-5 B)")
    dflt = src[src.index("            default {", src.index("        switch op {")):]
    arm = dflt[dflt.index("if op == OP_TBL_GET {"):]
    arm = arm[:arm.index("return ST_CONT")]
    check("if VM_OP_TBL_GET {\n                    if op == OP_TBL_GET {"
          in dflt.replace("\r\n", "\n"),
          "the default arm tests for it inside its own fold")
    check(arm.count("f8()") == 3 and "tbl_op(" in arm and "g_tbyte" not in arm,
          "the arm fetches its three operands and hands the rest to tbl_op")


def _heap_values(noi_path, mp, read_byte):
    syms = {}
    for line in open(noi_path, encoding="utf-8", errors="replace"):
        g = re.match(r"DEF (\S+) 0x([0-9A-Fa-f]+)", line)
        if g:
            syms[g.group(1)] = int(g.group(2), 16)
    heap = syms["_vm_core_heap"]
    out = {}
    for var, *_r in READS:
        a = heap + 2 * mp[var]
        v = read_byte(a) | read_byte(a + 1) << 8
        out[var] = v - 0x10000 if v & 0x8000 else v
    return out, syms


def _build(root, plat):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", plat, "--debug", root],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, r.stdout + r.stderr


def roms(tmp):
    print("\n[ROMs: the cells reach the heap on the real interpreter]")
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    want = {var: w for var, _t, _r, _c, w in READS}
    root = _copy(os.path.join(tmp, "unbanked"), "vm-bkg16")
    _add_tables(root)
    mp = m.Compiler(m.load_tables(os.path.join(root, "scripts"))).compile(
        m.load_scripts(os.path.join(root, "scripts"))).variables
    ok, log = _build(root, "gameboy")
    if check(ok, "the Game Boy build links", log[-400:]):
        from pyboy import PyBoy
        pb = PyBoy(os.path.join(root, "build", "gameboy", "vm-bkg16.gb"), window="null",
                   sound_emulated=False)
        pb.tick(400, False)
        got, _ = _heap_values(os.path.join(root, "build", "gameboy", "vm-bkg16.noi"), mp,
                              lambda a: pb.memory[a])
        pb.stop(save=False)
        check(got == want, "Game Boy (unbanked): every read lands, signed and out of range too", got)
    ok, log = _build(root, "sms")
    cores = [c for c in os.listdir(os.path.join(ROOT, "emu", "libretro"))
             if c.startswith("genesis_plus_gx_libretro")]
    if check(ok, "the SMS build links", log[-400:]) and cores:
        got = _sms_heap(os.path.join(root, "build", "sms", "vm-bkg16.sms"),
                        os.path.join(root, "build", "sms", "vm-bkg16.noi"), mp, cores[0])
        check(got == want, "SMS: the same values (sdcc z80)", got)
    for plat in ("lynx", "pce"):
        if mosaik8_build.cc65_available():
            ok, log = _build(root, plat)
            check(ok, "%s links (cc65)" % plat, log[-400:])

    banked = _copy(os.path.join(tmp, "banked"), "vm-bkg16")
    _add_tables(banked, banked=True)
    mp = m.Compiler(m.load_tables(os.path.join(banked, "scripts"))).compile(
        m.load_scripts(os.path.join(banked, "scripts"))).variables
    ok, log = _build(banked, "gameboy")
    if check(ok, "the code-banked Game Boy build links", log[-400:]):
        from pyboy import PyBoy
        pb = PyBoy(os.path.join(banked, "build", "gameboy", "vm-bkg16.gb"), window="null",
                   sound_emulated=False)
        pb.tick(400, False)
        got, syms = _heap_values(os.path.join(banked, "build", "gameboy", "vm-bkg16.noi"), mp,
                                 lambda a: pb.memory[a])
        pb.stop(save=False)
        check(syms.get("_scripts_TABLES", 0) >> 16 >= 1, "the TABLES blob sits in a ROM bank",
              hex(syms.get("_scripts_TABLES", 0)))
        check(syms.get("_vm_core_tbl_read", 0) >> 16 >= 1, "tbl_read is banked",
              hex(syms.get("_vm_core_tbl_read", 0)))
        check(got == want, "Game Boy (banked): every read lands", got)


def _sms_heap(rom, noi, mp, core_file):
    """A libretro core is a process singleton: read the heap in a child."""
    code = ("import sys, json; sys.path.insert(0, %r); from retro import Core\n"
            "c = Core(%r, %r); c.run(400); sys.stdout.write(c.ram().hex())\n"
            % (os.path.join(ROOT, "emu", "libretro"),
               os.path.join(ROOT, "emu", "libretro", core_file), rom))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    ram = bytes.fromhex(r.stdout.strip() or "")
    if not ram:
        return {"error": r.stderr[-300:]}
    got, _ = _heap_values(noi, mp, lambda a: ram[a - 0xC000])
    return got


def main():
    print("=" * 60)
    print("E5 data tables")
    print("=" * 60)
    tmp = tempfile.mkdtemp(prefix="data_tables_")
    try:
        model()
        emission()
        wiring(tmp)
        source_contract()
        roms(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print("data tables FAILED (%d): %s" % (len(FAILS), FAILS))
        return 1
    print("data_tables_test: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
