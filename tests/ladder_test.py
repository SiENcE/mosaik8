#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""LADDERS: a third painted collision type, climbed by the platform handler.

The reference engine's `TILE_PROP_LADDER` (0x10) imported as walkable AIR, so the sample's
ladder room had nothing to climb. The reference behaviour is
`states/platform.c` (`IS_LADDER`, `ladder_check`, `state_update_ladder`, and
the sample's own FEAT_PLATFORM_LADDERS + _HOP + _DROP + _WALK_OFF); this takes
the BEHAVIOUR, not the plugin's state machine - our platform handler has no
state enum, so "on a ladder" is one flag that owns the frame.

Measured on the converted ROM (`tools/ladderprobe/ladder_probe.py`, which asks
vm.core to change rooms the way a script would rather than walking there):
the grab snaps the box onto the column (x 112 -> 109), the climb runs at
**2.00 px per VM frame** - exactly the authored CLIMB - the player HANGS with
no gravity for 40 idle frames, descending returns it to the floor grounded,
and a sideways press steps it off. **+10 B of bank 0** on the GB and the GBC.

The rules this pins:

* the whole feature is OPT-IN. A world that paints no type-3 cell emits no
  probe, no wiring and no CLIMB const, and `has_lad` stays 0 - one compare per
  platform frame.
* the climb POSE is not a fifth clip state. The reference engine's `ANIM_CLIMB` is 6,
  i.e. its MOVING-UP animation slot, so ours is the walk clip's UP facing - a
  cell the per-kind table already reserves and a platform player never
  otherwise draws. `airborne()` reporting 0 on a ladder is what lets it be
  drawn instead of jump/fall.
* the ladder is probed at the box's middle PIXEL, `(pw - 1) / 2`. With `pw / 2`
  a 16 px box standing flush on a 1-tile ladder probes the FIRST pixel of the
  next column - so up did nothing on the very ladder the player stood on.
* a generated CALL and its generated DEFINITION are decided by the same
  condition: `COLLIDE_LADDER` is emitted AND exported only where a scene
  paints one.
"""

import tempfile

from mosaik_vm.rooms import emit_rooms_mos

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "..", "lib", "vm", "player.mos"),
           encoding="utf-8").read()
#: vm.player's exports run over SEVERAL lines, so "is it exported" is a scan of
#: every one of them - splitting on the last `export` reads one line and calls
#: a name on any other line missing.
EXPORTS = set()
for _line in SRC.splitlines():
    if _line.strip().startswith("export "):
        EXPORTS.update(n.strip() for n in _line.strip()[7:].split(","))


def _exported(src, name):
    """Is `name` in ANY of a module's `export` statements?

    NOT `src.split("export")[-1]`: a module may have several export lines, and
    taking only the last one reads as "not exported" the moment a new one is
    appended below it - which is exactly what W7j and W7c each did to a
    different module, breaking a green assertion for a reason that had nothing
    to do with what it pins.
    """
    return any(name in ln for ln in src.splitlines()
               if ln.strip().startswith("export "))


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def _engine():
    print("[vm.player: the ladder state]")
    ok = True
    ok &= check("set_ladder_cells is exported",
                "function set_ladder_cells" in SRC
                and "set_ladder_cells" in EXPORTS)
    ok &= check("it is OPT-IN: unset, has_lad is 0 and the arm is one compare",
                "var has_lad: u8" in SRC and "if has_lad == 1 {" in SRC)
    ok &= check("the probe AND the climb speed arrive together (an unset speed "
                "would freeze the player on the first rung)",
                "function set_ladder_cells(cb: function(u16, u16) -> bool, "
                "speed: u8)" in SRC and "if pclimb == 0 {" in SRC)
    ok &= check("every field is initialiser-free, so it is BSS not bank 0",
                "var on_lad: u8" in SRC and "var on_lad: u8 = " not in SRC
                and "var pclimb: u8" in SRC and "var pclimb: u8 = " not in SRC)

    # the middle-PIXEL rule
    ok &= check("the ladder is probed at the box's middle PIXEL, (pw - 1) / 2",
                "return (pw - 1) / 2" in SRC
                and "g_lad(px + lad_half(), y)" in SRC)
    ok &= check("...and the snap uses the SAME half, so a grabbed ladder stays "
                "grabbed", "var half: u16 = lad_half()" in SRC)

    # the state machine, such as it is
    step = SRC.split("local function lad_step()")[1].split("function update_platform")[0]
    ok &= check("DOWN + jump drops off the ladder (LADDERS_DROP)",
                "held(INPUT_DOWN) and held(INPUT_A)" in step)
    ok &= check("topping out onto ground stands the player on it",
                "lad_land(py + ph)" in step)
    ok &= check("...and topping out into a SHAFT hops (LADDERS_HOP)",
                "vy = 0 - pjump" in step)
    ok &= check("a horizontal press steps off (LADDERS_WALK_OFF), but only "
                "after the pad is released (plat_ladder_block_h)",
                "if lad_bh == 1 {" in step and "lad_off()" in step)
    join = SRC.split("local function lad_join()")[1].split("local function lad_step")[0]
    ok &= check("...and leaving BLOCKS a re-grab until up/down is released "
                "(plat_ladder_block_v)", "if lad_bv == 1 {" in join)
    ok &= check("a grab faces UP, which is the climb strip",
                "pface = 1" in join)
    ok &= check("a grab zeroes the fall and the run",
                "vy = 0" in join and "hvx = 0" in join)

    air = SRC.split("function airborne()")[1].split("function rising()")[0]
    ok &= check("airborne() reports 0 on a ladder (so vm.canim draws the climb "
                "pose, not jump/fall)", "if on_lad == 1 {" in air)
    ok &= check("climbing() is exported for an external animator",
                "function climbing()" in SRC
                and "climbing" in EXPORTS)

    # the ladder owns the frame: no gravity, no run, and it still renders
    upd = SRC.split("function update_platform()")[1][:900]
    ok &= check("the ladder arm owns the frame and still renders",
                "lad_step()" in upd and "follow_and_render()" in upd
                and "return" in upd)
    return ok


def _generated():
    print("")
    print("[the generated rooms.mos wiring]")
    ok = True
    base = {"types": ["platform"], "has_collision": True, "uniform": True}
    off = emit_rooms_mos(base)
    ok &= check("a world with no ladder cell emits NO probe (byte-identical)",
                "ladder_at" not in off and "CLIMB" not in off)
    on = emit_rooms_mos(dict(base, has_ladder_cells=True))
    ok &= check("...and one that paints a ladder gets the probe",
                "function ladder_at(x: u16, y: u16) -> bool" in on
                and "== scenes.COLLIDE_LADDER" in on)
    ok &= check("the probe reads the SHARED cell_at (one seam read)",
                "function cell_at" in on)
    ok &= check("the registration carries the probe AND the speed",
                "player.set_ladder_cells(ladder_at, CLIMB)" in on)
    ok &= check("CLIMB comes from studio.toml [player]",
                "const CLIMB: u8 = 1" in on)
    ok &= check("...and it follows the authored value",
                "const CLIMB: u8 = 4"
                in emit_rooms_mos(dict(base, has_ladder_cells=True,
                                       player={"climb": 4})))
    # a ladder world that ALSO has one-way platforms shares one cell_at
    both = emit_rooms_mos(dict(base, has_ladder_cells=True,
                               has_platform_cells=True))
    ok &= check("a world with both types emits ONE cell_at and two probes",
                both.count("function cell_at") == 1
                and "function platform_at" in both
                and "function ladder_at" in both)
    return ok


def _scenes():
    print("")
    print("[the scenes transpiler: COLLIDE_LADDER]")
    ok = True
    import mosaik_scenes
    from scene_transpile_test import make_tileset_png
    tmp = tempfile.mkdtemp(prefix="ladder_scene_test_")
    make_tileset_png(os.path.join(tmp, "tiles.png"))

    def world(ladder):
        # a full-size scene: the map/collision dimensions are validated, so a
        # 4x2 stub is rejected before it reaches the collision emitter
        col = [[0] * 32 for _ in range(32)]
        col[1][1] = 1
        col[1][2] = 2
        if ladder:
            col[2][3] = 3
        return {"tileset": {"png": "tiles.png"},
                "scene": [{"name": "a", "width": 32, "height": 32,
                           "map": [[0] * 32 for _ in range(32)],
                           "collision": col}]}

    plain = mosaik_scenes.transpile(world(False), tmp)
    lad = mosaik_scenes.transpile(world(True), tmp)
    ok &= check("no ladder cell -> the constant is not emitted at all",
                "COLLIDE_LADDER" not in plain)
    ok &= check("a ladder cell -> it is", "const COLLIDE_LADDER: u8 = 3" in lad)
    # the export is the half that fails as a COMPILE ERROR when it drifts
    ok &= check("...and it is EXPORTED, or rooms.mos cannot read it",
                _exported(lad, "COLLIDE_LADDER"))
    # the VALUE rides the existing per-cell array: no new table, no new
    # selector, just a third number in the layer that was always there.
    import re
    m = re.search(r"const A_COLLISION[^=]*= \[(.*?)\]", lad, re.S)
    vals = [v.strip() for v in (m.group(1) if m else "").split(",")]
    ok &= check("the cell value rides the existing collision array",
                bool(m) and "3" in vals)
    ok &= check("...and the array is otherwise unchanged (the same 0/1/2)",
                bool(m) and set(vals) <= {"0", "1", "2", "3"})
    return ok


def main():
    print("ladders: a climbable third collision type")
    print("=" * 58)
    ok = _engine() and _generated() and _scenes()
    print("=" * 58)
    print("All checks passed" if ok else "Some tests failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
