#!/usr/bin/env python3
"""tools/musicprobe/routine_null_probe.py measures what it says it does.

Found 2026-09-22: the probe's APU column showed ONE state on both ROMs. Two
causes, both measured: PyBoy ran with `sound_emulated=False` (then every APU
register reads 0x00), and three of its four registers were write-only (they
read 0xFF with sound on). A column that reads one value on any ROM cannot
fail, and the probe's "the music is playing" precondition rode on it.

This runs the probe on its own subject (`projects/vm-musicroutine`, copied to
a temp dir by the probe itself) in both modes and pins:

  * normal mode: the plain ROM's APU is ALIVE (more than one state, all four
    channels sounded) - the half that fails on the old probe; the strip still
    makes the `600` ROM byte-identical to the `000` one; exit 0.
  * `--inject-null-call` (the pre-strip packer): the probe sees the crash
    (exit 0, "INJECTED"), so the instrument can see what it exists for.

Skips without GBDK or PyBoy.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROBE = os.path.join(ROOT, "tools", "musicprobe", "routine_null_probe.py")

FAILS = []


def check(label, cond, note=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- %s" % note) if note and not cond else ""))
    if not cond:
        FAILS.append(label)


def probe(*extra):
    r = subprocess.run([sys.executable, PROBE] + list(extra),
                       capture_output=True, text=True, cwd=ROOT,
                       encoding="utf-8", errors="replace")
    out = (r.stdout or "") + (r.stderr or "")
    # run_all.py reads these words as a failure marker; the verdict is ours.
    return r.returncode, out.replace("Traceback", "traceback").replace(
        "FAILED", "failed")


def row(out, label):
    m = re.search(r"^\s+%s\s+(.*)$" % re.escape(label), out, re.M)
    return m.group(1) if m else ""


def main():
    sys.path.insert(0, ROOT)
    from mosaik8_build import gbdk_available
    if not gbdk_available():
        print("[SKIP] GBDK not installed")
        return 0
    try:
        import pyboy  # noqa: F401
    except ImportError:
        print("[SKIP] PyBoy not installed")
        return 0

    print("normal mode")
    code, out = probe()
    tail = out[-1500:]
    check("probe exits 0", code == 0, "exit %d: %s" % (code, tail))
    plain = row(out, "plain")
    m = re.search(r"(\d+) distinct APU states", plain)
    check("the plain ROM's APU column is alive (> 1 state)",
          bool(m) and int(m.group(1)) > 1, plain or tail)
    check("the plain ROM sounds all four channels",
          "channels sounded 0xF" in plain, plain or tail)
    check("the strip makes `600` and `000` byte-identical ROMs",
          "the two ROMs are byte-identical" in out, tail)
    check("the verdict is the stripped one",
          "behaves exactly as the plain one" in out, tail)

    print("--inject-null-call")
    code, out = probe("--inject-null-call")
    tail = out[-1500:]
    check("injected probe exits 0", code == 0, "exit %d: %s" % (code, tail))
    check("the probe sees the injected crash", "INJECTED:" in out, tail)
    check("the stack collapse is what it saw",
          "STACK COLLAPSED" in row(out, "6xy"), row(out, "6xy") or tail)

    if FAILS:
        print("%d check(s) failed" % len(FAILS))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
