"""mosaik compiler package.

Pipeline: Lexer -> Parser -> TypeChecker -> CodeGenerator (GBDK or cc65 C).
`MosaikCompiler.compile_program(sources, platform=...) -> C string` is the
entry point; `compile(source, ...)` is the single-source wrapper.

This package was split out of the former single-file `mosaik_compiler.py`
(now removed); import the public API from `mosaik` directly.
"""

from .lexer import Lexer, Token, TokenType
from .ast_nodes import *  # noqa: F401,F403
from .platforms import (PLATFORM_ALIASES, PLATFORM_CAPS, PLATFORM_FRAMEWORK,
                        canonical_platform, framework_for_platform,
                        platform_caps)
from .parser import Parser
from .typechecker import TypeChecker
from .codegen import CodeGenerator
from .stdlib import STDLIB_MODULE_NAMES, stdlib_module_names
from .compiler import MosaikCompiler

__all__ = [
    "Lexer", "Token", "TokenType",
    "Parser", "TypeChecker", "CodeGenerator", "MosaikCompiler",
    "PLATFORM_ALIASES", "PLATFORM_CAPS", "PLATFORM_FRAMEWORK",
    "canonical_platform", "framework_for_platform", "platform_caps",
    "STDLIB_MODULE_NAMES", "stdlib_module_names",
]
