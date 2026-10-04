"""mosaik_vm.songs - Tracker song loader + src/songs.mos emission (music as DATA)."""
import os

from .isa import VmError

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None


# --------------------------------------------------------------------------
# A song is now a MODULE-TRACKER model (audio plan Tier 1): GENERIC channels, each a
# KIND (pulse / wave / noise), and rows of per-channel CELLS [note, instrument, volume]
# + a frame duration. The driver ROUTES the channels onto each console's physical
# channels (see lib/vm/music.mos). `channels` defaults to the classic pulse/wave/noise
# (the old lead/bass/drum). CELL_FIELDS = note(0) / instrument(1) / volume(2).
_KIND_ID = {"pulse": 0, "wave": 1, "noise": 2}
_KIND_NAME = ["pulse", "wave", "noise"]
DEFAULT_CHANNELS = ["pulse", "wave", "noise"]

# The song CELL blob is CHUNKED at one ROM BANK. A banked const array is read in
# place while its single bank is mapped, so no symbol may ever cross a bank - the
# same ceiling the scene transpiler's concatenated per-scene tileset table hit
# (`_TS_CHUNK`). Before this the WHOLE library had to fit 16 KB, which is why the
# reference-engine sample import kept 3 of its 10 songs and booted to a silent title
# screen. 16 KB is the GB/SMS bank size, the tightest per-symbol ceiling of any
# target, so one constant covers them all.
CELL_CHUNK = 16384


def _norm_cell_row(row, g):
    """A flat song row [frames, note,inst,vol, note,inst,vol, ...] -> (frames, cells)
    where cells = g lists of [note, inst, vol]; missing trailing values default to 0."""
    row = [int(x) for x in (list(row) or [24])]
    frames = row[0] if row and row[0] > 0 else 24
    cells = []
    for c in range(g):
        b = 1 + c * 3
        cells.append([row[b] if b < len(row) else 0,
                      row[b + 1] if b + 1 < len(row) else 0,
                      row[b + 2] if b + 2 < len(row) else 0])
    return frames, cells


def _norm_fx_row(row, g):
    """A flat FX row [cmd,param, cmd,param, ...] -> g lists of [cmd, param] (0-padded).
    The per-cell EFFECT column (audio plan Tier 1, Stage 4), stored PARALLEL to `rows`."""
    row = [int(x) for x in (list(row) or [])]
    out = []
    for c in range(g):
        b = c * 2
        out.append([row[b] if b < len(row) else 0, row[b + 1] if b + 1 < len(row) else 0])
    return out


