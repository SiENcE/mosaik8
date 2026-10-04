"""Cc65Backend code banking (the PC Engine and the Atari Lynx), mixed into
Cc65Backend.

The GB family banks through sdcc's `__banked` calling convention and one
16 KB MBC window. `[build] code_banks` means the same thing on the two cc65
consoles -- the listed modules' functions leave the resident image -- so every
analysis the GB path runs (`_plan_code_banks`, `_apply_code_banks`, the
streamed-pointer check, the per-module bank numbering) is reused unchanged.
Only the LOWERING differs, per console:

THE PC ENGINE: a ROM bank (`_rom_banking_capable`)
  * ONE 16 KB switchable window at $4000-$7FFF, mapped through MPR2 + MPR3
    (two consecutive physical 8 KB banks). cc65's PCE crt0 never sets those
    two (it maps MPR0 = I/O, MPR1 = RAM, MPR4..6 = banks 1..3 and leaves MPR7 =
    0 for the vectors), and no library routine uses TAM/TMA, so they are ours.
    The resident image is today's 32 KB HuCard at $8000-$FFFF, unchanged.
  * A LOGICAL bank k >= 1 is 16 KB -- the GB's bank, so the GB numbering, the
    GB_BANK_BYTES packing and the bank-neutrality wrapper all carry over -- and
    lives in physical banks 2k + 2 and 2k + 3 (the 32 KB resident image is
    physical 0..3). The build appends bank k right behind the rotated resident
    image, so file offset = physical bank * 8 KB.

THE ATARI LYNX: a cart OVERLAY (`_overlay_banking`)
  * The Lynx has no mapper and its cart is not CPU-addressable: a "bank" is an
    overlay, linked at ONE RAM window at the bottom of RAM ($0200, MAIN moves up
    above it) and read from the cart (`lseek` + `read`, ~13 ms a KB) when a call
    finds another one loaded. Data does not go there (it streams through the
    cart archive as before); an overlay holds CODE and its switch tables only.
    String literals stay resident (a pointer to one must survive a reload), and
    so do the `--static-locals` locals (BSS is never part of an overlay).
  * A per-frame function in an overlay would reload every frame, so the pins
    are `bank(0)` AND `hot` (FunctionDecl.hot): the measured per-frame set.

BOTH
  * Calls: cc65's own banked-call hook, `#pragma wrapped-call (push, tramp,
    N)`, around a banked function's PROTOTYPE makes every call to it by name
    load tmp4 = N, ptr4 = the function and `jsr tramp`. The trampoline keeps the
    caller's bank / overlay on the HARDWARE stack (re-entrant) and restores it
    on the way back. It is ASSEMBLY (cc65's inline assembler knows no TAM and
    rejects pseudo-ops; `asm()` is not allowed at file scope), handed to the
    build as an `.s` unit.
  * Placement: one translation unit, as always on cc65. A banked body is
    wrapped in `code-name` / `rodata-name` pragmas naming its bank's segment
    (`BK<k>`); the build generates the ld65 config with one memory area per
    bank (PCE) or overlay (Lynx).

Engaged only when the program asks for code banking (`[build] code_banks`):
a bare `bank(N)` in a portable source keeps the image it always had, byte for
byte, on both consoles.
"""

#: Consoles whose cc65 backend banks ROM (the analysis is the GB family's).
CC65_BANKING_CONSOLES = ('pce',)
#: Consoles whose cc65 backend loads `code_banks` modules as cart OVERLAYS.
CC65_OVERLAY_CONSOLES = ('lynx',)
#: Where the Lynx overlay window starts (the bottom of RAM; MAIN moves up).
LYNX_OVERLAY_START = 0x0200


