#!/usr/bin/env python3
"""THE ACTOR POOL IS WALKED AS A LIVE LIST, not slot by slot (S1).

`[build] actor_pool` is sized for the BUSIEST room - a reference-engine conversion
needs 15 - and every per-frame pass used to test all of them. Measured on
the reference-engine sample conversion (GBC, the parallax room, two live actors): about
790 cycles for each unused slot, ~10.3k of `vm.actor.render`'s 38k, and again
in `vm.canim.tick_all` where each slot costs a cross-module (under
`[build] code_banks`, cross-BANK) `clip_of` call.

The reference engine walks an ACTIVE list for the same reason
(`actors_active_tail`, `build/src/src/core/actor.c`).

What changed, and what each piece protects:

* `live[]` + `n_live` - a compact ASCENDING array, not a linked list. Iteration
  is an indexed read rather than a pointer chase; ascending order keeps OAM
  written in the same order as the old `for i in 0..ACTORS`, which is what
  decides sprite priority when a fan overflows; and an EMPTY list is the
  all-zero BSS state, where a stale head byte would have walked a dead slot.
* The hide of an inactive slot MOVED to where the slot leaves the list
  (`deactivate`/`reset`), because a pass that never visits an idle slot cannot
  hide it. OAM is persistent on every backend, so a hide issued once holds.
  `reset` hides EVERY slot, including ones never activated: on the GB an
  untouched slot is all-zero, which is off-screen by luck, but the Lynx and PCE
  ports keep sprite state in RAM tables whose zero is the VISIBLE top-left
  corner. Caught by diffing OAM against the old build, not by reading the code.
* `n_retire` - the one thing an active-only walk cannot see is a slot that
  RETIRED still holding engine.anim state. `vm.canim` watches the counter and
  sweeps the pool on the frames where one happened. That sweep is also where a
  ROOM LOAD drops every slot's metasprite upload cache, which fixed a bug the
  cache shipped with (see the sweep's own comment: the big animated actor in
  the parallax room wore four of the previous room's enemy tiles).

Measured after (idle, same room, 120 game frames): `render` 37,888 -> 26,584,
`canim_tick_all` 17,814 -> 9,566, `step_all` 2,720 -> 964 cycles; the player's
trajectory is bit-identical over 460 game frames and the resident image does
not move on any of the four GBDK targets.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _body(src, sig):
    """The text of one function, up to the next same-indent `function`."""
    after = src.split(sig, 1)[1]
    cut = after.find("\n    function ")
    cut2 = after.find("\n    local function ")
    for c in (cut, cut2):
        if c != -1:
            after = after[:c]
    return after


def test_the_list():
    print("\n[vm.actor: the live list]")
    actor = _read("lib", "vm", "actor.mos")
    check("var live: array[u8, VM_ACTOR_POOL]" in actor and
          "var n_live: u8" in actor,
          "a compact array of live slot ids + a count")
    check("var n_live: u8\n" in actor and "var n_live: u8 = 0" not in actor,
          "...and it is BSS, not an initializer: an empty list is the "
          "all-zero boot state (bank-0 plan O1)")
    link = _body(actor, "local function link(i: u8)")
    check("live[k] = live[k - 1]" in link and "live[k] = i" in link,
          "link() inserts in ASCENDING slot order, so OAM keeps being written "
          "in the order the old pool walk used (sprite priority)")
    unlink = _body(actor, "local function unlink(i: u8)")
    check("n_live -= 1" in unlink and "live[k] = live[k + 1]" in unlink,
          "unlink() closes the gap")


def test_every_per_frame_pass_walks_it():
    print("\n[vm.actor: the per-frame passes]")
    actor = _read("lib", "vm", "actor.mos")
    # `blocked` is the player-move test; since the player-CONTACT On Hit scan
    # needs the SAME overlap but wants the SLOT rather than a yes/no, the walk
    # lives in `overlap_at` and `blocked` delegates. The invariant this pins is
    # unchanged - no per-frame pass may walk the whole pool.
    # step_all's walk lives in `step_live` since the move gained LCD pacing
    # (both arms of the module-level fork call it), so the walk is checked
    # where it now is - the property that matters is that no per-frame pass
    # scans the whole pool, not which function holds the loop.
    for sig, what in (("function render(locked: u8)", "render"),
                      ("local function step_live()", "step_live"),
                      ("function overlap_at(x: u16, y: u16) -> u8", "overlap_at")):
        body = _body(actor, sig)
        check("k < n_live" in body and "live[k]" in body,
              "%s walks the live list" % what)
        check("0..ACTORS" not in body,
              "... and no longer the whole pool" if what == "render"
              else "... %s: no whole-pool walk left" % what)
    blocked = _body(actor, "function blocked(x: u16, y: u16) -> bool")
    check("overlap_at(" in blocked and "live[k]" not in blocked,
          "blocked delegates to overlap_at rather than keeping a second walk")


def test_the_hide_moved_to_where_the_slot_leaves():
    print("\n[vm.actor: parking]")
    actor = _read("lib", "vm", "actor.mos")
    park = _body(actor, "local function park(i: u8)")
    check("sprite.move(base, 200, 200)" in park and "a_parked[i] = 1" in park,
          "park() hides a slot off-screen, once")
    check("if stride > 1 {" in park,
          "... re-fanning the metasprite first, so it hides as a block")
    deact = _body(actor, "function deactivate(i: u8)")
    check("unlink(i)" in deact and "park(i)" in deact,
          "deactivate() unlinks AND hides - render() no longer gets the chance")
    check("n_retire += 1" in deact,
          "... and counts the retirement for vm.canim")
    reset = _body(actor, "function reset()")
    check("for i in 0..ACTORS {\n            park(i)" in reset
          and "n_live = 0" in reset,
          "reset() hides EVERY slot, then empties the list: the previous "
          "room's actors, and also a slot that is never activated - render() "
          "used to hide that one on the first frame and now never sees it")
    check("n_retire += 1" in reset, "... and counts that too")
    act = _body(actor, "function activate(i: u8, tile: u8, x: u16, y: u16)")
    check("if a_active[i] == 0 {" in act and "link(i)" in act,
          "activate() joins the list once - re-activating never doubles it")


def test_the_accessors_are_exported():
    print("\n[vm.actor: the surface vm.canim needs]")
    actor = _read("lib", "vm", "actor.mos")
    for fn in ("live_count", "live_at", "retires"):
        check("function %s" % fn in actor, "%s() exists" % fn)
    exports = "\n".join(l for l in actor.splitlines()
                        if l.strip().startswith("export "))
    check("live_count" in exports and "live_at" in exports
          and "retires" in exports, "all three are exported")


def test_canim_walks_it_and_sweeps_on_a_retirement():
    print("\n[vm.canim]")
    canim = _read("lib", "vm", "canim.mos")
    tick = _body(canim, "function tick_all()")
    # Narrowed 2026-08-15 from the LIVE list to the VISIBLE subset of it:
    # actor.render() already decides off-window per slot, so it publishes the
    # visible ones and canim animates only those. A parked actor now costs this
    # loop nothing at all - previously it cost five banked accessor calls to
    # discover it was parked. See offscreen_anim_gate_test.py.
    # DIRECT reads since 2026-09-03: the count and the slot are exported
    # variables (a plain load each), not the vis_count()/vis_at() trampolines
    check("actor.n_vis" in tick and "actor.vis[c]" in tick,
          "tick_all walks the VISIBLE live slots")
    check("while i < actor.ACTORS" not in tick,
          "... and not the whole pool")
    check("actor.retires()" in tick and "sweep_retired()" in tick,
          "... sweeping for retired anim state only when a slot actually left")
    sweep = _body(canim, "local function sweep_retired()")
    check("anim.clear(i)" in sweep and "a_pstate[i] = 255" in sweep,
          "the sweep stops a retired slot's engine.anim animator")
    # The a_uok clear must NOT be nested inside the clip == 255 test. A room
    # load re-uses the pool from slot 0 up, so a slot very often lands on the
    # SAME OAM base in the new room with a live clip - and a cached (tile,
    # base) match then skips the sprite.set_meta that re-fans the metasprite at
    # its own w x h, leaving the cells the previous actor did not cover showing
    # the PREVIOUS ROOM's tiles. On the reference-engine sample conversion that stuck four enemy tiles
    # onto the big animated actor. See the sweep's comment.
    check(sweep.index("a_uok[i] = 0") < sweep.index("anim.clear(i)"),
          "... and drops EVERY slot's metasprite upload cache, not just the "
          "retired ones - a room load re-uses a slot at the same OAM base, and "
          "a cached hit there leaves the previous room's tiles inside the new "
          "actor's fan")
    check("a_uok[i] = 0" in tick,
          "... and an ACTIVE slot whose clip was cleared is still handled "
          "inline, as before")


def test_set_base_resets_the_fan_record():
    print("\n[vm.actor: the metasprite record at a base re-assignment]")
    actor = _read("lib", "vm", "actor.mos")
    base = _body(actor, "function set_base(i: u8, b: u8)")
    # Under external animation render() only MOVES a fan - the metasprite
    # record is written by vm.canim's apply, which runs AFTER the first
    # render of a freshly loaded room. So the first place() of the new
    # room's actor fanned through whatever record the PREVIOUS room left at
    # that OAM base: on the reference-engine sample conversion, opening the quest menu from the
    # parallax room re-used the big actor's base (slot 6) for a 1-tile
    # checkbox, whose first move dragged all 21 of its children on-screen
    # over the quest list - and a fan the new record no longer covers is
    # never re-parked, so they stayed. The canim upload-cache sweep cannot
    # close this (it only makes set_meta re-run, later); the record must be
    # reset where the room allocator assigns the base.
    check("sprite.set_meta(b, a_tile[i], 1, 1)" in base,
          "set_base() resets the base slot's record to 1x1")
    check("if b != NO_OAM {" in base,
          "... skipping a parked (NO_OAM) assignment")


def main():
    test_the_list()
    test_every_per_frame_pass_walks_it()
    test_the_hide_moved_to_where_the_slot_leaves()
    test_the_accessors_are_exported()
    test_canim_walks_it_and_sweeps_on_a_retirement()
    test_set_base_resets_the_fan_record()
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
