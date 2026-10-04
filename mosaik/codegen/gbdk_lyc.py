"""THE ONE OWNER OF `LYC_REG` when more than one feature wants it (W7d).

Up to three features want an interrupt at a scanline of their choosing:

  * the scanline PARALLAX bands (`bkg.parallax*`) - write that band's SCX/SCY
    as the beam reaches it;
  * the dialogue box's SPRITE CUT (`text.win_sprite_cut`) - HIDE_SPRITES one
    line before the window's first, because OBJ draws above the window;
  * the OVERLAY CUT (`text.win_overlay_cut`) - put the window away partway
    down the screen and give the sprites back there, which is what lets an
    overlay cover only the TOP of the screen with the room playing below it
    (the reference engine's `overlay_cut_scanline`, W7d phase 2).

There is one `LYC_REG`, and GBDK **chains** its LCD handlers: `add_LCD`
registers another, it does not replace, and it cannot be undone. So GB
Studio's own answer - install exactly one of three LCD ISRs per scene - is
not available to us. What IS available is one handler installed once that
dispatches over whatever is armed, which is what this emits.

**The model is a STOP LIST.** Each armed feature contributes stops: a
scanline plus an action byte. They are merged into one list in scanline order
by `gbs_lyc_rebuild`, which runs when a feature arms or disarms (a room load,
a box opening) and never per frame. The ISR walks the list, re-arms `LYC_REG`
for the next stop, and wraps at the end; the V-blank handler re-seeds the walk
and commits what has to land where no line is being drawn.

**THIS IS EMITTED ONLY FOR A PROGRAM THAT USES TWO OF THE THREE**, and that
is not a saving, it is the honest scope. A program with ONE tenant has no
arbitration to do: its stop list would be the feature's own chain, and the
merge machinery is ~145 B of resident image (MEASURED) that can never merge
anything. So the single-feature forms stay exactly where they were, in
`gbdk_bkg.py` and `gbdk_text.py`, character for character - which also means
every program that uses one of them is **byte-identical to before this
existed**, and the flagship samples of each (`parallax-spike`, every VM
project with a dialogue box) keep a measured, defect-hardened path untouched.

**What this retires for the program that DOES use both.** The two features
used to keep the peace by standing down for each other - four `if (gbs_px_n)`
guards across those two files - and both guards exist because a whole-screen
defect was found by PLAYING a ROM:

  1. a box OPENING in a parallax room: the chained cut ISR fired at every band
     boundary, line 0 included, so `HIDE_SPRITES` blanked every sprite for the
     whole visible frame (measured on the converted parallax room - the
     player was in OAM, OBJ was enabled in LCDC, and nothing drew);
  2. a box CLOSING there: `STAT_REG &= ~STATF_LYC` disables the LYC interrupt
     for the whole machine, so the band chain died permanently while the bands
     stayed armed in software and every one of them rendered at the full
     camera (measured on the platformer conversion's cutscene).

Neither is expressible now. There is one writer of `LYC_REG` and one writer of
`STATF_LYC`, and a feature that disarms rebuilds the list rather than reaching
for the register. The cost of the old peace was that a box in a parallax room
simply went WITHOUT its sprite cut; it gets one now, because the cut is just
another stop in the same walk.

A GbdkBackend concern mixin - methods run against the full CodeGenerator
instance (self.emit, self.caps, ...)."""


