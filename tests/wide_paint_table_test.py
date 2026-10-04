#!/usr/bin/env python3
"""`[world] stream + paint_table` composing with a WIDE world (scenes.warm).

The pair used to be a hard `SceneError` on a wide world: paint_table reads the
map through a range WINDOW that `paint()` warms, and a column-streamed room is
never painted, so `map_tile` would read a cold window (an empty level).

That refusal cost real capacity on the GB. Going wide forced paint_table OFF,
which put the per-scene `map_tile`/`paint` chains back in the RESIDENT image -
and those chains CANNOT be banked, because a streamed-asset read lowers to a
`SWITCH_ROM` and code that switches banks must run from the home bank. Measured
on the 17-scene reference-engine conversion: **+2,734 B resident, over bank 0**.

The fix: a wide room's map is ONE contiguous range in the concatenated `MAPS`,
so warming it at room load is exactly what a painted room already does.
`transpile` emits `warm(scene)` - everything `paint()` does EXCEPT the map
upload (the per-scene tileset, and the range_base/use_range pair) - and
`generate_rooms`' wide arm calls it where the narrow arm calls `paint()`.
Recovered 1,832 B of resident image on that conversion.

Contract pinned here:
  * a wide stream+paint_table world TRANSPILES (no SceneError) and emits
    `warm`, exported.
  * `warm` warms the map window and does NOT upload the map.
  * `warm` uploads the per-scene TILESET - a wide room skips paint() entirely,
    so without this it would render with the previous room's tiles.
  * a NON-wide world is byte-identical (no warm, no export).
  * `generate_rooms` calls `scenes.warm(rm)` in the wide arm for exactly this
    combination, and NOT otherwise - the two conditions must stay in lockstep,
    since calling an unemitted warm() is an unexported-symbol error and not
    calling an emitted one streams an empty level.
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

from mosaik_assets import write_png_indexed
from mosaik_scenes import transpile

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _world(wide, stream=True, paint_table=True, n_scenes=3, per_scene_ts=True):
    """A VM8 world whose FIRST scene is wide (or not), the rest narrow.

    Per-scene TILESETS are set on every scene by default, because a wide room
    skips paint() and so would never upload one - that is half of what warm()
    fixes, and the half that has nothing to do with paint_table.
    """
    scenes = []
    for i in range(n_scenes):
        w = 64 if (i == 0 and wide) else 20
        h = 18
        scenes.append({
            "name": "room%d" % i,
            "scene_type": "platform",
            "map_w": w, "map_h": h,
            "map": [(1 if (x == 0 or y == 0) else 0)
                    for y in range(h) for x in range(w)],
            "collision": [(1 if (x == 0 or y == 0) else 0)
                          for y in range(h) for x in range(w)],
        })
        if per_scene_ts:
            scenes[-1]["tileset"] = "ts%d.png" % i
    return {
        "world": {"module": "scenes", "vm": True, "map_w": 20, "map_h": 18,
                  "stream": stream, "paint_table": paint_table},
        "tileset": {"png": "tiles.png"},
        "kinds": {"player": 0},
        "scene": scenes,
    }


def _make_png(path):
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    write_png_indexed(path, 8, 16, [0] * 64 + [3] * 64, pal)


def _transpile(world):
    """Transpile a world dict to the scenes module text (in a temp dir)."""
    tmp = tempfile.mkdtemp(prefix="widept_")
    try:
        _make_png(os.path.join(tmp, "tiles.png"))
        for sc in world["scene"]:
            if sc.get("tileset"):
                _make_png(os.path.join(tmp, sc["tileset"]))
        return transpile(world, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_wide_stream_paint_table_transpiles():
    print("\n[compose]")
    try:
        src = _transpile(_world(wide=True))
        ok, err = True, ""
    except Exception as e:
        src, ok, err = "", False, str(e)
    check(ok, "a WIDE stream+paint_table world transpiles (%s)" % err[:70])
    if not ok:
        return
    check("function warm(" in src, "warm(scene) is emitted")
    exp = [l for l in src.splitlines() if l.strip().startswith("export ")]
    check(exp and "warm" in exp[-1], "warm is exported")
    body = src.split("function warm(")[1].split("\n    }")[0]
    check("use_range(MAPS" in body, "warm warms the MAP window")
    check("bkg.set_tiles" not in body,
          "warm does NOT upload the map (that would overrun the tilemap)")
    check("bkg.set_data" in body,
          "warm DOES upload the scene tileset (a wide room skips paint)")


def test_non_wide_is_unchanged():
    print("\n[byte-identical off]")
    src = _transpile(_world(wide=False))
    check("function warm(" not in src, "a narrow world emits no warm()")
    exp = [l for l in src.splitlines() if l.strip().startswith("export ")]
    check(exp and "warm" not in exp[-1], "a narrow world does not export warm")
    # A wide world with NO per-scene tilesets and no paint_table has nothing
    # to warm: its one shared TILESET is uploaded by the shell at boot.
    plain = _transpile(_world(wide=True, paint_table=False, per_scene_ts=False))
    check("function warm(" not in plain,
          "wide + stream alone, ONE shared tileset: no warm() (byte-identical)")


def test_per_scene_tileset_warms_without_paint_table():
    """The second, independent reason for warm() - and the one that bites on
    every console.

    A per-scene TILESET is uploaded by `paint()`. A room past the background
    is never painted, so without warm() its tile DATA never reaches VRAM and
    the room renders its (correct) map through whatever the PREVIOUS room left
    behind. Caught 2026-08-15 on the platformer conversion, which has 6 scenes and so
    keeps paint_table off: 182 of 182 tiles in VRAM were the logo room's, the
    link was clean and verify.py was green.

    This is the latent bug once recorded as "stream + wide
    WITHOUT paint_table renders the wrong background tiles on SMS/GG"; it was
    never SMS/GG-specific and never about `stream`.
    """
    print("\n[per-scene tileset, no paint_table]")
    for stream in (True, False):
        src = _transpile(_world(wide=True, stream=stream, paint_table=False))
        tag = "stream" if stream else "resident"
        check("function warm(" in src,
              "wide + per-scene tilesets (%s): warm() is emitted" % tag)
        if "function warm(" not in src:
            continue
        body = src.split("function warm(")[1].split("\n    }")[0]
        check("bkg.set_data" in body,
              "  ... and it uploads the scene's tileset (%s)" % tag)
        check("bkg.set_tiles" not in body,
              "  ... and never the map (%s)" % tag)
        exp = [l for l in src.splitlines() if l.strip().startswith("export ")]
        check(exp and "warm" in exp[-1], "  ... and warm is exported (%s)" % tag)
    # paint_table alone (no stream): the maps are resident, so warm() is just
    # the tileset upload - it must not reach for the range seam.
    src = _transpile(_world(wide=True, stream=False, paint_table=True))
    check("function warm(" in src, "wide + paint_table alone emits warm()")
    if "function warm(" in src:
        body = src.split("function warm(")[1].split("\n    }")[0]
        # `ptr_range` stays - it is how the TABLE-driven tileset upload indexes
        # the concatenated TILESETS, and lowers to a plain (SYM + off) on a
        # directly-mapped console. What must NOT be there is a window WARM:
        # with the maps resident there is no window to warm.
        check("use_range" not in body and "range_base" not in body,
              "  ... and warms no window (the maps are resident)")


def test_dead_shared_tileset_branch_is_not_emitted():
    """`paint_table`'s table-driven upload keeps a `TS_TC[scene] == 0` arm for
    a scene that falls back to the world's shared TILESET. When EVERY scene
    brought its own image that arm is provably dead - and it was the only
    reference left to `TILESET`, so emitting it pinned a 4-tile placeholder
    (and its `bkg.set_data` call) in the RESIDENT image of a conversion that
    can never draw it.

    Measured on the reference-engine sample conversion, which is exactly that shape (17 scenes, all
    with their own tileset): **+218 B of bank 0 on both GB targets** - GB
    1,796 -> 2,014 B spare, GBC 851 -> 1,069, which is a quarter of the
    tightest target's remaining headroom.

    A world where some scene DOES fall back keeps the branch and is unchanged.
    """
    print("\n[the dead shared-TILESET branch]")
    all_own = _transpile(_world(wide=True, per_scene_ts=True))
    check("if tsc == 0 {" not in all_own,
          "every scene has its own tileset: no shared-TILESET fallback arm")
    check("bkg.set_data(0, TILE_COUNT, TILESET)" not in all_own,
          "  ... so nothing references TILESET (tree-shaking drops the array)")
    # ... and the fallback must survive where a scene really needs it.
    mixed = _world(wide=True, per_scene_ts=True)
    del mixed["scene"][1]["tileset"]         # room1 uses the shared image
    src = _transpile(mixed)
    check("if tsc == 0 {" in src,
          "one scene without its own tileset: the fallback arm is kept")
    check("bkg.set_data(0, TILE_COUNT, TILESET)" in src,
          "  ... and TILESET is referenced, so it stays in the ROM")


def test_rooms_calls_warm_in_lockstep():
    print("\n[rooms.mos lockstep]")
    from mosaik_vm.rooms import emit_rooms_mos
    base = {"types": ["platform"], "uniform": False, "has_collision": True,
            "wide_rooms": True}
    on = emit_rooms_mos(dict(base, warm_fn=True))
    off = emit_rooms_mos(dict(base, warm_fn=False))
    check("scenes.warm(rm)" in on, "the wide arm calls scenes.warm(rm) when emitted")
    check("scenes.warm(rm)" not in off,
          "and does NOT when the transpiler did not emit it")
    # The narrow arm must still paint, either way.
    check("scenes.paint(rm)" in on and "scenes.paint(rm)" in off,
          "the narrow arm still calls paint()")
    narrow = emit_rooms_mos({"types": ["platform"], "uniform": True,
                             "has_collision": True, "wide_rooms": False})
    check("scenes.warm" not in narrow, "a world with no wide room never warms")
    # The ROAM arm (a room past the background on the HEIGHT axis) needs it
    # for the same reason - the platformer conversion's 20x36 title screen is exactly that shape.
    roam = emit_rooms_mos({"types": ["topdown"], "uniform": False,
                           "has_collision": True, "roam_rooms": True,
                           "warm_fn": True})
    check("scenes.warm(rm)" in roam, "the ROAM arm calls scenes.warm(rm) too")


def test_smsgg_row_limit_warms_in_lockstep():
    """A 29..32-row room is ROAM on the SMS / Game Gear (32x28 name table), so
    `generate_rooms` - which reads the project's `target_platforms` - calls
    `scenes.warm(rm)` for it. The transpiler decided on 32 rows and emitted no
    warm(), so every console of such a project failed to compile with
    `module "scenes" has no module-level symbol "warm"` (the adventure check project with
    sms/gamegear added to its targets, 2026-09-17). Both now read the one fact,
    from a world at the root or in `assets/`, and a GB-only or project-less
    world is unchanged."""
    print("\n[SMS/GG 28-row limit]")
    from mosaik_vm.rooms.config import _targets_smsgg
    world = _world(wide=False, stream=True, paint_table=True)
    world["scene"][0]["map_h"] = 32
    world["scene"][0]["map"] = [0] * (20 * 32)
    world["scene"][0]["collision"] = [0] * (20 * 32)
    world["scene"][0]["scene_type"] = "topdown"
    check("function warm(" not in _transpile(world),
          "a 32-row room with no project around it: no warm() (unchanged)")
    for targets, sub in ((["gameboy", "gameboy_color"], "assets"),
                         (["gameboy", "gamegear"], "assets"),
                         (["sms"], "")):
        root = tempfile.mkdtemp(prefix="widept_proj_")
        try:
            with open(os.path.join(root, "mosaik.toml"), "w",
                      encoding="utf-8") as f:
                f.write("[project]\nname = \"t\"\ntarget_platforms = [%s]\n"
                        % ", ".join('"%s"' % t for t in targets))
            wdir = os.path.join(root, sub) if sub else root
            os.makedirs(wdir, exist_ok=True)
            _make_png(os.path.join(wdir, "tiles.png"))
            for sc in world["scene"]:
                _make_png(os.path.join(wdir, sc["tileset"]))
            src = transpile(world, wdir)
            rooms_warms = _targets_smsgg(root)
            check(("function warm(" in src) == rooms_warms,
                  "targets %s (world in %s): warm() emitted = %s, as rooms.mos "
                  "decides" % ("/".join(targets), sub or "root", rooms_warms))
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("wide world + [world] paint_table (scenes.warm)")
    print("=" * 60)
    test_wide_stream_paint_table_transpiles()
    test_non_wide_is_unchanged()
    test_per_scene_tileset_warms_without_paint_table()
    test_dead_shared_tileset_branch_is_not_emitted()
    test_rooms_calls_warm_in_lockstep()
    test_smsgg_row_limit_warms_in_lockstep()
    print("\n" + "=" * 60)
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        sys.exit(1)
    print("All wide+paint_table checks passed")
