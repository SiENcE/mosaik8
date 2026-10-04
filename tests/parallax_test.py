#!/usr/bin/env python3
"""SCANLINE PARALLAX bands (the reference engine's core/parallax.c model).

The background splits into up to three horizontal bands, each shown at its own
horizontal scroll. Two halves, and the feature is wrong without either:

  * the SCANLINE half - `bkg.parallax*` drives an LYC/STAT interrupt that
    writes each band's SCX as the beam reaches it;
  * the STREAMING half - `engine.scrollpx` keeps a column cursor PER BAND, so a
    band whose shifted range passes the 256 px tilemap shows its own columns
    instead of wrapping onto the ring's start.

Pinned here:
  * the four verbs compile on every console, real on the GB register model and
    honest no-op stubs elsewhere, and a program that never calls them is
    byte-identical;
  * the ISR's chain encoding - each band stores its OWN last scanline and the
    last stores 0, so the chain restarts at line 0 (which is the interrupt that
    writes band 0);
  * the ARBITRATION with `text.win_sprite_cut`. GBDK CHAINS LCD handlers, so
    the box's sprite cut fires at every band boundary too; refusing to ARM it is
    not enough, the handler itself has to stand down. Measured before the guard:
    the converted parallax room had its player in OAM with OBJ enabled in
    LCDC and drew nothing at all;
  * the transpiler's PX_* tables + their by-use emission;
  * `generate_rooms` wiring the bands in lockstep with those tables.

The end-to-end proof is a ROM: `projects/parallax-spike/verify.py` reads three
bands' scroll straight off a rendered frame.
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

import shutil
import tempfile

import mosaik_assets as M
from mosaik import MosaikCompiler
from mosaik_scenes import transpile
from mosaik_vm.rooms import emit_rooms_mos

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


_APP = '''
module "app" {
    import "graphics.bkg"
    import "platform.video"
    function main() {
        bkg.parallax_band(0, 79)
        bkg.parallax_band(1, 0)
        bkg.parallax(2)
        loop {
            bkg.parallax_scx(0, 5)
            bkg.parallax_scx(1, 80)
            bkg.parallax_scy(0)
            video.wait_vblank()
        }
    }
    export main
}
'''

_PLAIN = '''
module "app" {
    import "graphics.bkg"
    import "platform.video"
    function main() {
        loop { video.wait_vblank() }
    }
    export main
}
'''


def test_verbs_per_console():
    print("\n[the stdlib verbs]")
    for p in ("gameboy", "gameboy_color"):
        c = MosaikCompiler().compile(_APP, platform=p)
        check("gbs_px_lcd_isr" in c and "add_LCD(gbs_px_lcd_isr)" in c,
              "%s: a real LYC interrupt handler is installed" % p)
        check("while (STAT_REG & STATF_BUSY) ;" in c,
              "%s: the scroll write waits for H-BLANK (or the line tears)" % p)
        check("gbs_px_livex[i] = gbs_px_shx[i]" in c,
              "%s: the scroll is double-buffered, committed in VBL" % p)
        check("SCY_REG = ny ? 0 : gbs_px_livey;" in c,
              "%s: upper bands are pinned to SCY 0, only the last scrolls" % p)
        check("gbs_px_i = ny ? (uint8_t)(i + 1) : 0;" in c,
              "%s: the chain restarts itself at the 0 terminator" % p)
        check("LYC_REG = 0;" in c,
              "%s: arming only sets LYC 0 - line 0 writes band 0" % p)
    for p in ("sms", "gamegear", "nes", "lynx", "pce"):
        c = MosaikCompiler().compile(_APP, platform=p)
        check("void gbs_px_arm(uint8_t n) { (void)n; }" in c
              and "gbs_px_lcd_isr" not in c,
              "%s: honest no-op stubs (no per-line scroll register)" % p)


def test_line_zero_is_committed_in_vblank():
    """Band 0's scroll must be in place BEFORE line 0 is drawn.

    Band 0's own interrupt is armed at `LYC = 0` and its write waits for
    H-blank, which is the END of line 0 - one line too late. Real hardware
    hides that (LY reads 0 for most of line 153, so the coincidence fires in
    V-blank), but a scanline emulator does not: measured on
    `projects/parallax-spike` in PyBoy, line 0 carried the LAST band's SCX
    (250/252/254/0 while band 0 was at 15/16) and flickered along with the
    playfield, which is exactly the reported "the upper row flickers". So the
    VBL commit also publishes band 0, where no line is being drawn.
    """
    print("\n[band 0 lands before line 0]")
    c = MosaikCompiler().compile(_APP, platform="gameboy")
    vbl = c.split("void gbs_px_vbl_isr")[1].split("\n}")[0]
    check("SCX_REG = gbs_px_livex[0];" in vbl,
          "the VBL commit publishes band 0's SCX itself")
    check("SCY_REG = gbs_px_last[0] ? 0 : gbs_px_livey;" in vbl,
          "... under the ISR's own rule: only the LAST band scrolls vertically")
    check(vbl.index("gbs_px_livex[i] = gbs_px_shx[i]") < vbl.index("SCX_REG ="),
          "... AFTER the shadow commit, or it would publish last frame's value")
    # The commit is gated on a COMPLETE shadow (review E-5): the game loop
    # writes the bands one at a time and a V-blank between two of them used to
    # commit band 0's new camera beside band 1's old one. parallax_scy is the
    # frame's last scroll write and publishes; the ISR consumes the latch.
    check("if (gbs_px_ready) {" in vbl
          and vbl.index("if (gbs_px_ready)") < vbl.index("gbs_px_livex[i] = gbs_px_shx[i]")
          and "gbs_px_ready = 0;" in vbl,
          "the VBL commit copies the shadow only once the frame's writes are complete")
    check("gbs_px_shy = scy; gbs_px_ready = 1;" in c,
          "parallax_scy, the frame's last scroll write, publishes the shadow")


def test_bkg_move_stands_down_while_armed():
    """The bands own SCX/SCY; a write from the game loop is a torn slice.

    `vm.player.wide_view` sets the hardware scroll after streaming (it must,
    for a plain wide/roam room), and with bands armed that write lands on
    whatever scanline the game loop happened to reach and survives to the next
    band boundary. Measured on the platformer conversion's cutscene room:
    band 0 read 60 (camera 120 >> 1) on two LCD frames in three and 120 from a
    random row down on the third, which drew the annotations baked into the
    art over the tower - the reported "flicker and overlaid background".
    """
    print("\n[bkg.move vs the bands]")
    app = _APP.replace("bkg.parallax_scy(0)", "bkg.parallax_scy(0) bkg.move(9, 3)")
    c = MosaikCompiler().compile(app, platform="gameboy")
    check("void gbs_scroll_move(uint8_t x, uint8_t y) {" in c,
          "bkg.move routes through the v-blank scroll shadow")
    check("gbs_scroll_move(9, 3)" in c, "the call site lowers onto it")
    # The COMMIT is what stands down while bands are armed (they own SCX/SCY
    # through the LYC chain); the shadow write itself keeps recording the
    # camera, which is what the next plain room's first present resumes from.
    w = c.split("void gbs_wait_vblank(void) {")[1].split("\n}")[0]
    check("if (!gbs_px_n) {" in w and "SCX_REG = gbs_scr_shx;" in w,
          "... and the v-blank commit stands down while bands are armed")
    # A program with no parallax keeps the shadow, without the px guard.
    plain = _PLAIN.replace("loop {", "loop { bkg.move(9, 3)")
    c2 = MosaikCompiler().compile(plain, platform="gameboy")
    w2 = c2.split("void gbs_wait_vblank(void) {")[1].split("\n}")[0]
    check("gbs_scroll_move(9, 3)" in c2 and "SCX_REG = gbs_scr_shx;" in w2
          and "gbs_px_n" not in w2,
          "a program without parallax commits unconditionally")
    # The Lynx has no shadow (it recomposites every frame); the SMS/GG DO take
    # one, but for the sprite-phase reason rather than the tear one - their
    # sprite table commits once per frame, so the scroll has to land on the
    # same clock (scroll_vbl_commit_test owns that contract).
    c3 = MosaikCompiler().compile(app, platform="lynx")
    check("gbs_scroll_move" not in c3,
          "lynx: no shadow - bkg.move keeps the direct scroll write")
    c3 = MosaikCompiler().compile(app, platform="sms")
    check("gbs_scroll_move" in c3,
          "sms: bkg.move defers to the v-blank commit (sprite phase)")


def test_byte_identical_unused():
    print("\n[byte-identical off]")
    for p in ("gameboy", "sms", "lynx"):
        c = MosaikCompiler().compile(_PLAIN, platform=p)
        check("gbs_px" not in c,
              "%s: a program that never calls them emits nothing" % p)


def _body(src, signature):
    """The BODY of the function whose definition line is `signature`.

    NOT `src.split("void gbs_px_arm")[1]`: a gated prelude helper is also
    PROTOTYPED further up (that lockstep is its own rule), so splitting on the
    bare name lands on the prototype and the "body" then runs on through
    whatever code sits between the two - which is how an assertion that a
    function does not touch STAT_REG failed on somebody else's STAT_REG. Split
    on the definition, including its opening brace.
    """
    assert signature in src, signature
    return _code(src.split(signature, 1)[1].split("\n}", 1)[0])


def _asmless(src):
    """`src` with ASSEMBLY `;` comments removed, inside `__asm` blocks only.

    `_code` strips C comments, which is what stops a test matching a register
    name in prose. The ISR is hand-written asm now and its comments are `;`
    ones, so the same trap came straight back: an assertion that the handler
    never touches STATF_LYC matched the comment saying it never touches it.
    Scoped to `__asm`..`__endasm` because a bare `;` is a statement terminator
    in the C around it.
    """
    out, in_asm = [], False
    for line in src.split(chr(10)):
        if "__asm" in line:
            in_asm = True
        if "__endasm" in line:
            in_asm = False
        if in_asm and ";" in line:
            line = line.split(";", 1)[0]
        out.append(line)
    return chr(10).join(out)


def _code(src):
    """`src` with its C comments removed.

    A test that greps generated C for a register name matches the COMMENT
    explaining why the register is not written there, which is the opposite of
    what it means to assert. Strip them where the claim is "this code does not
    touch X" - the prose above it is allowed to say X out loud.
    """
    return re.sub(r"/\*.*?\*/", "", src, flags=re.S)


def test_win_sprite_cut_arbitration():
    """ONE LYC register, two tenants - and since W7d, ONE writer of it.

    The two features used to stand down for each other, and each of those
    guards was put there after a whole-screen defect found by playing a ROM:
    a box OPENING in a parallax room cut sprites at every band boundary
    (line 0 included, so nothing drew at all), and a box CLOSING there
    cleared STATF_LYC and killed the band chain for good. The answer is not a
    better guard, it is a single owner: every armed feature contributes STOPS
    to one list and one emitted ISR walks it (mosaik/codegen/gbdk_lyc.py).

    The ROM-side gate is a local probe that re-runs
    both defects on the reference-engine sample conversion's parallax room. This pins the SHAPE, which
    is what makes each of them unexpressible.
    """
    print("\n[one LYC register, one writer]")
    # The cut helper lives inside the WINDOW-overlay text block, so the
    # program has to use that too - which is exactly the shape a UI box has.
    both = '''
module "app" {
    import "graphics.bkg"
    import "graphics.text"
    import "platform.video"
    function main() {
        bkg.parallax_band(0, 79)
        bkg.parallax_band(1, 0)
        bkg.parallax(2)
        text.to_window(12, 6)
        text.win_sprite_cut(1)
        text.win_sprite_cut(0)
        text.to_bkg()
        loop { bkg.parallax_scx(0, 4) video.wait_vblank() }
    }
    export main
}
'''
    c = MosaikCompiler().compile(both, platform="gameboy")
    check("gbs_lyc_isr" in c and c.count("add_LCD(") == 1
          and "gbs_px_lcd_isr" not in c and "gbs_win_lcd_isr" not in c,
          "two tenants get ONE emitted LCD handler, installed once")
    check("if (gbs_px_n) return;" not in c and "if (!gbs_px_n) return;" not in c,
          "...so the standing-down dance between the two is GONE")
    # DEFECT 2, made unexpressible: disabling the LYC interrupt is the whole
    # machine's, not one feature's. It may happen in exactly one place, and
    # only on the branch where the merged list came out EMPTY.
    # DEFECT 6 (2026-09-21, reported from play - fixed on the Game Boy COLOR
    # and still there on the Game Boy, which is the tell). On the DMG a
    # read-modify-write of STAT while the LCD is on raises a SPURIOUS STAT
    # interrupt (fixed on the CGB), and `gbs_lyc_rebuild` did one on every box
    # open, box close and room change. The handler then ran at whatever line
    # the beam was on and performed the cursor's stop EARLY, so the band below
    # drew from there rather than from its own boundary - a strip of the
    # picture at the wrong scroll, Game Boy only.
    #
    # STAT is written ONCE now, at wire time, and never again. Disarming needs
    # no register: the ISR's `if (!gbs_lyc_n) return;` is the stand-down, which
    # is what makes phase 1's defect 2 unexpressible now by CONSTRUCTION.
    check(_code(c).count("STAT_REG |= STATF_LYC") == 1
          and "STAT_REG &= ~STATF_LYC" not in _code(c),
          "STAT_REG is written ONCE (at wire) and never cleared - the DMG STAT-write bug")
    wire = _body(c, "void gbs_lyc_wire(void)")
    check("STAT_REG |= STATF_LYC;" in wire and "IF_REG &= ~LCD_IFLAG;" in wire,
          "...and that one write drops the spurious request it may raise")
    # By DEFINITION line, via _body - the bare-name split this used to do is
    # the exact trap that helper exists for, and it landed on a prototype the
    # moment the ISR's signature changed (it gained NAKED).
    import re as _re
    for name in ("gbs_px_arm", "gbs_text_win_cut", "gbs_text_win_overlay_cut",
                 "gbs_lyc_rebuild", "gbs_lyc_isr", "gbs_lyc_vbl"):
        m = _re.search(r"^(?:static )?void %s\(.*\{$" % name, _code(c), _re.M)
        if m:
            check("STATF_LYC" not in _body(_code(c), m.group(0)),
                  "...%s never reaches for it" % name)
    check("ret z" in _body(c, "void gbs_lyc_isr(void) NONBANKED NAKED {"),
          "...and the ISR's own guard is the stand-down a disarm relies on")
    # ...and DEFECT 2 - a box closing killing the band chain - is now
    # unexpressible by CONSTRUCTION rather than by care: the empty arm has no
    # register to reach for, it only resets the cursor.
    reb = _code(c).split("static void gbs_lyc_rebuild")[1]
    empty_arm = reb.split(chr(10) + " " * 8 + "} else {")[1]
    empty_arm = empty_arm.split(chr(10) + " " * 8 + "}")[0]
    check("gbs_lyc_k = 0;" in empty_arm and "STAT" not in empty_arm,
          "...the rebuild's EMPTY arm resets the cursor and touches no register")
    cut = _body(c, "void gbs_text_win_cut(uint8_t on) {")
    check("LYC_REG" not in cut and "STAT_REG" not in cut,
          "the cut publishes a stop and rebuilds; it never touches the registers")
    arm = _body(c, "void gbs_px_arm(uint8_t n) {")
    check("STAT_REG" not in arm and "gbs_lyc_rebuild();" in arm,
          "...and neither does arming or disarming the bands")
    # DEFECT 1, made unexpressible: the cut is ONE stop in the walk, so it
    # cannot fire at a band boundary. The list is built in scanline order.
    check("if (pend && gbs_cut_line <= ln)" in c and "gbs_lyc_put(ln, act);" in c,
          "the cut is merged into the list IN SCANLINE ORDER, as one stop")
    isr = _body(c, "void gbs_lyc_isr(void) NONBANKED NAKED {")
    check("cp #2" in isr and "jr c, 2$" in isr,
          "a single-stop list does not rewrite LYC with the line the beam is on")
    # DEFECT 3 (2026-09-20, found by PLAYING the platformer conversion's cutscene): a
    # rebuild runs from the GAME LOOP - a box opening - so the beam is usually
    # partway down a visible frame. Re-arming at stop 0 points LYC at a line
    # already gone by, so every stop below the beam is skipped for the REST of
    # that frame and each band under one draws at the scroll of the band above
    # it. Measured there: the FIXED lower band drew at the half-speed band's
    # SCX for its whole height, on 6 of 1,833 frames in that room, which is the
    # reported ground-scrolls-with-the-sky flicker. The rebuild walks to the
    # first stop past LY instead.
    reb_all = _code(c).split("gbs_lyc_rebuild(void)")[1].split("\n}")[0]
    check("LY_REG" in reb_all
          and "while (nk < gbs_lyc_n && gbs_lyc_line[nk] <= ly) ++nk;" in reb_all,
          "the rebuild re-arms at the first stop BELOW THE BEAM, not at stop 0")
    check("LYC_REG = gbs_lyc_line[0];" not in reb_all,
          "...so it never points LYC at a line the beam has already passed")
    check("gbs_lyc_k = nk;" in reb_all and "LYC_REG = gbs_lyc_line[nk];" in reb_all,
          "...and the cursor moves with the register, as it does in the ISR")
    # DEFECT 4 (2026-09-21, found by PLAYING the reference conversion's parallax room on
    # the Game Boy with A mashed at the sign): the CURSOR is shared state
    # and a rebuild can move it while a coincidence is already pending, so the
    # handler that takes it performs a stop the beam is nowhere near. Traced: a
    # rebuild entered at LY 151, finished past the wrap, armed stop 1, and the
    # stop-0 request pending from LY 0 was serviced at LY 1 - band 1's scroll
    # across the whole top band. The ISR derives its stop from LY now.
    # Dropping the pending request instead was MEASURED and is worse (the frame
    # then gets no stop at all), so this also pins that it is NOT dropped.
    check("if (IF_REG & LCD_IFLAG) {" in reb_all
          and "while (nk < gbs_lyc_n && gbs_lyc_line[nk] != ly) ++nk;" in reb_all,
          "a rebuild leaves a PENDING coincidence pointing at the stop it was raised for")
    check("ly = LYC_REG;" in reb_all,
          "...found by LINE, because removing a stop shifts every later index")
    # DEFECT 5 (2026-09-21, reported from play on the platformer conversion's cutscene and
    # the reference conversion's parallax room, with NO box on screen): a DISARM may not
    # kill the frame being drawn. Every tenant standing down empties the list,
    # and the empty arm clears STATF_LYC - so a room change calling
    # `gbs_px_arm(0)` from the game loop stopped the interrupt for the rest of
    # that frame and every band below the beam drew at the band above's scroll.
    # MEASURED: LY 46, screen still visible, lines 96..143 at the SKY's scroll.
    # The list now stays alive to the end of the frame and V-blank empties it.
    check("gbs_lyc_drop = 1;" in reb_all and "LY_REG < 144" in reb_all,
          "a mid-frame DISARM is deferred, so it cannot kill the frame being drawn")
    check("LCDC_REG & LCDCF_ON" in reb_all,
          "...only while the LCD is on; with it off the immediate path is right")
    vbl = _body(c, "void gbs_lyc_vbl(void) NONBANKED {")
    check(vbl.index("gbs_lyc_drop") < vbl.index("if (!gbs_lyc_n) return;"),
          "...and V-blank applies it BEFORE it reads the list")
    # The ISR is NAKED ASSEMBLY now (the reference's shape, the reference VM's
    # parallax_LCD_isr), which is what bought the margin back - but the rule it
    # was bought for still holds: this handler must not grow a walk. It reads
    # its stop from `gbs_lyc_k` and never from LY_REG.
    check("__asm" in isr and "_LY_REG" not in isr,
          "...and the ISR still takes its stop from the cursor, never from LY_REG")
    check("NAKED" in _code(c) and "__endasm" in isr,
          "...and it is NAKED asm, so it pays no C prologue at a band boundary")

    # A SOLE TENANT HAS NOTHING TO ARBITRATE, so it keeps its original emitter
    # whole and stays byte-identical to before the merge existed. That is the
    # reason the merge machinery is affordable at all: measured on a probe
    # program, it is ~170 B of resident image.
    solo_cut = both
    for gone in ("bkg.parallax_band(0, 79)", "bkg.parallax_band(1, 0)",
                 "bkg.parallax(2)", "bkg.parallax_scx(0, 4)"):
        solo_cut = solo_cut.replace(gone, "")
    c2 = MosaikCompiler().compile(solo_cut, platform="gameboy")
    check("gbs_win_lcd_isr" in c2 and "gbs_lyc_isr" not in c2
          and "gbs_px_n" not in c2,
          "a box-only program keeps the ORIGINAL cut, and no merge machinery")
    solo_px = both
    for gone in ("text.win_sprite_cut(1)", "text.win_sprite_cut(0)"):
        solo_px = solo_px.replace(gone, "")
    c3 = MosaikCompiler().compile(solo_px, platform="gameboy")
    check("gbs_px_lcd_isr" in c3 and "gbs_lyc_isr" not in c3
          and "gbs_cut_line" not in c3,
          "a parallax-only program keeps the ORIGINAL band chain, likewise")
    c4 = MosaikCompiler().compile(_PLAIN, platform="gameboy")
    check("gbs_lyc" not in c4 and "LYC_REG" not in c4,
          "a program with neither carries none of it (byte-identical)")


_SOLO_HEAD = '''
module "app" {
    import "graphics.bkg"
    import "graphics.text"
    import "graphics.window"
    import "platform.video"
    function main() {
'''
_SOLO_TAIL = '''
        loop { video.wait_vblank() }
    }
    export main
}
'''

#: The three SOLE tenants of LYC_REG: (name, body, verb, isr, flag, the ARM
#: write of the flag, the DISARM branch's opening line inside the verb).
_SOLO = (
    ("box sprite cut",
     "        text.to_window(12, 6)\n        text.win_sprite_cut(1)\n"
     "        text.win_sprite_cut(0)\n        text.to_bkg()",
     "void gbs_text_win_cut(uint8_t on) {",
     "void gbs_win_lcd_isr(void) NONBANKED {",
     "gbs_cut_on", "gbs_cut_on = 1;", "} else {"),
    ("parallax bands",
     "        bkg.parallax_band(0, 79)\n        bkg.parallax_band(1, 0)\n"
     "        bkg.parallax(2)\n        bkg.parallax(0)",
     "void gbs_px_arm(uint8_t n) {",
     "void gbs_px_lcd_isr(void) NONBANKED {",
     "gbs_px_n", "gbs_px_n = n;", "if (n == 0) {"),
    ("overlay cut",
     "        window.move(7, 0)\n        video.show_window()\n"
     "        text.win_overlay_cut(100)\n        text.win_overlay_cut(150)",
     "void gbs_text_win_overlay_cut(uint8_t y) {",
     "void gbs_ocut_lcd_isr(void) NONBANKED {",
     "gbs_ocut_on", "gbs_ocut_on = 1;", "} else {"),
)


def test_solo_tenants_write_stat_once():
    """A SOLE LYC tenant writes STAT once, at wire time, like the merged one.

    The DMG STAT-write bug (phase 3e, fixed in the merged path 2026-09-21)
    had a second home: the three SOLO emitters, which a program with ONE
    LYC_REG tenant keeps - every VM project with a dialogue box and no bands
    among them. Each set STATF_LYC on every arm and cleared it on every
    disarm, and on a MONOCHROME Game Boy each of those writes can raise a
    spurious LCD interrupt: the sprite cut then did HIDE_SPRITES from
    whatever line the beam was on (sprites gone to the bottom of the frame on
    box open AND close), and the band chain ran early.

    PyBoy does not emulate the quirk, so the SHAPE is the gate here, as it is
    for the merged path. `tools/lycprobe/solo_cut_probe.py` is the ROM-side
    check that the handler's stand-down - which now runs every frame - holds.
    """
    print("\n[the SOLO tenants write STAT once, at wire time]")
    for name, body, verb, isr, flag, arm, disarm in _SOLO:
        c = MosaikCompiler().compile(_SOLO_HEAD + body + _SOLO_TAIL,
                                     platform="gameboy")
        assert "gbs_lyc_isr" not in c, "%s: this program is not a SOLO tenant" % name
        code = _code(c)
        check(code.count("STAT_REG |= STATF_LYC") == 1
              and "STAT_REG &= ~STATF_LYC" not in code,
              "%s: STAT is written ONCE in the whole program and never cleared"
              % name)
        v = _body(code, verb)
        # INSIDE the block, not merely after its opening line: the block ends
        # at the first brace back at the CRITICAL line's own indentation.
        crit = v.find("CRITICAL {")
        indent = v[v.rfind("\n", 0, crit) + 1:crit] if crit >= 0 else ""
        crit_end = v.find("\n" + indent + "}", crit) if crit >= 0 else -1
        at = v.find("STAT_REG |= STATF_LYC;")
        drop = v.find("IF_REG &= ~LCD_IFLAG;")
        check(0 <= crit < at < drop < crit_end,
              "%s: ...inside the wire's CRITICAL block, which drops the request "
              "it may raise before interrupts come back" % name)
        h = _body(code, isr)
        stand = "if (!%s) return;" % flag
        # The first STATEMENT: C89 declarations come ahead of it.
        stmts = [ln.strip() for ln in h.splitlines()
                 if ln.strip() and not ln.strip().startswith("uint8_t ")]
        check(stmts and stmts[0] == stand,
              "%s: the handler's FIRST act is to stand down on %s (it is "
              "entered every frame now)" % (name, flag))
        check(0 <= v.rfind("LYC_REG =") < v.find(arm),
              "%s: an arm moves LYC_REG BEFORE it raises the flag" % name)
        off = v.split(disarm, 1)[1].split("\n    }", 1)[0] if disarm in v else ""
        check("%s = 0;" % flag in off and "STAT" not in off
              and "LYC_REG" not in off,
              "%s: a disarm drops the flag and touches no register" % name)


def test_arbiter_banks():
    """W7d PHASE 2: the stop-list ARBITER leaves the resident image.

    The two handlers vector from hardware and must stay in the always-mapped
    home bank; the state is WRAM, which is not banked at all, so there is one
    stop list either way. The third part - wire once, merge the stops, re-arm
    the register - runs on an ARM or a DISARM (a room load, a box opening) and
    never per frame, which is the `_prelude_data_bank` profile the CGB fade
    engine established. Measured on the reference-engine sample conversion: 229 B of bank 0 on both GB
    targets, taking the Game Boy Color from 45 B of spare to 274 B.

    A build that cannot bank keeps the original CHARACTER FOR CHARACTER, which
    is the same rule the merge itself follows for a one-tenant program.
    """
    print("\n[the arbiter banks (W7d phase 2)]")
    flat, gf = _compile_both()
    check(not gf.bank_units and "static void gbs_lyc_rebuild(void) {" in flat
          and "static void gbs_lyc_wire(void) {" in flat,
          "a flat cart keeps the ORIGINAL resident spelling, static and all")

    banked, g = _compile_both(code_banks=["app"])
    bank = "\n".join(g.bank_units.values())
    check("void gbs_lyc_rebuild(void) BANKED {" in bank
          and "void gbs_lyc_wire(void) BANKED {" in bank,
          "banking moves both entry points into a bank TU")
    # THE DEFECT THIS EXISTS FOR. The first cut left `static` on the rebuild's
    # definition inside the bank TU. sdcc drops an unused static, the linker
    # calls the result an "Undefined Global" WARNING and exits 0, and the ROM's
    # box-open path jumps to address 0. Match the DEFINITION, not the name: the
    # bank TU also mentions both in its own comment.
    check("static void gbs_lyc_rebuild" not in bank
          and "static void gbs_lyc_wire" not in bank,
          "...and DROPS their `static`, or sdcc discards them and the link "
          "only warns")
    check("static void gbs_lyc_put(uint8_t line, uint8_t act) {" in bank
          and "gbs_lyc_put" not in banked,
          "gbs_lyc_put stays private to whichever TU holds the body")
    check("void gbs_lyc_rebuild(void) BANKED;" in banked
          and "void gbs_lyc_wire(void) BANKED;" in banked
          and "void gbs_lyc_rebuild(void) {" not in banked,
          "the resident TU sees BANKED PROTOTYPES, not definitions")
    # A prototype that disagrees with its definition about BANKED is a call
    # through a trampoline that was never set up, so the qualifier is read back
    # off the placement rather than passed in (_lyc_arbiter_decls).
    check(g.prelude_bank_defs.get("gbs_lyc_rebuild") is not None,
          "the placement is recorded, so the prototypes can agree with it")

    check("void gbs_lyc_isr(void) NONBANKED NAKED {" in banked
          and "void gbs_lyc_vbl(void) NONBANKED {" in banked
          and "NONBANKED {" not in bank,
          "the two HANDLERS stay home - a hardware vector cannot be banked")
    # Match a DEFINITION, which is a line STARTING `uint8_t` - `"uint8_t
    # gbs_lyc_n;" in bank` is true of the bank TU's own `extern uint8_t
    # gbs_lyc_n;` and would pass while the state was duplicated.
    def defines(text, sym):
        return any(ln.startswith("uint8_t " + sym)
                   for ln in text.split("\n"))

    for sym in ("gbs_lyc_line[GBS_LYC_STOPS]", "gbs_lyc_n;", "gbs_px_n;",
                "gbs_cut_on;"):
        check(defines(banked, sym) and not defines(bank, sym),
              "state stays ONE copy in the main TU: %s" % sym.rstrip(";"))
    for sym in ("gbs_lyc_line[]", "gbs_lyc_n;", "gbs_px_n;", "gbs_cut_on;"):
        check(("extern uint8_t " + sym) in bank,
              "...and the bank TU sees it through extern: %s" % sym.rstrip(";"))
    # The bank TU gets only <gbdk/platform.h> and <stdint.h>, so it carries its
    # own copy of the action bits rather than the main TU's #defines.
    check("#define GBS_LYC_HIDE 0x10" in bank and "#define GBS_LYC_BAND 0x04" in bank,
          "the bank TU is self-contained (it re-states the action bits)")

    # The gate that is not a green suite is projects/lyc-merge-lab/verify.py,
    # which drives BOTH arms on real ROMs and reads where each symbol linked.
    lab = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "projects", "lyc-merge-lab", "verify.py")
    text = open(lab, encoding="utf-8").read() if os.path.exists(lab) else ""
    check("def build_banked(" in text and "placement(bsym, banked=True)" in text,
          "the lab's verify.py drives a BANKED arm as well as the resident one")


def _compile_both(**kw):
    """Compile the two-tenant program and hand back its generator too."""
    compiler = MosaikCompiler()
    c = compiler.compile_program([("app.mos", _BOTH)], platform="gameboy", **kw)
    assert not c.startswith("Compilation error"), c
    return c, compiler.code_generator


_BOTH = '''
module "app" {
    import "graphics.bkg"
    import "graphics.text"
    import "platform.video"
    function tick() {
        bkg.parallax_scx(0, 4)
    }
    function main() {
        bkg.parallax_band(0, 79)
        bkg.parallax_band(1, 0)
        bkg.parallax(2)
        text.to_window(12, 6)
        text.win_sprite_cut(1)
        text.win_sprite_cut(0)
        text.to_bkg()
        loop { tick() video.wait_vblank() }
    }
    export main
}
'''


def _world(parallax=True):
    scenes = [{"name": "flat", "map_w": 20, "map_h": 18, "map": [0] * 360},
              {"name": "px", "map_w": 80, "map_h": 18, "scene_type": "platform",
               "map": [0] * (80 * 18)}]
    if parallax:
        scenes[1]["parallax"] = [{"rows": 10, "speed": 4},
                                 {"rows": 3, "speed": 1},
                                 {"rows": 0, "speed": 0}]
    return {"world": {"module": "scenes", "vm": True, "map_w": 20, "map_h": 18},
            "tileset": {"png": "t.png"}, "kinds": {"player": 0},
            "scene": scenes}


def _transpile(world):
    tmp = tempfile.mkdtemp(prefix="px_")
    try:
        M.write_png_indexed(os.path.join(tmp, "t.png"), 8, 16,
                            [0] * 64 + [3] * 64,
                            [(255, 255, 255), (170, 170, 170),
                             (85, 85, 85), (0, 0, 0)])
        return transpile(world, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scene_tables():
    print("\n[the PX_* tables]")
    src = _transpile(_world())
    check("const PX_MAX: u8 = 3" in src and "const PX_FIXED: u8 = 255" in src,
          "the band limit and the FIXED sentinel are emitted")
    check("function px_count(" in src and "function px_shift(" in src,
          "the per-scene readers are emitted")
    # scene 0 has no bands, scene 1 has three: rows 0/10/13, shifts 4/1/0.
    rows = src.split("const PX_ROW: array[u8, 6] = [")[1].split("]")[0]
    check([int(v) for v in rows.replace("\n", "").split(",")] == [0, 0, 0, 0, 10, 13],
          "PX_ROW is fixed-stride (3 slots a scene), zeroed for a scene "
          "with no bands")
    sh = src.split("const PX_SHIFT: array[u8, 6] = [")[1].split("]")[0]
    check([int(v) for v in sh.replace("\n", "").split(",")] == [0, 0, 0, 4, 1, 0],
          "PX_SHIFT carries the speed divisors")
    exp = [l for l in src.splitlines() if l.strip().startswith("export ")]
    check(exp and "px_count" in exp[-1], "the readers are exported")
    plain = _transpile(_world(parallax=False))
    # (the scene is NAMED "px", so its map symbol is PX_MAP - match the real
    # names, not the prefix)
    check(not any(s in plain for s in ("PX_COUNT", "PX_SHIFT", "px_count(",
                                       "PX_FIXED")),
          "a world with no bands emits none of it (byte-identical)")


def test_rooms_lockstep():
    print("\n[rooms.mos lockstep]")
    base = {"types": ["platform"], "uniform": False, "has_collision": True,
            "wide_rooms": True}
    on = emit_rooms_mos(dict(base, parallax=True))
    off = emit_rooms_mos(dict(base, parallax=False))
    check('import "engine.scrollpx"' in on and "scrollpx" not in off,
          "engine.scrollpx is imported for a parallax world only")
    check("function arm_parallax(rm: u8)" in on,
          "the band declaration is generated from the scene tables")
    check("scenes.px_count(rm)" in on and "scenes.px_shift(rm, i)" in on,
          "... reading the transpiler's own readers (the lockstep)")
    check("scrollpx.arm(0)" in on,
          "every room load DISARMS first, so a room with no bands cannot "
          "inherit the previous room's")
    check("function gatherpx(c: u16, row: u8, rows: u8)" in on,
          "the column source takes the band's ROW RANGE")
    check("scrollpx.voff" in on,
          "the last band's vertical scroll is published, not written "
          "(the interrupt owns SCX/SCY once armed)")
    # A narrow-only world must not reach for it: parallax rides the streamer.
    narrow = emit_rooms_mos({"types": ["platform"], "uniform": True,
                             "has_collision": True, "parallax": True})
    check("scrollpx" not in narrow,
          "a world with no wide room emits none of it")


def test_gated_to_the_gb_family():
    """Every `scrollpx` site sits inside an `if platform ==` GB-family fork.

    Not a nicety - a REGRESSION guard. `bkg.parallax*` are no-op stubs off the
    GB register model, and `engine.scrollpx` publishes each band's scroll
    THROUGH them and never calls `bkg.move`. So on SMS/GG the bands streamed
    their columns at their own offsets while nothing ever wrote the scroll
    register, and the converted parallax room mis-rendered where it had been
    correct. Those consoles take the plain single-scroll streamer again.

    It has to be a `platform` fork rather than a runtime test: a per-console
    const leaves the REFERENCES live for tree-shaking, and nothing dead-strips
    inside a TU - the bodies would be resident image on a console that can
    never run them. Measured: with the fork, the SMS/GG sample conversion is back to its
    exact pre-parallax resident image (SMS 3,170 / GG 3,164 B spare).
    """
    print("\n[gated to the GB family]")
    for kind, info in (("wide", {"types": ["platform"], "wide_rooms": True}),
                       ("roam", {"types": ["topdown"], "roam_rooms": True})):
        src = emit_rooms_mos(dict(info, uniform=False, has_collision=True,
                                  parallax=True))
        bad = []
        # Walk the whole module once keeping the stack of OPEN blocks, so a
        # site nested several scopes deep (a call inside a function inside the
        # module-level fork) still counts as guarded.
        stack = []
        for ln in src.splitlines():
            body = ln.strip()
            is_px = ("scrollpx" in ln or "arm_parallax(rm)" in ln)
            if (is_px and not body.startswith("--")
                    and not body.startswith('import "')
                    and not any('if platform == "gameboy"' in o for o in stack)):
                bad.append(body[:60])
            for _ in range(ln.count("{")):
                stack.append(body)
            for _ in range(ln.count("}")):
                if stack:
                    stack.pop()
        check(not bad,
              "%s: every scrollpx call site is inside a GB-family fork%s"
              % (kind, (" (unguarded: %s)" % bad[:2]) if bad else ""))
        # The non-GB branch must still emit the PLAIN streamer. Its giveaway is
        # the plain seed call (`scrollpx.seed` is the parallax one, and
        # `refill2d` also appears in the SMS box-close redraw), so match on
        # that rather than on the old `fill`/`fill2d` - a room load seeds at the
        # entry camera now, never at the map origin.
        plain = ("scroll.seed(" if kind == "wide" else "scroll2d.seed2d(")
        check("} else {" in src and plain in src,
              "%s: the plain streamer is emitted in the non-GB branch" % kind)


def main():
    print("=" * 60)
    print("scanline parallax bands")
    print("=" * 60)
    test_verbs_per_console()
    test_line_zero_is_committed_in_vblank()
    test_bkg_move_stands_down_while_armed()
    test_byte_identical_unused()
    test_win_sprite_cut_arbitration()
    test_solo_tenants_write_stat_once()
    test_arbiter_banks()
    test_scene_tables()
    test_rooms_lockstep()
    test_gated_to_the_gb_family()
    print("\n" + "=" * 60)
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All parallax checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
