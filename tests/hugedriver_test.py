"""`native.huge` - the hUGEDriver bind.

Pins the contracts that are easy to break silently:
  * opt-in: a program that does not import it is byte-identical, and the driver
    object is not added to the link
  * GB-family only: a hard compile error elsewhere (it is Game Boy APU assembly;
    CrossZGB's "sms/gg hUGEDriver.c" are no-op STUBS, so there is nothing to
    fall back to)
  * the verbs lower to the wrapper names the prelude defines
  * `gbs_huge_play` is DECLARED, not defined - the generated song module owns it
  * **the vendored header matches the vendored object.** This is the trap that
    cost a session: the hUGEDriver repo's current header has a 4-byte tempo and
    a 1-byte order_cnt, while every shipped driver build (CrossZGB's objects
    here AND the reference engine's lib) wants a 1-byte tempo and a POINTER-typed
    order_cnt. Pairing them shifts every order pointer by two and the ROM goes
    SILENT with no error at all - init succeeds and the tick counters advance.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mosaik import MosaikCompiler  # noqa: E402
from mosaik8_targets import hugedriver_paths, HUGEDRIVER_DIR  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


USER = '''
module "main" {
    import "native.huge"
    var t: u8
    function main() {
        huge.play(0)
        loop {
            huge.update()
            t = t + 1
            if t == 200 { huge.stop() }
        }
    }
}
'''

PLAIN = '''
module "main" {
    var t: u8
    function main() { t = 1 }
}
'''

#: hUGEDriver AND effects, i.e. what every real VM8 game is (vm.snd is always
#: wired). The borrow hooks only exist when the sound helpers are emitted too.
SFX_AND_MUSIC = '''
module "main" {
    import "native.huge"
    import "platform.sound"
    var t: u8
    function main() {
        huge.play(0)
        loop {
            huge.update()
            t = t + 1
            if t == 200 { sound.beep(440, 10) }
        }
    }
}
'''


#: hUGEDriver in a program that also owns a SCANLINE interrupt. The tick is a
#: ~2k-cycle ISR and the bands' scroll writes have to be able to preempt it.
HUGE_AND_BANDS = '''
module "main" {
    import "native.huge"
    import "graphics.bkg"
    var t: u8
    function main() {
        bkg.parallax_band(0, 79)
        bkg.parallax_band(1, 0)
        bkg.parallax(2)
        huge.play(0)
        loop {
            bkg.parallax_scx(0, t)
            huge.update()
            t = t + 1
        }
    }
}
'''


# Both clients in one program must use the SAME timer vector, even when a
# real project's glue normally selects just one music driver.
BOTH_TIMERS = '''
module "main" {
    import "native.huge"
    var g_tick: function()
    local function tick() { }
    function main() {
        g_tick = tick
        system.music_isr(g_tick)
        huge.play(0)
        loop { huge.update() }
    }
}
'''


def compile_src(src, platform="gameboy"):
    c = MosaikCompiler()
    return c.compile_program([("m.mos", src)], platform=platform), c.code_generator


def _body(src, sig):
    """A function's body, from its DEFINITION (never its prototype)."""
    return src.split(sig, 1)[1].split("\n}", 1)[0]


def test_tick_nests_before_its_own_prologue():
    """The tick must re-enable interrupts BEFORE it does anything of its own.

    The LYC band chain writes each band's scroll from an interrupt and has
    until that scanline's H-blank to do it. Every cycle this ISR spends with
    interrupts off is exposure: nesting after the re-entrancy latch and the
    bank switch measured 176 cycles, 0.39 of a scanline, and the reference-engine sample conversion's band
    boundary slipped a line on 1.00% of frames. Nesting first took that to
    0.66%. The reference VM has none of this exposure - its timer VECTOR is `ei` then `jp`.
    """
    c, g = compile_src(HUGE_AND_BANDS)
    isr = _body(c, "void gbs_huge_isr(void) NONBANKED {")
    check("a program with bands AND hUGE nests the tick", "enable_interrupts();" in isr)
    check("...before the re-entrancy latch and the bank switch, not after",
          isr.index("enable_interrupts();") < isr.index("in_tick)")
          and isr.index("enable_interrupts();") < isr.index("CURRENT_BANK"),
          "enable_interrupts() must be the ISR's first statement")
    check("...and the dropped-tick path still leaves interrupts as it found them",
          "disable_interrupts();" in isr.split("in_tick)", 1)[1].split("}", 1)[0])
    plain, _ = compile_src(USER)
    plain_isr = _body(plain, "void gbs_huge_isr(void) NONBANKED {")
    check("a program with NO scanline consumer does not nest at all",
          "enable_interrupts();" not in plain_isr and "in_tick" not in plain_isr,
          "that ISR must stay byte-identical for every non-parallax hUGE game")


