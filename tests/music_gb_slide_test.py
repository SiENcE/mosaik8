"""vm.music's GB per-frame melodic EFFECTS, measured off a ROM in PyBoy.

Effect 2 is SLIDE UP and effect 3 SLIDE DOWN, `param` GB period units a frame
(G = 2048 - the frequency register, f = 131072 / G), as hUGEDriver's `1xy` /
`2xy` play them. Two defects are pinned:

  * the lead had them the WRONG WAY ROUND: cmd 2 subtracted from the frequency
    REGISTER, which lowers the pitch (one cell of `2 02` on note 37 fell from
    523 to 360 Hz);
  * a slide RESTARTED from the note every row (`PERIOD[base] -/+ param * tick`),
    where hUGEDriver moves the channel's period and the next row carries on
    from where the last one left it.

Since 2026-10-08 the same effects reach PULSE 2 (a music-only song) and the
WAVE channel, which only the lead had (`test_channels`): an arpeggio and a
slide on each, measured as pitch, and the wave channel's volume slide read off
NR32's four coarse levels. The wave channel sounds an octave below a pulse on
the same register: f = 65536 / (2048 - register).

Every expected pitch comes from the GB's own formula, so a check is also the
calibration.

    python tests/music_gb_slide_test.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mosaik_vm                                      # noqa: E402

passed = failed = 0
G37 = 250                                             # note 37's GB period (523 Hz)


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- %s" % (detail,)) if detail else ""))


def _hz(g):
    return 131072.0 / g


def _near(v, want, tol):
    return want * (1 - tol) <= v <= want * (1 + tol)


def _fixture(tmp, name, song, inst=None):
    root = os.path.join(tmp, name)
    shutil.copytree(os.path.join(ROOT, "projects", "vm-music"), root,
                    ignore=shutil.ignore_patterns("build"))
    sd = os.path.join(root, "scripts")
    for f in os.listdir(sd):
        if f.endswith(".evt.toml"):
            os.remove(os.path.join(sd, f))
    with open(os.path.join(sd, "main.evt.toml"), "w", encoding="utf-8") as f:
        f.write('[[script]]\nname = "main"\nevents = [\n'
                '  { event = "music_song", song = "s" }\n]\n')
    with open(os.path.join(sd, "songs.toml"), "w", encoding="utf-8") as f:
        f.write(song)
    with open(os.path.join(sd, "instruments.toml"), "w", encoding="utf-8") as f:
        f.write(inst or '[instrument.sq]\nkind = "pulse"\nduty = 2\nvol = 15\n')
    prog = mosaik_vm.compile_path(sd)
    with open(os.path.join(root, "src", "scripts.mos"), "w", encoding="utf-8") as f:
        f.write(prog.to_scripts_mos())
    mosaik_vm.generate_songs(root)
    mosaik_vm.generate_instruments(root)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "gameboy", root], capture_output=True, text=True)
    rom = os.path.join(root, "build", "gameboy", "vm-music.gb")
    if r.returncode or not os.path.isfile(rom):
        check("%s: the fixture links" % name, False, ((r.stdout or "") + (r.stderr or ""))[-600:])
        return None
    return rom


def _pitches(rom, frames=660, skip=300, regs=None, track=None):
    """The sounding pitch per video frame (zero-padded FFT peak of that frame's
    own samples, parabolic), from frame `skip` (past the boot ROM); 0 = quiet.
    `regs`, a list, collects NR32 (the wave channel's output level) per frame;
    `track` collects (level, pitch) per frame, quiet frames included."""
    import numpy as np
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", sound_emulated=True)
    rate = pb.sound.sample_rate
    out = []
    for i in range(frames):
        pb.tick(1, False)
        if i < skip:
            continue
        if regs is not None:
            regs.append(pb.memory[0xFF1C] & 0x60)
        x = np.array(pb.sound.ndarray, dtype=np.float64)
        x = x.reshape(-1, 2).mean(axis=1) if x.ndim == 1 else x.mean(axis=1)
        x = x - x.mean()
        if len(x) < 400 or x.std() < 1:
            out.append(0.0)
            if track is not None:
                track.append((0.0, 0.0))
            continue
        n = 16384
        p = np.abs(np.fft.rfft(x * np.hanning(len(x)), n))
        f = np.fft.rfftfreq(n, 1.0 / rate)
        band = np.flatnonzero((f > 150) & (f < 3000))
        k = band[np.argmax(p[band])]
        a, b, c = np.log(p[k - 1:k + 2] + 1e-9)
        den = a - 2 * b + c
        out.append(float((k + (0.5 * (a - c) / den if den else 0.0)) * rate / n))
        if track is not None:
            track.append((float(x.std()), out[-1]))
    pb.stop(save=False)
    return [x for x in out if x]


def test_slides(tmp):
    print("\n[the GB lead's slides: direction, rate, and carrying over the next row]")
    import numpy as np
    one = '[song.s]\nchannels = ["pulse"]\nrows = [[60, 37,1,0]]\nfx = [[%d, 2]]\n'
    up = _fixture(tmp, "up", one % 2)
    dn = _fixture(tmp, "down", one % 3)
    # 30 frames of note 37 sliding up, then a REST row that slides on: the
    # second row starts where the first stopped (G 250 - 2 x 29), not at 523 Hz
    carry = _fixture(tmp, "carry", '[song.s]\nchannels = ["pulse"]\nrows = [[30, 37,1,0], '
                     '[30, 0,0,0]]\nfx = [[2, 2], [2, 2]]\n')
    if not up or not dn or not carry:
        return
    for name, rom, sign in (("UP", up, -1), ("DOWN", dn, +1)):
        pt = _pitches(rom)
        if not pt:
            check("SLIDE %s: audio captured" % name, False)
            continue
        lo, med, hi = np.percentile(pt, 1), np.median(pt), np.percentile(pt, 99)
        ends = sorted((_hz(G37), _hz(G37 + sign * 2 * 59)))
        mid = _hz(G37 + sign * 2 * 30)
        print("    slide %s 2: %.0f .. %.0f Hz (median %.0f); the GB formula %.0f .. %.0f "
              "(median %.0f)" % (name.lower(), lo, hi, med, ends[0], ends[1], mid))
        check("SLIDE %s moves the pitch %s, 2 GB period units a frame (ends + median "
              "within 4 %%)" % (name, name.lower()), _near(lo, ends[0], 0.04)
              and _near(hi, ends[1], 0.04) and _near(med, mid, 0.04), (lo, med, hi))
    pt = _pitches(carry)
    if pt:
        hi = np.percentile(pt, 99)
        print("    slide up over a note row + a rest row: top %.0f Hz; carried on %.0f, "
              "restarted %.0f" % (hi, _hz(G37 - 2 * 59), _hz(G37 - 2 * 29)))
    check("a slide on the NEXT row carries on from where the last one stopped",
          pt and _near(np.percentile(pt, 99), _hz(G37 - 2 * 59), 0.04), pt and hi)


def _share(pt, hz, tol=0.03):
    """The fraction of measured frames within `tol` of `hz`."""
    return sum(1 for x in pt if _near(x, hz, tol)) / float(len(pt) or 1)


def test_channels(tmp):
    print("\n[pulse 2 and the wave channel get the per-frame effects too]")
    import numpy as np
    # pulse 2 plays only in a MUSIC-ONLY song; the lead's column is empty
    p2 = ('[song.s]\nchannels = ["pulse", "pulse"]\nrows = [[60, 0,0,0, 37,1,0]]\n'
          'fx = [[0,0, %d,%d]]\ngb_all_voices = true\n')
    wv = '[song.s]\nchannels = ["wave"]\nrows = [[60, 49,0,0]]\nfx = [[%d, %d]]\n'
    porta = ('[song.s]\nchannels = ["pulse", "pulse"]\nrows = [[30, 0,0,0, 37,1,0], '
             '[30, 0,0,0, 44,%d,0]]\nfx = [[0,0, 0,0], [0,0, 7,2]]\ngb_all_voices = true\n')
    fade = '[instrument.f]\nkind = "pulse"\nduty = 2\ndecay = -3\nvol = 15\n'
    roms = {
        "p2_arp": _fixture(tmp, "p2_arp", p2 % (1, 0x47)),
        "p2_up": _fixture(tmp, "p2_up", p2 % (2, 2)),
        "w_arp": _fixture(tmp, "w_arp", wv % (1, 0x47)),
        "w_up": _fixture(tmp, "w_up", wv % (2, 1)),
        "w_vsl": _fixture(tmp, "w_vsl", '[song.s]\nchannels = ["wave"]\nrows = [[4, 49,0,0]'
                          + ', [4, 0,0,0]' * 15 + ']\nfx = [' + ', '.join(['[5, 1]'] * 16)
                          + ']\n'),
        "p2_vib": _fixture(tmp, "p2_vib", p2 % (4, 0x1F)),
        # a QUIET wave note (volume 5: NR32 at 25 %), then a portamento naming no
        # instrument: no re-attack, so the level stays where it was
        "w_pq": _fixture(tmp, "w_pq", '[song.s]\nchannels = ["wave"]\nrows = '
                         '[[30, 49,0,5], [30, 56,0,0]]\nfx = [[0, 0], [7, 3]]\n'),
        # pulse 2, a FADING instrument: note 37, then a PORTAMENTO 2 onto note 44, the
        # cell naming the instrument (re-attack) or not (no new attack)
        "p2_pa": _fixture(tmp, "p2_pa", porta % 1, fade),
        "p2_pn": _fixture(tmp, "p2_pn", porta % 0, fade),
        # note 49, then a PORTAMENTO 3 onto note 56: G 125 -> 84 in 14 frames
        "w_porta": _fixture(tmp, "w_porta", '[song.s]\nchannels = ["wave"]\nrows = '
                            '[[30, 49,0,0], [30, 56,0,0]]\nfx = [[0, 0], [7, 3]]\n'),
        # the lead: an arpeggio over 15 rows of FOUR frames (not a multiple of 3)
        "arp_rows": _fixture(tmp, "arp_rows", '[song.s]\nchannels = ["pulse"]\nrows = [[4, 37,1,0]'
                             + ', [4, 0,0,0]' * 14 + ']\nfx = [' + ', '.join(['[1, 71]'] * 15)
                             + ']\n'),
    }
    if not all(roms.values()):
        return
    # an ARPEGGIO 4 / 7 cycles note, +4 and +7 semitones a frame each: every one of
    # the three pitches must own a real share of the frames
    for name, key, regs, hz_of in (("PULSE 2", "p2_arp", (250, 199, 167), _hz),
                                   ("WAVE", "w_arp", (125, 99, 84), lambda g: 65536.0 / g)):
        pt = _pitches(roms[key])
        shares = [_share(pt, hz_of(g)) for g in regs]
        print("    %s arpeggio 4 7: %s of the frames at %s Hz" % (
            name.lower(), ", ".join("%.0f %%" % (100 * s) for s in shares),
            " / ".join("%.0f" % hz_of(g) for g in regs)))
        check("%s plays an ARPEGGIO (note, +4 and +7 each on >= 15 %% of the frames)"
              % name, pt and min(shares) >= 0.15, shares)
    # hUGEDriver's ORDER and PHASE (its jump table, measured off the reference ROM): the
    # note, then +y, then +x, stepped by a tick counter no row restarts, the row's
    # first tick included. A per-row restart repeats the note at every row start.
    pt = _pitches(roms["p2_arp"])
    nxt = [pt[i + 1] for i in range(len(pt) - 1) if _near(pt[i], _hz(250), 0.03)]
    to_y = sum(1 for x in nxt if _near(x, _hz(167), 0.03)) / float(len(nxt) or 1)
    print("    pulse 2 arpeggio 4 7: the note is followed by +7 (y) on %.0f %% of %d steps"
          % (100 * to_y, len(nxt)))
    check("the ARPEGGIO steps note -> +y -> +x, hUGEDriver's order (>= 80 % of the steps "
          "after the note go to +y)", nxt and to_y >= 0.8, to_y)
    pt = _pitches(roms["arp_rows"])
    same = sum(1 for i in range(len(pt) - 1) if _near(pt[i + 1], pt[i], 0.03))
    print("    lead arpeggio over 4-frame rows: %d of %d frames repeat the last pitch"
          % (same, len(pt) - 1))
    check("the arpeggio's phase runs ON across rows (< 5 % of the frames repeat a pitch; "
          "a per-row restart repeats one in four)", pt and same < 0.05 * len(pt), same)
    # SLIDE UP: pulse 2 at 2 units a frame (G 250 -> 132), the wave at 1 (G 125 -> 66)
    for name, key, g0, step, hz_of in (("PULSE 2", "p2_up", 250, 2, _hz),
                                        ("WAVE", "w_up", 125, 1, lambda g: 65536.0 / g)):
        pt = _pitches(roms[key])
        if not pt:
            check("%s SLIDE UP: audio captured" % name, False)
            continue
        lo, med, hi = np.percentile(pt, 1), np.median(pt), np.percentile(pt, 99)
        ends = (hz_of(g0), hz_of(g0 - step * 59))
        print("    %s slide up %d: %.0f .. %.0f Hz (median %.0f); the GB formula %.0f .. "
              "%.0f (median %.0f)" % (name.lower(), step, lo, hi, med, ends[0], ends[1],
                                      hz_of(g0 - step * 30)))
        check("%s SLIDE UP raises the pitch %d GB period unit(s) a frame (ends + median "
              "within 4 %%)" % (name, step), _near(lo, ends[0], 0.04)
              and _near(hi, ends[1], 0.04) and _near(med, hz_of(g0 - step * 30), 0.04),
              (lo, med, hi))
    # a PORTAMENTO on the wave GLIDES (no re-attack onto the target): about 13 of
    # every 60 frames sound between the two notes, where a jump has none
    pt = _pitches(roms["w_porta"])
    lo_hz, hi_hz = 65536.0 / 125, 65536.0 / 84
    between = sum(1 for x in pt if lo_hz * 1.03 < x < hi_hz * 0.97)
    print("    wave portamento 3, %.0f -> %.0f Hz: %d of %d frames in between, top %.0f Hz"
          % (lo_hz, hi_hz, between, len(pt), max(pt or [0])))
    check("the WAVE channel's PORTAMENTO glides onto the note (>= 5 % of the frames "
          "between the two, ends on the target)", pt and between >= 0.05 * len(pt)
          and _near(np.percentile(pt, 99), hi_hz, 0.03), (between, len(pt)))
    # the wave channel's VOLUME SLIDE down 1 a ROW (hUGEDriver's: once, on the row's first
    # tick) from 15, over 4-frame rows: NR32's coarse levels step 100 % (0x20) -> 50 %
    # (0x40) -> 25 % (0x60) -> mute (0) in that order, and 100 % lasts the rows at
    # 14..11, 16 frames (a slide every TICK would reach 50 % on the fifth frame)
    regs = []
    _pitches(roms["w_vsl"], regs=regs)
    seen = [v for i, v in enumerate(regs) if i == 0 or v != regs[i - 1]]
    # (the capture starts before the song: NR32 may read 0 first)
    i20 = regs.index(0x20) if 0x20 in regs else -1
    i40 = regs.index(0x40, i20) if i20 >= 0 and 0x40 in regs[i20:] else -1
    print("    wave volume slide: NR32 levels %s; 100 %% for %d frames"
          % (["0x%02X" % v for v in seen[:8]], i40 - i20))
    run = [0x20, 0x40, 0x60, 0x00]
    check("the WAVE channel's VOLUME SLIDE steps NR32 100 -> 50 -> 25 % -> mute, once a "
          "ROW (100 % for 14..18 frames)", any(seen[i:i + 4] == run for i in range(len(seen)))
          and 14 <= i40 - i20 <= 18, (seen[:8], i40 - i20))
    # A PORTAMENTO cell that names an INSTRUMENT re-attacks it (hUGEDriver loads the
    # instrument, and its 'initial' flag retriggers the first glide tick) but glides on
    # from the held note; one without an instrument glides with no new attack. Counted
    # as level JUMPS (a frame 30 % louder than the last): one a loop more with it.
    def jumps(trk):
        return sum(1 for i in range(1, len(trk)) if trk[i][0] > 1.3 * trk[i - 1][0] + 1)
    ta, tn = [], []
    _pitches(roms["p2_pa"], track=ta)
    _pitches(roms["p2_pn"], track=tn)
    glide = sum(1 for lv, hz in ta if _hz(250) * 1.03 < hz < _hz(167) * 0.97)
    print("    pulse 2 portamento 2 onto a fading note: %d level jumps with the instrument "
          "named, %d without; %d frames mid-glide" % (jumps(ta), jumps(tn), glide))
    check("a PORTAMENTO naming an instrument RE-ATTACKS (twice the level jumps of one that "
          "does not) and still glides", jumps(tn) >= 4 and jumps(ta) >= 2 * jumps(tn) - 1
          and glide >= 0.2 * len(ta), (jumps(ta), jumps(tn), glide))
    regs = []
    pt = _pitches(roms["w_pq"], regs=regs)
    levels = sorted(set(regs) - {0})
    print("    wave portamento with no instrument after a volume-5 note: NR32 levels %s, "
          "%d frames mid-glide" % (["0x%02X" % v for v in levels],
                                   sum(1 for x in pt if lo_hz * 1.03 < x < hi_hz * 0.97)))
    check("a WAVE portamento naming no instrument does not re-attack (NR32 stays at 25 %)",
          levels == [0x60], levels)
    # VIBRATO 1 F on pulse 2: hUGEDriver's, the note + 15 period units on the ticks where
    # (counter AND 1) is 0, the note on the others: two pitches, half the frames each
    # (the old triangle swung up to 120 units either way)
    pt = _pitches(roms["p2_vib"])
    lo_s, hi_s = _share(pt, _hz(250), 0.01), _share(pt, _hz(235), 0.01)
    print("    pulse 2 vibrato 1 F: %.0f %% of the frames at %.0f Hz, %.0f %% at %.0f Hz"
          % (100 * lo_s, _hz(250), 100 * hi_s, _hz(235)))
    check("the VIBRATO alternates the note and + y period units (each on >= 40 % of the "
          "frames)", pt and lo_s >= 0.4 and hi_s >= 0.4, (lo_s, hi_s))


if __name__ == "__main__":
    print("vm.music: the GB per-frame effects (lead slides, pulse 2, wave)")
    print("=" * 50)
    from mosaik8_build import gbdk_available
    try:
        import pyboy  # noqa: F401
        import numpy  # noqa: F401
        have_py = True
    except ImportError:
        have_py = False
    if not gbdk_available():
        print("  [SKIP] GBDK not installed")
    elif not have_py:
        print("  [SKIP] pyboy / numpy not installed")
    else:
        tmp = tempfile.mkdtemp(prefix="music_gb_slide_")
        try:
            test_slides(tmp)
            test_channels(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, failed))
    if failed:
        print("FAILED")
        sys.exit(1)
