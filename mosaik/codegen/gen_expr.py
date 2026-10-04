"""CodeGenerator statement + expression + call codegen, mixed into generator.CodeGenerator.

Split out of the former monolithic generator.py; a plain mixin class composed
into CodeGenerator - methods reference sibling methods/attributes via self."""
import dataclasses
import re

from ..ast_nodes import *  # noqa: F401,F403
from ..platforms import (PLATFORM_CAPS, canonical_platform,
                         framework_for_platform, platform_caps)


class ExprStmtMixin:
    # -- statements --------------------------------------------------------

    def gen_statement(self, stmt, indent):
        pad = "    " * indent
        # WHERE WE ARE, for a codegen failure's location (review L-9). The
        # parser stamps `.line` on every statement; a RuntimeError raised
        # below is otherwise reported with a Python traceback and no mosaik
        # line at all.
        line = getattr(stmt, 'line', None)
        if line:
            self._cur_line = line
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
            self._stmt_ctx.append('loop')
            for s in self._hoist_var_decls(stmt.body):
                self.gen_statement(s, indent + 1)
            self._stmt_ctx.pop()
            self.emit(pad + "}")
        elif isinstance(stmt, WhileStmt):
            self.emit(pad + "while (%s) {" % self.gen_expression(stmt.condition))
            self._stmt_ctx.append('loop')
            for s in self._hoist_var_decls(stmt.body):
                self.gen_statement(s, indent + 1)
            self._stmt_ctx.pop()
            self.emit(pad + "}")
        elif isinstance(stmt, SwitchStmt):
            self.emit(pad + "switch (%s) {" % self.gen_expression(stmt.subject))
            self._stmt_ctx.append('switch')
            for labels, body in stmt.cases:
                for label in labels[:-1]:
                    self.emit(pad + "case %s:" % self.gen_expression(label))
                self.emit(pad + "case %s: {" % self.gen_expression(labels[-1]))
                for s in self._hoist_var_decls(body):
                    self.gen_statement(s, indent + 1)
                self.emit(pad + "} break;")
            if stmt.default_body is not None:
                self.emit(pad + "default: {")
                for s in self._hoist_var_decls(stmt.default_body):
                    self.gen_statement(s, indent + 1)
                self.emit(pad + "}")
            self._stmt_ctx.pop()
            self.emit(pad + "}")
        elif isinstance(stmt, BreakStmt):
            # mosaik switch cases auto-break, so a user `break` whose nearest
            # enclosing construct is the switch almost certainly means "break
            # the enclosing LOOP" -- but it would compile to a C break that
            # only exits the switch. Forbidden until the language defines it
            # (put the break outside the switch, e.g. gate it on a flag).
            if self._stmt_ctx and self._stmt_ctx[-1] == 'switch':
                raise RuntimeError(
                    "'break' directly inside a switch case is not supported: "
                    "cases auto-break, and a C break here would exit only the "
                    "switch, not the enclosing loop -- set a flag in the case "
                    "and break outside the switch instead")
            self.emit(pad + "break;")
        elif isinstance(stmt, ContinueStmt):
            self.emit(pad + "continue;")
        elif isinstance(stmt, ForStmt):
            v = stmt.var_name
            start = self.gen_expression(stmt.start)
            end = self.gen_expression(stmt.end)
            # The loop variable follows the bounds' width: with a u8 variable
            # a 16-bit bound is unreachable (`for i in 0..256` -> `i < 256`
            # with uint8_t i never exits). The typechecker annotates
            # loop_var_type from the bounds' inferred types; the literal check
            # is the fallback when the annotation is missing.
            ctype = 'uint8_t'
            if getattr(stmt, 'loop_var_type', None) == 'u16':
                ctype = 'uint16_t'
            for bound in (stmt.start, stmt.end):
                if (isinstance(bound, Literal) and bound.type == 'number'
                        and bound.value > 255):
                    ctype = 'uint16_t'
            # Declare the loop variable in an enclosing block rather than in the
            # for-init, so the loop is valid C89 (cc65 rejects C99 declarations
            # in a for-statement; sdcc accepts the block form too).
            self.emit(pad + "{ %s %s;" % (ctype, v))
            self.emit(pad + "for (%s = %s; %s < %s; %s++) {" % (v, start, v, end, v))
            self._stmt_ctx.append('loop')
            for s in self._hoist_var_decls(stmt.body):
                self.gen_statement(s, indent + 1)
            self._stmt_ctx.pop()
            self.emit(pad + "} }")
        elif isinstance(stmt, ReturnStmt):
            if isinstance(stmt.value, (StructLiteral, ArrayLiteral)):
                # `return {..}` would emit an invalid C brace list; only an
                # assignment target gets the per-field expansion.
                raise RuntimeError(
                    "returning a struct/array literal is not supported: "
                    "assign it to a local variable and return that")
            if stmt.value is not None:
                value = self.gen_expression(stmt.value)
                if self._bank_neutral_active:
                    # Bank-neutrality wrapper (GB code banking): evaluate the
                    # return value FIRST (it may itself switch the window),
                    # restore the entry bank, then return -- so a BANKED
                    # caller resumes under the bank it was called with.
                    self.emit(pad + "{ %s __gbs_ret = %s; "
                              "SWITCH_ROM(__gbs_bank_entry); "
                              "return __gbs_ret; }"
                              % (self._current_ret_ctype, value))
                else:
                    self.emit(pad + "return %s;" % value)
            else:
                if self._bank_neutral_active:
                    self.emit(pad + "{ SWITCH_ROM(__gbs_bank_entry); "
                              "return; }")
                else:
                    self.emit(pad + "return;")

    def _is_assets_use(self, expr) -> bool:
        """True if `expr` is a stdlib `assets.use(...)` call (the ensure-resident
        hint), and NOT a call through a local/field named `assets`."""
        if not (isinstance(expr, FunctionCall)
                and isinstance(expr.function, FieldAccess)
                and isinstance(expr.function.object, Identifier)):
            return False
        fn = expr.function
        # use / use_range lower to NOTHING off the streaming path; range_base is
        # pure registration and lowers to nothing on EVERY console (item 33).
        if fn.object.name != 'assets' or fn.field not in ('use', 'use_range',
                                                          'range_base'):
            return False
        if fn.object.name in self.local_names or fn.object.name in self.current_symbols:
            return False
        if fn.field == 'range_base':
            return True
        # Under Lynx streaming / GB banking, assets.use[_range](streamed) lowers to
        # a real load/bank-switch statement to keep, not a no-op to drop.
        if self._streaming and expr.arguments:
            if self.gen_expression(expr.arguments[0]) in self.streamed:
                return False
        return True

    def _is_cc65_lcd_off(self, expr) -> bool:
        """True if `expr` is a stdlib `video.disable_lcd()` call on a cc65
        console (where it lowers to nothing), and not a call through a
        local/field named `video`."""
        if self.framework != 'cc65':
            return False
        if not (isinstance(expr, FunctionCall)
                and isinstance(expr.function, FieldAccess)
                and isinstance(expr.function.object, Identifier)):
            return False
        fn = expr.function
        if fn.object.name != 'video' or fn.field != 'disable_lcd':
            return False
        if fn.object.name in self.local_names or fn.object.name in self.current_symbols:
            return False
        return True

    def _gen_expression_stmt(self, expr, indent):
        pad = "    " * indent
        # assets.use(id) is an ensure-resident hint that lowers to NOTHING on the
        # directly-mapped consoles (the data is already in ROM) -- emit no
        # statement at all, so the output stays byte-identical (a bare ';' would
        # not). The Lynx streaming lowering (Stage B) emits the cart load here.
        if self._is_assets_use(expr):
            return
        # assets.bank_leave lowers to nothing where the blob is resident.
        if (isinstance(expr, FunctionCall) and isinstance(expr.function, FieldAccess)
                and isinstance(expr.function.object, Identifier)
                and expr.function.object.name == 'assets'
                and expr.function.field == 'bank_leave'
                and self._gen_call(expr) == ""):
            return
        # video.disable_lcd() lowers to NOTHING on the cc65 framebuffer consoles
        # (see _gen_call) -- drop the whole statement so the output stays
        # byte-identical (a bare ';' would not), exactly like assets.use above.
        if self._is_cc65_lcd_off(expr):
            return
        # Assigning a struct literal must be expanded into per-field stores,
        # since C does not allow `target = {a, b};` outside an initializer.
        if (isinstance(expr, BinaryOp) and expr.operator == '=' and
                isinstance(expr.right, StructLiteral)):
            # The expansion repeats the generated target per field, so a
            # side-effecting target (`pts[next()] = {..}`) would run the
            # effect once per field and scatter the fields -- reject it.
            if self._has_side_effects(expr.left):
                raise RuntimeError(
                    "cannot assign a struct literal through a target with a "
                    "function call in it (%s) -- it would be evaluated once "
                    "per field; store the index/target in a local first"
                    % self.gen_expression(expr.left))
            target = self.gen_expression(expr.left)
            for field_name, field_value in expr.right.fields:
                self.emit(pad + "%s.%s = %s;" % (target, field_name,
                                                 self.gen_expression(field_value)))
            return
        # A statement-position assignment is emitted without the expression
        # parens (gen_expression parenthesizes assignment for nested use).
        if isinstance(expr, BinaryOp) and expr.operator in ('=', '+=', '-=', '|=', '&=', '^='):
            self.emit(pad + "%s %s %s;" % (self.gen_expression(expr.left),
                                           expr.operator,
                                           self.gen_expression(expr.right)))
            return
        self.emit(pad + self.gen_expression(expr) + ";")

    def _has_side_effects(self, expr) -> bool:
        """True when evaluating `expr` may run code (any function call)."""
        if isinstance(expr, FunctionCall):
            return True
        if isinstance(expr, BinaryOp):
            return (self._has_side_effects(expr.left)
                    or self._has_side_effects(expr.right))
        if isinstance(expr, UnaryOp):
            return self._has_side_effects(expr.operand)
        if isinstance(expr, ArrayAccess):
            return (self._has_side_effects(expr.array)
                    or self._has_side_effects(expr.index))
        if isinstance(expr, FieldAccess):
            return self._has_side_effects(expr.object)
        return False

    # -- expressions -------------------------------------------------------

    @staticmethod
    def _c_int_literal(value: int) -> str:
        """An integer literal in the C type the 8-bit compilers want. A bare
        32768..65535 is a C `long` (int is 16-bit on sdcc and cc65), so
        `x ^ 65535` or `x == 0xFFFF` dragged the whole expression through
        32-bit arithmetic; a `U` suffix keeps it `unsigned int`, the u16 the
        type checker already calls it. Negative literals keep their
        parentheses (a `-` glued to a preceding operator or `#define` body
        reads wrong), which is also what the goldens pin."""
        if value < 0:
            return "(%d)" % value
        if 32768 <= value <= 65535:
            return "%dU" % value
        return str(value)

    def gen_expression(self, expr) -> str:
        if isinstance(expr, Literal):
            if expr.type == "number":
                return self._c_int_literal(expr.value)
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
            if expr.operator in ('=', '+=', '-=', '|=', '&=', '^='):
                # Parenthesized like every other operator: C's `=` binds
                # looser than everything, so an unparenthesized nested
                # assignment regroups (`a = (b = c) + 1` -> `a = (b = c + 1)`).
                # Statement-position assignments are emitted without the
                # parens by _gen_expression_stmt, so ordinary statements stay
                # byte-identical.
                return "(%s %s %s)" % (self.gen_expression(expr.left),
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
            arr = self.gen_expression(expr.array)
            # Streamed asset: the const array left the resident image.
            #   Lynx: index through the RAM cache (e.g. scenes.map_tile's
            #     `MAP[idx]` -> `gbs_asset_ptr(id)[idx]`); loads on first use.
            #   GB banking: the const lives in a ROM bank, so map it, then index
            #     it (a comma expression). Switch on every access so a held
            #     pointer never spans a bank switch (the banking hazard); the read
            #     happens immediately while the bank is mapped.
            # A range-windowed base (paint_table + stream, item 33) is NEVER
            # indexed directly -- map_tile/collision_at read it through
            # assets.range_byte (the window cache), so exclude it from the
            # whole-asset ptr rewrite (which would call the wrong cache).
            if (self._streaming and arr in self.streamed
                    and arr not in self._range_bases):
                if self._stream_mode == 'gb':
                    return "(SWITCH_ROM(%d), %s)[%s]" % (
                        self.streamed[arr], arr, self.gen_expression(expr.index))
                arr = "gbs_asset_ptr(%d)" % self.streamed[arr]
            return "%s[%s]" % (arr, self.gen_expression(expr.index))

        if isinstance(expr, StructLiteral):
            return "{%s}" % ", ".join(self.gen_expression(v) for _, v in expr.fields)

        if isinstance(expr, ArrayLiteral):
            return "{%s}" % ", ".join(self.gen_expression(e) for e in expr.elements)

        # An unhandled node must be an error, never a silent literal 0 in the
        # emitted C (the one miscompile a compiler must not commit).
        raise RuntimeError(
            "internal error: cannot generate C for expression node %r"
            % (expr,))

    def _gen_call(self, call) -> str:
        for a in call.arguments:
            if isinstance(a, (StructLiteral, ArrayLiteral)):
                # A bare brace list is not a C expression; only an assignment
                # target gets the per-field expansion.
                raise RuntimeError(
                    "a struct/array literal cannot be passed as an argument: "
                    "assign it to a local variable and pass that")
        args = ", ".join(self.gen_expression(a) for a in call.arguments)

        if isinstance(call.function, Identifier):
            # gen_expression applies the module-symbol mangling (a bare call
            # targets a function of the current module).
            return "%s(%s)" % (self.gen_expression(call.function), args)

        if isinstance(call.function, FieldAccess) and isinstance(call.function.object, Identifier):
            module_name = call.function.object.name
            func_name = call.function.field
            # `var.field(...)` where `var` is a local/global (not a module alias)
            # is a call through a function pointer held in a struct field, not a
            # module call -- lower it as ordinary member access on the callee.
            if (module_name in self.local_names
                    or module_name in self.current_symbols):
                return "%s(%s)" % (self.gen_expression(call.function), args)
            key = (module_name, func_name)
            # Asset residency seam (asset-streaming groundwork).
            # On every directly-mapped console these lower transparently, so the
            # output is byte-identical to passing the const array to the setter:
            #   assets.ptr(X) -> X's bare symbol   (the pointer is the const)
            #   assets.use(X) -> nothing           (already resident in ROM)
            # The argument is the same asset reference on every console; only the
            # lowering differs (the Lynx streaming path, Stage B, returns a RAM-
            # cache pointer + loads from the cart). Intercepted here BEFORE the
            # generic c_name(args) path, after the local/field-pointer check above
            # (so a local named `assets` is unaffected).
            if key == ('assets', 'ptr'):
                if len(call.arguments) != 1:
                    raise RuntimeError("assets.ptr(id) takes exactly one argument")
                sym = self.gen_expression(call.arguments[0])
                # Lynx streaming: the asset lives in the cart archive -> return a
                # RAM-cache pointer (load it if not resident). GB banking: the
                # const lives in a ROM bank -> map it, then return the symbol (a
                # comma expression, since SWITCH_ROM is an expression -- the
                # pointer is read while mapped, never held across a switch).
                # Everywhere else the pointer IS the const symbol (byte-identical).
                if self._streaming and sym in self.streamed:
                    if self._stream_mode == 'gb':
                        return "(SWITCH_ROM(%d), %s)" % (self.streamed[sym], sym)
                    return "gbs_asset_ptr(%d)" % self.streamed[sym]
                return sym
            if key == ('assets', 'use'):
                # Lynx streaming: ensure the asset is resident (cart -> cache).
                # GB banking: map the asset's ROM bank (SWITCH_ROM). Else a no-op
                # as an expression; as a STATEMENT it is dropped by
                # _gen_expression_stmt (a bare ';' would not be byte-identical).
                if self._streaming and call.arguments:
                    sym = self.gen_expression(call.arguments[0])
                    # A LOAD-THROUGH tileset has no cache slot to warm -- the
                    # set_data below reads it from the cart itself -- so the
                    # prefetch hint lowers to nothing (see the interception
                    # further down and _collect_bkg_loadthrough).
                    if sym in self._bkg_loadthrough:
                        return ""
                    if sym in self.streamed:
                        if self._stream_mode == 'gb':
                            return "SWITCH_ROM(%d)" % self.streamed[sym]
                        return "gbs_asset_load(%d)" % self.streamed[sym]
                return ""
            # Streamed tileset LOAD-THROUGH (Lynx): `bkg.set_data(first, count,
            # assets.ptr(SYM))` where SYM is only ever this copy-out reads the
            # cart straight into the background tile table, so SYM never
            # occupies an LRU slot (which would size every slot to it). Every
            # other console -- and any SYM that is also indexed or streamed
            # some other way -- keeps the plain setter, byte-identical.
            if (key == ('bkg', 'set_data') and self._bkg_loadthrough
                    and len(call.arguments) == 3
                    and self._is_assets_ptr(call.arguments[2])):
                sym = self.gen_expression(call.arguments[2].arguments[0])
                if sym in self._bkg_loadthrough:
                    return "gbs_bkg_data_stream(%s, %s, %d)" % (
                        self.gen_expression(call.arguments[0]),
                        self.gen_expression(call.arguments[1]),
                        self.streamed[sym])
            # A SPRITE SHEET IN A DATA BANK is uploaded through the far read:
            # map the sheet's bank, upload, restore the caller's (the caller
            # is usually the generated rooms.mos loader, itself banked, so the
            # helper must be resident and bank-neutral). See
            # _collect_bank_local_consts - a code bank is one 16 KB window and
            # the art was competing with the module's code for it.
            if (key == ('sprite', 'set_data') and self.asset_far_bank
                    and len(call.arguments) == 3
                    and isinstance(call.arguments[2], Identifier)
                    and call.arguments[2].name in self.asset_far_bank):
                sym = call.arguments[2].name
                return "gbs_spr_data_far(%s, %s, %d, %s)" % (
                    self.gen_expression(call.arguments[0]),
                    self.gen_expression(call.arguments[1]),
                    self.asset_far_bank[sym], sym)
            # A SPRITE SHEET STREAMED FROM THE LYNX CART (`[world] stream`):
            # the upload reads it tile by tile from the archive straight into
            # the converter, so the sheet never occupies MAIN. See
            # _pack_lynx_sheets.
            if (key == ('sprite', 'set_data') and self.sheet_stream
                    and len(call.arguments) == 3
                    and isinstance(call.arguments[2], Identifier)
                    and call.arguments[2].name in self.sheet_stream):
                return "gbs_spr_data_stream(%s, %s, %d)" % (
                    self.gen_expression(call.arguments[0]),
                    self.gen_expression(call.arguments[1]),
                    self.sheet_stream[call.arguments[2].name])
            if key == ('hw', 'peek'):
                # An INLINE plain byte read (no helper call, not volatile): the
                # VM8 interpreter's fetch fast path reads its blob this way.
                if len(call.arguments) != 1:
                    raise RuntimeError("hw.peek(addr) takes exactly one argument")
                return "(*(const uint8_t *)(%s))" % self.gen_expression(call.arguments[0])
            if key == ('assets', 'address'):
                # The blob's address. On the GB family a banked blob's symbol IS
                # its address in the switchable window (valid while mapped -
                # assets.bank_enter). A Lynx-STREAMED blob has no fixed address.
                if len(call.arguments) != 1:
                    raise RuntimeError("assets.address(blob) takes exactly one argument")
                sym = self.gen_expression(call.arguments[0])
                if (self._streaming and self._stream_mode == 'lynx'
                        and (sym in self.streamed or sym in (
                            self.streamed_code_sym, self.streamed_code2_sym,
                            self.streamed_code3_sym))):
                    raise RuntimeError("assets.address(%s): a Lynx-streamed blob has no "
                                       "fixed address" % sym)
                return "((uint16_t)(%s))" % sym
            if key == ('assets', 'bank_enter'):
                # Map the blob's ROM bank and hand back the one to restore; on a
                # console where the blob is resident there is nothing to map.
                if len(call.arguments) != 1:
                    raise RuntimeError("assets.bank_enter(blob) takes exactly one argument")
                sym = self.gen_expression(call.arguments[0])
                if (self._streaming and self._stream_mode == 'gb'
                        and sym in self.streamed):
                    return "gbs_bank_enter(%d)" % self.streamed[sym]
                return "0"
            if key == ('assets', 'bank_leave'):
                if len(call.arguments) != 1:
                    raise RuntimeError("assets.bank_leave(saved) takes exactly one argument")
                if self._streaming and self._stream_mode == 'gb':
                    return "SWITCH_ROM(%s)" % self.gen_expression(call.arguments[0])
                return ""
            if key == ('assets', 'code_byte'):
                # The VM8 fetch seam (§7.1 #3): read one byte of the bytecode
                # blob. Byte-identical `blob[off]` everywhere EXCEPT when this blob
                # is streamed on the Lynx (a big blob), where it routes through the
                # current-page cache (gbs_code_byte). See _pack_lynx_code.
                if len(call.arguments) != 2:
                    raise RuntimeError("assets.code_byte(blob, off) takes two arguments")
                sym = self.gen_expression(call.arguments[0])
                off = self.gen_expression(call.arguments[1])
                # GB code banking: a data blob (song CELLS / STRINGS -- never
                # the first-seen bytecode blob, which stays resident) lives in
                # a ROM bank; switch-then-read per byte. The enclosing function
                # gets the bank-neutrality wrapper (_apply_code_banks), so the
                # caller's bank is restored before it returns.
                if (self._streaming and self._stream_mode == 'gb'
                        and sym in self.streamed):
                    return "(SWITCH_ROM(%d), %s)[%s]" % (
                        self.streamed[sym], sym, off)
                if self._streaming and sym == self.streamed_code_sym:
                    return "gbs_code_byte(%s)" % off
                if self._streaming and sym == self.streamed_code2_sym:
                    return "gbs_str_byte(%s)" % off
                if self._streaming and sym == self.streamed_code3_sym:
                    return "gbs_dat_byte(%s)" % off
                return "%s[%s]" % (sym, off)
            # The RANGE-windowed seam (paint_table + [world] stream, item 33):
            # the concatenated MAPS/COLLISION array is read one room-sized WINDOW
            # at a time. On a directly-mapped console it is the byte-identical
            # resident concatenation (a pointer/index into the array); on the Lynx
            # it streams the window into the RAM cache; on the banking GB family it
            # maps the array's bank then reads in place.
            if key == ('assets', 'range_base'):
                # Registration only (records the base + its window size for the
                # Lynx range cache in _collect_streamed) -- no runtime effect,
                # dropped as a statement (see _is_assets_use), a no-op expression.
                return ""
            if key in (('assets', 'use_range'), ('assets', 'ptr_range')):
                if len(call.arguments) != 3:
                    raise RuntimeError("assets.%s(sym, off, len) takes three "
                                       "arguments" % func_name)
                sym = self.gen_expression(call.arguments[0])
                off = self.gen_expression(call.arguments[1])
                length = self.gen_expression(call.arguments[2])
                streamed = self._streaming and sym in self.streamed
                if key == ('assets', 'use_range'):
                    # Warm the room's window. Lynx: cart read into the cache slot.
                    # GB banking: map the array's bank. Resident: nothing (dropped
                    # as a statement, byte-identical).
                    if streamed:
                        if self._stream_mode == 'gb':
                            return "SWITCH_ROM(%d)" % self.streamed[sym]
                        return "gbs_asset_load_range(%d, %s, %s)" % (
                            self.streamed[sym], off, length)
                    return ""
                # ptr_range: a pointer to the room's window (handed to a setter).
                if streamed:
                    if self._stream_mode == 'gb':
                        return "(SWITCH_ROM(%d), (%s) + (%s))" % (
                            self.streamed[sym], sym, off)
                    return "gbs_asset_ptr_range(%d, %s, %s)" % (
                        self.streamed[sym], off, length)
                return "((%s) + (%s))" % (sym, off)
            if key == ('assets', 'range_byte'):
                # One byte of the room's window (map_tile / collision_at). Lynx:
                # the ALREADY-WARM slot (paint warmed the room's map+collision).
                # GB banking: map then read. Resident: the plain indexed const.
                if len(call.arguments) != 3:
                    raise RuntimeError("assets.range_byte(sym, off, idx) takes "
                                       "three arguments")
                sym = self.gen_expression(call.arguments[0])
                off = self.gen_expression(call.arguments[1])
                idx = self.gen_expression(call.arguments[2])
                if self._streaming and sym in self.streamed:
                    if self._stream_mode == 'gb':
                        return "(SWITCH_ROM(%d), %s)[(%s) + (%s)]" % (
                            self.streamed[sym], sym, off, idx)
                    return "gbs_asset_find_range(%d, %s)[%s]" % (
                        self.streamed[sym], off, idx)
                return "%s[(%s) + (%s)]" % (sym, off, idx)
            if key == ('system', 'random') and self.framework == 'cc65':
                # cc65's rand() returns int 0..32767; GBDK-2020's returns a
                # u8. mosaik types the call u8, so cast here -- otherwise
                # `system.random() < 10` fires ~128x less often on Lynx/PCE
                # than on the GBDK consoles.
                return "((uint8_t)rand())"
            if key == ('video', 'disable_lcd') and self.framework == 'cc65':
                # No display-disable on the framebuffer cc65 consoles (Lynx/PCE).
                # The GB "LCD off for a fast VRAM fill" idiom has no equivalent --
                # Suzy/VDC composite from RAM, with no vblank-throttled VRAM
                # writes to avoid -- and the old gbs_video_done mapping tore down
                # the TGI + joystick driver, which is FATAL when called before
                # video init (a wide streamed level disables the LCD around its
                # boot column-fill, exactly that order, and tgi_uninstall() on an
                # uninstalled driver jumps through a null vector). Lower to
                # nothing; as a statement it is dropped by _gen_expression_stmt.
                # enable_lcd still maps to gbs_video_init, which brings video up.
                return ""
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
