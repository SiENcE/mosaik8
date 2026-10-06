#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""THE LETTERBOX VIEW (`studio.toml [scenes] letterbox`, `video.set_view`).

A room smaller than the screen - an 18-row room on the SMS's 24 rows or the
PC Engine's 28, a 20-column room on their 32 - used to sit in the TOP-LEFT
corner with the dialogue box anchored to the SCREEN's bottom, i.e. below the
room. Letterboxed, the room is CENTRED and the box sits on the room's own
bottom edge.

How, and what this pins:

* EVERYTHING the program computes stays in ROOM-VIEW space; the offset is
  added at the two hardware commits only. The scroll commit subtracts it
  (SMS/GG: GBS_VIEW_SCX/SCY in gbs_wait_vblank, the vertical register
  wrapping at 224; PCE: the BXR/BYR flush) and every on-screen sprite
  placement adds it (SMS/GG: GBDK's DEVICE_SPRITE_PX_OFFSET_X/Y, which all
  the placement sites already add, is redefined once per TU; PCE: the one
  gbs_move_sprite leaf). A PARK writes fixed coordinates without that term.
* vm.core anchors the box and the menu to the view (core.set_view), folded
  on VM_NO_VIEW so a project without the knob keeps its arms.
* The rooms generator emits both calls on every room load (the GB family gets
  offset 0 and the full screen) and clears the margin rows to the
  background's full height, because the top margin shows the rows that wrap.
* OFF is byte-identical: no prelude symbol, no rooms line, no define.

