#!/usr/bin/env python3
"""`vm.player`'s position IS the collision box, not the drawn sprite (K4).

The reference engine's `PLAYER.pos` is the box's origin and the art is drawn ABOVE it, so
its player can stand on the TOP ROW of a room with its head clipped off the
screen. We held the SPRITE's top-left and derived the box as `pos + offset`,
which meant the box could never go higher than that offset - 8 px for the
topdown player. The town room reaches a room with a launch pad through a trigger
at **x 39, y 0, 13x1**, the only reference to that scene in the whole project,
so an unreachable row 0 made a whole scene dead.

The fix moves the origin rather than patching the trigger test: `px`/`py` are
the box, `box_x()`/`box_y()` are the identity, the collision probes drop their
offset, and `put_player` is the ONLY thing that still subtracts it - the rule
this file already follows for the feet lift, that a drawing offset is a
RENDERING transform and nothing else should see it.

**It is byte-identical for any project that sets no box offset** (`bx == by ==
0` makes the two spaces the same point), which is every project except the two
reference-engine conversions.

ROM-verified on a probe spawned at tile (45,3) in the town room: walking up, the
player's sprite reaches world y **-2** (its head off the top of the room, which
is exactly the behaviour being restored) and the room CHANGES to a screen-sized
one whose camera cannot scroll - the launch-pad room. A second probe, spawned at the
tile an interior room returns you to (31,41), confirms the arrival still does not
re-fire the trigger it came through.

SOURCE-CONTRACT test: it pins where the offset is allowed to appear.
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
_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _body(src, name):
    m = re.search(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    indent = m.group(1)
    end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
    return src[m.start():end.end() if end else len(src)]


def main():
    with open(os.path.join(ROOT, "lib", "vm", "player.mos"),
              encoding="utf-8") as f:
        src = f.read()

    print("\n[the position IS the box]")
    check("return px\n" in _body(src, "box_x"), "box_x() is the identity")
    check("return py\n" in _body(src, "box_y"), "box_y() is the identity")

    print("\n[only the renderer sees the offset]")
    # Every `bx` / `by` read outside put_player is what the old model needed.
    #
    # `draw_x`/`draw_y` are the ONE sanctioned exception: the transform itself,
    # given a name, for callers that must line up with the PICTURE rather than
    # the box (the emote bubble hangs `drawn height` above the sprite's top,
    # and anchoring it to pos_y put it 8 px into the player's head). The point
    # of this check is that nobody re-derives the offset AD HOC - so a named
    # accessor that IS `pos - offset` is allowed, and anything else is not.
    reads = [(n, ln) for n, ln in enumerate(src.splitlines(), 1)
             if re.search(r"\b(bx|by)\b", ln)
             and not ln.lstrip().startswith("--")]
    pp = _body(src, "put_player")
    dx, dy = _body(src, "draw_x"), _body(src, "draw_y")
    allowed = set()
    for n, ln in reads:
        s = ln.strip()
        if (s in ("var bx: u8", "var by: u8", "bx = ox", "by = oy")
                or ln in pp.splitlines()
                or ln in dx.splitlines() or ln in dy.splitlines()):
            allowed.add(n)
    stray = [(n, ln.strip()) for n, ln in reads if n not in allowed]
    check(not stray,
          "bx/by are read ONLY by put_player and the draw_x/draw_y accessors "
          "(plus their declaration and setter)%s"
          % ("" if not stray else ": " + repr(stray[:3])))
    check("return px - bx" in dx and "return py - by" in dy,
          "draw_x/draw_y ARE the drawing transform (pos - offset)")
    check("if px > bx" in dx and "if py > by" in dy,
          "...and clamp at 0, since a u16 world pixel would WRAP past the "
          "map origin")
    check("var ax: u16 = x - bx" in pp and "var ay: u16 = y - by" in pp,
          "put_player draws the art up-left of the box")
    check("sprite.move(pbase, ax, ay)" in pp,
          "... and moves the sprite THERE, not to the box")

    print("\n[the collision probes measure the box in place]")
    bs = _body(src, "box_solid")
    check("var xl: u16 = x" in bs and "+ bx" not in bs and "+ by" not in bs,
          "box_solid takes the box's own top-left")
    bd = _body(src, "box_down")
    check("var feet: u16 = y + ph" in bd and "+ by" not in bd,
          "box_down's feet row is measured from the box")
    aa = _body(src, "actor_at")
    check("g_ablock(x, y)" in aa,
          "the actor-block seam is handed the box position")

    print("\n[byte-identical without an offset]")
    # bx/by default to 0 (BSS, no initializer), so px/py and the sprite's
    # top-left are the same point for every project that never calls
    # set_box_offset - which is every one but the reference-engine conversions.
    check(re.search(r"^\s*var bx: u8\s*$", src, re.M) is not None
          and re.search(r"^\s*var by: u8\s*$", src, re.M) is not None,
          "bx/by are zero-initialised BSS, so the two spaces coincide")

    print()
    if _FAILED:
        print("SOME CHECKS FAILED (%d)" % len(_FAILED))
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
