#!/usr/bin/env python3
"""The SMS/GG soft-flip bake under PER-ROOM sprite residency, build side.

SMS and Game Gear have no hardware sprite flip, so a `flip_left` clip's LEFT
facing needs real mirrored tiles there. Under per-room residency every kind's
sheet uploads at its own per-room VRAM base and the clips frames are
kind-RELATIVE, so the fixed-base boot bake cannot serve it (it was skipped, and
every left-facing actor of a reference-engine conversion drew unmirrored on SMS/GG).
Instead `mosaik8_build._bake_soft_flip` appends each flip_left kind's mirrored
RIGHT cells to the END of that kind's own sheet data on a no-flip console, so
they ride the kind's ordinary upload and `<sheet>_tile_count` grows there only.

Pinned here: SMS/GG grow by exactly the planner's tiles (the same planner the
clips and rooms generators read, `mosaik_anim.residency_flip_bake`), in the
build's own tile layout (8x16 column-major included); the GB family and a
boot-upload world are returned untouched, byte for byte. The mirror bytes
themselves are pinned against a pixel mirror in tests/vm_anim_test.py.

    python tests/soft_flip_bake_test.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_assets as ma          # noqa: E402
from mosaik8_build import MosaikBuilder, BuildConfig   # noqa: E402

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _project(tmp, residency=True, obj16=False):
    os.makedirs(os.path.join(tmp, "assets"))
    with open(os.path.join(tmp, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "p"\ntarget_platforms = ["gameboy", "sms"]\n'
                '[source]\nfolder = "src/"\n[build]\n%s[assets]\n'
                'sprites = ["assets/spr_hero.png", "assets/spr_rock.png"]\n'
                % ("obj_8x16 = true\n" if obj16 else ""))
    pal = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
    hero = os.path.join(tmp, "assets", "spr_hero.png")
    ma.write_png_indexed(hero, 16, 48, [[(x * 3 + y) % 4 for x in range(16)]
                                        for y in range(48)], pal)
    ma.write_sprite_manifest(hero, [("h_down", [0, 0, 16, 16]),
                                    ("h_right", [0, 16, 16, 16]),
                                    ("h_step", [0, 32, 16, 16])])
    rock = os.path.join(tmp, "assets", "spr_rock.png")
    ma.write_png_indexed(rock, 16, 16, [[x % 4 for x in range(16)]
                                        for _ in range(16)], pal)
    ma.write_sprite_manifest(rock, [("r0", [0, 0, 16, 16])])
    with open(os.path.join(tmp, "studio.toml"), "w", encoding="utf-8") as f:
        f.write(('[sprites]\nresidency = "room"\n' if residency else "")
                + '[kind_sprites]\nhero = "spr_hero"\nrock = "spr_rock"\n'
                '[animations.hero.walk]\nflip_left = true\n'
                'down = ["h_down"]\nright = ["h_right", "h_step"]\n'
                '[animations.rock.idle]\nframes = ["r0"]\n')
    b = MosaikBuilder()
    b.config = BuildConfig(os.path.join(tmp, "mosaik.toml"))
    paths = [hero, rock]
    return b, paths


def _run(residency=True, obj16=False):
    tmp = tempfile.mkdtemp(prefix="softflip_")
    try:
        b, paths = _project(tmp, residency, obj16)
        got = {}
        for plat in ("gameboy", "sms", "gamegear"):
            assets = ma.load_assets(paths, 2, obj_8x16=b._obj16_for(plat))
            got[plat] = (assets, b._bake_soft_flip(assets, tmp, plat))
        return got, paths
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    print("soft-flip bake under per-room residency (build side)")
    for obj16 in (False, True):
        got, _p = _run(obj16=obj16)
        before, after = got["gameboy"]
        check(after == before,
              "obj16=%s: the GB family is untouched (hardware flip)" % obj16)
        for plat in ("sms", "gamegear"):
            before, after = got[plat]
            b = dict((n, d) for n, d, _bpp in before)
            a = dict((n, d) for n, d, _bpp in after)
            # h_right (tiles 4..7) and h_step (8..11) are the RIGHT cells
            want = b["spr_hero"] + b"".join(
                ma.mirror_cell_tiles(b["spr_hero"], s, 2, 2, col_major=obj16)
                for s in (4, 8))
            check(a["spr_hero"] == want,
                  "obj16=%s %s: the hero sheet gains its two mirrored RIGHT "
                  "cells at its END (12 -> %d tiles)"
                  % (obj16, plat, len(a["spr_hero"]) // 16))
            check(len(a["spr_hero"]) // 16 == 20, "...8 extra tiles")
            check(a["spr_rock"] == b["spr_rock"],
                  "obj16=%s %s: a kind with no flip_left is untouched"
                  % (obj16, plat))
    got, _p = _run(residency=False)
    before, after = got["sms"]
    check(after == before,
          "a boot-upload world is untouched (its bake lives in clips.mos)")
    print("\n" + ("All soft_flip_bake checks passed" if not _FAILED
                  else "%d FAILED" % len(_FAILED)))
    return 0 if not _FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
