#!/usr/bin/env python3
"""On the Lynx, `songs.mos` computes a row's start ONCE (2026-10-15).

vm.music reads a row a byte at a time: per channel five `cell()` calls, then
`frames()` AFTER them. Each call paid a 16-bit SOFTWARE multiply for the row's
start (cc65), and a row straddling one of the cart archive's 256-byte pages
reloaded the page three times (the cells past the edge, frames before it, the
next row past it again; a reload is ~110,000 cycles). On the upright Lynx
shooter a music tick cost ~41,000 cycles on average (GearLynx), most of it the
row tick. The generator now emits, for the Lynx only, a memo of the current
row (song, row, start, frames byte): the first read of a row computes its
start and reads its frames byte with the row's FIRST page; every cell is an
add and a read. 33,500 cycles a tick, the shooter's on-time flips 93.9 ->
96.6 %. A row BUFFER was tried first and refused: 24 B of MAIN for the same
number of reads (the shooter overflowed by 7 B).

Pinned here:
  * the generated text: the Lynx arm memoises the row and its cell() has no
    multiply; the other arm is the old accessors, for one chunk and for a
    chunked library (`cbyte`);
  * off the Lynx (GB, SMS, PCE) the generated C is IDENTICAL to what the old
    accessors compile to, so those ROMs do not move;
  * on the Lynx the compiled `songs_cell` multiplies nothing;
  * the ROM (cc65 + a Lynx core): a 64-row song streamed from the cart, rows
    straddling pages and of varying length, plays with the SAME driver state
    at every song position under the old and the new accessors.
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

from mosaik_vm.songs import emit_songs_mos, CELL_CHUNK  # noqa: E402
from mosaik.compiler import MosaikCompiler  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


CH = ["pulse", "pulse", "wave", "noise"]


def _song(nrows, seed=0):
    """Rows of varying length and notes, so a wrong row, field or frames byte
    changes what the driver plays."""
    rows = []
    for r in range(nrows):
        cells = [[1 + (r * 7 + c * 3 + seed) % 60, 0, (r + c) % 16] for c in range(len(CH))]
        rows.append((3 + (r + seed) % 4, cells))
    return {"channels": list(CH), "rows": rows}


def old_form(text):
    """The module as the generator wrote it before 2026-10-15: the `else` arm
    of the platform fork, dedented, in place of the whole fork."""
    a = text.index('    if platform == "lynx" {\n')
    b = text.index("    } else {\n", a)
    c = text.index("\n    }\n", b + len("    } else {\n"))
    arm = text[b + len("    } else {\n"):c + 1]
    arm = "".join(line[4:] if line.startswith("    ") else line
                  for line in arm.splitlines(True))
    return text[:a] + arm + text[c + len("\n    }\n"):]


SHELL = '''module "main" {
    import "platform.video"
    import "vm.music"
    import "songs"
    function main() {
        music.set_song(songs.channels, songs.chkind, songs.cell, songs.frames, songs.rows)
        video.enable_lcd()
        music.play(0)
        loop {
            music.update()
            video.wait_vblank()
        }
    }
    export main
}
'''


def _compile(songs_text, platform):
    with open(os.path.join(ROOT, "lib", "vm", "music.mos"), encoding="utf-8") as f:
        music = f.read()
    return MosaikCompiler().compile_program(
        [("main.mos", SHELL), ("songs.mos", songs_text), ("music.mos", music)],
        platform=platform)


def _fn(c, name):
    m = re.search(r"\n[^\n]*\b%s\([^)]*\) \{\n(.*?)\n\}\n" % name, c, re.S)
    return m.group(1) if m else ""


def test_text():
    print("the generated text")
    one = emit_songs_mos([("a", _song(8))])
    lynx = one[one.index('if platform == "lynx" {'):one.index("} else {")]
    check("the Lynx arm memoises the row (song, row, start, frames)",
          all(s in lynx for s in ("var r_song: u8 = 255", "var r_row: u16",
                                  "var r_base: u16", "var r_frames: u8",
                                  "local function at_row(song: u8, row: u16)")))
    cell = lynx[lynx.index("function cell("):lynx.index("function frames(")]
    check("its cell() reads at the memoised start, with no multiply",
          "r_base + 1 + ch * 5 + field" in cell and "*" not in cell.replace("ch * 5", ""))
    check("its frames() returns the byte read with the row's first page",
          "r_frames = assets.code_byte(CELLS, r_base)" in lynx and "return r_frames" in lynx)
    old = old_form(one)
    check("the other arm is the old accessors",
          "    function cell(song: u8, row: u16, ch: u8, field: u8) -> u8 {\n"
          "        var i: u16 = CELLOFF[song] + row * (1 + CHN[song] * 5)\n"
          "        return assets.code_byte(CELLS, i + 1 + ch * 5 + field)\n    }\n" in old
          and "if platform" not in old)
    per = CELL_CHUNK // (1 + len(CH) * 5)
    big = emit_songs_mos([("a", _song(per - 2)), ("b", _song(per - 2, 1))])
    check("a chunked library memoises through cbyte",
          "r_frames = cbyte(song, r_base)" in big
          and "return cbyte(song, r_base + 1 + ch * 5 + field)" in big)


def test_codegen():
    print("codegen")
    for defs in ([("a", _song(8))],
                 [("a", _song(CELL_CHUNK // 21 - 2)), ("b", _song(CELL_CHUNK // 21 - 2, 1))]):
        new = emit_songs_mos(defs)
        tag = "chunked" if "CELLBLK" in new else "one chunk"
        for plat in ("gameboy", "sms", "pce"):
            a, b = _compile(old_form(new), plat), _compile(new, plat)
            check("%s, %s: the C is identical to the old accessors'" % (tag, plat),
                  not a.startswith("Compilation error") and a == b, b[:300])
        c = _compile(new, "lynx")
        check("%s, lynx: compiles" % tag, not c.startswith("Compilation error"), c[:300])
        cell = _fn(c, "songs_cell")
        check("%s, lynx: songs_cell multiplies nothing but the channel" % tag,
              cell and "songs_at_row(song, row);" in cell and "CHN" not in cell
              and cell.count("*") == 1 and "(ch * 5)" in cell, cell[:300])


# ------------------------------------------------------------------ the ROM
FIELDS = [("song", 1), ("row", 2), ("left", 1), ("playing", 1), ("tick", 1),
          ("ph3", 1), ("drum_left", 1), ("p_note", 1), ("p_cmd", 1), ("p_param", 1),
          ("sn", 3), ("sv", 3), ("se", 3), ("sa", 3), ("sc", 3)]


def _project(tmp, name, songs_text):
    proj = os.path.join(tmp, name, "memo")
    os.makedirs(os.path.join(proj, "src"))
    with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
        f.write(SHELL)
    with open(os.path.join(proj, "src", "songs.mos"), "w", encoding="utf-8") as f:
        f.write(songs_text)
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "memo"\nversion = "0.1.0"\n'
                'target_platforms = ["lynx"]\n\n[source]\nfolder = "src/"\n\n'
                '[build]\noutput_dir = "build"\n')
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "lynx", proj], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    rom = os.path.join(proj, "build", "lynx", "memo.lnx")
    if r.returncode or not os.path.isfile(rom):
        return None, r.stdout + r.stderr
    r2 = subprocess.run([sys.executable, os.path.join(ROOT, "emu", "libretro", "lynx_relabel.py"),
                         proj], capture_output=True, text=True, encoding="utf-8",
                        errors="replace")
    with open(os.path.join(proj, "build", "lynx", "memo.c"), encoding="utf-8") as f:
        streamed = "gbs_code_buf" in f.read()
    return (rom, streamed), r.stdout + r2.stdout + r2.stderr


def _states(rom, frames=1500):
    """vm.music's state every display frame, keyed by song position (rows
    passed, row, tick, frames left): a tick that lands either side of a frame
    edge is the same position, so builds of different speed compare. The
    FIRST sight of a position is kept: a frame can end in the middle of a
    row's update (the cart read is long), and that half-done state carries
    the position it is leaving."""
    lbl = rom[:-4] + ".lbl"
    code = r"""
