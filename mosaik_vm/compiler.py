"""mosaik_vm.compiler - The bytecode Compiler + CompiledProgram (src/scripts.mos emission)."""

from .events import lower_event
from .fmt import _disasm_rpn, _emit_array, _escape, _ident
from .isa import Anchor, Instr, Label, VmError, _INTERP_RE, iter_instructions


class Compiler:
    # Hard caps enforced at compile time (a clear error beats silent on-console
    # corruption): the interpreter heap is VM_HEAP i16 cells, string ids are u8,
    # and the PC / jump targets are u16. Kept in lockstep with lib/vm/core.mos.
    HEAP_CAP = 128         # core.mos VM_HEAP (heap[i16, 128], VM8 spec §14); index >= 128 is OOB
    STRING_CAP = 256       # u8 string id
    BLOB_CAP = 65535       # u16 PC / JUMP / CALL / THREAD targets

    def __init__(self):
        self.strings = []          # id -> text
        self._string_ids = {}      # text -> id
        self.choice_ids = set()    # ids a MENU offers as an option
        self.vars = {}             # name -> heap index
        self._label = 0            # auto intra-script label counter
        self._begin_label_scope()

    def new_label(self):
        n = "__L%d" % self._label
        self._label += 1
        return n

    # -- authored labels (`label` / `goto`) --------------------------------
    #
    # A label NAME is scoped to its SCRIPT, as the reference engine's `labelLookup` is
    # scoped to its script builder: two scripts may both say `label retry`.
    # Each name maps to an ordinary internal anchor, so layout and the
    # duplicate-anchor check are the ones `if` / `wait_until` already use.

    def _begin_label_scope(self):
        self._user_labels = {}     # authored name -> internal anchor
        self._user_defined = set()

    def _user_anchor(self, name):
        if name not in self._user_labels:
            self._user_labels[name] = self.new_label()
        return self._user_labels[name]

    def define_label(self, name):
        if name in self._user_defined:
            raise VmError("label %r is defined twice in one script" % name)
        self._user_defined.add(name)
        return self._user_anchor(name)

    def goto_label(self, name):
        return self._user_anchor(name)

    def _end_label_scope(self, script):
        missing = sorted(set(self._user_labels) - self._user_defined)
        self._begin_label_scope()
        if missing:
            raise VmError("script %r: goto names a label the script does not "
                          "define: %s" % (script, ", ".join(missing)))

    def intern_option(self, text):
        """intern_text for a MENU OPTION, and REMEMBER that it is one.

        render_choice's token-aware scanner is ~260 B of resident image on the
        GB family, and `_has_interp()` is true of the whole string table - so
        gating on it made every project with an interpolated TEXT BOX pay for a
        menu renderer that could never use it (measured: the reference-engine sample conversion, whose
        two options are "Save Game" / "Cancel", lost 263 B of bank 0). The
        byte-identical-when-absent rule is per FEATURE, so the choice renderer
        asks its own question."""
        self.choice_ids.add(self.intern_text(text))
        return self._string_ids[text]

    def intern_string(self, text):
        if text not in self._string_ids:
            self._string_ids[text] = len(self.strings)
            self.strings.append(text)
        return self._string_ids[text]

    def intern_text(self, text):
        """Intern a UI string AND register each $var$ token as a heap var, so
        the interpolation seam resolves the name to a cell.

        Text boxes AND menu options both come through here. Options used to
        take the plain intern_string ("they stay literal"), which was never
        true of the OUTPUT: `_encode_blob_string` runs over the whole string
        table, so an option naming a variable some OTHER string had already
        registered was encoded with the 0x01 token and then copied RAW into
        render_choice's line buffer - two junk glyphs where the value belongs
        (the RPG check conversion's inn reads "Rest (  Gold)" against the reference's
        "Rest (2 Gold)"). Registering here is what makes the token honest for
        an option that is the ONLY user of its variable."""
        for m in _INTERP_RE.finditer(text):
            self.var_index(m.group(1))
        return self.intern_string(text)

    def var_index(self, name):
        if name not in self.vars:
            self.vars[name] = len(self.vars)
        return self.vars[name]

    def compile(self, scripts, seed_vars=None):
        """scripts = ordered list of {name, events, loop?}. The FIRST script is
        the boot entry (conventionally 'main'). Returns a CompiledProgram.

        `seed_vars` (a name list) pre-interns heap variables in that exact index
        order BEFORE lowering. Per-console script filtering (review 2.1) compiles
        each target BUCKET separately, so without a seed a bucket that drops a
        script would renumber the heap; seeding with the FULL program's variable
        order keeps every console's heap layout identical (so a save / the
        Variables dock mean the same thing on every ROM)."""
        if not scripts:
            raise VmError("no scripts to compile")
        for _v in (seed_vars or []):
            self.var_index(_v)
        order = [s["name"] for s in scripts]
        if len(set(order)) != len(order):
            raise VmError("duplicate script name")

        # Lower each script to a list of items (Instr | Anchor); symbolic
        # targets (script names AND intra-script labels) stay Labels.
        lowered = {}
        for s in scripts:
            items = []
            self._begin_label_scope()
            for ev in s.get("events", []):
                for mnem, operands in lower_event(ev, self):
                    if mnem == "__ANCHOR__":
                        items.append(Anchor(operands[0]))
                    else:
                        items.append(Instr(mnem, operands))
            self._end_label_scope(s["name"])
            if s.get("loop"):
                # a looping thread jumps back to its own start
                items.append(Instr("JUMP", [Label(s["name"])]))
            elif s.get("sub"):
                # a subroutine returns to its caller (paired with the CALL op)
                items.append(Instr("RET", []))
            elif not _ends_thread(s.get("events", [])):
                # EVERY plain script must END. The scripts concatenate into ONE
                # blob, so a script that just runs out of events does not stop
                # -- the thread walks straight into whatever script was laid
                # down after it and keeps executing. That is not a subtle
                # corruption, it is the next script running at the wrong time:
                # a reference-engine import's town-room init fell through the next
                # scene's init into `logo_init`, whose `wait 30` +
                # change-scene threw the player back to the title screen a
                # second after arriving (and every other On Init did the same
                # into whatever followed it -- "the scenes change at random").
                # Only appended when the script does not already end itself, so
                # a terminated script stays byte-identical.
                items.append(Instr("STOP", []))
            lowered[s["name"]] = items

        # Layout: concatenate scripts in declaration order into ONE blob (the
        # (blob id, offset) residency split of the concept layers on later); each
        # script name AND each intra-script Anchor gets its byte offset.
        offsets = {}
        off = 0
        for name in order:
            offsets[name] = off
            for it in lowered[name]:
                if isinstance(it, Anchor):
                    if it.name in offsets:
                        raise VmError("duplicate label %r" % it.name)
                    offsets[it.name] = off
                off += it.size()
        total = off

        # Resolve + encode; record the debug map (offset -> script + event).
        code = bytearray()
        debug = []
        for name in order:
            idx = 0
            for it in lowered[name]:
                if isinstance(it, Anchor):
                    continue
                debug.append({"off": len(code), "script": name,
                              "event": idx, "op": it.mnem})
                code += it.encode(offsets)
                idx += 1
        assert len(code) == total
        # Enforce the hard caps -- exceeding any of these builds fine but corrupts
        # at run time (a heap write past cell 63, a string id wrapping in a u8, a
        # jump target wrapping in the u16 PC). Fail loudly instead.
        if len(self.vars) > self.HEAP_CAP:
            raise VmError(
                "too many heap variables: %d used, the interpreter heap holds %d "
                "(core.mos VM_HEAP). Reduce distinct variable names."
                % (len(self.vars), self.HEAP_CAP))
        if len(self.strings) > self.STRING_CAP:
            raise VmError(
                "too many unique strings: %d, the cap is %d (u8 string id)."
                % (len(self.strings), self.STRING_CAP))
        if total > self.BLOB_CAP:
            raise VmError(
                "bytecode blob is %d bytes, over the %d-byte cap (u16 PC)."
                % (total, self.BLOB_CAP))
        return CompiledProgram(bytes(code), offsets, order, list(self.strings),
                               dict(self.vars), debug, lowered,
                               choice_ids=set(self.choice_ids))


