#!/usr/bin/env python3
"""`[build] cgb_double_speed` runs a VM8 game at the Game Boy Color's
double-speed CPU.

The contract this pins:
  - the knob: absent = off, a bool only;
  - the build states VM_CPU_FAST on the gameboy_color target ONLY, and
    compile_program always supplies the False default (a statement-level
    guard must fold, or it survives as a test on an undeclared symbol);
  - vm.core's boot calls `system.cpu_fast(1)` FIRST, under the guard, so
    every CPU-clocked rate is programmed at the speed the game runs at;
  - the hUGEDriver timer is CPU-clocked: with cpu_fast in the program on the
    Color, `gbs_huge_set_rate` doubles its divisor when the hardware reports
    double speed (KEY1 bit 7, on a CGB only), and a switch after the timer
    was programmed re-sets it. Without cpu_fast, or on the monochrome target,
    set_rate is the single-speed text (byte-identical).

ROM-measured (2026-10-10, the studio's projects/raid-vm8): KEY1 reads 0x80 after boot and TMA
0x80 (= 256 - 8192/64), the 64 Hz tick at double speed; the same project
with the knob absent builds md5-identical to the ROM from before the knob.
A scratch copy with the switch ran stage 1 at 31.8 VM fps against 16.9.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler           # noqa: E402
from mosaik8_build import BuildConfig       # noqa: E402

FAILS = []


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    if not cond:
        FAILS.append(label)


def cfg(value):
    c = BuildConfig.__new__(BuildConfig)
    c.config = ({"build": {}} if value is None
                else {"build": {"cgb_double_speed": value}})
    return c


def _src(rel):
    return open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8").read()


def _function(text, name):
    """The body of one mosaik function (a whole-file match finds copies)."""
    m = re.search(r"function %s\(.*?\n    \}\n" % re.escape(name), text, re.S)
    return m.group(0) if m else ""


def _c_function(text, sig):
    i = text.find(sig)
    if i < 0:
        return ""
    j = text.find("\n}\n", i)
    return text[i:j + 3]


_FAST = '''
module "app" {
    import "native.huge"
    import "platform.system"
    function main() {
        system.cpu_fast(1)
        huge.play(0)
        loop { huge.update() }
    }
    export main
}
'''

_SLOW = _FAST.replace("        system.cpu_fast(1)\n", "")


def compile_src(src, platform):
    return MosaikCompiler().compile_program([("m.mos", src)], platform=platform)


def main():
    # 1. The knob.
    check("absent -> off", cfg(None).get_cgb_double_speed() is False)
    check("true reads true", cfg(True).get_cgb_double_speed() is True)
    for bad in (1, "yes"):
        try:
            cfg(bad).get_cgb_double_speed()
            check("%r refused" % (bad,), False)
        except ValueError:
            check("%r refused" % (bad,), True)
    check("the key is an APPLIED build key (config honesty)",
          "cgb_double_speed" in BuildConfig.APPLIED_KEYS["build"])

    # 2. The build states it on the Color only; the compiler defaults it.
    build = _src("mosaik8_build.py")
    check("the build states VM_CPU_FAST under the knob AND the Color target",
          re.search(r"if self\.config\.get_cgb_double_speed\(\) and platform == "
                    r"'gameboy_color':\s*\n(?:\s*#.*\n)*\s*defines = dict\(defines\)"
                    r"\s*\n\s*defines\['VM_CPU_FAST'\] = True", build) is not None)
    check("compile_program always supplies VM_CPU_FAST = False",
          "setdefault('VM_CPU_FAST', False)" in _src("mosaik/compiler.py"))

    # 3. vm.core's boot switches first, under the guard.
    boot = _function(_src("lib/vm/core.mos"), "boot")
    check("boot OPENS with the guarded switch",
          re.search(r"\{\s*\n(?:\s*--.*\n)*\s*if VM_CPU_FAST \{\s*\n\s*"
                    r"system\.cpu_fast\(1\)\s*\n\s*\}", boot) is not None)

    # 4. The hUGEDriver timer keeps its tempo.
    fast = compile_src(_FAST, "gameboy_color")
    slow = compile_src(_SLOW, "gameboy_color")
    dmg = compile_src(_FAST, "gameboy")
    rate_fast = _c_function(fast, "void gbs_huge_set_rate(uint8_t hz) {")
    rate_slow = _c_function(slow, "void gbs_huge_set_rate(uint8_t hz) {")
    rate_dmg = _c_function(dmg, "void gbs_huge_set_rate(uint8_t hz) {")
    check("both programs compiled", bool(rate_fast) and bool(rate_slow)
          and bool(rate_dmg), fast[:200] if not rate_fast else "")
    check("double speed doubles the divisor, read off the HARDWARE",
          "if (_cpu == CGB_TYPE && (KEY1_REG & 0x80u)) {" in rate_fast
          and "TMA_REG = (uint8_t)(256 - (8192u / hz));" in rate_fast
          and "TMA_REG = (uint8_t)(256 - (4096u / hz));" in rate_fast)
    check("8192/hz must fit a byte: the floor is 32 Hz at double speed",
          "if (hz < 32) hz = 32;" in rate_fast)
    check("without cpu_fast, set_rate is the single-speed text",
          "KEY1_REG" not in rate_slow and "8192u" not in rate_slow)
    check("the monochrome target never reads KEY1",
          "KEY1_REG" not in rate_dmg)
    cpu = _c_function(fast, "void gbs_cpu_fast(uint8_t on) {")
    check("a switch AFTER the timer started re-sets its divisor",
          "if (TAC_REG & TACF_START) gbs_huge_set_rate(gbs_huge_hz);" in cpu)
    check("the switch is defined before the hUGE glue it calls, so it "
          "declares it", fast.find("void gbs_huge_set_rate(uint8_t hz);")
          < fast.find("void gbs_cpu_fast(uint8_t on) {"))

    if FAILS:
        print("\n%d FAILED" % len(FAILS))
        return 1
    print("\nAll cgb_double_speed checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
