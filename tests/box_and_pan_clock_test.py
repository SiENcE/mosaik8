#!/usr/bin/env python3
"""The reference-engine parity fixes reported from playing the platformer conversion
(2026-08-22),
all of them about a CLOCK or a RECTANGLE the engine got from the wrong place:

  * **`core.set_box_min(rows)`** - the reference engine sizes a dialogue box
    `max(minHeight, min(maxHeight, min(lines, textHeight) + textY +
    showFrame))` (`shared/lib/helpers/dialogue.ts`) and BOTTOM-anchors it, so
    its `minHeight` - default 4, and 5 on every box of that conversion - is what puts the
    first line on a row. Ours was `lines + 2` with no floor at all, which drew
    a two-line box four rows tall where the reference draws five, and so its
    text one row LOW. Measured on both ROMs as the window's WY: 112 against
    the reference's 104, and 104 after.

  * **`player.set_pan_lcd(1)`** - a scripted camera pan re-enters once per VM
    FRAME, which is 1..3 display frames depending on the room, so `step` px
    per call runs at a different speed in every scene. The reference engine moves the
    camera a fixed number of subpixels per LCD frame. Display-paced (the
    overlay-curtain / typewriter clock rule) with `step` in QUARTER-pixels,
    Its opening pan measures 0.500 px per LCD frame against the reference
    ROM's 0.500, where before it ran at 1.562.

  * **sprites do not draw over the overlay CURTAIN** - OBJ is above the WINDOW
    on this hardware, so a curtain covering the screen still showed every
    actor through it: the conversion's "Once upon a time" narration revealed its
    "Press 'A'" prompt (an actor at tile row 15) from the first frame. GB
    Studio's `simple_LCD_isr` runs HIDE_SPRITES from the window's first
    scanline whenever the window is full width and `show_actors_on_overlay`
    is false; `vm.fx`'s `put()` arms exactly that, on every step because the
    curtain's whole job is to move WY.

Each is OPT-IN and folds away for a project that does not use it.
"""

import os
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


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_box_min():
    print("\n[core.set_box_min: the reference engine's minHeight]")
    core = _read("lib", "vm", "core.mos")
    check("function set_box_min" in core, "the seam exists")
    check("set_box_min" in core.split("export ")[-1],
          "...and is exported, so the generated rooms can call it")
    body = core.split("local function size_box")[1][:700]
    check("box_h < box_min" in body,
          "size_box floors the box at it")
    check("var box_min: u8" in core and "= 4" not in
          core.split("var box_min: u8")[1][:40],
          "the default is a plain BSS 0 = no floor, which is the historical "
          "`lines + 2` (and costs no bank-0 initializer)")


def test_pan_clock():
    print("\n[player.set_pan_lcd: the pan runs on the DISPLAY clock]")
    player = _read("lib", "vm", "player.mos")
    check("function set_pan_lcd" in player, "the seam exists")
    check("export " in player and "set_pan_lcd," in player,
          "...and is exported")
    step = player.split("function cam_pan_step")[1][:600]
    check("pan_lcd == 1" in step,
          "cam_pan_step forks on it, so an unset project keeps px per call")
    px = player.split("local function pan_pixels")[1][:800]
    check("system.frames()" in px, "the rate is paced on elapsed display frames")
    check("el = 8" in px,
          "...capped like the curtain and the music catch-up, so a room load "
          "cannot fast-forward the pan")
    check("pan_acc = q % 4" in px and "return q / 4" in px,
          "QUARTER-pixels with the remainder KEPT, so the reference engine's 1/4 and 1/2 "
          "speeds really take 4 and 2 frames per pixel")
    hold = player.split("function cam_hold")[1][:400]
    check("pan_last = system.frames()" in hold,
          "the clock is seeded when the pan STARTS, not at boot")


def test_curtain_sprite_cut():
    print("\n[the curtain cuts sprites, as the reference engine's simple_LCD_isr does]")
    fx = _read("lib", "vm", "fx.mos")
    check('import "graphics.text"' in fx, "vm.fx can reach the cut verb")
    put = fx.split("local function put")[1][:4000]
    check("text.win_sprite_cut(1)" in put and "text.win_sprite_cut(0)" in put,
          "put() arms the cut while the curtain is up and gives it back when "
          "it leaves")
    show = put.split("video.show_window()")[1][:2000]
    check("text.win_sprite_cut(1)" in show,
          "...armed AFTER the window has moved, so LYC follows the new WY")


