#!/usr/bin/env python3
"""Generate vm-percon: the PER-CONSOLE CONTENT FILTERING showcase. ONE source, but each console's ROM gets DIFFERENT content, via
the `platforms` allow-list on three kinds of thing:

  * per-OBJECT  -- a `guard` enemy placed only on ["lynx"]      (an extra actor there)
  * per-SCRIPT  -- the `guard_ai` event script tagged ["lynx"]  (its bytecode ships only there)
  * per-SCENE   -- a `bonus` room tagged ["gameboy_color"]      (a whole room, dropped elsewhere)

The transpilers (mosaik_scenes.py / mosaik_vm.py) fork the generated modules into
`if platform` conditional-compilation guards, so a console that isn't listed drops
that content at ZERO ROM cost -- and a world where nothing is tagged stays
byte-identical. Each console therefore gets exactly:

  gameboy        : hub + npc                       (baseline)
  gameboy_color  : hub + npc + the bonus room       (per-scene)
  lynx           : hub + npc + guard + guard_ai      (per-object + per-script)
  sms            : hub + npc                        (baseline)

Regenerate (writes tiles.png + world.toml + scripts + the generated src modules):
    python projects/vm-percon/assets/gen_world.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

import toml
from mosaik_assets import write_png_indexed
import mosaik_scenes
import mosaik_vm

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

PAL = [(224, 224, 224), (0, 0, 0)]     # 0 floor (light), 1 wall (black)
FLOOR, WALL = 0, 1
W, H = 20, 18                          # GB screen = 20x18 tiles


def _room():
    """A walled room: tiles + a parallel painted collision layer (1 = solid)."""
    tiles, coll = [], []
    for y in range(H):
        trow, crow = [], []
        for x in range(W):
            border = x == 0 or y == 0 or x == W - 1 or y == H - 1
            trow.append(WALL if border else FLOOR)
            crow.append(1 if border else 0)
        tiles.append(trow)
        coll.append(crow)
    return tiles, coll


def build_world():
    tiles, coll = _room()
    return {
        "world": {"module": "scenes", "map_w": W, "map_h": H, "vm": True},
        "tileset": {"png": "tiles.png"},
        "kinds": {"player": 0, "npc": 1, "guard": 2},
        "scene": [
            # The HUB ships on every console.
            {"name": "hub", "scene_type": "topdown", "map": tiles, "collision": coll,
             "object": [
                 {"kind": "player", "x": 80, "y": 72},
                 {"kind": "npc", "x": 32, "y": 32},
                 # per-OBJECT filter: the guard exists only on the Lynx.
                 {"kind": "guard", "x": 144, "y": 112, "platforms": ["lynx"]},
             ]},
            # per-SCENE filter: the whole bonus room ships only on the Game Boy Color;
            # every other console gets a 1x1 stub in its place (map + npc dropped).
            {"name": "bonus", "scene_type": "topdown", "map": tiles, "collision": coll,
             "platforms": ["gameboy_color"],
             "object": [{"kind": "npc", "x": 100, "y": 80}]},
        ],
    }


SCRIPTS = {
    "script": [
        {"name": "main", "events": [
            {"event": "start_thread", "script": "wander"},
            {"event": "start_thread", "script": "guard_ai"},
            {"event": "stop"},
        ]},
        # actor 0 = the npc (spawned by the shell from the object table): wanders.
        {"name": "wander", "loop": True, "events": [
            {"event": "actor_move_to", "actor": 0, "x": 120, "y": 32},
            {"event": "actor_move_to", "actor": 0, "x": 32, "y": 110},
        ]},
        # per-SCRIPT filter: guard_ai's bytecode ships only on the Lynx (where the
        # guard object exists). Elsewhere it compiles to a 1-byte STOP stub, so
        # `main`'s start_thread of it is a harmless no-op.
        {"name": "guard_ai", "loop": True, "platforms": ["lynx"], "events": [
            {"event": "actor_move_to", "actor": 1, "x": 32, "y": 112},
            {"event": "actor_move_to", "actor": 1, "x": 144, "y": 32},
        ]},
    ]
}


def main():
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 16,
                      [FLOOR] * 64 + [WALL] * 64, PAL)

    world = build_world()
    with open(os.path.join(PROJ, "world.toml"), "w") as f:
        toml.dump(world, f)

    os.makedirs(os.path.join(PROJ, "scripts"), exist_ok=True)
    with open(os.path.join(PROJ, "scripts", "main.evt.toml"), "w") as f:
        toml.dump(SCRIPTS, f)

    # Generate the two forked modules (the transpilers auto-fork on the tags).
    scenes_src = mosaik_scenes.transpile(world, PROJ)
    with open(os.path.join(PROJ, "src", "scenes.mos"), "w") as f:
        f.write(scenes_src)
    scripts_src = mosaik_vm.emit_scripts_module(SCRIPTS["script"])
    with open(os.path.join(PROJ, "src", "scripts.mos"), "w") as f:
        f.write(scripts_src)

    forks = scenes_src.count('if platform ==') + scripts_src.count('if platform ==')
    print("wrote vm-percon world + scripts + generated modules (%d platform forks)"
          % forks)


if __name__ == "__main__":
    main()
