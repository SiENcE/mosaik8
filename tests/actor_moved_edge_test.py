#!/usr/bin/env python3
"""vm.canim derives movement only for an actor that MOVED, and the edge that
says so has ONE writer.

`tick_all` derived state + facing from a position DELTA for every visible slot
every frame - two cross-bank position reads plus a pile of u16 compares, to
discover that a standing NPC is still standing. In a calm room that is every
actor, every frame. `anim_in`'s bit 2 is the position-changed edge; an unmoved
actor skips the reads and the delta, deriving state 0 and keeping its facing,
which is EXACTLY what the delta arm computes for a zero delta.

THE WHOLE RISK IS DRIFT between the edge and the position, and the two
directions are not symmetric (the engine.anim `n_on` lesson):
  - the flag reading HIGH costs one recompute that finds nothing - safe;
  - reading LOW freezes a walking actor's facing on whatever it last faced,
    silently, and only for the actor that happens to move.
So every write to a_x/a_y goes through `put_pos`, and this pins that: a raw
`a_x[i] = ...` anywhere outside it is exactly the bug.

SOURCE-CONTRACT test. The behaviour is measured on the ROM
(tools/framebudget/framebudget.py: a long walk-in room's idle reached 1.00
LCD/frame with it) and by the OAM stream A/B in
tools/framebudget/oam_trace.py.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTOR = os.path.join(ROOT, "lib", "vm", "actor.mos")
CANIM = os.path.join(ROOT, "lib", "vm", "canim.mos")

FAILS = []


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    if not cond:
        FAILS.append(label)


def body(src, name):
    m = re.search(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    indent = m.group(1)
    end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
    return src[m.start():end.end() if end else len(src)]


def main():
    a = open(ACTOR, encoding="utf-8").read()
    c = open(CANIM, encoding="utf-8").read()

    check("vm.actor declares the moved edge",
          re.search(r"^\s*var a_moved: array\[u8, VM_ACTOR_POOL\]\s*$", a, re.M)
          is not None)
    pp = body(a, "put_pos")
    # `|=`, not `=`: the same byte now carries a SECOND edge in bit 1 (the
    # animator-input edge, tests/actor_anim_edge_test.py), so a plain
    # assignment here would clear an edge another writer had just raised.
    check("put_pos is the funnel", bool(pp) and "a_moved[i] |= 1" in pp)

    # THE INVARIANT: no raw position write outside put_pos.
    raw = []
    for n, ln in enumerate(a.splitlines(), 1):
        if re.match(r"\s*a_[xy]\[\w+\] = ", ln) and ln not in pp.splitlines():
            raw.append(n)
    check("no raw `a_x[i] = ` / `a_y[i] = ` outside put_pos", not raw,
          "lines %s" % raw if raw else "")
    # ...and the sites that used to write directly now go through it.
    for fn in ("activate", "set_pos", "step_once"):
        b = body(a, fn)
        if b:
            check("%s() writes through put_pos" % fn, "put_pos(" in b)

    # anim_in publishes it read-and-clear, like the appear edge.
    ai = body(a, "anim_in")
    check("anim_in publishes the edge as bit 2", "v |= 4" in ai)
    check("...and CONSUMES it (read-and-clear)", "a_moved[i] = 0" in ai)
    check("...only for a slot that HAS a clip",
          ai.index("if a_clip[i] != 255") < ai.index("a_moved[i] = 0"))

    # canim gates the derivation on it, and the appear edge still forces it.
    t = body(c, "tick_all")
    m = re.search(r"if \(ai & 4\) != 0 or \(ai & 8\) != 0 \{", t)
    check("tick_all gates the delta on the moved OR appear edge", m is not None)
    if m:
        after = t[m.end():]
        # the position reads are DIRECT variable reads since 2026-09-03
        for what in ("actor.a_x[i]", "actor.a_y[i]", "a_px[i] = cx"):
            check("...%s is inside the gate" % what, what in after.split("\n                }")[0])

    print("\n" + ("All checks passed" if not FAILS
                  else "SOME CHECKS FAILED: %s" % FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
