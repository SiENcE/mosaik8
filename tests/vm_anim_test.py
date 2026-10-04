"""Unit tests for mosaik_anim -- the VM8 animation transpiler (studio [animations]
clips -> the generated `anim`/`clips` selector module)."""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mosaik_anim   # noqa: E402


def _exported(src, name):
    """Is `name` in ANY of a module's `export` statements?

    NOT `src.split("export")[-1]`: a module may have several export lines, and
    taking only the last one reads as "not exported" the moment a new one is
    appended below it - which is exactly what W7j and W7c each did to a
    different module, breaking a green assertion for a reason that had nothing
    to do with what it pins.
    """
    return any(name in ln for ln in src.splitlines()
               if ln.strip().startswith("export "))


def _arr(src, name):
    m = re.search(r"const %s: array\[u8, \d+\] = \[ ([^\]]*)\]" % name, src)
    assert m, "array %s not found" % name
    return [int(v) for v in m.group(1).split(",") if v.strip() != ""]


# tiles: cell name -> base tile
CELLS = {"idle_r": 0, "w1": 6, "w2": 12, "jump_r": 18}


def test_directional_with_flip_left():
    """A flip_left clip: LEFT is DERIVED from RIGHT (same frames) + flip=1; the other
    facings fall back to the first non-empty facing."""
    clips = {
        "walk": {"period": 5, "flip_left": True, "right": ["w1", "w2"]},
    }
    src = mosaik_anim.transpile(clips, CELLS, module="t")
    cnt = _arr(src, "F_CNT")
    flip = _arr(src, "F_FLIP")
    per = _arr(src, "PERIOD")
    # state WALK = id 1; facings down/up/left/right = 0/1/2/3
    base = 1 * 4
    assert cnt[base + 3] == 2, "right facing should have 2 frames"
    assert cnt[base + 2] == 2, "left facing derived from right -> 2 frames"
    assert flip[base + 3] == 0, "right facing is not flipped"
    assert flip[base + 2] == 1, "left facing (flip_left-derived) must set flip"
    assert per[1] == 5
    # FRAMES for left == FRAMES for right (same cells)
    off = _arr(src, "F_OFF")
    frames = _arr(src, "FRAMES")
    r = frames[off[base + 3]:off[base + 3] + 2]
    l = frames[off[base + 2]:off[base + 2] + 2]
    assert r == l == [6, 12], "left mirrors the right frames"


def test_flat_clip_all_facings_same():
    """A non-directional (flat `frames`) clip: every facing plays the same frames, no
    flip -- the player-symmetric case."""
    clips = {"idle": {"frames": ["idle_r"]}, "walk": {"period": 6, "frames": ["w1", "w2"]}}
    src = mosaik_anim.transpile(clips, CELLS, module="t")
    cnt = _arr(src, "F_CNT")
    flip = _arr(src, "F_FLIP")
    for f in range(4):
        assert cnt[1 * 4 + f] == 2, "flat walk: all facings 2 frames"
        assert flip[1 * 4 + f] == 0, "flat clip never flips"
    assert cnt[0 * 4 + 0] == 1, "flat idle: 1 frame"


def test_explicit_left_wins_over_flip():
    """Authored left frames are used verbatim (no flip) even if flip_left is set."""
    clips = {"walk": {"flip_left": True, "right": ["w1"], "left": ["w2"]}}
    src = mosaik_anim.transpile(clips, CELLS, module="t")
    off = _arr(src, "F_OFF")
    frames = _arr(src, "FRAMES")
    flip = _arr(src, "F_FLIP")
    base = 1 * 4
    assert flip[base + 2] == 0, "explicit left should not be flipped"
    assert frames[off[base + 2]] == 12, "left uses the authored w2"


def test_unknown_cell_raises():
    try:
        mosaik_anim.transpile({"walk": {"frames": ["nope"]}}, CELLS)
    except mosaik_anim.AnimError:
        return
    raise AssertionError("unknown sprite cell should raise AnimError")


