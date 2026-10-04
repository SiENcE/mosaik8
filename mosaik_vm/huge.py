"""mosaik_vm.huge - `scripts/songs.toml` -> GBDK C song data for hUGEDriver.

The phase-2 half of `native.huge`.
`songs.toml` stays the PORTABLE source of truth - it is what gives the Lynx and
SMS/GG music at all through `vm.music` - and this renders the same library into
the form the real hUGEDriver plays on the GB family.

Two things decide the shape of the output:

* **The driver reads its song data in place, so a song must be inside the mapped
  ROM bank the whole time it plays.** A real library is tens of KB (the reference-engine
  conversion's is ~25 KB), so the songs go into switchable banks and only a small
  table stays resident: `gbs_huge_song_init` records the playing song's bank and
  `gbs_huge_update` (in the prelude) maps it around every `hUGE_dosound`.
* **Bank numbers are assigned by the BUILD, not by the author.** The codegen
  allocates banks for `[build] code_banks` and streamed data while it compiles,
  so nothing generated earlier can know what is free. `mosaik8_build` therefore
  calls this after compiling and before linking, passing `first_bank`.

The descriptor layout follows the SHIPPED hUGEDriver ABI (one `tempo` byte and a
pointer-typed `order_cnt`), which is NOT what the upstream repo's header says -
see mosaik8/vendor/hugedriver/README.md. Getting that wrong produces a silent
ROM with no diagnostic.
"""
import os

from .instruments import _inst_wave_ram, load_instrument_defs
from .songs import load_song_defs

# hUGE fixes 64 rows per pattern and 4 channels: pulse 1, pulse 2, wave, noise.
HUGE_PATTERN_ROWS = 64
HUGE_CHANNELS = 4
#: hUGE's "no note" sentinel (`___` in its own exports).
HUGE_REST = 90
#: hUGE notes run C_3 = 0 .. B_8 = 71; our tracker's note 0 is a REST and note 1
#: is C-3, so ours is theirs + 1 (the same relation the studio's .uge importer
#: documents in reverse).
HUGE_MAX_NOTE = 71
#: Effect nibbles we emit. hUGETracker's own lettering: Cxy set volume, Fxx set
#: speed (ticks per row).
FX_SET_VOL = 0xC
FX_SET_SPEED = 0xF
#: Each hUGE instrument bank holds 15 slots (1..15; 0 means "no instrument").
HUGE_INSTR_SLOTS = 15
#: Wave RAM: 16 waveforms of 16 bytes, indexed by a wave instrument's `waveform`.
HUGE_WAVES = 16
HUGE_WAVE_BYTES = 16

_KIND_CHANNELS = {"pulse": (0, 1), "wave": (2,), "noise": (3,)}


def _route_channels(channels):
    """Map our GENERIC channel list onto hUGE's four fixed channels.

    hUGE's channels are hardware: two pulses, one wave, one noise. Ours are a
    list of kinds, so the first pulse takes pulse 1, a second takes pulse 2, and
    wave/noise take theirs. Returns ({our_index: huge_index}, [warnings])."""
    route, used, warn = {}, set(), []
    for i, kind in enumerate(channels):
        slots = _KIND_CHANNELS.get(str(kind), ())
        for s in slots:
            if s not in used:
                used.add(s)
                route[i] = s
                break
        else:
            warn.append("channel %d (%s) has no free hUGE channel and was dropped "
                        "(hUGE is 2 pulse + 1 wave + 1 noise)" % (i, kind))
    return route, warn


def _used_instruments(entry, route):
    """The instrument indices a song actually plays, per kind, in first-use order.

    hUGE's 15-slot limit is PER SONG (each song carries its own instrument
    tables), while our `instruments.toml` is one library shared by the whole
    project - the reference-engine conversion's has 30+ pulse instruments across its ten
    songs. Mapping the global list would drop everything past 15 and silently
    play those cells on instrument 0; mapping only what each song uses keeps
    every song that is itself within hUGE's limit."""
    channels = entry.get("channels") or []
    used = {"pulse": [], "wave": [], "noise": []}
    for _frames, cells in (entry.get("rows") or []):
        for oi, cell in enumerate(cells):
            if oi not in route or oi >= len(channels):
                continue
            kind = str(channels[oi])
            if kind not in used:
                continue
            idx = int(cell[1])
            if idx > 0 and idx not in used[kind]:
                used[kind].append(idx)
    return used


