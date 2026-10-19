"""A shmup ship FACES THE SCROLL, and only the cross axis turns it (2026-10-18).

The reference engine's shmup state (`shmup_update`, free movement) computes
the player's direction every frame: a HORIZONTAL stage faces the scroll
direction unless UP or DOWN is held (the tilt frames), a VERTICAL one faces
it unless LEFT or RIGHT is held, and nothing else ever turns the ship. Our
`update_shmup` set the facing from EVERY pressed direction and kept it after
release: on the reference-engine sample conversion's space battle a LEFT
press turned the ship round (a facing-left kind with `flip_left` mirrors, so
it flew backwards with its dark nose lost against the dark sky) and an UP left
it tilted for good.

The check builds `projects/vm-shmup` (a vertical stage) and
`projects/vm-combat` (a horizontal one) with symbols, holds each direction
and reads `vm.player`'s facing out of WRAM. Needs GBDK and PyBoy.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik8_build  # noqa: E402

FAILS = []
DOWN, UP, LEFT, RIGHT = 0, 1, 2, 3


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def symbols(noi):
    out = {}
    with open(noi, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
            if m:
                out[m.group(1)] = int(m.group(2), 16) & 0xFFFF
    return out


def _boot(tmp, name):
    """Build a copy of projects/<name> with symbols; (PyBoy, face, axis)."""
    from pyboy import PyBoy
    proj = os.path.join(tmp, name)
    shutil.copytree(os.path.join(ROOT, "projects", name), proj,
                    ignore=shutil.ignore_patterns("build"))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--debug", "--platform", "gameboy", proj],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT)
    rom = os.path.join(proj, "build", "gameboy", name + ".gb")
    noi = rom[:-3] + ".noi"
    if not (os.path.exists(rom) and os.path.exists(noi)):
        check("%s builds with symbols" % name, False, (r.stdout + r.stderr)[-600:])
        return None
    s = symbols(noi)
    face, axis = s.get("_vm_player_pface"), s.get("_vm_player_sh_dir")
    if face is None or axis is None:
        check("%s: vm.player's facing and scroll axis are in the symbols" % name, False)
        return None
    pb = PyBoy(rom, window="null", cgb=False)
    pb.tick(300, False)
    return pb, face, axis


def _held(pb, face, btn):
    pb.button_press(btn)
    pb.tick(12, False)
    f = pb.memory[face]
    pb.button_release(btn)
    pb.tick(12, False)
    return f, pb.memory[face]


def main():
    try:
        import pyboy  # noqa: F401
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    tmp = tempfile.mkdtemp(prefix="shmup_face_")
    try:
        # a VERTICAL stage, as authored
        got = _boot(tmp, "vm-shmup")
        if got:
            pb, face, axis = got
            check("vm-shmup is a VERTICAL stage", pb.memory[axis] == 1, pb.memory[axis])
            v = {b: _held(pb, face, b) for b in ("left", "right", "up", "down")}
            check("vertical: LEFT / RIGHT turn the ship, release faces UP again",
                  v["left"] == (LEFT, UP) and v["right"] == (RIGHT, UP), v)
            check("vertical: UP / DOWN never turn it (DOWN used to face it down)",
                  v["up"] == (UP, UP) and v["down"] == (UP, UP), v)
            pb.stop(save=False)
        # a HORIZONTAL stage, as authored (vm-combat), not a poked axis
        got = _boot(tmp, "vm-combat")
        if got:
            pb, face, axis = got
            check("vm-combat is a HORIZONTAL stage", pb.memory[axis] == 0, pb.memory[axis])
            check("horizontal: the ship faces RIGHT at rest",
                  pb.memory[face] == RIGHT, pb.memory[face])
            h = {b: _held(pb, face, b) for b in ("left", "right", "up", "down")}
            check("horizontal: LEFT never turns it round (it flew backwards)",
                  h["left"] == (RIGHT, RIGHT) and h["right"] == (RIGHT, RIGHT), h)
            check("horizontal: UP / DOWN tilt it and release straightens it",
                  h["up"] == (UP, RIGHT) and h["down"] == (DOWN, RIGHT), h)
            pb.stop(save=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("shmup: the ship faces the scroll")
    print("=" * 60)
    main()
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("shmup_facing_test: all checks passed")
