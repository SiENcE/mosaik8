#!/usr/bin/env python3
"""A player-less (menu/logo) room must RESET the view, not inherit it.

Every `player.setup_*` entry point clears the per-room modes it owns
(`clear_wide()` at its top), but a menu/logo room runs NO setup at all - so a
WIDE room's camera, stream flags and scroll register survived into it. On
the reference-engine sample conversion, opening the quest menu from the (wide) parallax room painted the
menu at the old scroll offset: the quest list sat shifted right with a band of
the previous room's streamed columns showing down the left edge.

The fix is `player.reset_view()` (clear_wide + camera to the origin), called
by the generated load_room's player-less arm next to hide()/clear_player() -
the same "anything a setup turns on, some other setup must turn off" rule the
wide-reset test already pins for the setups themselves.
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


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _build(root):
    """A minimal VM world: one wide platform room plus one MENU room."""
    os.makedirs(os.path.join(root, "assets"))
    os.makedirs(os.path.join(root, "src"))
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    mosaik_assets.write_png_indexed(
        os.path.join(root, "assets", "t.png"), 8, 16, [0] * 64 + [3] * 64, pal)
    world = {
        "world": {"module": "scenes", "map_w": 20, "map_h": 18, "vm": True},
        "tileset": {"png": "t.png"},
        "kinds": {"player": 0},
        "scene": [
            {"name": "wide", "scene_type": "platform", "map_w": 80,
             "map": [[0] * 80 for _ in range(18)],
             "object": [{"kind": "player", "x": 32, "y": 32}]},
            {"name": "pause", "scene_type": "menu",
             "map": [[0] * 20 for _ in range(18)]},
        ],
    }
    with open(os.path.join(root, "assets", "world.toml"), "w",
              encoding="utf-8") as f:
        toml.dump(world, f)
    open(os.path.join(root, "src", "rooms.mos"), "w").close()
    return root


def main():
    print("[vm.player: reset_view]")
    player = _read("lib", "vm", "player.mos")
    body = player.split("function reset_view()", 1)
    check("player.reset_view() exists", len(body) == 2)
    if len(body) == 2:
        head = body[1].split("\n    function ", 1)[0]
        check("... clears the wide/stream state", "clear_wide()" in head)
        check("... releases a scripted camera lock", "cam_lock = 0" in head)
        check("... and puts the camera back at the origin",
              "camera.set(0, 0)" in head)
    exports = "\n".join(l for l in player.splitlines()
                        if l.strip().startswith("export "))
    check("... and it is exported", "reset_view" in exports)

    print("\n[generate_rooms: the player-less arm calls it]")
    tmp = tempfile.mkdtemp(prefix="menu_view_")
    try:
        root = _build(os.path.join(tmp, "p"))
        out = mosaik_vm.generate_rooms(root)
        check("rooms.mos generated", bool(out))
        with open(out, encoding="utf-8") as f:
            rooms = f.read()
        check("the menu/logo arm hides the player", "player.hide()" in rooms)
        check("... and resets the view beside it",
              "player.reset_view()" in rooms)
        check("... after hide (same arm)",
              rooms.find("player.hide()") < rooms.find("player.reset_view()"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failed:
        print("SOME CHECKS FAILED (%d)" % failed)
        return 1
    print("All %d checks passed" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
