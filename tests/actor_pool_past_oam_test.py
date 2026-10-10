#!/usr/bin/env python3
"""An actor pool BIGGER THAN THE SPRITE TABLE must not write past it.

`[build] actor_pool` sizes vm.actor's slots; the GB has 40 sprite objects.
Before the first room load nothing has called `actor.set_base`, so the layout
is the static `i * stride`, and boot's `actor.reset()` parks EVERY slot there:
with a 42-actor pool it parked objects 40 and 41 through `sprite.move`, which
wrote past the 160-byte shadow OAM into GBDK's VBL handler list (the next
bytes in WRAM). The handler pointer became 0xD0D8, `vbl_done` was never set
again and the main loop hung in `wait_vbl_done` on the first frame the music
started (measured on the studio's raid-vm8, 2026-10-10). `VM_POOL_PAST_OAM`
(stated by the build only for a pool above 40) bounds `park()` by the table.

The check builds projects/vm-tallshmup (a scrolling stage) with
`actor_pool = 48` and asserts on the ROM that the main loop runs: the scroll
moves, and the WRAM right after shadow OAM was never written with a parked
object (y 216, x 208). Byte-identical for every pool that fits is pinned by
the define being stated only above 40 (checked on the build's defines).
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(cond, msg):
    print("  [%s] %s" % ("ok" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def _defines_for(pool):
    """The VM_POOL_PAST_OAM decision the build makes for `pool`."""
    import re
    src = open(os.path.join(ROOT, "mosaik8_build.py"), encoding="utf-8").read()
    m = re.search(r"pool = self\.config\.config\.get\('build', \{\}\)\.get\('actor_pool'\)\s*\n"
                  r"\s*if pool is not None and int\(pool\) > (\d+):", src)
    return m and pool > int(m.group(1))


#: The ROM check, run in a child process (see main).
_RUN = """
import sys
from pyboy import PyBoy
pb = PyBoy(sys.argv[1], window="null", sound_emulated=False)
scys, stray = set(), 0
for f in range(900):
    pb.tick(1, False)
    if f >= 300:
        scys.add(pb.memory[0xFF42])
    # objects 40.. would sit at 0xC0A0..: a PARKED object is y 216, x 208
    for a in range(0xC0A0, 0xC0C0, 4):
        if pb.memory[a] == 216 and pb.memory[a + 1] == 208:
            stray += 1
pb.stop(save=False)
print("SCYS", len(scys), "STRAY", stray)
"""


def main():
    print("actor_pool past the sprite table")
    check(_defines_for(41) and not _defines_for(40),
          "the build states VM_POOL_PAST_OAM only for a pool above 40")
    import mosaik8_build
    if not mosaik8_build.gbdk_available():
        print("  [skip] GBDK not installed")
        return 0
    try:
        import pyboy  # noqa: F401  (the child needs it)
    except Exception:             # noqa: BLE001
        print("  [skip] PyBoy not installed")
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        dst = os.path.join(tmp, "vm-tallshmup")
        shutil.copytree(os.path.join(ROOT, "projects", "vm-tallshmup"), dst,
                        ignore=shutil.ignore_patterns("build"))
        mt = os.path.join(dst, "mosaik.toml")
        text = open(mt, encoding="utf-8").read()
        assert "actor_pool = 32" in text
        open(mt, "w", encoding="utf-8").write(text.replace("actor_pool = 32",
                                                           "actor_pool = 48"))
        subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "gameboy", dst], check=True, cwd=ROOT,
                       stdout=subprocess.DEVNULL)
        rom = os.path.join(dst, "build", "gameboy", "vm-tallshmup.gb")
        # In a CHILD with a timeout: the corrupted ROM can wedge the emulator
        # inside one tick (measured with the guard removed), and a hang must
        # read as a failure, not stall the suite.
        try:
            r = subprocess.run([sys.executable, "-c", _RUN, rom], capture_output=True,
                               text=True, timeout=240)
            out = r.stdout.split()
            scys, stray = int(out[1]), int(out[3])
        except subprocess.TimeoutExpired:
            scys, stray = 0, -1
    check(scys > 50,
          "a 48-actor pool boots and the stage scrolls (%d scroll positions in "
          "600 frames; 0 = the emulator hung)" % scys)
    check(stray == 0,
          "nothing parked a sprite past the 40-object table (%d writes seen)" % stray)
    print("FAILED: %d" % len(FAILS) if FAILS else "all checks passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
