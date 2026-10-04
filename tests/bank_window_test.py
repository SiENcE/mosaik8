#!/usr/bin/env python3
import os, shutil, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""The banked-build resident/bank-window overlap guard (mosaik8.py).

The switchable-bank window on the sm83/z80 GBDK consoles starts at 0x4000
(MBC5 ROMX / Sega-mapper frame 1; makebin lays bank N at file offset
N*0x4000), and sdcc's linker does NOT stop a banked build's RESIDENT image
from growing past it -- bank 1 then silently overwrites the overflowing tail
(often the boot INITIALIZER) in the ROM file, so the ROM boots to a black
screen with no diagnostic. That was the REAL cause of "importing
graphics.palette blanks the SMS boot" in bigworld-paint: the palette prelude pushed the resident
image to 0x414E and bank 1 clobbered the initializer at 0x4101.

mosaik8.py now links banked builds on those consoles with a linker map
(-Wl-m), computes the resident end (gbdk_resident_end), and FAILS the build
loudly (removing the corrupt ROM) when it crosses 0x4000. This test covers
the map parser and both end-to-end outcomes (needs the GBDK toolchain).
"""

from mosaik8 import gbdk_resident_end, GBDK_BANK_WINDOW_BASE, gbdk_available


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


FAKE_MAP = """
_CODE                  00000100    00003350 =       13136. bytes (REL,CON)
_HOME                  00003450    00000CB1 =        3249. bytes (REL,CON)
_INITIALIZER           00004101    0000003C =          60. bytes (REL,CON)
_GSINIT                0000413D    00000010 =          16. bytes (REL,CON)
_GSFINAL               0000414D    00000001 =           1. bytes (REL,CON)
_DATA                  0000C0C0    0000001A =          26. bytes (REL,CON)
_INITIALIZED           0000C0DA    0000003C =          60. bytes (REL,CON)
_CODE_1                00014000    00000840 =        2112. bytes (REL,CON)
_CODE_2                00024000    00003DE0 =       15840. bytes (REL,CON)
"""

FAKE_MAP_OK = """
_CODE                  00000100    00003350 =       13136. bytes (REL,CON)
_HOME                  00003450    00000800 =        2048. bytes (REL,CON)
_DATA                  0000C0C0    0000001A =          26. bytes (REL,CON)
_CODE_1                00014000    00000840 =        2112. bytes (REL,CON)
"""


def banked_source(resident_bytes):
    """A GB program with a bank(1) function plus `resident_bytes` of resident
    const data referenced from main (so it can't be dropped)."""
    data = ", ".join("1" for _ in range(resident_bytes))
    return f'''
module "main" {{
    import "platform.video"
    const BIG: array[u8, {resident_bytes}] = [{data}]
    bank(1) function far_fn() -> u8 {{
        return 7
    }}
    function main() {{
        var v: u8 = BIG[3] + far_fn()
        if v == 0 {{ video.disable_lcd() }}
        video.enable_lcd()
        loop {{ video.wait_vblank() }}
    }}
    export main
}}
'''


def build(src_text, workdir):
    src = os.path.join(workdir, "prog.mos")
    with open(src, "w", encoding="utf-8") as f:
        f.write(src_text.strip())
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"),
                        "build", "--platform", "gameboy", src],
                       capture_output=True, text=True, cwd=ROOT,
                       encoding="utf-8", errors="replace")
    out = r.stdout + r.stderr
    rom = os.path.join(workdir, "build", "gameboy", "prog.gb")
    return r.returncode, out, rom


def main():
    print("Banked-build resident/bank-window overlap guard")
    print("=" * 50)
    ok = True

    # --- The map parser (bigworld-paint's actual failing layout). ---
    end = gbdk_resident_end(FAKE_MAP)
    ok &= check("parser: resident end = 0x414E (INITIALIZER tail counted)",
                end == 0x414E)
    ok &= check("parser: banked (_CODE_N) + RAM areas excluded",
                gbdk_resident_end(FAKE_MAP_OK) == 0x3450 + 0x800)
    ok &= check("threshold: 0x414E violates the 0x4000 window",
                end > GBDK_BANK_WINDOW_BASE)

    # --- End-to-end (needs GBDK; skip gracefully when absent). ---
    # The builder's own probe: a directory named `gbdk` is not a toolchain,
    # and a Windows one on Linux made this suite link-fail instead of skip.
    if not gbdk_available():
        print("  [SKIP] GBDK not installed; end-to-end link checks skipped")
        print("=" * 50)
        print("PASSED" if ok else "FAILED")
        return 0 if ok else 1

    tmp = tempfile.mkdtemp(prefix="bankwin_")
    try:
        # A small banked build stays under the window and links fine.
        rc, out, rom = build(banked_source(64), tmp)
        ok &= check("e2e: small banked build links (guard passes)",
                    rc == 0 and os.path.exists(rom))

        # ~17.5 KB of resident const pushes the resident image past 0x4000:
        # the guard must FAIL the build and remove the corrupt ROM.
        rc, out, rom = build(banked_source(18000), tmp)
        ok &= check("e2e: overflowing banked build FAILS loudly",
                    rc != 0 and "resident image ends at" in out)
        ok &= check("e2e: the corrupt ROM is removed",
                    not os.path.exists(rom))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 50)
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