import sys
sys.path.insert(0, %(lib)r)
import lynx_probe as lp
from libretro import SessionBuilder
from libretro.drivers.path import ExplicitPathDriver
L = lp.load_labels(%(lbl)r)
F = %(fields)r
A = [(L["_vm_music_m_" + k], w) for k, w in F]
d = lp.SYSTEM_DIR
b = (SessionBuilder.defaults(lp.CORE).with_content(%(rom)r)
     .with_paths(ExplicitPathDriver(corepath=lp.CORE, system=d, save=d, assets=d, playlist=d))
     .with_options(lp.HARDWARE_VIEW).with_perf(None))
with b.build() as s:
    ram = lp.Ram(s)
    for f in range(%(n)d):
        s.run()
        print("S " + " ".join("%%d" %% ram.mem[a + i] for a, w in A for i in range(w)))
""" % {"lib": os.path.join(ROOT, "emu", "libretro"), "lbl": lbl, "rom": rom,
       "fields": FIELDS, "n": frames}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out, rows, last = {}, 0, None
    for line in r.stdout.splitlines():
        if not line.startswith("S "):
            continue
        v = line.split()[1:]
        if v[4] != "1":                   # playing
            continue
        row = tuple(v[0:3])
        if last is not None and row != last:
            rows += 1
        last = row
        out.setdefault((rows, row, v[5], v[3]), v)
    return out, r.stderr[-300:]


def _lynx_core():
    for stem in ("mednafen_lynx", "handy"):
        for ext in ("_libretro.dll", "_libretro.so"):
            if os.path.isfile(os.path.join(ROOT, "emu", "libretro", stem + ext)):
                return True
    return False


def test_rom():
    print("ROM (Lynx core): the old and the memoised accessors play the same")
    from mosaik8_build import cc65_available
    if not cc65_available() or not _lynx_core():
        print("  [SKIP] cc65 or a Lynx core not installed")
        return
    new = emit_songs_mos([("a", _song(64))])     # 1,344 B: streamed from the cart
    tmp = tempfile.mkdtemp(prefix="songmemo_")
    try:
        built = {}
        for name, text in (("old", old_form(new)), ("new", new)):
            got, out = _project(tmp, name, text)
            check("%s: builds" % name, got, out[-1200:])
            if got:
                built[name] = got
        if len(built) < 2:
            return
        check("the song streams from the cart (the page cache is in play)",
              built["old"][1] and built["new"][1])
        a, ea = _states(built["old"][0])
        b, eb = _states(built["new"][0])
        both = set(a) & set(b)
        rows = len({k[0] for k in both})
        bad = sorted(k for k in both if a[k] != b[k])
        check("%d song positions over %d rows seen by both" % (len(both), rows),
              len(both) > 1000 and rows > 64, ea + eb)
        check("the driver state is the same at every one of them", not bad,
              "%d differ, first %s: %s / %s" % (len(bad), bad[0], a[bad[0]], b[bad[0]])
              if bad else "")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_text()
    test_codegen()
    test_rom()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
