#!/usr/bin/env python3
"""A room change must leave NO animator armed from the previous room.

`vm.canim.sweep_retired` clears every slot's engine.anim animator on a room
load. It used to clear only the slots the NEW room leaves un-clipped
(`clip_of(i) == 255`), which leaks in the commoner shape: a slot the new room
DOES clip keeps the old room's animator until `tick_all` re-arms it, and
tick_all walks the VISIBLE subset only - so an actor that is off-window where
the new room starts is never walked, and the previous room's animator fires
`apply(i)` every frame for ever.

Measured on the reference-engine sample conversion: entering the long walk-in room
from the town room - the normal route in play - left two slots armed with the
town's count-1 animators. `canim.apply`, absent when the room is entered
directly, cost **11,062 cycles a frame** and took the room from **1.07 to
2.00 LCD/frame**: the 60 fps room was only 60 fps if you teleported into it.

ROM test on the first-party `projects/vm-offscreen` (built fresh from a temp
copy, relinked with `-Wl-j` for symbols). Its `yard` (room 1) is one screen
with twelve animated actors, ALL visible, so all twelve animators are armed;
its `road` (room 2) is a long corridor whose slots 6..11 are off-window at the
entry camera. Walking yard -> road is therefore exactly the leaking shape: a
slot the new room DOES clip, off-window where it starts, that the previous
room armed. The test jumps between rooms the engine's own way (RAISE 2) and
compares what a room arms when reached VIA another room against what it arms
when reached from a FRESH BOOT.

**The baseline has to be a save-state rewind, not just another `goto`.** A
leaked animator survives every subsequent room change, so once the machine is
contaminated both sides of the comparison carry it equally and the test
passes on the very build it is meant to catch (measured: it did). Rewinding
to a boot state is what makes "direct" mean direct.

Also pins `n_on == count(a_count > 0)` on every frame of a long route - a
count that reads LOW freezes an animation silently.

Skips cleanly without GBDK or PyBoy.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBJECT = "vm-offscreen"
#: The subject's rooms (projects/vm-offscreen/assets/gen.py). The START room
#: (0, `gate`) has NO actors, and that is load-bearing: the boot save-state
#: the test rewinds to is taken there, so "entered directly" really arms
#: nothing beforehand. A start room with actors would contaminate the
#: baseline exactly the way a leak does (measured: with the yard as the start
#: room, a leaking build passed the via/direct comparison).
YARD, ROAD = 1, 2
#: Where the room jump puts the player: the left edge, so the road's entry
#: camera is x = 0 and its slots 6.. start off-window.
SPAWN = (40, 72)
#: engine.anim's slot count for the subject (`VM_ANIM_SLOTS`, max(8,
#: actor_pool)).
SLOTS = 24

#: (room the player comes FROM, room they arrive in). yard -> road is the
#: leaking shape; road -> yard is the control (every yard slot is visible, so
#: it is re-armed however it was reached).
ROUTES = [(YARD, ROAD), (ROAD, YARD)]
#: Longer walk for the invariant check.
ROUTE = [ROAD, YARD, ROAD, YARD, YARD, ROAD, YARD, ROAD]


def build_subject(tmp):
    """Build a temp copy of the subject and relink it with -Wl-j.

    THE ROM AND ITS SYMBOLS MUST COME FROM THE SAME LINK: a `.noi` from an
    earlier link names the OLD addresses and every hook misses silently.
    `tools/framebudget/build_noi.py` re-runs the build's own lcc line with
    -Wl-j, so the pair is written together. Returns (rom, noi) or None with
    the build output printed."""
    proj = os.path.join(tmp, SUBJECT)
    shutil.copytree(os.path.join(ROOT, "projects", SUBJECT), proj,
                    ignore=shutil.ignore_patterns("build"))
    r = subprocess.run([sys.executable,
                        os.path.join(ROOT, "tools", "framebudget", "build_noi.py"),
                        proj], capture_output=True, text=True, cwd=ROOT,
                       encoding="utf-8", errors="replace")
    bdir = os.path.join(proj, "build", "gameboy")
    rom = os.path.join(bdir, SUBJECT + ".gb")
    noi = os.path.join(bdir, SUBJECT + ".noi")
    if r.returncode != 0 or not (os.path.isfile(rom) and os.path.isfile(noi)):
        print(((r.stdout or "") + (r.stderr or ""))[-3000:])
        return None
    return rom, noi

FAILS = []


def check(label, cond, note=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- %s" % note) if note else ""))
    if not cond:
        FAILS.append(label)


def main():
    sys.path.insert(0, ROOT)
    from mosaik8_build import gbdk_available
    if not gbdk_available():
        print("[SKIP] GBDK not installed - cannot build %s" % SUBJECT)
        return 0
    try:
        from pyboy import PyBoy
    except ImportError:
        print("[SKIP] PyBoy not installed")
        return 0
    tmp = tempfile.mkdtemp(prefix="animleak_")
    try:
        built = build_subject(tmp)
        check("the subject builds with symbols", built is not None)
        if built is None:
            return finish()
        return run(PyBoy, *built)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(PyBoy, ROM, NOI):
    sym = {}
    for line in open(NOI, encoding="utf-8", errors="replace"):
        m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
        if m:
            sym[m.group(1)] = int(m.group(2), 16)
    need = ("_engine_anim_n_on", "_engine_anim_a_count", "_vm_core_pend_code",
            "_vm_core_pend_a", "_vm_core_pend_b", "_vm_core_pend_c")
    missing = [n for n in need if n not in sym]
    check("the .noi names every symbol the probe reads", not missing,
          ", ".join(missing))
    if missing:
        return finish()
    g = lambda n: sym[n] & 0xFFFF

    pb = PyBoy(ROM, window="null")
    pb.set_emulation_speed(0)

    def w16(a, v):
        pb.memory[a], pb.memory[a + 1] = v & 0xFF, (v >> 8) & 0xFF

    def goto(room):
        pb.memory[g("_vm_core_pend_code")] = 2
        pb.memory[g("_vm_core_pend_a")] = room
        w16(g("_vm_core_pend_b"), SPAWN[0])
        w16(g("_vm_core_pend_c"), SPAWN[1])
        for _ in range(300):
            pb.tick()

    n_on, a_count = g("_engine_anim_n_on"), g("_engine_anim_a_count")

    def armed_set():
        return {i for i in range(SLOTS) if pb.memory[a_count + i] > 0}

    def armed():
        return len(armed_set())

    for _ in range(120):
        pb.tick()
    boot = io.BytesIO()
    pb.save_state(boot)

    def rewind():
        boot.seek(0)
        pb.load_state(boot)

    def arrive(rooms):
        """Fresh boot, then walk the room list; report what ends up armed."""
        rewind()
        for r in rooms[:-1]:
            goto(r)
            for _ in range(60):
                pb.tick()
        goto(rooms[-1])
        # Two frames is enough: the sweep runs at the top of the first tick_all.
        for _ in range(2):
            pb.tick()
        return pb.memory[n_on], armed()

    # THE SUBJECT MUST HAVE THE LEAKING SHAPE, or the comparison below
    # passes on a build that leaks: the yard arms slots that the road, entered
    # directly, leaves UN-armed (they are off-window at its entry camera).
    rewind()
    goto(YARD)
    for _ in range(2):
        pb.tick()
    yard = armed_set()
    rewind()
    goto(ROAD)
    for _ in range(2):
        pb.tick()
    road = armed_set()
    check("the yard arms slots a direct road entry leaves un-armed "
          "(else this proves nothing)", len(yard - road) >= 3,
          "yard %s, road %s" % (sorted(yard), sorted(road)))

    print("a room arms the same animators however you got there")
    for frm, to in ROUTES:
        d_on, d_armed = arrive([to])
        v_on, v_armed = arrive([frm, to])
        check("room %d reached via room %d arms what a direct entry arms"
              % (to, frm), v_on == d_on,
              "via %d: n_on %d, direct: %d" % (frm, v_on, d_on))
        check("... and n_on agrees with the array both ways",
              v_on == v_armed and d_on == d_armed,
              "via %d/%d, direct %d/%d" % (v_on, v_armed, d_on, d_armed))

    print("the n_on invariant holds every frame of a long route")
    rewind()
    drift = 0
    frames = 0
    for room in ROUTE:
        goto(room)
        for _ in range(90):
            pb.tick()
            frames += 1
            if pb.memory[n_on] != armed():
                drift += 1
    check("n_on == count(a_count > 0) on all %d frames" % frames, drift == 0,
          "%d drifts" % drift)

    pb.stop(save=False)
    return finish()


def finish():
    print("\n" + ("All checks passed" if not FAILS
                  else "SOME CHECKS FAILED: %s" % FAILS))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