class CompiledProgram:
    def __init__(self, code, offsets, order, strings, variables, debug, lowered,
                 choice_ids=None):
        # Which interned strings a MENU offers as an option (see
        # Compiler.intern_option): what render_choice's interpolation is gated
        # on, so a game whose tokens are all in TEXT BOXES pays nothing here.
        self.choice_ids = set(choice_ids or ())
        self.code = code
        self.offsets = offsets
        self.order = order
        self.strings = strings
        self.variables = variables
        self.debug = debug
        self.lowered = lowered

    # Total authored-string bytes at/above which render_text/render_choice
    # switch from per-string `print_string` codegen (resident CODE + string
    # literals) to a byte BLOB + a generic line-buffer renderer (the string half
    # of the streaming story). Below
    # it, the switch form is cheaper and stays BYTE-IDENTICAL, so small-text
    # games are untouched; a real dialogue game crosses it and wins (its text
    # becomes one compact const the Lynx can stream, instead of scaling the
    # resident MAIN). Set above every current sample's string budget.
    STRING_BLOB_THRESHOLD = 256

    # Columns a $var$ token reserves in a text box (so trailing text can't
    # overlap a wide value): 0..65535 = up to 5 digits. A shorter value leaves a
    # gap; matching the composer's fixed-reserve rule.
    INTERP_COLS = 5

    @property
    def entry(self):
        return self.offsets[self.order[0]]

    def apply_font_map(self, mapping):
        """Recode the string table through the font's `mapping` (see
        `apply_font_map`). Runs on the COMPILED table, before any renderer is
        emitted, so the blob, the literal switch form and the line counts all
        read the recoded text. No mapping = no change, byte for byte."""
        if mapping:
            self.strings = [apply_font_map(s, mapping) for s in self.strings]
        return self

    # --- $var$ interpolation (text boxes only) ---
    def _has_interp(self):
        """True if any authored string carries a $var$ token."""
        return any(_INTERP_RE.search(s) for s in self.strings)

    def _has_choice_interp(self):
        """...and True if a MENU OPTION does. A separate question from
        `_has_interp` on purpose: the two renderers are independent features
        and the scanner is real resident image (263 B on the reference-engine sample conversion, whose
        only options are "Save Game" / "Cancel")."""
        return any(_INTERP_RE.search(self.strings[i])
                   for i in self.choice_ids if i < len(self.strings))

    def _interp_parts(self, line):
        """Split one line into [('lit', str) | ('var', heap_idx)] parts. An
        unknown token stays literal (it never allocated a var)."""
        parts, last = [], 0
        for m in _INTERP_RE.finditer(line):
            if m.start() > last:
                parts.append(("lit", line[last:m.start()]))
            name = m.group(1)
            if name in self.variables:
                parts.append(("var", self.variables[name]))
            else:
                parts.append(("lit", m.group(0)))
            last = m.end()
        if last < len(line):
            parts.append(("lit", line[last:]))
        return parts

    def interpolate(self, text, heap):
        """Expand $var$ tokens using an index-keyed `heap` (list or dict) --
        the Python mirror of render_text, for headless assertions."""
        def repl(m):
            idx = self.variables.get(m.group(1))
            if idx is None:
                return m.group(0)
            try:
                return str(heap[idx])
            except (KeyError, IndexError):
                return "0"
        return _INTERP_RE.sub(repl, text)

    def _encode_blob_string(self, s):
        """Encode one string for the STRINGS blob. A $var$ token becomes a 0x01
        marker + the heap-index byte (render_text prints the value there); plain
        text is ASCII -- except under VWF, where it is LATIN-1: a variable-width
        sheet covers ASCII 32..255 and its high glyphs are load-bearing (the platformer conversion
        pads lines with its authored 1 px / 4 px spacers at 239 / 255). No
        token -> byte-identical to the raw encoding."""
        enc = "latin-1" if getattr(self, "_vwf", False) else "ascii"
        if not self._has_interp() or "$" not in s:
            return s.encode(enc, "replace")
        out, last = bytearray(), 0
        for m in _INTERP_RE.finditer(s):
            out.extend(s[last:m.start()].encode(enc, "replace"))
            name = m.group(1)
            if name in self.variables:
                out.append(1)                          # SOH: a var token follows
                out.append(self.variables[name] & 0xFF)
            else:
                out.extend(m.group(0).encode(enc, "replace"))
            last = m.end()
        out.extend(s[last:].encode(enc, "replace"))
        return bytes(out)

    def _use_string_blob(self):
        """Whether the compact STRINGS-blob renderer wins over per-string code.

        FORCED whenever the program sets a text speed: the typewriter's step
        renderer is a per-character walk over the byte blob, and stepping the
        switch form's per-string print_string literals has no sane shape. A
        small-text game that opts into the typewriter simply gets the blob
        renderer too; a game that never sets a speed keeps its historical
        form byte-identically."""
        if not self.strings:
            return False
        if self._string_bytes() >= self.STRING_BLOB_THRESHOLD:
            return True
        return (self._uses_typewriter() or getattr(self, "_force_step", False)
                or getattr(self, "_vwf", False))

    def _uses_typewriter(self):
        """Does the compiled blob run TEXT_SPEED anywhere? Decoded with
        isa.iter_instructions (the ONE stepper) - never by scanning for the
        opcode byte, which any operand can hold."""
        for _off, name, _size, _rpn in iter_instructions(self.code):
            if name == "TEXT_SPEED":
                return True
        return False

    def _string_bytes(self):
        """Total encoded byte cost of the string table (lines + separators)."""
        return sum(len(s) + 1 for s in self.strings)  # +1: the 0x00 terminator

    def _string_blob(self):
        """Encode the string table as (blob_bytes, per-string u16 offsets).

        Each string's lines are joined by 0x0A and the whole string is 0x00
        terminated; render_text splits back on 0x0A (up to 3 lines), render_choice
        reads line 0. ASCII only (the console fonts are ASCII), matching the
        literal `print_string` path it replaces."""
        blob = bytearray()
        offsets = []
        for s in self.strings:
            offsets.append(len(blob))
            blob.extend(self._encode_blob_string(s))   # '\n' -> 0x0A; $var$ -> 0x01+idx
            blob.append(0)
        return bytes(blob), offsets

    def _emit_render_switch(self, L):
        """The original per-string renderers (a `switch` of literal print_string
        calls). Kept BYTE-IDENTICAL for small-text games (below the blob
        threshold), so existing samples are untouched."""
        L.append("    -- Draw string `id` into the bottom text box. The row is")
        L.append("    -- SCREEN_ROWS-relative so the box sits at the BOTTOM of EVERY")
        L.append("    -- console (byte-identical on the 18-row GB/GG; on the 12-row")
        L.append("    -- Lynx a hardcoded row 15 fell OFF the short screen).")
        interp = self._has_interp()
        L.append("    function render_text(id: u8) {")
        if self.strings:
            L.append("        switch id {")
            for sid, text in enumerate(self.strings):
                L.append("            case %d {" % sid)
                # A line with a $var$ token positions everything AFTER the
                # value at a RUNTIME column advanced by the value's actual
                # digit count -- a compile-time column had to reserve
                # INTERP_COLS (5) whatever the value, leaving a 4-col gap
                # after a 1-digit number and clipping the line's tail at the
                # 20-col window. INTERP_COLS survives only as the wrap
                # estimate (conservative, so a line can never overflow).
                if interp and any(_INTERP_RE.search(ln)
                                  for ln in text.split("\n")):
                    L.append("                var c: u8 = 0")
                    L.append("                var v: u16 = 0")
                for i, line in enumerate(text.split("\n")):
                    # CORE owns the box geometry (it sized the frame from this
                    # string's line count), so the text origin is asked for
                    # rather than computed -- frame and text cannot disagree.
                    row = "core.box_top() + %d" % i if i else "core.box_top()"
                    if interp and _INTERP_RE.search(line):
                        L.append("                c = core.box_left()")
                        for kind, val in self._interp_parts(line):
                            if kind == "lit":
                                if val:
                                    L.append('                text.print_string(c, %s, "%s")'
                                             % (row, _escape(val)))
                                    L.append("                c += %d" % len(val))
                            else:  # a $var$ token -> print the live heap value
                                L.append("                v = core.var_get(%d)" % val)
                                L.append("                text.print_number(c, %s, v)" % row)
                                L.append("                c += 1")
                                L.append("                if v > 9 { c += 1 }")
                                L.append("                if v > 99 { c += 1 }")
                                L.append("                if v > 999 { c += 1 }")
                                L.append("                if v > 9999 { c += 1 }")
                    else:
                        L.append('                text.print_string(core.box_left(), %s, "%s")'
                                 % (row, _escape(line)))
                L.append("            }")
            L.append("        }")
        L.append("    }")
        L.append("")
        # Per-option menu renderer: a '>' cursor + the option string. Same string
        # table as render_text; the shell wires it via core.set_choice.
        L.append("    -- Draw menu option `id` at `row` with a cursor when selected.")
        L.append("    function render_choice(id: u8, row: u8, sel: u8) {")
        L.append("        if sel == 1 {")
        L.append('            text.print_string(1, row, ">")')
        L.append("        } else {")
        L.append('            text.print_string(1, row, " ")')
        L.append("        }")
        if self.strings:
            L.append("        switch id {")
            for sid, text in enumerate(self.strings):
                line = text.split("\n")[0]
                L.append("            case %d {" % sid)
                # A $var$ token in an OPTION prints the live value at a
                # runtime column, exactly as render_text does. The reference engine
                # writes prices into its choices ("Rest ($gold$ Gold)"),
                # which a literal option cannot say.
                #
                # `sid in self.choice_ids` as well as the token: a case
                # is emitted for EVERY string, so a token that only ever
                # appears in a TEXT BOX would otherwise build a live read
                # into a menu case nothing can reach.
                if (interp and sid in self.choice_ids
                        and _INTERP_RE.search(line)):
                    L.append("                var c: u8 = 3")
                    L.append("                var v: u16 = 0")
                    for kind, val in self._interp_parts(line):
                        if kind == "lit":
                            if val:
                                L.append('                text.print_string(c, row, "%s")'
                                         % _escape(val))
                                L.append("                c += %d" % len(val))
                        else:
                            L.append("                v = core.var_get(%d)" % val)
                            L.append("                text.print_number(c, row, v)")
                            L.append("                c += 1")
                            L.append("                if v > 9 { c += 1 }")
                            L.append("                if v > 99 { c += 1 }")
                            L.append("                if v > 999 { c += 1 }")
                            L.append("                if v > 9999 { c += 1 }")
                else:
                    L.append('                text.print_string(3, row, "%s")'
                             % _escape(line))
                L.append("            }")
            L.append("        }")
        L.append("    }")
        L.append("")

    def _emit_render_step_vwf(self, L):
        """The typewriter step renderer, VARIABLE-WIDTH.

        Same CONTRACT as the fixed-width form - plot printable chars
        [from, upto) of string `id` and return how many were plotted - but the
        pen lives in the engine (`text.vwf_*`), so a glyph may straddle cells
        and a line no longer advances one cell per character.

        **The continuation is a CACHE, not a contract change.** The reference engine's
        renderer is a pure continuation that can never re-render, which is
        O(1) per character but gives up the ability to redraw a box from
        scratch. Here, `from` matching where the pen was parked means we keep
        compositing (the same O(1)); anything else - a redraw, a new box, a
        fast-forward arriving out of step - REWINDS and re-composites the
        prefix, which is always correct. So the windowed contract still holds
        and the fast path is the reference's.

        That also retires a hazard rather than adding one: the fixed-width
        form has to advance its column by the same amount a prefix call did or
        a later window prints at a drifted column (see `_emit_render_step`).
        On the continue path there is no prefix to disagree with.
        """
        L.append("    -- Typewriter step (variable width): composite printable chars")
        L.append("    -- [from, upto), return the count. core.set_text_step by rooms.mos.")
        L.append("    function render_text_step(id: u8, from: u8, upto: u8) -> u8 {")
        L.append("        var off: u16 = STR_OFF[id]")
        L.append("        var lineno: u8 = 0")
        L.append("        var n: u8 = 0        -- chars on THIS line (the line clamp)")
        L.append("        var idx: u8 = 0")
        L.append("        var drawn: u8 = 0")
        L.append("        var skip: u8 = 0     -- walk past these WITHOUT compositing")
        L.append("        if id == _vw_id {")
        L.append("            if from == _vw_next {")
        L.append("                skip = from        -- the pen is already here")
        L.append("            }")
        L.append("        }")
        L.append("        if skip == 0 {")
        L.append("            text.vwf_start(core.box_left(), core.box_top())")
        L.append("        }")
        L.append("        var ch: u8 = assets.code_byte(STRINGS, off)")
        L.append("        while ch != 0 {")
        L.append("            if idx >= upto {")
        L.append("                _vw_id = id")
        L.append("                _vw_next = idx")
        L.append("                return drawn")
        L.append("            }")
        L.append("            if ch == 10 {")
        L.append("                lineno += 1")
        L.append("                n = 0")
        L.append("                if lineno >= 3 {")
        L.append("                    _vw_id = id")
        L.append("                    _vw_next = idx")
        L.append("                    return drawn")
        L.append("                }")
        L.append("                -- A part-filled cell is abandoned here, in the engine.")
        L.append("                if idx >= skip {")
        L.append("                    text.vwf_nl(core.box_left(), core.box_top() + lineno)")
        L.append("                }")
        if self._has_interp():
            L.append("            } else if ch == 1 {")
            L.append("                -- a $var$ token: its digits appear as ONE step, through")
            L.append("                -- the same pen (print_number would stamp whole cells).")
            L.append("                off += 1")
            L.append("                var vi: u8 = assets.code_byte(STRINGS, off)")
            L.append("                var vv: u16 = core.var_get(vi)")
            L.append("                if idx >= skip {")
            L.append("                    if n < 20 {")
            L.append("                        text.vwf_number(vv)")
            L.append("                    }")
            L.append("                }")
            L.append("                if idx >= from {")
            L.append("                    drawn += 1")
            L.append("                }")
            L.append("                idx += 1")
        L.append("            } else {")
        L.append("                if idx >= skip {")
        L.append("                    -- n counts completed CELLS of PLOTTED chars (on the")
        L.append("                    -- continue path the prefix is uncounted, which only")
        L.append("                    -- matters for a line wider than the clamp).")
        L.append("                    if n < 20 {")
        L.append("                        n += text.vwf_glyph(ch)")
        L.append("                    }")
        L.append("                }")
        L.append("                if idx >= from {")
        L.append("                    drawn += 1")
        L.append("                }")
        L.append("                idx += 1")
        L.append("            }")
        L.append("            off += 1")
        L.append("            ch = assets.code_byte(STRINGS, off)")
        L.append("        }")
        L.append("        _vw_id = id")
        L.append("        _vw_next = idx")
        L.append("        return drawn")
        L.append("    }")
        L.append("")

    def _emit_render_step(self, L):
        """The TYPEWRITER's step renderer: plot printable chars [from, upto)
        of string `id`, return how many were plotted (fewer than asked = the
        text ran out, vm.core's completion signal; upto 255 = the rest).

        Emitted ONLY when the blob sets a text speed, and only in the BLOB
        form (_use_string_blob forces it for typewriter users): a per-char
        walk needs the byte blob. The walk itself is cheap (banked const
        reads); only the chars inside the window PLOT. A $var$ token counts
        as ONE printable char and its digits appear together - the reference engine
        bakes the value into its buffer at box open, we read it live, and
        the column advance below must match what a prefix call advanced or
        a later window would print at a drifted column."""
        if getattr(self, "_vwf", False):
            self._emit_render_step_vwf(L)
            return
        L.append("    var _ch: array[u8, 2]      -- scratch: one char + null")
        L.append("")
        L.append("    -- Typewriter step: plot printable chars [from, upto), return the")
        L.append("    -- count plotted. Registered as core.set_text_step by rooms.mos.")
        L.append("    function render_text_step(id: u8, from: u8, upto: u8) -> u8 {")
        L.append("        var off: u16 = STR_OFF[id]")
        L.append("        var lineno: u8 = 0")
        L.append("        var col: u8 = core.box_left()")
        L.append("        var n: u8 = 0        -- chars on THIS line (the 20-col clamp)")
        L.append("        var idx: u8 = 0")
        L.append("        var drawn: u8 = 0")
        L.append("        var ch: u8 = assets.code_byte(STRINGS, off)")
        L.append("        while ch != 0 {")
        L.append("            if idx >= upto {")
        L.append("                return drawn    -- past the window: nothing left to plot")
        L.append("            }")
        L.append("            if ch == 10 {")
        L.append("                lineno += 1")
        L.append("                col = core.box_left()")
        L.append("                n = 0")
        L.append("                if lineno >= 3 {")
        L.append("                    return drawn")
        L.append("                }")
        if self._has_interp():
            L.append("            } else if ch == 1 {")
            L.append("                -- a $var$ token: the digits appear as ONE step")
            L.append("                off += 1")
            L.append("                var vi: u8 = assets.code_byte(STRINGS, off)")
            L.append("                var vv: u16 = core.var_get(vi)")
            L.append("                if idx >= from {")
            L.append("                    text.print_number(col, core.box_top() + lineno, vv)")
            L.append("                    drawn += 1")
            L.append("                }")
            L.append("                col += 1")
            L.append("                if vv > 9 { col += 1 }")
            L.append("                if vv > 99 { col += 1 }")
            L.append("                if vv > 999 { col += 1 }")
            L.append("                if vv > 9999 { col += 1 }")
            L.append("                idx += 1")
        L.append("            } else {")
        L.append("                if idx >= from {")
        L.append("                    if n < 20 {     -- render_text's _line clamp")
        L.append("                        _ch[0] = ch")
        L.append("                        _ch[1] = 0")
        L.append("                        text.print_string(col, core.box_top() + lineno, _ch)")
        L.append("                    }")
        L.append("                    drawn += 1")
        L.append("                }")
        L.append("                col += 1")
        L.append("                n += 1")
        L.append("                idx += 1")
        L.append("            }")
        L.append("            off += 1")
        L.append("            ch = assets.code_byte(STRINGS, off)")
        L.append("        }")
        L.append("        return drawn")
        L.append("    }")
        L.append("")

    def _emit_render_blob_vwf(self, L):
        """`render_text` / `render_choice`, VARIABLE-WIDTH (vwf-text-plan V4b).

        The full-redraw siblings of `_emit_render_step_vwf`. Both ALWAYS start
        the ring afresh (they are redraws, so there is nothing to continue
        from), which is also why `render_text` invalidates the stepper's
        continuation cache: it has just rewound the ring out from under it, and
        a stale `_vw_next` would then let a later step believe the pen was
        parked somewhere it is not.

        The menu cursor goes through the pen too rather than staying a cell
        plot. It has to: a cell plot resolves through the glyph CACHE, which
        allocates out of the SAME tile band the ring walks, so the two would
        hand out the same tiles to different glyphs.
        """
        step = self._uses_typewriter() or getattr(self, "_force_step", False)
        if step:
            L.append("    -- VWF continuation cache: the string the pen is parked in, and")
            L.append("    -- the index it is parked AT. A match means keep compositing;")
            L.append("    -- anything else rewinds and re-composites the prefix (always")
            L.append("    -- correct, just slower). 255 = invalid, so a fresh box rewinds.")
            L.append("    -- Declared HERE, ahead of render_text, because that redraw")
            L.append("    -- invalidates them.")
            L.append("    var _vw_id: u8 = 255")
            L.append("    var _vw_next: u8 = 255")
            L.append("")
        L.append("    -- The row the LAST menu option was drawn at (255 = none). vm.core")
        L.append("    -- redraws a menu's options top to bottom on every cursor change, so")
        L.append("    -- 'this row is not the one below the last' is the first option of a")
        L.append("    -- redraw, where the ring restarts; render_text resets it, a box")
        L.append("    -- between two menus being the other way a ring gets rewound.")
        L.append("    var _mrow: u8 = 255")
        L.append("")
        L.append("    -- Draw string `id` (up to 3 lines split on 0x0A) at the bottom box,")
        L.append("    -- compositing at a sub-cell pen (variable width).")
        L.append("    function render_text(id: u8) {")
        L.append("        var off: u16 = STR_OFF[id]")
        L.append("        var lineno: u8 = 0")
        L.append("        var done: u8 = 0")
        L.append("        text.vwf_start(core.box_left(), core.box_top())")
        L.append("        _mrow = 255")
        if step:
            L.append("        -- This redraw just rewound the ring: the step renderer's pen is")
            L.append("        -- no longer where its cache says, so drop it.")
            L.append("        _vw_id = 255")
            L.append("        _vw_next = 255")
        L.append("        while done == 0 {")
        L.append("            var n: u8 = 0")
        L.append("            var ch: u8 = assets.code_byte(STRINGS, off)")
        L.append("            while ch != 0 and ch != 10 {")
        if self._has_interp():
            L.append("                if ch == 1 {")
            L.append("                    off += 1")
            L.append("                    var vi: u8 = assets.code_byte(STRINGS, off)")
            L.append("                    var vv: u16 = core.var_get(vi)")
            L.append("                    if n < 20 {")
            L.append("                        text.vwf_number(vv)")
            L.append("                    }")
            L.append("                } else {")
            L.append("                    -- n counts completed CELLS (the return), not chars:")
            L.append("                    -- a 44-char line is ~19 cells under this font.")
            L.append("                    if n < 20 {")
            L.append("                        n += text.vwf_glyph(ch)")
            L.append("                    }")
            L.append("                }")
        else:
            L.append("                if n < 20 {")
            L.append("                    n += text.vwf_glyph(ch)")
            L.append("                }")
        L.append("                off += 1")
        L.append("                ch = assets.code_byte(STRINGS, off)")
        L.append("            }")
        L.append("            if ch == 0 {")
        L.append("                done = 1")
        L.append("            } else {")
        L.append("                off += 1")
        L.append("            }")
        L.append("            lineno += 1")
        L.append("            if lineno >= 3 { done = 1 }")
        L.append("            if done == 0 {")
        L.append("                text.vwf_nl(core.box_left(), core.box_top() + lineno)")
        L.append("            }")
        L.append("        }")
        L.append("    }")
        L.append("")
        L.append("    -- Draw menu option `id` at `row`, cursor when selected. THE OPTIONS")
        L.append("    -- GO THROUGH THE PEN under VWF, as the reference engine's do (its menu text is")
        L.append("    -- the same variable-width font as its dialogue): the fixed-width")
        L.append("    -- glyph CACHE keeps only GBS_VWF_RESERVE (8) tiles of the band under")
        L.append("    -- the partition, and a menu of three options has ~20 distinct glyphs")
        L.append("    -- - measured on a tutorial conversion, every row drew the letters")
        L.append("    -- of the rows before it. The cursor stays a cell plot (one cached")
        L.append("    -- glyph), and the text starts at column 2, right after it, which is")
        L.append("    -- where the reference puts it (scriptBuilder's textCodeGoto(3, 2)).")
        L.append("    -- A redraw re-composites the whole menu from the ring's base: the")
        L.append("    -- first option restarts the ring, each next one continues it.")
        L.append("    function render_choice(id: u8, row: u8, sel: u8) {")
        L.append("        var off: u16 = STR_OFF[id]")
        L.append("        var n: u8 = 0")
        L.append("        if sel == 1 {")
        L.append('            text.print_string(1, row, ">")')
        L.append("        } else {")
        L.append('            text.print_string(1, row, " ")')
        L.append("        }")
        L.append("        if row == _mrow + 1 {")
        L.append("            text.vwf_nl(2, row)")
        L.append("        } else {")
        L.append("            text.vwf_start(2, row)")
        if step:
            L.append("            _vw_id = 255")
            L.append("            _vw_next = 255")
        L.append("        }")
        L.append("        _mrow = row")
        L.append("        var ch: u8 = assets.code_byte(STRINGS, off)")
        L.append("        while ch != 0 and ch != 10 {")
        if self._has_choice_interp():
            # As in render_text: the 0x01 token is a LIVE value, not two glyphs.
            L.append("            if ch == 1 {")
            L.append("                off += 1")
            L.append("                var vi: u8 = assets.code_byte(STRINGS, off)")
            L.append("                var vv: u16 = core.var_get(vi)")
            L.append("                if n < 18 {")
            L.append("                    text.vwf_number(vv)")
            L.append("                }")
            L.append("            } else {")
            L.append("                if n < 18 {")
            L.append("                    n += text.vwf_glyph(ch)")
            L.append("                }")
            L.append("            }")
        else:
            L.append("            if n < 18 {")
            L.append("                n += text.vwf_glyph(ch)")
            L.append("            }")
        L.append("            off += 1")
        L.append("            ch = assets.code_byte(STRINGS, off)")
        L.append("        }")
        L.append("    }")
        L.append("")

    def _emit_render_blob(self, L):
        """The compact renderers (workstream B): authored text is ONE `STRINGS`
        byte blob + per-string `STR_OFF` offsets, read through the assets.code_byte
        seam into a small line buffer -- so text is const DATA (Lynx-streamable)
        instead of resident per-string print_string CODE. Chosen when the string
        budget crosses STRING_BLOB_THRESHOLD (a real dialogue game)."""
        blob, offs = self._string_blob()
        L.append("    -- Authored text as ONE byte blob + per-string offsets")
        L.append("    -- (the string-blob mode): render_text /")
        L.append("    -- render_choice read it through the assets.code_byte seam into")
        L.append("    -- a line buffer, so text is compact const DATA (the Lynx can")
        L.append("    -- stream it) rather than resident per-string print_string code.")
        _emit_array(L, "u8", "STRINGS", len(blob), list(blob))
        _emit_array(L, "u16", "STR_OFF", len(offs), offs)
        if getattr(self, "_vwf", False):
            self._emit_render_blob_vwf(L)
            if self._uses_typewriter() or getattr(self, "_force_step", False):
                self._emit_render_step(L)
            return
        L.append("    var _line: array[u8, 21]   -- scratch: one text line (<=20 cols + null)")
        L.append("")
        L.append("    -- Draw string `id` (up to 3 lines split on 0x0A) at the bottom box,")
        L.append("    -- SCREEN_ROWS-relative (see the switch form for the row rationale).")
        L.append("    function render_text(id: u8) {")
        L.append("        var off: u16 = STR_OFF[id]")
        L.append("        var lineno: u8 = 0")
        L.append("        var done: u8 = 0")
        L.append("        while done == 0 {")
        if self._has_interp():
            # token-aware scan: 0x01 <idx> prints the live value of heap[idx] at
            # the running column ($var$ interpolation), reserving INTERP_COLS.
            L.append("            var col: u8 = core.box_left()")
            L.append("            var n: u8 = 0")
            L.append("            var ch: u8 = assets.code_byte(STRINGS, off)")
            L.append("            while ch != 0 and ch != 10 {")
            L.append("                if ch == 1 {")
            L.append("                    _line[n] = 0")
            L.append("                    text.print_string(col, core.box_top() + lineno, _line)")
            L.append("                    col += n")
            L.append("                    off += 1")
            L.append("                    var vi: u8 = assets.code_byte(STRINGS, off)")
            L.append("                    var vv: u16 = core.var_get(vi)")
            L.append("                    text.print_number(col, core.box_top() + lineno, vv)")
            # Advance by the value's ACTUAL digit count, not a fixed reserve.
            # A fixed 5 left a 4-column gap after a 1-digit value and pushed
            # the rest of the line off the 20-col window ("there is 3    lef"
            # where the reference engine prints "there is 3 left"). INTERP_COLS survives
            # only as the compile-time WRAP estimate (conservative: a line is
            # wrapped as if the value were 5 digits, so it can never overflow).
            L.append("                    col += 1")
            L.append("                    if vv > 9 { col += 1 }")
            L.append("                    if vv > 99 { col += 1 }")
            L.append("                    if vv > 999 { col += 1 }")
            L.append("                    if vv > 9999 { col += 1 }")
            L.append("                    n = 0")
            L.append("                } else {")
            L.append("                    if n < 20 {")
            L.append("                        _line[n] = ch")
            L.append("                        n += 1")
            L.append("                    }")
            L.append("                }")
            L.append("                off += 1")
            L.append("                ch = assets.code_byte(STRINGS, off)")
            L.append("            }")
            L.append("            _line[n] = 0")
            L.append("            text.print_string(col, core.box_top() + lineno, _line)")
        else:
            L.append("            var n: u8 = 0")
            L.append("            var ch: u8 = assets.code_byte(STRINGS, off)")
            L.append("            while ch != 0 and ch != 10 {")
            L.append("                if n < 20 {")
            L.append("                    _line[n] = ch")
            L.append("                    n += 1")
            L.append("                }")
            L.append("                off += 1")
            L.append("                ch = assets.code_byte(STRINGS, off)")
            L.append("            }")
            L.append("            _line[n] = 0")
            L.append("            text.print_string(core.box_left(), core.box_top() + lineno, _line)")
        L.append("            if ch == 0 {")
        L.append("                done = 1")
        L.append("            } else {")
        L.append("                off += 1")
        L.append("            }")
        L.append("            lineno += 1")
        L.append("            if lineno >= 3 { done = 1 }")
        L.append("        }")
        L.append("    }")
        L.append("")
        if self._uses_typewriter() or getattr(self, "_force_step", False):
            self._emit_render_step(L)
        L.append("    -- Draw menu option `id` at `row` with a cursor when selected.")
        L.append("    function render_choice(id: u8, row: u8, sel: u8) {")
        L.append("        if sel == 1 {")
        L.append('            text.print_string(1, row, ">")')
        L.append("        } else {")
        L.append('            text.print_string(1, row, " ")')
        L.append("        }")
        L.append("        var off: u16 = STR_OFF[id]")
        L.append("        var n: u8 = 0")
        L.append("        var ch: u8 = assets.code_byte(STRINGS, off)")
        if self._has_choice_interp():
            # The SAME token-aware walk render_text runs, at the option's own
            # base column. Without it the 0x01 marker + heap index were copied
            # into _line and PRINTED - two junk glyphs where the reference engine shows a
            # price ("Rest (2 Gold)" read "Rest (  Gold)").
            L.append("        var col: u8 = 3")
            L.append("        while ch != 0 and ch != 10 {")
            L.append("            if ch == 1 {")
            L.append("                _line[n] = 0")
            L.append("                text.print_string(col, row, _line)")
            L.append("                col += n")
            L.append("                off += 1")
            L.append("                var vi: u8 = assets.code_byte(STRINGS, off)")
            L.append("                var vv: u16 = core.var_get(vi)")
            L.append("                text.print_number(col, row, vv)")
            L.append("                col += 1")
            L.append("                if vv > 9 { col += 1 }")
            L.append("                if vv > 99 { col += 1 }")
            L.append("                if vv > 999 { col += 1 }")
            L.append("                if vv > 9999 { col += 1 }")
            L.append("                n = 0")
            L.append("            } else {")
            L.append("                if n < 20 {")
            L.append("                    _line[n] = ch")
            L.append("                    n += 1")
            L.append("                }")
            L.append("            }")
            L.append("            off += 1")
            L.append("            ch = assets.code_byte(STRINGS, off)")
            L.append("        }")
            L.append("        _line[n] = 0")
            L.append("        text.print_string(col, row, _line)")
        else:
            L.append("        while ch != 0 and ch != 10 {")
            L.append("            if n < 20 {")
            L.append("                _line[n] = ch")
            L.append("                n += 1")
            L.append("            }")
            L.append("            off += 1")
            L.append("            ch = assets.code_byte(STRINGS, off)")
            L.append("        }")
            L.append("        _line[n] = 0")
            L.append("        text.print_string(3, row, _line)")
        L.append("    }")
        L.append("")

    # ---- artifact emitters ----
    def to_vms(self):
        """The `.vms` text assembly (a first-class, hand-authorable artifact)."""
        out = ["; GENERATED by mosaik_vm.py -- the .vms text assembly form.",
               "; Labels are script entry points; THREAD/JUMP take a label.", ""]
        for name in self.order:
            out.append("%s:" % name)
            for it in self.lowered[name]:
                if isinstance(it, Anchor):
                    out.append("  %s:" % it.name)
                    continue
                if it.mnem == "RPN":
                    out.append("    RPN        %s" % _disasm_rpn(it.operands[0]))
                    continue
                if it.mnem == "MENU":
                    b = bytes(it.operands[0])
                    out.append("    MENU       var %d row %d ids %s"
                               % (b[0], b[1], list(b[3:3 + b[2]])))
                    continue
                args = []
                for v in it.operands:
                    args.append(v.name if isinstance(v, Label) else str(int(v)))
                out.append(("    %-10s %s" % (it.mnem, ", ".join(args))).rstrip())
            out.append("")
        return "\n".join(out)

    def to_map(self):
        return {"total": len(self.code), "entry": self.entry,
                "scripts": self.offsets, "strings": self.strings,
                "variables": self.variables, "instructions": self.debug}

    def to_scripts_mos(self, module="scripts"):
        """The generated `scripts` module: the CODE blob, a fetch callback (the
        interpreter reads the blob through it -- mosaik has no pointers), the
        per-script ENTRY_* offsets, and render_text (the strings lowered to
        literal print_string calls -- authored text becomes data-driven code)."""
        L = []
        L.append("-- %s.mos -- GENERATED by mosaik_vm.py; do not edit by hand." % module)
        L.append("-- Author the event lists (scripts/*.evt.toml) and re-run the")
        L.append("-- compiler. The vm.core interpreter reads CODE through fetch().")
        L.append("")
        L.append('module "%s" {' % module)
        L.append('    import "graphics.text"')
        L.append('    import "platform.assets"')
        # render_text asks CORE where the open box's text goes (box_top /
        # box_left) instead of computing rows itself, so the frame and the text
        # can never disagree about the box's size. ($var$ interpolation also
        # reads live heap values through core.var_get.)
        L.append('    import "vm.core"')
        L.append("")
        self._emit_scripts_body(L)
        L.append("    export %s" % ", ".join(
            _scripts_exports(self.order, step=self._uses_typewriter())))
        L.append("}")
        return "\n".join(L) + "\n"

    def _emit_scripts_body(self, L, force_step=False):
        """The console-varying half of the scripts module: the CODE blob, the
        per-script ENTRY_* offsets, the fetch callback + render_text/render_choice.
        Factored out so per-console filtering can emit it inside an `if platform`
        guard (one body per target bucket); the module wrapper + imports + the
        (uniform) export list stay OUTSIDE the guard. `force_step` makes this
        bucket emit the typewriter step renderer even though its own blob
        never sets a speed - the bucketed export list is shared, so every
        branch must define what any branch exports."""
        self._force_step = force_step
        _emit_array(L, "u8", "CODE", len(self.code), list(self.code))
        L.append("")
        for name in self.order:
            L.append("    const ENTRY_%s: u16 = %d" % (_ident(name), self.offsets[name]))
        L.append("")
        L.append("    -- Read one bytecode byte (the interpreter's fetch callback).")
        L.append("    -- Via the assets.code_byte SEAM: a plain CODE[off] on every")
        L.append("    -- directly-mapped console, and the Lynx cart PAGE-CACHE read")
        L.append("    -- when the blob is big enough to stream.")
        L.append("    function fetch(off: u16) -> u8 {")
        L.append("        return assets.code_byte(CODE, off)")
        L.append("    }")
        L.append("")
        L.append("    -- The CODE WINDOW (vm.core.set_code_window, review V-5): lets the")
        L.append("    -- interpreter read the blob INLINE at its address inside a slice,")
        L.append("    -- mapping its ROM bank once per slice where it is banked. Pinned")
        L.append("    -- home (bank(0)) - a banked enter/leave would be undone by its own")
        L.append("    -- trampoline. Not on the Lynx, whose blob may be cart-streamed and")
        L.append("    -- has no fixed address: there fetch stays the page-cache read.")
        L.append("    -- Call scripts.code_window() once after core.boot (the generated")
        L.append("    -- rooms.start does); a shell that does not is unchanged.")
        L.append("    bank(0) local function code_enter() -> u8 {")
        L.append("        if platform == \"lynx\" {")
        L.append("            return 0")
        L.append("        } else {")
        L.append("            return assets.bank_enter(CODE)")
        L.append("        }")
        L.append("    }")
        L.append("    bank(0) local function code_leave(saved: u8) {")
        L.append("        if platform == \"lynx\" {")
        L.append("        } else {")
        L.append("            assets.bank_leave(saved)")
        L.append("        }")
        L.append("    }")
        L.append("    bank(0) function code_window() {")
        L.append("        if platform == \"lynx\" {")
        L.append("        } else {")
        L.append("            -- VM_CODE_BANKED is stated by the build ([build] bank_bytecode):")
        L.append("            -- a RESIDENT blob skips the per-slice enter/leave pair.")
        L.append("            if VM_CODE_BANKED {")
        L.append("                core.set_code_window(assets.address(CODE), code_enter, code_leave)")
        L.append("            } else {")
        L.append("                core.set_code_window_res(assets.address(CODE))")
        L.append("            }")
        L.append("        }")
        L.append("    }")
        L.append("")
        self._emit_text_lines(L)
        if self._use_string_blob():
            self._emit_render_blob(L)
        else:
            self._emit_render_switch(L)

    def _emit_text_lines(self, L):
        """`text_lines(id)` -- how many LINES string `id` holds.

        `vm.core` asks this BEFORE it draws the frame, so the box can be
        `lines + 2` rows tall and bottom-anchored, exactly as the reference engine sizes
        its own (its generated scripts do `OVERLAY_CLEAR 0,0,20,<lines+2>` +
        `MOVE_TO 0,<18-h>`). Without it the box was a fixed 4 rows and a
        3-line text had nowhere to put its bottom border, so it overwrote it.

        A plain u8 table rather than a switch: one byte per string beats a
        compare chain in both ROM and time, and render_text is on the box-open
        path. Registered by the generated rooms.mos
        (`core.set_text_lines(scripts.text_lines)`); a shell that never
        registers it keeps the historical fixed box."""
        n = len(self.strings)
        L.append("    -- Lines per string: vm.core sizes the dialogue box from this")
        L.append("    -- (border + lines + border), the reference engine's own box geometry.")
        if n:
            _emit_array(L, "u8", "STR_LINES", n,
                        [max(1, min(3, t.count("\n") + 1)) for t in self.strings])
            L.append("    function text_lines(id: u8) -> u8 {")
            L.append("        return STR_LINES[id]")
            L.append("    }")
        else:
            L.append("    function text_lines(id: u8) -> u8 {")
            L.append("        return 1")
            L.append("    }")
        L.append("")
