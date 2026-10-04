#!/usr/bin/env python3
"""`step_all` must not walk the pool to discover that nobody is moving.

`vm.core` calls it every frame, and under the display-frame CATCH-UP it walks
the live list once per elapsed LCD frame - so the slower the game runs, the
more an empty walk costs, which is exactly backwards. Measured on
the reference-engine sample conversion's town room idle: **3,171 cycles a frame** over eight live
slots and two catch-up rounds, to find that no slot had the flag. A
script-driven `move_to` is rare and short; an actor moved by its own On Update
writes the position directly and never sets it at all.

`n_moving` is engine.anim's `n_on`, applied to the other per-frame walk that
usually has nothing to walk.

THE WHOLE RISK IS DRIFT between the counter and the array, and the directions
are NOT symmetric:
  - counted too HIGH costs one walk that steps nothing (the safe direction);
  - counted too LOW freezes a scripted walk mid-stride, and the thread waiting
    on `actor_await_move` never wakes - silently, and only for the actor that
    happened to be moving.
So every write to `a_moving` goes through `set_moving`, and this pins that. The
one exception is `reset()`, which clears the WHOLE pool in a loop and zeroes
the counter once afterwards (a per-slot funnel call there would be 16 banked
trampolines for a value that is exactly 0).

SOURCE-CONTRACT test. The behaviour is pinned on the ROM by the frame-budget
harness (`tools/framebudget/framebudget.py`: `vm_actor_step_all` 3,171 -> 236
in room 11 idle) and by the OAM stream A/B in `tools/framebudget/oam_trace.py`.
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
                   "lib", "vm", "actor.mos")


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    return bool(cond)


def bodies(src, name):
    out = []
    for m in re.finditer(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name),
                         src, re.M):
        indent = m.group(1)
        end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
        out.append(src[m.start():end.end() if end else len(src)])
    return out


def main():
    src = open(SRC, encoding="utf-8").read()
    lines = src.splitlines()
    ok = True

    ok &= check("the moving count exists",
                re.search(r"^\s*var n_moving: u8", src, re.M) is not None)
    funnel = bodies(src, "set_moving")
    ok &= check("set_moving() is the one write funnel", len(funnel) == 1)
    if funnel:
        f = funnel[0]
        ok &= check("...and moves the counter in both directions",
                    "n_moving += 1" in f and "n_moving -= 1" in f)
        ok &= check("...only on a real transition (no double count)",
                    re.search(r"if a_moving\[i\] == v \{\s*return", f)
                    is not None)

    # THE INVARIANT: no raw `a_moving[i] = ...` outside the funnel, except the
    # whole-pool clear in reset() - which is followed by `n_moving = 0`.
    reset = bodies(src, "reset")
    reset_src = reset[0] if reset else ""
    raw = []
    for n, ln in enumerate(lines, 1):
        if re.match(r"\s*a_moving\[\w+\] = ", ln):
            if funnel and ln in funnel[0].splitlines():
                continue
            if ln in reset_src.splitlines():
                continue
            raw.append(n)
    ok &= check("no raw `a_moving[i] = ...` outside set_moving()", not raw,
                "lines %s" % raw if raw else "")
    ok &= check("reset() zeroes the counter with the pool",
                "n_moving = 0" in reset_src)

    for name in ("deactivate", "move_cancel"):
        for b in bodies(src, name):
            ok &= check("%s() clears through set_moving()" % name,
                        "set_moving(" in b)
    for b in bodies(src, "step_live"):
        ok &= check("step_live() clears an arrival through set_moving()",
                    "set_moving(" in b)
    for b in bodies(src, "move_start"):
        ok &= check("move_start() arms through set_moving()",
                    "set_moving(i, 1)" in b)
        # step_all returns early while nothing moves, so mv_last is stale by
        # however long the room has been still - the first step would be
        # handed the whole idle interval (capped at MOVE_CATCHUP_MAX).
        ok &= check("...and RE-SEEDS the catch-up clock",
                    "mv_last = system.frames()" in b)

    steps = bodies(src, "step_all")
    ok &= check("step_all() has both build arms", len(steps) == 2,
                "found %d" % len(steps))
    for i, b in enumerate(steps):
        ok &= check("step_all arm %d skips the walk at zero" % i,
                    re.search(r"if n_moving == 0 \{\s*return\s*\}", b)
                    is not None)
        # compare the CODE, not the prose: the arm's own comment names
        # system.frames() above the gate.
        read = "var fnow: u8 = system.frames()"
        if read in b:
            ok &= check("...BEFORE the catch-up clock read",
                        b.index("if n_moving == 0") < b.index(read))

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
