#!/usr/bin/env python3
"""`[build] actor_deactivate` COMPILES on the cc65 consoles (Lynx, PCE).

Found 2026-09-23 building a reference-engine conversion for the Lynx: the build
stopped in cc65 with `Undeclared identifier 'ov_ok'` (vm.actor lines 2262 /
2275, from the Mosaik compiler's "Undefined variable: ov_ok").

`ov_ok` is the overlap_at memo, and it is declared for the GB family, SMS/GG
and the NES only - the Lynx and the PCE never had it, and every other write of
it sits in an `if platform == "lynx" or platform == "pce" {} else {...}`
fork. The two writes in `wake_scan` (the parked -> live transition, only
compiled under `VM_ACTOR_DEACT`) were not, so ANY cc65 build with the knob on
failed. No first-party sample set the knob and a cc65 target together
(vm-offscreen, the knob's sample, targets the GB pair), so nothing built that
combination. This builds vm-offscreen for the Lynx and the PCE.

    python tests/actor_deactivate_cc65_test.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik8_build import cc65_available  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok: " if cond else "  FAIL: ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    if not cc65_available():
        print("SKIP: cc65 not installed")
        return 0
    tmp = tempfile.mkdtemp(prefix="deact_cc65_")
    try:
        root = os.path.join(tmp, "vm-offscreen")
        shutil.copytree(os.path.join(ROOT, "projects", "vm-offscreen"), root,
                        ignore=shutil.ignore_patterns("build"))
        cfg = os.path.join(root, "mosaik.toml")
        text = open(cfg, encoding="utf-8").read()
        check(re.search(r"^actor_deactivate\s*=\s*true", text, re.M) is not None,
              "the sample turns actor_deactivate on")
        for plat in ("lynx", "pce"):
            r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"),
                                "build", "--platform", plat, root],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace")
            out = r.stdout + r.stderr
            check("ov_ok" not in out,
                  "%s: no undeclared overlap memo in the build output" % plat)
            errs = re.findall(r"\.c:\d+: Error: .*", out)
            check(not errs, "%s: the C compiles (%s)" % (plat, errs[:2]))
            if plat == "pce":
                # The Lynx then stops at LINK: vm-offscreen's BSS does not fit
                # its MAIN area (7,086 B over, measured the same day) - a
                # capacity fact about the sample, not this defect.
                check(r.returncode == 0, "pce: the build succeeds")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILS:
        print("FAILED %d check(s)" % len(FAILS))
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