# --------------------------------------------------------------------------
# Per-console SCRIPT filtering (review 2.1 / P1 #5, Stage 3). A `[[script]]` may
# carry a `platforms` allow-list; the scripts module then forks its CODE / ENTRY /
# render body into `if platform` guards, one per target BUCKET, like the scene
# transpiler forks the entity tables. Because `export` can NOT sit inside a
# conditional block, the export list is UNIFORM: every bucket keeps every script
# NAME (so every ENTRY_* is defined + exported), but a script EXCLUDED from a bucket
# is compiled to a 1-byte STOP stub -- its body bytes drop on that console, a
# reference to it (a THREAD / a scenes.mos slot selector) resolves to the stub and
# no-ops, so nothing ever dangles. Untagged programs have one bucket and are emitted
# guard-free = byte-identical.
# --------------------------------------------------------------------------
def _scripts_exports(order, step=False):
    return (["fetch", "code_window", "render_text", "render_choice", "text_lines"]
            + (["render_text_step"] if step else [])
            + ["ENTRY_%s" % _ident(n) for n in order])


def _script_platforms(s):
    """A script's per-console allow-list (canonical ids), or () = every console."""
    raw = s.get("platforms")
    if not raw or not isinstance(raw, (list, tuple)):
        return ()
    try:
        from mosaik.platforms import canonical_platform as _c
    except Exception:  # pragma: no cover
        def _c(x):
            return x
    return tuple(_c(p) for p in raw)


