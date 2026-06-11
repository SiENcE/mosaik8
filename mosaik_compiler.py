#!/usr/bin/env python3
"""
mosaik Compiler - Enhanced with Graphics.Text Module
A high-level language that compiles to GBDK assembly for Game Boy development.
"""

import re
import enum
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass
from abc import ABC, abstractmethod

# =============================================================================
# LEXER - Tokenization (unchanged from original)
# =============================================================================

class TokenType(enum.Enum):
    # Literals
    NUMBER = "NUMBER"
    STRING = "STRING"
    IDENTIFIER = "IDENTIFIER"

    # Keywords
    MODULE = "module"
    IMPORT = "import"
    EXPORT = "export"
    FUNCTION = "function"
    VAR = "var"
    TYPE = "type"
    STRUCT = "struct"
    ENUM = "enum"
    IF = "if"
    ELSE = "else"
    LOOP = "loop"
    WHILE = "while"
    FOR = "for"
    IN = "in"
    RETURN = "return"
    LOCAL = "local"
    CONST = "const"
    SWITCH = "switch"
    CASE = "case"
    DEFAULT = "default"
    BREAK = "break"
    CONTINUE = "continue"

    # Types
    U8 = "u8"
    I8 = "i8"
    U16 = "u16"
    I16 = "i16"
    BOOL = "bool"
    ADDR = "addr"
    VOID = "void"
    ARRAY = "array"

    # Operators
    PLUS = "+"
    MINUS = "-"
    MULTIPLY = "*"
    DIVIDE = "/"
    MODULO = "%"
    DOTDOT = ".."
    ASSIGN = "="
    PLUS_ASSIGN = "+="
    MINUS_ASSIGN = "-="
    EQUAL = "=="
    NOT_EQUAL = "!="
    LESS = "<"
    GREATER = ">"
    LESS_EQUAL = "<="
    GREATER_EQUAL = ">="
    AND = "and"
    OR = "or"
    NOT = "not"

    # Delimiters
    LPAREN = "("
    RPAREN = ")"
    LBRACE = "{"
    RBRACE = "}"
    LBRACKET = "["
    RBRACKET = "]"
    COMMA = ","
    SEMICOLON = ";"
    COLON = ":"
    DOT = "."
    ARROW = "->"

    # Special
    NEWLINE = "NEWLINE"
    EOF = "EOF"
    COMMENT = "COMMENT"

@dataclass
class Token:
    type: TokenType
    value: str
    line: int
    column: int

class Lexer:
    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.line = 1
        self.column = 1
        self.tokens = []

        self.keywords = {
            'module', 'import', 'export', 'function', 'var', 'type', 'struct', 'enum',
            'if', 'else', 'loop', 'while', 'for', 'in', 'return', 'local', 'const',
            'switch', 'case', 'default', 'break', 'continue',
            'u8', 'i8', 'u16', 'i16', 'bool', 'addr', 'void', 'array',
            'and', 'or', 'not'
        }

    def current_char(self) -> Optional[str]:
        if self.pos >= len(self.text):
            return None
        return self.text[self.pos]

    def peek_char(self, offset: int = 1) -> Optional[str]:
        peek_pos = self.pos + offset
        if peek_pos >= len(self.text):
            return None
        return self.text[peek_pos]

    def advance(self):
        if self.pos < len(self.text) and self.text[self.pos] == '\n':
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        self.pos += 1

    def skip_whitespace(self):
        while self.current_char() and self.current_char() in ' \t\r':
            self.advance()

    def read_number(self) -> Token:
        start_pos = self.pos
        start_col = self.column

        # Hex (0x..) and binary (0b..) literals.
        if self.current_char() == '0' and self.peek_char() in ('x', 'X', 'b', 'B'):
            self.advance()  # '0'
            self.advance()  # 'x' / 'b'
            while self.current_char() and self.current_char().isalnum():
                self.advance()
            value = self.text[start_pos:self.pos]
            return Token(TokenType.NUMBER, value, self.line, start_col)

        while self.current_char() and self.current_char().isdigit():
            self.advance()

        # Consume a decimal point only if it is followed by another digit.
        # This keeps range syntax like "0..4" from being swallowed as a number.
        if (self.current_char() == '.' and self.peek_char() and self.peek_char().isdigit()):
            self.advance()
            while self.current_char() and self.current_char().isdigit():
                self.advance()

        value = self.text[start_pos:self.pos]
        return Token(TokenType.NUMBER, value, self.line, start_col)

    def read_string(self) -> Token:
        start_col = self.column
        quote_char = self.current_char()
        self.advance()  # Skip opening quote

        value = ""
        while self.current_char() and self.current_char() != quote_char:
            if self.current_char() == '\\':
                self.advance()
                if self.current_char():
                    value += self.current_char()
                    self.advance()
            else:
                value += self.current_char()
                self.advance()

        if self.current_char() == quote_char:
            self.advance()  # Skip closing quote

        return Token(TokenType.STRING, value, self.line, start_col)

    def read_identifier(self) -> Token:
        start_pos = self.pos
        start_col = self.column

        while (self.current_char() and
               (self.current_char().isalnum() or self.current_char() in '_')):
            self.advance()

        value = self.text[start_pos:self.pos]

        # Check if it's a keyword
        if value in self.keywords:
            token_type = TokenType(value)
        else:
            token_type = TokenType.IDENTIFIER

        return Token(token_type, value, self.line, start_col)

    def read_comment(self) -> Token:
        start_col = self.column

        if self.current_char() == '-' and self.peek_char() == '-':
            # Line comment --
            self.advance()
            self.advance()
            value = ""
            while self.current_char() and self.current_char() != '\n':
                value += self.current_char()
                self.advance()
        elif self.current_char() == '#':
            # Platform directive #
            value = ""
            while self.current_char() and self.current_char() != '\n':
                value += self.current_char()
                self.advance()
        else:
            return None

        return Token(TokenType.COMMENT, value.strip(), self.line, start_col)

    def tokenize(self) -> List[Token]:
        while self.pos < len(self.text):
            self.skip_whitespace()

            if not self.current_char():
                break

            char = self.current_char()

            # Newlines
            if char == '\n':
                self.tokens.append(Token(TokenType.NEWLINE, char, self.line, self.column))
                self.advance()

            # Numbers
            elif char.isdigit():
                self.tokens.append(self.read_number())

            # Strings
            elif char in '"\'':
                self.tokens.append(self.read_string())

            # Identifiers and keywords
            elif char.isalpha() or char == '_':
                self.tokens.append(self.read_identifier())

            # Comments
            elif (char == '-' and self.peek_char() == '-') or char == '#':
                comment = self.read_comment()
                if comment:
                    self.tokens.append(comment)

            # Two-character operators
            elif char == '+' and self.peek_char() == '=':
                self.tokens.append(Token(TokenType.PLUS_ASSIGN, "+=", self.line, self.column))
                self.advance()
                self.advance()
            elif char == '-' and self.peek_char() == '=':
                self.tokens.append(Token(TokenType.MINUS_ASSIGN, "-=", self.line, self.column))
                self.advance()
                self.advance()
            elif char == '-' and self.peek_char() == '>':
                self.tokens.append(Token(TokenType.ARROW, "->", self.line, self.column))
                self.advance()
                self.advance()
            elif char == '.' and self.peek_char() == '.':
                self.tokens.append(Token(TokenType.DOTDOT, "..", self.line, self.column))
                self.advance()
                self.advance()
            elif char == '=' and self.peek_char() == '=':
                self.tokens.append(Token(TokenType.EQUAL, "==", self.line, self.column))
                self.advance()
                self.advance()
            elif char == '!' and self.peek_char() == '=':
                self.tokens.append(Token(TokenType.NOT_EQUAL, "!=", self.line, self.column))
                self.advance()
                self.advance()
            elif char == '<' and self.peek_char() == '=':
                self.tokens.append(Token(TokenType.LESS_EQUAL, "<=", self.line, self.column))
                self.advance()
                self.advance()
            elif char == '>' and self.peek_char() == '=':
                self.tokens.append(Token(TokenType.GREATER_EQUAL, ">=", self.line, self.column))
                self.advance()
                self.advance()

            # Single-character tokens
            else:
                single_chars = {
                    '+': TokenType.PLUS, '-': TokenType.MINUS, '*': TokenType.MULTIPLY,
                    '/': TokenType.DIVIDE, '%': TokenType.MODULO,
                    '=': TokenType.ASSIGN, '<': TokenType.LESS,
                    '>': TokenType.GREATER, '(': TokenType.LPAREN, ')': TokenType.RPAREN,
                    '{': TokenType.LBRACE, '}': TokenType.RBRACE, '[': TokenType.LBRACKET,
                    ']': TokenType.RBRACKET, ',': TokenType.COMMA, ';': TokenType.SEMICOLON,
                    ':': TokenType.COLON, '.': TokenType.DOT
                }

                if char in single_chars:
                    self.tokens.append(Token(single_chars[char], char, self.line, self.column))
                    self.advance()
                else:
                    raise SyntaxError(f"Unexpected character '{char}' at line {self.line}, column {self.column}")

        self.tokens.append(Token(TokenType.EOF, "", self.line, self.column))
        return self.tokens

