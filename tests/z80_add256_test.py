#!/usr/bin/env python3
"""The SDCC z80 `+ 256` comparison miscompile stays worked around.

SDCC's z80 backend (GBDK-2020, SMS/GG) miscompiles `u16var + SCREEN_WIDTH`
when it appears INLINE in a compound comparison and the constant is 256 (the
SMS screen width): the `+ 0x100` strength-reduces to `inc h` on an `h` that
was never loaded from the operand, so the limit computes as
`(low(cx)) + 256` and every actor right of a wide room's camera parked the
moment the camera passed x = 255 (the SMS/GG sample conversion's long walk-in room
missing-actors bug, 2026-08-17). A standalone `var lim: u16 = cx +
SCREEN_WIDTH` initializer compiles the add correctly, so the workaround is to
precompute the limit into a local (vm.actor.off_window, vm.emote.update).

This test pins the SOURCE shape: no lib line may compare against an inline
`+ SCREEN_WIDTH` sum. Comparing against the bare constant (`sx >
SCREEN_WIDTH`, vm.projectile) is fine - only the var-plus-256 sum inside a
comparison triggers the bug.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib")

# A comparison operator on the same line as an inline `+ SCREEN_WIDTH` /
# `+ SCREEN_HEIGHT` sum. SCREEN_HEIGHT is 192 everywhere today (an 8-bit
# add, safe), but a future 256-row console would hit the same bug, so both
# stay out of comparisons.
BAD = re.compile(r"[<>].*\+\s*SCREEN_(WIDTH|HEIGHT)|\+\s*SCREEN_(WIDTH|HEIGHT).*[<>]")


def main():
    offenders = []
    for dirpath, _dirs, files in os.walk(LIB):
        for fn in files:
            if not fn.endswith(".mos"):
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    code = line.split("--", 1)[0]
                    if BAD.search(code):
                        offenders.append("%s:%d: %s" % (
                            os.path.relpath(path, ROOT), n, line.strip()))
    if offenders:
        print("inline `+ SCREEN_WIDTH` inside a comparison (SDCC z80 "
              "miscompiles the 256 add; precompute into a local first):")
        for o in offenders:
            print("  " + o)
        sys.exit(1)

    # ... and the two fixed sites keep the precomputed-local shape.
    for mod in ("vm/actor.mos", "vm/emote.mos"):
        with open(os.path.join(LIB, mod), encoding="utf-8") as f:
            src = f.read()
        if "lim_x: u16 = cx + SCREEN_WIDTH" not in src:
            print("%s lost the precomputed off-window limit" % mod)
            sys.exit(1)
    print("z80 +256 comparison workaround intact")


if __name__ == "__main__":
    main()
