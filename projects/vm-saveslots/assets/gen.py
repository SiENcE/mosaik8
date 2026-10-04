#!/usr/bin/env python3
"""Generate vm-saveslots -- the reference engine's THREE save slots (W7c).

`vm-save` is the single-slot sample and stays exactly as it is: it is the
byte-identical control, the project that saves and never names a slot. This one
exercises everything the slot machinery adds - writing three slots, loading one
back, clearing one, and PEEKING a variable out of a slot without loading it.

Every check reads OAM, because the room is exactly one GB screen (so the camera
cannot move) and the player is TELEPORTED to `8 + mark * 8` whenever the game
wants to show the value of `mark`. That is the vm-camprops / vm-pointnclick
idiom: no text, no font, no VM clock, two bytes of OAM.

One button per verb, which is what keeps the checks free of timing assumptions:

    A       mark = mark + 1, and show it
    SELECT  mark = 0, and show it
    UP      save to slot 1        RIGHT  save to slot 2      DOWN  save to slot 3
    B       load slot 2, and show what came back
    LEFT    clear slot 2
    START   peek slot 3's `mark` into `probe`, and show THAT

The slots are authored as the catalogue's 0-based `slot` field (0, 1, 2) and
read as the reference engine's 1, 2, 3 everywhere a human sees them.

Regenerate:

  python projects/vm-saveslots/assets/gen.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PROJ))
sys.path.insert(0, ROOT)

import toml  # noqa: E402
import mosaik_vm as mv  # noqa: E402

W, H = 20, 18               # one GB screen, so the camera never moves

#: Where the readout draws. `mark` 0 puts the player at x = 8, and every step
#: is one tile - so the check reads a VALUE straight off OAM.
SHOW_X = "8 + mark * 8"
SHOW_Y = 64
#: ...and the same for the PEEK result, one row lower so a stale reading from
#: the other readout cannot be mistaken for it.
PROBE_X = "8 + probe * 8"
PROBE_Y = 96

SHELL = '''\
-- vm-saveslots -- the reference engine's three save slots (W7c). One room, one heap
-- variable, and a button per verb; the player's own position is the readout,
-- so the checks need no text and no clock. `vm-save` is the single-slot
-- sibling and the byte-identical control.
-- Regenerate with projects/vm-saveslots/assets/gen.py.

module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "graphics.bkg"
    import "scenes"
    import "scripts"
    import "vm.core"
    import "rooms"
    import "glue"

    const SPRITES: array[u8, 16] = [
        0x3C, 0x3C, 0x7E, 0x42, 0xFF, 0x81, 0xFF, 0x81,
        0xFF, 0x81, 0xFF, 0x81, 0x7E, 0x42, 0x3C, 0x3C
    ]

    function main() {
        core.font_preload()
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        sprite.set_data(0, 1, SPRITES)

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


def _show(x, y):
    return {"event": "player_setpos", "x": x, "y": y}


SCRIPTS = {"script": [
    # THE ATTACHMENTS LIVE IN THE ROOM'S On Init, NOT IN THE BOOT SCRIPT, and
    # that is the load path's own rule rather than a style choice: a LOAD
    # raises LOAD_COMPLETE, which re-enters the scene and tears down the
    # pre-load input, timer and UI state (vm.core's scene-change teardown -
    # the reference VM does the same). A game that attaches its buttons once at boot has
    # no buttons at all after its first Continue. On Init runs on every room
    # load, including that one.
    {"name": "main", "events": [{"event": "stop"}]},
    {"name": "bump", "events": [
        {"event": "set_var", "var": "mark", "expr": "mark + 1"},
        _show(SHOW_X, SHOW_Y),
        {"event": "stop"},
    ]},
    {"name": "reset", "events": [
        {"event": "set_var", "var": "mark", "value": 0},
        _show(SHOW_X, SHOW_Y),
        {"event": "stop"},
    ]},
    # The three writes. `slot` is the catalogue's 0-based field, so these are
    # the reference engine's slots 1, 2 and 3.
    {"name": "save1", "events": [
        {"event": "save", "slot": 0}, {"event": "stop"}]},
    {"name": "save2", "events": [
        {"event": "save", "slot": 1}, {"event": "stop"}]},
    {"name": "save3", "events": [
        {"event": "save", "slot": 2}, {"event": "stop"}]},
    # LOAD raises LOAD_COMPLETE, which re-enters the room and teleports the
    # player to the SAVED position - so the readout has to be re-drawn after
    # it, from the restored `mark`. That is not a quirk of this sample: it is
    # what a load is (the whole heap and the player's place come back).
    {"name": "load2", "events": [
        {"event": "load", "slot": 1},
        {"event": "stop"}]},
    {"name": "clear2", "events": [
        {"event": "save_clear", "slot": 1}, {"event": "stop"}]},
    # PEEK reads slot 3's `mark` WITHOUT loading it: the live heap keeps its
    # own `mark`, and an empty slot writes 0 rather than leaving `probe` alone.
    {"name": "peek3", "events": [
        {"event": "save_peek", "var": "probe", "src": "mark", "slot": 2},
        _show(PROBE_X, PROBE_Y),
        {"event": "stop"}]},
    # The room's On Init: the eight attachments (see the note on `main` -
    # a load tears the old ones down) and one draw of the readout, so the
    # boot frame already shows `mark`.
    {"name": "room_init", "events": [
        {"event": "input_attach", "button": "a", "script": "bump"},
        {"event": "input_attach", "button": "select", "script": "reset"},
        {"event": "input_attach", "button": "up", "script": "save1"},
        {"event": "input_attach", "button": "right", "script": "save2"},
        {"event": "input_attach", "button": "down", "script": "save3"},
        {"event": "input_attach", "button": "b", "script": "load2"},
        {"event": "input_attach", "button": "left", "script": "clear2"},
        {"event": "input_attach", "button": "start", "script": "peek3"},
        _show(SHOW_X, SHOW_Y),
        {"event": "stop"},
    ]},
]}


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def rows(g):
    return "[" + ", ".join("[" + ",".join(str(v) for v in r) + "]" for r in g) + "]"


def main():
    from mosaik_assets import write_png_indexed

    bpal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    floor = [0] * 64
    rule = [0] * 64                     # a one-tile ruler mark, every 8 px
    for i in range(8):
        rule[i * 8] = 2
    tile_rows = []
    for cell in (floor, rule):
        tile_rows += [[cell[y * 8 + x] for x in range(8)] for y in range(8)]
    write_png_indexed(os.path.join(PROJ, "assets", "tiles.png"), 8, 16,
                      tile_rows, bpal)

    # the two readout rows get the ruler tile, so the picture says what the
    # check reads; everything else is plain floor and nothing is solid.
    tiles = [[1 if y in (SHOW_Y // 8, PROBE_Y // 8) else 0 for _x in range(W)]
             for y in range(H)]
    coll = [[0] * W for _y in range(H)]

    world = [
        "# vm-saveslots -- GENERATED by assets/gen.py (the reference engine's three save "
        "slots, W7c).",
        "[[scene]]", 'name = "room"', 'scene_type = "topdown"',
        "map = %s" % rows(tiles), "collision = %s" % rows(coll),
        'on_init = "room_init"',
        '[[scene.object]]\nkind = "player"\nid = 0\nx = 8\ny = %d' % SHOW_Y,
        "",
        '[world]\nmodule = "scenes"\nmap_w = %d\nmap_h = %d\nvm = true'
        '\nnext_object_id = 1' % (W, H),
        '[tileset]\npng = "assets/tiles.png"',
        '[kinds]\nplayer = 0',
    ]
    _write(os.path.join(PROJ, "world.toml"), "\n".join(world) + "\n")
    _write(os.path.join(PROJ, "scripts", "main.evt.toml"), toml.dumps(SCRIPTS))
    _write(os.path.join(PROJ, "src", "main.mos"), SHELL)

    import mosaik_scenes
    wobj, base = mosaik_scenes.load_world(os.path.join(PROJ, "world.toml"))
    _write(os.path.join(PROJ, "src", "scenes.mos"),
           mosaik_scenes.transpile(wobj, base))
    prog = mv.compile_path(os.path.join(PROJ, "scripts"))
    _write(os.path.join(PROJ, "src", "scripts.mos"), prog.to_scripts_mos())
    mv.generate_glue(PROJ)
    mv.generate_rooms(PROJ)
    print("vm-saveslots regenerated at", PROJ)


if __name__ == "__main__":
    main()
