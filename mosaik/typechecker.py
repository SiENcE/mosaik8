"""Best-effort, non-blocking type checker (produces diagnostics only).

Diagnostics are COLLECTED (self.diagnostics), never raised: an aborting check
used to skip the rest of the pass, and since check_variable writes inferred
types into the AST (which codegen reads), whether an unrelated diagnostic
fired could change the generated C. Collecting keeps the pass total, so the
AST always ends up in the same state and diagnostics are purely advisory.
"""

from .ast_nodes import *  # noqa: F401,F403


class TypeChecker:
    def __init__(self):
        self.symbol_table = {}
        self.current_scope = {}
        # Names in `current_scope` that are `const`: a module-level const is a
        # VarDecl and lands in the scope beside the globals, so the flag has
        # to travel separately.
        self._const_names = set()
        self.imported_modules = {}
        self.diagnostics = []
        self.type_table = {
            'u8': {'size': 1, 'signed': False, 'min': 0, 'max': 255},
            'i8': {'size': 1, 'signed': True, 'min': -128, 'max': 127},
            'u16': {'size': 2, 'signed': False, 'min': 0, 'max': 65535},
            'i16': {'size': 2, 'signed': True, 'min': -32768, 'max': 32767},
            'bool': {'size': 1, 'signed': False, 'min': 0, 'max': 1},
            'addr': {'size': 2, 'signed': False, 'min': 0, 'max': 65535},
            'void': {'size': 0, 'signed': False, 'min': 0, 'max': 0}
        }

        # Add standard library functions
        self.add_stdlib_functions()

    def error(self, message: str):
        """Record a diagnostic. Never raises -- the pass must stay total so
        the AST type annotations it writes are deterministic (see module
        docstring). Prefixed with the source line of the statement or
        declaration being checked, when the parser stamped one."""
        line = getattr(self, '_line', None)
        where = getattr(self, '_file', None) or getattr(self, '_module', None)
        prefix = ""
        if line and where:
            prefix = "%s:%d: " % (where, line)
        elif line:
            prefix = "line %d: " % line
        self.diagnostics.append(prefix + message)

    def add_stdlib_functions(self):
        """Add standard library functions to the symbol table - Enhanced with Graphics.Text"""
        # Video functions
        self.symbol_table['video.wait_vblank'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': []
        }
        self.symbol_table['video.enable_lcd'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': []
        }
        self.symbol_table['video.disable_lcd'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': []
        }

        # Input functions
        for fn in ('input.pressed', 'input.held'):
            self.symbol_table[fn] = {
                'type': 'function',
                'return_type': PrimitiveType('bool'),
                'parameters': [Parameter('button', PrimitiveType('u8'))]
            }
        # The whole pad in ONE read (the console's own bit layout, so mask it
        # with the INPUT_* constants): eight per-button input.held calls a
        # frame were eight hardware reads (engine.pad.update).
        self.symbol_table['input.raw'] = {
            'type': 'function',
            'return_type': PrimitiveType('u8'),
            'parameters': []
        }

        # Hardware register access
        self.symbol_table['hw.write'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [
                Parameter('address', PrimitiveType('addr')),
                Parameter('value', PrimitiveType('u8'))
            ]
        }
        self.symbol_table['hw.read'] = {
            'type': 'function',
            'return_type': PrimitiveType('u8'),
            'parameters': [Parameter('address', PrimitiveType('addr'))]
        }
        # hw.peek: a plain (non-volatile) byte read at an address, lowered
        # INLINE - no helper call. For ROM/RAM data the compiler may keep in
        # registers; hw.read stays the volatile register access.
        self.symbol_table['hw.peek'] = {
            'type': 'function',
            'return_type': PrimitiveType('u8'),
            'parameters': [Parameter('address', PrimitiveType('addr'))]
        }
        # Write a byte to the console's SN76489 PSG data port (SMS / Game Gear).
        # The PSG is a Z80 I/O PORT, not memory, so hw.write can't reach it; this
        # lowers to `PSG = value` (an `out`). A no-op / unused on other consoles.
        self.symbol_table['hw.psg'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [Parameter('value', PrimitiveType('u8'))]
        }

        # Graphics.Text functions
        self.symbol_table['text.print_string'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [
                Parameter('x', PrimitiveType('u8')),
                Parameter('y', PrimitiveType('u8')),
                Parameter('text', ArrayType(PrimitiveType('u8'), None))  # String
            ]
        }

        self.symbol_table['text.print_number'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [
                Parameter('x', PrimitiveType('u8')),
                Parameter('y', PrimitiveType('u8')),
                Parameter('number', PrimitiveType('u8'))
            ]
        }

        self.symbol_table['text.clear_area'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [
                Parameter('x', PrimitiveType('u8')),
                Parameter('y', PrimitiveType('u8')),
                Parameter('width', PrimitiveType('u8')),
                Parameter('height', PrimitiveType('u8'))
            ]
        }

        # text.fill_box(c, r, w, h): a filled, bordered text box drawn as a real
        # framebuffer OVERLAY on the cc65 pixel consoles (the Lynx) -- an opaque
        # fill + a crisp 1px border via the TGI primitives, nicer than the
        # character-cell +--+ frame and pixel-precise. cc65-only (the GBDK
        # consoles are tile-based; engine.box keeps the tile frame there).
        self.symbol_table['text.fill_box'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [
                Parameter('c', PrimitiveType('u8')),
                Parameter('r', PrimitiveType('u8')),
                Parameter('width', PrimitiveType('u8')),
                Parameter('height', PrimitiveType('u8'))
            ]
        }

        # text.set_font(data): swap the console font's glyph tiles for a custom
        # sheet (a 96-glyph 8x8 font, ASCII 32.. -- MosaiK Studio's custom-font
        # feature). Real on the tile-plotting GBDK text consoles (GB family +
        # Game Gear); a no-op elsewhere (graceful degradation like platform.sound).
        self.symbol_table['text.set_font'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [
                Parameter('data', ArrayType(PrimitiveType('u8'), None))
            ]
        }

        # text.set_font_at(base, data): the font swap at a CALLER-chosen tile
        # base -- the SMS/GG escape for a large tileset that collides with the
        # low console font (opt-in; set_font keeps its console-default base).
        # Honored on the tile-plotting consoles; a no-op where set_font is one.
        self.symbol_table['text.set_font_at'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [
                Parameter('base', PrimitiveType('u8')),
                Parameter('data', ArrayType(PrimitiveType('u8'), None))
            ]
        }

        # text.to_window(origin_row, box_rows) / text.to_bkg(): route subsequent
        # UI text (the vm.core dialogue/menu boxes) onto the GB window overlay
        # layer, bottom-anchored (the reference engine's model) -- so a box no longer clobbers
        # the scene tilemap, scrolls with the camera, or needs a blank-band clear on
        # close. Real on the GB family; a no-op elsewhere (graceful degradation like
        # platform.sound), so the shared vm.core UI code calls it unconditionally.
        self.symbol_table['text.to_window'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [
                Parameter('origin_row', PrimitiveType('u8')),
                Parameter('box_rows', PrimitiveType('u8'))
            ]
        }
        self.symbol_table['text.to_bkg'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': []
        }
        self.symbol_table['text.window_active'] = {
            'type': 'function',
            'return_type': PrimitiveType('u8'),
            'parameters': []
        }

        # text.glyph_buffer(base, count): switch the text layer to GLYPH-BUFFER
        # mode -- the font stays in ROM and each character is rasterized on
        # demand into the tile band [base, base+count), instead of 96 glyph
        # tiles occupying VRAM for the whole run. That frees nearly the whole
        # background tile table for scene art (the reference engine's model) and retires
        # the SMS/GG set_font_at escape. `count` bounds how many DISTINCT
        # characters can be on screen at once; slot 0 is reserved for the space
        # glyph, so count must be at least 2. **Pass 0 for either to take the
        # DERIVED band** - base = one past the highest background tile the whole
        # program uploads, count = from there to the top of the table - so a
        # generated shell calls glyph_buffer(0, 0) and never carries the
        # numbers. Real on the tile-plotting GBDK
        # consoles (GB family + SMS/GG); a graceful no-op on the NES (glyphs
        # come from CHR) and on Lynx/PCE (TGI/conio text draws no tiles).
        self.symbol_table['text.glyph_buffer'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [
                Parameter('base', PrimitiveType('u8')),
                Parameter('count', PrimitiveType('u8'))
            ]
        }

        # text.vwf_start(col, row) / vwf_nl(col, row) / vwf_glyph(ch): the
        # VARIABLE-WIDTH text renderer. Where
        # the glyph buffer caches one tile PER CHARACTER (fixed width, so a
        # repeat is a cache hit), a variable-width glyph straddles cell
        # boundaries, so characters are COMPOSITED into a two-tile staging
        # buffer at a sub-cell pen and the band is walked as a linear RING -
        # the reference engine's own model, and CrossZGB's.
        #   vwf_start  begins a text buffer: rewinds the ring, pen to 0.
        #   vwf_nl     begins a line: abandons the partial cell (a part-filled
        #              cell is never packed, exactly as the reference does) and
        #              keeps walking the ring.
        #   vwf_glyph  composites one character and returns 1 when a whole cell
        #              was completed, so the caller can count cells.
        # Real on the GB family; a graceful no-op elsewhere, so a program that
        # uses them still builds everywhere and simply renders fixed-width.
        for _n, _params in (
                ('vwf_start', [Parameter('col', PrimitiveType('u8')),
                               Parameter('row', PrimitiveType('u8'))]),
                ('vwf_nl', [Parameter('col', PrimitiveType('u8')),
                            Parameter('row', PrimitiveType('u8'))])):
            self.symbol_table['text.' + _n] = {
                'type': 'function',
                'return_type': PrimitiveType('void'),
                'parameters': _params,
            }
        # vwf_number(v): the decimal digits of `v` through the SAME pen. A
        # $var$ token cannot go through text.print_number under this mode --
        # that plots whole CELLS and would stamp over the composited tiles
        # either side of it.
        self.symbol_table['text.vwf_number'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [Parameter('v', PrimitiveType('u16'))],
        }
        self.symbol_table['text.vwf_glyph'] = {
            'type': 'function',
            'return_type': PrimitiveType('u8'),
            'parameters': [Parameter('ch', PrimitiveType('u8'))],
        }

        # text.win_sprite_cut(on): while UI text owns the WINDOW overlay, stop
        # sprites drawing OVER it. On DMG hardware OBJ is above the window, so
        # an actor standing low in the room pokes through an open dialogue box;
        # the reference engine cuts them with an LCD (LYC) interrupt at the window's first
        # scanline and restores them each VBL (its `interrupts.c`). Real on the
        # GB family, a graceful no-op elsewhere (no GB-style window layer).
        self.symbol_table['text.win_sprite_cut'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [
                Parameter('on', PrimitiveType('u8'))
            ]
        }

        # text.win_reveal(): SHOW the window layer `to_window` prepared. The
        # two are split because the box is ASSEMBLED - the band is blanked, the
        # 9-slice frame is drawn, then the glyphs - and all of it inside one VM
        # frame that spans more than one LCD frame, so a layer switched on at
        # the START of that is displayed half-built (measured: blank paper,
        # then the border, then the text, three display frames). Every caller
        # reveals after it has drawn. A graceful no-op where there is no window
        # layer, like win_sprite_cut.
        self.symbol_table['text.win_reveal'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': []
        }

        # text.win_overlay_cut(y): stop the WINDOW OVERLAY at scanline `y` -
        # the window layer goes off there and sprites come back (unless the
        # program itself hid them), so an overlay covers only the TOP of the
        # screen with the room playing below it. The reference engine's
        # `overlay_cut_scanline`, whose default 150 is off-screen on a 144-line
        # display: anything from 144 up DISARMS the cut. Real on the GB family
        # (it is `LYC_REG` plus a window layer), a graceful no-op elsewhere.
        self.symbol_table['text.win_overlay_cut'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [
                Parameter('y', PrimitiveType('u8'))
            ]
        }

        # text.plot_tile(x, y, tile): plot ONE raw background tile id at a text
        # cell THROUGH the text-layer router -- on the GB family it follows
        # to_window onto the window overlay (the reference engine draws its 9-slice
        # dialogue frame into the window map the same way), elsewhere it lands
        # on the bkg/name table. The seam a CUSTOM tile frame needs to compose
        # with the UI overlay (a bkg.set_tiles frame is covered by the opaque
        # window). A graceful no-op on the cc65 consoles (Lynx/PCE draw text
        # off the tile table -- frame via text.fill_box there).
        self.symbol_table['text.plot_tile'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [
                Parameter('x', PrimitiveType('u8')),
                Parameter('y', PrimitiveType('u8')),
                Parameter('tile', PrimitiveType('u8'))
            ]
        }

        # Input constants
        for input_name in ['INPUT_LEFT', 'INPUT_RIGHT', 'INPUT_UP', 'INPUT_DOWN',
                          'INPUT_A', 'INPUT_B', 'INPUT_SELECT', 'INPUT_START']:
            self.symbol_table[input_name] = {
                'type': 'constant',
                'value_type': PrimitiveType('u8')
            }

        # Graphics / system stdlib functions. Argument types are not validated
        # at the call site, so only the return type matters here. 'u8' for the
        # few that yield a value, 'void' for the rest.
        u8_returning = {'sprite.get_tile', 'sprite.meta_cols', 'system.random',
                        'save.read_u8', 'system.frames', 'bkg.raster_get',
                        'sprite.hit'}
        # W7h: the next queued hUGE `6xy` parameter, 0xFFFF when the queue is
        # empty - a u16 because every BYTE value is a legal parameter, so there
        # is no spare sentinel below 256.
        u16_returning = {'huge.routine_next'}
        for fn in [
            'video.show_sprites', 'video.hide_sprites', 'video.show_background',
            'video.show_window', 'video.hide_window',
            # video.set_overlay(cb): register a PRESENT-time UI overlay drawer.
            # On the double-buffered Lynx the present recomposites the frame
            # (bkg strips + sprites) and that page scans out BEFORE any
            # post-present drawing lands -- so UI text over a moving background
            # strobed. The hook is called INSIDE gbs_present, after the blits
            # and before the flip, so the overlay is part of every composed
            # frame. A graceful no-op everywhere else (persistent tilemaps
            # compose freely; the GB family uses the hardware window instead).
            'video.set_overlay',
            # system.music_isr(update) / system.music_hold(on): vm.music's
            # VBL-interrupt tick + its main-loop stand-down latch (6.6 stage
            # 2). Only reachable behind the VM_MUSIC_ISR define, which the
            # build states per target; folded out everywhere else.
            'system.music_isr', 'system.music_hold', 'system.music_tick',
            # BATCH verbs - a whole pool in one native loop:
            #   plot(first, n, xs, ys, cols)   draw n entries from two byte
            #                                  arrays; each is `cols` sprites wide
            #   drift(pos, vel, n)             pos[i] += vel[i]
            #   hit_box(x, y, w, h); hit(xs, ys, n) -> u8
            #                                  first entry inside the box, or 255
            'sprite.plot', 'sprite.drift', 'sprite.hit_box', 'sprite.hit',
            'sprite.set_data', 'sprite.set_tile', 'sprite.get_tile',
            'sprite.set_meta',
            # set_meta_mask(base, tile, w, h, mask): set_meta with a per-COLUMN
            # blank mask (bit c = column c draws nothing; its objects park and
            # it consumes no tile) -- the sparse-frame form.
            'sprite.set_meta_mask',
            # set_meta_list(base, pw, tile, data, off, n): the per-OBJECT
            # descriptor form (the reference engine's metasprite_t model). `data + off`
            # points at n entries of (dy, dx, dtile, props), offsets from the
            # frame's top-left, dtile added to `tile`; `pw` is the frame's
            # pixel width (what a FLIP_X mirror reflects around). Rows may
            # overlap and tiles repeat freely -- the two things a dense
            # rectangle cannot say.
            'sprite.set_meta_list',
            'sprite.set_prop', 'sprite.move', 'sprite.set_palette',
            # sprite.vbl_hold(on): HOLD the shadow-OAM -> hardware copy while
            # the frame writes sprites, and release it when the frame is
            # complete. On the SMS/GG that copy runs every VBlank, and a
            # metasprite is written one COLUMN at a time (gbs_move_sprite's
            # fan loop) -- so a VBlank landing inside the loop copies half the
            # columns at the new position and half at the old, and the actor
            # is drawn torn down the middle (reported from play on
            # the SMS/GG sample conversion: a big animated actor and a tall NPC, both split by a
            # vertical seam). This is the reference engine's toggle_shadow_OAM /
            # activate_shadow_OAM pair expressed with the primitives GBDK's
            # z80 port gives us (DISABLE_/ENABLE_VBL_TRANSFER). A no-op on
            # every other console.
            'sprite.vbl_hold',
            # sprite.cut_y(y): park every sprite OBJECT at or below screen row
            # `y` (255 = off). On SMS/GG a dialogue box is plotted into the
            # scene's own background, and VDP sprites always draw ABOVE the
            # background - so any actor standing in the box's band covered the
            # text (reported from play: the town room's NPC and sign
            # drawn over a line of dialogue). The GB family answers
            # this with text.win_sprite_cut on the window layer, which is an
            # honest no-op on these two; this is that rule where they can
            # express it, in the one place every sprite is placed.
            'sprite.cut_y',
            # sprite.font_glyph(tile, ch): upload the CONSOLE font's glyph for
            # character `ch` into sprite tile `tile` (colour 3 on transparent
            # 0), read from the font the toolchain LINKS (GBDK's font_ibm, the
            # PCE's cc65 pce_font) - the overlay HUD's sprite text, which
            # carries character codes instead of any font's pixels.
            'sprite.font_glyph',
            # move_world(base, x, y): sprite.move for a WORLD-space renderer,
            # taking SIGNED screen coordinates. `sprite.move`'s x is a u8, so
            # a metasprite whose origin has scrolled past the left edge
            # arrives already wrapped - and its leading columns then draw at
            # the far RIGHT of the screen (measured on the GB: the converted
            # sample's 9-column actor at screen x -11 drew seven columns at
            # OAM 5..53 and a ghost at 253). Signed, the fan can PARK the
            # columns that fall outside the screen instead. The metasprite
            # width is read back from the meta tables, so the caller does not
            # have to know it.
            'sprite.move_world',
            # meta_cols(base): the metasprite's width in 8x8 TILE columns, as
            # the last set_meta on that base left it (0/1 = a single sprite).
            # What a world-space renderer needs to know how far past the left
            # edge an actor may go before NOTHING of it is on screen.
            'sprite.meta_cols',
            # set_meta_palettes(base, w, h, data, off): one palette per
            # CELL of a metasprite (8x8-tile units, row-major).
            'sprite.set_meta_palettes',
            # set_data_pal(first, count, data, off, slot): a 2bpp tile upload
            # rendered through a background palette SLOT (SMS/GG CRAM
            # slot*4..; a plain upload elsewhere).
            'bkg.set_data_pal',
            'bkg.set_data', 'bkg.set_tiles', 'bkg.scroll', 'bkg.move',
            # set_data_native(first, count, data): tiles already in the
            # console's OWN tile format - no run-time conversion. Differs from
            # set_data only on SMS/GG under the 16-colour tier (planar bytes
            # instead of packed nibbles).
            'bkg.set_data_native',
            'bkg.set_palette',
            # bkg.set_attrs(x, y, w, h, data): the ATTRIBUTE mirror of
            # set_tiles -- one background palette-slot byte per map cell.
            'bkg.set_attrs',
            # bkg.edge_mask(on): blank the leftmost 8 px column of the
            # background. Real only on the SMS, where the display is exactly
            # as wide as the tilemap ring and a column-streamed level has no
            # off-screen column to write into; an honest no-op everywhere
            # else (see _emit_gbdk_edge_mask).
            'bkg.edge_mask',
            # PARALLAX BANDS (the reference engine's model): the background is split into
            # up to 3 horizontal bands, each scrolled at its own rate by an
            # LYC/STAT scanline interrupt.
            #   parallax(n)              arm n bands (0 = off)
            #   parallax_band(i, last)   band i ends at scanline `last`
            #                            (0 = the last band, to the bottom)
            #   parallax_scx(i, scx)     band i's horizontal scroll, this frame
            #   parallax_scy(scy)        the LAST band's vertical scroll (the
            #                            frame's LAST scroll write: it publishes
            #                            every band's scx to the interrupt; the
            #                            upper bands are pinned to 0, as GB
            #                            Studio pins them)
            # Real on the GB family, a graceful no-op elsewhere, so a
            # target-neutral room loader calls them unconditionally.
            'bkg.parallax', 'bkg.parallax_band',
            'bkg.parallax_scx', 'bkg.parallax_scy',
            # THE PER-SCANLINE SCROLL TABLE: one scroll per screen line, played
            # back by an interrupt - a pseudo-3D road, a ripple, a "mode 7"
            # floor (bkg.parallax gives three bands; this gives every line).
            #   raster(on, first)        arm / disarm. Lines above `first` all
            #                            use line 0's entry (0 = whole screen)
            #   raster_set(line, x, y)   that line shows the map scrolled to
            #                            (x, y), as bkg.move(x, y) would
            #   raster_copy(line, n, t)  n lines from an array of (x, y) pairs
            #   raster_show()            publish; live at the next v-blank
            # Real on the GB family and on SMS/GG (horizontal only there: the
            # VDP latches the vertical scroll per frame), a no-op elsewhere.
            #   raster_get(line) -> u8   the x a line was given this frame
            # and two NATIVE fills that walk UP the screen from `line` (a road
            # is built from its nearest line; a C loop of raster_set calls is
            # five times slower):
            #   raster_curve_start(x, dx)     8.8 fixed-point value and slope
            #   raster_curve(line, n, ddx)    x scroll = high byte of x, then
            #                                 x += dx; dx += ddx (the state
            #                                 carries into the next call)
            #   raster_stripes(line, n, depth, phase, y)
            #                                 y scroll = y when bit 7 of
            #                                 depth[k] + phase is set, else 0
            'bkg.raster', 'bkg.raster_set', 'bkg.raster_copy',
            'bkg.raster_show', 'bkg.raster_get', 'bkg.raster_curve_start',
            'bkg.raster_curve', 'bkg.raster_stripes',
            'window.set_tiles', 'window.move',
            'palette.set_bkg', 'palette.set_sprite',
            'palette.load_bkg', 'palette.load_sprite', 'palette.load_sprite16',
            'palette.load_bkg16',
            # load_bkg_set / load_sprite_set(slot, count, colors, off): a RUN of
            # 4-colour slots out of one table (a scene's whole palette set).
            'palette.load_bkg_set', 'palette.load_sprite_set',
            # load_native(first, count, data): whole hardware palettes in the
            # console's NATIVE colour format (SMS/GG; emitted only inside the
            # transpiler's platform fork, a no-op elsewhere).
            'palette.load_native',
            # set_2bpp(map): which four CRAM entries a 2bpp tile's pixel
            # values expand onto (SMS/GG; an inline no-op elsewhere).
            'palette.set_2bpp',
            # fade(level): scale every LOADED palette towards black, 0 normal
            # .. 3 black. The COLOUR counterpart of the DMG BGP/OBP ramp (which
            # the hardware ignores in CGB mode); a graceful no-op on a console
            # whose palettes are not scalable at runtime.
            'palette.fade',
            'system.delay', 'system.random', 'system.seed_random',
            # frames() -> u8: a free-running DISPLAY-frame counter, for anything
            # whose rate must not depend on how long the game loop takes (the
            # music tick). Interrupt/timer driven on BOTH backends - GBDK's
            # sys_time off the VBL interrupt, cc65's clock() off a hardware
            # timer at ~60 Hz - so it keeps counting through a long frame, which
            # is the whole point. Emitted only when called.
            'system.frames',
            # cpu_fast(on): the Game Boy Color's double-speed CPU mode. A no-op
            # on every other console. Timer-clocked rates double with it;
            # v-blank-clocked ones do not.
            'system.cpu_fast',
            # set_view(ox, oy): the LETTERBOX offset in pixels - a room smaller
            # than the screen is shown centred (the scroll commit subtracts it,
            # sprite placement adds it). SMS / Game Gear / PC Engine; a no-op
            # where the screen is never bigger than a room.
            'video.set_view',
            'sound.beep', 'sound.stop', 'sound.sfx',
            'sound.beep2', 'sound.stop2',   # a 2nd simultaneous voice (music channel)
            # platform.save (battery SRAM): enable/disable map the cart RAM window,
            # write_u8(off, v) / read_u8(off) -> u8 access it. Honest-off via has_save.
            'save.enable', 'save.disable', 'save.write_u8', 'save.read_u8',
            # native.lynx escape hatch (real on Lynx, no-op elsewhere).
            'lynx.fade_in', 'lynx.fade_out', 'lynx.screen_shake', 'lynx.jingle',
            # native.huge (hUGEDriver, GB family only -- codegen refuses it
            # elsewhere rather than lowering it to a silent no-op).
            'huge.play', 'huge.update', 'huge.stop', 'huge.mute',
            'huge.set_position', 'huge.set_rate', 'huge.routine_next',
        ]:
            ret = (PrimitiveType('u8') if fn in u8_returning
                   else PrimitiveType('u16') if fn in u16_returning
                   else PrimitiveType('void'))
            self.symbol_table[fn] = {'type': 'function', 'return_type': ret,
                                     'parameters': []}

        # Asset residency seam (asset-streaming groundwork): assets.use(id) is a
        # void ensure-resident hint; assets.ptr(id) yields a pointer to the
        # asset's tile data (a u8 array/pointer) to hand to a setter. Argument
        # types aren't validated at the call site, so the loose signatures suffice.
        self.symbol_table['assets.use'] = {
            'type': 'function',
            'return_type': PrimitiveType('void'),
            'parameters': [Parameter('id', PrimitiveType('u8'))]
        }
        self.symbol_table['assets.ptr'] = {
            'type': 'function',
            'return_type': ArrayType(PrimitiveType('u8'), None),
            'parameters': [Parameter('id', PrimitiveType('u8'))]
        }
        # assets.code_byte(blob, off) reads one byte of a streamed const blob (the
        # VM8 bytecode blob's fetch seam, §7.1 #3): a byte-identical `blob[off]`
        # on every directly-mapped console + a small blob, and the Lynx page-cache
        # cart read when the blob is big enough to stream.
        # assets.address(SYM): the address of a const blob (valid while its bank
        # is mapped); assets.bank_enter(SYM) maps the blob's ROM bank and
        # returns the bank that was mapped; assets.bank_leave(saved) maps it
        # back. The VM8 interpreter's per-slice code window (review V-5).
        self.symbol_table['assets.address'] = {
            'type': 'function', 'return_type': PrimitiveType('addr'),
            'parameters': [Parameter('blob', PrimitiveType('u8'))]}
        self.symbol_table['assets.bank_enter'] = {
            'type': 'function', 'return_type': PrimitiveType('u8'),
            'parameters': [Parameter('blob', PrimitiveType('u8'))]}
        self.symbol_table['assets.bank_leave'] = {
            'type': 'function', 'return_type': PrimitiveType('void'),
            'parameters': [Parameter('saved', PrimitiveType('u8'))]}
        self.symbol_table['assets.code_byte'] = {
            'type': 'function',
            'return_type': PrimitiveType('u8'),
            'parameters': [Parameter('blob', ArrayType(PrimitiveType('u8'), None)),
                           Parameter('off', PrimitiveType('u16'))]
        }
        # Range-windowed residency seam (paint_table + [world] stream):
        # a concatenated map/collision array is read one room-sized
        # WINDOW at a time. range_base(sym, maxwin) registers it (+ the widest
        # window); use_range(sym, off, len) warms a window; ptr_range(sym, off,
        # len) yields a pointer to it (handed to a setter); range_byte(sym, off,
        # idx) reads one byte. Loosely typed like use/ptr (args not validated).
        self.symbol_table['assets.range_base'] = {
            'type': 'function', 'return_type': PrimitiveType('void'),
            'parameters': [Parameter('sym', ArrayType(PrimitiveType('u8'), None)),
                           Parameter('maxwin', PrimitiveType('u16'))]
        }
        self.symbol_table['assets.use_range'] = {
            'type': 'function', 'return_type': PrimitiveType('void'),
            'parameters': [Parameter('sym', ArrayType(PrimitiveType('u8'), None)),
                           Parameter('off', PrimitiveType('u16')),
                           Parameter('len', PrimitiveType('u16'))]
        }
        self.symbol_table['assets.ptr_range'] = {
            'type': 'function',
            'return_type': ArrayType(PrimitiveType('u8'), None),
            'parameters': [Parameter('sym', ArrayType(PrimitiveType('u8'), None)),
                           Parameter('off', PrimitiveType('u16')),
                           Parameter('len', PrimitiveType('u16'))]
        }
        self.symbol_table['assets.range_byte'] = {
            'type': 'function', 'return_type': PrimitiveType('u8'),
            'parameters': [Parameter('sym', ArrayType(PrimitiveType('u8'), None)),
                           Parameter('off', PrimitiveType('u16')),
                           Parameter('idx', PrimitiveType('u16'))]
        }

        # palette.rgb quantizes RGB888 to the console's native color word
        # (an opaque u16: RGB555 on GBC, RGB222/444 on SMS/GG, an NES master
        # palette index, a DMG shade, 12-bit GBR on Lynx, 9-bit GRB on PCE).
        self.symbol_table['palette.rgb'] = {
            'type': 'function',
            'return_type': PrimitiveType('u16'),
            'parameters': [Parameter('r', PrimitiveType('u8')),
                           Parameter('g', PrimitiveType('u8')),
                           Parameter('b', PrimitiveType('u8'))]
        }

        # Stdlib constants usable as plain identifiers.
        for const_name in ['REG_DIV', 'REG_NR10', 'REG_BGP', 'REG_OBP0', 'REG_OBP1']:
            self.symbol_table[const_name] = {
                'type': 'constant', 'value_type': PrimitiveType('addr')}
        for const_name in ['FLIP_X', 'FLIP_Y',
                           'SFX_COIN', 'SFX_HURT', 'SFX_JUMP', 'SFX_POINT',
                           'SFX_SELECT']:
            self.symbol_table[const_name] = {
                'type': 'constant', 'value_type': PrimitiveType('u8')}
        # Screen geometry (per-platform prelude #defines). u16: SMS/NES are
        # 256 px wide.
        for const_name in ['SCREEN_WIDTH', 'SCREEN_HEIGHT',
                           'SCREEN_COLS', 'SCREEN_ROWS']:
            self.symbol_table[const_name] = {
                'type': 'constant', 'value_type': PrimitiveType('u16')}

    def register_assets(self, assets, palettes=None, palettes16=None,
                        sprite_defs=None):
        """Register asset-pipeline symbols (`<name>_tiles` data arrays and
        `<name>_tile_count` defines, emitted into the TU by the codegen;
        plus `<name>_palette` (4-colour) / `<name>_palette16` (4bpp assets)
        native-color arrays for indexed PNGs)."""
        for asset in assets:
            name, data = asset[0], asset[1]   # (name, data) or (name, data, bpp)
            self.symbol_table['%s_tiles' % name] = {
                'type': 'constant',
                'value_type': ArrayType(PrimitiveType('u8'), len(data))}
            self.symbol_table['%s_tile_count' % name] = {
                'type': 'constant', 'value_type': PrimitiveType('u8')}
        for name, _colors in (palettes or []):
            self.symbol_table['%s_palette' % name] = {
                'type': 'constant',
                'value_type': ArrayType(PrimitiveType('u16'), 4)}
        for name, _colors in (palettes16 or []):
            self.symbol_table['%s_palette16' % name] = {
                'type': 'constant',
                'value_type': ArrayType(PrimitiveType('u16'), 16)}
        for sname, _off, _w, _h in (sprite_defs or []):
            for suffix in ('_tile', '_w', '_h'):
                self.symbol_table['%s%s' % (sname, suffix)] = {
                    'type': 'constant', 'value_type': PrimitiveType('u8')}

    def check_program(self, program: Program):
        # Pre-register every module's functions under their qualified name
        # (`<alias>.<function>`, alias = last name segment) so cross-module
        # calls resolve no matter the module order.
        for module in program.modules:
            alias = module.name.rsplit('.', 1)[-1]
            for decl in module.declarations:
                if isinstance(decl, FunctionDecl):
                    self.symbol_table['%s.%s' % (alias, decl.name)] = {
                        'type': 'function',
                        'return_type': decl.return_type,
                        'parameters': decl.parameters,
                        'arity_known': True,      # a user function: check calls
                    }
                elif isinstance(decl, VarDecl):
                    # ... and its consts/globals, so `alias.NAME` infers as its
                    # DECLARED (or literal-inferred) type. It used to fall
                    # through to u8, which sized `for i in 0..lim.LIMIT` as a
                    # uint8_t loop variable -- an endless loop for LIMIT > 255.
                    vtype = decl.type
                    if vtype is None and isinstance(decl.initializer, Literal):
                        try:
                            vtype = self.infer_type(decl.initializer)
                        except Exception:
                            vtype = None
                    if vtype is not None:
                        self.symbol_table['%s.%s' % (alias, decl.name)] = {
                            'type': 'constant', 'value_type': vtype}
        # ...and every module's TYPES, before any of them is checked: a type
        # declared in a later module used to warn "Unknown type" in an earlier
        # one purely because of declaration order (they are program-global -
        # type names are never mangled).
        for module in program.modules:
            for decl in module.declarations:
                if isinstance(decl, TypeDecl):
                    self.check_type_declaration(decl)
        for module in program.modules:
            self.check_module(module)

    def check_module(self, module: Module):
        self._module = module.name
        self._file = getattr(module, 'source_file', None)
        self._line = None
        # THE SCOPE IS PER MODULE. It used to accumulate every module's
        # globals as they were checked, so a BARE reference to another
        # module's variable type-checked here and failed in the C compiler
        # instead - with a C line number. Cross-module access is
        # `alias.name`, which resolves through the symbol table (pre-registered
        # in check_program) and is unaffected.
        self.current_scope = {}
        self._const_names = set()
        # Process imports first
        for imp in module.imports:
            self.imported_modules[imp.module_name] = True

        # Register types/constants first so they resolve everywhere.
        for decl in module.declarations:
            if isinstance(decl, TypeDecl):
                self.check_type_declaration(decl)

        # Pre-register all functions and module-level variables so that
        # forward references and global accesses resolve regardless of the
        # order in which declarations appear in the source.
        for decl in module.declarations:
            if isinstance(decl, FunctionDecl):
                self.symbol_table[decl.name] = {
                    'type': 'function',
                    'return_type': decl.return_type,
                    'parameters': decl.parameters,
                    'arity_known': True,          # a user function: check calls
                }
            elif isinstance(decl, VarDecl):
                self.current_scope[decl.name] = decl.type
                if decl.is_const:
                    # A module-level `const` is a VarDecl that lands in scope
                    # like any global, so the scope has to remember which
                    # names cannot be assigned to (see _check_assignable).
                    self._const_names.add(decl.name)

        # Then check functions and variables
        for decl in module.declarations:
            if not isinstance(decl, TypeDecl):
                self.check_declaration(decl)

    def check_declaration(self, decl: Declaration):
        self._line = getattr(decl, 'line', None)
        if isinstance(decl, FunctionDecl):
            self.check_function(decl)
        elif isinstance(decl, VarDecl):
            self.check_variable(decl)
        elif isinstance(decl, TypeDecl):
            self.check_type_declaration(decl)

    def check_function(self, func: FunctionDecl):
        # Add function to symbol table
        self.symbol_table[func.name] = {
            'type': 'function',
            'return_type': func.return_type,
            'parameters': func.parameters,
            'arity_known': True,              # a user function: check calls
        }

        # Create new scope for function
        old_scope = self.current_scope.copy()
        old_ret, old_seen = getattr(self, '_ret_type', None), getattr(self, '_ret_seen', False)
        self._ret_type, self._ret_seen = func.return_type, False

        # Add parameters to scope
        for param in func.parameters:
            self.current_scope[param.name] = param.type

        # Check function body
        for stmt in func.body:
            self.check_statement(stmt)

        # A VALUE FUNCTION THAT NEVER RETURNS ONE. Deliberately not a path
        # analysis - only "no `return <value>` anywhere in the body", which is
        # the case that cannot be a false positive and is exactly the one C
        # lets through as an undefined return value.
        if (func.return_type is not None
                and getattr(func.return_type, 'name', None) != 'void'
                and not self._ret_seen):
            self._line = getattr(func, 'line', None) or self._line
            self.error("Function '%s' is declared -> %s but never returns a "
                       "value" % (func.name, self.type_to_string(func.return_type)))

        # Restore previous scope
        self._ret_type, self._ret_seen = old_ret, old_seen
        self.current_scope = old_scope

    def validate_type(self, type_obj: Type):
        """Validate that a type is known/defined - ENHANCED VERSION."""
        if isinstance(type_obj, PrimitiveType):
            # Primitive types are always valid
            return
        elif isinstance(type_obj, UserDefinedType):
            # Check if user-defined type exists in type_table
            if type_obj.name not in self.type_table:
                self.error(f"Unknown type: '{type_obj.name}'. Available types: {list(self.type_table.keys())}")
        elif isinstance(type_obj, ArrayType):
            self.validate_type(type_obj.element_type)
        elif isinstance(type_obj, StructType):
            for field in type_obj.fields:
                self.validate_type(field.type)
        elif isinstance(type_obj, FunctionType):
            for pt in type_obj.param_types:
                self.validate_type(pt)
            if type_obj.return_type is not None:
                self.validate_type(type_obj.return_type)
        else:
            self.error(f"Invalid type: {type_obj}")

    def check_variable(self, var: VarDecl):
        if var.type:
            if isinstance(var.type, UserDefinedType):
                if var.type.name not in self.type_table:
                    self.error(f"Unknown type: '{var.type.name}'")
            elif isinstance(var.type, ArrayType):
                self.validate_type(var.type.element_type)
            elif isinstance(var.type, StructType):
                for field in var.type.fields:
                    self.validate_type(field.type)

        # Type inference if not specified. The STORAGE rule, not the
        # expression's own C width - see _storage_type. Using the value type
        # here would widen `var d = a - b` from uint8_t to int16_t and change
        # the C of every existing program.
        if var.type is None and var.initializer:
            var.type = self._storage_type(var.initializer)

        # Struct/array literal initializers are checked structurally elsewhere;
        # their element types are taken from the declared aggregate type.
        is_aggregate_literal = isinstance(var.initializer, (StructLiteral, ArrayLiteral))

        # A LITERAL THAT DOES NOT FIT. `types_compatible` lets the integer
        # types interoperate freely (the 8-bit target promotes and truncates
        # by itself, and that is the language's value semantics), so a literal
        # is the one case where the width is knowable and the truncation is
        # certainly not what was meant: `var q: u8 = 300` was `uint8_t q =
        # 300` and silent. Negative into unsigned keeps its own wording,
        # because that is the reading error, not an overflow.
        if (var.type and isinstance(var.initializer, Literal)
                and var.initializer.type == "number"
                and isinstance(var.type, PrimitiveType)):
            self._check_literal_fits(var.initializer.value, var.type,
                                     "'%s: %s'" % (var.name, var.type.name))

        # Check initializer type matches declared type
        if var.initializer and var.type and not is_aggregate_literal:
            init_type = self.infer_type(var.initializer)
            if init_type is not None and not self.types_compatible(var.type, init_type):
                self.error(f"Cannot assign {self.type_to_string(init_type)} to {self.type_to_string(var.type)}")

        if getattr(var, 'is_const', False):
            self._const_names.add(var.name)

        # Add to current scope
        self.current_scope[var.name] = var.type

    def check_type_declaration(self, type_decl: TypeDecl):
        # Add type to type table
        self.type_table[type_decl.name] = type_decl.type_def

        # If it's an enum, register the enum constants
        if isinstance(type_decl.type_def, EnumType):
            for variant in type_decl.type_def.variants:
                # Register each enum constant as a constant in the symbol table
                self.symbol_table[variant.name] = {
                    'type': 'constant',
                    'value_type': UserDefinedType(type_decl.name),  # The enum type
                    'value': variant.value
                }

    def check_statement(self, stmt: Statement):
        self._line = getattr(stmt, 'line', None) or getattr(self, '_line', None)
        if isinstance(stmt, ExpressionStmt):
            self.check_expression(stmt.expression)
        elif isinstance(stmt, VarDeclStmt):
            self.check_variable(stmt.var_decl)
        elif isinstance(stmt, IfStmt):
            cond_type = self.check_expression(stmt.condition)
            if cond_type is not None and not self.is_boolean_type(cond_type):
                self.error("If condition must be boolean")
            for s in stmt.then_body:
                self.check_statement(s)
            if stmt.else_body:
                for s in stmt.else_body:
                    self.check_statement(s)
        elif isinstance(stmt, LoopStmt):
            for s in stmt.body:
                self.check_statement(s)
        elif isinstance(stmt, WhileStmt):
            cond_type = self.check_expression(stmt.condition)
            if cond_type is not None and not self.is_boolean_type(cond_type):
                self.error("While condition must be boolean")
            for s in stmt.body:
                self.check_statement(s)
        elif isinstance(stmt, SwitchStmt):
            self.check_expression(stmt.subject)
            for labels, body in stmt.cases:
                for label in labels:
                    self.check_expression(label)
                for s in body:
                    self.check_statement(s)
            if stmt.default_body:
                for s in stmt.default_body:
                    self.check_statement(s)
        elif isinstance(stmt, (BreakStmt, ContinueStmt)):
            pass
        elif isinstance(stmt, ForStmt):
            # The loop variable's width follows the bounds: a 16-bit bound
            # with a u8 loop variable can never be reached (`for i in 0..256`
            # would loop forever), so annotate the AST for codegen -- the same
            # deterministic annotation pattern as check_variable's var.type.
            wide = False
            for bound in (stmt.start, stmt.end):
                bt = self.infer_type(bound)
                if (isinstance(bt, PrimitiveType)
                        and bt.name in ('u16', 'i16', 'addr')):
                    wide = True
            stmt.loop_var_type = 'u16' if wide else 'u8'
            # The loop variable is in scope for the body.
            self.current_scope[stmt.var_name] = PrimitiveType(
                stmt.loop_var_type)
            for s in stmt.body:
                self.check_statement(s)
        elif isinstance(stmt, ReturnStmt):
            declared = getattr(self, '_ret_type', None)
            is_void = declared is None or getattr(declared, 'name', None) == 'void'
            if stmt.value:
                self._ret_seen = True
                got = self.check_expression(stmt.value)
                if is_void:
                    self.error("Returning a value from a function declared "
                               "without a return type")
                elif (got is not None
                        and not self.types_compatible(declared, got)):
                    self.error("Cannot return %s from a function declared -> %s"
                               % (self.type_to_string(got),
                                  self.type_to_string(declared)))
                elif isinstance(stmt.value, Literal) and stmt.value.type == "number":
                    self._check_literal_fits(stmt.value.value, declared,
                                             "the return type %s"
                                             % self.type_to_string(declared))
            elif not is_void:
                self.error("Bare `return` in a function declared -> %s"
                           % self.type_to_string(declared))

    def check_expression(self, expr: Expression) -> Type:
        return self.infer_type(expr)

    def _is_stdlib_constant(self, expr) -> bool:
        """A bare identifier naming a registered constant (SCREEN_WIDTH, an
        asset symbol ...): an int #define in the generated C."""
        if not isinstance(expr, Identifier) or expr.name in self.current_scope:
            return False
        sym = self.symbol_table.get(expr.name)
        return bool(sym) and sym.get('type') == 'constant'

    @staticmethod
    def _is_int_literal(expr) -> bool:
        """A number literal that reaches C as a plain `int` (0..32767; see
        gen_expr._c_int_literal). It is typed u16 above 255, but C compares it
        SIGNED against an i16. 32768..65535 carries a `U` suffix and really is
        unsigned, so it does not qualify."""
        return (isinstance(expr, Literal) and expr.type == "number"
                and 0 <= expr.value <= 32767)

    def _check_assignable(self, target) -> None:
        """A `const` is not an assignment target: it is a `#define` in C, and
        `#define X (3)` on the left of an `=` is a C syntax error rather than
        anything the author would recognise."""
        if not isinstance(target, Identifier):
            return
        name = target.name
        if name in self._const_names:
            self.error("Cannot assign to constant '%s'" % name)
            return
        if name in self.current_scope:
            return
        sym = self.symbol_table.get(name)
        if sym and sym.get('type') == 'constant':
            self.error("Cannot assign to constant '%s'" % name)

    def _visit_arguments(self, arguments):
        """Type every argument, for its SIDE EFFECT of diagnosing what is in it.

        Arguments used not to be visited at all, so `f(undefined, 2)` was
        silent - the undefined name was never looked at, let alone typed."""
        out = []
        for arg in arguments or ():
            try:
                out.append(self.infer_type(arg))
            except Exception:
                # The pass must stay total (see `error`): a malformed argument
                # is the codegen's problem, not a reason to abandon the file.
                out.append(None)
        return out

    def _check_arguments(self, func_name, func_info, arguments):
        """Visit each argument and type it against its parameter.

        Only where BOTH types are known: `types_compatible` already lets the
        integer types interoperate, so what this catches is the shape errors -
        an array where a scalar is wanted, a struct of the wrong name, a
        function pointer with a different signature. A literal that cannot fit
        the parameter is the one width case worth naming, for the same reason
        it is worth naming at a `var`."""
        types = self._visit_arguments(arguments)
        params = func_info.get('parameters') or ()
        if not func_info.get('arity_known') or len(params) != len(arguments or ()):
            return
        for i, (param, arg) in enumerate(zip(params, arguments)):
            want = getattr(param, 'type', None)
            if want is None:
                continue
            if isinstance(arg, Literal) and arg.type == "number":
                self._check_literal_fits(
                    arg.value, want,
                    "parameter %d of '%s' (%s)"
                    % (i + 1, func_name, self.type_to_string(want)))
                continue
            got = types[i]
            if got is not None and not self.types_compatible(want, got):
                self.error("Argument %d of '%s' is %s, the parameter is %s"
                           % (i + 1, func_name, self.type_to_string(got),
                              self.type_to_string(want)))

    def _resolvable_ident(self, name: str) -> bool:
        """True when a bare identifier resolves as a VALUE (a local/param, a
        constant/function/global, or a type name) -- i.e. `infer_type` on it
        won't record an "Undefined variable". A module alias (`scenes`, a stdlib
        `video`) resolves as NONE of these, which is how a module-qualified
        FieldAccess tells itself apart from a struct-value field access."""
        return (name in ('true', 'false')
                or name in self.current_scope
                or name in self.symbol_table
                or name in self.type_table)

    def infer_type(self, expr: Expression) -> Type:
        if isinstance(expr, Literal):
            if expr.type == "number":
                # Infer based on value range
                if 0 <= expr.value <= 255:
                    return PrimitiveType("u8")
                elif -128 <= expr.value <= 127:
                    return PrimitiveType("i8")
                elif 0 <= expr.value <= 65535:
                    return PrimitiveType("u16")
                else:
                    return PrimitiveType("i16")
            elif expr.type == "string":
                # +1 for the NUL the C literal carries. Without it
                # `var s = "hi"` inferred `uint8_t s[2] = "hi"`, one byte
                # short of what every text verb then reads. No program in the
                # repo infers a string variable (checked), so this changes no
                # existing output.
                return ArrayType(PrimitiveType("u8"), len(expr.value) + 1)
            elif expr.type == "bool":
                return PrimitiveType("bool")

        elif isinstance(expr, Identifier):
            # Boolean literals are lexed as identifiers.
            if expr.name in ('true', 'false'):
                return PrimitiveType("bool")
            # Check current scope first (function parameters, local variables)
            if expr.name in self.current_scope:
                return self.current_scope[expr.name]
            # Check symbol table for constants, functions, and global variables
            elif expr.name in self.symbol_table:
                sym_info = self.symbol_table[expr.name]
                if sym_info['type'] == 'constant':
                    return sym_info['value_type']
                elif sym_info['type'] == 'function':
                    # A function name used as a *value* (not the callee of a
                    # call -- the FunctionCall branch resolves callees directly)
                    # is a function pointer: yield its FunctionType so it can be
                    # stored in / passed as a `function(...)`-typed slot.
                    return FunctionType(
                        [p.type for p in sym_info['parameters']],
                        sym_info['return_type'])
                else:
                    return PrimitiveType('void')
            # Check if it's a user-defined type name
            elif expr.name in self.type_table:
                return UserDefinedType(expr.name)
            else:
                # More helpful error message
                available_names = list(self.current_scope.keys()) + list(self.symbol_table.keys())
                self.error(f"Undefined variable: {expr.name}. Available: {available_names[:10]}")
                return None

        elif isinstance(expr, BinaryOp):
            left_type = self.infer_type(expr.left)
            right_type = self.infer_type(expr.right)

            if expr.operator in ['+', '-', '*', '/', '%', '&', '|', '^']:
                # Arithmetic / bitwise operations
                return self.promote_arithmetic_type(left_type, right_type)
            elif expr.operator in ['<<', '>>']:
                # A shift's width is its left operand's (the shift count does
                # not widen the result).
                return left_type
            elif expr.operator in ['==', '!=', '<', '>', '<=', '>=']:
                # Comparison operations. A signed 16-bit against an unsigned
                # 16-bit compares UNSIGNED in C (int is 16-bit on sdcc/cc65,
                # so the i16 converts): `-1 < 5u` is false. Say so.
                names = {getattr(left_type, 'name', None), getattr(right_type, 'name', None)}
                if (names == {'i16', 'u16'} and expr.operator in ('<', '>', '<=', '>=')
                        and not self._is_stdlib_constant(expr.left)
                        and not self._is_stdlib_constant(expr.right)
                        and not self._is_int_literal(expr.left)
                        and not self._is_int_literal(expr.right)):
                    # Equality is bit-exact either way; an ORDERING is what
                    # flips. A stdlib constant (SCREEN_WIDTH ...) is a plain
                    # int #define in C, so it does not force the conversion,
                    # and neither does a literal the codegen writes as a bare
                    # `int` (`a > 1023` in vm.trig compares signed).
                    self.error("Comparison of i16 with u16 is unsigned in C (a "
                               "negative i16 compares as a large value); cast "
                               "or widen one side explicitly")
                return PrimitiveType("bool")
            elif expr.operator in ['and', 'or']:
                # Logical operations
                return PrimitiveType("bool")
            elif expr.operator in ['=', '+=', '-=', '|=', '&=', '^=']:
                # An assignment expression's value is its target's - and the
                # right-hand side has to be able to become one. Only the shape
                # is checked (the integer types interoperate by design); the
                # width is checked for a LITERAL, where it is knowable.
                if left_type is not None and right_type is not None:
                    if isinstance(expr.right, Literal) and expr.right.type == "number":
                        self._check_literal_fits(
                            expr.right.value, left_type,
                            "the assignment target (%s)"
                            % self.type_to_string(left_type))
                    elif not self.types_compatible(left_type, right_type):
                        self.error("Cannot assign %s to %s"
                                   % (self.type_to_string(right_type),
                                      self.type_to_string(left_type)))
                self._check_assignable(expr.left)
                return left_type

        elif isinstance(expr, UnaryOp):
            operand_type = self.infer_type(expr.operand)
            if expr.operator == 'not':
                return PrimitiveType("bool")
            elif expr.operator == '-':
                return operand_type

        elif isinstance(expr, FunctionCall):
            # Check if function exists and validate call
            func_name = None
            if isinstance(expr.function, Identifier):
                func_name = expr.function.name
            elif isinstance(expr.function, FieldAccess):
                # Handle module.function calls
                if isinstance(expr.function.object, Identifier):
                    func_name = f"{expr.function.object.name}.{expr.function.field}"

            # A local / parameter of a FunctionType SHADOWS a same-named
            # function elsewhere in the program (the symbol table is flat
            # across modules): `gather(...)` inside engine.scrollpx.update is
            # its callback parameter, not some module's `gather` function.
            shadowed = (isinstance(expr.function, Identifier)
                        and expr.function.name in self.current_scope)
            if func_name and func_name in self.symbol_table and not shadowed:
                func_info = self.symbol_table[func_name]
                if func_info['type'] == 'function':
                    # Arity, for user functions (the stdlib entries register
                    # with an empty parameter list, so they are exempt). C
                    # would reject the call, but only after codegen and with
                    # a C line number.
                    if func_info.get('arity_known'):
                        want = len(func_info['parameters'])
                        got = len(expr.arguments)
                        if want != got:
                            self.error("Call to '%s' passes %d argument(s), it takes %d"
                                       % (func_name, got, want))
                    self._check_arguments(func_name, func_info, expr.arguments)
                    return func_info['return_type'] or PrimitiveType('void')
            else:
                # Calling through a function pointer: the callee is a variable,
                # parameter or array/struct element of a FunctionType (e.g.
                # `cb(x)` or `handlers[i](x)`), not a named function. Infer the
                # callee's type and, when it is callable, yield its return type.
                callee_type = self.infer_type(expr.function)
                self._visit_arguments(expr.arguments)
                if isinstance(callee_type, FunctionType):
                    return callee_type.return_type or PrimitiveType('void')
                # Function not found: record it, yield "unknown".
                if func_name:
                    self.error(f"Undefined function: {func_name}")
                else:
                    self.error("Invalid function call")
                return None

        elif isinstance(expr, FieldAccess):
            # A module-qualified function used as a value (`mod.handler` passed
            # as a callback) -- pre-registered as `alias.func` in the symbol
            # table -- is a function pointer.
            if isinstance(expr.object, Identifier):
                qualified = f"{expr.object.name}.{expr.field}"
                sym = self.symbol_table.get(qualified)
                if sym and sym['type'] == 'function':
                    return FunctionType(
                        [p.type for p in sym['parameters']], sym['return_type'])
                if (sym and sym['type'] == 'constant'
                        and not self._resolvable_ident(expr.object.name)):
                    return sym['value_type']      # another module's const/global
                # A module-qualified access whose object is a module ALIAS, not a
                # struct value (`scenes.SCENE_W`, `menu.confirmed()`, a stdlib
                # `video.*`): the alias is not a variable/const/type, so inferring
                # its type would raise a spurious "Undefined variable". Only a REAL
                # value (a struct var) resolves as an identifier; when the object
                # doesn't, treat it as module-qualified and yield unknown (u8, the
                # same fallthrough as a non-struct object -- byte-identical codegen).
                if not self._resolvable_ident(expr.object.name):
                    return PrimitiveType("u8")
            # Struct member access: resolve the field's declared type (so a
            # struct field holding a callback infers as its FunctionType).
            try:
                obj_type = self.infer_type(expr.object)
            except Exception:
                obj_type = None
            if isinstance(obj_type, UserDefinedType) and obj_type.name in self.type_table:
                td = self.type_table[obj_type.name]
                if isinstance(td, StructType):
                    for f in td.fields:
                        if f.name == expr.field:
                            return f.type
            return PrimitiveType("u8")

        elif isinstance(expr, ArrayAccess):
            # Array access returns element type
            array_type = self.infer_type(expr.array)
            if isinstance(array_type, ArrayType):
                return array_type.element_type
            else:
                return PrimitiveType("u8")

        elif isinstance(expr, ArrayLiteral):
            # Aggregate literal: a byte array sized to its elements.
            return ArrayType(PrimitiveType("u8"), len(expr.elements))

        # Unknown / not inferable. None (not void) so callers skip their
        # checks instead of reporting a bogus mismatch against "void".
        return None

    INTEGER_TYPES = {'u8', 'i8', 'u16', 'i16', 'addr'}

    # The value range of each integer type, for the one check where the width
    # is knowable: a literal. Everything else keeps interoperating freely.
    _INT_RANGE = {'u8': (0, 255), 'i8': (-128, 127),
                  'u16': (0, 65535), 'i16': (-32768, 32767),
                  'addr': (0, 65535)}

    def _check_literal_fits(self, value, type_obj, where) -> bool:
        """Diagnose an integer literal that does not fit `type_obj`."""
        rng = self._INT_RANGE.get(getattr(type_obj, 'name', None))
        if rng is None or not isinstance(value, int):
            return True
        lo, hi = rng
        if lo <= value <= hi:
            return True
        if value < 0 and lo == 0:
            self.error("Cannot assign negative literal %d to unsigned %s"
                       % (value, where))
        else:
            self.error("Literal %d does not fit %s (range %d..%d); it is "
                       "truncated silently in C" % (value, where, lo, hi))
        return False

    def types_compatible(self, type1: Type, type2: Type) -> bool:
        if isinstance(type1, PrimitiveType) and isinstance(type2, PrimitiveType):
            if type1.name == type2.name:
                return True
            # Integer types interoperate freely; the 8-bit target promotes and
            # truncates automatically, matching mosaik's value semantics.
            # `bool` IS one of them: it lowers to `uint8_t` (generator.py's
            # type map), which is why `topdown.facing4(input.held(...), ...)`
            # is correct C - and why leaving bool out of this set made the
            # argument check invent 44 warnings on working code the first time
            # it was swept.
            ok = self.INTEGER_TYPES | {'bool'}
            return type1.name in ok and type2.name in ok
        elif isinstance(type1, UserDefinedType) and isinstance(type2, UserDefinedType):
            return type1.name == type2.name
        elif isinstance(type1, ArrayType) and isinstance(type2, ArrayType):
            return (self.types_compatible(type1.element_type, type2.element_type) and
                    type1.size == type2.size)
        elif isinstance(type1, FunctionType) and isinstance(type2, FunctionType):
            # Structural equality: same arity and pairwise-compatible params,
            # compatible return types (void on both sides when omitted).
            if len(type1.param_types) != len(type2.param_types):
                return False
            for a, b in zip(type1.param_types, type2.param_types):
                if not self.types_compatible(a, b):
                    return False
            r1 = type1.return_type or PrimitiveType('void')
            r2 = type2.return_type or PrimitiveType('void')
            return self.types_compatible(r1, r2)
        return False

    def is_boolean_type(self, type_obj: Type) -> bool:
        return isinstance(type_obj, PrimitiveType) and type_obj.name == "bool"

    def _storage_type(self, expr) -> Type:
        """The type a `var` INITIALISED from `expr` gets.

        Deliberately not the expression's own C type (see
        `promote_arithmetic_type`): `var d = a - b` on two u8 is a `uint8_t`,
        and the C assignment truncates to it - which is what C itself does at
        an assignment, and what every existing program was compiled as. The
        WIDER value type is the truth about the expression while it is being
        evaluated; this is the truth about where it is put."""
        if isinstance(expr, BinaryOp):
            if expr.operator in ('+', '-', '*', '/', '%', '&', '|', '^'):
                return self.promote_arithmetic_type(self._storage_type(expr.left),
                                                    self._storage_type(expr.right),
                                                    storage=True)
            if expr.operator in ('<<', '>>'):
                return self._storage_type(expr.left)
        if isinstance(expr, UnaryOp) and expr.operator == '-':
            return self._storage_type(expr.operand)
        return self.infer_type(expr)

    def promote_arithmetic_type(self, type1: Type, type2: Type,
                                storage: bool = False) -> Type:
        # Unknown on either side -> unknown (never guess a width here: the
        # result may be written into the AST and read by codegen).
        if type1 is None or type2 is None:
            return None
        if isinstance(type1, PrimitiveType) and isinstance(type2, PrimitiveType):
            type_order = ['u8', 'i8', 'u16', 'i16']
            n1, n2 = type1.name, type2.name
            if not storage and n1 in type_order and n2 in type_order:
                # C'S RULE, which is what the generated code actually does
                # (review L-1, measured 2026-09-06). `gen_expression` emits
                # `(a op b)` with no casts, so C's usual arithmetic conversions
                # govern: on sdcc and cc65 `int` is 16 bits, so BOTH 8-bit
                # operands widen and the result does NOT wrap. The old rank
                # order said `u8 - u8` is u8 - a wrapping model the emitted C
                # never had.
                #
                # AND THE CODEBASE DEPENDS ON C'S RULE, which is why only the
                # model moved and not a single emitted byte: a sweep of all 95
                # build targets found 63 distinct sites relying on it -
                # `engine.camera`'s step clamp (`if target - cur < step`),
                # `vm.combat`'s axis overlap (`return b - a < aw`), and the
                # `0 - 4` negative-literal idiom all over vm.player. Emitting
                # the narrowing casts that would make the old model true (the
                # other half of L-1, and L-7's first bullet) would break every
                # one of them.
                # Anything narrower than int is promoted TO int, so a pair
                # of u8 is int too - there is no `n1 == n2` shortcut here, and
                # writing one was this fix's own first bug.
                if 'u16' in (n1, n2):
                    return PrimitiveType('u16')   # unsigned int wins in C
                return PrimitiveType('i16')       # int, incl. the u8/i8 pair
            if n1 == n2:
                return type1
            # STORAGE (a `var`'s inferred type): the wider of the two declared
            # widths, exactly as before, so no existing program's C changes.
            if n1 in type_order and n2 in type_order:
                return type1 if type_order.index(n1) > type_order.index(n2) else type2
        return type1

    def type_to_string(self, type_obj: Type) -> str:
        """Convert a Type object to a readable string"""
        if isinstance(type_obj, PrimitiveType):
            return type_obj.name
        elif isinstance(type_obj, UserDefinedType):
            return type_obj.name
        elif isinstance(type_obj, ArrayType):
            return f"array[{self.type_to_string(type_obj.element_type)}, {type_obj.size}]"
        elif isinstance(type_obj, FunctionType):
            params = ", ".join(self.type_to_string(p) for p in type_obj.param_types)
            sig = f"function({params})"
            if type_obj.return_type is not None:
                sig += f" -> {self.type_to_string(type_obj.return_type)}"
            return sig
        else:
            return str(type_obj)
