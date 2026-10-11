#!/usr/bin/env python3
"""`bkg.set_data_vbl`: tiles uploaded in the SAME v-blank as the scroll
(2026-10-11).

`bkg.move` writes a shadow that `video.wait_vblank` commits at the start of
v-blank (the GB register model and SMS/GG). A layer drawn by RE-UPLOADING
tiles as the ground moves (a parallax far layer: one pattern rotated a pixel)
was written the moment the game loop got there, so its new tiles were on
screen a display frame BEFORE their scroll: measured on the studio's SMS /
Game Gear shooter, the far layer jumped a pixel and back every eight frames
(6.8 and 7.4 one-frame glitches per 100 play frames; 0 and 0 with the verb).

The verb takes the console's own tile format (as set_data_native), COPIES up
to 8 tiles into a RAM buffer (so the data may live in any bank) and
gbs_wait_vblank uploads them right after the scroll commit. On the cc65
consoles there is no v-blank scroll commit: the plain upload, announced.

Checks: the C on every GBDK console (definition, buffer, the commit placed
after the scroll commit inside gbs_wait_vblank); absent when unused; the cc65
lowering and its note; and - with GBDK and PyBoy present - a Game Boy ROM
whose queued tile reaches video memory (and does not without the commit,
which is what the mutation of this file's ROM check was run against).
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
from mosaik.platforms import degraded_uses  # noqa: E402
import mosaik8_build  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


#: a tile whose 16 bytes are easy to find in video memory
PATTERN = [0x5A, 0xA5, 0x3C, 0xC3, 0x0F, 0xF0, 0x99, 0x66,
           0x81, 0x7E, 0x24, 0xDB, 0x18, 0xE7, 0x42, 0xBD]

SRC = '''
module "t" {
    import "graphics.bkg"
    import "platform.video"
    const T: array[u8, 16] = [%s]
    function main() {
        var y: u8 = 0
        bkg.set_data_vbl(200, 1, T)
        while true {
            bkg.move(0, y)
            video.wait_vblank()
            y = y + 1
        }
    }
    export main
}
''' % ", ".join(str(b) for b in PATTERN)

PLAIN = SRC.replace("        bkg.set_data_vbl(200, 1, T)\n", "")


def _c_function(text, sig):
    i = text.find(sig)
    if i < 0:
        return ""
    j = text.find("\n}\n", i)
    return text[i:j + 3]


def compile_src(src, platform):
    return MosaikCompiler().compile_program([("t.mos", src)], platform=platform)


def c_contract():
    print("\n[the C on the GBDK consoles]")
    for plat, size in (("gameboy", 16), ("gameboy_color", 16), ("sms", 32),
                       ("gamegear", 32), ("megaduck", 16), ("nes", 16)):
        c = compile_src(SRC, plat)
        if c.startswith("Compilation error"):
            check("%s compiles" % plat, False, c[:300])
            continue
        check("%s: the queue copies into a %d-byte-a-tile buffer of 8 tiles" % (plat, size),
              "static uint8_t gbs_vbl_buf[GBS_VBL_TILES * %d];" % size in c
              and "#define GBS_VBL_TILES 8" in c
              and "memcpy(gbs_vbl_buf, data, (uint16_t)count * %d);" % size in c)
        q = _c_function(c, "void gbs_bkg_data_vbl(")
        check("%s: more than 8 tiles are cut, and a half-copied queue is never committed" % plat,
              "if (count > GBS_VBL_TILES) count = GBS_VBL_TILES;" in q
              and q.find("gbs_vbl_n = 0;") < q.find("memcpy") < q.find("gbs_vbl_n = count;"))
        commit = _c_function(c, "void gbs_bkg_vbl_commit(void) {")
        native = plat in ("sms", "gamegear")
        check("%s: the commit uploads with %s" % (plat, "set_bkg_native_data" if native else "set_bkg_data"),
              ("set_bkg_native_data(gbs_vbl_first, gbs_vbl_n, gbs_vbl_buf);" if native
               else "set_bkg_data(gbs_vbl_first, gbs_vbl_n, gbs_vbl_buf);") in commit
              and "gbs_vbl_n = 0;" in commit)
        wv = _c_function(c, "void gbs_wait_vblank(void) {")
        scroll = max(wv.find("move_bkg("), wv.find("SCY_REG = gbs_scr_shy;"))
        at = wv.find("gbs_bkg_vbl_commit();")
        if plat == "nes":
            # no scroll shadow on the NES: the commit still lands in v-blank
            check("nes: the commit runs in gbs_wait_vblank, after vsync()",
                  at > wv.find("vsync();") >= 0, wv)
        else:
            check("%s: the commit runs in gbs_wait_vblank right AFTER the scroll commit" % plat,
                  0 <= scroll < at, wv)
    plain = compile_src(PLAIN, "sms")
    check("a program that never calls it carries none of it",
          "gbs_vbl" not in plain and "gbs_bkg_data_vbl" not in plain)


def cc65():
    print("\n[the cc65 consoles: the plain upload, announced]")
    for plat in ("lynx", "pce"):
        c = compile_src(SRC, plat)
        check("%s: the call lowers to the plain upload" % plat,
              "gbs_set_bkg_data(200, 1, " in c and "gbs_bkg_data_vbl" not in c, c[:200])
        notes = [m for (t, m) in degraded_uses(plat) if t == ("call", "bkg", "set_data_vbl")]
        check("%s: and is announced as degraded" % plat, len(notes) == 1, notes)
    check("the SMS is NOT announced (it is real there)",
          not [m for (t, m) in degraded_uses("sms") if t == ("call", "bkg", "set_data_vbl")])


def rom():
    print("\n[a Game Boy ROM: the queued tile reaches video memory]")
    try:
        from pyboy import PyBoy
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    tmp = tempfile.mkdtemp(prefix="bkg_vbl_")
    try:
        path = os.path.join(tmp, "t.mos")
        with open(path, "w", encoding="utf-8") as f:
            f.write(SRC)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                            "--platform", "gameboy", path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        rom_path = os.path.join(tmp, "build", "gameboy", "t.gb")
        if not os.path.exists(rom_path):
            check("the sample builds", False, (r.stdout + r.stderr)[-600:])
            return
        pb = PyBoy(rom_path, window="null", cgb=False)
        pb.tick(300, False)
        vram = bytes(pb.memory[0x8000:0x9800])
        pb.stop(save=False)
        check("the tile's 16 bytes are in video memory", bytes(PATTERN) in vram)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("bkg.set_data_vbl: tiles in the scroll's v-blank")
    print("=" * 60)
    c_contract()
    cc65()
    rom()
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("bkg_vbl_test: all checks passed")
