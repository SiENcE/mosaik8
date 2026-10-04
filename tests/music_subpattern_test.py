"""Instrument SUBPATTERNS on vm.music (hUGETracker v6 tables) + the portable
`6xy` wiring (D9).

A subpattern is a per-TICK macro an instrument starts with every note: each
row may move the pitch (a semitone offset, LATCHED), set the volume, run an
effect and jump. hUGEDriver's `do_table` is the reference, and hUGETracker's
`RenderSubpatternCell` the rule for row 31 (no jump of its own -> row 0).

What is pinned here:

  * the authored format (`instruments.toml`, SPARSE rows with `row = N`) and
    its 5-byte driver encoding, including the off-by-one "0 = absent" fields;
  * BYTE-IDENTICAL OFF: a library with no subpattern emits the module it
    always did, the glue wires nothing, and the build states no define;
  * the glue in both directions for `set_subpatterns` and `routine_next`;
  * ON A ROM: a GB build whose lead instrument carries a table really runs it
    one row per display frame - NR12 (the volume row), NR11 (the timbre row)
    and the PITCH off PyBoy's own audio buffer, with a `subpattern_off` twin
    as the control that must NOT move;
  * the POOLED consoles (Lynx, SMS, Game Gear) run the same table per melodic
    slot, measured off the core's captured AUDIO (pitch by zero crossings,
    loudness by RMS), each against a `subpattern_off` twin;
  * the REGISTRATION guard: table code compiled in by the glue's TEXT but never
    registered (a hand-wired shell) plays plain instead of calling address 0.

    python tests/music_subpattern_test.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mosaik_vm                                      # noqa: E402
from mosaik_vm import instruments as inst_mod, glue   # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- %s" % (detail,)) if detail else ""))


def test_format():
    print("\n[the authored format and its bytes]")
    rows = inst_mod.parse_subpattern([
        {"row": 0, "pitch": 0, "vol": 15},
        {"row": 3, "jump": 0},
        {"row": 2, "pitch": 99},              # clamps to +35
        {"row": 40, "pitch": 1},              # past the table: dropped
    ])
    check("sparse rows become a DENSE list trimmed after the last row",
          len(rows) == 4 and rows[1] == {}, rows)
    check("a pitch clamps to hUGE's -36..+35", rows[2] == {"pitch": 35}, rows[2])
    check("absent fields encode 0, present ones OFF BY ONE",
          inst_mod.subpattern_bytes(rows[0]) == [37, 0, 16, 0, 0]
          and inst_mod.subpattern_bytes(rows[3]) == [0, 1, 0, 0, 0]
          and inst_mod.subpattern_bytes({"pitch": -36}) == [1, 0, 0, 0, 0],
          [inst_mod.subpattern_bytes(r) for r in rows])
    check("an effect keeps its parameter",
          inst_mod.parse_subpattern([{"fx": 14, "param": 0xC0}])
          == [{"fx": 14, "param": 0xC0}])


def test_byte_identical_off():
    print("\n[byte-identical when no instrument plays a table]")
    plain = [("lead", {"kind": "pulse", "duty": 2, "wave": 0, "noisefreq": 0x18,
                       "env": 0x71, "decay": 0, "vol": 15})]
    off = [("lead", dict(plain[0][1], subpattern=[{"pitch": 12}],
                         subpattern_off=True))]
    on = [("lead", dict(plain[0][1], subpattern=[{"pitch": 12}]))]
    base = inst_mod.emit_instruments_mos(plain)
    check("a plain library's module has no table symbols",
          "SUBLEN" not in base and "sublen" not in base)
    check("subpattern_off emits the SAME module as no table at all",
          inst_mod.emit_instruments_mos(off) == base)
    text = inst_mod.emit_instruments_mos(on)
    check("a table emits SUBLEN / SUBOFF / SUBDATA and exports the readers",
          all(s in text for s in ("SUBLEN", "SUBOFF", "SUBDATA",
                                  "export kind, p0, p1, vol, wavebyte, sublen, subrow")))
    a = glue.emit_glue_mos(True, True, has_subpatterns=False)
    b = glue.emit_glue_mos(True, True, has_subpatterns=True)
    check("the glue wires set_subpatterns iff a table plays",
          "set_subpatterns" not in a
          and "music.set_subpatterns(instruments.sublen, instruments.subrow)" in b)
    r0 = glue.emit_glue_mos(True, False, music_routine=False)
    r1 = glue.emit_glue_mos(True, False, music_routine=True)
    check("D9: an all-vm.music glue registers OUR queue iff a routine is attached",
          "routine_next" not in r0
          and "core.set_music_routines(music.routine_next)" in r1)
    from mosaik8_build import _wants_music_subpat
    check("the build states VM_MUSIC_SUBPAT off the WIRING, not a comment",
          _wants_music_subpat([("g.mos", b)])
          and not _wants_music_subpat([("g.mos", a)])
          and not _wants_music_subpat(
              [("m.mos", "-- music.set_subpatterns(a, b) is how a shell wires it")]))


# --------------------------------------------------------------------------
# The ROM half. A two-row song: C-5 on the lead with instrument 1, held for
# 240 frames. The instrument's table, one row per tick:
#
#   row 0   pitch +0 (Base)   vol 15   timbre $00 (12.5 %)
#   row 1                     vol 4    timbre $C0 (75 %)
#   row 2   pitch +12
#   row 3   jump -> row 0
#
# so NR12 reads F0 40 40 40 | F0 ..., NR11's duty bits 0 3 3 3 | 0 ..., and
# the pitch is base, base, OCTAVE, octave | base ... - a period of 4 frames.
# --------------------------------------------------------------------------
MAIN = '''[[script]]
name = "main"
events = [
  { event = "music_song", song = "sp" },
  { event = "stop" },
]
'''
SONG = '''[song.sp]
channels = [ "pulse",]
rows = [ [ 240, 25, 1, 0,], [ 240, 0, 0, 0,],]
'''
INST = '''[instrument.lead]
kind = "pulse"
duty = 2
vol = 15
%s
[[instrument.lead.subpattern]]
row = 0
pitch = 0
vol = 15
fx = 14
param = 0
[[instrument.lead.subpattern]]
row = 1
vol = 4
fx = 14
param = 192
[[instrument.lead.subpattern]]
row = 2
pitch = 12
[[instrument.lead.subpattern]]
row = 3
jump = 0
'''


def _fixture(tmp, off):
    root = os.path.join(tmp, "subpat_off" if off else "subpat")
    src = os.path.join(ROOT, "projects", "vm-musicroutine")
    shutil.copytree(src, root, ignore=shutil.ignore_patterns("build", "assets"))
    sd = os.path.join(root, "scripts")
    with open(os.path.join(sd, "main.evt.toml"), "w", encoding="utf-8") as f:
        f.write(MAIN)
    with open(os.path.join(sd, "songs.toml"), "w", encoding="utf-8") as f:
        f.write(SONG)
    with open(os.path.join(sd, "instruments.toml"), "w", encoding="utf-8") as f:
        f.write(INST % ("subpattern_off = true" if off else ""))
    with open(os.path.join(root, "studio.toml"), "w", encoding="utf-8") as f:
        f.write('[audio]\ngb = "vm"\n')
    prog = mosaik_vm.compile_path(sd)
    with open(os.path.join(root, "src", "scripts.mos"), "w", encoding="utf-8") as f:
        f.write(prog.to_scripts_mos())
    mosaik_vm.generate_songs(root)
    mosaik_vm.generate_instruments(root)
    mosaik_vm.generate_glue(root)
    return root


def _cycles_per_frame(samples):
    """Rising crossings of the frame's own MIDPOINT, left channel. PyBoy's
    samples are UNSIGNED levels (0..amplitude), not a signed wave, so a zero
    crossing never happens - measured, the first cut read 0 everywhere."""
    vals = [int(s[0]) for s in samples]
    if not vals:
        return 0
    mid = (min(vals) + max(vals)) / 2.0
    n, prev = 0, None
    for v in vals:
        if prev is not None and prev <= mid < v:
            n += 1
        prev = v
    return n


def _run(rom, frames=48):
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", sound_emulated=True)
    out = []
    try:
        # Boot + music_song. The shell makes a boot blip on channel 1 long
        # before the song starts (measured: NR12 = F3 from frame 0, and NR52
        # reports channel 1 ON for it), so neither "NR12 non-zero" nor "ch1
        # on" is the song. The song's notes write the envelope OFF (low
        # nibble 0, the lead instrument has no decay); the blip's is 3.
        for _ in range(400):
            pb.tick(1, False)
            nr12 = pb.memory[0xFF12]
            if (nr12 & 0xF0) and not (nr12 & 0x0F):
                break
        for _ in range(8):
            pb.tick(1, False)
        for _ in range(frames):
            pb.tick(1, False)
            out.append((pb.memory[0xFF12], (pb.memory[0xFF11] >> 6) & 3,
                        _cycles_per_frame(pb.sound.ndarray)))
    finally:
        pb.stop(save=False)
    return out


def test_rom():
    print("\n[ON A ROM: the table runs one row per tick]")
    from mosaik8_build import gbdk_available
    if not gbdk_available():
        print("  SKIP: GBDK is not installed")
        return
    try:
        import pyboy  # noqa: F401
    except ImportError:
        print("  SKIP: PyBoy is not installed")
        return
    tmp = tempfile.mkdtemp(prefix="subpat_")
    roms = {}
    for off in (False, True):
        try:
            root = _fixture(tmp, off)
        except Exception as exc:                # noqa: BLE001
            check("the %s fixture generates" % ("off" if off else "on"), False, exc)
            return
        r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"),
                            "build", "--platform", "gameboy", root],
                           capture_output=True, text=True)
        rom = os.path.join(root, "build", "gameboy", os.path.basename(root) + ".gb")
        if not os.path.isfile(rom):
            import glob
            found = glob.glob(os.path.join(root, "build", "gameboy", "*.gb"))
            rom = found[0] if found else None
        check("the %s fixture links" % ("off" if off else "on"),
              r.returncode == 0 and rom is not None,
              ((r.stdout or "") + (r.stderr or ""))[-800:])
        if rom is None:
            return
        roms[off] = rom
    on = _run(roms[False])
    off = _run(roms[True])
    vols = [v >> 4 for v, _d, _c in on]
    duties = [d for _v, d, _c in on]
    pitch = [c for _v, _d, c in on]
    print("    on : vol %s" % vols[:12])
    print("         duty %s" % duties[:12])
    print("         cycles/frame %s" % pitch[:12])
    print("    off: vol %s  cycles %s" % ([v >> 4 for v, _d, _c in off][:8],
                                          [c for _v, _d, c in off][:8]))

    def periodic4(seq):
        body = seq[8:40]
        return all(body[i] == body[i + 4] for i in range(len(body) - 4))
    check("the VOLUME row cycles with the table's period (F 4 4 4)",
          periodic4(vols) and sorted(vols[8:12]) == [4, 4, 4, 15], vols[8:16])
    check("the TIMBRE row writes the duty (0 3 3 3)",
          periodic4(duties) and sorted(duties[8:12]) == [0, 3, 3, 3], duties[8:16])
    # Pitch per TABLE PHASE, keyed on the volume row (phase 0 = the vol-15
    # row, whose Base pitch the table re-sets), as a MEDIAN over the window:
    # the vol row RETRIGGERS the channel, which restarts the duty cycle at a
    # frame edge and can read one odd frame (measured: a lone 1 among 5s).
    starts = [i for i in range(8, 40) if vols[i] == 15]
    by_phase = [[], [], [], []]
    for s in starts:
        for ph in range(4):
            if s + ph < len(pitch):
                by_phase[ph].append(pitch[s + ph])
    med = [sorted(p)[len(p) // 2] if p else 0 for p in by_phase]
    check("the PITCH row jumps an octave and the Base row returns",
          med[0] > 0 and med[1] > 0 and 1.6 <= med[2] / float(med[0]) <= 2.4
          and 1.6 <= med[3] / float(med[1]) <= 2.4,
          "median cycles per phase %s" % med)
    ov = set(v >> 4 for v, _d, _c in off[8:40])
    oc = [c for _v, _d, c in off[8:40]]
    check("CONTROL: with subpattern_off nothing moves (a plain note)",
          len(ov) == 1 and max(oc) - min(oc) <= 1, (ov, oc[:8]))


# --------------------------------------------------------------------------
# The POOLED consoles (Lynx, SMS, Game Gear, 2026-09-23): the same driver rules
# per melodic SLOT. Their chips' registers are not readable, so the check is
# the AUDIO the core plays, per video frame: rising zero crossings (pitch) and
# RMS (loudness). The table HOLDS each state for 4+ ticks, because a 2-tick
# state reads blurred (a core's audio buffer straddles frame edges; measured: an
# octave read +6..+10). One loop of 16 ticks:
#   rows 0..7   base, vol 15      rows 8..11   +12 (a PITCH row ALONE), still loud
#   rows 12..15 +12, vol 4 (a VOLUME row ALONE, re-sounding at the table's pitch)
# The pitch and volume rows are SEPARATE on purpose: a row carrying both let the
# volume row's re-sound retune the channel, and a driver whose pitch row wrote
# nothing passed (mutation-tested).
# --------------------------------------------------------------------------
POOL_INST = """[instrument.lead]
kind = "pulse"
duty = 2
vol = 15
%s
[[instrument.lead.subpattern]]
row = 0
pitch = 0
vol = 15
[[instrument.lead.subpattern]]
row = 8
pitch = 12
[[instrument.lead.subpattern]]
row = 12
vol = 4
[[instrument.lead.subpattern]]
row = 15
jump = 0
"""
POOL = (("lynx", ".lnx"), ("sms", ".sms"), ("gamegear", ".gg"))


def _pool_fixture(tmp, off):
    root = os.path.join(tmp, "pool_off" if off else "pool_on")
    shutil.copytree(os.path.join(ROOT, "projects", "vm-music"), root,
                    ignore=shutil.ignore_patterns("build"))
    sd = os.path.join(root, "scripts")
    for f in os.listdir(sd):
        if f.endswith(".evt.toml"):
            os.remove(os.path.join(sd, f))
    with open(os.path.join(sd, "main.evt.toml"), "w", encoding="utf-8") as f:
        f.write(MAIN)
    with open(os.path.join(sd, "songs.toml"), "w", encoding="utf-8") as f:
        f.write(SONG)
    with open(os.path.join(sd, "instruments.toml"), "w", encoding="utf-8") as f:
        f.write(POOL_INST % ("subpattern_off = true" if off else ""))
    prog = mosaik_vm.compile_path(sd)
    with open(os.path.join(root, "src", "scripts.mos"), "w", encoding="utf-8") as f:
        f.write(prog.to_scripts_mos())
    mosaik_vm.generate_songs(root)
    mosaik_vm.generate_instruments(root)
    mosaik_vm.generate_glue(root)
    if not off:
        # vm-music's shell wires the driver BY HAND (it never calls the glue's
        # setup), so it registers the tables itself, as such a project must.
        mp = os.path.join(root, "src", "main.mos")
        with open(mp, encoding="utf-8") as f:
            m = f.read()
        anchor = "        music.set_waves(instruments.wavebyte)"
        m = m.replace(anchor, anchor + "\n        music.set_subpatterns(instruments.sublen, "
                      "instruments.subrow)")
        with open(mp, "w", encoding="utf-8") as f:
            f.write(m)
    return root


def _capture(rom, frames=360):
    """[(rising zero crossings, rms)] per video frame of the core's audio. Run
    in a CHILD process (a libretro core is a process singleton)."""
    import numpy as np
    lib = os.path.join(ROOT, "emu", "libretro")
    sys.path.insert(0, lib)
    import run_lynx                                        # noqa: F401 (the NULL-frame patch)
    from libretro import SessionBuilder
    from libretro.drivers.path import ExplicitPathDriver
    from libretro.drivers.audio import ArrayAudioDriver
    core = run_lynx.CORE if rom.endswith(".lnx") else \
        os.path.join(lib, "genesis_plus_gx_libretro.dll")
    audio = ArrayAudioDriver()
    b = (SessionBuilder.defaults(core).with_content(rom)
         .with_paths(ExplicitPathDriver(corepath=core, system=lib, save=lib, assets=lib,
                                        playlist=lib))
         .with_audio(audio).with_perf(None))
    if not rom.endswith(".lnx"):
        # genesis_plus_gx renders NO audio unless the AV mask asks for it
        from libretro.api.av import AvEnableFlags
        b = b.with_av_mask(AvEnableFlags.VIDEO | AvEnableFlags.AUDIO)
    out = []
    with b.build() as sess:
        last = 0
        for _ in range(frames):
            sess.run()
            buf = audio._buffer
            w = np.array(buf[last:], dtype=np.float32).reshape(-1, 2).mean(axis=1)
            last = len(buf)
            x = w - (w.mean() if len(w) else 0)
            ups = int(np.count_nonzero((x[:-1] <= 0) & (x[1:] > 0))) if len(x) > 1 else 0
            out.append((ups, float(np.sqrt(np.mean(w * w))) if len(w) else 0.0))
    return out


def _capture_child(rom):
    import json
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--capture", rom],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith("CAPTURE "):
            return json.loads(line[8:])
    return None


def test_pooled_consoles():
    print("\n[the POOLED consoles (Lynx, SMS, GG) run the table per melodic slot]")
    from mosaik8_build import gbdk_available, cc65_available
    lib = os.path.join(ROOT, "emu", "libretro")
    try:
        import libretro  # noqa: F401
        import numpy     # noqa: F401
    except ImportError:
        print("  SKIP: libretro.py / numpy not installed")
        return
    tmp = tempfile.mkdtemp(prefix="subpat_pool_")
    roots = {off: _pool_fixture(tmp, off) for off in (False, True)}
    for plat, ext in POOL:
        core = "genesis_plus_gx_libretro.dll" if plat != "lynx" else None
        if plat == "lynx" and not cc65_available() or plat != "lynx" and not gbdk_available():
            print("  SKIP %s: no toolchain" % plat)
            continue
        if core and not os.path.isfile(os.path.join(lib, core)) or plat == "lynx" and not (
                os.path.isfile(os.path.join(lib, "mednafen_lynx_libretro.dll"))
                or os.path.isfile(os.path.join(lib, "handy_libretro.dll"))):
            print("  SKIP %s: no emulator core" % plat)
            continue
        caps = {}
        for off in (False, True):
            root = roots[off]
            r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                                "--platform", plat, root], capture_output=True, text=True)
            rom = os.path.join(root, "build", plat, "vm-music" + ext)
            if r.returncode or not os.path.isfile(rom):
                check("%s: the %s fixture links" % (plat, "off" if off else "on"), False,
                      ((r.stdout or "") + (r.stderr or ""))[-600:])
                break
            c = open(os.path.join(root, "build", plat, "vm-music.c"), encoding="utf-8",
                     errors="replace").read()
            if off:
                check("%s: no table code in a subpattern_off build" % plat,
                      "vm_music_sp_tick" not in c)
            else:
                check("%s: the table code is compiled in when a table plays" % plat,
                      "vm_music_sp_tick" in c and "vm_music_set_subpatterns(" in c)
            caps[off] = _capture_child(rom)
        if len(caps) < 2 or not caps[False] or not caps[True]:
            check("%s: audio captured" % plat, False, caps)
            continue
        # MEANS, not medians: a frame holds ~4.35 base cycles, so an integer
        # median reads 4 vs 9 (2.25) where the true ratio is 2.0
        mean = lambda v: sum(v) / float(len(v)) if v else 0     # noqa: E731
        offv = [(u, rm) for u, rm in caps[True][120:360] if u > 0]
        base = mean([u for u, _rm in offv])             # the plain note's crossings
        on = [(u, rm) for u, rm in caps[False][120:360] if u > 0]
        levels = sorted(rm for _u, rm in on)
        cut = (levels[0] + levels[-1]) / 2.0 if levels else 0
        loud = [u for u, rm in on if rm > cut]
        quiet = [u for u, rm in on if rm <= cut]
        loud_up = [u for u in loud if u > 1.5 * base]
        quiet_up = [u for u in quiet if u > 1.5 * base]
        share = lambda part, whole: len(part) / float(len(whole)) if whole else 0  # noqa: E731
        print("    %s on : %d frames; loud %d (%.0f%% an octave up), quiet %d (%.0f%% up); "
              "plain note %.2f crossings, octave frames %.2f" % (
                  plat, len(on), len(loud), 100 * share(loud_up, loud), len(quiet),
                  100 * share(quiet_up, quiet), base, mean(loud_up + quiet_up)))
        check("%s: the VOLUME row takes the level down (vol 15 -> 4), a quarter of the loop"
              % plat, levels and levels[-1] > 2.5 * levels[0]
              and 0.15 <= share(quiet, on) <= 0.35, (levels[:1], levels[-1:], len(quiet), len(on)))
        check("%s: a PITCH row ALONE retunes: a third of the LOUD frames are an octave up"
              % plat, 0.2 <= share(loud_up, loud) <= 0.45
              and 1.85 <= mean(loud_up) / base <= 2.15 if base and loud_up else False,
              (share(loud_up, loud), mean(loud_up), base))
        check("%s: the VOLUME row re-sounds at the table's pitch (the quiet frames are up)"
              % plat, share(quiet_up, quiet) >= 0.8, share(quiet_up, quiet))
        orms = [rm for _u, rm in offv]
        oups = sorted(u for u, _rm in offv)
        check("%s: CONTROL: subpattern_off plays one steady note" % plat,
              orms and max(orms) < 1.2 * min(orms) and oups[-1] - oups[0] <= 2,
              (min(orms) if orms else 0, max(orms) if orms else 0, oups[:1], oups[-1:]))


# --------------------------------------------------------------------------
# The REGISTRATION GUARD. The build states VM_MUSIC_SUBPAT off the glue's TEXT,
# and a hand-wired shell never calls the glue's setup, so the table code can
# compile in with NULL accessors. Measured without the guard: the first tabled
# note calls address 0 and the GB's stack runs away (SP 0x0077).
# --------------------------------------------------------------------------
def test_unregistered_tables():
    print("\n[a table compiled in but never REGISTERED plays plain, and does not crash]")
    from mosaik8_build import gbdk_available
    if not gbdk_available():
        print("  SKIP: GBDK is not installed")
        return
    try:
        from pyboy import PyBoy
    except ImportError:
        print("  SKIP: PyBoy is not installed")
        return
    import glob
    tmp = tempfile.mkdtemp(prefix="subpat_null_")
    root = _fixture(tmp, False)
    gp = os.path.join(root, "src", "glue.mos")
    with open(gp, encoding="utf-8") as f:
        g = f.read()
    line = "music.set_subpatterns(instruments.sublen, instruments.subrow)"
    g = g.replace(line, "")
    g = g.rstrip().rstrip("}") + "\n    function never_called() {\n        %s\n    }\n}\n" % line
    with open(gp, "w", encoding="utf-8") as f:
        f.write(g)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "gameboy", root], capture_output=True, text=True)
    roms = glob.glob(os.path.join(root, "build", "gameboy", "*.gb"))
    c = "".join(open(p, encoding="utf-8", errors="replace").read()
                for p in glob.glob(os.path.join(root, "build", "gameboy", "*.c")))
    check("the fixture is the hazard: table code IN, registration OUT",
          r.returncode == 0 and roms and "vm_music_sp_start" in c
          and "vm_music_set_subpatterns(" not in c, (r.returncode, len(roms)))
    if not roms:
        return
    pb = PyBoy(roms[0], window="null", sound_emulated=True)
    sp_min, nr12 = 0xFFFF, set()
    try:
        for _ in range(400):
            pb.tick(1, False)
            sp_min = min(sp_min, pb.register_file.SP)
            nr12.add(pb.memory[0xFF12] & 0xF0)
    finally:
        pb.stop(save=False)
    check("the stack stays sane (a call through a NULL accessor ran it away)",
          sp_min >= 0xC000, "SP min 0x%04X" % sp_min)
    check("the tabled note plays PLAIN (vol F, no table volume row)",
          0xF0 in nr12 and 0x40 not in nr12, sorted(nr12))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--capture"]:
        import json
        print("CAPTURE " + json.dumps(_capture(sys.argv[2])))
        sys.exit(0)
    print("Instrument SUBPATTERN checks")
    print("=" * 50)
    test_format()
    test_byte_identical_off()
    test_rom()
    test_pooled_consoles()
    test_unregistered_tables()
    print("\n%d passed, %d failed" % (passed, failed))
    if failed:
        print("FAILED")
        sys.exit(1)