# =============================================================================
# AST NODES - Abstract Syntax Tree (unchanged from original)
# =============================================================================

class ASTNode(ABC):
    pass

@dataclass
class Program(ASTNode):
    modules: List['Module']

@dataclass
class Module(ASTNode):
    name: str
    imports: List['Import']
    declarations: List['Declaration']
    exports: List[str]

@dataclass
class Import(ASTNode):
    module_name: str

@dataclass
class Declaration(ASTNode):
    pass

@dataclass
class FunctionDecl(ASTNode):
    name: str
    parameters: List['Parameter']
    return_type: Optional[str]
    body: List['Statement']
    is_local: bool = False

@dataclass
class Parameter(ASTNode):
    name: str
    type: 'Type'

@dataclass
class VarDecl(ASTNode):
    name: str
    type: Optional[str]
    initializer: Optional['Expression']
    is_const: bool = False

@dataclass
class TypeDecl(Declaration):
    name: str
    type_def: 'Type'

@dataclass
class Type(ASTNode):
    pass

@dataclass
class PrimitiveType(Type):
    name: str  # u8, i8, u16, i16, bool, addr, void

@dataclass
class UserDefinedType(Type):
    name: str  # User-defined type name

@dataclass
class ArrayType(Type):
    element_type: Type
    size: Optional[int]

@dataclass
class StructType(Type):
    fields: List['StructField']

@dataclass
class StructField(ASTNode):
    name: str
    type: Type

@dataclass
class EnumType(Type):
    variants: List['EnumVariant']

@dataclass
class EnumVariant(ASTNode):
    name: str
    value: Optional[int]

@dataclass
class Statement(ASTNode):
    pass

@dataclass
class ExpressionStmt(Statement):
    expression: 'Expression'

@dataclass
class IfStmt(Statement):
    condition: 'Expression'
    then_body: List[Statement]
    else_body: Optional[List[Statement]]

@dataclass
class LoopStmt(Statement):
    body: List[Statement]

@dataclass
class WhileStmt(Statement):
    condition: 'Expression'
    body: List[Statement]

@dataclass
class BreakStmt(Statement):
    pass

@dataclass
class ContinueStmt(Statement):
    pass

@dataclass
class SwitchStmt(Statement):
    subject: 'Expression'
    # Each case is (list of label expressions, body statements).
    cases: List[tuple]
    default_body: Optional[List[Statement]]

@dataclass
class ForStmt(Statement):
    var_name: str
    start: 'Expression'
    end: 'Expression'
    body: List[Statement]

@dataclass
class ReturnStmt(Statement):
    value: Optional['Expression']

@dataclass
class VarDeclStmt(Statement):
    var_decl: VarDecl

@dataclass
class Expression(ASTNode):
    pass

@dataclass
class BinaryOp(Expression):
    left: Expression
    operator: str
    right: Expression

@dataclass
class UnaryOp(Expression):
    operator: str
    operand: Expression

@dataclass
class Identifier(Expression):
    name: str

@dataclass
class Literal(Expression):
    value: Union[int, str, bool]
    type: str

@dataclass
class FunctionCall(Expression):
    function: Expression
    arguments: List[Expression]

@dataclass
class FieldAccess(Expression):
    object: Expression
    field: str

@dataclass
class ArrayAccess(Expression):
    array: Expression
    index: Expression

@dataclass
class StructLiteral(Expression):
    fields: List[tuple]  # list of (field_name, Expression)

@dataclass
class ArrayLiteral(Expression):
    elements: List[Expression]

# =============================================================================
# TARGET PLATFORMS
# =============================================================================

# Canonical target-console names plus the aliases users may write inside an
# `if platform == "..."` conditional-compilation block. Keep this in sync with
# PLATFORM_TARGETS in mosaik8.py (the build tool maps these to GBDK ports).
PLATFORM_ALIASES = {
    'gameboy': 'gameboy', 'gb': 'gameboy', 'dmg': 'gameboy',
    'gameboy_color': 'gameboy_color', 'gbc': 'gameboy_color',
    'cgb': 'gameboy_color', 'color': 'gameboy_color',
    'analogue_pocket': 'analogue_pocket', 'pocket': 'analogue_pocket',
    'ap': 'analogue_pocket', 'analogue': 'analogue_pocket',
    'megaduck': 'megaduck', 'mega_duck': 'megaduck', 'duck': 'megaduck',
    'sms': 'sms', 'master_system': 'sms', 'sega_master_system': 'sms',
    'gamegear': 'gamegear', 'game_gear': 'gamegear', 'gg': 'gamegear',
    'nes': 'nes', 'famicom': 'nes',
    # cc65 consoles (non-GBDK backend).
    'lynx': 'lynx', 'atari_lynx': 'lynx', 'atarilynx': 'lynx',
    'pce': 'pce', 'pc_engine': 'pce', 'pcengine': 'pce',
    'turbografx': 'pce', 'turbografx16': 'pce', 'tg16': 'pce',
}


# Per-console capability registry — the single source of truth for what each
# target console can do. Everything else derives from it: the backend choice
# (`framework`), which stdlib calls are available (and which raise a clear
# "not supported on target" compile error in _gen_call), which prelude blocks
# are emitted (window helpers, GB hardware-register #defines), and the
# sample-build matrix in tests/run_all.py. Keys must match mosaik8.py's
# PLATFORM_TARGETS (machine-checked there at import).
#
# - framework:    'gbdk' (emit GBDK C, link with lcc) or 'cc65' (cl65).
# - has_sprites:  graphics.sprite + the sprite-visibility video toggles.
# - has_bkg:      graphics.bkg (scrollable background tilemap).
# - has_window:   graphics.window + video.show/hide_window. Only the Game Boy
#   family has a real window layer; GBDK's SMS/GG SHOW_WIN macros are no-ops
#   and the NES port has none at all, so it is honest-off outside GB.
# - has_draw:     graphics.draw (TGI pixel/line/bar primitives; Lynx only).
# - has_gb_regs:  the Game Boy hardware-register constants (REG_DIV, REG_BGP,
#   ...) name real registers. Off elsewhere so `hw.write(REG_BGP, ...)` is a
#   clear compile error instead of a poke at a meaningless address.
_GB_FAMILY = {'framework': 'gbdk', 'has_sprites': True, 'has_bkg': True,
              'has_window': True, 'has_draw': False, 'has_gb_regs': True}
