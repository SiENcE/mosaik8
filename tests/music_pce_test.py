"""vm.music on the PC Engine (the HuC6280 PSG branch), measured off the ROM.

The branch was ported from the vm-megademo driver and then corrected; every
check below is one of the defects that port had, measured on the captured
AUDIO of the mednafen_pce_fast core:

  * ROUTING BY KIND: noise channels take PSG 5 then 4 (the only voices with a
    noise mode), pulse / wave fill PSG 1..3 (+ 4 when free), a channel past the
    free voices is DROPPED. The port mapped channel N to PSG N + 1, so a wave
    channel played a square at the written pitch and a noise channel a tone;
  * a wave channel plays the instrument's own TABLE (the port loaded one fixed
    bass wave) and noise the instrument's NR43 CLOCK (the port took 3 rates
    from the note);
  * the software ENVELOPE steps once per `pace` frames. Written as
    `m_pct[c] >= (e & 0x07)` it stepped on EVERY frame on this console (a
    15-step fade took 15 frames, not 75);
  * a NOTE CUT on a cell that also starts a note (the port's trigger reset the
    cut, so only a cut on a rest worked), and RESUME re-sounding a held note.

    python tests/music_pce_test.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mosaik_vm                                      # noqa: E402

passed = failed = 0
RATE = 44100
HOP = 735                                             # samples per video frame


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- %s" % (detail,)) if detail else ""))


def _core():
    p = os.path.join(ROOT, "emu", "libretro", "mednafen_pce_fast_libretro.dll")
    return p if os.path.isfile(p) else None


INSTRUMENTS = """
[instrument.sq]
kind = "pulse"
duty = 2
vol = 15

[instrument.fade]
kind = "pulse"
duty = 2
decay = -3
vol = 15

[instrument.tri]
kind = "wave"
wave = 0
vol = 15

[instrument.square]
kind = "wave"
wave = 2
vol = 15

[instrument.dark]
kind = "noise"
noisefreq = 0x66
env = 0xF0
vol = 15

