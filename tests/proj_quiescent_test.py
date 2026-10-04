#!/usr/bin/env python3
"""A projectile pool with nothing in flight must cost nothing per frame.

`vm.core` calls `projectile.update()` and `projectile.render()` every frame
whether or not anything has ever been fired, and both walked the whole pool.
Under the dynamic base (plan P1) render also made a
CROSS-BANK `actor.oam_watermark()` call to decide a base that nothing was
going to be drawn at. Measured on the reference-engine sample conversion's long walk-in room, a
room that never fires a shot: 1,876 + 4,322 = **6,198 cycles a frame**, and
the pool walk there never even reaches `slot_of`.

`p_quiet` is the fix: set by a render pass that walked to the END with no
active slot, cleared by anything that can make a slot live or a park stale.

THE WHOLE RISK IS A STUCK FLAG, in either direction:
  - stuck at 1, and the pool never wakes: the game fires nothing;
  - cleared but `p_slots` stale, and a launch claims a slot the block has no
    OAM for: the shot flies invisibly (render's `i >= p_slots` arm returns
    before reaching it).
The second is why `launch16` re-bases for itself - `p_slots` has to be
current at the moment a claim is refused, and while the pool is quiescent
`render` is no longer keeping it so.

SOURCE-CONTRACT test: it pins the flag's set site, every clear site, and the
launch-time re-base. The BEHAVIOUR is pinned on the ROM by
`tools/projprobe/proj_quiet_probe.py` (the shooter room: shots launch, no live
slot outside the block, the flag never set while something is live; and
`--control` on a room that never fires: it really does go quiescent).
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
                   "lib", "vm", "projectile.mos")


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    return bool(cond)


def body(src, name):
    # `[ 	]*`, not `\s*`: the latter spans NEWLINES, so a blank line before a
    # declaration put one in the captured indent and the closing-brace pattern
    # then matched nothing - the "body" ran to the end of the file.
    m = re.search(r"^([ 	]*)(?:hot )?(?:local )?function %s\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    indent = m.group(1)
    end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
    return src[m.start():end.end() if end else len(src)]


def main():
    src = open(SRC, encoding="utf-8").read()
    ok = True

    ok &= check("vm.projectile declares the quiescence flag",
                re.search(r"^\s*var p_quiet: u8\s*$", src, re.M) is not None)

    # -- the two per-frame entry points take the early-out -------------------
    for fn in ("update", "render"):
        b = body(src, fn)
        ok &= check("%s() exists" % fn, bool(b))
        ok &= check("%s() returns immediately while quiescent" % fn,
                    re.search(r"if p_quiet == 1 \{\s*return\s*\}", b) is not None)

    # ...and render's early-out is BEFORE the watermark call it exists to skip
    r = body(src, "render")
    ok &= check("render()'s early-out precedes the re-base",
                r.index("p_quiet == 1") < r.index("rebase()"))

    # -- the re-base is its own function, and a LAUNCH runs it ---------------
    rb = body(src, "rebase")
    ok &= check("the dynamic re-base is split out as rebase()", bool(rb))
    # ONE call site, and it is inside rebase(): the whole saving is not making
    # this cross-bank trampoline on a frame that draws nothing.
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("--"))
    # ONE watermark read, and it is inside rebase(): the whole saving is not
    # asking for a base on a frame that draws nothing. It is the VARIABLE
    # (`actor.a_wm`) rather than `oam_watermark()` on the consoles with bank
    # trampolines - same byte, ~370 T-cycles cheaper (2026-09-06).
    wm_reads = code.count("actor.a_wm") + code.count("actor.oam_watermark()")
    ok &= check("rebase() is the ONE actor watermark read",
                ("actor.a_wm" in rb or "actor.oam_watermark()" in rb)
                and wm_reads == 1,
                "%d read(s) in code" % wm_reads)
    ok &= check("rebase() recomputes the slot count when it moves the block",
                "fit_slots()" in rb)
    l16 = body(src, "launch16")
    ok &= check("launch16() re-bases BEFORE it tests p_slots",
                "rebase()" in l16
                and l16.index("rebase()") < l16.index("while i < p_slots"))

    # -- every site that can invalidate the flag clears it -------------------
    # A missing one is the stuck-at-1 bug: the pool never wakes and the game
    # fires nothing. They are listed by NAME so a new one has to be added here
    # deliberately rather than discovered from play.
    for site, why in (("launch16", "a slot just went live"),
                      ("reset", "a killed-while-flying shot must be re-hidden"),
                      ("set_base", "the new base's entries are unparked"),
                      ("rebase", "a moved block unparks every slot")):
        b = body(src, site)
        ok &= check("%s() clears the flag (%s)" % (site, why),
                    re.search(r"p_quiet = 0", b) is not None)

    # -- and only a COMPLETE walk may set it --------------------------------
    # A walk cut short by the p_slots bound has left the slots above it
    # untouched, so it must not claim the pool is settled.
    # The pass's verdict and its new walk bound are ONE number now: `seen`,
    # the highest live slot it saw plus one (2026-09-06). p_quiet is
    # `seen == 0`, which is exactly what the old `quiet` flag meant.
    ok &= check("only a full walk sets the flag",
                "var seen: u8 = 0" in r
                and re.search(r"p_hi = seen\s+p_quiet = 0\s+if seen == 0 \{",
                              r) is not None)
    cut = r[r.index("if i >= p_slots {"):]
    ok &= check("a walk cut short by p_slots does NOT set it",
                cut.index("p_quiet = 0") < cut.index("return"))
    ok &= check("a live slot clears the pass's own flag",
                re.search(r"if p_active\[i\] == 1 \{\s*seen = i \+ 1", r)
                is not None)

    # -- THE WALK BOUND (p_hi) has the same stuck-flag risk, per slot --------
    # Stuck LOW is a live shot neither pass ever visits: it stops moving and
    # stops being drawn. So it grows at the one place a slot goes live and at
    # every site that makes a park stale - the same list p_quiet keeps - and
    # shrinks ONLY from a walk that reached the end.
    ok &= check("the walk bound starts at the whole pool",
                re.search(r"var p_hi: u8 = NPROJ", src) is not None)
    for site, why in (("launch16", "the claimed slot must be in the walk"),
                      ("reset", "every park is stale"),
                      ("set_base", "the new base's entries are unparked"),
                      ("rebase", "a moved block unparks every slot")):
        b = body(src, site)
        ok &= check("%s() grows the walk bound (%s)" % (site, why),
                    re.search(r"p_hi = NPROJ|p_hi = slot \+ 1", b) is not None)
    ok &= check("both passes walk to the bound, not to NPROJ",
                body(src, "update").count("for i in 0..p_hi") == 1
                and r.count("for i in 0..p_hi") == 1)
    ok &= check("only render shrinks it, after the walk",
                body(src, "update").count("p_hi =") == 0
                and r.count("p_hi = seen") == 1)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
