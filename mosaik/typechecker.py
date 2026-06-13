"""Best-effort, non-blocking type checker (produces diagnostics only)."""

from .ast_nodes import *  # noqa: F401,F403


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
            'sprite.set_prop', 'sprite.move', 'sprite.set_palette',
            'bkg.set_data', 'bkg.set_tiles', 'bkg.scroll', 'bkg.move',
            'bkg.set_palette',
            'window.set_tiles', 'window.move',
            'palette.set_bkg', 'palette.set_sprite',
            'palette.load_bkg', 'palette.load_sprite',
            'system.delay', 'system.random', 'system.seed_random',
            'sound.beep', 'sound.stop',
        ]:
            ret = PrimitiveType('u8') if fn in u8_returning else PrimitiveType('void')
            self.symbol_table[fn] = {'type': 'function', 'return_type': ret,
                                     'parameters': []}

        # palette.rgb quantizes RGB888 to the console's native color word
        # (an opaque u16: RGB555 on GBC, RGB222/444 on SMS/GG, an NES master
        # palette index, a DMG shade, 12-bit GBR on Lynx, 9-bit GRB on PCE).
        self.symbol_table['palette.rgb'] = {
            'type': 'function',
            'return_type': PrimitiveType('u16'),
            'parameters': [Parameter('r', PrimitiveType('u8')),
                           Parameter('g', PrimitiveType('u8')),
                           Parameter('b', PrimitiveType('u8'))]
        }

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

    def register_assets(self, assets, palettes=None):
        """Register asset-pipeline symbols (`<name>_tiles` data arrays and
        `<name>_tile_count` defines, emitted into the TU by the codegen;
        plus `<name>_palette` native-color arrays for indexed PNGs)."""
        for name, data in assets:
            self.symbol_table['%s_tiles' % name] = {
                'type': 'constant',
                'value_type': ArrayType(PrimitiveType('u8'), len(data))}
            self.symbol_table['%s_tile_count' % name] = {
                'type': 'constant', 'value_type': PrimitiveType('u8')}
        for name, _colors in (palettes or []):
            self.symbol_table['%s_palette' % name] = {
                'type': 'constant',
                'value_type': ArrayType(PrimitiveType('u16'), 4)}

    def check_program(self, program: Program):
        # Pre-register every module's functions under their qualified name
        # (`<alias>.<function>`, alias = last name segment) so cross-module
        # calls resolve no matter the module order.
        for module in program.modules:
            alias = module.name.rsplit('.', 1)[-1]
            for decl in module.declarations:
                if isinstance(decl, FunctionDecl):
                    self.symbol_table['%s.%s' % (alias, decl.name)] = {
                        'type': 'function',
                        'return_type': decl.return_type,
                        'parameters': decl.parameters
                    }
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
