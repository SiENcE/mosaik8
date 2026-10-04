#!/usr/bin/env python3
"""An actor whose clip has no frames for its current STATE must fall back to idle.

`vm.canim.tick_all` derives an actor's animation state from its movement delta
(0 idle / 1 walk) and (re)starts its `engine.anim` slot on a state or facing
change - but only `if cnt > 0`, i.e. only when the clip actually has frames for
that state. It latched `a_pstate` either way, so a kind with a SINGLE idle
animation (a reference-engine enemy: every `[animations.<kind>]` there carries state 0
for all four facings and nothing else) was asked for a WALK clip it does not
have and simply never got an animator slot.

`apply` is the ONLY thing that calls `sprite.set_meta` for an actor
(`actor.render` is in external-animation mode and just positions it), so such an
actor was never FANNED: one stale OAM object at its base carrying whatever tile
the room-load sweep left there, the rest of its metasprite parked off-screen.
An actor that starts moving before it has ever settled - which is what happens on
the frame after a room load, where the movement delta is measured against the
PREVIOUS room's `a_px`/`a_py` - was never drawn correctly at all.

Measured on the reference-engine sample conversion's long walk-in room, which places three
enemy actors that chase the player: each drew ONE object with tile 0 (the
player's art) instead of its own 2x2 fan at VRAM 40/42.

`tick_player` already had this guard for the platform jump/fall states ("ONLY
when the clip actually provides them"); this is the same rule on the actor path.

SOURCE-CONTRACT test: it pins that the fallback exists and runs BEFORE both the
`a_astate` latch that `apply` reads and the change test that arms `anim.set`.
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

CANIM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "lib", "vm", "canim.mos")


def check(label, cond):
    print("[%s] %s" % ("PASS" if cond else "FAIL", label))
    return bool(cond)


def _body(src, name):
    """The text of function `name` (to its closing brace at the same indent)."""
    m = re.search(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name),
                  src, re.M)
    if not m:
        return ""
    indent = m.group(1)
    end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
    return src[m.start():end.end() if end else len(src)]


def main():
    with open(CANIM, encoding="utf-8") as f:
        src = f.read()
    ok = True

    tick = _body(src, "tick_all")
    ok &= check("tick_all() exists", bool(tick))

    fallback = re.search(
        r"if g_count\(k, st, a_face\[i\]\) == 0 \{\s*\n\s*st = 0\s*\n\s*\}", tick)
    ok &= check("tick_all() falls back to idle when the state has no frames",
                fallback is not None)

    if fallback:
        latch = tick.index("a_astate[i] = st")
        arm = tick.index("if st != a_pstate[i]")
        ok &= check("the fallback runs BEFORE the a_astate latch apply() reads",
                    fallback.start() < latch)
        ok &= check("the fallback runs BEFORE the anim.set change test",
                    fallback.start() < arm)

    # apply() draws from a_astate[i], so the fallback has to reach it - a frame
    # looked up in a state with no clip reads F_OFF 0, i.e. another kind's tiles.
    ap = _body(src, "apply")
    ok &= check("apply() reads a_astate[i] for the frame",
                "g_frame(k, a_astate[i]" in ap)
    ok &= check("apply() is what fans the metasprite (the draw_frame upload)",
                "draw_frame(base," in ap)

    # the player path's equivalent guard must stay - it is the precedent
    tp = _body(src, "tick_player")
    ok &= check("tick_player() keeps its own no-frames guard",
                "if g_count(p_clip, js, p_face) > 0 {" in tp)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
