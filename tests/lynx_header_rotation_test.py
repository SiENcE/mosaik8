#!/usr/bin/env python3
"""An upright Lynx build marks its .lnx header, so an emulator set to "Auto"
shows it upright.

Byte 58 of the 64-byte LNX header is the rotation an emulator turns the
window by. cc65's own header (lynx/exehdr.s) always writes 0, so a
`[build] lynx_orientation = "portrait_*"` game came up sideways in GearLynx
while a commercial upright cart (Raiden, found in its game database) did not
(2026-10-15). The build now writes 2 for "portrait_left" (d-pad below) and 1
for "portrait_right"; landscape keeps cc65's 0 and its ROM stays
byte-identical. Every reader agrees on what the value means for the picture:
Handy's CART_ROTATE_RIGHT and GearLynx's GLYNX_ROTATION_LEFT are both 2 and
both show the physical left column at the bottom, which is what
`lynx_images.rotate` bakes for portrait_left.

The engine's own harness (run_lynx.py, lynx_probe.py) pins Handy to the
physical view (HARDWARE_VIEW), so its probes still read the 160x102 screen
and press the physical d-pad.

Pinned here:
  * the mapping: portrait_left = 2, portrait_right = 1, landscape absent;
  * the header write on a stand-in .lnx, and a headerless image untouched;
  * the ROM (cc65): each orientation's built header byte; landscape's whole
    header is cc65's (byte 58 = 0);
  * the harness: run_lynx.py and lynx_probe.py open their session with
    HARDWARE_VIEW, and Handy, given it, still hands back 160x102 for an
    upright ROM (and 102x160 without it: the check can fail).
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

import mosaik8_build  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print("  [PASS] %s" % name)
    else:
        failed += 1
        print("  [FAIL] %s %s" % (name, ("-- " + detail) if detail else ""))


class _Cfg:
    def __init__(self, orient):
        self.orient = orient

    def get_lynx_orientation(self):
        return self.orient


def _write(builder_cls, orient, data):
    fd, path = tempfile.mkstemp(suffix=".lnx")
    os.close(fd)
    with open(path, "wb") as f:
        f.write(data)
    b = builder_cls.__new__(builder_cls)
    b.config = _Cfg(orient)
    b._lynx_header_rotation(path)
    with open(path, "rb") as f:
        out = f.read()
    os.remove(path)
    return out


def _builder_cls():
    for v in vars(mosaik8_build).values():
        if isinstance(v, type) and hasattr(v, "_lynx_header_rotation"):
            return v
    return None


def test_header_write():
    print("the header write")
    cls = _builder_cls()
    check("the build owns _lynx_header_rotation", cls is not None)
    if cls is None:
        return
    check("portrait_left = 2, portrait_right = 1, landscape not listed",
          cls.LNX_ROTATION == {"portrait_left": 2, "portrait_right": 1}
          and cls.LNX_ROTATION_BYTE == 58)
    hdr = b"LYNX" + bytes(range(4, 64)) + b"\xAA" * 100
    for orient, want in (("portrait_left", 2), ("portrait_right", 1)):
        out = _write(cls, orient, hdr)
        check("%s: byte 58 = %d, nothing else moves" % (orient, want),
              out[58] == want and out[:58] == hdr[:58] and out[59:] == hdr[59:])
    check("landscape: the file is untouched", _write(cls, "landscape", hdr) == hdr)
    bare = b"\x00" * 128
    check("a headerless image is untouched", _write(cls, "portrait_left", bare) == bare)


PROGRAM = """module "main" {
    import "graphics.sprite"
    import "platform.video"
    function main() {
        video.enable_lcd()
        video.show_sprites()
        sprite.set_data(0, s_tile_count, s_tiles)
        sprite.set_tile(0, 0)
        sprite.move(0, 20, 20)
        loop {
            video.wait_vblank()
        }
    }
    export main
}
"""


def _project(tmp, orient):
    from PIL import Image
    proj = os.path.join(tmp, orient)
    os.makedirs(os.path.join(proj, "src"))
    os.makedirs(os.path.join(proj, "assets"))
    im = Image.new("P", (8, 8), 1)
    im.putpalette([255, 0, 255, 255, 255, 255, 170, 170, 170, 85, 85, 85] + [0] * 756)
    im.save(os.path.join(proj, "assets", "s.png"))
    with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
        f.write(PROGRAM)
    build = "" if orient == "landscape" else 'lynx_orientation = "%s"\n' % orient
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "rot"\nversion = "0.1.0"\n'
                'target_platforms = ["lynx"]\n\n[source]\nfolder = "src/"\n\n'
                '[assets]\nsprites = ["assets/s.png"]\n\n'
                '[build]\noutput_dir = "build"\nlynx_sprites = "whole"\n' + build)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "lynx", proj], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    rom = os.path.join(proj, "build", "lynx", "rot.lnx")
    return (rom if r.returncode == 0 and os.path.isfile(rom) else None), r.stdout + r.stderr


def _handy():
    p = os.path.join(ROOT, "emu", "libretro",
                     "handy_libretro" + (".dll" if os.name == "nt" else ".so"))
    return p if os.path.isfile(p) else None


def _frame_size(rom, hardware_view):
    """Width x height Handy hands back after a few frames, in a child process
    (a libretro core is loaded once per process)."""
    code = r"""