def test_timer_chain_selection():
    """Only parallax selects the early-nesting, still-chained timer vector."""
    watchdog = BOTH_TIMERS.replace('    import "native.huge"\n', '') \
        .replace('        huge.play(0)\n', '').replace('huge.update()', '')
    clients = (("hUGE", USER, ("gbs_huge_isr",)),
               ("watchdog", watchdog, ("gbs_mdrv_isr",)),
               ("both", BOTH_TIMERS, ("gbs_huge_isr", "gbs_mdrv_isr")))
    for platform in ("gameboy", "gameboy_color", "analogue_pocket", "megaduck"):
        for label, source, handlers in clients:
            for bands in (False, True):
                src = source
                if bands:
                    src = src.replace('module "main" {',
                                      'module "main" {\n    import "graphics.bkg"')
                    src = src.replace('    function main() {', '''    function main() {
        bkg.parallax_band(0, 79)
        bkg.parallax_band(1, 0)
        bkg.parallax(2)''')
                c, g = compile_src(src, platform)
                check("%s %s bands=%s: fixture compiles and detects bands" %
                      (platform, label, bands),
                      not c.startswith("Compilation error") and g.parallax_used == bands)
                installer = "add_low_priority_TIM" if bands else "add_TIM"
                other = "add_TIM" if bands else "add_low_priority_TIM"
                check("%s %s bands=%s: every client uses %s" %
                      (platform, label, bands, installer),
                      all("%s(%s);" % (installer, h) in c for h in handlers)
                      and other + "(" not in c)

    # A sprite cut alone already nests INSIDE its callback. It must not opt
    # into the new vector or otherwise change that existing callback.
    cut = BOTH_TIMERS.replace('    function main() {',
                             '    function main() {\n        text.win_sprite_cut(1)')
    c, g = compile_src(cut)
    check("a cut without parallax keeps BOTH ordinary timer registrations",
          g.win_cut_used and not g.parallax_used
          and "add_TIM(gbs_huge_isr);" in c and "add_TIM(gbs_mdrv_isr);" in c
          and "add_low_priority_TIM(" not in c)
    isr = _body(c, "void gbs_huge_isr(void) NONBANKED {")
    check("a cut without parallax retains callback nesting and the latch",
          "enable_interrupts();" in isr and "if (in_tick)" in isr)

    sms, _ = compile_src(watchdog, "sms")
    check("the non-GB watchdog stays on VBL, not either GB timer vector",
          "add_VBL(gbs_mdrv_isr);" in sms and "add_TIM(" not in sms
          and "add_low_priority_TIM(" not in sms)


