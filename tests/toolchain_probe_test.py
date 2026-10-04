#!/usr/bin/env python3
"""A directory named `gbdk` is not a toolchain.

The probes that answer "can this machine link a ROM" had no test at all, and
the one difference between them was a real bug. `GBDKInterface._find_tool_in_dir`
accepts the `.exe` spelling only ON Windows; `Cc65Interface._find_cl65` accepted
it everywhere. So a Windows cc65 tree validated on Linux and the build got as
far as exec'ing a PE binary:

    Error running cc65 tools: [Errno 13] Permission denied:
        '/src/mosaik8/cc65/bin/cl65.exe'

That is not hypothetical plumbing. `tools/release/linux_build.sh` rsyncs the
Windows working tree into the release container, and the four ROM-behaviour
suites each asked `os.path.isdir(ROOT/'gbdk')` for themselves. The answer was
yes, the link then failed, and `tight_image_test` reported three shipped
samples as NO LONGER FITTING THEIR CONSOLE - the most alarming sentence the
suite can print, from a machine that could not have linked anything at all
(2026-09-11: 5 red engine modules, zero engine defects).

Two rules, pinned here:

  1. **A toolchain is validated by an executable this OS can run**, not by a
     folder. The `.exe` names count on Windows and nowhere else, and GBDK and
     cc65 answer that identically.
  2. **The availability question has ONE owner**: `mosaik8_build.gbdk_available`
     / `cc65_available`, which every skip-politely test calls, so "the test
     thinks it can build" and "the build can build" cannot disagree.

    python tests/toolchain_probe_test.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mosaik8_build as mb  # noqa: E402

ok = True


def check(label, cond):
    global ok
    ok = ok and bool(cond)
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))


def _fake_gbdk(root, suffix):
    """A GBDK tree whose tools carry `suffix` ('' or '.exe')."""
    for d in ("bin", "lib", "include"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    for tool in ("lcc", "sdcc", "sdasgb", "makebin"):
        open(os.path.join(root, "bin", tool + suffix), "w").close()
    return root


def _fake_cc65(root, suffix):
    """A cc65 tree whose driver carries `suffix`."""
    for d in ("bin", "cfg", "lib"):
        os.makedirs(os.path.join(root, d), exist_ok=True)
    open(os.path.join(root, "bin", "cl65" + suffix), "w").close()
    return root


def _as(osname, fn, *args):
    """Run `fn` as though this were `osname`. Both probes read `os.name` at
    call time, which is what makes the rule testable from either platform -
    and testing it from only one is how the asymmetry survived."""
    real = os.name
    os.name = osname
    try:
        return fn(*args)
    finally:
        os.name = real


def main():
    print("[toolchain probes: an executable, not a directory]")
    gbdk = mb.GBDKInterface.__new__(mb.GBDKInterface)
    cc65 = mb.Cc65Interface.__new__(mb.Cc65Interface)

    with tempfile.TemporaryDirectory() as tmp:
        win_g = _fake_gbdk(os.path.join(tmp, "win_gbdk"), ".exe")
        nix_g = _fake_gbdk(os.path.join(tmp, "nix_gbdk"), "")
        win_c = _fake_cc65(os.path.join(tmp, "win_cc65"), ".exe")
        nix_c = _fake_cc65(os.path.join(tmp, "nix_cc65"), "")

        print("\n  GBDK")
        check("a .exe tree validates on Windows",
              _as("nt", gbdk.validate_gbdk_installation, win_g))
        check("...and is REFUSED off Windows",
              not _as("posix", gbdk.validate_gbdk_installation, win_g))
        check("a POSIX tree validates off Windows",
              _as("posix", gbdk.validate_gbdk_installation, nix_g))

        print("\n  cc65 (the same rule, which it used not to follow)")
        check("a .exe tree validates on Windows",
              _as("nt", cc65.validate_cc65_installation, win_c))
        check("...and is REFUSED off Windows",
              not _as("posix", cc65.validate_cc65_installation, win_c))
        check("a POSIX tree validates off Windows",
              _as("posix", cc65.validate_cc65_installation, nix_c))

        print("\n  the shape, not just the binary")
        os.remove(os.path.join(nix_c, "bin", "cl65"))
        check("a cc65 tree with no driver is refused",
              not _as("posix", cc65.validate_cc65_installation, nix_c))
        check("an empty directory named gbdk is not a toolchain",
              not _as("posix", gbdk.validate_gbdk_installation,
                      os.path.join(tmp, "gbdk")))

    print("\n  one owner for the question")
    check("gbdk_available() is the finder's own answer",
          mb.gbdk_available() == (mb.GBDKInterface().find_gbdk() is not None))
    check("cc65_available() is the finder's own answer",
          mb.cc65_available() == (mb.Cc65Interface().find_cc65() is not None))
    # The skip-politely suites must ask THAT question, not roll their own.
    # (A directory test is what let a wrong-platform tree read as installed.)
    for mod in ("bitwise_test.py", "bank_window_test.py",
                "tight_image_test.py", "project_verify_test.py"):
        with open(os.path.join(ROOT, "tests", mod), encoding="utf-8") as f:
            src = f.read()
        check("%s asks mosaik8_build, not the filesystem" % mod,
              "gbdk_available" in src or "cc65_available" in src)

    print("\n%s" % ("All checks passed" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
