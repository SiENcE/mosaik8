"""Cc65Backend: the Lynx-native sprite modes, mixed into Cc65Backend.

Two `[build]` knobs, both Lynx-only and both byte-identical at their default:

* `lynx_sprites = "whole"` -- every named sprite of a sheet (one
  `.sprites.toml` rectangle) is ONE literal Suzy image, baked at build time
  (`lynx_images.py`), so a slot draws the whole picture with one SCB. Suzy
  pays per sprite LINE (~6 us measured in GearLynx and Handy): a 16x16 is 16
  lines whole and 32 as four 8x8 tiles. `sprite.set_meta` of a named sprite
  collapses to its base slot.
* `lynx_orientation = "portrait_left" | "portrait_right"` -- the player turns
  the console a quarter turn. The program sees a 102x160 screen; the art is
  baked already rotated, positions and flips are mapped on the way to the
  SCB, and the d-pad is turned with the screen.

Either knob selects the BAKED engine below instead of the run-time 8x8
converter: tile ids address build-time images (`gbs_img[tile]`), so the
40-tile table and its conversion are gone too. With portrait on and
`lynx_sprites` left at "tiles", every tile is its own 8x8 image and the
metasprite fan works tile by tile, exactly as in landscape.

Positions stay LOGICAL u8 screen pixels per slot (`gbs_spr_lx/ly`), and one
function (`gbs_spr_place`) turns position, image size and flips into the
SCB's physical hpos/vpos and SPRCTL0. A flipped Suzy sprite is mirrored
about its reference point (the hardware manual: "The pivot point is the
sprite's reference point"), so a flip moves the reference to the far edge
to keep the picture where the program put it.
"""

from .lynx_images import sheet_images


