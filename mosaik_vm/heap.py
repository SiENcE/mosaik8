"""mosaik_vm.heap - a PINNED heap layout for saves (E10).

A save (`vm.sram`) stores the heap by CELL INDEX, and the compiler numbers a
variable the first time it meets it (`Compiler.var_index`), so a new variable
in an early script moves every later one and an old save restores into the
wrong cells. `scripts/heap.toml` pins the order:

    save_version = 1
    cells = ["pa_sp", "pa_lv", "pa_hp", ...]

- Every listed name gets exactly its list position as its cell, whether or
  not a script still uses it (a retired variable leaves a HOLE, never a
  shift). A variable the scripts use but the file does not list is appended
  after the list, in compile order, as before.
- The only safe edits are APPENDING a name and renaming one in place.
- With the file, the save's check byte is seeded from `save_version`, not
  from the scripts: an edited script keeps every save, an appended cell
  keeps every save (an old save has 0 there, which is what a new variable
  starts as), and bumping `save_version` voids them on purpose. Scenes are
  append-only by the same convention (a save stores the room INDEX).

No file = nothing changes anywhere (byte-identical).
"""
import hashlib
import os

from .isa import VmError

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None

HEAP_FILE = "heap.toml"


def heap_path(scripts_dir):
    return os.path.join(scripts_dir, HEAP_FILE)


def load_heap(scripts_dir):
    """The validated layout of `scripts/heap.toml`, or None when the file is
    absent. Raises VmError on a malformed file."""
    path = heap_path(scripts_dir)
    if not os.path.isfile(path):
        return None
    if toml is None:
        raise VmError("the `toml` package is required")
    return validate_heap(toml.load(path))


def validate_heap(doc):
    """Check a parsed heap file and return {"cells": [...], "save_version": n}.
    The error says what is wrong and where."""
    from .compiler import Compiler
    if not isinstance(doc, dict):
        raise VmError("heap.toml: not a table")
    cells = doc.get("cells", [])
    if not isinstance(cells, list) or not all(
            isinstance(c, str) and c for c in cells):
        raise VmError("heap.toml: `cells` must be a list of variable names")
    seen = set()
    for i, c in enumerate(cells):
        if c in seen:
            raise VmError("heap.toml: %r is listed twice (cell %d and cell %d)"
                          % (c, cells.index(c), i))
        seen.add(c)
    if len(cells) > Compiler.HEAP_CAP:
        raise VmError("heap.toml: %d cells listed, the heap holds %d"
                      % (len(cells), Compiler.HEAP_CAP))
    ver = doc.get("save_version", 1)
    if isinstance(ver, bool) or not isinstance(ver, int) or ver < 0:
        raise VmError("heap.toml: `save_version` must be a whole number >= 0, "
                      "got %r" % (ver,))
    return {"cells": list(cells), "save_version": ver}


def heap_seed(layout):
    """The compiler's `seed_vars` for a layout (None when there is none)."""
    return list(layout["cells"]) if layout else None


def append_cells(layout, names):
    """A NEW layout with every name of `names` the layout lacks appended, in
    the given order (the only edit a tool makes to the file)."""
    cells = list(layout["cells"])
    have = set(cells)
    for n in names:
        if n not in have:
            cells.append(n)
            have.add(n)
    return {"cells": cells, "save_version": layout["save_version"]}


def format_heap(layout):
    """The file text for a layout: a header saying the rule, then the two
    keys, one cell per line so an append is a one-line diff."""
    lines = [
        "# The PINNED heap layout (E10): each variable's save cell is its",
        "# position in `cells`. Append new names at the END or rename one in",
        "# place; never reorder or delete (a retired name keeps its cell).",
        "# Bump save_version to make every existing save read as 'no save'.",
        "save_version = %d" % layout["save_version"],
        "cells = [",
    ]
    lines += ['    "%s",' % c.replace("\\", "\\\\").replace('"', '\\"')
              for c in layout["cells"]]
    lines.append("]")
    return "\n".join(lines) + "\n"


def layout_build_id(layout):
    """The save's check-byte seed for a pinned layout: an 8-bit digest of
    `save_version` alone, never 0. The cells and the scenes are left out ON
    PURPOSE: both are append-only, and a digest of a list cannot tell an
    append (keeps every save) from a reorder (the one edit the file's header
    forbids). The check byte still XORs every saved byte, so a corrupted cart
    still reads as "no save"."""
    blob = ("heap-layout %d" % layout["save_version"]).encode("utf-8")
    return (hashlib.sha1(blob).digest()[0] % 255) + 1