def _instrument_maps(inst_defs, used, song_name=""):
    """Our GLOBAL instrument indices -> hUGE's PER-KIND 1..15 slots, for ONE song.

    Returns ({kind: {our_index: huge_slot}}, [warnings])."""
    maps = {"pulse": {}, "wave": {}, "noise": {}}
    warn = []
    for kind, idxs in used.items():
        for i in idxs:
            if not (1 <= i <= len(inst_defs)) or inst_defs[i - 1][1]["kind"] != kind:
                continue
            slot = len(maps[kind]) + 1
            if slot > HUGE_INSTR_SLOTS:
                warn.append("song '%s': instrument '%s' dropped - hUGE allows %d %s "
                            "instruments per song"
                            % (song_name, inst_defs[i - 1][0], HUGE_INSTR_SLOTS, kind))
                continue
            maps[kind][i] = slot
    return maps, warn


def _duty_instr(d):
    """Our pulse instrument -> hUGE's (sweep, len_duty, envelope, highmask).

    `_inst_p0` already packs the duty into NR11's top bits and `_inst_p1` the
    envelope's low nibble (direction + period), so the volume just goes in the
    high nibble of NR12 - the same bytes vm.music feeds the hardware."""
    from .instruments import _inst_p0, _inst_p1
    nr11 = _inst_p0(d) & 0xC0
    nr12 = ((d["vol"] & 0x0F) << 4) | (_inst_p1(d) & 0x0F)
    return 0x00, nr11, nr12, 0x80


def _wave_instr(d, wave_slot):
    """Our wave instrument -> hUGE's (length, volume, waveform, highmask).

    hUGE's wave volume is NR32's 2-bit level in bits 5-6, so our 0..15 scales to
    0..3 (0 = silent, 1 = 25%, 2 = 50%, 3 = 100%)."""
    vol = d["vol"] & 0x0F
    level = 0 if vol == 0 else (1 if vol < 6 else (2 if vol < 11 else 3))
    return 0x00, (level & 3) << 5, wave_slot & 0x0F, 0x80


def _noise_instr(d):
    """Our noise instrument -> hUGE's (envelope, highmask). `env` is already an
    NR42 byte in our model, so it passes straight through."""
    from .instruments import _inst_p1
    return _inst_p1(d) & 0xFF, 0x00


#: Our portable effect ids (the studio's `audio_caps.FX_*`, vm.music's cmd
#: numbers) -> hUGE's effect nibble, for the ones that are the same effect.
#: PAN, the two jumps and the routine call are converted in `_hfx`; SWEEP is an
#: INSTRUMENT property in hUGE and has no effect letter.
_FX_TO_HUGE = {1: 0x0, 2: 0x1, 3: 0x2, 7: 0x3, 4: 0x4, 12: 0x5, 13: 0x7,
               14: 0x9, 5: 0xA, 6: 0xE}


