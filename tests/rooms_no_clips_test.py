#!/usr/bin/env python3
"""A world with an On Interact slot but NO animations must still generate rooms.mos.

`emit_rooms_mos` keeps `clips` as None for a world with no `[animations]`
(there is no generated `clips` module to call), and every read of it is behind
`dyn_oam`, which is itself `bool(clips) and ...`. One was not: the per-kind
interact-BOX registration tested `sel["has_obj_interact"] and
clips.get("per_kind_size")`, so a world that places an actor with an On
Interact script and animates nothing crashed rooms generation outright with a
bare `AttributeError: 'NoneType' object has no attribute 'get'`.

`projects/vm-showcase` is exactly that world, which is how this was found - its
checked-in `rooms.mos` could not be regenerated at all.
"""

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import toml  # noqa: E402
import mosaik_assets  # noqa: E402
import mosaik_vm  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def _build(root):
    """A minimal VM world: one topdown room, one placed NPC with an On Interact
    slot, and NO src/clips.mos."""
    os.makedirs(os.path.join(root, "assets"))
    os.makedirs(os.path.join(root, "src"))
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    mosaik_assets.write_png_indexed(
        os.path.join(root, "assets", "t.png"), 8, 16, [0] * 64 + [3] * 64, pal)
    world = {
        "world": {"module": "scenes", "map_w": 20, "map_h": 18, "vm": True},
        "tileset": {"png": "t.png"},
        "kinds": {"player": 0, "npc": 1},
        "scene": [{"name": "room", "scene_type": "topdown",
                   "map": [[0] * 20 for _ in range(18)],
                   "object": [
                       {"kind": "player", "x": 32, "y": 32},
                       {"kind": "npc", "x": 64, "y": 32,
                        "on_interact": "talk"},
                   ]}],
    }
    with open(os.path.join(root, "assets", "world.toml"), "w",
              encoding="utf-8") as f:
        toml.dump(world, f)
    # rooms.mos must already exist for generate_rooms to (re)write it.
    open(os.path.join(root, "src", "rooms.mos"), "w").close()
    return root


def main():
    tmp = tempfile.mkdtemp(prefix="rooms_noclips_")
    try:
        root = _build(os.path.join(tmp, "p"))
        check("src/clips.mos really is absent",
              not os.path.isfile(os.path.join(root, "src", "clips.mos")))
        try:
            path = mosaik_vm.generate_rooms(root)
            ok = True
        except Exception as exc:  # noqa: BLE001 - the bug was a bare AttributeError
            path, ok = None, False
            check("generate_rooms does not crash without clips", False, repr(exc))
        if ok:
            check("generate_rooms does not crash without clips", True)
            src = open(path, encoding="utf-8").read()
            check("the On Interact slot is still wired",
                  "scenes.obj_interact(i)" in src)
            check("no clips module is imported",
                  'import "clips"' not in src)
            check("no per-kind interact BOX is registered (uniform sprites)",
                  "entity.set_box(" not in src)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("\n%d passed, %d failed" % (passed, failed))
    print("All checks passed" if not failed else "SOME CHECKS FAILED")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
