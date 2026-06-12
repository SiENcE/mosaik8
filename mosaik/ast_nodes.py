"""AST node definitions (dataclasses) produced by the Parser."""

from abc import ABC
from dataclasses import dataclass
from typing import List, Optional, Union


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
    # ROM bank the function is placed in via `bank(N)`; 0 = the home bank.
    # Acted on only where PLATFORM_CAPS has_banking (see docs/banking-plan.md).
    bank: int = 0

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
