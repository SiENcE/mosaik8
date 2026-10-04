#!/usr/bin/env python3
"""THE OVERLAY SCANLINE CUTOFF (W7d phase 2, the reference engine's `overlay_cut_scanline`).

At the cut line the WINDOW layer goes off and the sprites come back, so an
overlay - vm.fx's curtain, a full-width box - covers only the TOP of the screen
with the room playing below it. It is the THIRD tenant of `LYC_REG`, after the
scanline parallax bands and the dialogue box's sprite cut, and everything here
exists because a third tenant is where an arbiter either holds or stops being
one.

Pinned here:

  * the verb compiles on every console - real on the GB family, an honest
    no-op on SMS/GG/NES (no GB-style window layer) and on the cc65 consoles
    (no LYC either) - and a program that never calls it is BYTE-IDENTICAL;
  * the TENANT COUNT. Two tenants get the merged stop list; the overlay cut
    ALONE is a sole tenant like the other two and keeps its own emitter, so it
    never pays for arbitration it cannot use;
  * the MERGE. With the cut in the program the non-band stops become a little
    sorted list, two on one line fold into one entry, and the ISR tests WINOFF
    after HIDE so a folded line still ends with the sprites given back;
  * a two-tenant program WITHOUT the overlay cut keeps the merge's original
    text, character for character (the `pend` spelling);
  * `gbs_win_want` - the window's own `gbs_spr_want`: every place that shows or
    hides the layer records the program's wish, because the V-blank restore may
    only give back what was asked for;
  * the state/verb LOCKSTEP (`overlay_cut` is state 41, `ST_OVERLAY_CUT` in
    core.mos, and RefVM holds it), and the `[scenes] overlay_cut` project
    default the generated rooms.mos applies at boot.

The end-to-end proof is a ROM: `projects/lyc-merge-lab/verify.py` phase [D],
which arms the cut inside an open box and reads off a rendered frame that the
box's paper stops there, the room draws below it and the sprite comes back -
on the flat cart AND on a banked copy.
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

import mosaik_vm
from mosaik import MosaikCompiler
from mosaik_vm.rooms import emit_rooms_mos

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _code(src):
    """`src` with its C comments removed.

    A test that greps generated C for a register name otherwise matches the
    COMMENT explaining why the register is not written there, which is the
    opposite of what it means to assert.
    """
    return re.sub(r"/\*.*?\*/", "", src, flags=re.S)


def _rebuild(src):
    """Just the body of `gbs_lyc_rebuild`, comments stripped.

    Split on the DEFINITION and stop at its closing brace: the rest of the
    generated file names every one of these symbols again, so an unbounded
    split makes a "not in" assertion pass for the wrong reason.
    """
    return _code(src).split("gbs_lyc_rebuild(void)")[1].split("\n}")[0]


def _c(body, platform="gameboy", imports=(), **kw):
    src = ('module "app" {\n'
           + "".join('    import "%s"\n' % i for i in imports)
           + "    function main() {\n" + body + "\n        loop { video.wait_vblank() }\n"
           + "    }\n    export main\n}\n")
    compiler = MosaikCompiler()
    out = compiler.compile_program([("app.mos", src)], platform=platform, **kw)
    assert not out.startswith("Compilation error"), out
    return out, compiler.code_generator


_IMPORTS = ("platform.video", "graphics.bkg", "graphics.text", "graphics.window")

#: The three tenants, each on its own line, so a body can drop any of them.
_BANDS = ("        bkg.parallax_band(0, 79)\n"
          "        bkg.parallax_band(1, 0)\n"
          "        bkg.parallax(2)\n"
          "        bkg.parallax_scx(0, 4)")
_BOX = ("        text.to_window(12, 6)\n"
        "        text.win_sprite_cut(1)\n"
        "        text.win_sprite_cut(0)\n"
        "        text.to_bkg()")
_CUT = "        text.win_overlay_cut(100)"


def test_byte_identical_unused():
    """A program that never asks for the cut carries NONE of it.

    The whole feature - the action bit, the two state bytes, the window's own
    wish flag and the extra arm in both interrupt halves - is gated on the
    CALL, so this is the byte-identical norm rather than a saving.
    """
    print("\n[a non-user carries none of it]")
    for name, body in (("bands + box", _BANDS + "\n" + _BOX),
                       ("box only", _BOX),
                       ("bands only", _BANDS),
                       ("neither", "        video.show_bkg()")):
        c, _g = _c(body, imports=_IMPORTS)
        check("ocut" not in c and "WINOFF" not in c and "gbs_win_want" not in c,
              "%s: no overlay-cut text at all" % name)
    # ...and the two-tenant merge keeps the text that shipped with phase 1.
    c, _g = _c(_BANDS + "\n" + _BOX, imports=_IMPORTS)
    check("if (pend && gbs_cut_line <= ln)" in c and "ex_line" not in c,
          "the phase-1 merge is character for character what it was")
    check("void gbs_show_win(void) { SHOW_WIN; }" in c,
          "...and the window verbs keep no wish flag")


def test_sole_tenant_keeps_its_own_emitter():
    """The overlay cut ALONE has nothing to arbitrate.

    One tenant's stop list IS its own chain, and the merge machinery is ~145 B
    of resident image that could never merge anything - the measurement that
    shaped phase 1, and it binds the third tenant exactly as it binds the other
    two.
    """
    print("\n[the overlay cut alone is a SOLE tenant]")
    c, _g = _c("        window.move(7, 0)\n"
               "        video.show_window()\n" + _CUT, imports=_IMPORTS)
    check("gbs_lyc_isr" not in c and "gbs_lyc_rebuild" not in c
          and "GBS_LYC_STOPS" not in c,
          "no stop list, no arbiter, no merged handler")
    check("gbs_ocut_lcd_isr" in c and "gbs_ocut_vbl_isr" in c
          and _code(c).count("add_LCD(") == 1,
          "it owns LYC_REG itself, through ONE installed handler")
    cut = _code(c).split("void gbs_text_win_overlay_cut(uint8_t y) {")[1]
    check("LYC_REG = y;" in cut and "STAT_REG |= STATF_LYC;" in cut,
          "...and arms the register directly")
    isr = _code(c).split("void gbs_ocut_lcd_isr(void) NONBANKED {")[1].split("\n}")[0]
    check("if (!gbs_ocut_on) return;" in isr,
          "a disarmed cut stands down: add_LCD cannot be undone")
    check("HIDE_WIN;" in isr and "if (gbs_spr_want) SHOW_SPRITES;" in isr,
          "the window goes away and the sprites come back, to the wish only")
    check("while (STAT_REG & STATF_BUSY) ;" in isr,
          "...in H-blank, or the LCDC write tears the line it lands on")
    # No window ROUTER in this program, so the verb declares the wish itself.
    # Match a DEFINITION, which is a LINE starting `uint8_t` - every `extern`
    # contains that substring too, and counting substrings would pass while
    # the flag was duplicated.
    defs = [ln for ln in c.split("\n") if ln.startswith("uint8_t gbs_win_want;")]
    check(len(defs) == 1 and "extern uint8_t gbs_win_want;" in c,
          "gbs_win_want is ONE definition plus externs")


def test_third_tenant_merges():
    """With a second tenant the cut is a STOP in the one walk."""
    print("\n[the third tenant joins the stop list]")
    c, _g = _c(_BANDS + "\n" + _BOX + "\n" + _CUT, imports=_IMPORTS)
    check("gbs_ocut_lcd_isr" not in c and "gbs_win_lcd_isr" not in c
          and "gbs_px_lcd_isr" not in c and _code(c).count("add_LCD(") == 1,
          "three tenants get ONE emitted LCD handler, installed once")
    check("#define GBS_LYC_STOPS 5" in c,
          "the list is sized for three bands + both cuts")
    check("#define GBS_LYC_WINOFF 0x20" in c,
          "the cut is one more ACTION BIT, not a second mechanism")
    verb = _code(c).split("void gbs_text_win_overlay_cut(uint8_t y) {")[1]
    check("LYC_REG" not in verb and "STAT_REG" not in verb,
          "the verb publishes a stop and rebuilds; it never touches the registers")
    check("gbs_ocut_on = 0;" in verb and "y < SCREEN_HEIGHT" in verb,
          "a line off the bottom of the screen DISARMS it (its default is 150)")
    # The ORDER of the two tests in the ISR is the semantics: a line carrying
    # both bits must end with the sprites back, which is what the reference VM does when
    # WY - 1 reaches overlay_cut_scanline.
    # The ISR is NAKED ASSEMBLY now (the reference's shape, the reference VM's
    # parallax_LCD_isr), so the order is read off the BIT TESTS - bit 4 is
    # GBS_LYC_HIDE, bit 5 is GBS_LYC_WINOFF. NOT the `;` comments beside
    # them: `_code` strips C comments, not assembly ones, so a name sitting
    # in a comment would satisfy this check with no code behind it.
    isr = _code(c).split("void gbs_lyc_isr(void) NONBANKED NAKED {")[1].split("\n}")[0]
    isr = chr(10).join(l.split(";")[0] for l in isr.split(chr(10)))
    check("bit 4, b" in isr and "bit 5, b" in isr
          and isr.index("bit 4, b") < isr.index("bit 5, b"),
          "the ISR tests HIDE before WINOFF, so a folded line ends sprites-on")
    reb = _rebuild(c)
    check("ex_act[0] |= ex_act[1];" in reb,
          "two stops on ONE line MERGE: a second entry would arm LYC behind the beam")
    check("ex_line[1] < ex_line[0]" in reb,
          "...and two entries are SORTED before the bands are walked")
    check("while (ex_i < ex_n && ex_line[ex_i] <= ln) {" in reb,
          "the walk places each of them at the band it reaches")
    vbl = _code(c).split("void gbs_lyc_vbl(void) NONBANKED {")[1].split("\n}")[0]
    check("if (gbs_win_want) SHOW_WIN;" in vbl
          and vbl.index("SHOW_WIN") < vbl.index("if (!gbs_lyc_n) return;"),
          "V-blank gives the layer back BEFORE the empty-list return")


def test_merge_without_every_tenant():
    """Two of the three is still a merge, and it carries only what it needs."""
    print("\n[two of the three]")
    c, _g = _c(_BANDS + "\n" + _CUT, imports=_IMPORTS)
    reb = _rebuild(c)
    check("uint8_t ex_line[1], ex_act[1], ex_n, ex_i;" in reb,
          "bands + cut: the extras list holds ONE, so there is nothing to sort")
    # `ex_line[1]` also spells the ARRAY SIZE in the declaration above, so the
    # claim has to be about the sort's own comparison.
    check("ex_line[1] <" not in reb and "GBS_LYC_HIDE" not in reb,
          "...and no box-cut stop at all")
    check("#define GBS_LYC_STOPS 4" in c, "...four stops")

    c, _g = _c(_BOX + "\n" + _CUT, imports=_IMPORTS)
    reb = _rebuild(c)
    check("for (i = 0; i < gbs_px_n; ++i)" not in reb and "GBS_LYC_BAND" not in c,
          "box + cut: no band loop and no band bit")
    check("ex_line[1] < ex_line[0]" in reb and "#define GBS_LYC_STOPS 2" in c,
          "...but the two DO have to be sorted against each other")


def test_the_window_records_its_wish():
    """`gbs_win_want` is the window's `gbs_spr_want`, and every writer sets it.

    The V-blank half turns the layer back on, so a program that put its own
    window away must not find it back up - the same rule that keeps the sprite
    cut from un-hiding a game's deliberately hidden sprites.
    """
    print("\n[the window's own wish]")
    c, _g = _c(_BOX + "\n" + _CUT, imports=_IMPORTS)
    check("void gbs_show_win(void) { gbs_win_want = 1; SHOW_WIN; }" in c
          and "void gbs_hide_win(void) { gbs_win_want = 0; HIDE_WIN; }" in c,
          "video.show_window / hide_window record it")
    # THE WRITER IS THE REVEAL, NOT to_window (2026-09-19, B2). `to_window` no
    # longer touches LCDC bit 5: it PREPARES the band and `text.win_reveal()`
    # shows it, once the box has been drawn into it. The rule this test states
    # is unchanged and this is what it now means - every writer of the bit
    # records the wish, and `to_window` is no longer one of them. Recording it
    # there as well would be actively WRONG: a box that is drawn but not yet
    # revealed would be turned back on by the next V-blank restore, which is
    # exactly the half-built frame B2 removed.
    router = _code(c).split("void gbs_text_to_window(uint8_t origin_row, "
                            "uint8_t box_rows) {")[1].split("\n}")[0]
    reveal = _code(c).split("void gbs_text_win_reveal(void) {")[1].split("\n}")[0]
    check("gbs_win_want = 1;" in reveal and "SHOW_WIN" in reveal,
          "text.win_reveal records it (it is what shows the layer now)")
    check("gbs_win_want" not in router and "SHOW_WIN" not in router,
          "...and text.to_window does NOT - it no longer shows the layer")
    check("gbs_win_want = 0;" in _code(c).split("void gbs_text_to_bkg(void)")[1][:120],
          "...and text.to_bkg clears it")
    check("uint8_t gbs_win_want = 0;" not in c and "uint8_t gbs_win_want;" in c,
          "it is BSS, not an initialised global (which is resident image)")


def test_banked_arbiter_carries_it():
    """The arbiter banks, and the bank TU is self-contained.

    `_emit_data_bank_units` gives a bank TU only <gbdk/platform.h> and
    <stdint.h>, so everything the rebuild touches has to travel with it - and
    the DEFINITION must not keep its `static`, or sdcc discards it, the linker
    warns and exits 0, and the ROM's every arm path jumps to address 0.
    """
    print("\n[the banked arbiter carries the third tenant]")
    c, g = _c(_BANDS + "\n" + _BOX + "\n" + _CUT, imports=_IMPORTS,
              code_banks=["app"])
    bank = "\n".join(g.bank_units.values())
    check("void gbs_lyc_rebuild(void) BANKED {" in bank
          and "static void gbs_lyc_rebuild" not in bank,
          "the rebuild is banked and is NOT static")
    for sym in ("gbs_ocut_on;", "gbs_ocut_line;"):
        check(("extern uint8_t " + sym) in bank,
              "the bank TU sees the cut's state through extern: %s"
              % sym.rstrip(";"))
        check(any(ln.startswith("uint8_t " + sym) for ln in c.split("\n")),
              "...and its ONE definition stays in the main TU: %s"
              % sym.rstrip(";"))
    check("#define GBS_LYC_WINOFF 0x20" in bank,
          "...and re-states the action bit (it has no view of the other TU)")


def test_honest_no_op_off_the_gb_family():
    """Every other console links it and does nothing, and says so.

    The window layer and `LYC_REG` are GB-family hardware. The SMS, Game Gear
    and NES draw their UI into the name table itself, which no scanline
    interrupt can put away; the Lynx and the PC Engine have neither.
    """
    print("\n[an honest no-op everywhere else]")
    for plat in ("sms", "gamegear", "nes", "lynx", "pce"):
        c, _g = _c(_BOX + "\n" + _CUT, platform=plat, imports=_IMPORTS)
        body = ("void gbs_text_win_overlay_cut(uint8_t y) { (void)y; }")
        check(body in c, "%s: the verb is a no-op with a body" % plat)
        check("LYC_REG" not in _code(c) and "gbs_ocut_on" not in c,
              "%s: and no LYC machinery at all" % plat)


def test_state_lockstep():
    """`overlay_cut` is state 41 in all five places the VM is spelled."""
    print("\n[the five in lockstep]")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    check(mosaik_vm.STATES.get("overlay_cut") == 41,
          "mosaik_vm.STATES: overlay_cut = 41")
    check(mosaik_vm.OVERLAY_CUT_OFF == 150,
          "...and 150 is the OFF value (the reference VM's LYC_SYNC_VALUE)")
    core = open(os.path.join(here, "lib", "vm", "core.mos"),
                encoding="utf-8").read()
    check("const ST_OVERLAY_CUT = 41" in core, "lib/vm/core.mos: ST_OVERLAY_CUT")
    check("if VM_ST_OVERLAY_CUT {" in core,
          "...behind its own fold, so a non-writer keeps nothing")
    arm = core.split("if VM_ST_OVERLAY_CUT {")[1].split("\n        }")[0]
    check("text.win_overlay_cut(" in arm,
          "...and the arm is where the only call lives - which is what keeps "
          "the third LYC tenant out of a program that never writes the state")
    spec = open(os.path.join(here, "docs", "vm8-spec.md"), encoding="utf-8").read()
    check("| 41 | overlay_cut" in spec, "docs/vm8-spec.md carries the row")
    vm = mosaik_vm.RefVM(b"\x00")
    check(vm.overlay_cut == 150, "RefVM boots at 150 (no cut)")
    vm._set_state(41, 96)
    check(vm.overlay_cut == 96, "...and holds what a SET_STATE writes")


#: The smallest world `emit_rooms_mos` accepts (the fade_style test's BASE).
_BASE = {"types": ["topdown"], "scene_count": 1, "uniform": True,
         "map_w": 20, "map_h": 18, "has_objs": False, "has_ent": False,
         "has_trig": False, "has_doors": False}


def test_project_default():
    """`[scenes] overlay_cut`: the project's own cut, applied at boot.

    Absent is byte-identical - `start()` emits nothing and `graphics.text` is
    not even imported for it.
    """
    print(chr(10) + "[the [scenes] overlay_cut default]")
    off = emit_rooms_mos(dict(_BASE))
    check("win_overlay_cut" not in off,
          "absent: start() says nothing about it (byte-identical)")

    on = emit_rooms_mos(dict(_BASE, overlay_cut=96))
    check("text.win_overlay_cut(96)" in on, "declared: start() applies it")
    check('import "graphics.text"' in on,
          "...and the module imports the layer that owns it")
    check("-- [scenes] overlay_cut = 96" in on,
          "...beside the MARKER comment, never a bare call: lib/vm/core.mos "
          "calls the same verb and a scan for it would match every writer")
    start = on.split("function start(")[1]
    check(start.index("win_overlay_cut") < start.index("load_room("),
          "...before the first room load, so the boot room already draws with it")


def test_project_default_is_validated():
    """A cut off the bottom of the screen is REFUSED, not silently ignored.

    150 is the engine's own "no cut", so a project asking for 150 (or 200, or
    0) is asking for something it already has - and a typo that reads as "off"
    is exactly the kind of setting a build should not accept in silence.
    Driven through `generate_rooms`, which is what reads `studio.toml`.
    """
    print(chr(10) + "[the default is validated where studio.toml is read]")
    import shutil
    import tempfile
    from mosaik_vm.rooms import generate_rooms
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = os.path.join(here, "projects", "vm-hud")
    if not os.path.isdir(src):
        print("  (skip: projects/vm-hud is not here)")
        return
    for value, want in ((96, True), (144, False), (150, False), (200, False)):
        tmp = tempfile.mkdtemp(prefix="overlay_cut_")
        try:
            root = os.path.join(tmp, "p")
            shutil.copytree(src, root, ignore=shutil.ignore_patterns("build"))
            with open(os.path.join(root, "studio.toml"), "a",
                      encoding="utf-8") as f:
                f.write("%s[scenes]%soverlay_cut = %d%s"
                        % (chr(10), chr(10), value, chr(10)))
            try:
                generate_rooms(root)
                got = True
            except ValueError:
                got = False
            check(got == want, "overlay_cut = %d is %s"
                  % (value, "accepted" if want else "refused (it is off-screen)"))
            if want:
                text = open(os.path.join(root, "src", "rooms.mos"),
                            encoding="utf-8").read()
                check("text.win_overlay_cut(%d)" % value in text,
                      "...and reaches the generated rooms.mos")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("=" * 60)
    print("the overlay scanline cutoff (W7d phase 2)")
    print("=" * 60)
    test_byte_identical_unused()
    test_sole_tenant_keeps_its_own_emitter()
    test_third_tenant_merges()
    test_merge_without_every_tenant()
    test_the_window_records_its_wish()
    test_banked_arbiter_carries_it()
    test_honest_no_op_off_the_gb_family()
    test_state_lockstep()
    test_project_default()
    test_project_default_is_validated()
    print("\n" + "=" * 60)
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All overlay-cut checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