import sys
sys.path.insert(0, %(lib)r)
import run_lynx
from libretro import SessionBuilder
from libretro.drivers.path import ExplicitPathDriver
d = %(lib)r
b = (SessionBuilder.defaults(%(core)r).with_content(%(rom)r)
     .with_paths(ExplicitPathDriver(corepath=%(core)r, system=d, save=d, assets=d, playlist=d))
     .with_perf(None))
if %(hw)r:
    b = b.with_options(run_lynx.HARDWARE_VIEW)
with b.build() as s:
    for _ in range(30):
        s.run()
    shot = s.video.screenshot()
    print("SIZE %%dx%%d" %% (shot.width, shot.height))
""" % {"lib": os.path.join(ROOT, "emu", "libretro"), "core": _handy(), "rom": rom,
       "hw": hardware_view}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    m = re.search(r"SIZE (\d+)x(\d+)", r.stdout)
    return (int(m.group(1)), int(m.group(2))) if m else None


def test_harness_source():
    print("the harness opens its sessions with HARDWARE_VIEW")
    for name in ("run_lynx.py", "lynx_probe.py"):
        with open(os.path.join(ROOT, "emu", "libretro", name), encoding="utf-8") as f:
            src = f.read()
        check("%s: HARDWARE_VIEW pins handy_rot to None" % name,
              'HARDWARE_VIEW = {"handy_rot": "None"}' in src)
        check("%s: its session builder uses it" % name,
              ".with_options(HARDWARE_VIEW)" in src)


def test_rom():
    print("ROM (cc65): the built header")
    if not mosaik8_build.cc65_available():
        print("  [SKIP] cc65 not installed")
        return
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        print("  [SKIP] Pillow not installed")
        return
    tmp = tempfile.mkdtemp(prefix="lynxrot_")
    try:
        roms = {}
        for orient, want in (("landscape", 0), ("portrait_left", 2), ("portrait_right", 1)):
            rom, out = _project(tmp, orient)
            check("%s: builds" % orient, rom is not None, out[-1200:])
            if rom is None:
                continue
            roms[orient] = rom
            with open(rom, "rb") as f:
                hdr = f.read(64)
            check("%s: header byte 58 = %d" % (orient, want),
                  hdr[:4] == b"LYNX" and hdr[58] == want, repr(hdr[56:60]))
        if _handy() and "portrait_left" in roms:
            hw = _frame_size(roms["portrait_left"], True)
            auto = _frame_size(roms["portrait_left"], False)
            check("Handy with HARDWARE_VIEW: the physical 160x102", hw == (160, 102), str(hw))
            check("Handy on Auto turns the upright ROM to 102x160", auto == (102, 160), str(auto))
        else:
            print("  [SKIP] the Handy core is not installed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_header_write()
    test_harness_source()
    test_rom()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
