#!/usr/bin/env python3
"""Generate vm-offscreen -- actors the camera has not reached yet.

Three topdown rooms, a 24-slot actor pool and `[build] actor_deactivate`
on, so a slot that is off the visible window is PARKED (taken out of the live
list) and only the camera can bring it back. It is the subject of two engine
ROM tests, and each needs exactly one shape:

  * `gate` (room 0, the start room) is one screen with NO actors, so a
    save-state taken at boot has no animator armed. A test that compares "this
    room entered directly" against "entered via another room" rewinds to that
    state; if the start room armed anything, "directly" would already be
    contaminated and the comparison would pass on a build that leaks.
  * `yard` (room 1) is one screen with twelve blobs, ALL visible, so all
    twelve of their animators are armed. Walking from the yard into the road
    hands the road's slots 6..11 - off-window at the road's entry camera - a
    slot that the previous room armed. `vm.canim.sweep_retired` must clear
    them on the room load, or the yard's animators keep firing `apply(i)` for
    ever in the road (`tests/room_change_animator_leak_test.py`).
  * `road` (room 2) is a long corridor, 80 tiles wide. Twenty-four animated
    blobs stand in two rows along it, 24 px apart, so at the entry camera six
    are on screen and eighteen are parked to the right. Walking right scrolls
    the camera over them one after another, and `vm.actor.wake_scan` has to
    bring each back within `[build] actor_scan` frames of the camera reaching
    it (`tests/actor_wake_latency_test.py`). The parked list is LONG on
    purpose: the scan's per-frame budget is `n_plist / actor_scan`, and only a
    long list separates a sweep that keeps that budget from one that does not.

Every actor has NO script: nothing moves them, nothing locks the VM, so the
only way a slot enters or leaves the live list is the camera. That is what
makes the two contracts measurable.

All art is procedurally generated here.

Regenerate:

  python projects/vm-offscreen/assets/gen.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PROJ))
sys.path.insert(0, ROOT)

import toml  # noqa: E402
import mosaik_vm as mv  # noqa: E402

#: The yard: exactly one GB screen, so every actor in it is visible.
YARD_W, YARD_H = 20, 18
#: The road: long enough that most of the pool starts off to the right.
ROAD_W, ROAD_H = 80, 18
#: Where the player stands on entry (world px). The tests' room jump spawns the
#: player at this same point, so the entry camera is the room's left edge.
SPAWN = (40, 72)
#: The yard's twelve blobs: two rows of six, all on the one screen.
YARD_BLOBS = [(16 + 24 * k, 24) for k in range(6)] +              [(16 + 24 * k, 112) for k in range(6)]
#: The road's twenty-four blobs: 24 px apart, alternating above and below the
#: player's walking line (y = 72), so nothing ever blocks the walk.
ROAD_BLOBS = [(32 + 24 * k, 32 if k % 2 == 0 else 112) for k in range(24)]

# ---------------------------------------------------------------------------
# Art. Index 0 transparent, 1 light, 2 mid, 3 ink.
# ---------------------------------------------------------------------------
HERO = [
    "..3333..",
    ".311113.",
    "31311313",
    "31111113",
    ".322223.",
    "..3113..",
    ".31..13.",
    ".33..33.",
]
BLOB1 = [
    "........",
    "..3333..",
    ".322223.",
    "32122123",
    "32222223",
    "32211223",
    ".322223.",
    "..3333..",
]
BLOB2 = [
    "........",
    "........",
    "..3333..",
    ".321123.",
    "32222223",
    "32222223",
    "33333333",
    "........",
]
SPRITE_TILES = [HERO, BLOB1, BLOB2]
SPRITE_NAMES = ["hero", "blob1", "blob2"]
SPRITE_PALETTE = [(255, 0, 255), (224, 224, 224), (120, 120, 120), (0, 0, 0)]

SHELL = '''\
-- vm-offscreen -- actors the camera has not reached yet. Two rooms, a 24-slot
-- pool and `[build] actor_deactivate`; the room wiring (load, clips, the
-- player, the scroll) is the generated rooms.mos, so this shell is the plain
-- VM8 starter. Regenerate with projects/vm-offscreen/assets/gen.py.

module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "graphics.bkg"
    import "scenes"
    import "scripts"
    import "vm.core"
    import "rooms"
    import "glue"

    function main() {
        core.font_preload()
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        sprite.set_data(0, sprites_tile_count, sprites_tiles)

        core.boot(scripts.fetch, scripts.render_text, scripts.ENTRY_main)
        glue.setup()
        rooms.start(rooms.START)

        video.enable_lcd()
        video.show_background()
        video.show_sprites()
        core.run()
    }
    export main
}
'''

# Nothing is scripted: the room jump the tests use is the engine's own
# (vm.core's pending room change), and a script would only add a way for a slot
# to be woken or locked that is not the camera.
SCRIPTS = {"script": [
    {"name": "main", "events": [
        {"event": "stop"},
    ]},
]}

STUDIO = '''\
# vm-offscreen -- GENERATED by assets/gen.py.

[audio]
music = "off"

[player]
width = 8
height = 8

[kind_sprites]
blob = "blob1"

[animations.player.idle]
period = 255
frames = [ "hero",]

# Two frames, so every placed blob ARMS an engine.anim animator the moment it
# is first drawn - the thing a room change must not carry over.
[animations.blob.idle]
period = 12
frames = [ "blob1", "blob2",]
'''

MOSAIK = '''\
[project]
name = "vm-offscreen"
version = "0.1.0"
# Actors the camera has not reached yet: `[build] actor_deactivate` parks every
# off-window slot, and two engine ROM tests drive it (see assets/gen.py and
# Readme.md). GB family only - both tests read a GB symbol file.
target_platforms = ["gameboy", "gameboy_color"]

[source]
folder = "src/"

[assets]
sprites = ["assets/sprites.png"]

[build]
output_dir = "build"
shake_exports = true
# The whole point of the sample: 24 slots (8x8 actors, one OAM object each),
# off-window slots PARKED out of the live list, and a parked slot re-tested
# one in four frames.
actor_pool = 24
actor_deactivate = true
actor_scan = 4
'''


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def rows(g):
    return "[" + ", ".join("[" + ",".join(str(v) for v in r) + "]" for r in g) + "]"


def room(w, h):
    """Tile 0 grass, 1 the walking path along the player's row, 2 a fence
    post every eight columns along the top and bottom edges (decorative; the
    collision layer is empty, so nothing stops the walk)."""
    tiles, coll = [], []
    for y in range(h):
        trow = []
        for x in range(w):
            if y in (8, 9):
                trow.append(1)
            elif y in (0, h - 1) and x % 8 == 0:
                trow.append(2)
            else:
                trow.append(0)
        tiles.append(trow)
        coll.append([0] * w)
    return tiles, coll


def _objects(blobs, first_id):
    out = []
    for n, (x, y) in enumerate(blobs):
        out.append('[[scene.object]]\nkind = "blob"\nid = %d\nx = %d\ny = %d'
                   % (first_id + n, x, y))
    return out


def main():
    from mosaik_assets import write_png_indexed, write_sprite_manifest

    srows = [[int(tile[y][x].replace(".", "0"))
              for tile in SPRITE_TILES for x in range(8)]
             for y in range(8)]
    png = os.path.join(PROJ, "assets", "sprites.png")
    write_png_indexed(png, 8 * len(SPRITE_TILES), 8, srows, SPRITE_PALETTE)
    write_sprite_manifest(png, [(n, [i * 8, 0, 8, 8])
                                for i, n in enumerate(SPRITE_NAMES)])

    # Background: grass (sparse dots), the path (a light band with edges), and
    # a fence post, so a scrolling room visibly scrolls.
    bpal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    grass = [2 if (x * 3 + y * 5) % 11 == 0 else 0
             for y in range(8) for x in range(8)]
    path = [2 if y in (0, 7) else (1 if (x + y) % 4 == 0 else 0)
            for y in range(8) for x in range(8)]
    post = [3 if 2 <= x <= 5 else (1 if y == 3 else 0)
            for y in range(8) for x in range(8)]
    tile_rows = []
    for cell in (grass, path, post):
        tile_rows += [[cell[y * 8 + x] for x in range(8)] for y in range(8)]
    write_png_indexed(os.path.join(PROJ, "assets", "tiles.png"), 8, 24,
                      tile_rows, bpal)

    yt, yc = room(YARD_W, YARD_H)
    rt, rc = room(ROAD_W, ROAD_H)
    oid = 0
    world = ["# vm-offscreen -- GENERATED by assets/gen.py.",
             "[[scene]]", 'name = "gate"', 'scene_type = "topdown"',
             "map = %s" % rows(yt), "collision = %s" % rows(yc),
             '[[scene.object]]\nkind = "player"\nid = %d\nx = %d\ny = %d'
             % (oid, SPAWN[0], SPAWN[1]), ""]
    oid += 1
    world += ["[[scene]]", 'name = "yard"', 'scene_type = "topdown"',
             "map = %s" % rows(yt), "collision = %s" % rows(yc),
             '[[scene.object]]\nkind = "player"\nid = %d\nx = %d\ny = %d'
             % (oid, SPAWN[0], SPAWN[1])]
    oid += 1
    world += _objects(YARD_BLOBS, oid)
    oid += len(YARD_BLOBS)
    world += ["", "[[scene]]", 'name = "road"', 'scene_type = "topdown"',
              "map_w = %d" % ROAD_W, "map_h = %d" % ROAD_H,
              "map = %s" % rows(rt), "collision = %s" % rows(rc),
              '[[scene.object]]\nkind = "player"\nid = %d\nx = %d\ny = %d'
              % (oid, SPAWN[0], SPAWN[1])]
    oid += 1
    world += _objects(ROAD_BLOBS, oid)
    oid += len(ROAD_BLOBS)
    world += ["",
              '[world]\nmodule = "scenes"\nmap_w = %d\nmap_h = %d\nvm = true'
              '\nnext_object_id = %d' % (YARD_W, YARD_H, oid),
              '[tileset]\npng = "assets/tiles.png"',
              '[kinds]\nplayer = 0\nblob = 1']
    _write(os.path.join(PROJ, "world.toml"), "\n".join(world) + "\n")
    _write(os.path.join(PROJ, "studio.toml"), STUDIO)
    _write(os.path.join(PROJ, "mosaik.toml"), MOSAIK)
    _write(os.path.join(PROJ, "scripts", "main.evt.toml"), toml.dumps(SCRIPTS))
    _write(os.path.join(PROJ, "src", "main.mos"), SHELL)

    import mosaik_scenes
    wobj, base = mosaik_scenes.load_world(os.path.join(PROJ, "world.toml"))
    _write(os.path.join(PROJ, "src", "scenes.mos"),
           mosaik_scenes.transpile(wobj, base))
    prog = mv.compile_path(os.path.join(PROJ, "scripts"))
    _write(os.path.join(PROJ, "src", "scripts.mos"), prog.to_scripts_mos())
    mv.generate_clips(PROJ, os.path.join(PROJ, "src", "clips.mos"))
    mv.generate_glue(PROJ)
    mv.generate_rooms(PROJ)
    print("vm-offscreen regenerated at", PROJ)


if __name__ == "__main__":
    main()
