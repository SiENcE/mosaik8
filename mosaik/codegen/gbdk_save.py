"""platform.save on the GB family: battery-backed cart SRAM.

A GbdkBackend concern mixin - methods run against the full
CodeGenerator instance (self.emit, self.caps, ...)."""


class GbdkSaveMixin:
    def _emit_gbdk_save(self):
        """platform.save helper bodies (GB family): battery-backed cart RAM at
        0xA000. The MBC5+RAM+BATTERY cart header is emitted separately via
        [build] ram_size (gbdk_size_flags); these map/unmap the RAM window and
        do byte access. Emitted only when the program imports platform.save."""
        self.emit("/* --- platform.save: battery-backed cart SRAM (0xA000, MBC5) --- */")
        self.emit("/* enable/disable map the RAM window (battery-safety hygiene: keep")
        self.emit("   it disabled except around a block of accesses). RAM bank 0.")
        self.emit("")
        self.emit("   STATIC INLINE, in the shared prelude, and not four functions in")
        self.emit("   the main TU: every one of them is a single instruction or two,")
        self.emit("   and out-of-line they sat RESIDENT - 28 B of bank 0 that only")
        self.emit("   `vm.sram` (which BANKS) ever calls. Inlined they cost bank 0")
        self.emit("   nothing and land in whichever bank uses them; a TU that does")
        self.emit("   not save emits none of them. Measured on the reference-engine sample conversion, whose")
        self.emit("   save point put it 32 B over its GBC ceiling. */")
        self.emit("static inline void gbs_save_enable(void) { ENABLE_RAM; SWITCH_RAM(0); }")
        self.emit("static inline void gbs_save_disable(void) { DISABLE_RAM; }")
        self.emit("static inline void gbs_save_write(uint16_t off, uint8_t v) {")
        self.emit("    ((volatile uint8_t *)0xA000)[off] = v;")
        self.emit("}")
        self.emit("static inline uint8_t gbs_save_read(uint16_t off) {")
        self.emit("    return ((volatile uint8_t *)0xA000)[off];")
        self.emit("}")
