"""ORDER JUMPS (effect 11, hUGE's Bxx) land on their order position's start.

vm.music plays a FLAT row list, and its order jump used to read `param * 64`
as the target row. That is the order position's start only when every pattern
before it is 64 rows long (every `.uge` import), so a hand-authored song with
32-row patterns jumped twice too far, and the studio preview did the same.

The fix keeps every existing song byte-identical: `songs.order_jump_table`
emits a JUMPROW table only when some jump would land wrong, rewrites every
effect-11 param into an index into it, the glue registers `songs.jumprow`, and
that line states VM_MUSIC_JUMPS, which is the only thing that makes the driver
read the table.

What is pinned here:

  * BYTE-IDENTICAL OFF: a library whose jumps all land right under
    `param * 64` emits the module it always did and the glue wires nothing;
  * the table, the rewritten params and the glue line when one would not;
  * a pattern break past row 255 (lowered to an order jump) uses it too;
  * the build states the define off the WIRING, not a comment, and refuses a
    table nobody registers (a hand-wired shell would jump to wrong rows);
  * the generated C reads the table on every console the driver plays on;
  * ON A ROM (GBDK + PyBoy): a song whose last pattern jumps back to order
    position 5 plays that pattern's high note again 160 rows later.

    python tests/music_order_jump_test.py
"""
import glob
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mosaik_vm                                      # noqa: E402
from mosaik_vm import glue, songs                     # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- %s" % (detail,)) if detail else ""))


ROWS = 32           # pattern length: anything but 64 exposes `param * 64`
FRAMES = 2          # display frames per row on the ROM
LOW, HIGH = 13, 37  # two octaves apart, easy to tell by pitch


def _pattern(note, jump=None):
    """One pattern: `note` on row 0, held for the rest."""
    cells = [[FRAMES, note, 1, 0]] + [[FRAMES, 0, 0, 0] for _ in range(ROWS - 1)]
    fx = [[0, 0] for _ in range(ROWS)]
    if jump is not None:
        fx[-1] = list(jump)
    return cells, fx


def song_toml(rows=ROWS, jump=(11, 5)):
    """Order 0..9: positions 0-4 and 6-8 play LOW, position 5 HIGH, and
    position 9 (LOW) ends with `jump` on its last row."""
    global ROWS
    keep, ROWS = ROWS, rows
    try:
        pats = [_pattern(LOW), _pattern(HIGH), _pattern(LOW, jump)]
    finally:
        ROWS = keep
    order = [0, 0, 0, 0, 0, 1, 0, 0, 0, 2]

    def rows_of(cells):
        return "[" + ", ".join("[%s]" % ", ".join(str(v) for v in r) for r in cells) + "]"
    lines = ['[song.loop]', 'channels = ["pulse"]',
             'order = [%s]' % ", ".join(str(i) for i in order),
             'patterns = [%s]' % ", ".join(rows_of(c) for c, _f in pats),
             'fxpatterns = [%s]' % ", ".join(rows_of(f) for _c, f in pats)]
    return "\n".join(lines) + "\n"


