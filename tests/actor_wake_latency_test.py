#!/usr/bin/env python3
"""A parked actor the camera has reached is back on the live list in time.

`vm.actor.wake_scan` is what brings an actor back after `[build]
actor_deactivate` parked it, and 2026-09-03 it stopped being a phase-tested
walk of the whole parked list and became a ROLLING CURSOR over
`n_plist / actor_scan` entries a frame. The contract it has to keep is the
knob's own: a parked slot is re-tested at least once every `actor_scan`
frames, so a slot the camera has reached is drawn again within that many
game frames.

The source-shape half is in `actor_deactivate_test.py`; this is the half a
shape cannot answer. It builds the first-party `projects/vm-offscreen` (a
fresh temp copy, relinked with `-Wl-j` for symbols) and walks its `road`
(room 2): a corridor 80 tiles wide with twenty-four animated actors
standing 24 px apart, eighteen of them parked off to the right of the entry
camera. The test HOLDS RIGHT, so the camera scrolls over the parked slots one
after another, and asserts per GAME frame that no slot sits parked while its
position is on the window by the terms that do NOT depend on how wide it is
drawn: `wx >= cx` (the exact left margin is the sprite's own drawn width,
which this test does not re-derive) and `wx <= cx + 160`, which IS
`off_window`'s own right-hand term. The right edge is where a rightward
scroll brings every parked slot in, so the count starts on the frame the
engine's rule says the slot is back - a sweep that visits it late shows as
lateness, not only a sweep that forgets it.

Nothing in the room is scripted, so the camera is the ONLY thing that can
wake a slot: a sweep that forgets one is visible as that slot staying parked.

Skips cleanly without GBDK or PyBoy.
"""
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
#: The road (see projects/vm-offscreen/assets/gen.py).
ROOM = 2
#: The spawn the room jump hands the player: the room's left edge, so the
#: entry camera is x = 0 and most of the pool starts parked to the right.
SPAWN = (40, 72)
#: `[build] actor_pool` of the subject.
POOL = 24
#: `[build] actor_scan` of the subject. The budget is ceil, and a slot can
#: be reached one sweep late when the list shifts under the cursor, so allow
#: one sweep of slack on top.
SCAN = 4
GRACE = SCAN * 2
#: LCD frames of held RIGHT: long enough to walk the camera most of the road.
FRAMES = 900

FAILS = []


def build_subject(tmp):
    """Build a temp copy of the subject and relink it with -Wl-j.

    THE ROM AND ITS SYMBOLS MUST COME FROM THE SAME LINK: the ordinary build
    writes no full symbol file, and a `.noi` from an earlier link names the
    OLD addresses so every hook misses silently ("0 sampled game frames",
    2026-09-05). `tools/framebudget/build_noi.py` re-runs the build's own lcc
    line with -Wl-j, so the pair is written together. Returns (rom, noi) or
    None with the build output printed."""
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