def test_optin():
    on, g = compile_src(USER)
    off, g0 = compile_src(PLAIN)
    check("importing it sets the flag", g.native_huge_imported)
    check("not importing it does not", not g0.native_huge_imported)
    check("the header is included only when imported",
          '#include "hUGEDriver.h"' in on and "hUGEDriver.h" not in off)
    check("the verbs lower to the wrappers",
          "gbs_huge_play(0)" in on and "gbs_huge_update()" in on
          and "gbs_huge_stop()" in on)
    check("the wrappers are defined in the prelude",
          "void gbs_huge_update(void) {" in on and "hUGE_dosound();" in on
          and "void gbs_huge_stop(void) {" in on)
    # THE TICK IS A TIMER INTERRUPT, and this is the contract that matters most.
    # hUGE's tempo is in ticks, so the driver must be called at a FIXED rate;
    # driving it from the VM loop ties the music to the frame rate, and a VM
    # frame is not a display frame. Measured against the reference engine's own ROM: theirs
    # holds 64.0 Hz standing AND walking, ours ran 59.7 Hz standing and collapsed
    # to 24.3 Hz walking until this moved to the timer.
    check("the tick is a TIMER ISR at the reference engine's own 64 Hz",
          "void gbs_huge_isr(void) NONBANKED {" in on
          and "add_TIM(gbs_huge_isr);" in on
          and "uint8_t gbs_huge_hz = 64;" in on           # the default rate
          and "TMA_REG = (uint8_t)(256 - (4096u / hz));" in on
          and "TAC_REG = TACF_START | TACF_4KHZ;" in on)
    # Same-song replay is a NO-OP, the reference engine's own rule (`music_load` early-outs
    # when bank and track both match). Without it every scene whose On Init plays
    # the area theme restarts it from the top, so walking between rooms never lets
    # the song past its opening - which is heard as "the music does not continue".
    check("replaying the song already playing is a no-op",
          "if (gbs_huge_on && song == gbs_huge_cur) return;" in on
          and "gbs_huge_on = 0;" in on)
    check("the per-frame seam call is EMPTY (ticking there too would double tempo)",
          "void gbs_huge_update(void) { }" in on)
    # SFX <-> MUSIC ARBITRATION (the reference engine's model). hUGEDriver owns all four
    # channels and vm.snd writes two of them directly - sound.beep is pulse 2,
    # the 2nd music voice is pulse 1 - so without a borrow an effect takes a
    # channel mid-note and leaves its envelope at zero. (Note the reference conversion's
    # "pulse 1 permanently dead at channel bits 0xE" symptom was NOT this - it
    # was the order_cnt dereference bug, pinned in test_song_generation - but
    # the borrow is still required the moment a real effect plays, and GB
    # Studio's driver_set_mute_mask is this exact `hUGE_mute_mask =` write.)
    # The hook sites only exist for a program that plays SFX as well, which is
    # every real VM8 game (vm.snd is always wired) but not the minimal fixture.
    both, _gb = compile_src(SFX_AND_MUSIC)
    check("an effect BORROWS its channel from the driver and hands it back",
          "void gbs_huge_sfx_take(uint8_t ch) {" in on
          and "void gbs_huge_sfx_give(uint8_t ch) {" in on
          and "gbs_huge_sfx_take(0x02);" in both    # sound.beep = pulse 2 = CH2
          and "gbs_huge_sfx_give(0x02);" in both)
    # Two masks OR-ed, never assigned: an effect ending must not clear a mute the
    # GAME asked for (and vice versa) - the same bug shape as the interrupt mask.
    check("the game's mute and the effect's borrow are OR-ed, not assigned",
          "uint8_t m = gbs_huge_gmute | gbs_huge_sfxm;" in on
          and "gbs_huge_gmute = mask;" in on)
    # hUGEDriver CACHES the loaded waveform, so an effect that used CH3 has
    # invalidated it without the driver knowing - CH3 would play the overwritten
    # RAM forever. The reference engine resets it on every effect end.
    check("giving a channel back invalidates the driver's wave cache",
          "hUGE_reset_wave();" in on)
    # The driver reads its patterns IN PLACE every tick, so a banked song has to
    # be mapped around each tick as well as around hUGE_init - now inside the ISR.
    check("the ISR maps the playing song's bank and restores the caller's",
          "extern uint8_t gbs_huge_bank;" in on
          and "if (gbs_huge_bank) SWITCH_ROM(gbs_huge_bank);" in on
          and "SWITCH_ROM(saved);" in on)
    # Assigning the interrupt mask instead of OR-ing it is how one feature turns
    # another's interrupt off; the dialogue box's sprite cut owns VBL/LCD, and an
    # assign there would stop the music dead the moment a box opened.
    check("the interrupt mask is OR-ed into, not assigned",
          "set_interrupts(IE_REG | VBL_IFLAG | TIM_IFLAG);" in on)
    # The split is HARDWARE in the prelude, DATA in the generated song module:
    # play() powers the APU and delegates the table lookup. A declaration here
    # and a definition there is what makes a project that calls play() with no
    # songs fail LOUDLY at link instead of playing silence.
    check("gbs_huge_song_init is declared, not defined",
          "void gbs_huge_song_init(uint8_t song);" in on
          and "void gbs_huge_song_init(uint8_t song) {" not in on)
    # hUGEDriver does NOT power up the APU (its README leaves that to the game),
    # and a driver ticking into a powered-down APU is indistinguishable from a
    # broken one -- NR52 reads 0x70 and no channel bit ever sets.
    check("play() powers up the APU", "NR52_REG = 0x80;" in on
          and "gbs_huge_song_init(song);" in on)


