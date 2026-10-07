"""Top-level driver: lex + parse + typecheck + codegen for a program."""

import dataclasses
import sys

from .errors import CompileError
from .lexer import Lexer
from .parser import Parser
from .typechecker import TypeChecker
from .codegen import CodeGenerator
from .platforms import canonical_platform, obj16_effective
from .stdlib import stdlib_module_names
from .ast_nodes import *  # noqa: F401,F403  (AST node types used by tree-shaking)
from .ast_nodes import (Program, Module, FunctionDecl, ASTNode)


# Integer defines that size a FIXED RUNTIME POOL in `lib/vm`, with the sizes
# the runtime shipped with. They are always supplied (see compile_program), so
# a bare `compile_program(sources)` parses `lib/vm` unchanged and the default
# build stays byte-identical; `[build] actor_pool` / `trigger_pool` override
# them. Growing a pool costs BSS on every console, which is why it is a knob
# and not simply a bigger constant -- measured on the GB with pools at 20:
# +29 B per actor slot (vm.actor + vm.entity + vm.canim; +15 more if a project
# links vm.clip) and +9 B per trigger slot, with zero code growth.
POOL_DEFINE_DEFAULTS = {
    "VM_ACTOR_POOL": 8,
    "VM_TRIGGER_POOL": 8,
    # engine.anim's animator pool. `vm.canim` indexes it BY ACTOR SLOT, so it
    # must be >= VM_ACTOR_POOL; the build derives max(8, actor_pool).
    "VM_ANIM_SLOTS": 8,
}


