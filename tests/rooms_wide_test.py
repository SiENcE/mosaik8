#!/usr/bin/env python3
"""Wide rooms are generated PER ROOM, derived from the scene's own size.

`[world] wide` used to be a WORLD flag that made `generate_rooms` bail on the
whole project ("the shell is hand-written"), so a world that mixed one
oversized room with a dozen normal ones got no generated wiring at all.

Wideness is now DERIVED per room from its dimensions (a room wider than the
32-tile hardware background must COLUMN-STREAM through engine.scroll instead
of being painted once), so:

  * a world with no oversized room is byte-identical - engine.scroll is not
    even imported;
  * a world that mixes them emits BOTH paths and picks per room at load;
  * a wide room is NOT painted (paint() uploads the whole map in one
    bkg.set_tiles, which overruns the hardware tilemap and corrupts what the
    streamer seeded) - engine.scroll owns its background;
  * what cannot be generated yet raises a clear error NAMING the room,
    instead of silently dropping the world's wiring.

`[world] wide` survives as the hand-written-shell escape hatch
(the platformer port).

Behaviour is covered end-to-end by the throwaway ROM built in the session
record: a 64-tile (512 px) room streamed correctly past the 256 px hardware
wrap (player x 16 -> 504, camera SCX 96, floor still drawn).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm  # noqa: E402
from mosaik_vm.rooms import BKG_TILES  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


BASE = {"types": ["platform"], "has_collision": True}


def test_narrow_is_byte_identical():
    a = mosaik_vm.emit_rooms_mos(BASE)
    b = mosaik_vm.emit_rooms_mos(dict(BASE, wide_rooms=False))
    check("a world with no wide room is byte-identical", a == b)
    check("engine.scroll is not imported", "engine.scroll" not in a)
    check("the normal paint path is unconditional",
          "        scenes.paint(rm)" in a)


def test_wide_emits_the_stream_path():
    w = mosaik_vm.emit_rooms_mos(dict(BASE, wide_rooms=True, uniform=True))
    check("engine.scroll is imported", 'import "engine.scroll"' in w)
    check("gather + stream are emitted",
          "function gather(" in w and "function stream(" in w)
    check("the handler branches per ROOM, not per world",
          "if mw > BKG_PX {" in w)
    check("wide uses setup_wide + set_scroll",
          "player.setup_wide(" in w and "player.set_scroll(stream)" in w)
    check("narrow rooms in the SAME world keep setup_platform",
          "player.setup_platform(" in w)
    check("a wide room is NOT painted (paint would overrun the tilemap)",
          "-- streamed: engine.scroll/scroll2d paints it" in w
          and "            scenes.paint(rm)" in w)


def test_dimension_source():
    uni = mosaik_vm.emit_rooms_mos(dict(BASE, wide_rooms=True, uniform=True))
    non = mosaik_vm.emit_rooms_mos(dict(BASE, wide_rooms=True, uniform=False))
    check("a uniform world reads the MAP_W const",
          "scenes.MAP_W" in uni and "SCENE_W" not in uni)
    check("a non-uniform world indexes SCENE_W",
          "scenes.SCENE_W[room]" in non)


def test_refusals_name_the_room(tmpdir):
    """A room the generator cannot do yet must say which one, and why."""
    from mosaik_vm.isa import VmError
    import mosaik_assets
    import toml

    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]

    def build(name, w, h, stype):
        root = os.path.join(tmpdir, name)
        os.makedirs(os.path.join(root, "assets"))
        os.makedirs(os.path.join(root, "src"))
        mosaik_assets.write_png_indexed(
            os.path.join(root, "assets", "t.png"), 8, 16, [0] * 64 + [3] * 64, pal)
        world = {
            "world": {"module": "scenes", "map_w": w, "map_h": h, "vm": True},
            "tileset": {"png": "t.png"},
            "kinds": {"player": 0},
            "scene": [{"name": "big", "scene_type": stype, "map_w": w,
                       "map_h": h,
                       "map": [[0] * w for _ in range(h)]}],
        }
        with open(os.path.join(root, "assets", "world.toml"), "w",
                  encoding="utf-8") as f:
            toml.dump(world, f)
        open(os.path.join(root, "src", "rooms.mos"), "w").close()
        return root

    # too TALL -> needs scroll2d, which is not generated
    try:
        mosaik_vm.generate_rooms(build("tall", 40, 40, "platform"))
        check("a too-tall room is refused", False, "no error raised")
    except VmError as exc:
        check("a too-tall room is refused, naming it",
              "big" in str(exc) and "scroll2d" in str(exc), str(exc)[:120])

    # A wide TOPDOWN room is no longer refused: since 2026-08-06 it ROAMS
    # (engine.scroll2d, vm.player's setup_roam), which is what converts a
    # 56x56 reference-engine town instead of cropping it to a corner.
    def gen(name, w, h, stype):
        """generate_rooms returns the PATH; read the module text back."""
        path = mosaik_vm.generate_rooms(build(name, w, h, stype))
        if not path:
            return ""
        with open(path, encoding="utf-8") as f:
            return f.read()

    r = gen("wtop", 40, 18, "topdown")
    check("a wide TOPDOWN room roams instead of being refused",
          "player.setup_roam(" in r and "player.set_scroll2d(stream2)" in r)
    check("...and it streams both axes through scroll2d",
          'import "engine.scroll2d"' in r
          and "scroll2d.update2d(camx, camy, tile_at)" in r)
    check("...and a TALL topdown room roams too",
          "player.setup_roam(" in gen("ttop", 20, 40, "topdown"))

    # A wide SHMUP column-streams too. Its camera AUTO-SCROLLS rather than
    # following the player, so it is its own vm.player entry point
    # (setup_wide_shmup) over the same engine.scroll machinery the wide
    # platformer uses - which is what un-cropped the reference-engine sample's
    # 255-tile `space/Space Battle`.
    r = gen("wshm", 40, 18, "shmup")
    check("a wide SHMUP room streams instead of being refused",
          "player.setup_wide_shmup(" in r and "player.set_scroll(stream)" in r)
    check("...and it fills the column ring at room load",
          "scroll.fill(" in r and 'import "engine.scroll"' in r)
    check("...while a NARROW shmup in the same world keeps the painted path",
          "player.setup_shmup(" in r and "if mw > BKG_PX {" in r)

    # a wide room whose type has no handler at all is still refused BY NAME
    try:
        mosaik_vm.generate_rooms(build("wmenu", 40, 18, "menu"))
        check("a wide room with no handler is refused", False, "no error raised")
    except VmError as exc:
        check("a wide room with no handler is refused, naming it + the type",
              "big" in str(exc) and "menu" in str(exc), str(exc)[:120])


def test_world_flag_is_still_the_escape_hatch(tmpdir):
    import mosaik_assets
    import toml
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    root = os.path.join(tmpdir, "handwritten")
    os.makedirs(os.path.join(root, "assets"))
    os.makedirs(os.path.join(root, "src"))
    mosaik_assets.write_png_indexed(os.path.join(root, "assets", "t.png"),
                                    8, 16, [0] * 64 + [3] * 64, pal)
    world = {
        "world": {"module": "scenes", "map_w": 40, "map_h": 18, "vm": True,
                  "wide": True},
        "tileset": {"png": "t.png"},
        "kinds": {"player": 0},
        "scene": [{"name": "big", "scene_type": "platform", "map_w": 40,
                   "map_h": 18, "map": [[0] * 40 for _ in range(18)]}],
    }
    with open(os.path.join(root, "assets", "world.toml"), "w",
              encoding="utf-8") as f:
        toml.dump(world, f)
    open(os.path.join(root, "src", "rooms.mos"), "w").close()
    check("[world] wide still means 'hand-written shell' (generates nothing)",
          mosaik_vm.generate_rooms(root) is None)


def main():
    import tempfile
    print("Wide rooms (per-room, derived) -- BKG_TILES = %d" % BKG_TILES)
    print("=" * 50)
    test_narrow_is_byte_identical()
    test_wide_emits_the_stream_path()
    test_dimension_source()
    with tempfile.TemporaryDirectory() as tmp:
        test_refusals_name_the_room(tmp)
        test_world_flag_is_still_the_escape_hatch(tmp)
    print("=" * 50)
    if failed:
        print("%d FAILED, %d passed" % (failed, passed))
        return 1
    print("All wide-room checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