def test_gb_family_only():
    for plat in ("sms", "gamegear", "nes", "lynx", "pce"):
        out, _ = compile_src(USER, platform=plat)
        check("refused on %s" % plat,
              out.startswith("Compilation error") and "native.huge" in out,
              out.splitlines()[0] if out else "")
    for plat in ("gameboy", "gameboy_color", "analogue_pocket", "megaduck"):
        out, _ = compile_src(USER, platform=plat)
        check("accepted on %s" % plat, not out.startswith("Compilation error"),
              out.splitlines()[0] if out else "")


def test_vendored_files():
    inc, obj = hugedriver_paths("gameboy")
    _inc, duck = hugedriver_paths("megaduck")
    check("the GB object is vendored", obj is not None)
    check("the Mega Duck object is vendored (its APU is remapped)",
          duck is not None and duck != obj)
    hdr = os.path.join(HUGEDRIVER_DIR, "hUGEDriver.h")
    check("the header is vendored beside them", os.path.isfile(hdr))
    if not os.path.isfile(hdr):
        return
    text = open(hdr, encoding="utf-8", errors="replace").read()
    song = re.search(r"typedef struct hUGESong_t \{(.*?)\} hUGESong_t;",
                     text, re.S)
    check("hUGESong_t is present", song is not None)
    if not song:
        return
    body = song.group(1)
    # THE match rule. Both are valid hUGEDriver headers; only one matches the
    # objects that ship. If someone refreshes the header from upstream without
    # refreshing the objects, this is what catches it -- a ROM cannot.
    check("the header matches the SHIPPED driver ABI "
          "(1-byte tempo + pointer-typed order_cnt)",
          re.search(r"unsigned char\s+tempo\s*;", body) is not None
          and re.search(r"const unsigned char\s*\*\s*order_cnt\s*;", body) is not None,
          "the upstream 4-byte-tempo header would silently mis-play every song")
    check("it is NOT the newer upstream layout",
          "tempo1" not in body)


SONGS = {
    "song": {
        "One": {"channels": ["pulse", "pulse", "wave", "noise"],
                # [frames, n,i,v] x4 -- note 1 = C-3 (our 0 is a REST)
                "rows": [[6, 25, 1, 0, 0, 0, 0, 13, 2, 8, 0, 3, 0],
                         [6, 0, 0, 0, 27, 1, 0, 0, 0, 0, 5, 3, 0],
                         [3, 30, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]]},
    }
}
INSTS = {
    "instrument": {
        "Lead": {"kind": "pulse", "duty": 2, "vol": 15, "decay": 3},
        "Bass": {"kind": "wave", "wave": 0, "vol": 12},
        "Drum": {"kind": "noise", "env": 0xF1, "noisefreq": 0x30, "vol": 15},
    }
}


def _song_project(tmp):
    import toml
    os.makedirs(os.path.join(tmp, "scripts"), exist_ok=True)
    with open(os.path.join(tmp, "scripts", "songs.toml"), "w", encoding="utf-8") as f:
        toml.dump(SONGS, f)
    with open(os.path.join(tmp, "scripts", "instruments.toml"), "w", encoding="utf-8") as f:
        toml.dump(INSTS, f)
    return tmp


