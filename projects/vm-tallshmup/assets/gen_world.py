#!/usr/bin/env python3
"""Generate vm-tallshmup -- a VERTICAL shmup stage TALLER than the hardware
background: one 20 x 120-tile map (960 px) that auto-scrolls UP from its bottom
to its top, its ROWS streamed by engine.scroll2d under the shmup's own camera
(`player.setup_tall_shmup`, chosen per room by generate_rooms for a shmup room
past 32 rows).

What it shows, and what verify.py reads back:

  * the stage    -> marker rows (a solid band at rows 100, 60 and 20, a checker
                    finish line at rows 0-1) that can only reach the tilemap if
                    the streamer wrote them as the camera crossed them
  * placed foes  -> 24 stationary 16x16 BEACONS placed in the Scene editor at
                    their world positions up the stage. They are drawn against
                    the camera that is really scrolling, so each one enters
                    from the top of the screen when the camera reaches it
  * sprite slots -> 24 beacons are 96 sprite objects against a 40-object table;
                    `[build] oam_on_wake` hands each one a range as it comes on
                    screen, so every beacon is drawn
  * the end      -> the scroll stops at the top of the map (row 0), where a
                    boss would be
  * shooting     -> B fires straight up; shots live in world space too

Everything is authored DATA + event scripts over the generated modules, the
way the studio writes them.

Regenerate:

  python projects/vm-tallshmup/assets/gen_world.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PROJ))
sys.path.insert(0, ROOT)

import toml  # noqa: E402
import mosaik_vm as mv  # noqa: E402

W, H = 20, 120          # 960 px tall: past the 32-row (256 px) hardware tilemap
BAND_ROWS = (100, 60, 20)
FINISH_ROWS = (0, 1)
# (x, y) in WORLD pixels, far up the stage: each is off screen at the start.
# 24 beacons of 2x2 tiles = 96 sprite objects placed in one room against the
# Game Boy's 40-object table. Only four or five are on screen together, so
# with `[build] oam_on_wake` every one of them is drawn; the static layout
# would leave every beacon past the first eight or so without a sprite.
BEACONS = [((24, 120, 72, 136, 8)[k % 5], 40 + k * 36) for k in range(24)]
# the one at world y 480 sits ON the band at row 60 (verify's Game Gear check)
BEACONS[12] = (120, 480)
PLAYER = (76, 900)
# A DRONE at the top of the stage that shuttles across the right edge of the
# screen for ever: every crossing parks it and every return WAKES it again,
# usually onto a different sprite range - the re-wake the beacons (which each
# come on screen once) never exercise.
DRONE = (40, 96)
DRONE_SPAN = (40, 184)

SHIP = [
    "...33...",
    "...33...",
    "..3223..",
    "..3223..",
    ".322223.",
    "33222233",
    "3.1221.3",
    "..3..3..",
]
BEACON = [                      # 16 x 16: a 2x2 metasprite (4 objects)
    "3333333333333333",
    "3222222222222223",
    "3233333333333323",
    "3231111111111323",
    "3231222222221323",
    "3231233333321323",
    "3231231111321323",
    "3231231331321323",
    "3231231331321323",
    "3231231111321323",
    "3231233333321323",
    "3231222222221323",
    "3231111111111323",
    "3233333333333323",
    "3222222222222223",
    "3333333333333333",
]
BULLET = [
    "........",
    "...33...",
    "...33...",
    "...33...",
    "...33...",
    "........",
    "........",
    "........",
]
DRONE_ART = [                   # 16 x 16, its own tiles (6-9 in the sheet)
    "......3333......",
    ".....322223.....",
    "....32111123....",
    "3333321111233333",
    "3222221111222223",
    "3211111111111123",
    "3222221111222223",
    "3333321111233333",
    "....32111123....",
    "....32111123....",
    "...3221111223...",
    "..322211112223..",
    ".32233211233223.",
    ".333..3223..333.",
    "......3333......",
    "................",
]
SPRITE_TILES = [SHIP, BEACON, BULLET, DRONE_ART]
SPRITE_PALETTE = [(255, 0, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]

SHELL = '''\
-- vm-tallshmup -- a vertical shmup stage TALLER than the hardware background:
-- one 20 x 120 map that auto-scrolls up, its rows streamed by engine.scroll2d
-- under the shmup camera, with objects placed along the whole stage.
-- Regenerate with projects/vm-tallshmup/assets/gen_world.py.

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

STUDIO = '''[audio]
music = "off"

[player]
shmup_speed = 2

[kind_sprites]
player = "spr_0"
beacon = "spr_1"
drone = "spr_3"

# One-frame clips: they are what give the room its metasprite LAYOUT (the 2x2
# beacon beside the 1x1 ship), which the per-room OAM allocator works on.
[animations.player.idle]
period = 8
frames = [ "spr_0",]

[animations.beacon.idle]
period = 8
frames = [ "spr_1",]

[animations.drone.idle]
period = 8
frames = [ "spr_3",]
'''

SCRIPTS = {"script": [
    {"name": "main", "events": [{"event": "stop"}]},
    # 1 px a frame: the whole 816 px of stage passes in 816 game frames.
    {"name": "scene_init", "events": [
        {"event": "shmup_scroll", "pace": 1},
        {"event": "input_attach", "button": "b", "script": "shoot"},
        {"event": "stop"},
    ]},
    {"name": "drone_init", "events": [
        {"event": "actor_set_speed", "actor": "self", "speed": 3},
        {"event": "stop"},
    ]},
    # Out past the right edge of the screen and back, for ever.
    {"name": "drone_fly", "loop": True, "events": [
        {"event": "actor_move_to", "actor": "self", "x": DRONE_SPAN[1], "y": DRONE[1]},
        {"event": "actor_move_to", "actor": "self", "x": DRONE_SPAN[0], "y": DRONE[1]},
    ]},
    {"name": "shoot", "events": [
        {"event": "projectile", "x": "player_x() + 2", "y": "player_y() - 6",
         "vx": 0, "vy": "0 - 4", "tile": 5, "life": 40},   # ship 0, beacon 1-4
        {"event": "stop"},
    ]},
]}


def stage():
    tiles, coll = [], []
    for y in range(H):
        if y in FINISH_ROWS:
            trow = [3] * W
        elif y in BAND_ROWS:
            trow = [2] * W
        else:
            trow = [1 if (x * 7 + y * 11) % 23 == 0 else 0 for x in range(W)]
        tiles.append(trow)
        coll.append([0] * W)
    return tiles, coll


def rows(g):
    return "[" + ", ".join("[" + ",".join(str(v) for v in r) + "]" for r in g) + "]"


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    from mosaik_assets import write_png_indexed, write_sprite_manifest

    # One 48 x 16 sheet: the ship (8x8) at x 0, the beacon (16x16) at x 8,
    # the bullet (8x8) at x 24, the drone (16x16) at x 32; the empty cells
    # are transparent.
    def px(art, x, y):
        if y < len(art) and x < len(art[y]):
            return int(art[y][x].replace(".", "0"))
        return 0
    srows = [[px(SHIP, x, y) for x in range(8)]
             + [px(BEACON, x, y) for x in range(16)]
             + [px(BULLET, x, y) for x in range(8)]
             + [px(DRONE_ART, x, y) for x in range(16)]
             for y in range(16)]
    write_png_indexed(os.path.join(PROJ, "assets", "sprites.png"),
                      48, 16, srows, SPRITE_PALETTE)
    write_sprite_manifest(
        os.path.join(PROJ, "assets", "sprites.png"),
        [("spr_0", [0, 0, 8, 8]), ("spr_1", [8, 0, 16, 16]),
         ("spr_2", [24, 0, 8, 8]), ("spr_3", [32, 0, 16, 16])])

    # tile 0 white field, 1 a star, 2 a solid band, 3 a checker finish line
    bpal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    field = [[0] * 8 for _ in range(8)]
    star = [[3 if (x in (3, 4) and y in (2, 3)) else 0 for x in range(8)]
            for y in range(8)]
    band = [[2] * 8 for _ in range(8)]
    check = [[3 if (x // 2 + y // 2) % 2 == 0 else 0 for x in range(8)]
             for y in range(8)]
    write_png_indexed(os.path.join(PROJ, "assets", "tiles.png"), 8, 32,
                      field + star + band + check, bpal)

    t, c = stage()
    world = [
        "# vm-tallshmup -- GENERATED by assets/gen_world.py (a 20 x 120 vertical stage).",
        '[[scene]]', 'name = "stage"', 'scene_type = "shmup"',
        "map = %s" % rows(t), "collision = %s" % rows(c), 'on_init = "scene_init"',
        '[[scene.object]]\nkind = "player"\nid = 0\nx = %d\ny = %d' % PLAYER,
    ]
    oid = 1
    for (bx, by) in BEACONS:
        world.append('[[scene.object]]\nkind = "beacon"\nid = %d\nx = %d\ny = %d'
                     % (oid, bx, by))
        oid += 1
    world.append('[[scene.object]]\nkind = "drone"\nid = %d\nx = %d\ny = %d\n'
                 'on_init = "drone_init"\non_update = "drone_fly"' % ((oid,) + DRONE))
    oid += 1
    world += [
        '[world]\nmodule = "scenes"\nmap_w = %d\nmap_h = %d\nvm = true'
        '\nnext_object_id = %d' % (W, H, oid),
        '[tileset]\npng = "assets/tiles.png"',
        '[kinds]\nplayer = 0\nbeacon = 1\ndrone = 2',
    ]
    _write(os.path.join(PROJ, "world.toml"), "\n".join(world) + "\n")
    _write(os.path.join(PROJ, "scripts", "main.evt.toml"), toml.dumps(SCRIPTS))
    _write(os.path.join(PROJ, "src", "main.mos"), SHELL)
    _write(os.path.join(PROJ, "studio.toml"), STUDIO)

    import mosaik_scenes
    wobj, base = mosaik_scenes.load_world(os.path.join(PROJ, "world.toml"))
    _write(os.path.join(PROJ, "src", "scenes.mos"), mosaik_scenes.transpile(wobj, base))
    prog = mv.compile_path(os.path.join(PROJ, "scripts"))
    _write(os.path.join(PROJ, "src", "scripts.mos"), prog.to_scripts_mos())
    mv.generate_clips(PROJ)
    mv.generate_glue(PROJ)
    mv.generate_rooms(PROJ)
    print("vm-tallshmup regenerated at", PROJ)


if __name__ == "__main__":
    main()
