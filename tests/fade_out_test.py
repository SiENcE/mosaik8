#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""The scene-transition FADE, both halves (gbs-fidelity-plan K5).

Two independent defects, fixed together because they are the same transition:

  * the fade-OUT was missing entirely -- `load_room` blacked out with
    `fx.set_level(3)`, painted, and ramped back, so the room being LEFT was cut
    to black in one frame and only the arrival was animated. The ramp now runs
    at vm.core's ONE pending-exception service point, before g_change.
  * on the GAME BOY COLOR there was no fade in EITHER direction, because
    `vm.fx` darkens BGP/OBP0/OBP1 and **the hardware ignores those three
    registers in CGB mode**. The colour half scales the loaded palettes
    instead (the reference engine's fade_manager.c), through a `graphics.palette` verb
    that vm.fx drives via a seam.

Everything here is opt-in: no `studio.toml [scenes] fade` means no fade code at
all, and a program that never calls `palette.fade` keeps the plain palette
write path.
"""

from mosaik import MosaikCompiler
from mosaik_vm.rooms import emit_rooms_mos

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ok = True


def check(cond, label):
    global ok
    ok = ok and bool(cond)
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))


def gen(platform, src):
    return MosaikCompiler().compile_program([('m.mos', src)], platform=platform)


# A minimal world for emit_rooms_mos, mirroring the shape sprite_residency_test
# uses (only the keys these assertions read).
BASE = {"types": ["topdown"], "scene_count": 2, "uniform": True,
        "map_w": 20, "map_h": 18, "has_objs": False, "has_ent": False,
        "has_trig": False, "has_doors": False}

PAL_PROG = '''
module "main" {
    import "platform.video"
    import "graphics.palette"
    const PAL: array[u16, 8] = [0,1,2,3,4,5,6,7]
    function main() {
        palette.load_bkg_set(0, 2, PAL, 0)
        palette.load_sprite_set(0, 2, PAL, 0)
%s
    }
}
'''
WITH_FADE = PAL_PROG % "        palette.fade(3)"
NO_FADE = PAL_PROG % "        video.wait_vblank()"

# The SMS/GG shape: their one CRAM writer is palette.load_native, out of a
# staging buffer whose width is the console's own palette_color_t.
NATIVE_PROG = '''
module "main" {
    import "platform.video"
    import "graphics.palette"
    var BUF: array[u8, 32]
    function main() {
        palette.load_native(0, 2, BUF)
%s
    }
}
'''
NATIVE_FADE = NATIVE_PROG % "        palette.fade(3)"
NATIVE_NO_FADE = NATIVE_PROG % "        video.wait_vblank()"


def test_core_ramps_the_outgoing_room_out():
    """vm.core fades the room being LEFT at the exception service point."""
    print("\n[vm.core: the fade-OUT half]")
    core = open(os.path.join(ROOT, "lib", "vm", "core.mos"),
                encoding="utf-8").read()
    check("function set_fade(hold: u8)" in core and "var fade_hold: u8" in core,
          "the hold is a registered seam (BSS, so it costs no initializer)")
    check("set_fade" in core.split("export ")[-1],
          "... and set_fade is exported")
    check("local function fade_out()" in core,
          "fade_out() exists")
    # It must sit at the ONE service point, inside the CHANGE_SCENE arm, and
    # the historical hard cut must survive as the `else` (fade_hold 0).
    arm = core[core.index("EX_CHANGE_SCENE or code == EX_LOAD_COMPLETE"):]
    arm = arm[:arm.index("reset_scene_ui()")]
    check("if fade_hold > 0 {" in arm and "fade_out()" in arm,
          "the ramp runs in the CHANGE_SCENE arm, before g_change")
    check("fx.set_level(0)" in arm,
          "... and the unregistered path keeps the historical reveal")
    check(arm.index("fade_out()") < arm.index("fx.set_level(0)"),
          "fade_out is the `then`, the hard cut the `else`")
    # GB-family only, for the reason rooms.py's FADE_GUARD documents: a held
    # frame is a wait_vblank, which on the Lynx is a full present.
    body = core[core.index("local function fade_out()"):]
    body = body[:body.index("\n    }\n")]
    check('if platform == "gameboy"' in body and 'platform == "lynx"' not in body,
          "the ramp itself is GB-family guarded (a Lynx present here blanks it)")
    check("video.wait_vblank()" in body and "fx.set_level(fl)" in body,
          "it holds fade_hold frames per darkness step")


def test_fx_gained_a_colour_seam():
    """vm.fx keeps its DMG register ramp and gains a colour drawer."""
    print("\n[vm.fx: the seam]")
    fx = open(os.path.join(ROOT, "lib", "vm", "fx.mos"),
              encoding="utf-8").read()
    # The ramp used to be a const table [0xE4, 0xF9, 0xFE, 0xFF]. It is
    # DERIVED now (the reference engine's DMGFadeToBlackStep: each 2-bit field += the
    # level, clamped at 3) because the base is authorable - a reference-engine
    # project's OBP0/OBP1 are not the hardware identity, and the fade's
    # level-0 write is what used to put the identity back. Over the identity
    # base it is the same four values, which box_and_pan_clock_test pins
    # arithmetically.
    check("local function ramp" in fx and "const DMG_ID: u8 = 0xE4" in fx,
          "the DMG register ramp is derived from an authorable base")
    check("hw.write(REG_OBP0, ramp(o0, l))" in fx
          and "hw.write(REG_OBP1, ramp(o1, l))" in fx,
          "... and all three registers ramp from their own base")
    check("function set_pal(cb: function(u8))" in fx and "set_pal" in fx.split("export ")[-1],
          "set_pal registers the colour drawer")
    check("if has_pal == 1 {" in fx and "g_pal(l)" in fx,
          "set_level drives it when registered (and links nothing when not)")
    # vm.fx is linked into EVERY VM8 game, so it must NOT import the palette
    # module: that would put the resident palette prelude in every console's
    # image. The wrapper is passed IN instead.
    check('import "graphics.palette"' not in fx,
          "vm.fx does NOT import graphics.palette (it is linked everywhere)")
    for plat in ("gameboy", "gameboy_color", "lynx", "sms", "nes"):
        try:
            gen(plat, fx)
        except Exception as exc:                       # pragma: no cover
            check(False, "vm.fx compiles on %s (%s)" % (plat, exc))
            break
    else:
        check(True, "vm.fx compiles on every console class")


def test_rooms_wires_both_halves():
    print("\n[rooms.mos wiring]")
    off = emit_rooms_mos(dict(BASE))
    check("core.set_fade" not in off and "pal_fade" not in off,
          "no [scenes] fade: neither half is emitted (byte-identical)")

    on = emit_rooms_mos(dict(BASE, fade=2))
    check("core.set_fade(FADE_HOLD)" in on,
          "fade: the OUT half is registered with the SAME hold as the ramp in")
    check("pal_fade" not in on and 'import "graphics.palette"' not in on,
          "... but a COLOURLESS world wires no palette fade (nothing to scale)")

    col = emit_rooms_mos(dict(BASE, fade=2, colour=True))
    check('import "graphics.palette"' in col and "palette.fade(l)" in col,
          "colour + fade: the wrapper is emitted")
    guard = ('if platform == "gameboy_color" or platform == "analogue_pocket" '
             'or platform == "sms" or platform == "gamegear" {')
    check(col.count(guard) == 2,
          "... forked to the consoles that scale PALETTES rather than a "
          "palette register, at the definition and the registration "
          "(unforked it cost the consoles that do neither ~225 B)")
    check("fx.set_pal(pal_fade)" in col
          and col.index("fx.set_pal(pal_fade)") < col.index("load_room(start_room"),
          "registered BEFORE the first load_room, so the boot room fades too")


def test_cells_outside_a_small_scene_are_cleared():
    """A room smaller than the console's SCREEN must not show the last one.

    The Game Boy never hits this (its 20x18 screen is the smallest a scene can
    be), which is exactly why it went unnoticed: the SMS shows 32x24, so an
    imported 20x18 room left 12 columns and 6 rows of the previous picture on
    screen. Measured on the SMS/GG sample conversion: the band under the parallax room
    went from 3 distinct colours to 1."""
    print("\n[cells outside the scene]")
    small = emit_rooms_mos(dict(BASE, clear_outside=True))
    check('import "graphics.text"' in small,
          "the clear pulls graphics.text in (that is where a guaranteed-blank "
          "tile lives - the font's SPACE glyph)")
    check("text.clear_area(cw8, 0, SCREEN_COLS - cw8, SCREEN_ROWS)" in small
          and "text.clear_area(0, ch8, SCREEN_COLS, SCREEN_ROWS - ch8)" in small,
          "both axes, against the per-console SCREEN_COLS/ROWS at RUNTIME "
          "(so one target-neutral rooms.mos is right everywhere)")
    check("if cw < SCREEN_COLS {" in small and "if ch < SCREEN_ROWS {" in small,
          "... and only when the scene really is smaller")
    check("core.font_preload()" in small,
          "the resident-font path preloads first: the clear is a TEXT call, "
          "and the lazy font_init would otherwise land over the tileset "
          "load_room just uploaded")
    check('platform == "lynx"' not in small.split("clear_area")[0].split(
              "-- CELLS")[-1],
          "the Lynx is excluded (clear_area is a TGI pixel op there, which "
          "would fight the strip engine)")
    # The PC Engine's conio clear IS a BAT write (and its background engine
    # copies a background-space clear into the scroll replicas), so a world
    # that targets it gets it -- and only such a world, so every other
    # project's rooms.mos stays byte-identical.
    pce = emit_rooms_mos(dict(BASE, clear_outside=True, targets_pce=True))
    check('or platform == "nes" or platform == "pce" {' in pce,
          "a PCE-targeting world clears outside a small scene on the PCE too")
    check('platform == "pce"' not in small,
          "... and a world that never targets the PCE carries no PCE clause")
    # ... and it must run AFTER the paint, or the paint puts the stale cells
    # straight back.
    check(small.index("text.clear_area") > small.index("scenes.paint(rm)"),
          "the clear runs after the paint")
    glyph = emit_rooms_mos(dict(BASE, clear_outside=True, glyph_text=True))
    check("core.font_preload()" not in glyph,
          "glyph-buffer mode keeps no font in VRAM, so it needs no preload")
    off = emit_rooms_mos(dict(BASE))
    check("clear_area" not in off,
          "a world whose scenes fill every target's screen emits nothing")


def test_palette_fade_is_real_on_the_cgb():
    print("\n[palette.fade: the colour half]")
    gbc = gen("gameboy_color", WITH_FADE)
    check("void gbs_pal_fade(uint8_t level)" in gbc, "gameboy_color: emitted")
    check("static palette_color_t gbs_pal_sh[64];" in gbc,
          "... with the RAM shadow of what the program asked for")
    check("gbs_pal_hw(spr, slot, count, gbs_pal_buf);" in gbc,
          "the SET loader writes through the fade")
    check("gbs_pal_msk" in gbc,
          "only slots the program actually loaded are re-applied "
          "(an all-zero shadow entry is BLACK)")
    # The shadow is what makes a room load survive the black-out: load_room
    # blacks out, load_palettes writes the NEW room's colours, then the ramp
    # runs. Writing THROUGH the level is what keeps the load from undoing it.
    hw = gbc[gbc.index("static void gbs_pal_hw"):]
    hw = hw[:hw.index("\n}\n")]
    check("gbs_pal_sh[base + i] = buf[i];" in hw and "if (gbs_fade_lvl)" in hw,
          "gbs_pal_hw records first, then dims -- so the ramp back is exact")

    ap = gen("analogue_pocket", WITH_FADE)
    check("void gbs_pal_fade(uint8_t level)" in ap
          and "static palette_color_t gbs_pal_sh[64];" in ap,
          "analogue_pocket: the same machinery (it is CGB class)")
    check("gbs_pal_shade(buf[0])" in ap,
          "... and its DMG-register mirror reads the DIMMED buffer, so a "
          "palette loaded mid-fade cannot un-black a DMG-mode core")

    for plat in ("gameboy", "nes"):
        c = gen(plat, WITH_FADE)
        check("void gbs_pal_fade(uint8_t level) { (void)level; }" in c
              and "gbs_pal_sh[" not in c,
              "%s: an honest no-op (the DMG family fades through its palette "
              "REGISTERS instead)" % plat)
    c = gen("lynx", WITH_FADE)
    check("void gbs_pal_fade(uint8_t level) { (void)level; }" in c,
          "lynx: an honest no-op (the Lynx fades through native.lynx)")
    # The PC Engine's fade is REAL since 2026-10-03 (record
    # pce-rendering-parity.md): every VCE write goes through a shadow and the
    # fade rewrites it scaled; its brightness per level is measured on a ROM in
    # pce_bat_and_fan_test.test_pce_fade.
    c = gen("pcengine", WITH_FADE)
    check("void gbs_pal_fade(uint8_t level) {" in c and "(void)level" not in c
          and "static uint16_t gbs_vsh[160];" in c and "gbs_vce_dim(color)" in c,
          "pcengine: palette.fade scales the VCE through its shadow")
    c = gen("pcengine", NO_FADE)
    check("gbs_vsh" not in c and "gbs_pal_fade" not in c,
          "pcengine: a program that never fades keeps the plain VCE write")


def test_palette_fade_is_real_on_the_z80_pair():
    """SMS / Game Gear fade by scaling CRAM.

    They have no palette REGISTER either, so before this their transitions
    were hard cuts for the same reason the CGB's were - and they are the
    consoles the reference-engine conversion's second sample targets."""
    print("\n[palette.fade: SMS / Game Gear]")
    for plat, width, mask in (("sms", 2, 3), ("gamegear", 4, 15)):
        c = gen(plat, NATIVE_FADE)
        check("static palette_color_t gbs_pal_sh[32];" in c,
              "%s: a 32-entry CRAM shadow at the console's OWN "
              "palette_color_t width (a uint16_t here would misread the SMS "
              "staging buffer - the trap the colour work documents)" % plat)
        check("static void gbs_cram(uint8_t idx, palette_color_t c) {" in c,
              "%s: ONE seam, because every CRAM write here is a single entry"
              % plat)
        check("for (k = 0; k < %d; k += %d)" % (width * 3, width) in c
              and "& %d) * n / 3" % mask in c,
              "%s: scales %d-bit channels" % (plat, width))
        check("void gbs_pal_native(uint8_t first, uint8_t count," in c
              and "const palette_color_t *data) {" in c,
              "%s: load_native becomes a real helper - it is the ONE CRAM "
              "writer a generated coloured room uses" % plat)
        check("gbs_pal_msk[i >> 3] & (uint8_t)(1 << (i & 7))" in c,
              "%s: entries the program never wrote are left alone" % plat)
    for plat in ("sms", "gamegear"):
        c = gen(plat, NATIVE_NO_FADE)
        check("gbs_pal_native" not in c and "set_palette(0, 2," in c,
              "%s: no fade -> load_native still lowers STRAIGHT onto the "
              "port's set_palette (byte-identical)" % plat)


def test_unused_is_byte_identical():
    """A program that never fades keeps the plain palette write path."""
    print("\n[byte-identical off]")
    for plat in ("gameboy_color", "analogue_pocket", "gameboy", "sms", "nes",
                 "lynx", "pcengine"):
        c = gen(plat, NO_FADE)
        # (the shadow by its FULL declaration: the Analogue Pocket's own
        # `gbs_pal_shade` shares the prefix)
        check("gbs_pal_fade" not in c and "gbs_pal_sh[" not in c,
              "%s: no fade verb, no shadow, no scale on the write path" % plat)
    # ... and specifically that the CGB set loader is untouched.
    c = gen("gameboy_color", NO_FADE)
    check("if (spr) set_sprite_palette(slot, count, gbs_pal_buf);" in c,
          "gameboy_color: the SET loader still writes the hardware directly")


def test_the_scale_is_exact():
    """The emitted x/3 table reproduces the levels measured on the ROM.

    Read off the reference-engine sample conversion under PyBoy (CGB background palette 0,
    entry 0 = r25 g29 b31): the fade passes through 16/19/20 then 8/9/10 then
    black. That is x*2/3 and x/3 per 5-bit channel, so the table is the
    contract and not an implementation detail -- getting it wrong tints the
    fade instead of darkening it."""
    print("\n[the scale]")
    gbc = gen("gameboy_color", WITH_FADE)
    tbl = gbc[gbc.index("static const uint8_t gbs_pal_t3[64]"):]
    tbl = tbl[tbl.index("{") + 1:tbl.index("};")]
    t3 = [int(v) for v in tbl.replace("\n", "").split(",") if v.strip()]
    check(len(t3) == 64 and all(t3[i] == i // 3 for i in range(64)),
          "the table is x/3 over 0..63")
    # level 1 indexes x << 1 (n = 2), level 2 indexes x (n = 1).
    for chan, two_thirds, one_third in ((25, 16, 8), (29, 19, 9), (31, 20, 10)):
        check(t3[chan << 1] == two_thirds and t3[chan] == one_third,
              "channel %d -> %d (level 1) -> %d (level 2) -> 0, as measured"
              % (chan, two_thirds, one_third))


if __name__ == "__main__":
    print("=" * 60)
    print("scene-transition fade: the outgoing ramp + the CGB colour half")
    print("=" * 60)
    test_core_ramps_the_outgoing_room_out()
    test_fx_gained_a_colour_seam()
    test_rooms_wires_both_halves()
    test_cells_outside_a_small_scene_are_cleared()
    test_palette_fade_is_real_on_the_cgb()
    test_palette_fade_is_real_on_the_z80_pair()
    test_unused_is_byte_identical()
    test_the_scale_is_exact()
    print("\n%s" % ("All checks passed" if ok else "FAILURES"))
    sys.exit(0 if ok else 1)
