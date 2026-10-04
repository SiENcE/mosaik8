"""mosaik_vm.instruments - Instrument-definition loader + src/instruments.mos emission."""
import os

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None

from .songs import _KIND_ID, _mos_u8_array


# --------------------------------------------------------------------------
# The INSTRUMENT LIBRARY (audio plan Tier 1): named per-chip TIMBRE presets the
# vm.music driver applies PER NOTE (the MOD `instrument` model -- our chips are
# SYNTHESIS not samples, so an "instrument" is a timbre + volume, not PCM). Index 0
# is the built-in default (the fixed timbre the driver used before Tier 1), so a song
# that references no instrument is behaviour-identical. Stage A = the GB pulse LEAD
# channel: an instrument is a DUTY (0..3 = 12.5/25/50/75 %) + a VOLUME (0..15).
# --------------------------------------------------------------------------
def load_instrument_defs(scripts_dir):
    """Load `instruments.toml` as an ORDERED list of (name, dict), or []. Order = the
    instrument INDEX (1..N; 0 = the per-kind built-in default). Each `[instrument.<name>]`
    declares a `kind` (pulse / wave / noise) + kind params + a `vol` (0..15):
      pulse: `duty` 0..3;  wave: `wave` 0..3 (waveform preset);  noise: `noisefreq` (NR43
      byte) + `env` (NR42 byte). Params for other kinds default harmlessly (Tier 1)."""
    if toml is None:
        return []
    path = os.path.join(scripts_dir, "instruments.toml")
    if not os.path.isfile(path):
        return []
    data = toml.load(path)
    out = []
    for name, d in (data.get("instrument", {}) or {}).items():
        if not isinstance(d, dict):
            continue
        kind = str(d.get("kind", d.get("type", "pulse")))
        if kind not in _KIND_ID:
            kind = "pulse"
        entry = {
            "kind": kind,
            "duty": int(d.get("duty", 2)) & 0x03,               # pulse 0..3
            "wave": int(d.get("wave", 0)) & 0x03,               # wave preset 0..3
            "noisefreq": int(d.get("noisefreq", 0x18)) & 0xFF,  # noise NR43
            "env": int(d.get("env", 0x71)) & 0xFF,              # noise NR42 envelope
            "decay": max(-7, min(7, int(d.get("decay", 0)))),   # pulse VOLUME ENVELOPE
            "vol": max(0, min(15, int(d.get("vol", 15)))),
        }
        # A CUSTOM wavetable (32 nibbles, 0..15) for the GB wave channel (audio plan Tier 1,
        # the hUGE-import fidelity win): overrides the 4 built-in presets so an imported hUGE
        # wave instrument keeps its exact timbre. Absent -> the `wave` preset is used.
        wt = d.get("wave_table")
        if isinstance(wt, list) and len(wt) >= 32:
            entry["wave_table"] = [int(x) & 0x0F for x in wt[:32]]
        sub = parse_subpattern(d.get("subpattern"))
        if sub:
            entry["subpattern"] = sub
            if d.get("subpattern_off"):
                entry["subpattern_off"] = True
        out.append((str(name), entry))
    return out


# --------------------------------------------------------------------------
# INSTRUMENT SUBPATTERNS (hUGETracker v6's per-instrument "table"): a small
# per-TICK macro that starts with every note the instrument triggers. Each row
# may move the pitch (a semitone offset from the note, LATCHED until another
# row moves it), set the volume, run an effect, and JUMP to another row; one row
# runs per driver tick. hUGEDriver's own rules, kept verbatim:
#
#   * 32 rows. Row 31 ALWAYS jumps to row 0 (the reference engine's `formatSubPatternCell`
#     forces its jump), so a table that never jumps loops every 32 ticks;
#   * a row with no pitch leaves the frequency alone; the offset is -36..+35.
#
# Authored in `instruments.toml` as SPARSE rows, only the non-empty ones:
#     [[instrument.lead.subpattern]]
#     row = 0
#     pitch = 12        # semitones from the note (absent = leave the pitch)
#     jump = 2          # next row (absent = continue with row + 1)
#     vol = 8           # 0..15 (absent = leave the volume)
#     fx = 14           # an FX_* effect id (song cells use the same ids)
#     param = 64
# `subpattern_off = true` keeps the rows but plays the instrument plain.
# --------------------------------------------------------------------------
SUBPATTERN_ROWS = 32
SUBPATTERN_PITCH = (-36, 35)


