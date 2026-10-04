#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Parking a metasprite child is `(0, PARK_Y)`, never `(0, 0)`.

On the Game Boy, OAM coordinates are biased by (8, 16), so y = 0 is 16 px ABOVE
the screen and an object parked there vanishes. The z80 ports have NO such bias
- the SMS writes its SAT directly, so (0, 0) is the VISIBLE top-left corner of
the screen. All three park sites in the metasprite prelude used (0, 0):

* the DESCRIPTOR upload's stale-child loop (frames of one kind differ in object
  count, so a shrinking frame retires children);
* the dense masked upload's blank-cell arm;
* the 8x16 masked upload's blank-column arm;
* and the one that actually caused the reported artefact - `gbs_move_sprite`'s
  HIDE path for a list-shaped base (`y >= SCREEN_HEIGHT`), which parks every
  child of a hidden descriptor actor.

MEASURED on the shooter conversion (SMS): its `lives` readout is a descriptor
kind whose frames hold 0/1/2/3 objects, so a retired heart parked in the corner
of the screen as a white block wearing the pool cell's tile. The Game Gear hid
it by accident - its 160x144 viewport is a centre crop of the same 256x192
plane, so plane (0, 0) is outside the visible window there - and the Game Boy
hid it by the OAM bias, which is why it read as "SMS only".

200 is the value the rest of the engine already parks at (the generated room
load's `sprite.move(s, 200, 200)`, and vm.projectile's own park). It is
off-screen on the GB family (144 lines) and on SMS/GG (192), and it
deliberately avoids the SMS's **0xD0 = 208, which TERMINATES the sprite list**
and would hide every object after it.
"""

from mosaik import MosaikCompiler

SRC = '''
module "main" {
    import "graphics.sprite"
    const D: array[u8, 4] = [ 0, 0, 0, 0 ]
    function main() {
        sprite.set_meta(0, 0, 2, 2)
        sprite.set_meta_mask(4, 8, 2, 2, 1)
        sprite.set_meta_list(8, 8, 0, D, 0, 1)
        sprite.move(0, 8, 8)
        sprite.move(8, 200, 200)
    }
    export main
}
'''


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("a parked metasprite child goes OFF SCREEN on every console")
    print("=" * 58)
    ok = True
    for plat in ("gameboy", "gameboy_color", "sms", "gamegear", "nes"):
        c = MosaikCompiler().compile(SRC.strip(), platform=plat)
        if "gbs_set_metasprite" not in c:
            continue
        ok &= check("%s: no child is parked at the visible (0, 0)" % plat,
                    "move_sprite(s, 0, 0)" not in c)
        ok &= check("%s: hiding a LIST base parks its children off-screen"
                    % plat, "GBS_SPR_PARK_Y); ++s" in c)
        ok &= check("%s: the park constant is defined once" % plat,
                    c.count("#define GBS_SPR_PARK_Y") == 1)
        # ...and it must not be the SMS list terminator.
        ok &= check("%s: the park y is not 0xD0 (208), the SAT terminator"
                    % plat, "#define GBS_SPR_PARK_Y 208" not in c)
    print("=" * 58)
    print("All checks passed" if ok else "Some tests failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
