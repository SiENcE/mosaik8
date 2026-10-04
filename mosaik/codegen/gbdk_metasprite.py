"""The GBDK metasprite layer: `sprite.set_meta` and its two sparse
variants (the per-column MASK and the per-object DESCRIPTOR list), in the
8x8-object form and the 8x16 OBJ-mode form.

A GbdkBackend concern mixin - methods run against the full
CodeGenerator instance (self.emit, self.caps, ...).

RESIDENCY: every function here is a `static` PER-TU definition, emitted
into exactly the TUs whose code calls it (`_tu_meta_needs`) - the main TU
and each bank TU get their own copy of what they use and nothing else, so
on a project whose sprite users all live in code banks the whole layer
leaves bank 0 and lands in the banks that call it. (`static inline` alone
does NOT do this: sdcc emits an unused static-inline function as dead code
the moment its body has a loop - measured +236 B of bank 0 on the GBC
conversion. Gating the EMISSION is the only mechanism that works.)
The per-slot STATE stays ONE set of arrays: defined (BSS, free) in the
main TU, `extern` in the bank TUs - `_emit_gbdk_meta_tables(extern=...)`
is the one emitter both preludes call, so the two views cannot drift."""
import dataclasses

from ..ast_nodes import *  # noqa: F401,F403
from ..platforms import obj16_effective