def test_song_generation():
    import shutil
    import tempfile
    from mosaik_vm import huge

    tmp = tempfile.mkdtemp()
    try:
        _song_project(tmp)
        out = os.path.join(tmp, "out")
        paths, _warn, _banks = huge.generate_huge_songs(tmp, out, first_bank=7)
        names = sorted(os.path.basename(p) for p in paths)
        check("it emits a resident table + banked data",
              "songs_huge.c" in names and any(n.startswith("songs_huge_b") for n in names),
              str(names))
        res = open(os.path.join(out, "songs_huge.c"), encoding="utf-8").read()
        bank = open(os.path.join(out, "songs_huge_b7.c"), encoding="utf-8").read()
        # The bank number comes from the BUILD (after the codegen allocated its
        # own), so it must be honoured, not invented by the generator.
        check("the requested first bank is used", "#pragma bank 7" in bank)
        check("the table names the song's bank",
              "gbs_huge_song_bank[] = {7}" in res)
        # The song data is read in place, so the seam has to map the bank both
        # at init and (via the prelude) on every tick.
        check("song_init switches to the song's bank",
              "SWITCH_ROM(gbs_huge_bank);" in res
              and "hUGE_init(gbs_huge_songs[song])" in res)
        check("it restores the caller's bank",
              "saved = CURRENT_BANK;" in res and "SWITCH_ROM(saved);" in res)
        # Our note 0 is a REST and note 1 is C-3; hUGE's C_3 is 0 and its rest is
        # 90. Getting this off by one transposes the whole song a semitone.
        check("note 25 lowers to hUGE 24, and a 0 to the rest sentinel",
              "DN(24,1,0x000)" in bank and "DN(90,0," in bank)
        # A per-row frame change has to become an Fxx, because hUGE keeps ONE
        # tempo per song while our rows each carry a duration.
        check("a row whose frames change emits an Fxx set-speed",
              "0xF03" in bank, "row 3 drops from 6 frames to 3")
        check("a per-cell volume emits a Cxy set-volume", "0xC80" in bank)
        # order_cnt is a POINTER the driver DEREFERENCES (the reference engine emits
        # `static const unsigned char order_cnt = N;` and passes &order_cnt).
        # Casting the count itself to a pointer made the driver read ROM byte
        # 0x0002 as the order length: pattern 0 played correctly, then the
        # driver "advanced" through neighbouring ROM - each channel slid onto
        # its neighbour's pattern, CH4 hit a NULL, and pulse 1 went permanently
        # dead ~8 s into every song (the reference conversion's channel bits stuck at 0xE).
        check("the descriptor's order_cnt is a real dereferenceable byte",
              "static const unsigned char gbs_huge_song0_ocnt = 2;" in bank
              and "&gbs_huge_song0_ocnt," in bank
              and "(const unsigned char *)2," not in bank)
        check("the wave instrument's RAM is emitted", "_waves[] = {" in bank)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_per_song_instrument_slots():
    """hUGE's 15-instrument limit is PER SONG; ours is one shared library. Mapping
    the GLOBAL list would drop everything past 15 and silently play those cells on
    instrument 0, so only what a song USES is mapped."""
    from mosaik_vm import huge, songs as _songs
    insts = [("i%d" % i, {"kind": "pulse", "duty": 0, "vol": 15, "decay": 0})
             for i in range(1, 41)]
    # A song using only instruments 30 and 31 of a 40-instrument library. Rows
    # arrive from load_song_defs already normalized to (frames, cells).
    entry = {"channels": ["pulse"],
             "rows": [_songs._norm_cell_row([6, 25, 30, 0], 1),
                      _songs._norm_cell_row([6, 27, 31, 0], 1)]}
    text, warn = huge.render_song("S", entry, insts, "s0")
    check("a late library instrument still maps", not warn, "; ".join(warn))
    check("it takes hUGE slot 1 (first use), not its library index",
          "DN(24,1,0x000)" in text and "DN(26,2,0x000)" in text)


