"""vm.music's GB lead SLIDES, measured off a ROM in PyBoy.

Effect 2 is SLIDE UP and effect 3 SLIDE DOWN, `param` GB period units a frame
(G = 2048 - the frequency register, f = 131072 / G), as hUGEDriver's `1xy` /
`2xy` play them. Two defects are pinned:

  * the lead had them the WRONG WAY ROUND: cmd 2 subtracted from the frequency
    REGISTER, which lowers the pitch (one cell of `2 02` on note 37 fell from
    523 to 360 Hz);
  * a slide RESTARTED from the note every row (`PERIOD[base] -/+ param * tick`),
    where hUGEDriver moves the channel's period and the next row carries on
    from where the last one left it.

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


def _fixture(tmp, name, song):
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
        f.write('[instrument.sq]\nkind = "pulse"\nduty = 2\nvol = 15\n')
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


def _pitches(rom, frames=660, skip=300):
    """The lead's pitch per video frame (zero-padded FFT peak of that frame's
    own samples, parabolic), from frame `skip` (past the boot ROM); 0 = quiet."""
    import numpy as np
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", sound_emulated=True)
    rate = pb.sound.sample_rate
    out = []
    for i in range(frames):
        pb.tick(1, False)
        if i < skip:
            continue
        x = np.array(pb.sound.ndarray, dtype=np.float64)
        x = x.reshape(-1, 2).mean(axis=1) if x.ndim == 1 else x.mean(axis=1)
        x = x - x.mean()
        if len(x) < 400 or x.std() < 1:
            out.append(0.0)
            continue
        n = 16384
        p = np.abs(np.fft.rfft(x * np.hanning(len(x)), n))
        f = np.fft.rfftfreq(n, 1.0 / rate)
        band = np.flatnonzero((f > 150) & (f < 3000))
        k = band[np.argmax(p[band])]
        a, b, c = np.log(p[k - 1:k + 2] + 1e-9)
        den = a - 2 * b + c
        out.append(float((k + (0.5 * (a - c) / den if den else 0.0)) * rate / n))
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


if __name__ == "__main__":
    print("vm.music: the GB lead's slides")
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
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (passed, failed))
    if failed:
        print("FAILED")
        sys.exit(1)
