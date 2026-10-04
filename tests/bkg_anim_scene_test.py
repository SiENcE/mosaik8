#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""PER-SCENE background tile animation (`[[scene.animated_tile]]`).

The world-global `[[animated_tile]]` animates the ONE shared tileset, which is
why it is refused with per-scene tilesets / stream / paint_table - exactly the
modes every reference-engine conversion runs in, and why the sample town's waterfall
and flowers stood still. The per-scene form belongs to the room whose tileset
is loaded, so it combines with all three.

The reference engine's `vm_replace_tile_xy` reads the map's tile INDEX at (x, y) and
writes 16 bytes of tileset data at that index - a tile-DATA swap, which is
exactly what this is.

Measured on the converted ROM (`tools/bganimprobe/bganim_probe.py`, reading the
tiles' DATA out of VRAM): all three of the sample town's animated tiles cycle
4 frames, every **30 LCD frames**, which is the reference ROM's own authored
rate; a control tile never changes. **+8 B of bank 0.**

The rules this pins:

* opt-in: a world with no per-scene animation emits no `anim_tick_at`, no
  import and no wiring, and keeps the argument-less `anim_tick()` alone.
* the frames are baked from their OWN image, so an animation adds nothing to
  the scene's uploaded tileset and costs no background VRAM.
* it steps by ELAPSED DISPLAY FRAMES, capped. A per-call counter would ride
  the VM clock, which is 1 to 3 LCD frames deep depending on the room: the
  sample town measured 45 frames a step against the reference's 30. A
  decoration has no balance to preserve, so it does not ride that clock - the
  same exception the overlay curtain and the music catch-up take.
* a room change RE-SEEDS it: the room load re-uploads the scene's tileset,
  which puts the static pixels back under the animated index.
"""

import tempfile

import mosaik_scenes
from mosaik_vm.rooms import emit_rooms_mos

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scene_transpile_test import make_tileset_png


def _exported(src, name):
    """Is `name` in ANY of a module's `export` statements?

    NOT `src.split("export")[-1]`: a module may have several export lines, and
    taking only the last one reads as "not exported" the moment a new one is
    appended below it - which is exactly what W7j and W7c each did to a
    different module, breaking a green assertion for a reason that had nothing
    to do with what it pins.
    """
    return any(name in ln for ln in src.splitlines()
               if ln.strip().startswith("export "))


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def _world(anim, tmp):
    sc = {"name": "a", "width": 32, "height": 32,
          "map": [[0] * 32 for _ in range(32)]}
    if anim:
        sc["animated_tile"] = anim
    return {"tileset": {"png": "tiles.png"}, "scene": [sc]}


def _transpiler():
    print("[the scenes transpiler]")
    ok = True
    tmp = tempfile.mkdtemp(prefix="bkg_anim_scene_test_")
    make_tileset_png(os.path.join(tmp, "tiles.png"))

    off = mosaik_scenes.transpile(_world(None, tmp), tmp)
    ok &= check("no animation -> no anim_tick_at, no import (byte-identical)",
                "anim_tick_at" not in off and "platform.system" not in off)
    ok &= check("...and the argument-less anim_tick() is still there",
                "function anim_tick()" in off)

    on = mosaik_scenes.transpile(
        _world([{"tile": 62, "frames": [0, 1], "period": 30}], tmp), tmp)
    ok &= check("an animation -> anim_tick_at(scene)",
                "function anim_tick_at(scene: u8)" in on)
    ok &= check("...exported, or the generated shell cannot call it",
                _exported(on, "anim_tick_at"))
    ok &= check("the frames are BAKED (no VRAM cost, nothing uploaded)",
                "SAN0_F0" in on and "SAN0_F1" in on)
    # ...and a frame outside the source image is a clear error, not a silent
    # read past the end of it
    bad = False
    try:
        mosaik_scenes.transpile(
            _world([{"tile": 62, "frames": [0, 9], "period": 30}], tmp), tmp)
    except Exception as exc:                                # noqa: BLE001
        bad = "outside its" in str(exc)
    ok &= check("a frame past the source image is refused by name", bad)
    ok &= check("the tile INDEX is the map's, kept as authored",
                "const SAN0_TILE: u8 = 62" in on)
    ok &= check("the period passes through RAW (it counts display frames)",
                "const SAN0_PERIOD: u8 = 30" in on)
    ok &= check("it steps by ELAPSED display frames, capped",
                "system.frames()" in on and "san0_timer += fd" in on
                and "if fd > 8 {" in on)
    ok &= check("...and wraps by SUBTRACTING the period (a delta > 1 must not "
                "lose the remainder)", "san0_timer -= SAN0_PERIOD" in on)
    ok &= check("a room change re-seeds it (the tileset upload put the static "
                "pixels back)", "san_seeded = 0" in on and "san_room" in on)
    ok &= check("the scene guard is the room the shell passes",
                "if scene == 0 {" in on)
    ok &= check("the state is initialiser-free, so it is BSS not bank 0",
                "var san0_phase: u8\n" in on and "var san0_phase: u8 = " not in on)

    # ...and it combines with the modes the world-global form is refused in
    w = _world([{"tile": 5, "frames": [0, 1], "period": 12}], tmp)
    w["world"] = {"stream": True, "paint_table": True}
    w["scene"][0]["tileset"] = "tiles.png"
    try:
        both = mosaik_scenes.transpile(w, tmp)
        ok &= check("it combines with stream + paint_table + a per-scene "
                    "tileset (what every conversion runs)",
                    "function anim_tick_at" in both)
    except Exception as exc:                                # noqa: BLE001
        ok &= check("it combines with stream + paint_table + a per-scene "
                    "tileset (raised: %s)" % exc, False)
    return ok


def _generated():
    print("")
    print("[the generated rooms.mos wiring]")
    ok = True
    base = {"types": ["topdown"], "has_collision": True, "uniform": True}
    off = emit_rooms_mos(base)
    ok &= check("no animation -> nothing wired", "bkg_anim" not in off)
    on = emit_rooms_mos(dict(base, has_scene_bkg_anim=True))
    ok &= check("the shell passes the ROOM (the seam takes no arguments)",
                "function bkg_anim()" in on
                and "scenes.anim_tick_at(room)" in on)
    ok &= check("...and registers it", "core.set_bkg_anim(bkg_anim)" in on)
    world = emit_rooms_mos(dict(base, has_bkg_anim=True))
    ok &= check("a WORLD-global animation still wires the old path",
                "core.set_bkg_anim(scenes.anim_tick)" in world
                and "function bkg_anim()" not in world)
    return ok


def main():
    print("per-scene background tile animation")
    print("=" * 58)
    ok = _transpiler() and _generated()
    print("=" * 58)
    print("All checks passed" if ok else "Some tests failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
