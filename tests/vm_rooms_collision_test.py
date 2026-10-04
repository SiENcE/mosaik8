#!/usr/bin/env python3
"""`emit_rooms_mos` wires solid_at for BOTH collision models.

Regression for the tile-based-collision bug: a world that uses the TILE-BASED
solid set (`[collision] solid = [...]`, which emits `scenes.is_solid` +
`scenes.map_tile`, NOT the per-cell `scenes.collision_at`) used to generate a
`solid_at` that just `return false`d -- so the player fell through every solid
tile (vm-showcase's "solid tiles collision don't work anymore" + vm-plat's
`scenes.collision_at` build error after the world switched models).

`generate_rooms` now reports `has_solid_tiles`, and `emit_rooms_mos` emits:
  * a PAINTED per-cell layer (`has_collision`)  -> `collision_at(...) == COLLIDE_SOLID`
  * a TILE-BASED solid set   (`has_solid_tiles`) -> `is_solid(map_tile(...))`
  * NEITHER                                       -> `return false`
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm


def _solid_at(**info):
    """The generated solid_at body for a world with the given collision flags."""
    info.setdefault("types", ["platform"])
    src = mosaik_vm.emit_rooms_mos(info)
    # slice out the solid_at function body (brace-matched: the out-of-bounds
    # guard closes an inner block, so first-} slicing truncates it)
    a = src.index("function solid_at")
    depth = 0
    for i in range(a, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[a:i + 1]
    raise AssertionError("unbalanced solid_at")


def check(label, cond):
    print(("[PASS] " if cond else "[FAIL] ") + label)
    return cond


def main():
    ok = True

    # tile-based solid set -> is_solid(map_tile(...)), NOT return false
    tile = _solid_at(has_solid_tiles=True, uniform=True)
    ok &= check("[tile-based] solid_at samples is_solid(map_tile)",
                "scenes.is_solid(scenes.map_tile(room, idx))" in tile)
    ok &= check("[tile-based] solid_at is NOT a constant false",
                "return false" not in tile)

    # non-uniform tile-based uses the per-scene width
    nu = _solid_at(has_solid_tiles=True, uniform=False)
    ok &= check("[tile-based non-uniform] indexes with SCENE_W[room]",
                "scenes.SCENE_W[room]" in nu
                and "scenes.is_solid(scenes.map_tile(room, idx))" in nu)

    # painted per-cell layer -> collision_at == COLLIDE_SOLID (unchanged)
    layer = _solid_at(has_collision=True, uniform=True)
    ok &= check("[painted layer] solid_at samples collision_at",
                "scenes.collision_at(room, idx) == scenes.COLLIDE_SOLID" in layer)
    ok &= check("[painted layer] does NOT use is_solid",
                "is_solid" not in layer)

    # a painted layer wins if a world somehow has both (per-cell is richer)
    both = _solid_at(has_collision=True, has_solid_tiles=True, uniform=True)
    ok &= check("[both] the painted layer takes precedence",
                "collision_at" in both and "is_solid" not in both)

    # OUT OF BOUNDS is SOLID (reference-engine parity: its collision.c returns
    # COLLISION_ALL past the scene). The unbounded read used to hit ANOTHER
    # room's bytes through the paint_table window, so a player falling into an
    # open-bottom pit landed on phantom collision below the screen and was
    # stranded; solid-OOB gives the invisible sub-scene floor the reference's
    # pit-recovery design relies on (the reference-engine sample conversion's long walk-in room).
    for label, kw in (("tile-based", dict(has_solid_tiles=True)),
                      ("painted", dict(has_collision=True))):
        u = _solid_at(uniform=True, **kw)
        ok &= check("[oob %s uniform] guard on MAP_W/MAP_H returns true" % label,
                    "cx >= scenes.MAP_W or cy >= scenes.MAP_H" in u
                    and "return true" in u)
        n = _solid_at(uniform=False, **kw)
        ok &= check("[oob %s non-uniform] guard on SCENE_W/SCENE_H" % label,
                    "cy >= scenes.SCENE_H[room]" in n and "return true" in n)
    # ... and the shared cell_at (the platform-cells shape) answers SOLID there
    src = mosaik_vm.emit_rooms_mos({"types": ["platform"], "has_collision": True,
                                    "has_platform_cells": True, "uniform": True})
    a = src.index("function cell_at")
    cell = src[a:src.index("function solid_at")]
    ok &= check("[oob cell_at] guard returns COLLIDE_SOLID",
                "cx >= scenes.MAP_W or cy >= scenes.MAP_H" in cell
                and "return scenes.COLLIDE_SOLID" in cell)

    # no collision at all -> the constant-false stub (setters still take a cb)
    none = _solid_at(uniform=True)
    ok &= check("[no collision] solid_at is the constant-false stub",
                "return false" in none)

    # ONE-WAY PLATFORM cells (painted type 2). They are NOT solid - you walk
    # and jump THROUGH them - so they cannot ride solid_at, whose answer is
    # the same in all four directions. Without a separate probe the player
    # fell straight through every one of them (the reference-engine sample's
    # long walk-in room paints 20 such cells in its ground).
    rows = [[0, 0], [2, 1]]                     # a type-2 cell + a solid one
    plat = mosaik_vm.emit_rooms_mos(
        {"types": ["platform"], "uniform": True, "has_collision": True,
         "has_platform_cells": True})
    ok &= check("[one-way] platform_at is emitted",
                "function platform_at" in plat
                and "scenes.COLLIDE_PLATFORM" in plat)
    ok &= check("[one-way] it is registered on the player",
                "player.set_platform_cells(platform_at)" in plat)
    # Both probes read the SAME cell, and a seam read pins its function
    # resident - so the generator emits ONE `cell_at` and makes solid_at /
    # platform_at plain comparisons over it (they then bank with `rooms`).
    ok &= check("[one-way] one seam read: cell_at, with both probes over it",
                "function cell_at(" in plat
                and "return cell_at(x, y) == scenes.COLLIDE_SOLID" in plat
                and "return cell_at(x, y) == scenes.COLLIDE_PLATFORM" in plat)

    # ...and a world with NO type-2 cell stays byte-identical (no probe at all)
    without = mosaik_vm.emit_rooms_mos(
        {"types": ["platform"], "uniform": True, "has_collision": True})
    ok &= check("[one-way] absent when the world paints none",
                "platform_at" not in without)

    # the flag is derived from NESTED rows (world.toml stores a list of rows);
    # a membership test on the nested form silently never matches
    ok &= check("[one-way] _cells flattens a nested collision layer",
                2 in mosaik_vm.rooms._cells(rows)
                and 2 in mosaik_vm.rooms._cells([0, 2, 1]))

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
