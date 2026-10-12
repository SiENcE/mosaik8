#!/usr/bin/env python3
"""vm.music on the Lynx: a leaner driver, and its cold half as an overlay.

Contract pinned here (2026-10-12, measured on the turned Lynx shooter, the
studio's projects/raid-lynx, which needed the RAM to keep its music):
  * A note's eight Mikey registers go through ONE helper, `ch_note(base,
    vol, feedback, backup, control)`, over `CHREG` = 0, 1, 2, 3, 7, 4, 6, 5:
    the order the driver always wrote them (volume, feedback, output, shift,
    other, backup, count, control). A tone is (1, 0x1C), a drum hit on
    channel D (0xAE, 0x1A). It was sixteen call sites (~20 B each in cc65);
    with a cell's five fields read in one loop it saved ~150 B of MAIN.
    The register writes were proven unchanged by a music-only fixture that
    checksums every `hw.write` (three songs, pause / resume, beeps that
    borrow a channel, stop): 159 of 159 checkpoints identical, and a
    swapped register order leaves 0 - the checks below pin the shape.
  * The Lynx branch marks its per-frame functions `hot`, so a Lynx project
    that lists "vm.music" in `[build] code_banks` gets the cold half (play,
    stop, pause, resume, set_*) as a cart overlay and keeps update() and
    everything it reaches resident (586 B of MAIN on the shooter).

    python tests/music_lynx_driver_test.py
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
except Exception:
    pass

import mosaik8_build  # noqa: E402

FAILS = []


def check(label, cond):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label)


MAIN = '''module "main" {
    import "platform.video"
    import "vm.music"
    import "songs"
    import "instruments"

    function main() {
        music.set_song(songs.channels, songs.chkind, songs.cell, songs.frames, songs.rows)
        music.set_instruments(instruments.kind, instruments.p0, instruments.p1, instruments.vol)
        music.set_voicing(songs.flags)
        music.set_waves(instruments.wavebyte)
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
# a lead and a drum, eight rows, by hand (the accessor shapes vm.music takes)
SONGS = '''module "songs" {
    function channels(s: u8) -> u8 {
        return 2
    }
    function chkind(s: u8, c: u8) -> u8 {
        if c == 1 {
            return 2
        }
        return 0
    }
    function cell(s: u8, r: u16, c: u8, f: u8) -> u8 {
        if f == 0 {
            return 30 + (r & 7)
        }
        if f == 1 {
            return 1
        }
        return 0
    }
    function frames(s: u8, r: u16) -> u8 {
        return 6
    }
    function rows(s: u8) -> u16 {
        return 8
    }
    function flags(s: u8) -> u8 {
        return 0
    }
    export channels, chkind, cell, frames, rows, flags
}
'''
INSTRUMENTS = '''module "instruments" {
    function kind(i: u8) -> u8 {
        return 0
    }
    function p0(i: u8) -> u8 {
        return 0
    }
    function p1(i: u8) -> u8 {
        return 3
    }
    function vol(i: u8) -> u8 {
        return 12
    }
    function wavebyte(i: u8, b: u8) -> u8 {
        return 0
    }
    export kind, p0, p1, vol, wavebyte
}
'''


def build(tmp, banks):
    proj = os.path.join(tmp, "mld" + ("_banked" if banks else ""))
    os.makedirs(os.path.join(proj, "src"))
    for name, text in (("main.mos", MAIN), ("songs.mos", SONGS), ("instruments.mos", INSTRUMENTS)):
        with open(os.path.join(proj, "src", name), "w", encoding="utf-8") as f:
            f.write(text)
    toml = ('[project]\nname = "mld"\nversion = "0.1.0"\ntarget_platforms = ["lynx"]\n'
            '[source]\nfolder = "src/"\n[build]\noutput_dir = "build"\n')
    if banks:
        toml += 'code_banks = ["vm.music"]\n'
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write(toml)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "lynx", proj], capture_output=True, text=True, cwd=ROOT)
    c = os.path.join(proj, "build", "lynx", "mld.c")
    if not os.path.exists(c):
        print(r.stdout[-1500:] + r.stderr[-1500:])
        return None, r.returncode
    with open(c, encoding="latin-1") as f:
        return f.read(), r.returncode


def body(c, head):
    """The definition starting with `head` up to its closing brace."""
    i = 0
    while True:
        i = c.index(head, i)
        brace, semi = c.find("{", i), c.find(";", i)
        if brace != -1 and (semi == -1 or brace < semi):
            return c[i:c.index("\n}\n", brace)]
        i += len(head)


def main():
    if not mosaik8_build.cc65_available():
        print("  [SKIP] cc65 is not installed")
        return 0
    tmp = tempfile.mkdtemp(prefix="mld_")
    try:
        c, rc = build(tmp, banks=False)
        check("the Lynx music fixture builds", c is not None and rc == 0)
        if c:
            check("CHREG is the order the driver always wrote a note",
                  "const uint8_t vm_music_CHREG[8] = {0, 1, 2, 3, 7, 4, 6, 5};" in c)
            note = body(c, "void vm_music_ch_note(")
            check("ch_note writes CHREG's eight registers in one loop",
                  "gbs_hw_write((base + vm_music_CHREG[k]), vm_music_m_chv[k]);" in note
                  and note.count("gbs_hw_write") == 1)
            check("a tone: volume, feedback 1, its backup, control 0x1C",
                  "vm_music_ch_note(base, (v << 3), 1, vm_music_BKUP[idx], 28);"
                  in body(c, "void vm_music_set_tone("))
            check("a drum hit on channel D: feedback 0xAE, control 0x1A",
                  "vm_music_ch_note(64824U, (v << 3), 174, vm_music_BKUP[idx], 26);"
                  in body(c, "void vm_music_set_ldrum("))
            upd = body(c, "void vm_music_update(")
            check("update reads a cell's fields through ONE accessor call",
                  len(re.findall(r"vm_music_g_cell\(", upd)) == 1)
        b, rc = build(tmp, banks=True)
        check("banked: builds", b is not None and rc == 0)
        if b:
            for cold in ("play", "stop", "pause", "resume", "set_song", "set_instruments"):
                check("banked: %s is in the overlay" % cold, "vm_music_%s__bk(" % cold in b)
            for hot in ("update", "set_tone", "set_ldrum", "slot_frame", "ch_note", "arp_note"):
                check("banked: %s stays resident (hot)" % hot,
                      "vm_music_%s__bk(" % hot not in b and "vm_music_%s(" % hot in b)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILS:
        print("FAILED (%d): %s" % (len(FAILS), "; ".join(FAILS)))
        return 1
    print("music_lynx_driver_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
