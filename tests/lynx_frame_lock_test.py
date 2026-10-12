#!/usr/bin/env python3
"""`lynx.frame_lock(n)` (native.lynx): every present flips exactly n display
frames after the one before.

The Suzy present draws at once and HOLDS the flip until the clock tick the
lock has scheduled (a schedule, never a clock read taken at a flip: the tick
and the swap fall together and such a read was one off either way, flips 2
and 4 frames apart); a late frame flips at once and the beat restarts from
it. The wait slides the turned background's strips (one at a time, the
present's own pass keeping only the slides a gap forces). Built for the
upright Lynx shooter (2026-10-13): a 30 fps cap gave 2 to 5 display frames a
game frame; under frame_lock(3) 94 % of its stage 1 flips land exactly 3
apart (the rest are frames whose work outran the beat).

Pinned here:
  * codegen: on the Lynx the state, the setter and the hold only when the
    program calls it, the strip slides in the hold and GBS_BKG_SLIDES 0 when
    there are turned strips; a no-op (void) on the PC Engine and the GB
    family, named as degraded there; nothing of it without the call;
  * the ROM (cc65 + a Lynx core): a sprite moved once a pass, under
    frame_lock(3) and (2), changes the picture exactly every 3 (2) display
    frames, and without the lock every frame.
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
from mosaik.platforms import degraded_uses  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


PROGRAM = '''
module "main" {
    import "graphics.sprite"
    import "graphics.bkg"
    import "platform.video"
    import "native.lynx"
    const MAP: array[u8, 1] = [0]
    function main() {
        sprite.set_data(0, s_tile_count, s_tiles)
        video.enable_lcd()
        video.show_sprites()
        sprite.set_tile(0, 0)
        %(lock)s
        var x: u8 = 0
        loop {
            x = x + 1
            if x > 80 {
                x = 0
            }
            sprite.move(0, x, 40)
            video.wait_vblank()
        }
    }
    export main
}
'''


def _c(platform, lock="lynx.frame_lock(3)", **kw):
    src = (PROGRAM % {"lock": lock}).replace("s_tile_count, s_tiles", "1, MAP")
    return MosaikCompiler().compile_program([("main.mos", src)], platform=platform, **kw)


def test_codegen():
    print("codegen")
    for kw in ({}, {"lynx_sprites": "whole"}, {"lynx_orientation": "portrait_left"}):
        c = _c("lynx", **kw)
        tag = list(kw.values())[0] if kw else "8x8"
        check("lynx %s: compiles" % tag, not c.startswith("Compilation error"), c[:300])
        check("lynx %s: the setter schedules the first beat n ticks on" % tag,
              "void gbs_lynx_frame_lock(uint8_t n) { gbs_lock_n = n; "
              "gbs_lock_t = (uint8_t)((uint8_t)clock() + n); }" in c)
        hold = c[c.index("if (gbs_lock_n) {"):c.index("tgi_updatedisplay();")] \
            if "if (gbs_lock_n) {" in c else ""
        check("lynx %s: the present holds the flip until the scheduled tick, then "
              "schedules the next from the flip's own tick" % tag,
              "while ((signed char)((uint8_t)clock() - gbs_lock_t) < 0) {" in hold
              and "gbs_lock_t = (uint8_t)((uint8_t)clock() + gbs_lock_n);" in hold
              and c.index("while (tgi_busy()) { }  /* let Suzy finish before the flip */")
              < c.index("if (gbs_lock_n) {"))
    turned = _c("lynx", lynx_orientation="portrait_left")
    hold = turned[turned.index("if (gbs_lock_n) {"):turned.index("tgi_updatedisplay();")]
    check("turned: the hold slides one lagging strip at a time",
          "if (gbs_bkg_lag) gbs_bkg_catch(1);" in hold)
    check("turned: the present's own pass keeps only the slides a gap forces",
          "#define GBS_BKG_SLIDES 0" in turned)
    check("landscape: no strips to slide in the hold",
          "gbs_bkg_catch(1)" not in _c("lynx", lynx_sprites="whole"))
    plain = _c("lynx", lock="")
    plain_t = _c("lynx", lock="", lynx_orientation="portrait_left")
    check("lynx without the call: none of it, the strips slide 3 a frame",
          "gbs_lock_" not in plain and "#define GBS_BKG_SLIDES 3" in plain_t)
    for plat in ("pce", "gameboy", "sms"):
        c = _c(plat)
        check("%s: a (void) no-op" % plat,
              not c.startswith("Compilation error")
              and "void gbs_lynx_frame_lock(uint8_t n) { (void)n; }" in c, c[:300])
        check("%s: no call, no stub" % plat, "gbs_lynx_frame_lock" not in _c(plat, lock=""))
    named = {t for t, _m in degraded_uses("pce")} | {t for t, _m in degraded_uses("gameboy")}
    check("the build names the no-op as degraded off the Lynx, and not on it",
          ("call", "lynx", "frame_lock") in named
          and ("call", "lynx", "frame_lock") not in {t for t, _m in degraded_uses("lynx")})


def _sheet(path):
    from PIL import Image
    im = Image.new("P", (8, 8), 0)
    im.putpalette([255, 0, 255, 255, 255, 255, 170, 170, 170, 85, 85, 85] + [0] * 756)
    px = im.load()
    for y in range(8):
        for x in range(8):
            px[x, y] = 1 + (x + y) % 3
    im.save(path)


def _project(tmp, name, lock):
    proj = os.path.join(tmp, name)
    os.makedirs(os.path.join(proj, "src"))
    os.makedirs(os.path.join(proj, "assets"))
    _sheet(os.path.join(proj, "assets", "s.png"))
    with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
        f.write(PROGRAM % {"lock": lock})
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "fl"\nversion = "0.1.0"\n'
                'target_platforms = ["lynx"]\n\n[source]\nfolder = "src/"\n\n'
                '[assets]\nsprites = ["assets/s.png"]\n\n[build]\noutput_dir = "build"\n')
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "lynx", proj], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    rom = os.path.join(proj, "build", "lynx", "fl.lnx")
    return (rom if r.returncode == 0 and os.path.isfile(rom) else None), r.stdout + r.stderr


def _runs(rom):
    """Run lengths (display frames) of the picture over frames 100..400, one
    libretro session (run_lynx.py's set-up), in this process."""
    import hashlib
    sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
    import run_lynx
    from libretro import SessionBuilder
    from libretro.drivers.path import ExplicitPathDriver
    d = run_lynx.SYSTEM_DIR
    builder = (SessionBuilder.defaults(run_lynx.CORE).with_content(rom)
               .with_paths(ExplicitPathDriver(corepath=run_lynx.CORE, system=d, save=d,
                                              assets=d, playlist=d))
               .with_perf(None))
    hashes = []
    with builder.build() as session:
        for f in range(400):
            session.run()
            if f >= 100:
                hashes.append(hashlib.md5(run_lynx.frame_image(session).tobytes()).digest())
    runs, n = [], 1
    for a, b in zip(hashes, hashes[1:]):
        if a == b:
            n += 1
        else:
            runs.append(n)
            n = 1
    return runs[1:]          # the first run starts mid-way


def _lynx_core():
    for stem in ("mednafen_lynx", "handy"):
        for ext in ("_libretro.dll", "_libretro.so"):
            if os.path.isfile(os.path.join(ROOT, "emu", "libretro", stem + ext)):
                return True
    return False


def test_rom():
    print("ROM (Lynx core): the picture changes every n display frames")
    from mosaik8_build import cc65_available
    if not cc65_available() or not _lynx_core():
        print("  [SKIP] cc65 or a Lynx core not installed")
        return
    tmp = tempfile.mkdtemp(prefix="lynxlock_")
    try:
        for n, lock in ((3, "lynx.frame_lock(3)"), (2, "lynx.frame_lock(2)"), (1, "")):
            rom, out = _project(tmp, "lock%d" % n, lock)
            check("frame_lock %d: builds" % n, rom, out[-1200:])
            if not rom:
                continue
            r = subprocess.run([sys.executable, "-c",
                                "import sys; sys.path.insert(0, %r); import lynx_frame_lock_test as t; "
                                "print(','.join(map(str, t._runs(%r))))" % (os.path.dirname(__file__), rom)],
                               capture_output=True, text=True, cwd=ROOT)
            runs = [int(v) for v in r.stdout.strip().splitlines()[-1].split(",") if v] \
                if r.stdout.strip() else []
            check("frame_lock %d: %d picture changes, every one %d frame(s) apart"
                  % (n, len(runs), n),
                  len(runs) > 50 and set(runs) == {n}, str(sorted(set(runs))) + r.stderr[-300:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_codegen()
    test_rom()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
