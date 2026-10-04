"""mosaik_vm.uge - the hUGETracker `.uge` reader (the ENGINE's canonical copy).

A `.uge` is the FULL-FIDELITY form of a song: subpatterns, every effect and the
exact instrument registers hUGEDriver plays. `songs.toml` is our portable model
and is what gives the Lynx and SMS/GG music at all, but converting through it
loses whatever hUGE can express and we cannot - so a project that carries its
original `.uge` assets can have them rendered straight into hUGEDriver song data
(mosaik_vm.huge), bit-true, with the portable copy still driving every other
console.

This is a port of hUGETracker's own reader (`src/codegen.pas` +
`instruments.pas`), and it lives HERE rather than in the studio because a project
has to build with bare mosaik8. The studio's parity rig imports it, so
there is ONE parser rather than a second copy to drift.

**The v6 subpattern trap** (fixed 2026-08-11): an instrument's subpattern is a
FIXED 64-cell array present in the record whether or not `SubpatternEnabled` is
set. Reading those cells only when the flag is set misaligns every following
field - tempo, patterns, orders - and walks off the end of the file. Every GB
Studio 4.x module is v6.
"""

import struct

NOTE_NAMES = ["C_", "C#", "D_", "D#", "E_", "F_", "F#", "G_", "G#", "A_", "A#", "B_"]


