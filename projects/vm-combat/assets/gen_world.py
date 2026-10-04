#!/usr/bin/env python3
"""Generate vm-combat -- a FULLY VM8-NATIVE horizontal-scrolling SHMUP (the combat
program Part 2). A shmup is the better projectile testbed: the ship flies at the left,
enemies stream in from the RIGHT, and shots fly the OPEN GAP between them (no more
point-blank despawns like the topdown arena).

The map auto-scrolls right (the native `shmup` scene handler drags the ship forward);
EVERYTHING else is authored:

  * the ship        -> the native shmup player (4-dir flight, dragged by the scroll)
  * SHOOTING        -> B fires a projectile straight RIGHT from the ship (F1/F2) -
                       the shot crosses the screen and hits an enemy at RANGE
  * enemy movement  -> each enemy's On Update flies it LEFT (F1 expr coords); a foe
                       that slips PAST the ship costs HP + is gone
  * a shot KILLS    -> the enemy On Hit slot (F3) -> deactivate self + a kill counter (F4)
  * HP hearts       -> a HUD-editor GAUGE over `php` (F5) - not engine.hud
  * clear a wave    -> reload a fresh wave (kill/escape counter)

No vm.combat, no hand-written loop. Regenerate:

  python projects/vm-combat/assets/gen_world.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PROJ))
sys.path.insert(0, ROOT)

import toml  # noqa: E402
import mosaik_vm as mv  # noqa: E402

W, H = 30, 9        # a wide level (240 px) -> the shmup handler scrolls it RIGHT

SHELL = '''\
-- vm-combat -- a FULLY VM8-NATIVE horizontal SHMUP. The ship flies at the left, enemies stream in from the right, B shoots
-- straight right across the gap. Flight is the native shmup handler; shooting, enemy
-- movement, kills + the HP hearts are ALL authored (event scripts + a HUD gauge) --
-- no vm.combat, no hand loop. Regenerate with projects/vm-combat/assets/gen_world.py.

module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "graphics.bkg"
    import "scenes"
    import "scripts"
    import "vm.core"
    import "rooms"
    import "glue"
    import "hud"

    -- sprite tiles: 0 = the ship (points right), 1 = enemy, 2 = the bullet, 3 = a HEART
    -- (the gauge's sprite cell on the Lynx/PCE, where the tile-strip gauge no-ops).
    const SPRITES: array[u8, 64] = [
        0x00, 0xC0, 0xF0, 0x7C, 0x7F, 0x7C, 0xF0, 0xC0,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,   -- 0 ship (>)
        0x00, 0x00, 0x66, 0x66, 0xFF, 0xFF, 0xDB, 0xDB,
        0xFF, 0xFF, 0x7E, 0x7E, 0x3C, 0x3C, 0x66, 0x66,   -- 1 enemy
        0x00, 0x00, 0x00, 0x18, 0x3C, 0x7E, 0x3C, 0x18,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,   -- 2 bullet
        0x66, 0x66, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
        0x7E, 0x7E, 0x3C, 0x3C, 0x18, 0x18, 0x00, 0x00    -- 3 heart
    ]

    -- HUD gauge tiles at id 100: a FULL heart cell + a HOLLOW one (GB-family tile strip).
    const HUD_TILES: array[u8, 32] = [
        0x66, 0xFF, 0xFF, 0xFF, 0xFF, 0x7E, 0x3C, 0x18,   -- 100: full heart
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x66, 0x99, 0x81, 0x81, 0x81, 0x42, 0x24, 0x18,   -- 101: empty heart
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00
    ]

    function main() {
        core.font_preload()
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        bkg.set_data(100, 2, HUD_TILES)
        sprite.set_data(0, 4, SPRITES)

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

    # scene On Init (re-runs on every reload): reset the fight, arm B -> shoot, start
    # the watcher. `foes` counts kills+escapes; clearing them reloads a fresh wave.
    {"name": "scene_init", "events": [
        {"event": "set_var", "var": "php", "value": 6},
        {"event": "set_var", "var": "phpmax", "value": 6},
        {"event": "set_var", "var": "foes", "value": 4},
        {"event": "set_var", "var": "kills", "value": 0},
        {"event": "set_var", "var": "pihurt", "value": 0},
        {"event": "input_attach", "button": "b", "script": "shoot"},
        {"event": "start_thread", "script": "player_tick"},
        {"event": "stop"},
    ]},

    # B: fire a bullet straight RIGHT from the ship (F1 expr coords). It crosses the
    # open gap and hits an enemy at range -- the whole point of the shmup layout.
    {"name": "shoot", "events": [
        {"event": "projectile", "x": "player_x() + 6", "y": "player_y()",
         "vx": 6, "vy": 0, "tile": 2, "life": 48},
        {"event": "stop"},
    ]},

    # enemy On Update: fly LEFT (F1 expr coords). Slipping PAST the ship (behind it,
    # where a rightward shot can't reach) costs 2 HP + counts as gone; touching the
    # ship costs 1 HP (i-frame gated).
    {"name": "enemy_fly", "loop": True, "events": [
        {"event": "if", "cond": "actor_x(self) + 8 < player_x()", "then": [
            {"event": "set_var", "var": "php", "expr": "php - 2"},
            {"event": "set_var", "var": "pihurt", "value": 30},
            {"event": "actor_deactivate", "actor": "self"},
            {"event": "set_var", "var": "kills", "expr": "kills + 1"},
        ], "else": [
            {"event": "actor_set_pos", "actor": "self",
             "x": "actor_x(self) - 2", "y": "actor_y(self)"},
            {"event": "if",
             "cond": "pihurt == 0 and abs(player_x() - actor_x(self)) < 10 "
                     "and abs(player_y() - actor_y(self)) < 10",
             "then": [
                 {"event": "set_var", "var": "php", "expr": "php - 1"},
                 {"event": "set_var", "var": "pihurt", "value": 30}]},
        ]},
        {"event": "wait", "frames": 2},
    ]},

    # enemy On Init: seed the foe's per-actor HP (multi-HP -- it takes 3 hits).
    {"name": "enemy_init", "events": [
        {"event": "actor_set_hp", "actor": "self", "hp": 3},
        {"event": "stop"},
    ]},

    # enemy On Hit (a bullet struck it, F3): subtract 1 from its per-actor HP; only
    # the killing blow retires it + counts the kill (F4 death hook). The vm.entity
    # re-fire guard spaces the hits so a burst can't shred it in one frame.
    {"name": "enemy_hit", "events": [
        {"event": "actor_damage", "actor": "self", "amount": 1},
        {"event": "if", "cond": "actor_hp(self) == 0", "then": [
            {"event": "actor_deactivate", "actor": "self"},
            {"event": "set_var", "var": "kills", "expr": "kills + 1"},
        ]},
        {"event": "stop"},
    ]},

    # the per-frame watcher: tick i-frames; reload on death (0 HP) or a cleared wave.
    {"name": "player_tick", "loop": True, "events": [
        {"event": "if", "cond": "pihurt > 0",
         "then": [{"event": "set_var", "var": "pihurt", "expr": "pihurt - 1"}]},
        {"event": "if", "cond": "php <= 0",
         "then": [{"event": "change_scene", "room": 0, "x": 24, "y": 32}]},
        {"event": "if", "cond": "kills >= foes",
         "then": [{"event": "change_scene", "room": 0, "x": 24, "y": 32}]},
        {"event": "idle"},
    ]},
]}


def level():
    """A scrolling corridor: solid ceiling + floor (tile 1), open middle (tile 0)
    with scattered decorative 'stars' (tile 1, non-solid) so the scroll reads."""
    tiles, coll = [], []
    for y in range(H):
        trow, crow = [], []
        for x in range(W):
            edge = (y == 0 or y == H - 1)
            star = (not edge) and ((x * 5 + y * 3) % 13 == 0)
            trow.append(1 if (edge or star) else 0)
            crow.append(1 if edge else 0)          # only ceiling/floor block
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
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    write_png_indexed(os.path.join(PROJ, "assets", "tiles.png"), 8, 16,
                      [0] * 64 + [3] * 64, pal)

    t, c = level()
    # 4 enemies stream in from the right (world x 140..224, staggered y), flying left.
    enemies = [(140, 16), (176, 40), (208, 24), (224, 48)]
    world = [
        "# vm-combat -- GENERATED by assets/gen_world.py (horizontal shmup, no vm.combat).",
        '[[scene]]', 'name = "flight"', 'scene_type = "shmup"',
        "map = %s" % rows(t), "collision = %s" % rows(c), 'on_init = "scene_init"',
        '[[scene.object]]\nkind = "player"\nid = 0\nx = 24\ny = 32',
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
    print("vm-combat (horizontal shmup) regenerated at", PROJ)


if __name__ == "__main__":
    main()
