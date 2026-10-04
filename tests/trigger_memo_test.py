#!/usr/bin/env python3
"""The trigger scan must not re-ask a question whose inputs did not move.

`vm.trigger.update` walks every rect in the room with four u16 compares each,
once per unlocked frame. Measured on the reference-engine sample conversion's town room (14 rects)
with the player STANDING STILL: **9,458 cycles a frame** - 11% of an LCD frame,
and the largest single item in that room's idle budget after the sprite
pipeline (the frame-budget harness
charged it to `put_player`, the stage hook in front of it).

So `update` remembers the box its current `last` verdict was decided at, and
returns early while the box has not moved.

THE WHOLE RISK IS THE MEMO GOING STALE, and the two directions are not
symmetric (the `n_on` / `a_moved` lesson again):
  - reading LOW (memo dropped when it need not be) costs one scan that finds
    what it already knew - the safe direction;
  - reading HIGH (memo kept when the rects changed under it) is a trigger that
    NEVER FIRES AGAIN, silently, in whichever room re-uses that slot.
So every writer of the rect table drops it, and this pins that.

SOURCE-CONTRACT test. The behaviour is pinned on the ROM by
`tools/trigprobe/trigger_memo_probe.py` (enter / stay / leave / RE-ENTER, the
last being the edge a stale memo swallows) and by `door_probe.py`, whose two
arms exercise the FORCE path through a memo hit.
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

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "lib", "vm", "trigger.mos")


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    return bool(cond)


def bodies(src, name):
    """Every definition of `name` (the module forks on two build flags, so
    `update` and `add` each have several arms and ALL of them must comply)."""
    out = []
    for m in re.finditer(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name),
                         src, re.M):
        indent = m.group(1)
        end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
        out.append(src[m.start():end.end() if end else len(src)])
    return out


def main():
    src = open(SRC, encoding="utf-8").read()
    ok = True

    ok &= check("the memo's key and validity flag exist",
                all(re.search(r"^\s*var %s: u(8|16)" % n, src, re.M)
                    for n in ("lqx", "lqy", "lqok")))
    forget = bodies(src, "forget")
    ok &= check("forget() is the one drop funnel", len(forget) == 1
                and "lqok = 0" in forget[0])

    # THE INVARIANT: nothing but update() may raise the flag, and every writer
    # of the rect table must drop it.
    raisers = [n for n, ln in enumerate(src.splitlines(), 1)
               if re.match(r"\s*lqok = 1\s*$", ln)]
    updates = bodies(src, "update")
    ok &= check("update() has all four build arms", len(updates) == 4,
                "found %d" % len(updates))
    in_update = sum(u.count("lqok = 1") for u in updates)
    ok &= check("the flag is RAISED only inside update()",
                len(raisers) == in_update and in_update == len(updates),
                "%d raises, %d inside update" % (len(raisers), in_update))

    for name in ("clear", "add", "set_snap"):
        for i, b in enumerate(bodies(src, name)):
            ok &= check("%s()%s drops the memo" % (name, "" if i == 0
                                                   else " (arm %d)" % i),
                        "forget()" in b)

    for i, u in enumerate(updates):
        # the early-out compares the WHOLE key, and comes before the scan
        has_gate = re.search(
            r"if lqok == 1 and lqx == qx and lqy == qy \{", u) is not None
        ok &= check("update arm %d gates on the remembered box" % i, has_gate)
        if has_gate:
            ok &= check("...before the scan, not after" % (),
                        u.index("if lqok == 1") < u.index("hit_at("))
            ok &= check("...and records the box it just scanned at",
                        u.index("lqx = qx") < u.index("hit_at("))
        # a FORCE arm must still be able to re-fire from the remembered rect:
        # the whole point of the force is a re-fire at an UNCHANGED box, which
        # is exactly the case the memo hits.
        if "force_edge()" in u:
            gate = u[u.index("if lqok == 1"):u.index("hit_at(")]
            ok &= check("...a FORCE arm still re-fires under a memo hit",
                        "if f == 1 {" in gate and "tentry[last]" in gate)
            ok &= check("...and rolls the pad edge BEFORE the early-out",
                        u.index("force_edge()") < u.index("if lqok == 1"))

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
