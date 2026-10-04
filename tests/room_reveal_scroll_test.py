#!/usr/bin/env python3
"""A room is not REVEALED at the previous room's scroll.

`load_room` blacks the screen, paints, and ramps the fade back in. But the
scroll register is written by the PLAYER HANDLER, and the handler does not run
until load_room RETURNS - so every frame of the ramp was drawn through the
scroll the PREVIOUS room left behind.

Measured on the ROM before the fix (tools/framebudget/room_reveal_probe.py,
entering the reference-engine sample conversion's interior room from the town room at SCX 228 / SCY 236):
**sixteen LCD frames** of the new room shown at the old room's offset. The
hardware tilemap is 32 cells wide and a small room paints only 20 of them, so
the visible window straddled the painted region and the stale columns either
side of it - the reported "left portion in another room's art". Ten of the
sample's seventeen rooms did it; the tilemap, the tile DATA and the CGB
attribute map were all already correct when the reveal happened, which is what
ruled out the standing "the tileset upload is visible mid-flight" hypothesis.

The fix is one `player.settle_camera()` before the ramp. This pins the three
things that make it correct rather than merely present:

  * it runs INSIDE the fade guard, so a console with no fade (Lynx/PCE) does
    not gain a call it has no ramp to be early for;
  * it runs BEFORE the ramp, not after it;
  * `settle_camera` is the SAME code the handler runs (`place_camera`, split
    out of `follow_and_render`), not a second camera rule that could drift
    from it - and it stands down on a player-less room, whose camera
    `reset_view()` has already zeroed. Following a hidden player from there
    would undo exactly that.

SOURCE-CONTRACT test; the behaviour is measured with
`tools/framebudget/room_reveal_probe.py` (10 of 17 rooms stale -> 0, with every
settled screen digest byte-identical).
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
PLAYER = os.path.join(ROOT, "lib", "vm", "player.mos")
EMIT = os.path.join(ROOT, "mosaik_vm", "rooms", "emit_dispatch.py")

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
    p = open(PLAYER, encoding="utf-8").read()
    e = open(EMIT, encoding="utf-8").read()

    # --- the engine half ---------------------------------------------------
    pc = body(p, "place_camera")
    check("vm.player has place_camera", bool(pc))
    check("...it owns the WIDE arm's stream", "wide_view(camx16, camy16)" in pc)
    check("...and the narrow arm's follow",
          "camera.follow(cx, cy, HALFW, HALFH, cam_maxx, cam_maxy)" in pc)
    check("...and leaves a SCRIPT-PINNED camera to cam_apply",
          pc.count("cam_lock") >= 2)

    far = body(p, "follow_and_render")
    check("follow_and_render places through it, not a second copy",
          far.count("place_camera()") == 2, "found %d call(s)"
          % far.count("place_camera()"))
    for gone in ("camera.follow(cx, cy", "wide_view(camx16, camy16)"):
        check("...the inlined `%s` is gone from it" % gone[:22],
              gone not in far)

    sc = body(p, "settle_camera")
    check("vm.player exports settle_camera",
          bool(sc) and re.search(r"^\s*export settle_camera\s*$", p, re.M)
          is not None)
    check("...it runs the SAME code the handler runs", "place_camera()" in sc)
    # A player-less room's camera is already settled to (0,0) by reset_view;
    # following a HIDDEN player from here would undo it.
    check("...and stands down on a player-less room (pmode == 0)",
          re.search(r"if pmode == 0 \{\s*\n\s*return", sc) is not None)
    rv = body(p, "reset_view")
    check("reset_view is still what zeroes a player-less camera",
          "pmode = 0" in rv and "camera.set(0, 0)" in rv)

    # --- the generator half ------------------------------------------------
    i_call = e.find('"            player.settle_camera()"')
    i_guard = e.find('L += ["        " + FADE_GUARD')
    i_ramp = e.find('"            var fl: u8 = 3"')
    check("load_room settles the camera before the fade ramp",
          -1 < i_guard < i_call < i_ramp,
          "guard %d, call %d, ramp %d" % (i_guard, i_call, i_ramp))
    # INSIDE the guard: a console with no fade has no ramp to be early for,
    # and would only pay the call (and lose its byte-identity).
    check("...INSIDE the fade guard, so a console with no fade is untouched",
          i_call > i_guard)
    # ...and only where there IS a fade at all.
    m = re.search(r"\n    if fade:\n(.*?)\n    if info\.get\(\"on_load_hook\"\)",
                  e, re.S)
    check("...and only under `if fade:`",
          m is not None and "player.settle_camera()" in m.group(1))

    print("\n" + ("All checks passed" if not FAILS
                  else "SOME CHECKS FAILED: %s" % FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
