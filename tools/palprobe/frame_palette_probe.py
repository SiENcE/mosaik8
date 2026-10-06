#!/usr/bin/env python3
"""Does a sprite change PALETTE as its animation does?

A frame palette (`clips.frame_pal`) recolours a whole sprite per animation
FRAME: a checkpoint can alternate CGB OBJ palettes 5 and 4 frame by frame, an
enemy can fly on one palette and explode on another, and on the DMG the same
field flips a sprite between OBP0 and OBP1 to make it FLASH. This reads the
answer out of OAM rather than off the screen - the palette lives in the
attribute byte, which is where the claim is. Worked sample:
`projects/vm-palanim`.

The room under test may be a walk from the title, so the room change is poked:
RAISE 2 (CHANGE_SCENE) with the room and the spawn pixel in vm.core's
pend_a/b/c, serviced by `run()` at frame step 4. That runs the REAL room
load - sprite residency, the per-cell palette rows, the lot.

Usage: frame_palette_probe.py ROM.gbc SYM.noi [scene] [x] [y]
"""
import sys
from collections import Counter


def symbols(noi):
    out = {}
    for line in open(noi, encoding="utf-8", errors="replace"):
        p = line.split()
        if len(p) == 3 and p[0] == "DEF":
            try:
                out[p[1]] = int(p[2], 16)
            except ValueError:
                pass
    return out


def main():
    rom, noi = sys.argv[1], sys.argv[2]
    scene = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    sx = int(sys.argv[4]) if len(sys.argv) > 4 else 96
    sy = int(sys.argv[5]) if len(sys.argv) > 5 else 80
    s = symbols(noi)
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", sound_emulated=False)
    CUR, PCODE = s["_vm_core_cur_scene"], s["_vm_core_pend_code"]
    PA, PB, PC = s["_vm_core_pend_a"], s["_vm_core_pend_b"], s["_vm_core_pend_c"]

    def w16(a, v):
        pb.memory[a], pb.memory[a + 1] = v & 0xFF, (v >> 8) & 0xFF

    def run(n=1):
        for _ in range(n):
            pb.tick(1, False)

    def tap(b, after=20):
        pb.button_press(b)
        run(8)
        pb.button_release(b)
        run(after)

    run(300)
    for _ in range(10):
        if pb.memory[CUR] != 0 or scene == 0:
            break
        tap("start")
        tap("a")
    pb.memory[PA] = scene
    w16(PB, sx)
    w16(PC, sy)
    pb.memory[PCODE] = 2
    run(150)
    print("room %d" % pb.memory[CUR])

    # Per OAM SLOT, the set of palette values it was drawn under. A slot whose
    # set has more than one member is a sprite that changed palette while the
    # room stood still - which is the whole claim.
    seen = {}
    tiles = {}
    for _ in range(240):
        run(1)
        oam = pb.memory[0xFE00:0xFEA0]
        for i in range(40):
            y, _x, t, a = oam[i * 4:i * 4 + 4]
            if not (0 < y < 160):
                continue
            seen.setdefault(i, Counter())[a & 0x17] += 1
            tiles.setdefault(i, set()).add(t)
    pb.stop(save=False)
    changed = {i: dict(c) for i, c in sorted(seen.items()) if len(c) > 1}
    print("OAM slots drawn under MORE THAN ONE palette: %d" % len(changed))
    for i, c in changed.items():
        print("  slot %2d  palettes %s  tiles %s"
              % (i, {k: v for k, v in sorted(c.items())}, sorted(tiles[i])))
    if not changed:
        print("  (none - every sprite kept one palette for the whole run)")


if __name__ == "__main__":
    main()
