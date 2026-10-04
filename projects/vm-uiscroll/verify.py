#!/usr/bin/env python3
"""A dialogue BOX over a SCROLLING room on the window-less consoles (SMS / GG).

The gap this fills: the GB family draws UI on the hardware WINDOW layer, which
is screen space for free, and the Lynx redraws the band into every present. The
SMS and the Game Gear have neither - a box is plotted into the same name table
the level scrolls through, so it is held in place by three cooperating things
(`text.to_window` adding the hardware scroll per plot, the scroll SNAPPING to a
tile while the UI is up, and the camera HOLDING while a UI owns the screen).
All three were covered by CODEGEN tests only; nothing asserted the picture on a
real ROM, which is exactly where a frame-order or camera-owner change bites.

genesis_plus_gx exposes no VRAM through libretro, so every check here is on
PIXELS, and each one is built to have an unambiguous control:

  1. THE BOX IS DRAWN, and only where it should be. A NO-INPUT run never moves
     the camera, so the background is identical before and after the box opens
     and the frame diff IS the box: the region above it must change by ZERO and
     the bottom band by a lot.
  2. THE ROOM REALLY SCROLLS. With RIGHT held and no box yet, the region above
     the box band must change a lot - otherwise check 3 passes trivially on a
     ROM that never scrolled at all.
  3. THE CAMERA HOLDS WHILE THE BOX IS UP. Two frames 200 apart with RIGHT
     STILL HELD must be identical above the box band. This is the assertion a
     regression would break, and holding the pad through it is what makes it
     mean something.
  4. NO SPRITE DRAWS OVER THE BOX (added 2026-09-19 with B1). These consoles
     put sprites unconditionally in FRONT of the background and the box is
     plotted into the background, so `sprite.cut_y` parks every object in the
     box's band. Its control is TIME: the same rectangle must hold the marker
     BEFORE the box opens and nothing but paper after, so a ROM that simply
     never drew the marker fails the first half.

     The two `marker` actors exist for this and are deliberately SCRIPTLESS.
     The cut is a filter inside `sprite.move` rather than a hardware register,
     so it only takes effect when something re-places a sprite - and an actor
     that MOVES is re-placed anyway, by any build. Only a QUIESCENT one can
     catch the two latches that made the shipped feature do nothing:
     `actor.rq_ok` (the whole render pass) and `actor.a_lpok` (the per-slot
     same-position move latch). One marker per console, because the box is
     bottom-anchored and the two screens are 144 and 192 px tall; the other is
     off-window there and parks itself. On the SMS the 128 px marker is a
     second control in the same frame - it sits ABOVE the cut line and must
     KEEP drawing, so a build that simply hid every sprite fails too.
  5. THE BOX CLOSES AT ONCE (added 2026-09-19 with B3). The teardown used to
     CLEAR the band to the box's own paper and then repaint the whole
     32-column ring four columns a game frame, so the room grew back into a
     paper slab from the left over 43 display frames. It repaints the box's
     ROWS now. Its control is check 1 in the same run: the band must have
     differed from the room while the box was up, or "the band is the room
     again" is a statement about a box that never appeared.

The map is vertically STRIPED on purpose (a wall column every 4 tiles): over a
flat floor a scroll is invisible in pixels and checks 2 and 3 would both read
zero. The script waits and then opens a box and LEAVES it open, so no input is
needed to reach the state under test and the frame numbers are stable.

Run:  python verify.py
"""
import os
import subprocess
import sys

PROJ = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(PROJ))
HARNESS = os.path.join(ROOT, "emu", "libretro", "run_lynx.py")

#: Frames chosen to hold on BOTH consoles: a VM frame is a different number of
#: LCD frames per console, so the box opens later on the Game Gear (~700) than
#: on the SMS (~250). 150/210 are pre-box on both; 900/1100 are box-up on both.
PRE_A, PRE_B = 150, 210
BOX_A, BOX_B = 900, 1100
BAND = 48                      # bottom 6 tile rows: where a box is anchored
#: The box's OWN rows (border + one text line + border, bottom-anchored):
#: `scripts.text_lines` reports 1 line, so vm.core sizes it 1 + 2.
BOX_PX = 24
FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- " + detail) if detail else ""))
    if not cond:
        FAILS.append(label)