def test_transpile_kinds():
    """The DATA-DRIVEN path: per-KIND tables (kind * 16 + state * 4 + facing)."""
    anims = {"player": {"walk": {"frames": ["w1", "w2"]}},
             "enemy": {"walk": {"flip_left": True, "right": ["w1", "w2"]}}}
    src = mosaik_anim.transpile_kinds(anims, CELLS, {"player": 0, "enemy": 1}, module="clips")
    assert "const KINDS: u8 = 2" in src
    cnt = _arr(src, "F_CNT")
    flip = _arr(src, "F_FLIP")
    # player (kind 0) walk (state 1) right (facing 3): 0*16 + 1*4 + 3 = 7
    assert cnt[7] == 2, "player walk-right should have 2 frames"
    # enemy (kind 1) walk left (facing 2): 1*16 + 1*4 + 2 = 22 -> flip-derived
    assert cnt[22] == 2 and flip[22] == 1, "enemy walk-left mirrors right (flip)"
    assert "frame(kind: u8, state: u8, facing: u8, i: u8)" in src



def test_sparse_frame_masks():
    """SPARSE frames: a per-frame BLANK-COLUMN mask, emitted only when a frame
    really has gaps (a reference-engine metasprite leaves holes where a composed
    rectangle has empty cells - and a blank cell costs a tile AND one of the
    GB's 10 sprites per scanline)."""
    anims = {"hud": {"idle": {"frames": ["w1", "w2"]}}}
    ids = {"hud": 0}
    plain = mosaik_anim.transpile_kinds(anims, CELLS, ids, module="clips")
    assert "F_MSK" not in plain and "frame_mask" not in plain,         "a world of solid rectangles must stay byte-identical"
    # ...and identical to passing an all-dense mask map.
    dense = mosaik_anim.transpile_kinds(anims, CELLS, ids, module="clips",
                                        cell_mask={"w1": 0, "w2": 0})
    assert dense == plain, "an all-zero mask map must not emit the sparse path"
    sparse = mosaik_anim.transpile_kinds(anims, CELLS, ids, module="clips",
                                         cell_mask={"w1": 0b100000})
    assert "frame_mask(kind: u8, state: u8, facing: u8, i: u8) -> u16" in sparse
    assert ", frame_mask" in sparse, "frame_mask must be exported"
    msk = [int(v) for v in re.search(
        r"const F_MSK: array\[u16, \d+\] = \[ ([^\]]*)\]", sparse).group(1).split(",")]
    off = _arr(sparse, "F_OFF")
    # kind 0, idle (state 0), facing down (0): frame 0 is w1 (masked), 1 is w2
    assert msk[off[0]] == 0b100000 and msk[off[0] + 1] == 0


def _arr_in(src, name):
    """Every `const NAME: array[u8,..] = [...]` in order (the bake forks FRAMES/F_FLIP
    into an SMS/GG branch then an else branch, so there are two of each)."""
    return [[int(v) for v in body.split(",") if v.strip() != ""]
            for body in re.findall(
                r"const %s: array\[u8, \d+\] = \[ ([^\]]*)\]" % name, src)]