# `goto` never falls through either (its label is in the same script, so the
# STOP after it could only be reached by a jump to the end, which no event
# spells).
_ENDS_THREAD = ("stop", "raise", "change_scene", "reset", "goto")


def _ends_thread(events):
    """True when a script's LAST top-level event already ends its thread.

    Only the top level counts: a `change_scene` inside an `if` is skipped when
    the condition is false, so such a script still needs the implicit STOP."""
    for ev in reversed(events or []):
        name = ev.get("event")
        if name:
            return name in _ENDS_THREAD
    return False


def _stub_script(s):
    """The no-op stand-in for a script EXCLUDED from a bucket: keeps the name (so its
    ENTRY_* stays defined + exported) but drops the body to a single terminator -- a
    plain script -> STOP (ends the spawned thread); a `sub` -> an empty body -> RET
    (returns to its caller); a `loop` flag is dropped (an empty looping thread would
    busy-spin)."""
    if s.get("sub"):
        return {"name": s["name"], "events": [], "sub": True}
    return {"name": s["name"], "events": [{"event": "stop"}]}


def _read_toml(path):
    """Parse a TOML file: `tomllib` on Python 3.11+, else the `toml` package
    (the README's requirement). Both callers below swallow every exception, so
    a bare `import tomllib` turned VWF text and the font mapping OFF, silently,
    on an older Python."""
    try:
        import tomllib
    except ModuleNotFoundError:
        import toml
        with open(path, encoding="utf-8") as f:
            return toml.load(f)
    with open(path, "rb") as f:
        return tomllib.load(f)