def _defs(text):
    tmp = tempfile.mkdtemp(prefix="ordjump_defs_")
    try:
        os.makedirs(os.path.join(tmp, "scripts"))
        with open(os.path.join(tmp, "scripts", "songs.toml"), "w", encoding="utf-8") as f:
            f.write(text)
        return songs.load_song_defs(os.path.join(tmp, "scripts"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_off():
    print("\n[byte-identical when param * 64 already lands]")
    defs = _defs(song_toml(rows=64))
    check("64-row patterns need no table", songs.order_jump_table(defs) is None)
    text = songs.emit_songs_mos(defs)
    check("...so the module has no JUMPROW / jumprow",
          "JUMPROW" not in text and "jumprow" not in text)
    fx = defs[0][1]["fx"]
    check("...and the jump keeps its ORDER param (5)",
          any(p == [11, 5] for row in fx for p in row))
    g = glue.emit_glue_mos(True, False, has_jumps=False)
    check("the glue wires no set_jumps", "set_jumps" not in g)
    check("an order jump past the order keeps the driver's reading",
          songs.order_jump_target({"order_starts": [0, 32]}, 7) == 7 * 64
          and songs.order_jump_target({}, 3) == 192)


def test_on():
    print("\n[a table when param * 64 would miss]")
    defs = _defs(song_toml())
    d = defs[0][1]
    check("the order starts are recorded (32-row patterns)",
          d["order_starts"] == [i * 32 for i in range(10)], d.get("order_starts"))
    table = songs.order_jump_table(defs)
    check("position 5 starts at row 160, not 5 * 64 = 320",
          table is not None and table[0] == [160], table)
    text = songs.emit_songs_mos(defs)
    check("the module emits JUMPROW and exports jumprow",
          "JUMPROW: array[u16, 1] = [160]" in text
          and "function jumprow(i: u8) -> u16" in text
          and "export channels, chkind, cell, frames, rows, flags, jumprow" in text)
    # The jump cell's param byte is the table INDEX (0), not the position (5).
    blob = [int(v) for v in text.split("CELLS: array[u8,")[1].split("[", 1)[1]
            .split("]", 1)[0].split(",")]
    stride = 1 + 5
    last = 10 * ROWS - 1
    cell = blob[last * stride + 1: last * stride + 6]
    check("the jump cell carries effect 11 with param = table index 0",
          cell[3:] == [11, 0], cell)
    g = glue.emit_glue_mos(True, False, has_jumps=True)
    check("the glue registers songs.jumprow next to set_song",
          "music.set_jumps(songs.jumprow)" in g)

    print("\n[a pattern break past row 255 uses the table too]")
    # Position 8's last row breaks to row 0 of the next pattern: flat 288,
    # too far for the u8 FX 10, so it lowers to an order jump to position 9.
    text_toml = song_toml(jump=None)
    pats = text_toml.split("fxpatterns = ")[0]
    order = [0, 0, 0, 0, 0, 1, 0, 0, 2, 0]
    fxp = [[[0, 0]] * ROWS, [[0, 0]] * ROWS, [[0, 0]] * (ROWS - 1) + [[10, 0]]]

    def rows_of(cells):
        return "[" + ", ".join("[%s]" % ", ".join(str(v) for v in r) for r in cells) + "]"
    text_toml = (pats.replace("order = [0, 0, 0, 0, 0, 1, 0, 0, 0, 2]",
                              "order = [%s]" % ", ".join(str(i) for i in order))
                 + "fxpatterns = [%s]\n" % ", ".join(rows_of(f) for f in fxp))
    brk = _defs(text_toml)
    t = songs.order_jump_table(brk)
    check("the break lands on row 288 through the table", t is not None and t[0] == [288], t)
    check("glue.generate_glue decides the wiring from the library",
          _generated_glue_wires(song_toml()) and not _generated_glue_wires(song_toml(rows=64)))


def _generated_glue_wires(text):
    tmp = tempfile.mkdtemp(prefix="ordjump_glue_")
    try:
        os.makedirs(os.path.join(tmp, "scripts"))
        with open(os.path.join(tmp, "scripts", "songs.toml"), "w", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.join(tmp, "studio.toml"), "w", encoding="utf-8") as f:
            f.write('[audio]\ngb = "vm"\n')
        out = glue.generate_glue(tmp)
        return "music.set_jumps(songs.jumprow)" in open(out, encoding="utf-8").read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_build_define():
    print("\n[the build states VM_MUSIC_JUMPS off the wiring]")
    from mosaik8_build import _wants_music_jumps
    wired = 'import "vm.music"\nmusic.set_jumps(songs.jumprow)\n'
    table = "function jumprow(i: u8) -> u16 {\n"
    check("wired -> stated", _wants_music_jumps([("g.mos", wired), ("s.mos", table)]))
    check("a comment is not wiring",
          not _wants_music_jumps([("m.mos", "-- music.set_jumps(songs.jumprow)")]))
    check("no table and no wiring -> off",
          not _wants_music_jumps([("g.mos", 'import "vm.music"\n')]))
    try:
        _wants_music_jumps([("main.mos", 'import "vm.music"\n'), ("songs.mos", table)])
        refused = None
    except ValueError as exc:
        refused = str(exc)
    check("a table nobody registers is REFUSED with the fix named",
          refused is not None and "music.set_jumps(songs.jumprow)" in refused, refused)


def _fixture(tmp, name, text):
    root = os.path.join(tmp, name)
    shutil.copytree(os.path.join(ROOT, "projects", "vm-musicroutine"), root,
                    ignore=shutil.ignore_patterns("build", "assets"))
    sd = os.path.join(root, "scripts")
    with open(os.path.join(sd, "main.evt.toml"), "w", encoding="utf-8") as f:
        f.write('[[script]]\nname = "main"\nevents = [\n'
                '  { event = "music_song", song = "loop" },\n  { event = "stop" },\n]\n')
    with open(os.path.join(sd, "songs.toml"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(sd, "instruments.toml"), "w", encoding="utf-8") as f:
        f.write('[instrument.lead]\nkind = "pulse"\nduty = 2\nvol = 15\n')
    with open(os.path.join(root, "studio.toml"), "w", encoding="utf-8") as f:
        f.write('[audio]\ngb = "vm"\nsmsgg = "vm"\nlynx = "vm"\npce = "vm"\n')
    prog = mosaik_vm.compile_path(sd)
    with open(os.path.join(root, "src", "scripts.mos"), "w", encoding="utf-8") as f:
        f.write(prog.to_scripts_mos())
    mosaik_vm.generate_songs(root)
    mosaik_vm.generate_instruments(root)
    mosaik_vm.generate_glue(root)
    return root


def _build(root, platform):
    return subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"),
                           "build", "--platform", platform, root],
                          capture_output=True, text=True)


def test_c():
    print("\n[the generated C reads the table on every vm.music console]")
    tmp = tempfile.mkdtemp(prefix="ordjump_c_")
    try:
        root = _fixture(tmp, "ordjump", song_toml())
        for plat in ("gameboy", "gameboy_color", "sms", "gamegear", "lynx", "pce"):
            r = _build(root, plat)
            cs = glob.glob(os.path.join(root, "build", plat, "*.c"))
            c = "".join(open(p, encoding="utf-8").read() for p in cs)
            check("%s: C generated and the driver calls the registered table" % plat,
                  bool(cs) and "songs_jumprow" in c and "vm_music_jump_row" in c,
                  ((r.stdout or "") + (r.stderr or ""))[-600:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _cycles_per_frame(samples):
    """Rising crossings of the frame's own MIDPOINT (PyBoy's samples are
    unsigned levels), as music_subpattern_test measures pitch."""
    vals = [int(s[0]) for s in samples]
    if not vals:
        return 0
    mid = (min(vals) + max(vals)) / 2.0
    n, prev = 0, None
    for v in vals:
        if prev is not None and prev <= mid < v:
            n += 1
        prev = v
    return n


def test_rom():
    print("\n[ON A ROM: the jump replays order position 5, 160 rows later]")
    from mosaik8_build import gbdk_available
    if not gbdk_available():
        print("  SKIP: GBDK is not installed")
        return
    try:
        from pyboy import PyBoy
    except ImportError:
        print("  SKIP: PyBoy is not installed")
        return
    tmp = tempfile.mkdtemp(prefix="ordjump_rom_")
    try:
        root = _fixture(tmp, "ordjump", song_toml())
        r = _build(root, "gameboy")
        roms = glob.glob(os.path.join(root, "build", "gameboy", "*.gb"))
        check("the fixture links", r.returncode == 0 and bool(roms),
              ((r.stdout or "") + (r.stderr or ""))[-800:])
        if not roms:
            return
        pb = PyBoy(roms[0], window="null", sound_emulated=True)
        pitch = []
        try:
            for _ in range(60 + 2 * 10 * ROWS * FRAMES):
                pb.tick(1, False)
                pitch.append(_cycles_per_frame(pb.sound.ndarray))
        finally:
            pb.stop(save=False)
        top = max(pitch) if pitch else 0
        high = [i for i, c in enumerate(pitch) if top and c >= 0.75 * top]
        starts = [i for i in high if i - 1 not in high]
        print("    HIGH runs start at frames %s" % starts[:4])
        gap = ROWS * 5 * FRAMES       # position 5 -> 9, then back to 5
        check("the HIGH pattern plays, then again one jump later (%d frames)" % gap,
              len(starts) >= 2 and abs((starts[1] - starts[0]) - gap) <= 4,
              starts[:4])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_off()
    test_on()
    test_build_define()
    test_c()
    test_rom()
    print("\n%d passed, %d failed" % (passed, failed))
    if failed:
        print("Some tests failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
