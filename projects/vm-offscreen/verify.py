#!/usr/bin/env python3
"""vm-offscreen -- run the two engine ROM tests this sample exists for.

    python projects/vm-offscreen/verify.py

The checks themselves live in the engine suite, where they run by default:

  * tests/actor_wake_latency_test.py - a parked slot the camera reaches is
    back on the live list within the `[build] actor_scan` budget;
  * tests/room_change_animator_leak_test.py - a room change leaves no
    animator armed from the previous room.

Each builds a temp copy of this project relinked with `-Wl-j` (the ROM and
its symbols from one link) and skips without GBDK or PyBoy. This file only
runs them, so there is exactly one copy of each check.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
TESTS = ("actor_wake_latency_test.py", "room_change_animator_leak_test.py")


def main():
    failed = 0
    for name in TESTS:
        print("== %s" % name)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", name)],
                           cwd=ROOT)
        if r.returncode != 0:
            failed += 1
    print("\n%s" % ("All checks passed" if not failed
                    else "%d test(s) FAILED" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