def _hfx(cmd, param, hch, chunk, routines, warn):
    """One of our (cmd, param) cells -> a hUGE 12-bit effect, or None when it
    has no hUGE form. `hch` is the hUGE channel (for 8xx's NR51 bits), `chunk`
    the 64-row pattern the cell sits in (a resolved flat-row JUMP becomes a
    pattern break or a position jump relative to it)."""
    cmd, param = int(cmd or 0), int(param or 0) & 0xFF
    if cmd == 0:
        return None
    if cmd in _FX_TO_HUGE:
        return (_FX_TO_HUGE[cmd] << 8) | param
    if cmd == 8:                            # PAN: our bit0 R / bit1 L -> this channel's NR51
        p = param or 3
        return (0x8 << 8) | (((p & 1) << hch) | (((p >> 1) & 1) << (hch + 4)))
    if cmd == 15:                           # CALL ROUTINE (6xy): only with a routines table
        if routines:
            return (0x6 << 8) | param
        warn.add("the CALL ROUTINE effect is dropped: no script is attached to a "
                 "music routine, so the descriptor's routines table is NULL")
        return None
    if cmd == 11:                           # JUMP ORDER: our order position = hUGE Bxx
        return (0xB << 8) | param
    if cmd == 10:                           # a RESOLVED flat-row jump
        tgt_chunk, tgt_row = divmod(param, HUGE_PATTERN_ROWS)
        if tgt_chunk == chunk + 1:
            return (0xD << 8) | tgt_row     # break into the next pattern at that row
        if tgt_row != 0:
            warn.add("a jump into the MIDDLE of a pattern that is not the next "
                     "one lands on that pattern's first row (hUGE has no such jump)")
        return (0xB << 8) | (tgt_chunk & 0xFF)
    if cmd == 9:
        warn.add("SWEEP is an instrument property in hUGE; the per-cell sweep "
                 "effect is dropped on the hUGEDriver path")
    return None


def _cell(note, inst, effect):
    """One `DN(note, instrument, effect)` macro call - hUGE's 3-byte cell."""
    return "DN(%d,%d,0x%03X)" % (note, inst, effect & 0xFFF)


def _song_rows(entry):
    """(rows, base_tempo): our per-row `frames` is a DURATION, hUGE has one
    `tempo` per song plus an Fxx effect to change it, so the first row sets the
    tempo and any change emits Fxx."""
    rows = entry.get("rows") or []
    tempo = rows[0][0] if rows else 6
    return rows, max(1, min(255, int(tempo)))