def parse_subpattern(rows):
    """The authored sparse row list -> a DENSE ``[{pitch, jump, vol, fx, param}]``
    (None = absent) trimmed after its last non-empty row, or [] for none."""
    if not isinstance(rows, list):
        return []
    dense = [{} for _ in range(SUBPATTERN_ROWS)]
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        try:
            at = int(r.get("row", i))
        except (TypeError, ValueError):
            continue
        if not 0 <= at < SUBPATTERN_ROWS:
            continue
        cell = {}
        if r.get("pitch") is not None:
            lo, hi = SUBPATTERN_PITCH
            cell["pitch"] = max(lo, min(hi, int(r["pitch"])))
        if r.get("jump") is not None:
            cell["jump"] = max(0, min(SUBPATTERN_ROWS - 1, int(r["jump"])))
        if r.get("vol") is not None:
            cell["vol"] = max(0, min(15, int(r["vol"])))
        fx = int(r.get("fx", 0) or 0) & 0xFF
        if fx:
            cell["fx"] = fx
            cell["param"] = int(r.get("param", 0) or 0) & 0xFF
        dense[at] = cell
    last = max((i for i, c in enumerate(dense) if c), default=-1)
    return dense[:last + 1]


def subpattern_bytes(row):
    """One dense subpattern row -> its 5 driver bytes (pitch, jump, vol, fx,
    param). 0 is "absent" for the first three, so each is stored OFF BY ONE:
    pitch = offset + 37 (1..72), jump = target + 1, vol = level + 1."""
    p = row.get("pitch")
    j = row.get("jump")
    v = row.get("vol")
    return [0 if p is None else int(p) + 37,
            0 if j is None else int(j) + 1,
            0 if v is None else int(v) + 1,
            int(row.get("fx", 0) or 0) & 0xFF,
            int(row.get("param", 0) or 0) & 0xFF]


def has_subpatterns(inst_defs):
    """Does any instrument PLAY a subpattern? Decides whether instruments.mos
    carries the tables at all and whether glue.mos wires them (and so whether
    the driver's table machinery is compiled - `VM_MUSIC_SUBPAT`)."""
    return any(d.get("subpattern") and not d.get("subpattern_off")
               for _n, d in inst_defs)


def instrument_names(scripts_dir):
    """The ordered instrument NAMES (the index space, 1-based; 0 = default)."""
    return [name for name, _d in load_instrument_defs(scripts_dir)]


def _inst_p0(d):
    """Register param 0 for an instrument: pulse -> NR11 duty byte, wave -> waveform
    preset index, noise -> NR43 frequency byte."""
    k = d["kind"]
    if k == "pulse":
        return (d["duty"] & 0x03) << 6
    if k == "wave":
        return d["wave"] & 0x03
    return d["noisefreq"] & 0xFF


def _pulse_env(decay):
    """A pulse instrument's `decay` (-7..7, 0 = sustain) -> the NR12 envelope LOW nibble
    (bit 3 = direction: 1 up / 0 down; bits 2-0 = period, 0 = OFF). The magnitude is SPEED
    (7 = fastest), so period = 8 - |decay| (|decay| 7 -> period 1 fastest, 1 -> 7 slowest);
    decay 0 -> 0 = no envelope (byte-identical). The driver ORs this into NR12."""
    decay = max(-7, min(7, int(decay or 0)))
    if decay == 0:
        return 0
    period = 8 - abs(decay)                                 # |decay| 1..7 -> period 7..1
    return (0x08 if decay > 0 else 0) | period


def _inst_p1(d):
    """Register param 1: noise -> NR42 envelope byte; pulse -> the NR12 envelope low
    nibble (from `decay`); wave -> 0."""
    k = d["kind"]
    if k == "noise":
        return d["env"] & 0xFF
    if k == "pulse":
        return _pulse_env(d.get("decay", 0))
    return 0


