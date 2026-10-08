"""CodeGenerator Lynx tile budgets + scene-dim scans, mixed into generator.CodeGenerator.

Split out of the former monolithic generator.py; a plain mixin class composed
into CodeGenerator - methods reference sibling methods/attributes via self."""
import dataclasses
import re

from ..ast_nodes import *  # noqa: F401,F403
from ..platforms import (PLATFORM_CAPS, canonical_platform,
                         framework_for_platform, platform_caps)


class BudgetsMixin:
    # The band the GLYPH-BUFFER text mode falls back to when the program's own
    # tile uploads cannot be read (a hand-written program with no scenes module
    # and a runtime upload count). Deliberately at the very top of the table, so
    # art keeps everything below it.
    GLYPH_FALLBACK_COUNT = 48

    # SMS / Game Gear: the background can only address tiles 0..191. Pattern
    # data for "tile 192..247" IS the name table and "248..255" IS the SAT
    # (docs/vram-layout.md, measured via the glyph-corruption map) -- an upload
    # up there does not draw, it corrupts the map and the sprite attributes.
    SMSGG_BKG_TILES = 192

    def _check_smsgg_bkg_range(self, program):
        """Reject a resolvable `bkg.set_data` past the SMS/GG background range.

        The GB family addresses background tiles 0..255, so the idioms that
        park something at the TOP of the table -- the studio's hidden index
        space for `[[animated_tile]]` clips (255 downward), a 9-slice frame at
        a GB-style base -- are silently invalid here: the write lands in the
        name table / SAT, the tile never draws, and sprites flicker. It cost
        `projects/vm-bganim` its whole water animation on both consoles while
        the GB build was correct, which is exactly the kind of bug that has to
        be a build error rather than a screenshot someone has to notice.

        Only a COMPILE-TIME resolvable `first + count` is checked (the same
        literal / module const / imported const resolution the Lynx budgets
        use), so a runtime upload is not second-guessed."""
        if self.platform not in ('sms', 'gamegear'):
            return
        imported = self._module_int_consts(program)
        for module in program.modules:
            calls = []
            self._alias_calls(module, 'bkg', 'set_data', calls)
            for call in calls:
                top = self._bkg_upload_top(module, call, imported)
                if top is None or top <= self.SMSGG_BKG_TILES:
                    continue
                raise RuntimeError(
                    "module '%s': bkg.set_data uploads background tile data up "
                    "to id %d, but the %s background only addresses tiles "
                    "0..%d -- ids %d..247 are the name table and 248..255 the "
                    "sprite attribute table, so this write corrupts them "
                    "instead of drawing. Move the tile down (an animated "
                    "tile's hidden index belongs just ABOVE the real tileset, "
                    "not at the top of a 256-tile table) or drop this console "
                    "from target_platforms. See docs/vram-layout.md."
                    % (module.name, top - 1, self.platform,
                       self.SMSGG_BKG_TILES - 1, self.SMSGG_BKG_TILES))

    def _resolve_glyph_band(self, program):
        """Place the `text.glyph_buffer` band above the program's tile DATA.

        The band's whole purpose is to stop the scene art and the glyphs from
        fighting over the tile table, so its base is not something an author
        should be hand-picking: it is exactly one past the highest background
        tile the program ever uploads. That is `max(first + count)` over every
        resolvable `bkg.set_data` in the WHOLE program -- the scene tileset, the
        per-scene tilesets (via the transpiler's `TS_MAX_TC`) and the shell's
        own 9-slice frame all upload that way, and doing it here rather than in
        `generate_rooms` means it sees the FINAL program, after conditional
        compilation, including a hand-written shell's uploads.

        An upload whose count is a runtime value cannot be read; the same
        tolerance the Lynx budgets take applies, and `TS_MAX_TC` is what covers
        the one case (`paint_table`) where it matters.

        Sets `self.glyph_band = (base, count)`. `count` can come out SMALL on a
        program that fills the table; that is honest, and the runtime aliases
        rather than corrupting. Zero room is a hard error, since silently not
        drawing text would be the worst outcome.
        """
        self.glyph_band = None
        # Only the tile-plotting GBDK consoles rasterize into a band at all;
        # the NES draws from CHR and the cc65 consoles from a framebuffer, so
        # there is no band to place and nothing to refuse.
        if self.framework != 'gbdk' or not (self.caps['has_window']
                                            or self.platform in ('sms', 'gamegear')):
            return
        top = self.GLYPH_TOP_TILE.get(self.platform, 256)
        base = 0
        imported = self._module_int_consts(program)
        for module in program.modules:
            calls = []
            self._alias_calls(module, 'bkg', 'set_data', calls)
            for call in calls:
                n = self._bkg_upload_top(module, call, imported)
                if n is not None:
                    base = max(base, n)
        scenes = self._find_scenes_module(program)
        if scenes is not None:
            base = max(base, self._scan_scene_tileset_tiles(scenes))
        if base <= 0:
            # Nothing readable: keep the conservative top-of-table default.
            base = top - self.GLYPH_FALLBACK_COUNT
        count = top - base
        if count < 2 and self.glyph_buffer_used:
            raise ValueError(
                "text.glyph_buffer: no room for a glyph band -- the program "
                "uploads background tiles up to %d and this console's "
                "background ends at %d, leaving %d tile(s). The band needs at "
                "least 2 (slot 0 is the space glyph). Shrink the tileset, or "
                "pass an explicit base to text.glyph_buffer()." % (base, top, count))
        self.glyph_band = (base, min(count, 64))

    def _resolve_lynx_bkg_budgets(self, program):
        """Auto-size the cc65 Lynx ROW-strip bkg engine's two big BSS arrays
        (`gbs_bkg_tileset[N][16]` ~4 KB and `gbs_bkg_strip[STRIPS][...]` ~13.5 KB)
        from the world's scene data, so a VM8 game that uses a handful of tiles
        in screen-sized rooms stops paying the worst-case ~18 KB of MAIN.

        Resolves `self.bkg_max_tiles` (int, 1..256) and `self.bkg_strip_w`
        (int tiles, or None = the full scroll-period default). An explicit
        `[build]` knob is honoured verbatim; only an UNSET knob auto-derives.

        Gated tight, so every other program stays byte-identical:
          * cc65 + `graphics.bkg` + the ROW-strip engine only (the WIDE column
            engine `_emit_cc65_bkg_engine_wide` ignores strip_w; non-Lynx and
            non-bkg programs read neither value).
          * a VM8 game only (`import "vm.core"`). Its `vm.player` camera
            provably CLAMPS horizontal scroll to the scene bounds, so the widest
            scene bounds the strip. A hand-written game may scroll a small map
            continuously (`projects/background` wraps a 32-wide map), so its
            strip must stay the full period -- hence the VM gate.
          * `bkg_max_tiles` shrinks only when the world has NO animated tiles
            (their hidden index is not in the `TILE_COUNT` the shrink reads, so
            the table stays 256 -- detected via a non-empty `scenes.anim_tick`).
            Conservative rather than exact: the studio allocates that index
            just above the tileset now (it used to count down from 255, which
            never drew on SMS/GG -- see `_check_smsgg_bkg_range`), so the table
            could be sized to `TILE_COUNT + clips`. Left alone deliberately;
            256 is correct, just not tight.
        """
        req_t, req_s = self._bkg_max_tiles_req, self._bkg_strip_w_req
        # Resolved defaults = byte-identical: full 256-tile table, full strip.
        self.bkg_max_tiles = int(req_t) if req_t else 256
        self.bkg_strip_w = int(req_s) if req_s else None
        self.lynx_bkg16 = False
        if self.framework != 'cc65' or not self.cc65_bkg_imported:
            return
        scenes = self._find_scenes_module(program)
        # 4bpp (16-colour) Lynx background: the opt-in `[world] lynx_bkg16` makes
        # the scene transpiler give the Lynx branch the 32 B/tile 4bpp TILESET, so
        # BOTH Suzy bkg engines (the ROW-strip default and the WIDE column engine
        # for engine.scroll levels) widen to BPP_4 (~2x the bkg BSS -- the RAM cost
        # the opt-in pays). Detected from the tileset's actual depth so a 2bpp
        # tileset / a project that did not opt in is byte-identical. NOT VM-gated
        # and detected for wide worlds too (the wide engine reads self.lynx_bkg16).
        if scenes is not None:
            self.lynx_bkg16 = (self._scan_bkg_tile_bytes(scenes) == 32)
        # `[build] lynx_bkg16` forces it on for a hand-written game (no scenes
        # module); the developer then supplies 4bpp tiles + calls palette.load_bkg16.
        if getattr(self, '_lynx_bkg16_req', False):
            self.lynx_bkg16 = True
        # The STRIP shrink below is the ROW engine only: the WIDE column engine's
        # columns are fixed at the map width and its camera scroll is not clamped,
        # so strip_w can't shrink there. `bkg_max_tiles` is a property of the
        # TILESET, not of scrolling, so it derives for both engines (the wide
        # engine allocates the same gbs_bkg_tileset[N][16]) -- see below.
        # The derive is VM8-only (the strip shrink relies on the vm.player scroll
        # clamp; the tile-count shrink rides the same gate so hand-written Lynx
        # programs stay byte-identical).
        if not any(imp.module_name == 'vm.core'
                   for m in program.modules for imp in m.imports):
            return
        if scenes is None:
            # No scenes module (a script-only VM8 game: dialogue/menu/audio
            # demos). There is no TILE_COUNT to read, but the program's own
            # `bkg.set_data` uploads bound the table exactly as scenes.TILE_COUNT
            # does -- and a game that never uploads AND never composes a cell can
            # never index it at all, so it stops paying the worst-case 4 KB of
            # MAIN for a background it does not draw. Bails to 256 whenever it
            # can't prove the bound (byte-identical).
            if req_t is None:
                self.bkg_max_tiles = self._derive_bkg_max_tiles(program)
            return
        tile_count, max_scene_w, animated = self._scan_scene_dims(scenes)
        if req_t is None and tile_count and not animated:
            # Per-scene tilesets each upload their own table over the same
            # array, so the bound is the WIDEST upload, not just TILE_COUNT.
            self.bkg_max_tiles = max(1, min(256, max(tile_count,
                                                     self._scan_scene_tileset_tiles(scenes))))
        if self.cc65_wide_scroll:
            return                        # WIDE column engine: strip_w is fixed
        if getattr(self, 'lynx_orient', None):
            return                        # portrait: a scene's width is the strips' HEIGHT
        if req_s is None and max_scene_w:
            sw = self.cc65_profile.get('screen_w', 160)
            screen_tiles = (sw + 7) // 8
            full = (255 + sw + 7) // 8          # the full scroll-period width
            # The visible window at max scroll reaches strip cell (camx + sw)/8;
            # a room `max_scene_w` tiles wide (camx clamped to its right edge)
            # needs cells 0..max_scene_w, i.e. max_scene_w + 1 of them. A room
            # that fits the screen still needs screen_tiles + 1 for the sub-tile
            # pan. Never grow past the full default.
            self.bkg_strip_w = min(full, max(screen_tiles + 1, max_scene_w + 1))

    def _derive_bkg_max_tiles(self, program):
        """`bkg_max_tiles` for a VM8 game with NO scenes module, read from the
        program's own background calls (the `gbs_bkg_tileset[N][16]` bound):

          * every `bkg.set_data(first, count, ...)` resolvable -> the highest
            `first + count` (what the program actually fills, the same rule
            `_resolve_sprite_max_tiles` uses for the Suzy tile table);
          * NO upload AND no `bkg.set_tiles` compose -> 1 (a dialogue / menu /
            audio demo never touches the background layer, so no cell can index
            the table -- it is dead RAM);
          * anything else (an unresolvable upload, or cells composed with no
            upload we can bound) -> 256, the safe default (byte-identical).
        """
        top, any_upload = 0, False
        imported = self._module_int_consts(program)
        for module in program.modules:
            calls = []
            self._alias_calls(module, 'bkg', 'set_data', calls)
            for call in calls:
                any_upload = True
                n = self._bkg_upload_top(module, call, imported)
                if n is None:
                    return 256            # a runtime bound -> keep them all
                top = max(top, n)
        if not any_upload:
            composes = []
            for module in program.modules:
                self._alias_calls(module, 'bkg', 'set_tiles', composes)
            return 1 if not composes else 256
        return max(1, min(256, top)) if top else 256

    def _scan_scene_tileset_tiles(self, scenes):
        """The widest `bkg.set_data(first, count, ...)` upload in the `scenes`
        module, in tiles (0 when none resolves).

        PER-SCENE TILESETS (`[[scene]] tileset = "..."`) upload their own table
        over the same `gbs_bkg_tileset` array with their own `<NAME>_TC` count,
        so the shared `TILE_COUNT` alone does NOT bound the array -- a scene
        with a bigger private tileset would overrun a table sized to it. An
        upload this can't resolve is ignored (the caller still has TILE_COUNT),
        which keeps the long-standing tolerance of the TILE_COUNT-only rule.

        Under `paint_table` the transpiler uploads through an INDEXED table
        (`bkg.set_data(0, tsc, assets.ptr_range(TILESETS, ...))`), whose count is
        a runtime variable no scan can resolve -- so it also emits a
        `TS_MAX_TC` const naming the largest per-scene tileset, and that is read
        here. Without it the table would be sized to TILE_COUNT alone and a
        bigger per-scene tileset would overrun it."""
        top = 0
        calls = []
        self._alias_calls(scenes, 'bkg', 'set_data', calls)
        for call in calls:
            n = self._bkg_upload_top(scenes, call)
            if n is not None:
                top = max(top, n)
        for decl in scenes.declarations:
            if (isinstance(decl, VarDecl) and decl.is_const
                    and decl.name == 'TS_MAX_TC'
                    and isinstance(decl.initializer, Literal)
                    and isinstance(decl.initializer.value, int)):
                top = max(top, decl.initializer.value)
        return top

    @staticmethod
    def _bkg_upload_top(module, call, imported=None):
        """`first + count` of a `bkg.set_data(first, count, data)` call, or None
        when either is not a compile-time integer. An int literal, a
        module-level int `const` (the transpiler's `TILE_COUNT` / `<NAME>_TC`)
        and an IMPORTED one (`tiles.TILE_COUNT`) all resolve.

        The cross-module form matters because a hand-written game may keep its
        tile table in its own generated data module -- which is the natural
        shape once the art is generated (the falling-block assembly sample). Without it the
        count read as unresolvable and the Lynx kept the worst-case 256-tile
        table: 8 KB of MAIN at 4bpp for a program that uploads 50 tiles."""
        if len(call.arguments) < 2:
            return None
        consts = {d.name: d.initializer.value for d in module.declarations
                  if isinstance(d, VarDecl) and d.is_const
                  and isinstance(d.initializer, Literal)
                  and isinstance(d.initializer.value, int)}
        imported = imported or {}

        def val(expr):
            if isinstance(expr, Literal) and isinstance(expr.value, int):
                return expr.value
            if isinstance(expr, Identifier):
                return consts.get(expr.name)
            if (isinstance(expr, FieldAccess)
                    and isinstance(expr.object, Identifier)):
                return imported.get(expr.object.name, {}).get(expr.field)
            return None

        first, count = val(call.arguments[0]), val(call.arguments[1])
        if first is None or count is None:
            return None
        return first + count

    @staticmethod
    def _module_int_consts(program):
        """{module name: {const name: int}} over every module-level integer
        `const` in the program -- the table `_bkg_upload_top` resolves an
        `alias.NAME` through. A call-site alias IS the module's name (the last
        path segment), so no import bookkeeping is needed."""
        out = {}
        for m in program.modules:
            out[m.name] = {d.name: d.initializer.value for d in m.declarations
                           if isinstance(d, VarDecl) and d.is_const
                           and isinstance(d.initializer, Literal)
                           and isinstance(d.initializer.value, int)}
        return out

    def _alias_calls(self, node, alias, field, out):
        """Collect every `alias.field(...)` FunctionCall under `node`."""
        if isinstance(node, FunctionCall):
            fn = node.function
            if (isinstance(fn, FieldAccess) and isinstance(fn.object, Identifier)
                    and fn.object.name == alias and fn.field == field):
                out.append(node)
        if isinstance(node, ASTNode):
            for f in dataclasses.fields(node):
                self._alias_calls(getattr(node, f.name), alias, field, out)
        elif isinstance(node, (list, tuple)):
            for x in node:
                self._alias_calls(x, alias, field, out)

    @staticmethod
    def _find_scenes_module(program):
        """The Layer-3 `scenes` module (mosaik_scenes.py output), or None.

        Matched by name first, else by its signature const `TILE_COUNT` + the
        always-emitted `anim_tick` function, so a renamed module is still found.
        """
        for m in program.modules:
            if m.name == 'scenes':
                return m
        for m in program.modules:
            has_tc = any(isinstance(d, VarDecl) and d.is_const
                         and d.name == 'TILE_COUNT' for d in m.declarations)
            has_at = any(isinstance(d, FunctionDecl) and d.name == 'anim_tick'
                         for d in m.declarations)
            if has_tc and has_at:
                return m
        return None

    @staticmethod
    def _scan_bkg_tile_bytes(scenes):
        """Bytes per tile in the scenes module's TILESET (16 = GB 2bpp, 32 = the
        4bpp background tier), or 16 when it can't be read. Reads len(TILESET) /
        TILE_COUNT -- both are plain consts the scene transpiler emits, and after
        conditional compilation only the target's TILESET branch survives, so on a
        Lynx build this is the depth Lynx actually gets."""
        tile_count = 0
        tileset_len = 0
        for d in scenes.declarations:
            if isinstance(d, VarDecl) and d.is_const:
                init = d.initializer
                if d.name == 'TILE_COUNT' and isinstance(init, Literal) \
                        and isinstance(init.value, int):
                    tile_count = init.value
                elif d.name == 'TILESET' and isinstance(init, ArrayLiteral):
                    tileset_len = len(init.elements)
        if tile_count and tileset_len and tileset_len % tile_count == 0:
            return tileset_len // tile_count
        return 16

    @staticmethod
    def _scan_scene_dims(scenes):
        """(tile_count, max_scene_w, animated) read from a scenes module's
        const declarations. `tile_count` = `TILE_COUNT`; `max_scene_w` = the
        widest scene (`max(MAP_W, SCENE_W[])`, tiles); `animated` = True when
        `anim_tick` has a body (background animated tiles present). Any value
        it can't read as a plain int falls back to a conservative miss (0 /
        True) so the caller keeps the safe full default."""
        tile_count = 0
        map_w = 0
        scene_w_max = 0
        animated = False
        for d in scenes.declarations:
            if isinstance(d, VarDecl) and d.is_const:
                init = d.initializer
                if d.name == 'TILE_COUNT' and isinstance(init, Literal) \
                        and isinstance(init.value, int):
                    tile_count = init.value
                elif d.name == 'MAP_W' and isinstance(init, Literal) \
                        and isinstance(init.value, int):
                    map_w = init.value
                elif d.name == 'SCENE_W' and isinstance(init, ArrayLiteral):
                    vals = [e.value for e in init.elements
                            if isinstance(e, Literal) and isinstance(e.value, int)]
                    if vals:
                        scene_w_max = max(vals)
            elif isinstance(d, FunctionDecl) and d.name == 'anim_tick':
                animated = bool(d.body)
        return tile_count, max(map_w, scene_w_max), animated

    def _resolve_sprite_max_tiles(self, program):
        """Auto-size the cc65 Lynx Suzy sprite tile table `gbs_tiles[N][33]`
        (~1.3 KB of MAIN BSS at N=40) from the tiles the program actually
        UPLOADS via sprite.set_data, so a game using a handful of sprite tiles
        stops paying for all 40 slots (the same reclaim as bkg_max_tiles, whose
        upload count is scenes.TILE_COUNT).

        Resolves `self.sprite_max_tiles` (int 1..40, or None = the full 40
        default). An explicit `[build] sprite_max_tiles` wins; an unset knob
        auto-derives. The bound is `max(first + count)` over every
        `sprite.set_data(first, count, ...)` whose first + count are resolvable
        at compile time (an integer literal or an asset `<name>_tile_count`
        define). It IGNORES set_meta/set_tile (their tile args are runtime
        variables in the vm.actor/canim/player pool, but they only reference
        tiles that were UPLOADED, so set_data bounds them -- exactly the
        assumption bkg_max_tiles makes about map cells referencing bkg tiles).
        Bails to the full 40 (byte-identical) if ANY set_data upload is
        unresolvable, or none is found, so it can only ever keep too many.

        Only the cc65 Lynx sprite engine reads `sprite_max_tiles`; every other
        console / non-sprite program keeps 40 (golden-pinned byte-identical).
        The AUTO-derive is gated to a VM8 game (`import "vm.core"`) -- the same
        gate as the bkg budgets -- so hand-written Lynx samples (bounce/pong) stay
        byte-identical; a non-VM game that needs the reclaim sets the explicit
        `[build] sprite_max_tiles` knob (honoured on any program).
        """
        req = self._sprite_max_tiles_req
        self.sprite_max_tiles = int(req) if req else None
        if (self.framework != 'cc65' or not self.caps.get('has_sprites')
                or self.platform != 'lynx'):
            return
        if req is not None:
            return
        if not any(imp.module_name == 'vm.core'
                   for m in program.modules for imp in m.imports):
            return
        # asset-pipeline tile counts: `<name>_tile_count` resolves to the tile
        # count of the registered PNG (the same value the emitter #defines).
        tsize = 32 if self.sprite_src_bpp == 4 else 16
        asset_counts = {"%s_tile_count" % name: len(data) // tsize
                        for name, data, _bpp in self.assets}

        def _int_arg(expr):
            """The integer value of a set_data first/count arg, or None if it is
            not resolvable at compile time (a runtime variable)."""
            if isinstance(expr, Literal) and isinstance(expr.value, int):
                return expr.value
            if isinstance(expr, Identifier) and expr.name in asset_counts:
                return asset_counts[expr.name]
            return None

        calls = []
        for module in program.modules:
            self._sprite_set_data_calls(module, calls)
        if not calls:
            return                        # no uploads -> keep the safe full 40
        top = 0
        for call in calls:
            first = _int_arg(call.arguments[0])
            count = _int_arg(call.arguments[1])
            if first is None or count is None:
                return                    # an unresolvable upload -> bail to 40
            top = max(top, first + count)
        if top:
            self.sprite_max_tiles = max(1, min(40, top))

    def _resolve_cc65_sprite_need(self, program):
        """`(tiles, slots)` the busiest room needs, from the rooms generator's
        `SPR_TILE_NEED` / `SPR_SLOT_NEED` consts (emitted only for a residency
        project that targets the Lynx or the PC Engine), or None. A residency
        upload's base is a runtime value, so no scan of the calls can bound
        it; the generator, which knows every room's kinds, can."""
        self.cc65_spr_need = None
        if self.framework != 'cc65':
            return
        got = {}
        for module in program.modules:
            for decl in module.declarations:
                if (isinstance(decl, VarDecl) and decl.is_const
                        and decl.name in ('SPR_TILE_NEED', 'SPR_SLOT_NEED')
                        and isinstance(decl.initializer, Literal)
                        and isinstance(decl.initializer.value, int)):
                    got[decl.name] = decl.initializer.value
        if len(got) == 2:
            self.cc65_spr_need = (got['SPR_TILE_NEED'], got['SPR_SLOT_NEED'])

    def _sprite_set_data_calls(self, node, out):
        """Collect every `sprite.set_data(first, count, data)` call under `node`."""
        if isinstance(node, FunctionCall):
            fn = node.function
            if (isinstance(fn, FieldAccess) and isinstance(fn.object, Identifier)
                    and fn.object.name == 'sprite' and fn.field == 'set_data'
                    and len(node.arguments) == 3):
                out.append(node)
        if isinstance(node, ASTNode):
            for f in dataclasses.fields(node):
                self._sprite_set_data_calls(getattr(node, f.name), out)
        elif isinstance(node, (list, tuple)):
            for x in node:
                self._sprite_set_data_calls(x, out)

    def _collect_called_verbs(self, program):
        """Every `alias.field(...)` the program contains, in ONE traversal.

        `generate()` asks `_program_uses_call` about ~40 verbs to decide which
        prelude machinery to emit, and each question used to be its own whole-
        program walk. **Measured on the reference-engine sample conversion: 93 calls, 18.1 s - 58% of
        the entire compile** (review L-10). The answers are a pure function of
        the AST, so one walk answers all of them.

        Refreshed at the top of `generate()` rather than cached forever: the
        AST is rewritten before then (tree-shaking, the code-bank split), and a
        set that outlived a rewrite would answer about a program that no longer
        exists. Anything asking outside that window falls back to the walk.
        """
        found = set()

        def walk(node):
            if isinstance(node, FunctionCall):
                fn = node.function
                if (isinstance(fn, FieldAccess)
                        and isinstance(fn.object, Identifier)):
                    found.add((fn.object.name, fn.field))
            if isinstance(node, ASTNode):
                for f in dataclasses.fields(node):
                    walk(getattr(node, f.name))
            elif isinstance(node, (list, tuple)):
                for x in node:
                    walk(x)

        walk(program)
        return found

    def _program_uses_call(self, program, alias, field) -> bool:
        """True if the program contains a call `alias.field(...)` anywhere.

        Used to gate per-program engine machinery (e.g. the metasprite layer)
        so programs that don't use a feature keep byte-identical output -- the
        same discipline as the palette / Lynx-bkg import flags, but keyed on a
        call instead of an import (set_meta lives inside graphics.sprite, which
        every sprite program already imports).

        Answered from `_collect_called_verbs`' one traversal when `generate()`
        has one in hand for THIS program object; otherwise it walks, which is
        what the handful of callers outside that window get."""
        verbs = getattr(self, '_called_verbs', None)
        if verbs is not None and getattr(self, '_called_verbs_for', None) is program:
            return (alias, field) in verbs
        return self._walk_uses_call(program, alias, field)

    @staticmethod
    def _walk_uses_call(program, alias, field) -> bool:
        """The traversal itself (also the fallback, and what the memo is
        checked against by `walker_coverage_test.py`)."""
        found = []

        def walk(node):
            if found:
                return
            if isinstance(node, FunctionCall):
                fn = node.function
                if (isinstance(fn, FieldAccess)
                        and isinstance(fn.object, Identifier)
                        and fn.object.name == alias and fn.field == field):
                    found.append(True)
                    return
            if isinstance(node, ASTNode):
                for f in dataclasses.fields(node):
                    walk(getattr(node, f.name))
            elif isinstance(node, (list, tuple)):
                for x in node:
                    walk(x)

        walk(program)
        return bool(found)

    @staticmethod
    def _program_uses_identifier(program, name) -> bool:
        """True if the program mentions the bare identifier `name` anywhere.

        The call twin above cannot see a CONSTANT, and some of what a console
        degrades is a constant rather than a verb (`FLIP_X` on a VDP with no
        sprite mirroring; `INPUT_START` on a Lynx, where it is 0). Used only
        for the E-7 build notes, so a false positive costs a line of output."""
        found = []

        def walk(node):
            if found:
                return
            if isinstance(node, Identifier) and node.name == name:
                found.append(True)
                return
            if isinstance(node, ASTNode):
                for f in dataclasses.fields(node):
                    walk(getattr(node, f.name))
            elif isinstance(node, (list, tuple)):
                for x in node:
                    walk(x)

        walk(program)
        return bool(found)

    @staticmethod
    def _escape_string(value: str) -> str:
        """A mosaik string's BYTES as a C string literal body. Printable ASCII
        rides verbatim (backslash and quote escaped); everything else -- the
        newline / tab / NUL a source escape produced, or a non-ASCII char -- is
        a 3-digit OCTAL escape, which unlike \\xNN cannot swallow a following
        hex digit of the text."""
        out = []
        for ch in value:
            if ch == '\\':
                out.append('\\\\')
            elif ch == '"':
                out.append('\\"')
            elif ' ' <= ch <= '~':
                out.append(ch)
            else:
                out.append('\\%03o' % (ord(ch) & 0xFF))
        return ''.join(out)
