#!/usr/bin/env python3
import os, sys, subprocess, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""platform.save (battery SRAM) -- Stage 0 (GB family).

Verifies: the gbs_save_* helpers are emitted iff platform.save is imported (byte-
identical otherwise); the four calls lower to real cart-RAM accesses on the GB
family; has_save gates the calls so a save.* call on a no-battery console is the
clear "not supported on target" error on BOTH backends; the MBC5+RAM+BATTERY cart
header rides [build] ram_size; and -- when GBDK + PyBoy are installed -- a written
counter PERSISTS across power-off (the .ram round-trip)."""

from mosaik import MosaikCompiler, PLATFORM_CAPS
from mosaik8_build import gbdk_available

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SAVE_SRC = '''
module "main" {
    import "platform.video"
    import "platform.save"
    function main() {
        video.enable_lcd()
        save.enable()
        var n: u8 = save.read_u8(0)
        save.write_u8(1, n)
        save.disable()
        loop { video.wait_vblank() }
    }
}
'''

NOSAVE_SRC = '''
module "main" {
    import "platform.video"
    function main() {
        video.enable_lcd()
        loop { video.wait_vblank() }
    }
}
'''

FAILS = []


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def test_registry():
    print("[registry: has_save / save_bytes]")
    check(PLATFORM_CAPS['gameboy']['has_save']
          and PLATFORM_CAPS['gameboy_color']['has_save']
          and PLATFORM_CAPS['analogue_pocket']['has_save'],
          "has_save True on the GB family")
    check(not any(PLATFORM_CAPS[c]['has_save']
                  for c in ('megaduck', 'sms', 'gamegear', 'nes', 'lynx', 'pce')),
          "has_save False on every other console (Stage 0)")
    check(PLATFORM_CAPS['gameboy']['save_bytes'] == 8192
          and PLATFORM_CAPS['lynx']['save_bytes'] == 0,
          "save_bytes = 8192 on the GB family, 0 where has_save is off")


def test_codegen_gb():
    print("[codegen: GB-family helpers emitted iff imported]")
    gb = MosaikCompiler().compile(SAVE_SRC, platform="gameboy")
    check("void gbs_save_write(uint16_t off, uint8_t v)" in gb
          and "((volatile uint8_t *)0xA000)[off] = v;" in gb
          and "uint8_t gbs_save_read(uint16_t off)" in gb,
          "the four save accessors lower to 0xA000 cart-RAM access")
    check("ENABLE_RAM; SWITCH_RAM(0);" in gb and "DISABLE_RAM;" in gb,
          "enable/disable map the cart-RAM window (MBC5)")
    plain = MosaikCompiler().compile(NOSAVE_SRC, platform="gameboy")
    check("gbs_save" not in plain,
          "a program NOT importing platform.save emits no save helpers (byte-identical)")


def test_honest_off():
    print("[honest-off: save.* is a clear error on a no-battery console]")
    for plat, backend in (("lynx", "cc65"), ("sms", "gbdk"), ("nes", "gbdk")):
        try:
            out = MosaikCompiler().compile(SAVE_SRC, platform=plat)
        except Exception as e:
            out = str(e)
        # compile() surfaces an unsupported-call error as the returned text (or
        # raises); either way it must name the call + the honest-off reason.
        check("not supported on target '%s'" % plat in out and "save." in out,
              "%s: clear unsupported-on-target error (%s backend)" % (plat, backend))


def _gbdk_available():
    """The builder's own probe (`mosaik8_build.gbdk_available`): a directory
    named `gbdk` is not a toolchain, and a Windows one on Linux is not one
    either. See tests/toolchain_probe_test.py."""
    return gbdk_available()


def test_cart_header_and_persist():
    print("[cart header + PyBoy .ram persistence round-trip]")
    if not _gbdk_available():
        print("  skip: GBDK not installed")
        return
    try:
        import pyboy  # noqa: F401
    except Exception:
        print("  skip: PyBoy not installed")
        return
    proj = os.path.join(ROOT, "projects", "save-demo")
    if not os.path.isdir(proj):
        print("  skip: projects/save-demo not present")
        return
    tmp = tempfile.mkdtemp(prefix="savedemo_")
    try:
        dst = os.path.join(tmp, "save-demo")
        shutil.copytree(proj, dst, ignore=shutil.ignore_patterns("build"))
        r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"),
                            "build", "--platform", "gameboy", dst],
                           capture_output=True, text=True)
        rom = os.path.join(dst, "build", "gameboy", "save-demo.gb")
        if not os.path.isfile(rom):
            check(False, "save-demo built (%s)" % (r.stderr[-200:] or r.stdout[-200:]))
            return
        data = open(rom, "rb").read()
        check(data[0x147] == 0x1B and data[0x149] == 0x02,
              "cart header = MBC5+RAM+BATTERY (0x1B) + 8KB RAM (0x02) via ram_size")

        from pyboy import PyBoy
        ramf = rom + ".ram"
        counts = []
        for _ in range(3):
            pb = PyBoy(rom, window="null")
            for _ in range(150):
                pb.tick()
            pb.stop()               # flushes cart RAM to <rom>.ram
            with open(ramf, "rb") as f:
                b = f.read()
            counts.append((b[0], b[1]))
        check(all(h == 0x53 for h, _ in counts)
              and counts[0][1] == 1 and counts[1][1] == 2 and counts[2][1] == 3,
              "counter PERSISTS across power-off (1->2->3, header 0x53): %s"
              % [c for _, c in counts])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("platform.save (battery SRAM) -- Stage 0")
    print("=" * 50)
    test_registry()
    test_codegen_gb()
    test_honest_off()
    test_cart_header_and_persist()
    print()
    if FAILS:
        print("save tests FAILED (%d): %s" % (len(FAILS), FAILS))
        return 1
    print("All platform.save checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
