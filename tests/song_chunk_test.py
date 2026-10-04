#!/usr/bin/env python3
"""The song CELL blob CHUNKS at a ROM bank, so a library may outgrow one.

A banked const array is read in place while its ONE bank is mapped, so no symbol
may cross a bank. `songs.mos` emitted the whole library as a single `CELLS`
array, which capped a game's entire music at 16 KB - and that is what made the
reference-engine sample import keep 3 of its 10 songs and boot to a SILENT title screen
(the title theme lost the budget to one 10.7 KB song).

Each SONG's block now lives whole inside one chunk: `CELLS`, `CELLS2`, ... with
`CELLBLK[song]` naming the chunk and `CELLOFF[song]` the offset WITHIN it. This
is the same shape `mosaik_scenes`' `_TS_CHUNK` uses for the concatenated
per-scene tileset table.

Pinned here:
  * a library that fits ONE chunk emits exactly the old single-symbol module -
    BYTE-IDENTICAL, no CELLBLK and no fork (the additive rule);
  * a bigger library splits, each song whole inside its chunk, with the reader
    forked per chunk;
  * the emitted module COMPILES on a banking console and on the Lynx;
  * a single song too big for one chunk is a clear error naming it.
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

from mosaik_vm.songs import emit_songs_mos, CELL_CHUNK  # noqa: E402
from mosaik_vm.isa import VmError  # noqa: E402
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
STRIDE = 1 + len(CH) * 5          # frames + (note, inst, vol, fxc, fxp) per channel


def _song(nrows):
    return {"channels": list(CH),
            "rows": [(8, [[1, 1, 0]] * len(CH)) for _ in range(nrows)]}


def _arr(src, name):
    key = "const %s: array[" % name
    i = src.find(key)
    if i < 0:
        return None
    s = src.index("= [", i) + 3
    return [int(v) for v in src[s:src.index("]", s)].replace(",", " ").split()]


MAIN = '''
module "main" {
    import "songs"
    import "platform.video"
    function main() {
        var v: u8 = songs.cell(2, 5, 1, 0)
        var f: u8 = songs.frames(2, 5)
        v = v + f
        loop { video.wait_vblank() }
    }
}
'''


def test_single_chunk_is_unchanged():
    print("\n[one chunk = the old single-symbol module]")
    src = emit_songs_mos([("a", _song(8)), ("b", _song(8))])
    check("no CELLBLK table", "CELLBLK" not in src)
    check("exactly one CELLS symbol",
          src.count("const CELLS") == 1, src.count("const CELLS"))
    check("the readers are the plain one-liners",
          "return assets.code_byte(CELLS, i + 1 + ch * 5 + field)" in src
          and "return assets.code_byte(CELLS, i)" in src)
    check("CELLOFF is a plain running offset",
          _arr(src, "CELLOFF") == [0, 8 * STRIDE], _arr(src, "CELLOFF"))


def test_library_past_one_bank_splits():
    print("\n[a library past one bank splits, each song whole]")
    rows = (CELL_CHUNK // STRIDE) // 2 + 20       # just over half a chunk each
    src = emit_songs_mos([("a", _song(rows)), ("b", _song(rows)),
                          ("c", _song(rows))])
    blk = _arr(src, "CELLBLK")
    off = _arr(src, "CELLOFF")
    check("a CELLBLK table is emitted", blk is not None)
    check("each song lands in its own chunk (%s)" % blk, blk == [0, 1, 2])
    check("CELLOFF is the offset WITHIN the chunk, so each restarts at 0",
          off == [0, 0, 0], off)
    check("three CELLS symbols", src.count("const CELLS:") == 1
          and "const CELLS2:" in src and "const CELLS3:" in src)
    for nm in ("CELLS", "CELLS2", "CELLS3"):
        vals = _arr(src, nm)
        check("%s fits a ROM bank (%d B)" % (nm, len(vals)),
              len(vals) <= CELL_CHUNK, len(vals))
    # ONE shared reader, not the fork inlined per accessor: a seam read cannot
    # bank, so every arm is resident image (inlining it twice overflowed bank 0
    # on the reference-engine sample by 257 B).
    check("the chunk fork lives in ONE shared reader",
          "local function cbyte(song: u8, off: u16) -> u8 {" in src
          and "if CELLBLK[song] == 0 {" in src
          and "assets.code_byte(CELLS2, off)" in src
          and "assets.code_byte(CELLS3, off)" in src)
    check("both accessors go through it",
          src.count("return cbyte(song, ") == 2, src.count("return cbyte(song, "))


def test_two_songs_share_a_chunk():
    print("\n[songs pack into a chunk until one would cross the bank]")
    rows = (CELL_CHUNK // STRIDE) // 3            # three fit, the fourth starts a chunk
    src = emit_songs_mos([(n, _song(rows)) for n in "abcd"])
    blk = _arr(src, "CELLBLK")
    check("the first three share chunk 0, the fourth starts chunk 1 (%s)" % blk,
          blk == [0, 0, 0, 1], blk)
    off = _arr(src, "CELLOFF")
    check("offsets accumulate within a chunk and reset across one",
          off[0] == 0 and off[1] == rows * STRIDE and off[3] == 0, off)


def test_it_compiles():
    print("\n[the chunked module compiles]")
    rows = (CELL_CHUNK // STRIDE) // 2 + 20
    src = emit_songs_mos([("a", _song(rows)), ("b", _song(rows)),
                          ("c", _song(rows))])
    for plat in ("gameboy", "lynx"):
        try:
            c = MosaikCompiler().compile_program(
                [("songs.mos", src), ("main.mos", MAIN)], platform=plat)
            check("compiles for %s" % plat, bool(c))
        except Exception as exc:  # noqa: BLE001
            check("compiles for %s" % plat, False, repr(exc))


def test_one_oversized_song_is_refused():
    print("\n[a single song past one chunk is a clear error]")
    rows = CELL_CHUNK // STRIDE + 10
    try:
        emit_songs_mos([("huge_one", _song(rows))])
        check("an oversized song raises", False, "no error")
    except VmError as exc:
        check("an oversized song raises, naming it",
              "huge_one" in str(exc), str(exc)[:120])


def main():
    test_single_chunk_is_unchanged()
    test_library_past_one_bank_splits()
    test_two_songs_share_a_chunk()
    test_it_compiles()
    test_one_oversized_song_is_refused()
    print("\n%d passed, %d failed" % (passed, failed))
    print("All checks passed" if not failed else "SOME CHECKS FAILED")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
