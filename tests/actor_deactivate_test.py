#!/usr/bin/env python3
"""`[build] actor_deactivate` - the reference engine's OFFSCREEN DEACTIVATION.

The reference VM does not keep an offscreen actor in the list it walks. Its
`actors_update` bounds check ends in `deactivate_actor_impl(actor)`, which
`DL_REMOVE`s the actor from `actors_active_head` and pushes it onto
`actors_inactive_head` (core/actor.c). Measured on the reference ROM by walking
that list, its shooter room has FIVE active actors where ours has sixteen live
- and vm.actor walks the live list in four separate per-frame passes, so it is
a 3x on all of them at once.

THE CONTRACT THIS PINS - the membership rules, which is where a two-list
refactor goes wrong SILENTLY:

  * OFF is byte-identical: nothing is declared and every walk is the original.
  * a slot leaves the live list ONLY through `place`'s off verdict, and the
    walk does not advance its index when it does (unlink shifted the next slot
    into that position).
  * `deactivate` removes from BOTH lists. A retired slot left in the parked
    list is one `wake_scan` can RESURRECT when the camera next reaches it.
  * `activate` / `reactivate` on an already-active but PARKED slot move it
    back, rather than clearing `a_parked` and leaving it stranded in the
    parked list where nothing draws it and `parked_of()` misreports it.
  * `reset` clears both counts.
  * the wake scan is its OWN function, not a loop inside `render` - which
    opens `add sp, #-14` and charges ~1,000 cycles a slot for a loop that
    decides to skip, i.e. exactly what taking
    the slots out of the live walk was meant to save.
  * it re-tests on the `[build] actor_scan` phase, so the WAKE LATENCY is the
    same as with the flag off.

The runtime behaviour is proved on the ROM by
`tools/projprobe/deact_lists_probe.py`, which checks the full membership
invariant on every game frame (every active slot in exactly one list, every
inactive slot in neither, `a_parked` agreeing with which, no duplicates).
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
    a = read("lib", "vm", "actor.mos")

    print("\n[the parked list]")
    check("the list and its helpers are behind the fold",
          re.search(r"if VM_ACTOR_DEACT \{\s*\n\s*var plist: array\[u8, "
                    r"VM_ACTOR_POOL\]\s*\n\s*var n_plist: u8", a) is not None)
    check("plink inserts in ascending slot order, like link",
          "plist[k] = plist[k - 1]" in a and "plist[k] = i" in a)
    check("punlink closes the gap, like unlink",
          "plist[k] = plist[k + 1]" in a and "n_plist -= 1" in a)
    check("reset clears BOTH counts",
          re.search(r"n_live = 0\s*\n\s*if VM_ACTOR_DEACT \{\s*\n\s*n_plist = 0",
                    a) is not None)

    print("\n[membership - where a two-list refactor goes wrong]")
    de = body(a, "deactivate")
    check("deactivate removes from BOTH lists",
          "unlink(i)" in de and "punlink(i)" in de,
          "a retired slot left in the parked list can be RESURRECTED")
    for fn in ("activate", "reactivate"):
        f = body(a, fn)
        check("%s un-parks an already-active slot" % fn,
              re.search(r"if VM_ACTOR_DEACT \{\s*\n\s*if a_parked\[i\] == 1 \{"
                        r"\s*\n\s*punlink\(i\)\s*\n\s*link\(i\)", f) is not None,
              "else a_parked = 0 strands it where nothing draws it")

    print("\n[the park transition and the wake scan]")
    # ...and NEVER while the VM is LOCKED - the reference's own rule, its
    # `actors_update` offscreen branch being
    #     if (!VM_ISLOCKED()) { deactivate_actor_impl(actor); }
    #     else { SET_FLAG(actor->flags, ACTOR_FLAG_DISABLED); }
    # nor with a scripted move in flight (ours: step_all walks the live list
    # where the reference VM's move is driven by the script instruction). Either one
    # missing strands an awaited move and deadlocks the thread holding the
    # lock. Its own test spells it out (moving_actor_never_retired_test.py);
    # pinned here too because this IS the membership rule.
    check("parking happens right after place, in BOTH render arms",
          a.count("""                    if VM_ACTOR_DEACT {
                        if off and locked == 0 and a_moving[i] == 0 {
                            unlink(i)
                            plink(i)
                            continue
                        }
                    }""") == 2,
          "the SCAN_ALL arm and the amortised one, both guarded")
    ws = body(a, "wake_scan")
    check("the wake scan is its OWN function", bool(ws),
          "a loop inside render costs ~1,000 cycles a slot to skip")
    check("...and it is behind the fold",
          re.search(r"if VM_ACTOR_DEACT \{\s*\n\s*local function wake_scan",
                    a) is not None)
    check("it sweeps on an actor_scan BUDGET, not a per-entry phase test",
          "left = (n >> SCAN_SHIFT) + 1" in ws and "var i: u8 = plist[wcur]" in ws,
          "a phase test has to VISIT an entry to find it is not due; the "
          "cursor visits only the entries it tests")
    check("the cheap prefix is a call, not the loop's own u16 arithmetic",
          "if wake_look(i) == 1 {" in ws and "a_x[i]" not in ws
          and "wk_lx = cx + SCREEN_WIDTH" in ws,
          "wake_scan holding three u16 pairs put it past sdcc's register cliff")
    wl = body(a, "wake_look") or ""
    check("...and wake_look is off_window's three CHEAP terms, pin first",
          "if a_pin[i] == 1 {" in wl and "if wx > wk_lx {" in wl
          and "if wy > wk_ly {" in wl and "if wy < wk_ty {" in wl,
          "the left-margin term is the only one needing base_of + meta_cols")
    check("a woken slot moves lists and does NOT advance the cursor",
          "punlink(i)" in ws and "link(i)" in ws
          and "wcur += 1" in ws and "woke = 1" in ws)
    check("it raises the un-park edges place() raises",
          "a_appear[i] = 1" in ws and "a_parked[i] = 0" in ws
          and "ov_ok = 0" in ws)
    check("it runs BEFORE the live walk, from both render arms",
          a.count("wake_scan(cx, cy)") == 2)

    print("\n[the build surface]")
    b = read("mosaik8_build.py")
    check("actor_deactivate is a known [build] key", "'actor_deactivate'," in b)
    check("the getter defaults to False (byte-identical)",
          "def get_actor_deactivate" in b and "get('actor_deactivate')" in b)
    check("the define is stated only when opting IN",
          "defines['VM_ACTOR_DEACT'] = True" in b)
    c = read("mosaik", "compiler.py")
    check("the compiler supplies the default itself",
          "all_defines.setdefault('VM_ACTOR_DEACT', False)" in c)
    # wake_scan (VM_ACTOR_DEACT only) tests VM_ACTOR_SCAN_ALL at STATEMENT
    # level; the build states it only when actor_scan != 1, so at the default
    # an unfolded `if (VM_ACTOR_SCAN_ALL)` reached C and the build failed
    # (2026-10-09, a VM8 shooter with actor_deactivate and no actor_scan).
    from mosaik import MosaikCompiler
    fork = '''module "main" {
    import "platform.video"
    var n: u8
    function every() { n = 1 }
    function some() { n = 2 }
    function main() {
        if VM_ACTOR_SCAN_ALL { every() } else { some() }
        loop { video.wait_vblank() }
    }
    export main
}
'''
    out = MosaikCompiler().compile_program([("main.mos", fork)], platform="gameboy")
    check("VM_ACTOR_SCAN_ALL defaults to True: a statement-level guard folds",
          "VM_ACTOR_SCAN_ALL" not in out and "every();" in out and "some();" not in out)
    out = MosaikCompiler().compile_program([("main.mos", fork)], platform="gameboy",
                                           defines={"VM_ACTOR_SCAN_ALL": False})
    check("...and actor_scan != 1 (the build states False) takes the other arm",
          "some();" in out and "every();" not in out)

    print()
    if FAILS:
        print("FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("actor_deactivate_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
