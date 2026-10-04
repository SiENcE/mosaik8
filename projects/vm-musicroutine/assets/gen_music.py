"""Write `assets/music/routine.uge` - the sample's own hUGETracker module.

W7h's gate needs a `.uge` that authors the `6xy` CALL-ROUTINE effect on rows we
can NAME, and every module in the tree is someone else's song. So this writes
one: a v5 module, four channels, one pattern, silent except for the effects
that matter.

It is a WRITER for the exact layout `mosaik_vm.uge.parse` reads (that reader is
a port of hUGETracker's own `codegen.pas`), field for field and in the same
order - and it round-trips through `parse` before it writes, so a drift in
either direction is a hard error here rather than a mystery in a ROM.

**v5, not v6, on purpose.** A v6 instrument carries a FIXED 64-cell subpattern
array whether or not it is enabled, which is 1,088 bytes x 45 instruments: the
module would be ~65 KB of zeroes for a test asset. v5 has none, and the reader
takes both.

THE ROWS ARE THE TEST (see verify.py + the record):

    row  0   6 51   slot 1, argument 5  -- the first fire
    row 16   6 21   slot 1, argument 2  -- a SECOND fire while the first
                                           instance is still running: the busy
                                           gate must drop it, so the argument
                                           the script recorded stays 5, not 2
    row 32   6 32   slot 2, argument 3  -- a different slot, still gated
    row 48   6 40   slot 0, argument 4  -- slot 0 is UNATTACHED, which aborts
                                           the rest of that frame's drain

The parameter byte is `xy`: hUGEDriver indexes its 16-entry routines table by
`y & 0x0F` (all sixteen are the same thunk), and the VM reads the whole byte -
`& 3` is the slot, `>> 4` is the argument. So `6 51` is slot 1, argument 5.

    python projects/vm-musicroutine/assets/gen_music.py
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)

OUT = os.path.join(HERE, "music", "routine.uge")

VERSION = 5
TICKS = 6                 # ticks per row; 6 at 64 Hz = ~10.7 rows a second
PATTERN_ID = 0
ORDER_LEN = 1             # one pattern, looped

#: (row, effect, parameter). Effect 6 is CALL ROUTINE.
CELLS = [
    (0,  6, 0x51),
    (16, 6, 0x21),
    (32, 6, 0x32),
    (48, 6, 0x40),
]

NO_NOTE = 90              # >= 72 reads as "no note" (uge.note_name)

#: A NOTE on every effect row, on duty instrument 1. Not decoration: with no
#: note the driver never triggers a channel, the APU's NR52 reads 0x00, and
#: "is the driver actually running" has no cheap answer left - the verify would
#: be asserting the feature with no independent sign of life underneath it.
NOTE = 36                 # C_6
NOTE_INSTRUMENT = 1


def u32(v):
    return struct.pack("<I", v & 0xFFFFFFFF)


def u8(v):
    return bytes([v & 0xFF])


def sstr(text=""):
    """A 256-byte Pascal shortstring slot. The reader SKIPS it, so only the
    length matters; the text is here so a tracker opening the file sees a
    name."""
    raw = text.encode("ascii", "replace")[:255]
    return bytes([len(raw)]) + raw + b"\x00" * (255 - len(raw))


def tail():
    """The 6 bytes versions 4..5 carry after each instrument."""
    return b"\x00" * 6


def duty_instrument(i):
    # Instrument 1 (index 0) is the only one the pattern names, and it needs a
    # non-zero INITIAL VOLUME or the note triggers into silence.
    iv = 15 if i == 0 else 0
    return (u32(i) + sstr("duty %d" % i)
            + u32(0) + u8(0) + u8(iv)       # length, len_en, initial volume
            + u32(0) + u8(0)                # volume sweep dir, change
            + u32(0) + u32(0) + u32(0)      # sweep time, dir, shift
            + u8(2) + u32(0) + u32(0)       # duty cycle (50%), two ignored u32
            + u32(0)                        # v<6 only
            + u32(0)
            + u32(0)                        # v<6 subpattern slot
            + tail())


def wave_instrument(i):
    return (u32(i) + sstr("wave %d" % i)
            + u32(0) + u8(0) + u8(0) + u32(0) + u8(0)
            + u32(0) + u32(0) + u32(0) + u8(0)
            + u32(0) + u32(0)               # volume, waveform index
            + u32(0)                        # v<6 only
            + u32(0)
            + u32(0)                        # v<6 subpattern slot
            + tail())


def noise_instrument(i):
    return (u32(i) + sstr("noise %d" % i)
            + u32(0) + u8(0) + u8(0)
            + u32(0) + u8(0)
            + u32(0) + u32(0) + u32(0) + u8(0) + u32(0) + u32(0)
            + u32(0)                        # v<6 only
            + u32(0)                        # counter mode
            + u32(0)                        # v<6 subpattern slot
            + tail())


def build():
    out = bytearray()
    out += u32(VERSION)
    out += sstr("routine") + sstr("MosaiK8") + sstr("W7h call-routine gate")
    for i in range(15):
        out += duty_instrument(i)
    for i in range(15):
        out += wave_instrument(i)
    for i in range(15):
        out += noise_instrument(i)
    for _ in range(16):                     # 16 wavetables of 32 nibbles
        out += bytes(32)
    out += u32(TICKS)
    # patterns
    effects = {row: (eff, par) for row, eff, par in CELLS}
    out += u32(1)                           # one pattern in the table
    out += u32(PATTERN_ID)
    for row in range(64):
        eff, par = effects.get(row, (0, 0))
        note = NOTE if row in effects else NO_NOTE
        inst = NOTE_INSTRUMENT if row in effects else 0
        out += u32(note) + u32(inst) + u32(eff) + u8(par)
    # four order lists, each `len + 1` as the format's own off-by-one
    for _ in range(4):
        out += u32(ORDER_LEN + 1) + u32(PATTERN_ID) * ORDER_LEN + u32(0)
    return bytes(out)


#: The PORTABLE twin, `scripts/songs.toml`, for OUR driver (D9: effect 15 is
#: the same CALL ROUTINE, its parameter byte verbatim). One pulse channel, the
#: same 64 rows, the same note on the same rows. Its row lasts 6 FRAMES of
#: vm.music's VBL tick, where the .uge's lasts 6 ticks of hUGEDriver's 64 Hz
#: timer - so the two arms of verify.py read the same rows at slightly
#: different display frames, and each arm knows its own clock.
SONGS = os.path.join(HERE, "..", "scripts", "songs.toml")
FX_CALL = 15               # audio_caps.FX_CALL / vm.music effect 15


def build_songs_toml():
    effects = {row: par for row, _eff, par in CELLS}
    rows, fx = [], []
    for row in range(64):
        on = row in effects
        rows.append("  [ %d, %d, 0, 0,]," % (TICKS, NOTE + 1 if on else 0))
        fx.append("  [ %d, %d,]," % ((FX_CALL, effects[row]) if on else (0, 0)))
    return "\n".join([
        "# GENERATED by assets/gen_music.py -- the PORTABLE twin of",
        "# assets/music/routine.uge (same rows, same `6xy` parameters, as effect",
        "# 15). The default build plays the .uge through hUGEDriver; verify.py's",
        "# second arm switches the GB to vm.music and plays THIS.",
        "[song.routine]",
        'channels = [ "pulse",]',
        "rows = [",
        *rows,
        "]",
        "fx = [",
        *fx,
        "]",
        "",
    ])


def main():
    with open(SONGS, "w", encoding="utf-8", newline="\n") as f:
        f.write(build_songs_toml())
    data = build()
    from mosaik_vm import uge
    m = uge.parse(data)                     # round-trip, or fail loudly here
    assert m.version == VERSION, m.version
    assert m.ticks == TICKS, m.ticks
    assert m.orders == [[PATTERN_ID]] * 4, m.orders
    rows = m.patterns[PATTERN_ID]
    for row, eff, par in CELLS:
        got_eff, got_par = rows[row][2], rows[row][3]
        assert (got_eff, got_par) == (eff, par), (row, got_eff, got_par)
    for row in range(64):
        if row not in {c[0] for c in CELLS}:
            assert rows[row][2] == 0, row
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "wb") as f:
        f.write(data)
    print("wrote %s (%d bytes); effects on rows %s"
          % (os.path.relpath(OUT, ROOT), len(data),
             ", ".join("%d=6%02X" % (r, p) for r, _e, p in CELLS)))


if __name__ == "__main__":
    main()
