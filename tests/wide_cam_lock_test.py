#!/usr/bin/env python3
"""A SCRIPT-PINNED camera in a WIDE room.

A wide room's camera runs to the level width and lives in `camx16`/`camy16`;
`engine.camera`'s pair is u8, the hardware scroll register, which in a wide
room is the camera MOD 256. The two used to be driven independently:

  * `follow_and_render`'s wide arm ignored `cam_lock` entirely - it streamed
    the FOLLOW window and published the follow camera as `camx16`/`camy16`,
    which is what `cam_x()`/`cam_y()` return and therefore what `vm.actor` and
    `vm.projectile` place world-space content against;
  * `cam_apply` then wrote the u8 pin straight to the register.

So a scripted pan moved the register while the streamed content, the actors
and the projectiles all tracked the player. With PARALLAX armed it is worse
still: the scanline interrupt rewrites SCX at every band boundary from the
published values, so the stray register write survives only from where it
lands to the next boundary - one horizontal slice scrolls, the rest holds.

The fix has ONE owner per state. `wide_view(cx16, camy)` publishes the camera,
runs the stream seam and sets the hardware scroll; `cam_apply` calls it for a
pinned camera and the wide arm defers to it. That order matters and is not
arbitrary: a cutscene pan runs while the game is LOCKED, and a lock suspends
the player handler - `cam_apply` is the only camera code `core.run` keeps
calling. Doing it the other way round (applying the pin in the wide arm)
builds a camera that cannot move during the cutscenes it exists for; that was
tried, and the platformer conversion's cutscene simply never panned.

Contracts pinned here, all read off `lib/vm/player.mos`.
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

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _src():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "lib", "vm", "player.mos")
    with open(p, encoding="utf-8") as f:
        return f.read()


def _body(src, name):
    """The text of `function <name>(...) { ... }` by brace matching."""
    m = re.search(r"function %s\(" % re.escape(name), src)
    if not m:
        return ""
    i = src.index("{", m.end())
    depth, j = 0, i
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1
    return ""


def main():
    print("=" * 60)
    print("scripted camera in a wide room")
    print("=" * 60)
    src = _src()

    print("\n[one owner of the wide camera]")
    wv = _body(src, "wide_view")
    check(wv != "", "wide_view() exists (the single apply path)")
    check("camx16 = cx16" in wv and "camy16 = camy" in wv,
          "wide_view PUBLISHES the camera")
    check("g_stream(camx16)" in wv and "g_stream2(camx16, camy)" in wv,
          "... and runs the stream seam for both wide and roam")
    check(wv.index("camy16 = camy") < wv.index("g_stream"),
          "the camera is published BEFORE the seam runs - a parallax shell "
          "reads cam_y() inside it to feed the last band's vertical scroll")

    print("\n[cam_apply owns the PIN]")
    ca = _body(src, "cam_apply")
    check("if wide == 0 {" in ca and "camera.set(cam_lx, cam_ly)" in ca,
          "a NARROW room still pins through engine.camera (unchanged)")
    check("wide_view(lx, ly)" in ca,
          "a WIDE room applies the pin through wide_view - so the level is "
          "STREAMED to the pinned column, not just scrolled to it")
    check("if lx > cam_maxx16 {" in ca,
          "the pin is clamped to the room, as the follow camera is")
    # cam_apply must be the one that runs under a cutscene lock; the wide arm
    # of follow_and_render must NOT also apply it.
    far = _body(src, "follow_and_render")
    wide_arm = far.split("if wide > 0 {", 1)[1] if "if wide > 0 {" in far else ""
    check("if cam_lock == 1 {" in wide_arm
          and "put_player(px - camx16, py - camy16)" in wide_arm,
          "the wide arm DEFERS to it when locked (a lock suspends this "
          "handler, so applying the pin here would never run in a cutscene)")
    check("cam_lx" not in wide_arm.split("wide_view(cx16, camy)")[0],
          "... and does not compute the pin itself (one owner, not two)")

    print("\n[cam_hold seeds from the WIDE camera]")
    ch = _body(src, "cam_hold")
    check("if wide > 0 {" in ch and "camx16" in ch and "camy16" in ch,
          "a wide room seeds the pin from camx16/camy16, not from the u8 "
          "hardware register (which is the camera mod 256 there)")
    check("cam_lx = camera.camx" in ch,
          "a narrow room still seeds from engine.camera (unchanged)")

    print("\n" + "=" * 60)
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All wide cam_lock checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