def test_call_routine_is_stripped():
    """A `6xy` cell reaches the driver ONLY when a routines table exists.

    Before W7h this pinned an unconditional strip. The strip is still what
    happens for a project that attaches NO routine, and it still has to: the
    descriptor's `routines` field is NULL there, and MEASURED with
    `tools/musicprobe/routine_null_probe.py` (2026-09-17) that is a crash, not
    a no-op - the shipped `do_effect` returns early only when the effect code
    AND parameter are both zero, `fx_call_routine` has no NULL test, and the
    word at a GBDK image's ROM 0x0000 is 0xFFFF, so a patched shooter conversion's
    stack pointer collapsed from 0xDF8B to 0x0017 (`jp 0xFFFF` wraps to
    0x0000, which is `RST 0x38`, which recurses).

    W7h's half is the OTHER arm: with `routines=True` (the build states it from
    `VM_OP_MUSIC_ROUTINE`) the cell is carried verbatim and the descriptor
    points at the resident thunk table. Both arms are pinned here, because
    either one alone is a feature that quietly does nothing.
    """
    from mosaik_vm import uge

    m = uge.parse(_fake_uge_with_six())
    text = uge.render_uge_c(m, "s0")
    cells = re.findall(r"DN\(\d+,\d+,0x([0-9A-F])[0-9A-F]{2}\)", text)
    check("no routine attached: no rendered cell carries the effect",
          "6" not in cells, "effect nibbles seen: %s" % sorted(set(cells)))
    check("...and the strip is REPORTED, not silent",
          any("call-routine" in w for w in m.warnings), sorted(m.warnings))
    check("...and the descriptor emits a NULL routines table",
          ", NULL, s0_waves," in text)

    m2 = uge.parse(_fake_uge_with_six())
    text2 = uge.render_uge_c(m2, "s0", routines=True)
    cells2 = re.findall(r"DN\(\d+,\d+,0x([0-9A-F])[0-9A-F]{2}\)", text2)
    check("a routine IS attached: the effect is carried verbatim",
          "6" in cells2, "effect nibbles seen: %s" % sorted(set(cells2)))
    # The fixture is a real module from the tree, so it may warn about its own
    # v6 subpatterns; what must be gone is the call-routine line.
    check("...and the call-routine warning is gone",
          not any("call-routine" in w for w in m2.warnings),
          repr(sorted(m2.warnings)))
    check("...and the descriptor points at the resident thunk table",
          "(const hUGERoutine_t **)gbs_huge_routines, s0_waves," in text2)


def _fake_uge_with_six():
    """A real module from the tree, re-serialised with `600` on row 0.

    The palette-write check conversion's two songs already author one, but
    that conversion is local only and may not be on disk - so this writes the
    effect into whichever first-party module IS here (today
    `projects/vm-musicroutine`), through `uge.cell_offsets` so the file walk
    stays in the one .uge reader."""
    import glob
    import struct
    from mosaik_vm import uge

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands = sorted(glob.glob(os.path.join(root, "projects", "*", "assets",
                                          "music", "*.uge")))
    raw = bytearray(open(cands[0], "rb").read())
    m = uge.parse(bytes(raw))
    pid = m.orders[0][0]
    eff_at, par_at = uge.cell_offsets(m, pid, 0)
    struct.pack_into("<I", raw, eff_at, uge.FX_CALL_ROUTINE)
    raw[par_at] = 0
    return bytes(raw)


#: The v6 fixture's subpattern: an arpeggio on duty instrument 1 whose third
#: row jumps back to row 1 (1-based), so the table loops.
V6_SUB = {0: (36, 0, 0, 0), 1: (40, 0, 0, 0), 2: (43, 1, 0, 0)}
V6_TICKS = 7