# The 4 built-in wave-channel PRESETS as packed GB wave RAM (16 bytes = 32 nibbles each),
# MIRRORING lib/vm/music.mos WAVEFORMS. Used to expand a NON-custom wave instrument's `wave`
# preset into the same 16-byte form as an imported custom wavetable, so `wavebyte` is uniform.
_WAVEFORMS = [
    [0x01, 0x23, 0x45, 0x67, 0x89, 0xAB, 0xCD, 0xEF,      # 0 triangle
     0xFE, 0xDC, 0xBA, 0x98, 0x76, 0x54, 0x32, 0x10],
    [0x00, 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77,      # 1 sawtooth
     0x88, 0x99, 0xAA, 0xBB, 0xCC, 0xDD, 0xEE, 0xFF],
    [0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,      # 2 square
     0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
    [0x89, 0xAB, 0xCC, 0xDD, 0xEE, 0xEF, 0xFF, 0xFE,      # 3 sine
     0xED, 0xDC, 0xCB, 0xA9, 0x87, 0x65, 0x43, 0x21],
]


def _inst_wave_ram(d):
    """The 16-byte GB wave RAM for an instrument: its CUSTOM 32-nibble `wave_table` packed
    two-nibbles-per-byte, else the selected built-in `wave` PRESET (triangle for non-wave
    kinds). Uniform 16 bytes so the `wavebyte` accessor works for every instrument."""
    wt = d.get("wave_table")
    if isinstance(wt, list) and len(wt) >= 32:
        return [((wt[2 * i] & 0x0F) << 4) | (wt[2 * i + 1] & 0x0F) for i in range(16)]
    if d["kind"] == "wave":
        return list(_WAVEFORMS[d["wave"] & 0x03])
    return list(_WAVEFORMS[0])


def emit_instruments_mos(inst_defs):
    """The `instruments.mos` module for the ordered ``inst_defs`` (name, dict) list: the
    driver's per-instrument KIND + register params + O(1) accessors. Instrument INDEX 0 is
    the per-kind default (handled by the driver), so 1..N map to array slot i-1; an
    out-of-range index falls back to safe defaults."""
    kind = [_KIND_ID[d["kind"]] for _n, d in inst_defs]
    p0 = [_inst_p0(d) for _n, d in inst_defs]           # duty byte / wave preset / NR43
    p1 = [_inst_p1(d) & 0xFF for _n, d in inst_defs]    # noise NR42 / pulse NR12-env / wave 0
    vol = [d["vol"] & 0x0F for _n, d in inst_defs]
    # WAVEDATA: 16 bytes of GB wave RAM per instrument, ROW 0 = the default (triangle) so
    # `wavebyte(0)` / an out-of-range index is safe; rows 1..N follow the instrument order.
    wavedata = list(_WAVEFORMS[0])
    for _n, d in inst_defs:
        wavedata += _inst_wave_ram(d)
    if not inst_defs:
        kind = p0 = p1 = vol = [0]
    cnt = len(inst_defs)
    L = []
    L.append("-- GENERATED by MosaiK8 Studio (mosaik_vm) -- edit scripts/instruments.toml, not this.")
    L.append("-- The INSTRUMENT LIBRARY (audio plan Tier 1): TYPED per-note timbre presets the")
    L.append("-- vm.music driver applies. INST_KIND 0 pulse / 1 wave / 2 noise; P0 = duty byte /")
    L.append("-- wave preset / NR43; P1 = noise NR42 env. Index 0 = per-kind default; 1..N -> i-1.")
    L.append("-- A shell wires it: music.set_instruments(kind, p0, p1, vol).")
    L.append("")
    L.append('module "instruments" {')
    L.append(_mos_u8_array("INST_KIND", kind))
    L.append(_mos_u8_array("INST_P0", p0))
    L.append(_mos_u8_array("INST_P1", p1))
    L.append(_mos_u8_array("INST_VOL", vol))
    L.append(_mos_u8_array("WAVEDATA", wavedata))    # 16 bytes wave RAM / instrument (row 0 = default)
    L.append("")
    for fn, arr, dflt in (("kind", "INST_KIND", 0), ("p0", "INST_P0", 0),
                          ("p1", "INST_P1", 0), ("vol", "INST_VOL", 15)):
        L.append("    function %s(i: u8) -> u8 {" % fn)
        L.append("        if i == 0 {")
        L.append("            return %d" % dflt)
        L.append("        }")
        L.append("        if i > %d {" % cnt)
        L.append("            return %d" % dflt)
        L.append("        }")
        L.append("        return %s[i - 1]" % arr)
        L.append("    }")
    # wavebyte(i, b): byte `b` (0..15) of instrument `i`'s GB wave RAM. i 0 or out-of-range
    # -> the default (triangle) row. The opt-in music.set_waves seam loads this per note.
    L.append("    function wavebyte(i: u8, b: u8) -> u8 {")
    L.append("        if i > %d {" % cnt)
    L.append("            i = 0")
    L.append("        }")
    L.append("        return WAVEDATA[i * 16 + b]")
    L.append("    }")
    exports = "kind, p0, p1, vol, wavebyte"
    if has_subpatterns(inst_defs):
        # SUBPATTERNS, emitted only when an instrument plays one, so every
        # other project's module is byte-identical. SUBLEN = the rows each
        # instrument's table stores (0 = none; rows past it read as empty);
        # SUBOFF = its first row in SUBDATA, 5 bytes a row.
        sublen, suboff, subdata = [], [], []
        for _n, d in inst_defs:
            rows = d.get("subpattern") or []
            if d.get("subpattern_off"):
                rows = []
            sublen.append(len(rows))
            suboff.append(len(subdata) // 5)
            for r in rows:
                subdata += subpattern_bytes(r)
        L.append("")
        L.append("    -- SUBPATTERNS (hUGE v6 instrument tables): 5 bytes a row - pitch")
        L.append("    -- (offset + 37, 0 = none), jump (target + 1, 0 = next), vol (+1, 0 =")
        L.append("    -- none), fx cmd, fx param. The driver runs one row per tick.")
        L.append(_mos_u8_array("SUBLEN", sublen))
        L.append("    const SUBOFF: array[u16, %d] = [%s]"
                 % (len(suboff), ", ".join(str(x) for x in suboff)))
        L.append(_mos_u8_array("SUBDATA", subdata or [0]))
        L.append("    function sublen(i: u8) -> u8 {")
        L.append("        if i == 0 {")
        L.append("            return 0")
        L.append("        }")
        L.append("        if i > %d {" % cnt)
        L.append("            return 0")
        L.append("        }")
        L.append("        return SUBLEN[i - 1]")
        L.append("    }")
        L.append("    -- field 0 pitch / 1 jump / 2 vol / 3 fx / 4 param of row `r`; only")
        L.append("    -- ever asked for r < sublen(i), so `i` is a real instrument here.")
        L.append("    function subrow(i: u8, r: u8, f: u8) -> u8 {")
        L.append("        return SUBDATA[(SUBOFF[i - 1] + r) * 5 + f]")
        L.append("    }")
        exports += ", sublen, subrow"
    L.append("")
    L.append("    export %s" % exports)
    L.append("}")
    L.append("")
    return "\n".join(L)


def generate_instruments(root, out_path=None):
    """Generate `src/instruments.mos` from `scripts/instruments.toml`, or None when the
    project authors no instruments (then no module + no cost, byte-identical)."""
    scripts_dir = os.path.join(root, "scripts")
    defs = load_instrument_defs(scripts_dir)
    if not defs:
        return None
    out_path = out_path or os.path.join(root, "src", "instruments.mos")
    # Generate BEFORE opening the file. `open(..., "w")` TRUNCATES, so an
    # exception raised while emitting (a song over CELL_CHUNK, say) used to
    # leave a ZERO-BYTE generated module behind - which does not fail safe:
    # the build then dies layers later with `imports unknown module ...`,
    # naming neither the real cause nor the limit that was hit.
    text = emit_instruments_mos(defs)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path
