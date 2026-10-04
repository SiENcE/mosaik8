#!/usr/bin/env python3
"""A screen shake is an OFFSET on the scroll, not a scroll write of its own.

`vm.player.cam_apply`'s shake arm used to end in

    bkg.move(camera.camx, camera.camy + jitter)

and `camera.camx` is `engine.camera`'s u8 mirror. In a WIDE (streamed) room
that is not the camera - `camx16` is - so every shaken frame slammed SCX to an
unrelated value, which is the "graphic glitch" reported from
a long walk-in room (161 tiles wide). In a room with PARALLAX bands armed the
same call is a NO-OP: the LYC chain owns SCX/SCY and `gbs_px_move` stands down,
so the shake did nothing whatsoever. A one-screen room hid both, because there
`camera.camx` IS the camera - which is why `vm-cam` looked right.

The reference engine's model, which this now mirrors (`core/vm_camera.c` +
`core/scroll.c`): `camera_shake_frames` only writes `scroll_offset_x/y`, and
`scroll_update` folds them into the ONE scroll it publishes -

    draw_scroll_x = x + scroll_offset_x;
    draw_scroll_y = y + scroll_offset_y;

with the CAMERA clamped to the room BEFORE the offset is added and the offset
itself added raw. That last detail is load-bearing: clamping the sum instead
looks defensible and is measurably wrong, because a horizontal wide room parks
the vertical camera at 0, so a clamp at 0 throws away every negative sample.
Measured in the parallax room with a first cut that clamped: 0..+2 of a
requested +-4, a one-sided shudder instead of a shake.

MEASURED on the ROM (the reference-engine sample conversion, GB), per-scanline SCX/SCY out of
PyBoy's `screen.tilemap_position_list` against the settled frame, shake poked
through the `.noi` at amplitude 4 - see `tools/shakeprobe/shake_probe.py`:

                            BEFORE                    AFTER
  wide streamed (room 5)    dX {0, 120} dY {0,1,2,4}  dX {0}      dY {-4..+2}
  parallax      (room 4)    dX {0, 115} dY {0}        dX {0, 115} dY {-4..+2}

`{0, 115}` is AMBIENT in the parallax room - a control run that pokes no shake
at all reads the same - so the parallax verdict is dY: nothing before, a real
two-sided shake after. In the streamed room the 120 px X jump IS the defect.

Stage A is the offset plumbing. The axis MASK (`CAMERA_SHAKE_X`/`_Y`; ours is
vertical-only) and the WAIT (`waitable = TRUE`; ours returns immediately)
change `OP_SHAKE`'s encoding, so they are their own change - section 6.4
stage B.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik_vm.rooms import emit_rooms_mos

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def _code(src):
    """`src` with comment lines dropped - this file asserts on CODE, and half
    of what it looks for is quoted in the prose that explains why it is gone."""
    return chr(10).join(ln for ln in src.splitlines()
                        if not ln.lstrip().startswith("--"))


def _body(src, name):
    """The body of `function name(...)`, braces balanced."""
    m = re.search(r"function %s\([^)]*\)[^{]*\{" % re.escape(name), src)
    if not m:
        return ""
    i, depth = m.end(), 1
    while i < len(src) and depth:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return src[m.end():i - 1]


def main():
    print("a screen shake is a scroll OFFSET, not a scroll write")
    print("=" * 60)

    player = _read("lib", "vm", "player.mos")
    scrollpx = _read("lib", "engine", "scrollpx.mos")

    # --- 1. the raw write behind the camera's back is gone ----------------
    # `camera.camx` is still read legitimately - cam_x() publishes it, and
    # scroll_bg seeds its pin from it - so the invariant is narrower and
    # exact: no scroll WRITE may take it as its argument.
    check(not re.search(r"bkg\.move\(\s*camera\.cam", _code(player)),
          "no bkg.move in vm.player is aimed at engine.camera's u8 mirror")

    # --- 2. put_scroll is the ONE writer ----------------------------------
    ps = _body(player, "put_scroll")
    check(bool(ps), "vm.player has put_scroll()")
    inside = {ln.strip() for ln in _code(ps).splitlines()}
    outside = [ln for ln in _code(player).splitlines()
               if "bkg.move(" in ln and ln.strip() not in inside]
    check(not outside,
          "every bkg.move in vm.player is inside put_scroll (%d stray)"
          % len(outside))
    check("shk_ox" in ps and "shk_oy" in ps,
          "put_scroll folds in both offsets")
    # the reference engine adds the offset RAW - a clamp on the sum makes the shake
    # one-sided wherever the camera sits at 0. See the module docstring.
    check("< 0" not in ps and "> 223" not in ps,
          "put_scroll WRAPS the sum, it does not clamp it")
    check("if shk_ox == 0" in ps and "return" in ps,
          "put_scroll early-returns to plain bkg.move when no shake is live")

    # --- 3. cam_apply only ROLLS ------------------------------------------
    ca = _code(_body(player, "cam_apply"))
    check("bkg.move(" not in ca, "cam_apply issues no scroll write of its own")
    check("shk_oy = j" in ca, "cam_apply rolls the jitter into the offset")
    check(ca.index("shk_frames > 0") < ca.index("cam_lock == 1"),
          "the roll happens BEFORE the camera writes that read it")
    check("if wide == 0" in ca,
          "cam_apply re-issues the scroll only for a NON-wide room "
          "(wide_view owns its own register)")

    # --- 4. a shake does not survive a room load --------------------------
    cw = _code(_body(player, "clear_wide"))
    check("shk_frames = 0" in cw and "shk_ox = 0" in cw and "shk_oy = 0" in cw,
          "clear_wide zeroes the shake, as the reference engine's scene load does")

    # --- 5. wide_view folds the offset in on every arm --------------------
    wv = _code(_body(player, "wide_view"))
    check("bkg.move(" not in wv and wv.count("put_scroll(") >= 3,
          "wide_view's roam / vcam / plain-wide arms all go through put_scroll")
    check("shk_ox != 0 or shk_oy != 0" in wv,
          "the plain-wide arm re-issues the streamer's scroll while shaking")

    # --- 6. the parallax shadow carries it too ----------------------------
    up = _code(_body(scrollpx, "update"))
    check("sox" in up and "soy" in up,
          "engine.scrollpx.update publishes the offset into the band shadow")
    check("< 0" not in up.split("soy")[-1],
          "scrollpx wraps the vertical sum too")
    check("sox, soy" in scrollpx, "scrollpx exports sox/soy")

    # --- 7. the generated seam is the bridge ------------------------------
    for label, info in (
            ("wide+parallax", {"types": ["platform"], "wide_rooms": True,
                               "parallax": True}),
            ("roam+parallax", {"types": ["topdown"], "roam_rooms": True,
                               "wide_rooms": True, "parallax": True})):
        src = emit_rooms_mos(dict(info, uniform=False, has_collision=True))
        check("scrollpx.sox = player.shake_ox()" in src
              and "scrollpx.soy = player.shake_oy()" in src,
              "%s: the stream seam copies the offset into scrollpx" % label)

    # A world with no bands must not mention any of it.
    plain = emit_rooms_mos({"types": ["platform"], "wide_rooms": True,
                            "uniform": False, "has_collision": True})
    check("shake_ox" not in plain,
          "a non-parallax world's rooms.mos is untouched (byte-identical off)")

    print("=" * 60)
    print("All checks passed" if not _FAILED
          else "FAILED: %d" % len(_FAILED))
    return 1 if _FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
