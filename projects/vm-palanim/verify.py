#!/usr/bin/env python3
"""vm-palanim: does the per-FRAME sprite palette reach the hardware?

Read out of OAM, never off the screen - the palette lives in the attribute
byte, so that is where the claim is (a screenshot says the picture changed, not
WHICH mechanism changed it). Four assertions, and the last is the one that
guards the regression:

1. `blinker`  its objects are drawn under BOTH palette 1 and palette 2 - the
   per-FRAME recolour, over ONE piece of art (the two frames share their cells,
   so a recolour costs no tiles).
2. `flasher`  drawn with the DMG select (props bit 4) both SET and CLEAR - the
   flash, and the proof that bit 4 is independent of the palette number in
   bits 0-2 (it alternates while those stay 0).
3. `stately`  drawn under palette 3 while standing and palette 2 while walking
   - the per-STATE recolour, driven by movement alone with no bytecode.
4. the PLAYER keeps ONE palette throughout. Its clips author none, so `F_PAL`
   holds the 255 no-palette sentinel and the animator must stand down; an
   uncoloured default of 0 would flatten it - and, in a room with a per-CELL
   palette map, every coloured actor in it.

Keyed on OAM SLOT, not screen x: each 16x16 actor fans 4 objects from its pool
base (actor i -> 4i) and the player's fan sits above the pool, so the slots are
deterministic while the x positions of two actors can overlap.

GB family only - PyBoy is the emulator that exposes OAM. The other targets are
covered by the build matrix, and SMS/GG ignore the write by design (one sprite
palette, so `sprite.set_palette` is an honest no-op there).

    python projects/vm-palanim/verify.py [rom]
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.join(HERE, "build", "gameboy_color", "vm-palanim.gbc")

#: OAM slot ranges: actor i fans 4 objects from base 4i, the player above the
#: 8-slot pool. Asserted against the ROM below rather than assumed.
FANS = {"blinker": range(0, 4), "flasher": range(4, 8),
        "stately": range(8, 12), "player": range(32, 36)}

#: Frames to run before sampling. An actor is PLACED by `actor.render()`, which
#: `vm.core.run()` calls BEFORE the animator's `tick_all`, so a freshly spawned
#: fan wears its base property for exactly ONE frame before its first `apply`
#: writes the clip's palette (measured: the blinker appears on frame 69 with
#: attribute 0 and is on palette 1 from frame 70). That is the documented frame
#: order, not a defect - so the steady state is what this asserts on.
WARMUP = 90


def main():
    rom = sys.argv[1] if len(sys.argv) > 1 else DEFAULT
    if not os.path.isfile(rom):
        print("build it first: python mosaik8.py build --platform gameboy_color "
              "projects/vm-palanim")
        return 0
    try:
        from pyboy import PyBoy
    except ImportError:
        print("PyBoy not installed - skipping (pip install pyboy)")
        return 0

    pb = PyBoy(rom, window="null", sound_emulated=False)
    seen = {k: set() for k in FANS}
    # `stately` walks a slow beat, so its palette is sampled WITH its motion:
    # {palette -> did it move since the last frame}
    stately_moved = {}
    prev_y = None
    live = {k: 0 for k in FANS}
    for _ in range(WARMUP):
        pb.tick(1, False)
    for _ in range(700):
        pb.tick(1, False)
        oam = pb.memory[0xFE00:0xFEA0]
        for name, slots in FANS.items():
            for i in slots:
                y, _x, _t, a = oam[i * 4:i * 4 + 4]
                if 0 < y < 160:
                    seen[name].add(a & 0x17)
                    live[name] += 1
        y = oam[8 * 4]
        if prev_y is not None and 0 < y < 160:
            stately_moved.setdefault(oam[8 * 4 + 3] & 7, set()).add(y != prev_y)
        prev_y = y
    pb.stop(save=False)

    ok = True

    def check(cond, label, detail):
        nonlocal ok
        print("  [%s] %s -- %s" % ("PASS" if cond else "FAIL", label, detail))
        ok = ok and cond

    print("== the fans are where the pool put them ==")
    for name in FANS:
        check(live[name] > 0, "%s draws" % name, "%d object-frames" % live[name])

    print("== per-frame sprite palette ==")
    blink = {v & 7 for v in seen["blinker"]}
    check(blink == {1, 2}, "the blinker alternates palettes 1 and 2",
          "palettes seen: %s" % sorted(blink))

    flash = {bool(v & 0x10) for v in seen["flasher"]}
    check(flash == {False, True}, "the flasher alternates OBP0/OBP1",
          "DMG select seen: %s" % sorted(flash))
    check({v & 7 for v in seen["flasher"]} == {0},
          "...on ONE colour palette - bit 4 is independent of bits 0-2",
          "palettes seen: %s" % sorted(v & 7 for v in seen["flasher"]))

    walked = {p for p, moved in stately_moved.items() if True in moved}
    stood = {p for p, moved in stately_moved.items() if False in moved}
    check(2 in walked and 3 in stood,
          "stately recolours per STATE (3 standing, 2 walking)",
          "walking: %s  standing: %s" % (sorted(walked), sorted(stood)))

    check(len(seen["player"]) == 1,
          "the UNCOLOURED player keeps ONE palette (the 255 sentinel)",
          "values seen: %s" % sorted(seen["player"]))

    print("\n" + ("All checks passed" if ok else "FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
