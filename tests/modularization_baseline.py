"""Capture a byte-identical BASELINE of everything the four big generators emit.

Not part of run_all -- a scratch instrument for the file-splitting work:

    python tests/modularization_baseline.py out_dir

Writes one file per artefact under `out_dir` plus a `MANIFEST.txt` of
sha1 hashes, so a `diff -r` (or a hash diff) before and after a refactor
proves the split changed no output. Covers:

  * `mosaik_scenes.transpile`  -- every project world -> scenes.mos
  * `mosaik_vm.generate_rooms` -- every VM project -> rooms.mos
  * `mosaik.compile_program`   -- the samples x every console -> C
    (the GBDK backend's feature emitters ride this)
"""
import hashlib
import io
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_scenes
import mosaik_vm
from mosaik import MosaikCompiler
from mosaik.platforms import PLATFORM_CAPS


def _worlds():
    """(project name, world path) for every project carrying a world."""
    pdir = os.path.join(ROOT, "projects")
    for name in sorted(os.listdir(pdir)):
        root = os.path.join(pdir, name)
        if not os.path.isdir(root):
            continue
        for rel in ("world.toml", "assets/world.toml", "world",
                    "assets/world"):
            p = os.path.join(root, rel)
            if os.path.exists(p):
                yield name, root, p
                break


def _sources(path):
    """Load a .mos file plus the lib modules it imports (transitively)."""
    seen, out, queue = set(), [], [path]
    import re
    while queue:
        p = queue.pop(0)
        p = os.path.abspath(p)
        if p in seen or not os.path.isfile(p):
            continue
        seen.add(p)
        with open(p, encoding="utf-8") as f:
            text = f.read()
        out.append((os.path.basename(p), text))
        for m in re.finditer(r'import\s+"([^"]+)"', text):
            name = m.group(1)
            if name.split(".")[0] in ("platform", "graphics", "native"):
                continue
            cand = os.path.join(ROOT, "lib", *name.split(".")) + ".mos"
            queue.append(cand)
    return out


def capture(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    entries = []

    def record(rel, text):
        path = os.path.join(out_dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        h = hashlib.sha1(text.encode("utf-8")).hexdigest()
        entries.append("%s  %s" % (h, rel))

    # --- scene transpiler -------------------------------------------------
    for name, root, wp in _worlds():
        try:
            world, base = mosaik_scenes.load_world(wp)
            record("scenes/%s.mos" % name, mosaik_scenes.transpile(world, base))
        except Exception as exc:
            record("scenes/%s.ERROR" % name,
                   "%s: %s" % (type(exc).__name__, exc))

    # --- rooms generator --------------------------------------------------
    for name, root, _wp in _worlds():
        dest = os.path.join(out_dir, "rooms", name + ".mos")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            got = mosaik_vm.generate_rooms(root, out_path=dest)
            if got is None:
                record("rooms/%s.SKIPPED" % name, "no rooms.mos generated\n")
                continue
            with open(dest, encoding="utf-8") as f:
                text = f.read()
            entries.append("%s  rooms/%s.mos"
                           % (hashlib.sha1(text.encode("utf-8")).hexdigest(),
                              name))
        except Exception as exc:
            record("rooms/%s.ERROR" % name,
                   "%s: %s" % (type(exc).__name__, exc))

    # --- the compiler (gbdk feature emitters) -----------------------------
    sdir = os.path.join(ROOT, "samples")
    consoles = sorted(PLATFORM_CAPS)
    for fn in sorted(os.listdir(sdir)):
        if not fn.endswith(".mos"):
            continue
        srcs = _sources(os.path.join(sdir, fn))
        for con in consoles:
            try:
                c = MosaikCompiler().compile_program(srcs, platform=con)
            except Exception as exc:
                c = "%s: %s" % (type(exc).__name__, exc)
            record("c/%s/%s.c" % (con, fn[:-4]), c)

    entries.sort()
    with open(os.path.join(out_dir, "MANIFEST.txt"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write("\n".join(entries) + "\n")
    print("captured %d artefacts -> %s" % (len(entries), out_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(capture(sys.argv[1] if len(sys.argv) > 1
                             else os.path.join(ROOT, "build", "_baseline")))
