#!/usr/bin/env python3
"""tools/framebudget/trampoline_census.py on a ROM with NO banked code.

A ROM that banks nothing never links sdcc's ___sdcc_bcall_ehl, and the
census used to die on `KeyError: '___sdcc_bcall_ehl'` there (found
2026-09-22). The honest answer is zero trampolines, and the probe must say
so and exit 0.

Two arms:

  * a FIXTURE .noi (always runs, no toolchain): no trampoline symbol ->
    exit 0 and "no banked code"; no VM frame symbol either -> exit 2, so an
    empty or wrong file is not mistaken for "no banked code"; and the
    mapped-bank address is read under the name the .noi really uses
    (`__current_bank`).
  * a REAL build of `projects/vm-snake` (GBDK + PyBoy, else skipped): the
    subject precondition is checked first (its .noi must lack the
    trampoline, or this arm is not testing the case at all), then the
    census must report zero.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TOOL = os.path.join(ROOT, "tools", "framebudget", "trampoline_census.py")
BUILD_NOI = os.path.join(ROOT, "tools", "framebudget", "build_noi.py")
SUBJECT = "vm-snake"

FAILS = []


def check(label, cond, note=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- %s" % note) if note and not cond else ""))
    if not cond:
        FAILS.append(label)


def census(rom, noi):
    r = subprocess.run([sys.executable, TOOL, "--rom", rom, "--noi", noi],
                       capture_output=True, text=True, cwd=ROOT,
                       encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def short(out):
    """A failing run's output, with the words run_all.py reads as a failure
    marker defanged (the check line already carries the verdict)."""
    return out[-600:].replace("Traceback", "traceback").replace("\n", " | ")


def fixture_arm(tmp):
    print("fixture .noi")
    noi = os.path.join(tmp, "fixture.noi")
    with open(noi, "w", encoding="utf-8") as f:
        f.write("DEF _main 0x200\nDEF _vm_core_run_scripts 0x2C7D\n"
                "DEF __current_bank 0xFF91\n")
    # The ROM path does not exist: with no banked code it must not be run.
    code, out = census(os.path.join(tmp, "missing.gb"), noi)
    check("no trampoline symbol -> exit 0", code == 0,
          "exit %d: %s" % (code, short(out)))
    check("reports no banked code and zero calls",
          "no banked code" in out and "banked calls/frame: 0.00" in out,
          short(out))

    empty = os.path.join(tmp, "empty.noi")
    open(empty, "w").close()
    code, out = census(os.path.join(tmp, "missing.gb"), empty)
    check("a .noi with no VM frame symbol is refused (exit 2)", code == 2,
          "exit %d: %s" % (code, short(out)))

    sys.path.insert(0, os.path.dirname(TOOL))
    try:
        import trampoline_census as tc
        got = tc.current_bank_addr(tc.load_noi(noi))
    except BaseException as exc:            # noqa: BLE001 (SystemExit too)
        check("the census imports without running", False, repr(exc))
        return
    check("mapped bank is read as __current_bank from the .noi",
          got == 0xFF91, "got 0x%04X" % got)


def real_arm(tmp):
    print("real build: %s" % SUBJECT)
    sys.path.insert(0, ROOT)
    from mosaik8_build import gbdk_available
    if not gbdk_available():
        print("  [SKIP] GBDK not installed")
        return
    try:
        import pyboy  # noqa: F401
    except ImportError:
        print("  [SKIP] PyBoy not installed")
        return
    proj = os.path.join(tmp, SUBJECT)
    shutil.copytree(os.path.join(ROOT, "projects", SUBJECT), proj,
                    ignore=shutil.ignore_patterns("build"))
    r = subprocess.run([sys.executable, BUILD_NOI, proj], capture_output=True,
                       text=True, cwd=ROOT, encoding="utf-8", errors="replace")
    bdir = os.path.join(proj, "build", "gameboy")
    rom = os.path.join(bdir, SUBJECT + ".gb")
    noi = os.path.join(bdir, SUBJECT + ".noi")
    built = r.returncode == 0 and os.path.isfile(rom) and os.path.isfile(noi)
    check("the subject builds with symbols", built,
          short((r.stdout or "") + (r.stderr or "")))
    if not built:
        return
    text = open(noi, encoding="utf-8", errors="replace").read()
    check("subject precondition: the .noi carries the VM frame symbol",
          "_vm_core_run_scripts " in text)
    check("subject precondition: no ___sdcc_bcall_ehl (no banked code)",
          "___sdcc_bcall_ehl " not in text,
          "the subject banks code now - pick another sample")
    code, out = census(rom, noi)
    check("census exits 0 on the real ROM", code == 0,
          "exit %d: %s" % (code, short(out)))
    check("census reports zero trampolines",
          "no banked code" in out and "banked calls/frame: 0.00" in out,
          short(out))


def main():
    tmp = tempfile.mkdtemp(prefix="tramp_census_")
    try:
        fixture_arm(tmp)
        real_arm(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
