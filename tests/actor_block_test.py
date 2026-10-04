#!/usr/bin/env python3
"""ACTORS BLOCK THE PLAYER (`studio.toml [scenes] solid_actors`, reference-engine parity).

The reference engine's topdown handler refuses the move when an actor is in the way, and
skips any actor without ACTOR_FLAG_COLLISION (`build/src/src/states/topdown.c`
+ `core/actor.c`):

    if (player_moving) {
        hit_actor = actor_in_front_of_player(topdown_grid, FALSE);
        if (hit_actor != NULL) { ... player_moving = FALSE; }
    }

so an NPC is SOLID there - which is what every conversation in its sample
assumes (you walk UP TO the person and press A, you do not stand inside them).
We had no actor test at all: `vm.player`'s only gate was `box_solid`, the scene
collision layer. Reported from play on the reference-engine sample conversion.

**What it blocks over is the sprite's authored `bounds`, not the drawn
rectangle** - the reference engine keeps that box on the SPRITE (`boundsX/Y/W/H`, the
same field the player's box already comes from), so it is per KIND, and its
big animated actor is a 7x6-TILE drawing with a 47x39 box while its sign is a
16x16 drawing whose box straddles the tile the post stands on. Blocking over the
metasprite makes big scenery far more solid than the reference. The box rides
`studio.toml [hitbox.<kind>]`, the per-kind rect the studio ALREADY edits (the
Inspector's object-type page) and draws on the scene canvas, and which had no
engine consumer since the composer was retired.

Two more places the port is deliberately not 1:1:

* **Actors block in TOPDOWN rooms only.** The reference engine's platform state blocks on
  an actor only when its `collision_group` carries the SOLID or PLATFORM flag -
  a rideable moving platform, a whole mechanism we do not convert - so 80 of
  the sample's 81 actors are walk-through there. That is why its big animated actor, in
  the platform room the game starts in, can be walked past.
* **A static "walk-through" is DATA.** The reference engine says it by running
  EVENT_ACTOR_COLLISIONS_DISABLE on `$self$` at the top level of the actor's
  own On Init; the importer folds that into `solid = false` and drops the
  event. A clear inside an `if` is a decision (the sample's stompable enemy stops
  colliding when you stomp it) and keeps the opcode, as does one aimed at
  ANOTHER actor (its pushable block clears the hidden stairs' collision when
  pushed).

Where the port IS one step off on purpose: the reference engine tests the box translated
one whole TILE ahead because its topdown movement is tile-quantised; ours
moves per pixel, so the caller passes the WOULD-BE position instead. The
overlap is ported, the grid offset is not.

Opt-in, because turning it on changes how every existing world plays (a
hand-authored game may well mean its decorative actors to be walked over). A
script clears it per actor with `actor_set_collision` (A_SET_COLLISION 0x2D),
the reference engine's EVENT_ACTOR_COLLISIONS_DISABLE.

ROM-measured twice, both on the reference-engine sample:

* the TOPDOWN half - spawned beside an NPC in the town room and walked
  up, the player stops with its sprite at world y **48**, flush under the
  NPC's authored 16x16 box at 40..55 (terrain in that column is clear for
  another four rows, so the stop is the actor);
* the PLATFORM half - walked right from the start of the parallax room,
  the player passes the big animated actor and reaches world x **465**, as the
  reference does.

Test coverage below is the source + generator + RefVM contract.
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
from mosaik_vm import isa
from mosaik_vm.refvm import RefVM
from mosaik_vm.rooms import emit_rooms_mos

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


BASE = {"types": ["topdown"], "uniform": True, "has_collision": True,
        "has_objects": True, "has_player_kind": True, "has_entity": True}
CLIPS = {"meta_w": 2, "meta_h": 2, "per_kind_size": True,
         "flip": False, "player": True}


def test_opcode_lockstep():
    """The op must be spelled identically in all five VM surfaces."""
    print("\n[A_SET_COLLISION lockstep]")
    check(isa.OPS.get("A_SET_COLLISION") == (0x2D, ["u8", "u8"]),
          "isa: A_SET_COLLISION = 0x2D (actor, on)")
    spec = _read("docs", "vm8-spec.md")
    check("| 2D | A_SET_COLLISION |" in spec, "spec §6 carries the row")
    core = _read("lib", "vm", "core.mos")
    check("const OP_A_SET_COLLISION = 0x2D" in core,
          "core.mos: the opcode constant")
    check("case OP_A_SET_COLLISION {" in core and
          "if VM_OP_A_SET_COLLISION {" in core,
          "core.mos: the arm behind its own dispatch-pruning guard")
    check("actor.set_collision(i, f8())" in core, "core.mos: the arm acts")


def test_event_lowering_and_refvm():
    print("\n[actor_set_collision -> RefVM]")
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "actor_activate", "actor": 0, "tile": 1, "x": 8, "y": 8},
            {"event": "actor_set_collision", "actor": 0, "on": False},
            {"event": "actor_activate", "actor": 1, "tile": 1, "x": 8, "y": 8},
            {"event": "stop"}]}])
    blob = bytes(prog.code)
    check(0x2D in blob, "the event lowers to the A_SET_COLLISION opcode")
    vm = RefVM(prog.code, entry=prog.entry)
    for _ in range(8):
        vm.frame()
    check(vm.actors[0].solid == 0, "the cleared actor is walk-through")
    check(vm.actors[1].solid == 1,
          "a freshly activated actor is SOLID (the reference engine's default)")
    # ... and turning it back on is the same op with on = 1
    prog2 = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "actor_activate", "actor": 0, "tile": 1, "x": 8, "y": 8},
            {"event": "actor_set_collision", "actor": 0, "on": False},
            {"event": "actor_set_collision", "actor": 0, "on": True},
            {"event": "stop"}]}])
    vm2 = RefVM(prog2.code, entry=prog2.entry)
    for _ in range(8):
        vm2.frame()
    check(vm2.actors[0].solid == 1, "... and re-enabling it works")


def test_runtime_sources():
    print("\n[lib/vm]")
    actor = _read("lib", "vm", "actor.mos")
    entity = _read("lib", "vm", "entity.mos")
    player = _read("lib", "vm", "player.mos")

    check("var a_solid: array[u8, VM_ACTOR_POOL]" in actor,
          "vm.actor: the per-slot flag")
    check(actor.count("a_solid[i] = 1") >= 1 and "a_solid[i] = 1" in actor,
          "vm.actor: reset() defaults it solid")
    check("a_solid[i] = 1              -- blocks the player" in actor,
          "vm.actor: activate() defaults it solid too (the reference engine's default)")
    check("function set_collision(i: u8, on: u8)" in actor and
          "function solid_of(i: u8) -> u8" in actor,
          "vm.actor: the setter + getter")
    check("set_collision" in actor.split("export", 1)[-1] or
          "set_collision, solid_of" in actor, "vm.actor exports them")

    check("function set_box(i: u8, w: u8, h: u8, ox: u8, oy: u8)" in actor,
          "vm.actor: the per-slot BLOCK box (size + offset inside the sprite)")
    check("function blocked(x: u16, y: u16) -> bool" in actor,
          "vm.actor: the overlap test")
    check("function blocked" not in entity,
          "... and NOT in vm.entity: an actor blocks whether or not it has "
          "scripts, so keying the scan off the script-slot registry made a "
          "placed decoration walk-through")
    check("player.pw" in actor and "player.ph" in actor,
          "... reads the player box SIZE rather than taking it as arguments, "
          "so the seam keeps g_solid's 2-argument pointer shape (the resident "
          "forwarding stub is charged to bank 0)")
    check("if n_solid == 0 {" in actor,
          "... and a room with no solid actor costs ONE compare per move")
    # It used to test `a_active[i] == 1` per slot. The scan now walks vm.actor's
    # LIVE list (S1 of the sprite-pipeline plan), where deactivate() unlinks the
    # slot, so being active is a property of what is scanned rather than a test
    # inside it - and the killed enemy is still not a wall.
    blocked_body = actor.split("function blocked")[1]
    check("var i: u8 = live[k]" in blocked_body and "k < n_live" in blocked_body,
          "... skips a DEACTIVATED actor (a killed enemy is not a wall): the "
          "scan walks the live list, which deactivate() removes it from")
    check("a_solid[i] != 0" in actor.split("function blocked")[1],
          "... and one whose collision flag is cleared")
    check("x < ax + aw and ax < x + pw and y < ay + ah and ay < y + ph" in actor,
          "... a HALF-OPEN overlap, so the player may rest adjacent")
    check("a_bw[i] != 0" in actor.split("function blocked")[1],
          "... and a kind with no box (solid = false) does not block at all")

    check("function set_actor_block(cb: function(u16, u16) -> bool)"
          in player, "vm.player: the seam (not an import - packs stay decoupled)")
    check("if has_ablock == 0 {" in player,
          "... unregistered costs one compare and is byte-identical")
    check("return g_ablock(x, y)" in player,
          "... and it is handed the BOX position - which is what px/py hold "
          "since K4, so no offset is applied here any more")
    # both movement paths must go through the combined test
    check("box_taken(px - pspeed, py)" in player and
          "box_taken(px + pspeed, py)" in player and
          "box_taken(px, py - pspeed)" in player and
          "box_taken(px, py + pspeed)" in player,
          "topdown: all four directions test terrain AND actors")
    # ...and the PLATFORM handler does NOT, which is the reference engine's behaviour for
    # every actor without the SOLID/PLATFORM group flag (80 of the sample's 81).
    plat = player.split("function update_platform")[-1]
    check("box_taken" not in plat,
          "platform: actors do NOT block (the reference engine gates that on a "
          "SOLID/PLATFORM collision group, which is not converted)")
    # The terrain test is span_solid, not box_solid: a sideways move only grows
    # the box into the columns between its old and new far edge, so only those
    # are scanned (same answer, a third of the collision probes). What matters
    # here is unchanged:
    # it is a plain TERRAIN test (no box_taken) and step-up still follows.
    check("span_solid(px + pw, nx + pw - 1, py)" in plat
          and "try_step(nx)" in plat,
          "... so its horizontal move keeps the plain terrain test + step-up")


def test_generator_is_opt_in():
    print("\n[generate_rooms]")
    off = emit_rooms_mos(dict(BASE, clips=dict(CLIPS)))
    check("set_actor_block" not in off,
          "absent: no registration at all (byte-identical)")
    on = emit_rooms_mos(dict(BASE, clips=dict(CLIPS), solid_actors=True))
    check("player.set_actor_block(actor.blocked)" in on,
          "present: start() wires the seam")
    check("actor.set_box(slot, sbw, sbh, 0, 0)" in on,
          "... a world that authors NO hitbox falls back to the drawn "
          "rectangle, which is what a hand-made game means by solid")
    # ... and with authored hitboxes the per-kind table drives it.
    boxed = emit_rooms_mos(dict(BASE, clips=dict(CLIPS), solid_actors=True,
                                nkinds=3,
                                kind_boxes={1: (16, 8, 0, 8),
                                            2: (0, 0, 0, 0)}))
    check("const KBW: array[u8, 3] = [ 0, 16, 0 ]" in boxed,
          "authored: the per-kind box table (a kind with solid = false is an "
          "all-zero row, so opting scenery out costs a row, not an opcode)")
    check("const KBH: array[u8, 3] = [ 0, 8, 0 ]" in boxed
          and "const KBY: array[u8, 3] = [ 0, 8, 0 ]" in boxed,
          "... with its size and its offset inside the sprite")
    check("actor.set_box(slot, KBW[fk], KBH[fk], KBX[fk], KBY[fk])" in boxed,
          "... registered per placed actor at activation")
    # A world with no interact slots still gets boxes once it is solid.
    no_int = emit_rooms_mos(dict(BASE, clips=dict(CLIPS), solid_actors=True,
                                 nkinds=3, kind_boxes={1: (16, 8, 0, 8)},
                                 has_obj_interact=False))
    check("actor.set_box(slot, KBW[fk]" in no_int,
          "... including a world whose actors have no On Interact script")


def main():
    test_opcode_lockstep()
    test_event_lowering_and_refvm()
    test_runtime_sources()
    test_generator_is_opt_in()
    print()
    if _FAILED:
        print("SOME CHECKS FAILED (%d)" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
