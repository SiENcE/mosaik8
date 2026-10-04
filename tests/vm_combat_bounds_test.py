#!/usr/bin/env python3
"""vm.combat COLLISION BOUNDS geometry.

`lib/vm/combat.mos` overlaps are a real AABB of two body boxes (each anchored to an
actor ORIGIN) instead of the old Chebyshev point-proximity, so an authored (tighter)
box shrinks the hittable area -- an enemy's transparent CORNER no longer bites. This
is a pure, deterministic MIRROR of that math (kept in lockstep with combat.mos, the
same way the studio catalogue mirrors mosaik_vm.EVENTS), asserting:

  * default 8x8 boxes reproduce the old within(.,8) sword reach + a full-body contact;
  * a TIGHT enemy/player box REJECTS a corner-adjacent overlap the default ACCEPTS
    (the whole point of bounds) while still hitting on a real center overlap.

No ROM: the runtime path is proven end-to-end by projects/vm-hitbox on a real build.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


# --- exact mirror of lib/vm/combat.mos (u8, guarded subtraction only) ----------
HALF = 4
SWORD_REACH = 6
SWORD_HALF = 8
SWORD_W = 16
SWORD_H = 16


def axis_hit(a, aw, b, bw):
    if a <= b:
        return b - a < aw
    return a - b < bw


def box_hit(ax, ay, aw, ah, bx, by, bw, bh):
    return axis_hit(ax, aw, bx, bw) and axis_hit(ay, ah, by, bh)


def sub_clamp(a, b):
    return a - b if a > b else 0


def hitbox_x(px, face, reach):
    if face == 2:
        return px + HALF - reach
    if face == 3:
        return px + HALF + reach
    return px + HALF


def hitbox_y(py, face, reach):
    if face == 1:
        return py + HALF - reach
    if face == 0:
        return py + HALF + reach
    return py + HALF


# the OLD proximity test, for the "default reproduces the old reach" control
def within(ax, ay, bx, by, d):
    dx = ax - bx if ax > bx else bx - ax
    dy = ay - by if ay > by else by - ay
    return dx <= d and dy <= d


def sword_hit(px, py, face, ax, ay, ebx, eby, ebw, ebh):
    hx = hitbox_x(px, face, SWORD_REACH)
    hy = hitbox_y(py, face, SWORD_REACH)
    sx = sub_clamp(hx, SWORD_HALF)
    sy = sub_clamp(hy, SWORD_HALF)
    return box_hit(sx, sy, SWORD_W, SWORD_H, ax + ebx, ay + eby, ebw, ebh)


def contact_hit(px, py, pbx, pby, pbw, pbh, ax, ay, ebx, eby, ebw, ebh):
    return box_hit(px + pbx, py + pby, pbw, pbh, ax + ebx, ay + eby, ebw, ebh)


DEF = (0, 0, 8, 8)      # default body box: full 8x8 footprint
TIGHT = (2, 2, 4, 4)    # a centred 4x4 box (a diamond kind's real body)


def test_default_reproduces_reach():
    # face RIGHT: an enemy touching on the right (its default body vs the sword box)
    # is hit by BOTH the old proximity test and the new AABB -- the far-side reach
    # is preserved when nothing authors a tighter box.
    px, py, face = 50, 50, 3
    for ax in range(px, px + 12):
        old = within(hitbox_x(px, face, SWORD_REACH), hitbox_y(py, face, SWORD_REACH),
                     ax, py, 8)
        new = sword_hit(px, py, face, ax, py, *DEF)
        # the sword still lands on every enemy the old reach caught in front
        if old:
            check(new, "default sword still hits a front enemy the old reach caught (ax=%d)" % ax)


def test_contact_tightens():
    # Player at (50,50); enemy at (57,57): the two DEFAULT 8x8 boxes overlap at the
    # corner (contact), but the two TIGHT 4x4 centred boxes do NOT -- the corner no
    # longer bites. This is the core bounds property.
    px, py, ax, ay = 50, 50, 57, 57
    check(contact_hit(px, py, 0, 0, 8, 8, ax, ay, *DEF),
          "default player vs default enemy CONTACTS at the corner")
    check(not contact_hit(px, py, *TIGHT[:2], TIGHT[2], TIGHT[3], ax, ay, *TIGHT),
          "TIGHT player vs TIGHT enemy does NOT contact at the same corner (tightened)")
    # a real center overlap still contacts with tight boxes (it isn't broken, just tighter)
    check(contact_hit(px, py, TIGHT[0], TIGHT[1], TIGHT[2], TIGHT[3], px, py, *TIGHT),
          "TIGHT boxes still contact on a real center overlap")


def test_sword_tightens():
    # face RIGHT, an enemy just at the far edge of the sword reach: the default body
    # is hit, a tight centred body at the same origin is missed (the sword grazes the
    # transparent corner cell, not the body).
    px, py, face = 50, 50, 3
    ax, ay = 66, 44          # far-right + up: overlaps the 8x8 corner, not the 4x4 body
    check(sword_hit(px, py, face, ax, ay, *DEF),
          "default enemy body is caught at the sword's far corner")
    check(not sword_hit(px, py, face, ax, ay, *TIGHT),
          "a TIGHT enemy body at the same origin is NOT caught (corner grazed, not body)")


def main():
    test_default_reproduces_reach()
    test_contact_tightens()
    test_sword_tightens()
    print()
    if FAILS:
        print("vm.combat bounds tests FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        return 1
    print("All vm.combat bounds tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
