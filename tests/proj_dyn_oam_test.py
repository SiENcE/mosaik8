#!/usr/bin/env python3
"""The dynamic projectile OAM block (plan P1).

The shooter room fits ONE shot in flight under the static layout: 16 actor fans +
the emote reserve leave 2 of 40 objects, so `fit_slots` clamps the pool to a
single slot while 13 of those fans belong to PARKED actors whose entries sit
off-screen. The reference (the reference engine) allocates OAM per frame and fires five.

Stage P1 sits the block on the parked-actor WATERMARK instead of the
load-time worst case. The rules this pins:

* shots may re-base because they RE-ASSERT everything per frame; the old
  block is parked WHOLESALE before the base moves (the p_parked latch is per
  SLOT, not per entry);
* the watermark counts live, based, NON-parked fans and answers 255 for a
  drawn fan it was never told the size of - the caller then falls back to
  the STATIC base, never guesses;
* the floor is the player fan's end (`pob0`), so an empty window never puts
  a shot on the player;
* everything is emitted only for a `uses_projectile` world, and a shell that
  never calls `set_dyn` keeps the static layout verbatim.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik_vm.rooms import emit_rooms_mos  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = open(os.path.join(HERE, "..", "lib", "vm", "projectile.mos"),
            encoding="utf-8").read()
ACT = open(os.path.join(HERE, "..", "lib", "vm", "actor.mos"),
           encoding="utf-8").read()

FAILS = []


def check(label, cond):
    print(("  [ok] " if cond else "  FAIL: ") + label)
    if not cond:
        FAILS.append(label)
    return cond


def test_engine_surface():
    print("[the engine surface]")
    check("vm.projectile exposes set_dyn", "function set_dyn" in PROJ)
    check("the dynamic arm falls back to the STATIC base on an unknown "
          "watermark (255)", "nb = p_sbase" in PROJ)
    check("the OLD block is parked before the base moves",
          "while j < p_slots" in PROJ and "sprite.move(ob, 200, 200)" in PROJ)
    check("vm.actor exposes set_fan + oam_watermark",
          "function set_fan" in ACT and "function oam_watermark" in ACT)
    # The accumulation moved INTO render()'s walk (R2) - a walk of its own
    # from vm.projectile cost ~4k cycles a frame; oam_watermark() is now one
    # cached byte. Both render arms must carry it.
    check("the watermark answers UNKNOWN for an untold fan (both arms)",
          ACT.count("wmu = 1") >= 2 and ACT.count("a_wm = 255") >= 3)
    # 2026-08-28: the watermark moved INSIDE the looked/placed path, testing
    # the `off` local instead of re-reading a_parked[i] - exact, because after
    # place() a looked slot's a_parked IS `off`, and an out-of-phase slot is
    # parked by definition. Each render arm has two `off == false` gates: the
    # vis push and the watermark.
    check("a parked fan is excluded (the watermark rides the placed path)",
          ACT.count("if off == false {") >= 4)
    check("the read is a cached byte, not a walk",
          "return a_wm" in ACT.split("function oam_watermark", 1)[1][:200])


def test_generated_wiring():
    print("[the generated rooms.mos wiring]")
    # Multi-object fans -> dynamic OAM; objects + entity so the actor loop
    # (where set_fan rides set_base) is emitted at all.
    base = {"types": ["shmup"], "uniform": True, "has_objects": True,
            "has_entity": True,
            "clips": {"meta_w": 2, "meta_h": 2, "per_kind_size": True,
                      "flip": False, "player": True}}
    on = emit_rooms_mos(dict(base, uses_projectile=True))
    check("a projectile world captures the player-fan floor (pob0)",
          "var pob0: u8 = ob" in on)
    check("...registers the dynamic base beside the static one",
          "projectile.set_dyn(pob0)" in on
          and "projectile.set_base(ob)" in on)
    check("...tells vm.actor each fan's size beside its base",
          "actor.set_fan(slot, fan)" in on)

    off = emit_rooms_mos(dict(base))
    check("a world with NO projectiles emits none of it (byte-identical)",
          "set_dyn" not in off and "set_fan" not in off
          and "pob0" not in off)


def main():
    print("the dynamic projectile OAM block (P1)")
    print("=" * 50)
    test_engine_surface()
    test_generated_wiring()
    print()
    if FAILS:
        print("FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        return 1
    print("All dynamic-OAM checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
