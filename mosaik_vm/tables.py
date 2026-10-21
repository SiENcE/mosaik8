"""mosaik_vm.tables - DATA TABLES: rows x named columns of constants, read by
one opcode (TBL_GET, spec 6) instead of a code switch per row.

Authored in `scripts/tables.toml` (the scripts folder stays the one compiler
input):

    [[table]]
    name = "species"
    columns = ["el", "hp", "atk", "def"]
    cell = "u8"                     # or "i16"
    rows = [[0, 20, 7, 6], [0, 24, 6, 9]]
    row_names = ["sproutle", "mossback"]   # optional: editor + name pickers

Compiled into ONE byte blob, `TABLES` in `scripts.mos`: a 6-byte header per
table (cell offset u16, rows u16, row stride u8, cell width u8, little-endian),
then every table's cells, row-major. `scripts.table_get(t, row, col)` reads it
through the `assets.code_byte` seam, like STRINGS, so a banked build keeps the
blob in a DATA bank and only the reader is resident. A row outside the table
reads 0. A program whose scripts never read a table emits none of it.
"""
import os
import re

from .isa import VmError

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None

#: the whole blob is ONE const array, and an array never spans a ROM bank
TABLES_CAP = 16384
HEADER_BYTES = 6
CELL_TYPES = {"u8": (1, 0, 255), "i16": (2, -32768, 32767)}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def tables_path(scripts_dir):
    return os.path.join(scripts_dir, "tables.toml")


def load_tables(scripts_dir):
    """The validated `[[table]]` list of `scripts/tables.toml`, [] when the file
    is absent. Raises VmError on a malformed table."""
    path = tables_path(scripts_dir)
    if not os.path.isfile(path):
        return []
    if toml is None:
        raise VmError("the `toml` package is required")
    return validate_tables(toml.load(path).get("table", []))


def validate_tables(tables):
    """Check every table and return them as plain dicts (name, columns, cell,
    rows, row_names). The error names the table and what is wrong."""
    out, seen = [], set()
    if len(tables) > 255:
        raise VmError("%d data tables, the cap is 255 (u8 table operand)" % len(tables))
    for i, t in enumerate(tables):
        name = t.get("name", "")
        where = "table %r" % (name or "#%d" % i)
        if not _IDENT.match(str(name)):
            raise VmError("%s: the name must be an identifier" % where)
        if name in seen:
            raise VmError("%s: two tables share the name" % where)
        seen.add(name)
        cols = list(t.get("columns", []))
        if not cols:
            raise VmError("%s: no columns" % where)
        if len(set(cols)) != len(cols) or not all(_IDENT.match(str(c)) for c in cols):
            raise VmError("%s: column names must be distinct identifiers" % where)
        cell = t.get("cell", "u8")
        if cell not in CELL_TYPES:
            raise VmError("%s: cell is %r, not one of %s" % (where, cell, sorted(CELL_TYPES)))
        width, lo, hi = CELL_TYPES[cell]
        if len(cols) * width > 255:
            raise VmError("%s: a row is %d bytes, the cap is 255 (u8 row stride)"
                          % (where, len(cols) * width))
        rows = [list(r) for r in t.get("rows", [])]
        if len(rows) > 65535:
            raise VmError("%s: %d rows, the cap is 65535" % (where, len(rows)))
        for ri, r in enumerate(rows):
            if len(r) != len(cols):
                raise VmError("%s: row %d has %d cells, the table has %d columns"
                              % (where, ri, len(r), len(cols)))
            for ci, v in enumerate(r):
                if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                    raise VmError("%s: row %d column %r is %r, a %s holds %d..%d"
                                  % (where, ri, cols[ci], v, cell, lo, hi))
        names = list(t.get("row_names", []))
        if names and len(names) != len(rows):
            raise VmError("%s: %d row names for %d rows" % (where, len(names), len(rows)))
        out.append({"name": name, "columns": cols, "cell": cell, "rows": rows,
                    "row_names": names})
    return out


def encode_tables(tables):
    """The TABLES blob for a validated table list (bytes)."""
    head = bytearray()
    body = bytearray()
    base = HEADER_BYTES * len(tables)
    for t in tables:
        width = CELL_TYPES[t["cell"]][0]
        off = base + len(body)
        n = len(t["rows"])
        head += bytes((off & 0xFF, off >> 8, n & 0xFF, n >> 8,
                       len(t["columns"]) * width, width))
        for r in t["rows"]:
            for v in r:
                v &= 0xFFFF
                body += bytes((v & 0xFF,)) if width == 1 else bytes((v & 0xFF, v >> 8))
    blob = bytes(head + body)
    if len(blob) > TABLES_CAP:
        raise VmError("the data tables are %d bytes, over the %d-byte cap (one "
                      "const array, one ROM bank)" % (len(blob), TABLES_CAP))
    return blob


def table_cell(blob, t, row, col):
    """Read one cell out of an encoded blob, exactly as `scripts.table_get`
    does on the console (the reference VM and the tests share it): 0 for a
    row outside the table. The table and column are compile-time operands the
    compiler validated, so the console does not check them; here a table past
    the blob reads 0 rather than raising."""
    if t < 0 or HEADER_BYTES * (t + 1) > len(blob):
        return 0
    h = HEADER_BYTES * t
    off = blob[h] | blob[h + 1] << 8
    rows = blob[h + 2] | blob[h + 3] << 8
    stride, width = blob[h + 4], blob[h + 5]
    if row < 0 or row >= rows:
        return 0
    p = off + row * stride + col * width
    if width == 1:
        return blob[p]
    v = blob[p] | blob[p + 1] << 8
    return v - 0x10000 if v & 0x8000 else v
