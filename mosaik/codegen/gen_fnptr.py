"""CodeGenerator function pointers + decl formatting + hoisting, mixed into generator.CodeGenerator.

Split out of the former monolithic generator.py; a plain mixin class composed
into CodeGenerator - methods reference sibling methods/attributes via self."""
import dataclasses
import re

from ..ast_nodes import *  # noqa: F401,F403
from ..platforms import (PLATFORM_CAPS, canonical_platform,
                         framework_for_platform, platform_caps)


class FnPtrDeclMixin:
    # -- function-pointer typedefs (callbacks) -----------------------------

    def _fnptr_key(self, ftype) -> str:
        """A canonical signature string for a FunctionType (its C return type +
        C parameter types), used to share one typedef per distinct signature."""
        ret = self.c_type(ftype.return_type) if ftype.return_type else 'void'
        params = ",".join(self.c_type(p) for p in ftype.param_types) or 'void'
        return "%s(%s)" % (ret, params)

    def _collect_fnptr_typedefs(self, program):
        """Walk every type annotation in the program and assign a typedef name
        to each distinct callback signature. Inner signatures register before
        the signatures that use them, so the emitted typedefs are well-ordered.
        Empty when no program uses callbacks -> nothing emitted, so callback-free
        programs stay byte-identical (same discipline as the palette/bkg flags)."""
        self.fnptr_typedefs = {}
        self.fnptr_sigs = {}

        def visit_type(t):
            if isinstance(t, ArrayType):
                visit_type(t.element_type)
            elif isinstance(t, StructType):
                for f in t.fields:
                    visit_type(f.type)
            elif isinstance(t, FunctionType):
                for p in t.param_types:
                    visit_type(p)
                if t.return_type is not None:
                    visit_type(t.return_type)
                key = self._fnptr_key(t)
                if key not in self.fnptr_typedefs:
                    self.fnptr_typedefs[key] = "gbs_fnptr_%d" % len(self.fnptr_typedefs)
                    ret = self.c_type(t.return_type) if t.return_type else 'void'
                    self.fnptr_sigs[key] = (
                        ret, [self.c_type(p) for p in t.param_types])

        def walk(node):
            if isinstance(node, (Parameter, StructField)):
                visit_type(node.type)
            elif isinstance(node, VarDecl) and node.type is not None:
                visit_type(node.type)
            elif isinstance(node, FunctionDecl) and node.return_type is not None:
                visit_type(node.return_type)
            elif isinstance(node, TypeDecl):
                visit_type(node.type_def)
            if isinstance(node, ASTNode):
                for f in dataclasses.fields(node):
                    walk(getattr(node, f.name))
            elif isinstance(node, (list, tuple)):
                for x in node:
                    walk(x)

        walk(program)

    def _emit_fnptr_typedefs(self):
        """Emit the callback typedef block (before structs, so a struct field
        may be a callback). Re-emitted per translation unit, including each
        bank TU's shared declarations."""
        if not self.fnptr_typedefs:
            return
        for key, name in self.fnptr_typedefs.items():
            ret, params = self.fnptr_sigs[key]
            self.emit("typedef %s (*%s)(%s);"
                      % (ret, name, ", ".join(params) or "void"))
        self.emit("")

    def _emit_fnptr_value_protos(self, program):
        """Forward-declare any function whose address is taken in a global/const
        initializer (`var cb: function(...) = handler`). Global definitions are
        emitted before the per-module function prototypes, and C forbids using
        an undeclared function in an initializer, so those targets need an early
        prototype. The ordinary prototype is still emitted later (a harmless
        repeat). No-op when no callbacks are used -> byte-identical output."""
        if not self.fnptr_typedefs:
            return
        func_decls = {}       # module name -> {func name: FunctionDecl}
        module_by_name = {}
        for module in program.modules:
            module_by_name[module.name] = module
            func_decls[module.name] = {
                d.name: d for d in module.declarations
                if isinstance(d, FunctionDecl)}

        targets = []          # (module, FunctionDecl), deduped by C name
        seen = set()

        def add(mod, fdecl):
            self._enter_module(mod)
            key = self._mangled(mod.name, fdecl.name)
            if key not in seen:
                seen.add(key)
                targets.append((mod, fdecl))

        def walk(node, owner):
            if isinstance(node, Identifier):
                if node.name in func_decls[owner.name]:
                    add(owner, func_decls[owner.name][node.name])
                return
            if isinstance(node, FieldAccess) and isinstance(node.object, Identifier):
                self._enter_module(owner)
                tmod = self.current_imports.get(node.object.name)
                if tmod and node.field in func_decls.get(tmod, {}):
                    add(module_by_name[tmod], func_decls[tmod][node.field])
                return
            if isinstance(node, ASTNode):
                for f in dataclasses.fields(node):
                    walk(getattr(node, f.name), owner)
            elif isinstance(node, (list, tuple)):
                for x in node:
                    walk(x, owner)

        for module in program.modules:
            for d in module.declarations:
                if isinstance(d, VarDecl) and d.initializer is not None:
                    walk(d.initializer, module)

        if not targets:
            return
        self.emit("/* prototypes for functions whose address is taken in a "
                  "global initializer */")
        for mod, fdecl in targets:
            self._enter_module(mod)
            self.emit(self._function_signature(fdecl) + ";")
        self.emit("")

    def _check_fnptr_arg_bytes(self):
        """Reject a callback TYPE whose arguments total more than 2 bytes on the
        NES. GBDK's NES port compiles through SDCC's mos6502 backend, whose
        function-POINTER calls use the non-reentrant convention (a static
        arg-passing area) that tops out at 2 bytes of arguments -- SDCC errors
        with `Functions called via pointers must be 'reentrant' to take this
        many (bytes for) arguments`. A DIRECT call is fine; only a call THROUGH
        a pointer is limited, so the check is on the pointer type, not the
        function. This turns that cryptic linker-stage SDCC error into a clear
        compile-time diagnostic -- the case a 2D-streamed world hits, whose
        `engine.scroll2d` `tile_at(c, r)` callback is 2 u16s = 4 bytes (the
        1D `engine.scroll` `gather(u16)` is 2 bytes and builds fine). The cc65
        6502 consoles (lynx/pce) and the z80/sm83 consoles are unaffected."""
        if self.platform != 'nes' or not self.fnptr_typedefs:
            return
        for key, name in self.fnptr_typedefs.items():
            _ret, params = self.fnptr_sigs[key]
            # A pointer/16-bit param is 2 bytes; everything else (u8, enum,
            # bool, i8) is 1. Matches the C types c_type emits.
            nbytes = sum(2 if ('16' in p or '*' in p or p.startswith('gbs_fnptr'))
                         else 1 for p in params)
            if nbytes > 2:
                raise RuntimeError(
                    "callback type '(%s)' takes %d bytes of arguments, but the "
                    "NES (SDCC mos6502) cannot call through a function pointer "
                    "with more than 2 argument bytes (its non-reentrant calling "
                    "convention). This is the 2D column-streaming case "
                    "(engine.scroll2d's tile_at callback); the NES is not a "
                    "supported target for it -- remove 'nes' from "
                    "target_platforms." % (", ".join(params), nbytes))

    def _check_fnptr_targets(self, program, banked):
        """Reject a callback that references a banked (`bank(N)`) function.

        sdcc's banked far-call is call-site codegen (save bank -> switch MBC ->
        call -> restore), not a runtime-dispatchable address: a plain pointer
        carries no bank number, so calling through it from another bank reads
        the wrong code (same hazard as banked const data). Only fires where banking is real (GB family); on consoles
        where bank() is ignored the function is in the linear image and a
        pointer to it is fine. A direct call to a banked function (the trampoline
        path) is unaffected -- only address-taking references are flagged."""
        if not self.banking_active:
            return
        banked_bare = {}    # declaring module name -> {func name}
        banked_alias = {}   # (alias, func name) -> bank
        for module, func in banked:
            banked_bare.setdefault(module.name, set()).add(func.name)
            alias = module.name.rsplit('.', 1)[-1]
            banked_alias[(alias, func.name)] = func.bank

        # Mark every node that is a call's *callee* so direct calls are exempt.
        callees = set()

        def mark(node):
            if isinstance(node, FunctionCall):
                callees.add(id(node.function))
            if isinstance(node, ASTNode):
                for f in dataclasses.fields(node):
                    mark(getattr(node, f.name))
            elif isinstance(node, (list, tuple)):
                for x in node:
                    mark(x)

        mark(program)

        def fail(name, bank):
            raise RuntimeError(
                "cannot reference banked function '%s' (bank %d) as a callback: "
                "a function pointer must target a home-bank function -- sdcc's "
                "banked far-call is generated at the call site, so the address "
                "alone cannot reach a switched-out ROM bank. Move '%s' to the "
                "home bank (drop its bank() placement)." % (name, bank, name))

        for module in program.modules:
            bare = banked_bare.get(module.name, set())

            def check(node):
                if id(node) not in callees:
                    if isinstance(node, Identifier) and node.name in bare:
                        fail(node.name, banked_alias.get(
                            (module.name.rsplit('.', 1)[-1], node.name), 0))
                    elif (isinstance(node, FieldAccess)
                          and isinstance(node.object, Identifier)):
                        bank = banked_alias.get((node.object.name, node.field))
                        if bank is not None:
                            fail(node.field, bank)
                if isinstance(node, ASTNode):
                    for f in dataclasses.fields(node):
                        check(getattr(node, f.name))
                elif isinstance(node, (list, tuple)):
                    for x in node:
                        check(x)

            for decl in module.declarations:
                check(decl)

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
        signature = "%s %s(%s)" % (ret, name, params)
        # bank(N) functions use sdcc's banked far calls; BANKED must appear on
        # the prototype and the definition alike (GBDK <gbdk/platform.h>).
        # EXCEPT a module-private local whose callers all share its bank
        # (_collect_near_bank_locals): `#pragma bank` still places its body in
        # the bank's code segment, but every call to it is a direct near call
        # (~24 cycles / 3 bytes) instead of the __banked_call trampoline
        # (~164 cycles / 8 bytes) - the 2026-08-28 same-bank census, ~4.4k
        # cycles a frame of pure trampoline in the town room idle.
        if self._in_bank_unit(func) and id(func) not in self._near_bank_funcs:
            signature += " BANKED"
        return signature

    def _hoist_var_decls(self, stmts: list) -> list:
        """For C89 compliance (cc65): a VarDeclStmt after a non-declaration
        statement in a compound block is invalid, so it is hoisted to the
        block top. The hoist must NOT move the initializer's evaluation: an
        initializer that reads a variable an earlier statement mutates would
        otherwise run too early and silently change the program on Lynx/PCE
        only. So a hoisted decl is SPLIT: a bare declaration goes to the top
        and the initializer stays in place as an assignment. Only a pure
        literal initializer (whose value cannot depend on earlier statements)
        is moved whole. A block whose declarations already lead it (the
        common mosaik pattern) is returned unchanged."""
        if self.framework != 'cc65':
            return stmts
        seen_stmt = needs_hoist = False
        for s in stmts:
            if isinstance(s, VarDeclStmt):
                if seen_stmt:
                    needs_hoist = True
                    break
            else:
                seen_stmt = True
        if not needs_hoist:
            return stmts

        decls, rest = [], []
        seen_stmt = False
        for s in stmts:
            if not isinstance(s, VarDeclStmt):
                seen_stmt = True
                rest.append(s)
                continue
            vd = s.var_decl
            if not seen_stmt or vd.initializer is None \
                    or self._literal_only(vd.initializer):
                decls.append(s)
                continue
            # Split: bare decl up top (const dropped -- C cannot assign a
            # const later), the initializer as an in-place assignment.
            decls.append(VarDeclStmt(VarDecl(vd.name, vd.type, None,
                                             is_const=False)))
            target = Identifier(vd.name)
            if isinstance(vd.initializer, ArrayLiteral):
                # An array cannot be assigned in C; store per element.
                for i, elem in enumerate(vd.initializer.elements):
                    rest.append(ExpressionStmt(BinaryOp(
                        ArrayAccess(target, Literal(i, "number")),
                        '=', elem)))
            else:
                # A StructLiteral RHS is expanded per field by
                # _gen_expression_stmt; scalars assign directly.
                rest.append(ExpressionStmt(BinaryOp(target, '=',
                                                    vd.initializer)))
        return decls + rest

    def _literal_only(self, expr) -> bool:
        """True when `expr`'s value cannot depend on any earlier statement
        (literals, negated literals, and aggregates of them only)."""
        if isinstance(expr, Literal):
            return True
        if isinstance(expr, UnaryOp):
            return self._literal_only(expr.operand)
        if isinstance(expr, ArrayLiteral):
            return all(self._literal_only(e) for e in expr.elements)
        if isinstance(expr, StructLiteral):
            return all(self._literal_only(v) for _n, v in expr.fields)
        return False

    def _emit_function(self, func):
        # Parameters and locals shadow module-level symbols, so collect them
        # up front: a shadowed name must never be mangled (see gen_expression).
        self.local_names = {p.name for p in func.parameters}
        self._collect_locals(func.body, self.local_names)
        self._check_use_before_decl(func)
        self._stmt_ctx = []  # break-context stack (loop vs switch), per function
        # Bank-neutrality wrapper (GB code banking): a function whose body
        # switches the ROM window restores the ENTRY bank on every exit, so a
        # BANKED caller never resumes under a switched-out window. Only marked
        # functions in a code-banking build pay it; everything else is
        # byte-identical.
        self._bank_neutral_active = (id(func) in self._bank_neutral_funcs)
        if self._bank_neutral_active:
            self._current_ret_ctype = (self.c_type(func.return_type)
                                       if func.return_type else 'uint8_t')
        # cc65 banking: the body lands in its bank's segment of the one TU
        # (code, rodata and its own literals; see cc65_bank).
        cc65_bank = func.bank if self._cc65_banked(func) else 0
        if cc65_bank:
            self._emit_cc65_seg_open(cc65_bank)
            self.emit(self._cc65_body_signature(
                func, self._function_signature(func)) + " {")
        else:
            self.emit(self._function_signature(func) + " {")
        if self._bank_neutral_active:
            self.emit("    uint8_t __gbs_bank_entry = CURRENT_BANK;")
        if func.name == 'main' and self.platform in ('sms', 'gamegear'):
            # Match the GB crt0, which blanks the BG map before main(): the z80
            # SMS/GG port leaves the name table uninitialised, so clear it here
            # before any user code paints (a sprite-only or partially-painted
            # screen would otherwise show VRAM garbage). See gbdk.py prelude.
            self.emit("    gbs_sms_clear_bkg();")
        if func.name == 'main' and self.platform in ('gameboy_color',
                                                     'analogue_pocket'):
            # Same shape, different console: the CGB boot ROM leaves palette
            # RAM uninitialised for a CGB-flagged cart and the hardware ignores
            # BGP/OBP0/OBP1, so seed the DMG grey ramp before any user code
            # runs. Here rather than in gbs_enable_lcd precisely so a program
            # that DOES load colours (before or after enabling the LCD)
            # overwrites this instead of being overwritten by it. See the
            # gbdk.py prelude note.
            self.emit("    gbs_cgb_default_palettes();")
        for stmt in self._hoist_var_decls(func.body):
            self.gen_statement(stmt, 1)
        if self._bank_neutral_active:
            # The fall-off-the-end exit restores too (unreachable after a
            # trailing return -- harmless).
            self.emit("    SWITCH_ROM(__gbs_bank_entry);")
            self._bank_neutral_active = False
        self.emit("}")
        if cc65_bank:
            self._emit_cc65_seg_close()
        self.emit("")
        self.local_names = set()

    def _check_use_before_decl(self, func):
        """Reject a reference to a module-level symbol that a LATER local of
        the same name shadows. local_names is collected function-wide, so
        such a reference would emit the bare local name BEFORE its C
        declaration: an undeclared-identifier C error in a multi-module
        build, silently shifted semantics in a single-module one. Rename the
        local or move its declaration above the reference."""
        params = {p.name for p in func.parameters}
        shadowing = (self.local_names - params) & set(self.current_symbols)
        if not shadowing:
            return
        declared = set(params)

        def fail(name):
            raise RuntimeError(
                "function '%s' references '%s' before the local declaration "
                "that shadows the module-level '%s'; rename the local or "
                "move its declaration above the reference"
                % (func.name, name, name))

        def walk_expr(expr):
            if isinstance(expr, Identifier):
                if expr.name in shadowing and expr.name not in declared:
                    fail(expr.name)
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
            elif isinstance(expr, FieldAccess):
                walk_expr(expr.object)
            elif isinstance(expr, StructLiteral):
                for _n, v in expr.fields:
                    walk_expr(v)
            elif isinstance(expr, ArrayLiteral):
                for e in expr.elements:
                    walk_expr(e)

        def walk_stmts(stmts):
            for stmt in stmts or []:
                if isinstance(stmt, VarDeclStmt):
                    if stmt.var_decl.initializer is not None:
                        walk_expr(stmt.var_decl.initializer)
                    declared.add(stmt.var_decl.name)
                elif isinstance(stmt, ExpressionStmt):
                    walk_expr(stmt.expression)
                elif isinstance(stmt, IfStmt):
                    walk_expr(stmt.condition)
                    walk_stmts(stmt.then_body); walk_stmts(stmt.else_body)
                elif isinstance(stmt, (LoopStmt, WhileStmt)):
                    if isinstance(stmt, WhileStmt):
                        walk_expr(stmt.condition)
                    walk_stmts(stmt.body)
                elif isinstance(stmt, ForStmt):
                    walk_expr(stmt.start); walk_expr(stmt.end)
                    declared.add(stmt.var_name)
                    walk_stmts(stmt.body)
                elif isinstance(stmt, SwitchStmt):
                    walk_expr(stmt.subject)
                    for labels, body in stmt.cases:
                        for label in labels:
                            walk_expr(label)
                        walk_stmts(body)
                    walk_stmts(stmt.default_body)
                elif isinstance(stmt, ReturnStmt):
                    if stmt.value is not None:
                        walk_expr(stmt.value)

        walk_stmts(func.body)

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
