#!/usr/bin/env python3
"""An actor nobody can see is not animated (the off-window gate).

A VM8 room may hold a full actor pool spread along a WIDE level with one or
none of them on screen. Before 2026-08-15 every live actor was ticked, had its
facing and animation state derived, and had its metasprite re-asserted into OAM
every frame regardless of where it was - all of it invisible.

Measured on the converted `space/Space Battle` (255 tiles wide, a full 15-actor
pool, usually nothing but the player on screen), with `emu/gb_profile.py`:

    stage                 before    after
    vm_canim_apply       108,024    2,344 cycles
    vm_canim_tick_all     76,481   50,515
    total work per frame   4.44     2.63  LCD frames

    LCD frames per GAME frame:  idle 4.29 -> 3.20, walking 5.22 -> 3.20,
                                shooting 5.85 -> 2.76

**the reference engine does MORE than this**: `core/actor.c`'s `actors_update` deactivates
an offscreen actor outright, drops it from the active list and terminates its
update script (amortised - it checks one actor in four per frame). Gating the
ANIMATION is the same saving without changing what a script may assume about a
live actor, which is why it was done this way round.

The first cut walked the live list and SKIPPED parked slots, which still cost
five banked accessor calls per parked slot per frame - `vm.actor` is a banked
module, so every accessor `vm.canim` calls is a trampoline, and that was most of
what a parked slot cost. `render()` already decides visibility, so it now
publishes the visible SUBSET and canim walks that: a parked actor costs the
animation path nothing at all.

Pinned here:
  * `vm.actor.render` decides off-window once per slot (camera read ONCE for the
    whole pool), publishes the visible subset, and skips the metasprite upload
    for a parked slot;
  * `vm.canim.tick_all` walks `vis` and never visits a parked slot;
  * a slot coming back on screen raises `a_appear`, which `tick_all` consumes to
    SEED its movement delta - otherwise a re-entering actor measures the delta
    against wherever it was when it left and derives a facing from that jump.

Behaviour on real hardware is covered by `projects/vm-shmup`, `vm-combat`,
`vm-showcase` and (locally) the platformer port, whose verify suites all exercise actors
entering and leaving the window.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(cond, msg):
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILS.append(msg)


def _src(mod):
    return open(os.path.join(ROOT, "lib", "vm", "%s.mos" % mod),
                encoding="utf-8").read()


def _body(src, name):
    m = re.search(r"function %s\([^)]*\)[^{]*\{" % re.escape(name), src)
    if not m:
        return ""
    i, depth = m.end(), 1
    while i < len(src) and depth:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return src[m.end():i]


def test_actor_publishes_the_park_state():
    print("\n[vm.actor owns the decision]")
    src = _src("actor")
    check("function parked_of(" in src, "vm.actor exposes parked_of(i)")
    check(re.search(r"export .*\bparked_of\b", src) is not None,
          "parked_of is exported")
    check("local function off_window(" in src,
          "the off-window test is one shared local")
    rd = _body(src, "render")
    check("off_window(i," in rd, "render() asks it per slot")
    check("player.cam_x()" in rd and rd.count("player.cam_x()") == 1,
          "render() reads the camera exactly ONCE for the whole pool")


def test_render_skips_the_upload_when_parked():
    print("\n[no upload for an invisible actor]")
    rd = _body(_src("actor"), "render")
    check("off == false" in rd,
          "the set_tile/set_meta upload is gated on being on-window")
    # the placement itself must still run, or the park would never be recorded
    check("place(i, base," in rd,
          "place() still runs for every slot (it is what records the park)")
    check("vis[n_vis] = i" in rd and "n_vis = 0" in rd,
          "render publishes the VISIBLE subset while it is deciding anyway")


def test_canim_walks_only_the_visible_slots():
    print("\n[vm.canim never visits a parked slot at all]")
    body = _body(_src("canim"), "tick_all")
    check("actor.n_vis" in body and "actor.vis[c]" in body,
          "tick_all walks the VISIBLE subset, not the live list "
          "(as direct variable reads since 2026-09-03)")
    check("actor.live_count()" not in body,
          "... so it does not walk the live list and test each slot")
    # The first cut DID walk the live list and skip parked slots, which still
    # cost five banked accessor calls per parked slot per frame - vm.actor is a
    # banked module, so every accessor out of vm.canim is a trampoline. Having
    # render() publish the subset it already computes removes that entirely.
    check("actor.parked_of(i)" not in body and "actor.a_parked[" not in body,
          "... and does not test per slot whether it is parked at all")


def test_a_reappearing_actor_reseeds_its_delta():
    print("\n[coming back on screen]")
    body = _body(_src("canim"), "tick_all")
    # The appear edge rides the R2 batched read (actor.anim_in, bit 3), which
    # CONSUMES it exactly as the read-and-clear appeared() accessor did.
    m = re.search(r"if ai & 8 != 0 \{(.*?)\}", body, re.S)
    check(m is not None, "tick_all asks whether the slot just reappeared")
    check("a_appear[i] = 0" in _src("actor").split("function anim_in", 1)[1][:600],
          "...and anim_in consumes the edge (read-and-clear, as appeared() was)")
    if m:
        arm = m.group(1)
        check("a_px[i]" in arm and "a_py[i]" in arm,
              "and SEEDS the movement delta (an actor that has been off screen "
              "has a stale position; deriving a facing from that jump is wrong)")
    act = _src("actor")
    check("function appeared(" in act and "a_appear[i] = 0" in act,
          "vm.actor's appeared() is READ-AND-CLEAR, so the seed happens once")
    pl = _body(act, "place")
    check("a_appear[i] = 1" in pl,
          "the flag is raised on the parked -> visible transition in place()")


def main():
    print("=" * 50)
    print("Off-window actors are not animated")
    print("=" * 50)
    test_actor_publishes_the_park_state()
    test_render_skips_the_upload_when_parked()
    test_canim_walks_only_the_visible_slots()
    test_a_reappearing_actor_reseeds_its_delta()
    print("\n" + "=" * 50)
    if FAILS:
        print("FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("All off-window gate checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
