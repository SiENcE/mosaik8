"""vm.music LENDS the beep's channel to the beep while it sounds (hUGEDriver's
model), and a song channel with no note takes no voice.

THE BORROW. A song with `gb_all_voices` (VOICING bit0, "music-only") plays music
on the beep's own channel too: the GB's pulse 2, SMS/GG tone 0, Lynx Mikey A,
PC Engine PSG 0. hUGEDriver's answer is a BORROW: a muted channel is "left
entirely alone by the driver" and handed back when the effect ends. Ours under
`VM_MUSIC_BORROW` (stated by the build when a song sets bit0) skips every write
to that channel while `sound.busy()`, and on the pooled consoles gives the music
that channel LAST (the PC Engine's order), so the lead does not sit on it.

  * GB, measured off PyBoy through NR22 (0xFF17, pulse 2's volume register,
    READABLE): the song re-attacks pulse 2 at volume 5 (0x50) every 4-frame row,
    the effect starts at full volume (0xF0); with the borrow the register holds
    0xF0 for the effect's whole duration, and the effect is HEARD at its pitch.
  * Game Gear, Lynx, PC Engine: no readable sound registers, so the arm is
    heard - the dominant pitch per frame must stay the effect's for its length.
    The song fills the melodic voices so that its LOUD channel lands on the
    beep's channel (the third slot on the GG / Lynx, the sixth voice on the PCE)
    and the others play quietly.

Each arm has a CONTROL built from the same fixture with the define forced off,
which must show the song overwriting the effect within a row - otherwise the
instrument could not see the defect at all.

THE EMPTY CHANNEL (`songs.KIND_EMPTY`, `VM_MUSIC_EMPTY`): a song laid out lead /
EMPTY pulse 2 / wave lost its wave on the GG (the empty pulse took the second
of two melodic slots), and on the GB an empty FIRST pulse took the lead from a
pulse that plays. CONTROL: the fixture generated without the empty marking.

    python tests/music_borrow_test.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mosaik_vm                                      # noqa: E402
import mosaik_vm.songs as vm_songs                    # noqa: E402

passed = failed = 0
SFX_HZ = 1000                                         # the effect's tone
SFX_FRAMES = 40                                       # ... and its length
EXT = {"gameboy": "gb", "gamegear": "gg", "lynx": "lnx", "pce": "pce"}

# GB: pulse 2 (the song's 2nd pulse) re-attacked at volume 5 every 4-frame row; the
# lead plays quietly (an EMPTY first channel would hand the lead to the second)
SONG_GB = ('[song.s]\nchannels = ["pulse", "pulse"]\nrows = [[4, 44,1,1, 37,1,5]'
           + ', [4, 44,1,1, 37,1,5]' * 31 + ']\ngb_all_voices = true\n')


def _quiet_then_loud(n, loud="37,1,12"):
    """A music-only song of `n` pulse channels: the first n-1 play quietly at other
    pitches, the LAST one loud on note 37 (~523 Hz), re-attacked every 4 frames.
    `loud` "0,0,0" empties the last channel: it then takes no voice at all, so the
    beep's channel is the beep's alone - the LEVEL reference."""
    cells = ", ".join(["%d,1,1" % (44 + 2 * k) for k in range(n - 1)] + [loud])
    row = "[4, %s]" % cells
    return ('[song.s]\nchannels = [%s]\nrows = [%s]\ngb_all_voices = true\n'
            % (", ".join(['"pulse"'] * n), ", ".join([row] * 32)))


SONG_POOL = _quiet_then_loud(3)    # GG / Lynx: the 3rd melodic slot is the beep's channel
SONG_PCE = _quiet_then_loud(6)     # PCE: PSG 1..5 first, PSG 0 for the sixth
SONG_POOL_REF = _quiet_then_loud(3, "0,0,0")
SONG_PCE_REF = _quiet_then_loud(6, "0,0,0")

SCRIPT = ('[[script]]\nname = "main"\nevents = [\n'
          '  { event = "music_song", song = "s" },\n'
          '  { event = "wait", frames = 30 },\n'
          '  { event = "sound_tone", freq = %d, frames = %d },\n'
          '  { event = "wait", frames = 200 },\n]\n' % (SFX_HZ, SFX_FRAMES))
PLAY_ONLY = ('[[script]]\nname = "main"\nevents = [\n'
             '  { event = "music_song", song = "s" },\n'
             '  { event = "wait", frames = 250 },\n]\n')
# The build, in-process, so a CONTROL arm can force a detector off.
BUILD = ("import sys; sys.path.insert(0, %r); import mosaik8_build as b, mosaik8\n"
         "if sys.argv[2] == 'off': b._wants_music_borrow = lambda s: False\n"
         "if sys.argv[2] == 'off': b._wants_music_empty = lambda s: False\n"
         "sys.argv = ['mosaik8.py', 'build', '--platform', sys.argv[3], sys.argv[1]]\n"
         "mosaik8.main()\n")


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- %s" % (detail,)) if detail else ""))


INST = '[instrument.sq]\nkind = "pulse"\nduty = 2\nvol = 15\n'


def _fixture(tmp, name, arm, platform, song, script=SCRIPT, mark_empty=True, inst=INST):
    """vm-music with this song + script, built for `platform`; `arm` 'off' forces
    both detectors off; `mark_empty` False generates the songs without KIND_EMPTY."""
    root = os.path.join(tmp, name)
    shutil.copytree(os.path.join(ROOT, "projects", "vm-music"), root,
                    ignore=shutil.ignore_patterns("build"))
    sd = os.path.join(root, "scripts")
    for f in os.listdir(sd):
        if f.endswith(".evt.toml"):
            os.remove(os.path.join(sd, f))
    with open(os.path.join(sd, "main.evt.toml"), "w", encoding="utf-8") as f:
        f.write(script)
    with open(os.path.join(sd, "songs.toml"), "w", encoding="utf-8") as f:
        f.write(song)
    with open(os.path.join(sd, "instruments.toml"), "w", encoding="utf-8") as f:
        f.write(inst)
    prog = mosaik_vm.compile_path(sd)
    with open(os.path.join(root, "src", "scripts.mos"), "w", encoding="utf-8") as f:
        f.write(prog.to_scripts_mos())
    keep = vm_songs.channel_is_empty
    if not mark_empty:
        vm_songs.channel_is_empty = lambda rows, c: False
    try:
        mosaik_vm.generate_songs(root)
    finally:
        vm_songs.channel_is_empty = keep
    mosaik_vm.generate_instruments(root)
    r = subprocess.run([sys.executable, "-c", BUILD % ROOT, root, arm, platform],
                       capture_output=True, text=True, cwd=ROOT)
    rom = os.path.join(root, "build", platform, "vm-music." + EXT[platform])
    if r.returncode or not os.path.isfile(rom):
        check("%s: the fixture links" % name, False, ((r.stdout or "") + (r.stderr or ""))[-600:])
        return None, None
    with open(os.path.join(root, "build", platform, "vm-music.c"), encoding="utf-8") as f:
        c = f.read()
    return rom, c


def _gb_trace(rom, frames=420):
    """NR22 per frame, and the sounding pitch per frame (0 = quiet), off PyBoy."""
    import numpy as np
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", sound_emulated=True)
    rate = pb.sound.sample_rate
    nr22, hz = [], []
    for _ in range(frames):
        pb.tick(1, False)
        nr22.append(pb.memory[0xFF17])
        x = np.array(pb.sound.ndarray, dtype=np.float64)
        x = x.reshape(-1, 2).mean(axis=1) if x.ndim == 1 else x.mean(axis=1)
        hz.append(_peak(x, rate))
    pb.stop(save=False)
    return nr22, hz


def _peak(x, rate):
    """The dominant pitch of one frame of mono audio (150..3000 Hz), 0 = quiet."""
    import numpy as np
    x = x - x.mean() if len(x) else x
    if len(x) < 200 or x.std() < 1e-3 * (1 if x.dtype != np.float64 else 1):
        return 0.0
    n = 16384
    p = np.abs(np.fft.rfft(x * np.hanning(len(x)), n))
    f = np.fft.rfftfreq(n, 1.0 / rate)
    band = np.flatnonzero((f > 150) & (f < 3000))
    return float(f[band[np.argmax(p[band])]])


# One libretro core per PROCESS: the GG / Lynx / PCE trace runs in a child.
CHIP_TRACE = r"""
import json, os, sys
import numpy as np
root, rom = sys.argv[1], sys.argv[2]
lib = os.path.join(root, "emu", "libretro")
sys.path.insert(0, lib)
sys.path.insert(0, os.path.join(root, "tests"))
import run_lynx                                        # the NULL-frame patch
from libretro import SessionBuilder
from libretro.drivers.path import ExplicitPathDriver
from libretro.drivers.audio import ArrayAudioDriver
from libretro.api.av import AvEnableFlags
from music_borrow_test import _peak
core = {".gg": os.path.join(lib, "genesis_plus_gx_libretro.dll"),
        ".lnx": run_lynx.CORE,
        ".pce": os.path.join(lib, "mednafen_pce_fast_libretro.dll")}[os.path.splitext(rom)[1]]
