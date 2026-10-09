#!/usr/bin/env python3
"""Pause and Option 1 / 2 on the Atari Lynx.

The Lynx has no START or SELECT, but a PAUSE switch ($FCB1 bit 0) and two
Option buttons ($FCB0 bits 3 / 2, which cc65's joystick driver masks out). A
program that NAMES `INPUT_START` or `INPUT_SELECT` gets them: START = PAUSE,
SELECT = either Option (`gbs_lynx_pad`). Any other program keeps the plain
joystick read and the defines of 0, byte-identical.

Contract pinned here: the codegen both ways, the portrait d-pad turn applied
over the new read, nothing off the Lynx; and on the Handy core (whose
libretro START is PAUSE and L / R are Option 1 / 2) a sprite that the
program moves while each button is held.
"""

import os
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

FAILS = []


def check(label, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + label + (("  -- " + detail) if detail and not cond else ""))
    if not cond:
        FAILS.append(label)


_SRC = '''module "main" {
    import "graphics.sprite"
    import "platform.input"
    import "platform.video"
    const T: array[u8, 16] = [255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255]
    function main() {
        sprite.set_data(0, 1, T)
        sprite.set_tile(0, 0)
        video.enable_lcd()
        loop {
            var x: u8 = 10
            var y: u8 = 10
%s
            sprite.move(0, x, y)
            video.wait_vblank()
        }
    }
    export main
}
'''
_BOTH = ("            if input.held(INPUT_START) { x = 80 }\n"
         "            if input.held(INPUT_SELECT) { y = 60 }\n")
_NONE = "            if input.held(INPUT_A) { x = 80 }\n"


def _c(body, platform="lynx", **kw):
    return MosaikCompiler().compile_program([("m.mos", _SRC % body)], platform=platform, **kw)


def test_codegen():
    plain = _c(_NONE)
    check("a program that names neither compiles", not plain.startswith("Compilation error"))
    check("...keeps the plain read and START / SELECT of 0",
          "gbs_lynx_pad" not in plain and "#define INPUT_START  0\n" in plain
          and "#define INPUT_SELECT 0\n" in plain and "SUZY.switches" not in plain)
    both = _c(_BOTH)
    check("a program that names START / SELECT compiles", not both.startswith("Compilation error"))
    check("...START is the PAUSE switch on the free bit 2",
          "#define INPUT_START  0x04" in both
          and "if (SUZY.switches & BUTTON_PAUSE) v |= INPUT_START;" in both)
    check("...SELECT is either Option button on bit 3",
          "#define INPUT_SELECT 0x08" in both
          and "if (j & (BUTTON_OPTION1 | BUTTON_OPTION2)) v |= INPUT_SELECT;" in both)
    check("...the d-pad read is the one every Lynx program has",
          "joy_read(0) | (j & (JOY_UP_MASK | JOY_DOWN_MASK))" in both
          and "return (uint8_t)(gbs_lynx_pad() & button);" in both)
    for name in ("INPUT_START", "INPUT_SELECT"):
        one = _c("            if input.held(%s) { x = 80 }\n" % name)
        check("naming only %s is enough" % name, "gbs_lynx_pad" in one)
    turned = _c(_BOTH, lynx_orientation="portrait_left")
    check("portrait turns the d-pad over the new read",
          "return (uint8_t)(gbs_turn_pad(gbs_lynx_pad()) & button);" in turned)
    for plat in ("gameboy", "pce"):
        c = _c(_BOTH, platform=plat)
        check("%s: untouched" % plat, "gbs_lynx_pad" not in c and "BUTTON_PAUSE" not in c)


# ------------------------------------------------------------------ the ROM
_PROBE = r'''
import os, sys
sys.path.insert(0, os.path.join(%(root)r, "emu", "libretro"))
import run_lynx as R
from libretro import SessionBuilder
from libretro.drivers.path import ExplicitPathDriver
from libretro.drivers.input import IterableInputDriver
from libretro.api.input import JoypadState
core = %(core)r
state = {"b": None}
def inputs():
    while True:
        yield JoypadState(**({state["b"]: True} if state["b"] else {}))
b = (SessionBuilder.defaults(core).with_content(%(rom)r)
     .with_paths(ExplicitPathDriver(corepath=core, system=R.SYSTEM_DIR, save=R.SYSTEM_DIR,
                                    assets=R.SYSTEM_DIR, playlist=R.SYSTEM_DIR))
     .with_input(IterableInputDriver(inputs)).with_perf(None))
with b.build() as s:
    for _ in range(120):
        s.run()
    for btn in (None, "start", None, "l", None, "r", None, "a"):
        state["b"] = btn
        for _ in range(40):
            s.run()
        im = R.frame_image(s)
        px = im.load()
        pts = [(x, y) for y in range(im.height) for x in range(im.width) if px[x, y] == (255, 255, 255)]
        print("POS %%s %%s" %% (btn or "-", (min(p[0] for p in pts), min(p[1] for p in pts)) if pts else None))
'''


def test_rom():
    print("ROM: the sprite moves while a button is held (Handy)")
    from mosaik8_build import cc65_available
    core = os.path.join(ROOT, "emu", "libretro", "handy_libretro" + (".dll" if os.name == "nt" else ".so"))
    if not cc65_available() or not os.path.isfile(core):
        print("  [SKIP] cc65 or the Handy core not installed")
        return
    try:
        import PIL  # noqa: F401
        import libretro  # noqa: F401
    except ImportError:
        print("  [SKIP] Pillow / libretro.py not installed")
        return
    tmp = tempfile.mkdtemp(prefix="lynxbtn_")
    try:
        src = os.path.join(tmp, "btn.mos")
        with open(src, "w", encoding="utf-8") as f:
            f.write(_SRC % _BOTH)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                            "--platform", "lynx", src], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        rom = os.path.join(tmp, "build", "lynx", "btn.lnx")
        check("the fixture builds", os.path.isfile(rom), (r.stdout + r.stderr)[-800:])
        if not os.path.isfile(rom):
            return
        p = subprocess.run([sys.executable, "-c", _PROBE % {"root": ROOT, "core": core, "rom": rom}],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        pos = [ln.split(" ", 2)[1:] for ln in p.stdout.splitlines() if ln.startswith("POS ")]
        got = {}
        for btn, where in pos:
            got.setdefault(btn, []).append(where)
        check("the probe ran", len(pos) == 8, (p.stdout + p.stderr)[-800:])
        if len(pos) != 8:
            return
        check("nothing held: (10, 10)", set(got["-"]) == {"(10, 10)"}, str(got["-"]))
        check("PAUSE (libretro START) is INPUT_START", got["start"] == ["(80, 10)"], str(got["start"]))
        check("Option 1 (libretro L) is INPUT_SELECT", got["l"] == ["(10, 60)"], str(got["l"]))
        check("Option 2 (libretro R) is INPUT_SELECT", got["r"] == ["(10, 60)"], str(got["r"]))
        check("A is neither", got["a"] == ["(10, 10)"], str(got["a"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_codegen()
    test_rom()
    print()
    if FAILS:
        print("FAILED (%d): %s" % (len(FAILS), "; ".join(FAILS)))
        return 1
    print("lynx_buttons_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
