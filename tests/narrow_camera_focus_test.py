#!/usr/bin/env python3
"""A narrow room's follow camera SATURATES the focus before it narrows it to
u8 (the 2026-11-03 review's M13).

`player.place_camera()` follows a NARROW room (at most 32 x 32 tiles, so at
most 256 px a side) through `camera.follow`, whose operands are u8, and it
used to narrow the u16 focus (`focus_x()` / `focus_y()`) by plain
truncation. Inside the room the focus never passes 255; out of bounds is
solid, so the one way past the edge is the platformer BLANK state (a pit
death falls THROUGH the floor). A blank fall past y 256 wrapped the focus
to the top of the room, and the camera snapped back to the top of the level
every 256 px of the fall.

The fix saturates both axes at 255 before narrowing: the camera's maximum in
a narrow room is at most 112 px (GB), so a saturated focus asks for exactly
the camera the true focus would. It is folded on `VM_ST_PLAYER_BLANK`, the
state that is the only way there, so every other program keeps its
`place_camera` byte for byte.

Checks:
  - the source contract: the saturation is in `place_camera`, inside the fold,
    and the off arm is the plain narrowing it always was;
  - a real ROM (GBDK + PyBoy present): a fresh VM8 project in a temp dir
    (one 20 x 20 platform room, GB camera max y = 16, the generated rooms.mos)
    whose script raises the blank state with gravity. The camera reaches its
    maximum and STAYS there for the whole fall; on the unfixed engine it fell
    back to 0 every time the focus wrapped past a multiple of 256 (SCY 16, 0,
    16, 0 ... over 1,500 frames: the check's mutation, run 2026-11-07).
"""
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik8_build  # noqa: E402
import vm_fixture  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _src(rel):
    return open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8").read()


def _function(text, name):
    """One mosaik function's text, to its closing brace at its own indent."""
    m = re.search(r"(?:hot )?(?:local )?function %s\(" % re.escape(name), text)
    if not m:
        return ""
    start = text.rfind("\n", 0, m.start()) + 1
    indent = len(text[start:m.start()]) - len(text[start:m.start()].lstrip())
    end = text.find("\n" + " " * indent + "}\n", m.end())
    return text[start:end + indent + 3]


def _code(body):
    return "\n".join(ln.split("--")[0].rstrip() for ln in body.splitlines()
                     if ln.split("--")[0].strip())


def source_contract():
    print("\n[the source contract: player.place_camera]")
    pc = _code(_function(_src("lib/vm/player.mos"), "place_camera"))
    narrow = pc[pc.find("if cam_lock == 0 {"):]
    fold = re.search(r"if VM_ST_PLAYER_BLANK \{(.*?)\n            \} else \{(.*?)\n"
                     r"            \}", narrow, re.S)
    check("the narrow arm forks on VM_ST_PLAYER_BLANK", fold is not None, narrow[:600])
    on = fold.group(1) if fold else ""
    off = fold.group(2) if fold else ""
    check("... the ON arm reads the focus whole (u16)",
          "var fx: u16 = focus_x()" in on and "var fy: u16 = focus_y()" in on, on)
    check("... saturates BOTH axes at 255 before narrowing",
          re.search(r"if fx > 255 \{\s*fx = 255\s*\}", on) is not None
          and re.search(r"if fy > 255 \{\s*fy = 255\s*\}", on) is not None, on)
    check("... and only then narrows into the u8 the follow takes",
          on.find("cx = fx") > on.find("fx = 255") > 0
          and on.find("cy = fy") > on.find("fy = 255") > 0, on)
    check("the OFF arm is the plain narrowing it always was",
          "cx = focus_x()" in off and "cy = focus_y()" in off and "255" not in off, off)
    check("the narrowed focus is declared u8 ahead of the fork",
          re.search(r"var cx: u8\s*\n\s*var cy: u8\s*\n\s*if VM_ST_PLAYER_BLANK", narrow)
          is not None)


SCRIPT = """[[script]]
name = "main"
events = [
  { event = "wait", frames = 20 },
  { event = "player_blank_grav", gravity = 1 },
  { event = "player_blank", on = 1 },
  { event = "wait", frames = 250 },
  { event = "stop" },
]
"""

# The world: ONE 20 x 20 platform room (a floor on the last two rows), so the
# room is NARROW and its GB camera range is 0..16. A generated rooms.mos, never
# a hand shell: place_camera is reached through the generated platform setup.
_MAP = vm_fixture.grid([[0] * 20 for _ in range(18)] + [[1] * 20, [1] * 20])
WORLD = """[[scene]]
name = "pit"
scene_type = "platform"
map_h = 20
map = %s
collision = %s
[[scene.object]]
kind = "player"
x = 24
y = 40

[world]
module = "scenes"
map_w = 20
map_h = 20
vm = true

[tileset]
png = "tiles.png"

[kinds]
player = 0
""" % (_MAP, _MAP)


def _scy_trail(rom, frames):
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", cgb=False)
    trail = []
    for _ in range(frames):
        pb.tick()
        trail.append(pb.memory[0xFF42])
    pb.stop(save=False)
    return trail


def rom():
    print("\n[a real ROM: a blank fall past y 256 in a 20-row room]")
    try:
        import pyboy  # noqa: F401
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    fx = vm_fixture.build(WORLD, SCRIPT)
    path = fx.rom
    try:
        check("the fixture's script and world compiled",
              fx.scripts.returncode == 0 and fx.scenes.returncode == 0,
              (fx.scripts.stdout + fx.scripts.stderr + fx.scenes.stderr)[-400:])
        if not path:
            check("the fixture builds", False, (fx.build.stdout + fx.build.stderr)[-600:])
            return
        trail = _scy_trail(path, 1500)
        top = max(trail)
        check("the room's camera maximum is 16 (160 px room, 144 px screen)",
              top == 16, "max SCY %d" % top)
        first = trail.index(top) if top in trail else len(trail)
        after = trail[first:]
        check("the fall reaches the camera maximum", first < 700,
              "first at frame %d" % first)
        # 1 px/frame^2 of gravity from rest reaches the default max fall within
        # a few dozen VM frames; 800 LCD frames after the camera topped out the
        # player is hundreds of px under the room, past y 256 several times over.
        check("... and the camera STAYS there for the rest of the fall "
              "(%d frames; unfixed it wrapped back to 0)" % len(after),
              len(after) >= 800 and min(after) == top,
              "min %d after frame %d; drops at %s" % (
                  min(after), first,
                  [first + i for i, v in enumerate(after) if v != top][:8]))
    finally:
        shutil.rmtree(fx.tmp, ignore_errors=True)


def main():
    print("=== narrow_camera_focus_test ===")
    source_contract()
    rom()
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
