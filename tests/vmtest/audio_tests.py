"""Sound, music, songs, instruments, glue and the PSG.

Split out of tests/vm_test.py (2026-08-26); run via tests/vm_test.py."""
import os
import sys

from .common import *  # noqa: F401,F403 - FAILS/check/m + shared helpers
from .common import FAILS, _run_expr, check, m
from .common import SPIKE_BLOB, SPIKE_SCRIPTS, ROOT



def test_sound():
    print("[sound: SFX / TONE / SND_STOP ops raise through the sound seam]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "sound_sfx", "id": 2},
        {"event": "sound_tone", "freq": 880, "frames": 10},
        {"event": "sound_stop"},
        {"event": "stop"}]}])
    # byte shape: SFX(0x50) id, TONE(0x51) freq_lo,freq_hi frames, SND_STOP(0x52), STOP
    check(list(prog.code)[:8] == [0x50, 2, 0x51, 880 & 0xFF, 880 >> 8, 10, 0x52, 0x00],
          "sound ops encode with the vm.snd opcodes + little-endian freq")
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(3)
    check(vm.sound_log == [("sfx", 2), ("tone", 880, 10), ("stop",)],
          "the RefVM logs sfx(2), tone(880,10), stop in order")


def test_sound_library():
    print("[sound library: `sound {ref}` lowers to sound_tone via sounds.toml]")
    defs = {"blip": {"freq": 1046, "frames": 4}}
    scripts = [{"name": "main", "events": [
        {"event": "sound", "ref": "blip"},
        {"event": "sound", "ref": "missing"},   # a dangling ref -> dropped
        {"event": "stop"}]}]
    expanded = m.snd_expand_scripts(scripts, defs)
    evs = expanded[0]["events"]
    check(evs[0] == {"event": "sound_tone", "freq": 1046, "frames": 4},
          "a `sound` ref lowers to sound_tone with the library freq/frames")
    check(all(e.get("event") != "sound" for e in evs),
          "a dangling ref is dropped (missing def -> no op)")
    prog = m.Compiler().compile(expanded)
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(2)
    check(vm.sound_log == [("tone", 1046, 4)],
          "the lowered library effect plays in the RefVM")
    # nested: a `sound` ref inside an if/then body is resolved too
    nested = m.snd_expand_scripts([{"name": "main", "events": [
        {"event": "if", "cond": "1", "then": [{"event": "sound", "ref": "blip"}]}]}], defs)
    check(nested[0]["events"][0]["then"][0]["event"] == "sound_tone",
          "a `sound` ref inside an if/then body is lowered")
    # byte-identical when no library (or no `sound` node): the scripts pass through
    plain = [{"name": "main", "events": [{"event": "stop"}]}]
    check(m.snd_expand_scripts(plain, {}) is plain,
          "no defs -> the scripts object passes through unchanged")


