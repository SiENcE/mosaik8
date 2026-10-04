#!/usr/bin/env python3
"""Generate vm-shmup -- the VM8 migration of the hand-written `shmup` (starfall),
a VERTICAL shoot'em up over an ENDLESS scrolling starfield. It is the vertical
counterpart to vm-combat's horizontal shmup: the ship sits at the BOTTOM, enemies
stream DOWN from the top, B fires straight UP, and the tiled star background scrolls
FOREVER (the shmup handler's endless-loop mode, dir 3).

Everything but the native flight + the auto-scroll is authored DATA + event scripts
over generated rooms.mos / scripts.mos / glue.mos / hud.mos -- no genre.shmup kit,
no hand-written game loop:

  * the ship        -> the native shmup player (4-dir flight, SCREEN-fixed in loop
                       mode -- NOT dragged), fixed at the bottom band
  * ENDLESS bkg     -> a seamless 32-row starfield + shmup_scroll dir 3 (the loop
                       mode added for this sample): the auto-scroll WRAPS mod 256
                       (the GB tilemap period) so the stars flow down forever
  * SHOOTING        -> B fires a projectile straight UP from the ship (F1 expr coords)
  * enemy movement  -> each enemy's On Update descends it (F1 expr coords); off the
                       bottom it CONTINUOUSLY re-enters from the top at a fresh column
  * a shot KILLS    -> the enemy On Hit slot (F3) -> per-actor HP + score (F4), with a
                       little EXPLOSION (a zero-velocity, mask-0 projectile particle)
  * off-screen shots-> the engine despawns a projectile the instant it leaves the
                       screen (no more top-exit-wraps-to-the-bottom)
  * touch the ship  -> costs HP (i-frame gated); at 0 HP the run restarts IN PLACE
                       (no change_scene, so the persistent window HUD is never lost)
  * HP hearts       -> a HUD-editor GAUGE over `php` (F5) - not engine.hud

Regenerate:

  python projects/vm-shmup/assets/gen_world.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PROJ))
sys.path.insert(0, ROOT)

import toml  # noqa: E402
import mosaik_vm as mv  # noqa: E402

W, H = 20, 32       # a screen-wide, 256-px-TALL level -> the shmup handler scrolls it UP

# ----------------------------------------------------------------------------
# Sprite art (ASCII -> indexed PNG via [assets] sprites; the same pipeline the
# hand-written starfall used). Index 0 transparent, 1 light, 2 dark, 3 ink.
# ----------------------------------------------------------------------------
SHIP = [                       # points UP (narrow top): the player at the bottom
    "...33...",
    "...33...",
    "..3223..",
    "..3223..",
    ".322223.",
    "33222233",
    "3.1221.3",
    "..3..3..",
]
ENEMY = [
    "..3333..",
    ".322223.",
    "33211233",
    "33222233",
    ".333333.",
    "3.3..3.3",
    "3......3",
    ".3....3.",
]
BULLET = [
    "...33...",
    "...33...",
    "...22...",
    "...22...",
    "...11...",
    "........",
    "........",
    "........",
]
HEART = [                      # the gauge's Lynx/PCE sprite cell (sprite tile 3)
    ".33.33..",
    "3223322.",
    "32222320",
    "32222320",
    ".322232.",
    "..3223..",
    "...32...",
    "........",
]
BOOM = [                        # the hit explosion (sprite tile 4)
    "3..33..3",
    ".3.11.3.",
    "..1221..",
    "31222213",
    "31222213",
    "..1221..",
    ".3.11.3.",
    "3..33..3",
]
SPRITE_TILES = [SHIP, ENEMY, BULLET, HEART, BOOM]
SPRITE_PALETTE = [(255, 0, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]

SHELL = '''\
-- vm-shmup -- the VM8 migration of the hand-written `shmup` (starfall): a VERTICAL
-- shoot'em up over an ENDLESS scrolling starfield (the vertical counterpart to
-- vm-combat). The ship sits at the bottom, enemies descend,
-- B shoots straight up, the star background scrolls forever. Flight + the auto-scroll
-- are the native shmup handler (endless-loop mode); shooting, enemy movement, kills +
-- the HP hearts are ALL authored (event scripts + a HUD gauge) -- no genre.shmup kit,
-- no hand loop. Regenerate with projects/vm-shmup/assets/gen_world.py.

module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "graphics.bkg"
    import "graphics.palette"
    import "scenes"
    import "scripts"
    import "vm.core"
    import "rooms"
    import "glue"
    import "hud"

    -- HUD gauge tiles at id 100: a FULL heart cell + a HOLLOW one (GB-family tile strip).
    const HUD_TILES: array[u8, 32] = [
        0x66, 0xFF, 0xFF, 0xFF, 0xFF, 0x7E, 0x3C, 0x18,   -- 100: full heart
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x66, 0x99, 0x81, 0x81, 0x81, 0x42, 0x24, 0x18,   -- 101: empty heart
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
    ]

    -- WHITE background on every console. On the GB family index 0 is already the
    -- lightest shade (white) under the default palette, so this is a no-op there. The
    -- Lynx maps a bkg index-0 pixel to pen 0 (the transparent/backdrop pen, dark by
    -- default), so force pen 0 WHITE -- and, since the Lynx sprite pens default to a
    -- grey ramp whose top is white (invisible on white), darken the sprite slot so the
    -- ship / foes / bullets read on the white sky. (`if platform` folds at MODULE level
    -- only, so the palette setup is a platform-picked function, not a mid-main branch.)
    if platform == "lynx" {
        function setup_palette() {
            palette.set_bkg(0, palette.rgb(255, 255, 255), palette.rgb(120, 120, 120), palette.rgb(60, 60, 60), palette.rgb(0, 0, 0))
            palette.set_sprite(0, palette.rgb(0, 0, 0), palette.rgb(150, 40, 40), palette.rgb(70, 20, 20), palette.rgb(0, 0, 0))
        }
    } else {
        function setup_palette() { }
    }

    function main() {
        core.font_preload()
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)   -- the seamless starfield
        bkg.set_data(100, 2, HUD_TILES)
        sprite.set_data(0, sprites_tile_count, sprites_tiles)  -- ship/enemy/bullet/heart
        setup_palette()                     -- WHITE sky (a no-op on the GB family)

        core.boot(scripts.fetch, scripts.render_text, scripts.ENTRY_main)
        glue.setup()
        rooms.start(rooms.START)          -- the shmup handler + actors/projectiles
        core.set_hud(hud.update, hud.draw)
        hud.show(0)

        video.enable_lcd()
        video.show_background()
        video.show_sprites()
        core.run()
    }
    export main
}
'''

HUD = '''\
[[panel]]
name = "hp-hud"
rows = 1

[[panel.element]]
id = 1
kind = "label"
x = 1
y = 0
text = "HP"

[[panel.element]]
id = 2
kind = "gauge"
x = 4
y = 0
var = "php"
max = 6
max_var = "phpmax"
base_tile = 100
sprite_tile = 3
style = "hearts"
'''

# the authored combat logic (all event scripts, edit in the Events dock).
SCRIPTS = {"script": [
    {"name": "main", "events": [{"event": "stop"}]},

    # scene On Init: seed the fight, TURN ON the endless starfield loop (shmup_scroll
    # dir 3), arm B -> shoot, start the watcher. Runs ONCE (there is no change_scene).
    {"name": "scene_init", "events": [
        {"event": "set_var", "var": "php", "value": 6},
        {"event": "set_var", "var": "phpmax", "value": 6},
        {"event": "set_var", "var": "pihurt", "value": 0},
        {"event": "set_var", "var": "score", "value": 0},
        {"event": "set_var", "var": "spawnx", "value": 40},
        {"event": "shmup_scroll", "pace": 2, "dir": 3},   # dir 3 = endless vertical loop
        {"event": "input_attach", "button": "b", "script": "shoot"},
        {"event": "start_thread", "script": "player_tick"},
        {"event": "stop"},
    ]},

    # B: fire a bullet straight UP from the ship (F1 expr coords). vy is negative (an
    # expression, so the compiler picks the signed _E projectile op). The engine now
    # despawns it the instant it leaves the screen top (no more bottom-wrap).
    {"name": "shoot", "events": [
        {"event": "projectile", "x": "player_x() + 2", "y": "player_y() - 6",
         "vx": 0, "vy": "0 - 6", "tile": 2, "life": 60},
        {"event": "stop"},
    ]},

    # enemy On Update: DESCEND (F1 expr coords). Off the bottom it RE-ENTERS from the
    # top edge at a fresh column (a cheap LCG in `spawnx`) -- a continuous stream, never
    # a static wave. Touching the ship costs 1 HP (i-frame gated). The respawn is inlined
    # (not a sub-thread) because `self` is bound HERE, in the actor's own script.
    {"name": "enemy_fly", "loop": True, "events": [
        {"event": "if", "cond": "actor_y(self) > 146", "then": [
            {"event": "set_var", "var": "spawnx", "expr": "(spawnx * 37 + 11) % 148"},
            {"event": "actor_set_pos", "actor": "self", "x": "spawnx + 4", "y": 0},
            {"event": "actor_set_hp", "actor": "self", "hp": 3},
        ], "else": [
            {"event": "actor_set_pos", "actor": "self",
             "x": "actor_x(self)", "y": "actor_y(self) + 1"},
            {"event": "if",
             "cond": "pihurt == 0 and abs(player_x() - actor_x(self)) < 10 "
                     "and abs(player_y() - actor_y(self)) < 10",
             "then": [
                 {"event": "set_var", "var": "php", "expr": "php - 1"},
                 {"event": "set_var", "var": "pihurt", "value": 30}]},
        ]},
        {"event": "wait", "frames": 2},
    ]},

    # enemy On Init: seed the foe's per-actor HP (multi-HP -- it takes 3 hits). The
    # staggered start Ys (world.toml) desync the stream so respawns trickle in.
    {"name": "enemy_init", "events": [
        {"event": "actor_set_hp", "actor": "self", "hp": 3},
        {"event": "stop"},
    ]},

    # enemy On Hit (a bullet struck it, F3): a little EXPLOSION at the impact (a
    # zero-velocity, mask-0 projectile that hits nothing + auto-despawns), then take
    # 1 HP off; the killing blow scores + re-enters it from the top (F4 death hook,
    # inlined -- `self` is bound here). The vm.entity re-fire guard spaces the hits.
    {"name": "enemy_hit", "events": [
        {"event": "projectile", "x": "actor_x(self)", "y": "actor_y(self)",
         "vx": 0, "vy": 0, "tile": 4, "life": 10, "mask": 0},
        {"event": "actor_damage", "actor": "self", "amount": 1},
        {"event": "if", "cond": "actor_hp(self) == 0", "then": [
            {"event": "set_var", "var": "score", "expr": "score + 1"},
            {"event": "set_var", "var": "spawnx", "expr": "(spawnx * 37 + 11) % 148"},
            {"event": "actor_set_pos", "actor": "self", "x": "spawnx + 4", "y": 0},
            {"event": "actor_set_hp", "actor": "self", "hp": 3},
        ]},
        {"event": "stop"},
    ]},

    # the per-frame watcher: tick i-frames; on death (0 HP) restart the run IN PLACE
    # (refill HP + brief invuln + zero the score) -- NO change_scene, so the persistent
    # window HUD is never torn down. The endless starfield keeps scrolling across it.
    {"name": "player_tick", "loop": True, "events": [
        {"event": "if", "cond": "pihurt > 0",
         "then": [{"event": "set_var", "var": "pihurt", "expr": "pihurt - 1"}]},
        {"event": "if", "cond": "php <= 0", "then": [
            {"event": "set_var", "var": "php", "expr": "phpmax"},
            {"event": "set_var", "var": "pihurt", "value": 60},
            {"event": "set_var", "var": "score", "value": 0},
        ]},
        {"event": "idle"},
    ]},
]}


def starfield():
    """A seamless 32-row starfield: tile 0 = black field, tile 1 = a star. The star
    pattern has a VERTICAL PERIOD of 8 rows (`y % 8`), and 32 = 4*8 divides the GB
    tilemap's 256-px wrap, so the endless scroll loops with NO visible seam.
    Collision is all-zero (open sky) -- the ship flies freely, foes are actors."""
    tiles, coll = [], []
    for y in range(H):
        trow, crow = [], []
        for x in range(W):
            star = ((x * 7 + (y % 8) * 11) % 23 == 0)
            trow.append(1 if star else 0)
            crow.append(0)
        tiles.append(trow)
        coll.append(crow)
    return tiles, coll


def rows(g):
    return "[" + ", ".join("[" + ",".join(str(v) for v in r) + "]" for r in g) + "]"


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def main():
    from mosaik_assets import write_png_indexed

    # sprite sheet (ship/enemy/bullet/heart) -> assets/sprites.png ([assets] sprites).
    srows = [[int(tile[y][x].replace(".", "0"))
              for tile in SPRITE_TILES for x in range(8)]
             for y in range(8)]
    write_png_indexed(os.path.join(PROJ, "assets", "sprites.png"),
                      8 * len(SPRITE_TILES), 8, srows, SPRITE_PALETTE)
    # ... and its manifest, from the SAME tile list. The sidecar used to be
    # written by hand, and a regen that shrank the sheet left the old (bigger)
    # manifest behind - whose out-of-range rects broke the asset decode with a
    # bare IndexError. studio.toml's kind_sprites names spr_0/spr_1 from here.
    from mosaik_assets import write_sprite_manifest
    write_sprite_manifest(
        os.path.join(PROJ, "assets", "sprites.png"),
        [("spr_%d" % i, [i * 8, 0, 8, 8]) for i in range(len(SPRITE_TILES))])

    # bkg starfield tileset -> assets/tiles.png (world.toml [tileset]). Tile 0 = a WHITE
    # field (index 0 = the GB-family lightest shade; the Lynx backdrop pen 0 is forced
    # white by the shell's palette.set_bkg), tile 1 = a white cell with a dark star dot.
    bpal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    field = [0] * 64
    star = [0] * 64
    for (sx, sy) in ((3, 2), (4, 2), (3, 3), (4, 3)):
        star[sy * 8 + sx] = 3
    tile_rows = [[field[y * 8 + x] for x in range(8)]      # tile 0 rows
                 for y in range(8)]
    tile_rows += [[star[y * 8 + x] for x in range(8)]      # tile 1 rows
                  for y in range(8)]
    write_png_indexed(os.path.join(PROJ, "assets", "tiles.png"), 8, 16, tile_rows, bpal)

    t, c = starfield()
    # 4 enemies enter from the top, staggered DOWN so the stream desyncs (each
    # re-enters at the top edge on escape / death -- continuous, never a static wave).
    enemies = [(20, 0), (72, 20), (120, 40), (150, 60)]
    world = [
        "# vm-shmup -- GENERATED by assets/gen_world.py (vertical shmup, endless starfield).",
        '[[scene]]', 'name = "sky"', 'scene_type = "shmup"',
        "map = %s" % rows(t), "collision = %s" % rows(c), 'on_init = "scene_init"',
        '[[scene.object]]\nkind = "player"\nid = 0\nx = 76\ny = 120',
    ]
    oid = 1
    for (ex, ey) in enemies:
        world.append('[[scene.object]]\nkind = "enemy"\nid = %d\nx = %d\ny = %d\n'
                     'on_init = "enemy_init"\non_update = "enemy_fly"\n'
                     'on_hit = "enemy_hit"' % (oid, ex, ey))
        oid += 1
    world += [
        "",
        '[world]\nmodule = "scenes"\nmap_w = %d\nmap_h = %d\nvm = true'
        '\nnext_object_id = %d' % (W, H, oid),
        '[tileset]\npng = "assets/tiles.png"',
        '[kinds]\nplayer = 0\nenemy = 1',
    ]
    _write(os.path.join(PROJ, "world.toml"), "\n".join(world) + "\n")
    _write(os.path.join(PROJ, "scripts", "main.evt.toml"), toml.dumps(SCRIPTS))
    _write(os.path.join(PROJ, "scripts", "hud.toml"), HUD)
    _write(os.path.join(PROJ, "src", "main.mos"), SHELL)

    import mosaik_scenes
    wobj, base = mosaik_scenes.load_world(os.path.join(PROJ, "world.toml"))
    _write(os.path.join(PROJ, "src", "scenes.mos"), mosaik_scenes.transpile(wobj, base))
    prog = mv.compile_path(os.path.join(PROJ, "scripts"))
    _write(os.path.join(PROJ, "src", "scripts.mos"), prog.to_scripts_mos())
    mv.generate_glue(PROJ)
    mv.generate_rooms(PROJ)
    mv.generate_hud(PROJ)
    print("vm-shmup (vertical endless shmup) regenerated at", PROJ)


if __name__ == "__main__":
    main()