def check(label, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- " + detail) if detail else ""))
    if not cond:
        FAILS.append(label)


def symbols(path):
    out = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
        if m:
            out[m.group(1)] = int(m.group(2), 16)
    return out


def main():
    from mosaik8_build import gbdk_available
    if not gbdk_available():
        print("[SKIP] GBDK not installed - cannot build %s" % SUBJECT)
        return 0
    try:
        from pyboy import PyBoy
    except ImportError:
        print("[SKIP] PyBoy is not installed")
        return 0
    tmp = tempfile.mkdtemp(prefix="wake_")
    try:
        built = build_subject(tmp)
        check("the subject builds with symbols", built is not None)
        if built is None:
            return report()
        return run(PyBoy, *built)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def run(PyBoy, ROM, NOI):
    s = symbols(NOI)
    need = ["_vm_actor_a_parked", "_vm_actor_a_x", "_vm_actor_a_y",
            "_vm_actor_a_active", "_vm_actor_a_vis", "_vm_core_run_scripts",
            "_vm_core_pend_code", "_vm_player_camx16", "_vm_player_camy16",
            "_vm_actor_n_plist"]
    missing = [n for n in need if n not in s]
    check("the .noi names every symbol the probe reads", not missing,
          ", ".join(missing))
    if missing:
        return report()

    pb = PyBoy(ROM, window="null")
    pb.set_emulation_speed(0)
    g = lambda n: s[n] & 0xFFFF

    def w16(a, v):
        pb.memory[a], pb.memory[a + 1] = v & 0xFF, (v >> 8) & 0xFF

    for _ in range(120):
        pb.tick()
    pb.memory[g("_vm_core_pend_code")] = 2
    pb.memory[g("_vm_core_pend_a")] = ROOM
    w16(g("_vm_core_pend_b"), SPAWN[0])
    w16(g("_vm_core_pend_c"), SPAWN[1])
    for _ in range(300):
        pb.tick()
    # HOLD, never tap: the walk is the camera, and a held button is one input.
    pb.button_press("right")

    # The camera lives in vm.player; read it the way the engine writes it.
    camx, camy = g("_vm_player_camx16"), g("_vm_player_camy16")
    pool = POOL
    stuck = [0] * pool
    worst = [0, -1]          # (frames, slot)
    parked_seen = [0]
    rows = [0]
    cam0 = [None]
    cam1 = [0]
    plist_hi = [0]
    woke = [0]
    last_plist = [None]

    def rd16(a):
        return pb.memory[a] | (pb.memory[a + 1] << 8)

    def sample(_c):
        rows[0] += 1
        if rows[0] > 60:
            cx = rd16(camx)
            cy = rd16(camy)
            if cam0[0] is None:
                cam0[0] = cx
            cam1[0] = cx
            n = pb.memory[g("_vm_actor_n_plist")]
            plist_hi[0] = max(plist_hi[0], n)
            if last_plist[0] is not None and n < last_plist[0]:
                woke[0] += last_plist[0] - n
            last_plist[0] = n
            for i in range(pool):
                if not pb.memory[g("_vm_actor_a_active") + i]:
                    stuck[i] = 0
                    continue
                if not pb.memory[g("_vm_actor_a_vis") + i]:
                    stuck[i] = 0
                    continue
                if not pb.memory[g("_vm_actor_a_parked") + i]:
                    stuck[i] = 0
                    continue
                parked_seen[0] += 1
                wx = rd16(g("_vm_actor_a_x") + 2 * i)
                wy = rd16(g("_vm_actor_a_y") + 2 * i)
                inside = (cx <= wx <= cx + 160
                          and cy <= wy <= cy + 144 - 16)
                if inside:
                    stuck[i] += 1
                    if stuck[i] > worst[0]:
                        worst[0], worst[1] = stuck[i], i
                else:
                    stuck[i] = 0

    v = s["_vm_core_run_scripts"]
    pb.hook_register(v >> 16, v & 0xFFFF, sample, None)
    for _ in range(FRAMES):
        pb.tick()

    pb.button_release("right")
    pb.stop(save=False)

    print("the road (room %d), %d sampled game frames" % (ROOM, rows[0]))
    check("the camera really scrolled over the pool (else this proves nothing)",
          cam0[0] is not None and cam1[0] - cam0[0] >= 160,
          "camera x %s -> %d" % (cam0[0], cam1[0]))
    check("the room really parks actors (else this proves nothing)",
          parked_seen[0] > 0 and plist_hi[0] >= 6,
          "%d parked-slot samples, n_plist peaked at %d"
          % (parked_seen[0], plist_hi[0]))
    check("...and the camera really woke parked slots",
          woke[0] >= 4, "%d wake(s) seen" % woke[0])
    check("no slot stays parked inside the window past the scan budget",
          worst[0] <= GRACE,
          "worst %d frame(s) on slot %d (budget %d)"
          % (worst[0], worst[1], GRACE))
    return report()


def report():
    print()
    if FAILS:
        print("SOME CHECKS FAILED:", FAILS)
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
