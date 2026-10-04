#!/usr/bin/env python3
"""The WIDE render state must not survive a scene change.

`setup_wide` set `wide = 1` and NOTHING ever cleared it, so once a game entered
a wide (column-streamed) room, every later room kept taking the wide arm of
`follow_and_render`: the player was positioned through a stale `camx16` and the
shell kept streaming the OLD room's columns. Walking from a wide PLATFORM room
into an ordinary TOPDOWN one therefore showed no player and no movement.

Every `setup_*` entry point now clears it first (`setup_shmup` inherits the
reset by calling `setup`), and `setup_wide` re-enables it immediately after -
it calls `setup_platform`, so the ordering holds.

The reset must stay narrow, though: it clears the per-room RENDER state, never
the shell's `set_scroll`/`set_scroll2d` REGISTRATIONS (see below).

This is a SOURCE-CONTRACT test: it pins that the reset exists and is reached
from each entry point. The behaviour itself needs a wide room handing off to a
narrow one on real hardware, which the reference-engine sample cannot reach by walking
alone (its route needs a jump).
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

PLAYER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "lib", "vm", "player.mos")


def check(label, cond):
    print("[%s] %s" % ("PASS" if cond else "FAIL", label))
    return bool(cond)


def _body(src, name):
    """The text of function `name` (to its closing brace at the same indent)."""
    m = re.search(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name),
                  src, re.M)
    if not m:
        return ""
    indent = m.group(1)
    end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
    return src[m.start():end.end() if end else len(src)]


def main():
    with open(PLAYER, encoding="utf-8") as f:
        src = f.read()
    ok = True

    reset = _body(src, "clear_wide")
    ok &= check("clear_wide() exists", bool(reset))
    for var in ("wide", "cam_maxx16", "cam_maxy_wide", "pvoff"):
        ok &= check("clear_wide() clears %s" % var,
                    re.search(r"\b%s = 0\b" % var, reset) is not None)

    # ...but it must NOT drop the shell's STREAM REGISTRATIONS. Those are a
    # boot-time wiring seam (player.set_scroll / set_scroll2d), not room state,
    # and they are only read under `wide > 0` - which clear_wide already resets.
    # Clearing them silently disabled every HAND-WRITTEN wide shell, which
    # registers once in main() before the first load_level: setup_wide runs
    # setup_platform -> clear_wide, so the registration was wiped and
    # scroll.update() never ran again. The 32-column ring then showed logical
    # columns 0..31 forever - a frozen background (no vcam writing the scroll
    # register) or, with a vcam, the WRONG columns sliding past a still-correct
    # collision map. Seen on a local scratch project and the platformer port.
    for var in ("has_stream", "has_stream2"):
        ok &= check("clear_wide() does NOT clear %s (a registration seam)" % var,
                    re.search(r"\b%s = 0\b" % var, reset) is None)

    # every entry point that installs a player handler resets first
    for fn in ("setup", "setup_platform"):
        ok &= check("%s() calls clear_wide()" % fn,
                    "clear_wide()" in _body(src, fn))
    ok &= check("setup_shmup() inherits the reset via setup()",
                re.search(r"setup\(tile, x, y, w, h, speed, cb\)",
                          _body(src, "setup_shmup")) is not None)

    # ...and setup_wide re-enables it AFTER delegating to setup_platform
    sw = _body(src, "setup_wide")
    ok &= check("setup_wide() delegates to setup_platform then sets wide = 1",
                sw.index("setup_platform(") < sw.index("wide = 1"))

    # the sticky bug in one line: `wide = 1` must not be the only assignment
    ok &= check("`wide` is assigned 0 somewhere (it never used to be)",
                re.search(r"\bwide = 0\b", src) is not None)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
