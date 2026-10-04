#!/usr/bin/env python3
"""`system.delay(ms)` on the Lynx converts against the rate the prelude PROGRAMS.

Review E-8, measured 2026-09-06. `gbs_delay` converted milliseconds to ticks
with `CLOCKS_PER_SEC`, and the bundled cc65 `<time.h>` does not give the Lynx a
constant at all - for `__LYNX__` it is a RUNTIME call, `__clocks_per_sec()`.
Two ROM measurements on the Handy/Beetle core settle what that means:

  * `clock()` advances **exactly once per PRESENTED frame** - 120 ticks across
    120 `wait_vblank`s (measured with a throwaway ROM that prints the
    delta on screen).
  * `CLOCKS_PER_SEC` therefore evaluates to **50**, because `delay(2000)` took
    100 display frames and `delay(1000)` took 50, against a control of 120
    frames for 120 `wait_vblank`s in the same ROM
    (measured with a throwaway ROM and probe).

So every Lynx program's delays ran at 50/60 of their length - **20% short,
linearly, everywhere `system.delay` is used** (the dialogue freeze among
them). Converting against the profile's own `frame_hz` instead makes
`delay(2000)` take 120 frames and `delay(1000)` 60, measured the same way.

This pins the two halves that can drift apart:
  * `frame_hz` is the number `video_init` really programs, and
  * only a console with one uses it; the PC Engine, whose header states a 60
    that matches its display, keeps `CLOCKS_PER_SEC` and its C unchanged.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler
from mosaik.codegen.cc65 import Cc65Backend

ok = True


def check(cond, label, detail=""):
    global ok
    ok = ok and bool(cond)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- " + detail) if detail else ""))


PROG = '''
module "main" {
    import "platform.video"
    import "platform.system"
    function main() {
        video.enable_lcd()
        system.delay(1000)
    }
}
'''


def gen(platform):
    out = MosaikCompiler().compile_program([("m.mos", PROG)], platform=platform)
    assert not out.startswith("Compilation error"), out
    return out


def test_profile_agrees_with_what_it_programs():
    print("\n[the profile's frame_hz IS the rate video_init programs]")
    prof = Cc65Backend.CC65_PROFILES["lynx"]
    hz = prof.get("frame_hz")
    check(hz == 60, "the Lynx profile names its frame rate", "frame_hz=%r" % hz)
    init = " ".join(prof["video_init"])
    m = re.search(r"tgi_setframerate\((\d+)\)", init)
    check(m is not None, "video_init programs a frame rate")
    check(m and int(m.group(1)) == hz,
          "... and it is the same number frame_hz carries",
          "programs %s, frame_hz %s" % (m.group(1) if m else "?", hz))
    # A console whose <time.h> constant already matches its display must not
    # grow one, or its generated C changes for nothing.
    check("frame_hz" not in Cc65Backend.CC65_PROFILES["pce"],
          "the PC Engine has none (its CLOCKS_PER_SEC is right)")


def test_lynx_converts_against_it():
    print("\n[the Lynx delay does not ask CLOCKS_PER_SEC]")
    c = gen("lynx")
    body = c[c.index("void gbs_delay(uint16_t ms) {"):]
    body = body[:body.index("\n}")]
    check("* 60 + 999) / 1000" in body,
          "ticks are milliseconds against the programmed 60 Hz")
    check("CLOCKS_PER_SEC" not in body,
          "CLOCKS_PER_SEC is not in the conversion")
    check("if (ms > 0 && ticks == 0) ticks = 1;" in body,
          "the 1-tick floor survives (delay(1..16) must still yield)")
    check("__clocks_per_sec" in c or "E-8" in c,
          "the C says why, at the site")


def test_pce_is_unchanged():
    print("\n[the PC Engine keeps the macro]")
    c = gen("pce")
    body = c[c.index("void gbs_delay(uint16_t ms) {"):]
    body = body[:body.index("\n}")]
    check("(clock_t)ms * CLOCKS_PER_SEC + 999) / 1000" in body,
          "ticks are milliseconds against CLOCKS_PER_SEC, as before")
    check("* 60 + 999)" not in body, "no literal rate leaked into it")


def main():
    print("Lynx system.delay: the tick base")
    print("=" * 50)
    test_profile_agrees_with_what_it_programs()
    test_lynx_converts_against_it()
    test_pce_is_unchanged()
    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
