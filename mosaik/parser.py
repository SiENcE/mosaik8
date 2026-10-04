"""Hand-written recursive-descent parser: tokens -> AST."""

from typing import List, Optional

from .lexer import Token, TokenType
from .ast_nodes import *  # noqa: F401,F403
from .platforms import canonical_platform


class Parser:
    def __init__(self, tokens: List[Token], platform: str = 'gameboy',
                 defines: dict = None):
        self.tokens = [t for t in tokens if t.type != TokenType.COMMENT]
        self.pos = 0
        self.current_token = self.tokens[0] if self.tokens else None
        # Build target, used to resolve `if platform == "..."` blocks.
        self.platform = canonical_platform(platform)
        # Build-supplied compile-time flags (name -> bool | int), resolvable in
        # a conditional-compilation condition exactly like `platform`. The build
        # tool derives them from the program itself -- today the opcode set a
        # VM8 game's bytecode actually contains, so `lib/vm/core.mos` can fold
        # away the dispatch arms the blob can never reach. A name that is NOT in
        # this dict stays unresolvable, and an unresolvable condition keeps its
        # `then` branch (module level) / stays a runtime `if` (statement level),
        # so a build that passes no defines is byte-identical.
        #
        # An INTEGER define additionally resolves as a VALUE: as an array LENGTH
        # (`array[u8, VM_ACTOR_POOL]`) and as a bare expression atom (so
        # `const ACTORS = VM_ACTOR_POOL` tracks the same knob). That is what
        # makes a fixed runtime pool -- the VM8 actor / trigger slot tables --
        # a `[build]` knob without forking every declaration behind a
        # conditional. Both forms fold HERE, at parse time, so `ArrayType.size`
        # stays a plain int and the typechecker / codegen are untouched; a build
        # that passes the default value emits byte-identical C.
        self.defines = dict(defines or {})

    def advance(self):
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
            self.current_token = self.tokens[self.pos]
        else:
            self.current_token = None

    def peek(self, offset: int = 1) -> Optional[Token]:
        peek_pos = self.pos + offset
        if peek_pos < len(self.tokens):
            return self.tokens[peek_pos]
        return None

    def expect(self, token_type: TokenType) -> Token:
        if not self.current_token or self.current_token.type != token_type:
            current_type = self.current_token.type.value if self.current_token else 'EOF'
            current_line = self.current_token.line if self.current_token else 'EOF'
            raise SyntaxError(f"Expected {token_type.value}, got {current_type} at line {current_line}")
        token = self.current_token
        self.advance()
        return token

    def match(self, *token_types: TokenType) -> bool:
        if self.current_token and self.current_token.type in token_types:
            return True
        return False

    def _expected_separator(self, where, closer):
        tok = self.current_token
        got = tok.value if tok and tok.value else (tok.type.name if tok else "end of input")
        line = tok.line if tok else "?"
        raise SyntaxError("Expected ',' or '%s' in %s, got %r at line %s"
                          % (closer, where, got, line))

    def skip_newlines(self):
        """Skip all consecutive newlines"""
        while self.current_token and self.current_token.type == TokenType.NEWLINE:
            self.advance()

    def parse(self) -> Program:
        modules = []

        while self.current_token and self.current_token.type != TokenType.EOF:
            self.skip_newlines()
            if self.current_token and self.current_token.type != TokenType.EOF:
                modules.append(self.parse_module())

        return Program(modules)

    def parse_module(self) -> Module:
        self.expect(TokenType.MODULE)
        name_token = self.expect(TokenType.STRING)
        name = name_token.value

        self.expect(TokenType.LBRACE)
        self.skip_newlines()

        imports = []
        declarations = []
        exports = []

        while self.current_token and self.current_token.type != TokenType.RBRACE:
            self.skip_newlines()

            if not self.current_token or self.current_token.type == TokenType.RBRACE:
                break

            if self.match(TokenType.IMPORT):
                imports.append(self.parse_import())
            elif self.match(TokenType.EXPORT):
                exports.extend(self.parse_export())
            elif self.match(TokenType.IF):
                # Conditional compilation at module scope. We keep the "then"
                # branch declarations and discard the "else" branch.
                declarations.extend(self.parse_conditional_declarations())
            else:
                declarations.append(self.parse_declaration())

            self.skip_newlines()

        self.expect(TokenType.RBRACE)
        return Module(name, imports, declarations, exports)

    def parse_conditional_declarations(self) -> List[Declaration]:
        """Parse a module-level `if <cond> { ... } else { ... }` block.

        Conditional compilation is resolved against the build target: the
        condition (e.g. `platform == "gameboy_color"`) is evaluated at compile
        time and only the matching branch's declarations are kept. Both
        branches are always *parsed* so syntax errors surface regardless of the
        target, and `else if` chains are supported. When the condition cannot be
        resolved to a constant the `then` branch is kept (legacy behaviour).
        """
        self.expect(TokenType.IF)
        condition = self.parse_condition()
        then_decls = self._parse_decl_block()

        else_decls: List[Declaration] = []
        if self.match(TokenType.ELSE):
            self.advance()
            if self.match(TokenType.IF):
                else_decls = self.parse_conditional_declarations()  # else if ...
            else:
                else_decls = self._parse_decl_block()

        return else_decls if self._eval_platform_cond(condition) is False else then_decls

    def _parse_decl_block(self) -> List[Declaration]:
        """Parse a `{ ... }` block of module-level declarations.

        A nested `if <cond> { ... }` inside the block is itself conditional
        compilation (same as at module top level), so both `} else { if ... }`
        and a conditional nested in a then-branch parse - matching the else-if
        chain and the statement-level `else { if }`.
        """
        self.expect(TokenType.LBRACE)
        self.skip_newlines()
        decls = []
        while self.current_token and self.current_token.type != TokenType.RBRACE:
            self.skip_newlines()
            if not self.current_token or self.current_token.type == TokenType.RBRACE:
                break
            if self.match(TokenType.IF):
                decls.extend(self.parse_conditional_declarations())
            else:
                decls.append(self.parse_declaration())
            self.skip_newlines()
        self.expect(TokenType.RBRACE)
        return decls

    def _eval_platform_cond(self, expr) -> Optional[bool]:
        """Best-effort compile-time evaluation of a conditional-compilation
        condition. Returns True/False when the expression depends only on
        `platform`, a build-supplied `defines` flag, and string/bool literals,
        or None when it cannot be resolved (the caller then keeps the `then`
        branch)."""
        # A bare identifier naming a build-supplied flag (see __init__). Only
        # names the build actually passed resolve; anything else is None, which
        # is what keeps an ordinary runtime `if some_var { }` a runtime if.
        if isinstance(expr, Identifier) and expr.name in self.defines:
            return bool(self.defines[expr.name])
        if isinstance(expr, BinaryOp):
            if expr.operator in ('==', '!='):
                left = self._platform_operand(expr.left)
                right = self._platform_operand(expr.right)
                if left is None or right is None:
                    return None
                equal = left == right
                return equal if expr.operator == '==' else not equal
            if expr.operator in ('and', 'or'):
                lhs = self._eval_platform_cond(expr.left)
                rhs = self._eval_platform_cond(expr.right)
                if lhs is None or rhs is None:
                    return None
                return (lhs and rhs) if expr.operator == 'and' else (lhs or rhs)
            return None
        if isinstance(expr, UnaryOp) and expr.operator in ('not', '!'):
            inner = self._eval_platform_cond(expr.operand)
            return None if inner is None else (not inner)
        if isinstance(expr, Literal) and expr.type == 'bool':
            return bool(expr.value)
        return None

    def _platform_operand(self, expr) -> Optional[str]:
        """Resolve one side of a `platform == "..."` comparison to a canonical
        platform string, or None if it isn't a platform/string reference."""
        if isinstance(expr, Identifier) and expr.name == 'platform':
            return self.platform
        if isinstance(expr, Literal) and expr.type == 'string':
            return canonical_platform(expr.value)
        return None

    def parse_import(self) -> Import:
        self.expect(TokenType.IMPORT)
        module_name = self.expect(TokenType.STRING).value
        return Import(module_name)

    def parse_export(self) -> List[str]:
        """Parse export statement with stricter error checking."""
        self.expect(TokenType.EXPORT)
        exports = []

        if self.match(TokenType.LBRACE):
            # Braced export list: export { a, b, c }
            self.advance()
            self.skip_newlines()

            while self.current_token and self.current_token.type != TokenType.RBRACE:
                if self.match(TokenType.IDENTIFIER):
                    exports.append(self.expect(TokenType.IDENTIFIER).value)
                    self.skip_newlines()
                    if self.match(TokenType.COMMA):
                        self.advance()
                        self.skip_newlines()
                        # After comma, must have another identifier or closing brace
                        if not self.match(TokenType.IDENTIFIER, TokenType.RBRACE):
                            raise SyntaxError(f"Expected identifier after comma in export list at line {self.current_token.line}")
                    elif self.current_token and self.current_token.type != TokenType.RBRACE:
                        break
                else:
                    break

            self.expect(TokenType.RBRACE)
        else:
            # Comma-separated export list: export a, b, c
            if not self.match(TokenType.IDENTIFIER):
                raise SyntaxError(f"Expected identifier in export at line {self.current_token.line}")
            exports.append(self.expect(TokenType.IDENTIFIER).value)

            while self.match(TokenType.COMMA):
                self.advance()
                self.skip_newlines()
                # After comma, must have an identifier
                if not self.match(TokenType.IDENTIFIER):
                    raise SyntaxError(f"Expected identifier after comma in export list at line {self.current_token.line}")
                exports.append(self.expect(TokenType.IDENTIFIER).value)

        return exports

    def parse_declaration(self) -> Declaration:
        tok = self.current_token
        decl = self._parse_declaration_inner()
        if tok is not None and decl is not None and not hasattr(decl, 'line'):
            decl.line = tok.line
        return decl

    def _parse_declaration_inner(self) -> Declaration:
        # `hot` is a contextual keyword too: directly before `function`,
        # `local` or `bank(N)` it marks a function as called per frame (see
        # FunctionDecl.hot); a variable named `hot` keeps working.
        if (self.match(TokenType.IDENTIFIER) and self.current_token.value == 'hot'
                and self.peek() is not None
                and (self.peek().type in (TokenType.FUNCTION, TokenType.LOCAL)
                     or (self.peek().type == TokenType.IDENTIFIER
                         and self.peek().value == 'bank'))):
            line = self.current_token.line
            self.advance()
            func = self._parse_declaration_inner()
            if not isinstance(func, FunctionDecl):
                raise SyntaxError(f"Expected function after 'hot' at line {line}")
            func.hot = True
            return func
        # `bank` is a contextual keyword: only `bank(N)` directly before a
        # function declaration is the ROM-bank placement annotation
        # (variables named `bank` keep working).
        if (self.match(TokenType.IDENTIFIER) and self.current_token.value == 'bank'
                and self.peek() and self.peek().type == TokenType.LPAREN):
            return self.parse_banked_function()
        if self.match(TokenType.FUNCTION):
            return self.parse_function()
        elif self.match(TokenType.VAR, TokenType.CONST):
            return self.parse_variable()
        elif self.match(TokenType.TYPE):
            return self.parse_type_declaration()
        elif self.match(TokenType.ENUM):
            return self.parse_enum_declaration()
        elif self.match(TokenType.LOCAL):
            self.advance()
            if self.match(TokenType.FUNCTION):
                func = self.parse_function()
                func.is_local = True
                return func
            else:
                raise SyntaxError("Expected function after 'local'")
        else:
            current_type = self.current_token.type.value if self.current_token else 'EOF'
            current_line = self.current_token.line if self.current_token else 'EOF'
            raise SyntaxError(f"Unexpected token in declaration: {current_type} at line {current_line}")

    def parse_banked_function(self) -> FunctionDecl:
        """Parse `bank(N) [local] function ...` (ROM-bank placement).

        N is 1..511 (MBC5's bank range). The annotation always comes first:
        `bank(2) local function helper() { ... }`.

        **`bank(0)` PINS the function to the home bank.** Omitting the
        annotation used to mean the same thing and this was an error, but
        `[build] code_banks` changed that: there, an unannotated function in a
        listed module BANKS, and the only way to say "this one is hot, keep it
        resident" is to say so. Without code banking `bank(0)` is a no-op, so
        an existing program is byte-identical either way.
        """
        line = self.current_token.line
        self.expect(TokenType.IDENTIFIER)  # the contextual 'bank'
        self.expect(TokenType.LPAREN)
        number = self.expect(TokenType.NUMBER)
        bank = int(number.value, 0)
        self.expect(TokenType.RPAREN)
        if not 0 <= bank <= 511:
            raise SyntaxError(
                f"bank({bank}) at line {line} is out of range: ROM banks are "
                f"1..511, and bank(0) pins the function to the home bank")

        is_local = False
        if self.match(TokenType.LOCAL):
            self.advance()
            is_local = True
        if not self.match(TokenType.FUNCTION):
            raise SyntaxError(
                f"Expected function after bank({bank}) at line {line} "
                f"(bank() only places functions)")
        func = self.parse_function()
        func.is_local = is_local
        func.bank = bank
        # bank=0 is also the DEFAULT, so record that this one was asked for:
        # `_plan_code_banks` must be able to tell "unannotated, free to bank"
        # from "explicitly pinned home".
        func.bank_pinned = (bank == 0)
        return func

    def parse_function(self) -> FunctionDecl:
        self.expect(TokenType.FUNCTION)
        name = self.expect(TokenType.IDENTIFIER).value

        self.expect(TokenType.LPAREN)
        parameters = []

        self.skip_newlines()
        while self.current_token and self.current_token.type != TokenType.RPAREN:
            param_name = self.expect(TokenType.IDENTIFIER).value
            self.expect(TokenType.COLON)
            param_type = self.parse_type()
            parameters.append(Parameter(param_name, param_type))
            self.skip_newlines()
            if self.match(TokenType.COMMA):
                self.advance()
                self.skip_newlines()
            elif not self.match(TokenType.RPAREN):
                self._expected_separator("parameter list", ")")

        self.expect(TokenType.RPAREN)

        return_type = None
        if self.match(TokenType.ARROW):
            self.advance()
            return_type = self.parse_type()

        self.expect(TokenType.LBRACE)
        body = self.parse_statement_list()
        self.expect(TokenType.RBRACE)

        return FunctionDecl(name, parameters, return_type, body)

    def parse_variable(self) -> VarDecl:
        is_const = self.match(TokenType.CONST)
        if is_const:
            self.advance()
        else:
            self.expect(TokenType.VAR)

        name = self.expect(TokenType.IDENTIFIER).value

        var_type = None
        if self.match(TokenType.COLON):
            self.advance()
            var_type = self.parse_type()

        initializer = None
        if self.match(TokenType.ASSIGN):
            self.advance()
            initializer = self.parse_expression()

        return VarDecl(name, var_type, initializer, is_const)

    def parse_type_declaration(self) -> TypeDecl:
        self.expect(TokenType.TYPE)
        name = self.expect(TokenType.IDENTIFIER).value
        self.expect(TokenType.ASSIGN)
        type_def = self.parse_type()
        return TypeDecl(name, type_def)

    def parse_enum_declaration(self) -> TypeDecl:
        """Parse standalone enum declaration."""
        self.expect(TokenType.ENUM)
        name = self.expect(TokenType.IDENTIFIER).value
        self.expect(TokenType.LBRACE)
        self.skip_newlines()
        variants = []

        while self.current_token and self.current_token.type != TokenType.RBRACE:
            variant_name = self.expect(TokenType.IDENTIFIER).value
            variant_value = None

            if self.match(TokenType.ASSIGN):
                self.advance()
                variant_value = int(self.expect(TokenType.NUMBER).value, 0)

            variants.append(EnumVariant(variant_name, variant_value))

            if self.match(TokenType.COMMA):
                self.advance()
            self.skip_newlines()

        self.expect(TokenType.RBRACE)
        enum_type = EnumType(variants)
        return TypeDecl(name, enum_type)

    def int_define(self, name: str):
        """The value of `name` if it is an INTEGER define, else None.

        `bool` is excluded on purpose: it is an `int` subclass in Python, and a
        boolean dispatch flag must never silently size an array.
        """
        val = self.defines.get(name)
        if isinstance(val, bool) or not isinstance(val, int):
            return None
        return val

    def parse_array_size(self) -> int:
        """An array LENGTH: a number literal, or a build-supplied int define.

        Only a define the build actually passed resolves; an unknown identifier
        is a clear error naming it, never a silent default (a mis-sized pool
        would be a memory-corruption bug, not a build failure).
        """
        if self.match(TokenType.IDENTIFIER):
            name = self.current_token.value
            size = self.int_define(name)
            if size is None:
                raise SyntaxError(
                    f"Array length {name!r} at line {self.current_token.line} "
                    f"is not a build-supplied integer define")
            self.advance()
            return size
        return int(self.expect(TokenType.NUMBER).value, 0)

    def parse_type(self) -> Type:
        """Parse type with validation for known types."""
        if self.match(TokenType.U8, TokenType.I8, TokenType.U16, TokenType.I16,
                     TokenType.BOOL, TokenType.ADDR, TokenType.VOID):
            type_name = self.current_token.value
            self.advance()
            return PrimitiveType(type_name)

        elif self.match(TokenType.IDENTIFIER):
            # User-defined type - for now we'll allow it and let type checker validate
            # But we could add a validation flag here for stricter parsing
            type_name = self.current_token.value
            self.advance()
            return UserDefinedType(type_name)

        elif self.match(TokenType.ARRAY):
            self.advance()
            self.expect(TokenType.LBRACKET)
            element_type = self.parse_type()
            self.expect(TokenType.COMMA)
            size = self.parse_array_size()
            self.expect(TokenType.RBRACKET)
            return ArrayType(element_type, size)

        elif self.match(TokenType.FUNCTION):
            # A function type (a callback): `function(u8, u16) -> bool`, or
            # `function(u8)` for a void callback. Reuses the `function` keyword
            # in type position; the `(` after it (no identifier) marks it a type.
            self.advance()
            self.expect(TokenType.LPAREN)
            param_types = []
            while self.current_token and self.current_token.type != TokenType.RPAREN:
                param_types.append(self.parse_type())
                if self.match(TokenType.COMMA):
                    self.advance()
            self.expect(TokenType.RPAREN)
            return_type = None
            if self.match(TokenType.ARROW):
                self.advance()
                return_type = self.parse_type()
            return FunctionType(param_types, return_type)

        elif self.match(TokenType.STRUCT):
            self.advance()
            self.expect(TokenType.LBRACE)
            self.skip_newlines()
            fields = []

            while self.current_token and self.current_token.type != TokenType.RBRACE:
                field_name = self.expect(TokenType.IDENTIFIER).value
                self.expect(TokenType.COLON)
                field_type = self.parse_type()
                fields.append(StructField(field_name, field_type))

                if self.match(TokenType.COMMA):
                    self.advance()
                self.skip_newlines()

            self.expect(TokenType.RBRACE)
            return StructType(fields)

        elif self.match(TokenType.ENUM):
            self.advance()
            self.expect(TokenType.LBRACE)
            self.skip_newlines()
            variants = []

            while self.current_token and self.current_token.type != TokenType.RBRACE:
                variant_name = self.expect(TokenType.IDENTIFIER).value
                variant_value = None

                if self.match(TokenType.ASSIGN):
                    self.advance()
                    variant_value = int(self.expect(TokenType.NUMBER).value, 0)

                variants.append(EnumVariant(variant_name, variant_value))

                if self.match(TokenType.COMMA):
                    self.advance()
                self.skip_newlines()

            self.expect(TokenType.RBRACE)
            return EnumType(variants)

        else:
            current_type = self.current_token.type.value if self.current_token else 'EOF'
            current_line = self.current_token.line if self.current_token else 'EOF'
            raise SyntaxError(f"Expected type, got {current_type} at line {current_line}")

    def parse_statement_list(self) -> List[Statement]:
        """Parse a list of statements, handling newlines properly"""
        statements = []

        while (self.current_token and
               self.current_token.type not in [TokenType.RBRACE, TokenType.EOF]):
            self.skip_newlines()

            if not self.current_token or self.current_token.type == TokenType.RBRACE:
                break

            stmt = self.parse_statement()
            folded = self._fold_conditional_stmt(stmt)
            if folded is None:
                statements.append(stmt)
            else:
                statements.extend(folded)      # compile-time branch: splice it in
            self.skip_newlines()

        return statements

    def _fold_conditional_stmt(self, stmt) -> Optional[List[Statement]]:
        """Conditional compilation at STATEMENT level.

        A module-level `if <cond> { } else { }` is resolved against the build
        target by `parse_conditional_declarations`; this is the same thing one
        level down, so a compile-time condition INSIDE a function body drops the
        branch that cannot be taken instead of emitting a runtime test. That is
        what lets `lib/vm/core.mos` guard each opcode arm with a build-supplied
        flag and have the unreachable ones cost zero bytes.

        Returns the surviving statement list, or None when the condition is not
        a compile-time constant -- then the caller keeps the ordinary runtime
        `IfStmt`, so every existing program is unaffected.
        """
        if not isinstance(stmt, IfStmt):
            return None
        # An `else if` chain is built by parse_if_statement's own recursion, so
        # parse_statement_list never sees those links -- fold them here, or a
        # chain could only ever be pruned at its head.
        if stmt.else_body:
            stmt.else_body = self._fold_stmt_list(stmt.else_body) or None
        cond = self._simplify_cond(stmt.condition)
        value = self._eval_platform_cond(cond)
        if value is None:
            # Not decidable, but a resolvable SUB-term may have been folded out
            # (`FLAG and tk == 3` -> `tk == 3`). Keep the runtime if, minus the
            # compile-time part -- leaving it in would emit an undefined symbol.
            stmt.condition = cond
            return None
        kept = stmt.then_body if value else stmt.else_body
        return self._fold_stmt_list(list(kept or []))

    def _fold_stmt_list(self, stmts) -> List[Statement]:
        """Fold every compile-time conditional in a statement list, splicing the
        surviving branches in place."""
        out = []
        for s in stmts:
            folded = self._fold_conditional_stmt(s)
            if folded is None:
                out.append(s)
            else:
                out.extend(folded)
        return out

    def _simplify_cond(self, expr):
        """Fold the compile-time-resolvable parts out of a condition.

        `platform`/defines terms are known at build time while the rest is not,
        so a mixed condition needs partial evaluation rather than all-or-nothing:
        `FLAG and tk == 0x01` becomes `tk == 0x01` when FLAG is on and plain
        false when it is off. That is what lets an `else if` CHAIN be pruned a
        link at a time (the RPN evaluator) instead of only whole `if` blocks.
        """
        value = self._eval_platform_cond(expr)
        if value is not None:
            return Literal(value, 'bool')
        if isinstance(expr, BinaryOp) and expr.operator in ('and', 'or'):
            left = self._simplify_cond(expr.left)
            right = self._simplify_cond(expr.right)
            lv = self._eval_platform_cond(left)
            rv = self._eval_platform_cond(right)
            if expr.operator == 'and':
                if lv is False or rv is False:
                    return Literal(False, 'bool')
                if lv is True:
                    return right
                if rv is True:
                    return left
            else:
                if lv is True or rv is True:
                    return Literal(True, 'bool')
                if lv is False:
                    return right
                if rv is False:
                    return left
            return BinaryOp(left, expr.operator, right)
        return expr

    def parse_statement(self) -> Statement:
        """Parse a single statement, stamping its source line on the node
        (`.line`, read by the type checker's diagnostics)."""
        tok = self.current_token
        stmt = self._parse_statement_inner()
        if tok is not None and not hasattr(stmt, 'line'):
            stmt.line = tok.line
        return stmt

    def _parse_statement_inner(self) -> Statement:
        if self.match(TokenType.IF):
            return self.parse_if_statement()
        elif self.match(TokenType.LOOP):
            return self.parse_loop_statement()
        elif self.match(TokenType.WHILE):
            return self.parse_while_statement()
        elif self.match(TokenType.SWITCH):
            return self.parse_switch_statement()
        elif self.match(TokenType.BREAK):
            self.advance()
            return BreakStmt()
        elif self.match(TokenType.CONTINUE):
            self.advance()
            return ContinueStmt()
        elif self.match(TokenType.FOR):
            return self.parse_for_statement()
        elif self.match(TokenType.RETURN):
            return self.parse_return_statement()
        elif self.match(TokenType.VAR, TokenType.CONST):
            # Handle variable declarations as statements
            var_decl = self.parse_variable()
            return VarDeclStmt(var_decl)
        else:
            # Parse as expression statement
            expr = self.parse_expression()
            return ExpressionStmt(expr)

    def parse_if_statement(self) -> IfStmt:
        self.expect(TokenType.IF)
        condition = self.parse_condition()
        self.expect(TokenType.LBRACE)
        then_body = self.parse_statement_list()
        self.expect(TokenType.RBRACE)

        else_body = None
        if self.match(TokenType.ELSE):
            self.advance()
            if self.match(TokenType.IF):
                # `else if` chains as a single nested if-statement.
                else_body = [self.parse_if_statement()]
            else:
                self.expect(TokenType.LBRACE)
                else_body = self.parse_statement_list()
                self.expect(TokenType.RBRACE)

        return IfStmt(condition, then_body, else_body)

    def parse_loop_statement(self) -> LoopStmt:
        self.expect(TokenType.LOOP)
        self.expect(TokenType.LBRACE)
        body = self.parse_statement_list()
        self.expect(TokenType.RBRACE)
        return LoopStmt(body)

    def parse_while_statement(self) -> WhileStmt:
        """Parse `while <expr> { ... }`."""
        self.expect(TokenType.WHILE)
        condition = self.parse_condition()
        self.expect(TokenType.LBRACE)
        body = self.parse_statement_list()
        self.expect(TokenType.RBRACE)
        return WhileStmt(condition, body)

    def parse_switch_statement(self) -> SwitchStmt:
        """Parse `switch <expr> { case <expr>(, <expr>)* { ... } ... default { ... } }`."""
        self.expect(TokenType.SWITCH)
        subject = self.parse_expression()
        self.expect(TokenType.LBRACE)
        self.skip_newlines()

        cases = []
        default_body = None
        while self.current_token and self.current_token.type != TokenType.RBRACE:
            self.skip_newlines()
            if not self.current_token or self.current_token.type == TokenType.RBRACE:
                break

            if self.match(TokenType.CASE):
                self.advance()
                labels = [self.parse_expression()]
                while self.match(TokenType.COMMA):
                    self.advance()
                    labels.append(self.parse_expression())
                self.expect(TokenType.LBRACE)
                body = self.parse_statement_list()
                self.expect(TokenType.RBRACE)
                cases.append((labels, body))
            elif self.match(TokenType.DEFAULT):
                self.advance()
                self.expect(TokenType.LBRACE)
                default_body = self.parse_statement_list()
                self.expect(TokenType.RBRACE)
            else:
                current_type = self.current_token.type.value if self.current_token else 'EOF'
                current_line = self.current_token.line if self.current_token else 'EOF'
                raise SyntaxError(f"Expected 'case' or 'default' in switch, got {current_type} at line {current_line}")

            self.skip_newlines()

        self.expect(TokenType.RBRACE)
        return SwitchStmt(subject, cases, default_body)

    def parse_for_statement(self) -> ForStmt:
        """Parse `for <ident> in <start>..<end> { ... }`."""
        self.expect(TokenType.FOR)
        var_name = self.expect(TokenType.IDENTIFIER).value
        self.expect(TokenType.IN)
        start = self.parse_expression()
        self.expect(TokenType.DOTDOT)
        end = self.parse_expression()
        self.expect(TokenType.LBRACE)
        body = self.parse_statement_list()
        self.expect(TokenType.RBRACE)
        return ForStmt(var_name, start, end, body)

    def parse_return_statement(self) -> ReturnStmt:
        self.expect(TokenType.RETURN)
        value = None
        if not self.match(TokenType.NEWLINE, TokenType.RBRACE):
            value = self.parse_expression()
        return ReturnStmt(value)

    def parse_expression(self) -> Expression:
        return self.parse_assignment()

    def parse_condition(self) -> Expression:
        """A condition is a boolean expression; assignment is NOT allowed, so
        the C-classic `if x = 1` (meaning ==) typo is a parse error here
        instead of compiling to `if (x = 1)`."""
        expr = self.parse_logical_or()
        if self.match(TokenType.ASSIGN, TokenType.PLUS_ASSIGN, TokenType.MINUS_ASSIGN,
                      TokenType.OR_ASSIGN, TokenType.AND_ASSIGN, TokenType.XOR_ASSIGN):
            raise SyntaxError(
                f"'{self.current_token.value}' in a condition at line "
                f"{self.current_token.line} -- use '==' to compare "
                f"(assignment is not allowed in a condition)")
        return expr

    def parse_assignment(self) -> Expression:
        expr = self.parse_logical_or()

        if self.match(TokenType.ASSIGN, TokenType.PLUS_ASSIGN, TokenType.MINUS_ASSIGN,
                      TokenType.OR_ASSIGN, TokenType.AND_ASSIGN, TokenType.XOR_ASSIGN):
            operator = self.current_token.value
            line = self.current_token.line
            # Only a variable, struct field, or array element is assignable;
            # rejecting the rest here beats a downstream C error (`1 = 2`).
            if not isinstance(expr, (Identifier, FieldAccess, ArrayAccess)):
                raise SyntaxError(
                    f"Invalid assignment target for '{operator}' at line "
                    f"{line} (expected a variable, struct field, or array "
                    f"element)")
            self.advance()
            right = self.parse_assignment()
            return BinaryOp(expr, operator, right)

        return expr

    def parse_logical_or(self) -> Expression:
        expr = self.parse_logical_and()

        while self.match(TokenType.OR):
            operator = self.current_token.value
            self.advance()
            right = self.parse_logical_and()
            expr = BinaryOp(expr, operator, right)

        return expr

    def parse_logical_and(self) -> Expression:
        expr = self.parse_equality()

        while self.match(TokenType.AND):
            operator = self.current_token.value
            self.advance()
            right = self.parse_equality()
            expr = BinaryOp(expr, operator, right)

        return expr

    def parse_equality(self) -> Expression:
        expr = self.parse_comparison()

        while self.match(TokenType.EQUAL, TokenType.NOT_EQUAL):
            operator = self.current_token.value
            self.advance()
            right = self.parse_comparison()
            expr = BinaryOp(expr, operator, right)

        return expr

    def parse_comparison(self) -> Expression:
        expr = self.parse_bitor()

        while self.match(TokenType.LESS, TokenType.GREATER,
                         TokenType.LESS_EQUAL, TokenType.GREATER_EQUAL):
            operator = self.current_token.value
            self.advance()
            right = self.parse_bitor()
            expr = BinaryOp(expr, operator, right)

        return expr

    # Bitwise tiers bind TIGHTER than comparison (the Rust/Go ordering, not
    # C's): `a & b == c` means `(a & b) == c`. Loosest to tightest:
    # `|` -> `^` -> `&` -> `<<`/`>>` -> `+`/`-`.
    def parse_bitor(self) -> Expression:
        expr = self.parse_bitxor()
        while self.match(TokenType.BITOR):
            operator = self.current_token.value
            self.advance()
            right = self.parse_bitxor()
            expr = BinaryOp(expr, operator, right)
        return expr

    def parse_bitxor(self) -> Expression:
        expr = self.parse_bitand()
        while self.match(TokenType.BITXOR):
            operator = self.current_token.value
            self.advance()
            right = self.parse_bitand()
            expr = BinaryOp(expr, operator, right)
        return expr

    def parse_bitand(self) -> Expression:
        expr = self.parse_shift()
        while self.match(TokenType.BITAND):
            operator = self.current_token.value
            self.advance()
            right = self.parse_shift()
            expr = BinaryOp(expr, operator, right)
        return expr

    def parse_shift(self) -> Expression:
        expr = self.parse_addition()
        while self.match(TokenType.SHL, TokenType.SHR):
            operator = self.current_token.value
            self.advance()
            right = self.parse_addition()
            expr = BinaryOp(expr, operator, right)
        return expr

    def parse_addition(self) -> Expression:
        expr = self.parse_multiplication()

        while self.match(TokenType.PLUS, TokenType.MINUS):
            operator = self.current_token.value
            self.advance()
            right = self.parse_multiplication()
            expr = BinaryOp(expr, operator, right)

        return expr

    def parse_multiplication(self) -> Expression:
        expr = self.parse_unary()

        while self.match(TokenType.MULTIPLY, TokenType.DIVIDE, TokenType.MODULO):
            operator = self.current_token.value
            self.advance()
            right = self.parse_unary()
            expr = BinaryOp(expr, operator, right)

        return expr

    def parse_unary(self) -> Expression:
        if self.match(TokenType.NOT, TokenType.MINUS):
            operator = self.current_token.value
            self.advance()
            operand = self.parse_unary()
            # `-<number>` IS a negative literal, not a negation of a positive
            # one: the type checker ranges a literal by its VALUE (-1 is an
            # i8, -300 an i16), and as a UnaryOp it inherited the positive
            # operand's u8, so `var q: u8 = -1` passed without a word.
            if (operator == '-' and isinstance(operand, Literal)
                    and operand.type == "number"):
                return Literal(-operand.value, "number")
            return UnaryOp(operator, operand)

        return self.parse_postfix()

    def parse_postfix(self) -> Expression:
        expr = self.parse_primary()

        while True:
            if self.match(TokenType.LPAREN):
                self.advance()
                arguments = []

                # A call may span lines inside its parentheses; its arguments
                # are comma-separated (`f(1 2)` used to parse as `f(1, 2)`).
                self.skip_newlines()
                while self.current_token and self.current_token.type != TokenType.RPAREN:
                    arguments.append(self.parse_expression())
                    self.skip_newlines()
                    if self.match(TokenType.COMMA):
                        self.advance()
                        self.skip_newlines()
                    elif not self.match(TokenType.RPAREN):
                        self._expected_separator("argument list", ")")

                self.expect(TokenType.RPAREN)
                expr = FunctionCall(expr, arguments)

            elif self.match(TokenType.DOT):
                self.advance()
                field = self.expect(TokenType.IDENTIFIER).value
                expr = FieldAccess(expr, field)

            elif self.match(TokenType.LBRACKET):
                self.advance()
                index = self.parse_expression()
                self.expect(TokenType.RBRACKET)
                expr = ArrayAccess(expr, index)

            else:
                break

        return expr

    def parse_primary(self) -> Expression:
        if self.match(TokenType.NUMBER):
            try:
                value = int(self.current_token.value, 0)
            except ValueError:
                raise SyntaxError(
                    "Invalid number literal '%s' at line %d (mosaik has no "
                    "floating-point type; integers are decimal, 0x hex or 0b binary)"
                    % (self.current_token.value, self.current_token.line))
            self.advance()
            return Literal(value, "number")

        elif self.match(TokenType.STRING):
            value = self.current_token.value
            self.advance()
            return Literal(value, "string")

        elif self.match(TokenType.IDENTIFIER):
            name = self.current_token.value
            # An INTEGER define reads as its value, so a `const` can track a
            # build knob (`const ACTORS = VM_ACTOR_POOL`) and stay in step with
            # the arrays it bounds. Boolean dispatch flags are NOT folded here --
            # they belong in a condition, where they already resolve.
            size = self.int_define(name)
            if size is not None:
                self.advance()
                return Literal(size, "number")
            self.advance()
            return Identifier(name)

        elif self.match(TokenType.LPAREN):
            self.advance()
            expr = self.parse_expression()
            self.expect(TokenType.RPAREN)
            return expr

        elif self.match(TokenType.LBRACE):
            return self.parse_struct_literal()

        elif self.match(TokenType.LBRACKET):
            return self.parse_array_literal()

        else:
            current_type = self.current_token.type.value if self.current_token else 'EOF'
            current_line = self.current_token.line if self.current_token else 'EOF'
            raise SyntaxError(f"Unexpected token in expression: {current_type} at line {current_line}")

    def parse_struct_literal(self) -> StructLiteral:
        """Parse `{ field: value, field: value }`."""
        self.expect(TokenType.LBRACE)
        self.skip_newlines()
        fields = []
        while self.current_token and self.current_token.type != TokenType.RBRACE:
            field_name = self.expect(TokenType.IDENTIFIER).value
            self.expect(TokenType.COLON)
            value = self.parse_expression()
            fields.append((field_name, value))
            if self.match(TokenType.COMMA):
                self.advance()
            self.skip_newlines()
        self.expect(TokenType.RBRACE)
        return StructLiteral(fields)

    def parse_array_literal(self) -> ArrayLiteral:
        """Parse `[ value, value, ... ]`."""
        self.expect(TokenType.LBRACKET)
        self.skip_newlines()
        elements = []
        while self.current_token and self.current_token.type != TokenType.RBRACKET:
            elements.append(self.parse_expression())
            if self.match(TokenType.COMMA):
                self.advance()
            self.skip_newlines()
        self.expect(TokenType.RBRACKET)
        return ArrayLiteral(elements)
