#!/usr/bin/env python3
"""vm-musicroutine -- W7h + D9: a `6xy` cell runs a VM8 script, measured on a ROM.

    python projects/vm-musicroutine/verify.py [--platform gameboy] [--arm huge|vm|both]

TWO ARMS, one subject (D9, 2026-09-23). The `huge` arm is the project as it
ships: the GB plays `assets/music/routine.uge` through hUGEDriver. The `vm`
arm builds a TEMP COPY whose `[audio] gb` is OUR driver, vm.music, which plays
the portable twin `scripts/songs.toml` (same rows, the `6xy` cells as effect
15) and feeds the same core drain through its own queue. Every check below
runs on both, each against its own driver clock - so the busy gate, the
per-slot gate, the argument and the unattached-slot survival are proven for
the portable effect column, not only for the bit-true path. Default: both.

There is nothing on screen: the whole subject is four VM8 variables, read out
of WRAM through `_vm_core_heap` (the falling-block assembly sample idiom), and the driver's own
clock is what advances the test. That makes this an unusually direct gate -
every check below is a number the interpreter wrote because hUGEDriver reached
a row of a pattern.

The song is `assets/music/routine.uge`, written by `assets/gen_music.py`, and
what each of its rows is FOR is spelled out there and in
`scripts/main.evt.toml`. In short:

    row  0   6 51   slot 1, argument 5   -> `hit` runs, and it runs LONG
    row 16   6 21   slot 1, argument 2   -> the BUSY GATE must drop this
    row 32   6 32   slot 2, argument 3   -> a different slot still fires
    row 48   6 40   slot 0, unattached   -> the drain survives it

THE CLOCK, and it is worth getting right the first time. The driver ticks at
64 Hz and the module is 6 ticks a row: 10.67 rows a second, which against a
59.7 fps LCD is **5.6 display frames per row** and 358 for the whole 64-row
pattern. So

    row  0 -> frame   0        row 32 -> frame 179
    row 16 -> frame  90        row 48 -> frame 269      loop -> frame 358

The `vm` arm's clock is vm.music's: one tick per display frame, so a 6-frame
row is exactly 6 frames and the pattern 384 - row 16 at 96, row 32 at 192.
`hit` (180 frames) still covers row 16 and has ended by row 0 of pass 2.

and `hit`'s 180-frame wait covers rows 0..32 of a pass. The clock is ANCHORED
ON THE FIRST FIRE rather than on boot (the shell takes ~70 frames to reach
`music_song`, and that number is not a property of this feature), and every
window after it is an offset in the song's own rows, with slack. The point of
a check is the COUNT it reads, never the exact frame it read it on. (A first
cut used 56 frames a row - the same digits with the decimal point in the wrong
place - and every window landed two passes late, which reads EXACTLY like a
busy gate that does not hold. The counts had been right the whole time.)

TWO TRAPS THIS INHERITS from the step-0 probe
(`tools/musicprobe/routine_null_probe.py`), both live here too:

  * THE MUSIC HAS TO BE PLAYING. The routine cannot fire before the song does,
    and a boot script that forgets `music_song` gives four zeroes for a reason
    that has nothing to do with the feature. `main` plays it, and the first
    check reads the APU rather than trusting it.
  * A RUNAWAY LOOKS LIKE A PASS from the outside. A `6xy` reaching a NULL
    routines table crashes the ROM (measured: the stack collapses from 0xDF8B
    to 0x0017), and a crashed ROM also writes no counters. The stack-pointer
    check is what tells "nothing happened" from "everything stopped".
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

#: 64 Hz driver, 6 ticks a row -> 10.67 rows a second at 59.7 LCD fps.
FRAMES_PER_ROW = 59.7 / (64.0 / 6.0)
PATTERN_ROWS = 64

passed = failed = 0


#: vm.music ticks once per display frame and the portable twin's rows are 6
#: frames long (gen_music.TICKS), so its clock is exact.
FRAMES_PER_ROW_VM = 6.0
_fpr = [FRAMES_PER_ROW]


def at_row(row):
    """The display frame a pattern row lands on, from the song's first tick,
    on the clock of the arm being run."""
    return int(row * _fpr[0])


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAILED] %s%s" % (label, ("  -- " + detail) if detail else ""))


def relink_with_symbols(build_dir, platform, huge=True):
    """Relink the project's own C into `probe.<ext>` WITH a symbol file.

    A plain build emits no `.noi` (only a banked one asks the linker for a
    map), and the heap's WRAM address is the only thing this sample reads. So
    the check relinks rather than asking the user to - the falling-block assembly sample's verify
    documents the same relink as a MANUAL step in its docstring, and a manual
    step is exactly what rots once a file joins a suite. Returns the probe
    ROM's path, or None when GBDK is not installed (the caller skips politely).

    The flags mirror `mosaik8_build.compile_with_gbdk`'s: the hUGEDriver
    include and the prebuilt object are not optional here - this project links
    the real driver.
    """
    import subprocess
    from mosaik8_build import gbdk_available, tool_prefix
    if not gbdk_available():
        return None
    prefix = tool_prefix()
    gbdk = None
    for cand in (os.environ.get("GBDK_HOME"),
                 os.path.join(prefix, "gbdk") if prefix else None,
                 os.path.join(ROOT, "gbdk")):
        if cand and os.path.isdir(os.path.join(cand, "include")):
            gbdk = cand
            break
    if gbdk is None:
        return None
    lcc = os.path.join(gbdk, "bin", "lcc" + (".exe" if os.name == "nt" else ""))
    if not os.path.isfile(lcc):
        return None
    vendor = os.path.join(ROOT, "vendor", "hugedriver")
    obj = os.path.join(vendor, "hUGEDriver_gb.o")
    c_files = sorted(f for f in os.listdir(build_dir) if f.endswith(".c"))
    if not c_files or (huge and not os.path.isfile(obj)):
        return None
    ext = "gb" if platform == "gameboy" else "gbc"
    out = os.path.join(build_dir, "probe." + ext)
    cmd = [lcc, "-msm83:gb", "-Wm-yt0x19", "-Wl-j",
           "-I" + vendor, "-I" + os.path.join(gbdk, "include"),
           "-o", out] + [os.path.join(build_dir, f) for f in c_files]
    if huge:                        # the vm arm links no hUGEDriver at all
        cmd.append(obj)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(out):
        print("SKIP: the symbol relink failed:\n%s"
              % (r.stderr or r.stdout)[-800:])
        return None
    return out


def heap_base(build_dir, stem):
    """`_vm_core_heap`'s address, off the linker's own symbol file."""
    path = os.path.join(build_dir, stem + ".noi")
    if not os.path.exists(path):
        return None
    m = re.search(r"DEF _vm_core_heap (0x[0-9A-Fa-f]+)",
                  open(path, encoding="utf-8", errors="replace").read())
    return int(m.group(1), 16) if m else None


def cells():
    """name -> heap cell, asked of the COMPILER rather than hardcoded: the
    allocation is first-use order and a reordered event list would move it."""
    from mosaik_vm import loader
    return loader.compile_path(os.path.join(HERE, "scripts")).variables


def build_vm_arm(platform):
    """A TEMP COPY of this project with the GB family on OUR driver: `[audio]
    gb = "vm"`, its generated songs/glue regenerated (the glue now registers
    `music.routine_next`), built. Returns the build dir, or None (skip)."""
    import shutil
    import subprocess
    import tempfile
    import mosaik_vm
    tmp = tempfile.mkdtemp(prefix="musicroutine_vm_")
    root = os.path.join(tmp, "vm-musicroutine")
    shutil.copytree(HERE, root, ignore=shutil.ignore_patterns("build"))
    studio = os.path.join(root, "studio.toml")
    with open(studio, "w", encoding="utf-8") as f:
        f.write('[audio]\ngb = "vm"\n')
    mosaik_vm.generate_songs(root)
    mosaik_vm.generate_glue(root)
    glue = open(os.path.join(root, "src", "glue.mos"), encoding="utf-8").read()
    if "music.routine_next" not in glue or "music_huge" in glue:
        print("SKIP (vm arm): the regenerated glue does not wire vm.music's "
              "routine queue")
        return None
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", platform, root],
                       capture_output=True, text=True)
    build = os.path.join(root, "build", platform)
    if r.returncode != 0 or not os.path.isdir(build):
        print("FAILED (vm arm): the build did not link:\n%s"
              % ((r.stdout or "") + (r.stderr or ""))[-1500:])
        return False
    return build


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="gameboy")
    ap.add_argument("--arm", default="both", choices=("huge", "vm", "both"))
    args = ap.parse_args()
    try:
        import pyboy  # noqa: F401
    except ImportError:
        print("SKIP: PyBoy is not installed")
        return 0
    var = cells()
    for name in ("runs", "arg0", "other", "after"):
        if name not in var:
            print("SKIP: the scripts no longer declare '%s'" % name)
            return 0
    if args.arm in ("huge", "both"):
        print("\n######## the huge arm: hUGEDriver plays routine.uge ########")
        build = os.path.join(HERE, "build", args.platform)
        ext = "gb" if args.platform == "gameboy" else "gbc"
        if not os.path.isfile(os.path.join(build, "vm-musicroutine." + ext)):
            print("SKIP: vm-musicroutine.%s not built" % ext)
        else:
            _fpr[0] = FRAMES_PER_ROW
            run_arm(build, args.platform, var, huge=True)
    if args.arm in ("vm", "both"):
        print("\n######## the vm arm: OUR driver plays songs.toml (D9) ########")
        build = build_vm_arm(args.platform)
        if build is False:
            global failed
            failed += 1
        elif build is not None:
            _fpr[0] = FRAMES_PER_ROW_VM
            run_arm(build, args.platform, var, huge=False)
    print("\n%d passed, %d failed" % (passed, failed))
    if failed:
        print("FAILED")
        return 1
    print("All checks passed")
    return 0


def run_arm(build, platform, var, huge):
    from pyboy import PyBoy
    rom = relink_with_symbols(build, platform, huge=huge)
    if rom is None:
        print("SKIP: could not relink for symbols (GBDK not installed?)")
        return
    base = heap_base(build, "probe")
    if base is None:
        print("SKIP: no _vm_core_heap in the relinked symbol file")
        return

    # SOUND EMULATION ON, deliberately: PyBoy leaves the APU registers
    # unemulated without it, so NR52 reads 0x00 on a perfectly healthy
    # ROM and the one check that says "the driver really is running"
    # fails for a reason that has nothing to do with this feature.
    pb = PyBoy(rom, window="null", sound_emulated=True)
    frame = [0]

    def read(name):
        a = base + var[name] * 2
        return pb.memory[a] | (pb.memory[a + 1] << 8)

    def run_to(target):
        """Advance to an ABSOLUTE display frame from boot."""
        while frame[0] < target:
            pb.tick(1, False)
            frame[0] += 1

    try:
        # ANCHOR ON THE FIRST FIRE, not on boot. The boot + `music_song` take
        # ~70 display frames to reach the driver on this ROM, and that number
        # is not a property of the feature - it moves with the shell. So the
        # clock starts where row 0 lands, and everything after it is an offset
        # in the song's own rows.
        while read("runs") == 0 and frame[0] < 300:
            pb.tick(1, False)
            frame[0] += 1
        t0 = frame[0]
        print("\n== the song plays, and row 0 fires the routine ==")
        check("slot 1's script ran", read("runs") == 1,
              "runs=%d after %d frames" % (read("runs"), t0))
        check("it was handed the effect's own argument", read("arg0") == 5,
              "arg0=%d, wanted 5 (the `6 51` cell's high nibble)" % read("arg0"))
        check("no other slot has run yet",
              read("other") == 0 and read("after") == 0,
              "other=%d after=%d" % (read("other"), read("after")))
        nr52 = pb.memory[0xFF26]
        check("the APU is powered on and channels are sounding",
              (nr52 & 0x80) != 0 and (nr52 & 0x0F) != 0,
              "NR52=0x%02X" % nr52)
        sp = pb.register_file.SP
        check("the stack is where a running game keeps it",
              0x8000 <= sp <= 0xFFFE,
              "SP=0x%04X (a NULL routines jump collapses it to ~0x0017)" % sp)

        print("\n== row 16: the BUSY GATE drops a second fire ==")
        # Row 16 is 90 frames after row 0; `hit` waits 180 VM frames, so it is
        # still alive and the gate is the only thing that can stop the spawn.
        run_to(t0 + at_row(16) + 30)
        check("the second `6x1` did NOT spawn a second instance",
              read("runs") == 1,
              "runs=%d (2 means the gate is open)" % read("runs"))
        check("...and the first instance's argument was not overwritten",
              read("arg0") == 5,
              "arg0=%d (2 is row 16's argument arriving)" % read("arg0"))

        print("\n== row 32: a DIFFERENT slot is gated on its own ==")
        run_to(t0 + at_row(32) + 30)
        check("slot 2's script ran", read("other") >= 1,
              "other=%d" % read("other"))
        check("...while slot 1 is still held", read("runs") == 1,
              "runs=%d" % read("runs"))
        check("slot 3, which the song never names, never ran",
              read("after") == 0, "after=%d" % read("after"))

        print("\n== the pattern loops, and the ROM is still running ==")
        # Past row 48 (the unattached slot 0, which aborts that frame's drain)
        # and round to row 0 of pass 2: `hit` ended at ~180 frames, so slot 1
        # fires afresh.
        run_to(t0 + at_row(PATTERN_ROWS) + 40)
        check("slot 1 fired again on the next pass", read("runs") == 2,
              "runs=%d" % read("runs"))
        run_to(t0 + at_row(PATTERN_ROWS * 3) + 40)
        runs = read("runs")
        check("...and at most once a pass, over three passes", runs <= 4,
              "runs=%d; the gate stops holding above 4" % runs)
        check("slot 3 is STILL untouched", read("after") == 0,
              "after=%d" % read("after"))
        sp = pb.register_file.SP
        check("the stack is still healthy after the unattached row 48",
              0x8000 <= sp <= 0xFFFE, "SP=0x%04X" % sp)
    finally:
        pb.stop(save=False)


if __name__ == "__main__":
    sys.exit(main())