Plus the SHAKE SETTLE fix it exposed: cam_apply zeroed the offset before
testing it, so the settle frame never re-wrote the clean scroll and a
player-less menu room kept the last jittered value.
"""

import glob
import io
import re
import shutil
import tempfile

from mosaik import MosaikCompiler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VIEW = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    import "graphics.sprite"
    function main() {
        video.enable_lcd()
        video.set_view(48, 24)
        bkg.move(0, 0)
        sprite.move(0, 10, 10)
        loop { video.wait_vblank() }
    }
    export main
}
'''
PLAIN = VIEW.replace("        video.set_view(48, 24)\n", "")
# ...with text and a background engine, for the PCE margin clear
VIEW_TEXT = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    import "graphics.sprite"
    import "graphics.text"
    const T: array[u8, 1] = [0]
    function main() {
        video.enable_lcd()
        video.set_view(48, 24)
        bkg.move(0, 0)
        bkg.set_tiles(0, 0, 1, 1, T)
        sprite.move(0, 10, 10)
        text.clear_area(0, 18, 32, 14)
        loop { video.wait_vblank() }
    }
    export main
}
'''

FAILS = []


def check(label, cond):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label)
    return cond


def c_for(src, platform):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def scy(y, oy):
    """GBS_VIEW_SCY in Python: the shadow in view space -> the SMS register."""
    v = y - 256 if y >= 224 else y
    return (v - oy + 224) % 224


def test_prelude():
    print("[the prelude: real where the screen outgrows a room]")
    for plat in ("sms", "gamegear"):
        c = c_for(VIEW, plat)
        check("%s: the offset is DEFINED once" % plat,
              c.count("uint8_t gbs_view_ox = 0, gbs_view_oy = 0;") == 1)
        check("%s: the sprite term is redefined over the view" % plat,
              "#define DEVICE_SPRITE_PX_OFFSET_X (GBS_DSPX + gbs_view_ox)" in c
              and "#define DEVICE_SPRITE_PX_OFFSET_Y (GBS_DSPY + gbs_view_oy)" in c)
        check("%s: ...after capturing the console's own value" % plat,
              c.index("enum { GBS_DSPX = DEVICE_SPRITE_PX_OFFSET_X")
              < c.index("#undef DEVICE_SPRITE_PX_OFFSET_X"))
        check("%s: the scroll commit goes through the view" % plat,
              "move_bkg(GBS_VIEW_SCX(gbs_scr_shx), GBS_VIEW_SCY(gbs_scr_shy));" in c
              and "move_bkg(gbs_scr_shx, gbs_scr_shy);" not in c)
    c = c_for(VIEW, "sms")
    check("the vertical commit wraps at 224 and reads 252..255 as negative",
          ">= 224u ? (int16_t)(y) - 256" in c and "+ 224) % 224" in c)
    check("...which puts a centred room where it belongs",
          scy(0, 24) == 200 and scy(255, 24) == 199 and scy(10, 0) == 10
          and scy(200, 0) == 200)

    c = c_for(VIEW, "gameboy")
    check("GB: a no-op (its screen is the smallest a room can be)",
          "void gbs_set_view(uint8_t x, uint8_t y) { (void)x; (void)y; }" in c
          and "#undef DEVICE_SPRITE_PX_OFFSET_X" not in c
          and "GBS_VIEW_SCY" not in c)

    c = c_for(VIEW, "pce")
    check("PCE: the offset is defined",
          "static uint8_t gbs_view_ox = 0, gbs_view_oy = 0;" in c)
    check("PCE: the BXR/BYR flush subtracts it in the registers' FULL width "
          "(conio text is not replicated, so a u8 wrap hid every box)",
          "- (int16_t)gbs_view_ox) & 0x3FF) : gbs_bkg_x);  /* BXR */" in c
          and "- (int16_t)gbs_view_oy) & 0x1FF) : gbs_bkg_y);  /* BYR */" in c)
    check("PCE: an axis with no offset keeps its historical register value",
          "gbs_vreg(7, gbs_view_ox ? " in c and "gbs_vreg(8, gbs_view_oy ? " in c)
    ct = c_for(VIEW_TEXT, "pce")
    check("PCE: a clear below the 28 conio rows is left to the BAT replicate "
          "(a cclearxy there wrote over row 0)",
          "if ((uint8_t)(y + j) < SCREEN_ROWS) cclearxy(" in ct
          and "gbs_bat_replicate(x, y, w, h);" in ct)
    check("PCE off: the historical clear loop",
          "if ((uint8_t)(y + j) < SCREEN_ROWS)" not in
          c_for(VIEW_TEXT.replace("        video.set_view(48, 24)\n", ""), "pce"))
    check("PCE: the sprite leaf adds it",
          "(uint16_t)(64u + y + gbs_view_oy)" in c
          and "(uint16_t)(32u + x + gbs_view_ox)" in c)

    print("[off is byte-identical: nothing without the call]")
    for plat in ("sms", "gamegear", "gameboy", "pce"):
        c = c_for(PLAIN, plat)
        check("%s: no view symbol at all" % plat,
              "gbs_view" not in c and "gbs_set_view" not in c
              and "GBS_VIEW" not in c and "GBS_DSPX" not in c)
    c = c_for(PLAIN, "sms")
    check("SMS off: the plain commit", "move_bkg(gbs_scr_shx, gbs_scr_shy);" in c)


def _rooms_text(letterbox):
    """Generate rooms.mos for a COPY of vm-shardlings with the knob set."""
    import toml
    import mosaik_vm
    tmp = tempfile.mkdtemp(prefix="letterbox_test_")
    try:
        dst = os.path.join(tmp, "p")
        shutil.copytree(os.path.join(ROOT, "projects", "vm-shardlings"), dst,
                        ignore=shutil.ignore_patterns("build"))
        sp = os.path.join(dst, "studio.toml")
        s = toml.load(sp)
        if letterbox:
            s.setdefault("scenes", {})["letterbox"] = True
        else:
            s.get("scenes", {}).pop("letterbox", None)
        with open(sp, "w", encoding="utf-8") as f:
            f.write(toml.dumps(s))
        mosaik_vm.generate_rooms(dst)
        return io.open(os.path.join(dst, "src", "rooms.mos"), encoding="utf-8").read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_rooms():
    print("[the rooms generator]")
    on, off = _rooms_text(True), _rooms_text(False)
    check("on: every room load hands the prelude its offset and core its view",
          "video.set_view(vox, voy)" in on and "core.set_view(vcols, vrows)" in on)
    check("...centred, in whole tiles",
          "vox = ((SCREEN_COLS - vcols) / 2) * 8" in on
          and "voy = ((SCREEN_ROWS - vrows) / 2) * 8" in on)
    check("...unconditionally (the GB family needs the full view for the box)",
          on.index("video.set_view(vox, voy)") > on.index("function load_room")
          and 'platform == "sms"' not in on[on.index("var vw: u16"):
                                            on.index("core.set_view(vcols, vrows)")])
    check("on: the margin clear runs to the background's height (the top margin "
          "shows the rows that wrap)",
          "text.clear_area(0, ch8, SCREEN_COLS, bkr - ch8)" in on
          and "bkr = 28" in on)
    check("off: no view call and the screen-height clear (byte-identical)",
          "video.set_view(" not in off and "core.set_view(" not in off
          and "text.clear_area(0, ch8, SCREEN_COLS, SCREEN_ROWS - ch8)" in off
          and "bkr" not in off)


def test_define():
    print("[the build's VM_NO_VIEW]")
    import mosaik8_build as B

    def proj(name):
        d = os.path.join(ROOT, "projects", name, "src")
        srcs = [(f, io.open(f, encoding="utf-8").read())
                for f in sorted(glob.glob(os.path.join(d, "*.mos")))]
        return B._vm_dispatch_defines(srcs)
    check("a letterboxed project states VM_NO_VIEW false",
          proj("vm-shardlings").get("VM_NO_VIEW") is False)
    check("any other project states it true (the screen-anchored arms)",
          proj("vm-uiscroll").get("VM_NO_VIEW") is True)
    core = io.open(os.path.join(ROOT, "lib", "vm", "core.mos"), encoding="utf-8").read()
    i = core.index("bank(0) function box_row() -> u8 {")
    body = core[i:core.index("}\n    bank(0) function box_top()", i)]
    check("box_row keeps SCREEN_ROWS in the VM_NO_VIEW arm, view_rows in the other",
          re.search(r"if VM_NO_VIEW \{\s*return SCREEN_ROWS - box_h\s*\} else \{"
                    r"\s*return view_rows - box_h", body) is not None)


def _function(src, header):
    """The text of ONE function (a whole-file substring can match a copy)."""
    i = src.index(header)
    j = src.find("\n    function ", i + 1)
    k = src.find("\n    hot function ", i + 1)
    ends = [e for e in (j, k) if e != -1]
    return src[i:min(ends) if ends else len(src)]


def test_shake_settle():
    print("[the shake's settle frame re-writes the clean scroll]")
    p = io.open(os.path.join(ROOT, "lib", "vm", "player.mos"), encoding="utf-8").read()
    body = _function(p, "hot function cam_apply()")
    check("the last shaken frame raises the settle flag",
          re.search(r"if shk_frames == 0 \{[^}]*shk_settle = 1", body) is not None)
    check("...and the non-wide re-issue honours it (zeroed offset or not)",
          "if shk_ox != 0 or shk_oy != 0 or shk_settle == 1 {" in body)


def main():
    test_prelude()
    test_rooms()
    test_define()
    test_shake_settle()
    print("\n%d failure(s)" % len(FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
