"""Per-FRAME sprite palettes: the clips table, vm.canim's write, and the OAM
property byte that carries both a CGB palette and the DMG OBP select.

The reference engine keeps a palette on every metasprite TILE. Read at the FRAME level
that one field does two things nothing else here could say: it recolours a
whole sprite as its animation runs (an exploding mine turns orange) and, on a
DMG where there is no colour to change, it flips a sprite between OBP0 and OBP1
to make it FLASH. Both lower to one number per frame, spelled exactly as GB
Studio's own compiler spells a metasprite's props.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mosaik_anim                                   # noqa: E402
from mosaik import MosaikCompiler                    # noqa: E402

CELLS = {"a": 0, "b": 2, "c": 4}


def _arr(src, name):
    m = re.search(r"const %s: array\[u8, \d+\] = \[ ([^\]]*)\]" % name, src)
    assert m, "array %s not found" % name
    return [int(v) for v in m.group(1).split(",") if v.strip() != ""]


def test_no_palette_emits_no_table():
    """The byte-identical rule: a world that colours nothing must not gain a
    table, a selector or an export - and so never links the palette write."""
    src = mosaik_anim.transpile_kinds(
        {"k": {"idle": {"period": 8, "frames": ["a", "b"]}}}, CELLS, {"k": 0})
    assert "F_PAL" not in src
    assert "frame_pal" not in src


def test_per_frame_palette_table():
    src = mosaik_anim.transpile_kinds(
        {"k": {"idle": {"period": 8, "frames": ["a", "b"], "pal": [5, 4]}}},
        CELLS, {"k": 0})
    assert "function frame_pal(" in src
    assert ", frame_pal" in src, "the selector must be exported"
    pal = _arr(src, "F_PAL")
    off = _arr(src, "F_OFF")
    # a flat clip fills every facing; each reads back its own two frames
    for facing in range(4):
        o = off[0 * 16 + 0 * 4 + facing]
        assert pal[o:o + 2] == [5, 4], (facing, pal[o:o + 2])


def test_a_scalar_palette_colours_the_whole_clip():
    """`pal = 6` is how a sprite STATE recolours: one value, every frame."""
    src = mosaik_anim.transpile_kinds(
        {"k": {"idle": {"period": 8, "frames": ["a", "b", "c"], "pal": 6}}},
        CELLS, {"k": 0})
    off = _arr(src, "F_OFF")[0]
    assert _arr(src, "F_PAL")[off:off + 3] == [6, 6, 6]


def test_a_derived_left_wears_the_right_palettes():
    """A flip_left LEFT draws RIGHT's art, so it must draw RIGHT's colours -
    the palette follows the frames, not the facing that asked for them."""
    src = mosaik_anim.transpile_kinds(
        {"k": {"walk": {"period": 5, "flip_left": True,
                        "right": ["a", "b"], "pal_right": [3, 6]}}},
        CELLS, {"k": 0})
    pal, off = _arr(src, "F_PAL"), _arr(src, "F_OFF")
    right = off[0 * 16 + 1 * 4 + 3]
    left = off[0 * 16 + 1 * 4 + 2]
    assert pal[right:right + 2] == [3, 6]
    assert pal[left:left + 2] == [3, 6], "the derived left keeps right's colours"


def test_an_uncoloured_kind_reads_the_no_palette_sentinel():
    """THE REGRESSION THIS FILE EXISTS TO HOLD: once ANY kind is recoloured,
    F_PAL exists for every kind - and an uncoloured kind's entries must be 255
    (the no-palette sentinel), never 0. 0 is a REAL palette: written over an
    uncoloured actor it flattens the per-CELL map the room load applied, which
    is exactly how recolouring the savepoint turned the parallax room's big
    actor (per-cell palette 6) and the player (its 0/1/2 row) palette-0 flat. vm.canim
    stands down on 255 and leaves the room-load colour alone."""
    src = mosaik_anim.transpile_kinds(
        {"k": {"idle": {"period": 8, "frames": ["a", "b"], "pal": [5, 4]}},
         "plain": {"idle": {"period": 8, "frames": ["c"]}}},
        CELLS, {"k": 0, "plain": 1})
    pal, off = _arr(src, "F_PAL"), _arr(src, "F_OFF")
    o = off[1 * 16]                     # kind `plain`, idle, facing down
    assert pal[o] == 255, "an uncoloured frame must read 255, got %d" % pal[o]
    ok = off[0 * 16]
    assert pal[ok:ok + 2] == [5, 4], "the coloured kind keeps its values"


def test_a_short_palette_list_pads_by_repeating():
    src = mosaik_anim.transpile_kinds(
        {"k": {"idle": {"period": 8, "frames": ["a", "b", "c"], "pal": [1]}}},
        CELLS, {"k": 0})
    off = _arr(src, "F_OFF")[0]
    assert _arr(src, "F_PAL")[off:off + 3] == [1, 1, 1]


def _c(defines):
    """Compile a program that calls sprite.set_palette behind VM_CLIP_PAL, the
    build-stated flag vm.canim's palette write is folded by."""
    src = '''module "main" {
    import "graphics.sprite"
    function main() {
        if VM_CLIP_PAL {
            sprite.set_palette(0, 1)
        }
        sprite.set_meta(0, 0, 2, 2)
        sprite.move(0, 10, 10)
    }
    export main
}
'''
    return MosaikCompiler().compile_program([("t.mos", src)],
                                            platform="gameboy",
                                            defines=defines)


def test_the_palette_engine_is_folded_out_for_a_non_user():
    """`sprite.set_palette` sits in vm.canim's library source either way, and a
    by-use scan cannot tell the call is unreachable - it would turn on the whole
    sprite-palette prelude (and make set_meta/set_prop merge through
    GBS_KEEP_PAL) in every animated game. VM_CLIP_PAL is what states it, exactly
    as VM_META_MASK states the sparse upload."""
    assert "gbs_sprite_palette" not in _c({"VM_CLIP_PAL": False})
    assert "gbs_sprite_palette" in _c({"VM_CLIP_PAL": True})


def test_the_prop_byte_carries_the_cgb_palette_and_the_dmg_select():
    """The reference engine's `makeProps`: bits 0-2 the CGB OBJ palette, bit 4 (S_PALETTE)
    the DMG OBP0/OBP1 select. They are INDEPENDENT authored fields - a sprite on
    CGB palette 3 is on OBP0 unless its own `objPalette` says otherwise - so the
    old `slot & 1` rule was wrong in both directions: it forced OBP1 on every
    odd palette and gave nothing a way to ASK for OBP1."""
    c = _c({"VM_CLIP_PAL": True})
    m = re.search(r"static uint8_t gbs_pal_prop\(uint8_t prop, uint8_t slot\)"
                  r"\s*\{(.*?)\n\}", c, re.S)
    assert m, "gbs_pal_prop not emitted"
    body = m.group(1)
    assert "slot & 1" not in body, "the odd-slot heuristic must be gone"
    assert "S_PALETTE | 0x07" in body


def run():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("  %s OK" % name[5:].replace("_", " "))
    print("frame_palette_test: all passed")


if __name__ == "__main__":
    run()
