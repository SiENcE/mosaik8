"""CodeGenerator module + top-level C emission, mixed into generator.CodeGenerator.

Split out of the former monolithic generator.py; a plain mixin class composed
into CodeGenerator - methods reference sibling methods/attributes via self."""
import dataclasses
import re

from ..ast_nodes import *  # noqa: F401,F403
from ..platforms import (PLATFORM_CAPS, canonical_platform,
                         framework_for_platform, platform_caps)


class EmitModulesMixin:
    def _collect_modules(self, program):
        """Build the cross-module symbol table and pick the C naming scheme.

        With more than one module in the program, every module-level symbol
        is emitted as `<module>_<name>` in C (the entry function `main`
        excepted) so modules cannot collide; references through an imported
        module's alias resolve to those names and are checked against the
        exporting module's `export` list. A single-module program keeps
        plain C names (identical output to previous releases).
        """
        self.multi_module = len(program.modules) > 1
        self.module_symbols = {}
        self.module_aliases = {}
        stdlib_aliases = {key[0] for key in self.ALL_STDLIB_CALLS}
        main_module = None
        for module in program.modules:
            functions = {d.name for d in module.declarations
                         if isinstance(d, FunctionDecl)}
            variables = {d.name for d in module.declarations
                         if isinstance(d, VarDecl)}
            self.module_symbols[module.name] = {
                'prefix': re.sub(r'\W+', '_', module.name) + '_',
                'functions': functions,
                'symbols': functions | variables,
                'exports': set(module.exports),
            }
            if not self.multi_module:
                continue
            if 'main' in functions:
                if main_module is not None:
                    raise RuntimeError(
                        'both module "%s" and module "%s" define main(); a '
                        'program has exactly one entry point'
                        % (main_module, module.name))
                main_module = module.name
            alias = module.name.rsplit('.', 1)[-1]
            if alias in stdlib_aliases:
                raise RuntimeError(
                    'module "%s" would be referenced as "%s.*", which is '
                    'reserved for the standard library; rename the module'
                    % (module.name, alias))
            if alias in self.module_aliases:
                raise RuntimeError(
                    'modules "%s" and "%s" would both be referenced as '
                    '"%s.*"; rename one of them'
                    % (self.module_aliases[alias], module.name, alias))
            self.module_aliases[alias] = module.name
        # The `<module>_<name>` mangling is ambiguous (module `a` symbol
        # `b_c` and module `a_b` symbol `c` both mangle to `a_b_c`), which
        # would surface as a confusing C redefinition error. Detect the
        # collision here with a source-level message instead.
        if self.multi_module:
            mangled_owner = {}
            for module in program.modules:
                for name in sorted(self.module_symbols[module.name]['symbols']):
                    c_name = self._mangled(module.name, name)
                    other = mangled_owner.get(c_name)
                    if other is not None and other != (module.name, name):
                        raise RuntimeError(
                            "'%s' of module \"%s\" and '%s' of module \"%s\" "
                            "would share the C name '%s'; rename one of them"
                            % (other[1], other[0], name, module.name, c_name))
                    mangled_owner[c_name] = (module.name, name)

    def _enter_module(self, module):
        """Make `module` the current context for symbol resolution."""
        # For a codegen failure's location (review L-9): the same
        # `<file>:<line>:` shape the type checker prints.
        self._cur_file = getattr(module, 'source_file', None) or module.name
        self._cur_line = None
        self.current_module = module.name
        self.current_symbols = self.module_symbols[module.name]['symbols']
        self.current_imports = {}
        self.local_names = set()
        for imp in module.imports:
            if imp.module_name in self.module_symbols:
                alias = imp.module_name.rsplit('.', 1)[-1]
                self.current_imports[alias] = imp.module_name

    def _mangled(self, module_name, name) -> str:
        """The C name of module-level symbol `name` of `module_name`."""
        info = self.module_symbols.get(module_name)
        if not self.multi_module or info is None:
            return name
        if name == 'main' and name in info['functions']:
            return name  # the program entry point keeps its C name
        full = info['prefix'] + name
        if self.framework == 'cc65' and len(full) > self.CC65_NAME_MAX:
            # cc65 keeps only 64 characters of an identifier (IDENTSIZE), so two
            # long names that differ past it COLLIDE: a reference-engine conversion's
            # `scripts_ENTRY_..._on_update` and `..._on_update_2` were one macro
            # ("Macro redefinition is not identical") and the PCE build failed.
            # Shortened to a prefix + a hash of the whole name, with room left
            # for the banked suffixes appended later (`__bimpl__bk`, 11).
            import hashlib
            digest = hashlib.sha1(full.encode('utf-8')).hexdigest()[:8]
            return full[:self.CC65_NAME_MAX - 9] + '_' + digest
        return full

    #: The longest module-symbol C name the cc65 backend emits as is (63
    #: significant characters, less the longest suffix a banked function's
    #: thunk and body add to it).
    CC65_NAME_MAX = 52

    def _module_member(self, alias, member):
        """Resolve `alias.member` against the imported program modules.

        Returns the mangled C name when `alias` names an imported module and
        `member` is exported by it; returns None when `alias` is not a module
        reference at all (e.g. a struct variable, handled by the caller); and
        raises a clear error for module references that cannot resolve
        (not imported / unknown member / not exported).
        """
        if not self.multi_module:
            return None
        if alias in self.local_names or alias in self.current_symbols:
            return None  # shadowed by a variable; ordinary member access
        target = self.current_imports.get(alias)
        if target is None:
            owner = self.module_aliases.get(alias)
            if owner == self.current_module:
                # A module may refer to its own members qualified; no export
                # check against yourself.
                if member in self.current_symbols:
                    return self._mangled(owner, member)
                raise RuntimeError(
                    'module "%s" has no module-level symbol "%s"'
                    % (owner, member))
            if owner is not None:
                raise RuntimeError(
                    'module "%s" uses "%s.%s" but does not import "%s" '
                    '(add `import "%s"`)'
                    % (self.current_module, alias, member, owner, owner))
            return None
        info = self.module_symbols[target]
        if member not in info['symbols']:
            raise RuntimeError(
                'module "%s" has no module-level symbol "%s" '
                '(referenced from module "%s")'
                % (target, member, self.current_module))
        if member not in info['exports']:
            raise RuntimeError(
                '"%s" is not exported by module "%s" (add it to the export '
                'list to use it from module "%s")'
                % (member, target, self.current_module))
        return self._mangled(target, member)

    def emit(self, line: str = ""):
        self.output.append(line)

    def _emit_palette_set_real(self):
        """palette.load_bkg_set / load_sprite_set, the working bodies.

        Backend-neutral: both preludes define gbs_rgb + the single-slot
        setters with the same signatures, so the loop is written once here and
        each backend calls it from its own palette block (where those helpers
        are already in scope).

        NOTE the colour ENCODING. `palette.load_bkg` / `load_sprite` take
        NATIVE colour words (what `palette.rgb` and the codegen's baked
        `<name>_palette` produce). These two take PORTABLE 5-5-5 RGB words, the
        same encoding as `palette.load_bkg16` and for the same reason: a
        per-scene palette table is GENERATED data in a target-neutral
        `scenes.mos`, which cannot bake per-console words, so the rounding to
        the target's real depth happens here at runtime through gbs_rgb.

        The Game Boy Color takes a DIRECT path: its hardware loads a RUN of
        palettes in one call, so the whole set goes through one `set_bkg_palette`
        instead of eight four-argument calls. That is not a micro-optimisation -
        every byte of a prelude helper is RESIDENT (it cannot bank), and the
        eight-call form is what took the reference-engine import's CGB build past the
        resident image."""
        self.emit("/* palette.load_bkg_set / load_sprite_set: COUNT consecutive 4-colour")
        self.emit("   slots out of one table, starting at WORD index `off`. Colours are")
        self.emit("   portable 5-5-5 RGB (like load_bkg16), rounded to this console's")
        self.emit("   depth. */")
        if self._palette_set_direct():
            # 5-5-5 RGB -> the CGB's own BGR555: the same five-bit fields in
            # the opposite order, so this is a swap and not a re-quantisation
            # (gbs_rgb would scale up to 8 bits and straight back down).
            self.emit("static uint16_t gbs_pal_word(uint16_t c) {")
            self.emit("    return (uint16_t)(((c >> 10) & 0x1F) | (c & 0x03E0)")
            self.emit("                      | ((c & 0x1F) << 10));")
            self.emit("}")
            self.emit("static palette_color_t gbs_pal_buf[32];  /* 8 slots x 4 */")
            # ONE conversion loop behind two thin entry points: the layers
            # differ only in which hardware setter takes the finished run, and
            # every byte here is resident.
            self.emit("static void gbs_pal_set(uint8_t spr, uint8_t slot, uint8_t count,")
            self.emit("                        const uint16_t *colors, uint16_t off) {")
            self.emit("    uint8_t i, n;")
            self.emit("    if (count > 8) count = 8;")
            self.emit("    n = (uint8_t)(count * 4);")
            self.emit("    for (i = 0; i < n; ++i)")
            self.emit("        gbs_pal_buf[i] = gbs_pal_word(colors[off + i]);")
            if self.palette_fade_used:
                # THROUGH the fade (see _emit_cgb_fade): a room loading its
                # palettes while the screen is dark must be written dimmed, or
                # the load undoes the black-out load_room paints under.
                self.emit("    gbs_pal_hw(spr, slot, count, gbs_pal_buf);")
            else:
                self.emit("    slot &= 7;")
                self.emit("    if (spr) set_sprite_palette(slot, count, gbs_pal_buf);")
                self.emit("    else set_bkg_palette(slot, count, gbs_pal_buf);")
            self.emit("}")
            for name, spr in (("gbs_load_bkg_set", 0), ("gbs_load_spr_set", 1)):
                self.emit("void %s(uint8_t slot, uint8_t count," % name)
                self.emit("%s const uint16_t *colors, uint16_t off) {"
                          % (" " * len(name)))
                self.emit("    gbs_pal_set(%d, slot, count, colors, off);" % spr)
                self.emit("}")
            return
        self.emit("static uint16_t gbs_pal_word(uint16_t c) {")
        self.emit("    return gbs_rgb((uint8_t)(((c >> 10) & 0x1F) << 3),")
        self.emit("                   (uint8_t)(((c >> 5) & 0x1F) << 3),")
        self.emit("                   (uint8_t)((c & 0x1F) << 3));")
        self.emit("}")
        for name, setter in (("gbs_load_bkg_set", "gbs_set_bkg_palette"),
                             ("gbs_load_spr_set", "gbs_set_spr_palette")):
            self.emit("void %s(uint8_t slot, uint8_t count," % name)
            self.emit("%s const uint16_t *colors, uint16_t off) {" % (" " * len(name)))
            self.emit("    uint8_t i;")
            self.emit("    const uint16_t *p;")
            self.emit("    for (i = 0; i < count; ++i) {")
            self.emit("        p = colors + off + (uint16_t)i * 4;")
            self.emit("        %s((uint8_t)(slot + i), gbs_pal_word(p[0]),"
                      % setter)
            self.emit("            gbs_pal_word(p[1]), gbs_pal_word(p[2]),")
            self.emit("            gbs_pal_word(p[3]));")
            self.emit("    }")
            self.emit("}")

    def _palette_set_direct(self):
        """True where load_bkg_set / load_sprite_set drive the hardware
        directly instead of looping the single-slot setters.

        Only the Game Boy Color: its GBDK setters take a palette COUNT, and it
        has no DMG-register mirror to keep in step (the Analogue Pocket does,
        so it stays on the generic path)."""
        return self.platform == 'gameboy_color'

    def _emit_prelude(self):
        if self.framework == 'cc65':
            self._emit_prelude_cc65()
        else:
            self._emit_prelude_gbdk()

    # Portable one-shot sound effects (platform.sound sound.sfx). A small
    # fixed bank of (frequency, duration) one-shots over the single square-wave
    # beep channel -- the same SFX_* ids on every console, replacing per-game
    # named SFX banks. Real beep where there's a tone generator (everywhere),
    # so it is portable like sound.beep. Emitted only when sound.sfx is used.
    SFX_IDS = [
        ('SFX_COIN', 1200, 6),    # bright pickup / positive
        ('SFX_HURT', 180, 18),    # low buzz / negative
        ('SFX_JUMP', 700, 6),     # short hop
        ('SFX_POINT', 1500, 3),   # score tick
        ('SFX_SELECT', 500, 8),   # menu / confirm
    ]

    def _emit_sound_sfx(self):
        n = len(self.SFX_IDS)
        for i, (name, _f, _d) in enumerate(self.SFX_IDS):
            self.emit("#define %s %d" % (name, i))
        self.emit("static const uint16_t gbs_sfx_freq[%d] = { %s };"
                  % (n, ", ".join(str(f) for _n, f, _d in self.SFX_IDS)))
        self.emit("static const uint8_t gbs_sfx_frames[%d] = { %s };"
                  % (n, ", ".join(str(d) for _n, _f, d in self.SFX_IDS)))
        self.emit("void gbs_sound_sfx(uint8_t id) {")
        self.emit("    if (id < %d) gbs_sound_beep(gbs_sfx_freq[id], gbs_sfx_frames[id]);" % n)
        self.emit("}")

    def _emit_assets(self):
        """Emit asset-pipeline tile data into the translation unit.

        The data is GB 2bpp on every console: GBDK's `set_sprite_data` takes
        it natively on the GB family, converts to CHR layout on NES, and
        expands 2bpp->4bpp through the compat layer on SMS/Game Gear; the cc65
        Lynx engine converts it to Suzy literal sprites at runtime. One format,
        all consoles.
        """
        if not self.assets:
            return
        fmt = "4bpp packed-nibble" if self.sprite_src_bpp == 4 else "GB 2bpp"
        self.emit("/* --- Assets (PNG -> %s tiles via the asset pipeline) --- */" % fmt)
        tile_size = 32 if self.sprite_src_bpp == 4 else 16
        total_tiles = 0
        for name, data, _bpp in self.assets:
            count = len(data) // tile_size
            total_tiles += count
            self.emit("#define %s_tile_count %d" % (name, count))
            sym = "%s_tiles" % name
            if sym in self.sheet_stream:
                # Streamed from the Lynx cart (`[world] stream`): its bytes are
                # in the archive and only gbs_spr_data_stream reads them.
                self.emit("/* %s: %d bytes in the cart archive (block %d) */"
                          % (sym, len(data), self.sheet_stream[sym]))
                continue
            if sym in self.asset_far_bank:
                # In a DATA bank, uploaded through gbs_spr_data_far (see
                # _collect_bank_local_consts). The definition travels with
                # the bank TU; every other TU gets the view. (cc65: the one
                # TU defines it at the end, in the bank's segment.)
                self.emit("extern const uint8_t %s[%d];" % (sym, len(data)))
                continue
            if sym in self._bank_local_consts:
                # Code banking co-located this sheet in a ROM bank (every read
                # comes from that bank's code -- see _collect_bank_local_consts).
                # The main TU only needs the view; cc65 has no bank TU, so the
                # definition goes here, in the bank's segment.
                if self._cc65_banking:
                    self._emit_cc65_bank_const(
                        self._bank_local_consts[sym],
                        self._asset_bank_def(sym))
                else:
                    self.emit("extern const uint8_t %s[%d];" % (sym, len(data)))
                continue
            self.emit("const uint8_t %s[%d] = {" % (sym, len(data)))
            for i in range(0, len(data), 16):
                self.emit("    " + " ".join(
                    "0x%02X," % b for b in data[i:i + 16]))
            self.emit("};")
        # Named-sprite sheet defines: each sub-sprite's first tile index + its
        # size in tiles, for sprite.set_meta(slot, <s>_tile, <s>_w, <s>_h).
        if self.asset_sprites:
            self.emit("/* Named sprites (sheet manifest): <s>_tile / _w / _h. */")
            for sname, offset, wt, ht in self.asset_sprites:
                self.emit("#define %s_tile %d" % (sname, offset))
                self.emit("#define %s_w %d" % (sname, wt))
                self.emit("#define %s_h %d" % (sname, ht))
        # 16-colour authored palettes of 4bpp assets, as native colour words --
        # load into the hardware pens with palette.load_sprite16 so the Lynx's
        # 16-colour sprites show their real colours. Emitted whenever the
        # program calls load_sprite16 (even on 2bpp consoles, where the call is
        # a no-op) so `<name>_palette16` always resolves -- one portable source.
        if self.asset_palettes16 and (self.sprite_src_bpp == 4
                                      or self.load_sprite16_used):
            self.emit("/* Authored 16-colour palettes of 4bpp assets (native words). */")
            for name, colors in self.asset_palettes16:
                entries = ", ".join(self._native_color_expr(*rgb)
                                    for rgb in colors)
                self.emit("const uint16_t %s_palette16[16] = { %s };"
                          % (name, entries))
        if self.asset_palettes and self.palette_imported:
            self.emit("/* Authored palettes of indexed-PNG assets, converted to the native")
            self.emit("   color format at build time; load with palette.load_bkg/_sprite. */")
            for name, colors in self.asset_palettes:
                entries = ", ".join(self._native_color_expr(*rgb)
                                    for rgb in colors)
                self.emit("const uint16_t %s_palette[4] = { %s };"
                          % (name, entries))
        self.emit("")
        if (self.framework == 'cc65' and self.caps['has_sprites']
                and total_tiles > self.CC65_MAX_TILES):
            print("    Warning: assets define %d tiles but the %s sprite "
                  "engine holds %d (GBS_MAX_TILES); tiles beyond that are "
                  "dropped by sprite.set_data"
                  % (total_tiles, self.platform, self.CC65_MAX_TILES))

    def _emit_prototype(self, func):
        """One function prototype. cc65 banking wraps a banked function's in
        the wrapped-call hook (cc65_bank); everything else is the plain line."""
        if self.framework == 'cc65':
            self._emit_cc65_prototype(func)
        else:
            self.emit(self._function_signature(func) + ";")

    def _emit_module(self, module):
        """Single-module emission (plain C names), the layout used since the
        first release. Multi-module programs go through _emit_module_types/
        _emit_module_data/_emit_module_functions instead (see generate())."""
        self._enter_module(module)
        self.emit("/* Module: %s */" % module.name)
        self.emit("")

        consts = [d for d in module.declarations
                  if isinstance(d, VarDecl) and d.is_const]
        global_vars = [d for d in module.declarations
                       if isinstance(d, VarDecl) and not d.is_const]
        type_decls = [d for d in module.declarations if isinstance(d, TypeDecl)]
        functions = [d for d in module.declarations if isinstance(d, FunctionDecl)]

        # Enum + const values -> #define so they are usable everywhere.
        for decl in type_decls:
            if isinstance(decl.type_def, EnumType):
                self._emit_enum(decl)
        for const in consts:
            # Streamed asset / bytecode blob: its bytes leave the resident image
            # (single-module).
            if self._streaming and (const.name in self.streamed
                                    or const.name == self.streamed_code_sym
                                    or const.name == self.streamed_code2_sym
                                    or const.name == self.streamed_code3_sym):
                self._emit_streamed_const_extern(const, const.name)
                continue
            # Bank-local const (code banking): defined in the code bank TU,
            # only banked code reads it -- extern here (cc65: defined here, in
            # the bank's segment of the one TU).
            if const.name in self._bank_local_consts:
                if self._cc65_banking:
                    self._emit_cc65_bank_const(
                        self._bank_local_consts[const.name],
                        "const " + self._format_var_decl(const) + ";")
                    continue
                var_type = const.type or self._infer_decl_type(const.initializer)
                self.emit("extern const "
                          + self._format_decl(var_type, const.name) + ";")
                continue
            # Array consts hold aggregate data that cannot live in a #define;
            # emit them as real C `const` arrays instead.
            if (isinstance(const.type, ArrayType) or
                    isinstance(const.initializer, ArrayLiteral)):
                self.emit("const " + self._format_var_decl(const) + ";")
            else:
                value = self.gen_expression(const.initializer) if const.initializer else "0"
                self.emit("#define %s (%s)" % (const.name, value))
        if consts or any(isinstance(d.type_def, EnumType) for d in type_decls):
            self.emit("")

        # Struct typedefs.
        for decl in type_decls:
            if isinstance(decl.type_def, StructType):
                self._emit_struct(decl)

        # Global variables.
        for var in global_vars:
            self.emit(self._format_var_decl(var) + ";")
        if global_vars:
            self.emit("")

        # Forward declarations so call order does not matter (banked
        # functions keep their prototype here; their bodies go to bank TUs).
        for func in functions:
            self._emit_prototype(func)
        if functions:
            self.emit("")

        # Function definitions.
        for func in functions:
            if not self._in_bank_unit(func):
                self._emit_function(func)

    # -- multi-module emission (whole-program builds) ------------------------

    def _emit_module_types(self, module):
        """Enums and struct typedefs. Type/enum-variant names are
        program-global (never mangled); duplicates were rejected up front."""
        type_decls = [d for d in module.declarations if isinstance(d, TypeDecl)]
        enums = [d for d in type_decls if isinstance(d.type_def, EnumType)]
        for decl in enums:
            self._emit_enum(decl)
        if enums:
            self.emit("")
        for decl in type_decls:
            if isinstance(decl.type_def, StructType):
                self._emit_struct(decl)

    def _emit_module_data(self, module):
        """Consts, globals and function prototypes, under mangled C names.
        Emitted for every module before any function body, so cross-module
        references are declared before use regardless of module order."""
        consts = [d for d in module.declarations
                  if isinstance(d, VarDecl) and d.is_const]
        global_vars = [d for d in module.declarations
                       if isinstance(d, VarDecl) and not d.is_const]
        functions = [d for d in module.declarations
                     if isinstance(d, FunctionDecl)]

        for const in consts:
            c_name = self._mangled(module.name, const.name)
            # Streamed asset / bytecode blob: its bytes leave the resident image.
            if self._streaming and (c_name in self.streamed
                                    or c_name == self.streamed_code_sym
                                    or c_name == self.streamed_code2_sym
                                    or c_name == self.streamed_code3_sym):
                self._emit_streamed_const_extern(const, c_name)
                continue
            # Bank-local const (code banking): defined in the code bank TU,
            # only banked code reads it -- extern here (cc65: defined here, in
            # the bank's segment of the one TU).
            if c_name in self._bank_local_consts:
                if self._cc65_banking:
                    self._emit_cc65_bank_const(
                        self._bank_local_consts[c_name],
                        "const " + self._format_var_decl(const, c_name) + ";")
                    continue
                var_type = const.type or self._infer_decl_type(const.initializer)
                self.emit("extern const "
                          + self._format_decl(var_type, c_name) + ";")
                continue
            if (isinstance(const.type, ArrayType) or
                    isinstance(const.initializer, ArrayLiteral)):
                self.emit("const " + self._format_var_decl(const, c_name) + ";")
            else:
                value = self.gen_expression(const.initializer) if const.initializer else "0"
                self.emit("#define %s (%s)" % (c_name, value))
        for var in global_vars:
            self.emit(self._format_var_decl(
                var, self._mangled(module.name, var.name)) + ";")
        for func in functions:
            self._emit_prototype(func)
        self.emit("")
