"""Lexer: source text -> tokens."""

import enum
from dataclasses import dataclass
from typing import List, Optional


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
