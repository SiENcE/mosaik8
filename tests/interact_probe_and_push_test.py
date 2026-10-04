#!/usr/bin/env python3
"""THE INTERACT PROBE FACES; A PUSHED ACTOR STOPS AGAINST ANOTHER ACTOR.

Two divergences from the reference engine in the reference-engine sample conversion's rock pocket
(a room with pushable rocks), both reported from play, both measured on BOTH
ROMs before anything was changed (with a local probe).

The pocket places three 2-tile rocks flush in a row - rock A at tile x 19..20,
rock B at 21..22, rock C at 23..24 - and the player box is 16 px, TWO tiles.

## 1. Standing under the LEFT rock and pressing A shoved the MIDDLE one

`vm.entity.boxes_touch` inflated the player's box by `INTERACT_REACH` on ALL
FOUR SIDES. That is a different test from the reference engine's, not a looser one:
`actor_with_script_in_front_of_player` TRANSLATES the box one grid step in the
facing direction (`point_translate_dir_word(&offset, PLAYER.dir,
PX_TO_SUBPX(grid_size))`) and then asks for a plain overlap. The lateral slack
made the neighbouring rock a candidate, and the ascending scan preferred it
for having the lower registration index. Measured, standing flush under each
rock in turn:

    stand x     ours (before)   reference
    152         rock B          rock A
    168         rock C          rock B
    184         rock C          rock C

The edges are EXCLUSIVE, which is the reference's too: it spells a 16 px box
as bounds `left 0, right 511` in 1/32 px subpixels - 15.97 px, one subpixel
short of touching the next box - read off its own `_actors` array, not
assumed. So two boxes sharing an edge do not overlap there either.

The SCAN runs descending, which is the reference's tie-break. Two candidates
is not a corner case here: every 8 px stand position between two rocks reaches
both. The reference VM walks its ACTIVE list from `PLAYER.prev`, and `DL_PUSH_HEAD` builds
that list as actors activate, so the walk visits the LAST-activated first -
for a room whose actors all come up at load, descending placement order.
Measured: straddling rock A/rock B the reference shoves rock A, straddling
rock B/rock C it shoves rock B - the higher index both times, where ascending
gave the other one both times. After both changes the two ROMs pick the same
rock at all five stand positions.

**The `near()` path keeps its own origin.** It is the native VM8 authoring
model - a chebyshev radius around the player, with no facing in it - and has
no reference-engine counterpart to be faithful to, so it measures from the player's
box and not from the translated probe. Measured 2026-09-07: feeding it the
translated origin made "walk up to the NPC and press A" work from one side
only, and broke the On Interact check in vm-overworld, vm-quest, vm-rpg and
vm-shop, none of which register an interact box. It does share the new scan
ORDER, which is the one thing here a box-less project can observe - only
where two entities are within reach at once, where the pick was arbitrary
either way.

## 2. A rock shoved into another rock landed ON it

`actorPush` lowers with `ACTOR_ATTR_CHECK_COLL` unconditionally
(`scriptBuilder.ts`), and that constant is WALLS 0x02 | ACTORS 0x20;
`vm_actor_move_to` tests `actor_overlapping_bb` after every step and stops the
move flush against whatever it hits. Ours checked walls only (`edge_solid`).
Measured with the same route on both ROMs - shove rock A up, step into the
cells it left, shove rock B right at rock C:

    ours        rock B (168,208) -> (184,208)   = rock C's own cell
    reference   rock B refused to move at all

Two rocks in one place is a corrupted puzzle, so this is not cosmetic.

Costs nothing resident: `vm.entity` and `vm.actor` both bank on a conversion,
and the reference-engine sample conversion's bank-0 image is unchanged at 0x3D63 (gameboy_color) /
0x3CA0 (gameboy) either way.
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

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def main():
    print("The interact probe faces; a push stops against an actor")
    print("=" * 55)
    with open(os.path.join(ROOT, "lib", "vm", "entity.mos"),
              encoding="utf-8") as f:
        ent = f.read()
    with open(os.path.join(ROOT, "lib", "vm", "actor.mos"),
              encoding="utf-8") as f:
        act = f.read()

    # --- 1a. the box test carries no slack any more -----------------------
    touch = ent.split("local function boxes_touch(")[1]
    touch = touch[:touch.index("\n    }")]
    check("boxes_touch is a PLAIN overlap - no reach on any side",
          "INTERACT_REACH" not in touch, touch)
    check("...and its four edges are exclusive, as the reference's are",
          touch.count("if px >= ax + aw {") == 1
          and touch.count("if ax >= px + pw {") == 1
          and touch.count("if py >= ay + ah {") == 1
          and touch.count("if ay >= py + ph {") == 1)

    # --- 1b. the reach moved onto the FACING ------------------------------
    check("the probe origin is translated by the reach, per axis",
          "local function probe_x(px: u16, f: u8) -> u16 {" in ent
          and "local function probe_y(py: u16, f: u8) -> u16 {" in ent)
    find = ent.split("local function find() -> u8 {")[1]
    find = find[:find.index("\n    }")]
    check("find() asks the player which way it faces",
          "player.face()" in find, find[:200])
    check("...and probes from there, not from the box origin",
          "var px: u16 = probe_x(bx, pf)" in find
          and "var py: u16 = probe_y(by, pf)" in find, find[:400])
    # TWO origins on purpose: only the BOX test is the reference engine's. The RADIUS
    # test is the native VM8 model (a chebyshev distance, no facing in it),
    # and measuring it from the translated origin made "walk up to the NPC
    # and press A" work from one side only - it broke the On Interact check
    # in vm-overworld, vm-quest, vm-rpg and vm-shop, none of which register
    # an interact box.
    check("...while the RADIUS test keeps the player's own box origin",
          "var bx: u16 = player.box_x()" in find
          and "var by: u16 = player.box_y()" in find
          and "near(bx, by, ax, ay)" in find)
    # A u16 world position one tile from the map edge would otherwise probe
    # the far corner of the world.
    for fn, lo in (("probe_x", "if px < INTERACT_REACH {"),
                   ("probe_y", "if py < INTERACT_REACH {")):
        body = ent.split("local function %s(" % fn)[1]
        body = body[:body.index("\n    }")]
        check("%s clamps at 0 rather than wrapping" % fn, lo in body, body)

    # --- 1c. the tie-break is the reference's -----------------------------
    check("find() scans DESCENDING (the reference VM's PLAYER.prev walk)",
          "var i: u8 = NENT" in find and "while i > 0 {" in find
          and "i -= 1" in find, find[:400])
    check("...and the old ascending loop is gone",
          "for i in 0..NENT {" not in find)

    # The native authoring model is deliberately NOT converted to the
    # directional rule - it has no reference-engine counterpart.
    check("the near() radius path still exists for box-less entities",
          "local function near(" in ent and "near(bx, by, ax, ay)" in find)

    # --- 2. push checks actors as well as walls ---------------------------
    check("there is an actor-vs-actor test for the push",
          "local function push_blocked(" in ent or
          "local function push_blocked(" in act)
    push = act.split("function push(i: u8, tiles: u8) {")[1]
    push = push[:push.index("move_start(i, x, y)")]
    check("push() asks it on the same step the wall test guards",
          "push_blocked(i, nx, ny, bw, bh, ox, oy)" in push, push[-400:])
    check("...only when the walls let the step through (no wasted walk)",
          "if hit == false {" in push)
    blocked = act.split("local function push_blocked(")[1]
    blocked = blocked[:blocked.index("\n    }")]
    check("the walk skips the pushed actor itself", "if j != me {" in blocked)
    check("...walks the LIVE list, so a retired actor stops blocking",
          "while k < n_live" in blocked and "live[k]" in blocked)
    check("...and only solid, boxed actors block",
          "if a_bw[j] != 0 {" in blocked and "if a_solid[j] != 0 {" in blocked)
    check("...with the same half-open box test as overlap_at",
          "mx < ax + aw and ax < mx + mw and my < ay + ah and ay < my + mh"
          in blocked)
    check("an empty solid set costs no walk", "if n_solid == 0 {" in blocked)

    print("")
    if FAILS:
        print("FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("interact_probe_and_push_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