class GbdkLycMixin:
    # Action bits in a stop's `act` byte. The low two bits are the band index
    # (PARALLAX_BANDS is 3), so a stop is one byte and the ISR's dispatch is a
    # pair of bit tests.
    LYC_ACT_BAND = 0x04     # write this band's SCX (and SCY, see below)
    LYC_ACT_SCY = 0x08      # ...and this band carries the vertical scroll
    LYC_ACT_HIDE = 0x10     # HIDE_SPRITES (the box's window cut)
    # The window goes OFF here and the sprites come back (the overlay cut).
    # Tested AFTER the hide in the ISR, so a program whose box cut and overlay
    # cut land on ONE line ends that line with sprites on - which is what
    # the reference VM's simple_LCD_isr does when WY - 1 reaches overlay_cut_scanline.
    LYC_ACT_WINOFF = 0x20

    def _lyc_used(self):
        """True when this program has TWO OR MORE tenants for `LYC_REG`.

        One tenant needs no arbitration, so it keeps its own emitter and its
        own byte-identical output; the merge only exists where two features
        would otherwise have to stand down for each other. Count, do not spell
        the pair out, so the reserved third tenant joins by being counted.

        `has_gb_regs` and `has_window` are the same four consoles (gameboy,
        gameboy_color, analogue_pocket, megaduck), so one capability gate
        covers every half."""
        if not self.caps.get('has_gb_regs'):
            return False
        return (bool(self.parallax_used) + bool(self.win_cut_used)
                + bool(self.overlay_cut_used)) >= 2

    def _emit_gbdk_lyc(self):
        if not self._lyc_used():
            return
        px = self.parallax_used
        cut = self.win_cut_used
        ocut = self.overlay_cut_used
        bands = self.PARALLAX_BANDS
        # Stops: one per armed band, plus the box's cut line, plus the overlay
        # cut's. Sized for the worst case, which is what a feature is allowed
        # to arm at once.
        stops = (bands if px else 0) + (1 if cut else 0) + (1 if ocut else 0)
        self.emit("/* ==== The ONE owner of LYC_REG (the LCD scanline interrupt) ====")
        self.emit("   Every feature that wants an interrupt at a scanline contributes")
        self.emit("   STOPS - a line plus an action byte - and they are merged into one")
        self.emit("   list in line order whenever a feature arms or disarms. This ISR")
        self.emit("   walks that list and re-arms LYC for the next stop; the last stop")
        self.emit("   wraps to the first.")
        self.emit("")
        self.emit("   GBDK CHAINS LCD handlers and add_LCD cannot be undone, so GB")
        self.emit("   Studio's shape (install one of three LCD ISRs per scene) is not")
        self.emit("   available here; one handler installed once, dispatching over what")
        self.emit("   is armed, is. It replaces the arbitration the two features used to")
        self.emit("   keep by standing down for each other, which had cost a box in a")
        self.emit("   parallax room its sprite cut and produced two whole-screen defects")
        self.emit("   (see mosaik/codegen/gbdk_lyc.py). */")
        self.emit("#define GBS_LYC_STOPS %d" % stops)
        if px:
            self.emit("#define GBS_LYC_BAND 0x%02X   /* write band (act & 3)'s SCX */" %
                      self.LYC_ACT_BAND)
            self.emit("#define GBS_LYC_SCY  0x%02X   /* ...and it carries SCY */" %
                      self.LYC_ACT_SCY)
        if cut:
            self.emit("#define GBS_LYC_HIDE 0x%02X   /* HIDE_SPRITES (the box's cut) */" %
                      self.LYC_ACT_HIDE)
        if ocut:
            self.emit("#define GBS_LYC_WINOFF 0x%02X /* window off, sprites back */" %
                      self.LYC_ACT_WINOFF)
        # NOT static, and not initialised. The gate for this path is a ROM
        # MEASUREMENT (projects/lyc-merge-lab/verify.py), and a file-static
        # symbol is absent from the .noi even with -Wl-j, so a probe cannot
        # read the stop list it exists to check - the first run could not tell
        # "the cut was never armed" from "the cut fired at the wrong line".
        # External linkage costs nothing here; an INITIALISER would, because
        # BSS is free and an initialised global is resident image.
        self.emit("uint8_t gbs_lyc_line[GBS_LYC_STOPS];  /* stop's scanline */")
        self.emit("uint8_t gbs_lyc_act[GBS_LYC_STOPS];   /* what to do there */")
        self.emit("uint8_t gbs_lyc_n;      /* stops in use; 0 = interrupt off */")
        self.emit("uint8_t gbs_lyc_k;      /* which stop the ISR is on */")
        self.emit("uint8_t gbs_lyc_wired;  /* zero-init: BSS is free, an initialiser is not */")
        self.emit("uint8_t gbs_lyc_drop;   /* a disarm waiting for V-blank; see gbs_lyc_rebuild */")
        if px:
            self.emit("")
            self.emit("/* ---- PARALLAX band state (the ISR is its only reader) ----")
            self.emit("   The four `bkg.parallax*` verbs that write it are emitted with the")
            self.emit("   rest of the background helpers; the scroll values are DOUBLE-")
            self.emit("   BUFFERED (the game publishes into a shadow, V-blank commits it)")
            self.emit("   so a band's SCX cannot change between the top and bottom of the")
            self.emit("   frame it is being drawn in. */")
            self.emit("#define GBS_PX_BANDS %d" % bands)
            # Zero-init by BSS, never by an initialiser: the solo emitter spells
            # `= 0` on four of these and pays resident image for it, which is
            # only kept there because that text is byte-identical to what
            # shipped. New text has no such obligation.
            self.emit("uint8_t gbs_px_n;                  /* bands armed; 0 = off */")
            self.emit("uint8_t gbs_px_last[GBS_PX_BANDS];   /* last scanline of band i */")
            self.emit("uint8_t gbs_px_shx[GBS_PX_BANDS];    /* SCX shadow (game writes) */")
            self.emit("uint8_t gbs_px_livex[GBS_PX_BANDS];  /* SCX live (ISR reads) */")
            self.emit("uint8_t gbs_px_shy, gbs_px_livey;")
            self.emit("/* 1 once the game loop has written EVERY band's scroll for this frame")
            self.emit("   (parallax_scy is the last write, see engine.scrollpx.update). The")
            self.emit("   V-blank commit below copies the shadow only then: a V-blank landing")
            self.emit("   between two bands' writes used to commit band 0's NEW camera beside")
            self.emit("   band 1's OLD one - a torn frame whenever the loop ran long. */")
            self.emit("uint8_t gbs_px_ready;")
        if cut:
            self.emit("")
            self.emit("/* ---- Window SPRITE CUT state ----")
            self.emit("   OBJ draws ABOVE the WINDOW on this hardware, so an actor low in the")
            self.emit("   room shows through an open dialogue box (measured on the reference-engine")
            self.emit("   import: the player's lower objects intruded 4 px into the box).")
            self.emit("   Cut them one line BEFORE the window's first - the reference engine's own")
            self.emit("   interrupts.c does exactly that, at WY_REG - 1 - and restore them")
            self.emit("   each V-blank. `gbs_spr_want` mirrors what the PROGRAM asked for")
            self.emit("   (video.show_sprites / hide_sprites), so the restore can never turn")
            self.emit("   sprites back on for a game that wanted them off; it is defined with")
            self.emit("   those two verbs further down the prelude. */")
            self.emit("extern uint8_t gbs_spr_want;")
            self.emit("uint8_t gbs_cut_on;     /* a box wants the cut */")
            self.emit("uint8_t gbs_cut_line;   /* ...at this scanline */")
        if ocut:
            self.emit("")
            self.emit("/* ---- The OVERLAY CUT state (W7d phase 2) ----")
            self.emit("   the reference engine's `overlay_cut_scanline`: at this line the WINDOW layer")
            self.emit("   goes off and the sprites come back, so an overlay (the curtain, a")
            self.emit("   box) covers only the TOP of the screen with the room playing")
            self.emit("   below it - the second half of its own simple_LCD_isr.")
            self.emit("")
            self.emit("   `gbs_ocut_on` is a FLAG rather than a sentinel line, because the")
            self.emit("   feature's default is OFF and BSS zero would otherwise read as a")
            self.emit("   cut at line 0 (i.e. no window at all). `gbs_win_want` mirrors what")
            self.emit("   the PROGRAM asked for (video.show_window / hide_window,")
            self.emit("   text.to_window / to_bkg) exactly as gbs_spr_want does for sprites:")
            self.emit("   the V-blank restore below may only give back what was wanted. */")
            if not cut:
                self.emit("extern uint8_t gbs_spr_want;")
            self.emit("extern uint8_t gbs_win_want;")
            self.emit("uint8_t gbs_ocut_on;     /* a program wants the overlay cut */")
            self.emit("uint8_t gbs_ocut_line;   /* ...at this scanline */")
        self.emit("")
        self._emit_lyc_isr(px, cut, ocut)
        self._emit_lyc_vbl(px, cut, ocut)
        self._emit_lyc_arbiter(px, cut, ocut)

    # ---- where the ARBITER lives (W7d phase 2) --------------------------
    #
    # The two interrupt handlers above must stay in the always-mapped home
    # bank (they are NONBANKED and the hardware vectors into them), and the
    # state is WRAM, which is not banked at all. The THIRD part - wire once,
    # merge the stops, re-arm the register - runs when a feature ARMS OR
    # DISARMS, which is a room load or a box opening, never per frame. That is
    # exactly the `_prelude_data_bank` profile
    # established for the CGB fade engine, and the merge left the reference-engine sample conversion
    # with 45 B of resident spare on the Game Boy Color, so phase 2's overlay
    # cut has nowhere to go until this moves.
    #
    # A build that cannot bank (no banked-ROM support, or no `bank(N)` /
    # `[build] code_banks` / streamed data in the program, so the cart is
    # flat) keeps the original text CHARACTER FOR CHARACTER - the same rule
    # the merge itself follows for a one-tenant program.

    def _emit_lyc_arbiter(self, px, cut, ocut):
        """`gbs_lyc_wire` + `gbs_lyc_put` + `gbs_lyc_rebuild`, banked if it can.

        Only `gbs_lyc_wire` and `gbs_lyc_rebuild` cross the boundary (the two
        resident verbs `gbs_px_arm` and `gbs_text_win_cut` call them);
        `gbs_lyc_put` stays private to whichever TU holds the body. Nothing in
        a BANK TU calls either, so `_emit_prelude_gbdk_decls` needs no row -
        the arbiter's only callers are in the prelude itself."""
        want = bool(self.caps['has_banking'] and self.banking_active)
        # The size argument is a ROM-BYTE budget for the bank packer and the C
        # text is no guide to it. Conservative, as the fade engine's 1024 is:
        # over-stating only risks a bank the packer would otherwise have
        # shared, while under-stating silently over-packs one and the linker's
        # bank-overflow check is the only thing that would catch it.
        bank = (self._prelude_data_bank("gbs_lyc_rebuild", 512,
                                        self._lyc_bank_unit(px, cut, ocut))
                if want else 0)
        if not bank:
            for line in self._lyc_arbiter_body(px, cut, False, ocut):
                self.emit(line)
            return
        self.emit("/* The stop-list REBUILD lives in ROM bank %d (W7d phase 2)."
                  % bank)
        self.emit("   It runs when a feature arms or disarms - a room load, a box")
        self.emit("   opening - and never per frame, so the trampoline is paid where")
        self.emit("   the screen is already being rebuilt. The two handlers and the")
        self.emit("   state stay here: an interrupt vector cannot be banked and WRAM")
        self.emit("   is not banked at all, so there is still exactly ONE stop list. */")
        for line in self._lyc_arbiter_decls():
            self.emit(line)

    def _lyc_arbiter_decls(self):
        """The resident view of the arbiter: the two entry points.

        LOCKSTEP with `_lyc_arbiter_body`. The BANKED qualifier is read back
        off the PLACEMENT rather than passed in (the `_cgb_fade_decls` rule):
        a prototype that disagrees with its definition about BANKED is a call
        through a trampoline that was never set up."""
        b = " BANKED" if self.prelude_bank_defs.get("gbs_lyc_rebuild") else ""
        return ["void gbs_lyc_wire(void)%s;" % b,
                "void gbs_lyc_rebuild(void)%s;" % b]

    def _lyc_bank_unit(self, px, cut, ocut):
        """The whole bank translation unit's text for the arbiter.

        `_emit_data_bank_units` gives a bank TU only `<gbdk/platform.h>` and
        `<stdint.h>`, so this carries its own view of everything it touches:
        the action bits (the resident `#define`s are in the other TU) and an
        `extern` for each piece of state. The arrays are declared INCOMPLETE -
        the bank TU only ever indexes them, and spelling a size here would be
        a second place to keep `GBS_LYC_STOPS` right."""
        out = ["",
               "/* ==== The LYC stop-list ARBITER (W7d phase 2) ====",
               "   Banked out of the resident image: it runs on an arm or a disarm",
               "   (a room load, a box opening), never per frame. The handlers that",
               "   walk this list are NONBANKED and live in the main TU with the",
               "   state, which is WRAM and therefore one copy either way. */"]
        if px:
            out.append("#define GBS_LYC_BAND 0x%02X" % self.LYC_ACT_BAND)
            out.append("#define GBS_LYC_SCY  0x%02X" % self.LYC_ACT_SCY)
        if cut:
            out.append("#define GBS_LYC_HIDE 0x%02X" % self.LYC_ACT_HIDE)
        if ocut:
            out.append("#define GBS_LYC_WINOFF 0x%02X" % self.LYC_ACT_WINOFF)
        out.append("extern uint8_t gbs_lyc_line[];")
        out.append("extern uint8_t gbs_lyc_act[];")
        out.append("extern uint8_t gbs_lyc_n;")
        out.append("extern uint8_t gbs_lyc_k;")
        out.append("extern uint8_t gbs_lyc_wired;")
        out.append("extern uint8_t gbs_lyc_drop;")
        if px:
            out.append("extern uint8_t gbs_px_n;")
            out.append("extern uint8_t gbs_px_last[];")
        if cut:
            out.append("extern uint8_t gbs_cut_on;")
            out.append("extern uint8_t gbs_cut_line;")
        if ocut:
            out.append("extern uint8_t gbs_ocut_on;")
            out.append("extern uint8_t gbs_ocut_line;")
        # The two handlers are ADDRESS-TAKEN here (add_LCD / add_VBL) and they
        # are NONBANKED, so the address is a plain home-bank one.
        out.append("void gbs_lyc_isr(void) NONBANKED;")
        out.append("void gbs_lyc_vbl(void) NONBANKED;")
        out.extend(self._lyc_arbiter_body(px, cut, True, ocut))
        return "\n".join(out)

    def _lyc_arbiter_body(self, px, cut, banked, ocut=False):
        """The arbiter itself, as LINES.

        `banked` qualifies the two entry points and drops their `static`; with
        it False this is character for character the text that shipped with
        the merge, which is what keeps a flat-cart program byte-identical."""
        out = []
        self._emit_lyc_wire(out.append, banked)
        self._emit_lyc_rebuild(out.append, px, cut, banked, ocut)
        return out

    def _emit_lyc_isr(self, px, cut, ocut=False):
        """The one LCD handler, as NAKED assembly - the reference's own shape.

        THE DEADLINE IS THIS LINE'S H-BLANK. The handler fires at a band
        boundary and its SCX write must land in the H-blank of that line, or
        the band starts one line late - a visible thin flicker at the horizon.
        A C body pays sdcc's prologue on top of GBDK's chained dispatcher, and
        MEASURED that left the write short on ~0.5% of frames. The reference VM's
        `parallax_LCD_isr` (the reference VM's src/core/parallax.c) is `NONBANKED NAKED` for
        exactly this reason. Note what it does NOT do: it still installs
        through `add_LCD`, so the dispatcher is not bypassed here either - the
        saving is the C prologue alone.

        TWO THINGS WERE MEASURED ON THE WAY IN, and both are why this is
        shaped as it is:

          * A STRAIGHT PORT IS NOT ENOUGH. Written with the LYC re-arm still
            ahead of the H-blank spin - where the C version had it - the
            handler was SLOWER than the C it replaced: the lab's bands moved at
            lines 49 and 97 instead of 48 and 96, and the reference-engine sample conversion's slip went
            0.5% -> 100%. Those ~20 cycles are the whole margin. The re-arm is
            for the NEXT stop, so it is done AFTER the write, which puts the
            lab back on 48 and 96.
          * THEREFORE ONLY `B` SURVIVES THE ACTIONS. With the re-arm at the end,
            anything it needs has to outlive the action blocks, and there are
            not enough registers for three values. So it re-reads `gbs_lyc_n`
            and `gbs_lyc_k` from memory (8 cycles, off the critical path) and
            the actions get A, C, D, E and HL to themselves. Carrying the count
            in C instead was tried: the band action reused C, the re-arm then
            compared a scroll value against 2 and armed LYC from garbage, and
            the sprite cut never lifted (caught by the lab, phase [C]).

        THE PRICE OF DOING THE RE-ARM LAST: the next stop must be at least two
        lines below this one, because the spin can hold us into the following
        line. Band stops are a tile row apart by construction and the box and
        overlay cuts sit 8 lines off the nearest band in every conversion, so
        nothing on disk is near it - but it is a real constraint and the
        rebuild does not enforce it yet.

        THE REGISTER CONTRACT is the reference's: the dispatcher preserves
        AF/DE/HL across a handler (the reference VM's clobbers all three freely). BC is
        pushed here because this one uses it and the reference VM's does not, so it is not
        covered by that precedent.

        Bit numbers, not masks (`res 1, a` for LCDCF_OBJON, `res 5, a` for
        LCDCF_WINON): one instruction instead of a load-and-mask, and no
        literal to get wrong.
        """
        self.emit('void gbs_lyc_isr(void) NONBANKED NAKED {')
        self.emit('__asm')
        self.emit('        ; An empty list must stand down completely rather than replay')
        self.emit("        ; the previous room's stops - and this is the ONLY stand-down a")
        self.emit('        ; disarm has, because the LYC enable bit is never cleared (the')
        self.emit('        ; DMG STAT-write bug, see gbs_lyc_wire).')
        self.emit('        ld a, (#_gbs_lyc_n)')
        self.emit('        or a')
        self.emit('        ret z')
        self.emit('        push bc')
        self.emit('')
        self.emit('        ld a, (#_gbs_lyc_k)')
        self.emit('        ld e, a')
        self.emit('        ld d, #0')
        self.emit('        ld hl, #_gbs_lyc_act')
        self.emit('        add hl, de')
        self.emit("        ld b, (hl)              ; B = this stop's action byte.")
        self.emit('                                ; B, and nothing else, survives below.')
        if cut:
            self.emit('')
            self.emit("        ; HIDE_SPRITES - the box's window cut.")
            self.emit('        bit 4, b                ; GBS_LYC_HIDE')
            self.emit('        jr z, 3$')
            self.emit('        ldh a, (#_LCDC_REG)')
            self.emit('        res 1, a                ; LCDCF_OBJON')
            self.emit('        ldh (#_LCDC_REG), a')
            self.emit('3$:')
        if ocut:
            self.emit('')
            self.emit('        ; The OVERLAY CUT: window off, sprites back. In H-BLANK, or the')
            self.emit('        ; LCDC write tears the line it lands on. AFTER the hide on purpose -')
            self.emit("        ; a line that does both ends with sprites ON, which is what the reference VM's")
            self.emit('        ; simple_LCD_isr does.')
            self.emit('        bit 5, b                ; GBS_LYC_WINOFF')
            self.emit('        jr z, 5$')
            self.emit('4$:')
            self.emit('        ldh a, (#_STAT_REG)')
            self.emit('        bit STATF_B_BUSY, a')
            self.emit('        jr nz, 4$')
            self.emit('        ldh a, (#_LCDC_REG)')
            self.emit('        res 5, a                ; LCDCF_WINON')
            self.emit('        ldh (#_LCDC_REG), a')
            self.emit('        ld a, (#_gbs_spr_want)')
            self.emit('        or a')
            self.emit('        jr z, 5$')
            self.emit('        ldh a, (#_LCDC_REG)')
            self.emit('        set 1, a                ; LCDCF_OBJON')
            self.emit('        ldh (#_LCDC_REG), a')
            self.emit('5$:')
        if px:
            self.emit('')
            self.emit("        ; The band's scroll, computed BEFORE the spin and written inside")
            self.emit("        ; it - the reference engine's parallax asm spins the same way. Only the LAST")
            self.emit('        ; band carries the vertical scroll: an upper band shows the top of')
            self.emit("        ; the map whatever the camera's y is.")
            self.emit('        bit 2, b                ; GBS_LYC_BAND')
            self.emit('        jr z, 9$')
            self.emit('        ld a, b')
            self.emit('        and #3                  ; the band index is in the low bits')
            self.emit('        ld e, a')
            self.emit('        ld d, #0')
            self.emit('        ld hl, #_gbs_px_livex')
            self.emit('        add hl, de')
            self.emit("        ld c, (hl)              ; C = this band's SCX")
            self.emit('        ld d, #0')
            self.emit('        bit 3, b                ; GBS_LYC_SCY')
            self.emit('        jr z, 7$')
            self.emit('        ld a, (#_gbs_px_livey)')
            self.emit('        ld d, a')
            self.emit('7$:')
            self.emit('        ldh a, (#_STAT_REG)')
            self.emit('        bit STATF_B_BUSY, a')
            self.emit('        jr nz, 7$')
            self.emit('        ld a, c')
            self.emit('        ldh (#_SCX_REG), a')
            self.emit('        ld a, d')
            self.emit('        ldh (#_SCY_REG), a')
            self.emit('9$:')
        self.emit('')
        self.emit('        ; Re-arm for the NEXT stop, LAST. `gbs_lyc_n` and `gbs_lyc_k` are')
        self.emit('        ; re-read rather than carried: with the actions above between, there')
        self.emit('        ; are not enough registers to hold three values, and these two loads')
        self.emit('        ; are off the H-blank deadline. With a single stop there is no next')
        self.emit('        ; one and LYC already holds it.')
        self.emit('        ld a, (#_gbs_lyc_n)')
        self.emit('        ld c, a')
        self.emit('        cp #2')
        self.emit('        jr c, 2$')
        self.emit('        ld a, (#_gbs_lyc_k)')
        self.emit('        inc a')
        self.emit('        cp c')
        self.emit('        jr c, 1$')
        self.emit('        xor a                   ; past the last stop: wrap')
        self.emit('1$:')
        self.emit('        ld (#_gbs_lyc_k), a')
        self.emit('        ld e, a')
        self.emit('        ld d, #0')
        self.emit('        ld hl, #_gbs_lyc_line')
        self.emit('        add hl, de')
        self.emit('        ld a, (hl)')
        self.emit('        ldh (#_LYC_REG), a')
        self.emit('2$:')
        self.emit('        pop bc')
        self.emit('        ret')
        self.emit('__endasm;')
        self.emit('}')

    def _emit_lyc_vbl(self, px, cut, ocut=False):
        self.emit("void gbs_lyc_vbl(void) NONBANKED {")
        if px:
            self.emit("    uint8_t i;")
        if ocut:
            # NOT the `cut`-only wording below, and not by accident: with the
            # overlay cut in the program the ISR does give sprites back (at the
            # cut line), so the claim "it only ever takes them away" would be
            # false here. The RESTORE's reason is the same either way.
            self.emit("    /* Restore what the PROGRAM asked for, every frame and whether or")
            self.emit("       not a cut is armed: the ISR may take sprites away at a box's")
            self.emit("       line and give them back at the overlay cut, and neither of")
            self.emit("       those may outlive the frame it happened in. */")
            self.emit("    if (gbs_spr_want) SHOW_SPRITES;")
            self.emit("    /* ...and the window layer, with the same ceiling: the overlay cut")
            self.emit("       only ever turns it OFF, so a program that put its own window")
            self.emit("       away keeps it away. */")
            self.emit("    if (gbs_win_want) SHOW_WIN;")
        elif cut:
            self.emit("    /* Restore what the PROGRAM asked for, every frame and whether or")
            self.emit("       not a cut is armed - the ISR only ever takes sprites away. */")
            self.emit("    if (gbs_spr_want) SHOW_SPRITES;")
        self.emit("    /* Apply a disarm the game loop DEFERRED, now that no line is being")
        self.emit("       drawn (see gbs_lyc_rebuild). No banked call is needed for it: an")
        self.emit("       empty list is the one shape the rebuild does not have to compute. */")
        self.emit("    if (gbs_lyc_drop) {")
        self.emit("        gbs_lyc_drop = 0;")
        self.emit("        gbs_lyc_n = 0;")
        self.emit("    }")
        self.emit("    if (!gbs_lyc_n) return;")
        if px:
            self.emit("    if (gbs_px_n) {")
            self.emit("        /* Commit the shadow ONCE per frame, and only a COMPLETE one")
            self.emit("           (gbs_px_ready): a half-written shadow keeps last frame's")
            self.emit("           live values, which are at least consistent with each other. */")
            self.emit("        if (gbs_px_ready) {")
            self.emit("            for (i = 0; i < GBS_PX_BANDS; ++i) gbs_px_livex[i] = gbs_px_shx[i];")
            self.emit("            gbs_px_livey = gbs_px_shy;")
            self.emit("            gbs_px_ready = 0;")
            self.emit("        }")
            self.emit("        /* Band 0's own stop is at LY = 0 and its write has to wait for")
            self.emit("           H-blank - which is the END of line 0, one line too late.")
            self.emit("           Hardware hides that (LY reads 0 for most of line 153, so the")
            self.emit("           coincidence fires in V-blank), but nothing else does: on a")
            self.emit("           scanline emulator the top row kept the LAST band's scroll and")
            self.emit("           flickered along with the playfield. Commit band 0 here, in")
            self.emit("           V-blank, where no line is being drawn. */")
            self.emit("        SCX_REG = gbs_px_livex[0];")
            self.emit("        SCY_REG = gbs_px_last[0] ? 0 : gbs_px_livey;")
            self.emit("    }")
        self.emit("    /* Re-seed the walk at the first stop. The list heals itself anyway")
        self.emit("       (it always reaches the wrap), but an interrupt missed under a long")
        self.emit("       CRITICAL section would otherwise show one wrong frame. */")
        self.emit("    gbs_lyc_k = 0;")
        self.emit("    LYC_REG = gbs_lyc_line[0];")
        self.emit("}")

    def _emit_solo_lyc_source(self, pad):
        """The ONE `STAT_REG` write a SOLE `LYC_REG` tenant makes.

        The solo emitters (`_emit_gbdk_parallax_solo`,
        `_emit_win_sprite_cut_solo`, `_emit_win_overlay_cut_solo`) used to set
        `STATF_LYC` on every arm and clear it on every disarm - which on a
        MONOCHROME Game Boy can raise a spurious LCD interrupt each time (Pan
        Docs `STAT.md`, "Spurious STAT interrupts"; the CGB does not have the
        quirk), and the tenant's handler then acted at whatever line the beam
        was on. The same defect as phase 3e of the merge, which
        `gbs_lyc_wire` fixed for the merged handler.

        The same fix, and the reference VM's own shape (`core.c` sets `STATF_LYC` once at
        boot and never clears it): enable the source ONCE, inside the wire's
        CRITICAL block so the request that write may raise is dropped before
        it can be taken, and let each handler stand down on its own flag. A
        disarm then writes no register at all. The price is that the handler
        is ENTERED once a frame whether or not it is armed, as the reference VM's is.
        """
        for ln in (
                "/* LYC is switched on HERE, once, and STAT is never written again:",
                "   on the DMG a write to STAT while the LCD is on can raise a",
                "   SPURIOUS LCD interrupt (fixed on the CGB), and the handler would",
                "   then act at whatever line the beam is on. Arming and disarming",
                "   move LYC_REG and the handler's own flag instead. Interrupts are",
                "   off here, so the request this one write may raise is dropped",
                "   before it can be taken. */",
                "STAT_REG |= STATF_LYC;",
                "IF_REG &= ~LCD_IFLAG;"):
            self.emit(pad + ln)

    def _emit_lyc_wire(self, emit, banked=False):
        emit("/* Installed ONCE, on the first arm of any feature. GBDK's add_LCD")
        emit("   chains and cannot be undone, which is the whole reason there is one")
        emit("   handler rather than one per feature. */")
        emit("%svoid gbs_lyc_wire(void)%s {"
             % ("" if banked else "static ", " BANKED" if banked else ""))
        emit("    if (gbs_lyc_wired) return;")
        emit("    CRITICAL {")
        emit("        add_LCD(gbs_lyc_isr);")
        emit("        add_VBL(gbs_lyc_vbl);")
        emit("    }")
        # OR into the LIVE mask rather than assigning it: another feature may
        # already own an interrupt (native.huge drives hUGEDriver off the
        # TIMER), and assigning would turn it off the moment a box opened -
        # which for the music driver means the song stops dead mid-dialogue.
        emit("    set_interrupts(IE_REG | VBL_IFLAG | LCD_IFLAG);")
        emit("    /* THE ONLY WRITE TO STAT_REG IN THE PROGRAM, and it happens ONCE.")
        emit("       On the DMG a read-modify-write of STAT while the LCD is on raises a")
        emit("       SPURIOUS STAT interrupt (the hardware momentarily reads every enable")
        emit("       bit as set - the DMG STAT-write bug, fixed on the CGB). This handler")
        emit("       would then run at whatever line the beam happened to be on and")
        emit("       perform the stop the cursor names EARLY, so the band below drew from")
        emit("       there instead of from its own boundary: a strip of the picture at")
        emit("       the wrong scroll, on the Game Boy only. `gbs_lyc_rebuild` used to do")
        emit("       this write on every box open, box close and room change.")
        emit("")
        emit("       So the source is enabled here and NEVER touched again. Disarming")
        emit("       needs no register: the ISR's own `if (!gbs_lyc_n) return;` is the")
        emit("       stand-down, which also makes phase 1's defect 2 - a box close killing")
        emit("       the band chain - unexpressible by construction rather than by care.")
        emit("       The one spurious interrupt this write may still raise is dropped on")
        emit("       the next line. */")
        emit("    STAT_REG |= STATF_LYC;")
        emit("    IF_REG &= ~LCD_IFLAG;")
        emit("    gbs_lyc_wired = 1;")
        emit("}")

    def _emit_lyc_rebuild(self, emit, px, cut, banked=False, ocut=False):
        emit("/* Append one stop. ONE store site for all three callers below:")
        emit("   sdcc-sm83 addresses each array from scratch, so spelling the pair")
        emit("   out three times is most of what this function costs. */")
        emit("static void gbs_lyc_put(uint8_t line, uint8_t act) {")
        emit("    gbs_lyc_line[gbs_lyc_n] = line;")
        emit("    gbs_lyc_act[gbs_lyc_n] = act;")
        emit("    ++gbs_lyc_n;")
        emit("}")
        emit("/* Merge every armed feature's stops into one list in SCANLINE order.")
        emit("   Runs when a feature arms or disarms - a room load, a box opening -")
        emit("   never per frame, and under CRITICAL because the ISR walks the list")
        emit("   it rewrites (the cursor IS gbs_lyc_n, so a half-built list is only")
        emit("   ever visible with interrupts off). Clearing STATF_LYC is the ONE")
        emit("   place it can happen, and only when nothing at all is armed: a")
        emit("   feature that disarms while another is live simply contributes no")
        emit("   stop. That is what makes the box-closing defect - a dead band")
        emit("   chain - unexpressible. */")
        emit("%svoid gbs_lyc_rebuild(void)%s {"
             % ("" if banked else "static ", " BANKED" if banked else ""))
        # ONE stop source per tenant, and the third (the overlay cut) is what
        # turns the box cut's single `pend` into a little SORTED LIST. Kept
        # apart rather than generalised: with no overlay cut in the program
        # this emits the text that shipped with the merge, character for
        # character, so every two-tenant program that predates phase 2 is
        # byte-identical to it.
        nex = (1 if cut else 0) + (1 if ocut else 0)
        # The cursor the tail re-arms with, and the beam it compares against.
        # Declared here rather than inside the CRITICAL block because sdcc is
        # compiling C89 and a declaration after a statement is not one.
        emit("    uint8_t nk, ly, done;")
        if px:
            emit("    uint8_t i, ln, act;")
        if px and cut and not ocut:
            emit("    uint8_t pend = gbs_cut_on;   /* the cut stop, not yet placed */")
        if ocut:
            emit("    /* The stops that are NOT a band - the box's sprite cut and the")
            emit("       overlay cut - gathered in scanline order first, then placed")
            emit("       against the bands as the walk below reaches each one. */")
            emit("    uint8_t ex_line[%d], ex_act[%d], ex_n, ex_i;" % (nex, nex))
            if cut:
                emit("    uint8_t ex_t;")
        emit("    /* A DISARM MAY NOT KILL THE FRAME BEING DRAWN. Every tenant has")
        emit("       stood down, so the list about to be built is EMPTY - and the empty")
        emit("       arm below clears STATF_LYC, which stops the interrupt for the REST")
        emit("       OF THIS FRAME. A room change does exactly that from the game loop:")
        emit("       MEASURED on the platformer conversion's cutscene, `gbs_px_arm(0)` ran at LY 46 with the")
        emit("       screen still VISIBLE and the stop at line 95 never fired, so lines")
        emit("       96..143 - the whole ground band - drew at the SKY's scroll for that")
        emit("       frame. (On the reference-engine sample conversion the same disarm usually lands after the fade")
        emit("       has whitened the screen, which is why it reads as harmless there.)")
        emit("")
        emit("       So keep the CURRENT list alive to the end of the frame - its stops")
        emit("       write the scroll values this frame is being drawn with, which is")
        emit("       exactly right - and let V-blank do the emptying, where no line is")
        emit("       being drawn. With the LCD off there is no frame to protect and the")
        emit("       immediate path is both correct and cheaper. */")
        # ONE term per tenant, from the flags this program actually has - the
        # same "count, do not spell the pair out" rule `_lyc_used` follows, so
        # a fourth tenant joins by contributing its flag here.
        quiet = []
        if px:
            quiet.append("!gbs_px_n")
        if cut:
            quiet.append("!gbs_cut_on")
        if ocut:
            quiet.append("!gbs_ocut_on")
        emit("    if (gbs_lyc_n && %s && (LCDC_REG & LCDCF_ON)"
             % " && ".join(quiet))
        # 144 spelled out, not SCREEN_HEIGHT: a BANK TU is given only
        # <gbdk/platform.h> and <stdint.h>, so gb.h's names are not in scope
        # there (the build fails with "Undefined identifier", which is how
        # this was found).
        emit("            && LY_REG < 144) {   /* 144 = the visible height */")
        emit("        gbs_lyc_drop = 1;")
        emit("        return;")
        emit("    }")
        emit("    gbs_lyc_drop = 0;")
        emit("    CRITICAL {")
        emit("        gbs_lyc_n = 0;")
        if ocut:
            emit("        ex_n = 0;")
            emit("        ex_i = 0;")
            if cut:
                emit("        if (gbs_cut_on) {")
                emit("            ex_line[ex_n] = gbs_cut_line;")
                emit("            ex_act[ex_n] = GBS_LYC_HIDE;")
                emit("            ++ex_n;")
                emit("        }")
            emit("        if (gbs_ocut_on) {")
            emit("            ex_line[ex_n] = gbs_ocut_line;")
            emit("            ex_act[ex_n] = GBS_LYC_WINOFF;")
            emit("            ++ex_n;")
            emit("        }")
            if cut:
                emit("        if (ex_n == 2) {")
                emit("            /* Two entries, so ONE compare sorts them - and two on the")
                emit("               SAME line MERGE into one, because a second entry at a")
                emit("               line already passed would arm LYC behind the beam. The")
                emit("               ISR tests WINOFF after HIDE, so a merged line still ends")
                emit("               with the sprites given back, as the reference VM's does. */")
                emit("            if (ex_line[1] < ex_line[0]) {")
                emit("                ex_t = ex_line[0]; ex_line[0] = ex_line[1]; ex_line[1] = ex_t;")
                emit("                ex_t = ex_act[0];  ex_act[0]  = ex_act[1];  ex_act[1]  = ex_t;")
                emit("            } else if (ex_line[1] == ex_line[0]) {")
                emit("                ex_act[0] |= ex_act[1];")
                emit("                ex_n = 1;")
                emit("            }")
                emit("        }")
        if px:
            emit("        for (i = 0; i < gbs_px_n; ++i) {")
            emit("            /* Band i BEGINS where band i-1 ended (band 0 at line 0),")
            emit("               and the band whose own end is 0 is the last - it is the")
            emit("               one that carries SCY, and its 0 is what wraps the walk.")
            emit("               the reference engine's encoding (parallax.h: next_y = (end << 3) - 1,")
            emit("               0 last). */")
            emit("            ln = i ? gbs_px_last[i - 1] : 0;")
            emit("            act = (uint8_t)(GBS_LYC_BAND | i);")
            emit("            if (!gbs_px_last[i]) act |= GBS_LYC_SCY;")
            if ocut:
                emit("            while (ex_i < ex_n && ex_line[ex_i] <= ln) {")
                emit("                /* Same line: ONE stop does both, or the second would")
                emit("                   arm LYC at a line the beam has already passed. */")
                emit("                if (ex_line[ex_i] == ln) act |= ex_act[ex_i];")
                emit("                else gbs_lyc_put(ex_line[ex_i], ex_act[ex_i]);")
                emit("                ++ex_i;")
                emit("            }")
            elif cut:
                emit("            if (pend && gbs_cut_line <= ln) {")
                emit("                /* Same line: ONE stop does both, or the second would")
                emit("                   arm LYC at a line the beam has already passed. */")
                emit("                if (gbs_cut_line == ln) act |= GBS_LYC_HIDE;")
                emit("                else gbs_lyc_put(gbs_cut_line, GBS_LYC_HIDE);")
                emit("                pend = 0;")
                emit("            }")
            emit("            gbs_lyc_put(ln, act);")
            emit("        }")
        if ocut:
            emit("        while (ex_i < ex_n) {")
            emit("            gbs_lyc_put(ex_line[ex_i], ex_act[ex_i]);")
            emit("            ++ex_i;")
            emit("        }")
        elif cut:
            emit("        if (%s) gbs_lyc_put(gbs_cut_line, GBS_LYC_HIDE);"
                      % ("pend" if px else "gbs_cut_on"))
        emit("        /* RE-ARM AT THE NEXT STOP BELOW THE BEAM, never at the first")
        emit("           one. A rebuild runs from the GAME LOOP - a box opening is the")
        emit("           case that matters - so the beam is normally partway down a")
        emit("           visible frame, and arming stop 0 points LYC at a line that has")
        emit("           already gone by: every stop below the beam is then skipped for")
        emit("           the REST OF THAT FRAME, and each band under one renders at the")
        emit("           scroll of the band above it. Measured on the platformer conversion's")
        emit("           opening cutscene, whose lower band is FIXED: on the frame a box")
        emit("           opened, the band drew at the half-speed band's SCX for its whole")
        emit("           height, sliding map columns in from the right (reported from")
        emit("           play as the ground scrolling with the sky and overlapping it).")
        emit("           Walking forward to the first stop past LY keeps the rest of the")
        emit("           frame; past the last stop there is nothing left to serve this")
        emit("           frame and stop 0 is right, which is what V-blank re-seeds anyway.")
        emit("           `gbs_lyc_k` is the stop that fires NEXT, so it and LYC_REG move")
        emit("           together here exactly as they do in the ISR. */")
        emit("        done = 0;")
        emit("        if (gbs_lyc_n) {")
        emit("            /* A COINCIDENCE MAY ALREADY BE WAITING. This block runs with")
        emit("               interrupts off, so the LYC match that LYC_REG still names can")
        emit("               have been raised and not yet served - and the handler that")
        emit("               finally takes it reads `gbs_lyc_k`. Move the cursor and it")
        emit("               performs a stop the beam is nowhere near. MEASURED on")
        emit("               the reference-engine sample conversion's parallax room (Game Boy, A mashed at the")
        emit("               sign): a rebuild entered at LY 151 finished after the beam had")
        emit("               wrapped, read LY as 0, armed stop 1 - and the stop-0 request")
        emit("               pending from LY 0 was served at LY 1, writing BAND 1's scroll")
        emit("               across the whole top band for that frame.")
        emit("")
        emit("               So point the cursor at the stop the HARDWARE matched - the one")
        emit("               on the line LYC_REG still holds - and leave the register alone.")
        emit("               Its INDEX may have moved (removing the box's cut stop shifts")
        emit("               every later one down), which is why this searches by LINE.")
        emit("               Simply dropping the request instead was tried and is worse: the")
        emit("               frame then gets no stop at all and every band below the rebuild")
        emit("               draws at band 0's scroll. Dropping is right in ONE case, the")
        emit("               one below - the stop it was raised for no longer exists. */")
        emit("            if (IF_REG & LCD_IFLAG) {")
        emit("                ly = LYC_REG;")
        emit("                nk = 0;")
        emit("                while (nk < gbs_lyc_n && gbs_lyc_line[nk] != ly) ++nk;")
        emit("                if (nk < gbs_lyc_n) {")
        emit("                    gbs_lyc_k = nk;")
        emit("                    done = 1;")
        emit("                } else {")
        emit("                    IF_REG &= ~LCD_IFLAG;")
        emit("                }")
        emit("            }")
        emit("            if (!done) {")
        emit("                /* RE-ARM AT THE NEXT STOP BELOW THE BEAM, never at the first")
        emit("                   one. A rebuild runs from the GAME LOOP - a box opening is")
        emit("                   the case that matters - so the beam is normally partway")
        emit("                   down a visible frame, and arming stop 0 points LYC at a")
        emit("                   line that has already gone by: every stop below the beam")
        emit("                   is then skipped for the REST OF THAT FRAME, and each band")
        emit("                   under one renders at the scroll of the band above it.")
        emit("                   Measured on the platformer conversion's opening cutscene, whose lower band")
        emit("                   is FIXED: on the frame a box opened, the band drew at the")
        emit("                   half-speed band's SCX for its whole height. Past the last")
        emit("                   stop there is nothing left to serve this frame and stop 0")
        emit("                   is right, which is what V-blank re-seeds anyway. */")
        emit("                nk = 0;")
        emit("                ly = LY_REG;")
        emit("                while (nk < gbs_lyc_n && gbs_lyc_line[nk] <= ly) ++nk;")
        emit("                if (nk >= gbs_lyc_n) nk = 0;")
        emit("                gbs_lyc_k = nk;")
        emit("                LYC_REG = gbs_lyc_line[nk];")
        emit("            }")
        emit("        } else {")
        emit("            gbs_lyc_k = 0;")
        emit("        }")
        emit("    }")
        emit("}")
