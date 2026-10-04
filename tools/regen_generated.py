"""Regenerate EVERY generated module of every project, not just scripts.mos.

    python tools/regen_generated.py            # regenerate what is stale
    python tools/regen_generated.py --check    # report only, write nothing
    python tools/regen_generated.py --all      # rewrite every project
    python tools/regen_generated.py --only rooms,scenes

This REPLACES `tools/regen_scripts.py` (retired 2026-09-08), which swept
`src/scripts.mos` alone and stated the staleness argument for it. That
argument is true of every OTHER generated module too - there are nine of
them, owned by four different generators, and only one was ever swept.
Its one good idea, the severity split, is kept below in `_code()`.

    module           generator                            source

    scripts.mos      mosaik_vm (the bytecode compiler)   scripts/*.evt.toml
    scenes.mos       mosaik_scenes                       world.toml / world/
    rooms.mos        mosaik_vm.generate_rooms            world + studio.toml
    glue.mos         mosaik_vm.generate_glue             studio.toml
    clips.mos        mosaik_vm.generate_clips            studio.toml [animations]
    songs.mos        mosaik_vm.generate_songs            scripts/songs.toml
    instruments.mos  mosaik_vm.generate_instruments      scripts/instruments.toml
    emotes.mos       mosaik_vm.generate_emotes           studio.toml [emotes]
    hud.mos          mosaik_vm.generate_hud              studio.toml [hud]

(`uidata.mos` is deliberately absent: it is written by a project's OWN
`assets/gen.py`, not by a shared generator, so nothing here can refresh it.)

**scenes.mos and rooms.mos are a COUPLED PAIR, and that is why this tool
exists as one command rather than several.** Measured 2026-09-08: seven
samples had a stale `rooms.mos`, and regenerating it ALONE stopped them
compiling -

    rooms:75: module "scenes" has no module-level symbol "obj_scene_at"

because the current `rooms.mos` reads the scene tables through ACCESSORS
(which lets them bank - a const array read from another module is pinned
resident) while the stale `scenes.mos` only exported the raw arrays, and the
equally stale `rooms.mos` read those arrays directly. The two old files were
consistent WITH EACH OTHER, which is exactly what let the drift hide. Refresh
a project's whole set, never one module of it.

A project's module is regenerated only if it ALREADY EXISTS - the same rule
`generate_rooms` itself follows. This refreshes what a project has; it never
adds a module a project was built without.
"""
import argparse
import io
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


#: module file -> the `mosaik_vm.generate_*` that owns it. Each takes (root)
#: and writes in place, so they are driven uniformly.
VM_GENERATORS = {
    "rooms.mos": "generate_rooms",
    "glue.mos": "generate_glue",
    "clips.mos": "generate_clips",
    "songs.mos": "generate_songs",
    "instruments.mos": "generate_instruments",
    "emotes.mos": "generate_emotes",
    "hud.mos": "generate_hud",
}

#: (project, module) pairs the sweep must NOT touch, each with its reason.
#: This is for a project the GENERATOR no longer serves - not for one that is
#: merely out of date. (Empty today: the one entry it held named a project
#: that has left `projects/`. Keep the mechanism; an entry must name a
#: project that is actually here.)
SKIP = {}

ALL_MODULES = ["scripts", "scenes"] + [m[:-4] for m in VM_GENERATORS]


def _code(text):
    """The `const CODE` blob of a scripts module, whitespace-normalised.

    Ported from the retired `regen_scripts.py`, whose one genuinely useful
    idea this is: a stale `scripts.mos` is not equally serious both ways.
    If the BLOB differs the project is BROKEN on hardware (when the actor
    operands widened u8 -> u16 the old blobs kept the narrow encoding and
    every instruction after the first was misaligned - `vm-cutmusic` looked
    completely dead); if only the generated wrapper moved it RUNS, and
    regenerating merely brings its dialogue box to the current geometry.
    """
    m = re.search(r"const CODE: array\[u8, \d+\] = \[(.*?)\]", text, re.S)
    return re.sub(r"\s+", "", m.group(1)) if m else None


def _world_path(root):
    """A project's world source: a file or a split-per-resource directory."""
    for rel in ("world.toml", os.path.join("assets", "world.toml"),
                "world", os.path.join("assets", "world")):
        p = os.path.join(root, rel)
        if os.path.exists(p):
            return p
    return None


def _regen_scenes(root, out):
    import mosaik_scenes
    wp = _world_path(root)
    if not wp:
        return None
    world, base = mosaik_scenes.load_world(wp)
    return mosaik_scenes.core.transpile(world, base)


