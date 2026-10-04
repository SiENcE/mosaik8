"""OAM property + per-sprite palette emitters for the GBDK backend.

The line this module draws: `sprite.set_prop` owns a slot's FLIP and
priority bits, `sprite.set_palette` / `set_meta_palettes` own its PALETTE
bits, and neither may overwrite the other - the state lives in OAM, where
it already is.

A GbdkBackend concern mixin - methods run against the full
CodeGenerator instance (self.emit, self.caps, ...)."""


class GbdkSpriteMixin:
    def _spr_pal_mask(self):
        """The OAM property bits `sprite.set_palette` owns on this console.

        0 where sprites have no per-sprite palette select (SMS/GG), so the
        merge below compiles away and those builds stay byte-identical."""
        if self.platform in ('sms', 'gamegear'):
            return None
        if self.platform == 'nes':
            return "0x03"                      # OAM attribute bits 0-1
        return "(S_PALETTE | 0x07)"            # CGB palette 0-2 + the DMG select

    def _spr_keep_pal(self):
        """Whether prop writes must preserve a slot's palette bits here."""
        return self.palette_imported and self._spr_pal_mask() is not None

    def _emit_spr_keep_pal_macro(self):
        """`GBS_KEEP_PAL(slot, prop)`: a property byte that carries `prop`'s
        FLIP/priority bits but leaves the slot's own PALETTE bits alone.

        `sprite.set_prop` owns the flip, `sprite.set_palette` /
        `set_meta_palettes` own the palette, and the two must not overwrite each
        other. It is per SLOT rather than per metasprite BASE because a
        metasprite may wear a different palette in each cell - keeping the state
        in OAM, where it already lives, is what lets the per-cell map be applied
        ONCE instead of remembered and re-applied (a remembered pointer into a
        banked table dangles, which is exactly how that first cut failed).

        Without any of this a facing flip erased the colour: vm.canim
        re-asserts FLIP_X through set_prop on every turn, and set_meta re-fans
        every child whenever a clip frame changes (vm.canim's apply cache
        skips the unchanged frames - measured on the reference-engine sample conversion: apply runs
        0.2-0.4 times per game frame, review E-2). The PCE backend already drew this line (it
        keeps the SATB attribute's palette nibble); this is the same rule on the
        GB family and the NES. Emitted only where a program uses palettes, so
        everything else is byte-identical."""
        mask = self._spr_pal_mask()
        if not self._spr_keep_pal():
            return
        self.emit("/* A prop write that keeps the slot's own palette bits: set_prop")
        self.emit("   owns flip/priority, set_palette owns the colour. */")
        self.emit("#define GBS_KEEP_PAL(slot, prop) \\")
        self.emit("    ((uint8_t)(((prop) & ~%s) | (get_sprite_prop(slot) & %s)))"
                  % (mask, mask))

    def _emit_gbdk_prop_wrapper(self):
        """The palette-preserving `sprite.set_prop` for a program that colours
        sprites but uses no metasprites (the metasprite layer has its own copy
        of the merge). Emitted only in that combination."""
        if self.metasprite_used or not self.palette_imported:
            return
        mask = self._spr_pal_mask()
        if mask is None:
            return
        if not self._needs_meta('gbs_set_sprite_prop'):
            return
        self.emit("/* sprite.set_prop: flip/priority only -- the palette bits belong to")
        self.emit("   sprite.set_palette, so a flip cannot reset a sprite's colour. */")
        self.emit("static void gbs_set_sprite_prop(uint8_t nb, uint8_t prop) {")
        self.emit("    set_sprite_prop(nb, (uint8_t)((prop & ~%s)" % mask)
        self.emit("                        | (get_sprite_prop(nb) & %s)));" % mask)
        self.emit("}")

    def _emit_gbdk_meta_pal(self, obj16):
        """`sprite.set_meta_palettes`: one palette PER CELL of a metasprite.

        `sprite.set_palette` colours a whole actor; this colours it cell by
        cell, which is what the reference engine does (its platform player wears three OBJ
        palettes - hair, face, body - across one 2x4-tile metasprite). The map
        is in 8x8-TILE units, row-major, exactly like `sprite.set_meta`'s own
        `w`/`h`, so the AUTHORED data is target-neutral; only this fan knows
        the console's object order.

        The map is REMEMBERED per base, because the two writers that re-fan a
        metasprite would otherwise flatten it back to one palette: `set_meta`
        re-tiles every child whenever the clip frame changes (vm.canim calls
        it per actor per changed frame; its apply cache skips the rest) and a
        facing flip re-fans the prop. Both re-apply the map here.
        `sprite.set_palette` clears it - the two are exclusive, so "colour this
        actor" always wins over a stale per-cell map.

        Emitted only when called: it costs a pointer per hardware sprite slot
        plus the second fan."""
        if not self.meta_palettes_used:
            return
        if not self._needs_meta('gbs_set_meta_pal'):
            return
        if self._spr_pal_mask() is None:
            self.emit("/* sprite.set_meta_palettes: SMS/GG sprites share one palette, so")
            self.emit("   there is nothing to select per cell. An honest no-op. */")
            self.emit("static void gbs_set_meta_pal(uint8_t nb, uint8_t w, uint8_t h,")
            self.emit("                      const uint8_t *data, uint16_t off) {")
            self.emit("    (void)nb; (void)w; (void)h; (void)data; (void)off;")
            self.emit("}")
            return
        self.emit("/* sprite.set_meta_palettes: write one palette per CELL over the fan.")
        self.emit("   The map is ROW-MAJOR in 8x8-tile units (sprite.set_meta's own w/h),")
        if obj16:
            self.emit("   and an 8x16 object covers a vertical PAIR of cells, taking the")
            self.emit("   upper one's palette. Nothing is remembered: each child's palette")
        else:
            self.emit("   the same order as the tile fan. Nothing is remembered: each")
            self.emit("   child's palette")
        self.emit("   lives in its own OAM byte, and the re-fans below preserve it. */")
        self.emit("static void gbs_set_meta_pal(uint8_t nb, uint8_t w, uint8_t h,")
        self.emit("                      const uint8_t *data, uint16_t off) {")
        self.emit("    const uint8_t *m = data + off;")
        self.emit("    uint8_t base;")
        if obj16:
            self.emit("    uint8_t c, p, h2 = (uint8_t)(h >> 1), s = nb;")
            self.emit("    if (nb >= GBS_META_SLOTS) return;")
            # the product check must be WIDE: a u8-truncated w * h2 lets a
            # garbage size (248 * 127 & 0xFF = 8) slip the guard and overrun
            # every meta table + the shadow OAM (the reference-engine sample conversion's room-change
            # crash; the garbage came from an out-of-bounds clip read).
            self.emit("    if ((uint16_t)(w * h2) > (uint16_t)(GBS_META_SLOTS - nb)) return;")
            self.emit("    base = (uint8_t)(gbs_meta_prop[nb] & ~%s);"
                      % self._spr_pal_mask())
            self.emit("    for (c = 0; c < w; ++c)")
            self.emit("        for (p = 0; p < h2; ++p) {")
            self.emit("            set_sprite_prop(s, gbs_pal_prop(base, m[(uint8_t)((p << 1) * w + c)]));")
            self.emit("            ++s;")
            self.emit("        }")
        else:
            self.emit("    uint8_t k, n = (uint8_t)(w * h);")
            self.emit("    if (nb >= GBS_META_SLOTS) return;")
            self.emit("    if ((uint16_t)(w * h) > (uint16_t)(GBS_META_SLOTS - nb)) return;")
            self.emit("    base = (uint8_t)(gbs_meta_prop[nb] & ~%s);"
                      % self._spr_pal_mask())
            self.emit("    for (k = 0; k < n; ++k)")
            self.emit("        set_sprite_prop((uint8_t)(nb + k), gbs_pal_prop(base, m[k]));")
        self.emit("}")

    def _emit_gbdk_pal_prop(self):
        """One OAM property byte carrying palette slot `slot` over `prop`'s
        flip/priority bits. The single place that knows how a palette is
        spelled in an attribute byte on this console.

        `slot` is spelled the way the reference engine's own compiler spells it
        (`compileSprites.ts` `makeProps`): bits 0-2 are the CGB palette number
        and bit 4 (S_PALETTE) is the DMG OBP0/OBP1 select, which its editor
        authors as a SEPARATE per-tile field (`objPalette`). The two are
        independent - a sprite drawn through CGB palette 3 is on OBP0 unless
        its `objPalette` says otherwise - so this used to be wrong in both
        directions: an odd CGB slot forced OBP1 (measured on the reference-engine sample conversion's
        DMG build, where an enemy sprite sits on slot 3 and the reference draws it
        through OBP0), and nothing could ASK for OBP1, which is how a DMG game
        flashes a sprite (the platformer conversion's `press_a` alternates the two, and the flash
        was simply missing). Carrying both fields in the one value keeps that
        honest and drops a branch."""
        if not self.palette_imported or self._spr_pal_mask() is None:
            return
        if not self._needs_meta('gbs_pal_prop'):
            return
        self.emit("static uint8_t gbs_pal_prop(uint8_t prop, uint8_t slot) {")
        if self.platform == 'nes':
            self.emit("    return (uint8_t)((prop & ~0x03) | (slot & 0x03));")
        else:
            self.emit("    /* bits 0-2 = CGB palette, bit 4 = the DMG OBP1 select:")
            self.emit("       the reference engine's own metasprite prop encoding. */")
            self.emit("    return (uint8_t)((prop & ~(S_PALETTE | 0x07))")
            self.emit("                     | (slot & (S_PALETTE | 0x07)));")
        self.emit("}")

    def _emit_gbdk_prop_setters(self, count_expr):
        """`sprite.set_prop` on a metasprite base, and the raw fan under it.

        A program that colours sprites needs TWO entry points, because the two
        writers own different bits: `sprite.set_prop` (the flip, re-asserted by
        vm.canim on every facing change) must MERGE so it cannot erase a
        palette, while `sprite.set_palette` must write the palette bits it just
        computed. Routing the palette through the merging setter made setting a
        palette a silent no-op - which is exactly how it looked on screen, every
        actor stuck on OBJ palette 0.

        So the FAN is factored out and the merging setter is a two-line wrapper
        over it; splitting rather than duplicating keeps the pair barely bigger
        than the single function it replaces (measured: duplicating the fan cost
        130 B of the reference-engine import's resident image, which it did not have).
        A program that never touches palettes emits the original single
        function, byte-identical."""
        pal = self.palette_imported and self._spr_pal_mask() is not None
        if not self._needs_meta('gbs_set_sprite_prop'):
            self._emit_gbdk_fan_prop(count_expr, pal)
            return
        self.emit("static void gbs_set_sprite_prop(uint8_t nb, uint8_t prop) {")
        self.emit("    uint8_t w, h, s, n;")
        self.emit("    if (nb >= GBS_META_SLOTS) return;")
        self.emit("    w = gbs_meta_w[nb]; h = gbs_meta_h[nb];")
        self.emit("    gbs_meta_prop[nb] = prop;")
        self.emit("    if (w > 1 || h > 1) {")
        if self.meta_list_used:
            # A LIST base's child count is in h (w is the 0xFF marker); the
            # merged prop still fans over every child, so a flip re-asserted
            # by vm.canim reaches them (per-object props bits are only
            # guaranteed until the next set_prop on the base).
            self.emit("        n = (w == 0xFF) ? h : %s;" % count_expr)
        else:
            self.emit("        n = %s;" % count_expr)
        if pal:
            self.emit("        for (s = 0; s < n; ++s)")
            self.emit("            set_sprite_prop((uint8_t)(nb + s),")
            self.emit("                            GBS_KEEP_PAL((uint8_t)(nb + s), prop));")
        else:
            self.emit("        for (s = 0; s < n; ++s) set_sprite_prop((uint8_t)(nb + s), prop);")
        self.emit("        return;")
        self.emit("    }")
        self.emit("    set_sprite_prop(nb, %s);"
                  % ("GBS_KEEP_PAL(nb, prop)" if pal else "prop"))
        self.emit("}")
        self._emit_gbdk_fan_prop(count_expr, pal)

    def _emit_gbdk_fan_prop(self, count_expr, pal):
        # The RAW fan (verbatim, palette bits and all) exists only where
        # sprite.set_palette needs it -- it is the one writer that OWNS those
        # bits (main TU only, via _tu_meta_needs). A program that colours
        # purely per CELL never emits it.
        if not (pal and self.sprite_palette_used):
            return
        if not self._needs_meta('gbs_fan_prop'):
            return
        self.emit("/* The raw fan: writes the prop byte VERBATIM over the whole")
        self.emit("   metasprite. sprite.set_palette's path -- it owns the palette. */")
        self.emit("static void gbs_fan_prop(uint8_t nb, uint8_t prop) {")
        self.emit("    uint8_t w, h, s, n;")
        self.emit("    if (nb >= GBS_META_SLOTS) return;")
        self.emit("    w = gbs_meta_w[nb]; h = gbs_meta_h[nb];")
        self.emit("    if (w > 1 || h > 1) {")
        if self.meta_list_used:
            self.emit("        n = (w == 0xFF) ? h : %s;" % count_expr)
        else:
            self.emit("        n = %s;" % count_expr)
        self.emit("        for (s = 0; s < n; ++s) set_sprite_prop((uint8_t)(nb + s), prop);")
        self.emit("        return;")
        self.emit("    }")
        self.emit("    set_sprite_prop(nb, prop);")
        self.emit("}")

    def _emit_gbdk_meta_prop_of(self):
        """The BASE's current OAM property byte, metasprite-aware.

        gbs_meta_prop records what gbs_set_sprite_prop last fanned out, which
        is the truth for a metasprite base (get_sprite_prop would read one
        child). Used by sprite.set_palette to change the palette bits without
        losing a live flip.

        Emitted only where sprite.set_palette actually reads it, so a program
        that never recolours a sprite (and every SMS/GG build, whose sprites
        share one palette) stays byte-identical."""
        if not self.palette_imported or self.platform in ('sms', 'gamegear'):
            return
        if not self._needs_meta('gbs_meta_prop_of'):
            return
        self.emit("static uint8_t gbs_meta_prop_of(uint8_t nb) {")
        self.emit("    return nb < GBS_META_SLOTS ? gbs_meta_prop[nb]")
        self.emit("                               : get_sprite_prop(nb);")
        self.emit("}")

    #: How many parallax bands the runtime carries. The reference engine's own limit, and
    #: the reason is the same: each band costs an LYC interrupt per frame and a
    #: column cursor in the streamer.
    PARALLAX_BANDS = 3