def load_song_defs(scripts_dir):
    """Load `songs.toml` as an ORDERED list of (name, {channels, rows}), or []. A song
    declares generic CHANNELS (kinds pulse/wave/noise, up to 6) + rows of per-channel
    CELLS [note, instrument, volume] with a frame duration -- authored flat (`rows =
    [[frames, n,i,v, n,i,v, ...], ...]`) OR as `patterns` + an `order` (flattened here).
    `channels` defaults to the classic pulse/wave/noise (lead/bass/drum). Order of songs
    = the `music_song` index."""
    if toml is None:
        return []
    path = os.path.join(scripts_dir, "songs.toml")
    if not os.path.isfile(path):
        return []
    data = toml.load(path)
    out = []
    for name, d in (data.get("song", {}) or {}).items():
        if not isinstance(d, dict):
            out.append((str(name), {"channels": list(DEFAULT_CHANNELS), "rows": []}))
            continue
        channels = [str(k) for k in (d.get("channels") or DEFAULT_CHANNELS)]
        g = max(1, len(channels))
        if d.get("patterns"):
            patterns = [[_norm_cell_row(r, g) for r in (pat or [])] for pat in d["patterns"]]
            fxsrc = d.get("fxpatterns") or []
            fxpats = [[_norm_fx_row((fxsrc[i] if i < len(fxsrc) else [])[ri]
                                    if ri < len(fxsrc[i] if i < len(fxsrc) else []) else [], g)
                       for ri in range(len(pat))] for i, pat in enumerate(patterns)]
            order = []
            for idx in (d.get("order") or list(range(len(patterns)))):
                try:
                    idx = int(idx)
                except (TypeError, ValueError):
                    continue
                if 0 <= idx < len(patterns):
                    order.append(idx)
            # A per-cell PATTERN BREAK (FX_GOTO = effect 10) is stored PATTERN-RELATIVE (param =
            # the row in the NEXT pattern, hUGE's Dxx param); resolve it here to the flat-row
            # target = (next order position's flat start) + that row, since the driver plays a
            # flat row list. FX_GOTO_ORD (11, position jump) keeps its ORDER param (the driver
            # scales it by the pattern length). Byte-identical for a song with no pattern break.
            starts, roff = [], 0
            for idx in order:
                starts.append(roff)
                roff += len(patterns[idx])
            total_rows = max(1, roff)
            rows, fx = [], []
            for pos, idx in enumerate(order):
                nxt = starts[pos + 1] if pos + 1 < len(starts) else 0
                rows.extend(patterns[idx])
                for frow in fxpats[idx]:
                    frow = [list(pair) for pair in frow]
                    for pair in frow:
                        if pair and int(pair[0]) == 10:      # FX_GOTO pattern break -> flat target
                            tgt = (nxt + int(pair[1])) % total_rows
                            if tgt <= 255:
                                pair[0], pair[1] = 10, tgt
                            else:                            # too far for the u8 goto param
                                pair[0], pair[1] = 11, (pos + 1) % len(order)   # -> next order start
                    fx.append(frow)
        else:
            rows = [_norm_cell_row(r, g) for r in d.get("rows", [])]
            fxsrc = d.get("fx") or []
            fx = [_norm_fx_row(fxsrc[ri] if ri < len(fxsrc) else [], g) for ri in range(len(rows))]
        entry = {"channels": channels, "rows": rows}
        if any(v for frow in fx for pair in frow for v in pair):   # only when an effect is set
            entry["fx"] = fx
        # Per-song VOICING (audio plan Tier 1): how the driver routes channels onto each
        # console's limited voices. bit0 = GB uses pulse 2 for MUSIC (all 4 voices, faithful
        # playback) instead of reserving it for SFX; bit1 = the interchangeable chips
        # (Lynx / SMS-GG) PREFER the wave/bass channel over a 2nd pulse when pooling melodic
        # voices. 0 (default) = the game-friendly behaviour (byte-identical).
        flags = 0
        if d.get("gb_all_voices"):
            flags |= 1
        if d.get("bass_priority"):
            flags |= 2
        entry["flags"] = flags
        out.append((str(name), entry))
    return out


def song_names(scripts_dir):
    """The ordered song NAMES from `songs.toml` (the `music_song` index space)."""
    return [name for name, _d in load_song_defs(scripts_dir)]


def song_expand_events(events, names):
    """Resolve every `music_song` whose `song` is a NAME to its index (recursing
    into if/switch bodies). A missing name -> 0. An int `song` is left as-is."""
    out = []
    for ev in (events or []):
        if not isinstance(ev, dict):
            out.append(ev)
            continue
        ev = dict(ev)
        if ev.get("event") == "music_song" and isinstance(ev.get("song"), str):
            nm = ev["song"].strip()
            ev["song"] = names.index(nm) if nm in names else 0
        for key in ("then", "else", "default"):
            if isinstance(ev.get(key), list):
                ev[key] = song_expand_events(ev[key], names)
        if isinstance(ev.get("cases"), list):
            ev["cases"] = [dict(c, then=song_expand_events(c.get("then", []), names))
                           if isinstance(c, dict) else c for c in ev["cases"]]
        out.append(ev)
    return out


def song_expand_scripts(scripts, names):
    """Apply :func:`song_expand_events` to every script (a copy)."""
    if not names:
        return scripts
    return [dict(s, events=song_expand_events(s.get("events", []), names))
            for s in (scripts or [])]


def _mos_u8_array(name, vals):
    body = ", ".join(str(int(v) & 0xFF) for v in vals) or "0"
    n = max(1, len(vals))
    if not vals:
        vals = [0]
    return "    const %s: array[u8, %d] = [%s]" % (name, n, body)


