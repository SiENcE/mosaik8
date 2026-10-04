"""Audit resident prelude helpers: which gbs_* functions in the MAIN TU are
only ever called from BANKED TUs (and never address-taken), so `static
inline` in the shared prelude would move them off bank 0 entirely.

Usage: python prelude_audit.py <build_dir> <rom_base> [resident_end_hex]

Reads <rom_base>-noi.noi (or .noi) for resident symbol spans, then scans the
main TU C for call sites inside resident code and every bank TU for call
sites there, and prints a verdict per helper.
"""

import re
import sys
from pathlib import Path

ABS_SYMS = {"_rROMB0", "_rROMB1", "_rRAMG", "_rROMB", ".init", "_shadow_OAM"}


def parse_noi(path):
    syms = []
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"DEF (_\S+) 0x([0-9A-Fa-f]+)", line)
        if m:
            val = int(m.group(2), 16)
            syms.append((m.group(1), val >> 16, val & 0xFFFF))
    return syms


def main():
    bdir = Path(sys.argv[1])
    base = sys.argv[2]
    res_end = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0x4000

    noi = bdir / f"{base}-noi.noi"
    if not noi.exists():
        noi = bdir / f"{base}.noi"
    syms = parse_noi(noi)
    res = sorted(
        [(a, n) for (n, b, a) in syms
         if b == 0 and 0x0200 <= a < res_end and n not in ABS_SYMS],
        key=lambda t: t[0],
    )
    spans = {}
    for i, (a, n) in enumerate(res):
        nxt = res[i + 1][0] if i + 1 < len(res) else res_end
        spans[n] = nxt - a

    main_c = (bdir / f"{base}.c").read_text(encoding="utf-8", errors="replace")
    bank_c = ""
    for f in bdir.glob(f"{base}_bank*.c"):
        bank_c += f.read_text(encoding="utf-8", errors="replace")

    helpers = sorted(n[1:] for n in spans if n.startswith("_gbs_"))
    print(f"{'helper':34s} {'span':>5s} {'mainTU':>6s} {'banks':>6s} {'&addr':>5s}")
    for h in helpers:
        # call sites: name followed by ( ... but not its own definition line
        call_re = re.compile(r"(?<![A-Za-z0-9_])" + h + r"\s*\(")
        def_re = re.compile(
            r"(?:void|uint8_t|uint16_t|int8_t|int16_t)\s+" + h + r"\s*\("
        )
        main_calls = len(call_re.findall(main_c)) - len(def_re.findall(main_c))
        bank_calls = len(call_re.findall(bank_c)) - len(def_re.findall(bank_c))
        addr = len(re.findall(r"&\s*" + h + r"(?![A-Za-z0-9_(])", main_c + bank_c))
        addr += len(re.findall(r"(?<![A-Za-z0-9_&])" + h + r"\s*[,;)\]]", main_c + bank_c))
        span = spans.get("_" + h, 0)
        flag = "  <-- CANDIDATE" if main_calls <= 0 and addr == 0 and bank_calls > 0 else ""
        print(f"{h:34s} {span:5d} {main_calls:6d} {bank_calls:6d} {addr:5d}{flag}")


if __name__ == "__main__":
    main()
