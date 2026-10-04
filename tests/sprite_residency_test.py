#!/usr/bin/env python3
"""Per-room sprite residency + the dynamic OAM layout (generate_rooms).

Two coupled mechanisms, both born from the reference-engine import:

1. DYNAMIC OAM BASES. vm.actor's default layout is one OAM slot per pool
   index (stride 1). A multi-tile metasprite world under that layout has
   every fan OVERLAPPING its neighbours' slots, each owner re-asserting per
   frame - the "sprites wrong AND flickering" bug (measured: a 2x4 NPC
   fan in slots 5..12 fighting the player's 2x2 in 8..11). The generated
   load_room instead packs each actor's fan at a cumulative base (player fan
   first at 0), parks overflow at actor.NO_OAM, and moves the projectile
   block above the watermark.

2. PER-ROOM SPRITE RESIDENCY (studio.toml [sprites] residency = "room").
   One sheet PER KIND; load_room uploads only the kinds THIS room places, at
   a cumulative VRAM base - so the world's art is unbounded and only the
   busiest single ROOM must fit the GB's 128 exclusive OBJ tiles. The clips
   FRAMES are then kind-RELATIVE and vm.canim adds each actor's base
   (activate's tile, read back via actor.tile_of).

Contract pinned here:
  * a single-tile-clip world emits NEITHER (byte-identical: no set_base, no
    OAM sweep, no allocator).
  * a multi-tile world emits the OAM allocator (fan packing + NO_OAM + the
    room-load OAM sweep + player.set_base(0)).
  * residency info additionally emits KT/kvb/upload_kind + the VRAM
    allocator + canim.set_frames_rel(), and activate() seeds the kind's
    per-room VRAM base as the actor's tile.
  * projectiles re-base above the watermark only when both wired.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik_vm.rooms import emit_rooms_mos

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


BASE = {"types": ["topdown"], "uniform": True, "has_collision": True,
        "has_objects": True, "has_player_kind": True, "has_entity": True}


def test_single_tile_world_is_unchanged():
    print("\n[single-tile world]")
    src = emit_rooms_mos(dict(BASE, clips={"meta_w": 1, "meta_h": 1,
                                           "per_kind_size": False,
                                           "flip": False, "player": True}))
    check("set_base" not in src, "no OAM allocator (stride 1 is correct)")
    check("sprite.move(s, 200, 200)" not in src, "no room-load OAM sweep")
    check("upload_kind" not in src, "no VRAM allocator")
    check("set_frames_rel" not in src, "no relative-frames mode")


def test_multi_tile_world_gets_the_oam_allocator():
    print("\n[multi-tile world -> dynamic OAM]")
    src = emit_rooms_mos(dict(BASE, clips={"meta_w": 2, "meta_h": 2,
                                           "per_kind_size": False,
                                           "flip": False, "player": True}))
    check("actor.set_base(slot, ob)" in src, "fans pack at a cumulative base")
    check("actor.set_base(slot, actor.NO_OAM)" in src,
          "overflow parks at NO_OAM instead of writing OOB OAM")
    check("if ob + fan > OAM_SLOTS {" in src,
          "the console's OAM table is the bound")
    # ... and it is the CONSOLE's count, not the Game Boy's. SMS/GG/NES have
    # 64 hardware sprites (the engine's own GBS_META_SLOTS says so), and
    # hardcoding 40 parked the tail of a crowded room at NO_OAM on a console
    # with room to spare - a reference-engine sample room needs 48
    # objects, so both its signs did not draw on the SMS.
    check('const OAM_SLOTS: u8 = 64' in src and 'const OAM_SLOTS: u8 = 40' in src
          and 'platform == "sms"' in src,
          "... forked per console (64 on SMS/GG/NES, 40 on the GB family)")
    check("sprite.move(s, 200, 200)" in src,
          "room load sweeps stale OAM (bases re-pack per room)")
    check("player.set_base(0)" in src, "the player fan owns slot 0")
    check("var ob: u8 = 4" in src,
          "uniform 2x2: actor bases start above the player's 4-slot fan")


def test_per_kind_sizes_read_the_selectors():
    print("\n[mixed sizes -> per-kind fans]")
    src = emit_rooms_mos(dict(BASE, clips={"meta_w": 7, "meta_h": 6,
                                           "per_kind_size": True,
                                           "flip": False, "player": True}))
    check("clips.meta_w(fk)" in src, "each actor's fan is ITS kind's size")
    check("canim.set_clip_size(clips.meta_w, clips.meta_h)" in src,
          "canim draws per-kind sizes")
    check("var ob: u8 = clips.meta_w(scenes.KIND_PLAYER)" in src,
          "the player reserve is the player KIND's own fan")


def test_residency_emits_the_vram_allocator():
    print("\n[residency]")
    res = {"nk": 3, "kt": [12, 4, 8],
           "stems": {0: "spr_player", 1: "spr_duck", 2: "spr_cat"},
           "player_stem": "spr_player"}
    src = emit_rooms_mos(dict(BASE, residency=res, uses_projectile=True,
                              clips={"meta_w": 2, "meta_h": 2,
                                     "per_kind_size": True,
                                     "flip": False, "player": True}))
    check("const NKIND: u8 = 3" in src, "kind count emitted")
    check("const KT: array[u8, 3] = [ 12, 4, 8 ]" in src,
          "per-kind sheet sizes emitted")
    check("sprite.set_data(vb, spr_duck_tile_count, spr_duck_tiles)" in src,
          "upload_kind dispatches to the kind's own sheet asset")
    check("kvb[scenes.KIND_PLAYER] = 0" in src,
          "the player's sheet is the resident base at VRAM 0")
    # The player's sheet must actually be UPLOADED here. It used to be left to
    # the shell's boot upload, and when that was (correctly) dropped so the
    # sheet could co-locate into a ROM bank, nothing wrote VRAM 0 at all -- the
    # player rendered as whatever happened to be there, a one-tile-wide sliver
    # on the reference-engine sample conversion.
    check("upload_kind(scenes.KIND_PLAYER, 0)" in src,
          "the player's own sheet is uploaded, not assumed resident")
    check("var vt: u16 = spr_player_tile_count" in src,
          "room kinds pack above the player's tiles")
    check("if need <= 128 {" in src,
          "the 128 exclusive GB OBJ tiles are the bound")
    check("actor.activate(slot, tb, scenes.obj_x_at(i), scenes.obj_y_at(i))" in src,
          "activate seeds the kind's VRAM base as the actor tile")
    check("canim.set_frames_rel()" in src,
          "canim adds the base to the kind-relative frames")
    check("projectile.set_base(ob)" in src and "projectile.reset()" in src,
          "projectiles re-base above the room's fans")
    # residency without projectiles: no rebase
    src2 = emit_rooms_mos(dict(BASE, residency=res,
                               clips={"meta_w": 2, "meta_h": 2,
                                      "per_kind_size": True,
                                      "flip": False, "player": True}))
    check("projectile.set_base" not in src2,
          "no projectile rebase when the world fires none")
    check("PKIND" not in src and "var PW: u8" not in src,
          "one player sprite: no per-scene table, box stays a const")


def test_a_playerless_room_stops_animating_the_player():
    """`core.clear_player()` is not enough to make a room player-less.

    It stops `vm.player.update()` running, but vm.canim's `tick_player` is
    driven from the ANIM seam and ends in "re-assert every frame" (the Lynx
    present needs that), so it put the just-hidden player straight back on
    screen the next frame. Measured on the reference-engine conversion before the fix:
    two OAM objects - the player's 8x16 fan - parked at screen (0,0) for the
    whole Logo screen. `canim.set_player(255)` is the documented "no player
    clip" sentinel `tick_player` already returns on, and the next real room's
    `canim.set_player(pk)` re-arms it."""
    print("\n[a player-less room stops animating the player]")
    src = emit_rooms_mos(dict(BASE, types=["topdown", "logo", "menu"],
                              clips={"meta_w": 2, "meta_h": 2,
                                     "per_kind_size": False,
                                     "flip": False, "player": True}))
    check(src.count("canim.set_player(255)") == 2,
          "both player-less arms (logo + menu) stop the player animator")
    for verb in ("core.clear_player()", "player.hide()", "player.reset_view()"):
        check(verb in src, "... and still calls %s" % verb)
    plain = emit_rooms_mos(dict(BASE, types=["topdown", "logo"]))
    check("canim.set_player(255)" not in plain,
          "a world with no player clips has no animator to stop")


def test_a_player_room_rearms_the_player_animator():
    """...and a room WITH a player binds the clip back.

    With per-scene player kinds, load_room's `canim.set_player(pk)` does it on
    every load. With ONE player sheet, `start()` binds the clip exactly once,
    so the first logo or menu room unbound it for the rest of the game: the
    player moved and published its facing while vm.canim's tick_player
    returned on the 255 sentinel. Found on the adventure check project, whose start scene
    is a LOGO scene - the player stood in its front pose in every room and
    never animated. The re-arm is emitted only where the unbind is."""
    print("\n[a player room re-arms the player animator]")
    clips = {"meta_w": 2, "meta_h": 2, "per_kind_size": False,
             "flip": False, "player": True}
    rearm = "canim.set_player(scenes.KIND_PLAYER)"
    src = emit_rooms_mos(dict(BASE, types=["topdown", "logo", "menu"],
                              clips=clips))
    check(src.count(rearm) == 2,
          "start() binds once AND the topdown arm re-binds on every load")
    arm = src[src.index("SCTYPE_TOPDOWN {"):src.index("SCTYPE_LOGO {")]
    check(rearm in arm, "... inside the topdown arm, not a player-less one")
    only = emit_rooms_mos(dict(BASE, types=["topdown"], clips=clips))
    check(only.count(rearm) == 1,
          "a world with no player-less room keeps start()'s one bind")


def test_an_emote_does_not_shrink_the_smsgg_vram_ceiling():
    """The emote bubble reserves four sprite tiles. Where they come from is PER
    CONSOLE, and it has to be.

    Taking them off the room allocator's ceiling evicts a room's last kind,
    which then draws at VRAM base 0 - the player's sheet - as garbage. That is
    exactly what happened to the converted parallax room on SMS/GG, whose 126 tiles
    (an 80-tile soft-flip-baked platform player + a 4-tile signpost + the
    42-tile big animated actor) fit 128 but not 124.

    The GB has nowhere else to put them, so its ceiling does drop to 124. SMS/GG
    sprite patterns are a SEPARATE 192-slot VDP area (docs/vram-layout.md tile
    ids 256..447), so theirs come off the top of THAT: 192 - 4 = 188.

    The SMS/GG ceiling used to be a flat 128 - the GB's number, inherited by a
    console that does not share the GB's reason for it (tile 128+ collides with
    background data in 0x8800 mode there, and nowhere else). It left 60 of
    their 192 slots unused, which is what refused a long walk-in room's last
    kinds and made its four health HEARTS vanish on the SMS (ROM A/B at 128 vs
    188: absent, then drawn)."""
    print("\n[emote tile reservation is per console]")
    res = {"nk": 3, "kt": [12, 4, 8],
           "stems": {0: "spr_player", 1: "spr_duck", 2: "spr_cat"},
           "player_stem": "spr_player"}
    base = dict(BASE, residency=res,
                clips={"meta_w": 2, "meta_h": 2, "per_kind_size": True,
                       "flip": False, "player": True})
    src = emit_rooms_mos(dict(base, uses_emote=True))
    check("if need <= OBJ_TILE_TOP {" in src,
          "the allocator reads the per-console ceiling, not a literal")
    check("const OBJ_TILE_TOP: u16 = 188" in src,
          "SMS/GG get their WHOLE 192-slot pattern area, less the bubble's 4")
    check("const OBJ_TILE_TOP: u16 = 124" in src,
          "the GB family gives up four, which is all it can do")
    check("const EMOTE_TILE: u8 = 188" in src,
          "SMS/GG put the bubble at the top of their 192 sprite slots")
    check("const EMOTE_TILE: u8 = 124" in src,
          "the GB family puts it just above the allocator's range")
    # THE TWO HALVES OF THE RESERVATION POINT OPPOSITE WAYS, and that is the
    # point (found on an RPG conversion): a TILE index carries no priority, so the bubble's
    # tiles come off the TOP, out of the upward packer's way. An OAM index IS
    # the draw priority on the GB family - lower draws on top - and the reference engine
    # renders its emote FIRST (actors_render, into a cursor that restarts at
    # 0), so the objects come off the BOTTOM. Reserved at the top, the bubble
    # drew behind the very actor it belonged to.
    check("const EMOTE_OAM: u8 = 0" in src,
          "the OAM fan is reserved at the BOTTOM, so the bubble draws IN FRONT")
    check("const EMOTE_FAN: u8 = 4" in src,
          "...and the four objects it takes are named, for the packer's seed")
    check("        ob += EMOTE_FAN" in src,
          "the actor packer starts ABOVE the bubble's objects")
    check("player.set_base(EMOTE_FAN)" in src,
          "so does the player's fan - only the bubble is below it")
    check("if ob + fan > OAM_SLOTS {" in src,
          "and the ceiling is the whole table again, nothing sits at the top")
    # ... and a world with no emote that will never build for those two is
    # untouched: the literal, as before.
    plain = emit_rooms_mos(base)
    check("if need <= 128 {" in plain and "OBJ_TILE_TOP" not in plain,
          "no emote, no SMS/GG target: the allocator keeps its literal 128 "
          "(byte-identical)")
    check("EMOTE_TILE" not in plain and "EMOTE_OAM" not in plain
          and "EMOTE_FAN" not in plain,
          "no emote: nothing is reserved at all")
    check("player.set_base(0)" in plain,
          "...and the player's fan is back at object 0")
    # ...but a residency world that DOES target them carries the ceiling fork
    # even with no bubble, or its rooms are held to a GB limit for no reason.
    smsgg = emit_rooms_mos(dict(base, targets_smsgg=True))
    check("const OBJ_TILE_TOP: u16 = 192" in smsgg
          and "const OBJ_TILE_TOP: u16 = 128" in smsgg,
          "SMS/GG target, no emote: 192 there, 128 everywhere else")
    check("EMOTE_TILE" not in smsgg,
          "...and still no emote reservation (the two are independent)")
    # The fork is worth carrying only where something READS it - the per-room
    # allocator. A world that uploads its sprites once at boot emits none, so
    # it is byte-identical whichever consoles it targets.
    no_res = emit_rooms_mos(dict(BASE, targets_smsgg=True))
    check("OBJ_TILE_TOP" not in no_res,
          "no residency: no ceiling fork at all, on any target")


def test_per_scene_player_sprite():
    """The reference engine keys its default player sprite by scene TYPE, so a platform
    room's player is different (and TALLER) art than a topdown room's. The
    sheet, the OAM fan, the collision box and the clip binding all follow it
    per ROOM; binding the topdown art everywhere drew the wrong character,
    and keeping the 16x16 box under a 16x32 sprite stood it in the ground."""
    print("\n[per-scene player sprite]")
    # pboxes: the box is the drawn rect down to the FEET, so the 16x32
    # platform sprite (28 px of character in a 32 px frame) boxes at 16x28
    res = {"nk": 3, "kt": [24, 8, 4],
           "stems": {0: "spr_player", 1: "spr_player_platform", 2: "spr_cat"},
           "player_stem": "spr_player", "pkinds": [0, 1, 0],
           "pboxes": [(16, 16), (16, 28), (16, 16)]}
    src = emit_rooms_mos(dict(BASE, residency=res,
                              clips={"meta_w": 2, "meta_h": 2,
                                     "per_kind_size": True,
                                     "flip": False, "player": True}))
    check("const PKIND: array[u8, 3] = [ 0, 1, 0 ]" in src,
          "the per-scene player KIND table is emitted")
    check("var pk: u8 = PKIND[rm]" in src, "load_room reads this room's kind")
    check("upload_kind(pk, 0)" in src and "kvb[pk] = 0" in src,
          "this room's player sheet is the one uploaded at VRAM 0")
    check("var vt: u16 = KT[pk]" in src,
          "other kinds pack above THIS player's tiles, not a fixed sheet's")
    check("clips.meta_w(pk)" in src and "clips.meta_h(pk)" in src,
          "the OAM fan is sized from this room's player sprite")
    check("canim.set_player(pk)" in src,
          "the player's clips are re-bound per room")
    check("canim.set_player(scenes.KIND_PLAYER)" not in src,
          "... and NOT bound once at boot")
    # the collision box follows the sprite - down to the FEET, not to the
    # frame's padded bottom (that padding floated the character 4 px)
    check("var PW: u8 = " in src and "var PH: u8 = " in src,
          "the player box becomes per-room state, not a const")
    check("const PBH: array[u8, 3] = [ 16, 28, 16 ]" in src,
          "the per-scene box height stops at the feet, not the padded rect")
    check("PH = PBH[rm]" in src and "clips.meta_h(pk) * 8" not in src,
          "load_room reads the authored box, not the metasprite size")
    # the spawn MARKER stays the canonical player kind
    check("if scenes.obj_kind_at(i) != scenes.KIND_PLAYER {" in src,
          "the object loop still skips the player MARKER kind")


def test_auto_fade_is_opt_in():
    """`studio.toml [scenes] fade` fades a room IN on load, and is
    byte-identical when absent.

    No new engine capability: `vm.fx` (the 0..3 darkness LEVEL the FADE opcode
    already steps) was simply never driven from the room-load path, so every
    scene transition CUT where the reference engine fades. The ramp runs BEFORE the
    shell's on_load hook, because vm.fx darkens OBP0/OBP1 too and that hook is
    what re-asserts a game's real sprite palettes."""
    print("\n[auto-fade]")
    off = emit_rooms_mos(dict(BASE))
    check("fx.set_level" not in off and "FADE_HOLD" not in off,
          "absent: no fade code at all (byte-identical)")
    on = emit_rooms_mos(dict(BASE, fade=2, on_load_hook=True))
    check('import "vm.fx"' in on and "const FADE_HOLD: u8 = 2" in on,
          "present: vm.fx imported + the hold emitted")
    check("fx.set_level(3)" in on, "load_room blacks out BEFORE painting")
    check("fl = fl - 1" in on and "fx.set_level(fl)" in on,
          "... and ramps back up at the end")
    check(on.index("fx.set_level(fl)") < on.index("g_on_load()"),
          "the ramp runs BEFORE the shell's palette hook")


