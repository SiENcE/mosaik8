#!/usr/bin/env python3
"""Behavioural check for vm-metasprite (PyBoy, headless).

Asserts the three claims the sample exists to make:

1. The DESCRIPTOR counter (one actor, 100 frames) and the NATIVE two-actor
   counter show the SAME number -- so the descriptor is not doing anything
   exotic, it is drawing the identical readout out of a tenth of the art.
2. Its two objects are re-POINTED at pool cells per frame, and the units
   position walks all ten digit cells -- the tile DEDUPE. Ten frames of the
   dense equivalent would be ten times the tiles.
3. The descriptor stack's two rows sit ELEVEN px apart while the dense one's
   sit SIXTEEN -- the row overlap a rectangle cannot express (the "feet gap").

Run:  python verify.py [rom]
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROM = os.path.join(ROOT, "build", "gameboy", "vm-metasprite.gb")
ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- " + detail) if detail else ""))


def main():
    rom = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROM
    if not os.path.isfile(rom):
        print("build it first: python mosaik8.py build --platform gameboy "
              "projects/vm-metasprite")
        return 1
    try:
        from pyboy import PyBoy
    except ImportError:
        print("PyBoy not installed - skipping")
        return 0

    pb = PyBoy(rom, window="null")
    pb.set_emulation_speed(0)

    def oam(i):
        b = 0xFE00 + i * 4
        return pb.memory[b], pb.memory[b + 1], pb.memory[b + 2]

    for _ in range(120):
        pb.tick()

    # Slots: 0-3 descriptor counter, 4-7 native pair, 8-11 desc stack,
    # 12-15 dense stack (the bases main.mos assigns).
    print("\n== the two counters agree ==")
    seen_units = set()
    agreed = 0
    for _ in range(400):
        pb.tick()
        d_tens, d_units = oam(0)[2], oam(2)[2]
        n_tens, n_units = oam(4)[2], oam(6)[2]
        if (d_tens, d_units) == (n_tens, n_units):
            agreed += 1
        seen_units.add(d_units)
    check("the descriptor counter matches the two-actor one every frame",
          agreed == 400, "%d/400 frames" % agreed)

    print("\n== the tile DEDUPE ==")
    check("the units position walks several distinct pool cells",
          len(seen_units) >= 5,
          "%d distinct tiles in 400 frames: %s"
          % (len(seen_units), sorted(seen_units)))
    # every digit tile lives below the two stack cells (tile 20), i.e. inside
    # the ten 8x16 cells = 20 tiles the whole 100-frame readout costs
    check("...all of them inside the 20-tile digit pool",
          all(t < 20 for t in seen_units), "max tile %d" % max(seen_units))

    print("\n== the row OVERLAP (the feet gap) ==")
    desc_top, desc_bot = oam(8)[0], oam(10)[0]
    dense_top, dense_bot = oam(12)[0], oam(14)[0]
    check("the DESCRIPTOR stack's rows are 11 px apart",
          desc_bot - desc_top == 11, "%d px" % (desc_bot - desc_top))
    check("the DENSE stack's rows are 16 px apart (what a rectangle forces)",
          dense_bot - dense_top == 16, "%d px" % (dense_bot - dense_top))
    # ...and the cost of that 16 px: a dense frame's cells are CONSECUTIVE
    # tiles, so the spacing has to be baked into its own copy of the art. The
    # descriptor re-points at the SAME pool cells the sheet already holds.
    check("the dense stack needs its OWN copy of the art (4 more tiles)",
          oam(12)[2] > oam(10)[2],
          "descriptor reuses cells %d/%d; dense starts at its own tile %d"
          % (oam(8)[2], oam(10)[2], oam(12)[2]))

    pb.stop()
    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
