#!/usr/bin/env python3
"""Run the full mosaik test suite.

Executes every `*_test.py` / `test_*.py` / `quick_verification.py` script in this
folder and, optionally, compiles all sample programs end-to-end with the build
tool.  Reports a single pass/fail summary.

Usage:
    python tests/run_all.py                 # run unit tests
    python tests/run_all.py --samples       # also build every sample to a ROM
    python tests/run_all.py --strict-skips  # fail on a skip that is not a
                                            # missing toolchain / core / PyBoy

A module's output is shown only when it FAILS, so a module whose real check
SKIPPED would read exactly like one that ran it. Every skip line is therefore
collected and listed at the end, green run or not, split into skips for a
missing TOOLCHAIN (fine on a machine without one) and every OTHER skip, which
means a check did not run for a reason the machine cannot fix by installing
something. A release is accepted only with no OTHER skip (`--strict-skips`).
"""

import os
import re
import sys
import glob
import subprocess

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(TESTS_DIR)
# A test FAILS when it exits nonzero (the primary contract: every test must
# sys.exit(1) / raise on failure) OR prints one of these markers -- the safety
# net for print-style checks. "❌" (the cross mark) is printed by every
# print-style test exclusively on a failure path.
FAIL_MARKERS = ("FAILED", "Traceback", "Some tests failed", "\U0001F4A5",
                "❌")

sys.path.insert(0, ROOT_DIR)
from mosaik import PLATFORM_CAPS  # noqa: E402


#: A line that says a check was skipped. Tests word it in several ways
#: (`[SKIP] ...`, `skip: ...`, `  (skipped: ...)`), so this is deliberately wide;
#: prose that merely contains the word is not printed by a passing test.
SKIP_LINE = re.compile(r"(?i)(\[\s*skip\s*\]|\bskip(?:ped|ping)?\s*[:(-]"
                       r"|^\s*\(?skip(?:ped)?\b|\bskipped\)?\s*$)")

#: A skip whose reason is a missing TOOL is expected on a machine without it.
#: Anything else - a missing reference file, project or sibling checkout - is a
#: check that cannot run anywhere a public clone lands, and is listed apart.
TOOLCHAIN_SKIP = re.compile(r"(?i)gbdk|cc65|pyboy|libretro|\bcore\b|cores\b|"
                            r"toolchain|lynxboot|genesis_plus_gx|mednafen|"
                            r"fceumm|handy|holani|beetle|8\.3 names|"
                            r"not installed|no emulator|pillow|\bPIL\b|numpy|"
                            r"toml not installed")


#: A passing check whose TEXT is about skipping ("[PASS] a gauge with no max is
#: skipped") describes behaviour under test; it is not a skipped check.
PASS_LINE = re.compile(r"(?i)^\[?\s*(pass|ok)\s*\]?[\s:]")


def skip_lines(output):
    """The distinct skip lines in a module's output, in order."""
    seen, out = set(), []
    for line in output.splitlines():
        s = line.strip()
        if s and SKIP_LINE.search(s) and not PASS_LINE.match(s) \
                and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def discover_tests():
    names = set()
    for pattern in ("*_test.py", "test_*.py", "quick_verification.py"):
        for path in glob.glob(os.path.join(TESTS_DIR, pattern)):
            names.add(path)
    return sorted(names)


def run_test(path):
    result = subprocess.run([sys.executable, path], capture_output=True,
                            text=True, encoding='utf-8', errors='replace')
    output = (result.stdout or "") + (result.stderr or "")
    passed = result.returncode == 0 and not any(m in output for m in FAIL_MARKERS)
    return passed, output


# All samples now use `if platform == "..."` conditional compilation to handle
# platform-specific features, so every sample builds for every console.
# SAMPLE_NEEDS is kept as an empty dict; the build loop will cover all
# sample × platform combinations automatically.
SAMPLE_NEEDS = {}


def _build(sample, platform):
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT_DIR, "mosaik8.py"),
         "build", "--platform", platform, sample],
        capture_output=True, text=True, encoding='utf-8', errors='replace')
    output = (proc.stdout or "") + (proc.stderr or "")
    return "ROM created" in output, output


