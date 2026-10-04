#!/usr/bin/env python3
"""THE PLAYER RE-LAYS ITS FAN ONLY WHEN THE LAYOUT CHANGED - apply's rule, the
half that was missing.

`canim.apply` learnt on 2026-08-28 that a clip STEP is usually not a layout
change: the fan's geometry is a function of (base, clip KIND, mask) alone,
because `draw_meta` takes its w/h from the kind and parks exactly the masked
columns itself - so a step that changes only the TILE re-tiles objects that are
already in the right place, and `repos` writes back positions they already
hold. `draw_player` kept calling `player.repos()` on every drawn change.

That is the same shape as the move latch itself, where the player half was the
missing one. It went unnoticed because the two towns' players STAND STILL, so
draw_player's R1 fast path returns before reaching it. Measured on
the reference-engine sample conversion's shooter room (2026-08-31), whose ship steps its clip ~1.1 times
per GAME frame: `vm_player_repos` **1.00 -> 0.00 calls a frame** idle and
0.78 -> 0.00 walking, `canim.draw_player` **14,062 -> 11,316 cycles**, room 8
idle 2.19 -> 2.14 LCD/frame. Every one of those repos calls was re-positioning
the fan to where it already was.

THE CONTRACT THIS PINS:

  * the relay decision is (base, mask) + "the clip kind cannot have changed",
    computed BEFORE the cache fields are updated;
  * `set_player` is the ONE writer of `p_clip` and it drops `p_uok` - which is
    what lets the gate stand in for apply's explicit `a_uclip` compare. A
    second writer of p_clip would make the gate wrong SILENTLY (a new kind's
    fan drawn at the old kind's cell layout), so this test fails on one;
  * a DESCRIPTOR kind always relays: its object LIST is the frame
    (`clips.draw` -> `sprite.set_meta_list` writes each child's authored
    dx/dy), so its layout changes with the frame while base, mask and kind do
    not;
  * the Lynx/PCE arm is the original unconditional `player.repos()`, verbatim -
    their present model re-asserts every frame and their sample builds are held
    md5-identical;
  * the FLIP arm below still relays unconditionally (a flip reverses the cell
    order inside the move helper).

`draw_player` is TWO compile-time arms since the batched selector
(VM_CLIP_SEL, 2026-09-03), exactly as `apply` is: the batched arm takes the
re-lay verdict off the selector's is_desc bit and inlines draw_frame over it,
the original arm is kept character for character. Every rule here holds PER
ARM, so the body is split at the fork and each half checked on its own.

SOURCE-CONTRACT test; the behaviour is proved on the ROM with
tools/framebudget/oam_trace.py (all nine room x regime OAM streams
byte-identical per game frame) and measured with framebudget.py.
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
    src = open(CANIM, encoding="utf-8").read()
    dp_full = body(src, "draw_player")
    check("vm.canim has draw_player", bool(dp_full))
    split = dp_full.find("if VM_CLIP_SEL {} else {")
    check("draw_player carries the VM_CLIP_SEL fork", split >= 0)

    for name, dp in (("batched", dp_full[:max(0, split)]),
                     ("original", dp_full[max(0, split):])):
        print("  [%s arm]" % name)
        # --- the gate -----------------------------------------------------
        check("the relay flag exists and defaults to RELAY",
              "var relay: u8 = 1" in dp,
              "reading it LOW freezes a layout silently; HIGH only costs the fan")
        check("the layout key is (base, mask)",
              "p_uok == 1 and p_ubase == p_base and p_umsk == msk" in dp)
        gate = dp.split("var relay: u8 = 1", 1)[1]
        check("the gate is decided BEFORE the cache fields are refreshed",
              gate.index("relay = 0") < gate.index("p_ubase = p_base"))
        if name == "batched":
            # The verdict rides the SELECTOR's is_desc bit, which is the whole
            # point of the arm: the un-batched body read g_isdesc twice per
            # step frame, once here and once inside draw_frame.
            check("a DESCRIPTOR kind always relays",
                  re.search(r"relay = 0\s*\n\s*if dsc == 1 \{\s*\n\s*relay = 1",
                            gate) is not None)
            check("...off the selector, not a second banked table read",
                  "g_isdesc(p_clip)" not in gate)
        else:
            check("a DESCRIPTOR kind always relays",
                  re.search(r"if VM_META_LIST \{\s*\n\s*if g_hasdraw == 1 \{\s*\n\s*"
                            r"if g_isdesc\(p_clip\) == 1 \{\s*\n\s*relay = 1",
                            gate) is not None)
        # The cc65 arm of BOTH forks is EMPTY, and that is not the same shape
        # as apply's (whose Lynx/PCE arm still calls actor.repos): the player's
        # re-lay was already folded away there before the gate existed, because
        # their put_player moves unconditionally every frame. So the gate must
        # leave that arm exactly as it found it - empty - which is what keeps
        # those builds md5-identical. A `relay = 0` reachable on cc65 would be
        # a fold that CHANGES the folded console.
        fold = 'if platform == "lynx" or platform == "pce" {'
        # scoped to the drawn-change branch: draw_player has other platform
        # forks (the R1 fast path above it, the shadow refresh below)
        region = gate.split("if VM_CLIP_PAL", 1)[0]
        parts = region.split(fold)
        # The forks are identified by CONTENT, not position: the batched arm
        # also folds the kind-size latch (`draw_meta(..., p_mw, p_mh, ...)`,
        # 2026-09-05), whose Lynx/PCE arm is the original meta_w_of call
        # and is NOT expected to be empty.
        gate_arm = [a for a in parts[1:] if "relay = 0" in a.split(fold)[0]]
        repos_arm = [a for a in parts[1:] if "player.repos()" in a]
        check("both the gate and the repos call sit behind the fold",
              len(gate_arm) == 1 and len(repos_arm) == 1,
              "found %d fork(s): %d gate, %d repos"
              % (len(parts) - 1, len(gate_arm), len(repos_arm)))
        if len(gate_arm) == 1 and len(repos_arm) == 1:
            parts = [None, gate_arm[0], repos_arm[0]]
            for arm, what in ((parts[1], "gate"), (parts[2], "repos call")):
                cc65 = arm.split("} else {", 1)[0]
                check("the Lynx/PCE arm of the %s is EMPTY" % what,
                      cc65.strip() == "",
                      "apply's is not - do not copy its shape here")
            check("the gate's else arm holds the layout compare",
                  "if p_uok == 1" in parts[1].split("} else {", 1)[1])
            check("the repos else arm is guarded by relay",
                  re.search(r"if relay == 1 \{\s*\n\s*player\.repos\(\)",
                            parts[2].split("} else {", 1)[1]) is not None)
        # --- what must NOT have been gated --------------------------------
        flip = dp.split("if f != p_pflip", 1)[-1]
        check("the FLIP arm still relays unconditionally",
              "player.repos()" in flip and "relay" not in flip,
              "a flip reverses the cell order inside the move helper")

    # --- what the gate leans on -------------------------------------------
    # p_clip is not in the layout key; it does not need to be, because a clip
    # change cannot reach the gate with p_uok still set. That is a property of
    # exactly one function, so it is asserted rather than assumed.
    writers = [m.start() for m in re.finditer(r"^\s*p_clip = ", src, re.M)]
    sp = body(src, "set_player")
    check("set_player is the ONE writer of p_clip", len(writers) == 1,
          "found %d" % len(writers))
    check("...and it is inside set_player",
          bool(sp) and src.index(sp) <= writers[0] < src.index(sp) + len(sp))
    check("...which drops p_uok", "p_uok = 0" in sp,
          "so a new clip always relays")

    # --- the rule this mirrors, one level down ----------------------------
    ap = body(src, "apply")
    check("apply still carries the same rule (this is its player half)",
          "var relay: u8 = 1" in ap and "a_uclip[i] == k" in ap)

    print()
    if FAILS:
        print("FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("player_relay_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
