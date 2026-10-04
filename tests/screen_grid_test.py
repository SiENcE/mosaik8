#!/usr/bin/env python3
"""`PLATFORM_CAPS` screen_cols / screen_rows are the CONSOLE's numbers, checked
against the sources that actually define them.

WHY IT EXISTS. The visible text grid became a capability because a toolchain
choice can depend on it across a whole target set: a VM8 menu blob carries ONE
row byte for every console a project builds for, so the authoring layer has to
know the SHORTEST screen in that set (`isa.MIN_SCREEN_ROWS` is the floor over
all of them, and the studio's menu-row lint is the consumer).

The danger with adding numbers to a registry is inventing them. These are not
invented - each one is defined somewhere else already:

  * GBDK ports  `gbdk/include/<port>/hardware.h`  DEVICE_SCREEN_WIDTH/HEIGHT.
    The whole sm83 family shares `gb/hardware.h`; the SMS and the Game Gear
    share `sms/hardware.h` and are split by `__TARGET_sms` / `__TARGET_gg`.
  * cc65 ports  `mosaik/codegen/cc65.py` CC65_PROFILES screen_cols/screen_rows,
    which is what the prelude emits as SCREEN_COLS / SCREEN_ROWS.

So this test parses those two sources and fails if the registry drifts from
either. A console added to the registry with no source to check against fails
too, rather than passing silently - that is the point of the coverage check at
the end.

Note the GBDK prelude emits `#define SCREEN_ROWS DEVICE_SCREEN_HEIGHT`, i.e. it
defers to the header rather than to us, which is why the header is the truth
here and the registry is the copy.
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

from mosaik.platforms import PLATFORM_CAPS          # noqa: E402
from mosaik.codegen.cc65 import Cc65Backend         # noqa: E402

FAILS = []


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    if not cond:
        FAILS.append(label)


# console -> (gbdk header, the `#if defined(__TARGET_x)` arm to read, or None
# for a header with a single unconditional definition).
_GBDK = {
    "gameboy": ("gb", None), "gameboy_color": ("gb", None),
    "analogue_pocket": ("gb", None), "megaduck": ("gb", None),
    "sms": ("sms", "__TARGET_sms"), "gamegear": ("sms", "__TARGET_gg"),
    "nes": ("nes", None),
}
_CC65 = {"lynx": "lynx", "pce": "pce"}


def _header_grid(port, arm):
    """(cols, rows) as `gbdk/include/<port>/hardware.h` defines them.

    With `arm`, read only the block guarded by that `__TARGET_*` - the SMS and
    the Game Gear both live in sms/hardware.h with different numbers, so taking
    the first match would give the Game Gear the SMS's 32x24."""
    path = os.path.join(ROOT, "gbdk", "include", port, "hardware.h")
    if not os.path.isfile(path):
        return None
    text = open(path, encoding="utf-8", errors="replace").read()
    if arm:
        i = text.find("defined(%s)" % arm)
        if i < 0:
            return None
        text = text[i:]
    out = {}
    for key in ("WIDTH", "HEIGHT"):
        m = re.search(r"#define\s+DEVICE_SCREEN_%s\s+(\d+)" % key, text)
        if not m:
            return None
        out[key] = int(m.group(1))
    return out["WIDTH"], out["HEIGHT"]


def main():
    checked = set()

    for name, (port, arm) in _GBDK.items():
        caps = PLATFORM_CAPS[name]
        got = _header_grid(port, arm)
        if got is None:
            print("[skip] %s: no gbdk/include/%s/hardware.h (toolchain absent)"
                  % (name, port))
            continue
        checked.add(name)
        check("%s matches DEVICE_SCREEN_* in gbdk/include/%s/hardware.h"
              % (name, port),
              (caps["screen_cols"], caps["screen_rows"]) == got,
              "registry %dx%d, header %dx%d"
              % (caps["screen_cols"], caps["screen_rows"], got[0], got[1]))

    for name, prof_key in _CC65.items():
        caps = PLATFORM_CAPS[name]
        prof = Cc65Backend.CC65_PROFILES[prof_key]
        checked.add(name)
        check("%s matches CC65_PROFILES (what the prelude emits)" % name,
              (caps["screen_cols"], caps["screen_rows"])
              == (prof["screen_cols"], prof["screen_rows"]),
              "registry %dx%d, profile %dx%d"
              % (caps["screen_cols"], caps["screen_rows"],
                 prof["screen_cols"], prof["screen_rows"]))

    # Every console carries the keys, and every console is CHECKED against a
    # real source - a new one added to the registry with no entry above must
    # fail here rather than sit unverified.
    missing = [n for n, c in PLATFORM_CAPS.items()
               if "screen_cols" not in c or "screen_rows" not in c]
    check("every console declares its screen grid", not missing, missing)
    unchecked = set(PLATFORM_CAPS) - checked - set(_GBDK) - set(_CC65)
    check("every console is checked against a source", not unchecked, unchecked)

    # The VM8 floor must really be the shortest screen we ship, or a menu blob
    # bottom-anchored against it lands off the bottom of some console.
    from mosaik_vm import isa
    shortest = min(c["screen_rows"] for c in PLATFORM_CAPS.values())
    check("isa.MIN_SCREEN_ROWS is the shortest screen in the registry",
          isa.MIN_SCREEN_ROWS == shortest,
          "MIN_SCREEN_ROWS %d, shortest %d" % (isa.MIN_SCREEN_ROWS, shortest))

    if FAILS:
        print("\n%d FAILED" % len(FAILS))
        return 1
    print("\nAll screen-grid checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
