"""Does the shipped hUGEDriver survive a `6xy` (call routine) with a NULL
`routines` table?

Both `mosaik_vm/huge.py` and `mosaik_vm/uge.py` emit `NULL` for
`hUGESong_t`'s `routines` field, and the bit-true `.uge` path packs the
per-cell effect verbatim (`render_uge_c`: `DN(note, inst, 0x<eff><par>)`), so a
module that authors `6xy` reaches the driver with no table at all.

Read off the driver's own source first (`hUGEDriver.asm`; the vendored object
is built from it):

  * `do_effect` returns early ONLY when the effect code and the parameter are
    both zero, so `600` dispatches like any other effect;
  * `fx_call_routine` has NO null test - it computes `routines + 2 * id`,
    loads the word there and `jp hl`;
  * the word at a GBDK image's ROM 0x0000 is 0xFFFF.

**The 2026-09-19 change made the effect real, but only for a project that ATTACHES
a routine** (`projects/vm-musicroutine` is the sample and the gate). This probe
measures the OTHER half of that rule on the same sample with its routines
DETACHED (every `music_routine` event removed from a temp copy, so the blob
carries no MUSIC_ROUTINE and the song descriptor keeps a NULL table): with a
NULL table the cell must still be stripped, because the crash below is
exactly what it costs not to be.

This checks that reading against a real ROM. It copies that `gb = "huge"`
project, detaches its routines, writes `600` into row 0 of the first pattern its song orders (through
`uge.cell_offsets`, so the file walk stays in the one .uge reader), builds,
and runs the patched ROM beside the unpatched one - reporting the picture, the
stack pointer range and the APU's activity for both. A runaway `jp` shows up
as a collapsing stack: ROM 0x0000 is 0xFF, which is `RST 0x38`, so the CPU
recurses into itself until the stack walks over WRAM.

    python tools/musicprobe/routine_null_probe.py [--inject-null-call]

The APU column needs PyBoy's sound emulation and READABLE registers (see
`APU_REGS`); before 2026-09-23 it had neither and read one state on every
ROM. What it can and cannot tell, measured on this sample:

  * it proves the song is PLAYING (the plain ROM's APU still changes in the
    last quarter of the run, all four channels sound), and the probe refuses
    to report when it is not - a silent ROM beside a silent ROM agrees for a
    reason that has nothing to do with the routines table;
  * with `--inject-null-call` (the `6xy` ROM built WITHOUT the strip) the
    crash lands on the song's FIRST row, so that ROM never sounds more than
    the driver's boot blip (channels 0x1 against 0xF). That is the same fact
    the stack pointer reports; the APU column corroborates it, it does not
    see anything the stack cannot here.

Exit 0 = measured and reported (whatever the answer); 2 = could not measure;
1 = `--inject-null-call` and the probe did NOT see the crash (the instrument
is broken).
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, ROOT)

PROJECT = "vm-musicroutine"     # small, one .uge, already `gb = "huge"`
#: A project whose boot script does not play music gets a `music_song`
#: PREPENDED: the driver has to be ticking before the patched row can be
#: reached at all. Measured without this the APU never leaves its power-on
#: state and both ROMs agree for a reason that has nothing to do with the
#: routines table. (The sample's own boot script already plays its song.)
BOOT_MUSIC = '''[[script]]
name = "__probe_boot"
[[script.events]]
event = "music_song"
song = "%s"

'''
#: The READABLE APU registers: NR11/NR21 (duty), NR12/NR22/NR42 (envelope),
#: NR32 (wave level), NR43 (noise), NR50/NR51 (volume, panning), NR52
#: (power + channel-on bits). A driver that is still ticking rewrites them on
#: every note, and a dead CPU cannot.
#:
#: MEASURED 2026-09-23, why the column used to show ONE state on both ROMs:
#: (1) PyBoy was built with `sound_emulated=False`, and then EVERY APU
#: register reads 0x00, NR52 included, on a healthy ROM; (2) three of the
#: four registers read (NR13/NR23/NR33, the period lows) are WRITE-ONLY on
#: the hardware (Pan Docs, Audio_Registers.md) and PyBoy with sound on reads
#: them as 0xFF for ever. Only NR43 could ever have moved.
APU_REGS = (0xFF11, 0xFF12, 0xFF16, 0xFF17, 0xFF1C, 0xFF21, 0xFF22,
            0xFF24, 0xFF25, 0xFF26)
NR52 = 0xFF26
#: A stack pointer below this is a runaway (a GBDK GB program keeps its stack
#: at the top of WRAM; the healthy sample holds 0xDFE3..0xFFFE). The run STOPS
#: there, because PyBoy stops returning from `tick()` a frame or so into the
#: collapse (measured: SP 0xCCC0 at frame 71, and the next tick never came
#: back - the probe hung for ten minutes).
STACK_FLOOR = 0xD000
#: A song that is really playing is still rewriting the APU in the last
#: quarter of the run (measured: last change at frame 882 of 900). A ROM
#: whose APU went quiet before that never started its song, and then both
#: ROMs agree for a reason that has nothing to do with the routines table.
LIVE_TAIL = 0.75


def patch_six(path, param=0, effect=6):
    """Write `<effect><param>` into row 0 of the first pattern the module
    ORDERS (`6<param>` by default; the plain copy gets `000`).

    The plain copy is patched too, because the sample ALREADY authors `6xy`
    cells (row 0 reads `651`): detached, the strip keeps the PARAMETER, so
    that cell packs as `051` - an arpeggio - while the patched `600` packs as
    `000`, and with a live APU column the two ROMs then differ for a reason
    that has nothing to do with the routines table. With `000` beside `600`
    a working strip makes the two ROMs byte-identical.

    Returns (pattern id, row) so the caller can say what it changed."""
    from mosaik_vm import uge
    raw = bytearray(open(path, "rb").read())
    m = uge.parse(bytes(raw))
    order_len = min(len(o) for o in m.orders) if m.orders else 0
    if not order_len:
        raise SystemExit("%s orders no patterns" % path)
    pid = m.orders[0][0]
    eff_at, par_at = uge.cell_offsets(m, pid, 0)
    struct.pack_into("<I", raw, eff_at, effect)
    raw[par_at] = param & 0xFF
    open(path, "wb").write(bytes(raw))
    # Read it back through the same parser: a write nobody re-parses is a guess.
    back = uge.parse(bytes(raw)).patterns[pid][0]
    if back[2] != effect:
        raise SystemExit("the patch did not land (row 0 reads %r)" % (back,))
    return pid, 0


def detach_routines(proj):
    """Remove every `music_routine` event from the copy's scripts, so the
    project attaches NO routine and its song keeps a NULL `routines` table.
    Refuses when there was nothing to remove: then the copy is not the
    subject this probe describes."""
    removed = 0
    sdir = os.path.join(proj, "scripts")
    for name in os.listdir(sdir):
        if not name.endswith(".evt.toml"):
            continue
        path = os.path.join(sdir, name)
        lines = open(path, encoding="utf-8").read().splitlines(True)
        keep = [ln for ln in lines
                if not ln.lstrip().startswith('{ event = "music_routine"')]
        removed += len(lines) - len(keep)
        open(path, "w", encoding="utf-8", newline="").write("".join(keep))
    if not removed:
        raise SystemExit("%s attaches no routine - nothing to detach" % proj)
    return removed


def plays_music_at_boot(proj):
    """Does the copy's `main` script already start a song?"""
    main = os.path.join(proj, "scripts", "main.evt.toml")
    text = open(main, encoding="utf-8").read()
    body = text.split('name = "main"', 1)[-1].split("[[script]]", 1)[0]
    return "music_song" in body


