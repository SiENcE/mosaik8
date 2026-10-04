#!/usr/bin/env python3
"""Link the samples that sit AT a console's hard image ceiling.

Two shipped samples are deliberately parked against a ceiling with only a few
hundred bytes to spare, so ordinary engine growth pushes them over:
`vm-showcase` (GBC) and `vm-maxcaps-gb` (GB), against the GB family's resident
image - everything below 0x4000, where bank 1 maps. Past it the linker still
writes a ROM and bank 1 silently overwrites the overflow: a black-screen boot,
not a link error (the build refuses it and deletes the ROM, which is the only
reason it is visible at all).

Nothing watched them. `run_all.py` discovers `*_test.py` here; `PROJECT_DIRS`
only builds, only behind `--samples`, and lists no `vm-*` project at all (its
own comment claims "the VM8 samples carry the tight-Lynx regression coverage
now", which was never wired). The rows had been failing since 2026-08-24 and
2026-09-06 -- found by hand, not by the suite. Same lesson as
`project_verify_test.py`: a check nobody runs is a check that does not exist,
so this is in the DEFAULT path, not behind a flag.

A third row used to link the falling-block assembly sample against the ONE
~46.6 KB Lynx MAIN area (code, rodata, BSS, the stack and both screen buffers).
That sample is local-only now, and its first-party successor `vm-snake` is NOT
parked at that ceiling: measured 2026-09-22 (ld65 map), its Lynx link leaves
about 2 KB of MAIN free, so it does not belong on this list.

Cost ~70 s. It links ONE platform per project - the tightest one - because the
ceiling is per console and a second target only re-measures the same code.

    python tests/tight_image_test.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik8_build import cc65_available, gbdk_available  # noqa: E402

# (project, platform, which toolchain must be present, what it is parked against)
TIGHT = (
    ("vm-showcase", "gameboy_color", "gbdk", "GB bank-0 resident image"),
    ("vm-maxcaps-gb", "gameboy", "gbdk", "GB bank-0 resident image"),
)

ok = True


def _have(kind):
    """**The builder's own probe** (`mosaik8_build.gbdk_available`), never a
    lookalike. This module's failure text says a sample no longer FITS ITS
    CONSOLE, which is the most alarming thing the suite can say - so it must
    not be reachable by a machine that could never have linked at all. It was:
    a directory test saw the Windows `gbdk/`+`cc65/` that rsync had carried
    into the Linux release container, and every sample was reported as
    over budget when the link had not run."""
    return gbdk_available() if kind == "gbdk" else cc65_available()


def run_one(name, platform, toolchain, ceiling):
    global ok
    if not _have(toolchain):
        print("  skip: %s [%s] -- %s not installed" % (name, platform, toolchain))
        return
    proj = os.path.join(ROOT, "projects", name)
    if not os.path.isdir(proj):
        print("  skip: %s not present" % name)
        return
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", platform, proj],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=ROOT)
    out = (r.stdout or "") + (r.stderr or "")
    if "ROM created" in out:
        print("  [PASS] %s [%s] still fits the %s" % (name, platform, ceiling))
        return
    ok = False
    print("  [FAIL] %s [%s] no longer fits the %s" % (name, platform, ceiling))
    # The overflow line only -- see project_verify_test.py on FAIL_MARKERS
    # matching a wrapper's own forwarded output.
    for line in out.splitlines():
        if "resident image ends at" in line or "overflows memory area" in line:
            print("         " + line.strip())
    print(out[-1500:])


def main():
    print("[samples parked at a console's image ceiling]")
    for name, platform, toolchain, ceiling in TIGHT:
        run_one(name, platform, toolchain, ceiling)
    print("\n%s" % ("All checks passed" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