def _shot(rom, core, frames, png, presses=()):
    cmd = [sys.executable, HARNESS, rom, str(frames), "--core", core,
           "--png", png]
    for p in presses:
        cmd += ["--press", p]
    r = subprocess.run(cmd, capture_output=True, cwd=ROOT)
    return os.path.exists(png) and not r.returncode


def _pixels(png):
    from PIL import Image
    im = Image.open(png).convert("RGB")
    w, h = im.size
    return w, h, list(im.getdata())


def _changed(a, b, y0, y1):
    wa, _ha, da = a
    _wb, _hb, db = b
    return sum(1 for y in range(y0, y1) for x in range(wa)
               if da[y * wa + x] != db[y * wa + x])


# The libretro core per console. The PC Engine joined 2026-08-31, when its
# box was mapped into screen space: its conio plotters write ABSOLUTE cells
# of the BAT, which is the table the VDC scrolls through, so its box slid
# with the level exactly as the SMS/GG one did before `text.to_window` became
# real there. The Lynx is
# deliberately absent: it redraws the open box into the framebuffer every
# present (`draw_open_ui`), so it never had the fault and has nothing to pin.
_CORES = {"sms": "genesis_plus_gx", "gamegear": "genesis_plus_gx",
          "pce": "mednafen_pce_fast"}


