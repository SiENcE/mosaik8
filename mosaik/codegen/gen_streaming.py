"""CodeGenerator asset streaming + Lynx caches + bank units, mixed into generator.CodeGenerator.

Split out of the former monolithic generator.py; a plain mixin class composed
into CodeGenerator - methods reference sibling methods/attributes via self."""
import dataclasses
import re

from ..ast_nodes import *  # noqa: F401,F403
from ..platforms import (PLATFORM_CAPS, canonical_platform,
                         framework_for_platform, platform_caps)


class StreamingMixin:
    # -- Asset streaming (Lynx cart) -----------------------------------------
    # The Lynx cart ROM is not in the CPU address space, so data must be copied
    # into the 64 KB RAM before use. When a program calls assets.use/ptr AND the
    # target is the Lynx, the referenced const arrays (the per-scene maps) are
    # moved OUT of the resident image into a cart archive (appended past the
    # linked image by the build) and loaded on demand into a 2-slot LRU cache
    # (current + previous room). On every other console assets.* stays the
    # byte-identical Stage A seam (use = nothing, ptr = the const symbol).

    # The assets-seam verbs whose FIRST argument names a streamable const array:
    # the whole-asset seam (use/ptr) + the range-windowed seam (paint_table +
    # stream: use_range/ptr_range/range_byte read a WINDOW of a concatenated
    # array, range_base registers it + its window size). All route the referenced
    # const out of the resident image (Lynx archive / GB bank).
    _STREAM_VERBS = ('use', 'ptr', 'use_range', 'ptr_range', 'range_byte',
                     'range_base')

    def _streamed_calls(self, node, out):
        """Collect every assets seam FunctionCall under `node` (with an arg)."""
        if isinstance(node, FunctionCall):
            fn = node.function
            if (isinstance(fn, FieldAccess) and isinstance(fn.object, Identifier)
                    and fn.object.name == 'assets'
                    and fn.field in self._STREAM_VERBS and node.arguments):
                out.append(node)
        if isinstance(node, ASTNode):
            for f in dataclasses.fields(node):
                self._streamed_calls(getattr(node, f.name), out)
        elif isinstance(node, (list, tuple)):
            for x in node:
                self._streamed_calls(x, out)

    @staticmethod
    def _const_byte_values(decl):
        """The u8 bytes of a const ARRAY initializer, or None if not all literal
        numbers (then it can't be archived -- leave it resident)."""
        vals = []
        for e in decl.initializer.elements:
            if isinstance(e, Literal) and e.type == "number":
                vals.append(int(e.value) & 0xFF)
            else:
                return None
        return vals

    # Threshold (bytes) above which a GB-family build BANKS its streamed const
    # arrays instead of leaving them resident. The cart can address ROM directly,
    # so banking only pays off once the streamed data is too big to keep resident
    # alongside the code (the home + fixed banks). Below it the seam stays the
    # byte-identical Stage A no-op -- a small world on the GB pays no MBC cost
    # (the zero-cost-on-directly-mapped-consoles principle). One ROM bank (16 KB)
    # is the natural line: more streamed data than a whole bank cannot stay
    # comfortably resident, so bank it. (bigworld's ~50 KB banks; the tiny
    # seam/opt-in test worlds stay resident.)
    # One 16 KB bank. Do NOT lower this for the GB family without CODE banking
    # (tried 2026-08-05, reverted): banking data is not free there, it is a
    # TRADE. A GB ROM with no banked data is a FLAT 32 KB image that code and
    # consts share; the moment any data banks, the resident image must fit
    # 0x0000-0x3FFF (16 KB) because 0x4000+ becomes the switchable window. A
    # VM8 game's code alone is ~20 KB, so banking its scene data turns a
    # "cart is 6 KB too big" error into a "resident image is 12 KB too big"
    # one. Banking only pays when the DATA is larger than the bank it frees,
    # which is what this threshold expresses.
    GB_BANK_DATA_THRESHOLD = 0x4000
    GB_BANK_BYTES = 0x4000            # 16 KB per MBC5 ROM bank

    def _collect_streamed(self, program):
        """Decide which const arrays leave the resident image, and how. Two
        residency strategies, picked by the target's hardware:

          * Lynx -- the cart is NOT CPU-addressable, so const data must be copied
            into RAM before use: archive the arrays + stream them via a cart read
            into an LRU cache (Stage B). Populates streamed/stream_offsets/
            stream_lengths/streamed_archive, _stream_mode = 'lynx'.
          * GB family (has_banking) -- the cart IS CPU-addressable through the MBC,
            so the const stays in ROM, placed in a switchable bank, and the seam
            bank-switches to read it (no copy, no cache). Only banks when the
            streamed data exceeds GB_BANK_DATA_THRESHOLD; below it the seam stays
            the byte-identical Stage A no-op. Populates streamed (sym -> bank) +
            data_bank_syms/data_bank_decls, _stream_mode = 'gb'.

        A no-op (byte-identical) on every other console, or when the program never
        calls assets.use/ptr on a const array."""
        is_lynx = self.platform == 'lynx'
        # The PC Engine banks exactly like the GB family once the program opts
        # into code banking (cc65_bank); without it, its data stays resident.
        is_gb = self._rom_banking_capable() and not is_lynx
        if not (is_lynx or is_gb):
            # Every directly-mapped non-banking console keeps the data resident;
            # the seam stays a no-op/passthrough (Stage A), nothing streams.
            return
        # Candidate streamable arrays, keyed by mangled C name.
        const_decls = {}
        for module in program.modules:
            for decl in module.declarations:
                if (isinstance(decl, VarDecl) and decl.is_const
                        and isinstance(decl.initializer, ArrayLiteral)):
                    const_decls[self._mangled(module.name, decl.name)] = decl
        # Symbols handed to assets.use/ptr, resolved through the calling module.
        syms = []
        for module in program.modules:
            self._enter_module(module)
            calls = []
            for decl in module.declarations:
                if isinstance(decl, FunctionDecl):
                    self._streamed_calls(decl, calls)
            for call in calls:
                sym = self.gen_expression(call.arguments[0])
                if sym in const_decls and sym not in syms:
                    syms.append(sym)
                # Whole-asset loads (assets.use/ptr) size the whole cache's RAM
                # slots; a range base is archived whole but never whole-loaded.
                if call.function.field in ('use', 'ptr') and sym in const_decls:
                    self._whole_syms.add(sym)
                # A range-windowed base (paint_table + stream): record it + the
                # widest window (the range cache's slot size). The window literal
                # is emitted by the transpiler as max(scene_w*scene_h).
                if call.function.field == 'range_base' and sym in const_decls:
                    self._range_bases.add(sym)
                    if len(call.arguments) >= 2:
                        try:
                            self._range_maxwin = max(
                                self._range_maxwin,
                                int(self.gen_expression(call.arguments[1])))
                        except ValueError:
                            raise RuntimeError(
                                "assets.range_base(sym, maxwin) needs a literal "
                                "window size")
        syms.sort()                       # deterministic ids / bank order
        if is_lynx:
            # The Lynx packs BOTH the streamed asset arrays AND (if big enough) the
            # VM8 bytecode blob into ONE cart archive; code streaming works even
            # with no streamed assets (a script-only VM game). See _pack_lynx_*.
            blob = bytearray()
            if syms:
                self._pack_lynx_assets(syms, const_decls, blob)
            self._pack_lynx_code(program, const_decls, blob)
            # The world streams (`[world] stream` routed a map / tileset through
            # the seam): its sprite SHEETS ride the same archive, LAST, so no
            # existing asset or bytecode offset moves.
            if self.streamed:
                self._pack_lynx_sheets(program, blob)
            if self.streamed or self.streamed_code_sym is not None:
                self._streaming = True
                self._stream_mode = 'lynx'
                self.streamed_archive = bytes(blob)
                self._collect_bkg_loadthrough(program)
        else:
            # With code banking active the cart is banked anyway, so the
            # assets.code_byte data blobs (song CELLS, the STRINGS text) leave
            # the resident image too -- every byte freed is engine headroom in
            # bank 0. The FIRST-SEEN blob is the interpreter's bytecode fetch
            # (the VM8 emits fetch(CODE) before render_text(STRINGS), the same
            # order contract the Lynx slots rely on) and it stays RESIDENT by
            # default: it costs a bank switch per FETCHED BYTE, which is the
            # hot path. `[build] bank_bytecode` opts into that trade -- see the
            # knob in compiler.compile_program. Measured on the two ends of the
            # range (PyBoy CPU hooks, bank0-optimization-plan O3): an
            # event-script game runs ~0 bytecode instructions in a steady-state
            # frame and peaks at ~0.5% of an LCD frame, while a game written
            # WHOLLY in bytecode pays ~28% at its peak.
            if self._code_bank_num:
                first = 0 if getattr(self, '_bank_bytecode', False) else 1
                for sym in self._code_byte_syms(program, const_decls)[first:]:
                    if sym not in syms:
                        syms.append(sym)
            if syms:
                self._collect_streamed_gb(syms, const_decls)

    @staticmethod
    def _is_assets_ptr(node):
        """True for an `assets.ptr(SYM)` call node."""
        return (isinstance(node, FunctionCall)
                and isinstance(node.function, FieldAccess)
                and isinstance(node.function.object, Identifier)
                and node.function.object.name == 'assets'
                and node.function.field == 'ptr'
                and len(node.arguments) == 1)

    def _walk_ast(self, node, visit):
        """Depth-first walk calling `visit(node)` on every AST node."""
        visit(node)
        if isinstance(node, ASTNode):
            for f in dataclasses.fields(node):
                self._walk_ast(getattr(node, f.name), visit)
        elif isinstance(node, (list, tuple)):
            for x in node:
                self._walk_ast(x, visit)

    def _collect_bkg_loadthrough(self, program):
        """Pick the streamed arrays that can LOAD THROUGH straight into the
        background tile table instead of occupying a whole-asset cache slot.

        A per-scene TILESET is only ever `assets.use(X)` +
        `bkg.set_data(0, N, assets.ptr(X))`: the setter copies it out
        immediately and nothing ever reads it again -- yet holding it in the
        LRU cache sizes EVERY slot to the biggest tileset, because the slots
        are one shared size. Measured on the reference-engine import: 2 x 2,992 B of
        the scarce Lynx MAIN spent on data that is dead the moment set_data
        returns, while the assets the cache actually exists for (the room's map
        and collision layer, read every frame) are under 600 B. Reading the
        tileset straight into gbs_bkg_tileset takes it out of the slot sizing
        entirely -- which is what fits a big-tileset room on the Lynx.

        A symbol qualifies only when EVERY `assets.ptr` on it is that copy-out
        argument and it is never indexed anywhere (`map_tile`'s `MAP[idx]`
        rewrite reads through the cache, so an indexed array must stay in it).
        Nothing qualifying = byte-identical, and no other console is reachable
        here (Lynx streaming only)."""
        eligible = set()
        excluded = set(self._range_bases)
        for module in program.modules:
            self._enter_module(module)
            copy_out = set()          # id() of the assets.ptr nodes in set_data
            ptr_uses = []             # (id(node), symbol) for every assets.ptr
            indexed = set()

            def visit(n):
                if isinstance(n, FunctionCall):
                    fn = n.function
                    if (isinstance(fn, FieldAccess)
                            and isinstance(fn.object, Identifier)):
                        if (fn.object.name == 'bkg' and fn.field == 'set_data'
                                and len(n.arguments) == 3
                                and self._is_assets_ptr(n.arguments[2])):
                            copy_out.add(id(n.arguments[2]))
                    if self._is_assets_ptr(n):
                        ptr_uses.append(
                            (id(n), self.gen_expression(n.arguments[0])))
                elif isinstance(n, ArrayAccess):
                    # Only an identifier / alias.member can name a const array;
                    # any other indexed expression cannot be a streamed symbol.
                    if isinstance(n.array, (Identifier, FieldAccess)):
                        indexed.add(self.gen_expression(n.array))

            for decl in module.declarations:
                self._walk_ast(decl, visit)
            for nid, sym in ptr_uses:
                (eligible if nid in copy_out else excluded).add(sym)
            excluded |= indexed
        cand = {s for s in (eligible - excluded)
                if s in self.streamed and s in self._whole_syms}
        # Only switch when it actually BUYS something. The slots are one shared
        # size, so the gain is whatever taking these assets out of that sizing
        # frees -- huge for a real world (the reference-engine import: 2 x 2,992 B ->
        # 2 x 576 B) and nothing for a program whose only streamed asset is one
        # small tileset, where the ~200 B of reader would cost more MAIN than
        # the slot it removes. Below the threshold the program keeps the plain
        # cache and stays byte-identical, which is why the existing seam tests
        # and the small streamed samples do not churn.
        if cand:
            gain = self._whole_cache_size(set()) - self._whole_cache_size(cand)
            if gain < self.LYNX_BKG_LOADTHROUGH_MIN_GAIN:
                cand = set()
        self._bkg_loadthrough = cand

    # Bytes of MAIN the load-through switch must save before it is worth its
    # own code (~200 B of reader). See _collect_bkg_loadthrough.
    LYNX_BKG_LOADTHROUGH_MIN_GAIN = 512

    def _whole_cache_size(self, loadthrough):
        """RAM the whole-asset cache would cost (slots x slot size) if
        `loadthrough` were the load-through set."""
        saved = self._bkg_loadthrough
        self._bkg_loadthrough = loadthrough
        try:
            ids = self._whole_stream_ids()
        finally:
            self._bkg_loadthrough = saved
        if not ids:
            return 0
        return min(2, len(ids)) * max(self.stream_lengths[i] for i in ids)

    def _code_byte_syms(self, program, const_decls):
        """The DISTINCT const blobs read via assets.code_byte, in first-seen
        order (same collection as _pack_lynx_code)."""
        syms = []
        for module in program.modules:
            self._enter_module(module)
            calls = []
            for decl in module.declarations:
                if isinstance(decl, FunctionDecl):
                    self._code_byte_calls(decl, calls)
            for call in calls:
                s = self.gen_expression(call.arguments[0])
                if s in const_decls and s not in syms:
                    syms.append(s)
        return syms

    # Bytecode-blob page streaming (VM8, §7.1 #3), Lynx only. The page cache has
    # a FIXED cost (~577 B CODE for the lseek/read machinery + a 256 B page buffer +
    # a few BSS bytes = ~840 B), so streaming a blob SMALLER than that is a net LOSS
    # of MAIN. The threshold is set above break-even so streaming only ever WINS:
    # blobs <= it stay resident (byte-identical); a big blob (KBs of a real game's
    # scripts) frees its whole RODATA for a decisive net saving.
    #
    # WHAT STREAMING COSTS IN SPEED (measured 2026-08-08 on the falling-block assembly sample
    # with the GearLynx debugger, and it is not small): the page cache holds ONE
    # 256 B page, and refilling it is ~98,000 ticks -- 0.42 of an LCD frame for a
    # single miss, since the cart read runs at ~96 CPU cycles a byte. The VM8
    # scheduler round-robins several threads and each one's pc sits in a
    # different page, so EVERY context switch is a miss, and a bytecode CALL to a
    # subroutine a page away is two more (the call and the return). Measured on
    # the falling-block assembly sample: ~1 miss per thread slice, ~60% of the interpreter's whole
    # non-present frame budget. That is the trade `[build] lynx_code_resident`
    # exists to let a project refuse -- see _lynx_code_resident below.
    LYNX_CODE_PAGE = 256                  # bytes per current-page cache read
    LYNX_CODE_STREAM_THRESHOLD = 1024     # blobs <= this stay resident (byte-identical)

    def _pack_lynx_assets(self, syms, const_decls, blob):
        """Lynx: pack the streamed const arrays into the cart archive `blob`
        (loaded into the RAM LRU cache on demand). See _emit_lynx_asset_cache."""
        for sym in syms:
            data = self._const_byte_values(const_decls[sym])
            if data is None:
                continue                  # unevaluable -> keep it resident
            # Block-align each asset to 1024 so none straddles a cart block.
            while len(blob) % 1024 != 0:
                blob.append(0)
            self.streamed[sym] = len(self.stream_offsets)
            self.stream_offsets.append(len(blob))
            self.stream_lengths.append(len(data))
            blob.extend(data)

    def _pack_lynx_sheets(self, program, blob):
        """Lynx, `[world] stream`: the `[assets] sprites` sheets that are only
        ever UPLOADED (`sprite.set_data(first, count, <stem>_tiles)`, which on
        the Lynx converts each tile into the Suzy table and never reads the
        source again) go into the cart archive instead of MAIN. Each starts on
        a 1 KB cart block so a load seeks without skipping. The upload lowers
        to gbs_spr_data_stream (gen_expr), the array is not emitted
        (_emit_assets). Measured on the showcase RPG: 15,872 B of RODATA."""
        sheets = self._upload_only_sheets(program)
        if not sheets:
            return
        for name, data, _bpp in self.assets or []:
            sym = "%s_tiles" % name
            if sym not in sheets:
                continue
            while len(blob) % 1024 != 0:
                blob.append(0)
            self.sheet_stream[sym] = len(blob) // 1024
            blob.extend(data)

    def _emit_lynx_sheet_stream(self):
        """gbs_spr_data_stream: sprite.set_data of a cart-streamed sheet. One
        seek to the sheet's block, then a small read per tile into a scratch
        buffer and the engine's own converter -- the body of
        gbs_set_sprite_data with the source read from the cart."""
        stride = 32 if self.sprite_src_bpp == 4 else 16
        self.emit("/* --- Sprite sheets streamed from the cart (Lynx, [world] stream):")
        self.emit("   sprite.set_data reads the sheet tile by tile from the archive")
        self.emit("   into the Suzy table, so the sheet never occupies MAIN. --- */")
        self.emit("#include <unistd.h>")
        self.emit("#include <stdio.h>")
        self.emit("#ifndef GBS_ARCHIVE_BASE")
        self.emit("#define GBS_ARCHIVE_BASE 0")
        self.emit("#endif")
        self.emit("static uint8_t gbs_sheet_buf[%d];" % stride)
        self.emit("void gbs_spr_data_stream(uint8_t first, uint8_t count, uint16_t blk) {")
        self.emit("    uint8_t i;")
        self.emit("    gbs_spr_init();")
        self.emit("    lseek(1, (long)((unsigned long)GBS_ARCHIVE_BASE + ((unsigned long)blk << 10)), SEEK_SET);")
        self.emit("    for (i = 0; i < count; ++i) {")
        self.emit("        read(1, gbs_sheet_buf, %d);" % stride)
        self.emit("        if ((uint8_t)(first + i) < GBS_MAX_TILES)")
        self.emit("            gbs_conv_tile((uint8_t)(first + i), gbs_sheet_buf);")
        self.emit("    }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("    gbs_force = 1;   /* new sprite tile data -> present must re-blit */")
        self.emit("}")
        self.emit("")

    def _code_byte_calls(self, node, out):
        """Collect every assets.code_byte(blob, off) FunctionCall under `node`."""
        if isinstance(node, FunctionCall):
            fn = node.function
            if (isinstance(fn, FieldAccess) and isinstance(fn.object, Identifier)
                    and fn.object.name == 'assets'
                    and fn.field == 'code_byte' and len(node.arguments) == 2):
                out.append(node)
        if isinstance(node, ASTNode):
            for f in dataclasses.fields(node):
                self._code_byte_calls(getattr(node, f.name), out)
        elif isinstance(node, (list, tuple)):
            for x in node:
                self._code_byte_calls(x, out)

    def _pack_lynx_code(self, program, const_decls, blob):
        """Lynx: if a large const blob is read through assets.code_byte(blob, off)
        (the interpreter's fetch seam), archive it into `blob` for page-cache
        streaming so it leaves the resident RODATA. A small blob stays resident
        (code_byte lowers to the byte-identical `sym[off]`)."""
        # Collect the DISTINCT const blobs read via assets.code_byte, in first-seen
        # order (the VM8 emits fetch(CODE) before render_text(STRINGS), so CODE is
        # first). A single-blob game (vm-bigcode) archives exactly as before ->
        # byte-identical.
        syms = []
        for module in program.modules:
            self._enter_module(module)         # resolve the blob symbol in-context
            calls = []
            for decl in module.declarations:
                if isinstance(decl, FunctionDecl):
                    self._code_byte_calls(decl, calls)
            for call in calls:
                s = self.gen_expression(call.arguments[0])
                if s in const_decls and s not in syms:
                    syms.append(s)
        # Archive each blob that exceeds the stream threshold; the first becomes the
        # PRIMARY reader (the hot CODE fetch), the second the STRINGS reader. A small
        # blob stays resident (code_byte lowers to the byte-identical sym[off]).
        slot = 0
        for i, sym in enumerate(syms):
            data = self._const_byte_values(const_decls[sym])
            if data is None or len(data) <= self.LYNX_CODE_STREAM_THRESHOLD:
                continue
            # `[build] lynx_code_resident` PINS the first blob -- the VM8 bytecode,
            # the one the interpreter fetches a byte at a time -- in RAM, trading
            # its size in MAIN for the page-miss cost documented above. Only the
            # FIRST: the STRINGS text and a song's cells are read once per box /
            # per row, so streaming those is nearly free and their RODATA is worth
            # far more. Off by default and byte-identical, because whether a
            # project can afford the MAIN is a project's question, not codegen's.
            if i == 0 and getattr(self, '_lynx_code_resident', False):
                continue
            # There are three page readers, so a FOURTH streamable blob stays
            # resident - stop BEFORE archiving it. (This test used to sit after
            # the append, so the 4th blob's bytes went into the cart archive AND
            # stayed resident: dead cart space nothing ever read. A song library
            # chunked past one ROM bank is the first program that can have four.)
            if slot > 2:
                break
            # Block-align the region to 1024 (a cart block + whole pages), archive it,
            # then pad to a full page so the last page read never runs past the end.
            while len(blob) % 1024 != 0:
                blob.append(0)
            base = len(blob)
            blob.extend(data)
            while len(blob) % self.LYNX_CODE_PAGE != 0:
                blob.append(0)
            if slot == 0:
                self.streamed_code_sym = sym
                self.streamed_code_base = base
                self.streamed_code_len = len(data)
            elif slot == 1:
                self.streamed_code2_sym = sym
                self.streamed_code2_base = base
                self.streamed_code2_len = len(data)
            else:         # slot == 2 (the loop breaks above past three)
                self.streamed_code3_sym = sym
                self.streamed_code3_base = base
                self.streamed_code3_len = len(data)
            slot += 1

    def _collect_streamed_gb(self, syms, const_decls):
        """GB family: place the streamed arrays into MBC5 ROM banks (read in
        place after a SWITCH_ROM). Only banks when the streamed data is big enough
        to need it -- below GB_BANK_DATA_THRESHOLD the data stays resident and the
        seam is the byte-identical Stage A no-op. See _emit_data_bank_units and the
        GB lowerings in _gen_call/gen_expression."""
        evaluable = {}
        total = 0
        for sym in syms:
            data = self._const_byte_values(const_decls[sym])
            if data is None:
                continue                  # unevaluable -> keep it resident
            evaluable[sym] = len(data)
            total += len(data)
        # Small worlds fit resident: leave the seam a byte-identical no-op, so a
        # GB game pays no MBC/bank-switch cost it does not need. With CODE
        # banking opted in the trade is decided -- the cart has banks either
        # way, so every streamed byte banks (resident bank-0 headroom is the
        # scarce resource the opt-in exists to buy).
        if total <= self.GB_BANK_DATA_THRESHOLD and not self._code_bank_num:
            return
        # Pack whole arrays into 16 KB banks, greedily, starting above any bank(N)
        # function placement so data and code banks never collide.
        start = (max(self._func_banks) + 1) if self._func_banks else 1
        bank, used = start, 0
        for sym in syms:
            if sym not in evaluable:
                continue
            size = evaluable[sym]
            # An array can never span banks (it is read in place while its one
            # bank is mapped), so one bigger than the window is a clear error
            # here rather than an opaque sdcc/makebin area overflow later.
            if size > self.GB_BANK_BYTES:
                raise RuntimeError(
                    "streamed const array '%s' is %d bytes, more than one %d-"
                    "byte ROM bank; a banked array must fit a single bank -- "
                    "split the data (e.g. smaller per-scene maps)"
                    % (sym, size, self.GB_BANK_BYTES))
            if used and used + size > self.GB_BANK_BYTES:
                bank += 1
                used = 0
            self.streamed[sym] = bank
            self.data_bank_syms.setdefault(bank, []).append(sym)
            self.data_bank_decls[sym] = const_decls[sym]
            used += size
        if not self.streamed:
            return
        self._streaming = True
        self._stream_mode = 'gb'

    def _prelude_data_bank(self, sym, size, definition):
        """Place one PRELUDE const table in a switchable ROM bank, or decline.

        The prelude is emitted into the resident TU, so a table it owns is
        resident no matter how much of the program banks -- `gbs_glyph_font` is
        768 B of exactly that (bank0-optimization-plan O5). This is the same
        mechanism `_collect_streamed_gb` uses for a mosaik const, except the
        bytes arrive as finished C text rather than as a VarDecl, so they are
        registered for `_emit_data_bank_units` instead.

        Returns the bank number, or 0 when the table must stay resident: no
        banked-ROM support on this console, or no banking active in this build
        (a program with neither `bank(N)`, `[build] code_banks` nor streamed
        data keeps a flat cart, and adding a bank for one table would be the
        whole MBC cost for 768 B). 0 means the caller emits it as before, so
        every non-banking build stays byte-identical.

        Packs into the LAST existing data bank when it fits, so the common case
        costs no extra bank and cannot grow the cart.
        """
        if not self._rom_banking_capable() or not self.banking_active:
            return 0
        if size > self.GB_BANK_BYTES:
            return 0
        placed = [(b, n) for (b, _t, n) in self.prelude_bank_defs.values()]
        bank = 0
        if self.data_bank_syms:
            last = max(self.data_bank_syms)
            used = sum(len(self._const_byte_values(self.data_bank_decls[s]) or b"")
                       for s in self.data_bank_syms[last])
            used += sum(n for b, n in placed if b == last)
            if used + size <= self.GB_BANK_BYTES:
                bank = last
        if not bank:
            # A fresh bank above everything already placed (function banks,
            # code banks, data banks, and any prelude table before this one).
            taken = set(self._func_banks) | set(self.data_bank_syms)
            taken |= {b for b, _n in placed}
            bank = (max(taken) + 1) if taken else 1
        self.prelude_bank_defs[sym] = (bank, definition, size)
        return bank

    def _emit_streamed_const_extern(self, const, c_name):
        """Emit the main-TU view of a streamed const array. On the Lynx its bytes
        are gone (archived), so emit nothing -- accesses route through the RAM
        cache. On the GB family the array lives in a ROM bank, so declare it
        `extern const` here (its definition is in the bank TU) so home code can
        read it after a SWITCH_ROM."""
        if self._stream_mode == 'gb':
            var_type = const.type or self._infer_decl_type(const.initializer)
            self.emit("extern const " + self._format_decl(var_type, c_name) + ";")

    def _emit_stream_runtime(self):
        """The Lynx streaming runtime read from the cart archive via the cc65 fd-1
        cart read (lseek/read). Two independent consumers, each emitted only when
        used: the per-scene asset LRU cache (maps/collision) AND the VM8 bytecode
        current-page cache (§7.1 #3). The build appends the archive past the linked
        image and bakes its cart byte offset into GBS_ARCHIVE_BASE at link time (a
        -D on the second cl65 pass)."""
        if self._stream_mode != 'lynx':
            return  # GB-family banking emits no RAM cache (see _emit_data_bank_units)
        # The asset cache keeps its exact original emission (byte-identical for an
        # asset-only streaming game); the bytecode page cache is additive. Both read
        # the cart via fd 1 -- the #include<unistd.h/stdio.h> repeat and the
        # ifndef-guarded GBS_ARCHIVE_BASE define are harmless when both are present.
        if self.stream_offsets:
            # paint_table + stream windows a concatenated array (item 33); a plain
            # streamed world (item 24) loads whole per-scene assets. The two use
            # different caches (a window slot is sized to the widest scene, not the
            # whole array), and each is emitted ONLY when its seam is used.
            #
            # A program may use BOTH at once (a per-scene tileset loads whole while
            # the concatenated maps window), so this is not either/or: emitting only
            # one left the other's helper undeclared at compile. When both are
            # present the range cache REUSES the whole cache's offsets table (same
            # id space, identical contents) instead of emitting a duplicate; a
            # single-seam program is byte-identical either way.
            whole_ids = self._whole_stream_ids()
            if whole_ids:
                self._emit_lynx_asset_cache(whole_ids)
            if self._range_bases:
                self._emit_lynx_range_cache(
                    off_table="gbs_asset_off" if whole_ids else None)
            if self._bkg_loadthrough:
                self._emit_lynx_bkg_loadthrough(
                    off_table="gbs_asset_off" if whole_ids else None)
        if self.streamed_code_sym is not None:
            self._emit_lynx_code_cache()
        if self.sheet_stream:
            self._emit_lynx_sheet_stream()

    def _emit_lynx_code_cache(self):
        """The VM8 bytecode current-page cache (§7.1 #3): the CODE blob is read a
        page at a time from the cart archive into ONE small buffer instead of living
        resident in RODATA. fetch(off) lowers to gbs_code_byte(off). PC is mostly
        sequential so the page hits almost always; a THREAD/CALL/far JUMP that leaves
        the page just reloads it. Frees the whole blob (+ its RODATA) from the tight
        Lynx MAIN -- the scale play for a real game's KBs of scripts."""
        page = self.LYNX_CODE_PAGE
        self.emit("/* --- VM8 bytecode streaming (Lynx cart, %d B page cache):"
                  % page)
        self.emit("   the CODE blob streams from the cart archive a page at a time")
        self.emit("   (fetch(off) = gbs_code_byte(off)), freeing resident RODATA. */")
        self.emit("#include <unistd.h>")
        self.emit("#include <stdio.h>")
        self.emit("#ifndef GBS_ARCHIVE_BASE")
        self.emit("#define GBS_ARCHIVE_BASE 0")
        self.emit("#endif")
        assert page and not (page & (page - 1)),             "LYNX_CODE_PAGE must be a power of two (the reader page-aligns with a mask)"
        self.emit("#define GBS_CODE_PAGE %d" % page)
        self._emit_lynx_page_reader("code", self.streamed_code_base,
                                    "GBS_CODE_BASE")
        # The SECOND streamed blob (the VM8 STRINGS text blob, workstream B): its
        # own page buffer so infrequent, bursty text reads (render_text draws a page)
        # never evict the hot instruction page. Emitted only when text is big enough
        # to stream; code_byte(STRINGS, off) lowers to gbs_str_byte(off).
        if self.streamed_code2_sym is not None:
            self.emit("/* --- VM8 STRINGS text streaming (Lynx cart, its own page): */")
            self._emit_lynx_page_reader("str", self.streamed_code2_base,
                                        "GBS_STR_BASE")
        # The THIRD streamed blob (the songs.mos CELLS data, audio plan Tier D):
        # its own page buffer so the music driver's per-row cell reads never evict
        # the hot instruction page or a text page mid-dialogue. Emitted only when a
        # third big blob streams; code_byte(CELLS, off) lowers to gbs_dat_byte(off).
        if self.streamed_code3_sym is not None:
            self.emit("/* --- streamed DATA blob (song cells; Lynx cart, its own page): */")
            self._emit_lynx_page_reader("dat", self.streamed_code3_base,
                                        "GBS_DAT_BASE")

    def _emit_lynx_page_reader(self, tag, base_off, base_macro):
        """One cart page-cache reader: `gbs_<tag>_byte(off)`.

        THE HOT PATH IS THE WHOLE POINT. The VM8 interpreter calls this for every
        byte of every instruction it decodes, so on the Lynx it is the single most
        executed routine in the program. It used to compute the page as
        `(long)off / GBS_CODE_PAGE` and index with `off %% GBS_CODE_PAGE` -- a
        32-bit DIVISION plus a modulo, in cc65 software, per byte. Measured on
        the falling-block assembly sample (2 KB of streamed bytecode, ~16 dispatches a frame):
        that arithmetic alone cost ~5 of the 7 LCD frames each VM frame took, i.e.
        the Lynx ran the game ~3x slower than it had to, and it read as "the Lynx
        background engine is too slow" until it was measured.

        Now a hit is one 16-bit subtract and one unsigned compare (the subtract
        wraps on a miss, so a single compare covers both ends of the window), and
        the page base is a MASK rather than a multiply. The window is tracked as a
        byte offset instead of a page index, so the miss path drops its 32-bit
        multiply too."""
        self.emit("#define %s %dUL" % (base_macro, base_off))
        self.emit("static unsigned char gbs_%s_buf[GBS_CODE_PAGE];" % tag)
        self.emit("static unsigned int gbs_%s_lo = 0;   /* first offset in the buffer */"
                  % tag)
        self.emit("static unsigned char gbs_%s_have = 0;  /* 0 until the first read */"
                  % tag)
        self.emit("unsigned char gbs_%s_byte(unsigned int off) {" % tag)
        self.emit("    unsigned int rel = off - gbs_%s_lo;" % tag)
        self.emit("    if (!gbs_%s_have || rel >= GBS_CODE_PAGE) {" % tag)
        self.emit("        gbs_%s_lo = off & (unsigned int)~(GBS_CODE_PAGE - 1);" % tag)
        self.emit("        lseek(1, (long)((unsigned long)GBS_ARCHIVE_BASE + %s "
                  "+ (unsigned long)gbs_%s_lo), SEEK_SET);" % (base_macro, tag))
        self.emit("        read(1, gbs_%s_buf, GBS_CODE_PAGE);" % tag)
        self.emit("        gbs_%s_have = 1;" % tag)
        self.emit("        rel = off - gbs_%s_lo;" % tag)
        self.emit("    }")
        self.emit("    return gbs_%s_buf[rel];" % tag)
        self.emit("}")
        self.emit("")

    def _whole_stream_ids(self):
        """Stream ids loaded WHOLE (assets.use/ptr), in id order.

        A program with no range base whole-loads everything it streams (the
        long-standing case), so this is every id and the emission below is
        byte-identical. With both seams in play it excludes the range bases,
        whose concatenated arrays are archived whole but only ever WINDOWED --
        sizing a RAM slot to one would cost the whole array. A LOAD-THROUGH
        tileset is excluded for the same reason: it is read from the cart
        straight into the tile table and never lives in a slot at all (see
        _collect_bkg_loadthrough)."""
        if not self._range_bases and not self._bkg_loadthrough:
            return sorted(self.streamed.values())
        return sorted(sid for sym, sid in self.streamed.items()
                      if sym in self._whole_syms
                      and sym not in self._bkg_loadthrough)

    def _emit_lynx_asset_cache(self, whole_ids=None):
        """The per-scene map/collision LRU cache (Lynx cart).

        `whole_ids` are the ids actually whole-loaded; they size the RAM slots
        (the offset/length TABLES stay indexed by the global stream id, so a
        range base's entry is simply never looked up here)."""
        if whole_ids is None:
            whole_ids = sorted(self.streamed.values())
        offs = ", ".join(str(o) for o in self.stream_offsets)
        lens = ", ".join(str(n) for n in self.stream_lengths)
        # The LRU cache holds GBS_STREAM_SLOTS streamed assets. A room touches
        # exactly two per frame -- its map AND its collision layer (collision is
        # sampled every frame by solid(), the map on paint) -- so 2 slots keeps
        # the CURRENT room thrash-free, which is the minimum correct size and the
        # leanest on the Lynx's single ~46.6 KB MAIN area (the whole point of
        # streaming is to fit there). A door crossing re-reads the destination
        # (already required for a never-seen room) and a back-track re-reads too;
        # both are hidden behind a load/fade, and each slot is GBS_STREAM_MAXLEN
        # bytes of scarce RAM, so we do NOT also resident-cache the previous room.
        # A program that streams a SINGLE asset (a one-level wide world reading
        # its map through the cache) can't thrash, so it gets one slot -- the
        # second would be permanently empty RAM (GBS_STREAM_MAXLEN of it).
        slots = min(2, len(whole_ids))
        maxlen = max(self.stream_lengths[i] for i in whole_ids)
        self.emit("/* --- Asset streaming (Lynx cart): per-scene map + collision")
        self.emit("   loaded from the cart archive into a %d-slot LRU cache (the"
                  % slots)
        self.emit("   CURRENT room's map + collision) via the cc65 fd-1 cart read")
        self.emit("   -- frees resident RAM. The build appends the archive past the")
        self.emit("   linked image and sets GBS_ARCHIVE_BASE (its cart byte offset)")
        self.emit("   at link time. --- */")
        self.emit("#include <unistd.h>")
        self.emit("#include <stdio.h>")
        self.emit("#ifndef GBS_ARCHIVE_BASE")
        self.emit("#define GBS_ARCHIVE_BASE 0")
        self.emit("#endif")
        self.emit("#define GBS_STREAM_COUNT %d" % len(self.stream_offsets))
        self.emit("#define GBS_STREAM_MAXLEN %d" % maxlen)
        self.emit("#define GBS_STREAM_SLOTS %d" % slots)
        self.emit("static const unsigned long gbs_asset_off[GBS_STREAM_COUNT] = { %s };"
                  % offs)
        self.emit("static const unsigned int gbs_asset_len[GBS_STREAM_COUNT] = { %s };"
                  % lens)
        self.emit("static unsigned char gbs_cache[GBS_STREAM_SLOTS][GBS_STREAM_MAXLEN];")
        self.emit("static unsigned char gbs_cache_id[GBS_STREAM_SLOTS] = { %s };"
                  % ", ".join(["0xFF"] * slots))
        self.emit("static unsigned int gbs_cache_use[GBS_STREAM_SLOTS] = { %s };"
                  % ", ".join(["0"] * slots))
        self.emit("static unsigned int gbs_use_clock = 0;")
        self.emit("static unsigned char gbs_asset_slot(unsigned char id) {")
        self.emit("    unsigned char s, victim;")
        self.emit("    unsigned int oldest;")
        self.emit("    for (s = 0; s < GBS_STREAM_SLOTS; ++s)")
        self.emit("        if (gbs_cache_id[s] == id) { "
                  "gbs_cache_use[s] = ++gbs_use_clock; return s; }")
        self.emit("    victim = 0; oldest = gbs_cache_use[0];")
        self.emit("    for (s = 1; s < GBS_STREAM_SLOTS; ++s)")
        self.emit("        if (gbs_cache_use[s] < oldest) "
                  "{ oldest = gbs_cache_use[s]; victim = s; }")
        self.emit("    lseek(1, (long)((unsigned long)GBS_ARCHIVE_BASE + gbs_asset_off[id]), SEEK_SET);")
        self.emit("    read(1, gbs_cache[victim], gbs_asset_len[id]);")
        self.emit("    gbs_cache_id[victim] = id;")
        self.emit("    gbs_cache_use[victim] = ++gbs_use_clock;")
        self.emit("    return victim;")
        self.emit("}")
        self.emit("void gbs_asset_load(unsigned char id) { gbs_asset_slot(id); }")
        self.emit("unsigned char *gbs_asset_ptr(unsigned char id) { "
                  "return gbs_cache[gbs_asset_slot(id)]; }")
        self.emit("")

    def _emit_lynx_bkg_loadthrough(self, off_table=None):
        """Emit gbs_bkg_data_stream: a streamed background TILESET read from the
        cart STRAIGHT into the tile table, never through a cache slot.

        The read lands IN PLACE and unpacks in place, so it needs no scratch
        buffer and no chunk loop: a GB 2bpp tile and its packed-Suzy form are
        BOTH 16 bytes, and gbs_bkg_pack_row takes a row's two source bytes BY
        VALUE before writing that row's two output bytes. (The 4bpp tier is a
        verbatim copy -- its packed-nibble source already IS the Suzy literal
        format -- so it only needs the read.)

        Strip invalidation is the conservative "all of them": a tileset upload
        is a ROOM LOAD, where the map upload that follows stales every strip
        anyway, so tracking which strips use the changed tile indices (what
        gbs_set_bkg_data does, for an animated tile changing a few cells) would
        cost code for no saved work here."""
        bpp4 = bool(getattr(self, 'lynx_bkg16', False))
        tile_bytes = 32 if bpp4 else 16
        self.emit("/* --- Background tileset LOAD-THROUGH (Lynx cart): the")
        self.emit("   per-scene tileset is copied out by set_bkg_data and never")
        self.emit("   read again, so it is streamed straight into the tile table")
        self.emit("   instead of into an LRU slot -- which would size EVERY slot")
        self.emit("   to the biggest tileset. --- */")
        if off_table is None:
            # No whole-asset cache in this program (every whole-loaded asset
            # loads through), so carry the offsets here.
            self.emit("#include <unistd.h>")
            self.emit("#include <stdio.h>")
            self.emit("#ifndef GBS_ARCHIVE_BASE")
            self.emit("#define GBS_ARCHIVE_BASE 0")
            self.emit("#endif")
            off_table = "gbs_bkg_lt_off"
            self.emit("static const unsigned long gbs_bkg_lt_off[%d] = { %s };"
                      % (len(self.stream_offsets),
                         ", ".join(str(o) for o in self.stream_offsets)))
        self.emit("void gbs_bkg_data_stream(uint8_t first, uint8_t count, "
                  "uint8_t id) {")
        decls = ["s"]
        if not bpp4:
            decls = ["row", "s"]
        self.emit("    uint8_t %s;" % ", ".join(decls))
        if not bpp4:
            self.emit("    uint16_t t; uint8_t *o;")
        self.emit("    gbs_bkg_init();")
        self.emit("    if (first >= GBS_BKG_MAX_TILES) return;")
        self.emit("    if ((uint16_t)first + count > GBS_BKG_MAX_TILES)")
        self.emit("        count = (uint8_t)(GBS_BKG_MAX_TILES - first);"
                  "  /* shrunk bkg_max_tiles: never overrun the table */")
        self.emit("    lseek(1, (long)((unsigned long)GBS_ARCHIVE_BASE + "
                  "%s[id]), SEEK_SET);" % off_table)
        self.emit("    read(1, gbs_bkg_tileset[first], "
                  "(unsigned int)count * %d);" % tile_bytes)
        if not bpp4:
            self.emit("    for (t = 0; t < count; ++t) {   "
                      "/* unpack in place: 16 B in, 16 B out */")
            self.emit("        o = gbs_bkg_tileset[(uint8_t)(first + t)];")
            self.emit("        for (row = 0; row < 8; ++row)")
            self.emit("            gbs_bkg_pack_row(o[row*2], o[row*2+1], "
                      "&o[row*2]);")
            self.emit("    }")
        if self.cc65_wide_scroll:
            self.emit("    for (s = 0; s < GBS_BKG_MAP_W; ++s) "
                      "gbs_bkg_compose_col(s);  /* tiles changed */")
        else:
            self.emit("    for (s = 0; s < GBS_BKG_STRIPS; ++s) "
                      "gbs_bkg_strip_col[s] = 0;  /* recompose the ring */")
            self.emit("    gbs_bkg_built = 0;")
        self.emit("    gbs_force = 1;   /* new tile data -> present must recomposite */")
        self.emit("}")
        self.emit("")

    def _emit_lynx_range_cache(self, off_table=None):
        """Range-windowed asset cache (Lynx cart): paint_table + [world] stream
        (the paint interpreter). The concatenated MAPS/COLLISION arrays are archived
        WHOLE (their `gbs_range_off[base]` is the archive base); paint() streams
        the CURRENT room's WINDOW (offset MAP_OFF[room], length w*h) into a slot
        sized to the WIDEST scene, and map_tile/collision_at read from that slot.
        Keyed by ABSOLUTE archive offset (base + window off), so the room's map
        AND collision each own one of the 2 LRU slots -- the minimum correct size
        on the tight Lynx MAIN. This is what makes the room count stop growing
        resident code (the O(1) paint interpreter) AND resident data (streaming)
        at once.

        `off_table` names an ALREADY-EMITTED offsets table to read (the whole
        cache's `gbs_asset_off` when a program uses both seams -- same id space,
        identical contents), so the duplicate is not emitted. None = emit our
        own `gbs_range_off` (the byte-identical single-seam case)."""
        offs = ", ".join(str(o) for o in self.stream_offsets)
        # One slot per windowed base (the room's map AND its collision layer),
        # capped at 2 -- a world with a single base can't thrash, and each slot
        # costs GBS_RANGE_MAXWIN bytes of the scarce Lynx MAIN.
        slots = min(2, len(self.stream_offsets))
        off_sym = off_table or "gbs_range_off"
        self.emit("/* --- Range asset streaming (Lynx cart, item 33): paint_table")
        self.emit("   windows the concatenated map/collision arrays -- the CURRENT")
        self.emit("   room's window (MAP_OFF[room], w*h bytes) streams into a %d-slot"
                  % slots)
        self.emit("   LRU cache (its map + collision). Frees resident RAM AND the")
        self.emit("   per-scene paint() code. GBS_ARCHIVE_BASE is baked at link. */")
        self.emit("#include <unistd.h>")
        self.emit("#include <stdio.h>")
        self.emit("#ifndef GBS_ARCHIVE_BASE")
        self.emit("#define GBS_ARCHIVE_BASE 0")
        self.emit("#endif")
        self.emit("#define GBS_RANGE_COUNT %d" % len(self.stream_offsets))
        self.emit("#define GBS_RANGE_MAXWIN %d" % self._range_maxwin)
        self.emit("#define GBS_RANGE_SLOTS %d" % slots)
        if off_table is None:
            self.emit("static const unsigned long gbs_range_off[GBS_RANGE_COUNT] = { %s };"
                      % offs)
        self.emit("static unsigned char gbs_rcache[GBS_RANGE_SLOTS][GBS_RANGE_MAXWIN];")
        self.emit("static unsigned long gbs_rkey[GBS_RANGE_SLOTS] = { %s };"
                  % ", ".join(["0xFFFFFFFFUL"] * slots))
        self.emit("static unsigned int gbs_ruse[GBS_RANGE_SLOTS] = { %s };"
                  % ", ".join(["0"] * slots))
        self.emit("static unsigned int gbs_rclock = 0;")
        self.emit("static unsigned char gbs_rslot(unsigned char base, "
                  "unsigned int off, unsigned int len) {")
        self.emit("    unsigned long key = %s[base] + off;" % off_sym)
        self.emit("    unsigned char s, victim; unsigned int oldest;")
        self.emit("    for (s = 0; s < GBS_RANGE_SLOTS; ++s)")
        self.emit("        if (gbs_rkey[s] == key) { gbs_ruse[s] = ++gbs_rclock; return s; }")
        self.emit("    victim = 0; oldest = gbs_ruse[0];")
        self.emit("    for (s = 1; s < GBS_RANGE_SLOTS; ++s)")
        self.emit("        if (gbs_ruse[s] < oldest) { oldest = gbs_ruse[s]; victim = s; }")
        self.emit("    lseek(1, (long)((unsigned long)GBS_ARCHIVE_BASE + key), SEEK_SET);")
        self.emit("    read(1, gbs_rcache[victim], len);")
        self.emit("    gbs_rkey[victim] = key; gbs_ruse[victim] = ++gbs_rclock;")
        self.emit("    return victim;")
        self.emit("}")
        self.emit("void gbs_asset_load_range(unsigned char base, unsigned int off, "
                  "unsigned int len) { gbs_rslot(base, off, len); }")
        self.emit("unsigned char *gbs_asset_ptr_range(unsigned char base, "
                  "unsigned int off, unsigned int len) { "
                  "return gbs_rcache[gbs_rslot(base, off, len)]; }")
        # range_byte reads a byte of an ALREADY-WARM window (paint warmed the
        # room's map + collision): find the slot, or slot 0 if a caller reads
        # before paint (the same graceful-degrade shape as the whole-asset cache).
        self.emit("unsigned char *gbs_asset_find_range(unsigned char base, "
                  "unsigned int off) {")
        self.emit("    unsigned long key = %s[base] + off; unsigned char s;" % off_sym)
        self.emit("    for (s = 0; s < GBS_RANGE_SLOTS; ++s)")
        self.emit("        if (gbs_rkey[s] == key) { gbs_ruse[s] = ++gbs_rclock; "
                  "return gbs_rcache[s]; }")
        self.emit("    return gbs_rcache[0];")
        self.emit("}")
        self.emit("")

    def _emit_data_bank_units(self):
        """Emit one C translation unit per ROM bank holding streamed const arrays
        (GB family). The cart is CPU-addressable, so a streamed const stays in ROM
        -- placed in a bank via SDCC's file-scoped `#pragma bank` (the same
        machinery as bank(N) functions, here carrying DATA) -- and the seam
        bank-switches to read it (SWITCH_ROM), no copy-to-RAM. The main TU declares
        each array `extern` (see _emit_module_data) and reads it after a switch;
        the definitions live here, in the banked area ($4000-$7FFF when mapped).

        A data bank holds only data (banks are assigned above any bank(N) function
        placement), so its TU needs nothing from the program but the integer types
        -- the arrays are self-contained literals."""
        main_output = self.output
        # Prelude tables (O5) share these banks, so gather them per bank first.
        prelude = {}
        for sym, (bank, text, _size) in sorted(self.prelude_bank_defs.items()):
            prelude.setdefault(bank, []).append(text)
        for bank in sorted(set(self.data_bank_syms) | set(prelude)):
            self.output = []
            self.emit("/* Generated by mosaik -> GBDK C backend -- ROM bank %d "
                      "(streamed asset data) */" % bank)
            self.emit("/* Target console: %s */" % self.platform)
            self.emit("#pragma bank %d" % bank)
            self.emit("")
            self.emit("#include <gbdk/platform.h>")
            self.emit("#include <stdint.h>")
            self.emit("")
            for sym in self.data_bank_syms.get(bank, ()):
                decl = self.data_bank_decls[sym]
                self.emit("const " + self._format_var_decl(decl, sym) + ";")
            for text in prelude.get(bank, ()):
                self.emit(text)
            self.bank_units[bank] = "\n".join(self.output)
        self.output = main_output

    def _emit_module_functions(self, module):
        functions = [d for d in module.declarations
                     if isinstance(d, FunctionDecl)
                     and not self._in_bank_unit(d)]
        if not functions:
            return
        self.emit("/* Module: %s -- functions */" % module.name)
        self.emit("")
        for func in functions:
            self._emit_function(func)