def _v6_module():
    """A hUGETracker v6 module, written byte for byte in the layout
    `mosaik_vm.uge.parse` reads (TInstrumentV3 records with their FIXED 64-cell
    subpattern array, the timer-tempo pair, TCellV2 cells).

    Written here rather than read from a real project, so the check runs on
    every clone. It is built to hit the v6 trap the parser once fell into: the
    subpattern array is present whether or not its flag is set, so duty
    instrument 1 carries an ENABLED table, noise instrument 1 a DISABLED one
    full of non-zero junk, and the instruments AFTER each carry fields the test
    reads back - a reader that skips a disabled array misaligns everything
    after it."""
    import struct

    def u32(v):
        return struct.pack("<I", v & 0xFFFFFFFF)

    def u8(v):
        return bytes([v & 0xFF])

    def sstr(text=""):
        raw = text.encode("ascii")[:255]
        return bytes([len(raw)]) + raw + b"\x00" * (255 - len(raw))

    def cells(table, junk=False):
        out = b""
        for k in range(64):
            if junk:
                note, jump, eff, par = 37, 3, 12, 0x55
            else:
                note, jump, eff, par = table.get(k, (90, 0, 0, 0))
            out += u32(note) + u32(0) + u32(jump) + u32(eff) + u8(par)
        return out

    def instrument(typ, i, length=0, len_en=0, iv=0, duty=0, vol=0, wave=0,
                   mode=0, enabled=0, table=None, junk=False):
        return (u32(typ) + sstr("i%d" % i)
                + u32(length) + u8(len_en) + u8(iv)
                + u32(0) + u8(0)                    # volume sweep dir, change
                + u32(0) + u32(0) + u32(0)          # sweep time, dir, shift
                + u8(duty) + u32(vol) + u32(wave)
                + u32(mode) + u8(enabled) + cells(table or {}, junk))

    out = bytearray(u32(6) + sstr("One") + sstr("MosaiK8") + sstr("v6 fixture"))
    for i in range(15):
        if i == 0:
            out += instrument(0, i, iv=15, duty=2, enabled=1, table=V6_SUB)
        elif i == 1:
            out += instrument(0, i, length=5, len_en=1, iv=9, duty=1)
        else:
            out += instrument(0, i)
    for i in range(15):
        out += instrument(1, i, vol=1, wave=3 if i == 0 else 0)
    for i in range(15):
        if i == 0:
            out += instrument(2, i, iv=12, mode=1, enabled=0, junk=True)
        elif i == 1:
            out += instrument(2, i, iv=7)
        else:
            out += instrument(2, i)
    for _ in range(16):
        out += bytes(32)
    out += u32(V6_TICKS) + u8(0) + u32(0)       # ticks, timer enabled, divider
    out += u32(1) + u32(0)                      # one pattern, key 0
    for row in range(64):
        note, inst = {0: (24, 1), 8: (28, 2)}.get(row, (90, 0))
        out += u32(note) + u32(inst) + u32(0) + u32(0) + u8(0)
    for _ in range(4):
        out += u32(2) + u32(0) + u32(0)         # order: [pattern 0], len + 1
    return bytes(out)