class MosaikCompiler:
    def __init__(self):
        self.lexer = None
        self.parser = None
        self.type_checker = TypeChecker()
        self.code_generator = CodeGenerator()

    def compile(self, source_code: str, platform: str = None,
                assets: list = None, asset_palettes: list = None,
                asset_palettes16: list = None, asset_sprites: list = None) -> str:
        """Compile one mosaik source to C for the given target console.

        Convenience wrapper around compile_program() for a single source
        (the contained modules still link against each other).

        `platform` selects the build target (default: the code generator's
        current platform). It drives `if platform == "..."` conditional
        compilation and the platform-specific prelude.

        `assets` is a list of (name, gb_2bpp_bytes) pairs from the asset
        pipeline (mosaik_assets.py); each is emitted into the TU as a
        `const uint8_t <name>_tiles[]` array plus a `<name>_tile_count`
        define, usable directly from mosaik code.

        `asset_palettes` is a list of (name, [(r,g,b)] x 4) pairs -- the
        authored palettes of indexed-PNG assets (mosaik_assets.py's
        load_asset_palettes). For programs that import graphics.palette,
        each is emitted as a `const uint16_t <name>_palette[4]` array of
        native color words (converted at build time per console).
        """
        return self.compile_program([("<source>", source_code)],
                                    platform=platform, assets=assets,
                                    asset_palettes=asset_palettes,
                                    asset_palettes16=asset_palettes16,
                                    asset_sprites=asset_sprites)

    def compile_program(self, sources: list, platform: str = None,
                        assets: list = None,
                        asset_palettes: list = None,
                        asset_palettes16: list = None,
                        asset_sprites: list = None,
                        bkg_max_tiles: int = None,
                        bkg_strip_w: int = None,
                        sprite_max_tiles: int = None,
                        sprite_max_slots: int = None,
                        lynx_bkg16: bool = False,
                        lynx_code_resident: bool = False,
                        bank_bytecode: bool = False,
                        shake_exports: bool = False,
                        defines: dict = None,
                        code_banks: list = None,
                        glyph_font: list = None,
                        glyph_widths: list = None,
                        obj_8x16: bool = False,
                        sms_start_button: bool = False) -> str:
        """Compile a whole program (one or more sources) to a single C TU.

        `sources` is a list of (filename, source_code) pairs -- every .mos
        file taking part in the build. All modules are parsed up front and
        generated into one C translation unit (whole-program compilation, the
        natural model for sdcc/cc65 which optimize poorly across translation
        units). Cross-module references resolve through each module's
        `export` list; see CodeGenerator._collect_modules for the C-level
        name mangling.

        `defines` is an optional {name: bool} of build-derived compile-time
        flags. They resolve in a conditional-compilation condition exactly like
        `platform`, so a lib module can fold away code the build knows this
        program cannot reach (the VM8 opcode dispatch). Passing none leaves
        every such condition unresolvable, which keeps the `then` branch and
        the output byte-identical.

        Returns the generated C, or a string starting with
        "Compilation error:" on failure (matching compile()).
        """
        try:
            platform = canonical_platform(platform or self.code_generator.platform)
            self.code_generator.platform = platform
            self.code_generator.assets = list(assets or [])
            self.code_generator.asset_palettes = list(asset_palettes or [])
            self.code_generator.asset_palettes16 = list(asset_palettes16 or [])
            self.code_generator.asset_sprites = list(asset_sprites or [])
            self.code_generator.bkg_max_tiles = int(bkg_max_tiles) if bkg_max_tiles else 256
            # The RESOLVED bkg budgets are decided in generate() (once the
            # program is parsed): an explicit knob is honoured, an unset one may
            # auto-derive for a VM8 game -- see _resolve_lynx_bkg_budgets.
            self.code_generator._bkg_max_tiles_req = bkg_max_tiles
            self.code_generator._bkg_strip_w_req = bkg_strip_w
            self.code_generator._sprite_max_tiles_req = sprite_max_tiles
            # Sprite SLOT table budget (cc65 Lynx). Explicit only -- slot ids are
            # runtime values, so nothing auto-derives it; unset keeps all 40.
            self.code_generator.sprite_max_slots = sprite_max_slots
            # Explicit `[build] lynx_bkg16` opt-in: force the 4bpp Lynx background
            # engine for a HAND-WRITTEN game (no scenes module to auto-detect the
            # tileset depth from). A scenes/VM8 game uses `[world] lynx_bkg16`
            # instead (the transpiler forks + the depth is auto-detected).
            self.code_generator._lynx_bkg16_req = bool(lynx_bkg16)
            # `[build] lynx_code_resident` -- keep the VM8 BYTECODE blob in RAM on
            # the Lynx instead of streaming it from the cart. The interpreter
            # fetches it a byte at a time, so a 256 B page miss (~98,000 ticks) on
            # every thread switch and cross-page CALL is the single biggest cost in
            # a Lynx VM8 game; this spends the blob's size in MAIN to remove it.
            # Off = the streamed default, byte-identical.
            self.code_generator._lynx_code_resident = bool(lynx_code_resident)
            # `[build] bank_bytecode` -- the GB-family MIRROR of the knob above, and
            # the opposite trade: put the VM8 BYTECODE blob in a ROM bank instead of
            # the resident image. `_scripts_CODE` is the largest single resident
            # symbol in a VM8 game (2,748 B on the reference-engine conversion) and the one
            # thing the reference engine keeps out of its own bank 0 entirely. It costs a
            # SWITCH_ROM per fetched byte, which is why it is opt-in rather than
            # implied by code_banks: a game whose logic is mostly bytecode pays for
            # it every instruction, while an event-script game barely runs any.
            # Requires code_banks (there is no bank to switch to without it).
            self.code_generator._bank_bytecode = bool(bank_bytecode)
            # `[build] code_banks` -- cold-code ROM banking on the GB family
            # (module names whose function bodies leave the resident image;
            # see CodeGenerator._plan_code_banks). Empty = off, byte-identical.
            self.code_generator.code_banks = list(code_banks or [])
            # `[assets] font` -- a CUSTOM console font for the glyph-buffer
            # text mode: 96 glyphs x 8 one-byte 1bpp rows (ASCII 32..127),
            # replacing the built-in table in the emitted prelude. Build-time,
            # so it costs nothing at runtime and nothing when unset (None =
            # the built-in font, byte-identical).
            self.code_generator.glyph_font_override = (
                list(glyph_font) if glyph_font else None)
            # Per-glyph ADVANCE widths, set only for a VARIABLE-width sheet
            # (`png_to_font_sheet`). None keeps the fixed-width renderer, which
            # is every project today, so this is byte-identical unset.
            self.code_generator.glyph_widths_override = (
                list(glyph_widths) if glyph_widths else None)
            # `[build] obj_8x16` -- 8x16 OBJ sprite mode (G4): a GLOBAL LCDC
            # bit on the GB family, halving every metasprite's OAM cost. The
            # sprite data must be COLUMN-major per frame (the asset pipeline's
            # reorder_tiles_8x16 rides the same flag); off = byte-identical.
            self.code_generator.obj_8x16 = bool(obj_8x16)
            # `[build] sms_start_button` -- the SMS pad's button 1 also
            # answers J_START (it is labelled "1 START"). Off by default
            # because the two are ONE bit to a program, so a script bound
            # to both `a` and `start` would fire twice on one press.
            self.code_generator.sms_start_button = bool(sms_start_button)

            # Integer defines that SIZE a lib pool always have a value, because
            # `lib/vm` names them in its array declarations -- a caller that
            # passes no defines at all (the goldens, the tests, a bare
            # compile_program) must still parse. The caller's value wins; the
            # defaults are the historical sizes, so the default build is
            # byte-identical.
            all_defines = dict(POOL_DEFINE_DEFAULTS)
            all_defines.update(defines or {})
            # `[build] obj_8x16` as a LIB-VISIBLE flag. lib/vm/projectile.mos
            # halves its per-shot OAM fan exactly where a hardware object is
            # 8x16, and it cannot name the console set itself: the mode is a
            # per-project BUILD choice on six consoles, not a property of any
            # one of them (it used to be spelled as a GB-family `if platform`
            # list, which was right only because every project that calls
            # set_cell happens to ask for the mode). Derived from the same
            # obj16_effective() the codegen, the asset reorder and the
            # generated rooms.mos guard read, so the four cannot disagree;
            # ALWAYS present, so a caller passing no defines still parses.
            all_defines.setdefault('VM_OBJ16',
                                   obj16_effective(platform, obj_8x16))
            # vm.music's VBL-interrupt tick. Stated
            # TRUE by the build only when the program wires vm.music AND the
            # target is in platforms.MUSIC_ISR_CONSOLES; ALWAYS present so a
            # caller passing no defines still parses - the False default keeps
            # the main-loop catch-up arms verbatim (byte-identical).
            all_defines.setdefault('VM_MUSIC_ISR', False)
            # vm.music's instrument SUBPATTERN machinery (hUGE v6 tables).
            # Stated TRUE by the build only when the program wires
            # `music.set_subpatterns(` (`_wants_music_subpat`); the False
            # default folds every table arm away, byte-identical.
            all_defines.setdefault('VM_MUSIC_SUBPAT', False)
            # vm.music LENDS the beep's channel (GB pulse 2, SMS/GG tone 0) back
            # to the beep while it sounds
            # (hUGEDriver's model). Stated TRUE by the build only when a song
            # plays pulse 2 (`_wants_music_borrow`); the False default keeps
            # every channel write verbatim, byte-identical.
            all_defines.setdefault('VM_MUSIC_BORROW', False)
            # vm.music gives a song channel with no note (generated kind 3)
            # no voice on the pooled consoles. Stated TRUE by the build only
            # when a song has one (`_wants_music_empty`); byte-identical off.
            all_defines.setdefault('VM_MUSIC_EMPTY', False)
            # The VM FRAME LOCK (`[build] frame_lock`): hold a game frame to a
            # fixed number of DISPLAY frames, so the VM-frame/LCD-frame ratio
            # is a constant a conversion can be scaled against instead of
            # whatever a room's load costs. BOTH are ALWAYS present, so a
            # caller passing no defines still parses AND the statement-level
            # guard folds rather than surviving as a runtime test on an
            # undeclared symbol (the VM_OBJ16 rule). Off = 1 = byte-identical.
            all_defines.setdefault('VM_FRAME_LOCK_ON', False)
            all_defines.setdefault('VM_FRAME_LOCK', 1)
            # Projectile flight under a cutscene LOCK (`[build]
            # proj_under_lock`). The reference VM runs
            # `projectiles_update()` outside its !VM_ISLOCKED() block, so a
            # shot keeps flying through a cutscene there; ours freezes it.
            # ALWAYS present for the same VM_OBJ16 reason, and False keeps
            # vm.core's lock-gated arm verbatim (byte-identical).
            all_defines.setdefault('VM_PROJ_UNDER_LOCK', False)
            # PROJECTILE COLLISION SPREAD (`[build] proj_scan`): the reference VM's own
            # amortisation (`PROJECTILES_COLLISION_SPREAD SPREAD_4`), which
            # tests a shot every FOURTH frame phase-spread by its index. BOTH
            # are ALWAYS present for the same VM_OBJ16 reason - these guards are
            # STATEMENT level inside `update()`, so an unresolvable name would
            # survive as a runtime test on an undeclared symbol rather than
            # folding. All = TRUE, mask = 0, is the every-frame test and is
            # byte-identical.
            # OFFSCREEN ACTOR DEACTIVATION (`[build] actor_deactivate`):
            # The reference VM drops an offscreen actor from the list its per-frame passes
            # walk. ALWAYS present for the same VM_OBJ16 reason - the guards
            # are module- AND statement-level, and an unresolvable name would
            # keep the `then` arm instead of folding. False = the actor stays
            # in the live list = byte-identical.
            all_defines.setdefault('VM_ACTOR_DEACT', False)
            # `[build] bank_bytecode` stated to the generated scripts module,
            # which picks the banked or the resident code window from it.
            all_defines.setdefault('VM_CODE_BANKED', False)
            all_defines.setdefault('VM_PROJ_SCAN_ALL', True)
            all_defines.setdefault('VM_PROJ_SCAN_MASK', 0)
            # FADE DIRECTION (vm.fx's white ramp + graphics.palette's bit-7
            # white scale): stated TRUE by the build when the blob writes the
            # `fade_style` state or the generated rooms.mos declares a
            # `[scenes] fade_style` default. ALWAYS present for the VM_OBJ16
            # reason - fx's guards are statement-level - and False keeps both
            # ramps towards black, character for character (byte-identical).
            all_defines.setdefault('VM_FADE_STYLE', False)
            # The code generator reads a few of these too (the palette prelude
            # emits its white scale only under VM_FADE_STYLE), so hand it the
            # same table the parser folds on.
            self.code_generator.defines = all_defines

            # Lex + parse every source; collect all modules into one program.
            modules = []
            module_files = {}  # module name -> defining file (for diagnostics)
            for filename, source_code in sources:
                self.lexer = Lexer(source_code)
                tokens = self.lexer.tokenize()
                self.parser = Parser(tokens, platform=platform,
                                     defines=all_defines)
                ast = self.parser.parse()
                for module in ast.modules:
                    if module.name in module_files:
                        raise RuntimeError(
                            'duplicate module "%s" (defined in %s and %s)'
                            % (module.name, module_files[module.name], filename))
                    module_files[module.name] = filename
                    module.source_file = filename    # for diagnostics
                    modules.append(module)
            program = Program(modules)

            self._check_imports(program, module_files)
            self._validate_module_names(program)
            program = self._tree_shake(program)
            if shake_exports:
                program = self._shake_declarations(program)

            # Type checking is best-effort: it collects diagnostics (it never
            # raises, so the type annotations it writes into the AST -- which
            # codegen reads -- are the same whether or not anything is
            # reported) and must never block code generation. The try/except
            # is only a safety net against internal checker bugs.
            try:
                self.type_checker = TypeChecker()
                self.type_checker.register_assets(assets or [],
                                                  asset_palettes or [],
                                                  asset_palettes16 or [],
                                                  asset_sprites or [])
                self.type_checker.check_program(program)
                for diag in self.type_checker.diagnostics:
                    print(f"    Warning: {diag}", file=sys.stderr)
            except Exception as type_error:
                print(f"    Warning: type check skipped ({type_error})",
                      file=sys.stderr)

            # Code generation
            c_code = self.code_generator.generate(program)

            return c_code

        except Exception as e:
            # THE STRING CONTRACT STAYS (see mosaik/errors.py: 59 test files
            # and the build tool read it), but what it says is the SOURCE
            # location now, not a Python traceback naming gen_expr.py. The
            # traceback is still one env var away, and `last_error` carries
            # the structured form for a caller that wants it.
            import os
            import traceback
            if not isinstance(e, CompileError):
                e = CompileError(str(e),
                                 file=getattr(self.code_generator, '_cur_file', None),
                                 line=getattr(self.code_generator, '_cur_line', None))
            self.last_error = e
            out = "Compilation error: %s" % e.located()
            if os.environ.get("MOSAIK_TRACEBACK"):
                out += "\n\nFull traceback:\n%s" % traceback.format_exc()
            return out

    def _tree_shake(self, program):
        """Drop modules unreachable from the program entry point.

        When some module defines `main()`, only it and the modules it reaches
        -- through `import` *or* a qualified reference (`other.foo`), both
        followed transitively -- are kept, so dragging an unused module into a
        project-mode build doesn't bloat the ROM. With no `main()` (e.g. a
        library or a codegen-only test) every module is kept, since there is
        no single root to measure reachability from.

        Reachability deliberately follows references as well as imports (and
        over-approximates -- a module shadowed by a local of the same name is
        still kept) so that a module which is *referenced but not imported*
        survives to produce codegen's clear "does not import" diagnostic
        instead of being pruned into a dangling call. Stdlib imports are not
        program modules and never affect this.
        """
        by_name = {m.name: m for m in program.modules}
        # alias (last name segment) -> module name, for resolving `alias.foo`.
        aliases = {name.rsplit('.', 1)[-1]: name for name in by_name}
        roots = [m for m in program.modules
                 if any(isinstance(d, FunctionDecl) and d.name == 'main'
                        for d in m.declarations)]
        if len(roots) != 1:
            return program  # 0 roots: keep all; >1 is reported later in codegen

        reachable = set()
        queue = [roots[0].name]
        while queue:
            name = queue.pop()
            if name in reachable:
                continue
            reachable.add(name)
            module = by_name[name]
            for imp in module.imports:
                if imp.module_name in by_name:
                    queue.append(imp.module_name)
            for alias in self._referenced_aliases(module):
                if alias in aliases:
                    queue.append(aliases[alias])

        if len(reachable) == len(program.modules):
            return program
        dropped = sorted(set(by_name) - reachable)
        print("    Pruned unused module(s): %s" % ", ".join(dropped))
        # Preserve source order among the kept modules.
        return Program([m for m in program.modules if m.name in reachable])

    def _shake_declarations(self, program):
        """Drop module-level functions/vars unreachable from main() (opt-in).

        Whole-program compilation emits every declaration of every kept
        module into one C TU, and neither sdcc nor cc65 dead-strips inside a
        TU -- so an exported-but-unused function (and any module var only it
        touches) costs ROM, and under cc65's --static-locals also BSS, in
        every program linking the module (the lib/vm Lynx-budget problem).

        Reachability is per DECLARATION: from main(), a bare identifier
        reference keeps a same-module function/var, an `alias.member`
        reference keeps the aliased module's declaration, and ANY reference
        counts (a call, an address-taken callback, a const-table entry, a
        switch case label). Type declarations are always kept (typedefs /
        #defines: no ROM/RAM cost). Like _tree_shake this over-approximates
        (a local shadowing a module symbol still keeps it), so it can only
        keep too much -- and a missed reference shape would fail LOUDLY as an
        undefined C symbol, never silently drop live code.

        Opt-in (`[build] shake_exports` / compile_program(shake_exports=True))
        because dropping dead declarations changes the generated C and the
        golden tests pin default output byte-identical. With no single main()
        every declaration is kept, mirroring _tree_shake.
        """
        decls = {}  # module name -> {decl name: decl} (functions + vars)
        for m in program.modules:
            decls[m.name] = {d.name: d for d in m.declarations
                             if isinstance(d, (FunctionDecl, VarDecl))}
        aliases = {name.rsplit('.', 1)[-1]: name for name in decls}
        roots = [(m.name, 'main') for m in program.modules
                 if isinstance(decls[m.name].get('main'), FunctionDecl)]
        if len(roots) != 1:
            return program  # 0 roots: keep all; >1 is reported in codegen

        keep = set()
        queue = [roots[0]]
        # Consts no CODE references but a codegen budget pass READS by name:
        # the scenes transpiler's TS_MAX_TC bounds the Lynx background tile
        # table through a paint_table upload whose count is a runtime value.
        # Shaken, the table was sized to the shared tileset alone (39 against
        # a 91-tile per-scene tileset: the tiles past it were never uploaded).
        # A scalar const is a #define, so keeping it costs no byte.
        # The rooms generator's SPR_TILE_NEED / SPR_SLOT_NEED size the cc65
        # sprite table + slot pool the same way (gen_budgets).
        for m in program.modules:
            for name in ('TS_MAX_TC', 'SPR_TILE_NEED', 'SPR_SLOT_NEED'):
                d = decls[m.name].get(name)
                if isinstance(d, VarDecl) and d.is_const:
                    queue.append((m.name, name))
        while queue:
            key = queue.pop()
            if key in keep:
                continue
            keep.add(key)
            mod, name = key
            names, members = self._decl_refs(decls[mod][name])
            for n in names:
                if n in decls[mod]:
                    queue.append((mod, n))
            for alias, member in members:
                target = aliases.get(alias)
                if target is not None and member in decls[target]:
                    queue.append((target, member))

        dropped = {}  # module name -> [decl name, ...]
        new_modules = []
        for m in program.modules:
            kept = []
            for d in m.declarations:
                if (isinstance(d, (FunctionDecl, VarDecl))
                        and (m.name, d.name) not in keep):
                    dropped.setdefault(m.name, []).append(d.name)
                else:
                    kept.append(d)
            kept_names = {d.name for d in kept
                          if isinstance(d, (FunctionDecl, VarDecl))}
            exports = [e for e in m.exports
                       if e in kept_names or e not in decls[m.name]]
            new_modules.append(Module(m.name, m.imports, kept, exports))
        if not dropped:
            return program
        total = sum(len(v) for v in dropped.values())
        print("    Shook %d unused declaration(s): %s"
              % (total, ", ".join("%s (%d)" % (name, len(dropped[name]))
                                  for name in sorted(dropped))))
        return Program(new_modules)

    def _decl_refs(self, decl):
        """Every symbol reference anywhere under `decl`, over-approximate.

        Returns (bare_names, member_pairs): `bare_names` = each Identifier's
        name (same-module references; includes shadowed locals -- harmless),
        `member_pairs` = each `object.field` where object is an Identifier
        (cross-module `alias.member` references; struct-var field accesses
        land here too and simply match no module). Walks the AST generically
        over dataclass fields so every expression position -- including
        switch case labels and nested literals -- is covered.
        """
        names = set()
        members = set()

        def walk(node):
            if isinstance(node, Identifier):
                names.add(node.name)
            elif isinstance(node, FieldAccess):
                if isinstance(node.object, Identifier):
                    members.add((node.object.name, node.field))
            if isinstance(node, ASTNode):
                for f in dataclasses.fields(node):
                    walk(getattr(node, f.name))
            elif isinstance(node, (list, tuple)):
                for item in node:
                    walk(item)

        walk(decl)
        return names, members

    def _validate_module_names(self, program):
        """Validate module aliases across the *whole* authored program.

        Run before tree-shaking so these structural diagnostics don't depend
        on reachability: every module is referenced by the last segment of its
        name, which must not collide with a stdlib alias (video, input, hw,
        ...) or with another module's last segment. (Single-module programs
        keep plain C names and need no aliasing, so the check is skipped.)
        """
        if len(program.modules) < 2:
            return
        reserved = {key[0] for key in CodeGenerator.ALL_STDLIB_CALLS}
        seen = {}
        for module in program.modules:
            alias = module.name.rsplit('.', 1)[-1]
            if alias in reserved:
                raise RuntimeError(
                    'module "%s" would be referenced as "%s.*", which is '
                    'reserved for the standard library; rename the module'
                    % (module.name, alias))
            if alias in seen:
                raise RuntimeError(
                    'modules "%s" and "%s" would both be referenced as '
                    '"%s.*"; rename one of them'
                    % (seen[alias], module.name, alias))
            seen[alias] = module.name

    def _referenced_aliases(self, module) -> set:
        """Module aliases named as the object of a `alias.member` reference
        anywhere in `module`'s code (over-approximate: ignores shadowing)."""
        found = set()

        def walk_expr(expr):
            if isinstance(expr, FieldAccess):
                if isinstance(expr.object, Identifier):
                    found.add(expr.object.name)
                walk_expr(expr.object)
            elif isinstance(expr, BinaryOp):
                walk_expr(expr.left); walk_expr(expr.right)
            elif isinstance(expr, UnaryOp):
                walk_expr(expr.operand)
            elif isinstance(expr, FunctionCall):
                walk_expr(expr.function)
                for a in expr.arguments:
                    walk_expr(a)
            elif isinstance(expr, ArrayAccess):
                walk_expr(expr.array); walk_expr(expr.index)
            elif isinstance(expr, StructLiteral):
                for _name, value in expr.fields:
                    walk_expr(value)
            elif isinstance(expr, ArrayLiteral):
                for e in expr.elements:
                    walk_expr(e)

        def walk_stmts(stmts):
            for stmt in stmts or []:
                if isinstance(stmt, ExpressionStmt):
                    walk_expr(stmt.expression)
                elif isinstance(stmt, VarDeclStmt):
                    if stmt.var_decl.initializer is not None:
                        walk_expr(stmt.var_decl.initializer)
                elif isinstance(stmt, IfStmt):
                    walk_expr(stmt.condition)
                    walk_stmts(stmt.then_body); walk_stmts(stmt.else_body)
                elif isinstance(stmt, (LoopStmt, WhileStmt)):
                    if isinstance(stmt, WhileStmt):
                        walk_expr(stmt.condition)
                    walk_stmts(stmt.body)
                elif isinstance(stmt, SwitchStmt):
                    walk_expr(stmt.subject)
                    for labels, body in stmt.cases:
                        # THE LABELS TOO. Skipping them made this walker
                        # disagree with the one in gen_fnptr, and a module
                        # referenced ONLY from a `case other.CONST` was pruned
                        # as unreferenced - after which the label emitted
                        # `case codes.RED:` verbatim into the C, with no
                        # mosaik diagnostic at all, where the same reference
                        # in an expression gives the clear "does not import"
                        # error. Pinned by walker_coverage_test.py.
                        for label in labels:
                            walk_expr(label)
                        walk_stmts(body)
                    walk_stmts(stmt.default_body)
                elif isinstance(stmt, ForStmt):
                    walk_expr(stmt.start); walk_expr(stmt.end)
                    walk_stmts(stmt.body)
                elif isinstance(stmt, ReturnStmt):
                    if stmt.value is not None:
                        walk_expr(stmt.value)

        for decl in module.declarations:
            if isinstance(decl, FunctionDecl):
                walk_stmts(decl.body)
            elif isinstance(decl, VarDecl) and decl.initializer is not None:
                walk_expr(decl.initializer)
        return found

    def _check_imports(self, program, module_files):
        """Every import must name a stdlib module or a module in the build."""
        stdlib_names = stdlib_module_names()
        for module in program.modules:
            for imp in module.imports:
                if (imp.module_name in stdlib_names
                        or imp.module_name in module_files):
                    continue
                raise RuntimeError(
                    'module "%s" (%s) imports unknown module "%s" -- not a '
                    'stdlib module and not defined by any compiled source '
                    '(include its .mos file in the build)'
                    % (module.name, module_files[module.name],
                       imp.module_name))

    def compile_file(self, filename: str) -> str:
        """Compile a mosaik file."""
        with open(filename, 'r') as f:
            source_code = f.read()
        return self.compile(source_code)
