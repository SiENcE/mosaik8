#!/usr/bin/env python3
"""SCREEN-SPACE (pinned) actors - the reference engine's `isPinned` / ACTOR_FLAG_PINNED.

A pinned actor is drawn at its x/y with NO camera subtraction and is never
culled off the visible window, so it holds its place on screen while the level
scrolls under it. The reference engine uses the one flag for two things that look
unrelated - a hearts readout AND a foreground cutscene character - which is why
the conversion cannot answer it with a HUD panel: the platformer conversion's cutscene character is a
32x32 drawing standing in front of a scrolling parallax scene, and it was
DROPPED wholesale before this.

Pinned here:
  * `vm.actor` draws a pinned slot in screen space and never parks it;
  * the flag is per ROOM (cleared by reset(), like group / hp / dir);
  * the transpiler emits `obj_pinned` ONLY for a world that pins something,
    and `generate_rooms` calls `set_pinned` in lockstep with it;
  * a world that pins nothing is byte-identical.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import shutil
import tempfile

import mosaik_assets as M
from mosaik_scenes import transpile
from mosaik_vm.rooms import emit_rooms_mos

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _world(pin):
    obj = {"kind": "npc", "x": 16, "y": 16, "id": 1}
    if pin:
        obj["pinned"] = True
    return {"world": {"module": "scenes", "vm": True, "map_w": 20, "map_h": 18},
            "tileset": {"png": "t.png"}, "kinds": {"npc": 0},
            "scene": [{"name": "room", "map": [0] * 360, "object": [obj]}]}


def _transpile(world):
    tmp = tempfile.mkdtemp(prefix="pin_")
    try:
        M.write_png_indexed(os.path.join(tmp, "t.png"), 8, 16,
                            [0] * 64 + [3] * 64,
                            [(255, 255, 255), (170, 170, 170),
                             (85, 85, 85), (0, 0, 0)])
        return transpile(world, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_transpiler():
    print("\n[the obj_pinned selector]")
    on = _transpile(_world(True))
    check("function obj_pinned(i: u8) -> u8 {" in on,
          "a world with a pinned object emits the selector")
    body = on.split("function obj_pinned")[1].split("\n    }")[0]
    check("if i == 0 {" in body and "return 1" in body and "return 0" in body,
          "... which answers 1 for that OBJ index and 0 for the rest")
    exp = [l for l in on.splitlines() if l.strip().startswith("export ")]
    check(any("obj_pinned" in l for l in exp), "... and it is exported")
    off = _transpile(_world(False))
    check("obj_pinned" not in off,
          "a world that pins nothing emits none of it (byte-identical)")


def test_rooms_lockstep():
    print("\n[rooms.mos lockstep]")
    base = {"types": ["topdown"], "uniform": True, "has_collision": True,
            "has_objects": True, "has_entity": True,
            "clips": {"meta_w": 2, "meta_h": 2, "per_kind_size": True,
                      "flip": False, "player": True}}
    on = emit_rooms_mos(dict(base, has_obj_pin=True))
    off = emit_rooms_mos(dict(base, has_obj_pin=False))
    check("actor.set_pinned(slot, scenes.obj_pinned(i))" in on,
          "the room load pins the slot from the transpiler's own selector")
    check("set_pinned" not in off,
          "a world that pins nothing never calls it (byte-identical)")


def test_runtime_contract():
    """The two draw-path rules, read off `lib/vm/actor.mos`.

    They are a SOURCE contract rather than a ROM assertion because both live on
    the per-frame render path of a banked module: what matters is that the pin
    is consulted at BOTH sites. Consulting only `place` would leave a pinned
    actor culled the moment the camera passed it (its screen x is small and a
    wide room's camera is not), and consulting only `off_window` would draw it
    at `x - camera` - carried away by the level, the bug this closes.
    """
    print("\n[vm.actor draw path]")
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "lib", "vm", "actor.mos"),
               encoding="utf-8").read()
    off_w = src.split("local function off_window")[1].split("\n    }")[0]
    check("a_pin[i] == 1" in off_w and "return false" in off_w,
          "a pinned slot is never off-window (the camera cannot take it away)")
    place = src.split("local function place")[1].split("\n    }")[0]
    check("a_pin[i] == 1" in place and "sprite.move(base, wx, wy)" in place,
          "... and it draws at its own coordinates, not at x - camera")
    reset = src.split("function reset()")[1].split("\n    }")[0]
    check("a_pin[i] = 0" in reset,
          "the pin is per ROOM: reset() clears it like group / hp / dir")
    exports = " ".join(l for l in src.splitlines()
                       if l.strip().startswith("export "))
    check("set_pinned" in exports, "set_pinned is exported")


def main():
    print("=" * 60)
    print("screen-space (pinned) actors")
    print("=" * 60)
    test_transpiler()
    test_rooms_lockstep()
    test_runtime_contract()
    print("\n" + "=" * 60)
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All pinned-actor checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