[instrument.bright]
kind = "noise"
noisefreq = 0x11
env = 0xF0
vol = 15
"""
# instrument indices (1-based, the file's order)
SQ, FADE, TRI, SQUARE, DARK, BRIGHT = 1, 2, 3, 4, 5, 6


def _fixture(tmp, name, song, events, insts=INSTRUMENTS, tables=False):
    root = os.path.join(tmp, name)
    shutil.copytree(os.path.join(ROOT, "projects", "vm-music"), root,
                    ignore=shutil.ignore_patterns("build"))
    sd = os.path.join(root, "scripts")
    for f in os.listdir(sd):
        if f.endswith(".evt.toml"):
            os.remove(os.path.join(sd, f))
    with open(os.path.join(sd, "main.evt.toml"), "w", encoding="utf-8") as f:
        f.write('[[script]]\nname = "main"\nevents = [\n%s\n]\n'
                % ",\n".join("  " + e for e in events))
    with open(os.path.join(sd, "songs.toml"), "w", encoding="utf-8") as f:
        f.write(song)
    with open(os.path.join(sd, "instruments.toml"), "w", encoding="utf-8") as f:
        f.write(insts)
    prog = mosaik_vm.compile_path(sd)
    with open(os.path.join(root, "src", "scripts.mos"), "w", encoding="utf-8") as f:
        f.write(prog.to_scripts_mos())
    mosaik_vm.generate_songs(root)
    mosaik_vm.generate_instruments(root)
    if tables:
        # vm-music's shell wires the driver BY HAND, so it registers the
        # subpattern tables itself (the line the build reads VM_MUSIC_SUBPAT off)
        mp = os.path.join(root, "src", "main.mos")
        with open(mp, encoding="utf-8") as f:
            m = f.read()
        anchor = "        music.set_waves(instruments.wavebyte)"
        m = m.replace(anchor, anchor + "\n        music.set_subpatterns(instruments.sublen, "
                      "instruments.subrow)")
        with open(mp, "w", encoding="utf-8") as f:
            f.write(m)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "pce", root], capture_output=True, text=True)
    rom = os.path.join(root, "build", "pce", "vm-music.pce")
    if r.returncode or not os.path.isfile(rom):
        check("%s: the fixture links" % name, False,
              ((r.stdout or "") + (r.stderr or ""))[-600:])
        return None
    return rom


def _capture(rom, out, frames):
    """Run the ROM and save its mono audio as .npy. In a CHILD process: a
    libretro core is a process singleton."""
    import numpy as np
    lib = os.path.join(ROOT, "emu", "libretro")
    sys.path.insert(0, lib)
    import run_lynx                                        # noqa: F401 (the NULL-frame patch)
    from libretro import SessionBuilder
    from libretro.drivers.path import ExplicitPathDriver
    from libretro.drivers.audio import ArrayAudioDriver
    from libretro.api.av import AvEnableFlags
    core = _core()
    audio = ArrayAudioDriver()
    b = (SessionBuilder.defaults(core).with_content(rom)
         .with_paths(ExplicitPathDriver(corepath=core, system=lib, save=lib, assets=lib,
                                        playlist=lib))
         .with_audio(audio).with_perf(None)
         .with_av_mask(AvEnableFlags.VIDEO | AvEnableFlags.AUDIO))
    ends = []
    with b.build() as sess:
        for _ in range(frames):
            sess.run()
            ends.append(len(audio._buffer) // 2)       # where each video frame's audio ends
        w = np.array(audio._buffer, dtype=np.float32).reshape(-1, 2)
    np.savez(out, w=w, ends=np.array(ends))


def _stereo(rom, frames=420):
    """(left/right audio, DC removed, as an (n, 2) array; the sample index where
    each video frame's audio ends). The PSG is stereo: panning is per voice."""
    out = rom + ".npz"
    subprocess.run([sys.executable, os.path.abspath(__file__), "--capture", rom, out,
                    str(frames)], capture_output=True, text=True)
    if not os.path.isfile(out):
        return None, None
    import numpy as np
    d = np.load(out)
    w = d["w"].astype(np.float64)
    k = np.ones(801) / 801
    for i in (0, 1):
        w[:, i] -= np.convolve(w[:, i], k, "same")             # drop the DC drift
    return w, d["ends"]


def _audio(rom, frames=420):
    w, _ends = _stereo(rom, frames)
    return None if w is None else w.mean(axis=1)


def _spectrum(x):
    import numpy as np
    x = x - x.mean()
    p = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2 + 1e-9
    return np.fft.rfftfreq(len(x), 1.0 / RATE), p


def _band_db(f, p, hz):
    """Energy within 3 % of `hz`, in dB over the spectrum's median."""
    import numpy as np
    m = (f > hz * 0.97) & (f < hz * 1.03)
    return 10 * np.log10(p[m].max() / np.median(p[(f > 50) & (f < 8000)]))


def _flatness(f, p):
    import numpy as np
    s = (f > 50) & (f < 10000)
    return float(np.exp(np.log(p[s]).mean()) / p[s].mean())


def _frame_rms(a):
    import numpy as np
    n = len(a) // HOP
    return np.sqrt((a[:n * HOP].reshape(n, HOP) ** 2).mean(axis=1))


def _hz(n):
    return 65.406 * 2 ** ((n - 1) / 12.0)                      # note 1 = C2


def test_routing(tmp):
    print("\n[ROUTING by kind: pulse, pulse, wave, pulse, noise, noise]")
    song = ('[song.s]\nchannels = ["pulse", "pulse", "wave", "pulse", "noise", "noise"]\n'
            'rows = [[60, 37,%d,0, 41,%d,0, 30,%d,0, 44,%d,0, 40,%d,0, 60,%d,0]]\n'
            % (SQ, SQ, TRI, SQ, DARK, BRIGHT))
    play = '{ event = "music_song", song = "s" }'
    melodic = _fixture(tmp, "route_mel", song,
                       [play, '{ event = "music_mute", mask = 48 }'])   # noise channels off
    noise5 = _fixture(tmp, "route_n5", song,
                      [play, '{ event = "music_mute", mask = 31 }'])    # channel 5 alone
    if not melodic or not noise5:
        return
    a = _audio(melodic)
    f, p = _spectrum(a[RATE * 3:RATE * 3 + 32768])
    lv = {hz: _band_db(f, p, hz) for hz in (_hz(37), _hz(41), _hz(18), _hz(30), _hz(44))}
    print("    peaks over the median (dB): %s" % ", ".join("%.0f Hz %.0f" % kv for kv in lv.items()))
    check("pulse channels 0 + 1 sound at their notes (523 / 659 Hz)",
          lv[_hz(37)] > 30 and lv[_hz(41)] > 30, lv)
    check("the WAVE channel plays an octave below its note (175 Hz, not 349)",
          lv[_hz(18)] > 30 and lv[_hz(18)] - lv[_hz(30)] > 10, lv)
    check("the 4th melodic channel is DROPPED (no 784 Hz: PSG 4 went to a noise channel)",
          lv[_hz(44)] < 20, lv)
    b = _audio(noise5)
    f2, p2 = _spectrum(b[RATE * 3:RATE * 3 + 32768])
    fl = _flatness(f2, p2)
    print("    channel 5 alone: rms %.0f, spectral flatness %.3f" % (b.std(), fl))
    check("the SECOND noise channel plays NOISE on its own voice (flat spectrum, not a tone)",
          b.std() > 500 and fl > 0.02, (b.std(), fl))


def test_timbres(tmp):
    print("\n[the instrument's own WAVE table and NOISE clock]")
    waves = _fixture(tmp, "waves", '[song.s]\nchannels = ["wave"]\nrows = [[60, 49,%d,0], [60, 49,%d,0]]\n'
                     % (TRI, SQUARE), ['{ event = "music_song", song = "s" }'])
    noise = _fixture(tmp, "noise", '[song.s]\nchannels = ["noise"]\nrows = [[30, 40,%d,0], [30, 40,%d,0]]\n'
                     % (DARK, BRIGHT), ['{ event = "music_song", song = "s" }'])
    if not waves or not noise:
        return
    import numpy as np
    a = _audio(waves)
    h = []                                       # 3rd harmonic over the fundamental, per window
    for s in range(RATE, len(a) - 4096, HOP):
        f, p = _spectrum(a[s:s + 4096])
        h.append(_band_db(f, p, 3 * _hz(37)) - _band_db(f, p, _hz(37)))
    h = np.sort(np.array(h))
    q = len(h) // 4
    spread = h[-q:].mean() - h[:q].mean()
    print("    3rd harmonic re fundamental: triangle-ish %.1f dB, square-ish %.1f dB"
          % (h[:q].mean(), h[-q:].mean()))
    check("the two wave instruments sound DIFFERENT (triangle vs square table, > 6 dB)",
          spread > 6, spread)
    b = _audio(noise)
    med = []
    for s in range(RATE, len(b) - 4096, HOP):
        f, p = _spectrum(b[s:s + 4096])
        m = f > 60
        c = np.cumsum(p[m]) / p[m].sum()
        med.append(f[m][np.searchsorted(c, 0.5)])
    med = np.sort(np.array(med))
    q = len(med) // 4
    ratio = med[-q:].mean() / med[:q].mean()
    print("    noise median frequency: dark %.0f Hz, bright %.0f Hz" % (med[:q].mean(), med[-q:].mean()))
    check("the NR43 clock reaches the PSG (0x11 is > 4x brighter than 0x66, same note)",
          ratio > 4, ratio)


def test_envelope_cut_resume(tmp):
    print("\n[envelope pace, note cut on a note, resume]")
    # the fading note on song channel 1, beside a silent channel 0 (the fault
    # was measured on channel 1 of a 4-channel song)
    env = _fixture(tmp, "env", '[song.s]\nchannels = ["pulse", "pulse"]\n'
                   'rows = [[180, 0,0,0, 37,%d,0], [60, 0,0,0, 0,0,0]]\n'
                   % FADE, ['{ event = "music_song", song = "s" }'])
    cut = _fixture(tmp, "cut", '[song.s]\nchannels = ["pulse"]\nrows = [[30, 37,%d,0]]\nfx = [[6, 5]]\n'
                   % SQ, ['{ event = "music_song", song = "s" }'])
    hold = _fixture(tmp, "hold", '[song.s]\nchannels = ["pulse"]\nrows = [[240, 37,%d,0], [240, 0,0,0]]\n'
                    % SQ, ['{ event = "music_song", song = "s" }', '{ event = "wait", frames = 60 }',
                           '{ event = "music_pause" }', '{ event = "wait", frames = 30 }',
                           '{ event = "music_resume" }'])
    if not env or not cut or not hold:
        return
    import numpy as np
    e = _frame_rms(_audio(env))
    top = e.max()
    start = int(np.argmax(e > 0.7 * top))                  # the note's first frame
    # each volume step is 3 dB on the PSG (the driver writes 2 * vol + 1 on its
    # 1.5 dB scale), so 6 steps = 18 dB = an eighth of the amplitude
    six = start + int(np.argmax(e[start:] < top / 7.94))
    print("    decay -3 (pace 5): 6 volume steps (18 dB) took %d frames" % (six - start))
    check("the software ENVELOPE steps once per `pace` frames (6 steps x 5 ~ 30 frames)",
          22 <= six - start <= 40, six - start)
    a = _audio(cut)
    act = np.convolve(np.abs(a), np.ones(88) / 88, "same") > 0.1 * np.abs(a).max()
    ed = np.flatnonzero(np.diff(act.astype(int)))
    runs = [(ed[i + 1] - ed[i]) / float(HOP) for i in range(len(ed) - 1) if act[ed[i] + 1]]
    print("    note + cut 5: voiced runs %s frames" % ["%.1f" % r for r in runs[-4:]])
    check("a NOTE CUT on a cell that starts a note ends it after 5 frames",
          len(runs) >= 4 and all(4.5 <= r <= 6.0 for r in runs[-4:]), runs[-4:])
    h = _frame_rms(_audio(hold))
    on = h > 0.3 * h.max()
    s = int(np.argmax(on))
    parts = (on[s + 5:s + 55].mean(), on[s + 65:s + 85].mean(), on[s + 100:s + 200].mean())
    print("    hold: playing %.0f%%, paused %.0f%%, after resume %.0f%%" % tuple(100 * x for x in parts))
    check("PAUSE silences and RESUME re-sounds the held note (the next row is a rest)",
          parts[0] > 0.9 and parts[1] < 0.1 and parts[2] > 0.9, parts)


def _frame_pitch(a, cands):
    """Per video frame: which of the candidate frequencies holds the most energy
    in that frame's own 735 samples (zero-padded for resolution: an arpeggio
    changes pitch EVERY frame, so a longer window averages it away), or 0."""
    import numpy as np
    out = []
    win = np.hanning(HOP)
    f = np.fft.rfftfreq(8192, 1.0 / RATE)
    for s in range(0, len(a) - HOP, HOP):
        x = a[s:s + HOP]
        if x.std() < 300:
            out.append(0)
            continue
        p = np.abs(np.fft.rfft((x - x.mean()) * win, 8192)) ** 2
        out.append(max(cands, key=lambda hz: p[(f > hz * 0.97) & (f < hz * 1.03)].max()))
    return out


def test_effects_and_voicing(tmp):
    print("\n[arpeggio, pattern jump, volume on a rest, music-only, bass priority]")
    import numpy as np
    play = '{ event = "music_song", song = "s" }'
    arp = _fixture(tmp, "arp", '[song.s]\nchannels = ["pulse"]\nrows = [[30, 37,%d,0]]\n'
                   'fx = [[1, 0x47]]\n' % SQ, [play])
    jump = _fixture(tmp, "jump", '[song.s]\nchannels = ["pulse"]\nrows = [[30, 37,%d,0], '
                    '[30, 41,%d,0], [30, 44,%d,0]]\nfx = [[0, 0], [10, 0], [0, 0]]\n'
                    % (SQ, SQ, SQ), [play])
    rest = _fixture(tmp, "rest", '[song.s]\nchannels = ["pulse"]\nrows = [[60, 37,%d,0], '
                    '[120, 0,0,15]]\n' % FADE, [play])
    if not arp or not jump or not rest:
        return
    trio = (_hz(37), _hz(41), _hz(44))              # 523 / 659 / 784 Hz
    pa = [x for x in _frame_pitch(_audio(arp), trio)[60:300] if x]
    share = [pa.count(hz) / float(len(pa) or 1) for hz in trio]
    print("    arpeggio 0x47 on 523 Hz: %s of the frames at 523 / 659 / 784 Hz"
          % " / ".join("%.0f%%" % (100 * x) for x in share))
    check("ARPEGGIO cycles base, +4, +7 (a third of the frames each)",
          all(0.25 <= x <= 0.42 for x in share), share)
    pj = [x for x in _frame_pitch(_audio(jump), trio)[60:360] if x]
    print("    jump: 523 Hz %d frames, 659 Hz %d, 784 Hz (row 2) %d"
          % tuple(pj.count(hz) for hz in trio))
    check("PATTERN JUMP (10 0) on row 1 loops rows 0-1 (row 2 never plays)",
          pj.count(_hz(44)) == 0 and pj.count(_hz(37)) > 80 and pj.count(_hz(41)) > 80,
          [pj.count(hz) for hz in trio])
    e = _frame_rms(_audio(rest))
    top = e.max()
    start = int(np.argmax(e > 0.7 * top))
    faded, back = e[start + 55], e[start + 70:start + 170]
    print("    volume on a rest: %.0f%% of full before it, %.0f%%..%.0f%% after"
          % (100 * faded / top, 100 * back.min() / top, 100 * back.max() / top))
    check("a VOLUME on a rest re-sounds the faded note at full level, envelope OFF",
          faded < 0.5 * top and back.min() > 0.8 * top, (faded / top, back.min() / top))

    four = ('[song.s]\nchannels = ["pulse", "pulse", "pulse", "pulse", "noise", "noise"]\n%s'
            'rows = [[60, 37,%d,0, 41,%d,0, 30,%d,0, 44,%d,0, 40,%d,0, 60,%d,0]]\n')
    mute = '{ event = "music_mute", mask = 48 }'
    game = _fixture(tmp, "game4", four % ("", SQ, SQ, SQ, SQ, DARK, BRIGHT), [play, mute])
    only = _fixture(tmp, "only4", four % ("gb_all_voices = true\n", SQ, SQ, SQ, SQ, DARK,
                                          BRIGHT), [play, mute])
    bass = _fixture(tmp, "bass4", ('[song.s]\nchannels = ["pulse", "pulse", "pulse", "wave", '
                                   '"noise", "noise"]\nbass_priority = true\n'
                                   'rows = [[60, 37,%d,0, 41,%d,0, 44,%d,0, 30,%d,0, 40,%d,0, '
                                   '60,%d,0]]\n') % (SQ, SQ, SQ, TRI, DARK, BRIGHT), [play, mute])
    if not game or not only or not bass:
        return
    lv = {}
    for name, rom in (("game", game), ("only", only), ("bass", bass)):
        a = _audio(rom)
        f, p = _spectrum(a[RATE * 3:RATE * 3 + 32768])
        lv[name] = {hz: _band_db(f, p, hz) for hz in (_hz(44), _hz(18), _hz(30))}
    print("    4th pulse (784 Hz): game %.0f dB, music-only %.0f dB; bass priority: wave "
          "(175 Hz) %.0f dB, 3rd pulse (784 Hz) %.0f dB"
          % (lv["game"][_hz(44)], lv["only"][_hz(44)], lv["bass"][_hz(18)], lv["bass"][_hz(44)]))
    check("GAME mode drops the 4th melodic channel (PSG 0 stays the SFX's)",
          lv["game"][_hz(44)] < 20, lv["game"])
    check("MUSIC-ONLY plays it on PSG 0", lv["only"][_hz(44)] > 30, lv["only"])
    check("BASS PRIORITY gives the wave a voice and drops the 3rd pulse instead",
          lv["bass"][_hz(18)] > 30 and lv["bass"][_hz(44)] < 20, lv["bass"])


# --------------------------------------------------------------------------
# The per-frame EFFECTS (plan 3.1). A parameter means what the tracker
# documents (audio_caps): a slide / portamento speed counts GB period units a
# frame (G = 2048 - the GB frequency register, f = 131072 / G), so every
# expected pitch below comes from the GB's own formula, not from the PSG
# driver's arithmetic: the check is the calibration too.
# --------------------------------------------------------------------------
def _gb_hz(g):
    return 131072.0 / g


G37 = 250                                            # the GB period of note 37 (523 Hz)


def _frame_chunks(w, ends):
    """Each video frame's own samples (None for a frame with too few)."""
    out, last = [], 0
    for e in ends:
        out.append(w[last:e] if e - last >= 600 else None)
        last = e
    return out


def _peak_hz(x, lo=150.0, hi=3000.0, floor=300.0):
    """The strongest frequency in one frame's samples (zero-padded FFT with a
    parabolic peak), or 0 for a frame quieter than `floor`."""
    import numpy as np
    if x is None or x.std() < floor:
        return 0.0
    n = 16384
    p = np.abs(np.fft.rfft((x - x.mean()) * np.hanning(len(x)), n))
    f = np.fft.rfftfreq(n, 1.0 / RATE)
    band = np.flatnonzero((f > lo) & (f < hi))
    k = band[np.argmax(p[band])]
    a, b, c = np.log(p[k - 1:k + 2] + 1e-9)
    den = a - 2 * b + c
    d = 0.5 * (a - c) / den if den else 0.0
    return float((k + d) * RATE / n)


def _rms(x):
    import numpy as np
    return float(np.sqrt((x ** 2).mean())) if x is not None else 0.0


def _track(rom, frames=420, skip=60):
    """Per video frame from frame `skip`: the mono pitch (Hz, 0 = quiet) and RMS."""
    w, ends = _stereo(rom, frames)
    if w is None:
        return [], []
    ch = _frame_chunks(w.mean(axis=1), ends)[skip:]
    return [_peak_hz(x) for x in ch], [_rms(x) for x in ch]


def _near(v, want, tol):
    return want * (1 - tol) <= v <= want * (1 + tol)


def test_pitch_effects(tmp):
    print("\n[slide up / down, vibrato, portamento: GB period units, measured in Hz]")
    import numpy as np
    play = '{ event = "music_song", song = "s" }'
    one = '[song.s]\nchannels = ["pulse"]\nrows = [[60, 37,%d,0]]\nfx = [[%%d, %%d]]\n' % SQ
    up = _fixture(tmp, "slide_up", one % (2, 2), [play])
    dn = _fixture(tmp, "slide_dn", one % (3, 2), [play])
    vib = _fixture(tmp, "vibrato", '[song.s]\nchannels = ["pulse"]\nrows = [[64, 37,%d,0]]\n'
                   'fx = [[4, 0x14]]\n' % SQ, [play])
    por = _fixture(tmp, "porta", '[song.s]\nchannels = ["pulse"]\nrows = [[30, 37,%d,0], '
                   '[60, 44,%d,0]]\nfx = [[0, 0], [7, 4]]\n' % (SQ, SQ), [play])
    if not up or not dn or not vib or not por:
        return
    # SLIDE 2: two units a frame from the row's note, on ticks 1..59 of each
    # 60-frame row, so the frames spread over G = 250 -/+ 2t
    for name, rom, sign in (("UP", up, -1), ("DOWN", dn, +1)):
        pt = [x for x in _track(rom)[0] if x]
        if not pt:
            check("SLIDE %s: audio captured" % name, False)
            continue
        lo, med, hi = np.percentile(pt, 1), np.median(pt), np.percentile(pt, 99)
        ends = sorted((_gb_hz(G37), _gb_hz(G37 + sign * 2 * 59)))
        mid = _gb_hz(G37 + sign * 2 * 30)
        print("    slide %s 2: %.0f .. %.0f Hz (median %.0f); the GB formula %.0f .. %.0f "
              "(median %.0f)" % (name.lower(), lo, hi, med, ends[0], ends[1], mid))
        check("SLIDE %s moves the pitch 2 GB period units a frame (ends + median within 4 %%)"
              % name, _near(lo, ends[0], 0.04) and _near(hi, ends[1], 0.04)
              and _near(med, mid, 0.04), (lo, med, hi, ends, mid))
    # VIBRATO 0x14: rate 1, depth 4 -> the GB lead's triangle, 16 x (0 1 2 1)
    # units up then down over 8 frames: peaks at G = 250 -/+ 32
    pt = [x for x in _track(vib)[0] if x]
    if pt:
        lo, med, hi = np.percentile(pt, 2), np.median(pt), np.percentile(pt, 98)
        print("    vibrato 0x14: %.0f / %.0f / %.0f Hz; the GB formula %.0f / %.0f / %.0f"
              % (lo, med, hi, _gb_hz(G37 + 32), _gb_hz(G37), _gb_hz(G37 - 32)))
    check("VIBRATO swings +-32 GB units around the note (peaks within 3 %, centred)",
          pt and _near(lo, _gb_hz(G37 + 32), 0.03) and _near(hi, _gb_hz(G37 - 32), 0.03)
          and _near(med, _gb_hz(G37), 0.03), pt and (lo, med, hi))
    # PORTAMENTO 4, note 37 -> 44: G 250 -> 167 at 4 units a frame, so the
    # glide sits strictly between G 240.5 and 172.5 on ticks 3..19 (17 frames)
    pt = _track(por, frames=480)[0]
    lo_hz, hi_hz = _gb_hz(240.5), _gb_hz(172.5)
    glides = []
    for i in range(len(pt) - 1):                   # the last frame at 523 Hz before a rise
        if _near(pt[i], _gb_hz(G37), 0.02) and pt[i + 1] > pt[i] * 1.01:
            run = 0
            for x in pt[i + 1:]:
                if lo_hz < x < hi_hz:
                    run += 1
                elif x >= hi_hz or x <= _gb_hz(G37) * 1.01:
                    break
            glides.append(run)
    print("    portamento 4 (523 -> 784 Hz): frames between %.0f and %.0f Hz, per glide: %s"
          % (lo_hz, hi_hz, glides))
    # (a 1:1 GB-to-PSG unit scale glides in 14: the window is the calibration)
    check("PORTAMENTO glides onto the new note at 4 GB units a frame (17 frames between)",
          len(glides) >= 3 and all(16 <= g <= 18 for g in glides), glides)
    # The row AFTER an arpeggio / vibrato puts the held note back: rows of 30
    # frames, arp 0x47 / rest / vibrato 0x14 / rest. Each rest row is a run of
    # 30 frames at 523 Hz; left where the last frame swung, the arpeggio's rest
    # sits at +7 (frame 29 is its third phase) and the vibrato's below the note.
    back = _fixture(tmp, "restore", '[song.s]\nchannels = ["pulse"]\nrows = [[30, 37,%d,0], '
                    '[30, 0,0,0], [30, 37,%d,0], [30, 0,0,0]]\nfx = [[1, 0x47], [0, 0], '
                    '[4, 0x14], [0, 0]]\n' % (SQ, SQ), [play])
    pt = _track(back, frames=540)[0] if back else []
    runs, run = [], 0
    for x in pt + [0.0]:
        if _near(x, _gb_hz(G37), 0.015):
            run += 1
        else:
            if run:
                runs.append(run)
            run = 0
    long_runs = [r for r in runs if r >= 28]
    print("    arp / rest / vibrato / rest: runs of 28+ frames at 523 Hz: %s" % long_runs)
    check("the row AFTER an arpeggio or a vibrato holds the base note (both rest rows)",
          len(long_runs) >= 6, long_runs)


def test_volume_and_panning(tmp):
    print("\n[volume slide, panning]")
    play = '{ event = "music_song", song = "s" }'
    vs = _fixture(tmp, "volslide", '[song.s]\nchannels = ["pulse"]\nrows = [[60, 37,%d,0], '
                  '[60, 0,0,0]]\nfx = [[5, 0x01], [5, 0x10]]\n' % SQ, [play])
    pan = _fixture(tmp, "pan", '[song.s]\nchannels = ["pulse", "pulse"]\n'
                   'rows = [[60, 37,%d,0, 44,%d,0]]\nfx = [[8, 1, 8, 2]]\n' % (SQ, SQ), [play])
    if not vs or not pan:
        return
    # Row 0 starts the note at 15 and slides it down one PSG step (3 dB) a
    # frame to silence; row 1, a REST, slides the held note back up from 0.
    # Each slide is a run of consecutive frames moving >= 1.5 dB the same way:
    # 15 frames each (14 steps between levels + the step to or from off).
    e = _track(vs, frames=480)[1]
    top = max(e) if e else 0
    falls, rises, run, way = [], [], 0, 0
    for a, b in zip(e, e[1:]):
        now = -1 if a > 0.005 * top and b < a * 0.85 else \
            (1 if b > 0.005 * top and b > a * 1.18 else 0)
        if now and now == way:
            run += 1
            continue
        if run >= 3:
            (falls if way < 0 else rises).append(run)
        run, way = (1, now) if now else (0, 0)
    print("    volume slide 0x01 then 0x10 on a rest: falling runs %s, rising runs %s frames"
          % (falls, rises))
    check("VOLUME SLIDE down steps the level once a frame (full to off in ~15 frames)",
          len(falls) >= 2 and all(13 <= r <= 16 for r in falls), falls)
    check("VOLUME SLIDE up on a REST row swells the held note back from silence (~15 frames)",
          len(rises) >= 2 and all(13 <= r <= 16 for r in rises), rises)
    w, _ends = _stereo(pan)
    if w is None:
        check("PANNING: audio captured", False)
        return
    lv = {}
    for side, i in (("L", 0), ("R", 1)):
        f, p = _spectrum(w[RATE * 3:RATE * 3 + 32768, i])
        lv[side] = (_band_db(f, p, _hz(37)), _band_db(f, p, _hz(44)))
    print("    pan: left 523 Hz %.0f dB / 784 Hz %.0f dB, right %.0f / %.0f dB"
          % (lv["L"] + lv["R"]))
    check("PANNING puts channel 0 (8 1) on the RIGHT and channel 1 (8 2) on the LEFT",
          lv["L"][1] - lv["L"][0] > 20 and lv["R"][0] - lv["R"][1] > 20, lv)


SUB_INSTRUMENTS = INSTRUMENTS + """
[instrument.tab]
kind = "pulse"
duty = 2
vol = 15
[[instrument.tab.subpattern]]
row = 0
pitch = 0
vol = 15
[[instrument.tab.subpattern]]
row = 8
pitch = 12
[[instrument.tab.subpattern]]
row = 12
vol = 4
[[instrument.tab.subpattern]]
row = 15
jump = 0

[instrument.rpan]
kind = "pulse"
duty = 2
vol = 15
[[instrument.rpan.subpattern]]
row = 0
fx = 8
param = 1

[instrument.ntab]
kind = "noise"
noisefreq = 0xFF
env = 0xF0
vol = 15
[[instrument.ntab.subpattern]]
row = 0
pitch = 0
vol = 15
[[instrument.ntab.subpattern]]
row = 8
pitch = 24
[[instrument.ntab.subpattern]]
row = 12
vol = 4
[[instrument.ntab.subpattern]]
row = 15
jump = 0
"""
TAB, RPAN, NTAB = 7, 8, 9


def test_subpatterns(tmp):
    print("\n[instrument SUBPATTERNS: pitch / volume / pan rows, melodic and noise]")
    import numpy as np
    play = '{ event = "music_song", song = "s" }'
    mel = _fixture(tmp, "sub_mel", '[song.s]\nchannels = ["pulse", "pulse"]\n'
                   'rows = [[240, 37,%d,0, 44,%d,0]]\n' % (TAB, RPAN), [play],
                   insts=SUB_INSTRUMENTS, tables=True)
    noi = _fixture(tmp, "sub_noise", '[song.s]\nchannels = ["noise"]\nrows = [[240, 30,%d,0]]\n'
                   % NTAB, [play], insts=SUB_INSTRUMENTS, tables=True)
    if not mel or not noi:
        return
    c = open(os.path.join(tmp, "sub_mel", "build", "pce", "vm-music.c"), encoding="utf-8",
             errors="replace").read()
    check("the table code is compiled in when a table plays", "vm_music_sp_tick" in c)
    share = lambda part, whole: len(part) / float(len(whole) or 1)  # noqa: E731
    # One 16-tick loop on channel 0 (note 37): rows 0..7 base and loud, 8..11 an
    # octave up and loud (a PITCH row alone), 12..15 an octave up and quiet (a
    # VOLUME row alone). Channel 1's table pans it RIGHT, so the LEFT side
    # carries channel 0 alone.
    w, ends = _stereo(mel, 480)
    if w is None:
        check("subpatterns: audio captured", False)
        return
    left = _frame_chunks(w[:, 0], ends)[120:]
    pt = [_peak_hz(x, hi=1300.0, floor=20.0) for x in left]     # vol 4 is 33 dB down
    rm = [_rms(x) for x in left]
    cut = (min(rm) + max(rm)) / 2.0
    loud = [p for p, r in zip(pt, rm) if r > cut]
    quiet = [p for p, r in zip(pt, rm) if 0 < r <= cut]
    octave = lambda v: [p for p in v if _near(p, 2 * _hz(37), 0.04)]  # noqa: E731
    print("    melodic: loud %d frames (%.0f%% an octave up), quiet %d (%.0f%% up)"
          % (len(loud), 100 * share(octave(loud), loud), len(quiet),
             100 * share(octave(quiet), quiet)))
    check("a table VOLUME row takes the level down for a quarter of the loop",
          0.18 <= share(quiet, quiet + loud) <= 0.32, (len(quiet), len(loud)))
    check("a table PITCH row alone retunes: a third of the LOUD frames an octave up, every "
          "quiet one", 0.25 <= share(octave(loud), loud) <= 0.42
          and share(octave(quiet), quiet) > 0.9,
          (share(octave(loud), loud), share(octave(quiet), quiet)))
    fl, pl = _spectrum(w[RATE * 3:RATE * 3 + 32768, 0])
    fr, pr = _spectrum(w[RATE * 3:RATE * 3 + 32768, 1])
    print("    pan row: channel 1 (784 Hz) left %.0f dB, right %.0f dB"
          % (_band_db(fl, pl, _hz(44)), _band_db(fr, pr, _hz(44))))
    check("a table PAN row pans (channel 1's row 0 = right: its 784 Hz is gone on the left)",
          _band_db(fl, pl, _hz(44)) < 20 and _band_db(fr, pr, _hz(44)) > 30,
          (_band_db(fl, pl, _hz(44)), _band_db(fr, pr, _hz(44))))
    # The NOISE table (a pitched-noise instrument): rows 0..7 loud at note 30's
    # clock, 8..11 loud and brighter (+24: a PITCH row re-derives the clock),
    # 12..15 quiet (a VOLUME row)
    w, ends = _stereo(noi, 480)
    if w is None:
        check("noise subpattern: audio captured", False)
        return
    med, nr = [], []
    for x in _frame_chunks(w.mean(axis=1), ends)[120:]:
        if x is None:
            continue
        q = np.abs(np.fft.rfft(x - x.mean())) ** 2
        fq = np.fft.rfftfreq(len(x), 1.0 / RATE)
        med.append(fq[np.searchsorted(np.cumsum(q) / q.sum(), 0.5)])
        nr.append(_rms(x))
    cut = (min(nr) + max(nr)) / 2.0
    loud = [m for m, r in zip(med, nr) if r > cut]
    split = (min(loud) + max(loud)) / 2.0
    bright = [m for m in loud if m > split]
    dark = [m for m in loud if m <= split] or [1.0]
    print("    noise: loud %d of %d frames, %d of them bright (median %.0f vs %.0f Hz)"
          % (len(loud), len(med), len(bright), np.mean(bright) if bright else 0, np.mean(dark)))
    check("a NOISE table: a PITCH row re-derives the pitched clock (a third of the loud "
          "frames > 2x brighter), a VOLUME row quietens a quarter",
          0.25 <= share(bright, loud) <= 0.42 and np.mean(bright) > 2 * np.mean(dark)
          and 0.18 <= 1 - share(loud, med) <= 0.32, (len(bright), len(loud), len(med)))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--capture"]:
        _capture(sys.argv[2], sys.argv[3], int(sys.argv[4]))
        sys.exit(0)
    print("vm.music on the PC Engine")
    print("=" * 50)
    from mosaik8_build import cc65_available
    try:
        import libretro  # noqa: F401
        import numpy     # noqa: F401
        have_py = True
    except ImportError:
        have_py = False
    if not cc65_available() or not _core():
        print("  [SKIP] cc65 or the mednafen_pce_fast core not installed")
    elif not have_py:
        print("  [SKIP] libretro.py / numpy not installed")
    else:
        tmp = tempfile.mkdtemp(prefix="music_pce_")
        try:
            test_routing(tmp)
            test_timbres(tmp)
            test_envelope_cut_resume(tmp)
            test_effects_and_voicing(tmp)
            test_pitch_effects(tmp)
            test_volume_and_panning(tmp)
            test_subpatterns(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, failed))
    if failed:
        print("FAILED")
        sys.exit(1)
