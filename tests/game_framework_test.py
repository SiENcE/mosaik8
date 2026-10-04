#!/usr/bin/env python3
"""Game-framework engine modules + the top-down kit (lib/).

See docs/game-framework.md. Tier A (genre-agnostic): `engine.pad`
(per-button input edge detection), `engine.camera` (a follow camera with exported,
shared camx/camy), `engine.collision` (the pure box-corner test) and the owner
modules `engine.scene`/`engine.dialogue`/`engine.hud`. Tier B (genre kits): `genre.topdown`
(facing / walk-anim / chase) plus `topdown_template.mos`, and `genre.shmup`
(sprite-vs-sprite overlap / frame-timer cooldown / u8-safe projectile despawn)
plus `shmup_template.mos` — each the canonical loop that composes its kit.

These are *vendored source libraries* composed by a game that owns its loop, so
this test compiles the actual template (via MosaikCompiler.compile_program, the
whole-program path) against the library modules and checks (a) it links clean on
all nine consoles and (b) the cross-module lowering is right: edge state, the
exported camera globals read straight from the game, the vendored tile_at feeding
collision.any_solid, and the top-down kit helpers.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from mosaik import MosaikCompiler, PLATFORM_CAPS

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB = os.path.join(ROOT, "lib")
# Tier-A engine modules live in lib/engine/, Tier-B genre kits in lib/genre/
# (the namespace split). lib_module() finds either by filename.
LIB_TIERS = (os.path.join(LIB, "engine"), os.path.join(LIB, "genre"))


def lib_module(name):
    for tier in LIB_TIERS:
        p = os.path.join(tier, name)
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                return (name, f.read())
    raise FileNotFoundError(name)


# The game source under test IS the shipped canonical template -- so the
# copy-me skeleton is guaranteed to stay valid on every console.
def compile_game(platform):
    return MosaikCompiler().compile_program(
        [lib_module('topdown_template.mos'), lib_module('pad.mos'),
         lib_module('camera.mos'), lib_module('collision.mos'),
         lib_module('topdown.mos'), lib_module('anim.mos')],
        platform=platform)


# engine.anim -- callback-driven sprite animation (Tier-A, built on first-class
# function pointers): the game registers an "apply" callback per animated
# sprite, anim.tick() advances each and fires the callback on frame changes.
ANIM_GAME = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "engine.anim"
    function hero(slot: u8, frame: u8) {
        sprite.set_tile(slot, frame)
    }
    function main() {
        video.enable_lcd()
        anim.set(0, 8, 2, hero)
        loop { anim.tick() video.wait_vblank() }
    }
    export main
}
'''


def compile_anim(platform):
    return MosaikCompiler().compile_program(
        [lib_module('anim.mos'), ("main.mos", ANIM_GAME)], platform=platform)


# engine.anim one-shot animator: play once, then stop and raise done() -- covers the
# explosion / chest-open case without inline timer+state code.
ANIM_ONESHOT_GAME = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "engine.anim"
    var spark_x: u8 = 80
    function spark(slot: u8, frame: u8) {
        sprite.set_tile(slot, frame)
    }
    function main() {
        video.enable_lcd()
        anim.play_once(0, 6, 3, spark)
        loop {
            anim.tick()
            if anim.done(0) { spark_x = SCREEN_HEIGHT }   -- one-shot finished -> hide
            if anim.active(0) { sprite.move(0, spark_x, 40) }
            video.wait_vblank()
        }
    }
    export main
}
'''


def compile_anim_oneshot(platform):
    return MosaikCompiler().compile_program(
        [lib_module('anim.mos'), ("main.mos", ANIM_ONESHOT_GAME)], platform=platform)


# genre.combat -- the top-down action genre kit: pure helpers (overlap / within /
# cooldown / step_toward / hitbox) over a GAME-owned enemy pool (struct-of-arrays),
# the way game-slice's combat is shaped. Exercises every exported helper so the
# kit can't rot.
COMBAT_GAME = '''
module "main" {
    import "platform.video"
    import "platform.input"
    import "graphics.sprite"
    import "genre.combat"
    const MAXE: u8 = 3
    var ex: array[u8, 3]
    var ey: array[u8, 3]
    var ehp: array[u8, 3]
    var px: u8 = 40
    var py: u8 = 40
    var face: u8 = 3
    var hp: u8 = 3
    var atk: u8 = 0
    function main() {
        video.enable_lcd()
        loop {
            if input.held(INPUT_B) { atk = 8 }
            atk = combat.cooldown(atk)
            var hx: u8 = combat.hitbox_x(px, face, 12)
            var hy: u8 = combat.hitbox_y(py, face, 12)
            for i in 0..MAXE {
                ex[i] = combat.step_toward(ex[i], px)
                ey[i] = combat.step_toward(ey[i], py)
                if atk > 0 and combat.within(hx, hy, ex[i] + 8, ey[i] + 8, 12) {
                    ehp[i] = combat.cooldown(ehp[i])
                }
                if combat.overlap(px, py, ex[i], ey[i], 12) {
                    hp = combat.cooldown(hp)
                }
            }
            video.wait_vblank()
        }
    }
    export main
}
'''


def compile_combat(platform):
    return MosaikCompiler().compile_program(
        [lib_module('combat.mos'), ("main.mos", COMBAT_GAME)], platform=platform)


# A minimal game that composes the Tier-A owner modules scene / dialogue / hud
# the way the slice does (these were factored out of projects/game-slice). NES
# text gating is module-level (`if platform == "nes"`), the idiom the rulebook
# requires, so this links no printf on the NES.
OWNER_GAME = '''
module "main" {
    import "platform.video"
    import "platform.input"
    import "graphics.sprite"
    import "graphics.text"
    import "engine.scene"
    import "engine.dialogue"
    import "engine.hud"
    import "engine.camera"
    const HEARTS: u8 = 4
    const KEY: u8 = 7
    const MAP_SCENE: u8 = 9
    var hp: u8 = 3
    var has_key: u8 = 0
    if platform == "nes" {
        function render_dlg() { }
    } else {
        function render_dlg() {
            var c: u8 = dialogue.col(camera.camx / 8, 2)
            var r: u8 = dialogue.row(camera.camy / 8, 14)
            if dialogue.page == 0 {
                text.print_string(c, r, "HELLO!")
            } else {
                text.print_string(c, r, "BYE!")
            }
        }
    }
    function main() {
        video.enable_lcd()
        video.show_sprites()
        loop {
            if dialogue.is_open() {
                if input.held(INPUT_A) { dialogue.advance() }
            } else {
                if input.held(INPUT_A) { dialogue.start(2) }
                if input.held(INPUT_START) {
                    if scene.is_at(MAP_SCENE) {
                        scene.set(scene.leave_overlay())
                    } else {
                        scene.enter_overlay(64, 64)
                        scene.set(MAP_SCENE)
                    }
                }
            }
            hud.hearts(HEARTS, hp, 3, 6, 4, 10, SCREEN_HEIGHT)
            hud.icon(KEY, has_key, 140, 2, SCREEN_HEIGHT)
            video.wait_vblank()
            if dialogue.is_open() { render_dlg() }
        }
    }
    export main
}
'''


def compile_owner_game(platform):
    return MosaikCompiler().compile_program(
        [("owner_game.mos", OWNER_GAME), lib_module('scene.mos'),
         lib_module('dialogue.mos'), lib_module('box.mos'), lib_module('hud.mos'),
         lib_module('camera.mos')],
        platform=platform)


# The shmup genre kit + its canonical loop. As with the top-down template, the
# game under test IS the shipped copy-me skeleton, so it can't rot. A shmup has a
# static playfield, so it composes engine.pad + genre.shmup and NOT camera/collision.
def compile_shmup(platform):
    return MosaikCompiler().compile_program(
        [lib_module('shmup_template.mos'), lib_module('pad.mos'),
         lib_module('shmup.mos')],
        platform=platform)


# engine.menu -- the Tier-A menu primitive (cursor nav + A-confirm edge + a framed
# text box). The game owns the labels/actions; the kit owns the mechanics. The
# game's ONLY text here is the kit's draw_box (a NES no-op), so the NES build links
# no printf -- the same gating as engine.dialogue.
MENU_GAME = '''
module "main" {
    import "platform.video"
    import "platform.input"
    import "engine.menu"
    import "engine.box"
    function main() {
        video.enable_lcd()
        menu.arm()
        menu.set_cursor(0)
        loop {
            menu.nav(3)
            if menu.confirmed() { menu.reset() }
            box.draw_box(2, 2, 12, 5)
            video.wait_vblank()
        }
    }
    export main
}
'''


def compile_menu(platform):
    return MosaikCompiler().compile_program(
        [lib_module('menu.mos'), lib_module('box.mos'), ("main.mos", MENU_GAME)],
        platform=platform)


# engine.camera scripted verbs (set / approach / pan_to) + engine.sequence -- the
# cutscene primitives. This game IS the shape of a cinematic intro: pin the camera
# to the bottom, scroll it to the top (pan_to), hold for the logo, then hand off to
# a menu -- all sequenced by engine.sequence's beat/timer state machine, no script
# graph. Drives the new verbs so they can't rot, and links on every console.
CINEMATIC_GAME = '''
module "main" {
    import "platform.video"
    import "platform.input"
    import "graphics.sprite"
    import "graphics.bkg"
    import "engine.camera"
    import "engine.sequence"
    import "engine.menu"
    import "engine.box"
    import "engine.pad"
    const START_Y: u8 = 112
    function main() {
        video.enable_lcd()
        video.show_background()
        camera.set(0, START_Y)
        sequence.start()
        loop {
            pad.update()
            if sequence.at(0) {
                if camera.pan_to(0, 0, 2) { sequence.advance() }
            } else if sequence.at(1) {
                sprite.move(0, 72, 60)
                sequence.tick()
                if pad.a() or sequence.elapsed(120) {
                    sprite.move(0, 72, SCREEN_HEIGHT)
                    menu.arm()
                    sequence.advance()
                }
            } else {
                menu.nav(2)
                if menu.confirmed() { sequence.start() }
                box.draw_box(4, 12, 12, 4)
            }
            video.wait_vblank()
        }
    }
    export main
}
'''


def compile_cinematic(platform):
    return MosaikCompiler().compile_program(
        [lib_module('sequence.mos'), lib_module('camera.mos'), lib_module('menu.mos'),
         lib_module('box.mos'), lib_module('pad.mos'), ("main.mos", CINEMATIC_GAME)],
        platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("Game-framework engine modules + top-down kit (lib/)")
    print("=" * 50)
    ok = True

    # (a) Links clean on every console -- the framework is cross-platform.
    for platform in PLATFORM_CAPS:
        out = compile_game(platform)
        ok &= check("compiles on %s" % platform,
                    not out.startswith("Compilation error:"))

    # (a2) The Tier-A owner modules (scene/dialogue/hud) compose + compile on
    # every console too.
    for platform in PLATFORM_CAPS:
        out = compile_owner_game(platform)
        ok &= check("owner modules (scene/dialogue/hud) compile on %s" % platform,
                    not out.startswith("Compilation error:"))

    # (a3) The shmup genre kit + template compose + compile on every console.
    for platform in PLATFORM_CAPS:
        out = compile_shmup(platform)
        ok &= check("shmup kit + template compile on %s" % platform,
                    not out.startswith("Compilation error:"))

    # (a3b) The combat genre kit (top-down action: enemy pool + sword helpers)
    # compiles on every console.
    for platform in PLATFORM_CAPS:
        out = compile_combat(platform)
        ok &= check("combat kit compiles on %s" % platform,
                    not out.startswith("Compilation error:"))

    # (a4) engine.anim (callback-driven animation) composes + compiles on every
    # console -- first-class function pointers lower to portable C everywhere.
    for platform in PLATFORM_CAPS:
        out = compile_anim(platform)
        ok &= check("engine.anim (callbacks) compiles on %s" % platform,
                    not out.startswith("Compilation error:"))

    # (a4b) engine.anim one-shot animator (play_once / done / active) compiles on a
    # representative GBDK + cc65 pair.
    for platform in ('gameboy', 'lynx'):
        out = compile_anim_oneshot(platform)
        ok &= check("[%s] engine.anim one-shot (play_once/done/active) compiles"
                    % platform, not out.startswith("Compilation error:"))

    # (a5) The cutscene primitives (camera.set/pan_to + engine.sequence) compose +
    # compile on every console -- a scripted intro is portable engine/language code.
    for platform in PLATFORM_CAPS:
        out = compile_cinematic(platform)
        ok &= check("cinematic intro (camera.pan_to + engine.sequence) compiles on %s"
                    % platform, not out.startswith("Compilation error:"))

    # (b) Cross-module lowering (checked on a representative GBDK + cc65 pair).
    for platform in ('gameboy', 'lynx'):
        out = compile_game(platform)
        ok &= check("[%s] engine.pad edge module lowers (actions + d-pad)" % platform,
                    "void engine_pad_update(void)" in out
                    and "engine_pad_update();" in out
                    and "engine_pad_a()" in out
                    # rising-edge logic: down now, up last frame
                    and "engine_pad_cur_a > 0" in out
                    and "engine_pad_last_a == 0" in out
                    # d-pad edges (for grid/menu movement, one step per tap)
                    and "engine_pad_up(void)" in out and "engine_pad_down(void)" in out
                    and "engine_pad_left(void)" in out and "engine_pad_right(void)" in out)
        ok &= check("[%s] engine.camera owns exported, shared scroll state" % platform,
                    "uint8_t engine_camera_camx;" in out
                    and "engine_camera_follow(" in out
                    # the game reads the exported camera globals directly
                    and "engine_camera_camx" in out
                    and "engine_camera_camy" in out)
        # The scripted-camera + sequence cutscene primitives lower correctly: a
        # pinned/panned camera (set/pan_to) and the beat/timer state machine.
        cin = compile_cinematic(platform)
        ok &= check("[%s] engine.camera scripted verbs (set / pan_to / approach)"
                    % platform,
                    "engine_camera_set(" in cin
                    and "engine_camera_pan_to(" in cin
                    and "uint8_t engine_camera_approach(uint8_t" in cin)
        ok &= check("[%s] engine.sequence beat/timer state machine lowers" % platform,
                    "uint8_t engine_sequence_step;" in cin
                    and "uint16_t engine_sequence_timer;" in cin
                    and "engine_sequence_start(" in cin
                    and "engine_sequence_at(" in cin
                    and "engine_sequence_advance(" in cin
                    and "engine_sequence_tick(" in cin
                    and "engine_sequence_elapsed(" in cin)
        ok &= check("[%s] engine.collision corner test + vendored tile_at" % platform,
                    "engine_collision_any_solid(" in out
                    and "tile_at(" in out)
        ok &= check("[%s] genre.topdown kit lowers (facing/anim/chase + consts)" % platform,
                    "genre_topdown_facing4(" in out
                    and "uint8_t genre_topdown_anim2(uint8_t" in out
                    and "uint8_t genre_topdown_toward(uint8_t" in out
                    and "#define genre_topdown_FACE_LEFT" in out)
        an = compile_anim(platform)
        ok &= check("[%s] engine.anim drives apply callbacks via a pointer array" % platform,
                    "gbs_fnptr_0 engine_anim_a_apply[8];" in an
                    and "engine_anim_a_apply[i](i, engine_anim_a_frame[i]);" in an
                    and "engine_anim_set(0, 8, 2, main_hero);" in an)
        # The shmup kit: sprite-vs-sprite overlap, the frame-timer countdown,
        # and the u8-safe rising-projectile despawn test (bool lowers to uint8_t).
        sh = compile_shmup(platform)
        ok &= check("[%s] genre.shmup kit lowers (overlap/cooldown/off_top)" % platform,
                    "uint8_t genre_shmup_overlap(uint8_t" in sh
                    and "uint8_t genre_shmup_cooldown(uint8_t" in sh
                    and "uint8_t genre_shmup_off_top(uint8_t" in sh
                    # the template composes pad (the bomb edge) + the kit
                    and "engine_pad_b()" in sh
                    and "genre_shmup_overlap(" in sh)

    # (c) Tier-A owner modules lower correctly (scene state + verbs, the
    # dialogue state machine with per-backend text coords, the sprite HUD).
    for platform in ('gameboy', 'lynx'):
        out = compile_owner_game(platform)
        ok &= check("[%s] engine.scene owns exported scene-id + overlay state" % platform,
                    "uint8_t engine_scene_cur;" in out
                    and "engine_scene_set(" in out
                    and "engine_scene_enter_overlay(" in out
                    and "engine_scene_leave_overlay(" in out)
        ok &= check("[%s] engine.dialogue state machine + paging" % platform,
                    "uint8_t engine_dialogue_open;" in out
                    and "engine_dialogue_start(" in out
                    and "engine_dialogue_advance(" in out
                    and "engine_dialogue_is_open(" in out)
        ok &= check("[%s] engine.hud draws hearts + icon as sprites" % platform,
                    "engine_hud_hearts(" in out
                    and "engine_hud_icon(" in out
                    and "gbs_move_sprite(" in out)
        # engine.menu (Tier-A UI primitive): cursor state, the clamped Up/Down nav,
        # the A-confirm edge, and the framed text box.
        mn = compile_menu(platform)
        ok &= check("[%s] engine.menu cursor nav + A-confirm edge + framed box"
                    % platform,
                    "uint8_t engine_menu_cursor;" in mn
                    and "engine_menu_nav(" in mn
                    and "engine_menu_confirmed(" in mn
                    and "engine_menu_arm(" in mn
                    and "engine_box_draw_box(" in mn)
    # engine.menu's box is the cross-console gotcha (like dialogue text): it draws on
    # the GBDK/cc65 text layer but is a NES no-op, so a menu game links no printf on
    # the NES (the rulebook requirement).
    ok &= check("engine.menu draws a box on GBDK but is a NES no-op (no printf)",
                "printf" in compile_menu('gameboy')
                and "printf" not in compile_menu('nes'))
    # The text-cell coords are per-backend (the encapsulated gotcha), now factored
    # into engine.box (E1b): GBDK adds the camera tile offset to the scrolling bkg
    # map; the Lynx (and PCE/NES) use fixed screen cells. engine.dialogue.col/row
    # DELEGATE to engine.box.col/row (shared with engine.menu), so dialogue itself
    # stays printf-free and the gotcha lives in one place.
    gb = compile_owner_game('gameboy')
    lx = compile_owner_game('lynx')
    ok &= check("engine.box text coords add the camera offset on GBDK only",
                "return (scroll_cols + sc);" in gb
                and "return (scroll_cols + sc);" not in lx
                and "return sc;" in lx)
    ok &= check("engine.dialogue.col/row delegate to engine.box (shared primitive)",
                "engine_box_col(" in gb and "engine_box_row(" in gb)
    # NES gating: module-level `if platform == "nes"` keeps printf out of the
    # NES build (the rulebook's hard requirement).
    nes = compile_owner_game('nes')
    ok &= check("NES build links no printf (text gated out)",
                "printf" not in nes)

    # The framework is opt-in: a program that imports none of it is unaffected
    # (no game_* symbols leak in).
    bare = MosaikCompiler().compile(
        'module "main" { import "platform.video"\n'
        'function main() { video.enable_lcd() } export main }',
        platform='gameboy')
    ok &= check("framework is opt-in (no leakage when unused)",
                "engine_pad" not in bare and "engine_camera" not in bare)

    # Distribution: the sample projects now consume lib/engine + lib/genre through
    # the shared lib/ search path (no vendoring) -- they must carry NO src/engine/ or
    # src/genre/ copies, so a stray copy can't silently shadow (and drift from) the
    # canonical modules.
    for proj in ("box-pusher", "scene-demo", "platformer"):
        ok &= check("%s has no vendored src/engine|genre/ (uses the lib/ search path)"
                    % proj,
                    not os.path.isdir(os.path.join(ROOT, "projects", proj, "src",
                                                    "engine"))
                    and not os.path.isdir(os.path.join(ROOT, "projects", proj, "src",
                                                        "genre")))

    # projects/vendor-override is the override demo: it vendors a *modified*
    # engine.camera (which must therefore DIFFER from lib/engine/camera.mos) and
    # copies nothing else (pad/collision from lib/engine, topdown from lib/genre).
    vo = os.path.join(ROOT, "projects", "vendor-override", "src", "engine")
    vo_cam = os.path.join(vo, "camera.mos")
    with open(os.path.join(LIB, "engine", "camera.mos"), encoding="utf-8") as f:
        lib_cam = f.read()
    vend_cam = open(vo_cam, encoding="utf-8").read() if os.path.isfile(vo_cam) else None
    ok &= check("vendor-override vendors a MODIFIED camera (differs from lib/engine/)",
                vend_cam is not None and vend_cam != lib_cam)
    ok &= check("vendor-override vendors ONLY camera (pad/collision/topdown from lib)",
                not os.path.isfile(os.path.join(vo, "pad.mos"))
                and not os.path.isfile(os.path.join(vo, "collision.mos"))
                and not os.path.isfile(os.path.join(ROOT, "projects",
                                                   "vendor-override", "src", "genre",
                                                   "topdown.mos")))

    print("=" * 50)
    print("All game-framework checks passed" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