def verify_console(plat, ext, tmp):
    rom = os.path.join(PROJ, "build", plat, "vm-uiscroll." + ext)
    # REBUILD, NEVER REUSE. This file used to build only when the ROM was
    # ABSENT, which is the stale-ROM trap `tests/project_verify_test.py`'s own
    # docstring names: a leftover binary reports the behaviour of whatever the
    # engine looked like when it was made, and reports it as current. It
    # matters more here than anywhere, because the wrapper in the suite builds
    # ONE platform and this file tests three.
    build = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"),
                            "build", "--platform", plat, PROJ],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=ROOT)
    if "ROM created" not in ((build.stdout or "") + (build.stderr or "")):
        # No toolchain for this console (cc65 has no download off Windows), or
        # a real build failure - either way, say so rather than measuring a
        # ROM from some earlier engine.
        print("\n== %s ==" % plat)
        print("  skip: no %s ROM was built" % plat)
        return
    core = _CORES.get(plat, "genesis_plus_gx")
    print("\n== %s ==" % plat)

    # 1. THE BOX IS DRAWN -- no input at all, so the camera never moves and the
    #    only thing that can differ between the two frames is the box itself.
    shots = {}
    for f in (PRE_A, BOX_A):
        png = os.path.join(tmp, "%s_still_%d.png" % (plat, f))
        if not _shot(rom, core, f, png):
            print("  skip: %s core unavailable" % plat)
            return
        shots[f] = _pixels(png)
    _w, h, _d = shots[PRE_A]
    above = _changed(shots[PRE_A], shots[BOX_A], 0, h - BAND)
    band = _changed(shots[PRE_A], shots[BOX_A], h - BAND, h)
    check("the box is drawn in the bottom band", band > 100,
          "%d pixels appeared there" % band)
    check("...and nothing above it moved (no input = no camera move)",
          above == 0, "%d pixels changed above the box" % above)

    # 4. NO SPRITE DRAWS OVER THE BOX (B1). The metric needs no hardcoded
    #    colour or rectangle: with the box up the band holds exactly the box's
    #    own two colours, so ANY other colour in the band before it opened is
    #    the marker, by construction. That also gives the control for free -
    #    if the marker was never drawn there is no third colour and the check
    #    says so rather than passing on an empty band.
    #
    #    The PCE is excluded: vm.core has no `ui_snap_sprites` arm for it,
    #    so `sprite.cut_y` is not part of its box at all
    #    and asserting this there would be asserting a feature it never had.
    if plat != "pce":
        def _count(shot, y0, y1, cols):
            wq, _hq, dq = shot
            return sum(1 for y in range(y0, y1) for x in range(wq)
                       if dq[y * wq + x] in cols)

        # THE MARKER COLOUR IS DERIVED FROM THE PRE FRAME ALONE, never by
        # subtracting the box frame's own colours: on a BROKEN build the
        # marker is in the box frame too, so that subtraction empties the set
        # and the failure reads "the marker was never drawn" - a true
        # statement about the measurement and a wrong one about the ROM.
        # This map and this box are two-colour by construction (the striped
        # test tileset, and text on paper), so the sprite's grey is the only
        # third colour on the screen.
        from collections import Counter
        _wq, _hq, _dq = shots[PRE_A]
        flat = Counter(_dq)
        paper_ink = {c for c, _n in flat.most_common(2)}
        mark_cols = {_dq[y * _wq + x]
                     for y in range(h - BOX_PX, h) for x in range(_wq)} - paper_ink
        before = _count(shots[PRE_A], h - BOX_PX, h, mark_cols)
        after = _count(shots[BOX_A], h - BOX_PX, h, mark_cols)
        check("the marker IS drawn in the box's band before it opens",
              bool(mark_cols) and before > 0,
              "%d px in %d colour(s) the box itself never uses"
              % (before, len(mark_cols)))
        check("...and NO sprite draws over the box once it is up",
              after == 0, "%d marker pixels survive in the band" % after)
        # ...AND IT IS A CUT, NOT A BLANKET HIDE. The SMS screen is 192 px and
        # the room 144, so its second marker sits ABOVE the cut line and must
        # keep drawing; on the Game Gear that one is off-window and there is
        # nothing above the band to check, which `pre_above` reports honestly.
        pre_above = _count(shots[PRE_A], 0, h - BOX_PX, mark_cols)
        if pre_above:
            box_above = _count(shots[BOX_A], 0, h - BOX_PX, mark_cols)
            check("...while a sprite ABOVE the cut line still draws",
                  box_above == pre_above,
                  "%d of %d marker pixels above the box survive"
                  % (box_above, pre_above))
        else:
            print("  [note] no marker above the box band on this console"
                  " (it is off-window here), so the cut/hide control is"
                  " the other console's")

    # 2 + 3. Hold RIGHT throughout: the room must scroll BEFORE the box, and
    #        must stop dead once the box owns the screen - with the pad still
    #        held, which is what makes the hold a real assertion.
    held = {}
    for f in (PRE_A, PRE_B, BOX_A, BOX_B):
        png = os.path.join(tmp, "%s_held_%d.png" % (plat, f))
        if not _shot(rom, core, f, png, ["RIGHT@60-%d" % (BOX_B + 100)]):
            print("  skip: %s core unavailable" % plat)
            return
        held[f] = _pixels(png)
    scrolled = _changed(held[PRE_A], held[PRE_B], 0, h - BAND)
    check("the room scrolls while the pad is held", scrolled > 1000,
          "%d pixels changed between frames %d and %d"
          % (scrolled, PRE_A, PRE_B))
    # ...AND THE BOX IS IN THE SAME PLACE IT WAS WITHOUT THE SCROLL. This is
    # the check the other three could not make, and the one the sample exists
    # for: the box holds SCREEN space, so its own rows must look the same
    # whether the room scrolled under it or not. Comparing the band before
    # and after the box opens cannot say this (the room scrolls between those
    # two frames, so the band changes either way), and the hold check below
    # passes on a ROM whose box never appeared at all - the player ends up
    # pinned against the room's right wall and the view is static for a
    # reason that has nothing to do with the UI. Both were true at once until
    # 2026-08-31: a missing core.set_ui_freeze let the camera keep following
    # the player under an open box, so the column streamer repainted the ring
    # straight over the box on SMS and GG, and every check here still passed.
    if plat == "pce":
        # THE PCE ANSWERS A DIFFERENT QUESTION, and a weaker version of the
        # z80 one would have had no teeth. Its box is mapped into screen space
        # but the scroll is NOT rounded to a tile, because
        # the rounding only works with a `ui_snap_sprites` arm to move the
        # sprites with it and vm.core has none for this console. So the
        # box is on the right CELL and up to 7 px from the authored pixel, and
        # a pixel-exact compare cannot pass.
        #
        # A TOLERANCED pixel-diff was tried first and REJECTED, because the
        # metric runs the wrong way: the broken ROM scores BETTER (360 changed
        # pixels) than the fixed one (472), since a box that has slid off the
        # left edge has less content left to differ. Measure the CONTENT
        # instead - how much of the box's text is still on screen - which is
        # what the bug actually destroyed and what the sub-tile shift leaves
        # alone. Measured on this ROM: broken 330 -> 116 glyph pixels (ten of
        # sixteen characters gone), fixed 330 -> 330.
        def _glyphs(shot):
            wq, hq, dq = shot
            return sum(1 for y in range(hq - BOX_PX, hq) for x in range(wq)
                       if dq[y * wq + x] != (0, 0, 0))
        still_px, held_px = _glyphs(shots[BOX_A]), _glyphs(held[BOX_A])
        check("the box keeps ALL of its text after a scroll",
              still_px > 0 and held_px >= still_px * 0.98,
              "%d of %d glyph pixels survive" % (held_px, still_px))
    else:
        moved = _changed(shots[BOX_A], held[BOX_A], h - BOX_PX, h)
        check("the box lands at the same SCREEN position after a scroll",
              moved == 0, "%d pixels differ from the un-scrolled box" % moved)
    frozen = _changed(held[BOX_A], held[BOX_B], 0, h - BAND)
    check("the camera HOLDS while the box is up (pad still held)",
          frozen == 0,
          "%d pixels changed between frames %d and %d" % (frozen, BOX_A, BOX_B))

    # 5. THE BOX CLOSES AT ONCE (B3). The teardown used to CLEAR the band to
    #    the box's own paper and then repaint the whole 32-column ring four
    #    columns a game frame, so the room grew back into a paper-coloured
    #    slab from the left over 43 display frames - the reported "yellow
    #    tiles are overdrawn from left to right". It repaints the box's ROWS
    #    now, in one slice.
    #
    #    Measured at the time (Game Gear, this ROM): 43 display frames before,
    #    0 after - the band is the room again on the first frame sampled. The
    #    bound below is deliberately loose enough not to be flaky and tight
    #    enough that the column walk cannot pass it.
    #
    #    Its control is check 1 in the same run: the band MUST have differed
    #    from the room while the box was up, or "the band matches the room"
    #    is a statement about a box that never appeared.
    if plat != "pce":
        CLOSE_BY = 20
        png = os.path.join(tmp, "%s_closed.png" % plat)
        if _shot(rom, core, BOX_A + CLOSE_BY, png,
                 ["A@%d-%d" % (BOX_A, BOX_A + 6)]):
            closed = _pixels(png)
            left = _changed(shots[PRE_A], closed, h - BAND, h)
            check("the box is GONE %d display frames after the dismiss"
                  % CLOSE_BY, left == 0,
                  "%d band pixels are still not the room" % left)


def main():
    import shutil
    import tempfile
    try:
        from PIL import Image     # noqa: F401
    except Exception:             # noqa: BLE001
        print("skip: Pillow not installed")
        return 0
    if not os.path.isfile(HARNESS):
        print("skip: no libretro harness")
        return 0
    tmp = tempfile.mkdtemp(prefix="vmuiscroll_")
    try:
        verify_console("sms", "sms", tmp)
        verify_console("gamegear", "gg", tmp)
        verify_console("pce", "pce", tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 50)
    if FAILS:
        print("vm-uiscroll verify FAILED (%d): %s" % (len(FAILS), "; ".join(FAILS)))
        return 1
    print("vm-uiscroll verify: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