def test_flip_bake_sms():
    """The SMS/GG soft-flip bake: build_flip_bake mirrors the RIGHT cells a flip_left
    LEFT derives from, and transpile forks the module -- SMS/GG use the baked tiles
    (flip 0), every other console keeps the FLIP_X frames (byte-identical)."""
    import tempfile, shutil
    import mosaik_assets as ma
    tmp = tempfile.mkdtemp(prefix="flipbake_")
    try:
        png = os.path.join(tmp, "sheet.png")
        # two 8x8 cells, "a" left-heavy (asymmetric so the mirror is observably different)
        rows = []
        for y in range(8):
            rows.append([1 if x < 4 else 0 for x in range(8)]      # cell a
                        + [3 if x == 0 else 0 for x in range(8)])  # cell b
        ma.write_png_indexed(png, 16, 8, rows,
                             [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)])
        ma.write_sprite_manifest(png, [("a", [0, 0, 8, 8]), ("b", [8, 0, 8, 8])])
        cell_tile = {n: off for n, off, _w, _h in ma.sheet_sprite_defs(png)}  # a:0 b:1
        anims = {"hero": {"walk": {"flip_left": True, "right": ["a", "b"]}}}

        cells = mosaik_anim._mirror_cells(
            clip for clips in anims.values() for clip in clips.values())
        assert cells == ["a", "b"], "mirror targets are the RIGHT cells of the derived left"
        bake = mosaik_anim.build_flip_bake(cells, [png])
        assert bake["base"] == 2 and bake["index"] == {"a": 2, "b": 3}, \
            "baked tiles sit above the sheet's 2 tiles"

        # the MIRROR bytes must be the exact horizontal flip (reuse the asset encoder)
        _w, _h, sh = ma.png_to_shades(png)
        exp = bytearray()
        for name in ("a", "b"):
            x, y, w, h = {n: (x, y, w, h) for n, x, y, w, h
                          in ma.load_sprite_manifest(png)}[name]
            rect = ma._slice_rows(sh, x, y, w, h)
            exp += ma.shades_to_gb_tiles(w, h, [list(reversed(r)) for r in rect])
        assert bytes(bake["tiles"]) == bytes(exp), "MIRROR is the h-flip of the cells"

        src = mosaik_anim.transpile_kinds(anims, cell_tile, {"hero": 0}, bake=bake)
        assert 'if platform == "sms" or platform == "gamegear"' in src
        assert "sprite.set_data(2, 2, MIRROR)" in src, "upload above the sheet"
        assert ", upload_flip" in src, "upload_flip exported"
        sms_src, _, else_src = src.partition("} else {")
        # walk-left index (kind0 state1 facing2) = 6
        sms_off, sms_fr, sms_fl = (_arr_in(sms_src, "F_OFF"), _arr_in(sms_src, "FRAMES"),
                                   _arr_in(sms_src, "F_FLIP"))
        # F_OFF/F_FLIP live once outside the branch, FRAMES once inside each branch
        off = _arr_in(src, "F_OFF")[0]
        sms_frames = _arr_in(sms_src, "FRAMES")[0]
        else_frames = _arr_in(else_src, "FRAMES")[0]
        sms_flip = _arr_in(sms_src, "F_FLIP")[0]
        else_flip = _arr_in(else_src, "F_FLIP")[0]
        li = off[6]
        assert sms_frames[li:li + 2] == [2, 3], "SMS walk-left uses the baked mirror tiles"
        assert sms_flip[6] == 0, "SMS walk-left is not FLIP_X'd (it's pre-mirrored)"
        assert else_frames[li:li + 2] == [0, 1], "other consoles keep the RIGHT tiles"
        assert else_flip[6] == 1, "other consoles FLIP_X the derived left"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_flip_bake_residency():
    """Under PER-ROOM residency each kind's sheet uploads at its own per-room
    base and the FRAMES are kind-RELATIVE, so the fixed-base boot bake cannot
    serve it (it was skipped, and SMS/GG drew every left-facing converted actor
    unflipped). residency_flip_bake puts each sheet's mirrors right after that
    sheet's OWN tiles; the build appends them there (mirror_cell_tiles) on a
    no-flip console. Pinned: the kind-relative indices, one set of mirrors per
    SHEET, the mirrored sparse mask, no upload_flip in the module, and - the
    one that matters - that mirroring the ENCODED tiles equals mirroring the
    PIXELS, in both tile layouts (row-major, and 8x16 column-major)."""
    import tempfile, shutil
    import mosaik_assets as ma
    tmp = tempfile.mkdtemp(prefix="flipres_")
    try:
        pal = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255)]
        hero = os.path.join(tmp, "spr_hero.png")
        # asymmetric everywhere: every pixel differs from its mirror partner
        ma.write_png_indexed(hero, 32, 16, [[(x * 3 + y + x // 8) % 4
                                             for x in range(32)]
                                            for y in range(16)], pal)
        # h0 = a 2x2 cell (tiles 0..3), h1 = a SPARSE frame 3 columns wide
        # whose LEFT column is blank, stored as its 2 drawn columns (4..7)
        ma.write_sprite_manifest(hero, [("h0", [0, 0, 16, 16]),
                                        ("h1", [16, 0, 16, 16], 3, 0b001)])
        npc = os.path.join(tmp, "spr_npc.png")
        ma.write_png_indexed(npc, 16, 16, [[x % 4 for x in range(16)]
                                           for _ in range(16)], pal)
        ma.write_sprite_manifest(npc, [("n0", [0, 0, 8, 16]), ("n1", [8, 0, 8, 16])])
        anims = {"hero": {"walk": {"flip_left": True, "right": ["h0", "h1"],
                                   "down": ["h0"]}},
                 "npc": {"idle": {"flip_left": True, "right": ["n1"], "down": ["n0"]}},
                 "rock": {"idle": {"frames": ["n0"]}}}
        plan = mosaik_anim.residency_flip_bake(
            anims, {"hero": hero, "npc": npc, "rock": npc})
        assert plan["index"] == {"h0": 8, "h1": 12, "n1": 4}, \
            "each mirror sits after ITS OWN sheet, kind-relative: %r" % plan["index"]
        assert plan["by_stem"]["spr_hero"] == {"extra": 8,
                                               "cells": [(0, 2, 2), (4, 2, 2)]}
        assert plan["by_stem"]["spr_npc"] == {"extra": 2, "cells": [(2, 1, 2)]}, \
            "a sheet shared by two kinds carries ONE set of mirrors"
        assert plan["mask"] == {"h1": 0b100}, \
            "the blank LEFT column of h1 is the blank RIGHT one of its mirror"
        assert "tiles" not in plan, "residency: no pixels in the module"

        cell_tile = {}
        for png in (hero, npc):
            for n, off, _w, _h in ma.sheet_sprite_defs(png):
                cell_tile[n] = off
        src = mosaik_anim.transpile_kinds(anims, cell_tile,
                                          {"hero": 0, "npc": 1, "rock": 2},
                                          bake=plan, cell_mask={"h1": 0b001})
        assert "upload_flip" not in src and "MIRROR" not in src, \
            "the mirrors ride the kind's own sheet upload"
        sms_src, _, else_src = src.partition("} else {")
        off = _arr_in(src, "F_OFF")[0]
        li = off[1 * 4 + 2]                  # hero walk LEFT
        assert _arr_in(sms_src, "FRAMES")[0][li:li + 2] == [8, 12]
        assert _arr_in(else_src, "FRAMES")[0][li:li + 2] == [0, 4]
        masks = [[int(v) for v in b.split(",")] for b in re.findall(
            r"const F_MSK: array\[u16, \d+\] = \[ ([^\]]*)\]", src)]
        assert len(masks) == 2 and masks[0][li + 1] == 0b100 \
            and masks[1][li + 1] == 0b001, "the sparse mask forks: SMS/GG mirrored"

        # The build's mirror of the ENCODED stream == the mirror of the PIXELS.
        _w, _h, rows = ma.png_to_shades(hero)
        for obj16 in (False, True):
            stream = ma.sheet_to_tiles(hero, 2, obj_8x16=obj16)
            for name, (off_t, w, h) in (("h0", (0, 2, 2)), ("h1", (4, 2, 2))):
                x, y, pw, ph = {n: (x, y, w_, h_) for n, x, y, w_, h_
                                in ma.load_sprite_manifest(hero)}[name]
                want = ma.shades_to_gb_tiles(pw, ph, [list(reversed(r)) for r in
                                                      ma._slice_rows(rows, x, y, pw, ph)])
                if obj16:
                    want = ma.reorder_tiles_8x16(want, w, h)
                got = ma.mirror_cell_tiles(stream, off_t, w, h, col_major=obj16)
                assert got == want, "%s mirror, obj16=%s" % (name, obj16)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_no_flip_left_no_bake():
    """A world with no flip_left produces a byte-identical (non-forked) module."""
    anims = {"hero": {"walk": {"frames": ["w1", "w2"]}}}
    plain = mosaik_anim.transpile_kinds(anims, CELLS, {"hero": 0})
    baked = mosaik_anim.transpile_kinds(
        anims, CELLS, {"hero": 0},
        bake=mosaik_anim.build_flip_bake(
            mosaik_anim._mirror_cells([anims["hero"]["walk"]]), []))
    assert "if platform ==" not in plain, "no flip_left -> no platform fork"
    assert plain == baked, "no flip_left -> bake is None -> byte-identical module"


def test_per_kind_metasprite_size():
    """A world that MIXES sprite shapes gets per-kind meta_w/meta_h.

    `sprite.set_meta(base, tile, w, h)` fans w*h OAM objects PER ACTOR, so one
    global size taken as the MAX is catastrophic when the sheet mixes shapes:
    a 7x6 boss in the reference-engine sample made every actor a 42-object metasprite
    against the GB's 40-object limit, and the whole screen rendered as garbage
    (every live OAM entry read tile 0). Measured on the real ROM before/after.

    Emitted ONLY when the sizes actually differ, so a uniform world keeps the
    single size the shell passes to canim.set_clips and stays byte-identical.
    """
    anims = {"player": {"walk": {"frames": ["w1", "w2"]}},
             "enemy": {"walk": {"frames": ["w1"]}}}
    ids = {"player": 0, "enemy": 1}

    # UNIFORM: no per-kind tables, no extra exports.
    same = mosaik_anim.transpile_kinds(anims, CELLS, ids, module="clips",
                                       kind_size={"player": (2, 2),
                                                  "enemy": (2, 2)})
    assert "META_W" not in same, "a uniform world must stay byte-identical"
    assert not _exported(same, "meta_w")

    # MIXED: tables + selectors + exports.
    mix = mosaik_anim.transpile_kinds(anims, CELLS, ids, module="clips",
                                      kind_size={"player": (2, 2),
                                                 "enemy": (7, 6)})
    assert "META_W" in mix and "META_H" in mix, "mixed sizes need per-kind tables"
    mw = _arr(mix, "META_W")
    mh = _arr(mix, "META_H")
    assert mw[0] == 2 and mh[0] == 2, "player keeps its own 2x2 (%s,%s)" % (mw[0], mh[0])
    assert mw[1] == 7 and mh[1] == 6, "the big kind keeps 7x6, and only IT does"
    assert "function meta_w(kind: u8)" in mix
    assert _exported(mix, "meta_w") and _exported(mix, "meta_h"),         "selectors must be exported"

    # No size info at all -> unchanged (the caller may not know sizes).
    none = mosaik_anim.transpile_kinds(anims, CELLS, ids, module="clips")
    assert "META_W" not in none and none == same, "no sizes == uniform sizes"
    print("  per-kind metasprite size: uniform stays flat, mixed forks OK")


def run():
    test_per_kind_metasprite_size()
    test_directional_with_flip_left()
    test_flat_clip_all_facings_same()
    test_explicit_left_wins_over_flip()
    test_unknown_cell_raises()
    test_transpile_kinds()
    test_sparse_frame_masks()
    test_flip_bake_sms()
    test_flip_bake_residency()
    test_no_flip_left_no_bake()
    print("vm_anim_test: OK")


if __name__ == "__main__":
    run()
