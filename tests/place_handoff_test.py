#!/usr/bin/env python3
"""off_window and place are FUSED through the lk_* handoff (R2's last piece).

The two functions used to read a_pin/a_x/a_y twice and compute the same
screen delta twice per looked slot - once for the window verdict, once for
the move latch - and every call site calls place() immediately after
off_window() for the SAME slot. off_window therefore publishes what it
already holds (lk_pin, and the lk_sx/lk_sy screen delta of an on-window
unpinned slot) and place consumes it instead of re-deriving.

THE CONTRACT THIS PINS:

  * the handoff is only valid BETWEEN an off_window call and the place call
    that follows it - so every place() call site must take its `off` verdict
    from an off_window call on the same slot, with nothing between them that
    could move the slot. A new call site that computes `off` any other way
    (or re-orders the pair) reads a stale delta and places the actor at the
    PREVIOUS looked slot's position - silently, and only when the camera
    moves.
  * the Lynx/PCE keep the original re-reads (their present model has no
    latch to feed, and MAIN must not pay BSS for a dead handoff) - the fold
    must compile back to the original body, which is what keeps those ten
    sample builds md5-identical (verified at the ROM level when this
    shipped).
  * the delta is published BEFORE the left-margin test, so the Lynx/PCE fold
    leaves off_window's original tail (meta_cols asked LAST) verbatim.

SOURCE-CONTRACT test; the behaviour is measured on the ROM with
tools/framebudget/framebudget.py and the oam_trace.py stream A/B.
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
    ow = body(a, "off_window")
    pl = body(a, "place")

    # --- the handoff state, folded off the Lynx/PCE ------------------------
    for v in ("lk_pin: u8", "lk_sx: i16", "lk_sy: i16"):
        check("vm.actor declares the handoff var %s" % v.split(":")[0],
              re.search(r"^\s*var %s\s*$" % re.escape(v), a, re.M) is not None)
    decl = a[:a.index("var lk_pin: u8")]
    check("the handoff BSS is inside the non-Lynx/PCE block",
          decl.rindex('if platform == "lynx" or platform == "pce" {')
          > decl.rindex("function "))

    # --- off_window publishes ---------------------------------------------
    check("off_window publishes the pin flag on BOTH arms",
          ow.count("lk_pin = 1") == 1 and ow.count("lk_pin = 0") == 1)
    check("off_window publishes the screen delta",
          "lk_sx = wx - cx" in ow and "lk_sy = wy - cy" in ow)
    check("...BEFORE the left-margin test, so the Lynx/PCE fold keeps the "
          "original tail (meta_cols LAST, the compare as the return)",
          ow.index("lk_sx = wx - cx") < ow.index("sprite.meta_cols(")
          and ow.rstrip().endswith("return wx + mw < cx\n    }"))
    # Every publish is folded off the Lynx/PCE (BSS that must not exist there
    # may not be written there either - it would be a C compile error).
    for m in re.finditer(r"^\s*lk_(?:pin|sx|sy) = ", ow, re.M):
        head = ow[:m.start()]
        check("the publish at off_window:+%d is platform-folded" %
              ow[:m.start()].count("\n"),
              head.rindex('if platform == "lynx" or platform == "pce" {')
              > head.rindex("if a_pin") if "if a_pin" in head
              else 'platform == "lynx"' in head)

    # --- place consumes ----------------------------------------------------
    check("place's fused arm moves through the published delta",
          "sprite.move_world(base, lk_sx, lk_sy)" in pl)
    check("...and latches against it",
          "a_lpx[i] == lk_sx and a_lpy[i] == lk_sy" in pl)
    check("place's fused arm forks on lk_pin, not a_pin",
          "if lk_pin == 1 {" in pl)
    # The unpinned fused arm must NOT re-read the position arrays - that is
    # the whole point. The a_x/a_y reads that remain are the Lynx/PCE arm's
    # and the pinned arm's (off_window returns before reading for a pinned
    # slot, so place still must).
    check("the fused arm re-reads a_x/a_y only where it has to "
          "(Lynx/PCE arm + pinned arm)",
          pl.count("a_x[i]") == 2 and pl.count("a_y[i]") == 2,
          "found %d / %d" % (pl.count("a_x[i]"), pl.count("a_y[i]")))
    # The Lynx/PCE arm keeps the original re-derive verbatim.
    check("the Lynx/PCE arm recomputes the delta itself",
          any("var sx: i16 = wx - cx" in
              pl[m.end():m.end() + 600] and "lk_" not in
              pl[m.end():pl.find("var sx: i16 = wx - cx", m.end()) + 1]
              for m in re.finditer(
                  r'if platform == "lynx" or platform == "pce" \{', pl)))

    # --- every place() call site takes its verdict from off_window --------
    sites = [m.start() for m in re.finditer(r"^\s*place\(", a, re.M)]
    check("place has exactly three call sites (repos + both render arms)",
          len(sites) == 3, "found %d" % len(sites))
    for pos in sites:
        line = a[a.rfind("\n", 0, pos) + 1:a.find("\n", pos)].strip()
        if "off_window(" in line:
            ok = True          # repos: place(i, base, off_window(...), ...)
        else:
            # render: `var off: bool = off_window(...)` a few lines above,
            # with no other statement between that could move the slot.
            back = a[max(0, pos - 2000):pos]
            m = re.search(r"var off: bool = off_window\(i, base, cx, cy\)"
                          r"(.*)$", back, re.S)
            ok = m is not None and "a_x[" not in m.group(1) \
                and "put_pos" not in m.group(1)
        check("place call site at line %d pairs with off_window"
              % (a[:pos].count("\n") + 1), ok)

    print("\n" + ("All checks passed" if not FAILS
                  else "SOME CHECKS FAILED: %s" % FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
