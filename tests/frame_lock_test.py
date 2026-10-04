#!/usr/bin/env python3
"""`[build] frame_lock` holds a game frame to N DISPLAY frames.

WHY IT EXISTS (measured 2026-08-30): a VM frame is not a display frame,
and it is not a CONSTANT number of them either. The reference-engine sample
conversion's 17 rooms span
1.00 to 2.14 LCD frames per game frame; the platformer
conversion's six span 1.17 to 3.53.
A CONVERSION scales every authored duration and velocity by ONE constant
(`VM_FRAMES_PER_LCD`), which can only be right where the ratio equals it - so
with a varying ratio some room is always wrong, and wrong in the direction
that punishes optimisation: a room taken from 2.00 to 1.00 runs its whole game
at DOUBLE the reference speed. Locking turns the ratio back into the constant
the conversion assumes.

The contract this pins:
  - OFF (absent, or 1) is BYTE-IDENTICAL: no lock arm, no BSS sample, not even
    the `system.frames()` call at the top of the frame;
  - the guard is a STATEMENT-level define, so it must be supplied ALWAYS or it
    survives as a runtime test on an undeclared symbol (the VM_OBJ16 rule);
  - it is a FLOOR: the wait is `while elapsed < N`, so a frame that already
    overran does not wait (the reference engine's own `wait_vbl_done` does not either);
  - the elapsed subtraction is u8 and WRAPS (the music catch-up's idiom).

ROM-measured: with `frame_lock = 2`, 30 of 34 (room x regime)
readings on the reference-engine sample conversion become a flat 2.00-2.01 where they spanned 1.00 to
2.14, and the pit-death probe's blank gravity returns to the reference's own
schedule (58/178 against script_13.s's 60/180, from 30/90 unlocked).
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

from mosaik8_build import BuildConfig       # noqa: E402

FAILS = []


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    if not cond:
        FAILS.append(label)


SRC = open(os.path.join(ROOT, "lib", "vm", "core.mos"),
           encoding="utf-8").read()

def cfg(value):
    c = BuildConfig.__new__(BuildConfig)
    c.config = {"build": {}} if value is None else {"build": {"frame_lock": value}}
    return c


def main():
    # 1. The knob: default, range, and refusals.
    check("frame_lock absent -> 1 (off)", cfg(None).get_frame_lock() == 1)
    check("frame_lock = 2 reads 2", cfg(2).get_frame_lock() == 2)
    check("frame_lock = 8 reads 8", cfg(8).get_frame_lock() == 8)
    for bad in (0, 9, -1, "two"):
        try:
            cfg(bad).get_frame_lock()
            check("frame_lock %r refused" % (bad,), False)
        except ValueError:
            check("frame_lock %r refused" % (bad,), True)

    # 2. The lib contract. `vm.core` imports the whole VM pack, so it does not
    # compile standalone - the established shape for a core.mos feature is a
    # SOURCE contract plus a ROM measurement (see music_isr_test). The
    # byte-identity of the OFF path is checked on real projects and recorded:
    # the reference-engine sample and platformer conversions are md5-identical with the feature present and
    # the lock absent (2026-08-30).
    check("the lock STATE is declared inside the fork (nothing when off)",
          re.search(r"if VM_FRAME_LOCK_ON \{\s*\n\s*var lk_t0: u8\s*\n\s*\}",
                    SRC) is not None)
    check("run() samples the DISPLAY clock under the guard",
          re.search(r"if VM_FRAME_LOCK_ON \{\s*\n\s*lk_t0 = system\.frames\(\)",
                    SRC) is not None)
    check("the wait is a FLOOR (`while ... < N`), never an equality",
          re.search(r"while lkd < VM_FRAME_LOCK \{", SRC) is not None
          and not re.search(r"while lkd == VM_FRAME_LOCK", SRC))
    check("the elapsed delta is u8, so the display counter's WRAP is handled "
          "by the subtraction (the music catch-up's idiom)",
          re.search(r"var lkd: u8 = system\.frames\(\) - lk_t0", SRC) is not None)
    check("the wait re-reads the clock inside the loop (or it spins forever)",
          re.search(r"video\.wait_vblank\(\)\s*\n\s*lkd = system\.frames\(\) - lk_t0",
                    SRC) is not None)
    check("the lock sits AFTER the frame's own vblank wait",
          SRC.index("if VM_FRAME_LOCK_ON {\n                var lkd")
          > SRC.index("            video.wait_vblank()"))

    # 3. The guard must be a define that is ALWAYS supplied, or a statement
    # -level `if` survives as a runtime test on an undeclared symbol.
    csrc = open(os.path.join(ROOT, "mosaik", "compiler.py"),
                encoding="utf-8").read()
    check("compile_program always supplies VM_FRAME_LOCK_ON",
          "setdefault('VM_FRAME_LOCK_ON'" in csrc)
    check("compile_program always supplies VM_FRAME_LOCK",
          "setdefault('VM_FRAME_LOCK'" in csrc)

    if FAILS:
        print("\n%d FAILED" % len(FAILS))
        return 1
    print("\nAll frame-lock checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