class Cc65LynxNativeMixin:
    # Physical Lynx screen, which the baked engine maps the logical one onto.
    LYNX_PHYS_W = 160
    LYNX_PHYS_H = 102

    @property
    def _lynx_baked(self):
        """The baked-image sprite engine is in use (Lynx + either knob)."""
        return (self.platform == 'lynx'
                and bool(getattr(self, 'lynx_whole_sprites', False)
                         or getattr(self, 'lynx_orient', None)))

    def _lynx_logical_screen(self):
        """(width, height, cols, rows) the PROGRAM sees on the Lynx."""
        if getattr(self, 'lynx_orient', None):
            return (self.LYNX_PHYS_H, self.LYNX_PHYS_W,
                    self.LYNX_PHYS_H // 8, self.LYNX_PHYS_W // 8)
        return None

    # ------------------------------------------------------------ the images
    def _resolve_lynx_baked_sheets(self, program):
        """Which sheets get build-time images: the ones the program hands to
        `sprite.set_data` by name (`<sheet>_tiles`, or a pointer into one). A
        sheet only ever uploaded to the BACKGROUND costs no image. Any upload
        whose data is not a plain sheet name keeps every sheet (None)."""
        self._lynx_sprite_sheets = None
        self._lynx_no_raw = set()
        if not self._lynx_baked:
            return
        from ..ast_nodes import Identifier, BinaryOp
        calls = []
        for module in program.modules:
            self._sprite_set_data_calls(module, calls)
        names = set()
        plain = {}                                      # name -> uses as a plain arg
        for call in calls:
            arg = call.arguments[2]
            if isinstance(arg, Identifier) and arg.name.endswith("_tiles"):
                plain[arg.name] = plain.get(arg.name, 0) + 1
            while isinstance(arg, BinaryOp):          # `sheet_tiles + k * 16`
                arg = arg.left
            if not (isinstance(arg, Identifier) and arg.name.endswith("_tiles")):
                return                                  # unknown: keep them all
            names.add(arg.name[:-len("_tiles")])
        self._lynx_sprite_sheets = names
        # A sheet whose `<sheet>_tiles` is named NOWHERE but as a whole
        # sprite.set_data upload needs no raw tile bytes at all: the images
        # are the picture, and the upload is handed the image table instead.
        # Kept when a child must convert a single tile at run time (it reads
        # the raw source), and in a banked build (the tables are TU-local).
        if self._lynx_singles or getattr(self, '_cc65_banking', False):
            return
        uses = {}
        self._count_idents(program, uses)
        self._lynx_no_raw = {n[:-len("_tiles")] for n, k in plain.items()
                             if uses.get(n) == k}

    def _count_idents(self, node, out):
        """Every Identifier name under `node`, counted."""
        import dataclasses
        from ..ast_nodes import ASTNode, Identifier
        if isinstance(node, Identifier):
            out[node.name] = out.get(node.name, 0) + 1
        if isinstance(node, ASTNode):
            for f in dataclasses.fields(node):
                self._count_idents(getattr(node, f.name), out)
        elif isinstance(node, (list, tuple)):
            for x in node:
                self._count_idents(x, out)

    def _lynx_raw_elided(self, name):
        """True when sheet `name` is emitted as images only (no raw tiles)."""
        return name in (getattr(self, '_lynx_no_raw', None) or ())

    def _lynx_baked_sheets(self):
        """The sheets the baked engine draws: every asset resident in this
        translation unit (a streamed sheet has no build-time image) that the
        program uploads as SPRITES (`_resolve_lynx_baked_sheets`)."""
        wanted = getattr(self, '_lynx_sprite_sheets', None)
        out = []
        for name, data, bpp in self.assets:
            sym = "%s_tiles" % name
            if sym in self.sheet_stream or sym in self.asset_far_bank:
                continue
            if sym in getattr(self, 'baked_stream', {}):
                continue                     # its images are on the cart
            if wanted is not None and name not in wanted:
                continue
            out.append((name, data, bpp))
        return out

    def _emit_lynx_baked_images(self):
        """The build-time Suzy images and `gbs_img_table`, the lookup from an
        uploaded sheet pointer to its per-tile image table. Emitted after the
        assets (the table compares against `<sheet>_tiles`)."""
        if not self._lynx_baked or not self.caps.get('has_sprites'):
            return
        whole = bool(getattr(self, 'lynx_whole_sprites', False))
        orient = getattr(self, 'lynx_orient', None)
        rects_of = getattr(self, 'sheet_rects', {}) or {}
        self.emit("/* --- Lynx baked sprite images (%s%s): one literal Suzy"
                  % ("whole sprites" if whole else "8x8 tiles",
                     ", " + orient if orient else ""))
        self.emit("   sprite per image, [logical w, logical h, data...]. --- */")
        seen = {}                        # blob bytes -> C name (dedupe)
        tables = []
        n = 0
        for name, data, bpp in self._lynx_baked_sheets():
            rects = rects_of.get(name, []) if whole else []
            blobs, table, _one = sheet_images(data, bpp, rects, orient)
            names = []
            for blob in blobs:
                key = bytes(blob)
                if key not in seen:
                    cname = "gbs_li_%d" % n
                    n += 1
                    seen[key] = cname
                    self.emit("static const uint8_t %s[%d] = {" % (cname, len(blob)))
                    for i in range(0, len(blob), 16):
                        self.emit("    " + " ".join("0x%02X," % b
                                                    for b in blob[i:i + 16]))
                    self.emit("};")
                names.append(seen[key])
            self._emit_lynx_img_table("gbs_lt_%s" % name, table, names)
            if self._lynx_raw_elided(name):
                self.emit("#define %s_tiles ((const uint8_t *)gbs_lt_%s)  /* images only */"
                          % (name, name))
            tables.append((name, len(data), 32 if bpp == 4 else 16))
        self.emit("/* An uploaded sheet pointer (or a pointer into one) -> its images. */")
        self.emit("const uint8_t * const *gbs_img_table(const uint8_t *d) {")
        for name, size, tsize in tables:
            if self._lynx_raw_elided(name):
                # no raw tiles: `<sheet>_tiles` names the image table itself
                self.emit("    if (d == (const uint8_t *)gbs_lt_%s) return gbs_lt_%s;"
                          % (name, name))
                continue
            self.emit("    if (d >= %s_tiles && d < %s_tiles + %d)" % (name, name, size))
            self.emit("        return gbs_lt_%s + (uint16_t)(d - %s_tiles) / %d;"
                      % (name, name, tsize))
        self.emit("    (void)d;")
        self.emit("    return 0;")
        self.emit("}")
        self.emit("")

    def _emit_lynx_img_table(self, cname, table, names):
        entries = [names[t] if t is not None else "0" for t in table]
        self.emit("static const uint8_t * const %s[%d] = {" % (cname, max(1, len(entries))))
        for i in range(0, len(entries), 8):
            self.emit("    " + ", ".join(entries[i:i + 8]) + ",")
        if not entries:
            self.emit("    0")
        self.emit("};")

    @property
    def _lynx_singles(self):
        """Whole sprites + a per-object form (descriptor lists, masked fans):
        those children draw ONE tile each. Baking an 8x8 of every tile would
        double the images, so a child's tile is converted at run time into
        its SLOT's own buffer instead (`gbs_one_buf`), and only when the tile
        it shows changes."""
        return bool(self.meta_list_used or self.meta_mask_used)

    # ------------------------------------------------------------ the engine
    def _lynx_check_baked(self):
        """Refuse what the baked engine does not (yet) draw, by name."""
        if getattr(self, 'sheet_stream', None):
            raise RuntimeError(
                "[build] lynx_sprites / lynx_orientation: sprite sheets streamed "
                "from the cart ([world] stream) are not supported yet")

    def _emit_lynx_baked_engine(self, prof, with_bkg, sw, sh, clear_pen, bpp4):
        """The baked-image Suzy sprite engine (see the module docstring)."""
        self._lynx_check_baked()
        whole = bool(getattr(self, 'lynx_whole_sprites', False))
        orient = getattr(self, 'lynx_orient', None)
        bpp_macro = 'BPP_4' if bpp4 else 'BPP_2'
        max_slots = self.sprite_max_slots or self.CC65_MAX_SPRITES
        max_slots = max(1, min(self.CC65_MAX_SPRITES, int(max_slots)))
        lw, lh = (sh, sw) if orient else (sw, sh)
        self.emit("/* --- Suzy sprite engine, BAKED images (Atari Lynx, %s%s) --- */"
                  % ("whole sprites" if whole else "8x8 tiles",
                     ", " + orient if orient else ""))
        self.emit("#define GBS_MAX_SPRITES %d" % max_slots)
        self.emit("#define GBS_LOG_W %d   /* the screen the program sees */" % lw)
        self.emit("#define GBS_LOG_H %d" % lh)
        self.emit("typedef struct { SCB_REHV_PAL s; uint8_t pad_[9]; } gbs_scb_t;")
        self.emit("static gbs_scb_t gbs_scb[GBS_MAX_SPRITES];")
        self.emit("static uint8_t gbs_spr_tile[GBS_MAX_SPRITES];")
        self.emit("static uint8_t gbs_spr_lx[GBS_MAX_SPRITES];   /* logical position */")
        self.emit("static uint8_t gbs_spr_ly[GBS_MAX_SPRITES];")
        self.emit("static uint8_t gbs_spr_pr[GBS_MAX_SPRITES];   /* logical FLIP_X | FLIP_Y */")
        self.emit("static const uint8_t *gbs_spr_im[GBS_MAX_SPRITES];  /* image: [lw, lh, data] */")
        self.emit("static uint8_t gbs_spr_e[GBS_MAX_SPRITES];   /* 1 = the image draws nothing */")
        self.emit("/* Image per tile id, filled by sprite.set_data from the baked tables")
        self.emit("   (0 = nothing to draw: a tile inside a whole sprite, or none uploaded). */")
        self.emit("static const uint8_t *gbs_img[256];")
        self.emit("static const uint8_t gbs_img_none[3] = { 0, 0, 0 };  /* 0x0 px, end of data */")
        self.emit("const uint8_t * const *gbs_img_table(const uint8_t *d);")
        singles = whole and self._lynx_singles
        if singles:
            tb = 2 + 8 * ((4 if bpp4 else 2) + 2) + 1   # header + 8 lines + end
            self.emit("/* A descriptor-list or masked-fan child draws ONE tile (gbs_spr_one is")
            self.emit("   raised while those place children): converted at run time from the")
            self.emit("   uploaded source into the slot's own buffer, only when it changes. */")
            self.emit("static const uint8_t *gbs_src[256];   /* source tile per tile id */")
            self.emit("static uint8_t gbs_one_buf[GBS_MAX_SPRITES][%d];" % tb)
            self.emit("static const uint8_t *gbs_one_at[GBS_MAX_SPRITES];  /* source in the buffer */")
            self.emit("static uint8_t gbs_spr_one = 0;")
            self.emit("static uint8_t gbs_spr_sm[GBS_MAX_SPRITES];   /* slot shows a single tile */")
        self.emit("static uint8_t gbs_spr_max = 0;     /* highest slot touched + 1 */")
        self.emit("static uint8_t gbs_spr_used = 0;    /* engine active this program */")
        self.emit("static uint8_t gbs_spr_visible = 1;")
        self.emit("static uint8_t gbs_spr_db = 0;      /* double-buffering engaged */")
        self.emit("uint8_t gbs_draw_page = 0;    /* page tgi_sprite/text draw to now (tracks the flip) */")
        self.emit("static uint8_t gbs_spr_inited = 0;")
        if self.metasprite_used:
            self._emit_cc65_meta_state()
        if bpp4:
            self.emit("static const uint8_t gbs_spr_penpal[8] = {")
            self.emit("    0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF")
            self.emit("};")
        elif not self.palette_imported:
            self.emit("static const uint8_t gbs_spr_penpal[8] = {")
            self.emit("    (COLOR_TRANSPARENT << 4) | COLOR_LIGHTGREY,   /* pixels 0,1 */")
            self.emit("    (COLOR_GREY << 4) | COLOR_WHITE,              /* pixels 2,3 */")
            self.emit("    0, 0, 0, 0, 0, 0")
            self.emit("};")
        self.emit("static void gbs_spr_init(void) {")
        self.emit("    uint8_t s, i;")
        self.emit("    if (gbs_spr_inited) return;")
        if self.palette_imported and not bpp4:
            self.emit("    gbs_pal_init();  /* grey-ramp pen defaults */")
        self.emit("    for (s = 0; s < GBS_MAX_SPRITES; ++s) {")
        self.emit("        gbs_scb[s].s.sprctl0 = %s | TYPE_NORMAL;" % bpp_macro)
        self.emit("        gbs_scb[s].s.sprctl1 = LITERAL | REHV;")
        self.emit("        gbs_scb[s].s.sprcoll = 0;")
        self.emit("        gbs_scb[s].s.next = (char *)0;")
        self.emit("        gbs_scb[s].s.data = (unsigned char *)(gbs_img_none + 2);")
        self.emit("        gbs_scb[s].s.hpos = -16; gbs_scb[s].s.vpos = -16;")
        self.emit("        gbs_scb[s].s.hsize = 0x100; gbs_scb[s].s.vsize = 0x100;")
        if self.palette_imported and not bpp4:
            self.emit("        gbs_scb[s].s.penpal[0] = gbs_pal_penpal[0][0];")
            self.emit("        gbs_scb[s].s.penpal[1] = gbs_pal_penpal[0][1];")
            self.emit("        for (i = 2; i < 8; ++i) gbs_scb[s].s.penpal[i] = 0;")
        else:
            self.emit("        for (i = 0; i < 8; ++i) gbs_scb[s].s.penpal[i] = gbs_spr_penpal[i];")
        self.emit("        gbs_spr_im[s] = gbs_img_none;")
        self.emit("        gbs_spr_e[s] = 1;")
        self.emit("        gbs_spr_lx[s] = 255; gbs_spr_ly[s] = 255;  /* off screen */")
        self.emit("    }")
        self.emit("    gbs_spr_inited = 1;")
        self.emit("    gbs_force = 1;")
        self.emit("}")
        self._emit_lynx_place(bpp_macro, orient)
        if singles:
            self._emit_lynx_one_conv(orient, bpp4)
        if self.load_sprite16_used:
            if bpp4:
                self._emit_lynx_load_sprite_pal16()
            else:
                self.emit("void gbs_load_sprite_pal16(const uint16_t *pal) { (void)pal; }")
        self._emit_cc65_present(prof, with_bkg, sw, sh, clear_pen)
        self.emit("void gbs_set_sprite_data(uint8_t first, uint8_t count, const uint8_t *data) {")
        self.emit("    const uint8_t * const *t = gbs_img_table(data);")
        self.emit("    uint8_t i, s;")
        self.emit("    gbs_spr_init();")
        self.emit("    for (i = 0; i < count; ++i) {")
        self.emit("        gbs_img[(uint8_t)(first + i)] = t ? t[i] : 0;")
        if singles:
            self.emit("        gbs_src[(uint8_t)(first + i)] = data + (uint16_t)i * %d;"
                      % (32 if bpp4 else 16))
        self.emit("    }")
        self.emit("    /* A slot already showing a re-uploaded tile shows the new picture,")
        self.emit("       as it would on the Game Boy. */")
        self.emit("    for (s = 0; s < gbs_spr_max; ++s)")
        self.emit("        if ((uint8_t)(gbs_spr_tile[s] - first) < count)")
        if singles:
            self.emit("            gbs_spr_show(s, gbs_spr_sm[s] ? gbs_one(s, gbs_spr_tile[s])")
            self.emit("                                        : gbs_img[gbs_spr_tile[s]]);")
        else:
            self.emit("            gbs_spr_show(s, gbs_img[gbs_spr_tile[s]]);")
        self.emit("    gbs_spr_used = 1;")
        self.emit("    gbs_force = 1;")
        self.emit("}")
        self.emit("void gbs_set_sprite_tile(uint8_t nb, uint8_t tile) {")
        self.emit("    gbs_spr_init();")
        if self.metasprite_used:
            self._emit_cc65_meta_branch('tile')
        self.emit("    if (nb < GBS_MAX_SPRITES) {")
        self.emit("        gbs_spr_tile[nb] = tile;")
        if singles:
            self.emit("        gbs_spr_sm[nb] = gbs_spr_one;")
            self.emit("        gbs_spr_show(nb, gbs_spr_one ? gbs_one(nb, tile) : gbs_img[tile]);")
        else:
            self.emit("        gbs_spr_show(nb, gbs_img[tile]);")
        self.emit("        if (nb >= gbs_spr_max) gbs_spr_max = nb + 1;")
        self.emit("    }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        if getattr(self, 'baked_groups', None):
            self._emit_lynx_baked_stream()
        self.emit("uint8_t gbs_get_sprite_tile(uint8_t nb) {")
        self.emit("    return nb < GBS_MAX_SPRITES ? gbs_spr_tile[nb] : 0;")
        self.emit("}")
        self.emit("/* prop carries the LOGICAL FLIP_X / FLIP_Y; gbs_spr_place maps them. */")
        self.emit("void gbs_set_sprite_prop(uint8_t nb, uint8_t prop) {")
        self.emit("    gbs_spr_init();")
        if self.metasprite_used:
            self._emit_cc65_meta_branch('prop')
        self.emit("    if (nb < GBS_MAX_SPRITES) {")
        self.emit("        prop &= (uint8_t)(FLIP_X | FLIP_Y);")
        self.emit("        if (gbs_spr_pr[nb] != prop) { gbs_spr_pr[nb] = prop; gbs_spr_place(nb); }")
        self.emit("    }")
        self.emit("}")
        self.emit("/* sprite.move takes LOGICAL screen pixels (top-left origin). */")
        self.emit("void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y) {")
        self.emit("    gbs_spr_init();")
        if self.metasprite_used:
            self._emit_cc65_meta_branch('move')
        self.emit("    if (nb < GBS_MAX_SPRITES) {")
        self.emit("        if (gbs_spr_lx[nb] != x || gbs_spr_ly[nb] != y) {")
        self.emit("            gbs_spr_lx[nb] = x; gbs_spr_ly[nb] = y;")
        self.emit("            gbs_spr_place(nb);")
        self.emit("        }")
        self.emit("        if (nb >= gbs_spr_max) gbs_spr_max = nb + 1;")
        self.emit("    }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        self._emit_cc65_move_world()
        if self.batch_used:
            self._emit_lynx_baked_plot(orient)
            self._emit_lynx_baked_drift()
        self.emit("void gbs_show_sprites(void) { gbs_spr_used = 1;")
        self.emit("    if (!gbs_spr_visible) { gbs_spr_visible = 1; gbs_force = 1; } }")
        self.emit("void gbs_hide_sprites(void) {")
        self.emit("    if (gbs_spr_visible) { gbs_spr_visible = 0; gbs_force = 1; } }")
        if with_bkg:
            self.emit("void gbs_show_bkg(void) {")
            self.emit("    if (!gbs_bkg_visible) { gbs_bkg_visible = 1; gbs_force = 1; } }")
        else:
            self.emit("void gbs_show_bkg(void) { }")
        if self.metasprite_used:
            if whole:
                self._emit_cc65_meta_func(whole=True)
                self._emit_lynx_whole_meta_func()
            else:
                self._emit_cc65_meta_func()

    def _emit_lynx_baked_stream(self):
        """`gbs_spr_data_bstream`: sprite.set_data of a baked sheet that takes
        turns in its upload slot (gen_streaming._pack_lynx_baked). The blob --
        a u16 offset per tile, then the images -- is read from its 1 KB cart
        block into the slot's buffer, and the tiles point into it."""
        self.emit("/* Baked sheets that take turns in one upload slot: images from")
        self.emit("   the cart, one RAM buffer per slot (the biggest blob). */")
        self.emit("#include <unistd.h>")
        self.emit("#include <stdio.h>")
        self.emit("#ifndef GBS_ARCHIVE_BASE")
        self.emit("#define GBS_ARCHIVE_BASE 0")
        self.emit("#endif")
        for g, n in enumerate(self.baked_groups):
            self.emit("static uint8_t gbs_bimg_%d[%d];" % (g, n))
        self.emit("void gbs_spr_data_bstream(uint8_t first, uint8_t count, uint16_t blk,")
        self.emit("                          uint16_t len, uint8_t *buf) {")
        self.emit("    uint8_t i, s;")
        self.emit("    uint16_t o;")
        self.emit("    const uint8_t *p = buf;")
        self.emit("    gbs_spr_init();")
        self.emit("    lseek(1, (long)((unsigned long)GBS_ARCHIVE_BASE + ((unsigned long)blk << 10)), SEEK_SET);")
        self.emit("    read(1, buf, len);")
        self.emit("    for (i = 0; i < count; ++i, p += 2) {")
        self.emit("        o = (uint16_t)(p[0] | ((uint16_t)p[1] << 8));")
        self.emit("        gbs_img[(uint8_t)(first + i)] = o ? buf + o : 0;")
        self.emit("    }")
        self.emit("    /* the buffer changed under any slot showing these tiles: re-show */")
        self.emit("    for (s = 0; s < gbs_spr_max; ++s)")
        self.emit("        if ((uint8_t)(gbs_spr_tile[s] - first) < count) {")
        self.emit("            gbs_spr_im[s] = 0;")
        self.emit("            gbs_spr_show(s, gbs_img[gbs_spr_tile[s]]);")
        self.emit("        }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("    gbs_force = 1;")
        self.emit("}")

    def _emit_lynx_place(self, bpp_macro, orient):
        """`gbs_spr_place` (logical -> physical SCB) and `gbs_spr_show`."""
        self.emit("/* Logical position + image size + logical flips -> the SCB. A flipped")
        self.emit("   Suzy sprite is mirrored about its reference point, so the reference")
        self.emit("   moves to the far edge and the picture stays where it was put. */")
        self.emit("static void gbs_spr_place(uint8_t nb) {")
        self.emit("    gbs_scb_t *q = &gbs_scb[nb];")
        self.emit("    const uint8_t *im = gbs_spr_im[nb];")
        self.emit("    uint8_t f = gbs_spr_pr[nb], c0;")
        self.emit("    int hp, vp;")
        if orient is None:
            self.emit("    hp = gbs_spr_lx[nb]; vp = gbs_spr_ly[nb];")
            self.emit("    c0 = f;")
            self.emit("    if (f & HFLIP) hp += im[0] - 1;")
            self.emit("    if (f & VFLIP) vp += im[1] - 1;")
        else:
            # Logical FLIP_X mirrors along the logical x axis, which is the
            # PHYSICAL y axis once turned (and FLIP_Y the physical x); the
            # physical width is the logical height and vice versa.
            self.emit("    c0 = (uint8_t)(((f & FLIP_X) ? VFLIP : 0) | ((f & FLIP_Y) ? HFLIP : 0));")
            if orient == 'portrait_left':
                # px = 159 - ly, py = lx: the image's physical top-left is
                # (160 - ly - lh, lx).
                self.emit("    hp = %d - (int)gbs_spr_ly[nb] - im[1];" % self.LYNX_PHYS_W)
                self.emit("    vp = gbs_spr_lx[nb];")
            else:
                # px = ly, py = 101 - lx: top-left (ly, 102 - lx - lw).
                self.emit("    hp = gbs_spr_ly[nb];")
                self.emit("    vp = %d - (int)gbs_spr_lx[nb] - im[0];" % self.LYNX_PHYS_H)
            self.emit("    if (c0 & HFLIP) hp += im[1] - 1;")
            self.emit("    if (c0 & VFLIP) vp += im[0] - 1;")
        self.emit("    c0 = (uint8_t)(c0 | %s | TYPE_NORMAL);" % bpp_macro)
        self.emit("    if (q->s.hpos != hp) { q->s.hpos = hp; gbs_force = 1; }")
        self.emit("    if (q->s.vpos != vp) { q->s.vpos = vp; gbs_force = 1; }")
        self.emit("    if (q->s.sprctl0 != c0) { q->s.sprctl0 = c0; gbs_force = 1; }")
        self.emit("}")
        self.emit("/* Point slot `nb` at an image (0 = draw nothing) and re-place it. */")
        self.emit("static void gbs_spr_show(uint8_t nb, const uint8_t *im) {")
        self.emit("    if (!im) im = gbs_img_none;")
        self.emit("    if (gbs_spr_im[nb] == im) return;")
        self.emit("    gbs_spr_im[nb] = im;")
        self.emit("    gbs_spr_e[nb] = (uint8_t)(im[0] == 0);")
        self.emit("    gbs_scb[nb].s.data = (unsigned char *)(im + 2);")
        self.emit("    gbs_force = 1;")
        self.emit("    gbs_spr_place(nb);")
        self.emit("}")

    def _emit_lynx_one_conv(self, orient, bpp4):
        """`gbs_one`: a slot's single 8x8 tile, converted (and turned) from
        the uploaded source into the slot's buffer when it changes."""
        if orient == 'portrait_left':
            src = "7 - px", "py"
        elif orient == 'portrait_right':
            src = "px", "7 - py"
        else:
            src = "py", "px"
        n = 4 if bpp4 else 2
        self.emit("static const uint8_t *gbs_one(uint8_t nb, uint8_t tile) {")
        self.emit("    const uint8_t *gb = gbs_src[tile];")
        self.emit("    uint8_t *o, py, px, ly, lx, ci;")
        self.emit("    if (!gb) return 0;")
        self.emit("    o = gbs_one_buf[nb];")
        self.emit("    if (gbs_one_at[nb] == gb) return o;")
        self.emit("    gbs_one_at[nb] = gb;")
        self.emit("    gbs_spr_im[nb] = 0;   /* the buffer changes under the slot: re-show */")
        self.emit("    *o++ = 8; *o++ = 8;")
        self.emit("    for (py = 0; py < 8; ++py) {")
        self.emit("        *o++ = %d;" % (n + 2))
        for k in range(n):
            self.emit("        o[%d] = 0;" % k)
        self.emit("        for (px = 0; px < 8; ++px) {")
        self.emit("            ly = (uint8_t)(%s); lx = (uint8_t)(%s);" % src)
        if bpp4:
            self.emit("            ci = (uint8_t)((gb[ly * 4 + (lx >> 1)] >> ((lx & 1) ? 0 : 4)) & 15);")
            self.emit("            o[px >> 1] |= (uint8_t)(ci << ((px & 1) ? 0 : 4));")
        else:
            self.emit("            ci = (uint8_t)((((gb[ly * 2 + 1] >> (7 - lx)) & 1) << 1)")
            self.emit("                         | ((gb[ly * 2] >> (7 - lx)) & 1));")
            self.emit("            o[px >> 2] |= (uint8_t)(ci << ((3 - (px & 3)) * 2));")
        self.emit("        }")
        self.emit("        o += %d; *o++ = 0;" % n)
        self.emit("    }")
        self.emit("    *o = 0;")
        self.emit("    return gbs_one_buf[nb];")
        self.emit("}")

    def _emit_lynx_baked_plot(self, orient):
        """`sprite.plot` for the baked engine: ONE pass over the pool's slots
        by pointer, with no call per sprite. It was the Lynx shooter's biggest
        cost (113,000 GearLynx ticks a frame, >3,000 a sprite: plot -> move ->
        init -> place, each a cc65 call, plus 16-bit arithmetic). A slot whose
        image is EMPTY (the inner tile of a whole sprite, the second slot of
        a 16 px enemy drawn as 8x16 pairs) only records its position; an
        unflipped landscape slot writes its SCB position straight; the rest go
        through gbs_spr_place."""
        self.emit("/* sprite.plot: one pass over the slots, positions written straight.")
        self.emit("   The parameters are copied ONCE: read through the C stack they cost")
        self.emit("   cc65 runtime calls per entry. */")
        self.emit("static const uint8_t *gbs_pl_xs;")
        self.emit("static const uint8_t *gbs_pl_ys;")
        self.emit("void gbs_spr_plot(uint8_t first, uint8_t n, const uint8_t *xs,")
        self.emit("                  const uint8_t *ys, uint8_t cols) {")
        self.emit("    uint8_t i, x, y, c, k, s = first;")
        self.emit("    gbs_scb_t *q;")
        self.emit("    gbs_spr_init();")
        self.emit("    if (first >= GBS_MAX_SPRITES) return;")
        self.emit("    gbs_pl_xs = xs; gbs_pl_ys = ys; k = cols;")
        self.emit("    q = gbs_scb + first;")
        self.emit("    gbs_spr_used = 1;")
        self.emit("    for (i = n; i; --i) {")
        self.emit("        x = *gbs_pl_xs++;")
        self.emit("        y = *gbs_pl_ys++;")
        self.emit("        for (c = k; c; --c) {")
        self.emit("            if (s >= GBS_MAX_SPRITES) return;")
        self.emit("            if (gbs_spr_lx[s] != x || gbs_spr_ly[s] != y) {")
        self.emit("                gbs_spr_lx[s] = x; gbs_spr_ly[s] = y;")
        self.emit("                if (!gbs_spr_e[s]) {         /* an empty slot has nothing to place */")
        if orient is None:
            self.emit("                    if (gbs_spr_pr[s] == 0) {")
            self.emit("                        q->s.hpos = x; q->s.vpos = y; gbs_force = 1;")
            self.emit("                    } else {")
            self.emit("                        gbs_spr_place(s);")
            self.emit("                    }")
        else:
            self.emit("                    gbs_spr_place(s);")
        self.emit("                }")
        self.emit("            }")
        self.emit("            if (s >= gbs_spr_max) gbs_spr_max = (uint8_t)(s + 1);")
        self.emit("            ++s; ++q; x += 8;")
        self.emit("        }")
        self.emit("    }")
        self.emit("}")

    def _emit_lynx_baked_drift(self):
        """`sprite.drift` for the baked engine, as 6502 assembly. The portable
        C loop reads its three parameters off the C stack through five
        runtime helpers PER BYTE (>150 cycles); here they go to ptr1 / ptr2
        once and the loop is `lda (ptr1),y / clc / adc (ptr2),y / sta (ptr1),y`.
        The optimiser is OFF around it: left on, cc65 rewrote the inline
        assembly (dropped the `ldy #0` / `iny` for the 65C02 zp-indirect form,
        turned `bne` into a long-branch macro) and broke it."""
        self.emit("/* sprite.drift: pos[i] += vel[i], an assembly loop (see the codegen).")
        self.emit("   After the prologue's pusha the C stack holds n, vel, pos. */")
        self.emit("#pragma optimize (push, off)")
        self.emit("void gbs_spr_drift(uint8_t *pos, const uint8_t *vel, uint8_t n) {")
        self.emit("    if (!n) return;")
        for line in ("ldy #4", "lda (c_sp),y", "sta ptr1+1", "dey", "lda (c_sp),y", "sta ptr1",
                     "dey", "lda (c_sp),y", "sta ptr2+1", "dey", "lda (c_sp),y", "sta ptr2",
                     "dey", "lda (c_sp),y", "tax",
                     "ldy #0", "lda (ptr1),y", "clc", "adc (ptr2),y", "sta (ptr1),y",
                     "iny", "dex", "bne *-9"):
            self.emit('    __asm__("%s");' % line)
        self.emit("}")
        self.emit("#pragma optimize (pop)")

    def _emit_lynx_camera_state(self):
        """`lynx.sprite_camera(first, count, x, y)` (native.lynx): the slots
        [first, first + count) hold WORLD positions and are drawn through Suzy's
        HOFF / VOFF with the camera (x, y) taken off, so a pan rewrites no SCB
        and the game copies no coordinates; every other slot is screen space
        and draws AFTER them (a HUD on top). Culling is on the logical screen
        position (world - camera, u8), exactly as if the game had subtracted.
        `lynx.screen_shake` keeps its VOFF through `gbs_lynx_shk`."""
        self.emit("/* lynx.sprite_camera: world slots [first, end) drawn through HOFF/VOFF. */")
        self.emit("static uint8_t gbs_cam_first = 0, gbs_cam_end = 0;")
        self.emit("static uint8_t gbs_cam_x = 0, gbs_cam_y = 0;")
        self.emit("static uint8_t gbs_lynx_shk = 0;   /* lynx.screen_shake's VOFF */")
        self.emit("void gbs_lynx_sprite_camera(uint8_t first, uint8_t count, uint8_t x, uint8_t y) {")
        self.emit("    uint8_t e = (uint8_t)(first + count);")
        self.emit("    if (e < first) e = 255;")
        self.emit("    if (gbs_cam_first != first || gbs_cam_end != e || gbs_cam_x != x || gbs_cam_y != y) {")
        self.emit("        gbs_cam_first = first; gbs_cam_end = e;")
        self.emit("        gbs_cam_x = x; gbs_cam_y = y;")
        self.emit("        gbs_force = 1;")
        self.emit("    }")
        self.emit("}")

    def _lynx_camera_regs(self):
        """(HOFF, VOFF) for the logical camera (gbs_cam_x, gbs_cam_y): a
        sprite draws at its SCB position minus the offset, and the portrait
        maps turn the logical axes (see `_emit_lynx_place`)."""
        orient = getattr(self, 'lynx_orient', None)
        if orient == 'portrait_left':       # hp = 160 - ly - h, vp = lx
            return "(unsigned)(-(int)gbs_cam_y)", "(unsigned)gbs_cam_x"
        if orient == 'portrait_right':      # hp = ly, vp = 102 - lx - w
            return "(unsigned)gbs_cam_y", "(unsigned)(-(int)gbs_cam_x)"
        return "(unsigned)gbs_cam_x", "(unsigned)gbs_cam_y"

    def _emit_lynx_camera_draw(self, head, tail):
        """Draw the world chain `head`..`tail` under the camera offset, then
        put the offsets back (Suzy must be idle around each register write)."""
        h, v = self._lynx_camera_regs()
        self.emit("        if (%s) {" % tail)
        self.emit("            %s->next = (char *)0;" % tail)
        self.emit("            while (tgi_busy()) { }")
        self.emit("            SUZY.hoff = %s;" % h)
        self.emit("            SUZY.voff = %s + gbs_lynx_shk;" % v)
        self.emit("            tgi_sprite(%s);" % head)
        self.emit("            while (tgi_busy()) { }")
        self.emit("            SUZY.hoff = 0; SUZY.voff = gbs_lynx_shk;")
        self.emit("        }")

    def _emit_lynx_baked_chain(self):
        """The present's sprite chain for the baked engine: cull on the
        LOGICAL position (u8, so only the right/bottom edges can be crossed)
        and on an empty image."""
        if getattr(self, 'lynx_camera_used', False):
            self._emit_lynx_baked_chain_camera()
            return
        self.emit("    if (gbs_spr_visible) {")
        self.emit("        SCB_REHV_PAL *h = 0, *pv = 0;")
        self.emit("        gbs_scb_t *q = gbs_scb;")
        self.emit("        for (s = 0; s < gbs_spr_max; ++s, ++q) {")
        self.emit("            if (gbs_spr_lx[s] >= GBS_LOG_W || gbs_spr_ly[s] >= GBS_LOG_H")
        self.emit("                || gbs_spr_e[s]) continue;")
        self.emit("            if (pv) pv->next = (char *)&q->s; else h = &q->s;")
        self.emit("            pv = &q->s;")
        self.emit("        }")
        self.emit("        if (pv) { pv->next = (char *)0; tgi_sprite(h); }")
        self.emit("    }")

    def _emit_lynx_baked_chain_camera(self):
        """The baked chain under `lynx.sprite_camera`: two chains, the world
        slots (culled on world - camera) under the offset, then the rest."""
        self.emit("    if (gbs_spr_visible) {")
        self.emit("        SCB_REHV_PAL *h = 0, *pv = 0, *wh = 0, *wv = 0;")
        self.emit("        gbs_scb_t *q = gbs_scb;")
        self.emit("        for (s = 0; s < gbs_spr_max; ++s, ++q) {")
        self.emit("            if (gbs_spr_e[s]) continue;")
        self.emit("            if (s >= gbs_cam_first && s < gbs_cam_end) {")
        self.emit("                if ((uint8_t)(gbs_spr_lx[s] - gbs_cam_x) >= GBS_LOG_W")
        self.emit("                    || (uint8_t)(gbs_spr_ly[s] - gbs_cam_y) >= GBS_LOG_H) continue;")
        self.emit("                if (wv) wv->next = (char *)&q->s; else wh = &q->s;")
        self.emit("                wv = &q->s;")
        self.emit("                continue;")
        self.emit("            }")
        self.emit("            if (gbs_spr_lx[s] >= GBS_LOG_W || gbs_spr_ly[s] >= GBS_LOG_H) continue;")
        self.emit("            if (pv) pv->next = (char *)&q->s; else h = &q->s;")
        self.emit("            pv = &q->s;")
        self.emit("        }")
        self._emit_lynx_camera_draw("wh", "wv")
        self.emit("        if (pv) { pv->next = (char *)0; tgi_sprite(h); }")
        self.emit("    }")

    def _emit_lynx_whole_meta_func(self):
        """`sprite.set_meta` with whole sprites: the base slot draws the named
        sprite starting at `tile` as ONE image; the rest of the reserved run
        is cleared, so code written for the tile fan still runs."""
        self.emit("/* sprite.set_meta, whole sprites: one SCB for the whole picture. */")
        self.emit("void gbs_set_metasprite(uint8_t base, uint8_t tile, uint8_t w, uint8_t h) {")
        self.emit("    uint8_t k, n;")
        self.emit("    if (base >= GBS_MAX_SPRITES) return;")
        self.emit("    gbs_meta_w[base] = 0; gbs_meta_h[base] = 0;")
        self.emit("    gbs_set_sprite_tile(base, tile);")
        self.emit("    gbs_set_sprite_prop(base, gbs_meta_prop[base]);")
        self.emit("    n = (uint8_t)(w * h);")
        self.emit("    for (k = 1; k < n && (uint8_t)(base + k) < GBS_MAX_SPRITES; ++k) {")
        self.emit("        gbs_spr_show((uint8_t)(base + k), 0);")
        self.emit("        gbs_spr_lx[base + k] = 255; gbs_spr_ly[base + k] = 255;")
        self.emit("    }")
        self.emit("}")

    # ------------------------------------------------------------ the background
    # Portrait keeps the row-strip engine; only its INPUTS turn. A logical
    # tile (tx, ty) is physical map cell (row tx, col 31 - ty) turned left,
    # (row 31 - tx, col ty) turned right; its pixels are turned at upload; the
    # logical scroll becomes the physical camera. So the game's vertical
    # scroll is the strips' HORIZONTAL one (pure SCB hpos, no recompose), and
    # a streamed logical ROW is a physical COLUMN: it recomposes just that
    # column of each strip holding one of its rows, never a whole strip.
    def _emit_lynx_bkg_rot(self, orient, bpp4):
        """`gbs_bkg_rot`: one source tile -> its turned, packed literal rows."""
        if orient == 'portrait_left':
            src = "7 - px", "py"         # P[py][px] = L[7 - px][py]
        else:
            src = "px", "7 - py"         # P[py][px] = L[px][7 - py]
        self.emit("/* [build] lynx_orientation = %s: turn a tile on upload. */" % orient)
        self.emit("static void gbs_bkg_rot(const uint8_t *gb, uint8_t *out) {")
        self.emit("    uint8_t py, px, ly, lx, ci;")
        self.emit("    for (py = 0; py < 8; ++py) {")
        if bpp4:
            self.emit("        out[0] = 0; out[1] = 0; out[2] = 0; out[3] = 0;")
        else:
            self.emit("        out[0] = 0; out[1] = 0;")
        self.emit("        for (px = 0; px < 8; ++px) {")
        self.emit("            ly = (uint8_t)(%s); lx = (uint8_t)(%s);" % src)
        if bpp4:
            self.emit("            ci = (uint8_t)((gb[ly * 4 + (lx >> 1)] >> ((lx & 1) ? 0 : 4)) & 15);")
            self.emit("            out[px >> 1] |= (uint8_t)(ci << ((px & 1) ? 0 : 4));")
        else:
            self.emit("            ci = (uint8_t)((((gb[ly * 2 + 1] >> (7 - lx)) & 1) << 1)")
            self.emit("                         | ((gb[ly * 2] >> (7 - lx)) & 1));")
            self.emit("            out[px >> 2] |= (uint8_t)(ci << ((3 - (px & 3)) * 2));")
        self.emit("        }")
        self.emit("        out += %d;" % (4 if bpp4 else 2))
        self.emit("    }")
        self.emit("}")

    def _emit_lynx_bkg_tiles_portrait(self, orient):
        """`gbs_set_bkg_tiles` for a portrait screen (column-granular)."""
        if orient == 'portrait_left':
            cell = "pr = lx; pc = (uint8_t)(31 - ly);"
        else:
            cell = "pr = (uint8_t)(31 - lx); pc = ly;"
        self.emit("static void gbs_bkg_compose_cols(uint8_t p, uint8_t map_row, uint8_t c0, uint8_t c1);")
        self.emit("/* [build] lynx_orientation = %s: logical cell (lx, ly) is physical" % orient)
        self.emit("   map cell (pr, pc). Only the strip showing row pr re-composes, and only")
        self.emit("   the strip columns showing map column pc (pc, pc + 32, ...) that it has")
        self.emit("   already composed; the rest are composed from the map when they come. */")
        self.emit("void gbs_set_bkg_tiles(uint8_t x, uint8_t y, uint8_t w, uint8_t h,")
        self.emit("                       const uint8_t *tiles) {")
        self.emit("    uint8_t cx, cy, lx, ly, pr, pc, p, c;")
        self.emit("    gbs_bkg_init();")
        self.emit("    for (cy = 0; cy < h; ++cy) {")
        self.emit("        ly = (uint8_t)((y + cy) & 31);")
        self.emit("        for (cx = 0; cx < w; ++cx) {")
        self.emit("            lx = (uint8_t)((x + cx) & 31);")
        self.emit("            " + cell)
        self.emit("            gbs_bkg_map[(uint16_t)pr * 32 + pc] = tiles[(uint16_t)cy * w + cx];")
        self.emit("            p = (uint8_t)(pr & (GBS_BKG_STRIPS - 1));")
        self.emit("            c = (uint8_t)((pc - gbs_bkg_cb) & 31);   /* its strip column */")
        self.emit("            if (gbs_bkg_strip_row[p] == pr && c < gbs_bkg_strip_col[p])")
        self.emit("                gbs_bkg_compose_cols(p, pr, c, (uint8_t)(c + 1));")
        self.emit("        }")
        self.emit("    }")
        self.emit("    gbs_bkg_used = 1;")
        self.emit("    gbs_force = 1;       /* both pages must show the new cells */")
        self.emit("}")

    def _emit_lynx_bkg_slide(self, bpp4):
        """`gbs_bkg_slide`: keep the screen-width strips under the camera.

        When the physical camera crosses a tile, every COMPLETE strip moves one
        column with ONE memmove (the line records move with it; their offset
        and pad bytes are written back) and only the column that came in is
        composed; an incomplete strip simply starts again. A jump of more than
        a column recomposes everything (a load, a big pan)."""
        cb = 4 if bpp4 else 2
        self.emit("/* Portrait: slide the strips with the camera (see gbs_bkg_cb). */")
        self.emit("static void gbs_bkg_slide(void) {")
        self.emit("    uint8_t want = (uint8_t)(gbs_bkg_x >> 3);")
        self.emit("    uint8_t d, s, k, *o;")
        self.emit("    if (want == gbs_bkg_cb) return;")
        self.emit("    d = (uint8_t)((want - gbs_bkg_cb) & 31);")
        self.emit("    gbs_bkg_cb = want;")
        self.emit("    gbs_force = 1;")
        self.emit("    for (s = 0; s < GBS_BKG_STRIPS; ++s) {")
        self.emit("        if (gbs_bkg_strip_row[s] == 0xFF) continue;")
        self.emit("        if ((d != 1 && d != 31) || gbs_bkg_strip_col[s] < GBS_BKG_STRIP_W) {")
        self.emit("            gbs_bkg_strip_col[s] = 0;   /* rebuild from the map */")
        self.emit("            gbs_bkg_built = 0;")
        self.emit("            continue;")
        self.emit("        }")
        self.emit("        o = gbs_bkg_strip[s];")
        self.emit("        if (d == 1)       /* drop column 0, the new one comes in on the right */")
        self.emit("            memmove(o + 1, o + 1 + %d, 8 * GBS_BKG_STRIP_BYTES - 1 - %d);" % (cb, cb))
        self.emit("        else              /* drop the last column, the new one is column 0 */")
        self.emit("            memmove(o + 1 + %d, o + 1, 8 * GBS_BKG_STRIP_BYTES - 1 - %d);" % (cb, cb))
        self.emit("        for (k = 0; k < 8; ++k) {   /* the line records moved: restore them */")
        self.emit("            o[(uint16_t)k * GBS_BKG_STRIP_BYTES] = GBS_BKG_STRIP_BYTES;")
        self.emit("            o[(uint16_t)k * GBS_BKG_STRIP_BYTES + GBS_BKG_STRIP_BYTES - 1] = 0;")
        self.emit("        }")
        self.emit("        k = (d == 1) ? (uint8_t)(GBS_BKG_STRIP_W - 1) : 0;")
        self.emit("        gbs_bkg_compose_cols(s, gbs_bkg_strip_row[s], k, (uint8_t)(k + 1));")
        self.emit("    }")
        self.emit("}")

    def _emit_lynx_bkg_scroll_portrait(self, orient):
        """`gbs_move_bkg` / `gbs_scroll_bkg`: logical scroll -> physical camera."""
        if orient == 'portrait_left':
            # physical world x = 255 - logical y: camera x = 96 - sy, y = sx
            move = "px = (uint8_t)(%d - y); py = x;" % (256 - self.LYNX_PHYS_W)
            step = ("gbs_bkg_x = (uint8_t)(gbs_bkg_x - dy);",
                    "gbs_bkg_y = (uint8_t)(gbs_bkg_y + dx);")
        else:
            # physical world y = 255 - logical x: camera x = sy, y = 154 - sx
            move = "px = y; py = (uint8_t)(%d - x);" % (256 - self.LYNX_PHYS_H)
            step = ("gbs_bkg_x = (uint8_t)(gbs_bkg_x + dy);",
                    "gbs_bkg_y = (uint8_t)(gbs_bkg_y - dx);")
        self.emit("/* bkg.move / bkg.scroll take the LOGICAL scroll; the strips want the")
        self.emit("   physical camera. */")
        self.emit("void gbs_move_bkg(uint8_t x, uint8_t y) {")
        self.emit("    uint8_t px, py;")
        self.emit("    " + move)
        self.emit("    if (gbs_bkg_x != px || gbs_bkg_y != py) {")
        self.emit("        gbs_bkg_x = px; gbs_bkg_y = py; gbs_force = 1;")
        self.emit("    }")
        self.emit("    gbs_bkg_used = 1;")
        self.emit("}")
        self.emit("void gbs_scroll_bkg(int8_t dx, int8_t dy) {")
        self.emit("    if (dx || dy) {")
        self.emit("        " + step[0])
        self.emit("        " + step[1])
        self.emit("        gbs_force = 1;")
        self.emit("    }")
        self.emit("    gbs_bkg_used = 1;")
        self.emit("}")

    # ------------------------------------------------------------ the d-pad
    def _emit_lynx_turn_pad(self):
        """`gbs_turn_pad`: the physical joystick byte -> the LOGICAL one in a
        portrait orientation (the d-pad turns with the screen; A, B and the
        option buttons keep their bits). Nothing in landscape."""
        orient = getattr(self, 'lynx_orient', None)
        if self.platform != 'lynx' or not orient:
            return
        # portrait_left (turned counter-clockwise): pressing toward the top of
        # the turned screen is physical RIGHT, toward its right is DOWN, and
        # so on round; portrait_right is the opposite turn.
        if orient == 'portrait_left':
            m = (('JOY_RIGHT_MASK', 'JOY_UP_MASK'), ('JOY_DOWN_MASK', 'JOY_RIGHT_MASK'),
                 ('JOY_LEFT_MASK', 'JOY_DOWN_MASK'), ('JOY_UP_MASK', 'JOY_LEFT_MASK'))
        else:
            m = (('JOY_LEFT_MASK', 'JOY_UP_MASK'), ('JOY_UP_MASK', 'JOY_RIGHT_MASK'),
                 ('JOY_RIGHT_MASK', 'JOY_DOWN_MASK'), ('JOY_DOWN_MASK', 'JOY_LEFT_MASK'))
        self.emit("/* [build] lynx_orientation = %s: the d-pad turns with the screen. */" % orient)
        self.emit("static uint8_t gbs_turn_pad(uint8_t r) {")
        self.emit("    return (uint8_t)((r & (uint8_t)~(JOY_UP_MASK | JOY_DOWN_MASK | JOY_LEFT_MASK | JOY_RIGHT_MASK))")
        for src, dst in m:
            self.emit("        | ((r & %s) ? %s : 0)" % (src, dst))
        self.emit("        );")
        self.emit("}")
