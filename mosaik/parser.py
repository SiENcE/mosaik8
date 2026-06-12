"""Hand-written recursive-descent parser: tokens -> AST."""

from typing import List, Optional

from .lexer import Token, TokenType
from .ast_nodes import *  # noqa: F401,F403
from .platforms import canonical_platform


class Parser:
    def __init__(self, tokens: List[Token], platform: str = 'gameboy'):
        self.tokens = [t for t in tokens if t.type != TokenType.COMMENT]
        self.pos = 0
        self.current_token = self.tokens[0] if self.tokens else None
        # Build target, used to resolve `if platform == "..."` blocks.
        self.platform = canonical_platform(platform)

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
        condition = self.parse_expression()
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
        """Parse a `{ ... }` block of module-level declarations."""
        self.expect(TokenType.LBRACE)
        self.skip_newlines()
        decls = []
        while self.current_token and self.current_token.type != TokenType.RBRACE:
            self.skip_newlines()
            if not self.current_token or self.current_token.type == TokenType.RBRACE:
                break
            decls.append(self.parse_declaration())
            self.skip_newlines()
        self.expect(TokenType.RBRACE)
        return decls

    def _eval_platform_cond(self, expr) -> Optional[bool]:
        """Best-effort compile-time evaluation of a conditional-compilation
        condition. Returns True/False when the expression depends only on
        `platform` and string/bool literals, or None when it cannot be resolved
        (the caller then keeps the `then` branch)."""
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

        N must be 1..511 (MBC5's bank range); bank 0 *is* the home bank --
        an unannotated function already lives there. The annotation always
        comes first: `bank(2) local function helper() { ... }`.
        """
        line = self.current_token.line
        self.expect(TokenType.IDENTIFIER)  # the contextual 'bank'
        self.expect(TokenType.LPAREN)
        number = self.expect(TokenType.NUMBER)
        bank = int(number.value, 0)
        self.expect(TokenType.RPAREN)
        if not 1 <= bank <= 511:
            raise SyntaxError(
                f"bank({bank}) at line {line} is out of range: ROM banks are "
                f"1..511 (bank 0 is the home bank; just omit the annotation)")

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
        return func

    def parse_function(self) -> FunctionDecl:
        self.expect(TokenType.FUNCTION)
        name = self.expect(TokenType.IDENTIFIER).value

        self.expect(TokenType.LPAREN)
        parameters = []

        while self.current_token and self.current_token.type != TokenType.RPAREN:
            param_name = self.expect(TokenType.IDENTIFIER).value
            self.expect(TokenType.COLON)
            param_type = self.parse_type()
            parameters.append(Parameter(param_name, param_type))

            if self.match(TokenType.COMMA):
                self.advance()

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
            size = int(self.expect(TokenType.NUMBER).value, 0)
            self.expect(TokenType.RBRACKET)
            return ArrayType(element_type, size)

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

            statements.append(self.parse_statement())
            self.skip_newlines()

        return statements

    def parse_statement(self) -> Statement:
        """Parse a single statement"""
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
        condition = self.parse_expression()
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
        condition = self.parse_expression()
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

    def parse_assignment(self) -> Expression:
        expr = self.parse_logical_or()

        if self.match(TokenType.ASSIGN, TokenType.PLUS_ASSIGN, TokenType.MINUS_ASSIGN):
            operator = self.current_token.value
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
        expr = self.parse_addition()

        while self.match(TokenType.LESS, TokenType.GREATER,
                         TokenType.LESS_EQUAL, TokenType.GREATER_EQUAL):
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
            return UnaryOp(operator, operand)

        return self.parse_postfix()

    def parse_postfix(self) -> Expression:
        expr = self.parse_primary()

        while True:
            if self.match(TokenType.LPAREN):
                self.advance()
                arguments = []

                while self.current_token and self.current_token.type != TokenType.RPAREN:
                    arguments.append(self.parse_expression())
                    if self.match(TokenType.COMMA):
                        self.advance()

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
            value = int(self.current_token.value, 0)
            self.advance()
            return Literal(value, "number")

        elif self.match(TokenType.STRING):
            value = self.current_token.value
            self.advance()
            return Literal(value, "string")

        elif self.match(TokenType.IDENTIFIER):
            name = self.current_token.value
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
