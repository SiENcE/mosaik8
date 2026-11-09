#!/usr/bin/env python3
"""E10: a PINNED heap layout (`scripts/heap.toml`) keeps a save's cells put.

  * no file = nothing changes (the loader returns None, every compile path is
    the unseeded one);
  * a layout listing today's order compiles BYTE-IDENTICALLY (scripts.mos
    text), in the one-program and the per-console bucketed path alike;
  * a new variable in an EARLY script moves every later cell without the
    file and none with it; the new one lands after the list;
  * a listed name no script uses keeps its cell (a hole, never a shift);
  * the save id (rooms.mos `sram.set_build`) follows `save_version` when the
    file exists: unchanged by a script edit or an appended cell, changed by a
    version bump; without the file it is the scripts digest it always was;
  * a malformed file is a hard error, in the compiler AND in generate_rooms
    (which used to swallow its own read errors into a save-less world).

Pure Python (the compiler + the room generator); no toolchain needed."""
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mosaik_vm as m  # noqa: E402
from mosaik_vm.heap import (  # noqa: E402
    append_cells, format_heap, heap_path, layout_build_id, load_heap, validate_heap)
from mosaik_vm.loader import compile_path, scripts_module_path  # noqa: E402
from mosaik_vm.rooms.generate import generate_rooms  # noqa: E402

FAILS = []
SAMPLE = os.path.join(ROOT, "projects", "vm-saveslots")


def check(cond, msg):
    print(("  ok: " if cond else "  FAIL: ") + msg)
    if not cond:
        FAILS.append(msg)


def raises(fn, needle):
    try:
        fn()
    except m.VmError as e:
        return needle in str(e)
    return False


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _set_build(root):
    out = os.path.join(root, "rooms_probe.mos")
    generate_rooms(root, out_path=out)
    with open(out, encoding="utf-8") as f:
        got = re.search(r"sram\.set_build\((\d+)\)", f.read())
    return int(got.group(1)) if got else None


def _prepend_early_var(scripts_dir):
    """A new variable in the FIRST script (`main`), ahead of every other use."""
    p = os.path.join(scripts_dir, "main.evt.toml")
    with open(p, encoding="utf-8") as f:
        text = f.read()
    head = ('[[script]]\nname = "main"\n[[script.events]]\nevent = "set_var"\n'
            'var = "zz_early"\nvalue = 7\n\n')
    assert text.startswith('[[script]]\nname = "main"\n')
    _write(p, head + text[len('[[script]]\nname = "main"\n'):])


def unit_checks():
    print("[heap.toml: the file format]")
    lay = validate_heap({"cells": ["a", "b"], "save_version": 3})
    check(lay == {"cells": ["a", "b"], "save_version": 3, "arrays": {}, "pinned": True},
          "a valid layout reads back")
    check(validate_heap({"cells": ["a"]})["save_version"] == 1,
          "save_version defaults to 1")
    check(raises(lambda: validate_heap({"cells": ["a", "b", "a"]}), "listed twice"),
          "a name listed twice is refused, naming it")
    check(raises(lambda: validate_heap({"cells": ["v%d" % i for i in range(
        m.Compiler.HEAP_CAP + 1)]}), "the heap holds"),
          "more cells than the heap holds is refused")
    check(raises(lambda: validate_heap({"cells": ["a"], "save_version": -1}),
                 "save_version"), "a negative save_version is refused")
    check(raises(lambda: validate_heap({"cells": ["a", 3]}), "variable names"),
          "a non-name cell is refused")
    import toml
    lay2 = append_cells(lay, ["b", "c", "a", "d"])
    check(lay2["cells"] == ["a", "b", "c", "d"] and lay["cells"] == ["a", "b"],
          "append_cells appends only the missing names, in order, on a copy")
    check(validate_heap(toml.loads(format_heap(lay2))) == lay2,
          "format_heap round-trips through a TOML reader")
    check(layout_build_id(lay) == layout_build_id(lay2),
          "the layout id ignores the cells (an append keeps every save)")
    check(layout_build_id(lay) != layout_build_id({"cells": [], "save_version": 4}),
          "the layout id changes with save_version")
    check(all(1 <= layout_build_id({"cells": [], "save_version": v}) <= 255
              for v in range(64)), "the layout id is 1..255 (never 0)")


