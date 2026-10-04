#!/usr/bin/env python3
"""The 2D streamer's ROW PERIOD is the console's, not the Game Boy's (K8).

`engine.scroll2d` treats the hardware background as a 32x32 sliding ring: it
writes logical row `r` at ring row `r & 31` and sets the vertical scroll to
`camy & 255`. That is the GB family's tilemap exactly, and wrong on the SMS /
Game Gear, whose name table is 32x**28** and whose vertical scroll register
wraps at **224**. `graphics.bkg`'s set_tiles already refuses rows >= 28 (writing
past the name table overruns the SAT), so every logical row congruent to 28..31
mod 32 was silently DROPPED and everything below the first dropped row was drawn
four rows out of place - the picture stopped matching collision.

Measured on the SMS/GG sample conversion's 56x56 town room, walking up
column 24 with the camera settled (the drawn background cross-correlated against
the view rendered straight from world.toml):

    camera camy | before | after
    ------------|--------|------
    256         |      0 |   0     (the ring happens to align there)
    232         |    -32 |   0
    220         |    -32 |   0

Two halves, because a room can be too tall in two different ways:

  * the ROAM path (this module) gets a per-console row period;
  * a room of 29..32 rows was PAINTED rather than streamed, since 32 rows fit
    the GB's tilemap - on SMS/GG its bottom four rows can never be resident, so
    `generate_rooms` routes it through scroll2d there too (`BKG_ROWS`/`BKG_PY`,
    emitted only for a project that targets one of those consoles).

The GB family keeps `& 31` / `& 255` under conditional compilation, so its
generated code is unchanged - the reference-engine sample conversion's Game Boy ROM is
byte-identical across this change (md5 553177155c751f2d711ff0bd7f7138e4).
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

import mosaik_vm                                    # noqa: E402
from mosaik import MosaikCompiler                   # noqa: E402
from mosaik_vm.rooms import BKG_ROWS_SMSGG          # noqa: E402

LIB = os.path.join(ROOT, "lib")
passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def _mod(tier, name):
    with open(os.path.join(LIB, tier, name), encoding="utf-8") as f:
        return (name, f.read())


# A minimal program that links engine.scroll2d, so the module's own C is what
# is being read (the ring writes and the scroll write).
SHELL = '''
module "main" {
    import "engine.scroll2d"
    import "platform.video"
    var camx: u16 = 0
    var camy: u16 = 0
    function tile_at(c: u16, r: u16) -> u8 {
        var t: u16 = c + r
        return t
    }
    function main() {
        video.enable_lcd()
        scroll2d.fill2d(64, 64, tile_at)
        loop {
            camy = camy + 1
            scroll2d.update2d(camx, camy, tile_at)
            video.wait_vblank()
        }
    }
    export main
}
'''


def compile_scroll2d(platform):
    return MosaikCompiler().compile_program(
        [("main.mos", SHELL.strip()), _mod("engine", "scroll2d.mos")],
        platform=platform)


def test_the_module_wraps_to_the_consoles_background():
    gb = compile_scroll2d("gameboy")
    check("GB: the ring row is the cheap 32-row mask",
          "engine_scroll2d_HW - 1" in gb and "% 28" not in gb)
    check("GB: the vertical scroll is the cheap 256 px mask",
          "camy & 255" in gb and "% 224" not in gb,
          "no `camy & 255` in the gameboy output")
    for plat in ("sms", "gamegear"):
        c = compile_scroll2d(plat)
        check("%s: the ring row wraps at 28, not 32" % plat,
              "% engine_scroll2d_HV" in c and "#define engine_scroll2d_HV (28)" in c)
        check("%s: the vertical scroll wraps at 224, not 256" % plat,
              "% engine_scroll2d_VPX" in c
              and "#define engine_scroll2d_VPX (224)" in c)
        check("%s: a streamed COLUMN fills 28 rows" % plat,
              "n < engine_scroll2d_HV" in c)
        check("%s: COLUMNS still wrap at 32 (that half is the same hardware)"
              % plat, "engine_scroll2d_HW - 1" in c)
        check("%s: the row modulo is resolved per FILL, not per tile" % plat,
              c.count("% engine_scroll2d_HV") == 1,
              "%d modulo sites" % c.count("% engine_scroll2d_HV"))


def _function(c, name):
    """The C body of `name` (its DEFINITION, not the prototype)."""
    i = c.find("void %s(" % name)
    while i >= 0 and c[c.find(")", i) + 1:c.find(")", i) + 2] == ";":
        i = c.find("void %s(" % name, i + 1)
    if i < 0:
        return ""
    return c[i:c.find("\n}\n", i) + 3]


def test_the_ring_never_reads_outside_the_room():
    """A room shorter than the ring (SMS/GG 28 rows, GB 32) or narrower than
    32 columns must not ask `tile_at` for a cell past its edge: under
    `[world] paint_table` the maps are CONCATENATED, so row 18 of an 18-row
    town was the next scene's row 0, drawn under the town on the SMS's
    24-row screen (found by the showcase RPG, 2026-09-28). The guard sits in
    `put_tile`, the one writer every column, row and band fill goes through."""
    for plat in ("gameboy", "sms", "gamegear"):
        body = _function(compile_scroll2d(plat), "engine_scroll2d_put_tile")
        guard = body.find("engine_scroll2d_s_wrows")
        read = body.find("tile_at(", body.find("{"))         # the CALL, not the parameter
        check("%s: put_tile tests the room's width AND height" % plat,
              "engine_scroll2d_s_wcols" in body and guard >= 0, body[:200])
        check("%s: ... BEFORE it asks tile_at for the cell" % plat,
              0 <= guard < read, "guard at %d, read at %d" % (guard, read))


def test_the_player_scrolls_to_the_same_period():
    """vm.player writes the scroll register too (the ROAM arm), and the two
    must agree - a period only scroll2d knew would slip the picture anyway."""
    src = '''
module "main" {
    import "vm.player"
    function solid(x: u16, y: u16) -> bool { return false }
    function main() {
        player.setup_roam(0, 0, 0, 16, 16, 1, 100, 100, solid)
        loop { player.update() }
    }
    export main
}
'''
    mods = [("main.mos", src.strip()), _mod("vm", "player.mos"),
            _mod("engine", "camera.mos")]
    for plat, want, unwanted in (("sms", "% 224", None),
                                 ("gamegear", "% 224", None),
                                 ("gameboy", "camy & 255", "% 224")):
        try:
            c = MosaikCompiler().compile_program(mods, platform=plat)
        except Exception as exc:                     # pragma: no cover
            check("%s: vm.player compiles" % plat, False, str(exc)[:140])
            continue
        check("%s: the ROAM scroll uses %s" % (plat, want), want in c)
        if unwanted:
            check("%s: and NOT the SMS period" % plat, unwanted not in c)


def _world(root, w, h, stype, targets):
    """A one-scene project on disk, with mosaik.toml naming its consoles."""
    import mosaik_assets
    import toml
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    os.makedirs(os.path.join(root, "assets"))
    os.makedirs(os.path.join(root, "src"))
    mosaik_assets.write_png_indexed(os.path.join(root, "assets", "t.png"),
                                    8, 16, [0] * 64 + [3] * 64, pal)
    world = {
        "world": {"module": "scenes", "map_w": w, "map_h": h, "vm": True},
        "tileset": {"png": "t.png"},
        "kinds": {"player": 0},
        "scene": [{"name": "room", "scene_type": stype, "map_w": w, "map_h": h,
                   "map": [[0] * w for _ in range(h)]}],
    }
    with open(os.path.join(root, "assets", "world.toml"), "w",
              encoding="utf-8") as f:
        toml.dump(world, f)
    with open(os.path.join(root, "mosaik.toml"), "w", encoding="utf-8") as f:
        toml.dump({"project": {"name": "t", "target_platforms": targets}}, f)
    open(os.path.join(root, "src", "rooms.mos"), "w").close()
    return root


def _gen(root):
    path = mosaik_vm.generate_rooms(root)
    if not path:
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_a_29_to_32_row_room_streams_on_smsgg(tmpdir):
    gb = _gen(_world(os.path.join(tmpdir, "gb"), 20, 32, "topdown",
                     ["gameboy", "gameboy_color"]))
    sms = _gen(_world(os.path.join(tmpdir, "sms"), 20, 32, "topdown",
                      ["sms", "gamegear"]))
    check("a GB-only project keeps the flat 32 (byte-identical)",
          "BKG_ROWS" not in gb and "BKG_PY" not in gb and "setup_roam" not in gb)
    check("an SMS/GG project routes a 32-row room through scroll2d",
          "player.setup_roam(" in sms and 'import "engine.scroll2d"' in sms)
    check("...via a per-console const, not a baked 28",
          "const BKG_ROWS: u8 = %d" % BKG_ROWS_SMSGG in sms
          and "const BKG_PY: u16 = 224" in sms
          and "const BKG_ROWS: u8 = 32" in sms)
    check("...compared in PIXELS at setup", "mh > BKG_PY" in sms)
    check("...and in TILES at room load", "scenes.MAP_H > BKG_ROWS" in sms)
    # A room that fits BOTH consoles must not change at all.
    small_gb = _gen(_world(os.path.join(tmpdir, "sgb"), 20, 18, "topdown",
                           ["gameboy"]))
    small_sms = _gen(_world(os.path.join(tmpdir, "ssms"), 20, 18, "topdown",
                            ["sms", "gamegear"]))
    check("a room that fits 28 rows is byte-identical on either target",
          small_gb == small_sms and "BKG_ROWS" not in small_sms)


def test_no_project_is_newly_refused(tmpdir):
    """The refusals stay at 32 rows. Only the ROAM threshold moves, so a 32-row
    SHMUP or PLATFORM room on an SMS target keeps generating exactly as it did -
    it has always drawn its bottom rows wrong there, which is a separate defect
    and is reported rather than turned into a build failure."""
    for stype in ("shmup", "platform"):
        try:
            out = _gen(_world(os.path.join(tmpdir, "n" + stype), 20, 32, stype,
                              ["gameboy", "sms", "gamegear"]))
            check("a 32-row %s room on an SMS target still generates" % stype,
                  "player.setup" in out)
        except Exception as exc:
            check("a 32-row %s room on an SMS target still generates" % stype,
                  False, str(exc)[:140])


def main():
    import tempfile
    print("engine.scroll2d row period (K8)")
    print("=" * 52)
    test_the_module_wraps_to_the_consoles_background()
    test_the_ring_never_reads_outside_the_room()
    test_the_player_scrolls_to_the_same_period()
    with tempfile.TemporaryDirectory() as tmp:
        test_a_29_to_32_row_room_streams_on_smsgg(tmp)
        test_no_project_is_newly_refused(tmp)
    print("=" * 52)
    if failed:
        print("%d FAILED, %d passed" % (failed, passed))
        return 1
    print("All row-period checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
