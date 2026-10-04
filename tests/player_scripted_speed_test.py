#!/usr/bin/env python3
"""A scripted PLAYER_MOVE_TO walks at the player's MOVE SPEED in every scene
type, and a PLATFORM room was the one arm that never set it.

`player.step_to` - what `PLAYER_MOVE_TO` / `PLAYER_MOVE_TO_E` step once per VM
frame - walks at `pspeed`, and `pspeed` is the reference engine's `PLAYER.move_speed`
(`start_player_move_speed`, its `data_manager.c` load_player). That is a field
SEPARATE from the platform run velocity in both engines: the reference engine's platform
handler walks on the `plat_walk_vel` engine field and only the scripted move
reads move_speed, exactly as ours walks on `prun` and only `step_to` reads
`pspeed`.

`setup` (topdown) sets it from WALK and `setup_shmup` forwards its own speed,
but `setup_platform` / `setup_wide` take no walk argument at all - so a
platform room inherited whatever the last TOPDOWN room had left, or 1
(`step_to`'s floor) if none had run yet. Two defects in one: the speed was
wrong, and it depended on the ROUTE the player took to get there.

Measured on the reference-engine sample conversion's long walk-in room doorway (the trigger that
walks the player into the town and switches scene), PyBoy, GB build:

    before   118 px in 300 LCD frames = 0.39 px per LCD frame
    after    118 px in 118 LCD frames = 1.00 px per LCD frame
    reference `start_player_move_speed` = 32 subpx = 1.00 px per LCD frame

Bank 0 unchanged on that project (14,445 / 15,390 B both ways).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def _speed_lines(src):
    return [ln.strip() for ln in src.splitlines()
            if "player.set_speed(" in ln]


def test_platform_sets_the_scripted_walk_speed():
    src = mosaik_vm.emit_rooms_mos({"types": ["platform"],
                                    "has_collision": True})
    check("a platform room sets the scripted move speed",
          "player.set_speed(WALK)" in src)
    check("...from the room's own WALK const, not a literal",
          "const WALK: u8 =" in src)
    check("...exactly once, covering both arms of the wide/narrow fork",
          len(_speed_lines(src)) == 1,
          "%d set_speed lines" % len(_speed_lines(src)))


def test_it_rides_the_wide_fork_too():
    src = mosaik_vm.emit_rooms_mos({"types": ["platform"],
                                    "has_collision": True,
                                    "wide_rooms": True, "uniform": True})
    check("a world mixing wide and narrow platform rooms emits both setups",
          "player.setup_wide(" in src and "player.setup_platform(" in src)
    check("...and still ONE set_speed after the fork",
          len(_speed_lines(src)) == 1,
          "%d set_speed lines" % len(_speed_lines(src)))


def test_topdown_and_shmup_are_untouched():
    """They already carried it, through `setup` - so nothing is added there.

    `setup` takes the walk speed as an argument and `setup_shmup` forwards its
    own, which is right: the reference engine's topdown AND shmup handlers both move the
    player on `PLAYER.move_speed`, so one value drives the handler and the
    scripted move in those scene types. Only PLATFORM separates them."""
    td = mosaik_vm.emit_rooms_mos({"types": ["topdown"],
                                   "has_collision": True})
    check("a topdown room passes WALK to setup()",
          "player.setup(PTILE, px, py, PW, PH, WALK, solid_at)" in td)
    check("...and adds no second speed write",
          not _speed_lines(td), "%d set_speed lines" % len(_speed_lines(td)))

    sh = mosaik_vm.emit_rooms_mos({"types": ["shmup"], "has_collision": True})
    check("a shmup room forwards its own speed through setup_shmup",
          "player.setup_shmup(" in sh)
    check("...and adds no second speed write",
          not _speed_lines(sh), "%d set_speed lines" % len(_speed_lines(sh)))


def main():
    print("=== player_scripted_speed_test ===")
    test_platform_sets_the_scripted_walk_speed()
    test_it_rides_the_wide_fork_too()
    test_topdown_and_shmup_are_untouched()
    print("%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
