"""Shared code generator (backend-agnostic). Backend specifics are
mixed in from gbdk.GbdkBackend and cc65.Cc65Backend."""

import re

from ..ast_nodes import *  # noqa: F401,F403
from ..platforms import (PLATFORM_CAPS, canonical_platform,
                         framework_for_platform, platform_caps)
from .gbdk import GbdkBackend
from .cc65 import Cc65Backend


class CodeGenerator(GbdkBackend, Cc65Backend):
    """Code generator that emits GBDK C *or* cc65 C for the target console.

    The backend (prelude + stdlib lowering) is selected in generate() from
    the console's framework; backend-specific emitters and call maps come
    from the GbdkBackend / Cc65Backend mixins.
    """

    # mosaik primitive types -> C types.
    PRIMITIVE_C_TYPES = {
        'u8': 'uint8_t',
        'i8': 'int8_t',
        'u16': 'uint16_t',
        'i16': 'int16_t',
        'bool': 'uint8_t',
        'addr': 'uint16_t',
        'void': 'void',
    }

    # Every (module, func) pair known to any backend. Used to tell
    # "unsupported on this target" (a clear compile error) apart from an
    # ordinary cross-module call that lowers to `module_func(...)`.
    ALL_STDLIB_CALLS = (set(GbdkBackend.STDLIB_CALLS_GBDK)
                        | set(Cc65Backend.STDLIB_CALLS_CC65_CORE)
                        | set(Cc65Backend.STDLIB_CALLS_CC65_DRAW)
                        | set(Cc65Backend.STDLIB_CALLS_CC65_SPRITE)
                        | set(Cc65Backend.STDLIB_CALLS_CC65_BKG))

    # Stdlib calls gated by a PLATFORM_CAPS capability. On any backend, calling
    # one of these on a console whose registry entry lacks the capability is
    # the clear "not supported on target" compile error from _gen_call, not a
    # link failure on an undefined symbol.
    CALLS_NEEDING_WINDOW = {('window', 'set_tiles'), ('window', 'move'),
                            ('video', 'show_window'), ('video', 'hide_window')}
    CALLS_NEEDING_BKG = {('bkg', 'set_data'), ('bkg', 'set_tiles'),
                         ('bkg', 'scroll'), ('bkg', 'move')}
    CALLS_NEEDING_SPRITES = {('sprite', 'set_data'), ('sprite', 'set_tile'),
                             ('sprite', 'get_tile'), ('sprite', 'set_prop'),
                             ('sprite', 'move'), ('video', 'show_sprites'),
                             ('video', 'hide_sprites')}
    CALLS_NEEDING_SOUND = {('sound', 'beep'), ('sound', 'stop')}

    # Game Boy hardware-register constants. Emitted as prelude #defines only on
    # has_gb_regs consoles; referencing one anywhere else is a clear compile
    # error (the addresses are meaningless on other machines).
    GB_REG_CONSTANTS = {'REG_DIV', 'REG_NR10', 'REG_BGP', 'REG_OBP0', 'REG_OBP1'}

    BINARY_C_OPERATORS = {
        '+': '+', '-': '-', '*': '*', '/': '/', '%': '%',
        '==': '==', '!=': '!=', '<': '<', '>': '>', '<=': '<=', '>=': '>=',
        'and': '&&', 'or': '||',
    }

    def __init__(self):
        self.output = []
        self.platform = 'gameboy'
        self.framework = 'gbdk'
        self.caps = PLATFORM_CAPS['gameboy']
        self.cc65_profile = None
        self.cc65_bkg_imported = False
        self.stdlib_calls = self.STDLIB_CALLS_GBDK
        self.assets = []         # [(name, gb_2bpp_bytes)] from the asset pipeline
        self.struct_types = {}   # name -> StructType
        self.enum_types = set()  # names of enum types
        # Cross-module linking state (see _collect_modules).
        self.multi_module = False
        self.module_symbols = {}   # module name -> symbol-table dict
        self.module_aliases = {}   # alias (last name segment) -> module name
        self.current_module = None
        self.current_symbols = set()  # module-level names of current module
        self.current_imports = {}     # alias -> imported program-module name
        self.local_names = set()      # params/locals of the current function

    # -- top level ---------------------------------------------------------

    def generate(self, program) -> str:
        self.output = []
        self.struct_types = {}
        self.enum_types = set()

        # Pick the backend (prelude + stdlib lowering) for the target console.
        # The PLATFORM_CAPS registry decides which stdlib calls exist here.
        self.framework = framework_for_platform(self.platform)
        self.caps = platform_caps(self.platform)
        if self.framework == 'cc65':
            self.cc65_profile = self.CC65_PROFILES.get(
                canonical_platform(self.platform), self.CC65_PROFILES['lynx'])
            stdlib = dict(self.STDLIB_CALLS_CC65_CORE)
            if self.caps['has_draw']:
                stdlib.update(self.STDLIB_CALLS_CC65_DRAW)
            if self.caps['has_sprites']:
                stdlib.update(self.STDLIB_CALLS_CC65_SPRITE)
            if self.caps['has_bkg']:
                stdlib.update(self.STDLIB_CALLS_CC65_BKG)
            # The Lynx bkg engine costs ~21 KB of RAM (the composited
            # 256x256 background sprite + the bkg tile table), so the cc65
            # preludes emit the bkg engine only for programs that import
            # graphics.bkg -- everything else keeps its memory map (and its
            # golden snapshot) unchanged.
            self.cc65_bkg_imported = any(
                imp.module_name == 'graphics.bkg'
                for module in program.modules for imp in module.imports)
        else:
            self.cc65_profile = None
            self.cc65_bkg_imported = False
            stdlib = dict(self.STDLIB_CALLS_GBDK)
        # Drop capability-gated calls the target lacks so they raise the clear
        # unsupported-on-target diagnostic instead of failing at link time.
        for cap, calls in (('has_window', self.CALLS_NEEDING_WINDOW),
                           ('has_bkg', self.CALLS_NEEDING_BKG),
                           ('has_sprites', self.CALLS_NEEDING_SPRITES),
                           ('has_sound', self.CALLS_NEEDING_SOUND)):
            if not self.caps[cap]:
                for key in calls:
                    stdlib.pop(key, None)
        self.stdlib_calls = stdlib

        # Cross-module symbol tables + C name mangling scheme.
        self._collect_modules(program)

        # Discover user-defined types up front so they can be referenced
        # regardless of declaration order. Type and enum names are
        # program-global (they are never mangled), so in a multi-module build
        # two modules must not declare the same one.
        for module in program.modules:
            for decl in module.declarations:
                if isinstance(decl, TypeDecl):
                    if self.multi_module and (decl.name in self.struct_types
                                              or decl.name in self.enum_types):
                        raise RuntimeError(
                            "type '%s' (module \"%s\") is already defined by "
                            "another module; type names are program-global"
                            % (decl.name, module.name))
                    if isinstance(decl.type_def, StructType):
                        self.struct_types[decl.name] = decl.type_def
                    elif isinstance(decl.type_def, EnumType):
                        self.enum_types.add(decl.name)

        self._emit_prelude()
        self._emit_assets()

        if not self.multi_module:
            for module in program.modules:
                self._emit_module(module)
        else:
            # Whole-program layout: every module's types, then every module's
            # data (consts/globals) and function prototypes, then all function
            # bodies -- so cross-module references are always declared before
            # use regardless of module order.
            for module in program.modules:
                self._enter_module(module)
                self._emit_module_types(module)
            for module in program.modules:
                self._enter_module(module)
                self.emit("/* Module: %s */" % module.name)
                self.emit("")
                self._emit_module_data(module)
            for module in program.modules:
                self._enter_module(module)
                self._emit_module_functions(module)

        return "\n".join(self.output)

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

    def _enter_module(self, module):
        """Make `module` the current context for symbol resolution."""
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
        return info['prefix'] + name

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

    def _emit_prelude(self):
        if self.framework == 'cc65':
            self._emit_prelude_cc65()
        else:
            self._emit_prelude_gbdk()

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
        self.emit("/* --- Assets (PNG -> GB 2bpp tiles via the asset pipeline) --- */")
        total_tiles = 0
        for name, data in self.assets:
            count = len(data) // 16
            total_tiles += count
            self.emit("#define %s_tile_count %d" % (name, count))
            self.emit("const uint8_t %s_tiles[%d] = {" % (name, len(data)))
            for i in range(0, len(data), 16):
                self.emit("    " + " ".join(
                    "0x%02X," % b for b in data[i:i + 16]))
            self.emit("};")
        self.emit("")
        if (self.framework == 'cc65' and self.caps['has_sprites']
                and total_tiles > self.CC65_MAX_TILES):
            print("    Warning: assets define %d tiles but the %s sprite "
                  "engine holds %d (GBS_MAX_TILES); tiles beyond that are "
                  "dropped by sprite.set_data"
                  % (total_tiles, self.platform, self.CC65_MAX_TILES))

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

        # Forward declarations so call order does not matter.
        for func in functions:
            self.emit(self._function_signature(func) + ";")
        if functions:
            self.emit("")

        # Function definitions.
        for func in functions:
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
            self.emit(self._function_signature(func) + ";")
        self.emit("")

    def _emit_module_functions(self, module):
        functions = [d for d in module.declarations
                     if isinstance(d, FunctionDecl)]
        if not functions:
            return
        self.emit("/* Module: %s -- functions */" % module.name)
        self.emit("")
        for func in functions:
            self._emit_function(func)

    def _emit_enum(self, decl):
        next_value = 0
        for variant in decl.type_def.variants:
            value = variant.value if variant.value is not None else next_value
            self.emit("#define %s %d" % (variant.name, value))
            next_value = value + 1

    def _emit_struct(self, decl):
        self.emit("typedef struct {")
        for field in decl.type_def.fields:
            self.emit("    %s;" % self._format_decl(field.type, field.name))
        self.emit("} %s;" % decl.name)
        self.emit("")

    # -- type formatting ---------------------------------------------------

    def c_type(self, type_obj) -> str:
        if isinstance(type_obj, PrimitiveType):
            return self.PRIMITIVE_C_TYPES.get(type_obj.name, 'uint8_t')
        if isinstance(type_obj, UserDefinedType):
            if type_obj.name in self.enum_types:
                return 'uint8_t'
            return type_obj.name
        if isinstance(type_obj, ArrayType):
            return self.c_type(type_obj.element_type)
        if type_obj is None:
            return 'uint8_t'
        return 'uint8_t'

    def _format_decl(self, type_obj, name) -> str:
        """Build a C declarator like `uint8_t name` or `Position name[4]`."""
        if isinstance(type_obj, ArrayType):
            size = type_obj.size if type_obj.size is not None else ''
            return "%s %s[%s]" % (self.c_type(type_obj.element_type), name, size)
        return "%s %s" % (self.c_type(type_obj), name)

    def _format_var_decl(self, var, c_name: str = None) -> str:
        var_type = var.type
        if var_type is None:
            # Fall back to a sensible width when the type is omitted.
            var_type = self._infer_decl_type(var.initializer)
        decl = self._format_decl(var_type, c_name or var.name)
        if var.initializer is not None:
            decl += " = " + self.gen_expression(var.initializer)
        return decl

    def _infer_decl_type(self, initializer):
        if isinstance(initializer, ArrayLiteral):
            return ArrayType(PrimitiveType('u8'), len(initializer.elements))
        if isinstance(initializer, Literal) and initializer.type == "number":
            if 0 <= initializer.value <= 255:
                return PrimitiveType('u8')
            return PrimitiveType('u16')
        return PrimitiveType('u8')

    def _function_signature(self, func) -> str:
        ret = self.c_type(func.return_type) if func.return_type else 'void'
        if func.parameters:
            params = ", ".join(self._format_decl(p.type, p.name) for p in func.parameters)
        else:
            params = "void"
        name = self._mangled(self.current_module, func.name)
        return "%s %s(%s)" % (ret, name, params)

    def _hoist_var_decls(self, stmts: list) -> list:
        """For C89 compliance (cc65): move all VarDeclStmt before non-declaration
        statements in a compound block. This is a safe transformation when
        initializers are simple constants (the common mosaik pattern)."""
        if self.framework != 'cc65':
            return stmts
        decls = [s for s in stmts if isinstance(s, VarDeclStmt)]
        rest  = [s for s in stmts if not isinstance(s, VarDeclStmt)]
        return decls + rest

    def _emit_function(self, func):
        # Parameters and locals shadow module-level symbols, so collect them
        # up front: a shadowed name must never be mangled (see gen_expression).
        self.local_names = {p.name for p in func.parameters}
        self._collect_locals(func.body, self.local_names)
        self.emit(self._function_signature(func) + " {")
        for stmt in self._hoist_var_decls(func.body):
            self.gen_statement(stmt, 1)
        self.emit("}")
        self.emit("")
        self.local_names = set()

    def _collect_locals(self, stmts, names):
        """Add every name declared anywhere inside a statement list."""
        for stmt in stmts:
            if isinstance(stmt, VarDeclStmt):
                names.add(stmt.var_decl.name)
            elif isinstance(stmt, IfStmt):
                self._collect_locals(stmt.then_body, names)
                if stmt.else_body:
                    self._collect_locals(stmt.else_body, names)
            elif isinstance(stmt, (LoopStmt, WhileStmt)):
                self._collect_locals(stmt.body, names)
            elif isinstance(stmt, ForStmt):
                names.add(stmt.var_name)
                self._collect_locals(stmt.body, names)
            elif isinstance(stmt, SwitchStmt):
                for _labels, body in stmt.cases:
                    self._collect_locals(body, names)
                if stmt.default_body:
                    self._collect_locals(stmt.default_body, names)

    # -- statements --------------------------------------------------------

    def gen_statement(self, stmt, indent):
        pad = "    " * indent
        if isinstance(stmt, ExpressionStmt):
            self._gen_expression_stmt(stmt.expression, indent)
        elif isinstance(stmt, VarDeclStmt):
            self.emit(pad + self._format_var_decl(stmt.var_decl) + ";")
        elif isinstance(stmt, IfStmt):
            self.emit(pad + "if (%s) {" % self.gen_expression(stmt.condition))
            for s in self._hoist_var_decls(stmt.then_body):
                self.gen_statement(s, indent + 1)
            if stmt.else_body:
                self.emit(pad + "} else {")
                for s in self._hoist_var_decls(stmt.else_body):
                    self.gen_statement(s, indent + 1)
            self.emit(pad + "}")
        elif isinstance(stmt, LoopStmt):
            self.emit(pad + "while (1) {")
            for s in self._hoist_var_decls(stmt.body):
                self.gen_statement(s, indent + 1)
            self.emit(pad + "}")
        elif isinstance(stmt, WhileStmt):
            self.emit(pad + "while (%s) {" % self.gen_expression(stmt.condition))
            for s in self._hoist_var_decls(stmt.body):
                self.gen_statement(s, indent + 1)
            self.emit(pad + "}")
        elif isinstance(stmt, SwitchStmt):
            self.emit(pad + "switch (%s) {" % self.gen_expression(stmt.subject))
            for labels, body in stmt.cases:
                for label in labels[:-1]:
                    self.emit(pad + "case %s:" % self.gen_expression(label))
                self.emit(pad + "case %s: {" % self.gen_expression(labels[-1]))
                for s in body:
                    self.gen_statement(s, indent + 1)
                self.emit(pad + "} break;")
            if stmt.default_body is not None:
                self.emit(pad + "default: {")
                for s in stmt.default_body:
                    self.gen_statement(s, indent + 1)
                self.emit(pad + "}")
            self.emit(pad + "}")
        elif isinstance(stmt, BreakStmt):
            self.emit(pad + "break;")
        elif isinstance(stmt, ContinueStmt):
            self.emit(pad + "continue;")
        elif isinstance(stmt, ForStmt):
            v = stmt.var_name
            start = self.gen_expression(stmt.start)
            end = self.gen_expression(stmt.end)
            # Declare the loop variable in an enclosing block rather than in the
            # for-init, so the loop is valid C89 (cc65 rejects C99 declarations
            # in a for-statement; sdcc accepts the block form too).
            self.emit(pad + "{ uint8_t %s;" % v)
            self.emit(pad + "for (%s = %s; %s < %s; %s++) {" % (v, start, v, end, v))
            for s in stmt.body:
                self.gen_statement(s, indent + 1)
            self.emit(pad + "} }")
        elif isinstance(stmt, ReturnStmt):
            if stmt.value is not None:
                self.emit(pad + "return %s;" % self.gen_expression(stmt.value))
            else:
                self.emit(pad + "return;")

    def _gen_expression_stmt(self, expr, indent):
        pad = "    " * indent
        # Assigning a struct literal must be expanded into per-field stores,
        # since C does not allow `target = {a, b};` outside an initializer.
        if (isinstance(expr, BinaryOp) and expr.operator == '=' and
                isinstance(expr.right, StructLiteral)):
            target = self.gen_expression(expr.left)
            for field_name, field_value in expr.right.fields:
                self.emit(pad + "%s.%s = %s;" % (target, field_name,
                                                 self.gen_expression(field_value)))
            return
        self.emit(pad + self.gen_expression(expr) + ";")

    # -- expressions -------------------------------------------------------

    def gen_expression(self, expr) -> str:
        if isinstance(expr, Literal):
            if expr.type == "number":
                return str(expr.value)
            if expr.type == "string":
                return '"%s"' % self._escape_string(expr.value)
            if expr.type == "bool":
                return "1" if expr.value else "0"
            return str(expr.value)

        if isinstance(expr, Identifier):
            if expr.name == "true":
                return "1"
            if expr.name == "false":
                return "0"
            # GB hardware-register constants are only #defined on consoles
            # that have those registers; elsewhere this is a clear error
            # instead of a C compile failure on an undefined identifier.
            if (expr.name in self.GB_REG_CONSTANTS
                    and not self.caps['has_gb_regs']):
                raise RuntimeError(
                    "hardware register constant '%s' is Game Boy-specific and "
                    "not available on target '%s' (gate it with "
                    "`if platform == \"...\"`)" % (expr.name, self.platform))
            # In a multi-module program, a bare reference to one of the
            # current module's own top-level symbols resolves to its mangled
            # C name -- unless a parameter or local shadows it.
            if (self.multi_module and expr.name not in self.local_names
                    and expr.name in self.current_symbols):
                return self._mangled(self.current_module, expr.name)
            return expr.name

        if isinstance(expr, BinaryOp):
            if expr.operator in ('=', '+=', '-='):
                return "%s %s %s" % (self.gen_expression(expr.left),
                                     expr.operator,
                                     self.gen_expression(expr.right))
            c_op = self.BINARY_C_OPERATORS.get(expr.operator, expr.operator)
            return "(%s %s %s)" % (self.gen_expression(expr.left), c_op,
                                   self.gen_expression(expr.right))

        if isinstance(expr, UnaryOp):
            if expr.operator == 'not':
                return "(!%s)" % self.gen_expression(expr.operand)
            return "(%s%s)" % (expr.operator, self.gen_expression(expr.operand))

        if isinstance(expr, FunctionCall):
            return self._gen_call(expr)

        if isinstance(expr, FieldAccess):
            # `alias.member` may be a cross-module reference to an imported
            # module's exported const/var rather than struct member access.
            if isinstance(expr.object, Identifier):
                resolved = self._module_member(expr.object.name, expr.field)
                if resolved is not None:
                    return resolved
            return "%s.%s" % (self.gen_expression(expr.object), expr.field)

        if isinstance(expr, ArrayAccess):
            return "%s[%s]" % (self.gen_expression(expr.array),
                               self.gen_expression(expr.index))

        if isinstance(expr, StructLiteral):
            return "{%s}" % ", ".join(self.gen_expression(v) for _, v in expr.fields)

        if isinstance(expr, ArrayLiteral):
            return "{%s}" % ", ".join(self.gen_expression(e) for e in expr.elements)

        return "0"

    def _gen_call(self, call) -> str:
        args = ", ".join(self.gen_expression(a) for a in call.arguments)

        if isinstance(call.function, Identifier):
            # gen_expression applies the module-symbol mangling (a bare call
            # targets a function of the current module).
            return "%s(%s)" % (self.gen_expression(call.function), args)

        if isinstance(call.function, FieldAccess) and isinstance(call.function.object, Identifier):
            module_name = call.function.object.name
            func_name = call.function.field
            key = (module_name, func_name)
            c_name = self.stdlib_calls.get(key)
            if c_name is None:
                # Cross-module call into an imported program module: resolve
                # against its export list (raises clear errors for missing
                # imports / unknown / unexported symbols).
                resolved = self._module_member(module_name, func_name)
                if resolved is not None:
                    return "%s(%s)" % (resolved, args)
                # A stdlib call that some other backend supports but this one
                # does not is a hard error here (clear message) rather than a
                # link-time failure on an undefined symbol. Anything else is an
                # ordinary cross-module call lowered to `module_func(...)`.
                if key in self.ALL_STDLIB_CALLS:
                    raise RuntimeError(
                        "stdlib call '%s.%s' is not supported on target '%s' "
                        "(%s backend)" % (module_name, func_name,
                                          self.platform, self.framework))
                c_name = "%s_%s" % (module_name, func_name)
            return "%s(%s)" % (c_name, args)

        # Fallback: emit the callee expression directly.
        return "%s(%s)" % (self.gen_expression(call.function), args)

    @staticmethod
    def _escape_string(value: str) -> str:
        return value.replace('\\', '\\\\').replace('"', '\\"')
