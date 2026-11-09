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

ARRAYS (E2, 2026-11-09) live in the same file, with or without a pinned
order:

    [arrays]
    bag = 24        # 24 consecutive cells, read / written by index

An array is allocated like a variable (its first use, or its place in
`cells`) but takes `length` consecutive cells; `array_get` / `array_set`
(GET_IND / SET_IND) address it. A file that holds ONLY `[arrays]` is not a
pin: the layout is PINNED when it has `cells` or `save_version` (`pinned`
below), and only a pinned layout seeds the compiler and the save id.

The heap's size is the project's `[build] vm_heap` (default 128, at most
256; `project_heap_cap`).
"""
import hashlib
import os
import re

from .isa import VmError

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None

HEAP_FILE = "heap.toml"

#: The interpreter heap's size without `[build] vm_heap`, and its ceiling
#: (heap operands are one byte). Kept in lockstep with
#: mosaik.compiler.POOL_DEFINE_DEFAULTS["VM_HEAP_CELLS"].
DEFAULT_HEAP = 128
MAX_HEAP = 256
#: An array's length travels as a u8 operand.
MAX_ARRAY = 255


def project_heap_cap(root):
    """`[build] vm_heap` of the project at `root` (default 128). Raises
    VmError on a value outside 1..256."""
    path = os.path.join(root, "mosaik.toml")
    if not os.path.isfile(path) or toml is None:
        return DEFAULT_HEAP
    try:
        value = (toml.load(path).get("build", {}) or {}).get("vm_heap")
    except Exception:
        return DEFAULT_HEAP
    return check_heap_cap(value)


def check_heap_cap(value):
    """A `vm_heap` value as an int (None = the default), or VmError."""
    if value is None:
        return DEFAULT_HEAP
    if isinstance(value, bool) or not isinstance(value, int) \
            or not 1 <= value <= MAX_HEAP:
        raise VmError("[build] vm_heap must be a whole number 1..%d, got %r"
                      % (MAX_HEAP, value))
    return value


def heap_path(scripts_dir):
    return os.path.join(scripts_dir, HEAP_FILE)


def load_heap(scripts_dir):
    """The validated layout of `scripts/heap.toml`, or None when the file is
    absent. Raises VmError on a malformed file. Checked against the
    project's `[build] vm_heap` (the scripts folder's parent)."""
    path = heap_path(scripts_dir)
    if not os.path.isfile(path):
        return None
    if toml is None:
        raise VmError("the `toml` package is required")
    root = os.path.dirname(os.path.abspath(scripts_dir))
    return validate_heap(toml.load(path), project_heap_cap(root))


def validate_heap(doc, cap=None):
    """Check a parsed heap file and return {"cells": [...], "save_version": n,
    "arrays": {name: length}, "pinned": bool}. `cap` is the heap size the
    cells must fit (default 128). The error says what is wrong and where."""
    cap = DEFAULT_HEAP if cap is None else cap
    if not isinstance(doc, dict):
        raise VmError("heap.toml: not a table")
    arrays = doc.get("arrays", {})
    if not isinstance(arrays, dict):
        raise VmError("heap.toml: `[arrays]` must be a table of name = length")
    for name, n in arrays.items():
        if not _IDENT.match(name):
            raise VmError("heap.toml: array name %r is not an identifier" % name)
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_ARRAY:
            raise VmError("heap.toml: array %r needs a length 1..%d, got %r"
                          % (name, MAX_ARRAY, n))
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
    used = sum(arrays.get(c, 1) for c in cells)
    if used > cap:
        raise VmError("heap.toml: the listed cells take %d, the heap holds %d"
                      " ([build] vm_heap)" % (used, cap))
    ver = doc.get("save_version", 1)
    if isinstance(ver, bool) or not isinstance(ver, int) or ver < 0:
        raise VmError("heap.toml: `save_version` must be a whole number >= 0, "
                      "got %r" % (ver,))
    return {"cells": list(cells), "save_version": ver, "arrays": dict(arrays),
            "pinned": "cells" in doc or "save_version" in doc}


def is_pinned(layout):
    """True when a layout pins the heap (seeds the compiler, sets the save id)."""
    return bool(layout) and layout.get("pinned", True)


def heap_seed(layout):
    """The compiler's `seed_vars` for a layout (None when it pins nothing)."""
    return list(layout["cells"]) if is_pinned(layout) else None


def heap_arrays(layout):
    """The layout's arrays {name: length} ({} without a file)."""
    return dict(layout.get("arrays", {})) if layout else {}


def append_cells(layout, names):
    """A NEW layout with every name of `names` the layout lacks appended, in
    the given order (the only edit a tool makes to the file)."""
    cells = list(layout["cells"])
    have = set(cells)
    for n in names:
        if n not in have:
            cells.append(n)
            have.add(n)
    return {"cells": cells, "save_version": layout["save_version"],
            "arrays": dict(layout.get("arrays", {})), "pinned": True}


def format_heap(layout):
    """The file text for a layout: a header saying the rule, then the two
    keys, one cell per line so an append is a one-line diff, then the
    arrays (if any). A layout that pins nothing writes the arrays alone."""
    arrays = layout.get("arrays") or {}
    tail = []
    if arrays:
        tail = ["", "# ARRAYS (E2): name = length, consecutive cells read and",
                "# written by index (array_get / array_set).", "[arrays]"]
        tail += ["%s = %d" % (n, arrays[n]) for n in arrays]
    if not is_pinned(layout):
        return "\n".join(tail[1:]) + "\n"
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
    return "\n".join(lines + tail) + "\n"


def layout_build_id(layout):
    """The save's check-byte seed for a pinned layout: an 8-bit digest of
    `save_version` alone, never 0. The cells and the scenes are left out ON
    PURPOSE: both are append-only, and a digest of a list cannot tell an
    append (keeps every save) from a reorder (the one edit the file's header
    forbids). The check byte still XORs every saved byte, so a corrupted cart
    still reads as "no save"."""
    blob = ("heap-layout %d" % layout["save_version"]).encode("utf-8")
    return (hashlib.sha1(blob).digest()[0] % 255) + 1