def test_dmg_palette():
    print("\n[vm.fx.set_dmg_palette: the fade ramps from the AUTHORED base]")
    fx = _read("lib", "vm", "fx.mos")
    check("function set_dmg_palette" in fx, "the seam exists")
    check("set_dmg_palette" in fx.split("export ")[-1], "...and is exported")
    check(fx.count("function set_dmg_palette") == 2,
          "...in BOTH platform arms, since an export list cannot be "
          "conditional (a no-op off the GB register model)")
    # The WHOLE function, not a fixed byte window. `ramp` grew a towards-WHITE
    # arm on 2026-09-15 (state 32 `fade_style`) and it is
    # emitted FIRST, which pushed
    # the towards-black spelling past a `[:700]` slice - so this check failed
    # while the rule it pins was still true, twice over. Bounded by the next
    # declaration, which cannot drift with the body's length.
    body = fx.split("local function ramp")[1].split("local function regs")[0]
    check("f > 3" in body and "f = 3" in body,
          "the ramp is the reference engine's DMGFadeToBlackStep - each 2-bit field += "
          "the level, clamped at 3")
    regs = fx.split("local function regs")[1][:700]
    check("has_dmg == 1" in regs and "DMG_ID" in regs,
          "regs() takes the authored base when one was set and the hardware "
          "identity otherwise")
    setter = fx.split("function set_dmg_palette")[1][:400]
    check("regs(0)" in setter,
          "the setter APPLIES at once - a game that never fades would "
          "otherwise never reach regs() at all")
    # The historical table, reproduced by the identity base. This is the
    # byte-identical-off contract, stated as arithmetic rather than trusted.
    def ramp(p, l):
        out = 0
        for i in range(4):
            f = min(3, ((p >> (2 * i)) & 3) + l)
            out |= f << (2 * i)
        return out
    check([ramp(0xE4, l) for l in range(4)] == [0xE4, 0xF9, 0xFE, 0xFF],
          "...and over the identity it reproduces the const table this "
          "replaced, level for level")
    gen = _read("mosaik_vm", "rooms", "generate.py")
    check('info["dmg_palette"]' in gen, "generate.py reads [dmg]")
    check("[0xE4] * 3" in gen,
          "...and emits NOTHING for the hardware identity (byte-identical off)")
    start = _read("mosaik_vm", "rooms", "emit_start.py")
    check("fx.set_dmg_palette(0x%02X, 0x%02X, 0x%02X)" in start,
          "emit_start calls it")
    pre = _read("mosaik_vm", "rooms", "emit_prelude.py")
    check('info.get("dmg_palette")' in pre,
          "...and the vm.fx import follows the same condition, so the call "
          "and its module cannot disagree")


def test_camera_bounds():
    print("\n[Stage C: the camera's CLAMP RECTANGLE]")
    import mosaik_vm
    from mosaik_vm import isa
    for name, sid in (("camera_min_x", 25), ("camera_max_x", 26),
                      ("camera_min_y", 27), ("camera_max_y", 28)):
        check(isa.STATES.get(name) == sid, "%s is state %d" % (name, sid))
    core = _read("lib", "vm", "core.mos")
    check("const ST_CAM_MIN_X = 25" in core and "const ST_CAM_MAX_Y = 28" in core,
          "core.mos's consts match the ISA ids")
    arm = core.split("local function set_state")[1][:2200]
    check("if VM_ST_CAMERA_MIN_X {" in arm,
          "the arm is GUARDED - set_state is bank(0) resident, so four "
          "unguarded else-ifs would be paid by every project")
    check("player.set_cam_bound(sid - ST_CAM_MIN_X" in arm,
          "...and the four CONSECUTIVE ids collapse to one range test")
    player = _read("lib", "vm", "player.mos")
    check("function set_cam_bound" in player and "set_cam_bound," in player,
          "vm.player exports the setter")
    check("var cam_bset: u8" in player,
          "the armed flag is BSS, so an unauthored room keeps the old clamp")
    seed = player.split("function set_cam_bound")[1][:400]
    check("cam_bx1 = room_maxx()" in seed and "cam_by1 = room_maxy()" in seed,
          "the four SEED from the room's own bounds on the first write (the "
          "states are independent and a script may set only one)")
    # To the END of the function, not a fixed character count: the window used
    # to be [:1600] and W7b's follow-options arm pushed the clamp past it, so a
    # green check went red on a change that moved nothing.
    wide = player.split("local function calc_wide_cam")[1]
    wide = wide.split(chr(10) + "    -- ")[0]
    check("clamp_bound(cx16" in wide and "clamp_bound(camy" in wide,
          "the WIDE camera clamps into the rectangle")
    clampf = player.split("local function clamp_bound")[1][:600]
    check("if l > h" in clampf and "if o < l" in clampf,
          "MIN is applied LAST, so a rectangle whose min equals its max PINS "
          "the axis - which is the whole of the platformer conversion's first "
          "tutorial room")
    # The WHOLE function, not a fixed character window: a new per-room reset
    # added above this one (the blank state was) pushes the line out of a
    # magic slice and fails a check about code that never changed.
    clear = player.split("local function clear_wide")[1]
    clear = clear[:clear.index("\n    }")]
    check("cam_bset = 0" in clear,
          "a room load clears it (the clamp is per ROOM, like the shake)")
    # the lowering, end to end
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "camera_bounds", "x0": 16, "x1": 1032,
             "y0": 32, "y1": 32},
            {"event": "stop"}]}])
    names = [n for _o, n, _s, _r in isa.iter_instructions(prog.code)]
    check(names.count("SET_STATE") == 4,
          "the event lowers to FOUR state writes and no new opcode")


