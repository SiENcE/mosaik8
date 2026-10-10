#!/usr/bin/env python3
"""Layer-3 scene transpiler (mosaik_scenes.py) -- game-framework Phase 4.

Transpiles a small two-scene world (a PNG tileset + maps + objects + a door)
to a mosaik module and checks: the emitted module + a game using it compile on
a GBDK and a cc65 console, the generated tables / selector / door data are
present, and transpilation is deterministic.
"""

import copy
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import toml

from mosaik import MosaikCompiler
from mosaik_assets import write_png_indexed
from mosaik_scenes import transpile, load_world_dir, SceneError


def make_tileset_png(path):
    # 8x16 indexed PNG = two 8x8 tiles: floor (index 0) over wall (index 3).
    # A <=4-entry indexed PNG maps indices straight to GB colour values.
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    idx = [0] * 64 + [3] * 64
    write_png_indexed(path, 8, 16, idx, pal)


WORLD = {
    "world": {"module": "scenes", "map_w": 4, "map_h": 4},
    "tileset": {"png": "tiles.png"},
    "kinds": {"player": 0, "npc": 1, "chest": 2},
    "scene": [
        {"name": "field",
         "map": [[1, 1, 1, 1], [1, 0, 0, 1], [1, 0, 0, 1], [1, 1, 1, 1]],
         "object": [{"kind": "npc", "x": 16, "y": 16},
                    {"kind": "chest", "x": 24, "y": 16}]},
        {"name": "cave",
         "map": [1, 1, 1, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 1, 1, 1],  # flat form
         "object": [{"kind": "player", "x": 8, "y": 8}]},
    ],
    "door": [{"from": "field", "tx": 2, "ty": 3, "to": "cave", "ex": 16, "ey": 8}],
}

