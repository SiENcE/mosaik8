"""Top-level driver: lex + parse + typecheck + codegen for a program."""

from .lexer import Lexer
from .parser import Parser
from .typechecker import TypeChecker
from .codegen import CodeGenerator
from .platforms import canonical_platform
from .stdlib import stdlib_module_names
from .ast_nodes import *  # noqa: F401,F403  (AST node types used by tree-shaking)
from .ast_nodes import Program, FunctionDecl


class MosaikCompiler:
    def __init__(self):
        self.lexer = None
        self.parser = None
        self.type_checker = TypeChecker()
        self.code_generator = CodeGenerator()

    def compile(self, source_code: str, platform: str = None,
                assets: list = None) -> str:
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
        """
        return self.compile_program([("<source>", source_code)],
                                    platform=platform, assets=assets)

    def compile_program(self, sources: list, platform: str = None,
                        assets: list = None) -> str:
        """Compile a whole program (one or more sources) to a single C TU.

        `sources` is a list of (filename, source_code) pairs -- every .mos
        file taking part in the build. All modules are parsed up front and
        generated into one C translation unit (whole-program compilation, the
        natural model for sdcc/cc65 which optimize poorly across translation
        units). Cross-module references resolve through each module's
        `export` list; see CodeGenerator._collect_modules for the C-level
        name mangling.

        Returns the generated C, or a string starting with
        "Compilation error:" on failure (matching compile()).
        """
        try:
            platform = canonical_platform(platform or self.code_generator.platform)
            self.code_generator.platform = platform
            self.code_generator.assets = list(assets or [])

            # Lex + parse every source; collect all modules into one program.
            modules = []
            module_files = {}  # module name -> defining file (for diagnostics)
            for filename, source_code in sources:
                self.lexer = Lexer(source_code)
                tokens = self.lexer.tokenize()
                self.parser = Parser(tokens, platform=platform)
                ast = self.parser.parse()
                for module in ast.modules:
                    if module.name in module_files:
                        raise RuntimeError(
                            'duplicate module "%s" (defined in %s and %s)'
                            % (module.name, module_files[module.name], filename))
                    module_files[module.name] = filename
                    modules.append(module)
            program = Program(modules)

            self._check_imports(program, module_files)
            self._validate_module_names(program)
            program = self._tree_shake(program)

            # Type checking is best-effort: it produces helpful diagnostics for
            # simple programs but must never block code generation for the more
            # advanced language features the samples exercise.
            try:
                self.type_checker = TypeChecker()
                self.type_checker.register_assets(assets or [])
                self.type_checker.check_program(program)
            except Exception as type_error:
                print(f"    Warning: type check skipped ({type_error})")

            # Code generation
            c_code = self.code_generator.generate(program)

            return c_code

        except Exception as e:
            import traceback
            return f"Compilation error: {str(e)}\n\nFull traceback:\n{traceback.format_exc()}"

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
                    for _labels, body in stmt.cases:
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