def _regen_scripts(root, out):
    sd = os.path.join(root, "scripts")
    if not os.path.isdir(sd) or not any(f.endswith(".evt.toml")
                                        for f in os.listdir(sd)):
        return None                      # a .v8s project: no event lists
    tmp = os.path.join(tempfile.gettempdir(),
                       "regen_%s.mos" % os.path.basename(root))
    r = subprocess.run([sys.executable, "-m", "mosaik_vm", sd, "-o", tmp],
                       capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or "").strip().splitlines()[-1:] or "?")
    return io.open(tmp, encoding="utf-8").read()


def _regen_vm(root, out, fn_name):
    """Drive one `mosaik_vm.generate_*`. They write IN PLACE, so the caller
    snapshots first and restores when only checking."""
    import mosaik_vm
    getattr(mosaik_vm, fn_name)(root)
    return io.open(out, encoding="utf-8").read() if os.path.exists(out) else None


def _new_text(root, out, module):
    if module == "scenes":
        return _regen_scenes(root, out)
    if module == "scripts":
        return _regen_scripts(root, out)
    return _regen_vm(root, out, VM_GENERATORS[module + ".mos"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="report what is stale; write nothing")
    ap.add_argument("--all", action="store_true",
                    help="rewrite every project, not just the stale ones")
    ap.add_argument("--only", default="",
                    help="comma-separated module list (default: all of %s)"
                         % ",".join(ALL_MODULES))
    args = ap.parse_args(argv)
    want = [m.strip() for m in args.only.split(",") if m.strip()] or ALL_MODULES
    bad = [m for m in want if m not in ALL_MODULES]
    if bad:
        print("unknown module(s): %s" % " ".join(bad))
        return 2

    pdir = os.path.join(ROOT, "projects")
    stale, failed, broken, held = {}, [], [], []
    for name in sorted(os.listdir(pdir)):
        root = os.path.join(pdir, name)
        src = os.path.join(root, "src")
        if not os.path.isdir(src):
            continue
        for module in want:
            out = os.path.join(src, module + ".mos")
            if not os.path.isfile(out):
                continue                 # refresh what exists; never add
            if (name, module) in SKIP:
                held.append((name, module))
                continue
            # SNAPSHOT THE BYTES, not the text. The vm generators write in
            # place, so a check run has to put the file back - and restoring
            # through a TEXT write that normalises newlines rewrites every
            # CRLF file the tool merely LOOKED at. (Measured: one --check
            # run left 40 projects "modified" with no content difference.)
            raw = io.open(out, "rb").read()
            # ...but COMPARE as text with universal newlines. The generators
            # emit LF; a checked-in file may be CRLF, and comparing the raw
            # decode against LF output reports every project stale (50 of 50
            # for scripts.mos - the first version of this fix did exactly
            # that, trading a churn bug for a wrong answer).
            before = io.open(out, encoding="utf-8").read()
            try:
                new = _new_text(root, out, module)
            except Exception as exc:                    # noqa: BLE001
                io.open(out, "wb").write(raw)
                failed.append((name, module, str(exc)[:90]))
                continue
            if new is None:
                io.open(out, "wb").write(raw)
                continue
            changed = new != before
            if changed:
                stale.setdefault(module, []).append(name)
                # scripts.mos alone carries a severity - see _code().
                if module == "scripts" and _code(before) != _code(new):
                    broken.append(name)
            if args.check or not (changed or args.all):
                io.open(out, "wb").write(raw)           # byte-exact restore
            else:
                # written as BYTES for the same reason: the generators
                # emit LF and nothing here should re-encode newlines.
                io.open(out, "wb").write(new.encode("utf-8"))

    verb = "would regenerate" if args.check else "regenerated"
    total = 0
    for module in want:
        names = stale.get(module, [])
        total += len(names)
        print("%-12s %s %2d: %s"
              % (module + ".mos", verb, len(names), " ".join(names) or "-"))
    print("\n%s %d module(s) across %d project(s)."
          % (verb, total, len({n for v in stale.values() for n in v})))
    if broken:
        print("  ...of which the BYTECODE BLOB moved - BROKEN on hardware,"
              " not merely old: %s" % " ".join(broken))
    for name, module in held:
        print("HELD BACK %s/%s.mos: %s" % (name, module, SKIP[(name, module)]))
    for name, module, err in failed:
        print("FAILED %s/%s: %s" % (name, module, err))
    if not args.check and total:
        print("\nA project's scenes.mos and rooms.mos are COUPLED - rebuild "
              "every project this touched, and run its verify.py:")
        print("    python mosaik8.py build projects/<name>")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
