#!/usr/bin/env python3
"""Generate vm-pointnclick -- the POINT-AND-CLICK scene type (W7j).

One screen-sized room whose scene_type is `pointnclick`, so `vm.player`'s
cursor handler owns the player: eight directions with a NORMALISED diagonal, no
wall collision at all, clamped to the map, and a hover pose that swaps by
whether the cursor is over something scripted. The reference VM's `src/states/pointnclick.c`
is the reference, all 111 lines of it.

Everything the sample exists to prove is readable from OAM alone (the
vm-camprops idiom), because the room is exactly one screen and the camera
therefore never moves:

  * the cursor walks THROUGH the solid wall at tile column 6 - a point-and-click
    cursor floats over the scenery, and the reference consults neither the
    collision map nor the actor boxes in this state;
  * a DIAGONAL covers about three quarters of a cardinal step per axis
    (the reference's 90/128 sine term; ours is an exact 3/4 in quarter pixels);
  * the cursor CLAMPS its box to the room on all four sides;
  * a trigger does NOT fire on contact - the reference calls
    `trigger_at_intersection`, a query, never `trigger_activate_at_intersection`;
  * the drawn cell swaps to the HOVER one over a scripted trigger OR a scripted
    actor (ANIM_CURSOR / ANIM_CURSOR_HOVER, which reach our clip tables as
    facing 0 and facing 3);
  * A over a trigger runs its ENTER script, A over an actor runs that actor's
    On Interact - by OVERLAP, never by a facing probe.

The three parking buttons exist so the checks never depend on how far a walk
got: START parks the cursor at home, B parks it ON the hotspot rect and SELECT
parks it ON the shopkeeper. The two walking checks are then the only ones that
move, and they measure a RATIO rather than an absolute.

Regenerate:

  python projects/vm-pointnclick/assets/gen.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PROJ))
sys.path.insert(0, ROOT)

import toml  # noqa: E402
import mosaik_vm as mv  # noqa: E402

W, H = 20, 18               # exactly one GB screen, so the camera never moves

#: The player's home, and the two parking spots. World pixels, and they are the
#: BOX origin (which is what px/py are).
HOME = (16, 24)
#: The hotspot rect, in TILES (x0, y0, w, h) - and where B parks the cursor so
#: it overlaps it.
HOT_TILES = (14, 15, 2, 2)
HOT_PARK = (112, 120)
#: The shopkeeper, and where SELECT parks the cursor on top of it.
NPC = (40, 120)
#: Where each script teleports the cursor, so a check can tell WHICH one ran
#: from the cursor's position alone.
HOT_LANDING = (8, 8)
NPC_LANDING = (144, 8)

#: The solid column the cursor floats through (tile x). A topdown handler would
#: stop dead on it, which is exactly what makes it a check.
WALL_COL = 6

# ---------------------------------------------------------------------------
# Art. Index 0 transparent, 1 light, 2 dark, 3 ink.
# ---------------------------------------------------------------------------
CURSOR = [                      # ANIM_CURSOR: a hollow arrow
    "3.......",
    "33......",
    "323.....",
    "3223....",
    "32223...",
    "3333333.",
    "...33...",
    "....33..",
]
HOVER = [                       # ANIM_CURSOR_HOVER: the same arrow, filled
    "3.......",
    "33......",
    "333.....",
    "3333....",
    "33333...",
    "3333333.",
    "...33...",
    "....33..",
]
SHOPKEEP = [
    "..3333..",
    ".311113.",
    "3122213.",
    "3122213.",
    "3111113.",
    ".31113..",
    "..333...",
    "..3.3...",
]
SPRITE_TILES = [CURSOR, HOVER, SHOPKEEP]
SPRITE_NAMES = ["cur_idle", "cur_hover", "shopkeep"]
SPRITE_PALETTE = [(255, 0, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]

SHELL = '''\
-- vm-pointnclick -- the POINT-AND-CLICK scene type (W7j). One screen-sized
-- room, a cursor for a player, a hotspot rect and a shopkeeper. The handler is
-- picked by the generated rooms.mos from the scene's own `scene_type`, so this
-- shell is the plain VM8 starter and knows nothing about it.
-- Regenerate with projects/vm-pointnclick/assets/gen.py.

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

# The authored scripts. Nothing here knows about the scene type either: a
# hotspot is an ordinary trigger with an on-enter script, and the shopkeeper an
# ordinary actor with an On Interact - the scene type is what decides that
# neither of them fires on CONTACT.
SCRIPTS = {"script": [
    {"name": "main", "events": [
        {"event": "input_attach", "button": "start", "script": "park_home"},
        {"event": "input_attach", "button": "b", "script": "park_hotspot"},
        {"event": "input_attach", "button": "select", "script": "park_npc"},
        {"event": "stop"},
    ]},
    # The three parking scripts. A check that has to WALK somewhere measures
    # how far the walk got as well as what it landed on; these separate the
    # two questions.
    {"name": "park_home", "events": [
        {"event": "player_setpos", "x": HOME[0], "y": HOME[1]},
        {"event": "stop"},
    ]},
    {"name": "park_hotspot", "events": [
        {"event": "player_setpos", "x": HOT_PARK[0], "y": HOT_PARK[1]},
        {"event": "stop"},
    ]},
    {"name": "park_npc", "events": [
        {"event": "player_setpos", "x": NPC[0], "y": NPC[1]},
        {"event": "stop"},
    ]},
    # The hotspot's ENTER script. In a topdown room this fires the moment the
    # box touches the rect; here it fires only on A, and its whole job is to
    # move the cursor somewhere no walk could have put it.
    {"name": "hotspot", "events": [
        {"event": "player_setpos", "x": HOT_LANDING[0], "y": HOT_LANDING[1]},
        {"event": "stop"},
    ]},
    # ...and the shopkeeper's On Interact, which lands somewhere else again.
    {"name": "talk", "events": [
        {"event": "player_setpos", "x": NPC_LANDING[0], "y": NPC_LANDING[1]},
        {"event": "stop"},
    ]},
]}

STUDIO = '''\
# vm-pointnclick -- GENERATED by assets/gen.py.

[audio]
music = "off"

[player]
# 2 px per VM frame, so a diagonal's three quarters is a whole pixel most
# frames and the ratio the check measures is not quantisation noise.
walk = 2
width = 8
height = 8

[kind_sprites]
shopkeep = "shopkeep"

# THE CURSOR'S TWO CELLS, and they are FACINGS rather than states. The reference engine's
# ANIM_CURSOR and ANIM_CURSOR_HOVER are engine animations 0 and 1, and
# `animationMapBySpriteType` + `toEngineOrder` put authored animation 0 at
# engine slot 0 and authored animation 1 at engine slot 1 - which for our clip
# tables is facing DOWN and facing RIGHT. vm.canim pins the clip STATE at idle
# in a point-and-click room (the reference never sets a moving animation
# there), so `idle` is the only state this kind needs and `up` / `left` fall
# back to `down` the way an unauthored facing always does.
[animations.player.idle]
period = 255
down = [ "cur_idle",]
right = [ "cur_hover",]

[animations.shopkeep.idle]
period = 255
frames = [ "shopkeep",]

# An OVERLAP test needs a box: without one vm.entity falls back to its native
# chebyshev RADIUS, which answers "near" where the reference answers
# "overlapping".
[hitbox.shopkeep]
w = 8
h = 8
'''


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def rows(g):
    return "[" + ", ".join("[" + ",".join(str(v) for v in r) + "]" for r in g) + "]"


def room():
    """The map + collision. Tile 0 floor, 1 wall, 2 the hotspot's marked cells.

    The WALL is a full column of SOLID collision the cursor has to float
    through, and the hotspot is painted so the room reads as authored rather
    than as an invisible rectangle."""
    hx, hy, hw, hh = HOT_TILES
    tiles, coll = [], []
    for y in range(H):
        trow, crow = [], []
        for x in range(W):
            if x == WALL_COL:
                trow.append(1)
                crow.append(1)
            elif hx <= x < hx + hw and hy <= y < hy + hh:
                trow.append(2)
                crow.append(0)
            else:
                trow.append(0)
                crow.append(0)
        tiles.append(trow)
        coll.append(crow)
    return tiles, coll


def main():
    from mosaik_assets import write_png_indexed, write_sprite_manifest

    srows = [[int(tile[y][x].replace(".", "0"))
              for tile in SPRITE_TILES for x in range(8)]
             for y in range(8)]
    png = os.path.join(PROJ, "assets", "sprites.png")
    write_png_indexed(png, 8 * len(SPRITE_TILES), 8, srows, SPRITE_PALETTE)
    write_sprite_manifest(png, [(n, [i * 8, 0, 8, 8])
                                for i, n in enumerate(SPRITE_NAMES)])

    # Background: tile 0 white floor, tile 1 the solid wall, tile 2 the hotspot.
    bpal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    floor = [0] * 64
    wall = [2 if (x + y) % 2 == 0 else 3 for y in range(8) for x in range(8)]
    hot = [0] * 64
    for i in range(8):
        hot[i] = hot[56 + i] = hot[i * 8] = hot[i * 8 + 7] = 1
    tile_rows = []
    for cell in (floor, wall, hot):
        tile_rows += [[cell[y * 8 + x] for x in range(8)] for y in range(8)]
    write_png_indexed(os.path.join(PROJ, "assets", "tiles.png"), 8, 24,
                      tile_rows, bpal)

    t, c = room()
    hx, hy, hw, hh = HOT_TILES
    world = [
        "# vm-pointnclick -- GENERATED by assets/gen.py (the POINT-AND-CLICK "
        "scene type, W7j).",
        "[[scene]]",
        'name = "study"',
        'scene_type = "pointnclick"',
        "map = %s" % rows(t),
        "collision = %s" % rows(c),
        '[[scene.object]]\nkind = "player"\nid = 0\nx = %d\ny = %d'
        % (HOME[0], HOME[1]),
        '[[scene.object]]\nkind = "shopkeep"\nid = 1\nx = %d\ny = %d\n'
        'on_interact = "talk"' % (NPC[0], NPC[1]),
        "",
        '[[trigger]]\nfrom = "study"\ntx = %d\nty = %d\ntw = %d\nth = %d\n'
        'on_enter = "hotspot"\nid = 2' % (hx, hy, hw, hh),
        "",
        '[world]\nmodule = "scenes"\nmap_w = %d\nmap_h = %d\nvm = true'
        '\nnext_object_id = 3' % (W, H),
        '[tileset]\npng = "assets/tiles.png"',
        '[kinds]\nplayer = 0\nshopkeep = 1',
    ]
    _write(os.path.join(PROJ, "world.toml"), "\n".join(world) + "\n")
    _write(os.path.join(PROJ, "studio.toml"), STUDIO)
    _write(os.path.join(PROJ, "scripts", "main.evt.toml"), toml.dumps(SCRIPTS))
    _write(os.path.join(PROJ, "src", "main.mos"), SHELL)

    import mosaik_scenes
    wobj, base = mosaik_scenes.load_world(os.path.join(PROJ, "world.toml"))
    _write(os.path.join(PROJ, "src", "scenes.mos"),
           mosaik_scenes.transpile(wobj, base))
    prog = mv.compile_path(os.path.join(PROJ, "scripts"))
    _write(os.path.join(PROJ, "src", "scripts.mos"), prog.to_scripts_mos())
    # The CURSOR'S TWO CELLS live here: `[animations.player.idle]` becomes the
    # clips module vm.canim reads, and without it the kind would draw its
    # static `[kind_sprites]` cell and the hover pose would have nowhere to go.
    mv.generate_clips(PROJ, os.path.join(PROJ, "src", "clips.mos"))
    mv.generate_glue(PROJ)
    mv.generate_rooms(PROJ)
    print("vm-pointnclick regenerated at", PROJ)


if __name__ == "__main__":
    main()
