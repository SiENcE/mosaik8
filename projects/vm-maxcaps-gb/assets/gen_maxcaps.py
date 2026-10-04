#!/usr/bin/env python3
"""Generate the VM8 MAX-CAPS stress projects (Game Boy + Lynx).

A capacity showcase: one VM8 game that pushes the engine hard-caps
to their limits so the combined ceiling
is measurable, not theoretical. It maxes the ENGINE-BEHAVIOURAL caps:

  * 8 concurrent THREADS      (VM_CTXS) -- main + 7 spawned loops
  * 64 HEAP VARIABLES         (VM_HEAP) -- var0..var63 all written
  * 4 TIMERS                  (NTIMERS)
  * 4 INPUT ATTACHMENTS       (NIN)     -- b/up/down/left
  * 8 ACTORS + player         (ACTORS)  -- 4 combat enemies + 4 entity NPCs
  * 4 COMBAT enemies          (NENEM)   -- the whole combat pool
  * 8 PROJECTILES             (NPROJ)   -- fired in one burst
  * a MENU of 16 options
  * WAIT 255 (the u8 frame max)

The actor pool (8) is SHARED between combat enemies and entity-slot NPCs, so 4
combat + 4 entity maxes the pool while leaving entity slots at 4/8 -- that shared
budget IS the real combined limit and the point of the demo. Tileset + string
volume are MODERATED (not the 256 bkg-tile / 256-string art caps): on the Lynx
those fight the 46.6 KB MAIN, so this project measures the LOGIC ceiling, and the
per-target `bkg_max_tiles` + string streaming keep it fitting.

Run: python projects/vm-maxcaps-gb/assets/gen_maxcaps.py   (writes BOTH projects)
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # projects/

NVARS = 63            # 63 counters + the menu's `choice` var = the 64-cell heap cap
NTHREADS = 8          # main + 7
NTIMERS = 4
NINPUTS = 4
NENEMY = 4
NNPC = 4
NPROJ = 8
NMENU = 16

# ---- 4 sprite tiles: 0 player, 1 enemy, 2 heart, 3 npc, 4 bullet ----------
SPRITES = [
    0x00, 0x00, 0x7E, 0x7E, 0x7E, 0x7E, 0x7E, 0x7E,
    0x7E, 0x7E, 0x7E, 0x7E, 0x7E, 0x7E, 0x00, 0x00,   # 0 player (box)
    0x00, 0x00, 0x66, 0x66, 0xFF, 0xFF, 0xFF, 0xFF,
    0xFF, 0xFF, 0x7E, 0x7E, 0x3C, 0x3C, 0x18, 0x18,   # 1 enemy
    0x00, 0x00, 0x6C, 0x6C, 0xFE, 0xFE, 0xFE, 0xFE,
    0x7C, 0x7C, 0x38, 0x38, 0x10, 0x10, 0x00, 0x00,   # 2 heart
    0x3C, 0x3C, 0x42, 0x7E, 0x81, 0xFF, 0x81, 0xFF,
    0x81, 0xFF, 0x42, 0x7E, 0x3C, 0x3C, 0x00, 0x00,   # 3 npc
    0x18, 0x18, 0x3C, 0x3C, 0x3C, 0x3C, 0x18, 0x18,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,   # 4 bullet
]
NSPRITE_TILES = len(SPRITES) // 16


def toml_map(w, h):
    """A screen room: solid border, open floor."""
    rows = []
    col = []
    for y in range(h):
        r = []
        c = []
        for x in range(w):
            wall = 1 if (x == 0 or y == 0 or x == w - 1 or y == h - 1) else 0
            r.append(1 if wall else 0)
            c.append(1 if wall else 0)
        rows.append(r)
        col.append(c)
    return rows, col


def build_scripts():
    """The event lists that max the script-authorable caps."""
    scripts = []

    # main: init 64 vars, start 7 threads, 4 timers, 4 input attachments.
    main = []
    for i in range(NVARS):
        main.append({"event": "set_var", "var": "var%d" % i, "value": i})
    for t in range(1, NTHREADS):
        main.append({"event": "start_thread", "script": "worker%d" % t})
    for t in range(NTIMERS):
        main.append({"event": "timer_set", "timer": t,
                     "period": 30 + t * 15, "script": "ticker%d" % t})
    for b, btn in enumerate(["b", "up", "down", "left"][:NINPUTS]):
        main.append({"event": "input_attach", "button": btn, "script": "on_%s" % btn})
    main.append({"event": "stop"})
    scripts.append({"name": "main", "events": main})

    # 7 worker threads: each loops, waiting then bumping its own var (concurrency).
    for t in range(1, NTHREADS):
        scripts.append({"name": "worker%d" % t, "loop": True, "events": [
            {"event": "wait", "frames": 20 + t * 10},
            {"event": "set_var", "var": "var%d" % t, "expr": "var%d + 1" % t},
        ]})

    # 4 timers: each bumps a var; timer 0 also shows a page (text) so the box works.
    for t in range(NTIMERS):
        evs = [{"event": "set_var", "var": "var%d" % (32 + t),
                "expr": "var%d + 1" % (32 + t)}]
        evs.append({"event": "stop"})
        scripts.append({"name": "ticker%d" % t, "events": evs})

    # Inputs: B fires 8 projectiles (max the pool), UP opens the 16-option menu,
    # DOWN shows a WAIT-255 gated line, LEFT bumps a var.
    fire = [{"event": "projectile", "x": 40 + p * 8, "y": 60,
             "vx": 0, "vy": -4, "tile": 4, "life": 40} for p in range(NPROJ)]
    fire.append({"event": "stop"})
    scripts.append({"name": "on_b", "events": fire})

    menu_opts = ["OPTION %02d" % (i + 1) for i in range(NMENU)]
    scripts.append({"name": "on_up", "events": [
        {"event": "lock"},
        {"event": "menu", "var": "choice", "row": 2, "options": menu_opts},
        {"event": "unlock"},
        {"event": "stop"},
    ]})
    scripts.append({"name": "on_down", "events": [
        {"event": "text", "string": "WAITING THE MAX\n255 FRAMES THEN\nRESUMING NOW."},
        {"event": "wait", "frames": 255},
        {"event": "set_var", "var": "var62", "expr": "var62 + 1"},
        {"event": "stop"},
    ]})
    scripts.append({"name": "on_left", "events": [
        {"event": "set_var", "var": "var40", "expr": "var40 + 1"},
        {"event": "stop"},
    ]})

    # 4 NPC On-Interact dialogues (each a distinct multi-page string set) -- these
    # are the vm.entity slots (4/8, the pool sharing the other 4 with combat).
    npc_lines = [
        ["I GUARD THE WEST\nGATE OF THE OLD\nKEEP TRAVELLER.",
         "MANY HAVE TRIED\nAND FEW RETURN\nFROM THE DEPTHS."],
        ["THE BLACKSMITH\nSELLS BLADES BY\nTHE MARKET WELL.",
         "GOLD BUYS ARMOR\nAND KEEN ARROWS\nFOR THE JOURNEY."],
        ["SEEK THE SHRINE\nBENEATH THE FALLS\nAT FIRST LIGHT.",
         "THREE TRIALS GUARD\nTHE SACRED FLAME\nOF THE ELDERS."],
        ["THE DRAGON SLEEPS\nUPON A HOARD OF\nANCIENT TREASURE.",
         "DO NOT WAKE IT\nUNLESS YOUR HEART\nIS TRULY READY."],
    ]
    for n in range(NNPC):
        evs = [{"event": "lock"}]
        for line in npc_lines[n]:
            evs.append({"event": "text", "string": line})
        evs.append({"event": "unlock"})
        evs.append({"event": "stop"})
        scripts.append({"name": "npc%d_talk" % n, "events": evs})

    return scripts


def build_world(w, h):
    rows, col = toml_map(w, h)

    def grid(g):
        return "[" + ", ".join("[" + ",".join(str(v) for v in r) + "]" for r in g) + "]"

    lines = ["# vm-maxcaps -- GENERATED by assets/gen_maxcaps.py (edit that + rerun)."]
    lines.append('[[scene]]')
    lines.append('name = "arena"')
    lines.append("map = " + grid(rows))
    lines.append("collision = " + grid(col))
    # player
    lines.append("[[scene.object]]")
    lines.append('kind = "player"')
    lines.append("id = 0")
    lines.append("x = %d" % (w * 8 // 2))
    lines.append("y = %d" % (h * 8 // 2))
    oid = 1
    # 4 enemies (combat pool)
    for e in range(NENEMY):
        lines.append("[[scene.object]]")
        lines.append('kind = "enemy"')
        lines.append("id = %d" % oid); oid += 1
        lines.append("x = %d" % (16 + e * 16))
        lines.append("y = 16")
    # 4 NPCs (entity slots, On Interact)
    for n in range(NNPC):
        lines.append("[[scene.object]]")
        lines.append('kind = "npc"')
        lines.append("id = %d" % oid); oid += 1
        lines.append("x = %d" % (16 + n * 16))
        lines.append("y = %d" % (h * 8 - 24))
        lines.append('on_interact = "npc%d_talk"' % n)
    lines.append("")
    lines.append("[world]")
    lines.append('module = "scenes"')
    lines.append("map_w = %d" % w)
    lines.append("map_h = %d" % h)
    lines.append("vm = true")
    lines.append("next_object_id = %d" % oid)
    lines.append("[tileset]")
    lines.append('png = "assets/tiles.png"')
    lines.append("[kinds]")
    lines.append("player = 0")
    lines.append("enemy = 1")
    lines.append("npc = 2")
    return "\n".join(lines) + "\n"


def sprite_const():
    body = []
    for i in range(0, len(SPRITES), 16):
        body.append("        " + ", ".join(
            "0x%02X" % v for v in SPRITES[i:i + 16]) + ("," if i + 16 < len(SPRITES) else ""))
    return ("    const SPRITES: array[u8, %d] = [\n" % len(SPRITES)
            + "\n".join(body) + "\n    ]")


def build_shell(name, hud_hp=6):
    """The shell wiring EVERY kit: player + entity + trigger + combat + projectile
    + HUD. Mirrors vm-rpg's dispatch; adds the projectile pool + the 8-actor
    combat/entity split."""
    return '''-- %s -- the VM8 MAX-CAPS shell. Wires every opt-in kit at once:
-- vm.player (native player), vm.combat (4 enemies), vm.entity (4 On-Interact
-- NPCs; the 8-actor pool is shared), vm.projectile (8-bullet pool), vm.trigger,
-- engine.hud. The event scripts max the thread/var/timer/input/menu caps.
-- Regenerate: python projects/vm-maxcaps-gb/assets/gen_maxcaps.py
module "main" {
    import "platform.video"
    import "platform.input"
    import "graphics.sprite"
    import "graphics.bkg"
    import "scenes"
    import "scripts"
    import "vm.core"
    import "vm.player"
    import "vm.actor"
    import "vm.entity"
    import "vm.trigger"
    import "vm.combat"
    import "vm.projectile"
    import "engine.hud"

%s

    const HUD_BASE = 18
    const PMAX_HP = %d

    var cur_room: u8 = 0
    var prev_b: u8 = 0
    var pstart_x: u16 = 80
    var pstart_y: u16 = 52

    function solid_at(x: u16, y: u16) -> bool {
        var cx: u16 = x / 8
        var cy: u16 = y / 8
        var idx: u16 = cy * scenes.MAP_W + cx
        return scenes.collision_at(cur_room, idx) == scenes.COLLIDE_SOLID
    }

    function load_room(room: u8) {
        cur_room = room
        scenes.paint(room)
        entity.reset()
        combat.reset()
        var slot: u8 = 0
        for i in 0..scenes.OBJ_COUNT {
            if scenes.OBJ_SCENE[i] == room {
                if scenes.OBJ_KIND[i] != scenes.KIND_PLAYER {
                    if scenes.OBJ_KIND[i] == scenes.KIND_ENEMY {
                        actor.activate(slot, 1, scenes.OBJ_X[i], scenes.OBJ_Y[i])
                        combat.add(slot, 3)
                    } else {
                        actor.activate(slot, 3, scenes.OBJ_X[i], scenes.OBJ_Y[i])
                        entity.add(slot, scenes.NO_SCRIPT, scenes.NO_SCRIPT, scenes.obj_interact(i))
                    }
                    slot += 1
                }
            }
        }
    }

    function reset_game() {
        player.set_pos(pstart_x, pstart_y)
        combat.set_player_hp(PMAX_HP, PMAX_HP)
        load_room(0)
    }

    function tick() {
        player.update()
        entity.update()
        var b: u8 = 0
        if input.held(INPUT_B) {
            b = 1
        }
        var atk: u8 = 0
        if b == 1 {
            if prev_b == 0 {
                atk = 1
            }
        }
        prev_b = b
        combat.update(atk, player.face())
        hud.hearts(HUD_BASE, combat.player_hp(), PMAX_HP, 4, 4, 9, SCREEN_HEIGHT)
    }

    function main() {
        core.font_preload()
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        sprite.set_data(0, %d, SPRITES)
        for i in 0..PMAX_HP {
            sprite.set_tile(HUD_BASE + i, 2)
        }
        core.boot(scripts.fetch, scripts.render_text, scripts.ENTRY_main)
        core.set_choice(scripts.render_choice)
        core.set_projectiles(projectile.launch, projectile.update, projectile.render)
        combat.set_player_hp(PMAX_HP, PMAX_HP)
        combat.set_player_death(reset_game)
        projectile.reset()
        player.setup(0, pstart_x, pstart_y, 8, 8, 1, solid_at)
        load_room(0)
        core.set_player(tick)
        video.enable_lcd()
        video.show_background()
        video.show_sprites()
        core.run()
    }

    export main
}
''' % (name, sprite_const(), hud_hp, NSPRITE_TILES)


# ---- tiles.png (a tiny 3-tile tileset: 0 floor, 1 wall, 2 cave) ------------
def write_tiles_png(path):
    import sys
    sys.path.insert(0, ROOT + "/..")  # repo root for mosaik_assets
    from mosaik_assets import write_png_indexed
    pal = [(224, 224, 224), (96, 96, 96), (48, 48, 48), (0, 0, 0)]
    # 3 tiles side by side (24x8): floor=0, wall=1, cave=2
    px = []
    for y in range(8):
        row = []
        for t, v in enumerate([0, 1, 2]):
            for x in range(8):
                row.append(v if (t == 1) else (v if (x + y) % 4 == 0 else 0))
        px.extend(row)
    write_png_indexed(path, 24, 8, px, pal)


def emit(target, w, h, bkg_max_tiles):
    pdir = os.path.join(ROOT, "vm-maxcaps-%s" % target)
    os.makedirs(os.path.join(pdir, "src"), exist_ok=True)
    os.makedirs(os.path.join(pdir, "scripts"), exist_ok=True)
    os.makedirs(os.path.join(pdir, "assets"), exist_ok=True)

    # scripts/main.evt.toml
    import_toml_dump(os.path.join(pdir, "scripts", "main.evt.toml"), build_scripts())
    # world.toml
    with open(os.path.join(pdir, "world.toml"), "w", encoding="utf-8") as f:
        f.write(build_world(w, h))
    # tiles.png
    write_tiles_png(os.path.join(pdir, "assets", "tiles.png"))
    # src/main.mos
    with open(os.path.join(pdir, "src", "main.mos"), "w", encoding="utf-8") as f:
        f.write(build_shell("vm-maxcaps-%s" % target))
    # mosaik.toml
    plats = {"gb": '"gameboy", "gameboy_color"', "lynx": '"lynx"'}[target]
    knob = ("bkg_max_tiles = %d\n" % bkg_max_tiles) if bkg_max_tiles else ""
    with open(os.path.join(pdir, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "vm-maxcaps-%s"\nversion = "0.1.0"\n'
                '# VM8 MAX-CAPS stress test (%s). See assets/gen_maxcaps.py header for the\n'
                '# caps it maxes (8 threads / 64 vars / 4 timers / 4 inputs / 8 actors / 4 combat\n'
                '# / 8 projectiles / 16-option menu). Regenerate: python projects/vm-maxcaps-gb/assets/gen_maxcaps.py\n'
                'target_platforms = [%s]\n\n[source]\nfolder = "src/"\n\n[build]\noutput_dir = "build"\n%s'
                % (target, target, plats, knob))
    print("wrote projects/vm-maxcaps-%s" % target)


def import_toml_dump(path, scripts):
    """Write the [[script]] tables as readable TOML (inline event dicts)."""
    def val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, list):
            return "[" + ", ".join(val(x) for x in v) + "]"
        return '"%s"' % str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")

    def ev(d):
        return "{ " + ", ".join("%s = %s" % (k, val(v)) for k, v in d.items()) + " }"

    lines = ["# vm-maxcaps -- GENERATED by assets/gen_maxcaps.py (edit that + rerun)."]
    for s in scripts:
        lines.append("")
        lines.append("[[script]]")
        lines.append('name = "%s"' % s["name"])
        if s.get("loop"):
            lines.append("loop = true")
        lines.append("events = [")
        for e in s["events"]:
            lines.append("  %s," % ev(e))
        lines.append("]")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    # Game Boy: 20x18 screen room, no bkg-table cap needed (VRAM tiles, 8 KB WRAM).
    emit("gb", 20, 18, bkg_max_tiles=None)
    # Lynx: 20x13 screen room; cap the 4 KB resident bkg tile table (3-tile world).
    emit("lynx", 20, 13, bkg_max_tiles=4)
