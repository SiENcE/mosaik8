#!/usr/bin/env python3
"""engine.anim fires its callback EVERY frame only where the present needs it.

The every-frame re-assert is the LYNX rule - its present rebuilds the sprite
composite, so a metasprite must be pushed again or it drops out - and it was
carried unconditionally to the consoles whose OAM PERSISTS, where a redundant
re-assert is a no-op by construction: `vm.canim.apply`'s R1 fast path exists
precisely to detect it and return.

Detecting it is not free, and that is the whole point. Measured on
the reference-engine sample conversion's town room idle (three armed animators, none of them
stepping): **2,825 cycles per call, 8,474 a frame** - a resident wrapper, a
BANKED call into the pack, a banked accessor for the clip and frame pin, and
the shadow compares, all to conclude that nothing changed. Not calling costs
nothing at all.

WHAT MAKES IT SAFE is that `set` / `play_once` / `reset` APPLY IMMEDIATELY, so
every input to a callback other than `frame` has a re-arm behind it: engine.anim
owns `frame` and the driver owns the rest. `vm.clip.apply` reads c_state/c_face,
which only `play()` writes and which calls `anim.set`; `vm.canim.apply` reads
the state and facing `tick_all` re-arms on, the frame pin `tick_all` applies
directly, and the clip a room load's retire sweep re-arms; the sample callbacks
are pure functions of the frame alone.

SOURCE-CONTRACT test. The behaviour is pinned on the ROM by
`tools/framebudget/framebudget.py` (`vm_canim_apply` 13,629 -> 2,356 in room 11
idle) and by counting distinct OAM tile layouts per room, which is what says an
animation is still stepping rather than merely cheap.
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
SRC = os.path.join(ROOT, "lib", "engine", "anim.mos")


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
    t = body(src, "tick")
    ok = check("engine.anim.tick() exists", bool(t))
    if not t:
        return 1

    ok &= check("the step is FLAGGED where it happens", "var stepped: u8 = 0"
                in t and "stepped = 1" in t)
    # It must be set at the step site, never derived by comparing a_frame
    # before and after: a ONE-frame clip's step lands back on the same index
    # and still has to re-apply (the ANIM_PAUSED shape vm.canim arms for a
    # script-pinned frame).
    ok &= check("...at the tick rollover, not by comparing a_frame",
                re.search(r"a_tick\[i\] = 0\s*\n\s*stepped = 1", t)
                is not None)

    ok &= check("the Lynx/PCE keep the every-frame re-assert",
                re.search(r'if platform == "lynx" or platform == "pce" \{\s*\n'
                          r'\s*a_apply\[i\]\(i, a_frame\[i\]\)', t) is not None)
    ok &= check("...and every other console applies only on a step",
                re.search(r"if stepped == 1 \{\s*\n\s*a_apply\[i\]\(i, "
                          r"a_frame\[i\]\)", t) is not None)
    ok &= check("both arms stay behind the still-armed guard",
                t.index("if a_count[i] > 0 {", t.index("stepped = 1"))
                < t.index("if platform ==", t.index("stepped = 1")))
    ok &= check("tick() is the ONLY conditional caller", t.count("a_apply[i](") == 2)

    # ...and the immediate-apply contract the whole thing rests on.
    for fn in ("set", "play_once"):
        b = body(src, fn)
        ok &= check("%s() applies frame 0 immediately" % fn,
                    re.search(r"if count > 0 \{\s*\n\s*cb\(i, 0\)", b)
                    is not None)
    r = body(src, "reset")
    ok &= check("reset() applies the frame it jumps to",
                "a_apply[i](i, frame)" in r)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