def test_shake_opts():
    print("\n[6.4 stage B: the shake's axis, wait, range and clock]")
    from mosaik_vm import isa
    check(isa.OPS["SHAKE_OPTS"] == (0x4C, ["u8", "u8"]),
          "SHAKE_OPTS is 0x4C (axis, wait)")
    check(isa.OPS["SHAKE"] == (0x39, ["u8", "u8"]),
          "...and SHAKE keeps its encoding, so an unlatched program is "
          "byte-identical")
    core = _read("lib", "vm", "core.mos")
    check("const OP_SHAKE_OPTS = 0x4C" in core, "core.mos's const matches")
    arm = core.split("case OP_SHAKE {")[1][:1400]
    check("player.shake_waits() == 1" in arm and "pcr -= 3" in arm,
          "SHAKE blocks under the latch, rewinding opcode + two operands")
    check("vm_waiting[c] == 0" in arm,
          "...and vm_waiting keeps the re-entry from restarting the shake it "
          "is waiting on")
    player = _read("lib", "vm", "player.mos")
    roll = player.split("function cam_apply")[1][:3000]
    check("system.frames()" in roll and "el > 8" in roll,
          "the roll counts DISPLAY frames, capped like the music catch-up "
          "(the reference engine measures against sys_time)")
    check("var span: u16 = shk_amp * 2" in roll and "* 2 + 1" not in roll,
          "the jitter is the reference engine's [-amp, amp-1], not the symmetric range")
    # Spelled `(mask) != 0`, not a bare mask: an `if` condition is BOOLEAN in
    # this language and the typechecker says so out loud otherwise.
    check("(shk_axis & 1) != 0" in roll
          and "shk_axis == 0 or (shk_axis & 2) != 0" in roll,
          "the AXIS mask is honoured, and unlatched (0) it stays vertical - "
          "this engine's historical shake")
    check("shk_axis = 0" in roll and "shk_wait = 0" in roll,
          "the latch is ONE shot, cleared when the shake settles")


def test_generator_wiring():
    print("\n[the generated rooms decide both halves together]")
    gen = _read("mosaik_vm", "rooms", "generate.py")
    check('info["box_min_rows"]' in gen and '"box_min_rows"' in gen,
          "generate.py reads [scenes] box_min_rows")
    check('info["camera_pan_lcd"]' in gen,
          "...and [scenes] camera_pan_lcd")
    start = _read("mosaik_vm", "rooms", "emit_start.py")
    check("core.set_box_min(%d)" in start,
          "emit_start calls the box seam")
    check("player.set_pan_lcd(1)" in start,
          "...and the pan seam")


def test_byte_identical_off():
    print("\n[byte-identical off]")
    from mosaik_vm.rooms import generate as g
    src = _read("mosaik_vm", "rooms", "emit_start.py")
    # Both are guarded by a falsy default, so a world.toml/studio.toml that
    # says nothing emits neither call - the whole opt-in contract.
    for key, call in (("box_min_rows", "core.set_box_min"),
                      ("camera_pan_lcd", "player.set_pan_lcd")):
        blk = src.split('info.get("%s")' % key)[1][:600]
        check(call in blk,
              "%s is emitted only under info[%r]" % (call, key))
    check(hasattr(g, "generate_rooms"),
          "generate_rooms is importable (the generator still loads)")


def main():
    print("== the box floor, the pan clock and the curtain's sprite cut ==")
    test_box_min()
    test_pan_clock()
    test_curtain_sprite_cut()
    test_dmg_palette()
    test_camera_bounds()
    test_shake_opts()
    test_generator_wiring()
    test_byte_identical_off()
    print()
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
