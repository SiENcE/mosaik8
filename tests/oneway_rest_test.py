#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""A ONE-WAY platform rests the feet FLUSH, exactly like solid ground.

`box_down` answers ONE question for its caller, the per-pixel fall loop:
"may the box move into candidate position y". The solid arm tests the box's
OWN bottom row (`y + ph - 1`) for overlap, so the rest position puts the last
body pixel at `tile_top - 1` - standing ON the tile. The plat arm used to test
the first row BELOW the box (`y + ph`), which refuses the CONTACT position
itself, so the player rested at `tile_top - 2`: one pixel of air under the
feet on every one-way platform.

That is the reference engine's answer too - `states/platform.c` snaps a one-way landing
with the same `- bounds.bottom - 1` it uses for solid ground, so both surfaces
rest identically. Measured on the platformer conversion against its own reference ROM (feet
composited from live OAM + VRAM, `tools/cutprobe/pose_check.py`):

    surface              ours (before)   ours (after)   reference
    solid ground              111             111           111
    one-way scaffold           62              63            63

The ground matching on BOTH sides is why the earlier pixel-for-pixel idle A/B
read clean while the report "the
player stands one pixel higher" stayed true: the reporter was standing on a
roof, and every roof in that conversion's tutorial is a COLLISION_TOP cell.

Cost: 0 B of bank 0 both ways (vm.player banks; resident end 0x317C on the
platformer conversion's GB link with and without).

This pins the SEMANTICS at the source level, the ladder_test pattern:
the plat probe must ask about the box's own bottom row, not the row below.
"""

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "..", "lib", "vm", "player.mos"),
           encoding="utf-8").read()

_FAIL = []


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    if not cond:
        _FAIL.append(label)
    return cond


def main():
    print("[vm.player: one-way platform rest row]")
    body = SRC.split("local function box_down", 1)
    check("box_down exists", len(body) == 2)
    arm = body[1].split("local function", 1)[0] if len(body) == 2 else ""
    check("the plat probe tests the box's OWN bottom row (y + ph - 1), the "
          "same overlap question the solid arm asks",
          "var feet: u16 = y + ph - 1" in arm)
    import re
    check("the old row-below probe is gone (it refused the contact position, "
          "resting the feet one pixel above every one-way platform)",
          not re.search(r"var feet: u16 = y \+ ph(?!\s*-\s*1)", arm))
    check("only the tile's TOP row lands (the %8 gate keeps one-way "
          "semantics: entered mid-tile from below, the box passes through)",
          "if feet % 8 != 0 {" in arm)
    if _FAIL:
        print("Some tests failed")
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