def start_music_at_boot(proj, song):
    """Make the project's `main` script play `song` as its first event."""
    main = os.path.join(proj, "scripts", "main.evt.toml")
    text = open(main, encoding="utf-8").read()
    head = '[[script]]\nname = "main"\n'
    if head not in text:
        raise SystemExit("%s has no `main` script to seed" % main)
    seed = ('[[script.events]]\nevent = "music_song"\nsong = "%s"\n\n' % song)
    text = text.replace(head, head + seed, 1)
    open(main, "w", encoding="utf-8", newline="").write(text)


def regen_scripts(proj):
    """Recompile `scripts/*.evt.toml` into `src/scripts.mos` (the build does
    not: the studio owns that step)."""
    out = os.path.join(proj, "src", "scripts.mos")
    r = subprocess.run([sys.executable, "-m", "mosaik_vm",
                        os.path.join(proj, "scripts"), "-o", out],
                       capture_output=True, text=True, cwd=ROOT,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SystemExit("regenerating scripts.mos failed:\n%s"
                         % (r.stderr or r.stdout)[-2000:])


#: The generated modules besides scripts.mos, and the generator owning each.
#: Detaching the routines changes what `glue.mos` wires (the routine seam)
#: and what `songs.mos` packs, so the WHOLE set is refreshed, never
#: scripts.mos alone (tools/regen_generated.py: generated modules go stale
#: together).
VM_GENERATORS = (("rooms", "generate_rooms"), ("glue", "generate_glue"),
                 ("clips", "generate_clips"), ("songs", "generate_songs"),
                 ("instruments", "generate_instruments"),
                 ("emotes", "generate_emotes"), ("hud", "generate_hud"))


def regen_generated(proj):
    """Recompile the scripts, then refresh every generated module the copy
    already has (never adds one it was built without)."""
    import mosaik_vm
    regen_scripts(proj)
    for module, fn in VM_GENERATORS:
        if os.path.isfile(os.path.join(proj, "src", module + ".mos")):
            getattr(mosaik_vm, fn)(proj)


def run(PyBoy, rom, frames):
    # SOUND EMULATION ON: with it off every APU register reads 0x00 and the
    # APU column is one state on any ROM (see APU_REGS).
    pb = PyBoy(rom, window="null", sound_emulated=True)
    seen, apu = set(), set()
    sp_lo, sp_hi = 0xFFFF, 0
    last_change = apu_change = 0
    prev = prev_apu = None
    channels = 0
    stopped = None
    for n in range(frames):
        pb.tick(1, True)
        h = hash(bytes(pb.screen.ndarray))
        seen.add(h)
        if h != prev:
            last_change = n + 1
            prev = h
        sp = pb.register_file.SP
        sp_lo, sp_hi = min(sp_lo, sp), max(sp_hi, sp)
        if sp < STACK_FLOOR:
            stopped = n + 1
            break
        regs = tuple(pb.memory[a] for a in APU_REGS)
        apu.add(regs)
        if regs != prev_apu:
            apu_change = n + 1
            prev_apu = regs
        channels |= pb.memory[NR52] & 0x0F
    pb.stop(save=False)
    return {"frames": frames, "distinct": len(seen), "live_to": last_change,
            "apu": len(apu), "apu_to": apu_change, "channels": channels,
            "sp": (sp_lo, sp_hi), "stopped": stopped}


def describe(r):
    text = ("%d distinct screens (last change frame %d), %d distinct APU "
            "states (last change frame %d, channels sounded 0x%X), "
            "SP 0x%04X..0x%04X"
            % (r["distinct"], r["live_to"], r["apu"], r["apu_to"],
               r["channels"], r["sp"][0], r["sp"][1]))
    if r["stopped"]:
        text += ", STACK COLLAPSED at frame %d (run stopped)" % r["stopped"]
    return text


#: `--inject-null-call` builds the `6xy` copy with `uge._fx` returning every
#: effect verbatim, in the BUILD's own process (the build packs the song, so
#: patching this process would change nothing). That is the pre-strip packer:
#: the `600` reaches a driver whose `routines` table is NULL.
_INJECT = ("import sys, runpy; sys.path.insert(0, %r); "
           "import mosaik_vm.uge as u; "
           "u._fx = lambda m, eff, routines=False: eff; "
           "sys.argv = ['mosaik8.py'] + sys.argv[1:]; "
           "runpy.run_path(%r, run_name='__main__')")


def build(path, platform, strip=True):
    args = ["build", "--platform", platform, path]
    if strip:
        cmd = [sys.executable, os.path.join(ROOT, "mosaik8.py")] + args
    else:
        cmd = [sys.executable, "-c",
               _INJECT % (ROOT, os.path.join(ROOT, "mosaik8.py"))] + args
    return subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT,
                          encoding="utf-8", errors="replace")


