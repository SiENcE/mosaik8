"""Relink a built GBDK project with -Wl-j and survey the RESIDENT symbols.

Usage:
    python noi_resident.py <SYM.noi>

Reads a `-Wl-j` symbol file (`tools/relink_noi.py` or
`tools/framebudget/build_noi.py` writes one) and prints every resident
(bank 0, below 0x4000) symbol with its gap-to-next size, largest first.
Sizes are approximate (symbols reorder between builds); the reliable read is
which symbols are present at all.
"""

import re
import subprocess
import sys
from pathlib import Path


def parse_noi(path):
    syms = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"DEF (_\S+) 0x([0-9A-Fa-f]+)", line)
        if not m:
            continue
        name = m.group(1)
        val = int(m.group(2), 16)
        bank, addr = val >> 16, val & 0xFFFF
        syms.append((name, bank, addr))
    return syms


def main():
    noi = Path(sys.argv[1])
    syms = parse_noi(noi)
    # Resident: bank 0 and address below 0x4000 (code/const), skip headers.
    res = sorted(
        [(a, n) for (n, b, a) in syms if b == 0 and 0x0200 <= a < 0x4000],
        key=lambda t: t[0],
    )
    out = []
    for i, (a, n) in enumerate(res):
        nxt = res[i + 1][0] if i + 1 < len(res) else 0x4000
        out.append((nxt - a, a, n))
    total = sum(s for s, _, _ in out)
    print(f"{len(out)} resident symbols, {total} B spanned")
    for size, addr, name in sorted(out, reverse=True):
        print(f"{size:6d}  0x{addr:04X}  {name}")


if __name__ == "__main__":
    main()
