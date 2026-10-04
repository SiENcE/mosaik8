"""Relink a built GBDK project with `-Wl-j` so a probe has SYMBOLS.

The ordinary build does not emit a `.noi`, and without one every memory read
in a probe is a guess. This re-runs the same `lcc` line the build printed,
with `-Wl-j` added and `-o` retargeted to `<rom>-noi.<ext>`, so the real ROM
is untouched and the symbol file sits beside it.

Usage:  python relink_noi.py <build_dir> <rom_base> [--gbc]
"""
import os
import subprocess
import sys
from pathlib import Path


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    bdir = Path(sys.argv[1])
    base = sys.argv[2]
    gbc = "--gbc" in sys.argv
    # `--rst`: assembler listings relocated to ABSOLUTE addresses (.rst), one
    # per TU - what tools/framebudget/addr_profile.py maps hit addresses to
    # source lines with. Off by default: the listings are large.
    rst = "--rst" in sys.argv
    ext = "gbc" if gbc else "gb"
    root = Path(__file__).resolve().parent.parent          # mosaik8/
    gbdk = os.environ.get("GBDK_HOME") or str(root / "gbdk")
    # `subprocess` does not add PATHEXT, so on Windows the bare name is
    # "file not found" (2026-09-16).
    lcc = str(Path(gbdk) / "bin" / ("lcc.exe" if os.name == "nt" else "lcc"))

    # The home TU first, then bank TUs in numeric order, then any extras.
    home = bdir / f"{base}.c"
    banks = sorted(bdir.glob(f"{base}_bank*.c"),
                   key=lambda p: int(p.stem.split("bank")[-1]))
    extra = sorted(p for p in bdir.glob("*.c")
                   if p != home and p not in banks)
    srcs = [home, *banks, *extra]

    flags = ["-msm83:gb" if not gbc else "-msm83:gb",
             "-Wm-yt0x1B", "-Wm-yo64", "-Wm-ya1",
             f"-I{root / 'vendor' / 'hugedriver'}",
             "-Wl-m", "-Wl-j", f"-I{Path(gbdk) / 'include'}"]
    if rst:
        flags += ["-Wa-l", "-Wl-u"]
    huge = root / "vendor" / "hugedriver" / "hUGEDriver_gb.o"
    out = bdir / f"{base}-noi.{ext}"
    cmd = [lcc, *flags, "-o", str(out), *[str(s) for s in srcs]]
    if huge.exists() and any("songs_huge" in s.name for s in srcs):
        cmd.append(str(huge))
    print(" ".join(cmd[:6]), "...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-4000:])
        print(r.stderr[-4000:])
        return 1
    noi = out.with_suffix(".noi")
    print("OK ->", noi, noi.exists())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