def render_song(name, entry, inst_defs, sym, routines=False):
    """The C text for ONE song (patterns, orders, instruments, waves, descriptor).

    Returns (text, warnings)."""
    L, warn = [], []
    channels = entry.get("channels") or []
    route, w = _route_channels(channels)
    warn += w
    imaps, w = _instrument_maps(inst_defs, _used_instruments(entry, route), name)
    warn += w
    rows, tempo = _song_rows(entry)
    if not rows:
        rows = [(tempo, [[0, 0, 0] for _ in channels])]
    fxrows = entry.get("fx") or []
    fxwarn = set()
    npat = max(1, (len(rows) + HUGE_PATTERN_ROWS - 1) // HUGE_PATTERN_ROWS)

    # ---- patterns: one 64-row blob per hUGE channel per chunk -------------
    cur_tempo = tempo
    tempo_at = {}                       # flat row -> the Fxx to emit on channel 0
    for ri, (frames, _cells) in enumerate(rows):
        f = max(1, min(255, int(frames)))
        if f != cur_tempo:
            tempo_at[ri] = f
            cur_tempo = f
    for p in range(npat):
        # All four channels of the chunk first: ONE effect per hUGE cell, so a
        # row's tempo change (Fxx) has to find a channel with no effect of its own.
        grid = [[None] * HUGE_PATTERN_ROWS for _ in range(HUGE_CHANNELS)]
        for hch in range(HUGE_CHANNELS):
            ours = next((oi for oi, hi in route.items() if hi == hch), None)
            for r in range(HUGE_PATTERN_ROWS):
                ri = p * HUGE_PATTERN_ROWS + r
                note, inst, eff = HUGE_REST, 0, 0
                if ri < len(rows) and ours is not None:
                    cells = rows[ri][1]
                    if ours < len(cells):
                        n, i, v = cells[ours]
                        if n > 0:
                            note = min(HUGE_MAX_NOTE, max(0, int(n) - 1))
                        kind = str(channels[ours])
                        inst = imaps.get(kind, {}).get(int(i), 0)
                        fr = fxrows[ri] if ri < len(fxrows) else []
                        cmd, par = fr[ours] if ours < len(fr) else (0, 0)
                        h = _hfx(cmd, par, hch, p, routines, fxwarn)
                        if h is not None:           # the EFFECT column wins the cell
                            eff = h
                            if v:
                                fxwarn.add("a cell with both a VOLUME and an effect keeps "
                                           "the effect (hUGE has one effect per cell)")
                        elif v:                     # per-cell volume -> Cxy
                            eff = (FX_SET_VOL << 8) | ((int(v) & 0x0F) << 4)
                grid[hch][r] = [note, inst, eff]
        for r in range(HUGE_PATTERN_ROWS):
            ri = p * HUGE_PATTERN_ROWS + r
            if ri in tempo_at:
                free = next((h for h in range(HUGE_CHANNELS) if grid[h][r][2] == 0), None)
                if free is None:
                    fxwarn.add("a tempo change collided with an effect on every "
                               "channel and replaces channel 1's")
                    free = 0
                grid[free][r][2] = (FX_SET_SPEED << 8) | tempo_at[ri]
        for hch in range(HUGE_CHANNELS):
            body = [_cell(*c) for c in grid[hch]]
            L.append("static const unsigned char %s_P%d_%d[] = {" % (sym, p, hch))
            for i in range(0, len(body), 4):
                L.append("    " + ",".join(body[i:i + 4]) + ",")
            L.append("};")
    for hch in range(HUGE_CHANNELS):
        refs = ", ".join("%s_P%d_%d" % (sym, p, hch) for p in range(npat))
        L.append("static const unsigned char * const %s_order%d[] = {%s};"
                 % (sym, hch + 1, refs))

    # ---- instruments: 15 slots per kind, hUGE reads them by index ---------
    # Ordered BY hUGE SLOT, not by our library order: the map assigns slots in
    # first-use order, so building the tables any other way would play every
    # cell on the wrong instrument.
    duty, wave, noise = [], [], []
    wave_ram = []
    for i in sorted(imaps["pulse"], key=lambda k: imaps["pulse"][k]):
        duty.append(_duty_instr(inst_defs[i - 1][1]))
    for i in sorted(imaps["wave"], key=lambda k: imaps["wave"][k]):
        d = inst_defs[i - 1][1]
        slot = len(wave_ram)
        if slot < HUGE_WAVES:
            wave_ram.append(_inst_wave_ram(d))
        wave.append(_wave_instr(d, min(slot, HUGE_WAVES - 1)))
    for i in sorted(imaps["noise"], key=lambda k: imaps["noise"][k]):
        noise.append(_noise_instr(inst_defs[i - 1][1]))
    while len(duty) < HUGE_INSTR_SLOTS:
        duty.append((0x00, 0x80, 0xF0, 0x80))
    while len(wave) < HUGE_INSTR_SLOTS:
        wave.append((0x00, 0x20, 0x00, 0x80))
    while len(noise) < HUGE_INSTR_SLOTS:
        noise.append((0xF0, 0x00))
    # SUBPATTERNS: an instrument that plays a table gets it rendered the way
    # hUGETracker's codegen renders one (32 DN rows, row 31 jumping to 0 unless
    # it jumps elsewhere); every other instrument keeps NULL, so a library with
    # no table renders exactly what it did.
    sub_of = {}
    for kind, tag, hch in (("pulse", "d", 0), ("wave", "w", 2), ("noise", "n", 3)):
        for i, slot in sorted(imaps[kind].items(), key=lambda kv: kv[1]):
            d = inst_defs[i - 1][1]
            srows = d.get("subpattern") or []
            if not srows or d.get("subpattern_off"):
                continue
            nm = "%s_%sSP%d" % (sym, tag, slot)
            sub_of[(kind, slot)] = nm
            cells = []
            for k in range(32):
                c = srows[k] if k < len(srows) else {}
                note = HUGE_REST if c.get("pitch") is None else int(c["pitch"]) + 36
                jump = 0 if c.get("jump") is None else int(c["jump"]) + 1
                if k == 31 and jump == 0:
                    jump = 1
                eff = _hfx(c.get("fx", 0), c.get("param", 0), hch, 0, routines, fxwarn)
                if eff is None and c.get("vol") is not None:
                    eff = (FX_SET_VOL << 8) | (int(c["vol"]) & 0x0F)
                cells.append(_cell(note, jump, eff or 0))
            L.append("static const unsigned char %s[] = {" % nm)
            for k in range(0, 32, 4):
                L.append("    " + ",".join(cells[k:k + 4]) + ",")
            L.append("};")

    def _sp(kind, slot):
        return sub_of.get((kind, slot), "NULL")

    L.append("static const hUGEDutyInstr_t %s_duty[] = {" % sym)
    for k, (sweep, nr11, nr12, high) in enumerate(duty[:HUGE_INSTR_SLOTS]):
        L.append("    {0x%02X, 0x%02X, 0x%02X, %s, 0x%02X},"
                 % (sweep, nr11, nr12, _sp("pulse", k + 1), high))
    L.append("};")
    L.append("static const hUGEWaveInstr_t %s_wave[] = {" % sym)
    for k, (ln, vol, wf, high) in enumerate(wave[:HUGE_INSTR_SLOTS]):
        L.append("    {0x%02X, 0x%02X, 0x%02X, %s, 0x%02X},"
                 % (ln, vol, wf, _sp("wave", k + 1), high))
    L.append("};")
    L.append("static const hUGENoiseInstr_t %s_noise[] = {" % sym)
    for k, (env, high) in enumerate(noise[:HUGE_INSTR_SLOTS]):
        L.append("    {0x%02X, %s, 0x%02X, 0x00, 0x00}," % (env, _sp("noise", k + 1), high))
    L.append("};")

    while len(wave_ram) < HUGE_WAVES:
        wave_ram.append([0] * HUGE_WAVE_BYTES)
    L.append("static const unsigned char %s_waves[] = {" % sym)
    for w16 in wave_ram[:HUGE_WAVES]:
        L.append("    " + ",".join("0x%02X" % b for b in w16) + ",")
    L.append("};")

    # ---- the descriptor: the SHIPPED ABI (see vendor/hugedriver/README) ---
    # order_cnt is a POINTER the driver DEREFERENCES for the order length in
    # bytes (the reference engine emits `static const unsigned char order_cnt = N;` and
    # passes &order_cnt). Casting the count itself to a pointer makes the
    # driver read ROM byte 0x0002 as the length: the song plays pattern 0
    # correctly, then "advances" through neighbouring ROM - every channel
    # slides onto its neighbour's pattern and CH4 lands on a NULL, heard as
    # pulse 1 going permanently dead ~8 s into every song.
    L.append("static const unsigned char %s_ocnt = %d;" % (sym, npat * 2))
    L.append("const hUGESong_t %s = {" % sym)
    L.append("    %d," % tempo)
    L.append("    &%s_ocnt," % sym)
    L.append("    (const unsigned char **)%s_order1, (const unsigned char **)%s_order2,"
             % (sym, sym))
    L.append("    (const unsigned char **)%s_order3, (const unsigned char **)%s_order4,"
             % (sym, sym))
    L.append("    %s_duty, %s_wave, %s_noise, %s, %s_waves,"
             % (sym, sym, sym, _routines_field(routines), sym))
    L.append("};")
    warn += ["song '%s': %s" % (name, w) for w in sorted(fxwarn)]
    return "\n".join(L), warn


#: W7h - the song descriptor's `routines` field. NULL unless the program
#: ATTACHES a music routine (`VM_OP_MUSIC_ROUTINE`, read off the build's own
#: dispatch defines), which is what keeps every existing hUGE project's song
#: data byte-identical - and what kept the pre-W7h engine SAFE, since
#: hUGEDriver's `fx_call_routine` has no NULL test and jumps through the word
#: it finds there (measured: the ROM crashes, `tools/musicprobe/
#: routine_null_probe.py`). The table itself lives in the RESIDENT prelude
#: (`gbs_huge_routines`, `_emit_gbdk_huge`): the driver dereferences it from
#: inside its timer ISR with the SONG's bank mapped, so it cannot be banked.
def _routines_field(routines):
    return "(const hUGERoutine_t **)gbs_huge_routines" if routines else "NULL"


def _sym(i):
    return "gbs_huge_song%d" % i


def render_huge_c(song_defs, inst_defs, first_bank=1, bank_bytes=16384,
                  uge_files=None, routines=False):
    """Render the whole library. Returns ({filename: text}, [song_banks], [warnings]).

    Songs are packed greedily into banks (a song is read in place, so it may
    never straddle one) and the resident file holds only the table plus
    `gbs_huge_song_init`. `uge_files` ({slug: path}) upgrades any song whose
    original module the project still carries to bit-true rendering."""
    warn = []
    bodies, sizes = [], []
    for i, (name, entry) in enumerate(song_defs):
        src = _match_uge(name, uge_files or {})
        if src:
            from . import uge as _uge
            try:
                text, w = _uge.uge_to_c(src, _sym(i), routines=routines)
                warn += ["song '%s': rendered from %s (bit-true)"
                         % (name, os.path.basename(src))] + \
                        ["song '%s': %s" % (name, x) for x in w]
                bodies.append((name, text))
                # A .uge's patterns are SHARED between channels, so its size is
                # the distinct pattern count, not rows x channels.
                um = _uge.parse(open(src, "rb").read())
                npat = len({p for o in um.orders for p in o})
                # + 96 B per instrument SUBPATTERN table (32 rows x 3), counted
                # for every enabled one: the renderer drops unused ones, so
                # this can only over-estimate, which is the safe side of packing.
                nsub = sum(1 for b in (um.duty, um.wave, um.noise) for x in b
                           if x.get("sub"))
                sizes.append(npat * HUGE_PATTERN_ROWS * 3 + 4 * 64 * 2
                             + 15 * 6 * 2 + 15 * 5 + nsub * 32 * 3
                             + HUGE_WAVES * HUGE_WAVE_BYTES + 23)
                continue
            except Exception as e:
                warn.append("song '%s': could not read %s (%s); falling back to "
                            "the portable songs.toml rendering"
                            % (name, os.path.basename(src), e))
        text, w = render_song(name, entry, inst_defs, _sym(i),
                              routines=routines)
        warn += w
        bodies.append((name, text))
        rows, _t = _song_rows(entry)
        npat = max(1, (len(rows) + HUGE_PATTERN_ROWS - 1) // HUGE_PATTERN_ROWS)
        # patterns + orders + instruments + waves + descriptor, near enough for packing
        sizes.append(npat * HUGE_CHANNELS * HUGE_PATTERN_ROWS * 3
                     + npat * HUGE_CHANNELS * 2 + 15 * 6 * 2 + 15 * 5
                     + HUGE_WAVES * HUGE_WAVE_BYTES + 23)

    banks, cur, used = [], first_bank, 0
    for i, size in enumerate(sizes):
        if size > bank_bytes:
            warn.append("song '%s' is ~%d bytes and cannot fit one %d-byte ROM bank; "
                        "shorten it (fewer patterns/rows)"
                        % (song_defs[i][0], size, bank_bytes))
        if used and used + size > bank_bytes:
            cur += 1
            used = 0
        banks.append(cur)
        used += size

    files = {}
    for bank in sorted(set(banks)):
        L = ["/* GENERATED by MosaiK8 (mosaik_vm.huge) - hUGEDriver song data.",
             "   Bank %d. Assigned by the BUILD, after the codegen has allocated its"
             % bank,
             "   own banks - do not renumber by hand. */",
             "#pragma bank %d" % bank,
             '#include "hUGEDriver.h"',
             "#include <stddef.h>",
             ""]
        if routines:
            # The table is resident (see _routines_field); a banked song
            # descriptor only needs the view.
            L.append("extern const hUGERoutine_t gbs_huge_routines[];")
            L.append("")
        for i, b in enumerate(banks):
            if b == bank:
                L.append("/* %s */" % bodies[i][0])
                L.append(bodies[i][1])
                L.append("")
        files["songs_huge_b%d.c" % bank] = "\n".join(L)

    R = ["/* GENERATED by MosaiK8 (mosaik_vm.huge) - the hUGEDriver song TABLE.",
         "   RESIDENT: it is what maps a song index to its ROM bank, so it cannot",
         "   itself live in one. The song DATA is banked (see songs_huge_b*.c).",
         "   `gbs_huge_song_init` is the seam the prelude's gbs_huge_play calls. */",
         '#include "hUGEDriver.h"',
         "#include <gb/gb.h>",
         ""]
    for i, _b in enumerate(banks):
        R.append("extern const hUGESong_t %s;" % _sym(i))
    R.append("")
    R.append("static const hUGESong_t * const gbs_huge_songs[] = {%s};"
             % ", ".join("&" + _sym(i) for i in range(len(banks))))
    R.append("static const unsigned char gbs_huge_song_bank[] = {%s};"
             % ", ".join(str(b) for b in banks))
    R.append("#define GBS_HUGE_SONGS %d" % len(banks))
    R.append("")
    R.append("/* The playing song's bank, read by gbs_huge_update in the prelude: the")
    R.append("   driver reads patterns in place on every tick, so the bank has to be")
    R.append("   mapped around each hUGE_dosound as well as around hUGE_init. */")
    R.append("uint8_t gbs_huge_bank;")
    R.append("")
    R.append("void gbs_huge_song_init(uint8_t song) {")
    R.append("    uint8_t saved;")
    R.append("    if (song >= GBS_HUGE_SONGS) return;")
    R.append("    saved = CURRENT_BANK;")
    R.append("    gbs_huge_bank = gbs_huge_song_bank[song];")
    R.append("    SWITCH_ROM(gbs_huge_bank);")
    R.append("    __critical { hUGE_init(gbs_huge_songs[song]); }")
    R.append("    SWITCH_ROM(saved);")
    R.append("}")
    files["songs_huge.c"] = "\n".join(R) + "\n"
    return files, banks, warn


#: Where a project may keep its ORIGINAL `.uge` modules. `assets/music` is GB
#: Studio's own layout, which is what an imported project already looks like.
UGE_DIRS = ("assets/music", "assets/songs", "scripts")


def _slug(name):
    """Fold a name for matching a `.uge` FILENAME against a songs.toml song name.

    They come from the same place but through different sanitisers -- a file
    `Composer_Pause_Level2.uge` is imported as the song `Level2` -- so the
    match is on a lowercase alphanumeric fold, with a suffix match as the
    fallback."""
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def find_uge_files(root):
    """{slug: path} for every `.uge` the project carries."""
    out = {}
    for d in UGE_DIRS:
        full = os.path.join(root, *d.split("/"))
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if fn.lower().endswith(".uge"):
                out.setdefault(_slug(os.path.splitext(fn)[0]), os.path.join(full, fn))
    return out


def _match_uge(name, files):
    """The `.uge` for a song name, or None. Exact fold first, then a suffix match
    (`Composer_Pause_Level2` -> `Level2`)."""
    s = _slug(name)
    if s in files:
        return files[s]
    cands = [p for k, p in files.items() if k.endswith(s) or s.endswith(k)]
    return cands[0] if len(cands) == 1 else None


def generate_huge_songs(root, out_dir, first_bank=1, routines=False):
    """Render `<root>/scripts/songs.toml` into `out_dir`. Returns
    (paths, warnings, banks); ([], [], []) when the project has no songs.

    `banks` is every ROM bank the rendered song data occupies. The CALLER has
    to size the cart over it: these banks are allocated above everything the
    codegen placed, so a cart sized off the code banks alone can be too small
    for them, and the only symptom is makebin's generic "ROM is too large for
    number of banks specified" - whose build hints then blame a big resident
    song, which is the wrong cause entirely.

    A song whose ORIGINAL `.uge` the project still carries is rendered from THAT
    instead - bit-true playback, with subpatterns and every effect, which is the
    whole reason to run hUGEDriver. Matching is by NAME, so the song INDEX space
    (what `music_song` compiles to) is identical either way and a project can
    upgrade one song at a time."""
    scripts = os.path.join(root, "scripts")
    song_defs = load_song_defs(scripts)
    if not song_defs:
        return [], [], []
    inst_defs = load_instrument_defs(scripts)
    uge_files = find_uge_files(root)
    files, banks, warn = render_huge_c(song_defs, inst_defs, first_bank=first_bank,
                                       uge_files=uge_files, routines=routines)
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for name, text in sorted(files.items()):
        p = os.path.join(out_dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        paths.append(p)
    return paths, warn, sorted(set(banks))
