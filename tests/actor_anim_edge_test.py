#!/usr/bin/env python3
"""The animator walk must not re-derive what nothing changed.

`vm.canim.tick_all` walks the VISIBLE slots every frame and, for each, derives
a state and a facing, publishes them, compares them against its own shadows and
asks for the frame pin - three cross-bank accessor calls and a pile of compares
per slot. In a calm room every one of those answers is the one it already had.
Measured on the reference-engine sample conversion's town room idle: **2,866 cycles per visible
slot** (vis_at 332 + anim_in 2,048 + frame_pin 486), three slots, every frame.

`a_moved` already carried "the position changed" for the movement delta (bit
0). Bit 1 now carries "something else the walk reads changed" - the clip, the
facing, the frame pin, the state pin, the animation speed - and `anim_in`
answers QUIET (bit 7 of its packed return) when neither is set and the actor
has not reappeared. The walk then skips the slot outright. The ANIMATION is
untouched: engine.anim owns the frame index and this walk never did.

THE WHOLE RISK IS THE WRITE DISCIPLINE, and the directions are NOT symmetric
(the `n_on` lesson):
  - an edge that reads HIGH costs one walk that finds what it already knew;
  - one that reads LOW freezes an animation SILENTLY, in whichever room
    happens to write that field.
So every writer of the five fields raises bit 1, and this pins that.

The bit is raised INLINE rather than through a `touch_anim(i)` helper, and that
is measured rather than stylistic: a call to a LOCAL function of a BANKED
module is still a full GBDK banked trampoline (~144 cycles inside
`__banked_call` plus the argument setup, even when the target is in the same
bank), and `set_dir` is called once per MOVING actor per frame by the walk
itself.

SOURCE-CONTRACT test. The behaviour is pinned on the ROM by the frame-budget
harness (`vm_canim_tick_all` in room 11 idle) and by the OAM stream A/B in
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTOR = os.path.join(ROOT, "lib", "vm", "actor.mos")
CANIM = os.path.join(ROOT, "lib", "vm", "canim.mos")

#: field -> the writers that must raise the animator edge. These are exactly
#: the fields `tick_all` reads per slot; adding a sixth means adding it here.
WRITERS = {
    "a_clip": ("set_clip", "reactivate", "deactivate"),
    "a_dir": ("set_dir",),
    "a_fpin": ("set_frame", "set_frame16", "clear_frame"),
    "a_spin": ("set_anim_state", "clear_anim_state"),
    "a_aspd": ("set_anim_speed",),
}


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
    src = open(ACTOR, encoding="utf-8").read()
    can = open(CANIM, encoding="utf-8").read()
    ok = True

    put_pos = bodies(src, "put_pos")
    ok &= check("put_pos() still owns bit 0 (the position edge)",
                len(put_pos) == 1 and "a_moved[i] |= 1" in put_pos[0])

    # EVERY writer of a field the walk reads raises bit 1.
    for field, fns in WRITERS.items():
        for fn in fns:
            bs = bodies(src, fn)
            ok &= check("%s() exists" % fn, bool(bs))
            for b in bs:
                ok &= check("%s() raises the animator edge for %s"
                            % (fn, field), "a_moved[i] |= 2" in b)

    # ...and nothing writes one of those fields outside the listed functions
    # (reset() clears the whole pool, and the retire sweep forces a re-walk).
    listed = set()
    for fns in WRITERS.values():
        for fn in fns:
            for b in bodies(src, fn):
                listed.update(b.splitlines())
    reset_src = "\n".join(bodies(src, "reset"))
    stray = []
    for n, ln in enumerate(src.splitlines(), 1):
        if re.match(r"\s*(%s)\[\w+\] = " % "|".join(WRITERS), ln):
            if ln in listed or ln in reset_src.splitlines():
                continue
            stray.append((n, ln.strip()))
    ok &= check("no unlisted writer of a field the walk reads", not stray,
                "%s" % stray if stray else "")

    ai = bodies(src, "anim_in")
    ok &= check("anim_in() answers QUIET", len(ai) == 1
                and "return v | 0x80" in ai[0])
    if ai:
        a = ai[0]
        ok &= check("...only when the actor has neither moved nor reappeared",
                    re.search(r"if a_moved\[i\] == 0 \{\s*"
                              r"if a_appear\[i\] == 0 \{", a) is not None)
        ok &= check("...and consumes BOTH edges on the slow path",
                    "a_moved[i] = 0" in a and "(a_moved[i] & 1) != 0" in a)

    tw = bodies(src, "anim_touch_all")
    ok &= check("anim_touch_all() forces the walk to look at every slot",
                len(tw) == 1 and "a_moved[i] |= 2" in tw[0])
    ok &= check("...and is exported", re.search(r"export .*anim_touch_all",
                                                src) is not None)

    # vm.canim: the skip, and the two places that invalidate their OWN shadows
    # and so must ask for the forced walk.
    ta = bodies(can, "tick_all")
    ok &= check("tick_all() skips a quiet slot", len(ta) == 1
                and "(ai & 0x80) != 0" in ta[0])
    ok &= check("...and reads the state pin with the QUIET bit masked out",
                "(ai >> 4) & 0x07" in can and "(ai >> 4) & 0x0F" not in can)
    for fn in ("sweep_retired", "set_clips"):
        for b in bodies(can, fn):
            ok &= check("%s() forces a re-walk" % fn,
                        "actor.anim_touch_all()" in b)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