def build_samples():
    """Compile samples end-to-end: every sample x every console (all platforms),
    since every sample now handles unsupported features via conditional compilation."""
    sample_files = sorted(glob.glob(os.path.join(ROOT_DIR, "samples", "*.mos")))
    results = []
    for sample in sample_files:
        name = os.path.basename(sample)
        for platform in PLATFORM_CAPS:
            ok, output = _build(sample, platform)
            results.append((f"{name} [{platform}]", ok, output))
    return results


# Project samples (mosaik.toml-driven). Each is built for the platforms its
# project file declares; projects/shmup additionally exercises the PNG asset
# pipeline, projects/multifile the cross-file module linking, projects/background
# the scrollable bkg tilemap layer (+ a sprite on top) across all nine consoles
# — hardware tilemap on GBDK targets, VDC BAT on the PCE, the composited Suzy
# background sprite on the Lynx — and projects/colorlab the graphics.palette
# colour model across all nine (per-tile bkg palettes + sprite palette slots +
# the asset-palette pipeline, degrading to greyscale on the 4-grey consoles).
# (The native-feature pipeline sample that used to sit here left the tracked
# tree; native.lynx and sound.sfx keep their own tests, native_lynx_test.py
# and sound_sfx_test.py.)
# projects/game-slice is the reference-engine-style game-framework slice (cross-file
# modules + scenes/transitions/worldmap + dialogue + items + HUD + combat) across
# all nine consoles.
PROJECT_DIRS = ("projects/game", "projects/shmup", "projects/multifile",
                "projects/background", "projects/colorlab",
                "projects/game-slice",
                "projects/box-pusher", "projects/scene-demo",
                "projects/platformer", "projects/vendor-override")
# (The composed-studio Lynx-overflow tripwire `projects/platform-quest` was
# removed with the retired component composer; the VM8 samples carry the
# tight-Lynx regression coverage now -- see projects/vm-maxcaps-lynx.)


def build_projects():
    """Build each sample project for every platform its mosaik.toml lists."""
    import toml
    results = []
    for proj in PROJECT_DIRS:
        project_dir = os.path.join(ROOT_DIR, *proj.split("/"))
        config = toml.load(os.path.join(project_dir, "mosaik.toml"))
        for platform in config["project"]["target_platforms"]:
            ok, output = _build(project_dir, platform)
            results.append((f"{proj}/ [{platform}]", ok, output))
    return results


def main():
    print("MosaiK8 Test Suite")
    print("=" * 50)

    failures = 0
    skips = []                      # (module, line)

    for path in discover_tests():
        name = os.path.basename(path)
        passed, output = run_test(path)
        mod_skips = skip_lines(output)
        skips += [(name, s) for s in mod_skips]
        print(f"[{'PASS' if passed else 'FAIL'}] {name}"
              + (f"  ({len(mod_skips)} skip(s))" if mod_skips else ""))
        if not passed:
            failures += 1
            print(output)

    if "--samples" in sys.argv:
        print("\nBuilding samples")
        print("-" * 50)
        for name, ok, output in build_samples():
            print(f"[{'OK  ' if ok else 'FAIL'}] {name}")
            if not ok:
                failures += 1
                print(output)
        print("\nBuilding sample projects")
        print("-" * 50)
        for name, ok, output in build_projects():
            print(f"[{'OK  ' if ok else 'FAIL'}] {name}")
            if not ok:
                failures += 1
                print(output)

    tool = [(m, s) for m, s in skips if TOOLCHAIN_SKIP.search(s)]
    other = [(m, s) for m, s in skips if not TOOLCHAIN_SKIP.search(s)]
    print("\n" + "=" * 50)
    print(f"Skips: {len(skips)} line(s) in "
          f"{len({m for m, _ in skips})} module(s) "
          f"({len(tool)} missing toolchain / core, {len(other)} OTHER)")
    for m, s in tool:
        print(f"  [toolchain] {m}: {s}")
    for m, s in other:
        print(f"  [OTHER]     {m}: {s}")
    if other and "--strict-skips" in sys.argv:
        print(f"{len(other)} skip(s) are not a missing toolchain "
              "(--strict-skips): a check that cannot run on a clean clone")
        failures += len(other)
    if failures:
        print(f"{failures} check(s) FAILED")
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
