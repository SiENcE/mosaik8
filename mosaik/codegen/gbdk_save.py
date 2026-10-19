"""platform.save on the GBDK consoles: battery-backed cart SRAM.

The GB family's MBC5 RAM at 0xA000, and the Master System / Game Gear's
Sega-mapper cart RAM, which RAM_CONTROL (0xFFFC) bit 3 maps into slot 2
(0x8000-0xBFFF) in place of a ROM bank.

A GbdkBackend concern mixin - methods run against the full
CodeGenerator instance (self.emit, self.caps, ...)."""


class GbdkSaveMixin:
    def _emit_gbdk_save(self):
        """platform.save helper bodies. The MBC5+RAM+BATTERY cart header of
        the GB family is emitted separately via [build] ram_size
        (gbdk_size_flags); the Sega mapper has no header byte for cart RAM.
        These map/unmap the RAM window and do byte access. Emitted only when
        the program imports platform.save."""
        if self.platform in ('sms', 'gamegear'):
            self._emit_gbdk_save_sega()
            return
        self.emit("/* --- platform.save: battery-backed cart SRAM (0xA000, MBC5) --- */")
        self.emit("/* enable/disable map the RAM window (battery-safety hygiene: keep")
        self.emit("   it disabled except around a block of accesses). RAM bank 0.")
        self.emit("")
        self.emit("   STATIC INLINE, in the shared prelude, and not four functions in")
        self.emit("   the main TU: every one of them is a single instruction or two,")
        self.emit("   and out-of-line they sat RESIDENT - 28 B of bank 0 that only")
        self.emit("   `vm.sram` (which BANKS) ever calls. Inlined they land in whichever")
        self.emit("   bank uses them. Measured on the reference-engine sample conversion, whose")
        self.emit("   save point put it 32 B over its GBC ceiling. `static inline` alone")
        self.emit("   does not keep them out of a TU that never calls them (sdcc's z80")
        self.emit("   port emitted all four as dead code), so they are emitted only")
        self.emit("   into a TU whose own bodies call save.* (the per-TU gate). */")
        self.emit("static inline void gbs_save_enable(void) { ENABLE_RAM; SWITCH_RAM(0); }")
        self.emit("static inline void gbs_save_disable(void) { DISABLE_RAM; }")
        self.emit("static inline void gbs_save_write(uint16_t off, uint8_t v) {")
        self.emit("    ((volatile uint8_t *)0xA000)[off] = v;")
        self.emit("}")
        self.emit("static inline uint8_t gbs_save_read(uint16_t off) {")
        self.emit("    return ((volatile uint8_t *)0xA000)[off];")
        self.emit("}")

    def _emit_gbdk_save_sega(self):
        """The SMS / Game Gear twin: the Sega mapper's cart RAM page 0, mapped
        into slot 2 while enabled. Safe because nothing of ours lives in slot
        2: every banked area links at 0x4000 (slot 1, MAP_FRAME1) and no ISR
        reads 0x8000-0xBFFF, so the window may stay open across an interrupt.
        Static inline for the same bank-0 reason as the GB family's."""
        self.emit("/* --- platform.save: Sega-mapper cart RAM (slot 2, 0x8000) --- */")
        self.emit("/* RAM_CONTROL bit 3 maps cart RAM page 0 over slot 2, bit 2 picks")
        self.emit("   the page. Static inline: see the GB family's helpers. */")
        self.emit("static inline void gbs_save_enable(void) { SWITCH_RAM(0); ENABLE_RAM; }")
        self.emit("static inline void gbs_save_disable(void) { DISABLE_RAM; }")
        self.emit("static inline void gbs_save_write(uint16_t off, uint8_t v) {")
        self.emit("    ((volatile uint8_t *)0x8000)[off] = v;")
        self.emit("}")
        self.emit("static inline uint8_t gbs_save_read(uint16_t off) {")
        self.emit("    return ((volatile uint8_t *)0x8000)[off];")
        self.emit("}")
