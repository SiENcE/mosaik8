"""Relink a built Lynx ROM with a cc65 label file, matching the real link flags.

    python emu/libretro/lynx_relabel.py projects/<name>

`lynx_probe.py` needs symbol addresses, and the normal build omits the `-Ln`
label file. Relinking by hand is a trap for a project whose assets STREAM from
the cart: the build bakes `GBS_ARCHIVE_BASE` in a second pass from the first
pass's code size, so a relink with a stale base silently points the streamed
reads at the wrong cart offset. For the falling-block assembly sample that means the BYTECODE BLOB
itself is garbage and the VM executes noise -- a whole afternoon's worth of
"why did the game stop running?".

So this reads the base out of the generated C (where the build wrote it) and
passes the same value, plus the project's own stack size, producing a `.lbl`
that actually describes the shipped ROM.
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    proj = sys.argv[1].rstrip("/\\")
    name = os.path.basename(proj)
    build = os.path.join(proj, "build", "lynx")
    csrc = os.path.join(build, name + ".c")
    if not os.path.exists(csrc):
        sys.exit("no %s -- build for lynx first" % csrc)

    # The base is decided by the BUILD (second pass, from the first pass's code
    # size) and only ever appears on its cl65 command line -- the C itself
    # carries a `#define GBS_ARCHIVE_BASE 0` fallback, so reading the source
    # gives 0 and a silently broken stream. Re-run the build and take the value
    # it reports.
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"),
                        "build", "--platform", "lynx", proj],
                       capture_output=True, text=True, cwd=ROOT)
    m = re.search(r"GBS_ARCHIVE_BASE=(\d+)", r.stdout + r.stderr)
    base = m.group(1) if m else None

    stack = "0x0200"
    toml = os.path.join(proj, "mosaik.toml")
    if os.path.exists(toml):
        m = re.search(r"^lynx_stack_size\s*=\s*(\S+)", open(toml).read(), re.M)
        if m:
            stack = hex(int(m.group(1), 0))

    cc65 = os.path.join(ROOT, "cc65", "bin", "cl65.exe")
    lbl = os.path.join(build, name + ".lbl")
    cmd = [cc65, "-t", "lynx", "-O", "--static-locals"]
    if base:
        cmd += ["-D", "GBS_ARCHIVE_BASE=" + base]
    cmd += ["-Wl", "-D__STACKSIZE__=" + stack, "-Ln", lbl,
            "-o", os.path.join(build, name + ".relabel.lnx"), csrc]
    r = subprocess.run(cmd, capture_output=True, text=True)
    errs = [l for l in (r.stdout + r.stderr).splitlines() if "rror" in l]
    if errs:
        print("\n".join(errs[:10]))
        return 1
    n = sum(1 for l in open(lbl) if l.startswith("al "))
    print("wrote %s (%d symbols, GBS_ARCHIVE_BASE=%s, stack=%s)"
          % (lbl, n, base or "n/a", stack))
    return 0


if __name__ == "__main__":
    sys.exit(main())
