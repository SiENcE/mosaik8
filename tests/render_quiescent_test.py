#!/usr/bin/env python3
"""vm.actor.render's pass is SKIPPED whole when nothing it reads has changed,
and every writer of what it reads drops the gate.

`render` was the largest single stage in a calm room - 22,502 cycles a frame in
the reference-engine sample conversion's town room idle, over three visible actors that had not moved and
a camera that had not scrolled. Its per-slot move latch already held on 99.97%
of slots, so the pass was ALREADY deciding to do nothing; the deciding was the
cost. Everything the pass writes is persistent state (vis[]/n_vis, a_parked[],
a_wm, and the sprite hardware, which no backend clears between frames), so with
its inputs unchanged it has nothing to do but write back what is there.

THE WHOLE RISK IS A MISSED DROP SITE, and the two directions are not symmetric
(the `n_on` lesson, again):
  - the gate reading LOW costs one walk that finds what it knew - safe;
  - reading HIGH freezes every actor's sprite where it stands, SILENTLY.
So this pins the writer list. A new write to a_vis / a_base / a_fan / a_pin /
the live list / the metasprite shape that does not drop `rq_ok` is exactly the
bug, and it cannot be caught by looking at one room.

Two things beyond the writer list matter as much:

  * THE AMORTISED PHASE MUST STILL ADVANCE on a gated frame. A test the gate
    skips is one whose answer cannot have changed, but `scan_tick` is a plain
    counter and freezing it defers the next REAL test by however many quiet
    frames came before it - measured on the shooter room idle, a woken enemy
    appeared 2 game frames late and its whole oscillation ran 2 frames behind.
    With the increment ahead of the gate, 8 of the 9 (room x regime) OAM
    streams are byte-identical to a build with the gate pinned off.

  * `vm.canim.apply` must not RE-LAY a fan whose layout did not change. That is
    what lets an animator-step frame stay quiet at all: `repos` is a writer of
    what this walk reads, and it costs 10,700 cycles besides.

SOURCE-CONTRACT test. The behaviour is measured on the ROM
(tools/framebudget/framebudget.py: town room idle 1.04 -> 1.00 LCD/frame,
render 22,502 -> 1,079 cycles) and by the OAM stream A/B in
tools/framebudget/oam_trace.py against a gate-pinned-off build.
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

    # --- the gate itself ---------------------------------------------------
    check("vm.actor declares the render gate",
          re.search(r"^\s*var rq_ok: u8\s*$", a, re.M) is not None)
    check("...with the camera it was decided at",
          re.search(r"^\s*var rq_cx: u16\s*$", a, re.M) is not None
          and re.search(r"^\s*var rq_cy: u16\s*$", a, re.M) is not None)
    # The Lynx/PCE present model re-asserts every metasprite every frame, so
    # the gate, its BSS and its writes are all folded off there - which is
    # also what keeps those two builds md5-identical.
    decl = a[:a.index("var rq_ok: u8")]
    check("the gate's BSS is inside the non-Lynx/PCE block",
          decl.rindex('if platform == "lynx" or platform == "pce" {')
          > decl.rindex("function "))
    # BOTH render arms ([build] actor_scan on and off) must carry it.
    check("both render arms take the gate", a.count("if rq_ok == 1 {") == 2,
          "found %d" % a.count("if rq_ok == 1 {"))
    check("both render arms re-arm it", a.count("rq_ok = 1") == 2,
          "found %d" % a.count("rq_ok = 1"))

    # --- the amortised phase advances even when the pass is skipped --------
    amort = a[a.index("const SCAN_MASK = VM_ACTOR_SCAN_MASK"):]
    ti, gi = amort.index("scan_tick += 1"), amort.index("if rq_ok == 1 {")
    check("scan_tick advances BEFORE the gate can return", ti < gi,
          "the gate would otherwise defer a parked slot's next real test")

    # --- every writer of what the walk reads drops it ----------------------
    # The walk reads: the live list, a_vis, a_base/dyn_base/stride (base_of),
    # a_x/a_y, a_pin, a_tile (ext_anim == 0), a_fan, and the metasprite shape
    # at each base (off_window's left margin).
    WRITERS = {
        "put_pos": "a_x / a_y - THE position funnel",
        "reset": "the whole pool",
        "activate": "membership + a_vis + a_tile (via put_pos)",
        "reactivate": "membership",
        "deactivate": "membership",
        "set_visible": "a_vis gates the walk",
        "set_base": "base_of moves",
        "set_fan": "a_fan feeds the OAM watermark",
        "set_pinned": "a_pin picks screen vs world space",
        "set_size": "the fan shape",
        "set_anim_external": "render's tile arm folds away",
        "repos": "re-lays a fan under the pass",
        "park": "a_parked is the pass's own output",
    }
    for fn, why in sorted(WRITERS.items()):
        b = body(a, fn)
        if fn == "activate":
            # activate writes through put_pos, which is the funnel.
            check("activate() drops it (through put_pos)", "put_pos(" in b)
            continue
        check("%s() drops the gate (%s)" % (fn, why), "rq_ok = 0" in b)

    # ...and nothing outside vm.actor may reshape an actor's fan without one:
    # the only cross-module writer is vm.canim, whose geometry changes all run
    # through repos (see the relay check below).
    other = [f for f in re.findall(r"sprite\.set_meta\w*\(", c)]
    check("vm.canim's metasprite writes are the only cross-module ones",
          len(other) >= 1)

    # --- canim.apply re-lays only a fan whose LAYOUT changed ---------------
    ap = body(c, "apply")
    check("apply decides whether to re-lay", "var relay: u8 = 1" in ap)
    check("...on (base, mask, clip), not on the tile",
          re.search(r"a_uok\[i\] == 1 and a_ubase\[i\] == base and "
                    r"a_umsk\[i\] == msk and a_uclip\[i\] == k \{\s*\n\s*relay = 0",
                    ap) is not None)
    # A DESCRIPTOR frame's object list IS the frame: its layout changes with
    # `fr` even though base, mask and kind do not. `apply` is two
    # compile-time arms since the batched selector (VM_CLIP_SEL): the
    # batched arm carries the verdict in `dsc` (bit 9 of the one selector
    # read), the original arm keeps the g_isdesc call - check BOTH.
    split = ap.find("if VM_CLIP_SEL {} else {")
    check("apply carries the VM_CLIP_SEL fork", split >= 0)
    arm_a, arm_b = ap[:max(0, split)], ap[max(0, split):]
    check("...and a DESCRIPTOR kind is forced back to a re-lay (batched arm)",
          re.search(r"relay = 0.*?if dsc == 1 \{\s*\n\s*relay = 1",
                    arm_a, re.S) is not None)
    m = re.search(r"relay = 0(.*?)\n            \}", arm_b, re.S)
    check("...and a DESCRIPTOR kind is forced back to a re-lay (original arm)",
          m is not None and "g_isdesc(k) == 1" in m.group(1)
          and "relay = 1" in m.group(1))
    # The Lynx/PCE keep the unconditional call, verbatim.
    check("the Lynx/PCE arm keeps the original unconditional repos",
          re.search(r'if platform == "lynx" or platform == "pce" \{\s*\n'
                    r'\s*actor\.repos\(i\)\s*\n\s*\} else \{\s*\n'
                    r'\s*if relay == 1 \{', ap) is not None)

    print("\n" + ("All checks passed" if not FAILS
                  else "SOME CHECKS FAILED: %s" % FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