# A game that imports the generated module and uses the selector + tables.
GAME = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    import "scenes"
    var room: u8 = 0
    function tile_here(px: u8, py: u8) -> u8 {
        return scenes.map_tile(room, (py / 8) * scenes.MAP_W + (px / 8))
    }
    function try_doors(cx: u8, cy: u8) {
        for d in 0..scenes.DOOR_COUNT {
            if scenes.DOOR_FROM[d] == room and scenes.DOOR_TX[d] == cx and scenes.DOOR_TY[d] == cy {
                room = scenes.DOOR_TO[d]
            }
        }
    }
    function main() {
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        var k: u8 = 0
        for i in 0..scenes.OBJ_COUNT {
            if scenes.OBJ_SCENE[i] == 0 and scenes.OBJ_KIND[i] == scenes.KIND_NPC {
                k = scenes.OBJ_X[i]
            }
        }
        try_doors(2, 3)
        video.wait_vblank()
    }
    export main
}
'''


def _collision_body(src):
    """The generated collision_at selector's lines, up to its closing brace."""
    lines = src.splitlines()
    start = next(i for i, ln in enumerate(lines) if "function collision_at" in ln)
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "    }")
    return "\n".join(lines[start:end + 1])


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("Scene transpiler (Layer 3 / Phase 4)")
    print("=" * 50)
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        make_tileset_png(os.path.join(tmp, "tiles.png"))
        src = transpile(WORLD, tmp)
        src2 = transpile(WORLD, tmp)

        ok &= check("transpile is deterministic", src == src2)
        ok &= check("emits module + per-scene maps + selector",
                    'module "scenes"' in src
                    and "const FIELD_MAP: array[u8, 16]" in src
                    and "const CAVE_MAP: array[u8, 16]" in src
                    and "function map_tile(scene: u8, idx: u16)" in src)
        ok &= check("emits the tileset from the PNG pipeline",
                    "const TILE_COUNT: u8 = 2" in src
                    and "const TILESET: array[u8, 32]" in src)
        # The tileset must carry REAL tile data, not a blank all-zero array (a
        # flat-vs-rows misuse of write_png_indexed once produced a corrupt PNG
        # that decoded to all zeros, so the background rendered empty on every
        # console). The wall tile (index 3) is solid colour 3 -> 0xFF bytes.
        ts = src.split("const TILESET", 1)[1].split("= [", 1)[1].split("]", 1)[0]
        ok &= check("tileset has non-zero tile data (wall tile present)",
                    "255" in ts)
        ok &= check("emits object + door tables + kind constants",
                    "const OBJ_COUNT: u8 = 3" in src
                    and "const DOOR_COUNT: u8 = 1" in src
                    and "const KIND_NPC: u8 = 1" in src
                    and "DOOR_FROM" in src and "DOOR_TO" in src
                    and "DOOR_TX" in src and "DOOR_EX" in src)

        # Background tile animation (the reference-engine tile-data swap): an
        # [[animated_tile]] lowers to a scenes.anim_tick() the game loop calls.
        # anim_tick() is ALWAYS emitted + exported (a no-op when there are no
        # animated tiles) so a loop can call scenes.anim_tick() unconditionally
        # and keeps compiling when the last animated tile is removed.
        ok &= check("animation-free world still emits a no-op anim_tick + export",
                    "function anim_tick()" in src
                    and "anim_tick" in src.split("export", 1)[1]
                    and "ANIM0_TILE" not in src)
        aworld = dict(WORLD, animated_tile=[
            {"tile": 1, "count": 1, "period": 8, "frames": [0, 1, 0]}])
        asrc = transpile(aworld, tmp)
        ok &= check("[[animated_tile]] emits anim_tick + frame data + export",
                    "function anim_tick()" in asrc
                    and "const ANIM0_F0: array[u8, 16]" in asrc
                    and "bkg.set_data(ANIM0_TILE, ANIM0_COUNT, ANIM0_F1)" in asrc
                    and "anim_tick" in asrc.split("export", 1)[1])
        agame = GAME.replace("video.wait_vblank()",
                             "scenes.anim_tick()\n        video.wait_vblank()")
        for platform in ("gameboy", "lynx"):
            out = MosaikCompiler().compile_program(
                [("main.mos", agame), ("scenes.mos", asrc)], platform=platform)
            ok &= check("[%s] animated scenes module + game compile" % platform,
                        not out.startswith("Compilation error:"))

        # ---- Collision layer (reference-engine-style per-cell collision TYPE) -------
        # Collision-free worlds stay byte-identical: no constants, no array, no
        # selector, not exported (the additive rule, like animated tiles).
        ok &= check("collision-free world emits no collision layer",
                    "collision_at" not in src
                    and "_COLLISION" not in src
                    and "COLLIDE_SOLID" not in src)

        # An explicit per-scene `collision` array lowers to COLLISION_* arrays +
        # a collision_at selector + named cell-type constants, all exported.
        cworld = dict(WORLD)
        cworld["scene"] = [
            dict(WORLD["scene"][0],
                 collision=[[1, 1, 1, 1], [1, 0, 0, 1],
                            [1, 0, 0, 1], [1, 1, 1, 1]]),
            dict(WORLD["scene"][1]),  # no explicit + no solid set -> all-zero
        ]
        csrc = transpile(cworld, tmp)
        ok &= check("explicit collision -> arrays + selector + constants + export",
                    "const COLLIDE_SOLID: u8 = 1" in csrc
                    and "const FIELD_COLLISION: array[u8, 16]" in csrc
                    and "function collision_at(scene: u8, idx: u16)" in csrc
                    and "collision_at" in csrc.split("export", 1)[1]
                    and "FIELD_COLLISION" in csrc.split("export", 1)[1])
        # ...and a scene whose layer is ALL CLEAR costs no array at all: the
        # selector's fall-through answers 0 for it (one walled room used to
        # cost every other room a w*h array of zeros; 2026-10-09).
        ok &= check("an all-clear scene emits no collision array (selector falls through to 0)",
                    "CAVE_COLLISION" not in csrc
                    and "if scene == 1 {" not in _collision_body(csrc)
                    and "return 0" in _collision_body(csrc))

        # The TILE-BASED solid set: `[collision] solid` is the configurable
        # which-tiles-collide set, exported as SOLID_TILES / is_solid(t) for a
        # hand-written game to read. It is DECOUPLED from the per-cell layer: with
        # no painted `collision` array it emits NO per-cell arrays / collision_at
        # (that doubled scene data and overflowed the Lynx), just the small list.
        iworld = dict(WORLD, collision={"solid": [1, 3]})
        isrc = transpile(iworld, tmp)
        ok &= check("[collision] solid exports a readable SOLID_TILES + is_solid",
                    "const SOLID_COUNT: u8 = 2" in isrc
                    and "SOLID_TILES" in isrc
                    and "function is_solid(t: u8) -> bool" in isrc
                    and "is_solid" in isrc.split("export", 1)[1])
        ok &= check("[collision] solid alone emits NO per-cell layer (Lynx-safe)",
                    "function collision_at" not in isrc
                    and "FIELD_COLLISION" not in isrc)

        # The generated collision module + a game sampling collision_at compile.
        cgame = GAME.replace(
            "try_doors(2, 3)",
            "if scenes.collision_at(room, 0) == scenes.COLLIDE_SOLID {\n"
            "            k = 1\n        }\n        try_doors(2, 3)")
        for platform in ("gameboy", "lynx"):
            out = MosaikCompiler().compile_program(
                [("main.mos", cgame), ("scenes.mos", csrc)], platform=platform)
            ok &= check("[%s] collision scenes module + game compile" % platform,
                        not out.startswith("Compilation error:"))

        # ---- Per-scene map sizes --------------------------------------------
        # A UNIFORM world (every scene the world size) emits NO per-scene size
        # tables and stays byte-identical (the additive rule, like collision /
        # animated tiles). A world with a differently-sized scene (e.g. a
        # screen-sized title / menu room that needn't be a full scrolling room)
        # emits SCENE_W[]/SCENE_H[] + each scene's own-length map array + a
        # per-scene paint(), all exported.
        ok &= check("uniform world emits no per-scene size tables",
                    "SCENE_W" not in src and "SCENE_H" not in src)

        pworld = dict(WORLD)
        pworld["scene"] = [
            dict(WORLD["scene"][0]),                       # field: 4x4 (default)
            {"name": "menu", "map_w": 4, "map_h": 2,       # a short menu room
             "map": [[1, 1, 1, 1], [1, 0, 0, 1]], "object": []},
        ]
        pworld["door"] = [{"from": "field", "tx": 2, "ty": 3,
                           "to": "menu", "ex": 8, "ey": 0}]
        psrc = transpile(pworld, tmp)
        ok &= check("non-uniform world emits SCENE_W/SCENE_H tables + export",
                    "const SCENE_W: array[u8, 2] = [" in psrc
                    and "const SCENE_H: array[u8, 2] = [" in psrc
                    and "SCENE_W" in psrc.split("export", 1)[1]
                    and "SCENE_H" in psrc.split("export", 1)[1])
        ok &= check("each scene gets its own-size map array (4x4 vs 4x2)",
                    "const FIELD_MAP: array[u8, 16]" in psrc
                    and "const MENU_MAP: array[u8, 8]" in psrc)
        ok &= check("paint() uses each scene's own dims, not MAP_W/MAP_H",
                    "bkg.set_tiles(0, 0, 4, 4, FIELD_MAP)" in psrc
                    and "bkg.set_tiles(0, 0, 4, 2, MENU_MAP)" in psrc)

        # A game indexing a scene's map via SCENE_W[room] compiles on both
        # backends (the per-scene-width stride the composer will emit).
        pgame = GAME.replace("scenes.MAP_W + (px / 8)",
                             "scenes.SCENE_W[room] + (px / 8)")
        for platform in ("gameboy", "lynx"):
            out = MosaikCompiler().compile_program(
                [("main.mos", pgame), ("scenes.mos", psrc)], platform=platform)
            ok &= check("[%s] per-scene-size module + game compile" % platform,
                        not out.startswith("Compilation error:"))

        # Per-scene sizes round-trip through the split layout too (the per-scene
        # map_w/map_h live in scenes/<name>.toml -> byte-identical module).
        psplit = os.path.join(tmp, "world_psplit")
        os.makedirs(os.path.join(psplit, "scenes"))
        make_tileset_png(os.path.join(psplit, "tiles.png"))
        pheader = {"world": dict(WORLD["world"], scene_order=["field", "menu"]),
                   "tileset": WORLD["tileset"], "kinds": WORLD["kinds"]}
        with open(os.path.join(psplit, "world.toml"), "w", encoding="utf-8") as f:
            toml.dump(pheader, f)
        for sc in pworld["scene"]:
            with open(os.path.join(psplit, "scenes", sc["name"] + ".toml"),
                      "w", encoding="utf-8") as f:
                toml.dump(sc, f)
        with open(os.path.join(psplit, "doors.toml"), "w", encoding="utf-8") as f:
            toml.dump({"door": pworld["door"]}, f)
        psrc_split = transpile(load_world_dir(psplit), psplit)
        ok &= check("split per-scene-size world == single-file", psrc_split == psrc)

        # Split-per-resource layout (the reference engine's scene-resource analogue) assembles to the
        # SAME world and emits a byte-identical module: world.toml header +
        # scenes/<name>.toml per scene + doors.toml. scene_order pins the ids
        # to match the single-file scene array (sorted filenames would put cave
        # before field).
        split = os.path.join(tmp, "world_split")
        os.makedirs(os.path.join(split, "scenes"))
        make_tileset_png(os.path.join(split, "tiles.png"))
        header = {"world": dict(WORLD["world"], scene_order=["field", "cave"]),
                  "tileset": WORLD["tileset"], "kinds": WORLD["kinds"]}
        with open(os.path.join(split, "world.toml"), "w", encoding="utf-8") as f:
            toml.dump(header, f)
        for sc in WORLD["scene"]:
            with open(os.path.join(split, "scenes", sc["name"] + ".toml"),
                      "w", encoding="utf-8") as f:
                toml.dump(sc, f)
        with open(os.path.join(split, "doors.toml"), "w", encoding="utf-8") as f:
            toml.dump({"door": WORLD["door"]}, f)

        src_split = transpile(load_world_dir(split), split)
        ok &= check("split-per-resource world == single-file world",
                    src_split == src)

        # Collision round-trips through the split layout too: a per-scene
        # `collision` key in scenes/<name>.toml assembles to a byte-identical
        # module (the two-forms invariant, extended to the collision layer).
        csplit = os.path.join(tmp, "world_csplit")
        os.makedirs(os.path.join(csplit, "scenes"))
        make_tileset_png(os.path.join(csplit, "tiles.png"))
        cheader = {"world": dict(WORLD["world"], scene_order=["field", "cave"]),
                   "tileset": WORLD["tileset"], "kinds": WORLD["kinds"]}
        with open(os.path.join(csplit, "world.toml"), "w", encoding="utf-8") as f:
            toml.dump(cheader, f)
        for sc in cworld["scene"]:
            with open(os.path.join(csplit, "scenes", sc["name"] + ".toml"),
                      "w", encoding="utf-8") as f:
                toml.dump(sc, f)
        with open(os.path.join(csplit, "doors.toml"), "w", encoding="utf-8") as f:
            toml.dump({"door": WORLD["door"]}, f)
        csrc_split = transpile(load_world_dir(csplit), csplit)
        ok &= check("split-per-resource collision world == single-file",
                    csrc_split == csrc)

        # ---- One generic paint interpreter ----------------------------------
        # [world] paint_table concatenates every scene's map (+ collision) into
        # ONE flat MAPS/COLLISION table + a per-scene offset table MAP_OFF[], so
        # map_tile/collision_at are an O(1) indexed read and paint() is one loop
        # over the descriptor -- no per-scene dispatch code. Additive: a world
        # WITHOUT the flag is byte-identical.
        ok &= check("paint_table absent -> byte-identical (no MAPS/MAP_OFF)",
                    "const MAPS:" not in src and "MAP_OFF" not in src
                    and "return MAPS[MAP_OFF" not in src)
        ptworld = dict(WORLD, world=dict(WORLD["world"], paint_table=True))
        # give both scenes a collision layer so the concatenated COLLISION path runs
        ptworld["scene"] = [
            dict(WORLD["scene"][0],
                 collision=[[1, 1, 1, 1], [1, 0, 0, 1], [1, 0, 0, 1], [1, 1, 1, 1]]),
            dict(WORLD["scene"][1],
                 collision=[0, 0, 0, 0, 0, 1, 1, 0, 0, 1, 1, 0, 0, 0, 0, 0]),
        ]
        ptsrc = transpile(ptworld, tmp)
        ok &= check("paint_table -> one concatenated MAPS[32] + MAP_OFF offsets",
                    "const MAPS: array[u8, 32] = [" in ptsrc
                    and "const MAP_OFF" in ptsrc
                    and "0, 16" in ptsrc.split("MAP_OFF", 1)[1]
                    and "FIELD_MAP" not in ptsrc and "CAVE_MAP" not in ptsrc)
        ok &= check("paint_table -> O(1) map_tile (no per-scene dispatch)",
                    "return MAPS[MAP_OFF[scene] + idx]" in ptsrc
                    and "return FIELD_MAP[idx]" not in ptsrc)
        ok &= check("paint_table -> paint() is ONE loop over the descriptor",
                    "var off: u16 = MAP_OFF[scene]" in ptsrc
                    and "bkg.set_tiles(0, row, w, 1, paint_buf)" in ptsrc)
        ok &= check("paint_table -> concatenated COLLISION shares MAP_OFF, O(1)",
                    "const COLLISION: array[u8, 32] = [" in ptsrc
                    and "return COLLISION[MAP_OFF[scene] + idx]" in ptsrc
                    and "FIELD_COLLISION" not in ptsrc)
        ptexp = ptsrc.split("export", 1)[1]
        ok &= check("paint_table -> exports MAPS/MAP_OFF/COLLISION (not _MAP names)",
                    "MAPS" in ptexp and "MAP_OFF" in ptexp and "COLLISION" in ptexp
                    and "FIELD_MAP" not in ptexp)
        # The generated interpreter module + a game using it compile on both
        # backends (the new paint() loop + O(1) selectors are real runtime code).
        for platform in ("gameboy", "lynx"):
            out = MosaikCompiler().compile_program(
                [("main.mos", cgame), ("scenes.mos", ptsrc)], platform=platform)
            ok &= check("[%s] paint_table module + game compile" % platform,
                        not out.startswith("Compilation error:"))

        # paint_table + [world] stream COMPOSE (item 33 stream-compose): the
        # concatenated MAPS is archived whole and the CURRENT room's window streams
        # via the range seam (assets.range_base/use_range/ptr_range/range_byte), so
        # map_tile/paint stay O(1) code AND the map data streams off-resident.
        pmsrc = transpile(dict(ptworld,
                               world=dict(ptworld["world"], stream=True)), tmp)
        ok &= check("paint_table + stream -> range seam in the module",
                    'import "platform.assets"' in pmsrc
                    and "assets.range_base(MAPS," in pmsrc
                    and "assets.ptr_range(MAPS, off, n)" in pmsrc
                    and "return assets.range_byte(MAPS, MAP_OFF[scene], idx)" in pmsrc
                    and "assets.use_range(COLLISION, off, n)" in pmsrc
                    and "return assets.range_byte(COLLISION, MAP_OFF[scene], idx)" in pmsrc)
        # A driver that calls paint + map_tile + collision_at, so all three
        # survive tree-shaking and the range verbs are collected (MAPS/COLLISION
        # actually stream).
        PMGAME = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    import "scenes"
    var room: u8 = 0
    var t: u8 = 0
    function main() {
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        scenes.paint(room)
        t = scenes.map_tile(room, 5)
        t = scenes.collision_at(room, 5)
        video.wait_vblank()
    }
    export main
}
'''
        # On the Lynx the window streams: MAPS/COLLISION leave the resident image
        # into the cart archive + the range cache appears; a directly-mapped
        # console keeps them resident (the seam is a pointer/index -> no cache).
        cl = MosaikCompiler()
        cl_pm = cl.compile_program(
            [("main.mos", PMGAME), ("scenes.mos", pmsrc)], platform="lynx")
        ok &= check("[lynx] paint_table+stream streams the window (range cache)",
                    not cl_pm.startswith("Compilation error:")
                    and "gbs_asset_ptr_range" in cl_pm
                    and "gbs_asset_find_range" in cl_pm
                    and "lseek(1" in cl_pm
                    and "const unsigned char MAPS" not in cl_pm  # stripped resident
                    and len(cl.code_generator.streamed_archive) > 0)
        for platform in ("gameboy", "sms", "pce", "nes"):
            out = MosaikCompiler().compile_program(
                [("main.mos", PMGAME), ("scenes.mos", pmsrc)], platform=platform)
            ok &= check("[%s] paint_table+stream module + game compile" % platform,
                        not out.startswith("Compilation error:"))

        # ---- Wide level (> 255 tiles per axis) uses u16 dims ----------------
        # A WIDE world (the u16-column-streamed platformer path) must emit its
        # MAP_W / SCENE_W as u16: a width of 300 in a u8 wraps to 44, so the
        # composed column-streamer (which walks the map with += SCENE_W[room])
        # reads garbage and the background + collision both break (an imported platformer).
        # A narrow/short axis stays u8 (byte-identical for every existing world).
        wide_world = {
            "tileset": {"png": "tiles.png"},
            "world": {"map_w": 300, "map_h": 18},
            "kinds": {"player": 0},
            "scene": [
                {"name": "big", "map_w": 300, "map_h": 18,
                 "map": [[i % 2 for i in range(300)] for _ in range(18)]},
                {"name": "small", "map_w": 20, "map_h": 18,
                 "map": [[0] * 20 for _ in range(18)]},
            ],
            # A door whose TRIGGER column (298) is past a u8: it must widen to
            # u16 or it aliases (298 -> 42) and never matches the u16 player
            # column, so the door never fires (an imported platformer's fade-on-door).
            "door": [{"from": "big", "tx": 298, "ty": 15,
                      "to": "small", "ex": 8, "ey": 100}],
        }
        wsrc = transpile(wide_world, tmp)
        ok &= check("wide world emits u16 MAP_W + u16 SCENE_W (narrow axis stays u8)",
                    "const MAP_W: u16 = 300" in wsrc
                    and "const MAP_H: u8 = 18" in wsrc
                    and "const SCENE_W: array[u16, 2]" in wsrc
                    and "const SCENE_H: array[u8, 2]" in wsrc)
        # DOOR_TX widens to u16 (trigger col 298 survives); DOOR_TY stays u8
        # (row 15). A narrow world keeps both u8 (byte-identical).
        ok &= check("wide door widens DOOR_TX to u16, keeps 298 (DOOR_TY stays u8)",
                    "const DOOR_TX: array[u16, 1]" in wsrc
                    and "298" in wsrc.split("DOOR_TX", 1)[1].split("= [", 1)[1].split("]", 1)[0]
                    and "const DOOR_TY: array[u8, 1]" in wsrc)

        # ---- VM8 entity script slots -----------
        # A non-VM world is byte-identical (no `vm` -> no slot machinery, no
        # SCENE_TYPE). A VM world with NO attached scripts stays byte-identical to
        # the pre-slots VM output (SCENE_TYPE only, no NO_SCRIPT / import scripts).
        ok &= check("non-VM world emits no slot machinery",
                    "NO_SCRIPT" not in src and 'import "scripts"' not in src
                    and "obj_interact" not in src and "TRIG_COUNT" not in src)
        vm_world = dict(WORLD, world=dict(WORLD["world"], vm=True))
        vmsrc = transpile(vm_world, tmp)
        ok &= check("VM world without slots stays byte-identical (no slot block)",
                    "const SCENE_TYPE: array[u8, 2]" in vmsrc
                    and "NO_SCRIPT" not in vmsrc
                    and 'import "scripts"' not in vmsrc
                    and "obj_init" not in vmsrc and "TRIG_COUNT" not in vmsrc)

        # Bind per-instance actor slots + a per-scene On Init + a [[trigger]] and
        # the transpiler emits selectors referencing scripts.ENTRY_<name>, the
        # trigger rect table, `import "scripts"`, and exports them all.
        sworld = dict(WORLD, world=dict(WORLD["world"], vm=True))
        sworld["scene"] = [
            dict(WORLD["scene"][0], on_init="field_init",
                 object=[{"kind": "npc", "x": 16, "y": 16,
                          "on_interact": "npc_talk", "on_update": "npc_wander"},
                         {"kind": "chest", "x": 24, "y": 16,
                          "on_init": "chest_init"}]),
            dict(WORLD["scene"][1]),
        ]
        sworld["trigger"] = [
            {"id": 1, "from": "field", "tx": 2, "ty": 3, "tw": 2,
             "on_enter": "trap_enter"},
        ]
        ssrc = transpile(sworld, tmp)
        ok &= check("bound slots emit selectors + NO_SCRIPT + import scripts",
                    'import "scripts"' in ssrc
                    and "const NO_SCRIPT: u16 = 0xFFFF" in ssrc
                    and "function obj_interact(i: u16) -> u16" in ssrc
                    and "return scripts.ENTRY_npc_talk" in ssrc
                    and "function obj_update(i: u16) -> u16" in ssrc
                    and "function obj_init(i: u16) -> u16" in ssrc
                    and "return scripts.ENTRY_chest_init" in ssrc
                    and "function scene_init(scene: u8) -> u16" in ssrc
                    and "return scripts.ENTRY_field_init" in ssrc)
        ok &= check("[[trigger]] emits a rect table + trigger_enter selector",
                    "const TRIG_COUNT: u8 = 1" in ssrc
                    and "const TRIG_FROM: array[u8, 1]" in ssrc
                    and "const TRIG_TW: array[u8, 1]" in ssrc
                    and "function trigger_enter(i: u16) -> u16" in ssrc
                    and "return scripts.ENTRY_trap_enter" in ssrc)
        exp = ssrc.split("export", 1)[1]
        ok &= check("slot selectors + trigger table are exported",
                    all(s in exp for s in ("NO_SCRIPT", "obj_interact",
                        "obj_update", "obj_init", "scene_init", "trigger_enter",
                        "TRIG_COUNT", "TRIG_FROM")))
        # The obj slot selector indexes by the OBJ flatten id: the npc is OBJ 0,
        # the chest OBJ 1 (declaration order across scenes). Its interact is on 0.
        interact_body = ssrc.split("function obj_interact", 1)[1].split("}", 2)[0]
        ok &= check("obj_interact keys the npc by its OBJ id (0)",
                    "if i == 0 {" in interact_body)

        # The generated slot module links against a scripts module that provides
        # the ENTRY_* consts (the C linker resolves the symbolic slot refs). A stub
        # scripts module + a shell that calls the selectors compiles on both backends.
        SCRIPTS_STUB = '''
module "scripts" {
    const ENTRY_npc_talk: u16 = 0
    const ENTRY_npc_wander: u16 = 4
    const ENTRY_chest_init: u16 = 8
    const ENTRY_field_init: u16 = 12
    const ENTRY_trap_enter: u16 = 16
    export ENTRY_npc_talk, ENTRY_npc_wander, ENTRY_chest_init, ENTRY_field_init, ENTRY_trap_enter
}
'''
        SLOT_GAME = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    import "scenes"
    var e: u16 = 0
    function main() {
        e = scenes.obj_interact(0)
        e = scenes.scene_init(0)
        e = scenes.trigger_enter(0)
        if scenes.TRIG_COUNT > 0 {
            e = scenes.TRIG_FROM[0]
        }
        video.wait_vblank()
    }
    export main
}
'''
        for platform in ("gameboy", "lynx"):
            out = MosaikCompiler().compile_program(
                [("main.mos", SLOT_GAME), ("scenes.mos", ssrc),
                 ("scripts.mos", SCRIPTS_STUB)], platform=platform)
            ok &= check("[%s] slot scenes module + scripts + shell compile" % platform,
                        not out.startswith("Compilation error:")
                        and "scenes_obj_interact(" in out)

        # The generated module + a game that uses it compile on both backends.
        for platform in ("gameboy", "lynx"):
            out = MosaikCompiler().compile_program(
                [("main.mos", GAME), ("scenes.mos", src)], platform=platform)
            ok &= check("[%s] generated scenes module + game compile" % platform,
                        not out.startswith("Compilation error:"))
            ok &= check("[%s] cross-module scene access lowers" % platform,
                        "scenes_map_tile(" in out
                        and "scenes_DOOR_FROM[" in out
                        and "scenes_TILESET" in out)

        # ---- u8-count guard (OBJ_COUNT/DOOR_COUNT/SCENE_COUNT/TRIG_COUNT) -----
        # Without a bounds check, a world with > 255 placed objects emitted an
        # OUT-OF-RANGE `const OBJ_COUNT: u8 = 257`. That literal survives verbatim
        # into the generated C (a #define, never truncated), so `for i in
        # 0..scenes.OBJ_COUNT` compiled to `uint8_t i; i < 257` -- ALWAYS true for
        # every u8 value, a SILENT INFINITE LOOP at runtime with no compiler
        # warning and no link error. Must raise a clear SceneError at generate
        # time instead (discovered while stress-testing projects/bigworld-paint).
        oworld = dict(WORLD, door=[])
        oworld["scene"] = [dict(WORLD["scene"][0],
                                object=[{"kind": "npc", "x": 8, "y": 8}] * 256)]
        try:
            transpile(oworld, tmp)
            ok &= check("> 255 placed objects raises (was a silent infinite loop)",
                        False)
        except SceneError as e:
            ok &= check("> 255 placed objects raises (was a silent infinite loop)",
                        "255" in str(e))
        # 255 objects exactly (the boundary) still transpiles fine.
        bworld = dict(WORLD, door=[])
        bworld["scene"] = [dict(WORLD["scene"][0],
                                object=[{"kind": "npc", "x": 8, "y": 8}] * 255)]
        bsrc = transpile(bworld, tmp)
        ok &= check("exactly 255 placed objects transpiles (the boundary)",
                    "const OBJ_COUNT: u8 = 255" in bsrc)

        # ---- Per-scene tilesets (Gap 3) -------------------------------------
        # A scene may declare its OWN tileset image ([[scene]] tileset = "..."),
        # uploaded on room load so an IMAGE title/menu scene needn't share the
        # gameplay tileset's <=255-tile budget. Additive: a world where no scene
        # sets `tileset` is byte-identical (no <SCENE>_TS symbols, paint() gains no
        # tileset upload).
        ok &= check("no per-scene tileset -> byte-identical (no _TS/_TC symbols)",
                    "_TS: array" not in src and "_TC: u8" not in src
                    and "{ bkg.set_data(0, TILE_COUNT, TILESET) }" not in src)
        # title.png = a bigger 5-tile tileset (its own image); "field" overrides,
        # "cave" keeps the shared 2-tile [tileset].
        write_png_indexed(os.path.join(tmp, "title.png"), 8, 40,
                          [0] * 64 + [3] * 64 + [1] * 64 + [2] * 64 + [3] * 64,
                          [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)])
        tsworld = dict(WORLD, door=[])
        tsworld["scene"] = [dict(WORLD["scene"][0], tileset="title.png"),
                            dict(WORLD["scene"][1])]
        tssrc = transpile(tsworld, tmp)
        ok &= check("override scene emits its own FIELD_TS/FIELD_TC (5 tiles)",
                    "const FIELD_TC: u8 = 5" in tssrc
                    and "const FIELD_TS: array[u8, 80]" in tssrc
                    and "CAVE_TS" not in tssrc)     # the shared scene has no own array
        ok &= check("paint() uploads the resolved tileset per scene",
                    "if scene == 0 { bkg.set_data(0, FIELD_TC, FIELD_TS) }" in tssrc
                    and "if scene == 1 { bkg.set_data(0, TILE_COUNT, TILESET) }" in tssrc)
        # Two scenes drawing the SAME tileset share ONE array: the second names
        # the first's FIELD_TS/FIELD_TC instead of paying the art again (a
        # title and its first stage on one PNG cost 512 B of a full scenes
        # bank). Identity is by tile DATA, so a copy under another name shares.
        shutil.copyfile(os.path.join(tmp, "title.png"), os.path.join(tmp, "copy.png"))
        dupworld = dict(tsworld)
        dupworld["scene"] = [dict(WORLD["scene"][0], tileset="title.png"),
                             dict(WORLD["scene"][1], tileset="copy.png")]
        dupsrc = transpile(dupworld, tmp)
        ok &= check("a repeated per-scene tileset is emitted once and shared",
                    dupsrc.count("_TS: array[u8, 80]") == 1
                    and "CAVE_TS" not in dupsrc and "CAVE_TC" not in dupsrc
                    and "if scene == 1 { bkg.set_data(0, FIELD_TC, FIELD_TS) }"
                    in dupsrc)
        # [world] stream keeps the per-scene CHAIN but routes each tileset
        # through the residency seam (assets.use + assets.ptr - the Lynx loads
        # it from the cart at room load, the banking consoles bank it; a
        # many-scene world's tileset art would otherwise dominate the resident
        # image: measured -11,153 B on the 17-scene reference-engine sample).
        ssrc = transpile(dict(tsworld, world=dict(WORLD["world"], stream=True)), tmp)
        spaint = ssrc.split("function paint", 1)[1]
        ok &= check("per-scene tileset + stream: tileset streams, chain kept",
                    "assets.use(FIELD_TS) bkg.set_data(0, FIELD_TC, "
                    "assets.ptr(FIELD_TS))" in spaint
                    and "bkg.set_data(0, TILE_COUNT, TILESET)" in spaint)

        # paint_table TABLE-DRIVES the tileset upload too: the distinct tilesets
        # concatenate into ONE TILESETS array + per-scene TS_OFF/TS_TC, so
        # paint() uploads in O(1) and the `if scene ==` chain disappears (the
        # chain cost ~590 B of CODE per scene on the reference-engine import). TS_TC 0
        # means "use the shared TILESET". TS_MAX_TC is what lets the Lynx budget
        # pass bound the bkg tile table through the indexed upload.
        for mode, extra in (("paint_table alone", {}),
                            ("paint_table + stream", {"stream": True})):
            w = dict(WORLD["world"], paint_table=True, **extra)
            csrc = transpile(dict(tsworld, world=w), tmp)
            paint = csrc.split("function paint", 1)[1]
            streamed = bool(extra)
            ok &= check("per-scene tileset + %s: O(1) table-driven upload" % mode,
                        "const TILESETS:" in csrc
                        and "const TS_MAX_TC: u8 = 5" in csrc
                        and "TS_OFF" in csrc and "TS_TC" in csrc
                        and "var tsc: u8 = TS_TC[scene]" in paint
                        and "bkg.set_data(0, tsc, assets.ptr_range(TILESETS, "
                            "tso, tsn))" in paint
                        # the shared-tileset scenes keep ONE branch, not a chain
                        and "bkg.set_data(0, TILE_COUNT, TILESET)" in paint
                        and "if scene == 0 { bkg.set_data" not in paint
                        and "FIELD_TS" not in csrc
                        # streaming warms the room's tileset window; resident
                        # lowers the same seam to a plain (SYM + off)
                        and (("assets.range_base(TILESETS," in paint
                              and "assets.use_range(TILESETS, tso, tsn)" in paint)
                             == streamed))
        # pack_tiles / [[animated_tile]] REMAP / ANIMATE the one shared tileset, which a
        # per-scene tileset would corrupt -> still a clear error.
        for mode in ("pack_tiles",):
            try:
                transpile(dict(tsworld, world=dict(WORLD["world"], **{mode: True})), tmp)
                ok &= check("per-scene tileset + %s should raise" % mode, False)
            except SceneError as e:
                ok &= check("per-scene tileset + %s raises a clear error" % mode,
                            "per-scene" in str(e) and mode in str(e))

    # ---- WIDE world + stream + paint_table COMPOSE (via warm) ----------------
    # A scene wider than the 32-tile hardware background is column-streamed
    # through engine.scroll, reading arbitrary cells via map_tile; it never
    # calls paint(), which is what warms the paint_table range window. That
    # used to be a hard refusal -- the pair would have read an always-cold
    # window (an empty level).
    # It now composes: the transpiler emits `warm(scene)` (the per-scene
    # tileset + the range_base/use_range pair, WITHOUT the map upload that
    # would overrun the tilemap) and generate_rooms' wide arm calls it where
    # the narrow arm calls paint(). This matters on the GB because dropping
    # paint_table puts the per-scene map_tile/paint chains back in the RESIDENT
    # image -- they are seam-reading, so they cannot be banked (a SWITCH_ROM
    # cannot execute from a switchable bank). Dedicated test:
    # wide_paint_table_test.py.
    with tempfile.TemporaryDirectory() as tmp:
        make_tileset_png(os.path.join(tmp, "tiles.png"))
        wide_scene = dict(WORLD["scene"][0])
        rows = len(wide_scene["map"])
        wide_scene["map"] = [[0] * 40 for _ in range(rows)]
        wide_scene["map_w"] = 40
        wide_scene["map_h"] = rows
        wide = dict(WORLD, scene=[wide_scene] + list(WORLD["scene"][1:]))
        for flags, wants_warm in (({"stream": True, "paint_table": True}, True),
                                  ({"stream": True}, False),
                                  ({"paint_table": True}, False)):
            names = "+".join(sorted(flags))
            src = transpile(dict(wide, world=dict(WORLD["world"], **flags)), tmp)
            ok &= check("wide world + %s builds" % names, bool(src))
            # warm() exists for EXACTLY the combination that needs it, so a
            # wide world on either flag alone stays byte-identical.
            ok &= check("wide world + %s %s warm()"
                        % (names, "emits" if wants_warm else "does NOT emit"),
                        ("function warm(" in src) == wants_warm)
        # A world INSIDE the 32-tile background still combines them (bigworld-paint).
        narrow = transpile(dict(WORLD, world=dict(WORLD["world"], stream=True,
                                                  paint_table=True)), tmp)
        ok &= check("narrow world still combines stream + paint_table",
                    "assets.range_byte(" in narrow)

    # ---- Per-console CONTENT FILTERING (review 2.1, the `platforms` allow-list) --
    # A placed entity may carry `platforms = [ids]`: the entity tables fork into
    # `if platform` guards per target BUCKET, so a ROM built for a non-listed console
    # drops it (zero runtime bytes). Untagged worlds stay byte-identical (above).
    with tempfile.TemporaryDirectory() as tmp:
        make_tileset_png(os.path.join(tmp, "tiles.png"))

        # Tag the chest (flatten index 1) gameboy-only: gameboy keeps 3 objects,
        # every other console keeps 2.
        import copy
        w = copy.deepcopy(WORLD)
        w["scene"][0]["object"][1]["platforms"] = ["gameboy"]
        fsrc = transpile(w, tmp)
        ok &= check("a `platforms` tag forks the OBJ table into an if-platform guard",
                    'if platform == "gameboy" {' in fsrc and "} else {" in fsrc
                    and "const OBJ_COUNT: u8 = 3" in fsrc
                    and "const OBJ_COUNT: u8 = 2" in fsrc)
        ok &= check("an UNtagged world grows no guard (byte-identical rule)",
                    "if platform ==" not in transpile(WORLD, tmp))
        for platform in ("gameboy", "sms", "lynx", "nes"):
            out = MosaikCompiler().compile_program(
                [("main.mos", GAME), ("scenes.mos", fsrc)], platform=platform)
            ok &= check("[%s] guarded scenes module + game compile" % platform,
                        not out.startswith("Compilation error:"))

        # VM slot re-indexing: two NPCs each with an On Interact script; the FIRST
        # is gameboy-only, so on other consoles the surviving NPC (was index 1)
        # re-indexes to 0 and its selector must say `if i == 0 -> talk_b`.
        vw = {
            "world": {"module": "scenes", "map_w": 4, "map_h": 4, "vm": True},
            "tileset": {"png": "tiles.png"},
            "kinds": {"npc": 0},
            "scene": [{"name": "field",
                       "map": [1, 1, 1, 1, 1, 0, 0, 1, 1, 0, 0, 1, 1, 1, 1, 1],
                       "object": [
                           {"kind": "npc", "x": 8, "y": 8, "on_interact": "talk_a",
                            "platforms": ["gameboy"]},
                           {"kind": "npc", "x": 24, "y": 8, "on_interact": "talk_b"}]}],
        }
        vsrc = transpile(vw, tmp)
        gb_branch = vsrc.split('if platform == "gameboy"', 1)[1].split("} else {")[0]
        else_branch = vsrc.split("} else {", 1)[1]
        ok &= check("gameboy branch keeps talk_a at OBJ index 0",
                    "if i == 0 {\n            return scripts.ENTRY_talk_a" in gb_branch)
        ok &= check("other consoles RE-INDEX the surviving NPC's selector to 0",
                    "if i == 0 {\n            return scripts.ENTRY_talk_b" in else_branch
                    and "talk_a" not in else_branch)

        # ---- Whole-SCENE filtering (Stage 2): a `[[scene]] platforms` drops the room
        # (map + entities) per console via a STUB scene; SCENE_COUNT + exports stay
        # uniform, doors still resolve (no renumber), untagged stays byte-identical.
        sw = copy.deepcopy(WORLD)
        sw["scene"][1]["platforms"] = ["gameboy"]      # 'cave' is gameboy-only
        ssrc = transpile(sw, tmp)
        ok &= check("a `[[scene]] platforms` tag forks the module body per console",
                    'if platform == "gameboy" {' in ssrc and "\n    } else {\n" in ssrc)
        ok &= check("SCENE_COUNT stays uniform across branches (stub keeps the slot)",
                    ssrc.count("const SCENE_COUNT: u8 = 2") == 2)
        cave_sizes = re.findall(r"const CAVE_MAP: array\[u8, (\d+)\]", ssrc)
        ok &= check("the excluded scene's map drops to a 1-tile stub in the else",
                    sorted(cave_sizes) == ["1", "16"])
        ok &= check("an untagged world grows no scene guard (byte-identical)",
                    "if platform ==" not in transpile(WORLD, tmp))
        for platform in ("gameboy", "sms", "lynx", "nes"):
            out = MosaikCompiler().compile_program(
                [("main.mos", GAME), ("scenes.mos", ssrc)], platform=platform)
            ok &= check("[%s] scene-forked module + game compile" % platform,
                        not out.startswith("Compilation error:"))
        # stream / paint_table combos are refused with a clear error (not yet supported).
        try:
            transpile(dict(sw, world=dict(WORLD["world"], stream=True)), tmp)
            ok &= check("scene filter + [world] stream should raise", False)
        except SceneError as e:
            ok &= check("scene filter + [world] stream raises a clear error",
                        "per-scene" in str(e) and "stream" in str(e))

    ok &= test_metatiles()

    print("=" * 50)
    print("All scene-transpiler checks passed" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


def test_metatiles():
    """Item 32 -- `[world] metatiles`: every 2x2 tile block is deduplicated into
    a shared table and each map shrinks to a quarter. The contract is that this
    is invisible to callers: `map_tile(scene, idx)` and `paint(scene)` keep
    their signatures and `map_tile` returns exactly what the uncompressed build
    would, so rooms.mos / vm.player / engine.scroll need no change.
    """
    import random
    print("\n[metatiles: 2x2 map compression (item 32)]")
    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        make_tileset_png(os.path.join(tmp, "tiles.png"))

        # OFF is byte-identical: the additive rule every world feature follows.
        plain = transpile(WORLD, tmp)
        again = transpile(copy.deepcopy(WORLD), tmp)
        ok &= check("metatiles off -> byte-identical (additive opt-in)", plain == again)
        ok &= check("metatiles off -> no metatile symbols",
                    "METATILE" not in plain and "map_meta" not in plain)

        # Random maps at EVEN and ODD dimensions (odd exercises the padded edge).
        random.seed(3)
        for w, h in ((4, 4), (7, 5)):
            m = [[random.randrange(2) for _ in range(w)] for _ in range(h)]
            world = {"world": {"module": "scenes", "map_w": w, "map_h": h,
                               "metatiles": True},
                     "tileset": {"png": "tiles.png"},
                     "scene": [{"name": "a", "map": m}]}
            src = transpile(world, tmp)

            def arr(name):
                mm = re.search(re.escape(name) + r": array\[u8, \d+\] = \[(.*?)\]",
                               src, re.S)
                return [int(v) for v in mm.group(1).replace("\n", " ").split(",")
                        if v.strip()]

            tab, mp = arr("METATILES"), arr("A_MAP")
            mw = (w + 1) // 2
            # Reconstruct every cell exactly the way the emitted map_tile does.
            bad = sum(1 for y in range(h) for x in range(w)
                      if tab[mp[(y // 2) * mw + (x // 2)] * 4
                             + (y % 2) * 2 + (x % 2)] != m[y][x])
            ok &= check("%dx%d: every tile round-trips through the metatile table" % (w, h),
                        bad == 0)
            ok &= check("%dx%d: map shrinks to a quarter (%d -> %d cells)"
                        % (w, h, w * h, len(mp)),
                        len(mp) == mw * ((h + 1) // 2))
            ok &= check("%dx%d: keeps the caller-facing surface" % (w, h),
                        "function map_tile(scene: u8, idx: u16)" in src
                        and "function paint(scene: u8)" in src)
            out = MosaikCompiler().compile_program([("scenes.mos", src)],
                                                   platform="gameboy")
            ok &= check("%dx%d: the metatiled module compiles" % (w, h),
                        not out.startswith("Compilation error:"))

        # A world that repeats one 2x2 block everywhere collapses to ONE metatile.
        flat = {"world": {"module": "scenes", "map_w": 8, "map_h": 8,
                          "metatiles": True},
                "tileset": {"png": "tiles.png"},
                "scene": [{"name": "a", "map": [[0] * 8 for _ in range(8)]}]}
        src = transpile(flat, tmp)
        ok &= check("a uniform world dedups to a single metatile",
                    "const METATILE_COUNT: u8 = 1" in src)

        # -- Collision as a METATILE ATTRIBUTE (the biggest single data cut) --
        # Auto-detected: when no 2x2 block mixes cell types the collision type is
        # a property of the metatile, so one byte per metatile replaces every
        # per-scene collision array. An 8x8-fine world must keep the arrays.
        W = H = 8
        tiles = [[random.randrange(2) for _ in range(W)] for _ in range(H)]

        def world_with(col):
            return {"world": {"module": "scenes", "map_w": W, "map_h": H,
                              "metatiles": True},
                    "tileset": {"png": "tiles.png"},
                    "scene": [{"name": "a", "map": tiles, "collision": col}]}

        # 16x16-aligned: 2-tile-thick walls (the imported/organic-map shape).
        aligned = [[1 if (y // 2 in (0, H // 2 - 1) or x // 2 in (0, W // 2 - 1))
                    else 0 for x in range(W)] for y in range(H)]
        src = transpile(world_with(aligned), tmp)
        ok &= check("aligned collision -> emits the METATILE_COLLIDE attribute",
                    "const METATILE_COLLIDE" in src)
        ok &= check("aligned collision -> the per-cell array is GONE (the cut)",
                    "A_COLLISION" not in src)

        def arr2(name):
            mm = re.search(re.escape(name) + r": array\[u8, \d+\] = \[(.*?)\]",
                           src, re.S)
            return [int(v) for v in mm.group(1).replace("\n", " ").split(",")
                    if v.strip()]

        att, mp = arr2("METATILE_COLLIDE"), arr2("A_MAP")
        mw = (W + 1) // 2
        bad = sum(1 for y in range(H) for x in range(W)
                  if att[mp[(y // 2) * mw + (x // 2)]] != aligned[y][x])
        ok &= check("aligned collision -> every cell still answers correctly",
                    bad == 0)
        ok &= check("aligned collision -> collision_at keeps its signature",
                    "function collision_at(scene: u8, idx: u16)" in src)
        out = MosaikCompiler().compile_program([("scenes.mos", src)],
                                               platform="gameboy")
        ok &= check("aligned collision -> the module compiles",
                    not out.startswith("Compilation error:"))

        # Break ONE block (a 1-tile-thick wall is the everyday cause) -> fall back.
        fine = [r[:] for r in aligned]
        fine[0][1] = 1 - fine[0][1]
        src2 = transpile(world_with(fine), tmp)
        ok &= check("8x8-fine collision -> falls back to the per-cell array",
                    "A_COLLISION" in src2 and "METATILE_COLLIDE" not in src2)
        ok &= check("8x8-fine collision -> says WHY the attribute was skipped",
                    "could NOT ride the metatile" in src2)

        # The same tiles colliding two ways must stay DISTINCT metatiles, or the
        # attribute would be ambiguous (collision joins the dedup key).
        flat_tiles = [[0] * W for _ in range(H)]
        half = [[1 if y < H // 2 else 0 for x in range(W)] for y in range(H)]
        src3 = transpile({"world": {"module": "scenes", "map_w": W, "map_h": H,
                                    "metatiles": True},
                          "tileset": {"png": "tiles.png"},
                          "scene": [{"name": "a", "map": flat_tiles,
                                     "collision": half}]}, tmp)
        ok &= check("identical tiles with different collision stay 2 metatiles",
                    "const METATILE_COUNT: u8 = 2" in src3)

        # -- COMPOSES with the residency features (the item-32 completion) --
        # metatiles changes what a map CONTAINS; stream / paint_table change how
        # it is READ. All of that meets in the one map_meta selector, so every
        # combination must reconstruct the same tiles and compile on a banking
        # console (GB) and the cart-streaming one (Lynx).
        combo_tiles = [[random.randrange(2) for _ in range(W)] for _ in range(H)]
        for pt in (False, True):
            for st in (False, True):
                if not (pt or st):
                    continue                     # plain mode covered above
                wname = ("+paint_table" if pt else "") + ("+stream" if st else "")
                world = {"world": {"module": "scenes", "map_w": W, "map_h": H,
                                   "metatiles": True},
                         "tileset": {"png": "tiles.png"},
                         "scene": [{"name": "a", "map": combo_tiles,
                                    "collision": fine}]}   # per-cell fallback path
                if pt:
                    world["world"]["paint_table"] = True
                if st:
                    world["world"]["stream"] = True
                src = transpile(world, tmp)

                def carr(name):
                    mm = re.search(re.escape(name) + r": array\[\w+, \d+\] = \[(.*?)\]",
                                   src, re.S)
                    return None if not mm else [
                        int(v) for v in mm.group(1).replace("\n", " ").split(",")
                        if v.strip()]

                tab = carr("METATILES")
                if pt:
                    mm, off = carr("MAPS"), carr("MAP_OFF")
                    get = lambda mi: mm[off[0] + mi]     # noqa: E731
                else:
                    m0 = carr("A_MAP")
                    get = lambda mi: m0[mi]              # noqa: E731
                mw = (W + 1) // 2
                bad = sum(1 for y in range(H) for x in range(W)
                          if tab[get((y // 2) * mw + (x // 2)) * 4
                                 + (y % 2) * 2 + (x % 2)] != combo_tiles[y][x])
                ok &= check("metatiles%s: tiles round-trip" % wname, bad == 0)
                if pt:
                    # per-cell collision can no longer share the (compressed)
                    # MAP_OFF -- it must carry its OWN full-cell offset table.
                    cc, coff = carr("COLLISION"), carr("COL_OFF")
                    cbad = sum(1 for y in range(H) for x in range(W)
                               if cc[coff[0] + y * W + x] != fine[y][x])
                    ok &= check("metatiles%s: collision reads through COL_OFF"
                                % wname, cbad == 0)
                if st:
                    ok &= check("metatiles%s: paint warms the residency seam"
                                % wname,
                                ("assets.use_range(MAPS" in src) if pt
                                else ("assets.use(A_MAP)" in src))
                for platform in ("gameboy", "lynx"):
                    out = MosaikCompiler().compile_program(
                        [("scenes.mos", src)], platform=platform)
                    ok &= check("metatiles%s: compiles on %s" % (wname, platform),
                                not out.startswith("Compilation error:"))
    return ok


if __name__ == "__main__":
    sys.exit(main())