def packed_null_call(out_dir):
    """Does any generated hUGE song source in `out_dir` carry a `6xy` cell?
    (`DN(note, inst, 0x6..)`: the effect nibble is the third hex digit from
    the right.)"""
    import re
    for name in os.listdir(out_dir):
        if name.startswith("songs_huge") and name.endswith(".c"):
            text = open(os.path.join(out_dir, name), encoding="utf-8").read()
            if re.search(r"DN\(\d+,\d+,0x6[0-9A-Fa-f]{2}\)", text):
                return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=900)
    ap.add_argument("--platform", default="gameboy")
    ap.add_argument("--inject-null-call", action="store_true",
                    help="build the `6xy` ROM WITHOUT the strip (the "
                         "pre-fix packer): the probe must then see the crash")
    args = ap.parse_args()
    inject = args.inject_null_call

    from mosaik8_build import gbdk_available
    if not gbdk_available():
        print("SKIP: no GBDK toolchain")
        return 2
    try:
        from pyboy import PyBoy
    except Exception as exc:                        # noqa: BLE001
        print("SKIP: no PyBoy (%s)" % exc)
        return 2

    tmp = tempfile.mkdtemp(prefix="routine_null_")
    try:
        roms = {}
        for label in ("plain", "6xy"):
            proj = os.path.join(tmp, label, PROJECT)
            os.makedirs(os.path.dirname(proj), exist_ok=True)
            shutil.copytree(os.path.join(ROOT, "projects", PROJECT), proj,
                            ignore=shutil.ignore_patterns("build"))
            music = os.path.join(proj, "assets", "music")
            uge_path = os.path.join(
                music, [f for f in os.listdir(music) if f.endswith(".uge")][0])
            song = os.path.splitext(os.path.basename(uge_path))[0]
            detach_routines(proj)
            if not plays_music_at_boot(proj):
                start_music_at_boot(proj, song)
            effect = 6 if label == "6xy" else 0
            pid, _row = patch_six(uge_path, effect=effect)
            print("patched %s pattern %d row 0 to `%d00`"
                  % (os.path.basename(uge_path), pid, effect))
            regen_generated(proj)
            strip = not (inject and label == "6xy")
            r = build(proj, args.platform, strip=strip)
            if r.returncode != 0:
                print("FAIL: %s build failed" % label)
                print(r.stdout[-4000:].encode("ascii", "replace").decode())
                return 2
            out = os.path.join(proj, "build", args.platform)
            if packed_null_call(out) == strip:
                # A mutation that changes nothing MISSED: the injected build
                # must carry the cell, and a stripping build must not.
                print("could not measure: the %s build %s a `6xy` cell"
                      % (label, "carries" if strip else "does NOT carry"))
                return 2
            roms[label] = os.path.join(
                out, [f for f in os.listdir(out)
                      if f.endswith((".gb", ".gbc"))][0])
        with open(roms["plain"], "rb") as f:
            head = f.read(2)
        print("ROM word at 0x0000 = 0x%04X (where a NULL routines table sends "
              "`jp hl`)" % (head[0] | (head[1] << 8)))
        identical = (open(roms["plain"], "rb").read()
                     == open(roms["6xy"], "rb").read())
        print("the two ROMs are %sbyte-identical"
              % ("" if identical else "NOT "))

        res = {k: run(PyBoy, v, args.frames) for k, v in roms.items()}
        for k in ("plain", "6xy"):
            print("  %-5s %s" % (k, describe(res[k])))

        a, b = res["plain"], res["6xy"]
        if a["stopped"] or a["apu_to"] < LIVE_TAIL * args.frames:
            print("could not measure: the plain ROM's music is not playing "
                  "(its APU last changed at frame %d of %d), so a silent "
                  "6xy ROM would look the same" % (a["apu_to"], args.frames))
            return 2
        same = all(a[k] == b[k] for k in ("sp", "distinct", "apu", "apu_to",
                                          "channels", "stopped"))
        if inject:
            if same:
                print("INSTRUMENT FAILURE: the null-call build was injected "
                      "and the probe saw nothing - it cannot see the crash "
                      "it exists for.")
                return 1
            print("INJECTED: the pre-strip `6xy` ROM diverges as it must "
                  "(the instrument sees the crash; this is not a finding).")
            return 0
        if same:
            print("MEASURED: the `6xy` ROM behaves exactly as the plain one "
                  "over %d frames. That is STILL the expected result for THIS "
                  "project: its routines are DETACHED, so its "
                  "song descriptors keep a NULL `routines` table and "
                  "`uge._fx` strips the effect (`FX_CALL_ROUTINE`). The "
                  "2026-09-19 change did not alter that - it changed what happens when a project "
                  "DOES attach one (the sample as shipped), where the "
                  "table is real and the cell is carried verbatim. To "
                  "reproduce the original crash, run this again with "
                  "--inject-null-call - the stack pointer collapses."
                  % args.frames)
        else:
            print("MEASURED: the `6xy` ROM DIVERGES - the effect reached the "
                  "driver and a NULL `routines` table is not guarded: "
                  "`fx_call_routine` jumps through the word at ROM 0x0000. "
                  "On a project with NO music routine that is a defect: "
                  "either the strip in `uge._fx` is gone, or something is "
                  "emitting a routines table for a project that attaches "
                  "none, or a second path packs the cell.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
