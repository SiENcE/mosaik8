#!/usr/bin/env python3
"""Build a project and print its RESIDENT (bank-0) end - the byte-identical meter.

    python tools/resident.py vm-showcase:gameboy_color vm-offscreen:gameboy
    python tools/resident.py --projects ../my-games mygame:gameboy

A project is a directory path, or a NAME looked up under each `--projects DIR`
given (in order) and then under the engine's `projects/`. A platform after the
colon, defaulting to `gameboy_color` (GBC is the meter: it runs about 945 B
heavier than GB). The ceiling is 0x4000; past it the switchable
bank silently overwrites the tail and the ROM black-screens, so the build tool
refuses the ROM.

**This, not md5, is how "byte-identical off" is proved on the GBDK consoles.** A
pruned opcode arm still emits an empty `case`, which shifts the jump table - so
an md5 diff proves nothing while the resident end proves what the change costs.

Read it beside the linker map's `_CODE_N` areas when the question is which BANK
grew: `vm.player` and the rest of `[build] code_banks` do not touch bank 0 at
all, which is why a scene-type handler is nearly free there.
"""
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik8_targets import gbdk_resident_end      # noqa: E402

CEILING = 0x4000


#: Where a bare project NAME is looked up: every `--projects DIR` first, in
#: the order given, then the engine's own samples.
SEARCH = [os.path.join(ROOT, "projects")]


def project_dir(proj):
    """A directory path as given, else the name under the first search
    directory that has it (`--projects` dirs, then `projects/`)."""
    if os.path.isdir(proj):
        return os.path.abspath(proj)
    for base in SEARCH:
        p = os.path.join(base, proj)
        if os.path.isdir(p):
            return p
    return os.path.join(SEARCH[-1], proj)


def build(proj, platform):
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
         "--platform", platform, project_dir(proj)],
        capture_output=True, text=True, cwd=ROOT,
        encoding="utf-8", errors="replace")


def resident(proj, platform):
    """The resident end from the link map, or None when there is no map (a
    single-file or cc65 build does not write one)."""
    maps = glob.glob(os.path.join(project_dir(proj), "build", platform,
                                  "*.map"))
    if not maps:
        return None
    with open(maps[0], encoding="utf-8", errors="replace") as f:
        return gbdk_resident_end(f.read())


def main(argv):
    argv = list(argv)
    while "--projects" in argv:
        i = argv.index("--projects")
        if i + 1 >= len(argv):
            print("--projects needs a directory")
            return 2
        SEARCH.insert(len(SEARCH) - 1, os.path.abspath(argv[i + 1]))
        del argv[i:i + 2]
    if not argv:
        print(__doc__.strip().splitlines()[0])
        print("\n  usage: python tools/resident.py [--projects DIR]... "
              "<project>[:<platform>] ...")
        return 2
    bad = 0
    for spec in argv:
        proj, _, platform = spec.partition(":")
        platform = platform or "gameboy_color"
        r = build(proj, platform)
        end = resident(proj, platform)
        if r.returncode != 0:
            bad = 1
            print("%-24s %-16s BUILD FAILED" % (proj, platform))
            print(r.stdout[-3000:])
            continue
        if end is None:
            print("%-24s %-16s ok (no link map on this target)"
                  % (proj, platform))
            continue
        print("%-24s %-16s 0x%04X (%d), %d B spare"
              % (proj, platform, end, end, CEILING - end))
    return bad


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
