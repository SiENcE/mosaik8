#!/usr/bin/env python3
"""The topdown box test must not re-scan a box whose overlapped TILES did not
change (the bs_* whole-box verdict memo, 2026-09-09).

`box_solid` samples every tile its rectangle overlaps and nothing else, so its
answer is a pure function of the overlapped tile RECTANGLE. The topdown
`update()` re-ran the whole scan every frame anyway: measured in
the reference-engine sample conversion's room 10 (nine actors, held right) SIX `probe_solid` calls a
frame at ~1,533 T-cycles each, 9,200 of a 17,197-cycle `vm_player_update`.
Keyed on the four tile-aligned edges the room's walk went 2.13 -> 2.02
LCD/frame and five other regimes crossed with it.

It is the platform arm's hb_*/gd_* memos one room type over, and it carries
the same risk: reading LOW costs one scan that finds what it knew, reading
HIGH is a player walking THROUGH a wall, silently. What keeps it honest is
that the key IS the answer's input, so it self-validates on movement and on a
box resize (pw/ph reach it through xr/yb) and the only drop it needs is the
room load. This pins that discipline:

  - all FOUR edges are compared (a partial key is a stale verdict at the
    moment the box crosses a tile boundary on the axis left out);
  - the far edges are the ones the scan itself clamps to (xr/yb), so a
    `set_box` resize cannot outlive its own key;
  - the tile-aligned spelling is `& 0xFFF8`, the two byte ops the tile memo
    below it measured, never a u16 `>> 3`;
  - the drop is clear_wide (per room), with the same unreachable 0xFFFF the
    tile memo seeds, so no separate valid flag can go out of step with it;
  - the whole feature sits in the ELSE arm of the Lynx/PCE fork, so those
    builds compile back to the original body (ROM-checked: all ten Lynx and
    PCE sample builds md5-identical, 2026-09-09).

Behaviour is pinned on the ROM by the reference-engine sample and platformer
conversions' verify.py (local only) ("player walks horizontally" is exactly a memo that must not read
HIGH) and by a local door probe.
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


KEYS = ("bs_kx0", "bs_kx1", "bs_ky0", "bs_ky1")


def main():
    bs = "\n".join(code_lines(body("box_solid")))

    # 1. The HIT compares all four edges, or a box crossing a tile boundary on
    # the axis left out answers with the rectangle it has just left.
    hit = re.search(r"if bs_kx0 == bs_x0 and bs_kx1 == bs_x1"
                    r" and bs_ky0 == bs_y0 and bs_ky1 == bs_y1 \{", bs)
    check("the memo hit compares all four tile-aligned edges", hit is not None)

    # 2. The key is built from the SCAN's own far edges, so a set_box resize
    # invalidates it without a drop site of its own.
    check("the far-edge keys are derived from xr / yb (a resize self-validates)",
          re.search(r"bs_x1 = xr & 0xFFF8", bs) is not None
          and re.search(r"bs_y1 = yb & 0xFFF8", bs) is not None)
    check("the near-edge keys are derived from xl / yt",
          re.search(r"bs_x0 = xl & 0xFFF8", bs) is not None
          and re.search(r"bs_y0 = yt & 0xFFF8", bs) is not None)

    # 3. The measured spelling: an AND, never a u16 shift (the tile memo's own
    # note - `>> 3` compiles to three memory rr/srl rounds on the sm83).
    check("the tile-aligned key is `& 0xFFF8`, never a `>> 3`",
          len(re.findall(r"bs_[xy]\d = \w+ & 0xFFF8", bs)) == 4
          and not re.search(r"bs_[xy]\d = \w+ >> 3", bs))

    # 4. BOTH returns of the scan record the verdict. A `return true` that
    # forgets is a wall the memo never learns about; a `return false` that
    # forgets is the whole saving.
    stores = re.findall(r"bs_kx0 = bs_x0", bs)
    check("both scan exits record the verdict", len(stores) == 2,
          "%d found" % len(stores))
    for verdict in ("bs_r = 1", "bs_r = 0"):
        check("the scan records %s at its own exit" % verdict,
              verdict in bs)
    for k in KEYS:
        check("%s is written at both exits" % k,
              len(re.findall(r"%s = %s" % (k, k[:3] + k[4:]), bs)) == 2)

    # 5. The drop, at the one writer that can invalidate it: collision is
    # static INSIDE a room, so the room load is the only stale-maker.
    cw = "\n".join(code_lines(body("clear_wide")))
    check("clear_wide drops the whole-box memo", "bs_kx0 = 0xFFFF" in cw)
    check("the drop uses the unreachable 0xFFFF seed, not a second valid flag",
          not re.search(r"\bbs_ok\b", SRC))

    # 6. Writer census: exactly the two stores and the one drop. A new site
    # belongs HERE with its reasoning, not slipped in.
    check("bs_kx0 writers: exactly 2 stores + 1 drop",
          len(re.findall(r"bs_kx0 = ", "\n".join(code_lines(SRC)))) == 3)

    # 7. The fold: every memo token lives in the ELSE arm of a Lynx/PCE fork,
    # never in the THEN arm, which must stay the original body so the cc65
    # builds are byte-identical.
    then_arms, else_arms = [], []
    for m in re.finditer(r"^([ \t]*)if platform == \"lynx\" or platform == \"pce\" \{",
                         SRC, re.M):
        indent = m.group(1)
        close = re.compile(r"^%s\}( else \{)?" % indent, re.M)
        j = close.search(SRC, m.end())
        then_arms.append(SRC[m.end():j.start()])
        if j.group(1):
            j2 = close.search(SRC, j.end())
            else_arms.append(SRC[j.end():j2.start()])
    toks = re.compile(r"\b(bs_x0|bs_x1|bs_y0|bs_y1|bs_kx0|bs_kx1|bs_ky0|bs_ky1|bs_r)\b")
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
    print("\nAll whole-box collision-memo checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