def emit_songs_mos(song_defs):
    """The `songs.mos` module text for the ordered ``song_defs`` (name, {channels, rows})
    list: the driver's DATA accessors over ONE interleaved cell blob (CELLS, 5 bytes per
    cell: note / inst / vol / fx cmd / fx param, cells laid out row-major
    (row * channels + channel)) + per-song offset/length indexes. CELLS is read through
    ``assets.code_byte`` -- byte-identical indexing on the directly-mapped consoles, and
    the Lynx page-cache STREAMS a blob past the 1 KB threshold from the cart (so a big
    imported hUGE song leaves the resident MAIN area). Accessors (the shell wires
    `music.set_song(channels, chkind, cell, frames, rows)`):
      channels(song) / chkind(song, ch) / cell(song, row, ch, field) / frames(song, row)
      / rows(song)   -- field 0 note / 1 instrument / 2 volume / 3 fx cmd / 4 fx param."""
    chkind, choff, chn = [], [], []
    chunks = [[]]            # interleaved cell blobs, per ROW: [frames, (note, inst,
    celloff, length = [], []  # vol, fxc, fxp) x channels] -- stride 1 + channels * 5
    cellblk = []             # which CHUNK each song's block lives in
    ch_cur = 0
    for name, d in song_defs:
        channels = d.get("channels") or DEFAULT_CHANNELS
        rows = d.get("rows") or []
        fxrows = d.get("fx") or []
        g = max(1, len(channels))
        choff.append(ch_cur); chn.append(g)
        for k in channels:
            chkind.append(_KIND_ID.get(str(k), 0))
        ch_cur += g
        # This song's block, built whole so it can be placed in ONE chunk.
        block = []
        for ri, (frames, cells) in enumerate(rows):
            block.append(frames)
            fxr = fxrows[ri] if ri < len(fxrows) else []
            for c in range(g):
                cell = cells[c] if c < len(cells) else [0, 0, 0]
                fpair = fxr[c] if c < len(fxr) else [0, 0]
                block.extend([cell[0], cell[1], cell[2], fpair[0], fpair[1]])
        if len(block) > CELL_CHUNK:
            raise VmError(
                "song %r is %d B of cell data; one song must fit a single %d B "
                "chunk (a const array is read in place while its ONE ROM bank is "
                "mapped, so it can never cross a bank). Shorten it - fewer "
                "patterns, rows or channels."
                % (name, len(block), CELL_CHUNK))
        if chunks[-1] and len(chunks[-1]) + len(block) > CELL_CHUNK:
            chunks.append([])
        cellblk.append(len(chunks) - 1)
        celloff.append(len(chunks[-1]))     # offset WITHIN this song's chunk
        length.append(len(rows))
        chunks[-1].extend(block)
    if not chkind:
        chkind = [0]
    if not chunks[0]:
        chunks[0] = [0]
    cell_names = ["CELLS" if k == 0 else "CELLS%d" % (k + 1)
                  for k in range(len(chunks))]
    flags = [int(d.get("flags", 0)) & 0xFF for _name, d in song_defs] or [0]
    s = len(song_defs) or 1
    _u16 = lambda name, vals: "    const %s: array[u16, %d] = [%s]" % (
        name, max(1, len(vals)), ", ".join(str(x) for x in (vals or [0])))
    L = []
    L.append("-- GENERATED by MosaiK8 Studio (mosaik_vm) -- edit scripts/songs.toml, not this.")
    L.append("-- The song LIBRARY as DATA the vm.music driver plays (audio plan Tier 1):")
    L.append("-- GENERIC channels (kinds) + per-channel cells [note, instrument, volume] laid out")
    L.append("-- row-major (row*channels + channel). A shell wires it:")
    L.append("--   music.set_song(songs.channels, songs.chkind, songs.cell, songs.frames, songs.rows).")
    L.append("")
    L.append('module "songs" {')
    L.append('    import "platform.assets"      -- CELLS reads through the code-stream seam')
    L.append(_mos_u8_array("CHKIND", chkind))    # per-song channel kinds, concatenated
    # The interleaved song blob, per ROW: [frames, (note, inst, vol, fx cmd, fx param)
    # per channel] -- stride 1 + channels * 5. Read through `assets.code_byte`: the
    # byte-identical `CELLS[i]` on every directly-mapped console, but on the LYNX a
    # blob past the stream threshold is ARCHIVED + page-cache-streamed from the cart
    # (the VM8 fetch seam), so a big imported song (rows, frames AND effects) stops
    # eating the resident MAIN area.
    #
    # CHUNKED at a ROM bank, the same rule the scene transpiler's concatenated
    # per-scene tileset table follows: a const array is read in place while its ONE
    # bank is mapped, so no single symbol may cross one. Each SONG's block lives
    # whole inside one chunk, CELLBLK says which and CELLOFF is the offset WITHIN
    # it. A library that fits one chunk (every project before this) emits exactly
    # the one CELLS symbol and no CELLBLK - byte-identical. Without this the whole
    # library had to fit 16 KB, which is what made the reference-engine sample import 3
    # of its 10 songs and boot to a SILENT title screen.
    for k, chunk in enumerate(chunks):
        if k:
            L.append("    -- Chunk %d: no const array may cross a ROM bank, so the"
                     % (k + 1))
            L.append("    -- song blob continues in its own symbol.")
        L.append(_mos_u8_array(cell_names[k], chunk))
    L.append(_mos_u8_array("CHN", chn))          # channel count per song
    L.append(_u16("CHOFF", choff))               # per-song start into CHKIND
    L.append(_u16("CELLOFF", celloff))           # per-song start BYTE offset into its chunk
    L.append(_u16("LEN", length))                # per-song row count (u16: real hUGE
                                                 # imports run past 255 rows)
    L.append(_mos_u8_array("FLAGS", flags))      # per-song VOICING (bit0 GB pulse2 / bit1 bass-pri)
    if len(chunks) > 1:
        L.append(_mos_u8_array("CELLBLK", cellblk))   # which CELLS chunk a song is in
    L.append("")
    L.append("    -- channel COUNT of a song.")
    L.append("    function channels(song: u8) -> u8 {")
    L.append("        return CHN[song]")
    L.append("    }")
    L.append("    -- KIND of channel `ch` (0 pulse / 1 wave / 2 noise).")
    L.append("    function chkind(song: u8, ch: u8) -> u8 {")
    L.append("        return CHKIND[CHOFF[song] + ch]")
    L.append("    }")
    if len(cell_names) > 1:
        # ONE shared reader for the chunk fork, not one inlined per accessor.
        # `assets.code_byte` lowers to a SWITCH_ROM + read on a banking console,
        # and a function containing that lowering can NEVER bank (it would switch
        # its own code out), so every arm of the fork is RESIDENT image. Inlining
        # it in both cell() and frames() cost ~510 B of bank 0 and overflowed the
        # reference-engine sample by 257 B; sharing it halves that. Emitted only when the
        # library actually chunked, so a one-chunk project keeps its inline read
        # and stays byte-identical.
        L.append("    -- One byte of a song's cell blob. The chunk fork lives HERE")
        L.append("    -- rather than in both accessors: a seam read cannot bank, so")
        L.append("    -- each arm is resident image and duplicating it is expensive.")
        L.append("    local function cbyte(song: u8, off: u16) -> u8 {")
        for k, nm in enumerate(cell_names):
            if k == 0:
                L.append("        if CELLBLK[song] == 0 {")
            elif k == len(cell_names) - 1:
                L.append("        } else {")
            else:
                L.append("        } else if CELLBLK[song] == %d {" % k)
            L.append("            return assets.code_byte(%s, off)" % nm)
        L.append("        }")
        L.append("    }")
        L.append("")

    def _read(off_expr):
        if len(cell_names) == 1:
            return "        return assets.code_byte(%s, %s)" % (cell_names[0], off_expr)
        return "        return cbyte(song, %s)" % off_expr

    L.append("    -- cell field: 0 = note, 1 = instrument, 2 = volume, 3 = fx cmd, 4 = fx param.")
    L.append("    function cell(song: u8, row: u16, ch: u8, field: u8) -> u8 {")
    L.append("        var i: u16 = CELLOFF[song] + row * (1 + CHN[song] * 5)")
    L.append(_read("i + 1 + ch * 5 + field"))
    L.append("    }")
    L.append("    function frames(song: u8, row: u16) -> u8 {")
    L.append("        var i: u16 = CELLOFF[song] + row * (1 + CHN[song] * 5)")
    L.append(_read("i"))
    L.append("    }")
    L.append("    function rows(song: u8) -> u16 {")
    L.append("        return LEN[song]")
    L.append("    }")
    L.append("    -- per-song VOICING flags (bit0 = GB pulse 2 plays music / bit1 = Lynx-SMS")
    L.append("    -- prefer the wave/bass channel over a 2nd pulse); 0 = the default routing.")
    L.append("    function flags(song: u8) -> u8 {")
    L.append("        return FLAGS[song]")
    L.append("    }")
    L.append("")
    L.append("    export channels, chkind, cell, frames, rows, flags")
    L.append("}")
    L.append("")
    return "\n".join(L)


def generate_songs(root, out_path=None):
    """Generate `src/songs.mos` from `scripts/songs.toml` (the song library), or
    return None when there is no library (then no module + no cost). Mirrors
    `generate_clips`: files-are-truth song DATA -> the driver's accessor module."""
    scripts_dir = os.path.join(root, "scripts")
    defs = load_song_defs(scripts_dir)
    if not defs:
        return None
    out_path = out_path or os.path.join(root, "src", "songs.mos")
    # Generate BEFORE opening the file. `open(..., "w")` TRUNCATES, so an
    # exception raised while emitting (a song over CELL_CHUNK, say) used to
    # leave a ZERO-BYTE generated module behind - which does not fail safe:
    # the build then dies layers later with `imports unknown module ...`,
    # naming neither the real cause nor the limit that was hit.
    text = emit_songs_mos(defs)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path