class Cc65BankMixin:
    def _rom_banking_capable(self) -> bool:
        """Can THIS build place code in switchable banks? The GB family
        (PLATFORM_CAPS has_banking) always; the PC Engine (ROM banks) and the
        Lynx (cart overlays) only when the program opts in with `[build]
        code_banks` (see the module note)."""
        if self.caps['has_banking']:
            return True
        return (self.platform in CC65_BANKING_CONSOLES + CC65_OVERLAY_CONSOLES
                and bool(getattr(self, 'code_banks', None)))

    def _overlay_banking(self) -> bool:
        """A Lynx build whose code banks are cart overlays."""
        return (self.platform in CC65_OVERLAY_CONSOLES
                and bool(getattr(self, 'code_banks', None)))

    @property
    def _cc65_banking(self) -> bool:
        """True while emitting a cc65 program that actually banks."""
        return self.framework == 'cc65' and self.banking_active

    def _cc65_banked(self, func) -> bool:
        """A function whose body goes into a bank segment of the ONE cc65 TU."""
        return self._cc65_banking and func.bank > 0

    @staticmethod
    def _cc65_bank_seg(bank) -> str:
        return "BK%d" % bank

    def _cc65_used_banks(self):
        """The banks something actually lands in (a listed module whose every
        function is pinned keeps its number but places nothing)."""
        banks = set(self._bank_local_consts.values())
        banks |= set(self.data_bank_syms)
        banks |= {b for (b, _t, _n) in self.prelude_bank_defs.values()}
        banks |= set(self.asset_far_bank.values())
        banks |= set(getattr(self, '_cc65_func_banks_used', ()))
        return sorted(banks)

    def _cc65_max_bank(self) -> int:
        """The highest logical bank this program places anything in."""
        banks = set(self._func_banks)
        banks |= set(self.data_bank_syms)
        banks |= {b for (b, _t, _n) in self.prelude_bank_defs.values()}
        banks |= set(self.asset_far_bank.values())
        banks |= set(self._bank_local_consts.values())
        return max(banks) if banks else 0

    # -- placement pragmas ----------------------------------------------------

    def _emit_cc65_seg_open(self, bank, code=True):
        seg = self._cc65_bank_seg(bank)
        if code:
            self.emit('#pragma code-name (push, "%s")' % seg)
        self.emit('#pragma rodata-name (push, "%s")' % seg)
        if not self._overlay_banking():
            # PCE: literals in the function's own bank, as sdcc puts them on
            # the GB. NOT in a Lynx overlay: a literal handed to resident code
            # must survive the overlay being replaced.
            self.emit("#pragma local-strings (push, on)")

    def _emit_cc65_seg_close(self, code=True):
        if not self._overlay_banking():
            self.emit("#pragma local-strings (pop)")
        self.emit("#pragma rodata-name (pop)")
        if code:
            self.emit("#pragma code-name (pop)")

    def _cc65_thunked(self, func) -> bool:
        """A banked function reached through a resident THUNK: every banked
        function except a near-local, whose callers all share its bank and
        call its body directly (as on the GB)."""
        return self._cc65_banked(func) and id(func) not in self._near_bank_funcs

    def _emit_cc65_prototype(self, func):
        """A prototype. A thunked function keeps its plain prototype: the name
        is the resident thunk (assembly), so a call is an ordinary `jsr` and
        a function pointer to it is a resident address too. Its body is
        defined as `<name>__bk` (`_emit_function`) and registered here for
        the assembly unit."""
        self.emit(self._function_signature(func) + ";")
        if self._cc65_thunked(func):
            self._cc65_thunks.append(
                (self._mangled(self.current_module, func.name), func.bank))

    def _cc65_body_signature(self, func, signature):
        """The DEFINITION's signature: a thunked body is `<name>__bk`."""
        if not self._cc65_thunked(func):
            return signature
        name = self._mangled(self.current_module, func.name)
        return signature.replace(" %s(" % name, " %s__bk(" % name, 1)

    def _emit_cc65_bank_const(self, bank, text):
        """One banked const definition, placed in its bank's segment."""
        self._emit_cc65_seg_open(bank, code=False)
        self.emit(text)
        self._emit_cc65_seg_close(code=False)

    # -- prelude --------------------------------------------------------------

    def _emit_cc65_bank_prelude(self):
        """Right after the headers: the PCE's names for the GB lowering text
        (SWITCH_ROM / CURRENT_BANK / BANKED) over its helpers, or the Lynx's
        overlay loader."""
        if not self._cc65_banking:
            return
        if self._overlay_banking():
            self._emit_lynx_overlay_prelude()
            return
        self.emit("/* --- ROM banking (PC Engine): ONE 16 KB window at $4000-$7FFF")
        self.emit("   (MPR2 + MPR3). Logical bank k lives in physical banks 2k+2,")
        self.emit("   2k+3; the 32 KB resident image is physical 0..3. The trampoline")
        self.emit("   and the mapper are assembly (the build links the .s unit). --- */")
        self.emit("extern uint8_t gbs_bank;              /* logical bank in the window */")
        self.emit("void __fastcall__ gbs_bank_map(uint8_t b);")
        self.emit("#define SWITCH_ROM(b) gbs_bank_map(b)")
        self.emit("#define CURRENT_BANK gbs_bank")
        self.emit("#define BANKED")
        if self.bank_enter_used:
            self.emit("/* assets.bank_enter: map a data blob's ROM bank for a WINDOW of")
            self.emit("   direct reads (the VM8 code window), returning the bank to restore. */")
            self.emit("static uint8_t gbs_bank_enter(uint8_t b) { uint8_t o = gbs_bank; gbs_bank_map(b); return o; }")
        if self.asset_far_bank:
            self.emit("void gbs_spr_data_far(uint8_t first, uint8_t count, uint8_t bank, const uint8_t *data);")
        self.emit("")

    def _emit_lynx_overlay_prelude(self):
        """The Lynx overlay loader: one cart read of the overlay's bytes into
        the window. Its table (cart offset + length per overlay, from the
        linker) lives in the assembly unit."""
        self.emit("/* --- Code OVERLAYS (Lynx, [build] code_banks): each listed module's")
        self.emit("   cold functions are one overlay, linked at the RAM window at $%04X"
                  % LYNX_OVERLAY_START)
        self.emit("   and read from the cart when a call finds another one loaded")
        self.emit("   (each banked function's resident thunk, assembly). Per-frame")
        self.emit("   functions are `hot` and")
        self.emit("   stay resident. --- */")
        self.emit("#include <unistd.h>")
        self.emit("#include <stdio.h>")
        self.emit("extern uint8_t gbs_ovl;               /* the overlay in the window, 0 = none */")
        self.emit("extern const uint8_t gbs_ovl_tab[];   /* per overlay: cart offset (4), length (2) */")
        self.emit("/* Overlay loads so far: a steady frame should add none (a missing")
        self.emit("   `hot` shows up here as a load per frame). Read by verify probes. */")
        self.emit("uint16_t gbs_ovl_loads = 0;")
        self.emit("void __fastcall__ gbs_ovl_load(uint8_t id) {")
        self.emit("    const uint8_t *e = gbs_ovl_tab + (uint8_t)((uint8_t)(id - 1) * 6);")
        self.emit("    lseek(1, *(const long *)e, SEEK_SET);")
        self.emit("    read(1, (void *)0x%04X, *(const unsigned int *)(e + 4));"
                  % LYNX_OVERLAY_START)
        self.emit("    gbs_ovl = id;")
        self.emit("    ++gbs_ovl_loads;")
        self.emit("}")
        self.emit("")

    def _emit_cc65_bank_late(self):
        """At the end of the prelude (after the sprite engine): the far sheet
        upload. Resident and bank-neutral, because its caller is normally the
        generated room load, which may itself be banked."""
        if not (self._cc65_banking and self.asset_far_bank):
            return
        self.emit("/* sprite.set_data of a banked sheet: map the sheet's bank, upload,")
        self.emit("   restore the caller's (the caller may be banked itself). */")
        self.emit("void gbs_spr_data_far(uint8_t first, uint8_t count, uint8_t bank, const uint8_t *data) {")
        self.emit("    uint8_t save = gbs_bank;")
        self.emit("    gbs_bank_map(bank);")
        self.emit("    gbs_set_sprite_data(first, count, data);")
        self.emit("    gbs_bank_map(save);")
        self.emit("}")

    # -- banked data, defined in the one TU -----------------------------------

    def _emit_cc65_data_banks(self):
        """The cc65 counterpart of `_emit_data_bank_units`: streamed consts and
        prelude tables, each defined under its bank's rodata segment at the END
        of the TU (the main TU has already declared them `extern`)."""
        prelude = {}
        for sym, (bank, text, _size) in sorted(self.prelude_bank_defs.items()):
            prelude.setdefault(bank, []).append(text)
        banks = sorted(set(self.data_bank_syms) | set(prelude))
        if not banks:
            return
        self.emit("/* --- Banked data (streamed consts + sheets), one segment per bank --- */")
        for bank in banks:
            self._emit_cc65_seg_open(bank, code=False)
            for sym in self.data_bank_syms.get(bank, ()):
                decl = self.data_bank_decls[sym]
                self.emit("const " + self._format_var_decl(decl, sym) + ";")
            for text in prelude.get(bank, ()):
                self.emit(text)
            self._emit_cc65_seg_close(code=False)
        self.emit("")

    # -- the assembly unit ----------------------------------------------------

    def _cc65_bank_asm(self) -> str:
        if self._overlay_banking():
            return self._lynx_overlay_asm()
        return self._pce_bank_asm()

    def _cc65_thunk_lines(self):
        """Per thunked function: a 6-byte RESIDENT thunk under the function's
        own name -- `jsr gbs_thunk`, then its body (`<name>__bk`) and its bank
        inline. A caller makes an ordinary `jsr <name>` (no per-call-site
        setup: `#pragma wrapped-call` cost 12 bytes at every call site, ~2 KB
        on the showcase RPG), and the name is a resident address, so a
        function pointer to it stays valid in any bank."""
        seen, entries = set(), []
        for name, bank in self._cc65_thunks:
            if name not in seen:
                seen.add(name)
                entries.append((name, bank))
        out = []
        for name, _bank in entries:
            out.append("        .export _%s" % name)
            out.append("        .import _%s__bk" % name)
        out += ["", "        .segment \"CODE\""]
        for name, bank in entries:
            out += ["_%s:" % name,
                    "        jsr     gbs_thunk",
                    "        .word   _%s__bk" % name,
                    "        .byte   %d" % bank]
        return out

    #: The shared thunk entry: pop the thunk's return address (= thunk + 2,
    #: the last byte of its jsr) and read the target + bank through it. The
    #: CALLER's return address stays on the hardware stack beneath, so the
    #: final rts returns straight to the caller. Leaves the bank in A.
    _THUNK_ENTRY = [
        "gbs_thunk:",
        "        sta     tr_a            ; the callee's fastcall argument",
        "        stx     tr_x",
        "        pla",
        "        sta     ptr4",
        "        pla",
        "        sta     ptr4+1",
        "        ldy     #1",
        "        lda     (ptr4),y",
        "        sta     tr_go",
        "        iny",
        "        lda     (ptr4),y",
        "        sta     tr_go+1",
        "        iny",
        "        lda     (ptr4),y        ; the bank / overlay",
    ]

    def _pce_bank_asm(self) -> str:
        """The thunks + mapper (HuC6280 assembly). `gbs_bank` is the logical
        bank in the window (0 = none mapped yet; mapping 0 shows physical
        banks 2..3, which are resident code, harmless). The cells are only
        live between the thunk's entry and its jump, or between the callee's
        return and the thunk's own: nesting keeps the caller's bank on the
        hardware stack."""
        return "\n".join([
            "; Generated by mosaik -> cc65 backend: PC Engine ROM banking.",
            "; ONE 16 KB window at $4000-$7FFF = MPR2 + MPR3. Logical bank k is",
            "; physical banks 2k+2, 2k+3 (the resident 32 KB image is 0..3).",
            "        .setcpu \"HuC6280\"",
            "        .export _gbs_bank, _gbs_bank_map",
            "        .importzp ptr4",
        ] + self._cc65_thunk_lines() + [
            "",
            "        .segment \"BSS\"",
            "_gbs_bank:      .res 1",
            "tr_a:           .res 1",
            "tr_go:          .res 2",
            "",
            "        .segment \"CODE\"",
            "; void __fastcall__ gbs_bank_map(uint8_t b) -- A = logical bank.",
            "; Preserves X and Y.",
            "_gbs_bank_map:",
            "        sta     _gbs_bank",
            "        asl     a               ; b < 128, so C = 0 after the shift",
            "        adc     #$02",
            "        tam     #%00000100      ; MPR2 = 2k+2",
            "        inc     a",
            "        tam     #%00001000      ; MPR3 = 2k+3",
            "        rts",
            "",
            # X (a fastcall argument's / the result's high byte) rides
            # through untouched here: the mapper preserves it.
        ] + [ln for ln in self._THUNK_ENTRY if "tr_x" not in ln] + [
            "        tay                     ; the callee's bank",
            "        lda     _gbs_bank",
            "        pha                     ; the caller's bank",
            "        tya",
            "        jsr     _gbs_bank_map",
            "        lda     tr_a",
            "        jsr     @call",
            "        sta     tr_a            ; the return value's low byte",
            "        pla",
            "        jsr     _gbs_bank_map   ; X (the high byte) survives it",
            "        lda     tr_a",
            "        rts",
            "@call:  jmp     (tr_go)         ; tr_go is RAM, not zero page",
            "",
        ])

    def _lynx_overlay_asm(self) -> str:
        """The thunks + the overlay table. The loader is C (`gbs_ovl_load`,
        prelude) because it calls cc65's `lseek` / `read`; it clobbers every
        zero-page temporary, so the thunk keeps what it needs in its own
        cells: the target (read before the load), the callee's fastcall
        argument and, on the way back, the return value (A/X + sreg for a
        long). Nesting: the CALLER's overlay rides the hardware stack."""
        n = self._cc65_max_bank()
        used = set(self._cc65_used_banks())
        lines = [
            "; Generated by mosaik -> cc65 backend: Lynx code OVERLAYS.",
            "; Overlay k (segment BKk) runs at the RAM window at $%04X and is read"
            % LYNX_OVERLAY_START,
            "; from the cart on a miss; the caller's overlay is restored on return.",
            "        .export _gbs_ovl, _gbs_ovl_tab",
            "        .import _gbs_ovl_load",
            "        .importzp ptr4, sreg",
        ]
        for k in sorted(used):
            lines.append("        .import __OV%d_FILEOFFS__, __BK%d_SIZE__" % (k, k))
        lines += self._cc65_thunk_lines()
        lines += [
            "",
            "        .segment \"RODATA\"",
            "; per overlay: cart byte offset (the .lnx file offset less its 64-byte",
            "; header), then its length",
            "_gbs_ovl_tab:",
        ]
        for k in range(1, n + 1):
            if k in used:
                lines.append("        .dword  __OV%d_FILEOFFS__ - $40" % k)
                lines.append("        .word   __BK%d_SIZE__" % k)
            else:
                lines.append("        .dword  0               ; overlay %d: empty" % k)
                lines.append("        .word   0")
        lines += [
            "",
            "        .segment \"BSS\"",
            "_gbs_ovl:       .res 1          ; the overlay in the window, 0 = none",
            "tr_a:           .res 1",
            "tr_x:           .res 1",
            "tr_s:           .res 2",
            "tr_go:          .res 2",
            "",
            "        .segment \"CODE\"",
        ] + self._THUNK_ENTRY + [
            "        tay                     ; the callee's overlay",
            "        lda     _gbs_ovl",
            "        pha                     ; the caller's overlay",
            "        tya",
            "        cmp     _gbs_ovl",
            "        beq     @go",
            "        jsr     _gbs_ovl_load   ; A = the overlay (fastcall)",
            "@go:    lda     tr_a",
            "        ldx     tr_x",
            "        jsr     @call",
            "        sta     tr_a",
            "        stx     tr_x",
            "        lda     sreg",
            "        sta     tr_s",
            "        lda     sreg+1",
            "        sta     tr_s+1",
            "        pla                     ; the caller's overlay",
            "        beq     @done           ; resident caller: nothing to restore",
            "        cmp     _gbs_ovl",
            "        beq     @done",
            "        jsr     _gbs_ovl_load   ; the callee displaced it: back in",
            "@done:  lda     tr_s",
            "        sta     sreg",
            "        lda     tr_s+1",
            "        sta     sreg+1",
            "        lda     tr_a",
            "        ldx     tr_x",
            "        rts",
            "@call:  jmp     (tr_go)",
            "",
        ]
        return "\n".join(lines)

