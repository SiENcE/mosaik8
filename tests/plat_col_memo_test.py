#!/usr/bin/env python3
"""update_platform must not re-decide a collision verdict whose inputs did
not move (the hb_* / gd_* memos, 2026-08-30).

The R2 tile memo took the SEAM calls to ~0.8 a frame and the remaining cost
was its own HIT path: sdcc emits `probe_solid` at ~1.6k T-cycles a call, and
a frame PRESSED against a wall makes ~21 of them (span_solid + try_step's
whole ledge ladder + the ground re-probe) to re-decide two verdicts that
cannot have changed - measured as most of a long walk-in room's walking
window (room 5 walk 1.67 -> 1.09 LCD/frame with the memos in).

Collision is STATIC inside a room (replace_tile writes pixels, never the
map), so the keys are exact coordinates and self-validate on movement. THE
WHOLE RISK IS THE MEMO GOING STALE, and the directions are not symmetric:
reading LOW costs one walk that finds what it knew; reading HIGH is a player
STUCK at a climbable ledge or floating over a removed floor, silently. This
pins the discipline:

  - the hb (blocked-horizontal) memo is keyed on GROUNDED as well as the
    destination: try_step can climb a ledge only on the ground, so an
    airborne "blocked" must not outlive the landing (recorded while falling
    past the rest row, it would refuse the step-up for ever);
  - the gd (resting) memo is recorded only when the fall arm moved ZERO
    pixels (a rest, not a landing);
  - the drops: clear_wide (per room), set_step_up (try_step's answer is
    folded into hb), set_platform_cells (the floor gd found may be a
    platform);
  - the whole feature sits in the ELSE arm of the Lynx/PCE fork, so those
    builds compile back to the original body (ROM-checked md5-identical on
    vm-quest lynx and vm-shmup pce, 2026-08-30).

Behaviour is pinned on the ROM by tools/trigprobe/door_probe.py (the
platform door is a memo HIT followed by a force-trigger press),
tools/ladderprobe/ladder_probe.py and the platformer conversion's
verify.py (local only).
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

SRC = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "lib", "vm", "player.mos"), encoding="utf-8").read()

FAILS = []


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    if not cond:
        FAILS.append(label)


def body(name):
    m = re.search(r"^([ \t]*)(?:hot )?(?:local )?function %s\(" % re.escape(name), SRC, re.M)
    assert m, name
    end = re.compile(r"^%s\}" % m.group(1), re.M).search(SRC, m.end())
    return SRC[m.start():end.end()]


def code_lines(text):
    """Lines with the comment tail stripped."""
    return [ln.split("--", 1)[0] for ln in text.splitlines()]


def main():
    # 1. The hb HIT is gated on grounded, in BOTH horizontal arms.
    hits = re.findall(r"hb_ok == 1 and grounded == 1 and hb_dir == \d"
                      r" and hb_nx == \w+ and hb_py == py", SRC)
    check("hb memo hit keys grounded + dir + destination + row, both arms",
          len(hits) == 2, "%d found" % len(hits))

    # 2. The hb memo is ARMED only on the ground (the airborne-ledge trap).
    armed = [m.start() for m in re.finditer(r"hb_ok = 1", SRC)]
    ok = len(armed) == 2
    for pos in armed:
        window = SRC[max(0, pos - 260):pos]
        ok = ok and ("if grounded == 1 {" in window)
    check("hb memo armed only under `if grounded == 1`", ok)

    # 3. The gd memo records a REST (zero pixels moved), never a landing.
    check("gd memo records only when the fall arm moved zero pixels",
          re.search(r"if grounded == 1 and py == py0 \{\s*\n\s*gd_px = px",
                    SRC) is not None)

    # 4. The drops, each at its writer.
    cw = "\n".join(code_lines(body("clear_wide")))
    check("clear_wide drops the hb memo", "hb_ok = 0" in cw)
    check("clear_wide drops the gd memo", "gd_ok = 0" in cw)
    check("set_step_up drops the hb memo (try_step's answer is folded in)",
          "hb_ok = 0" in "\n".join(code_lines(body("set_step_up"))))
    check("set_platform_cells drops the gd memo (its floor may be a platform)",
          "gd_ok = 0" in "\n".join(code_lines(body("set_platform_cells"))))

    # 5. Writer census: nothing else may arm or drop the memos - a new site
    # must be added HERE with its reasoning, not slipped in.
    check("hb_ok writers: exactly 2 arms + 2 drops",
          len(re.findall(r"hb_ok = 1", SRC)) == 2
          and len(re.findall(r"hb_ok = 0", SRC)) == 2)
    check("gd_ok writers: exactly 1 arm + 2 drops",
          len(re.findall(r"gd_ok = 1", SRC)) == 1
          and len(re.findall(r"gd_ok = 0", SRC)) == 2)

    # 6. The fold: every memo token lives in the ELSE arm of a Lynx/PCE
    # platform fork, never in the THEN arm (which must stay the original
    # body so cc65 builds are byte-identical - a folded flag test is not
    # something cc65 reliably removes).
    then_arms = []
    else_arms = []
    for m in re.finditer(r"^([ \t]*)if platform == \"lynx\" or platform == \"pce\" \{",
                         SRC, re.M):
        indent = m.group(1)
        close = re.compile(r"^%s\}( else \{)?" % indent, re.M)
        pos = m.end()
        j = close.search(SRC, pos)
        then_arms.append(SRC[pos:j.start()])
        if j.group(1):
            j2 = close.search(SRC, j.end())
            else_arms.append(SRC[j.end():j2.start()])
    toks = re.compile(r"\b(hb_nx|hb_py|hb_dir|hb_ok|gd_px|gd_py|gd_ok)\b")
    in_then = [t for arm in then_arms for t in toks.findall(
        "\n".join(code_lines(arm)))]
    check("no memo token in a Lynx/PCE THEN arm", not in_then,
          ", ".join(sorted(set(in_then))) if in_then else "")
    inside = sum(len(toks.findall("\n".join(code_lines(a)))) for a in else_arms)
    total = len(toks.findall("\n".join(code_lines(SRC))))
    check("every memo token sits inside a fork's ELSE arm",
          inside == total and total > 0, "%d of %d" % (inside, total))

    if FAILS:
        print("\n%d FAILED" % len(FAILS))
        return 1
    print("\nAll platform-collision-memo checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
