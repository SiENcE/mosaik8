#!/usr/bin/env python3
"""engine.anim.tick() must not walk a pool to discover it is empty.

`vm.canim.tick_all` calls it every frame whether or not anything is animating,
and MAXA is `max(8, actor_pool)` - SIXTEEN in a reference-engine conversion. Measured
on the reference-engine sample conversion's long walk-in room, whose one visible actor has no clip
at all: **3,446 cycles a frame** to read sixteen zeroes. `n_on` (how many slots
are armed) both skips the walk entirely and ENDS it once every armed slot has
been ticked, which is the commoner case - one animated actor in a 16-slot pool.

THE WHOLE RISK IS DRIFT between `n_on` and the array it counts:
  - counted too LOW (0 while something is armed) and the animation freezes -
    silently, and only in the room that happens to arm that slot;
  - counted too HIGH and it costs the walk it was meant to save, which is the
    safe direction.
So every write to `a_count` goes through `arm()`, and this pins that: a raw
`a_count[i] = ...` anywhere outside `arm` is exactly the bug.

SOURCE-CONTRACT test. The behaviour is pinned on the ROM by
a local probe (distinct OAM tile layouts per room:
a frozen animation shows up as 1) and by the OAM stream A/B in
`tools/framebudget/oam_trace.py`.
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
                   "lib", "engine", "anim.mos")


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    return bool(cond)


def body(src, name):
    m = re.search(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    indent = m.group(1)
    end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
    return src[m.start():end.end() if end else len(src)]


def main():
    src = open(SRC, encoding="utf-8").read()
    ok = True

    ok &= check("engine.anim declares the armed count",
                re.search(r"^\s*var n_on: u8\s*$", src, re.M) is not None)
    a = body(src, "arm")
    ok &= check("arm() is the one place a_count is written", bool(a))

    # THE INVARIANT: no raw write to a_count outside arm(). A `>` or `>=`
    # comparison is a read and is fine; an assignment is not.
    raw = []
    for n, ln in enumerate(src.splitlines(), 1):
        if re.match(r"\s*a_count\[\w+\] = ", ln) and ln not in a.splitlines():
            raw.append(n)
    ok &= check("no raw `a_count[i] = ...` outside arm()", not raw,
                "lines %s" % raw if raw else "")

    # ...and arm() moves the counter in both directions.
    ok &= check("arm() increments when a slot goes armed", "n_on += 1" in a)
    ok &= check("arm() decrements when a slot goes idle", "n_on -= 1" in a)

    # every caller that used to assign a_count now calls arm()
    for fn in ("set", "play_once", "clear", "tick"):
        b = body(src, fn)
        ok &= check("%s() arms through arm()" % fn, "arm(" in b)

    t = body(src, "tick")
    ok &= check("tick() skips the walk when nothing is armed",
                re.search(r"if left == 0 \{\s*return\s*\}", t) is not None)
    # The copy matters: the one-shot finish inside the loop calls arm(i, 0),
    # which decrements the LIVE counter mid-walk. Counting down a local is what
    # makes the early exit correct anyway.
    ok &= check("...from a COPY of the counter, not the counter itself",
                "var left: u8 = n_on" in t)
    ok &= check("tick() also ENDS once every armed slot has ticked",
                "left -= 1" in t and t.count("return") >= 2)
    ok &= check("the countdown happens INSIDE the armed branch",
                t.index("if a_count[i] > 0 {") < t.index("left -= 1"))

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
