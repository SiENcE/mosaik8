#!/usr/bin/env python3
"""Run the full mosaik test suite.

Executes every `*_test.py` / `test_*.py` / `quick_verification.py` script in this
folder and, optionally, compiles all sample programs end-to-end with the build
tool.  Reports a single pass/fail summary.

Usage:
    python tests/run_all.py            # run unit tests
    python tests/run_all.py --samples  # also build every sample to a ROM
"""

import os
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
FAIL_MARKERS = ("FAILED", "Traceback", "Some tests failed", "\U0001F4A5")

sys.path.insert(0, ROOT_DIR)
from mosaik_compiler import PLATFORM_CAPS  # noqa: E402


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
# pipeline.
PROJECT_DIRS = ("projects/game", "projects/shmup")


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

    for path in discover_tests():
        name = os.path.basename(path)
        passed, output = run_test(path)
        print(f"[{'PASS' if passed else 'FAIL'}] {name}")
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

    print("\n" + "=" * 50)
    if failures:
        print(f"{failures} check(s) FAILED")
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
