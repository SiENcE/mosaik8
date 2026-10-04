#!/usr/bin/env python3
"""THE PER-OBJECT METASPRITE DESCRIPTOR (the reference engine's metasprite_t model).

Fidelity item 1.3's tile-dedupe half: a dense WxH frame cannot say "tile 3
then tile 7", which is exactly how the reference engine draws a NUMBER - the shooter
conversion's score is an actor wearing frame `score % 100` out of a 100-frame sprite whose
frames dedupe to TEN digit cells. Dense, that art was 400 tiles against the
GB's 124, so the conversion kept the handful that fit and the score stopped
counting at 7.

The chain, each stage pinned here:

  sprite.set_meta_list(base, pw, tile, data, off, n)   -- the primitive:
      n entries of (dy, dx, dtile, props); rows may OVERLAP at authored
      offsets and dtile may REPEAT. Emitted only when called.
  manifest [[frame]] entries                            -- named compositions
      over a sheet's [[sprite]] pool cells (append_frame_descs /
      sprite_frame_descs).
  clips DESC/D_OFF/D_CNT + draw()/is_desc()/fan()       -- mosaik_anim, with a
      per-platform fork under obj16 (one 8x16 object per cell on the GB
      family, two stacked 8x8 objects elsewhere) and a VALUE-DEDUPED blob (a
      non-directional clip repeats each frame once per facing).
  vm.canim set_clip_draw seam + rooms.py wiring          -- desc kinds route
      the upload through clips.draw; folded out by VM_META_LIST.

THE COST RULE (measured, the reason the importer GATES it): the resident
prelude machinery is ~1.1 KB of bank 0 (`gbs_move` +537 B, `gbs_set_` +532 B
on the GBC conversion, which had 94 B spare). So descriptors switch on ONLY
when the dense trim would actually cap a strip - the reference-engine sample conversion (whose pinned
strips fit densely) stays byte-identical, the shooter conversion (score capped at 7)
opts in and counts to 99. ROM-proven: the PyBoy aim-bot shot the score to 8
(nine distinct digit tiles on the units display), and the lives counter's two
hearts share ONE pooled tile.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_primitive_gating():
    print("\n[sprite.set_meta_list: emitted only when called]")
    from mosaik.compiler import MosaikCompiler
    plain = ('module "main" {\n'
             '    import "graphics.sprite"\n'
             '    function main() {\n'
             '        sprite.set_meta(0, 8, 2, 2)\n'
             '        sprite.move(0, 40, 40)\n'
             '    }\n'
             '}\n')
    listy = plain.replace(
        "sprite.set_meta(0, 8, 2, 2)",
        'sprite.set_meta(0, 8, 2, 2)\n'
        '        sprite.set_meta_list(0, 16, 8, D, 0, 1)').replace(
        'function main', 'const D: array[u8, 4] = [ 0, 0, 2, 0 ]\n'
                         '    function main')
    for plat in ("gameboy", "sms", "lynx"):
        a = MosaikCompiler().compile_program([("m.mos", plain)], platform=plat)
        b = MosaikCompiler().compile_program([("m.mos", listy)], platform=plat)
        check("gbs_set_metasprite_list" not in a,
              "%s: a non-user emits NO list machinery (byte-identical)" % plat)
        check("gbs_set_metasprite_list" in b and "gbs_meta_dx" in b,
              "%s: a user gets the setter + the per-child offset tables" % plat)
    gb = MosaikCompiler().compile_program([("m.mos", listy)],
                                          platform="gameboy")
    check("if (w == 0xFF)" in gb, "the move fan has the list arm")
    check("y >= SCREEN_HEIGHT" in gb,
          "a hidden base parks children (200 + a big dy would wrap on screen)")


def test_manifest_roundtrip():
    print("\n[manifest [[frame]] descriptors round-trip]")
    import mosaik_assets
    with tempfile.TemporaryDirectory() as td:
        png = os.path.join(td, "pool.png")
        rows = [[0] * 8 for _ in range(32)]
        mosaik_assets.write_png_indexed(png, 8, 32, rows,
                                        [(255, 0, 255), (170, 170, 170),
                                         (85, 85, 85), (0, 0, 0)])
        mosaik_assets.write_sprite_manifest(png, [
            ("k_c0", [0, 0, 8, 16]), ("k_c1", [0, 16, 8, 16])])
        mosaik_assets.append_frame_descs(png, [
            ("k_f0", 16, 16, [(0, 0, 0, 0), (0, 8, 0, 0)]),   # cell 0 twice
            ("k_f1", 16, 16, [(12, 4, 1, 0)]),                # overlap row
        ])
        descs = mosaik_assets.sprite_frame_descs(png)
        check(descs["k_f0"] == (16, 16, [(0, 0, "k_c0", 0), (0, 8, "k_c0", 0)]),
              "objs resolve cell INDICES to pool cell NAMES")
        check(descs["k_f1"][2][0][:2] == (12, 4),
              "authored offsets survive verbatim (the row overlap)")
        defs = mosaik_assets.sheet_sprite_defs(png)
        check([d[0] for d in defs] == ["k_c0", "k_c1"],
              "[[frame]] entries do not disturb the pool's tile offsets")


def test_clips_emission():
    print("\n[mosaik_anim: DESC emission, fork, dedupe]")
    from mosaik_anim import transpile_kinds
    anims = {"digit": {"idle": {"frames": ["d_f0", "d_f1"], "period": 255}}}
    cell_tile = {"d_c0": 4, "d_c1": 6}
    cd = {"d_f0": (16, 16, [(0, 0, "d_c0", 0), (0, 8, "d_c0", 0)]),
          "d_f1": (16, 16, [(12, 0, "d_c1", 0)])}
    out = transpile_kinds(anims, cell_tile, {"digit": 0},
                          kind_size={"digit": (2, 2)}, cell_desc=cd,
                          obj16=True)
    # The guard names `platforms.OBJ16_CONSOLES` - the ONE set the codegen,
    # the asset reorder and the generated rooms.mos read - so ask THAT rather
    # than spell a console list here, or the test pins a stale set.
    from mosaik.platforms import obj16_guard
    guard = obj16_guard("    ")
    check(guard in out and "} else {" in out,
          "obj16 forks the blob per platform")
    check("function draw(" in out and "sprite.set_meta_list(" in out,
          "draw() walks the blob through the primitive (in-module, so it banks)")
    check("function is_desc(" in out and "function fan(" in out,
          "is_desc + fan selectors emitted")
    # blob dedupe: 4 facings x 2 frames but only 2 DISTINCT frames -> the
    # 8x16 arm's DESC is 2 frames x entries (8 + 4 bytes), not 4x that
    arm16 = out.split(guard)[1].split("} else {")[0]
    import re
    m = re.search(r"DESC: array\[u8, (\d+)\]", arm16)
    check(m and int(m.group(1)) == 12,
          "the blob is VALUE-DEDUPED across facings (12 B, not 48)")
    # no descs -> byte-identical surface (no DESC/draw at all)
    plain = transpile_kinds(anims, {"d_f0": 0, "d_f1": 1}, {"digit": 0},
                            kind_size={"digit": (2, 2)})
    check("DESC" not in plain and "function draw(" not in plain,
          "a world without descriptors emits none of it")


def test_rooms_and_canim_lockstep():
    print("\n[rooms wiring + canim seam]")
    from mosaik_vm.rooms import emit_rooms_mos
    base = {"types": ["topdown"], "uniform": True, "has_collision": True,
            "has_objects": True, "has_player_kind": True, "has_entity": True}
    clips = {"meta_w": 2, "meta_h": 2, "per_kind_size": True, "desc": True,
             "frame_mask": False, "flip": False, "player": True}
    src = emit_rooms_mos(dict(base, clips=clips))
    check("canim.set_clip_draw(clips.draw, clips.is_desc)" in src,
          "rooms wires the draw seam for a desc world")
    check("clips.fan(" in src,
          "the OAM fan cost reads clips.fan (desc-aware, 8x8-object units)")
    src2 = emit_rooms_mos(dict(base, clips=dict(clips, desc=False)))
    check("set_clip_draw" not in src2 and "clips.fan(" not in src2,
          "a dense world keeps the old fan math (byte-identical)")
    canim = _read("lib", "vm", "canim.mos")
    check("if VM_META_LIST {" in canim and "g_isdesc(" in canim,
          "canim's desc path folds behind the build-stated VM_META_LIST")
    check("a_uclip" in canim,
          "the upload cache keys on the CLIP too (a desc frame's value is an "
          "identity, not a tile)")


def main():
    test_primitive_gating()
    test_manifest_roundtrip()
    test_clips_emission()
    test_rooms_and_canim_lockstep()
    print()
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("meta_desc_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
