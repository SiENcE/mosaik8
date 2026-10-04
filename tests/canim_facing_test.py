#!/usr/bin/env python3
"""Two reference-engine parity rules in `vm.canim`, both found on the converted sample.

1. **A period of 255 is PAUSED, not slow.** The reference engine gates its whole frame
   step on `anim_tick != ANIM_PAUSED` (255), and the importer emits 255 for a
   kind whose frame a SCRIPT picks. `engine.anim` steps at `tick >= period`, so
   255 was not "never" but "every 255 frames" - measured on the reference-engine sample conversion, the
   Quest menu's six checkboxes ticked themselves from unchecked to checked and
   back (tile 24 -> 26 at frame 360, back at 840, again at 1440) with no quest
   completed.

2. **`actor->dir` is the single source of an actor's facing.** The reference engine's
   `actor_set_dir(actor, dir, moving)` writes one field and picks the idle or
   moving animation from it. Ours derived the facing from the movement delta
   ONLY, so `actor_set_dir` had nothing to show: the sample hands an NPC
   `dir left` before an emote and it never turned round. Movement still OWNS the
   facing while it lasts - it publishes it on the actor - so a stopping actor
   keeps looking the way it walked instead of snapping back to its spawn dir.

SOURCE-CONTRACT test, the same shape as canim_flip_repos_test.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "lib", "vm")


def check(label, cond):
    print("[%s] %s" % ("PASS" if cond else "FAIL", label))
    return bool(cond)


def _read(name):
    with open(os.path.join(VM, name), encoding="utf-8") as f:
        return f.read()


def _body(src, name):
    m = re.search(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    indent = m.group(1)
    end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
    return src[m.start():end.end() if end else len(src)]


def main():
    canim = _read("canim.mos")
    actor = _read("actor.mos")
    ok = True

    tick_all = _body(canim, "tick_all")
    tick_player = _body(canim, "tick_player")
    ok &= check("tick_all() exists", bool(tick_all))
    ok &= check("tick_player() exists", bool(tick_player))

    # --- 1. ANIM_PAUSED ------------------------------------------------------
    # The actor arm must not hand engine.anim a frame COUNT it can step through
    # when the period says paused; arming one frame keeps apply() - and so the
    # script's frame pin - working exactly as before.
    m = re.search(r"if per == 255 \{\s*\n\s*cnt = 1\s*\n\s*\}", tick_all)
    ok &= check("tick_all(): period 255 arms ONE frame (ANIM_PAUSED)", bool(m))
    ok &= check("tick_all(): the pause check precedes anim.set",
                bool(m) and m.end() < tick_all.index("anim.set("))
    ok &= check("tick_player(): period 255 stops the self-timed advance",
                re.search(r"if pper == 255 \{\s*\n\s*cnt = 0", tick_player)
                is not None)
    # The player path must read the period ONCE - the compare and the step have
    # to be the same value or a future edit can make them disagree.
    ok &= check("tick_player(): one g_period read, reused by the step",
                tick_player.count("g_period(p_clip, st)") == 1
                and "p_tick >= pper" in tick_player)

    # --- 2. facing comes from the actor, and movement publishes it -----------
    # The standing read rides the R2 batched actor.anim_in (its low two bits
    # ARE a_dir - the same single source of facing, one call instead of four).
    ok &= check("tick_all(): a STANDING actor takes its facing from the actor",
                "a_face[i] = ai & 3" in tick_all
                and "a_dir[i] & 3" in _read("actor.mos"))
    ok &= check("tick_all(): movement PUBLISHES the facing it derived",
                "actor.set_dir(i, a_face[i])" in tick_all)
    # ...and in that order: the delta branch writes, the else branch reads.
    wr = tick_all.index("actor.set_dir(i, a_face[i])")
    rd = tick_all.index("a_face[i] = ai & 3")
    ok &= check("tick_all(): the write is the MOVING arm, the read the standing one",
                wr < rd)
    # Once per live actor, not per axis - this is the per-frame path.
    ok &= check("tick_all(): one write and one read per actor",
                tick_all.count("actor.set_dir(i,") == 1
                and tick_all.count("a_face[i] = ai & 3") == 1)
    ok &= check("vm.actor exports set_dir + dir_of",
                re.search(r"^\s*export .*\bset_dir\b", actor, re.M) is not None
                and re.search(r"^\s*export .*\bdir_of\b", actor, re.M) is not None)
    # `reset()` zeroes a_dir, so a room's actors start facing DOWN unless a
    # script says otherwise - which is what the importer's authored-direction
    # actor_set_dir relies on.
    rs = _body(actor, "reset")
    ok &= check("actor.reset() zeroes the facing (down is the room default)",
                "a_dir[i] = 0" in rs)

    # --- 3. A PLACEMENT IS NOT A MOVE (an RPG conversion, 2026-09-08) -------
    # `activate` is the SPAWN form - a room load dropping a slot at its
    # authored spot - and the jump from wherever that slot last stood must not
    # be read as movement: the delta arm above would derive a facing from it
    # and PUBLISH it. Measured on the RPG check conversion's shopkeeper, authored `down`
    # and drawn by reference-engine facing down: placed at (120,48) after its slot
    # had stood at (0,0) it "walked right" and stood in its SIDE pose for
    # ever. `a_appear` is the mechanism (canim re-seeds a_px/a_py and takes
    # the standing arm that once); only place() and the un-hide edge raised
    # it, and neither runs for a fresh placement.
    act = _body(actor, "activate")
    ok &= check("activate() exists", bool(act))
    ok &= check("activate(): raises the appear edge (a placement is not a move)",
                "a_appear[i] = 1" in act)
    # ...and the facing resets, the other half of the same bug: generate_rooms
    # emits actor_set_dir ONLY for a non-down placement, because down is the
    # documented runtime default - true of a fresh pool, false of a REUSED
    # slot still carrying the previous room's facing.
    # ...through set_dir, not a raw write: it is a_dir's one writer and owns
    # the animator edge the quiet walk keys on (actor_anim_edge_test pins it).
    ok &= check("activate(): resets the facing to DOWN (the room default)",
                "set_dir(i, 0)" in act)
    # Both must land AFTER the position write, or canim re-seeds from the old
    # position and the jump is back.
    if act:
        ok &= check("activate(): both follow put_pos, so canim re-seeds the NEW spot",
                    act.index("put_pos(") < act.index("a_appear[i] = 1")
                    and act.index("put_pos(") < act.index("set_dir(i, 0)"))
    # reactivate() is deliberately NOT touched: it re-arms an actor the room
    # already placed, and restoring what it had is its whole job.
    rea = _body(actor, "reactivate")
    ok &= check("reactivate() does NOT reset the facing (it restores)",
                bool(rea) and "set_dir(i, 0)" not in rea)

    # --- it still builds ----------------------------------------------------
    try:
        from mosaik import Lexer, Parser
        # The pool arrays are sized by a BUILD-supplied define, so the module
        # only parses with one in hand - the same shape mosaik8_build passes.
        Parser(Lexer(canim).tokenize(),
               defines={"VM_ACTOR_POOL": 8, "VM_ANIM_SLOTS": 8,
                        "VM_META_MASK": 0, "VM_META_LIST": 0}).parse()
        ok &= check("canim.mos parses", True)
    except Exception as exc:                                 # pragma: no cover
        ok &= check("canim.mos parses (%s)" % exc, False)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