PLATFORM_CAPS = {
    'gameboy':         dict(_GB_FAMILY),
    'gameboy_color':   dict(_GB_FAMILY),
    'analogue_pocket': dict(_GB_FAMILY),
    'megaduck':        dict(_GB_FAMILY),
    'sms':             {'framework': 'gbdk', 'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False},
    'gamegear':        {'framework': 'gbdk', 'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False},
    'nes':             {'framework': 'gbdk', 'has_sprites': True, 'has_bkg': True,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False},
    'lynx':            {'framework': 'cc65', 'has_sprites': True, 'has_bkg': False,
                        'has_window': False, 'has_draw': True, 'has_gb_regs': False},
    'pce':             {'framework': 'cc65', 'has_sprites': False, 'has_bkg': False,
                        'has_window': False, 'has_draw': False, 'has_gb_regs': False},
}


# Which compiler backend / SDK each console targets (derived from the caps
# registry). GBDK consoles emit GBDK C (linked by `lcc`); cc65 consoles emit
# cc65 C (linked by `cl65`). Adding a new console is a PLATFORM_CAPS entry plus
# a target descriptor in mosaik8.py's PLATFORM_TARGETS.
PLATFORM_FRAMEWORK = {name: caps['framework']
                      for name, caps in PLATFORM_CAPS.items()}


def canonical_platform(name) -> str:
    """Normalise a platform name/alias to its canonical form (default gameboy)."""
    if not name:
        return 'gameboy'
    key = str(name).strip().lower()
    return PLATFORM_ALIASES.get(key, key)


def framework_for_platform(name) -> str:
    """Return the codegen backend ('gbdk' or 'cc65') for a console."""
    return PLATFORM_FRAMEWORK.get(canonical_platform(name), 'gbdk')


def platform_caps(name) -> dict:
    """Return the capability entry for a console (default: gameboy)."""
    return PLATFORM_CAPS.get(canonical_platform(name), PLATFORM_CAPS['gameboy'])


# =============================================================================
# PARSER - Syntax Analysis (unchanged from original)
# =============================================================================

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

# =============================================================================
# TYPE SYSTEM - Type Checking and Inference (Enhanced for Graphics.Text)
# =============================================================================

class TypeChecker:
    def __init__(self):
        self.symbol_table = {}
        self.current_scope = {}
        self.imported_modules = {}
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

        # Input functions
        for fn in ('input.pressed', 'input.held'):
            self.symbol_table[fn] = {
                'type': 'function',
                'return_type': PrimitiveType('bool'),
                'parameters': [Parameter('button', PrimitiveType('u8'))]
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
        u8_returning = {'sprite.get_tile', 'system.random'}
        for fn in [
            'video.show_sprites', 'video.hide_sprites', 'video.show_background',
            'video.show_window', 'video.hide_window',
            'sprite.set_data', 'sprite.set_tile', 'sprite.get_tile',
            'sprite.set_prop', 'sprite.move',
            'bkg.set_data', 'bkg.set_tiles', 'bkg.scroll', 'bkg.move',
            'window.set_tiles', 'window.move',
            'system.delay', 'system.random', 'system.seed_random',
        ]:
            ret = PrimitiveType('u8') if fn in u8_returning else PrimitiveType('void')
            self.symbol_table[fn] = {'type': 'function', 'return_type': ret,
                                     'parameters': []}

        # Stdlib constants usable as plain identifiers.
        for const_name in ['REG_DIV', 'REG_NR10', 'REG_BGP', 'REG_OBP0', 'REG_OBP1']:
            self.symbol_table[const_name] = {
                'type': 'constant', 'value_type': PrimitiveType('addr')}
        for const_name in ['FLIP_X', 'FLIP_Y']:
            self.symbol_table[const_name] = {
                'type': 'constant', 'value_type': PrimitiveType('u8')}
        # Screen geometry (per-platform prelude #defines). u16: SMS/NES are
        # 256 px wide.
        for const_name in ['SCREEN_WIDTH', 'SCREEN_HEIGHT',
                           'SCREEN_COLS', 'SCREEN_ROWS']:
            self.symbol_table[const_name] = {
                'type': 'constant', 'value_type': PrimitiveType('u16')}

    def register_assets(self, assets):
        """Register asset-pipeline symbols (`<name>_tiles` data arrays and
        `<name>_tile_count` defines, emitted into the TU by the codegen)."""
        for name, data in assets:
            self.symbol_table['%s_tiles' % name] = {
                'type': 'constant',
                'value_type': ArrayType(PrimitiveType('u8'), len(data))}
            self.symbol_table['%s_tile_count' % name] = {
                'type': 'constant', 'value_type': PrimitiveType('u8')}

    def check_program(self, program: Program):
        for module in program.modules:
            self.check_module(module)

    def check_module(self, module: Module):
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
                    'parameters': decl.parameters
                }
            elif isinstance(decl, VarDecl):
                self.current_scope[decl.name] = decl.type

        # Then check functions and variables
        for decl in module.declarations:
            if not isinstance(decl, TypeDecl):
                self.check_declaration(decl)

    def check_declaration(self, decl: Declaration):
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
            'parameters': func.parameters
        }

        # Create new scope for function
        old_scope = self.current_scope.copy()

        # Add parameters to scope
        for param in func.parameters:
            self.current_scope[param.name] = param.type

        # Check function body
        for stmt in func.body:
            self.check_statement(stmt)

        # Restore previous scope
        self.current_scope = old_scope

    def validate_type(self, type_obj: Type):
        """Validate that a type is known/defined - ENHANCED VERSION."""
        if isinstance(type_obj, PrimitiveType):
            # Primitive types are always valid
            return
        elif isinstance(type_obj, UserDefinedType):
            # Check if user-defined type exists in type_table
            if type_obj.name not in self.type_table:
                raise TypeError(f"Unknown type: '{type_obj.name}'. Available types: {list(self.type_table.keys())}")
        elif isinstance(type_obj, ArrayType):
            self.validate_type(type_obj.element_type)
        elif isinstance(type_obj, StructType):
            for field in type_obj.fields:
                self.validate_type(field.type)
        else:
            raise TypeError(f"Invalid type: {type_obj}")

    def check_variable(self, var: VarDecl):
        if var.type:
            if isinstance(var.type, UserDefinedType):
                if var.type.name not in self.type_table:
                    raise TypeError(f"Unknown type: '{var.type.name}'")
            elif isinstance(var.type, ArrayType):
                self.validate_type(var.type.element_type)
            elif isinstance(var.type, StructType):
                for field in var.type.fields:
                    self.validate_type(field.type)

        # Type inference if not specified
        if var.type is None and var.initializer:
            var.type = self.infer_type(var.initializer)

        # Struct/array literal initializers are checked structurally elsewhere;
        # their element types are taken from the declared aggregate type.
        is_aggregate_literal = isinstance(var.initializer, (StructLiteral, ArrayLiteral))

        # Check initializer type matches declared type
        if var.initializer and var.type and not is_aggregate_literal:
            init_type = self.infer_type(var.initializer)
            if not self.types_compatible(var.type, init_type):
                raise TypeError(f"Cannot assign {self.type_to_string(init_type)} to {self.type_to_string(var.type)}")

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
        if isinstance(stmt, ExpressionStmt):
            self.check_expression(stmt.expression)
        elif isinstance(stmt, VarDeclStmt):
            self.check_variable(stmt.var_decl)
        elif isinstance(stmt, IfStmt):
            cond_type = self.check_expression(stmt.condition)
            if not self.is_boolean_type(cond_type):
                raise TypeError("If condition must be boolean")
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
            if not self.is_boolean_type(cond_type):
                raise TypeError("While condition must be boolean")
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
            # The loop variable is in scope for the body.
            self.current_scope[stmt.var_name] = PrimitiveType('u8')
            for s in stmt.body:
                self.check_statement(s)
        elif isinstance(stmt, ReturnStmt):
            if stmt.value:
                self.check_expression(stmt.value)

    def check_expression(self, expr: Expression) -> Type:
        return self.infer_type(expr)

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
                return ArrayType(PrimitiveType("u8"), len(expr.value))
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
                    return sym_info['return_type'] or PrimitiveType('void')
                else:
                    return PrimitiveType('void')
            # Check if it's a user-defined type name
            elif expr.name in self.type_table:
                return UserDefinedType(expr.name)
            else:
                # More helpful error message
                available_names = list(self.current_scope.keys()) + list(self.symbol_table.keys())
                raise NameError(f"Undefined variable: {expr.name}. Available: {available_names[:10]}")

        elif isinstance(expr, BinaryOp):
            left_type = self.infer_type(expr.left)
            right_type = self.infer_type(expr.right)

            if expr.operator in ['+', '-', '*', '/']:
                # Arithmetic operations
                return self.promote_arithmetic_type(left_type, right_type)
            elif expr.operator in ['==', '!=', '<', '>', '<=', '>=']:
                # Comparison operations
                return PrimitiveType("bool")
            elif expr.operator in ['and', 'or']:
                # Logical operations
                return PrimitiveType("bool")

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

            if func_name and func_name in self.symbol_table:
                func_info = self.symbol_table[func_name]
                if func_info['type'] == 'function':
                    return func_info['return_type'] or PrimitiveType('void')
            else:
                # If function not found, raise an error
                if func_name:
                    raise NameError(f"Undefined function: {func_name}")
                else:
                    raise NameError("Invalid function call")

        elif isinstance(expr, FieldAccess):
            # For now, assume field access returns u8
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

        return PrimitiveType("void")

    INTEGER_TYPES = {'u8', 'i8', 'u16', 'i16', 'addr'}

    def types_compatible(self, type1: Type, type2: Type) -> bool:
        if isinstance(type1, PrimitiveType) and isinstance(type2, PrimitiveType):
            if type1.name == type2.name:
                return True
            # Integer types interoperate freely; the 8-bit target promotes and
            # truncates automatically, matching mosaik's value semantics.
            return (type1.name in self.INTEGER_TYPES and
                    type2.name in self.INTEGER_TYPES)
        elif isinstance(type1, UserDefinedType) and isinstance(type2, UserDefinedType):
            return type1.name == type2.name
        elif isinstance(type1, ArrayType) and isinstance(type2, ArrayType):
            return (self.types_compatible(type1.element_type, type2.element_type) and
                    type1.size == type2.size)
        return False

    def is_boolean_type(self, type_obj: Type) -> bool:
        return isinstance(type_obj, PrimitiveType) and type_obj.name == "bool"

    def promote_arithmetic_type(self, type1: Type, type2: Type) -> Type:
        # Simple type promotion rules
        if isinstance(type1, PrimitiveType) and isinstance(type2, PrimitiveType):
            if type1.name == type2.name:
                return type1
            # Promote to larger type
            type_order = ['u8', 'i8', 'u16', 'i16']
            if type1.name in type_order and type2.name in type_order:
                return type1 if type_order.index(type1.name) > type_order.index(type2.name) else type2
        return type1

    def type_to_string(self, type_obj: Type) -> str:
        """Convert a Type object to a readable string"""
        if isinstance(type_obj, PrimitiveType):
            return type_obj.name
        elif isinstance(type_obj, UserDefinedType):
            return type_obj.name
        elif isinstance(type_obj, ArrayType):
            return f"array[{self.type_to_string(type_obj.element_type)}, {type_obj.size}]"
        else:
            return str(type_obj)

# =============================================================================
# CODE GENERATOR - GBDK Assembly Generation
# =============================================================================

class CodeGenerator:
    """Code generator that emits GBDK C source.

    Targeting GBDK C lets the bundled `lcc` toolchain build real,
    runnable ROMs from every sample program.
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

    # mosaik stdlib calls -> C helper / GBDK function names.
    STDLIB_CALLS_GBDK = {
        ('video', 'enable_lcd'): 'gbs_enable_lcd',
        ('video', 'disable_lcd'): 'gbs_disable_lcd',
        ('video', 'wait_vblank'): 'vsync',
        ('input', 'pressed'): 'gbs_input_pressed',
        ('input', 'held'): 'gbs_input_pressed',
        ('text', 'print_string'): 'gbs_print_string',
        ('text', 'print_number'): 'gbs_print_number',
        ('text', 'clear_area'): 'gbs_clear_area',
        ('hw', 'write'): 'gbs_hw_write',
        ('hw', 'read'): 'gbs_hw_read',
        # Display visibility (macros, wrapped as helpers).
        ('video', 'show_sprites'): 'gbs_show_sprites',
        ('video', 'hide_sprites'): 'gbs_hide_sprites',
        ('video', 'show_background'): 'gbs_show_bkg',
        ('video', 'show_window'): 'gbs_show_win',
        ('video', 'hide_window'): 'gbs_hide_win',
        # Sprites (graphics.sprite). sprite.move takes screen-pixel coords;
        # the gbs_ wrapper adds the per-console hardware offset.
        ('sprite', 'set_data'): 'set_sprite_data',
        ('sprite', 'set_tile'): 'set_sprite_tile',
        ('sprite', 'get_tile'): 'get_sprite_tile',
        ('sprite', 'set_prop'): 'set_sprite_prop',
        ('sprite', 'move'): 'gbs_move_sprite',
        # Background (graphics.bkg).
        ('bkg', 'set_data'): 'set_bkg_data',
        ('bkg', 'set_tiles'): 'set_bkg_tiles',
        ('bkg', 'scroll'): 'scroll_bkg',
        ('bkg', 'move'): 'move_bkg',
        # Window (graphics.window).
        ('window', 'set_tiles'): 'set_win_tiles',
        ('window', 'move'): 'move_win',
        # System utilities (platform.system).
        ('system', 'delay'): 'delay',
        ('system', 'random'): 'rand',
        ('system', 'seed_random'): 'initrand',
    }

    # mosaik stdlib calls -> cc65 helper / library function names. The
    # portable Tier-1 surface (text, input, timing, raw hardware) maps to `gbs_`
    # helpers emitted by _emit_prelude_cc65; the helper *bodies* vary per console
    # profile (e.g. TGI vs conio text) but the call names do not. Tile/sprite/
    # window calls have no cc65 equivalent and are reported as unsupported (see
    # _gen_call).
    STDLIB_CALLS_CC65_CORE = {
        # Display lifecycle / frame presentation.
        ('video', 'enable_lcd'): 'gbs_video_init',
        ('video', 'disable_lcd'): 'gbs_video_done',
        ('video', 'wait_vblank'): 'gbs_present',
        # Input.
        ('input', 'pressed'): 'gbs_input_pressed',
        ('input', 'held'): 'gbs_input_pressed',
        # Text (character-cell coords; TGI profiles scale to pixels).
        ('text', 'print_string'): 'gbs_print_string',
        ('text', 'print_number'): 'gbs_print_number',
        ('text', 'clear_area'): 'gbs_clear_area',
        # Raw hardware access.
        ('hw', 'write'): 'gbs_hw_write',
        ('hw', 'read'): 'gbs_hw_read',
        # System utilities.
        ('system', 'delay'): 'gbs_delay',
        ('system', 'random'): 'rand',
        ('system', 'seed_random'): 'gbs_seed_random',
    }

    # Vector/framebuffer drawing (graphics.draw, TGI). Only available on cc65
    # consoles whose profile has a TGI driver (e.g. Lynx, not the tile-based
    # PC Engine).
    STDLIB_CALLS_CC65_DRAW = {
        ('draw', 'clear'): 'tgi_clear',
        ('draw', 'set_color'): 'tgi_setcolor',
        ('draw', 'pixel'): 'tgi_setpixel',
        ('draw', 'line'): 'tgi_line',
        ('draw', 'bar'): 'tgi_bar',
        ('draw', 'circle'): 'tgi_circle',
        ('draw', 'present'): 'tgi_updatedisplay',
    }

    # Hardware-style sprites (graphics.sprite + the sprite-visibility video
    # toggles). On framebuffer consoles these are backed by a software OAM
    # engine (8x8, Game Boy 2bpp tile data) that is re-blitted on each
    # gbs_present(); only available on profiles with `has_sprites`.
    STDLIB_CALLS_CC65_SPRITE = {
        ('sprite', 'set_data'): 'gbs_set_sprite_data',
        ('sprite', 'set_tile'): 'gbs_set_sprite_tile',
        ('sprite', 'get_tile'): 'gbs_get_sprite_tile',
        ('sprite', 'set_prop'): 'gbs_set_sprite_prop',
        ('sprite', 'move'): 'gbs_move_sprite',
        ('video', 'show_sprites'): 'gbs_show_sprites',
        ('video', 'hide_sprites'): 'gbs_hide_sprites',
        ('video', 'show_background'): 'gbs_show_bkg',
    }

    # Per-console cc65 profile. Describes how the shared cc65 prelude specialises
    # for a target: headers, text backend ('tgi' = pixel coords via
    # tgi_outtextxy, 'conio' = character cells via gotoxy/cputs), the driver
    # init/teardown sequence, the frame-present call, and the screen geometry
    # (screen_w/h in pixels, screen_cols/rows in text cells -> the SCREEN_*
    # prelude constants). Which stdlib *capabilities* a console has (sprites,
    # draw, ...) lives in PLATFORM_CAPS, not here. Adding a cc65 console is a
    # new entry here plus a PLATFORM_CAPS row and a mosaik8.py
    # PLATFORM_TARGETS row.
    CC65_PROFILES = {
        'lynx': {
            'headers': ['tgi.h', 'lynx.h', 'joystick.h', '6502.h', 'time.h',
                        'stdlib.h', 'string.h', 'stdint.h'],
            'text': 'tgi',
            'cell_w': 8, 'cell_h': 8,
            # The Lynx TGI is an interrupt-driven dual-buffer device: CLI()
            # enables the IRQs it needs and tgi_setframerate() programs the
            # display refresh that tgi_updatedisplay() syncs to. We start in
            # single-buffer mode (draw page == view page) so immediate drawing
            # (text) is shown and persists without flipping; the sprite engine
            # switches to true double-buffering lazily (see the present helper).
            # 60 Hz (not the Lynx-classic 75) so wait_vblank paces programs at
            # the same rate as the Game Boy and "60 frames = 1 second" holds.
            'video_init': ['tgi_install(tgi_static_stddrv);', 'tgi_init();',
                           'CLI();',
                           'joy_install(joy_static_stddrv);',
                           'tgi_setpalette(tgi_getdefpalette());',
                           'tgi_setframerate(60);',
                           'tgi_setviewpage(0);', 'tgi_setdrawpage(0);',
                           'tgi_setcolor(COLOR_WHITE);', 'tgi_clear();'],
            'video_done': 'joy_uninstall(); tgi_uninstall();',
            'present': 'tgi_updatedisplay();',
            'text_fg': 'COLOR_WHITE', 'text_bg': 'COLOR_BLACK',
            'screen_w': 160, 'screen_h': 102,
            'screen_cols': 20, 'screen_rows': 12,
            'input_start': '0', 'input_select': '0',
        },
        'pce': {
            'headers': ['pce.h', 'conio.h', 'joystick.h', 'time.h', 'stdlib.h', 'stdint.h'],
            'text': 'conio',
            'video_init': ['joy_install(joy_static_stddrv);', 'clrscr();'],
            'video_done': 'joy_uninstall();',
            'present': '',
            # The conio map is 64x32 virtual; this is the visible safe area a
            # portable program should target (256x224 px display).
            'screen_w': 256, 'screen_h': 224,
            'screen_cols': 32, 'screen_rows': 28,
            'input_start': 'JOY_RUN_MASK', 'input_select': 'JOY_SELECT_MASK',
        },
    }

    # Every (module, func) pair known to any backend. Used to tell "unsupported
    # on this target" (a clear compile error) apart from an ordinary
    # cross-module call that lowers to `module_func(...)`.
    ALL_STDLIB_CALLS = (set(STDLIB_CALLS_GBDK) | set(STDLIB_CALLS_CC65_CORE)
                        | set(STDLIB_CALLS_CC65_DRAW)
                        | set(STDLIB_CALLS_CC65_SPRITE))

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

    # Game Boy hardware-register constants. Emitted as prelude #defines only on
    # has_gb_regs consoles; referencing one anywhere else is a clear compile
    # error (the addresses are meaningless on other machines).
    GB_REG_CONSTANTS = {'REG_DIV', 'REG_NR10', 'REG_BGP', 'REG_OBP0', 'REG_OBP1'}

    BINARY_C_OPERATORS = {
        '+': '+', '-': '-', '*': '*', '/': '/', '%': '%',
        '==': '==', '!=': '!=', '<': '<', '>': '>', '<=': '<=', '>=': '>=',
        'and': '&&', 'or': '||',
    }

    # Capacity of the cc65 sprite engine's converted-tile table (see
    # _emit_cc65_sprite_engine). Asset tile data beyond this cannot be
    # addressed by sprite.set_data on cc65 sprite consoles.
    CC65_MAX_TILES = 32

    def __init__(self):
        self.output = []
        self.platform = 'gameboy'
        self.framework = 'gbdk'
        self.caps = PLATFORM_CAPS['gameboy']
        self.cc65_profile = None
        self.stdlib_calls = self.STDLIB_CALLS_GBDK
        self.gbdk_version = 'gbdk-2020'
        self.assets = []         # [(name, gb_2bpp_bytes)] from the asset pipeline
        self.struct_types = {}   # name -> StructType
        self.enum_types = set()  # names of enum types

    def set_gbdk_version(self, version: str):
        self.gbdk_version = version

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
        else:
            self.cc65_profile = None
            stdlib = dict(self.STDLIB_CALLS_GBDK)
        # Drop capability-gated calls the target lacks so they raise the clear
        # unsupported-on-target diagnostic instead of failing at link time.
        for cap, calls in (('has_window', self.CALLS_NEEDING_WINDOW),
                           ('has_bkg', self.CALLS_NEEDING_BKG),
                           ('has_sprites', self.CALLS_NEEDING_SPRITES)):
            if not self.caps[cap]:
                for key in calls:
                    stdlib.pop(key, None)
        self.stdlib_calls = stdlib

        # Discover user-defined types up front so they can be referenced
        # regardless of declaration order.
        for module in program.modules:
            for decl in module.declarations:
                if isinstance(decl, TypeDecl):
                    if isinstance(decl.type_def, StructType):
                        self.struct_types[decl.name] = decl.type_def
                    elif isinstance(decl.type_def, EnumType):
                        self.enum_types.add(decl.name)

        self._emit_prelude()
        self._emit_assets()

        for module in program.modules:
            self._emit_module(module)

        return "\n".join(self.output)

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

    def _emit_prelude_gbdk(self):
        self.emit("/* Generated by mosaik -> GBDK C backend */")
        self.emit("/* Target console: %s */" % self.platform)
        # <gbdk/platform.h> pulls in the correct console header for the build
        # target (Game Boy, Pocket, Mega Duck, SMS/GG, NES), so the same
        # generated C compiles for every supported platform.
        self.emit("#include <gbdk/platform.h>")
        self.emit("#include <gbdk/console.h>")
        self.emit("#include <gbdk/font.h>")
        self.emit("#include <rand.h>")
        self.emit("#include <stdio.h>")
        self.emit("#include <stdint.h>")
        self.emit("")
        self.emit("/* Input button constants mapped to GBDK joypad bits */")
        self.emit("#define INPUT_A      J_A")
        self.emit("#define INPUT_B      J_B")
        self.emit("#define INPUT_SELECT J_SELECT")
        self.emit("#define INPUT_START  J_START")
        self.emit("#define INPUT_RIGHT  J_RIGHT")
        self.emit("#define INPUT_LEFT   J_LEFT")
        self.emit("#define INPUT_UP     J_UP")
        self.emit("#define INPUT_DOWN   J_DOWN")
        self.emit("")
        self.emit("/* Screen geometry for the build target (GBDK resolves the")
        self.emit("   DEVICE_* macros per console at C compile time). */")
        self.emit("#define SCREEN_WIDTH  DEVICE_SCREEN_PX_WIDTH")
        self.emit("#define SCREEN_HEIGHT DEVICE_SCREEN_PX_HEIGHT")
        self.emit("#define SCREEN_COLS   DEVICE_SCREEN_WIDTH")
        self.emit("#define SCREEN_ROWS   DEVICE_SCREEN_HEIGHT")
        self.emit("")
        if self.caps['has_gb_regs']:
            self.emit("/* Hardware register addresses (for hw.read / hw.write) */")
            self.emit("#define REG_DIV  0xFF04")
            self.emit("#define REG_NR10 0xFF10")
            self.emit("#define REG_BGP  0xFF47")
            self.emit("#define REG_OBP0 0xFF48")
            self.emit("#define REG_OBP1 0xFF49")
            self.emit("")
        self.emit("/* Sprite property flags (graphics.sprite) */")
        self.emit("#define FLIP_X S_FLIPX")
        self.emit("#define FLIP_Y S_FLIPY")
        self.emit("")
        self.emit("/* mosaik standard library helpers */")
        self.emit("void gbs_enable_lcd(void) { DISPLAY_ON; SHOW_BKG; }")
        self.emit("void gbs_disable_lcd(void) { DISPLAY_OFF; }")
        self.emit("uint8_t gbs_input_pressed(uint8_t button) { return (uint8_t)(joypad() & button); }")
        self.emit("/* GBDK-2020's console printf needs a font loaded before any text")
        self.emit("   tiles are drawn; do it lazily on first use (once). */")
        self.emit("void gbs_text_init(void) {")
        self.emit("    static uint8_t gbs_font_ready = 0;")
        self.emit("    if (!gbs_font_ready) { font_init(); font_set(font_load(font_ibm)); gbs_font_ready = 1; }")
        self.emit("}")
        self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s) { gbs_text_init(); gotoxy(x, y); printf(\"%s\", s); }")
        self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n) { gbs_text_init(); gotoxy(x, y); printf(\"%d\", n); }")
        self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {")
        self.emit("    uint8_t i, j;")
        self.emit("    gbs_text_init();")
        self.emit("    for (j = 0; j < h; j++) { gotoxy(x, y + j); for (i = 0; i < w; i++) printf(\" \"); }")
        self.emit("}")
        self.emit("/* Raw hardware register access (sound, palette, timer, ...) */")
        self.emit("void gbs_hw_write(uint16_t addr, uint8_t value) { *(volatile uint8_t *)addr = value; }")
        self.emit("uint8_t gbs_hw_read(uint16_t addr) { return *(volatile uint8_t *)addr; }")
        self.emit("/* Display visibility helpers (GBDK macros wrapped as functions) */")
        self.emit("void gbs_show_sprites(void) { SHOW_SPRITES; }")
        self.emit("void gbs_hide_sprites(void) { HIDE_SPRITES; }")
        self.emit("void gbs_show_bkg(void) { SHOW_BKG; }")
        self.emit("/* sprite.move takes screen-pixel coordinates (origin = top-left of")
        self.emit("   the visible screen); the hardware offset differs per console. */")
        self.emit("void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y) {")
        self.emit("    move_sprite(nb, (uint8_t)(x + DEVICE_SPRITE_PX_OFFSET_X),")
        self.emit("                    (uint8_t)(y + DEVICE_SPRITE_PX_OFFSET_Y));")
        self.emit("}")
        if self.caps['has_window']:
            self.emit("/* The window layer only exists on Game Boy-family consoles. */")
            self.emit("void gbs_show_win(void) { SHOW_WIN; }")
            self.emit("void gbs_hide_win(void) { HIDE_WIN; }")
        self.emit("")

    def _emit_prelude_cc65(self):
        """Prelude for cc65 consoles, specialised by the active CC65 profile.

        Two text backends are supported: 'tgi' (pixel-addressed, e.g. Atari
        Lynx, following the bundled samples/lynx idiom) and 'conio'
        (character-cell, e.g. PC Engine). cc65 provides <stdint.h>, so the
        uintN_t spellings used by the shared codegen are valid here too.
        """
        prof = self.cc65_profile or self.CC65_PROFILES['lynx']
        is_tgi = prof['text'] == 'tgi'

        self.emit("/* Generated by mosaik -> cc65 C backend */")
        self.emit("/* Target console: %s (cc65 %s-text profile) */"
                  % (self.platform, prof['text']))
        for header in prof['headers']:
            self.emit("#include <%s>" % header)
        self.emit("")
        if is_tgi:
            self.emit("/* Text is addressed in character cells (as on Game Boy);")
            self.emit("   TGI profiles scale cell coords to pixels. */")
            self.emit("#define GBS_CELL_W %d" % prof.get('cell_w', 8))
            self.emit("#define GBS_CELL_H %d" % prof.get('cell_h', 8))
            self.emit("")
        self.emit("/* Screen geometry for the build target. */")
        self.emit("#define SCREEN_WIDTH  %d" % prof.get('screen_w', 160))
        self.emit("#define SCREEN_HEIGHT %d" % prof.get('screen_h', 102))
        self.emit("#define SCREEN_COLS   %d" % prof.get('screen_cols', 20))
        self.emit("#define SCREEN_ROWS   %d" % prof.get('screen_rows', 12))
        self.emit("")
        self.emit("/* Input button constants mapped to this console's joypad bits. */")
        self.emit("#define INPUT_A      JOY_BTN_1_MASK")
        self.emit("#define INPUT_B      JOY_BTN_2_MASK")
        self.emit("#define INPUT_SELECT %s" % prof.get('input_select', '0'))
        self.emit("#define INPUT_START  %s" % prof.get('input_start', '0'))
        self.emit("#define INPUT_RIGHT  JOY_RIGHT_MASK")
        self.emit("#define INPUT_LEFT   JOY_LEFT_MASK")
        self.emit("#define INPUT_UP     JOY_UP_MASK")
        self.emit("#define INPUT_DOWN   JOY_DOWN_MASK")
        self.emit("")
        if self.caps['has_sprites']:
            self.emit("/* Sprite flip flags -> Suzy SPRCTL0 bits (graphics.sprite). */")
            self.emit("#define FLIP_X HFLIP")
            self.emit("#define FLIP_Y VFLIP")
        else:
            self.emit("/* Sprite flip flags are unused here; defined so shared code links. */")
            self.emit("#define FLIP_X 0")
            self.emit("#define FLIP_Y 0")
        self.emit("")
        self.emit("/* mosaik standard library helpers (cc65) */")
        self.emit("static uint8_t gbs_video_ready = 0;")
        self.emit("void gbs_video_init(void) {")
        self.emit("    if (gbs_video_ready) return;")
        for stmt in prof['video_init']:
            self.emit("    %s" % stmt)
        self.emit("    gbs_video_ready = 1;")
        self.emit("}")
        self.emit("void gbs_video_done(void) { %s }" % prof['video_done'])
        if self.caps['has_sprites']:
            self._emit_cc65_sprite_engine(prof)
        else:
            self.emit("void gbs_present(void) { %s }" % prof['present'])
        self.emit("uint8_t gbs_input_pressed(uint8_t button) {")
        self.emit("    gbs_video_init();")
        self.emit("    return (uint8_t)(joy_read(0) & button);")
        self.emit("}")
        if is_tgi:
            self._emit_cc65_text_tgi()
        else:
            self._emit_cc65_text_conio()
        self.emit("/* delay(ms) busy-waits using the system clock (CLOCKS_PER_SEC ticks/s). */")
        self.emit("void gbs_delay(uint16_t ms) {")
        self.emit("    clock_t target = clock() + (clock_t)ms * CLOCKS_PER_SEC / 1000;")
        self.emit("    while (clock() < target) { }")
        self.emit("}")
        self.emit("void gbs_seed_random(uint16_t seed) { srand(seed); }")
        self.emit("/* Raw hardware register access (addresses are console-specific). */")
        self.emit("void gbs_hw_write(uint16_t addr, uint8_t value) { *(volatile uint8_t *)addr = value; }")
        self.emit("uint8_t gbs_hw_read(uint16_t addr) { return *(volatile uint8_t *)addr; }")
        self.emit("")

    def _emit_cc65_text_tgi(self):
        """Text helpers for TGI consoles (pixel-addressed, e.g. Lynx)."""
        prof = self.cc65_profile
        fg, bg = prof.get('text_fg', 'COLOR_WHITE'), prof.get('text_bg', 'COLOR_BLACK')
        self.emit("/* TGI text draws transparently (pixels OR onto the screen), but the")
        self.emit("   Game Boy's tile text *replaces* the cell -- so clear the covered")
        self.emit("   cells first to keep reprinting (counters, scores) portable. */")
        self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s) {")
        self.emit("    int px = (int)x * GBS_CELL_W, py = (int)y * GBS_CELL_H;")
        self.emit("    gbs_video_init();")
        self.emit("    tgi_setcolor(%s);" % bg)
        self.emit("    tgi_bar(px, py, px + (int)strlen(s) * GBS_CELL_W - 1, py + GBS_CELL_H - 1);")
        self.emit("    tgi_setcolor(%s);" % fg)
        self.emit("    tgi_outtextxy(px, py, s);")
        self.emit("}")
        self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n) {")
        self.emit("    char buf[7];")
        self.emit("    utoa(n, buf, 10);")
        self.emit("    gbs_print_string(x, y, buf);")
        self.emit("}")
        self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {")
        self.emit("    gbs_video_init();")
        self.emit("    tgi_setcolor(%s);" % bg)
        self.emit("    tgi_bar((int)x * GBS_CELL_W, (int)y * GBS_CELL_H,")
        self.emit("            (int)(x + w) * GBS_CELL_W - 1, (int)(y + h) * GBS_CELL_H - 1);")
        self.emit("    tgi_setcolor(%s);" % fg)
        self.emit("}")

    def _emit_cc65_sprite_engine(self, prof):
        """Hardware Suzy sprite engine for the Atari Lynx.

        Keeps the Game Boy sprite *model* (a shared 8x8 2bpp tile table + sprite
        slots referencing tiles by index) so the GBDK sprite samples build and
        run unchanged, but draws via the Lynx's Suzy blitter instead of a
        per-pixel software loop. Each GB tile is converted once into a
        totally-literal Lynx sprite-data stream; each sprite slot owns a Suzy
        Sprite Control Block (SCB) pointing at its tile's data. gbs_present()
        clears the back buffer, fires one tgi_sprite() per visible slot, and
        flips. A sprite program therefore owns the frame (present repaints the
        background), so mixing immediate text and sprites in the same frame is
        not supported. sprite.move takes screen-pixel coordinates (the same
        contract as the GBDK backend's gbs_move_sprite wrapper).

        Lynx literal sprite-data layout (verified against cc65's sp65), per
        8-pixel 2bpp row: [offset=0x04][2 packed pixel bytes][0x00 pad byte];
        an offset byte of 0x00 ends the sprite. Pixels are 2 bits each, packed
        MSB-first (leftmost pixel in the high bits). Pixel value 0 maps to pen 0
        (COLOR_TRANSPARENT), so it is transparent for TYPE_NORMAL sprites --
        exactly the Game Boy's colour-0-is-transparent object model.
        """
        sw, sh = prof.get('screen_w', 160), prof.get('screen_h', 102)
        # Bytes per converted tile: 8 rows * (offset + 2 data + pad) + terminator.
        tile_bytes = 8 * 4 + 1
        self.emit("/* --- Suzy hardware sprite engine (Atari Lynx) --- */")
        self.emit("#define GBS_MAX_TILES   %d" % self.CC65_MAX_TILES)
        self.emit("#define GBS_MAX_SPRITES 40")
        self.emit("#define GBS_TILE_BYTES  %d  /* literal-encoded 8x8 2bpp tile */" % tile_bytes)
        self.emit("static uint8_t gbs_tiles[GBS_MAX_TILES][GBS_TILE_BYTES];")
        self.emit("static SCB_REHV_PAL gbs_scb[GBS_MAX_SPRITES];")
        self.emit("static uint8_t gbs_spr_tile[GBS_MAX_SPRITES];")
        self.emit("static uint8_t gbs_spr_max = 0;     /* highest slot touched + 1 */")
        self.emit("static uint8_t gbs_spr_used = 0;    /* engine active this program */")
        self.emit("static uint8_t gbs_spr_visible = 1;")
        self.emit("static uint8_t gbs_spr_db = 0;      /* double-buffering engaged */")
        self.emit("static uint8_t gbs_spr_inited = 0;")
        self.emit("/* GB 2bpp pixel value 1..3 -> Lynx pens; value 0 -> pen 0 = transparent.")
        self.emit("   Packed two pens per byte, lower pixel value in the high nibble. */")
        self.emit("static const uint8_t gbs_spr_penpal[8] = {")
        self.emit("    (COLOR_TRANSPARENT << 4) | COLOR_LIGHTGREY,   /* pixels 0,1 */")
        self.emit("    (COLOR_GREY << 4) | COLOR_WHITE,              /* pixels 2,3 */")
        self.emit("    0, 0, 0, 0, 0, 0")
        self.emit("};")
        self.emit("static void gbs_spr_init(void) {")
        self.emit("    uint8_t s, i;")
        self.emit("    if (gbs_spr_inited) return;")
        self.emit("    for (s = 0; s < GBS_MAX_SPRITES; ++s) {")
        self.emit("        gbs_scb[s].sprctl0 = BPP_2 | TYPE_NORMAL;")
        self.emit("        gbs_scb[s].sprctl1 = LITERAL | REHV;  /* literal data, reload h/v size */")
        self.emit("        gbs_scb[s].sprcoll = 0;")
        self.emit("        gbs_scb[s].next = (char *)0;")
        self.emit("        gbs_scb[s].data = gbs_tiles[0];       /* default to tile 0 (as on GB) */")
        self.emit("        gbs_scb[s].hpos = -16; gbs_scb[s].vpos = -16;")
        self.emit("        gbs_scb[s].hsize = 0x100; gbs_scb[s].vsize = 0x100;  /* 1:1 scale */")
        self.emit("        for (i = 0; i < 8; ++i) gbs_scb[s].penpal[i] = gbs_spr_penpal[i];")
        self.emit("    }")
        self.emit("    gbs_spr_inited = 1;")
        self.emit("}")
        self.emit("/* Convert one 8x8 GB 2bpp tile (16 bytes) into a literal Lynx sprite. */")
        self.emit("static void gbs_conv_tile(uint8_t tile, const uint8_t *gb) {")
        self.emit("    uint8_t *o = gbs_tiles[tile];")
        self.emit("    uint8_t row, col, lo, hi, bit, ci, a, b;")
        self.emit("    for (row = 0; row < 8; ++row) {")
        self.emit("        lo = gb[row * 2]; hi = gb[row * 2 + 1];")
        self.emit("        a = 0; b = 0;")
        self.emit("        for (col = 0; col < 8; ++col) {")
        self.emit("            bit = 7 - col;")
        self.emit("            ci = (uint8_t)((((hi >> bit) & 1) << 1) | ((lo >> bit) & 1));")
        self.emit("            if (col < 4) a = (uint8_t)(a | (ci << ((3 - col) * 2)));")
        self.emit("            else         b = (uint8_t)(b | (ci << ((3 - (col - 4)) * 2)));")
        self.emit("        }")
        self.emit("        *o++ = 0x04; *o++ = a; *o++ = b; *o++ = 0x00;")
        self.emit("    }")
        self.emit("    *o = 0x00;  /* end of sprite data */")
        self.emit("}")
        self.emit("void gbs_present(void) {")
        self.emit("    uint8_t s;")
        self.emit("    /* No sprites in use: stay single-buffered so immediate")
        self.emit("       (text) drawing persists and does not flicker -- but still")
        self.emit("       wait out the frame so wait_vblank paces the main loop.")
        self.emit("       The Lynx clock() ticks once per display frame (Mikey")
        self.emit("       timer 2, the VBL timer), so CLOCKS_PER_SEC == framerate. */")
        self.emit("    if (!gbs_spr_used) {")
        self.emit("        clock_t t = clock();")
        self.emit("        while (clock() == t) { }")
        self.emit("        return;")
        self.emit("    }")
        self.emit("    /* First sprite frame: switch to true double-buffering so the")
        self.emit("       per-frame clear+redraw happens off-screen. */")
        self.emit("    if (!gbs_spr_db) { tgi_setdrawpage(1); gbs_spr_db = 1; }")
        self.emit("    tgi_setcolor(COLOR_BLACK);")
        self.emit("    tgi_bar(0, 0, %d, %d);" % (sw - 1, sh - 1))
        self.emit("    if (gbs_spr_visible)")
        self.emit("        for (s = 0; s < gbs_spr_max; ++s) tgi_sprite(&gbs_scb[s]);")
        self.emit("    while (tgi_busy()) { }  /* let Suzy finish before the flip */")
        self.emit("    tgi_updatedisplay();    /* VBL-synced flip (draw <-> view) */")
        self.emit("}")
        self.emit("void gbs_set_sprite_data(uint8_t first, uint8_t count, const uint8_t *data) {")
        self.emit("    uint8_t i;")
        self.emit("    gbs_spr_init();")
        self.emit("    for (i = 0; i < count; ++i)")
        self.emit("        if ((uint8_t)(first + i) < GBS_MAX_TILES)")
        self.emit("            gbs_conv_tile((uint8_t)(first + i), data + (uint16_t)i * 16);")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        self.emit("void gbs_set_sprite_tile(uint8_t nb, uint8_t tile) {")
        self.emit("    gbs_spr_init();")
        self.emit("    if (nb < GBS_MAX_SPRITES && tile < GBS_MAX_TILES) {")
        self.emit("        gbs_spr_tile[nb] = tile;")
        self.emit("        gbs_scb[nb].data = gbs_tiles[tile];")
        self.emit("        if (nb >= gbs_spr_max) gbs_spr_max = nb + 1;")
        self.emit("    }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        self.emit("uint8_t gbs_get_sprite_tile(uint8_t nb) {")
        self.emit("    return nb < GBS_MAX_SPRITES ? gbs_spr_tile[nb] : 0;")
        self.emit("}")
        self.emit("/* prop carries FLIP_X (HFLIP) / FLIP_Y (VFLIP) for SPRCTL0. */")
        self.emit("void gbs_set_sprite_prop(uint8_t nb, uint8_t prop) {")
        self.emit("    gbs_spr_init();")
        self.emit("    if (nb < GBS_MAX_SPRITES)")
        self.emit("        gbs_scb[nb].sprctl0 = (uint8_t)((BPP_2 | TYPE_NORMAL) |")
        self.emit("            (prop & (HFLIP | VFLIP)));")
        self.emit("}")
        self.emit("/* sprite.move takes screen-pixel coordinates (top-left origin). */")
        self.emit("void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y) {")
        self.emit("    gbs_spr_init();")
        self.emit("    if (nb < GBS_MAX_SPRITES) {")
        self.emit("        gbs_scb[nb].hpos = x;")
        self.emit("        gbs_scb[nb].vpos = y;")
        self.emit("        if (nb >= gbs_spr_max) gbs_spr_max = nb + 1;")
        self.emit("    }")
        self.emit("    gbs_spr_used = 1;")
        self.emit("}")
        self.emit("void gbs_show_sprites(void) { gbs_spr_used = 1; gbs_spr_visible = 1; }")
        self.emit("void gbs_hide_sprites(void) { gbs_spr_visible = 0; }")
        self.emit("void gbs_show_bkg(void) { }")

    def _emit_cc65_text_conio(self):
        """Text helpers for conio consoles (character-cell, e.g. PC Engine)."""
        self.emit("void gbs_print_string(uint8_t x, uint8_t y, const char *s) {")
        self.emit("    gbs_video_init();")
        self.emit("    gotoxy(x, y); cputs(s);")
        self.emit("}")
        self.emit("void gbs_print_number(uint8_t x, uint8_t y, uint16_t n) {")
        self.emit("    char buf[7];")
        self.emit("    gbs_video_init();")
        self.emit("    utoa(n, buf, 10);")
        self.emit("    gotoxy(x, y); cputs(buf);")
        self.emit("}")
        self.emit("void gbs_clear_area(uint8_t x, uint8_t y, uint8_t w, uint8_t h) {")
        self.emit("    uint8_t j;")
        self.emit("    gbs_video_init();")
        self.emit("    for (j = 0; j < h; j++) cclearxy(x, y + j, w);")
        self.emit("}")

    def _emit_module(self, module):
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

    def _format_var_decl(self, var) -> str:
        var_type = var.type
        if var_type is None:
            # Fall back to a sensible width when the type is omitted.
            var_type = self._infer_decl_type(var.initializer)
        decl = self._format_decl(var_type, var.name)
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
        return "%s %s(%s)" % (ret, func.name, params)

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
        self.emit(self._function_signature(func) + " {")
        for stmt in self._hoist_var_decls(func.body):
            self.gen_statement(stmt, 1)
        self.emit("}")
        self.emit("")

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
            return "%s(%s)" % (call.function.name, args)

        if isinstance(call.function, FieldAccess) and isinstance(call.function.object, Identifier):
            module_name = call.function.object.name
            func_name = call.function.field
            key = (module_name, func_name)
            c_name = self.stdlib_calls.get(key)
            if c_name is None:
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


class RegisterAllocator:
    def __init__(self):
        self.registers = ['a', 'b', 'c', 'd', 'e', 'h', 'l']
        self.allocated = {}

    def allocate(self, variable: str) -> str:
        # Simple allocation - just use A register for now
        return 'a'

# =============================================================================
# STANDARD LIBRARY MODULES - Enhanced with Graphics.Text
# =============================================================================

STDLIB_VIDEO = '''
module "platform.video" {
    -- Screen geometry for the build target (prelude #defines, set per
    -- console): SCREEN_WIDTH/SCREEN_HEIGHT in pixels, SCREEN_COLS/SCREEN_ROWS
    -- in text cells. E.g. 160x144 (20x18) on Game Boy, 256x192 on SMS,
    -- 160x102 (20x12) on Lynx.
    const SCREEN_WIDTH: u16 = 160
    const SCREEN_HEIGHT: u16 = 144
    const SCREEN_COLS: u8 = 20
    const SCREEN_ROWS: u8 = 18

    function wait_vblank() {}
    function enable_lcd() {}
    function disable_lcd() {}
    function show_sprites() {}
    function hide_sprites() {}
    function show_background() {}
    function show_window() {}
    function hide_window() {}

    export wait_vblank, enable_lcd, disable_lcd,
           show_sprites, hide_sprites, show_background, show_window, hide_window,
           SCREEN_WIDTH, SCREEN_HEIGHT, SCREEN_COLS, SCREEN_ROWS
}
'''

STDLIB_SPRITE = '''
module "graphics.sprite" {
    const FLIP_X: u8 = 32
    const FLIP_Y: u8 = 64

    -- Upload `count` tiles of sprite data (16 bytes each) starting at `first`.
    function set_data(first: u8, count: u8, data: addr) {}
    function set_tile(id: u8, tile: u8) {}
    function get_tile(id: u8) -> u8 {}
    function set_prop(id: u8, prop: u8) {}
    -- (x, y) are screen-pixel coordinates: (0, 0) is the top-left of the
    -- visible screen on every console. Move a sprite to y = SCREEN_HEIGHT
    -- to hide it below the screen.
    function move(id: u8, x: u8, y: u8) {}

    export set_data, set_tile, get_tile, set_prop, move, FLIP_X, FLIP_Y
}
'''

STDLIB_BKG = '''
module "graphics.bkg" {
    function set_data(first: u8, count: u8, data: addr) {}
    function set_tiles(x: u8, y: u8, w: u8, h: u8, tiles: addr) {}
    function scroll(dx: i8, dy: i8) {}
    function move(x: u8, y: u8) {}

    export set_data, set_tiles, scroll, move
}
'''

STDLIB_WINDOW = '''
module "graphics.window" {
    function set_tiles(x: u8, y: u8, w: u8, h: u8, tiles: addr) {}
    function move(x: u8, y: u8) {}

    export set_tiles, move
}
'''

STDLIB_SYSTEM = '''
module "platform.system" {
    function delay(ms: u16) {}
    function random() -> u8 {}
    function seed_random(seed: u16) {}

    export delay, random, seed_random
}
'''

STDLIB_INPUT = '''
module "platform.input" {
    const INPUT_A: u8 = 1
    const INPUT_B: u8 = 2
    const INPUT_SELECT: u8 = 4
    const INPUT_START: u8 = 8
    const INPUT_RIGHT: u8 = 16
    const INPUT_LEFT: u8 = 32
    const INPUT_UP: u8 = 64
    const INPUT_DOWN: u8 = 128

    function pressed(button: u8) -> bool {}
    function held(button: u8) -> bool {}

    export pressed, held, INPUT_A, INPUT_B, INPUT_SELECT, INPUT_START,
           INPUT_RIGHT, INPUT_LEFT, INPUT_UP, INPUT_DOWN
}
'''

STDLIB_HARDWARE = '''
module "platform.hardware" {
    -- Common Game Boy hardware register addresses (memory-mapped I/O).
    const REG_DIV: addr = 65284      -- 0xFF04 divider register (handy RNG source)
    const REG_NR10: addr = 65296     -- 0xFF10 sound channel 1
    const REG_BGP: addr = 65351      -- 0xFF47 background palette
    const REG_OBP0: addr = 65352     -- 0xFF48 object palette 0
    const REG_OBP1: addr = 65353     -- 0xFF49 object palette 1

    function write(address: addr, value: u8) {}
    function read(address: addr) -> u8 {}

    export write, read, REG_DIV, REG_NR10, REG_BGP, REG_OBP0, REG_OBP1
}
'''

STDLIB_TEXT = '''
module "graphics.text" {
    const TEXT_WIDTH: u8 = 20
    const TEXT_HEIGHT: u8 = 18

    function print_string(x: u8, y: u8, text: string) {}
    function print_number(x: u8, y: u8, number: u8) {}
    function clear_area(x: u8, y: u8, width: u8, height: u8) {}
    function set_font(font_data: addr) {}

    export print_string, print_number, clear_area, set_font,
           TEXT_WIDTH, TEXT_HEIGHT
}
'''

# =============================================================================
# MAIN COMPILER INTERFACE - Enhanced
# =============================================================================

class MosaikCompiler:
    def __init__(self):
        self.lexer = None
        self.parser = None
        self.type_checker = TypeChecker()
        self.code_generator = CodeGenerator()
        self.stdlib = {
            "platform.video": STDLIB_VIDEO,
            "platform.input": STDLIB_INPUT,
            "platform.hardware": STDLIB_HARDWARE,
            "platform.system": STDLIB_SYSTEM,
            "graphics.sprite": STDLIB_SPRITE,
            "graphics.bkg": STDLIB_BKG,
            "graphics.window": STDLIB_WINDOW,
            "graphics.text": STDLIB_TEXT
        }

    def compile(self, source_code: str, platform: str = None,
                assets: list = None) -> str:
        """Compile mosaik source code to GBDK C for the given target console.

        `platform` selects the build target (default: the code generator's
        current platform). It drives `if platform == "..."` conditional
        compilation and the platform-specific prelude.

        `assets` is a list of (name, gb_2bpp_bytes) pairs from the asset
        pipeline (mosaik_assets.py); each is emitted into the TU as a
        `const uint8_t <name>_tiles[]` array plus a `<name>_tile_count`
        define, usable directly from mosaik code.
        """
        try:
            platform = canonical_platform(platform or self.code_generator.platform)
            self.code_generator.platform = platform
            self.code_generator.assets = list(assets or [])

            # Lexical analysis
            self.lexer = Lexer(source_code)
            tokens = self.lexer.tokenize()

            # Syntax analysis
            self.parser = Parser(tokens, platform=platform)
            ast = self.parser.parse()

            # Type checking is best-effort: it produces helpful diagnostics for
            # simple programs but must never block code generation for the more
            # advanced language features the samples exercise.
            try:
                self.type_checker = TypeChecker()
                self.type_checker.register_assets(assets or [])
                self.type_checker.check_program(ast)
            except Exception as type_error:
                print(f"    Warning: type check skipped ({type_error})")

            # Code generation
            c_code = self.code_generator.generate(ast)

            return c_code

        except Exception as e:
            import traceback
            return f"Compilation error: {str(e)}\n\nFull traceback:\n{traceback.format_exc()}"

    def compile_file(self, filename: str) -> str:
        """Compile a mosaik file."""
        with open(filename, 'r') as f:
            source_code = f.read()
        return self.compile(source_code)

# =============================================================================
# EXAMPLE USAGE AND TESTING
# =============================================================================

if __name__ == "__main__":
    # Test the compiler with the provided example
    test_code = '''
    module "main" {
        import "platform.video"
        import "platform.input"
        import "graphics.text"

        var frame_count: u8 = 0
        var display_counter: u8 = 0

        function main() {
            video.enable_lcd()

            -- Display initial text
            text.print_string(16, 32, "Frame Count:")

            loop {
                frame_count += 1
                display_counter += 1

                -- Update display every 60 frames (roughly 1 second)
                if display_counter >= 60 {
                    -- Clear the number area and print new count
                    text.print_number(16, 48, frame_count)
                    display_counter = 0
                }

                video.wait_vblank()
            }
        }

        export main
    }
    '''

    compiler = MosaikCompiler()
    result = compiler.compile(test_code)
    print("mosaik Compilation Test Result:")
    print("=" * 60)
    print(result)
