"""The UI freeze is published EVERY frame, not only on unlocked ones.

Whether a box or menu owns the screen is a FACT about this frame, not a
decision that belongs to the unlocked branch - and the case that matters is
precisely the locked one, because THE LOCK IS A PROPERTY OF THE SCRIPT KIND
(an On Interact, a trigger script and a scene init all lock), so essentially
every dialogue box in a conversion is opened by a locked script.

Published inside `vm_lockcount == 0`, `vm.player.in_block` was therefore never
set while a box was up, so `follow_and_render`'s SMS/GG freeze arm never ran -
and the `g_pdraw` seam (= `player.draw`), which vm.core calls on every LOCKED
frame so a locked walk
still animates, re-published the camera and rewrote the hardware scroll with
the value `text.to_window` had just SNAPPED to a tile. The box stayed on the
tile grid while the screen moved under it: it slid left by `camx & 7` px, which
pushes its left border off the window and opens a gap of the same width on the
right (reported from play 2026-08-25; measured on the SMS/GG sample conversion in
the parallax room as a 1..8 px gap cycling with the camera's sub-tile phase,
on the SMS and the Game Gear alike, and identical at every phase after).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

passed = 0
failed = 0


def check(name, ok, detail=None):
    global passed, failed
    if ok:
        passed += 1
        print("  ok: %s" % name)
    else:
        failed += 1
        print("  FAIL: %s" % name)
        if detail:
            print("    %s" % str(detail)[:500])


def _src(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def test_freeze_is_unconditional():
    c = _src("lib/vm/core.mos")
    run = c.split("function run()")[1]

    freeze = run.index("g_ui_freeze(uib)")
    gate = run.index("if vm_lockcount == 0 {", 0)
    # ...the LOCK GATE that guards the player handler, i.e. the one whose body
    # holds g_player(). Find it by that body rather than by position.
    gates = []
    at = 0
    while True:
        at = run.find("if vm_lockcount == 0 {", at)
        if at < 0:
            break
        gates.append(at)
        at += 1
    player_gate = None
    for g in gates:
        if 0 <= run.find("g_player()", g) - g < 400:
            player_gate = g
            break
    check("run() still gates the player handler on the lock",
          player_gate is not None)
    if player_gate is None:
        return
    check("the UI freeze is published BEFORE that gate, not inside it",
          freeze < player_gate,
          run[min(freeze, player_gate):max(freeze, player_gate)][:300])

    # ...and it must still be after the scripts (which is what opens a box)
    # and before the locked draw, or the first frame of a box is wrong.
    scripts_at = run.index("run_scripts()")
    # the CALL, not the prose about it: comments name the draw too.
    draw_at = run.index("\n                    g_pdraw()")
    check("...after run_scripts (the box opens there)", scripts_at < freeze)
    check("...and before g_pdraw() (the locked frame's camera user)",
          freeze < draw_at)

    # The player handler itself must NOT have escaped the gate.
    check("g_player() is still inside the lock gate",
          run.index("g_player()") > player_gate)


def test_player_freeze_arm_still_exists():
    p = _src("lib/vm/player.mos")
    fr = p.split("local function follow_and_render()")[1][:1400]
    check("the SMS/GG freeze arm keys on in_block", "in_block == 1" in fr, fr[:300])
    # the arm runs to its own `return`; slice to that, not to a magic window
    # (these arms carry long comments and a fixed window fails a check about
    # code that is there).
    arm = fr.split("in_block == 1")[1]
    arm = arm[:arm.index("return")]
    check("...and holds the view instead of re-publishing the camera",
          "put_player" in arm and "wide_view" not in arm, arm[:400])
    check("...rounding the camera down to the SAME tile the snap used",
          "cam_draw_x()" in arm and "cam_draw_y()" in arm, arm[:400])


def test_draw_camera_is_the_one_rounding():
    """EVERY sprite rounds with the box, not just the player.

    The 2026-08-25 fix corrected `follow_and_render` alone, so vm.actor,
    vm.projectile and vm.emote kept positioning against the raw
    `cam_x()/cam_y()` while `text.to_window` had snapped the scene under them:
    every NPC, shot and emote bubble slid 0..7 px against the ground for as
    long as the box was up. Reported from play on the SMS/GG sample conversion
    (the long walk-in room: an NPC standing beside its own feet), and
    measured on the Game Gear ROM through genesis_plus_gx at camera phase 3 -
    the NPC's window was 320 px wrong against a phase-0 control and 0 px
    wrong once every draw went through the shared rounding.
    """
    p = _src("lib/vm/player.mos")
    check("vm.player defines the DRAW camera",
          "function cam_draw_x()" in p and "function cam_draw_y()" in p)
    check("...only on the consoles whose box lives in the scrolling map",
          p.split("function cam_draw_x()")[0].rstrip().endswith(_BOX_IN_MAP_GUARD),
          _BOX_IN_MAP_GUARD)
    body = p.split("function cam_draw_x()")[1]
    body = body[:body.index("function cam_draw_y()")]
    check("...snapping to the tile only while the UI owns the screen",
          "in_block == 1" in body and "v - (v & 7)" in body, body[:400])
    check("...and it is exported", "export cam_draw_x, cam_draw_y" in p)
    # cam_x() itself must stay TRUE: vm.core's camera_x/camera_y state reads
    # and the generated rooms.mos streamer seeds read it, and `in_block` can
    # still be set across a room change (the door was opened under a box).
    camx = p.split("function cam_x()")[1]
    camx = camx[:camx.index("function cam_y()")]
    check("cam_x() itself still reports the TRUE camera",
          "in_block" not in camx and "& 7" not in camx, camx[:300])

    # vm.projectile has TWO sites since 2026-09-03: the Lynx/PCE arm of its
    # render walk (verbatim, per shot) and the banked arm's once-per-walk read.
    for mod, n in (("actor", 3), ("emote", 1), ("projectile", 2)):
        src = _src("lib/vm/%s.mos" % mod)
        check("vm.%s draws through the draw camera (%d site(s))" % (mod, n),
              src.count("player.cam_draw_x()") == n
              and src.count("player.cam_draw_y()") == n,
              "cam_draw_x x%d" % src.count("player.cam_draw_x()"))
        # The lookback has to clear the guard AND the explanatory comment
        # between it and the call - adding one console to the guard string was
        # enough to push vm.projectile's site past a 400-char window and fail a
        # check whose contract had not changed at all.
        check("...behind a statement-level platform fork (folded off elsewhere)",
              all(_BOX_IN_MAP_GUARD in src[max(0, at - 700):at]
                  for at in _finds(src, "player.cam_draw_x()")))
    # The projectile DESPAWN test keeps the true camera: it asks where the shot
    # is, not where it is drawn.
    pr = _src("lib/vm/projectile.mos")
    despawn = pr.split("Despawn once fully off the visible screen")[1][:600]
    check("the projectile despawn test keeps the TRUE camera",
          "player.cam_x()" in despawn and "cam_draw_x" not in despawn,
          despawn[:300])


def test_snap_and_sprites_move_together():
    """The scroll snap and the sprite re-place land in the SAME display frame.

    The SMS/GG box draw spans SEVERAL display frames inside one game frame
    (border + a page of glyphs on open, the ring re-seed on close), and the
    scroll snap fires at the START of the op while the sprite pass runs at
    the END of the game frame - so every sprite stood 0..7 px off the ground
    for the whole draw (measured: 9 display frames at open, 2 at close on
    the SMS/GG sample conversion's NPC), and the per-vblank SAT flush could catch
    a metasprite between its column writes (the reported split). Every site
    that moves the scroll must call ui_snap_sprites in the same display
    frame; with it, the residual measured 1 frame per transition, GG and SMS.
    """
    c = _src("lib/vm/core.mos")
    check("core defines ui_snap_sprites", "function ui_snap_sprites" in c)
    helper = c.split("local function ui_snap_sprites")[1]
    helper = helper[:helper.index(chr(10) + "    }")]
    check("...which publishes the freeze BEFORE drawing",
          helper.index("g_ui_freeze(on)") < helper.index("g_pdraw()"))
    check("...and re-places player, actors and the emote",
          "actor.render(lock_flag())" in helper and "g_emote_update()" in helper)
    fork = 'if platform == "sms" or platform == "gamegear" {'
    # DELIBERATELY the two-console guard, not _BOX_IN_MAP_GUARD: this helper
    # re-places sprites MID-frame because the z80 box draw spans several display
    # frames inside one game frame. The PCE needs the draw camera but not this.
    #
    # BRACE-COUNTED, not "no close brace since the fork" (2026-09-19). The
    # guard does not indent its contents, so a sibling function defined beside
    # this helper inside the same fork - `ui_sprite_cut` - closes with exactly
    # the `    }` the guard itself would, and the text heuristic read that as
    # the guard having ended.
    helper_at = c.index("local function ui_snap_sprites")
    at_fork = c.rindex(fork, 0, helper_at)
    seg = c[at_fork + len(fork):helper_at]
    seg = chr(10).join(ln.split("--")[0] for ln in seg.split(chr(10)))
    depth = 1 + seg.count("{") - seg.count("}")
    check("...defined only on SMS/GG (folds off everything else)",
          depth >= 1, "brace depth at the helper: %d" % depth)
    # every to_window is followed by ui_snap_sprites(1), every UI-close
    # to_bkg by ui_snap_sprites(0) (reset_scene_ui is exempt: the room
    # reload re-places everything itself).
    #
    # ...AND THE SPRITE CUT IS ARMED BETWEEN THE TWO (2026-09-19).
    # The cut is a
    # FILTER inside `sprite.move`, not a register, so the objects this pass
    # writes must already be filtered - armed after it, the pass has been and
    # gone and nothing walks again while the camera is frozen. The window is
    # sized for the whole open block rather than the old 400 chars, which the
    # arming block now sits inside.
    for at in _finds(c, "text.to_window("):
        win = c[at:at + 1400]
        check("to_window at %d is followed by ui_snap_sprites(1)" % at,
              "ui_snap_sprites(1)" in win, win[:200])
        if "ui_sprite_cut(" in win:
            check("...with the sprite cut armed BEFORE it, at %d" % at,
                  win.index("ui_sprite_cut(") < win.index("ui_snap_sprites(1)"),
                  win[:300])
    # Two UI closes re-place: the menu's, and the staged box teardown's last
    # stage. Each must RESTORE the scroll first, then re-place against it.
    rel = list(_finds(c, "ui_snap_sprites(0)"))
    check("both UI closes re-place after restoring the scroll",
          len(rel) == 2
          and all("text.to_bkg()" in c[max(0, at - 700):at] for at in rel),
          "ui_snap_sprites(0) x%d" % len(rel))


def test_freeze_spans_the_staged_teardown():
    """The UI freeze must outlast `box_open`, or the sliced repaint tears.

    `box_open` drops to 0 the moment A is pressed, but the teardown that
    follows walks the 32-column ring a few columns per frame. Unfrozen, the
    camera resumes on the first of those frames and the streamer advances
    `s_base` UNDER the walk - so the slices are taken against a moving base,
    some ring columns are written twice and others never, and the ones never
    written keep the box's own cells: vertical stripes of box paper left
    standing in the scene (reported from play on the SMS/GG sample conversion's parallax
    room). Measured after: 0 paper-coloured columns in the ground band, and
    the scene pixel-identical to before the box.
    """
    c = _src("lib/vm/core.mos")
    pub = c.split("g_ui_freeze(uib)")[0][-1200:]
    check("the freeze is also published while a teardown is pending",
          "ui_close != 0" in pub and "uib = 1" in pub, pub[-400:])


def test_sprites_and_scroll_share_one_clock():
    """SMS/GG defer the scroll so it commits with the sprite table.

    `sprite.vbl_hold` releases the shadow-OAM copy at the present, so sprites
    reach the hardware once per frame. A scroll written the moment the
    streamer computes it is then a frame AHEAD whenever a game frame spans
    more than one display frame, and every actor slides against the
    background while the camera moves (reported from play; measured as the
    sprite-to-background offset taking three different values while walking,
    and one value on 130/130 frames after).
    """
    gen = _src("mosaik/codegen/generator.py")
    swap = gen.split("stdlib[('bkg', 'move')] = 'gbs_scroll_move'")[0][-2600:]
    check("the scroll shadow is swapped in for SMS/GG as well as the GB",
          "self.platform in ('sms', 'gamegear')" in swap, swap[:300])
    txt = _src("mosaik/codegen/gbdk_text.py")
    check("the SMS/GG UI space reads the PENDING scroll, not the register",
          "gbs_scr_shx" in txt and "deferred = self.bkg_move_used" in txt)
    check("...and its snap/restore go through the same deferred setter",
          "gbs_scroll_move((uint8_t)(gbs_ui_scx & 0xF8)" in txt
          and "gbs_scroll_move(gbs_ui_scx, gbs_ui_scy);" in txt)


def test_everything_drawn_is_re_placed():
    """The forced re-place covers every world-space drawer, projectiles too.

    A shot in flight is positioned against the same camera as the actors, so
    leaving it out of the pass keeps it at the pre-snap position for the
    frames a box is opening.
    """
    c = _src("lib/vm/core.mos")
    helper = c.split("local function ui_snap_sprites")[1]
    helper = helper[:helper.index(chr(10) + "    }")]
    for what, call in (("player", "g_pdraw()"), ("actors", "actor.render(lock_flag())"),
                       ("emote", "g_emote_update()"), ("projectiles", "g_proj_render()")):
        check("the re-place pass covers %s" % what, call in helper, helper[:400])


def test_cam_apply_stands_down_under_the_ui():
    """A scripted pan must not re-assert the scroll the box snap owns.

    `cam_apply` runs every frame, even under a cutscene lock - so a pan (or a
    shake) while a box is up would write its own value over the snapped one
    and slide the box off the tile grid again, which is 6.15 by another
    route. The pan keeps running; only the register write stands down.
    `in_block` also spans the staged teardown, so the sliced repaint is not
    fought either.
    """
    p = _src("lib/vm/player.mos")
    body = p.split("function cam_apply()")[1]
    body = body[:body.index(chr(10) + "    }")]
    gate = body.split("if cam_lock == 1 {")[0]
    check("cam_apply stands down while the UI owns the scroll",
          _BOX_IN_MAP_GUARD in gate
          and "in_block == 1" in gate and "return" in gate,
          gate[-400:])
    code = [l for l in gate.split(chr(10)) if "--" not in l]
    check("...and only there - the GB family keeps writing every frame",
          sum(1 for l in code if "in_block" in l) == 1,
          [l.strip() for l in code if "in_block" in l])


#: The guard every DRAW-CAMERA site shares. Not "the z80 pair" - the property is
#: "this console's dialogue box is plotted into the same table the hardware
#: scrolls", which is true of the SMS/GG name table AND the PC Engine's BAT. The
#: PCE joined 2026-08-31: mapping its box into screen space
#: put it on the right CELL, and closing the last 0..7 px needed the scroll
#: SNAPPED to a tile - which is only safe if every sprite rounds the same way,
#: i.e. exactly this camera. The Lynx is deliberately absent: it redraws the box
#: into its framebuffer every present, so it has no table to scroll out from
#: under it.
#:
#: If a console is added here, ALL of it moves together or none of it does: a
#: snap without the draw camera leaves sprites 0..7 px off the ground, and the
#: draw camera without the `in_block` stand-down is overwritten by
#: follow_and_render before it is ever seen. Both were measured on the PCE - the
#: second one as "the snap changed literally nothing, 472 differing pixels either
#: way".
#:
#: NOTE this is NOT the same set as `ui_snap_sprites` below, which stays SMS/GG.
#: That helper exists because their box draw spans SEVERAL display frames inside
#: one game frame (ui_stage + the sliced repaint), so sprites must be re-placed
#: mid-frame. The PCE draws its box within one frame and commits its scroll in
#: vblank, so ground and sprites already move together there - measured: the
#: player's offset from the background stripes is 0 on EVERY frame across the
#: box opening, including the frame the snap moves both by 6 px.
_BOX_IN_MAP_GUARD = 'if platform == "sms" or platform == "gamegear" or platform == "pce" {'


def _finds(hay, needle):
    at = hay.find(needle)
    while at >= 0:
        yield at
        at = hay.find(needle, at + 1)


def main():
    print("The UI freeze under a LOCK (the sliding SMS/GG dialogue box)")
    print("=" * 60)
    test_freeze_is_unconditional()
    test_player_freeze_arm_still_exists()
    test_draw_camera_is_the_one_rounding()
    test_snap_and_sprites_move_together()
    test_freeze_spans_the_staged_teardown()
    test_sprites_and_scroll_share_one_clock()
    test_everything_drawn_is_re_placed()
    test_cam_apply_stands_down_under_the_ui()
    print("=" * 60)
    if failed:
        print("%d check(s) FAILED" % failed)
        return 1
    print("All UI-freeze checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