def test_music():
    print("[music: MUSIC_PLAY spawns a tracked looping song; MUSIC_STOP kills it]")
    song = {"name": "song", "loop": True, "events": [
        {"event": "sound_tone", "freq": 440, "frames": 2},
        {"event": "wait", "frames": 2}]}
    prog = m.Compiler().compile([
        {"name": "main", "events": [{"event": "music_play", "script": "song"},
                                    {"event": "stop"}]}, song])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()
    check(vm.music_ctx != 255 and vm.active[vm.music_ctx] == 1,
          "MUSIC_PLAY spawned a tracked song thread")
    for _ in range(20):
        vm.frame()
    tones = [e for e in vm.sound_log if e[0] == "tone"]
    check(len(tones) >= 2 and vm.music_ctx != 255,
          "the song loops (keeps playing tones; the tracked thread stays alive)")
    # MUSIC_STOP kills the song thread + clears the handle
    prog2 = m.Compiler().compile([
        {"name": "main", "events": [{"event": "music_play", "script": "song"},
                                    {"event": "music_stop"},
                                    {"event": "stop"}]}, song])
    vm2 = m.RefVM(prog2.code, entry=prog2.entry)
    vm2.run(5)
    check(vm2.music_ctx == 255 and ("music_stop",) in vm2.sound_log,
          "MUSIC_STOP kills the song thread + clears music_ctx")
    # a NEW music_play replaces the old song (only one song thread at a time)
    prog3 = m.Compiler().compile([
        {"name": "main", "events": [{"event": "music_play", "script": "song"},
                                    {"event": "music_play", "script": "song"},
                                    {"event": "stop"}]}, song])
    vm3 = m.RefVM(prog3.code, entry=prog3.entry)
    vm3.frame()   # main runs both music_plays then stops -> only ONE song thread left
    check(sum(vm3.active) == 1 and vm3.music_ctx != 255 and vm3.active[vm3.music_ctx] == 1,
          "a second MUSIC_PLAY replaces the song (one song thread survives, not two)")
    # a song built from MUSIC_TONE (the 2nd voice) records mtone, distinct from sfx tones
    prog4 = m.Compiler().compile([
        {"name": "main", "events": [{"event": "music_play", "script": "song2"},
                                    {"event": "sound_tone", "freq": 200, "frames": 2},
                                    {"event": "stop"}]},
        {"name": "song2", "loop": True, "events": [
            {"event": "music_tone", "freq": 660, "frames": 2},
            {"event": "wait", "frames": 2}]}])
    vm4 = m.RefVM(prog4.code, entry=prog4.entry)
    for _ in range(8):
        vm4.frame()
    check(("mtone", 660, 2) in vm4.sound_log and ("tone", 200, 2) in vm4.sound_log,
          "MUSIC_TONE (music voice) + TONE (SFX voice) log as distinct events")


