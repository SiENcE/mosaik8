#!/usr/bin/env python3
"""Which functions did sdcc SPILL? (`add sp, #-N` per function.)

The 2026-08-31 finding this exists for: `vm_actor_render` opens `add sp, #-14`
and the amortised scan's one extra `look` local was enough to push the array
addressing out of registers - reading one byte of a global array compiled to
FIFTEEN instructions that push HL and split it byte-wise into two frame slots.
Measured, that made SKIPPING a slot cost ~1,050 cycles against ~1,560 for
looking at one, so the amortisation was giving back two thirds of what it
saves.

The lesson generalises badly to guesswork: on this compiler "cheap" is a
property of the register allocator, not of the source, and there is no way to
tell by reading the .mos. So read the frame size, and read it BEFORE deciding a
loop body is already tight.

Usage:
    frame_spill.py <build_dir> [--min 8] [--only NAME,NAME]

  <build_dir>  a GBDK build directory (the .c files the build wrote), e.g.
               projects/vm-offscreen/build/gameboy

Cross-reference the output with framebudget.py's stage table: a big frame on a
function that is not per-frame is nobody's problem.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def compile_asm(c_file, out_dir):
    gbdk = os.environ.get("GBDK_HOME") or str(ROOT / "gbdk")
    lcc = str(Path(gbdk) / "bin" / "lcc")
    out = Path(out_dir) / (Path(c_file).stem + ".asm")
    cmd = [lcc, "-msm83:gb", "-S",
           "-I" + str(ROOT / "vendor" / "hugedriver"),
           "-I" + str(Path(gbdk) / "include"),
           "-o", str(out), str(c_file)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return out if out.exists() else None


def frames_of(asm):
    """`{function: frame bytes}` for one .asm - the `add sp, #-N` prologue."""
    out, cur = {}, None
    for line in open(asm, encoding="utf-8", errors="replace"):
        m = re.match(r"^(_\w+)::", line)
        if m:
            cur = m.group(1)
            continue
        if cur is None:
            continue
        if "add\tsp, #-" in line:
            out[cur] = int(line.split("#-")[1].strip())
            cur = None
        elif line.startswith("_") and "::" in line:
            cur = None
    return out


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    bdir = Path(sys.argv[1])
    arg = lambda n, d: (sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d)
    lo = int(arg("--min", "8"))
    only = [x for x in (arg("--only", "") or "").split(",") if x]

    cs = sorted(bdir.glob("*.c"))
    if not cs:
        print("no .c files in", bdir)
        return 1
    frames = {}
    with tempfile.TemporaryDirectory() as td:
        for c in cs:
            a = compile_asm(c, td)
            if a:
                frames.update(frames_of(a))
            else:
                print("!! could not compile", c.name, file=sys.stderr)

    rows = [(v, k.lstrip("_")) for k, v in frames.items()
            if v >= lo and (not only or any(o in k for o in only))]
    rows.sort(reverse=True)
    print("stack frame (bytes)  function        [%d function(s) at >= %d]"
          % (len(rows), lo))
    for v, k in rows:
        print("  %4d   %s" % (v, k))
    print("\nA big frame is only a problem on a PER-FRAME path - check it "
          "against framebudget.py's stage table before touching anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
