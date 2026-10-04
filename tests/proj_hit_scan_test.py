#!/usr/bin/env python3
"""A SHOT ASKS THE ACTOR POOL ONCE, NOT ONCE PER SLOT.

`vm.projectile.update` used to test every live shot against the actor pool
from OUTSIDE vm.actor: `actor.active_of(a)` and `actor.group_of(a)` for every
slot, then four more accessors (`box_w_of`/`box_h_of`/`x_of`/`y_of`, plus the
two offsets) for every candidate whose group matched. Every one of those is a
cross-bank trampoline in a conversion, and the shot's own rectangle was
re-derived inside `overlaps` for each candidate as well.

Measured on the reference-engine sample conversion's shooter room with the framebudget call census
(2026-08-31): 22.7 `active_of` + 18.3 `group_of` + 13.8 x 4 box reads =
~96 cross-bank calls a frame in the autofire regime, ~15 idle - and
`vm_projectile_update` was 62,297 cycles a frame in autofire and 21,503 on
exactly the idle frames that pushed that room from 2 LCD to 3. The walk lives
in `vm.actor.hit_scan` now, where the arrays are: one call per shot in flight.
Room 8 went idle 2.40 -> 2.19, walk 2.03 -> 1.88, autofire 2.73 -> 2.53.

THE CONTRACT THIS PINS (each half is a silent wrong-behaviour bug if undone):

  * the walk is the POOL in INDEX order, and the FIRST match spends the shot.
    The live list is not in index order, so taking it would change which of
    two overlapping targets runs its On Hit - and only in the rooms where
    two targets overlap.
  * an actor with no registered box keeps the historical 8x8 footprint AT ITS
    POSITION (no offsets added), which is what a hand-written game that never
    calls set_box relies on.
  * the group mask still selects the target.
  * the shot's rectangle is resolved ONCE per shot, by the caller, and the
    box offsets are applied there - not per candidate.
  * vm.projectile makes no per-slot accessor call any more.

SOURCE-CONTRACT test. The behaviour is measured on the ROM
(the shooter conversion's `verify.py`, `tools/framebudget/framebudget.py`) and the
rectangle arithmetic itself is exercised through the reference VM by
`tests/actor_hit_box_test.py`.
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

FAILS = []


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    if not cond:
        FAILS.append(label)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def body(src, name):
    m = re.search(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    indent = m.group(1)
    end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
    return src[m.start():end.end() if end else len(src)]


def main():
    actor = read("lib", "vm", "actor.mos")
    proj = read("lib", "vm", "projectile.mos")
    scan = body(actor, "hit_scan")
    upd = body(proj, "update")

    # --- the walk ---------------------------------------------------------
    check("vm.actor declares hit_scan", bool(scan))
    check("hit_scan is exported",
          re.search(r"^\s*export .*\bhit_scan\b", actor, re.M) is not None)
    # THE WALK IS THE LIVE LIST (2026-09-03), and the first-match tie-break
    # rests on `link` keeping that list in slot-index order - which it does
    # (the insertion shift stops at the first smaller index). Pinned here
    # because a link() that appended instead would silently change which of
    # two overlapping targets a shot spends itself on.
    check("the walk is the LIVE list", "while k < n_live" in scan
          and "var i: u8 = live[k]" in scan,
          "the sixteen-slot pool skip was ~2,500 T-cycles a call")
    check("...and does not re-test a_active (the list IS the active set)",
          "a_active[i]" not in scan)
    link = body(actor, "link")
    check("link() keeps the live list in slot-index order (the tie-break)",
          re.search(r"if live\[k - 1\] < i \{\s*\n\s*break", link) is not None)
    check("the group mask selects the target", "a_group[i] & mask" in scan)
    check("the registered box is the target rectangle",
          "a_bw[i]" in scan and "a_bh[i]" in scan
          and "a_box_x[i]" in scan and "a_box_y[i]" in scan)
    check("an unregistered box keeps 8x8 at the actor's position",
          re.search(r"if aw == 0 \{\s*\n\s*aw = 8\s*\n\s*\} else \{\s*\n\s*ax = ax \+ a_box_x\[i\]", scan)
          is not None
          and re.search(r"if ah == 0 \{\s*\n\s*ah = 8\s*\n\s*\} else \{\s*\n\s*ay = ay \+ a_box_y\[i\]", scan)
          is not None,
          "the offsets are added in the ELSE arms only")
    # The shot's edges are published to BSS once per call (the walk is past
    # sdcc's register cliff with four i16 parameters live), and the x axis
    # is settled before the y reads are made.
    check("the shot's edges are computed once, into BSS",
          "hs_r = sx + sw" in scan and "hs_b = sy + sh" in scan)
    check("the overlap is half-open, i16, all sums",
          "if hs_r > ax and ax + aw > hs_l {" in scan
          and "if hs_b > ay and ay + ah > hs_t {" in scan)
    check("255 means nothing was struck", scan.rstrip().endswith("return 255\n    }"))

    # --- the caller -------------------------------------------------------
    hit_arm = upd.split("if has_hit == 1 {", 1)[1] if "if has_hit == 1 {" in upd else ""
    fold = 'if platform == "lynx" or platform == "pce" {'
    check("the hit arm forks on the console", fold in hit_arm)
    cc65, banked = hit_arm.split(fold, 1)[1].split("} else {", 1)

    check("the banked arm asks once per shot", "actor.hit_scan(" in banked)
    check("...and does not walk the pool itself",
          "for a in 0..actor.ACTORS" not in banked)
    check("...and makes no per-slot accessor call",
          "actor.active_of(" not in banked and "actor.group_of(" not in banked
          and "actor.box_w_of(" not in banked and "actor.x_of(" not in banked)
    check("the shot's rectangle is resolved ONCE per shot, before BOTH tests",
          upd.index("q_r = q_l + b_w") < upd.index("if has_player_hit == 1 {")
          and "actor.hit_scan(q_l, q_t, q_r - q_l, q_b - q_t, p_mask[i])" in banked,
          "not once per candidate, and not once per test")
    check("a hit still spends the shot and runs the On Hit",
          "p_active[i] = 0" in banked and "g_hit(a, p_group[i])" in banked)

    # The cc65 arm is the ORIGINAL loop, verbatim: no bank trampolines to
    # save there and MAIN is what binds (measured +212 B on vm-shmup and
    # vm-combat with the fused call). That verbatim fold is what keeps all
    # eighteen Lynx/PCE sample builds md5-identical.
    check("the Lynx/PCE arm keeps the per-slot walk",
          "for a in 0..actor.ACTORS" in cc65 and "actor.active_of(a) == 1" in cc65
          and "actor.group_of(a) & p_mask[i]" in cc65
          and "hit_actor(p_x[i], p_y[i], a)" in cc65)
    check("...and its per-candidate helper is defined behind the same fork",
          "function hit_actor(" in proj
          and proj[:proj.index("function hit_actor(")].rindex(fold)
          > proj[:proj.index("function hit_actor(")].rindex("\n    function "))

    check("the Lynx/PCE player-hit arm is untouched",
          "overlaps(p_x[i], p_y[i], plx, ply, plw, plh)" in upd)
    check("the banked player-hit arm is the same four compares, inline",
          "if q_r > u_plx and u_plr > q_l and q_b > u_ply and u_plb > q_t {" in upd)

    print()
    if FAILS:
        print("FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("proj_hit_scan_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
