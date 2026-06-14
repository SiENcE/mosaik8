#!/usr/bin/env python3
"""Game-framework engine modules + the top-down kit (lib/game/).

Phases 1-2 of docs/game-framework-plan.md. Tier A (genre-agnostic): `game.pad`
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

    # The framework is opt-in: a program that imports none of it is unaffected
    # (no game_* symbols leak in).
    bare = MosaikCompiler().compile(
        'module "main" { import "platform.video"\n'
        'function main() { video.enable_lcd() } export main }',
        platform='gameboy')
    ok &= check("framework is opt-in (no leakage when unused)",
                "game_pad" not in bare and "game_camera" not in bare)

    # The distribution model is vendored source: projects copy lib/game/ into
    # their src/game/. Those copies must stay byte-identical to the canonical
    # lib/game/ (until the planned lib/ search path lands), so drift is caught.
    vendored = {
        "box-pusher": ("pad.mos", "camera.mos", "collision.mos", "topdown.mos"),
        "scene-demo": ("camera.mos", "collision.mos"),
        "platformer": ("pad.mos", "camera.mos", "collision.mos", "platformer.mos"),
    }
    for proj, names in vendored.items():
        vdir = os.path.join(ROOT, "projects", proj, "src", "game")
        for name in names:
            with open(os.path.join(LIB, name), encoding="utf-8") as f:
                canonical = f.read()
            vpath = os.path.join(vdir, name)
            vend = open(vpath, encoding="utf-8").read() if os.path.isfile(vpath) else None
            ok &= check("%s vendored %s matches lib/game/" % (proj, name),
                        vend == canonical)

    print("=" * 50)
    print("All game-framework checks passed" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