class GbdkMetaspriteMixin:
    #: The metasprite-family helpers the CURRENT TU needs (None = all, the
    #: safety fallback). Set per TU: the main TU's set is computed in
    #: generate() after bank placement is final, each bank TU's in
    #: _emit_bank_units from that bank's own function bodies.
    _tu_meta_needs = None

    def _needs_meta(self, name):
        return self._tu_meta_needs is None or name in self._tu_meta_needs

    def _meta_helper_needs(self, funcs, main_tu=False):
        """Scan the given function bodies for sprite.* verbs and answer the
        set of gbs_ helper names this TU must define. Mirrors the stdlib
        lowering gates: a verb that lowers to a native GBDK call when the
        metasprite layer is off names no helper here."""
        verbs = {'move': 'gbs_move_sprite'}
        if self.meta_clip_used:
            verbs['move_world'] = 'gbs_move_sprite_world'
            verbs['meta_cols'] = 'gbs_meta_cols'
        if self.metasprite_used:
            verbs['set_meta'] = 'gbs_set_metasprite'
            verbs['set_tile'] = 'gbs_set_sprite_tile'
            verbs['set_prop'] = 'gbs_set_sprite_prop'
            if self.meta_mask_used:
                verbs['set_meta_mask'] = 'gbs_set_metasprite_mask'
            if self.meta_list_used:
                verbs['set_meta_list'] = 'gbs_set_metasprite_list'
            if self.meta_palettes_used:
                verbs['set_meta_palettes'] = 'gbs_set_meta_pal'
        elif self._spr_keep_pal():
            # the palette-preserving prop wrapper (_emit_gbdk_prop_wrapper)
            verbs['set_prop'] = 'gbs_set_sprite_prop'
        used = set()

        def walk(node):
            if isinstance(node, FunctionCall):
                fn = node.function
                if (isinstance(fn, FieldAccess)
                        and isinstance(fn.object, Identifier)
                        and fn.object.name == 'sprite'
                        and fn.field in verbs):
                    used.add(verbs[fn.field])
            if isinstance(node, ASTNode):
                for f in dataclasses.fields(node):
                    walk(getattr(node, f.name))
            elif isinstance(node, (list, tuple)):
                for x in node:
                    walk(x)

        for func in funcs:
            walk(func)
        # Closures: the plain set_meta wrapper is emitted beside the masked
        # form (it no longer calls it - the dense set fan - but the emitter
        # writes the pair together); the per-cell palette fan reads
        # gbs_pal_prop.
        if 'gbs_set_metasprite' in used and self.meta_mask_used:
            used.add('gbs_set_metasprite_mask')
        # move_world resolves the sign and then delegates to the plain fan.
        if 'gbs_move_sprite_world' in used:
            used.add('gbs_move_sprite')
        if 'gbs_set_meta_pal' in used:
            used.add('gbs_pal_prop')
        if (main_tu and self.metasprite_used and self.sprite_palette_used
                and self._spr_pal_mask() is not None):
            # gbs_sprite_palette (the resident palette engine) fans through
            # these three - main TU only.
            used.update(('gbs_fan_prop', 'gbs_meta_prop_of', 'gbs_pal_prop'))
        return used

    def _obj16(self):
        """8x16 OBJ mode is EFFECTIVE: requested by the build AND on a console
        that HAS the mode (the GB family's LCDC bit 2, the SMS/GG VDP R1
        sprite-size bit). `platforms.obj16_effective` is the one source of
        truth - the asset reorder, the generated rooms.mos fan halving and the
        generated clips.mos descriptor shape all read the same set, and a
        disagreement between any two of them draws garbage."""
        return self.framework == 'gbdk' and obj16_effective(self.platform,
                                                            self.obj_8x16)

    def _emit_gbdk_meta_tables(self, extern):
        """The metasprite side tables + their #defines, once per TU.

        `extern=False` (the main TU) DEFINES them - bare arrays, so they are
        BSS and cost no resident image; `extern=True` (a bank TU) declares
        the same names. Non-static on purpose: the per-TU `static` functions
        below are compiled into whichever TU calls them and every copy must
        read the ONE set of arrays."""
        # Size the side tables to the console's hardware sprite count (the GB
        # family has 40 OAM entries; the NES and SMS/GG VDPs have 64) and
        # bounds-guard every entry point: the tables are adjacent, so an
        # unguarded slot >= the table size would silently corrupt a sibling
        # (a stray gbs_meta_w write landing in gbs_meta_h mis-fans later
        # moves).
        hw_sprites = 64 if self.platform in ('nes', 'sms', 'gamegear') else 40
        # PARKING A CHILD IS NOT `move_sprite(s, 0, 0)`.
        #
        # On the Game Boy, OAM coordinates are biased by (8, 16), so y = 0 is
        # 16 px ABOVE the screen and a parked object vanishes. The z80 ports
        # have no such bias: the SMS writes the SAT directly, so (0, 0) is the
        # VISIBLE top-left corner. Every stale child parked that way sat in the
        # corner of the screen wearing whatever tile it last held - measured on
        # the shooter conversion (SMS), whose `lives` readout is a DESCRIPTOR kind with a
        # different object count per frame (0/1/2/3 hearts), so dropping a life
        # left the retired object parked at (0, 0) as a white block. The Game
        # Gear hid it by accident: its 160x144 viewport is a centre crop of the
        # same 256x192 plane, so plane (0, 0) is off the visible window there.
        #
        # 200 is the value the rest of the engine already parks at
        # (`sprite.move(s, 200, 200)` in the generated room load, vm.projectile's
        # own park). It is off-screen on the GB family (screen 144) and on
        # SMS/GG (192), and it deliberately avoids the SMS's 0xD0 = 208, which
        # TERMINATES the sprite list and would hide every object after it.
        self.emit("#define GBS_SPR_PARK_Y 200  /* off-screen on EVERY GBDK console */")
        self.emit("#define GBS_META_SLOTS %d  /* hardware sprite count */"
                  % hw_sprites)
        kw = "extern uint8_t" if extern else "uint8_t"
        unit = "8x8-TILE units" if self._obj16() else "0/1 = single sprite"
        self.emit("%s gbs_meta_w[GBS_META_SLOTS];   /* 0/1 = single sprite */" % kw)
        self.emit("%s gbs_meta_h[GBS_META_SLOTS];   /* %s */" % (kw, unit))
        self.emit("%s gbs_meta_prop[GBS_META_SLOTS];" % kw)
        self._emit_meta_mask_table(extern)
        self._emit_meta_list_table(extern)
        self._emit_meta_clip_table(extern)

    def _emit_gbdk_metasprite(self):
        """Metasprite layer (emitted only when sprite.set_meta is used): the
        per-TU-gated `static` functions, in the mode the build asks for.

        A metasprite at base slot B with W*H tiles reserves the OAM slots
        B..B+W*H-1; set_meta assigns them tiles row-major from the base tile,
        and gbs_move_sprite lays them out as an 8x8 grid (flip-aware). The
        meta tables default to zero (== single sprite) so untouched slots and
        non-metasprite programs behave exactly as before. set_tile/set_prop on
        a metasprite base fan out to the children (the stdlib map routes them
        to these wrappers only while metasprites are in use).

        **8x16 OBJ mode (`[build] obj_8x16`, G4)**: the hardware pairs tile N
        with N+1 as its VERTICAL neighbour, and the asset pipeline stores each
        frame's tiles COLUMN-major to match (reorder_tiles_8x16) -- so the
        data stays fully contiguous and the fan is simply `w * h/2` objects
        taking tiles t, t+2, t+4, ... The meta tables keep 8x8-TILE units
        (`sprite.set_meta`'s contract is unchanged); only the fan math and the
        move layout know about the mode. `h` must be even -- the asset
        pipeline refuses an odd frame at build time, so the runtime never
        sees one."""
        self._emit_spr_keep_pal_macro()
        self._emit_gbdk_pal_prop()
        self._emit_gbdk_meta_prop_of()
        if self._obj16():
            self._emit_gbdk_metasprite_8x16()
        else:
            self._emit_gbdk_metasprite_8x8()

    def _want_set_meta(self):
        return (self._needs_meta('gbs_set_metasprite')
                or self._needs_meta('gbs_set_metasprite_mask'))

    def _emit_gbdk_metasprite_8x8(self):
        self.emit("/* --- Metasprite layer (graphics.sprite sprite.set_meta) --- */")
        self._emit_gbdk_meta_pal(False)
        prop_arg = ("GBS_KEEP_PAL(s, prop)"
                    if self._spr_keep_pal() else "prop")
        if self._want_set_meta():
            if self.meta_mask_used:
                self.emit("static void gbs_set_metasprite_mask(uint8_t base, uint8_t tile, uint8_t w,")
                self.emit("                                    uint8_t h, uint16_t mask) {")
            else:
                self.emit("static void gbs_set_metasprite(uint8_t base, uint8_t tile, uint8_t w, uint8_t h) {")
            self.emit("    uint8_t r, c, s = base, t = tile, prop;")
            self.emit("    if (base >= GBS_META_SLOTS ||")
            self.emit("        (uint16_t)(w * h) > (uint16_t)(GBS_META_SLOTS - base)) return;")
            self.emit("    prop = gbs_meta_prop[base];")
            self.emit("    for (r = 0; r < h; ++r)")
            self.emit("        for (c = 0; c < w; ++c) {")
            if self.meta_mask_used:
                self.emit("            if (mask & ((uint16_t)1 << c)) {")
                self.emit("                move_sprite(s, 0, GBS_SPR_PARK_Y);   /* blank cell: park, keep the tile */")
                self.emit("            } else {")
                self.emit("                set_sprite_tile(s, t);")
                self.emit("                set_sprite_prop(s, %s);" % prop_arg)
                self.emit("                ++t;")
                self.emit("            }")
                self.emit("            gbs_meta_w[s] = 1; gbs_meta_h[s] = 1;")
                self.emit("            ++s;")
            else:
                self.emit("            set_sprite_tile(s, t);")
                self.emit("            set_sprite_prop(s, %s);" % prop_arg)
                self.emit("            gbs_meta_w[s] = 1; gbs_meta_h[s] = 1;")
                self.emit("            ++s; ++t;")
            self.emit("        }")
            self.emit("    gbs_meta_w[base] = w; gbs_meta_h[base] = h;")
            if self.meta_mask_used:
                self.emit("    gbs_meta_msk[base] = mask;")
            self.emit("}")
            self._emit_meta_mask_plain()
        self._emit_meta_list_func(False)
        if self._needs_meta('gbs_set_sprite_tile'):
            self.emit("/* set_tile on a metasprite base re-tiles all its children (row-major). */")
            self.emit("static void gbs_set_sprite_tile(uint8_t nb, uint8_t tile) {")
            self.emit("    uint8_t w, h;")
            self.emit("    if (nb >= GBS_META_SLOTS) return;")
            self.emit("    w = gbs_meta_w[nb]; h = gbs_meta_h[nb];")
            if self.meta_list_used:
                self.emit("    if (w == 0xFF) return;  /* a LIST base re-tiles only via set_meta_list */")
            self.emit("    if (w > 1 || h > 1) {")
            self.emit("        uint8_t r, c, s = nb, t = tile;")
            if self.meta_mask_used:
                self.emit("        uint16_t m = gbs_meta_msk[nb];")
                self.emit("        for (r = 0; r < h; ++r)")
                self.emit("            for (c = 0; c < w; ++c) {")
                self.emit("                if (!(m & ((uint16_t)1 << c))) { set_sprite_tile(s, t); ++t; }")
                self.emit("                ++s;")
                self.emit("            }")
            else:
                self.emit("        for (r = 0; r < h; ++r)")
                self.emit("            for (c = 0; c < w; ++c) { set_sprite_tile(s, t); ++s; ++t; }")
            self.emit("        return;")
            self.emit("    }")
            self.emit("    set_sprite_tile(nb, tile);")
            self.emit("}")
        self._emit_gbdk_prop_setters("(uint8_t)(w * h)")

    def _emit_meta_list_table(self, extern):
        """Per-slot state for LIST-shaped metasprites (sprite.set_meta_list):
        each child's authored (dx, dy), and per BASE the frame's pixel width
        (what FLIP_X mirrors around). The base's marker is gbs_meta_w == 0xFF
        with the object COUNT in gbs_meta_h. All BSS (no resident cost)."""
        if not self.meta_list_used:
            return
        kw = "extern uint8_t" if extern else "uint8_t"
        self.emit("/* Per-object descriptor state (sprite.set_meta_list): a child's")
        self.emit("   authored offset from the frame origin, stored at set time so the")
        self.emit("   move fan is one add per axis per child. gbs_meta_w[base] == 0xFF")
        self.emit("   marks a list-shaped base; gbs_meta_h[base] is its object count. */")
        self.emit("%s gbs_meta_dx[GBS_META_SLOTS];" % kw)
        self.emit("%s gbs_meta_dy[GBS_META_SLOTS];" % kw)
        self.emit("%s gbs_meta_pw[GBS_META_SLOTS];" % kw)

    def _emit_meta_list_func(self, obj16):
        """`gbs_set_metasprite_list` -- the reference engine's metasprite_t model: n
        entries of (dy, dx, dtile, props) at `d + off`. Offsets are from the
        frame's top-left (unsigned - the importer re-bases them); dtile is
        added to `tile` (the sheet's VRAM base) and may REPEAT, which is the
        tile dedupe a dense rectangle cannot express; props OR into the
        latched prop (flip/palette survive). Children beyond this frame's
        count that the PREVIOUS frame used are parked - frames of one kind
        differ in object count, and a stale child would linger on screen.

        The C body is mode-agnostic: under obj_8x16 the DATA simply carries
        8x16-object offsets and even dtiles (the generated clips module forks
        per platform), so only the stale-count arithmetic differs."""
        if not self.meta_list_used:
            return
        if not self._needs_meta('gbs_set_metasprite_list'):
            return
        prop_arg = ("GBS_KEEP_PAL(s, pp)" if self._spr_keep_pal() else "pp")
        self.emit("static void gbs_set_metasprite_list(uint8_t base, uint8_t pw, uint8_t tile,")
        self.emit("                                    const uint8_t *d, uint16_t off, uint8_t n) {")
        self.emit("    uint8_t k, s = base, prev, prop, pp;")
        self.emit("    if (base >= GBS_META_SLOTS || n > (uint8_t)(GBS_META_SLOTS - base)) return;")
        self.emit("    prop = gbs_meta_prop[base];")
        self.emit("    if (gbs_meta_w[base] == 0xFF) prev = gbs_meta_h[base];")
        if obj16:
            self.emit("    else prev = (uint8_t)(gbs_meta_w[base] * (uint8_t)(gbs_meta_h[base] >> 1));")
        else:
            self.emit("    else prev = (uint8_t)(gbs_meta_w[base] * gbs_meta_h[base]);")
        self.emit("    if (prev > (uint8_t)(GBS_META_SLOTS - base)) prev = (uint8_t)(GBS_META_SLOTS - base);")
        self.emit("    d += off;")
        self.emit("    for (k = 0; k < n; ++k) {")
        self.emit("        gbs_meta_dy[s] = *d++;")
        self.emit("        gbs_meta_dx[s] = *d++;")
        self.emit("        set_sprite_tile(s, (uint8_t)(tile + *d++));")
        self.emit("        pp = (uint8_t)(prop | *d++);")
        self.emit("        set_sprite_prop(s, %s);" % prop_arg)
        self.emit("        gbs_meta_w[s] = 1; gbs_meta_h[s] = 1;")
        self.emit("        ++s;")
        self.emit("    }")
        self.emit("    for (; s < (uint8_t)(base + prev); ++s) move_sprite(s, 0, GBS_SPR_PARK_Y);")
        self.emit("    gbs_meta_w[base] = 0xFF;   /* list-shaped */")
        self.emit("    gbs_meta_h[base] = n;")
        self.emit("    gbs_meta_pw[base] = pw;")
        self.emit("}")

    def _emit_meta_clip_table(self, extern):
        """The EDGE CLIP - which of a metasprite's columns fall off the screen
        and must be PARKED rather than drawn (sprite.move_world).

        `sprite.move`'s x is a uint8_t, so a world-space renderer whose actor
        has scrolled past the left edge hands the fan an already-WRAPPED
        origin: screen x -11 arrives as 245, and the fan then walks +8 from
        there, so the leading columns draw at the far RIGHT of the screen and
        the rest wrap round to the left. Measured on the reference-engine conversion
        (GB, the parallax room): a 9-column actor at screen x -11 drew
        seven columns at OAM x 5..53 and a ghost at 253. It is symmetric - a
        wide actor ENTERING from the right wraps its trailing columns onto the
        left edge - and it is not console-specific, though the SMS shows it
        worst because its screen IS the whole 256 px plane, so no u8 value is
        off-screen and the wrap cannot even be detected here.

        Hence a SIGNED entry point. `gbs_move_sprite_world` resolves the sign
        into these bounds once per metasprite, in COLUMN units and already in
        fan order (so FLIP_X costs the fan nothing), and the walk stays 8-bit.
        `gbs_clip_hi == 0` is the NO-CLIP state, which is what plain BSS gives
        - so `sprite.move` behaves exactly as before and a program that never
        calls move_world is byte-identical."""
        if not self.meta_clip_used:
            return
        kw = "extern uint8_t" if extern else "uint8_t"
        self.emit("/* Edge clip for sprite.move_world: draw fan columns")
        self.emit("   [gbs_clip_lo, gbs_clip_hi) and PARK the rest. hi == 0 means no")
        self.emit("   clip, which is the BSS default - plain sprite.move is unchanged. */")
        self.emit("%s gbs_clip_lo;" % kw)
        self.emit("%s gbs_clip_hi;" % kw)
        if self.meta_list_used:
            self.emit("/* ...and the same bounds in authored dx for a LIST-shaped base,")
            self.emit("   whose children sit at arbitrary offsets rather than on a grid. */")
            self.emit("%s gbs_clip_dlo;" % kw)
            self.emit("%s gbs_clip_dhi;" % kw)

    def _emit_meta_mask_table(self, extern):
        """The per-base BLANK-COLUMN mask (sprite.set_meta_mask), emitted only
        when a program calls it -- a program that does not stays byte-identical.

        BSS, so it costs no resident image (the bank-0 rule: an initialized
        global lands in the bank-0 initializer, a bare one does not)."""
        if not self.meta_mask_used:
            return
        kw = "extern uint16_t" if extern else "uint16_t"
        self.emit("/* Per-metasprite BLANK-COLUMN mask (sprite.set_meta_mask): bit c set")
        self.emit("   = column c of this frame draws nothing, so its objects park off")
        self.emit("   screen and consume no tile. A SPARSE frame - the reference engine places a")
        self.emit("   metasprite's tiles at authored offsets and leaves gaps, and a blank")
        self.emit("   column still costs one of the GB's 10 sprites per scanline. */")
        self.emit("%s gbs_meta_msk[GBS_META_SLOTS];" % kw)

    def _emit_meta_mask_plain(self):
        """`sprite.set_meta` when the masked form is in use: THE DENSE SET FAN
        (2026-09-05). It used to forward to the masked body with an empty
        mask - one body, so the two could not drift - and that body walks
        w columns testing `mask & (1 << c)` (a variable u16 shift, a
        library call on the sm83) around a nested per-column loop, all under
        one stack frame. Measured on the reference-engine sample conversion's shooter room, where the
        player's ship steps its clip every game frame: the two-object upload
        cost ~3.2k T-cycles of draw_player's 9.4k (per-instruction profile,
        tools/framebudget/addr_profile.py).

        The empty-mask upload is every dense frame of every actor, shot and
        player, so it gets its own flat body - the gbs_move_fan shape: n
        objects in one loop, no shift, no nesting - and vm.canim's draw_meta
        routes a zero mask here. What the masked body wrote for mask 0 this
        writes identically (tile, prop, the per-child 1x1 shape, the base's
        w/h) AND it still clears the base's mask, which is the invariant the
        forwarder existed for: a stale mask would blank a later frame's
        columns. The cc65 wrapper keeps forwarding (its body is not the
        cost there and its ROMs stay md5-identical)."""
        if not self.meta_mask_used:
            return
        if not self._needs_meta('gbs_set_metasprite'):
            return
        prop_arg = ("GBS_KEEP_PAL(s, prop)"
                    if self._spr_keep_pal() else "prop")
        self.emit("/* The dense set fan: sprite.set_meta with the mask table in use - the")
        self.emit("   empty-mask upload in its own flat body (see the emitter). */")
        self.emit("static void gbs_set_metasprite(uint8_t base, uint8_t tile, uint8_t w, uint8_t h) {")
        self.emit("    uint8_t k, n, s = base, t = tile, prop;")
        if self._obj16():
            self.emit("    n = (uint8_t)(w * (uint8_t)(h >> 1));")
            self.emit("    if (base >= GBS_META_SLOTS || (uint16_t)(w * (uint8_t)(h >> 1)) > (uint16_t)(GBS_META_SLOTS - base)) return;")
        else:
            self.emit("    n = (uint8_t)(w * h);")
            self.emit("    if (base >= GBS_META_SLOTS || (uint16_t)(w * h) > (uint16_t)(GBS_META_SLOTS - base)) return;")
        self.emit("    prop = gbs_meta_prop[base];")
        self.emit("    for (k = 0; k < n; ++k) {")
        self.emit("        set_sprite_tile(s, t);")
        self.emit("        set_sprite_prop(s, %s);" % prop_arg)
        self.emit("        gbs_meta_w[s] = 1; gbs_meta_h[s] = 1;")
        if self._obj16():
            self.emit("        ++s; t += 2;")
        else:
            self.emit("        ++s; ++t;")
        self.emit("    }")
        self.emit("    gbs_meta_w[base] = w; gbs_meta_h[base] = h;")
        self.emit("    gbs_meta_msk[base] = 0;")
        self.emit("}")

    def _emit_gbdk_metasprite_8x16(self):
        """The 8x16-mode metasprite layer (see _emit_gbdk_metasprite's note).

        A W x H TILE metasprite is W * H/2 hardware OBJECTS. Column-major tile
        data means consecutive objects take tiles t, t+2, t+4, ... with no
        per-column arithmetic, and every object's tile index stays EVEN (the
        hardware masks bit 0 in this mode). The meta tables still store 8x8
        TILE units, so sprite.set_meta's call sites are untouched."""
        self.emit("/* --- Metasprite layer, 8x16 OBJ mode ([build] obj_8x16) ---")
        self.emit("   A WxH TILE block is W*(H/2) hardware objects; the sprite data is")
        self.emit("   COLUMN-major per frame (the asset pipeline reorders it), so the fan")
        self.emit("   just steps the tile by 2 per object. Tables keep 8x8-TILE units. */")
        self._emit_gbdk_meta_pal(True)
        prop_arg = ("GBS_KEEP_PAL(s, prop)"
                    if self._spr_keep_pal() else "prop")
        if self._want_set_meta():
            if self.meta_mask_used:
                self.emit("static void gbs_set_metasprite_mask(uint8_t base, uint8_t tile, uint8_t w,")
                self.emit("                                    uint8_t h, uint16_t mask) {")
                self.emit("    uint8_t c, p, h2 = (uint8_t)(h >> 1), n, s = base, t = tile, prop;")
                # WIDE product check (see _emit_gbdk_meta_pal): a u8-truncated
                # w * h2 let a garbage size slip through and fan across WRAM.
                self.emit("    n = (uint8_t)(w * h2);")
                self.emit("    if (base >= GBS_META_SLOTS || (uint16_t)(w * h2) > (uint16_t)(GBS_META_SLOTS - base)) return;")
                self.emit("    prop = gbs_meta_prop[base];")
                self.emit("    for (c = 0; c < w; ++c) {")
                self.emit("        if (mask & ((uint16_t)1 << c)) {")
                self.emit("            /* blank column: park its objects, consume no tile */")
                self.emit("            for (p = 0; p < h2; ++p) {")
                self.emit("                move_sprite(s, 0, GBS_SPR_PARK_Y);")
                self.emit("                gbs_meta_w[s] = 1; gbs_meta_h[s] = 1;")
                self.emit("                ++s;")
                self.emit("            }")
                self.emit("        } else {")
                self.emit("            for (p = 0; p < h2; ++p) {")
                self.emit("                set_sprite_tile(s, t);")
                self.emit("                set_sprite_prop(s, %s);" % prop_arg)
                self.emit("                gbs_meta_w[s] = 1; gbs_meta_h[s] = 1;")
                self.emit("                ++s; t += 2;")
                self.emit("            }")
                self.emit("        }")
                self.emit("    }")
                self.emit("    gbs_meta_w[base] = w; gbs_meta_h[base] = h;")
                self.emit("    gbs_meta_msk[base] = mask;")
                self.emit("}")
            else:
                self.emit("static void gbs_set_metasprite(uint8_t base, uint8_t tile, uint8_t w, uint8_t h) {")
                self.emit("    uint8_t k, n, s = base, t = tile, prop;")
                self.emit("    n = (uint8_t)(w * (uint8_t)(h >> 1));")
                self.emit("    if (base >= GBS_META_SLOTS || (uint16_t)(w * (uint8_t)(h >> 1)) > (uint16_t)(GBS_META_SLOTS - base)) return;")
                self.emit("    prop = gbs_meta_prop[base];")
                self.emit("    for (k = 0; k < n; ++k) {")
                self.emit("        set_sprite_tile(s, t);")
                self.emit("        set_sprite_prop(s, %s);" % prop_arg)
                self.emit("        gbs_meta_w[s] = 1; gbs_meta_h[s] = 1;")
                self.emit("        ++s; t += 2;")
                self.emit("    }")
                self.emit("    gbs_meta_w[base] = w; gbs_meta_h[base] = h;")
                self.emit("}")
            self._emit_meta_mask_plain()
        self._emit_meta_list_func(True)
        if self._needs_meta('gbs_set_sprite_tile'):
            self.emit("/* set_tile on a metasprite base re-tiles all its children. */")
            self.emit("static void gbs_set_sprite_tile(uint8_t nb, uint8_t tile) {")
            self.emit("    uint8_t w, h;")
            self.emit("    if (nb >= GBS_META_SLOTS) return;")
            self.emit("    w = gbs_meta_w[nb]; h = gbs_meta_h[nb];")
            if self.meta_list_used:
                self.emit("    if (w == 0xFF) return;  /* a LIST base re-tiles only via set_meta_list */")
            self.emit("    if (w > 1 || h > 1) {")
            if self.meta_mask_used:
                self.emit("        uint8_t c, p, h2 = (uint8_t)(h >> 1), s = nb, t = tile;")
                self.emit("        uint16_t m = gbs_meta_msk[nb];")
                self.emit("        for (c = 0; c < w; ++c) {")
                self.emit("            if (m & 1) { s = (uint8_t)(s + h2); }")
                self.emit("            else {")
                self.emit("                for (p = 0; p < h2; ++p) { set_sprite_tile(s, t); ++s; t += 2; }")
                self.emit("            }")
                self.emit("            m >>= 1;")
                self.emit("        }")
            else:
                self.emit("        uint8_t k, n, s = nb, t = tile;")
                self.emit("        n = (uint8_t)(w * (uint8_t)(h >> 1));")
                self.emit("        for (k = 0; k < n; ++k) { set_sprite_tile(s, t); ++s; t += 2; }")
            self.emit("        return;")
            self.emit("    }")
            self.emit("    set_sprite_tile(nb, tile);")
            self.emit("}")
        self._emit_gbdk_prop_setters("(uint8_t)(w * (uint8_t)(h >> 1))")