def test_uge_source():
    """A project's ORIGINAL `.uge` is rendered straight through - the bit-true path,
    and the actual reason to run hUGEDriver. Matching is by NAME so the song INDEX
    space (what `music_song` compiles to) is identical either way."""
    import shutil
    import tempfile
    from mosaik_vm import huge, uge

    # A v6 module, the version whose subpattern layout the parser had to get
    # right, written by `_v6_module` into a tempdir.
    fixture_dir = tempfile.mkdtemp()
    one = os.path.join(fixture_dir, "v6.uge")
    with open(one, "wb") as f:
        f.write(_v6_module())
    m = uge.parse(open(one, "rb").read())
    check("a v6 module parses", m.version == 6 and len(m.patterns) > 0,
          "version %s" % getattr(m, "version", "?"))
    check("...with its tempo", m.ticks == V6_TICKS, m.ticks)
    sub = m.duty[0]["sub"]
    check("an ENABLED subpattern is read, cell for cell",
          sub is not None and all(sub[k] == V6_SUB[k] for k in V6_SUB)
          and sub[3] == (90, 0, 0, 0), sub[:4] if sub else sub)
    check("the instrument AFTER an enabled table is aligned",
          (m.duty[1]["iv"], m.duty[1]["length"], m.duty[1]["len_en"],
           m.duty[1]["duty"]) == (9, 5, 1, 1),
          m.duty[1])
    check("a DISABLED table is still a fixed 64-cell array: skipped, not read",
          m.noise[0]["sub"] is None and m.noise[0]["iv"] == 12
          and m.noise[0]["mode"] == 1, m.noise[0])
    check("...so the instrument after IT is aligned too",
          m.noise[1]["iv"] == 7 and m.wave[0]["wave"] == 3, m.noise[1])
    check("the TCellV2 pattern cells land on their rows",
          m.patterns[0][0][:2] == (24, 1) and m.patterns[0][8][:2] == (28, 2))
    c, _w = uge.uge_to_c(one, "s0")
    check("an enabled subpattern of a USED instrument is rendered, row 31 "
          "looping to row 0",
          "static const unsigned char s0_dSP1[] = {" in c
          and "DN(36,0,0x000)" in c and "DN(43,1,0x000)" in c
          and "DN(90,1,0x000)" in c)
    check("...and wired into its instrument, the others NULL",
          "s0_dSP1, 0x" in c and "s0_nSP1" not in c)
    check("it renders the SHIPPED descriptor ABI",
          "const hUGESong_t s0 = {" in c and "tempo1" not in c)
    check("the .uge path's order_cnt is a real dereferenceable byte too",
          "static const unsigned char s0_ocnt = " in c
          and "&s0_ocnt," in c)
    check("patterns are shared between channels, not duplicated per channel",
          c.count("_P") > 0 and "s0_order1[] = {s0_P" in c)

    tmp = tempfile.mkdtemp()
    try:
        _song_project(tmp)
        os.makedirs(os.path.join(tmp, "assets", "music"))
        # name it so it matches the fixture song "One"
        shutil.copy(one, os.path.join(tmp, "assets", "music", "One.uge"))
        out = os.path.join(tmp, "out")
        _paths, warn, _banks = huge.generate_huge_songs(tmp, out, first_bank=3)
        check("a matching .uge upgrades the song",
              any("bit-true" in w for w in warn), "; ".join(warn))
        bank = open(os.path.join(out, "songs_huge_b3.c"), encoding="utf-8").read()
        check("the bank file carries the .uge rendering",
              "gbs_huge_song0_duty" in bank)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # An unmatched name must fall back to the portable rendering and SAY so,
    # never silently emit nothing.
    tmp2 = tempfile.mkdtemp()
    try:
        _song_project(tmp2)
        os.makedirs(os.path.join(tmp2, "assets", "music"))
        shutil.copy(one, os.path.join(tmp2, "assets", "music", "Unrelated.uge"))
        _p, warn, _b = huge.generate_huge_songs(tmp2, os.path.join(tmp2, "out"),
                                                first_bank=3)
        check("a non-matching .uge leaves the song on the portable rendering",
              not any("bit-true" in w for w in warn))
    finally:
        shutil.rmtree(tmp2, ignore_errors=True)
        shutil.rmtree(fixture_dir, ignore_errors=True)


def test_glue_wiring():
    from mosaik_vm import glue
    g = glue.emit_glue_mos(True, True,
                           audio={"enabled": True, "gb": "huge",
                                  "lynx": "vm", "smsgg": "vm"})
    check("hUGEDriver is a READY driver now", "huge" in glue._GLUE_READY_DRIVERS)
    check("the GB arm wires vm.music_huge",
          "core.set_music_driver(music_huge.play" in g
          and 'import "vm.music_huge"' in g)
    check("the other groups keep vm.music",
          "core.set_music_driver(music.play" in g)
    check("it registers the SAME core seam (vm.core is untouched)",
          "core.set_music_ctl(music_huge.pause" in g)
    # All-vm must stay exactly as it was: every existing project regenerates
    # byte-identical glue.
    a = glue.emit_glue_mos(True, True, audio={"enabled": True, "gb": "vm",
                                              "lynx": "vm", "smsgg": "vm"})
    check("an all-vm.music project is unchanged",
          "music_huge" not in a and "core.set_music_driver(music.play" in a)


if __name__ == "__main__":
    print("native.huge (hUGEDriver) checks")
    print("=" * 50)
    test_optin()
    test_tick_nests_before_its_own_prologue()
    test_timer_chain_selection()
    test_gb_family_only()
    test_vendored_files()
    test_song_generation()
    test_per_song_instrument_slots()
    test_uge_source()
    test_call_routine_is_stripped()
    test_glue_wiring()
    print("=" * 50)
    if failed:
        print("%d FAILED, %d passed" % (failed, passed))
        sys.exit(1)
    print("All hUGEDriver checks passed (%d)" % passed)
