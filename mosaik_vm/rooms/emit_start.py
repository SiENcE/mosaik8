"""mosaik_vm.rooms.emit_start - `start()`, the one call the fixed shell makes
after `core.boot` - every seam this world needs, wired once.

One section of the generated `rooms` module. `emit(c, L)` appends its
lines to `L`, reading the derived world shape off the RoomsCtx `c`
(see rooms.context)."""

#: The comment start() writes beside its `fx.set_style(n)` for a declared
#: `[scenes] fade_style`. mosaik8_build scans the sources for THIS to state
#: VM_FADE_STYLE - never for the call, which lib/vm/core.mos also makes.
FADE_STYLE_MARKER = '-- [scenes] fade_style = "'

#: ...and the one it writes beside its `text.win_overlay_cut(n)` for a declared
#: `[scenes] overlay_cut`. A COMMENT, never the call: `lib/vm/core.mos` calls
#: the same verb from its SET_STATE arm, so a scan for the call would match
#: every project that writes the state as well.
OVERLAY_CUT_MARKER = '-- [scenes] overlay_cut = '


def emit(c, L):
    """The start() boot function: wire the seams, find the player, load room 0."""
    info = c.info
    use_player, playerless, has_pk, clips = c.use_player, c.playerless, c.has_pk, c.clips
    dyn_oam, res, pkinds, sel = c.dyn_oam, c.res, c.pkinds, c.sel
    fade, cgb_fade, emote, ui_redraw = c.fade, c.cgb_fade, c.emote, c.ui_redraw
    PAL_FADE_GUARD, UI_REDRAW_GUARD = c.PAL_FADE_GUARD, c.UI_REDRAW_GUARD

    # start(): the one call the fixed shell makes after core.boot.
    L += ["    -- Boot into `start_room`: wire the animation clips (when the world",
          "    -- has any), register load_room as the scene-change callback, find",
          "    -- the player start in the object table, and load the room.",
          "    function start(start_room: u8) {",
          "        scripts.code_window()     -- the interpreter's inline fetch (V-5)"]
    if info.get("has_text"):
        # The dialogue box's HEIGHT follows the string it shows (border + lines
        # + border, bottom-anchored) - the reference engine's own geometry. Without this
        # seam vm.core keeps a fixed 4-row box, and a 3-LINE text overwrote its
        # own bottom border.
        L.append("        core.set_text_lines(scripts.text_lines)")
    if info.get("box_hides_sprites"):
        # OBJ draws above the WINDOW on GB hardware, so an actor low in the room
        # shows THROUGH an open box. The reference engine cuts sprites at the window's first
        # scanline (an LCD interrupt); this opts into the same. Scoped to the
        # box, so a persistent overlay HUD keeps its actors visible.
        L.append("        core.set_box_cut(1)")
    if info.get("dmg_palette"):
        # BGP / OBP0 / OBP1 as a DMG build renders through them. Before the
        # first room load, because it IS the base every fade ramps from; the
        # CGB ignores all three, so one call serves both builds of a project.
        L.append("        fx.set_dmg_palette(0x%02X, 0x%02X, 0x%02X)"
                 % info["dmg_palette"])
    if info.get("camera_pan_lcd"):
        # A scripted pan re-enters once per VM frame, which is 1..3 display
        # frames depending on the room - so `step` px per call runs at a
        # different speed in every scene. On, `step` is quarter-pixels per
        # DISPLAY frame and the pan keeps the reference engine's own rate whatever the
        # frame rate (the curtain / typewriter clock rule).
        L.append("        player.set_pan_lcd(1)")
    if info.get("box_min_rows"):
        # The reference engine's per-text `minHeight` (default 4): the box is
        # BOTTOM-anchored, so a box one row too short draws its text one row
        # too LOW. Ours was `lines + 2` with no floor, which put the platformer conversion's
        # two-line narration 8 px below the reference ROM's.
        L.append("        core.set_box_min(%d)" % int(info["box_min_rows"]))
    if info.get("box_full_width"):
        # The reference engine's box spans the FULL screen width with its text one cell
        # in; ours defaults to an inset box (col 1, SCREEN_COLS-2). A knob, not
        # a global change: the inset box is the right look for a game that is
        # not aiming at the reference engine's.
        L.append("        core.set_box_metrics(0, SCREEN_COLS)")
    if info.get("clear_outside") and not info.get("glyph_text"):
        # The room-load clear plots the font's SPACE glyph, and on the RESIDENT
        # font path that first text call is what triggers the lazy font_init --
        # which would land the font padding over tiles 0..138, i.e. over the tileset
        # load_room has just uploaded. Preloading here is the documented remedy
        # and it has to happen before the first load_room. Glyph-buffer mode
        # keeps no font in VRAM, so it needs nothing.
        L.append("        core.font_preload()")
    if info.get("glyph_text"):
        # GLYPH-BUFFER TEXT (studio.toml [scenes] glyph_text): the font stays in
        # ROM and characters are rasterized on demand into a band ABOVE the
        # scene art, instead of 96 glyph tiles sitting in VRAM. That is what
        # lets a room use nearly the whole tile table -- a 191-tile scene plus a
        # 96-tile resident font needs 287 of 256, which is why text rendered as
        # garbage in the big rooms of the reference-engine import.
        # (0, 0) asks for the DERIVED band, so no tile numbers live here and a
        # growing tileset moves it automatically. First thing in start(), before
        # the first load_room paints: the band must exist before any text draws.
        L.append("        text.glyph_buffer(0, 0)")
    if clips:
        L.append("        canim.set_clips(clips.frame, clips.count, clips.period, "
                 "clips.flip, %d, %d)" % (clips["meta_w"], clips["meta_h"]))
        if clips.get("per_kind_size"):
            # The world mixes sprite shapes, so each actor is drawn at ITS OWN
            # metasprite size rather than the largest one (which fans w*h OAM
            # objects per actor and blows the GB's 40-object limit).
            L.append("        canim.set_clip_size(clips.meta_w, clips.meta_h)")
        if clips.get("frame_mask"):
            # SPARSE frames: a blank column of a composed metasprite parks and
            # takes no tile (the reference engine's own frames are tile placements, not
            # rectangles -- a gap cost the GB one of its 10 sprites per line).
            L.append("        canim.set_clip_mask(clips.frame_mask)")
        if clips.get("frame_pal"):
            # PER-FRAME SPRITE PALETTE: reference-engine colours a whole sprite per
            # animation STATE or FRAME through the same per-tile palette field
            # it uses for a per-cell layout, so the animator writes the frame's
            # palette when the drawn frame changes.
            L.append("        canim.set_clip_pal(clips.frame_pal)")
        if clips.get("desc"):
            # DESCRIPTOR frames (the reference engine's real metasprite model): a desc
            # kind's upload routes through clips.draw -> sprite.set_meta_list,
            # so rows keep their authored offsets (the feet gap) and tiles
            # repeat freely (the score digits' dedupe).
            L.append("        canim.set_clip_draw(clips.draw, clips.is_desc)")
        if clips.get("sel"):
            # The BATCHED selector: vm.canim.apply reads the drawn frame's
            # tile + FLIP_X + is_desc in ONE cross-bank call on every
            # animator-step frame (three banked reads otherwise - most of
            # room 10's step spike).
            L.append("        canim.set_clip_sel(clips.sel)")
        if res:
            # Per-room residency: the clips FRAMES are kind-RELATIVE, so the
            # animator adds each actor's per-room VRAM base (activate's tile).
            L.append("        canim.set_frames_rel()")
        if dyn_oam and (use_player or playerless):
            # The dynamic OAM layout puts the PLAYER's fan first -- its base
            # must be constant across rooms (canim binds it once, right
            # below), so the per-room actor packing starts above it. With an
            # emote pack the bubble sits BELOW the player, at objects
            # 0..EMOTE_FAN-1, because an OAM index is the draw priority on the
            # GB family and the reference engine renders its emote before everything
            # (see emit_prelude).
            L.append("        player.set_base(%s)"
                     % ("EMOTE_FAN" if emote else "0"))
        if clips.get("flip"):
            L.append("        clips.upload_flip()")
    # ...THE FOUR SEAMS BELOW ARE NOT ABOUT CLIPS. They were nested inside
    # `if clips:` above, so a world with no [animations] never got them
    # however its studio.toml was set - and every reference-engine conversion has
    # clips, which is what hid it. Measured on vm-uiscroll (no clips,
    # ui_blocks_player = true): no set_ui_freeze, so on SMS/GG the camera
    # kept following the player under an open box and the column streamer
    # repainted the ring straight over the box. Emitted HERE, in the
    # original order, so a world that does have clips keeps byte-identical
    # rooms.mos.
    if info.get("ui_blocks_player") and use_player:
        # Reference-engine parity: its UI takes the controls, so the player can
        # neither walk nor jump out from under an open dialogue box. The
        # seam reports the UI state each frame; vm.player gates INPUT on
        # it, so gravity and the camera keep running.
        L.append("        core.set_ui_freeze(player.set_input_block)")
    if info.get("uses_input_consume") and use_player:
        # Reference-engine parity: an OVERRIDING input script consumes its button,
        # so the native handler never sees it (events_update runs before
        # state_update and clears the bit out of `joy`). The platformer conversion's title menu
        # IS this - its cursor is the player, and without the consume the
        # held d-pad walks it straight past every menu row.
        L.append("        core.set_input_consume(player.set_input_consume)")
    if info.get("uses_player_visible") and use_player:
        # The reference engine's Hide/Show Actor aimed at the PLAYER: a DRAW flag, so
        # the seam is vm.player's, not the player-less room teardown.
        L.append("        core.set_player_vis(player.set_hidden)")
    if info.get("uses_text_speed"):
        # The TYPEWRITER reveal (TEXT_SPEED): the compiler emitted the
        # per-char step renderer because the blob sets a speed, and this
        # is its one registration - unwired, every box keeps the whole
        # draw and the reveal state never arms (the set_save pattern:
        # the generator wires the seam from the same scan that decides
        # the op is used).
        L.append("        core.set_text_step(scripts.render_text_step)")
    if clips:
        if sel["has_obj_interact"] and clips.get("per_kind_size") and use_player:
            # Reference-engine parity: jump and interact share the A button, and an
            # interact SUPPRESSES the jump (platform.c's did_interact_actor) --
            # without this, talking to an NPC also launched the player. Paired
            # with the entity.set_box registrations in load_room above.
            L.append("        player.set_interact_gate(entity.probe)")
        if clips.get("player") and not pkinds:
            # (with a per-scene player sprite this is re-bound in load_room)
            L.append("        canim.set_player(scenes.KIND_PLAYER)")
        L.append("        core.set_anim(canim.tick_all)")
    if info.get("solid_actors") and use_player:
        # Reference-engine parity: its topdown handler refuses a move onto an actor
        # (actor_in_front_of_player), so an NPC is solid and you cannot walk
        # through the person you are talking to. vm.entity owns the boxes,
        # vm.player consults them beside its terrain collision through this
        # seam. Unregistered (the default), the movement code is unchanged.
        # OUTSIDE the `if clips:` block for the reason the ui_redraw note below
        # gives: solidity has nothing to do with animation, and a world can be
        # solid without animating anything. The test lives in vm.ACTOR, not
        # vm.entity: an actor blocks whether or not it carries scripts, and
        # keying the scan off the script-slot registry made a placed decoration
        # with no behaviour walk-through.
        L.append("        player.set_actor_block(actor.blocked)")
    if ui_redraw:
        # Paired with redraw() above: without the registration `has_redraw`
        # stays 0 and vm.core's close path clears the box and repaints nothing.
        # NOTE this sits OUTSIDE the `if clips:` block on purpose - it has
        # nothing to do with animation, and putting it inside re-nested every
        # following line under it (which silently dropped the interact gate for
        # a world with no text, and crashed on `clips.get` for a world with no
        # animations - the documented "guard every clips read" trap).
        L += ["        " + UI_REDRAW_GUARD,
              "            core.set_redraw(redraw)",
              "            core.set_redraw_cols(redraw_cols)",
              # The ROW BAND is what the close prefers; the column walk stays
              # as the fallback for a shell generated before it existed.
              "            core.set_redraw_rows(redraw_rows)",
              "        }"]
    if emote:
        # The emote pack: where its bubble lives, how to upload an emote's art,
        # and the three calls OP_A_EMOTE routes through. set_lift is what puts
        # the bubble on top of a TALL sprite rather than through its chest -
        # the reference engine stores the same thing per sprite as `emote_origin.y =
        # -canvasHeight`. Only meaningful with clips (which own the per-kind
        # metasprite sizes); without them every actor is one 16 px sprite.
        L.append("        emote.setup(EMOTE_OAM, EMOTE_TILE, emotes.upload)")
        if clips:
            L.append("        emote.set_lift(canim.actor_px_h)")
        L.append("        core.set_emote(emote.show, emote.active, emote.update)")
    if info.get("uses_curtain"):
        # The window OVERLAY CURTAIN. vm.fx already banks and already carries
        # the per-console fork, so the interpreter keeps a two-line arm.
        L.append("        core.set_curtain(fx.overlay)")
    if info.get("uses_stop_update") and sel["has_obj_update"]:
        # `actor_stop_update` kills an actor's On Update thread, whose handle
        # lives in vm.entity. vm.core cannot import that pack (a game with no
        # slot scripts must link none of it), so the op goes through this seam.
        L.append("        core.set_stop_update(entity.stop_update)")
    if info.get("uses_start_update") and sel["has_obj_update"]:
        # ...and its START half, the same seam shape (vm.entity owns the
        # handles and the update entry each actor was registered with).
        L.append("        core.set_start_update(entity.start_update)")
    if info.get("has_bkg_anim"):
        L.append("        core.set_bkg_anim(scenes.anim_tick)")
    elif info.get("has_scene_bkg_anim"):
        # PER-SCENE animated tiles: the seam takes no arguments, so the room
        # comes from the shell, which is the only thing that knows it. One
        # wrapper rather than widening `set_bkg_anim` - that seam is in
        # vm.core, which is resident image on the GB family.
        L.append("        core.set_bkg_anim(bkg_anim)")
    if info.get("has_repl_tile"):
        # The SCRIPT-driven tile-data write (the reference engine's
        # EVENT_REPLACE_TILE_XY). Takes the room as well as (dst, src): on
        # SMS/GG a background tile's pixels carry its palette, so the writer
        # looks the destination cell's slot up in that scene's own table.
        L.append("        core.set_bkg_tile(scenes.replace_tile)")
    if info.get("has_pal_write"):
        # The RUNTIME palette write (the reference engine's EVENT_PALETTE_SET_*). The
        # library table and this writer are both in the generated scenes
        # module, so the write banks with it instead of pinning the colours.
        L.append("        core.set_pal_write(scenes.set_palette)")
    if info.get("has_menu"):
        L.append("        core.set_choice(scripts.render_choice)")
    if info.get("uses_projectile"):
        # Opt into the projectile pool (a script fires a `projectile`); wire its
        # native flight + render. When the world also has On Hit slots, register the
        # native projectile<->actor hit dispatch (entity.on_projectile_hit).
        L.append("        core.set_projectiles(projectile.launch, projectile.update, "
                 "projectile.render)")
        if info.get("proj_cell"):
            # The launch sheet's cell is bigger than one hardware object, so
            # the pool must fan it - see vm.projectile.set_cell.
            L.append("        projectile.set_cell(%d, %d)" % info["proj_cell"])
        if info.get("proj_desc_kind") is not None:
            # PER-OBJECT art (the reference engine's own model): the shot draws through
            # the generated clips table, so its frames are object lists over a
            # deduped pool rather than a rectangle repeated per frame. The
            # table stays inside the clips module that banks it, which is why
            # the seam takes `clips.draw` rather than a table of its own.
            L.append("        projectile.set_desc(clips.draw, %d)"
                     % info["proj_desc_kind"])
        if info.get("proj_box"):
            # ...and the box it COLLIDES with, which is not the cell: GB
            # Studio gives a projectile its own authored `bounds` and tests
            # those, exactly as it tests the target's.
            L.append("        projectile.set_box(%d, %d, %d, %d)"
                     % info["proj_box"])
        # The TOP of the OAM range the pool may use: the same ceiling the actor
        # allocator packs fans against. Without it the pool sizes itself against
        # the whole table and a shot can fan over the emote reservation - and,
        # far worse, its block used to be dragged DOWN into the actor fans when
        # it did not fit. It gives up SHOTS now instead, which is why this
        # ceiling has to be the real one.
        L.append("        projectile.set_top(%s)" % c.ACTOR_OAM_TOP)
        if info.get("has_obj_hit"):
            L.append("        projectile.set_hit(entity.on_projectile_hit)")
        if info.get("uses_player_hit"):
            L.append("        projectile.set_player_hit(core.fire_player_hit)")
        if info.get("uses_proj_angle"):
            # An ANGLE launch is its own seam (see core.set_proj_angle) and
            # pulls in vm.trig's sine table with it.
            L.append("        core.set_proj_angle(projectile.launch_angle)")
        if info.get("uses_proj_anim"):
            # A launch with `frames` (the reference engine's loopAnim - the rotating
            # shot): the PROJ_ANIM latch reaches the pool through its seam.
            L.append("        core.set_proj_anim(projectile.set_anim)")
        if info.get("uses_proj_group"):
            # A launch that names its OWN collision group (the reference engine's
            # projectile def carries one beside its mask): the struck actor's
            # On Hit reads it as thread argument 0, which is how one script
            # serves "hit by group 1" and "hit by group 2" separately.
            L.append("        core.set_proj_group(projectile.set_group)")
    if info.get("uses_atan2"):
        # The `atan2()` expression. Wired here rather than imported by vm.core,
        # which cannot bank on the GB family - see core.set_atan2.
        L.append("        core.set_atan2(trig.atan2)")
    if cgb_fade:
        # register the colour fade BEFORE the first load_room, so the boot
        # room's own black-out + ramp already run in colour.
        L += ["        " + PAL_FADE_GUARD,
              "            fx.set_pal(pal_fade)",
              "        }"]
    if fade:
        # The fade-OUT half: vm.core ramps the room being LEFT to black at its
        # pending-exception service point, before it calls load_room. Same hold
        # as the ramp-in below, so the two halves of a transition match.
        L.append("        core.set_fade(FADE_HOLD)")
    if c.fade_style:
        # The fade DIRECTION (`[scenes] fade_style`), in the reference engine's own
        # numbering (0 = towards white, 1 = towards black). Before the first
        # load_room, so the boot room's own black-out + ramp already go that
        # way; a script's `set_state fade_style` may flip it later. This call
        # is also what makes the build state VM_FADE_STYLE (mosaik8_build
        # scans the sources for it), the fold vm.fx's white arms live under.
        L.append("        fx.set_style(%d)   %s%s\""
                 % (0 if c.fade_style == "white" else 1, FADE_STYLE_MARKER,
                    c.fade_style))
    if c.overlay_cut:
        # THE OVERLAY CUT's project default (`[scenes] overlay_cut`, W7d phase
        # 2), in the reference engine's own units: the SCANLINE at which the window
        # overlay stops, where the layer goes off and the sprites come back.
        # Before the first load_room, so the boot room already draws with it;
        # a script's `set_state overlay_cut` may move it later. A no-op on
        # every console with no GB-style window layer.
        L.append("        text.win_overlay_cut(%d)   %s%d"
                 % (c.overlay_cut, OVERLAY_CUT_MARKER, c.overlay_cut))
    if info.get("uses_save"):
        # Opt into SAVE/LOAD. vm.core's OP_SAVE / OP_LOAD and the
        # `save_exists()` state all run through this seam and are no-ops until
        # it is registered - so a project whose scripts save was building a ROM
        # that silently did not, and whose "Continue" menu could never appear
        # (an RPG conversion). Registered
        # BEFORE set_change, because vm.sram's restore requests a room change
        # and load_room is what serves it.
        # The build id seals every save (vm.sram's check byte): a save from
        # a build with different scripts reads as "no save".
        L.append("        sram.set_build(%d)" % int(info.get("save_build_id") or 0))
        L.append("        core.set_save(sram.persist, sram.restore, "
                 "sram.has_save)")
        # ...and the SLOT half (W7c), behind the build's own flag so the call
        # FOLDS AWAY for a project that only ever uses slot 0 - which also
        # stops `sram.set_slot` / `clear` / `peek` being address-taken, so
        # tree-shaking drops them too. The flag is derived from the BLOB (the
        # `save_slot` state or either new opcode), so the world fact that needs
        # the seam is what turns it on; an undecodable blob keeps the call,
        # which is the safe direction.
        L += ["        if VM_SAVE_SLOTS {",
              "            core.set_save_slots(sram.set_slot, sram.clear, "
              "sram.peek)",
              "        }"]
    L.append("        core.set_change(load_room)")
    L += ["        var pxs: u16 = 80",
          "        var pys: u16 = 72"]
    if has_pk:
        L += ["        for i in 0..scenes.OBJ_COUNT {",
              "            if scenes.obj_scene_at(i) == start_room {",
              "                if scenes.obj_kind_at(i) == scenes.KIND_PLAYER {",
              "                    pxs = scenes.obj_x_at(i)",
              "                    pys = scenes.obj_y_at(i)",
              "                }",
              "            }",
              "        }"]
    L += ["        load_room(start_room, pxs, pys)",
          "    }",
          "",
          "    export START, start, load_room, solid_at"
          + (", set_on_load" if info.get("on_load_hook") else ""),
          "}",
          ""]