def note_name(n):
    if n >= 72:
        return "___"
    return "%s%d" % (NOTE_NAMES[n % 12], n // 12 + 3)


class _R:
    def __init__(self, data):
        self.d = data
        self.o = 0

    def u32(self):
        v = struct.unpack_from("<I", self.d, self.o)[0]
        self.o += 4
        return v

    def u8(self):
        v = self.d[self.o]
        self.o += 1
        return v

    def sstr(self):
        self.o += 256
        return ""


class Uge:
    pass


#: hUGETracker's note -> GB frequency register (constants.pas `NotesToFreqs`),
#: used only by the v1..v3 noise-note upgrade.
_NOTE_FREQ = [
    44, 156, 262, 363, 457, 547, 631, 710, 786, 854, 923, 986,
    1046, 1102, 1155, 1205, 1253, 1297, 1339, 1379, 1417, 1452, 1486, 1517,
    1546, 1575, 1602, 1627, 1650, 1673, 1694, 1714, 1732, 1750, 1767, 1783,
    1798, 1812, 1825, 1837, 1849, 1860, 1871, 1881, 1890, 1899, 1907, 1915,
    1923, 1930, 1936, 1943, 1949, 1954, 1959, 1964, 1969, 1974, 1978, 1982,
    1985, 1988, 1992, 1995, 1998, 2001, 2004, 2006, 2009, 2011, 2013, 2015,
]
#: constants.pas `Ch4FreqToNoteCodeMap` at the 16 frequencies the v3 upgrade
#: can produce (524288 >> shift), indexed by shift.
_CH4_NOTE = [63, 62, 61, 59, 55, 51, 47, 43, 39, 35, 31, 27, 23, 19, 15, 11]
NO_NOTE = 90


def _instrument(r, layout):
    """One instrument record in hUGETracker's packed layouts (hugedatatypes.pas):
    "v1" TInstrumentV1 (304 B), "v2" TInstrumentV2 (+ the 6-byte noise macro),
    "v3" TInstrumentV3 (v6 files: no ShiftClockFreq / DividingRatio, + the
    subpattern flag and its FIXED 64 cells). Enums / Integers 4 B, Booleans and
    subranges 1 B."""
    d = {"type": r.u32()}
    r.sstr()
    d["length"] = r.u32(); d["len_en"] = r.u8(); d["iv"] = r.u8()
    d["vsd"] = r.u32(); d["vsc"] = r.u8()
    d["st"] = r.u32(); d["sdir"] = r.u32(); d["ssh"] = r.u32()
    d["duty"] = r.u8()
    d["vol"] = r.u32(); d["wave"] = r.u32()
    d["sub"] = None
    d["macro"] = None
    if layout == "v3":
        d["mode"] = r.u32()
        enabled = r.u8()
        # The subpattern is a FIXED 64-cell array in the record (TInstrumentV3),
        # present whether or not the flag is set -- read it unconditionally or
        # every following field misaligns (the 2026-08-11 trap).
        cells = []
        for _ in range(64):
            note = r.u32(); r.u32(); jump = r.u32(); eff = r.u32(); par = r.u8()
            cells.append((note, jump, eff, par))
        if enabled:
            d["sub"] = cells
    else:
        r.u32()                             # ShiftClockFreq (unused)
        d["mode"] = r.u32()                 # CounterStep
        r.u32()                             # DividingRatio
        if layout == "v2":
            d["macro"] = [struct.unpack_from("<b", r.d, r.o + k)[0] for k in range(6)]
            r.o += 6
    return d


def _macro_subpattern(macro, ticks):
    """A v4/v5 NOISE MACRO as the subpattern hUGETracker's own upgrade makes of
    it (song.pas `ConvertNoiseMacro`): rows 1..6 = the offsets + 36, and the row
    at min(ticks per row, 7) - 1 jumps to ITSELF (1-based `wrap`) to hold. None
    when every offset is 0 (the upgrade leaves the table disabled then)."""
    if not macro or not any(macro):
        return None
    cells = [(NO_NOTE, 0, 0, 0) for _ in range(64)]
    for j, off in enumerate(macro):
        cells[j + 1] = (off + 36, 0, 0, 0)
    wrap = min(ticks or 6, 7)
    note, _j, eff, par = cells[wrap - 1]
    cells[wrap - 1] = (note, wrap, eff, par)
    return cells


def parse(data):
    """Raw parse of a .uge (EVERY version, 1..6) with the full instrument fields
    the driver needs - a port of hUGETracker's `ReadSongFromStream` (song.pas,
    public domain) and of the upgrade steps that change what a field MEANS:

      v1  15 x TInstrumentV1 in ONE bank (placed by type at the same index into
          banks pre-filled with `UpgradeSong(TSongV2)`'s defaults), 33-byte
          waves, patterns keyed by POSITION
      v2  as v1 + the 16 routine strings
      v3  45 x TInstrumentV1, 32-byte waves; noise notes were raw frequencies
          and are re-coded (`UpgradeSong(TSongV3)`'s ConvertPattern)
      v4  45 x TInstrumentV2 (+ the noise macro, upgraded to a subpattern)
      v5  as v4, patterns carry their KEY
      v6  45 x TInstrumentV3 (+ subpattern), timer tempo, TCellV2 cells

    Until 2026-09-23 this read a pattern key and 45 v2 instruments for every
    version, so every v1..v4 module parsed to garbage or ran off its end
    (measured on hUGETracker's own sample songs: 6 of 23 raised)."""
    r = _R(data)
    m = Uge()
    m.version = r.u32()
    if not 1 <= m.version <= 6:
        raise ValueError("unsupported .uge version %d" % m.version)
    ver = m.version
    r.sstr(); r.sstr(); r.sstr()
    m.warnings = set()

    if ver <= 2:
        def blank(**kw):
            d = dict(type=0, length=0, len_en=0, iv=0, vsd=0, vsc=0, st=0,
                     sdir=0, ssh=0, duty=0, vol=0, wave=0, mode=0, sub=None,
                     macro=None)
            d.update(kw)
            return d
        banks = [[blank(type=0, iv=15, vsd=1, sdir=1, duty=2, vol=1) for _ in range(15)],
                 [blank(type=1, vol=1, wave=i) for i in range(15)],
                 [blank(type=2, iv=15, vsd=1) for _ in range(15)]]
        for i in range(15):
            d = _instrument(r, "v1")
            if 0 <= d["type"] <= 2:
                banks[d["type"]][i] = d
    else:
        layout = {3: "v1", 4: "v2", 5: "v2", 6: "v3"}[ver]
        banks = [[_instrument(r, layout) for _ in range(15)] for _ in range(3)]
    m.duty, m.wave, m.noise = banks

    m.wavetables = []
    for _ in range(16):
        m.wavetables.append([r.u8() for _ in range(32)])
        if ver < 3:
            r.u8()

    m.ticks = r.u32() or 6
    if ver >= 6:
        r.u8(); r.u32()
    for n in m.noise:                       # v4/v5: the macro becomes a subpattern
        if n["sub"] is None and n["macro"]:
            n["sub"] = _macro_subpattern(n["macro"], m.ticks)
    m.patterns = {}
    # Where each pattern's row 0 starts in the FILE, and how wide a cell is.
    # The parse is the only thing that knows (every field before the table is
    # variable-length), and a caller that wants to WRITE one cell - the
    # `6xy`/routine probe does - must not re-walk the header itself: this file
    # is the one .uge reader. `cell_offsets` turns the pair into the two byte
    # offsets a cell's effect and parameter live at.
    m.cell_bytes = 17 if ver >= 6 else 13
    m.pattern_at = {}
    for i in range(r.u32()):
        pid = r.u32() if ver >= 5 else i    # v1..v4 key a pattern by POSITION
        m.pattern_at[pid] = r.o
        rows = []
        for _ in range(64):
            note = r.u32(); inst = r.u32()
            if ver >= 6:
                r.u32()
            eff = r.u32(); par = r.u8()
            rows.append((note, inst, eff, par))
        m.patterns[pid] = rows
    m.orders = []
    for _ in range(4):
        ln = r.u32() - 1                    # the format's off-by-one
        m.orders.append([r.u32() for _ in range(max(0, ln))])
        r.u32()
    if ver <= 3:                            # v3 upgrade: noise notes were frequencies
        for pid in set(m.orders[3]):
            rows = m.patterns.get(pid)
            for k, (note, inst, eff, par) in enumerate(rows or []):
                if inst == 0 or note == NO_NOTE or not 0 <= note < len(_NOTE_FREQ):
                    continue
                shift = max(0, min(15, 15 - (_NOTE_FREQ[note] >> 7)))
                rows[k] = (_CH4_NOTE[shift], inst, eff, par)
    return m


# --------------------------------------------------------------------------
# GBDK C rendering, for the SHIPPED hUGEDriver ABI.
#
# NOTE the descriptor here is NOT the one this module's docstring quotes for the
# RGBDS rig: that targets hUGEDriver at HEAD (four tempo bytes + a 1-byte
# order_cnt), while the PREBUILT object we link wants one `tempo` byte and a
# POINTER-typed order_cnt. See mosaik8/vendor/hugedriver/README.md - getting it
# wrong produces a silent ROM with no diagnostic.
# --------------------------------------------------------------------------

def _duty_bytes(d):
    """NR10 / NR11 / NR12 / highmask, exactly as hUGETracker's instruments.pas
    computes them."""
    nr10 = ((d["st"] & 7) << 4) | ((d["sdir"] & 1) << 3) | (d["ssh"] & 7)
    nr11 = ((d["duty"] & 3) << 6) | (d["length"] & 0x3F)
    nr12 = ((d["iv"] & 0x0F) << 4) | (0x08 if d["vsd"] == 0 else 0) | (d["vsc"] & 7)
    high = 0x80 | (0x40 if d["len_en"] else 0)
    return nr10, nr11, nr12, high


#: hUGETracker's "call routine" effect. The descriptor's `routines` table is
#: the only thing that makes it safe, and we emit NULL there (both here and in
#: `huge.py`) - so the effect is STRIPPED, with a warning, rather than packed
#: into the cell.
#:
#: MEASURED 2026-09-17 (`tools/musicprobe/routine_null_probe.py`), because a
#: guessed answer here is a crash: the shipped driver's `do_effect` returns
#: early only when the effect code AND the parameter are both zero, so even
#: `600` dispatches; `fx_call_routine` has no NULL test at all; and the word
#: at a GBDK image's ROM 0x0000 is 0xFFFF. A patched the shooter conversion reached that
#: row and its stack pointer collapsed from 0xDF8B to 0x0017 - `jp 0xFFFF`
#: wraps into 0x0000, which is 0xFF, which is `RST 0x38`, which recurses until
#: the stack walks over WRAM. The plain ROM held 0xDF8B..0xFFFE.
#:
#: Two converted projects' modules already author one (both on the last row
#: of an ORDERED pattern), so this is not hypothetical. The studio's importer
#: drops the effect on its own path for the same reason.
FX_CALL_ROUTINE = 6


def _fx(m, eff, routines=False):
    """One cell's effect code, with the unsupported ones stripped.

    W7h made `6xy` REAL, but only where the program actually attaches a
    routine: `routines` is the build's `VM_OP_MUSIC_ROUTINE`, and with it False
    the descriptor's table is still NULL, so the effect must still be stripped
    or the driver jumps through NULL (see the comment above - it is a measured
    crash, not a theoretical one). The warning says which of the two it is,
    because "add a `music_routine` event" and "this effect is not supported"
    are completely different things for the author to hear."""
    if (eff & 0xF) == FX_CALL_ROUTINE:
        if routines:
            return eff
        m.warnings.add(
            "the `6xy` call-routine effect is stripped: no script is "
            "attached to a music routine anywhere in this project, so the "
            "song descriptor's `routines` table is NULL and the driver "
            "would jump through it without checking (measured: the ROM "
            "crashes). Add a `music_routine` event and the cell runs it. "
            "The cell keeps its note and instrument either way.")
        return 0
    return eff


def render_uge_c(m, sym, routines=False):
    """The GBDK C for one parsed `.uge`, as `const hUGESong_t <sym>`.

    Patterns are emitted by their own id and SHARED between channels exactly as
    the module stores them (a channel's order list is pattern pointers), so a
    song that reuses a pattern pays for it once - unlike the songs.toml path,
    which has no pattern structure left to reuse."""
    L = []
    order_len = min(len(o) for o in m.orders) if m.orders else 0
    used = sorted({p for o in m.orders for p in o[:order_len]})
    for pid in used:
        cells = m.patterns.get(pid) or [(90, 0, 0, 0)] * 64
        L.append("static const unsigned char %s_P%d[] = {" % (sym, pid))
        for i in range(0, len(cells), 4):
            row = ["DN(%d,%d,0x%X%02X)" % (n if n < 72 else 90, inst & 0x0F,
                                           _fx(m, eff, routines) & 0xF,
                                           par & 0xFF)
                   for n, inst, eff, par in cells[i:i + 4]]
            L.append("    " + ",".join(row) + ",")
        L.append("};")
    for c in range(4):
        refs = ", ".join("%s_P%d" % (sym, p) for p in m.orders[c][:order_len])
        L.append("static const unsigned char * const %s_order%d[] = {%s};"
                 % (sym, c + 1, refs))

    # INSTRUMENT SUBPATTERNS (hUGE v6 tables), rendered as hUGETracker's
    # `RenderGBDKSubpattern` does: only for an ENABLED table of an instrument the
    # song USES, 32 `DN(note, jump, effect)` rows, and row 31 with no jump of its
    # own gets jump 1 (back to row 0). Until 2026-09-23 every pointer was NULL,
    # so even the bit-true path played tabled instruments plain.
    used = [set(), set(), set()]            # duty (ch1+2) / wave (ch3) / noise (ch4)
    for c in range(4):
        for pid in m.orders[c][:order_len]:
            for _n, inst, _e, _p in (m.patterns.get(pid) or []):
                if inst:
                    used[(0, 0, 1, 2)[c]].add(inst & 0x0F)
    sp_name = {}
    for b, (bank, tag) in enumerate(((m.duty, "d"), (m.wave, "w"), (m.noise, "n"))):
        for i, ins in enumerate(bank):
            if not ins.get("sub") or (i + 1) not in used[b]:
                continue
            name = "%s_%sSP%d" % (sym, tag, i + 1)
            sp_name[(b, i)] = name
            L.append("static const unsigned char %s[] = {" % name)
            rows = []
            for k in range(32):
                note, jump, eff, par = ins["sub"][k]
                if k == 31 and jump == 0:
                    jump = 1
                rows.append("DN(%d,%d,0x%X%02X)" % (note if note < 72 else 90,
                                                    max(0, min(32, jump)),
                                                    _fx(m, eff, routines) & 0xF,
                                                    par & 0xFF))
            for k in range(0, 32, 4):
                L.append("    " + ",".join(rows[k:k + 4]) + ",")
            L.append("};")

    def _sp(b, i):
        return sp_name.get((b, i), "NULL")

    L.append("static const hUGEDutyInstr_t %s_duty[] = {" % sym)
    for i, d in enumerate(m.duty):
        nr10, nr11, nr12, high = _duty_bytes(d)
        L.append("    {0x%02X, 0x%02X, 0x%02X, %s, 0x%02X},"
                 % (nr10, nr11, nr12, _sp(0, i), high))
    L.append("};")
    L.append("static const hUGEWaveInstr_t %s_wave[] = {" % sym)
    for i, w in enumerate(m.wave):
        L.append("    {0x%02X, 0x%02X, 0x%02X, %s, 0x%02X},"
                 % (w["length"] & 0xFF, (w["vol"] & 3) << 5, w["wave"] & 0x0F,
                    _sp(1, i), 0x80 | (0x40 if w["len_en"] else 0)))
    L.append("};")
    L.append("static const hUGENoiseInstr_t %s_noise[] = {" % sym)
    for i, n in enumerate(m.noise):
        nr42 = ((n["iv"] & 0x0F) << 4) | (0x08 if n["vsd"] == 0 else 0) | (n["vsc"] & 7)
        high = ((n["length"] & 0x3F) | (0x40 if n["len_en"] else 0)
                | (0x80 if n["mode"] else 0))
        L.append("    {0x%02X, %s, 0x%02X, 0x00, 0x00}," % (nr42, _sp(2, i), high))
    L.append("};")
    L.append("static const unsigned char %s_waves[] = {" % sym)
    for wt in m.wavetables:
        packed = [((wt[j] & 0x0F) << 4) | (wt[j + 1] & 0x0F) for j in range(0, 32, 2)]
        L.append("    " + ",".join("0x%02X" % b for b in packed) + ",")
    L.append("};")

    # order_cnt is DEREFERENCED by the driver (the reference engine passes &order_cnt);
    # a literal-cast pointer makes it read ROM byte 0x0002 as the order
    # length and walk off the end of every order list. See render_song.
    L.append("static const unsigned char %s_ocnt = %d;" % (sym, order_len * 2))
    L.append("const hUGESong_t %s = {" % sym)
    L.append("    %d," % (m.ticks or 6))
    L.append("    &%s_ocnt," % sym)
    L.append("    (const unsigned char **)%s_order1, (const unsigned char **)%s_order2,"
             % (sym, sym))
    L.append("    (const unsigned char **)%s_order3, (const unsigned char **)%s_order4,"
             % (sym, sym))
    from .huge import _routines_field
    L.append("    %s_duty, %s_wave, %s_noise, %s, %s_waves,"
             % (sym, sym, sym, _routines_field(routines), sym))
    L.append("};")
    return "\n".join(L)


def cell_offsets(m, pid, row):
    """(effect offset, parameter offset) of one cell, in FILE bytes.

    Needs a module parsed by `parse` (for `pattern_at` / `cell_bytes`). The
    cell is note u32, instrument u32, [v6: a u32 the driver ignores], effect
    u32, parameter u8 - so the effect sits 8 or 12 bytes in and the parameter
    one u32 after it."""
    base = m.pattern_at[pid] + row * m.cell_bytes
    eff = base + (12 if m.cell_bytes == 17 else 8)
    return eff, eff + 4


def uge_to_c(path, sym, routines=False):
    """Parse a `.uge` file and render it. Returns (c_text, [warnings])."""
    with open(path, "rb") as f:
        m = parse(f.read())
    return render_uge_c(m, sym, routines=routines), sorted(m.warnings)