def project_checks(tmp):
    print("[heap.toml on a copy of projects/vm-saveslots]")
    root = os.path.join(tmp, "vm-saveslots")
    shutil.copytree(SAMPLE, root, ignore=shutil.ignore_patterns("build"))
    sd = os.path.join(root, "scripts")
    check(load_heap(sd) is None, "no heap.toml -> load_heap is None")
    base_vars = dict(compile_path(sd).variables)
    base_mos = scripts_module_path(sd)
    base_id = _set_build(root)
    check(base_id is not None and len(base_vars) >= 1,
          "the sample saves and has variables (setup)")
    order = sorted(base_vars, key=base_vars.get)

    # A layout of TODAY's order: byte-identical, both compile paths.
    _write(heap_path(sd), format_heap({"cells": order, "save_version": 1}))
    check(scripts_module_path(sd) == base_mos,
          "a layout of today's order emits a byte-identical scripts.mos")
    check(dict(compile_path(sd).variables) == base_vars,
          "...and the same variable indices")
    pinned_id = _set_build(root)
    check(pinned_id == layout_build_id(load_heap(sd)),
          "with the file, set_build is the layout id")

    # An early new variable: pinned cells stay, the new one goes after.
    _prepend_early_var(sd)
    got = dict(compile_path(sd).variables)
    check(all(got[n] == base_vars[n] for n in order),
          "a new early variable leaves every pinned cell where it was")
    check(got["zz_early"] == len(order), "the unlisted variable lands after the list")
    check(scripts_module_path(sd) == compile_path(sd).to_scripts_mos(),
          "the CLI / regen module path seeds the same layout as compile_path")
    check(_set_build(root) == pinned_id,
          "a script edit keeps the save id under a pinned layout")
    os.remove(heap_path(sd))
    loose = dict(compile_path(sd).variables)
    check(loose["zz_early"] == 0 and any(loose[n] != base_vars[n] for n in order),
          "(control) without the file the same edit MOVES the old cells")
    check(_set_build(root) != base_id,
          "(control) without the file a script edit changes the save id")

    # Append + a hole + a version bump.
    lay = append_cells({"cells": ["retired"] + order, "save_version": 1}, ["zz_early"])
    _write(heap_path(sd), format_heap(lay))
    got = dict(compile_path(sd).variables)
    check(got.get("retired") == 0 and all(got[n] == i + 1 for i, n in enumerate(order)),
          "a listed name no script uses keeps its cell (a hole, never a shift)")
    check(_set_build(root) == pinned_id, "an appended cell keeps the save id")
    _write(heap_path(sd), format_heap(dict(lay, save_version=2)))
    check(_set_build(root) != pinned_id, "bumping save_version changes the save id")

    # The bucketed (per-console) path seeds the pinned layout too.
    import mosaik_vm.compiler as mc
    scripts = m.load_scripts(sd)
    scripts[0] = dict(scripts[0], platforms=["gameboy"])
    seen = []
    real = mc.emit_scripts_module_bucketed

    def spy(cond_progs, module="scripts"):
        seen.extend(p for _ids, p in cond_progs)
        return real(cond_progs, module)
    mc.emit_scripts_module_bucketed = spy
    try:
        m.compiler.emit_scripts_module(scripts, heap=load_heap(sd))
    finally:
        mc.emit_scripts_module_bucketed = real
    check(len(seen) >= 2, "the per-console bucketed path is taken (setup)")
    check(all(p.variables.get("retired") == 0
              and all(p.variables[n] == i + 1 for i, n in enumerate(order))
              for p in seen),
          "every console's bucket keeps the pinned cells")

    # Malformed = a hard error everywhere (never a silently save-less world).
    _write(heap_path(sd), 'cells = ["a", "a"]\n')
    check(raises(lambda: compile_path(sd), "listed twice"),
          "a malformed heap.toml is a compile error")
    check(raises(lambda: _set_build(root), "listed twice"),
          "...and a generate_rooms error, not a swallowed one")


def main():
    unit_checks()
    tmp = tempfile.mkdtemp()
    try:
        project_checks(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILS:
        print("\nheap layout checks FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("\nAll heap layout checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
