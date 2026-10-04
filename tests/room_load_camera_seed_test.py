#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""A streamed room seeds its ring at the camera it is about to SHOW, never at
the map origin.

`engine.scroll.fill` / `scroll2d.fill2d` / `scrollpx.fill` all point the ring at
logical column 0 and fill from there, and the generated `load_room` used to call
one of them BEFORE `player.setup_*` - which is what derives the camera from the
player's entry position. The fade-in at the end of `load_room` therefore
revealed the map's top-left corner, and the first game frame then streamed every
column and row in between, on screen: "several parts of the map are drawn until
the right part is shown".

The reference engine has no such window - its `scene_init` runs `camera_update()` and then
repaints from the settled camera.

The order the generator must emit is therefore:

    player.setup_wide / setup_roam      -- position + bounds
    [player.set_wide_vcam]              -- BEFORE the seed: cam_seed reads it
    player.cam_seed()                   -- publish camx16 / camy16
    scroll*.seed*(player.cam_x(), ...)  -- point the ring there
    scroll*.refill*(...)                -- draw it
    player.set_scroll* (LAST)           -- only now may anything stream

MEASURED on the ROM (the reference-engine sample conversion, GB): walking into
the long walk-in room and straight back out fires its trig5, which re-enters the
80-column PARALLAX room at x = 616 - a camera at column ~68.
Comparing the picture during that room's fade-in against what it settles to,
with no input in between: **88 of 360 background cells wrong before, 0 after.**
Cost: **0 bytes** of bank 0 on both GB targets (every module involved banks).

The wide SHMUP arm deliberately keeps `fill`: an auto-scroll camera starts at
the level's beginning, so the origin IS its entry camera.
"""

from mosaik_vm.rooms import emit_rooms_mos


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def _order(src, names):
    """The line indices of `names` in emission order, or None if one is absent."""
    idx = []
    for n in names:
        hit = [i for i, ln in enumerate(src.splitlines()) if n in ln]
        if not hit:
            return None
        idx.append(hit[0])
    return idx


def main():
    print("a streamed room seeds its ring at the ENTRY camera")
    print("=" * 58)
    ok = True

    cases = [
        ("roam", {"types": ["topdown"], "roam_rooms": True},
         ["player.setup_roam(", "player.cam_seed()", "scroll2d.seed2d(",
          "scroll2d.refill2d(", "player.set_scroll2d("]),
        ("wide", {"types": ["platform"], "wide_rooms": True},
         ["player.setup_wide(", "player.set_wide_vcam(", "player.cam_seed()",
          "scroll.seed(", "scroll.refill(", "player.set_scroll("]),
        # `parallax` is only meaningful on a WIDE room (context.py), so the
        # roam-parallax arm needs both flags.
        ("roam+parallax", {"types": ["topdown"], "roam_rooms": True,
                           "wide_rooms": True, "parallax": True},
         ["player.setup_roam(", "player.cam_seed()", "scrollpx.seed(",
          "scrollpx.refill(", "player.set_scroll2d("]),
        ("wide+parallax", {"types": ["platform"], "wide_rooms": True,
                           "parallax": True},
         ["player.setup_wide(", "player.set_wide_vcam(", "player.cam_seed()",
          "scrollpx.seed(", "scrollpx.refill(", "player.set_scroll("]),
    ]
    for label, info, seq in cases:
        src = emit_rooms_mos(dict(info, uniform=False, has_collision=True))
        idx = _order(src, seq)
        ok &= check("%s: every step is emitted" % label, idx is not None)
        if idx is not None:
            ok &= check("%s: setup -> cam_seed -> seed -> refill -> set_scroll"
                        % label, idx == sorted(idx))
        # ...and the origin-seeding entry points are gone from the follow-camera
        # arms. `fill`/`fill2d` may still appear in a SHMUP world (below).
        ok &= check("%s: no origin fill left" % label,
                    "scroll2d.fill2d(" not in src
                    and "\n        scroll.fill(" not in src
                    and "scrollpx.fill(" not in src)
        # The camera must be the one vm.player derived, not a second copy of
        # the arithmetic in generated code.
        ok &= check("%s: the ring takes vm.player's own camera" % label,
                    "player.cam_x()" in src)

    # The wide SHMUP keeps the origin fill on purpose: its camera starts there.
    sh = emit_rooms_mos({"types": ["shmup"], "wide_shmup": True,
                         "wide_rooms": True, "uniform": False,
                         "has_collision": True})
    ok &= check("wide shmup: keeps fill (an auto-scroll camera starts at 0)",
                "scroll.fill(" in sh and "player.cam_seed()" not in sh)

    # A room that FITS the hardware background is painted, never streamed, so
    # nothing here may touch it.
    plain = emit_rooms_mos({"types": ["topdown"], "uniform": False,
                            "has_collision": True})
    ok &= check("a painted room is untouched",
                "player.cam_seed()" not in plain
                and "scroll2d" not in plain)

    print("=" * 58)
    print("All checks passed" if ok else "Some tests failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