def test_music_song():
    print("[music: MUSIC_SONG plays a driven song by index (Stage 4b)]")
    # music_song lowers to the MUSIC_SONG op carrying the song index; the RefVM has
    # no driver, so it just logs it (the driver + multi-channel playback is PyBoy-
    # verified on the vm-music ROM). MUSIC_STOP is unchanged.
    prog = m.Compiler().compile([
        {"name": "main", "events": [{"event": "music_song", "song": 2},
                                    {"event": "music_stop"},
                                    {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    for _ in range(4):
        vm.frame()
    check(("music_song", 2) in vm.sound_log,
          "MUSIC_SONG logs the requested song index")
    check(vm.sound_log.index(("music_song", 2)) < vm.sound_log.index(("music_stop",)),
          "MUSIC_SONG then MUSIC_STOP order preserved")
    # song defaults to 0 when omitted
    prog2 = m.Compiler().compile([
        {"name": "main", "events": [{"event": "music_song"}, {"event": "stop"}]}])
    vm2 = m.RefVM(prog2.code, entry=prog2.entry)
    vm2.frame()
    check(("music_song", 0) in vm2.sound_log, "music_song defaults to song 0")


def test_music_transport():
    print("[music transport: MUSIC_PAUSE / RESUME / MUTE (mask) lower + log]")
    # Pause / resume / per-channel mute route through the opt-in core.set_music_ctl seam
    # (no-op unregistered); the driver silences/restores/masks (PyBoy-verified on vm-music).
    prog = m.Compiler().compile([
        {"name": "main", "events": [{"event": "music_pause"}, {"event": "music_resume"},
                                    {"event": "music_mute", "mask": 4}, {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    for _ in range(4):
        vm.frame()
    check(("music_pause",) in vm.sound_log and ("music_resume",) in vm.sound_log,
          "MUSIC_PAUSE + MUSIC_RESUME lower + log")
    check(("music_mute", 4) in vm.sound_log, "MUSIC_MUTE carries the per-channel mask")
    check(vm.sound_log.index(("music_pause",)) < vm.sound_log.index(("music_resume",)),
          "pause-before-resume order preserved")
    # mask defaults to 0 (all audible) when omitted.
    prog2 = m.Compiler().compile([
        {"name": "main", "events": [{"event": "music_mute"}, {"event": "stop"}]}])
    vm2 = m.RefVM(prog2.code, entry=prog2.entry)
    vm2.frame()
    check(("music_mute", 0) in vm2.sound_log, "music_mute defaults to mask 0")


def test_glue():
    print("[glue: the auto-wire module reflects project content]")
    # no songs -> SOUND-only wiring (byte-behaviour-identical to the old inline shell).
    g0 = m.emit_glue_mos(has_songs=False, has_instruments=False)
    check("set_sound" in g0 and "set_music_voice" in g0, "glue always wires SOUND")
    check("vm.music" not in g0 and "set_music_driver" not in g0, "no songs -> no music wiring")
    check("function setup()" in g0 and "export setup" in g0, "glue exposes setup()")
    # songs -> the music driver + song + voicing + transport (+ instruments/waves when present).
    g1 = m.emit_glue_mos(has_songs=True, has_instruments=True)
    check("set_music_driver" in g1 and "set_song" in g1 and "set_voicing" in g1,
          "songs -> wire the music driver + song + voicing")
    check("set_instruments" in g1 and "set_waves" in g1,
          "instruments -> wire instruments + custom waves")
    check("set_music_ctl" in g1, "songs -> wire pause/resume/mute transport")
    check('import "songs"' in g1 and 'import "vm.music"' in g1, "glue imports what it wires")
    # Even with every configurable GROUP on vm.music the wiring must fork: the PC Engine
    # and NES have no driver at all, and the old unconditional setup() linked the whole
    # driver into them as dead code (~430 B of the PCE's 32 KB cart).
    cond = [l for l in g1.splitlines() if "if platform ==" in l]
    check(bool(cond), "all-vm still forks, so driverless consoles are excluded")
    check(cond and "pce" not in cond[0] and "nes" not in cond[0],
          "the music wiring names no driverless console")
    check(g1.count("function setup()") == 2,
          "driverless consoles get a sound-only setup()")
    # songs but no instruments -> the driver, but no instrument/wave wiring.
    g2 = m.emit_glue_mos(has_songs=True, has_instruments=False)
    check("set_music_driver" in g2 and "set_instruments" not in g2 and "set_waves" not in g2,
          "songs w/o instruments -> driver but no instrument wiring")
    # master disable (music="off" / enabled=False) -> never wire music even with songs.
    g3 = m.emit_glue_mos(has_songs=True, has_instruments=True, music="off")
    check("set_music_driver" not in g3 and "set_sound" in g3, "music=off -> sound only")
    check(g3 == m.emit_glue_mos(has_songs=True, has_instruments=True,
                                audio={"enabled": False}), "off == enabled=False")
    # gb=huge -> the REAL hUGEDriver on the GB family (vm.music_huge over the
    # prebuilt object), while Lynx/SMS-GG still play via vm.music: one arm per
    # driver, forked per platform. It used to wire NOTHING there (a deliberate
    # silent ROM) until the bind shipped.
    g4 = m.emit_glue_mos(has_songs=True, has_instruments=True,
                         audio={"enabled": True, "gb": "huge", "lynx": "vm", "smsgg": "vm"})
    check("if platform ==" in g4 and 'import "vm.music_huge"' in g4
          and "core.set_music_driver(music_huge.play" in g4
          and "core.set_music_driver(music.play" in g4,
          "gb=huge -> hUGEDriver on the GB family, vm.music elsewhere")
    _gbcond = g4.split("if platform ==")[1].split("{")[0]
    check("gameboy" not in _gbcond and "lynx" in _gbcond and "sms" in _gbcond,
          "the wiring branch EXCLUDES GB-family but keeps Lynx/SMS-GG (no vm.music fallback for GB)")
    # legacy scalar music="huge" == the gb=huge audio dict; "auto"/"vm" == the default.
    check(m.emit_glue_mos(has_songs=True, has_instruments=True, music="huge") == g4,
          "legacy scalar music='huge' maps to the gb=huge group config")
    check(m.emit_glue_mos(has_songs=True, has_instruments=True, music="auto")
          == m.emit_glue_mos(has_songs=True, has_instruments=True, music="vm")
          == g1, "legacy music='auto' == 'vm' == the default")


def test_song_library():
    print("[song library: songs.toml -> songs.mos + music_song NAME lowering]")
    # NAME -> index lowering (mirrors the SFX `sound {ref}` expander).
    names = ["intro", "boss"]
    evs = [{"event": "music_song", "song": "boss"},
           {"event": "if", "then": [{"event": "music_song", "song": "intro"}]},
           {"event": "music_song", "song": 3}]
    out = m.song_expand_events(evs, names)
    check(out[0]["song"] == 1, "a NAME ref lowers to its index (boss -> 1)")
    check(out[1]["then"][0]["song"] == 0, "a NAME ref inside an if body is lowered")
    check(out[2]["song"] == 3, "an int index passes through unchanged")
    check(m.song_expand_events([{"event": "music_song", "song": "ghost"}], names)[0]["song"] == 0,
          "a dangling NAME ref lowers to 0")
    # GENERIC channels (Tier 1): a song has `channels` (kinds) + rows of per-channel
    # cells [note, inst, vol] laid out row-major (row*channels + channel).
    defs = [("main", {"channels": ["pulse", "pulse", "wave", "noise"],
                      "rows": [(24, [[13, 1, 15], [0, 0, 0], [5, 0, 12], [30, 3, 15]]),
                               (24, [[17, 2, 12], [13, 1, 10], [8, 0, 15], [0, 0, 0]])]})]
    src = m.emit_songs_mos(defs)
    check('module "songs"' in src and "function cell(" in src
          and "export channels, chkind, cell, frames, rows" in src,
          "songs.mos has the generic driver accessors")
    check("const CHKIND: array[u8, 4] = [0, 0, 1, 2]" in src,
          "CHKIND encodes channel kinds (pulse=0, wave=1, noise=2)")
    # ONE interleaved song blob, per ROW: [frames, (note, inst, vol, fx cmd, fx param)
    # per channel] (stride 1 + channels*5), read through assets.code_byte -- byte-identical
    # indexing resident, but a big imported song (rows, tempo AND effects) STREAMS from the
    # cart on the Lynx (the page-cache seam), leaving the MAIN area.
    check("const CELLS: array[u8, 42] = "
          "[24, 13, 1, 15, 0, 0, 0, 0, 0, 0, 0, 5, 0, 12, 0, 0, 30, 3, 15, 0, 0, "
          "24, 17, 2, 12, 0, 0, 13, 1, 10, 0, 0, 8, 0, 15, 0, 0, 0, 0, 0, 0, 0]" in src,
          "rows interleave frames + per-channel note/inst/vol/fxc/fxp")
    check("return assets.code_byte(CELLS, i + 1 + ch * 5 + field)" in src
          and 'import "platform.assets"' in src,
          "cell() reads the blob through the code-stream seam (Lynx streams a big song)")
    # per-cell EFFECT column (Stage 4): interleaved as cell fields 3 (cmd) / 4 (param).
    fxdefs = [("m", {"channels": ["pulse"], "rows": [(24, [[13, 0, 12]]), (24, [[17, 0, 0]])],
                     "fx": [[[1, 0x37]], [[4, 0x82]]]})]
    fxsrc = m.emit_songs_mos(fxdefs)
    check("const CELLS: array[u8, 12] = [24, 13, 0, 12, 1, 55, 24, 17, 0, 0, 4, 130]" in fxsrc,
          "emit encodes the per-cell effect cmd + param")
    check("CELLOFF[song] + row * (1 + CHN[song] * 5)" in fxsrc,
          "cell()/frames() index the per-row stride (u16 rows)")
    # per-song VOICING flags (bit0 GB pulse2 plays music / bit1 Lynx-SMS prefer bass).
    vsrc = m.emit_songs_mos([("a", {"channels": ["pulse"], "rows": [(24, [[13, 0, 12]])], "flags": 3})])
    check("const FLAGS: array[u8, 1] = [3]" in vsrc and "function flags(song: u8) -> u8" in vsrc
          and "export channels, chkind, cell, frames, rows, flags" in vsrc,
          "songs.mos emits the per-song VOICING flags array + accessor + export")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "scripts"))
        with open(os.path.join(tmp, "scripts", "songs.toml"), "w") as f:
            f.write('[song.a]\nchannels = ["pulse"]\nrows = [[24, 13,0,0]]\n'
                    'gb_all_voices = true\nbass_priority = true\n')
        check(dict(m.load_song_defs(os.path.join(tmp, "scripts")))["a"]["flags"] == 3,
              "gb_all_voices + bass_priority -> flags bitmask 3")
        with open(os.path.join(tmp, "scripts", "songs.toml"), "w") as f:
            f.write('[song.a]\nchannels = ["pulse"]\nrows = [[24, 13,0,0]]\n')
        check(dict(m.load_song_defs(os.path.join(tmp, "scripts")))["a"]["flags"] == 0,
              "no voicing keys -> flags 0 (the default routing)")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "scripts"))
        with open(os.path.join(tmp, "scripts", "songs.toml"), "w") as f:
            f.write('[song.a]\nchannels = ["pulse", "wave"]\nrows = [[24, 13,0,12, 5,0,0]]\n'
                    'fx = [[2, 4, 0, 0]]\n')       # slide on ch0, none on ch1
        d = dict(m.load_song_defs(os.path.join(tmp, "scripts")))["a"]
        check(d.get("fx") == [[[2, 4], [0, 0]]], "load parses the parallel fx (per-channel [cmd, param])")
        with open(os.path.join(tmp, "scripts", "songs.toml"), "w") as f:
            f.write('[song.a]\nchannels = ["pulse"]\nrows = [[24, 13,0,0]]\n')   # no fx key
        d = dict(m.load_song_defs(os.path.join(tmp, "scripts")))["a"]
        check("fx" not in d, "an effect-less song carries NO fx (byte-identical load)")
    # load: channels default to pulse/wave/noise; flat rows -> (frames, cells).
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "scripts"))
        with open(os.path.join(tmp, "scripts", "songs.toml"), "w") as f:
            f.write('[song.a]\nrows = [[24, 13,1,15, 5,0,12, 1,0,0]]\n')  # default 3 channels
        d = dict(m.load_song_defs(os.path.join(tmp, "scripts")))["a"]
        check(d["channels"] == ["pulse", "wave", "noise"], "channels default to pulse/wave/noise")
        fr, cells = d["rows"][0]
        check(fr == 24 and cells == [[13, 1, 15], [5, 0, 12], [1, 0, 0]],
              "a flat row parses into frames + per-channel [note, inst, vol]")
    # PATTERNS + ORDER still flatten (order 0,1,0 -> 1+2+1 = 4 rows).
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "scripts"))
        with open(os.path.join(tmp, "scripts", "songs.toml"), "w") as f:
            f.write('[song.a]\nchannels = ["pulse"]\norder = [0, 1, 0]\n'
                    'patterns = [ [[24, 13,0,0]], [[24, 17,0,0],[24, 20,0,0]] ]\n')
        d = dict(m.load_song_defs(os.path.join(tmp, "scripts")))["a"]
        check([c[0][0] for _fr, c in d["rows"]] == [13, 17, 20, 13],
              "patterns + order flatten (reuse via order)")
    # generate_songs is a no-op with no songs.toml.
    with tempfile.TemporaryDirectory() as tmp:
        check(m.generate_songs(tmp) is None, "no songs.toml -> no songs.mos (opt-in)")
        os.makedirs(os.path.join(tmp, "scripts"))
        with open(os.path.join(tmp, "scripts", "songs.toml"), "w") as f:
            f.write('[song.intro]\nrows = [[24, 13,0,0]]\n')
        p = m.generate_songs(tmp)
        check(p and os.path.isfile(p) and 'module "songs"' in open(p).read(),
              "songs.toml -> src/songs.mos generated")


def test_instrument_library():
    print("[instrument library: instruments.toml -> instruments.mos (Tier 1)]")
    defs = [("bright", {"kind": "pulse", "duty": 1, "wave": 0, "noisefreq": 0x18, "env": 0x71, "vol": 12}),
            ("bassy", {"kind": "wave", "duty": 2, "wave": 2, "noisefreq": 0, "env": 0, "vol": 15}),
            ("kick", {"kind": "noise", "duty": 2, "wave": 0, "noisefreq": 0x54, "env": 0xF3, "vol": 15})]
    src = m.emit_instruments_mos(defs)
    check("const INST_KIND: array[u8, 3] = [0, 1, 2]" in src,
          "INST_KIND encodes pulse=0 / wave=1 / noise=2")
    # P0: pulse duty<<6 = 64; wave preset = 2; noise NR43 = 0x54 = 84.
    check("const INST_P0: array[u8, 3] = [64, 2, 84]" in src,
          "P0 = pulse duty byte / wave preset / noise NR43")
    check("const INST_P1: array[u8, 3] = [0, 0, 243]" in src, "P1 = noise NR42 envelope (0xF3)")
    check("const INST_VOL: array[u8, 3] = [12, 15, 15]" in src, "per-instrument volume")
    # pulse VOLUME ENVELOPE (Tier A #1): `decay` -> the NR12 low nibble (dir bit + period,
    # period = 8 - |decay|); decay 0 -> 0 = byte-identical.
    env = m.emit_instruments_mos([
        ("fade", {"kind": "pulse", "duty": 1, "wave": 0, "noisefreq": 0, "env": 0, "decay": -7, "vol": 10}),
        ("swell", {"kind": "pulse", "duty": 1, "wave": 0, "noisefreq": 0, "env": 0, "decay": 3, "vol": 2}),
        ("flat", {"kind": "pulse", "duty": 1, "wave": 0, "noisefreq": 0, "env": 0, "decay": 0, "vol": 9})])
    check("const INST_P1: array[u8, 3] = [1, 13, 0]" in env,
          "pulse decay -> NR12 env: -7 -> period 1 (fast fade), +3 -> 0x0D (up, period 5), 0 -> 0")
    check("function kind(" in src and "function p0(" in src and "function p1(" in src
          and "export kind, p0, p1, vol, wavebyte" in src, "instruments.mos has the typed accessors")
    # CUSTOM wavetable (audio plan Tier 1, imported hUGE timbre): a 32-nibble `wave_table`
    # packs 2 nibbles/byte into 16 bytes of GB wave RAM, after the default (triangle) row 0.
    wsrc = m.emit_instruments_mos([("saw", {"kind": "wave", "duty": 0, "wave": 0, "noisefreq": 0,
                                            "env": 0, "vol": 15, "wave_table": [15, 0] * 16})])
    check("WAVEDATA" in wsrc and "function wavebyte(i: u8, b: u8) -> u8" in wsrc
          and "return WAVEDATA[i * 16 + b]" in wsrc,
          "instruments.mos emits custom WAVEDATA + the wavebyte accessor")
    check("240, 240, 240, 240, 240, 240, 240, 240, 240, 240, 240, 240, 240, 240, 240, 240" in wsrc,
          "a custom wave_table packs 2 nibbles per byte ([15,0] -> 0xF0 = 240)")
    # a NON-custom wave instrument falls back to its `wave` preset (2 = square = 0xFF*8, 0x00*8).
    psrc = m.emit_instruments_mos([("sq", {"kind": "wave", "duty": 0, "wave": 2, "noisefreq": 0,
                                           "env": 0, "vol": 15})])
    check("255, 255, 255, 255, 255, 255, 255, 255, 0, 0, 0, 0, 0, 0, 0, 0" in psrc,
          "a wave instrument with no custom table expands its `wave` preset (square)")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        check(m.generate_instruments(tmp) is None,
              "no instruments.toml -> no instruments.mos (opt-in)")
        os.makedirs(os.path.join(tmp, "scripts"))
        with open(os.path.join(tmp, "scripts", "instruments.toml"), "w") as f:
            f.write('[instrument.bright]\nkind = "pulse"\nduty = 1\nvol = 12\n')
        p = m.generate_instruments(tmp)
        check(p and os.path.isfile(p), "instruments.toml -> src/instruments.mos generated")
        check(m.instrument_names(os.path.join(tmp, "scripts")) == ["bright"],
              "instrument_names is the ordered index space")
        loaded = dict(m.load_instrument_defs(os.path.join(tmp, "scripts")))["bright"]
        check(loaded["kind"] == "pulse" and loaded["duty"] == 1, "instrument round-trips with kind")


def test_hw_psg():
    print("[hw.psg: the SN76489 port-write primitive lowers on SMS/GG]")
    try:
        from mosaik import MosaikCompiler
    except Exception as e:  # pragma: no cover
        print("  skip: mosaik compiler import failed:", e)
        return
    src = ('module "t" {\n    import "platform.hardware"\n'
           '    function main() { hw.psg(0x9F) }\n    export main\n}\n')
    c_sms = MosaikCompiler().compile(src, platform="sms")
    check("void gbs_hw_psg(uint8_t value) { PSG = value; }" in c_sms,
          "SMS: hw.psg lowers to a PSG (Z80 port) write, not a memory store")
    check("gbs_hw_psg(" in c_sms, "SMS: the call site invokes gbs_hw_psg")
    c_gg = MosaikCompiler().compile(src, platform="gamegear")
    check("GG_SOUND_PAN = 0xFF" in c_gg and "PSG = value" in c_gg,
          "Game Gear: hw.psg also opens the stereo pan (both ears)")
    # GB has no PSG: hw.psg emits no port-write definition (byte-identical preserved;
    # the primitive is SMS/GG-only, guarded behind `if platform` in vm.music).
    c_gb = MosaikCompiler().compile(src, platform="gameboy")
    check("PSG = value" not in c_gb, "GB emits no PSG write (SMS/GG-only primitive)")


def test_music_play_from_song_thread():
    print("[music: MUSIC_PLAY / A_STOP_UPDATE from the thread they kill "
          "(the self-kill + respawn hazard)]")
    # A song whose last event chains into the next song: MUSIC_PLAY kills the
    # exempt thread -- which is the thread EXECUTING the op -- then spawns.
    # The pool must not hand the dying context straight back: the native
    # interpreter keeps that thread's pc in a register for the whole slice
    # and writes it back on exit, so a child spawned INTO `cur` had its entry
    # overwritten by the dead thread's pc (it never ran; the dead thread ran
    # on). The slice must end at the self-kill and the child must land in
    # another context.
    prog = m.Compiler().compile([
        {"name": "main", "events": [{"event": "music_play", "script": "intro"},
                                    {"event": "idle"},
                                    {"event": "idle"},
                                    {"event": "stop"}]},
        {"name": "intro", "events": [
            {"event": "sound_tone", "freq": 100, "frames": 1},
            {"event": "music_play", "script": "loop"},
            {"event": "sound_tone", "freq": 111, "frames": 1},   # must NOT play
            {"event": "stop"}]},
        {"name": "loop", "loop": True, "events": [
            {"event": "sound_tone", "freq": 200, "frames": 1},
            {"event": "wait", "frames": 1}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    # ONE frame: main spawns intro into ctx 1 and idles; the round-robin then
    # reaches ctx 1, which plays 100, chains into `loop` and dies; the chained
    # song must land in ctx 2 (not back in the dying ctx 1) and, being next
    # in the round-robin, plays its first 200 in the same frame.
    vm.frame()
    tones = [e[1] for e in vm.sound_log if e[0] == "tone"]
    check(vm.music_ctx == 2,
          "the chained song landed in a DIFFERENT context than the one that "
          "spawned it (its own, dying one is not free until the slice ends)")
    check(vm.active[1] == 0, "the intro's context is dead")
    check(tones[:2] == [100, 200] and 111 not in tones,
          "nothing after the self-killing MUSIC_PLAY ran; the chained song did")
    for _ in range(4):
        vm.frame()
    tones = [e[1] for e in vm.sound_log if e[0] == "tone"]
    check(tones.count(200) >= 2 and vm.active[vm.music_ctx] == 1,
          "the chained song is alive and looping")

    # The same shape through A_STOP_UPDATE: an On Update script that stops its
    # OWN actor's update thread kills the executing thread; the events after
    # it must not run, and a THREAD spawned right after must not be lost.
    p2 = m.Compiler().compile([
        {"name": "main", "events": [{"event": "wait", "frames": 10},
                                    {"event": "stop"}]},
        {"name": "patrol", "events": [
            {"event": "set_var", "var": "ticks", "expr": "ticks + 1"},
            {"event": "actor_stop_update", "actor": 2},
            {"event": "set_var", "var": "ticks", "expr": "ticks + 100"},
            {"event": "stop"}]}])
    vm2 = m.RefVM(p2.code, entry=p2.entry)
    uh = vm2._spawn(p2.offsets["patrol"])
    vm2.set_self(uh, 2)
    vm2.frame()
    check(vm2.heap[p2.variables["ticks"]] == 1,
          "an On Update script that stops its own actor ends at that op "
          "(the events after it never run)")
    check(vm2.active[uh] == 0 and vm2.actors[2].updating == 0,
          "the update thread is dead and the actor is marked not-updating")
