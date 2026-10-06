#!/usr/bin/env python3
"""Which part of a room load settles LAST, and is any of it VISIBLE?

The report: entering a small interior room the fade-in shows the room half-wrong
for several frames - part of it in what looks like another room's art - before
it settles. The fade exists to HIDE the load; if it reveals it instead, every
room transition in every project shows it.

This walks a room change frame by frame and records, per LCD frame:

  * the SCROLL the frame was drawn with (SCX/SCY),
  * a digest of the TILEMAP (0x9800) and of the background TILE DATA
    (0x8800..0x97FF), so "which settles last" has an answer,
  * whether the palettes are BLACK (the fade's whole job while loading): on
    the CGB the DMG registers are ignored, so this reads the real hardware
    palette back through BCPS/BCPD rather than BGP,
  * the screen digest, so a frame that is both visible AND unsettled can be
    named.

Read it against the SETTLED state at the end - the culprit is whatever is
still changing on the first frame that is not black.

Usage: room_reveal_probe.py --rom ROM.gb --noi SYM.noi [--from 0] [--to 1]
       [--at 40,72] [--frames 40] [--cgb]
"""
import hashlib
import re
import sys

arg = lambda k, d: (sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d)
ROM = arg("--rom", None)
NOI = arg("--noi", None)
if not (ROM and NOI):
    print(__doc__)
    print("!! --rom ROM.gb and --noi SYM.noi are required (a -Wl-j pair: "
          "tools/framebudget/build_noi.py <project>)")
    sys.exit(2)
FROM = int(arg("--from", "0"))
AT = [int(v) for v in arg("--at", "40,72").split(",")]
TO = int(arg("--to", "1"))
FRAMES = int(arg("--frames", "40"))
CGB = "--cgb" in sys.argv or ROM.endswith(".gbc")

s = {}
for line in open(NOI, encoding="utf-8", errors="replace"):
    m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
    if m:
        s[m.group(1)] = int(m.group(2), 16)

from pyboy import PyBoy

pb = PyBoy(ROM, window="null", cgb=True if CGB else None)
g = lambda n: s[n] & 0xFFFF


def w16(a, v):
    pb.memory[a], pb.memory[a + 1] = v & 0xFF, (v >> 8) & 0xFF


def goto(room, x, y, ticks=300):
    pb.memory[g("_vm_core_pend_code")] = 2
    pb.memory[g("_vm_core_pend_a")] = room
    w16(g("_vm_core_pend_b"), x)
    w16(g("_vm_core_pend_c"), y)
    for _ in range(ticks):
        pb.tick()


def dig(b):
    return hashlib.md5(bytes(b)).hexdigest()[:6]


def bkg_pal_black():
    """Is background palette 0 entry 1..3 all black RIGHT NOW?

    On a CGB the DMG registers are ignored, so BGP says nothing - read CRAM.
    Entry 0 is excluded: a room whose colour 0 is black tells us nothing.
    """
    try:
        pb.memory[0xFF68] = 0x80 | 2       # auto-increment from entry 1
        vals = [pb.memory[0xFF69] for _ in range(6)]
        return not any(vals)
    except Exception:
        return None


def snap():
    return {
        "scx": pb.memory[0xFF43], "scy": pb.memory[0xFF42],
        "lcdc": pb.memory[0xFF40], "bgp": pb.memory[0xFF47],
        "map": dig(pb.memory[0x9800:0x9C00]),
        "tiles": dig(pb.memory[0x8800:0x9800]),
        # CGB per-cell palette ATTRIBUTES live in VRAM bank 1 at the same
        # address; they are as much "the room's picture" as the tilemap is.
        "attr": dig(pb.memory[1, 0x9800:0x9C00]) if CGB else "-",
        "black": bkg_pal_black(),
        "screen": dig(pb.screen.ndarray.tobytes()),
    }


def main():
    for _ in range(120):
        pb.tick()
    goto(FROM, AT[0], AT[1])
    print("settled in room %d: %s" % (FROM, snap()))
    # the room change itself, ONE LCD frame at a time
    pb.memory[g("_vm_core_pend_code")] = 2
    pb.memory[g("_vm_core_pend_a")] = TO
    w16(g("_vm_core_pend_b"), 40)
    w16(g("_vm_core_pend_c"), 72)
    rows = []
    for _ in range(FRAMES):
        pb.tick()
        rows.append(snap())
    for _ in range(200):
        pb.tick()
    final = snap()
    print("\nSETTLED in room %d: map %s tiles %s scx %d scy %d screen %s"
          % (TO, final["map"], final["tiles"], final["scx"], final["scy"],
             final["screen"]))
    print("\n  f  scx scy lcdc bgp  black  map     tiles   screen   "
          "| map= tiles= scroll=")
    for i, r in enumerate(rows):
        print("  %2d %4d %3d  %02X  %02X  %-5s  %-7s %-7s %-7s %-7s  |  %-5s %-5s %-5s %-5s"
              % (i, r["scx"], r["scy"], r["lcdc"], r["bgp"], r["black"],
                 r["map"], r["tiles"], r["attr"], r["screen"],
                 r["map"] == final["map"], r["tiles"] == final["tiles"],
                 r["attr"] == final["attr"],
                 (r["scx"], r["scy"]) == (final["scx"], final["scy"])))
    # The answer: the first frame that is not black AFTER the fade-out has
    # blacked the screen. Frame 0 is the OLD room still ramping down, which is
    # not what this is about - reading it as "the reveal" was the first wrong
    # answer this probe gave.
    seen_black = False
    for i, r in enumerate(rows):
        if r["black"]:
            seen_black = True
            continue
        if seen_black:
            wrong = [k for k, ok in (("tilemap", r["map"] == final["map"]),
                                     ("tile data", r["tiles"] == final["tiles"]),
                                     ("attributes", r["attr"] == final["attr"]),
                                     ("scroll", (r["scx"], r["scy"])
                                      == (final["scx"], final["scy"])))
                     if not ok]
            print("\nFIRST NON-BLACK FRAME: %d -- still wrong: %s"
                  % (i, ", ".join(wrong) if wrong else "nothing (clean reveal)"))
            break
    else:
        print("\nno non-black frame in the window")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
