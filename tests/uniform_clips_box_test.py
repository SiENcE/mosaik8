#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""A UNIFORM clips world must never NAME `clips.meta_w` (2026-09-16).

`mosaik_anim` emits the per-kind size accessors under ONE guard that covers
the definition and the export together:

    if mixed or has_desc:  ->  function meta_w / meta_h  (+ both exported)

so a world whose sprites are all the same size never gets them, by design -
a uniform pool keeps the single size the shell passes to `canim.set_clips`,
and the module stays byte-identical.

The rooms generator knows this: `config._meta_w` / `_meta_h` return the
ACCESSOR when `clips["per_kind_size"]` and the BAKED LITERAL otherwise, and
the On Interact box branch goes through them too. The actor-BOX branch of
`load_room` was not - it hand-wrote `clips.meta_w(fk)` gated only on the
world HAVING a clips module at all, so a uniform world generated a rooms
module that cannot compile:

    module "clips" has no module-level symbol "meta_w" (referenced from
    module "rooms")

It sits unreachable because every world in the repo with clips AND actor
boxes happens to mix sprite sizes - all five carried reference-engine conversions
do. It was found on 2026-09-16 by an experiment that briefly produced a
uniform multi-kind world (8 kinds, every one 16x16) and was then removed for
unrelated reasons. **So no project exercises this
today** - the checks below BUILD a uniform world rather than naming one, or
the trap would simply re-arm itself the next time somebody makes one.

EVERY CHECK ASSERTS ITS OWN STIMULUS FIRST. `assertNotIn("clips.meta_w(")`
passes vacuously if the box branch never ran at all, which is the shape that
would let this regress silently.
"""

from mosaik_vm.rooms import emit_rooms_mos

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


#: `_wants_boxes(info)` is `solid_actors or uses_projectile`; `has_objs` is
#: what makes the per-slot loop that carries the box branch run at all.
#: NOTE the key is `has_objects`, which is what `context.derive` reads - a
#: neighbouring test's fixture spells it `has_objs`, where it happens to be
#: False and so is harmlessly ignored. Spelled that way here it silently
#: skipped the whole per-slot loop, and only the stimulus check below said so.
BASE = {"types": ["topdown"], "scene_count": 2, "uniform": True,
        "map_w": 20, "map_h": 18, "has_objects": True, "has_ent": False,
        "has_trig": False, "has_doors": False, "solid_actors": True}

#: The shape `generate_rooms` builds (it derives `per_kind_size` by asking
#: whether the generated clips module defines the accessor at all).
UNIFORM = {"meta_w": 2, "meta_h": 2, "per_kind_size": False}
MIXED = {"meta_w": 2, "meta_h": 2, "per_kind_size": True}


def test_uniform_world_bakes_the_literal():
    print("\n[a UNIFORM world bakes the size and names no accessor]")
    out = emit_rooms_mos(dict(BASE, clips=UNIFORM))
    # the STIMULUS: the box branch really ran
    check("the actor-box branch ran", "actor.set_box(slot, sbw, sbh, 0, 0)" in out,
          "box branch not reached - every check below would pass vacuously")
    check("...and it names NO clips accessor",
          "clips.meta_w(" not in out and "clips.meta_h(" not in out,
          [l for l in out.split("\n") if "clips.meta_" in l][:3])
    check("...it bakes the one size the pool shares (2 tiles -> 16 px)",
          "var sbw: u8 = 2" in out and "var sbh: u8 = 2" in out)


def test_mixed_world_still_calls_the_accessor():
    print("\n[a MIXED world is byte-identical: it still calls the accessor]")
    out = emit_rooms_mos(dict(BASE, clips=MIXED))
    check("the actor-box branch ran", "actor.set_box(slot, sbw, sbh, 0, 0)" in out)
    check("...through clips.meta_w(fk) / meta_h(fk), as before",
          "var sbw: u8 = clips.meta_w(fk)" in out
          and "var sbh: u8 = clips.meta_h(fk)" in out)


def test_a_world_with_no_clips_keeps_the_8x8_default():
    print("\n[no clips module at all: the historical 8x8 default]")
    out = emit_rooms_mos(dict(BASE))
    check("the 8x8 fallback is emitted",
          "actor.set_box(slot, 8, 8, 0, 0)" in out)
    check("...and nothing names the clips module's accessors",
          "clips.meta_w(" not in out and "clips.meta_h(" not in out)


def test_a_uniform_world_with_hitboxes_registers_interact_boxes():
    """The reference engine's interact test is a BOX probe in front of the player
    (`actor_with_script_in_front_of_player`), and a world that authors actor
    hitboxes - every import does - has an OFFSET player box, which the native
    point test cannot measure against a sprite corner. Gated on mixed sizes
    only, an all-16x16 import (the adventure check project) could talk to no NPC: player
    box (96,184) against the goblin's corner (80,176), 16 px apart, past the
    14 px radius. A uniform world with NO hitboxes keeps the point test."""
    print("\n[a uniform world with hitboxes registers interact boxes]")
    base = dict(BASE, has_entity=True, has_player_kind=True,
                has_obj_interact=True, nkinds=2)
    out = emit_rooms_mos(dict(base, clips=UNIFORM,
                              kind_boxes={1: (16, 8, 0, 8)}))
    check("the interact slot is wired", "scenes.obj_interact(i)" in out,
          "no interact slot - the checks below would pass vacuously")
    check("...and registers the box at the baked size",
          "var bw: u8 = 2" in out and "entity.set_box(slot, bw, bh)" in out)
    check("...naming no clips accessor",
          "clips.meta_w(" not in out and "clips.meta_h(" not in out)
    plain = emit_rooms_mos(dict(base, clips=UNIFORM))
    check("a uniform world with no hitboxes keeps the point test",
          "scenes.obj_interact(i)" in plain
          and "entity.set_box(" not in plain)


def test_the_emitter_goes_through_the_one_source_helpers():
    """A SOURCE contract: the size has ONE decider. Hand-writing the call is
    what broke, and it reads as correct right up until a uniform world."""
    print("\n[source contract: emit_load asks _meta_w/_meta_h, never hand-writes]")
    with open(os.path.join(ROOT, "mosaik_vm", "rooms", "emit_load.py"),
              encoding="utf-8") as f:
        src = f.read()
    box = src.split("elif clips:")[1].split("else:")[0]
    check("the box branch calls the helpers",
          '_meta_w(info, "fk")' in box and '_meta_h(info, "fk")' in box, box[:200])
    check("...and hand-writes neither accessor",
          'clips.meta_w(fk)"' not in box and 'clips.meta_h(fk)"' not in box)


if __name__ == "__main__":
    test_uniform_world_bakes_the_literal()
    test_mixed_world_still_calls_the_accessor()
    test_a_world_with_no_clips_keeps_the_8x8_default()
    test_a_uniform_world_with_hitboxes_registers_interact_boxes()
    test_the_emitter_goes_through_the_one_source_helpers()
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("uniform_clips_box_test: all checks passed")
