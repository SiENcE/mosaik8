#!/usr/bin/env python3
"""Game-framework engine modules + the top-down kit (lib/game/).

Phases 1-2 of docs/done/game-framework-plan.md. Tier A (genre-agnostic): `game.pad`
(per-button input edge detection), `game.camera` (a follow camera with exported,
shared camx/camy) and `game.collision` (the pure box-corner test). Tier B (the
top-down genre kit): `game.topdown` (facing / walk-anim / chase helpers) plus
`topdown_template.mos`, the canonical loop that composes them all.

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
LIB = os.path.join(ROOT, "lib", "game")


def lib_module(name):
    with open(os.path.join(LIB, name), encoding="utf-8") as f:
        return (name, f.read())


# The game source under test IS the shipped canonical template -- so the
# copy-me skeleton is guaranteed to stay valid on every console.
def compile_game(platform):
    return MosaikCompiler().compile_program(
        [lib_module('topdown_template.mos'), lib_module('pad.mos'),
         lib_module('camera.mos'), lib_module('collision.mos'),
         lib_module('topdown.mos')],
        platform=platform)


# A minimal game that composes the Tier-A owner modules scene / dialogue / hud
# the way the slice does (these were factored out of projects/zelda-slice). NES
# text gating is module-level (`if platform == "nes"`), the idiom the rulebook
# requires, so this links no printf on the NES.
OWNER_GAME = '''
module "main" {
    import "platform.video"
    import "platform.input"
    import "graphics.sprite"
    import "graphics.text"
    import "game.scene"
    import "game.dialogue"
    import "game.hud"
    import "game.camera"
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
         lib_module('dialogue.mos'), lib_module('hud.mos'),
         lib_module('camera.mos')],
        platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("Game-framework engine modules + top-down kit (lib/game/)")
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

    # (b) Cross-module lowering (checked on a representative GBDK + cc65 pair).
    for platform in ('gameboy', 'lynx'):
        out = compile_game(platform)
        ok &= check("[%s] game.pad edge module lowers (actions + d-pad)" % platform,
                    "void game_pad_update(void)" in out
                    and "game_pad_update();" in out
                    and "game_pad_a()" in out
                    # rising-edge logic: down now, up last frame
                    and "game_pad_cur_a > 0" in out
                    and "game_pad_last_a == 0" in out
                    # d-pad edges (for grid/menu movement, one step per tap)
                    and "game_pad_up(void)" in out and "game_pad_down(void)" in out
                    and "game_pad_left(void)" in out and "game_pad_right(void)" in out)
        ok &= check("[%s] game.camera owns exported, shared scroll state" % platform,
                    "uint8_t game_camera_camx = 0;" in out
                    and "game_camera_follow(" in out
                    # the game reads the exported camera globals directly
                    and "game_camera_camx" in out
                    and "game_camera_camy" in out)
        ok &= check("[%s] game.collision corner test + vendored tile_at" % platform,
                    "game_collision_any_solid(" in out
                    and "tile_at(" in out)
        ok &= check("[%s] game.topdown kit lowers (facing/anim/chase + consts)" % platform,
                    "game_topdown_facing4(" in out
                    and "uint8_t game_topdown_anim2(uint8_t" in out
                    and "uint8_t game_topdown_toward(uint8_t" in out
                    and "#define game_topdown_FACE_LEFT" in out)

    # (c) Tier-A owner modules lower correctly (scene state + verbs, the
    # dialogue state machine with per-backend text coords, the sprite HUD).
    for platform in ('gameboy', 'lynx'):
        out = compile_owner_game(platform)
        ok &= check("[%s] game.scene owns exported scene-id + overlay state" % platform,
                    "uint8_t game_scene_cur = 0;" in out
                    and "game_scene_set(" in out
                    and "game_scene_enter_overlay(" in out
                    and "game_scene_leave_overlay(" in out)
        ok &= check("[%s] game.dialogue state machine + paging" % platform,
                    "uint8_t game_dialogue_open = 0;" in out
                    and "game_dialogue_start(" in out
                    and "game_dialogue_advance(" in out
                    and "game_dialogue_is_open(" in out)
        ok &= check("[%s] game.hud draws hearts + icon as sprites" % platform,
                    "game_hud_hearts(" in out
                    and "game_hud_icon(" in out
                    and "gbs_move_sprite(" in out)
    # The dialogue text-cell coords are per-backend (the encapsulated gotcha):
    # GBDK adds the camera tile offset to the scrolling bkg map; the Lynx (and
    # PCE/NES) use fixed screen cells.
    gb = compile_owner_game('gameboy')
    lx = compile_owner_game('lynx')
    ok &= check("game.dialogue text coords add the camera offset on GBDK only",
                "return (scroll_cols + sc);" in gb
                and "return (scroll_cols + sc);" not in lx
                and "return sc;" in lx)
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
                "game_pad" not in bare and "game_camera" not in bare)

    # Distribution: the sample projects now consume lib/game/ through the shared
    # lib/ search path (no vendoring) -- they must carry NO src/game/ copies, so
    # a stray copy can't silently shadow (and drift from) the canonical modules.
    for proj in ("box-pusher", "scene-demo", "platformer"):
        vdir = os.path.join(ROOT, "projects", proj, "src", "game")
        ok &= check("%s has no vendored src/game/ (uses the lib/ search path)" % proj,
                    not os.path.isdir(vdir))

    # projects/vendor-override is the override demo: it vendors a *modified*
    # game.camera (which must therefore DIFFER from lib/game/camera.mos) and
    # copies nothing else (pad/collision/topdown still come from lib/).
    vo = os.path.join(ROOT, "projects", "vendor-override", "src", "game")
    vo_cam = os.path.join(vo, "camera.mos")
    with open(os.path.join(LIB, "camera.mos"), encoding="utf-8") as f:
        lib_cam = f.read()
    vend_cam = open(vo_cam, encoding="utf-8").read() if os.path.isfile(vo_cam) else None
    ok &= check("vendor-override vendors a MODIFIED camera (differs from lib/game/)",
                vend_cam is not None and vend_cam != lib_cam)
    ok &= check("vendor-override vendors ONLY camera (pad/collision/topdown from lib)",
                not os.path.isfile(os.path.join(vo, "pad.mos"))
                and not os.path.isfile(os.path.join(vo, "collision.mos"))
                and not os.path.isfile(os.path.join(vo, "topdown.mos")))

    print("=" * 50)
    print("All game-framework checks passed" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
