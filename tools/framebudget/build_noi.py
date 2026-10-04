#!/usr/bin/env python3
"""Build a project and relink it with -Wl-j so the .noi carries every symbol.

The framebudget harness hooks stage ENTRY symbols, and a plain build's .noi
is written without -Wl-j (no local/static symbols) AND is not rewritten when
only the link changes - so a plain rebuild silently STALES it and every hook
lands on the wrong address. This runs the build, scrapes the exact lcc line
it printed, and re-runs it verbatim with -Wl-j appended.

Usage: build_noi.py <project dir> [--platform gameboy]
"""
import subprocess
import sys
import shlex
import os

def main():
    proj = sys.argv[1]
    plat = "gameboy"
    if "--platform" in sys.argv:
        plat = sys.argv[sys.argv.index("--platform") + 1]
    here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    r = subprocess.run([sys.executable, os.path.join(here, "mosaik8.py"), "build",
                        "--platform", plat, proj],
                       capture_output=True, text=True, cwd=here,
                       encoding="utf-8", errors="replace")
    out = r.stdout + r.stderr
    line = None
    for ln in out.splitlines():
        if ln.strip().startswith("Compiling with") and "lcc" in ln:
            line = ln.split(":", 1)[1].strip()
    if line is None:
        sys.stdout.write(out[-4000:])
        print("!! no lcc line found (build failed?)")
        return 1
    if "ROM created" not in out:
        sys.stdout.write(out[-4000:])
        print("!! build did not produce a ROM")
        return 1
    cmd = shlex.split(line, posix=False)
    cmd = [c.strip('"') for c in cmd]
    cmd.insert(1, "-Wl-j")
    r2 = subprocess.run(cmd, capture_output=True, text=True, cwd=here,
                        encoding="utf-8", errors="replace")
    if r2.returncode != 0:
        print(r2.stdout[-3000:], r2.stderr[-3000:])
        return 1
    rom = cmd[cmd.index("-o") + 1]
    print("relinked with -Wl-j:", rom)
    print("noi:", os.path.splitext(rom)[0] + ".noi")
    return 0

if __name__ == "__main__":
    sys.exit(main())
