#!/usr/bin/env python3
"""The busiest room's sprite need (Lynx / PC Engine) and the generators' `hot`.

A residency upload's VRAM base is a runtime value, so no compile-time scan can
bound a cc65 console's sprite tile table or slot pool; the rooms generator,
which knows every room's kinds, states it. Pinned here:

  * `_cc65_sprite_need`: None unless the project targets the Lynx or the PCE;
    the worst room = the player's sheet + each distinct placed kind, a
    `[kind_variants]` placeholder counting as its LARGEST variant; slots = the
    drawn fans;
  * emit: `SPR_TILE_NEED` / `SPR_SLOT_NEED` beside KT only when stated, and a
    PCE `OAM_SLOTS` arm only past 40 (the engine's own number);
  * codegen: the shaker keeps the consts; the PCE engine grows its table +
    slots to them and moves the patterns to VRAM $5000 past 64 tiles; without
    the consts the 40 / $3000 layout is unchanged;
  * `mosaik.hotmark`: `hot` on a generated module's per-frame functions only
    for a project that targets the Lynx AND lists `[build] code_banks`.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler  # noqa: E402
from mosaik_vm.rooms import emit_rooms_mos  # noqa: E402

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _project(root, targets, banks=None):
    with open(os.path.join(root, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write("[project]\nname = \"x\"\ntarget_platforms = [%s]\n"
                % ", ".join('"%s"' % t for t in targets))
        if banks is not None:
            f.write("\n[build]\ncode_banks = [%s]\n"
                    % ", ".join('"%s"' % b for b in banks))


def test_need():
    print("\n[_cc65_sprite_need]")
    import mosaik_assets
    from mosaik_vm.rooms.generate import _cc65_sprite_need
    defs = {"p": [("p", 0, 2, 2)], "npc": [("n", 0, 2, 2)],
            "big": [("b", 0, 7, 8)], "small": [("s", 0, 4, 4)]}
    real = mosaik_assets.sheet_sprite_defs
    mosaik_assets.sheet_sprite_defs = (
        lambda png: defs.get(os.path.splitext(os.path.basename(png))[0], []))
    try:
        kmap = {"player": 0, "npc": 1, "foe": 2, "big": 3, "small": 4}
        kt = [24, 24, 0, 56, 16]
        stems = {0: "p", 1: "npc", 3: "big", 4: "small"}
        world = {"kind_variants": {"foe": {"var": "v", "kinds": ["small", "big"]}},
                 "scene": [{"object": [{"kind": "player"}, {"kind": "npc"},
                                       {"kind": "npc"}]},
                           {"object": [{"kind": "foe"}]}]}
        with tempfile.TemporaryDirectory() as root:
            _project(root, ["gameboy", "sms"])
            check(_cc65_sprite_need(root, world, kmap, kt, stems, None) is None,
                  "no Lynx / PCE target: nothing stated")
            _project(root, ["gameboy", "pce"])
            got = _cc65_sprite_need(root, world, kmap, kt, stems, None)
            # town: player 24 + npc 24 (once) = 48 tiles, 4 + 4 + 4 slots;
            # battle: player 24 + the LARGEST variant 56 = 80, 4 + 56 slots
            check(got == (80, 60), "the busiest room, variants at their largest: %r"
                  % (got,))
            _project(root, ["lynx"])
            check(_cc65_sprite_need(root, world, kmap, kt, stems, None) == (80, 60),
                  "the Lynx is stated too")
    finally:
        mosaik_assets.sheet_sprite_defs = real


BASE = {"types": ["topdown"], "uniform": True, "has_collision": True,
        "has_objects": True, "has_player_kind": True, "has_entity": True,
        "uses_projectile": True}
CLIPS = {"meta_w": 2, "meta_h": 2, "per_kind_size": True, "flip": False,
         "player": True}
RES = {"nk": 3, "kt": [12, 4, 8],
       "stems": {0: "spr_player", 1: "spr_duck", 2: "spr_cat"},
       "player_stem": "spr_player"}


def test_emit():
    print("\n[emit]")
    plain = emit_rooms_mos(dict(BASE, residency=dict(RES), clips=CLIPS))
    big = emit_rooms_mos(dict(BASE, residency=dict(RES, spr_need=(80, 60)),
                              clips=CLIPS))
    small = emit_rooms_mos(dict(BASE, residency=dict(RES, spr_need=(30, 12)),
                                clips=CLIPS))
    check("SPR_TILE_NEED" not in plain and 'platform == "pce"' not in plain,
          "no need stated: nothing new emitted")
    check("const SPR_TILE_NEED: u16 = 80" in big
          and "const SPR_SLOT_NEED: u8 = 60" in big, "the consts are emitted")
    check('} else if platform == "pce" {\n        const OAM_SLOTS: u8 = 60' in big,
          "a PCE OAM_SLOTS arm with the engine's own number")
    check('platform == "pce"' not in small and "const SPR_SLOT_NEED: u8 = 12" in small,
          "no PCE arm at or under 40 slots")


def _compile(src, platform="pce", **kw):
    c = MosaikCompiler().compile_program([("main.mos", src)], platform=platform, **kw)
    assert not c.startswith("Compilation error"), c
    return c


PROG = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
%s
    function main() {
        sprite.move(0, 10, 10)
        video.enable_lcd()
    }
}
'''


def test_codegen():
    print("\n[codegen]")
    need = ("    const SPR_TILE_NEED: u16 = %d\n"
            "    const SPR_SLOT_NEED: u8 = %d")
    c0 = _compile(PROG % "")
    check("#define GBS_MAX_TILES   40\n" in c0 and "#define GBS_MAX_SPRITES 40 " in c0
          and "#define GBS_VRAM_TILES 0x3000u" in c0, "no need: the 40 / $3000 layout")
    c1 = _compile(PROG % (need % (80, 60)), shake_exports=True)
    check("#define GBS_MAX_TILES   80 " in c1 and "#define GBS_MAX_SPRITES 60 " in c1
          and "#define GBS_VRAM_TILES 0x5000u" in c1,
          "80 tiles / 60 slots: the table + slots grow, patterns move to $5000 "
          "(and the shaker kept the consts)")
    c2 = _compile(PROG % (need % (60, 30)))
    check("#define GBS_MAX_TILES   60 " in c2 and "#define GBS_MAX_SPRITES 40 " in c2
          and "#define GBS_VRAM_TILES 0x3000u" in c2,
          "60 tiles still fit below $4000: the pattern area stays")
    c3 = _compile(PROG % (need % (300, 200)))
    check("#define GBS_MAX_TILES   188 " in c3 and "#define GBS_MAX_SPRITES 64 " in c3,
          "capped at the free VRAM (188 patterns) and the SATB (64)")


def test_hotmark():
    print("\n[hotmark]")
    from mosaik.hotmark import lynx_overlays, mark_hot
    text = ("    function tick_topdown() {\n    }\n"
            "    function load_room(rm: u8) {\n    }\n"
            "    local function tile_at(c: u16, r: u16) -> u8 {\n    }\n"
            "    bank(0) function solid_at(x: u16) -> bool {\n    }\n")
    with tempfile.TemporaryDirectory() as root:
        _project(root, ["gameboy", "lynx"])
        check(not lynx_overlays(root) and mark_hot(text, "rooms", root) == text,
              "the Lynx without code_banks: unchanged")
        _project(root, ["gameboy", "pce"], banks=["rooms"])
        check(mark_hot(text, "rooms", root) == text,
              "code_banks without the Lynx: unchanged")
        _project(root, ["gameboy", "lynx"], banks=["rooms"])
        got = mark_hot(text, "rooms", root)
        check("    hot function tick_topdown() {" in got
              and "    hot local function tile_at(" in got,
              "the per-frame functions are marked")
        check("    function load_room(" in got and "hot function load_room" not in got,
              "a cold function is not")
        check("    bank(0) function solid_at(" in got,
              "a pinned function is left alone")
        check(mark_hot(got, "rooms", root) == got, "idempotent")
        sub = os.path.join(root, "assets", "world")
        os.makedirs(sub)
        check(lynx_overlays(sub), "a world two levels down finds its project")


if __name__ == "__main__":
    print("=" * 60)
    print("cc65 sprite need + generated hot marks")
    print("=" * 60)
    test_need()
    test_emit()
    test_codegen()
    test_hotmark()
    print("\n" + "=" * 60)
    if _FAILED:
        print("%d FAILED" % len(_FAILED))
        sys.exit(1)
    print("all passed")