audio = ArrayAudioDriver()
b = (SessionBuilder.defaults(core).with_content(rom)
     .with_paths(ExplicitPathDriver(corepath=core, system=lib, save=lib, assets=lib, playlist=lib))
     .with_audio(audio).with_perf(None))
if not rom.endswith(".lnx"):
    b = b.with_av_mask(AvEnableFlags.VIDEO | AvEnableFlags.AUDIO)
out, last = [], 0
with b.build() as sess:
    rate = audio.system_av_info.timing.sample_rate
    for _ in range(420):
        sess.run()
        buf = audio._buffer
        w = np.array(buf[last:], dtype=np.float64).reshape(-1, 2).mean(axis=1)
        last = len(buf)
        out.append([_peak(w, rate), float(np.sqrt(np.mean((w - w.mean()) ** 2))) if len(w) else 0.0])
print("TRACE " + json.dumps(out))
"""


def _chip_trace(rom):
    import json
    r = subprocess.run([sys.executable, "-c", CHIP_TRACE, ROOT, rom],
                       capture_output=True, text=True, cwd=ROOT)
    for line in r.stdout.splitlines():
        if line.startswith("TRACE "):
            return json.loads(line[6:])
    check("the %s trace ran" % os.path.basename(rom), False, (r.stderr or "")[-400:])
    return None


def _near(h, hz, tol=0.05):
    return abs(h - hz) < hz * tol


def _window(hz, lv=None):
    """(first frame at the effect's pitch, frames it stayed there). With levels `lv`,
    a frame counts only when it is LOUD (a third of the trace's loudest): the quiet
    channels' noise floor wanders across 1 kHz by chance."""
    loud = [True] * len(hz) if lv is None else [x >= max(lv) / 3.0 for x in lv]
    near = [_near(h, SFX_HZ) and loud[i] for i, h in enumerate(hz)]
    try:
        s = near.index(True)
    except ValueError:
        return None, 0
    n = 0
    while s + n < len(near) and near[s + n]:
        n += 1
    return s, n


def test_gb(tmp):
    print("\n[Game Boy: pulse 2 is lent to the beep]")
    on, c_on = _fixture(tmp, "gb_on", "on", "gameboy", SONG_GB)
    off, c_off = _fixture(tmp, "gb_off", "off", "gameboy", SONG_GB)
    if not on or not off:
        return
    check("GB: the borrow compiles in only when stated",
          "gbs_sound_busy()" in c_on and "gbs_sound_busy" not in c_off)
    tr_on, hz_on = _gb_trace(on)
    tr_off, _hz_off = _gb_trace(off)

    def win(nr22):
        try:
            s = nr22.index(0xF0)
        except ValueError:
            return None, 0
        n = 0
        while s + n < len(nr22) and nr22[s + n] == 0xF0:
            n += 1
        return s, n
    s_on, n_on = win(tr_on)
    s_off, n_off = win(tr_off)
    print("    ON : the effect holds NR22 0xF0 for %s frames from frame %s" % (n_on, s_on))
    print("    OFF: the effect holds NR22 0xF0 for %s frames from frame %s" % (n_off, s_off))
    check("GB CONTROL: without the borrow the song overwrites the effect within a row",
          s_off is not None and 0 < n_off <= 5, (s_off, n_off))
    check("GB: with the borrow the effect keeps pulse 2 for its whole %d frames" % SFX_FRAMES,
          s_on is not None and n_on >= SFX_FRAMES - 1, (s_on, n_on))
    after = tr_on[s_on + n_on:s_on + n_on + 12] if s_on is not None else []
    check("GB: ... and the song takes pulse 2 back at its next row (NR22 0x50)",
          0x50 in after, [hex(v) for v in after])
    if s_on is not None:
        mid = hz_on[s_on + 4:s_on + SFX_FRAMES - 4]
        share = sum(1 for h in mid if _near(h, SFX_HZ)) / float(len(mid) or 1)
        check("GB: the effect is HEARD at its own pitch while it sounds (>= 80 %% of its "
              "frames; %.0f %%)" % (100 * share), share >= 0.8, share)


def test_chip(tmp, platform, song, label, ref_song):
    print("\n[%s: the beep's channel is lent to the beep, and is the music's LAST]" % label)
    on, c_on = _fixture(tmp, platform + "_on", "on", platform, song)
    off, c_off = _fixture(tmp, platform + "_off", "off", platform, song)
    if not on or not off:
        return
    check("%s: the borrow compiles in only when stated" % label,
          "gbs_sound_busy()" in c_on and "gbs_sound_busy" not in c_off)
    ref, _c = _fixture(tmp, platform + "_ref", "on", platform, ref_song)
    tr_on, tr_off = _chip_trace(on), _chip_trace(off)
    tr_ref = _chip_trace(ref) if ref else None
    if tr_on is None or tr_off is None or tr_ref is None:
        return
    hz_on, hz_off = [h for h, _l in tr_on], [h for h, _l in tr_off]
    lv_on = [lv for _h, lv in tr_on]
    s_on, n_on = _window(hz_on, lv_on)
    s_off, n_off = _window(hz_off, [lv for _h, lv in tr_off])
    print("    ON : %s Hz heard for %s frames from frame %s" % (SFX_HZ, n_on, s_on))
    print("    OFF: %s Hz heard for %s frames from frame %s" % (SFX_HZ, n_off, s_off))
    check("%s CONTROL: without the borrow the song takes the channel back within a row"
          % label, s_off is not None and 0 < n_off <= 6, (s_off, n_off))
    check("%s: with the borrow the effect keeps the channel for its whole %d frames"
          % (label, SFX_FRAMES), s_on is not None and n_on >= SFX_FRAMES - 2, (s_on, n_on))
    s_ref, _n = _window([h for h, _l in tr_ref], [lv for _h, lv in tr_ref])
    if s_on is not None and s_ref is not None:
        # the LEVEL too: a volume write that reaches the borrowed channel leaves the
        # pitch alone, so the pitch above cannot see it (the PCE's pc_out). Against
        # the beep ALONE on its channel (the loud channel emptied), not against the
        # effect's own median: the song turns it down within its first row, so its
        # own median is already the defect.
        med = lambda xs: sorted(xs)[len(xs) // 2] if xs else 0.0
        a = med(lv_on[s_on + 2:s_on + SFX_FRAMES - 2])
        r = med([lv for _h, lv in tr_ref][s_ref + 2:s_ref + SFX_FRAMES - 2])
        ratio = a / r if r else 0.0
        print("    ON : the effect's level / the beep alone: %.2f" % ratio)
        check("%s: the effect keeps its LEVEL (>= 0.8 of the beep alone)" % label,
              ratio >= 0.8, ratio)
    after = hz_on[s_on + n_on:s_on + n_on + 12] if s_on is not None else []
    check("%s: ... and the song comes back after it (its loud note, ~523 Hz)" % label,
          any(_near(h, 523, 0.06) for h in after), after)


TWO_EFFECTS = ('[[script]]\nname = "main"\nevents = [\n'
               '  { event = "music_song", song = "s" },\n'
               '  { event = "wait", frames = 30 },\n'
               '  { event = "sound_tone", freq = %d, frames = %d },\n'
               '  { event = "wait", frames = 100 },\n'
               '  { event = "sound_tone", freq = %d, frames = %d },\n'
               '  { event = "wait", frames = 200 },\n]\n' % (SFX_HZ, SFX_FRAMES, SFX_HZ, SFX_FRAMES))


def _effects(tr):
    """[(first frame, frames, median level)] of every loud run at the effect's pitch."""
    lv = [x for _h, x in tr]
    hz = [h for h, _x in tr]
    loud = max(lv) / 3.0
    runs, i = [], 0
    while i < len(hz):
        if _near(hz[i], SFX_HZ) and lv[i] >= loud:
            j = i
            while j < len(hz) and _near(hz[j], SFX_HZ) and lv[j] >= loud:
                j += 1
            mid = sorted(lv[i + 2:j - 2])
            runs.append((i, j - i, mid[len(mid) // 2] if mid else 0.0))
            i = j
        else:
            i += 1
    return runs


def test_pce_wave(tmp):
    print("\n[PC Engine: the beep reloads its own square after the song used PSG 0]")
    # a 12.5 % instrument: the song's square in PSG 0 differs from the beep's 50 %
    thin = '[instrument.sq]\nkind = "pulse"\nduty = 0\nvol = 15\n'
    on, _c = _fixture(tmp, "pcew_on", "on", "pce", SONG_PCE, TWO_EFFECTS, inst=thin)
    ref, _c = _fixture(tmp, "pcew_ref", "on", "pce", SONG_PCE_REF, TWO_EFFECTS, inst=thin)
    if not on or not ref:
        return
    a, r = _chip_trace(on), _chip_trace(ref)
    if a is None or r is None:
        return
    ea, er = _effects(a), _effects(r)
    print("    ON : effects %s; the beep alone %s" % (
        [(s, n, round(lv)) for s, n, lv in ea], [(s, n, round(lv)) for s, n, lv in er]))
    ok = len(ea) == 2 and len(er) == 2
    check("PCE: both effects sound", ok, (ea, er))
    if ok:
        check("PCE: the SECOND effect, after the song reloaded PSG 0, keeps the beep's own "
              "square (level >= 0.9 of the beep alone)", ea[1][2] >= 0.9 * er[1][2],
              (ea[1][2], er[1][2]))


def test_empty(tmp):
    print("\n[an EMPTY song channel takes no voice]")
    # GG, game voicing: lead quiet on note 44 (~784 Hz), pulse 2 EMPTY, the wave loud
    # on note 49 (an octave down on the PSG: ~523 Hz)
    gg_song = ('[song.s]\nchannels = ["pulse", "pulse", "wave"]\nrows = ['
               + ", ".join(["[8, 44,1,2, 0,0,0, 49,1,15]"] * 32) + ']\n')
    on, _c = _fixture(tmp, "gge_on", "on", "gamegear", gg_song, PLAY_ONLY)
    off, _c = _fixture(tmp, "gge_off", "on", "gamegear", gg_song, PLAY_ONLY, mark_empty=False)
    if on and off:
        tr_on, tr_off = _chip_trace(on), _chip_trace(off)
        if tr_on is not None and tr_off is not None:
            hz_on, hz_off = [h for h, _l in tr_on], [h for h, _l in tr_off]
            a = sum(1 for h in hz_on[120:] if _near(h, 523, 0.06)) / float(len(hz_on[120:]))
            b = sum(1 for h in hz_off[120:] if _near(h, 523, 0.06)) / float(len(hz_off[120:]))
            print("    GG: the wave (bass) heard on %.0f %% of the frames; CONTROL %.0f %%"
                  % (100 * a, 100 * b))
            check("GG CONTROL: unmarked, the empty pulse takes the wave's slot", b < 0.1, b)
            check("GG: marked, the wave gets the slot and plays", a >= 0.9, a)
    # GB: the FIRST pulse empty, the second plays note 37: it must get the lead
    gb_song = ('[song.s]\nchannels = ["pulse", "pulse"]\nrows = ['
               + ", ".join(["[8, 0,0,0, 37,1,12]"] * 32) + ']\n')
    on, _c = _fixture(tmp, "gbe_on", "on", "gameboy", gb_song, PLAY_ONLY)
    off, _c = _fixture(tmp, "gbe_off", "on", "gameboy", gb_song, PLAY_ONLY, mark_empty=False)
    if on and off:
        _t, hz_on = _gb_trace(on)
        _t, hz_off = _gb_trace(off)
        a = sum(1 for h in hz_on[320:] if _near(h, 523, 0.06)) / float(len(hz_on[320:]))
        b = sum(1 for h in hz_off[320:] if _near(h, 523, 0.06)) / float(len(hz_off[320:]))
        print("    GB: the second pulse heard on %.0f %% of the frames; CONTROL %.0f %%"
              % (100 * a, 100 * b))
        check("GB CONTROL: unmarked, the empty first pulse takes the lead", b < 0.1, b)
        check("GB: marked, the playing pulse gets the lead", a >= 0.9, a)


def main():
    print("vm.music lends the beep's channel (VM_MUSIC_BORROW) + empty channels")
    print("=" * 50)
    try:
        import pyboy  # noqa: F401
    except ImportError:
        print("  [SKIP] PyBoy is not installed")
        return 0
    import mosaik8_build
    if not mosaik8_build.gbdk_available():
        print("  [SKIP] GBDK is not installed")
        return 0
    lib = os.path.join(ROOT, "emu", "libretro")
    tmp = tempfile.mkdtemp(prefix="music_borrow_")
    try:
        test_gb(tmp)
        if os.path.isfile(os.path.join(lib, "genesis_plus_gx_libretro.dll")):
            test_chip(tmp, "gamegear", SONG_POOL, "Game Gear", SONG_POOL_REF)
        else:
            print("  [SKIP] no genesis_plus_gx core")
        if mosaik8_build.cc65_available():
            if any(os.path.isfile(os.path.join(lib, c)) for c in
                   ("handy_libretro.dll", "mednafen_lynx_libretro.dll")):
                test_chip(tmp, "lynx", SONG_POOL, "Lynx", SONG_POOL_REF)
            else:
                print("  [SKIP] no Lynx core")
            if os.path.isfile(os.path.join(lib, "mednafen_pce_fast_libretro.dll")):
                test_chip(tmp, "pce", SONG_PCE, "PC Engine", SONG_PCE_REF)
                test_pce_wave(tmp)
            else:
                print("  [SKIP] no mednafen_pce_fast core")
        else:
            print("  [SKIP] cc65 is not installed (Lynx, PC Engine)")
        if os.path.isfile(os.path.join(lib, "genesis_plus_gx_libretro.dll")):
            test_empty(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print()
    print("%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