def project_uses_vwf(root):
    """True when `root`'s `mosaik.toml [assets] font` names a VARIABLE-width
    sheet (one carrying the reference engine's width-marker colour). The ONE fact the VWF
    text mode keys on -- the importer, a studio edit and a CLI regeneration all
    derive it from the same file, so they can never disagree, and a project
    with no font (or a fixed-width one) is byte-identical by construction."""
    import os
    toml_path = os.path.join(root, "mosaik.toml")
    try:
        cfg = _read_toml(toml_path)
        font = (cfg.get("assets") or {}).get("font")
        if not font:
            return False
        import mosaik_assets
        return mosaik_assets.png_to_font_widths(
            os.path.join(root, font)) is not None
    except Exception:
        return False


def font_map_path(png_path):
    """The recode sidecar of a font sheet: `font.png` -> `font.json`, GB
    Studio's own layout (`lib/fonts/fontData.ts` reads `<name>.json`)."""
    import os
    return os.path.splitext(png_path)[0] + ".json"


def load_font_map(png_path):
    """`{text: [codes]}` from a font sheet's `.json` `mapping`, or {}.

    The reference engine's format: `{"name": ..., "mapping": {"ä": 228, "<3": [128]}}`,
    a key of one or more characters recoded to one or more glyph codes at
    compile time (`shared/lib/helpers/fonts.ts` `encodeString`). An entry is
    kept only when every code is a glyph the engine carries (32..255): a
    control code would split or end a line, and anything wider does not fit
    the string blob's bytes. `table` (code -> glyph index) is not read.

    Read as `utf-8-sig`: a Windows editor that saves with a byte-order mark
    must not turn the whole table into "no mapping" without a word."""
    import json
    try:
        with open(font_map_path(png_path), "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return font_map_entries(data.get("mapping") if isinstance(data, dict) else None)


def font_map_entries(mapping):
    """`{text: [codes]}` for the entries of a raw `mapping` the engine applies
    (see `load_font_map`); the studio's font editor asks the same question.

    An entry that outputs code 36 (`$`) is dropped as well: interpolation runs
    over the RECODED string, so a produced `$` could turn plain text into a
    `$var$` token (`#hp#` with `#` -> 36 printed the variable) or eat the `$`
    of a real one."""
    out = {}
    if not isinstance(mapping, dict):
        return out
    for key, val in mapping.items():
        codes = val if isinstance(val, list) else [val]
        if (key and codes and all(isinstance(c, int) and not isinstance(c, bool)
                                  and 32 <= c <= 255 and c != 36 for c in codes)):
            out[str(key)] = list(codes)
    return out


def project_font_map(root):
    """The recode mapping of `root`'s `mosaik.toml [assets] font`, or {}
    (also for a malformed file or a `font` that is not a path)."""
    import os
    try:
        font = ((_read_toml(os.path.join(root, "mosaik.toml")).get("assets")
                 or {}).get("font"))
        if not isinstance(font, str) or not font:
            return {}
        return load_font_map(os.path.join(root, font))
    except Exception:
        return {}


def apply_font_map(text, mapping):
    """Recode `text` through a font `mapping`: at each position the LONGEST key
    that matches wins (the reference engine's `resolveMapping`), and is replaced by its
    codes as characters; unmatched characters pass through. `$var$` tokens
    and newlines are left alone, so interpolation and line splitting see the
    same string they always did."""
    if not mapping:
        return text
    keys = sorted(mapping, key=len, reverse=True)
    out, last = [], 0

    def recode(seg):
        i, res = 0, []
        while i < len(seg):
            for k in keys:
                if seg.startswith(k, i):
                    res.extend(chr(c) for c in mapping[k])
                    i += len(k)
                    break
            else:
                res.append(seg[i])
                i += 1
        return "".join(res)

    for m in _INTERP_RE.finditer(text):
        out.append("\n".join(recode(ln) for ln in text[last:m.start()].split("\n")))
        out.append(m.group(0))
        last = m.end()
    out.append("\n".join(recode(ln) for ln in text[last:].split("\n")))
    return "".join(out)


def emit_scripts_module(scripts, module="scripts", vwf=False, font_map=None):
    """Generate the `scripts` module text, forking per target console when any
    `[[script]]` carries a `platforms` allow-list (else byte-identical to
    ``Compiler().compile(scripts).to_scripts_mos()``).

    `vwf` routes the text renderers through the VARIABLE-WIDTH compositor
    (`text.vwf_*`); derive it with `project_uses_vwf(root)` so every caller
    keys on the same project fact (the font sheet itself). `font_map` is the
    font's recode table (`project_font_map(root)`); empty changes nothing."""
    from mosaik_scenes import _platform_buckets
    buckets = _platform_buckets([[_script_platforms(s) for s in scripts]])
    if buckets is None:
        prog = Compiler().compile(scripts)
        prog._vwf = vwf
        prog.apply_font_map(font_map)
        return prog.to_scripts_mos(module)
    # Seed every bucket's heap with the FULL program's variable order so the heap
    # layout is identical on every console (stable saves + Variables dock).
    full = Compiler().compile(scripts)
    seed = sorted(full.variables, key=full.variables.get)
    cond_progs = []
    for ids, (keep_idx,) in buckets:
        keep = set(keep_idx)
        bucket = [s if i in keep else _stub_script(s)
                  for i, s in enumerate(scripts)]
        _bp = Compiler().compile(bucket, seed_vars=seed)
        _bp._vwf = vwf
        _bp.apply_font_map(font_map)
        cond_progs.append((ids, _bp))
    return emit_scripts_module_bucketed(cond_progs, module)


def emit_scripts_module_bucketed(cond_progs, module="scripts"):
    """Emit the scripts module with the CODE/ENTRY/render body forked per bucket.
    `cond_progs` = ordered [(platform_ids_or_None, CompiledProgram), ...] with the
    default (None -> the `else`) LAST. Every prog shares the same script `order`, so
    the ENTRY_* consts + the export list are uniform across branches."""
    L = []
    L.append("-- %s.mos -- GENERATED by mosaik_vm.py; do not edit by hand." % module)
    L.append("-- Author the event lists (scripts/*.evt.toml) and re-run the")
    L.append("-- compiler. Forked per console by a [[script]] `platforms` filter.")
    L.append("")
    L.append('module "%s" {' % module)
    L.append('    import "graphics.text"')
    L.append('    import "platform.assets"')
    L.append('    import "vm.core"')   # render_text reads the box geometry from core
    L.append("")
    # A module export list cannot be conditional (the trigger-leave rule), so
    # if ANY bucket's blob sets a text speed, EVERY bucket must define the
    # step renderer - a console whose filtered scripts never set one carries
    # a small dead function rather than an unexported-symbol error.
    step = any(prog._uses_typewriter() for _ids, prog in cond_progs)
    for bi, (ids, prog) in enumerate(cond_progs):
        if bi == 0:
            cond = " or ".join('platform == "%s"' % p for p in ids)
            L.append("    if %s {" % cond)
        elif ids is None:
            L.append("    } else {")
        else:
            cond = " or ".join('platform == "%s"' % p for p in ids)
            L.append("    } else if %s {" % cond)
        prog._emit_scripts_body(L, force_step=step)
    L.append("    }")
    L.append("")
    L.append("    export %s" % ", ".join(
        _scripts_exports(cond_progs[0][1].order, step=step)))
    L.append("}")
    return "\n".join(L) + "\n"
