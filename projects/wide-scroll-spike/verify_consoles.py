#!/usr/bin/env python3
"""Cross-console proof for the wide-scroll spike (Stage 1 of the large-levels-u16 plan).

The same engine.scroll-driven source streams a 512 px-wide map past the 256 px wrap on
every BACKGROUND console with a hardware tilemap. This runs each non-GB ROM through the
libretro harness and confirms the two MARKER columns (logical 40 + 55, both past the
wrap) reach the screen AFTER auto-scrolling to camx=320 -- they show as two full-height
~8 px bars darker than the ground. Without streaming, the boot fill only writes logical
columns 0..31 (no markers), so a marker bar on screen can only come from streaming.

GB/GBC are proven exactly (BG-map read) by verify.py -- run that for the GB family.

  python projects/wide-scroll-spike/verify_consoles.py

Needs the libretro cores (setup_tools.py). The Atari LYNX has NO hardware tilemap (its
bkg engine composites Suzy strips). For a WIDE streamed level the Lynx engine switches to
a per-COLUMN strip layout (one Suzy sprite per visible map column), so engine.scroll's
single-column set_tiles recomposites exactly one strip -- the column streaming now runs on
the Lynx too (use the Handy core, the HW proxy). The 2D ("2d") both-axes Lynx path is a
further follow-up (it needs column AND row strips, or a small 2D tile cache).
"""
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(os.path.dirname(ROOT))            # mosaik8/
HARNESS = os.path.join(ENGINE, "emu", "libretro", "run_lynx.py")
BUILD = os.path.join(ROOT, "build")

# label, rom suffix, libretro core (None = harness default), frames to settle
# camx=320, how many marker bars must be VISIBLE.
#
# Every console shows both markers except the SMS, which shows ONE. That is not
# a streaming failure: the SMS display is exactly as wide as the 32-column
# tilemap ring, so a streamed level deliberately blanks the leftmost column
# (`bkg.edge_mask`, VDP R0 bit 5) -- without it the wrap column is rewritten
# while the beam is still showing it and flickers every frame. The marker that
# lands under that mask is simply not drawn.
#
# Note what this suite used to report: before the mask, SMS passed with bars
# `[(83, 1152), (0, 1152)]` -- and the second, luma-0 one was the ARTEFACT, a
# black garbage column, not a marker. It was passing partly on the strength of
# the bug. The surviving `(83, ...)` bar is a real marker column past the 256 px
# wrap, which is what proves the streaming.
TILEMAP = [
    ("SMS",      "sms/wide-scroll-spike.sms",     "genesis_plus_gx",   340, 1),
    ("GameGear", "gamegear/wide-scroll-spike.gg",  "genesis_plus_gx",   340, 2),
    ("PCE",      "pce/wide-scroll-spike.pce",      "mednafen_pce_fast", 360, 2),
    ("NES",      "nes/wide-scroll-spike.nes",      "fceumm",            340, 2),
    # The Lynx now streams via the per-column Suzy strip engine (use Handy, the
    # default core = the HW proxy).
    ("Lynx",     "lynx/wide-scroll-spike.lnx",     None,                420, 2),
]


def _luma(r, g, b):
    return (r * 299 + g * 587 + b * 114) // 1000


def _marker_bars(rom, core, frames):
    """Run the harness and return the marker bars = distinct colours that are a
    single full-height ~8 px column (700..1400 px) and clearly darker than the
    brightest colour (the ground/paper)."""
    cmd = [sys.executable, HARNESS, rom, str(frames)]
    if core:
        cmd += ["--core", core]
    out = subprocess.run(cmd, capture_output=True, encoding="utf-8",
                         errors="replace", timeout=300).stdout
    cols = [(_luma(int(r), int(g), int(b)), int(n))
            for r, g, b, n in re.findall(
                r"\((\d+),\s*(\d+),\s*(\d+)\):\s*(\d+)\s*px", out)]
    if not cols:
        return None
    bright = max(l for l, _ in cols)
    return [(l, n) for l, n in cols if l < bright - 40 and 700 <= n <= 1400]


def main():
    ok = True
    for label, suffix, core, frames, want in TILEMAP:
        rom = os.path.join(BUILD, *suffix.split("/"))
        if not os.path.isfile(rom):
            print("  [SKIP] %-8s no ROM (build it first)" % label); continue
        bars = _marker_bars(rom, core, frames)
        passed = bars is not None and len(bars) >= want
        ok = ok and passed
        note = "  (per-column strip engine)" if label == "Lynx" else ""
        if label == "SMS":
            note = "  (1 expected: the left column is masked -- see TILEMAP)"
        print("  [%s] %-8s marker bars after scroll: %s%s"
              % ("PASS" if passed else "FAIL", label, bars, note))

    print("\nStage 1 PROVEN on every background console (incl. the Lynx)"
          if ok else "\nSOME CONSOLES FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
