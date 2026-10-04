"""graphics.palette on the GBDK backend: the colour prelude and the two
palette-SCALING fades (the CGB class ignores BGP/OBP, the SMS/GG pair has
only CRAM), plus the two "is gbs_rgb still needed" tests the prelude asks.

A GbdkBackend concern mixin - methods run against the full
CodeGenerator instance (self.emit, self.caps, ...)."""


def _cgb_mirror_args(faded):
    """The four colour expressions the Analogue Pocket's DMG-register mirror
    reads: the dimmed staging buffer once palette.fade is in play, the raw
    arguments otherwise (which is what it always emitted)."""
    if faded:
        return ["buf[%d]" % i for i in range(4)]
    return ["c%d" % i for i in range(4)]


class GbdkPaletteMixin:
    def _gbs_rgb_needed(self):
        """Whether the palette prelude still has a caller for gbs_rgb.

        Only asked on the Game Boy Color, where the per-scene SET loaders take
        the direct hardware path (see _emit_palette_set_real) and nothing else
        in the block converts a colour. A prelude helper is RESIDENT, so an
        uncalled one is pure bank-0 rent."""
        return (self.palette_rgb_used or self.load_bkg16_used
                or not self._palette_set_direct())

    def _sms_rgb_needed(self):
        """Whether SMS/GG still need the resident gbs_rgb (with its three
        divisions, ~275 B of bank 0). Everything legacy keeps it; only a
        program whose palette use is purely the raw `palette.set_entry` path
        (the generated banked scenes.load_palettes, which converts colours
        itself off a table) drops it -- so every existing program's output is
        byte-identical."""
        return (self.palette_rgb_used or self.load_bkg16_used
                or self.palette_set_used or self.palette_load_used
                or self.palette_setter_used or not self.pal_native_used)

    def _emit_cgb_fade(self):
        """Emit the CGB colour fade, resident or banked.

        The engine itself is `_cgb_fade_body`; this decides where it
        goes and what the resident side sees of it."""
        # THE WHOLE FAMILY LEAVES THE RESIDENT IMAGE WHERE IT CAN (review
        # E-13, 2026-09-06). It is ~400 B that only the CGB class carries, on
        # the one target with no headroom, and nothing in it is per-frame: the
        # fade runs on a level CHANGE (three times a transition) and gbs_pal_hw
        # on a palette LOAD. The state is RAM, so it goes with the code and
        # stays one copy - `#pragma bank` places CODE only.
        #
        # gbs_pal_dim / gbs_pal_re / gbs_pal_t3 / gbs_pal_b4 stay PRIVATE to
        # that translation unit; only gbs_pal_hw and gbs_pal_fade are reached
        # from the resident side (the two setters, gbs_pal_set, the boot seed
        # and the fade seam), so only those two pay a trampoline.
        want = bool(self.caps['has_banking'] and self.banking_active)
        text = self._cgb_fade_body(want)
        # The size argument is a ROM-BYTE budget for the bank packer, and this
        # is the first prelude entry that is CODE - the C text is no guide to
        # it (5.8 kB of source, ~700 B compiled). A conservative 1 KB: too
        # small silently over-packs a bank, and the linker's own bank-overflow
        # check is the only thing that would catch it.
        bank = (self._prelude_data_bank("gbs_pal_fade", 1024, text)
                if want else 0)
        if not bank:
            # Declined (no banking in this build): emit it resident, exactly as
            # before, so a non-banking program stays byte-identical.
            for line in self._cgb_fade_body(False).split("\n"):
                self.emit(line)
            return
        self.emit("/* palette.fade + the CGB fade engine live in ROM bank %d" % bank)
        self.emit("   (review E-13): ~400 B of resident image that only the CGB")
        self.emit("   class carries, and none of it runs per frame. */")
        for line in self._cgb_fade_decls():
            self.emit(line)

    def _cgb_fade_decls(self):
        """The resident view of the fade engine: its two entry points.

        LOCKSTEP with `_cgb_fade_body` and with `_emit_prelude_gbdk_decls`,
        which hands the same prototypes to every bank translation unit. The
        BANKED qualifier is read back off the placement rather than passed in,
        because the two call sites decide at different moments and a
        disagreement between a prototype and its definition is a link error at
        best (`prelude_bank_defs` is final before either runs)."""
        b = " BANKED" if self.prelude_bank_defs.get("gbs_pal_fade") else ""
        return ["void gbs_pal_hw(uint8_t spr, uint8_t slot, uint8_t count,",
                "                palette_color_t *buf)%s;" % b,
                "void gbs_pal_fade(uint8_t level)%s;" % b]

    def _cgb_fade_body(self, banked):
        """`palette.fade(level)` on the CGB class -- the COLOUR fade.

        A DMG fade ramps BGP/OBP0/OBP1, and **the hardware IGNORES those three
        registers in CGB mode**, so on a Game Boy Color the classic fade is a
        complete no-op in both directions (a hard cut between rooms). The only
        way to darken a coloured screen is to scale the loaded palettes, which
        is what the reference engine's CGBFadeToBlackStep does.

        A RAM SHADOW holds what the program asked for, so a palette LOADED
        while the screen is dark is written dimmed and the ramp back is exact.
        That is what lets `load_room` black out, paint, and ramp in without the
        paint undoing the black-out: applying the level once at the start would
        be overwritten by the load. Writing THROUGH the level means the paint
        order inside load_room does not matter.

        A slot the program never loaded is never touched: an all-zero shadow
        entry is BLACK, so fading an untouched slot would darken a screen that
        (with no palette loads at all) the fade cannot legitimately reach.

        128 B of WRAM, which is free on this console, and one 5-bit scale per
        channel per loaded entry -- paid on a LEVEL CHANGE only (the FADE
        opcode calls set_level every frame and the level moves 3 times).

        Returned as TEXT, because on a banking build the whole thing is placed
        in a ROM bank (see the caller): `banked` qualifies the two entry points
        the resident side calls."""
        out = []
        e = out.append
        b = " BANKED" if banked else ""
        # The fade DIRECTION (the reference engine's `fade_style`, whose default is
        # WHITE): bit 7 of the level asks for the scale towards 0x7FFF instead
        # of 0. Emitted only on a build that states VM_FADE_STYLE (a VM8 game
        # whose blob writes the state or whose rooms.mos declares a default),
        # so every other coloured game keeps this text character for
        # character. Without it bit 7 reads as "> 3" and clamps to black.
        white = bool(self.defines.get('VM_FADE_STYLE'))
        e("/* palette.fade: the CGB colour fade. BGP/OBP0/OBP1 -- the registers a")
        e("   DMG fade ramps -- are IGNORED in CGB mode, so a coloured screen can")
        e("   only be darkened by scaling the loaded palettes (the reference engine's")
        e("   CGBFadeToBlackStep). The shadow holds what the program asked for, so")
        e("   a palette LOADED while the screen is dark is written dimmed and the")
        e("   ramp back is exact -- which is what lets load_room black out, paint,")
        e("   and ramp in without the load undoing the fade. */")
        e("static palette_color_t gbs_pal_sh[64];  /* 8 bkg slots x 4, then 8 spr */")
        e("static uint8_t gbs_pal_msk[2];          /* slots actually loaded, per layer */")
        e("static uint8_t gbs_fade_lvl;            /* 0 normal .. 3 black */")
        if white:
            e("static uint8_t gbs_fade_white;          /* 1 = the ramp goes to WHITE */")
        # The scale is x*n/3 over a 5-bit channel with n = 3 - level in {1,2},
        # i.e. a LOOKUP of x/3 over 0..62 indexed by x << (n-1). sm83 has
        # neither a multiply nor a divide, and although the __muluint +
        # __divuint form is ~24 B smaller (both helpers are already linked by a
        # VM8 game), it is several times SLOWER -- and this runs 3x for every
        # entry of every loaded palette, twice per fade: with the divides a
        # single darkness step took ~12 LCD frames on the reference-engine import
        # rather than the authored hold, stretching one transition to ~1.8 s.
        # Not a per-frame path, but a visible one.
        #
        # The three channels are a LOOP, not three inlined expressions: this
        # was resident prelude code, and it still is on a build that cannot
        # bank it.
        e("/* x/3 for a doubled 5-bit channel (n in {1,2}: index x << (n-1)). */")
        e("static const uint8_t gbs_pal_t3[64] = {")
        for row in range(4):
            e("    " + ", ".join("%2d" % (i // 3)
                                 for i in range(row * 16, row * 16 + 16))
              + ("," if row < 3 else ""))
        e("};")
        e("static uint16_t gbs_pal_dim(uint16_t c) {")
        e("    uint8_t k, sh;")
        e("    uint16_t o = 0;")
        e("    if (gbs_fade_lvl == 0) return c;")
        if white:
            # Towards white: x + (31 - x) * level / 3 per channel, the same
            # x/3 table over the channel's DISTANCE to full (the reference engine's
            # CGBFadeToWhiteStep sets bits towards 0x7FFF; a proportional
            # scale is the mirror of the black arm below, so the two ramps
            # are the same shape either way).
            e("    if (gbs_fade_white) {")
            e("        uint8_t x;")
            e("        if (gbs_fade_lvl == 3) return 0x7FFF;   /* the whole white load */")
            e("        sh = (uint8_t)(gbs_fade_lvl == 2 ? 1 : 0);   /* n = level */")
            e("        for (k = 0; k < 15; k += 5) {")
            e("            x = (uint8_t)((c >> k) & 0x1F);")
            e("            o |= (uint16_t)((uint16_t)(x + gbs_pal_t3[(uint8_t)(31 - x) << sh]) << k);")
            e("        }")
            e("        return o;")
            e("    }")
        e("    if (gbs_fade_lvl == 3) return 0;   /* the whole black room load */")
        e("    sh = (uint8_t)(gbs_fade_lvl == 1 ? 1 : 0);   /* n = 3 - level */")
        e("    /* uniform 5-bit scale; the channel ORDER is irrelevant to it, so")
        e("       this works on the stored hardware word without unswapping. */")
        e("    for (k = 0; k < 15; k += 5)")
        e("        o |= (uint16_t)((uint16_t)gbs_pal_t3[((c >> k) & 0x1F) << sh] << k);")
        e("    return o;")
        e("}")
        e("/* EVERY palette write lands here: record what was asked for, then hand")
        e("   the hardware what the current fade level allows (buf is scratch and")
        e("   is dimmed in place). `buf` is always a RAM staging buffer, never a")
        e("   streamed const, so it stays valid across the bank switch. */")
        # `static` unless it is banked: the resident arm is then the original
        # character for character, so a build that cannot bank is unchanged.
        e("%svoid gbs_pal_hw(uint8_t spr, uint8_t slot, uint8_t count,"
          % ("" if banked else "static "))
        e("                palette_color_t *buf)%s {" % b)
        e("    uint8_t i, n, base;")
        e("    slot &= 7;")
        e("    if ((uint8_t)(slot + count) > 8) count = (uint8_t)(8 - slot);")
        e("    n = (uint8_t)(count << 2);")
        e("    base = (uint8_t)((spr ? 32 : 0) + (slot << 2));")
        e("    for (i = 0; i < n; ++i) gbs_pal_sh[base + i] = buf[i];")
        e("    gbs_pal_msk[spr ? 1 : 0] |=")
        e("        (uint8_t)((uint8_t)((1 << count) - 1) << slot);")
        e("    if (gbs_fade_lvl)")
        e("        for (i = 0; i < n; ++i) buf[i] = gbs_pal_dim(buf[i]);")
        e("    if (spr) set_sprite_palette(slot, count, buf);")
        e("    else set_bkg_palette(slot, count, buf);")
        e("}")
        e("static palette_color_t gbs_pal_b4[4];  /* re-apply scratch (a sm83")
        e("                                          stack frame costs more) */")
        e("/* Re-issue ONE recorded slot at the current level. Layer-parameterised")
        e("   for the same reason gbs_load_set is on the z80 targets: the two arms")
        e("   differ only in which hardware setter takes the run. */")
        e("static void gbs_pal_re(uint8_t spr, uint8_t s) {")
        e("    uint8_t i, base = (uint8_t)((spr ? 32 : 0) + (s << 2));")
        e("    for (i = 0; i < 4; ++i) gbs_pal_b4[i] = gbs_pal_dim(gbs_pal_sh[base + i]);")
        e("    if (spr) set_sprite_palette(s, 1, gbs_pal_b4);")
        e("    else set_bkg_palette(s, 1, gbs_pal_b4);")
        e("}")
        e("void gbs_pal_fade(uint8_t level)%s {" % b)
        e("    uint8_t s, l;")
        if white:
            e("    uint8_t w = (uint8_t)(level >> 7);   /* bit 7: towards white */")
            e("    level &= 0x7F;")
        e("    if (level > 3) level = 3;")
        if white:
            e("    if (level == gbs_fade_lvl && w == gbs_fade_white) return;")
            e("    gbs_fade_white = w;")
        else:
            e("    if (level == gbs_fade_lvl) return;   /* the ramp holds each step */")
        e("    gbs_fade_lvl = level;")
        e("    for (l = 0; l < 2; ++l)")
        e("        for (s = 0; s < 8; ++s)")
        e("            if (gbs_pal_msk[l] & (uint8_t)(1 << s)) gbs_pal_re(l, s);")
        e("}")
        return "\n".join(out)

    def _emit_smsgg_fade(self, levels, shift_g, shift_b):
        """`palette.fade(level)` on the Master System / Game Gear.

        The same idea as the CGB fade (`_emit_cgb_fade`) against different
        hardware: those consoles have no palette REGISTER to darken either, so
        the fade scales CRAM. The shadow is here for exactly the same ordering
        reason - `load_room` blacks out, `scenes.load_palettes` writes the new
        room's CRAM, and only then does the ramp run, so a level applied once
        would be wiped by that load.

        It is cheaper here than on the CGB: **every CRAM write on these
        consoles is one entry**, so a single `gbs_cram` is the whole seam and
        the four writers (the two slot setters, the per-scene SET loader and
        the 16-colour bkg load) just route through it. The shadow follows the
        console's own `palette_color_t` - one byte on the SMS (BGR-222), two on
        the Game Gear (BGR-444) - which is the width trap the colour work
        already documents.

        The channel depth differs too: the SMS has 2 bits per channel, so its
        ramp is coarse (3 -> 2 -> 1 -> 0) but still monotonic to black; the
        Game Gear's 4 bits give a smooth one.
        """
        width = 2 if self.platform == 'sms' else 4
        assert (shift_g, shift_b) == (width, width * 2)
        # The fade DIRECTION, exactly as on the CGB (see _cgb_fade_body): bit 7
        # of the level scales every channel towards FULL instead of 0, and the
        # branch exists only on a build that states VM_FADE_STYLE.
        white = bool(self.defines.get('VM_FADE_STYLE'))
        full = levels | (levels << shift_g) | (levels << shift_b)
        self.emit("/* palette.fade: these consoles have no palette REGISTER to darken, so")
        self.emit("   the fade scales CRAM. The shadow holds what the program asked for, so")
        self.emit("   a palette LOADED while the screen is dark is written dimmed and the")
        self.emit("   ramp back is exact - which is what lets load_room black out, paint,")
        self.emit("   and ramp in without the load undoing the fade. */")
        self.emit("static palette_color_t gbs_pal_sh[32];  /* CRAM: 16 bkg, then 16 sprite */")
        self.emit("static uint8_t gbs_pal_msk[4];          /* entries actually written */")
        self.emit("static uint8_t gbs_fade_lvl;            /* 0 normal .. 3 black */")
        if white:
            self.emit("static uint8_t gbs_fade_white;          /* 1 = the ramp goes to WHITE */")
        self.emit("static palette_color_t gbs_pal_dim(palette_color_t c) {")
        self.emit("    uint8_t k, n = (uint8_t)(3 - gbs_fade_lvl);")
        self.emit("    palette_color_t o = 0;")
        self.emit("    if (n == 3) return c;")
        if white:
            # Towards white: x + (full - x) * level / 3 per channel (the
            # mirror of the black scale below over the channel's distance to
            # full). A divide, like the black arm's: not a per-frame path.
            self.emit("    if (gbs_fade_white) {")
            self.emit("        uint8_t x;")
            self.emit("        if (n == 0) return 0x%X;   /* the whole white load */" % full)
            self.emit("        for (k = 0; k < %d; k += %d) {" % (width * 3, width))
            self.emit("            x = (uint8_t)((c >> k) & %d);" % levels)
            self.emit("            o |= (palette_color_t)((palette_color_t)")
            self.emit("                 ((uint8_t)(x + (%d - x) * gbs_fade_lvl / 3)) << k);" % levels)
            self.emit("        }")
            self.emit("        return o;")
            self.emit("    }")
        self.emit("    if (n == 0) return 0;")
        self.emit("    for (k = 0; k < %d; k += %d)" % (width * 3, width))
        self.emit("        o |= (palette_color_t)((palette_color_t)")
        self.emit("             ((uint8_t)(((c >> k) & %d) * n / 3)) << k);" % levels)
        self.emit("    return o;")
        self.emit("}")
        self.emit("/* EVERY CRAM write lands here: record what was asked for, then hand the")
        self.emit("   hardware what the current fade level allows. */")
        self.emit("static void gbs_cram(uint8_t idx, palette_color_t c) {")
        self.emit("    idx &= 31;")
        self.emit("    gbs_pal_sh[idx] = c;")
        self.emit("    gbs_pal_msk[idx >> 3] |= (uint8_t)(1 << (idx & 7));")
        self.emit("    set_palette_entry((uint8_t)(idx >> 4), (uint8_t)(idx & 15),")
        self.emit("                      gbs_pal_dim(c));")
        self.emit("}")
        self.emit("/* palette.load_native: the flat CRAM run a coloured room loads. Without a")
        self.emit("   fade this lowers straight onto the port's set_palette; with one it has")
        self.emit("   to go entry by entry so each is recorded and scaled. */")
        self.emit("void gbs_pal_native(uint8_t first, uint8_t count,")
        self.emit("                    const palette_color_t *data) {")
        self.emit("    uint8_t i, n = (uint8_t)(count << 4), base = (uint8_t)(first << 4);")
        self.emit("    for (i = 0; i < n; ++i) gbs_cram((uint8_t)(base + i), data[i]);")
        self.emit("}")
        self.emit("void gbs_pal_fade(uint8_t level) {")
        self.emit("    uint8_t i;")
        if white:
            self.emit("    uint8_t w = (uint8_t)(level >> 7);   /* bit 7: towards white */")
            self.emit("    level &= 0x7F;")
        self.emit("    if (level > 3) level = 3;")
        if white:
            self.emit("    if (level == gbs_fade_lvl && w == gbs_fade_white) return;")
            self.emit("    gbs_fade_white = w;")
        else:
            self.emit("    if (level == gbs_fade_lvl) return;   /* the ramp holds each step */")
        self.emit("    gbs_fade_lvl = level;")
        self.emit("    /* Entries the program never wrote are left alone: an untouched shadow")
        self.emit("       cell is 0, and writing that would black CRAM the fade never set. */")
        self.emit("    for (i = 0; i < 32; ++i)")
        self.emit("        if (gbs_pal_msk[i >> 3] & (uint8_t)(1 << (i & 7)))")
        self.emit("            set_palette_entry((uint8_t)(i >> 4), (uint8_t)(i & 15),")
        self.emit("                              gbs_pal_dim(gbs_pal_sh[i]));")
        self.emit("}")

    def _emit_gbdk_palette(self):
        """graphics.palette for GBDK consoles (emitted only when imported).

        The portable model: a palette slot holds 4 colors; gbs_rgb quantizes
        RGB888 to the console's native format inside one function (on the NES
        the RGB8 macro is a 64-way constant-folding chain -- one copy here
        beats inlining it per call site). Per console class:
        - gameboy / megaduck (4 greys): gbs_rgb yields a DMG shade 0-3; bkg
          slot 0 packs into BGP, sprite slots 0/1 into OBP0/OBP1 (the *_REG
          symbols resolve per console -- the Mega Duck register remap
          included). Other slots are honest no-ops (caps: 1 bkg / 2 spr).
        - gameboy_color: cgb.h set_bkg_palette / set_sprite_palette, RGB555.
        - analogue_pocket: the CGB path plus a DMG-register mirror of slot
          0/1, so a Pocket core running the ROM in DMG mode still shows
          quantized shades instead of ignoring the palette entirely.
        - sms / gamegear: CRAM entries 0-3 (BG) and sprite-bank entries 0-3 --
          exactly where the port's 2bpp compat layer puts GB pixel values
          (its default _current_2bpp_palette is the identity, 0x3210).
        - nes: PPU palettes via set_bkg/sprite_palette_entry; entry 0 of BG
          palettes is the shared backdrop, so only slot 0's color 0 shows.
        """
        cgb_class = self.platform in ('gameboy_color', 'analogue_pocket')
        ap_mirror = self.platform == 'analogue_pocket'
        self.emit("/* graphics.palette: 4-color GB-model palette slots. */")
        if not self.caps['has_color']:
            self.emit("/* 4-grey console: colors quantize to DMG shades (white=0 .. black=3),")
            self.emit("   the same thresholds as the asset pipeline's PNG luma mapping. */")
            self.emit("uint16_t gbs_rgb(uint8_t r, uint8_t g, uint8_t b) {")
            self.emit("    uint16_t luma = ((uint16_t)r * 3 + (uint16_t)g * 6 + b) / 10;")
            self.emit("    if (luma >= 240) return 0;")
            self.emit("    if (luma >= 160) return 1;")
            self.emit("    if (luma >= 80) return 2;")
            self.emit("    return 3;")
            self.emit("}")
            _setters_at = len(self.output)
            self.emit("void gbs_set_bkg_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    if (slot != 0) return;  /* one BG palette on this console */")
            self.emit("    BGP_REG = DMG_PALETTE(c0, c1, c2, c3);")
            self.emit("}")
            self.emit("void gbs_set_spr_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    if (slot == 0) OBP0_REG = DMG_PALETTE(c0, c1, c2, c3);")
            self.emit("    else if (slot == 1) OBP1_REG = DMG_PALETTE(c0, c1, c2, c3);")
            self.emit("}")
        elif cgb_class:
            # On the Game Boy Color the SET loaders convert 5-5-5 straight to
            # the hardware's own word (a channel swap), so gbs_rgb is left with
            # exactly one caller -- palette.rgb itself. Emitted only for a
            # program that calls it; every other console still emits it
            # unconditionally (their setters are built on it).
            if self._gbs_rgb_needed():
                self.emit("uint16_t gbs_rgb(uint8_t r, uint8_t g, uint8_t b) { return RGB8(r, g, b); }")
            if ap_mirror:
                self.emit("/* DMG-register mirror: quantize an RGB555 word to a DMG shade so a")
                self.emit("   Pocket core running this ROM in DMG mode still shows the palette")
                self.emit("   (in CGB mode the BGP/OBP writes are ignored and harmless). */")
                self.emit("static uint8_t gbs_pal_shade(uint16_t c) {")
                self.emit("    uint16_t luma = ((c & 0x1F) * 3 + ((c >> 5) & 0x1F) * 6 + ((c >> 10) & 0x1F)) / 10;")
                self.emit("    if (luma >= 30) return 0;")
                self.emit("    if (luma >= 20) return 1;")
                self.emit("    if (luma >= 10) return 2;")
                self.emit("    return 3;")
                self.emit("}")
            if self.palette_fade_used:
                self._emit_cgb_fade()
            _setters_at = len(self.output)
            self.emit("void gbs_set_bkg_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    palette_color_t buf[4];")
            self.emit("    buf[0] = c0; buf[1] = c1; buf[2] = c2; buf[3] = c3;")
            if self.palette_fade_used:
                self.emit("    gbs_pal_hw(0, slot, 1, buf);")
            else:
                self.emit("    set_bkg_palette(slot & 7, 1, buf);")
            if ap_mirror:
                # The mirror reads the BUFFER, not c0..c3, once a fade is in
                # play: gbs_pal_hw dims it in place, so a palette loaded
                # mid-transition mirrors the DARK shades and cannot un-black a
                # Pocket core running the ROM in DMG mode. Off, the arguments
                # ARE the buffer, so this is the same code it always emitted.
                a = _cgb_mirror_args(self.palette_fade_used)
                self.emit("    if (slot == 0)")
                self.emit("        BGP_REG = DMG_PALETTE(gbs_pal_shade(%s), gbs_pal_shade(%s),"
                          % (a[0], a[1]))
                self.emit("                              gbs_pal_shade(%s), gbs_pal_shade(%s));"
                          % (a[2], a[3]))
            self.emit("}")
            self.emit("void gbs_set_spr_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    palette_color_t buf[4];")
            self.emit("    buf[0] = c0; buf[1] = c1; buf[2] = c2; buf[3] = c3;")
            if self.palette_fade_used:
                self.emit("    gbs_pal_hw(1, slot, 1, buf);")
            else:
                self.emit("    set_sprite_palette(slot & 7, 1, buf);")
            if ap_mirror:
                a = _cgb_mirror_args(self.palette_fade_used)   # see above
                self.emit("    if (slot == 0)")
                self.emit("        OBP0_REG = DMG_PALETTE(gbs_pal_shade(%s), gbs_pal_shade(%s),"
                          % (a[0], a[1]))
                self.emit("                               gbs_pal_shade(%s), gbs_pal_shade(%s));"
                          % (a[2], a[3]))
                self.emit("    else if (slot == 1)")
                self.emit("        OBP1_REG = DMG_PALETTE(gbs_pal_shade(%s), gbs_pal_shade(%s),"
                          % (a[0], a[1]))
                self.emit("                               gbs_pal_shade(%s), gbs_pal_shade(%s));"
                          % (a[2], a[3]))
            self.emit("}")
        elif self.platform in ('sms', 'gamegear'):
            # GBDK's RGB8 macro TRUNCATES to the channel's top bits (r >> 6 on
            # SMS, r >> 4 on GG), so a muted mid-tone collapses to a primary --
            # e.g. the intended (216,56,56) crimson became pure (255,0,0) fire
            # engine red on SMS because 56 >> 6 == 0. Quantize by ROUNDING to
            # the nearest level instead (same CRAM bit layout as RGB8, just a
            # closer colour): SMS CRAM is 2 bits/channel (levels 0..3), the
            # Game Gear 4 bits/channel (levels 0..15).
            if self.platform == 'sms':
                levels, shift_g, shift_b = 3, 2, 4
            else:  # gamegear
                levels, shift_g, shift_b = 15, 4, 8
            if self._sms_rgb_needed():
                self.emit("/* Round RGB888 to the native CRAM depth (see note above); RGB8")
                self.emit("   would truncate and shift muted tones to primaries. */")
                self.emit("uint16_t gbs_rgb(uint8_t r, uint8_t g, uint8_t b) {")
                self.emit("    uint8_t rr = (uint8_t)(((uint16_t)r * %d + 127) / 255);" % levels)
                self.emit("    uint8_t gg = (uint8_t)(((uint16_t)g * %d + 127) / 255);" % levels)
                self.emit("    uint8_t bb = (uint8_t)(((uint16_t)b * %d + 127) / 255);" % levels)
                self.emit("    return (uint16_t)rr | ((uint16_t)gg << %d) | ((uint16_t)bb << %d);"
                          % (shift_g, shift_b))
                self.emit("}")
            self.emit("/* The 2bpp compat layer loads GB pixel values into CRAM entries 0-3")
            self.emit("   (BG bank) / sprite-bank entries 0-3, so those are the slot-0 colors. */")
            if self.palette_fade_used:
                self._emit_smsgg_fade(levels, shift_g, shift_b)
            # With a fade every CRAM write routes through gbs_cram (record +
            # scale); without one each keeps its direct entry write and the
            # output is byte-identical.
            _bkg = ((lambda i, c: "    gbs_cram(%d, %s);" % (i, c))
                    if self.palette_fade_used
                    else (lambda i, c: "    set_bkg_palette_entry(0, %d, %s);" % (i, c)))
            _spr = ((lambda i, c: "    gbs_cram(%d, %s);" % (16 + i, c))
                    if self.palette_fade_used
                    else (lambda i, c: "    set_sprite_palette_entry(0, %d, %s);" % (i, c)))
            _setters_at = len(self.output)
            self.emit("void gbs_set_bkg_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    if (slot != 0) return;  /* one BG palette on this console */")
            for i in range(4):
                self.emit(_bkg(i, "c%d" % i))
            self.emit("}")
            self.emit("void gbs_set_spr_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    if (slot != 0) return;  /* sprites share the second CRAM bank */")
            self.emit(_spr(0, "c0") + "  /* entry 0: transparent on screen,")
            self.emit("                                            but the SMS border shows it */")
            for i in range(1, 4):
                self.emit(_spr(i, "c%d" % i))
            self.emit("}")
        else:  # nes
            self.emit("/* RGB8 maps to the nearest NES master-palette index through a 64-way")
            self.emit("   constant-folding chain; keep the one copy inside this function. */")
            self.emit("uint16_t gbs_rgb(uint8_t r, uint8_t g, uint8_t b) { return RGB8(r, g, b); }")
            _setters_at = len(self.output)
            self.emit("void gbs_set_bkg_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    slot &= 3;")
            self.emit("    /* Entry 0 is the shared PPU backdrop ($3F00): the hardware only")
            self.emit("       displays slot 0's color 0. */")
            self.emit("    set_bkg_palette_entry(slot, 0, (palette_color_t)c0);")
            self.emit("    set_bkg_palette_entry(slot, 1, (palette_color_t)c1);")
            self.emit("    set_bkg_palette_entry(slot, 2, (palette_color_t)c2);")
            self.emit("    set_bkg_palette_entry(slot, 3, (palette_color_t)c3);")
            self.emit("}")
            self.emit("void gbs_set_spr_palette(uint8_t slot, uint16_t c0, uint16_t c1, uint16_t c2, uint16_t c3) {")
            self.emit("    slot &= 3;")
            self.emit("    set_sprite_palette_entry(slot, 0, (palette_color_t)c0);  /* transparent */")
            self.emit("    set_sprite_palette_entry(slot, 1, (palette_color_t)c1);")
            self.emit("    set_sprite_palette_entry(slot, 2, (palette_color_t)c2);")
            self.emit("    set_sprite_palette_entry(slot, 3, (palette_color_t)c3);")
            self.emit("}")
        if not self.palette_setter_used:
            # Nothing calls the two single-slot setters here: every byte of a
            # prelude helper is RESIDENT (it cannot bank), and on the Game Boy
            # Color the per-scene SET loaders drive the hardware directly. Drop
            # the pair rather than link them dead.
            del self.output[_setters_at:]
        if self.palette_load_used:
            self.emit("void gbs_load_bkg_palette(uint8_t slot, const uint16_t *colors) {")
            self.emit("    gbs_set_bkg_palette(slot, colors[0], colors[1], colors[2], colors[3]);")
            self.emit("}")
            self.emit("void gbs_load_spr_palette(uint8_t slot, const uint16_t *colors) {")
            self.emit("    gbs_set_spr_palette(slot, colors[0], colors[1], colors[2], colors[3]);")
            self.emit("}")
        if self.palette_set_used:
            # palette.load_bkg_set / load_sprite_set: a whole per-scene palette
            # SET out of one table. `off` is a WORD index, so a generated room
            # loader indexes a concatenated per-scene table with `room * 32`
            # and needs no pointer arithmetic in mosaik (which has none).
            # Real only where the multi-palette model is (has_tile_palettes);
            # a console with one palette per layer keeps the fixed palette its
            # art was quantized for, which is exactly what the reference engine's own DMG
            # build does.
            if self.caps['has_tile_palettes']:
                self._emit_palette_set_real()
            elif self.platform in ('sms', 'gamegear'):
                # SMS/GG: no per-tile attribute, but the CRAM is 16 entries a
                # layer = FOUR 4-colour slots, and the slot-rendered uploads
                # (bkg.set_data_pal / sprite.set_data_pal) point tiles at
                # them. So a scene's palette SET is real here: slots 0..3
                # land in CRAM entries slot*4.., extra slots are clamped off
                # (the converter folds a scene's palettes to 4 for these
                # consoles). Colours arrive as portable 5-5-5 words and round
                # to the native depth via gbs_rgb, like load_bkg16 does.
                # ONE shared core (set_palette_entry takes the CRAM bank as
                # its first argument), because every prelude byte is RESIDENT
                # and this project class is counted in tens of spare bytes.
                self.emit("static void gbs_load_set(uint8_t pal, uint8_t slot, uint8_t count,")
                self.emit("                         const uint16_t *colors, uint16_t off) {")
                self.emit("    uint8_t i, n;")
                self.emit("    uint16_t w;")
                self.emit("    if (slot > 3) return;")
                self.emit("    if ((uint8_t)(slot + count) > 4) count = (uint8_t)(4 - slot);")
                self.emit("    n = (uint8_t)(count << 2);")
                self.emit("    for (i = 0; i < n; ++i) {")
                self.emit("        w = colors[off + i];")
                if self.palette_fade_used:
                    # one flat CRAM index, so the write can be recorded + scaled
                    self.emit("        gbs_cram((uint8_t)((pal << 4) + (slot << 2) + i),")
                else:
                    self.emit("        set_palette_entry(pal, (uint8_t)((slot << 2) + i),")
                self.emit("            gbs_rgb((uint8_t)(((w >> 10) & 0x1F) << 3),")
                self.emit("                    (uint8_t)(((w >> 5) & 0x1F) << 3),")
                self.emit("                    (uint8_t)((w & 0x1F) << 3)));")
                self.emit("    }")
                self.emit("}")
                self.emit("void gbs_load_bkg_set(uint8_t slot, uint8_t count,")
                self.emit("                      const uint16_t *colors, uint16_t off) {")
                self.emit("    gbs_load_set(0, slot, count, colors, off);")
                self.emit("}")
                self.emit("void gbs_load_spr_set(uint8_t slot, uint8_t count,")
                self.emit("                      const uint16_t *colors, uint16_t off) {")
                self.emit("    gbs_load_set(1, slot, count, colors, off);")
                self.emit("}")
            else:
                self.emit("/* One background/sprite palette on this console: a colour SET has")
                self.emit("   nowhere to go, and the fixed palette is what the art was")
                self.emit("   quantized for. An honest no-op. */")
                self.emit("void gbs_load_bkg_set(uint8_t slot, uint8_t count,")
                self.emit("                      const uint16_t *colors, uint16_t off) {")
                self.emit("    (void)slot; (void)count; (void)colors; (void)off;")
                self.emit("}")
                self.emit("void gbs_load_spr_set(uint8_t slot, uint8_t count,")
                self.emit("                      const uint16_t *colors, uint16_t off) {")
                self.emit("    (void)slot; (void)count; (void)colors; (void)off;")
                self.emit("}")
        if self.load_bkg16_used:
            # palette.load_bkg16 (4bpp BACKGROUND tier). SMS/GG load the tileset's
            # 16 authored colours (portable 5-5-5 RGB words) into the BG CRAM
            # palette, rounding to the native depth via gbs_rgb (never the port's
            # RGB8, which truncates); entry 0 is the backdrop. Every other GBDK
            # console is 2bpp, so it is a no-op (the tileset luma-quantized to
            # greys at build time). Emitted here so gbs_rgb is already in scope.
            if self.platform in ('sms', 'gamegear'):
                self.emit("/* palette.load_bkg16: 16 colours (5-5-5 RGB words) -> the BG CRAM")
                self.emit("   palette (SMS BGR-222 / Game Gear BGR-444 via gbs_rgb). */")
                self.emit("void gbs_load_bkg_pal16(const uint16_t *pal) {")
                self.emit("    uint8_t p, r, g, b;")
                self.emit("    for (p = 0; p < 16; ++p) {")
                self.emit("        r = (uint8_t)(((pal[p] >> 10) & 0x1F) << 3);")
                self.emit("        g = (uint8_t)(((pal[p] >> 5) & 0x1F) << 3);")
                self.emit("        b = (uint8_t)((pal[p] & 0x1F) << 3);")
                # gbs_cram takes ONE flat CRAM index (0..31, bkg then sprite),
                # where set_bkg_palette_entry takes (bank, entry).
                if self.palette_fade_used:
                    self.emit("        gbs_cram(p, gbs_rgb(r, g, b));")
                else:
                    self.emit("        set_bkg_palette_entry(0, p, gbs_rgb(r, g, b));")
                self.emit("    }")
                self.emit("}")
            else:
                self.emit("/* 16-colour bkg palette: no-op on the 2bpp GB family (the 4bpp")
                self.emit("   tileset was luma-quantized to greys at build time). */")
                self.emit("void gbs_load_bkg_pal16(const uint16_t *pal) { (void)pal; }")
        if (self.palette_fade_used and not cgb_class
                and self.platform not in ('sms', 'gamegear')):
            # The CGB class scales its palettes (_emit_cgb_fade) and the z80
            # pair its CRAM (_emit_smsgg_fade); what is left is the DMG family,
            # which fades through BGP/OBP0/OBP1 in vm.fx (real there, and
            # cheaper than scaling four greys), and the NES, whose master
            # palette could be scaled the same way but is not wired. Either way
            # vm.fx calls the seam unconditionally, so the generated rooms.mos
            # stays target-neutral and this stub is what makes that honest.
            self.emit("/* palette.fade: this console fades through the DMG palette registers")
            self.emit("   (vm.fx) or not at all; nothing to scale here. An honest no-op. */")
            self.emit("void gbs_pal_fade(uint8_t level) { (void)level; }")
        # sprite.set_palette: which palette slot a sprite uses.
        if not self.sprite_palette_used:
            pass
        elif self.platform in ('sms', 'gamegear'):
            self.emit("/* SMS/GG sprites always use the sprite CRAM bank; there is no")
            self.emit("   per-sprite palette select, so this is an honest no-op. */")
            self.emit("void gbs_sprite_palette(uint8_t nb, uint8_t slot) { (void)nb; (void)slot; }")
        elif self.platform == 'nes':
            self.emit("/* NES OAM attribute bits 0-1 select the sprite palette. */")
            self.emit("void gbs_sprite_palette(uint8_t nb, uint8_t slot) {")
            if self.metasprite_used:
                self.emit("    %s(nb, gbs_pal_prop(gbs_meta_prop_of(nb), slot));"
                      % ("gbs_fan_prop" if self.sprite_palette_used
                         else "gbs_set_sprite_prop"))
            else:
                self.emit("    set_sprite_prop(nb, (uint8_t)((get_sprite_prop(nb) & ~0x03) | (slot & 0x03)));")
            self.emit("}")
        else:
            self.emit("/* The OAM attribute carries both the CGB palette number (bits 0-2)")
            self.emit("   and the DMG OBP0/OBP1 select (S_PALETTE); writing both keeps one")
            self.emit("   implementation correct in either mode. `slot` carries both,")
            self.emit("   spelled as the reference engine's own metasprite props (bit 4 = OBP1). */")
            self.emit("void gbs_sprite_palette(uint8_t nb, uint8_t slot) {")
            if self.metasprite_used:
                # A metasprite is a FAN of OAM objects: recolouring only the
                # base left every child on the old palette (a 2x2 actor showed
                # one coloured cell). Route through the raw fan, reading the
                # base's recorded prop so a live flip is preserved.
                self.emit("    %s(nb, gbs_pal_prop(gbs_meta_prop_of(nb), slot));"
                      % ("gbs_fan_prop" if self.sprite_palette_used
                         else "gbs_set_sprite_prop"))
            else:
                self.emit("    uint8_t prop = (uint8_t)(get_sprite_prop(nb) & ~(S_PALETTE | 0x07));")
                self.emit("    prop |= (uint8_t)(slot & (S_PALETTE | 0x07));")
                self.emit("    set_sprite_prop(nb, prop);")
            self.emit("}")
        # bkg.set_palette: per-tile background palette (has_tile_palettes only).
        if self.caps['has_tile_palettes'] and self.bkg_palette_fill_used:
            if cgb_class:
                self.emit("/* bkg.set_palette: fill a map rectangle in the CGB attribute map")
                self.emit("   (VRAM bank 1). Coordinates wrap mod 32 like set_bkg_tiles. */")
                self.emit("void gbs_bkg_palette_fill(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t slot) {")
                self.emit("    uint8_t i, j;")
                self.emit("    slot &= 7;")
                self.emit("    VBK_REG = VBK_ATTRIBUTES;")
                self.emit("    for (j = 0; j < h; ++j)")
                self.emit("        for (i = 0; i < w; ++i)")
                self.emit("            set_bkg_tile_xy((uint8_t)((x + i) & 31), (uint8_t)((y + j) & 31), slot);")
                self.emit("    VBK_REG = VBK_TILES;")
                self.emit("}")
            else:  # nes
                self.emit("/* bkg.set_palette: NES attribute granularity is 16x16 px (2x2 tiles);")
                self.emit("   the rectangle is rounded outward to whole attribute cells. */")
                self.emit("void gbs_bkg_palette_fill(uint8_t x, uint8_t y, uint8_t w, uint8_t h, uint8_t slot) {")
                self.emit("    uint8_t a = (uint8_t)((slot & 3) * 0x55);  /* all four quadrants */")
                self.emit("    uint8_t cx, cy;")
                self.emit("    if (w == 0 || h == 0) return;")
                self.emit("    for (cy = (uint8_t)(y >> 1); cy <= (uint8_t)((y + h - 1) >> 1); ++cy)")
                self.emit("        for (cx = (uint8_t)(x >> 1); cx <= (uint8_t)((x + w - 1) >> 1); ++cx)")
                self.emit("            set_bkg_attributes_nes16x16(cx, cy, 1, 1, &a);")
                self.emit("}")