def test_lib_seams_exist():
    print("\n[lib seams]")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    actor = open(os.path.join(root, "lib", "vm", "actor.mos"),
                 encoding="utf-8").read()
    canim = open(os.path.join(root, "lib", "vm", "canim.mos"),
                 encoding="utf-8").read()
    proj = open(os.path.join(root, "lib", "vm", "projectile.mos"),
                encoding="utf-8").read()
    check("const NO_OAM = 255" in actor and "set_base" in actor,
          "vm.actor: per-actor bases + the NO_OAM sentinel")
    check("function tile_of" in actor, "vm.actor: tile_of getter")
    check("if base == NO_OAM {" in actor,
          "vm.actor render skips a no-OAM actor (its move would write OOB)")
    check("set_frames_rel" in canim and "actor.tile_of(i)" in canim,
          "vm.canim: relative frames add the actor's VRAM base")
    check("if base == actor.NO_OAM {" in canim,
          "vm.canim skips a no-OAM actor")
    check("function set_base" in proj and "p_parked" in proj,
          "vm.projectile: settable base + inactive-slot park latch "
          "(an every-frame hide would stomp overlapping fans)")


def test_kind_variants():
    """world.toml [kind_variants]: a placed PLACEHOLDER kind becomes
    kinds[heap var] at room load, BEFORE the upload / palette / clip reads,
    so one room serves every variant and only the chosen sheet uploads."""
    print("\n[kind variants]")
    res = {"nk": 4, "kt": [4, 8, 8, 8],
           "stems": {0: "spr_player", 1: "spr_a", 2: "spr_b", 3: "spr_c"},
           "player_stem": "spr_player"}
    clips = {"meta_w": 2, "meta_h": 2, "per_kind_size": True,
             "flip": False, "player": True}
    src = emit_rooms_mos(dict(BASE, residency=res, clips=clips,
                              variants={1: (7, [2, 3])}))
    check("const VK1: array[u8, 2] = [ 2, 3 ]" in src,
          "the variant list is emitted per placeholder")
    check("var v: i16 = core.var_get(7)" in src,
          "the heap variable picks the variant")
    check("if v >= 0 and v < 2 {" in src, "an out-of-range value keeps the kind")
    load = src[src.index("function load_room("):]
    swap = load.find("fk = variant_kind(fk)")
    check(0 <= swap < load.find("upload_kind(fk, vb8)"),
          "the swap happens before the sheet upload")
    check("actor.set_clip(slot, fk)" in load,
          "the actor wears the CHOSEN kind's clips")
    off = emit_rooms_mos(dict(BASE, residency=res, clips=clips))
    check("variant_kind" not in off
          and "actor.set_clip(slot, scenes.obj_kind_at(i))" in off,
          "no [kind_variants]: nothing emitted (byte-identical)")
    # the resolver: names -> ids, the variable -> its heap index
    import tempfile
    from mosaik_vm.isa import VmError
    from mosaik_vm.rooms.generate import _kind_variants
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "scripts"))
        with open(os.path.join(root, "scripts", "a.evt.toml"), "w") as f:
            f.write("\n".join([
                "[[script]]", 'name = "main"',
                "[[script.events]]", 'event = "set_var"', 'var = "other"', "value = 1",
                "[[script.events]]", 'event = "set_var"', 'var = "pick"', "value = 1",
                ""]))
        world = {"kinds": {"player": 0, "foe": 1, "a": 2, "b": 3},
                 "kind_variants": {"foe": {"var": "pick", "kinds": ["b", "a"]}}}
        from mosaik_vm import compile_path
        idx = compile_path(os.path.join(root, "scripts")).variables["pick"]
        got = _kind_variants(root, world, res)
        check(got == {1: (idx, [3, 2])},
              "resolved to {placeholder: (heap index, kind ids)}: %r" % (got,))
        check(_kind_variants(root, {"kinds": world["kinds"]}, res) is None,
              "absent section resolves to None")
        for bad, what in ((dict(world), "no residency"),
                          (dict(world, kind_variants={"foe": {"var": "nope",
                                                              "kinds": ["a"]}}),
                           "an unknown variable"),
                          (dict(world, kind_variants={"foe": {"var": "pick",
                                                              "kinds": ["zz"]}}),
                           "an unknown kind")):
            try:
                _kind_variants(root, bad, None if what == "no residency" else res)
                check(False, "%s is refused" % what)
            except VmError:
                check(True, "%s is refused" % what)


if __name__ == "__main__":
    print("=" * 60)
    print("per-room sprite residency + dynamic OAM layout")
    print("=" * 60)
    test_single_tile_world_is_unchanged()
    test_multi_tile_world_gets_the_oam_allocator()
    test_per_kind_sizes_read_the_selectors()
    test_residency_emits_the_vram_allocator()
    test_a_playerless_room_stops_animating_the_player()
    test_a_player_room_rearms_the_player_animator()
    test_an_emote_does_not_shrink_the_smsgg_vram_ceiling()
    test_per_scene_player_sprite()
    test_auto_fade_is_opt_in()
    test_lib_seams_exist()
    test_kind_variants()
    print("\n" + "=" * 60)
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        sys.exit(1)
    print("All sprite-residency checks passed")
