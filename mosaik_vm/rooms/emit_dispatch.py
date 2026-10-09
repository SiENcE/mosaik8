"""mosaik_vm.rooms.emit_dispatch - the per-scene-type handler branches re-picked on
every room change, plus the fade ramp and the on-load hook.

One section of the generated `rooms` module. `emit(c, L)` appends its
lines to `L`, reading the derived world shape off the RoomsCtx `c`
(see rooms.context)."""
from .config import BKG_TILES, _dim_h, _dim_w, _pylim, _rowlim


def emit(c, L):
    """The scene-type handler dispatch, the fade-in ramp and the UI redraw."""
    info = c.info
    use_player, uniform, types, clips = c.use_player, c.uniform, c.types, c.clips
    wide, wide_shmup, roam, parallax = c.wide, c.wide_shmup, c.roam, c.parallax
    fade, p, FADE_GUARD, PARALLAX_GUARD = c.fade, c.p, c.FADE_GUARD, c.PARALLAX_GUARD
    UI_REDRAW_GUARD, ui_redraw = c.UI_REDRAW_GUARD, c.ui_redraw

    def _pump(ind):
        """The music pump at indent `ind`, or nothing without a driver.

        A room load blocks for many DISPLAY frames inside one VM frame and
        vm.core advances the song only from run()'s per-frame arm, so the music
        stops for the whole load. Empty for a world with no songs, which keeps
        its generated rooms.mos byte-identical."""
        return ["%score.music_pump()" % ind] if info.get("music") else []

    # effective room size in px (per-scene sizes honoured on a non-uniform world)
    if use_player:
        if uniform:
            L += ["        var mw: u16 = scenes.MAP_W",
                  "        var mh: u16 = scenes.MAP_H"]
        else:
            L += ["        var mw: u16 = scenes.SCENE_W[rm]",
                  "        var mh: u16 = scenes.SCENE_H[rm]"]
        L += ["        mw = mw * 8",
              "        mh = mh * 8"]

    # the handler pick: ONLY the branches for the scene types this world uses.
    branches = []          # (cond or None, body-lines)

    def _cam_lines():
        return ["var maxx16: u16 = 0",
                "var maxy16: u16 = 0",
                "if mw > SCREEN_WIDTH {",
                "    maxx16 = mw - SCREEN_WIDTH",
                "}",
                "if mh > SCREEN_HEIGHT {",
                "    maxy16 = mh - SCREEN_HEIGHT",
                "}",
                "var maxx: u8 = maxx16",
                "var maxy: u8 = maxy16",
                "player.set_camera(maxx, maxy)"]

    # The reference's POINT_N_CLICK_CAMERA_DEADZONE (the reference VM's `src/states/
    # pointnclick.c`), which its `pointnclick_init` writes on every load of such
    # a scene along with a ZERO follow offset. Both are ordinary camera
    # properties on our side (W7b), so they are emitted as four writes rather
    # than built into the handler - a hand-authored room can then tune them,
    # and the dead zone is dropped by the next room's setup exactly as the reference VM's
    # `camera_reset()` drops it.
    PNC_DEADZONE = 24

    for t in types:
        if t in ("topdown", "adventure", "pointnclick"):
            cond = ("scenes.scene_type_at(rm) == scenes.SCTYPE_%s" % t.upper())
            if roam:
                # A room past the hardware background on EITHER axis roams;
                # a room that fits keeps the paint path, so the choice is per
                # ROOM (the same rule the wide platform arm follows).
                body = _cam_lines() + [
                    "if mw > BKG_PX or mh > %s {" % _pylim(info)]
                def _roam_plain(ind):
                    """The plain 2D-streamed setup, at indent `ind`."""
                    out = []
                    if info.get("tile_pal"):
                        # A streamed room is never painted, so paint_attrs
                        # cannot colour it - the attribute rides the
                        # column/row writes.
                        out += ["%sscroll2d.attrs = 1" % ind]
                    # SET UP FIRST, THEN SEED THE RING AT THE ENTRY CAMERA.
                    # `fill2d` points the ring at the MAP ORIGIN, and the
                    # camera is derived from the player's entry position - so
                    # filling first meant the room-load fade revealed the map's
                    # top-left corner and the first game frame then streamed
                    # every column and row in between, on screen. The reference engine has
                    # no such window: its scene_init runs camera_update() and
                    # repaints from the settled camera. `set_scroll2d` stays
                    # LAST, so nothing streams while the camera is being found.
                    out += [
                        "%splayer.setup_roam(PTILE, px, py, PW, PH, WALK, "
                        "maxx16, maxy16, solid_at)" % ind,
                        "%splayer.cam_seed()" % ind,
                        "%sscroll2d.seed2d(player.cam_x(), player.cam_y(), "
                        "%s, %s)"
                        % (ind, _dim_w("rm", uniform), _dim_h("rm", uniform)),
                        "%sscroll2d.refill2d(tile_at)" % ind]
                    out += _pump(ind)
                    out += ["%splayer.set_scroll2d(stream2)" % ind]
                    return out

                if parallax:
                    # PARALLAX in a topdown room. Bands stream COLUMNS only -
                    # the reference engine's own parallax does too (`scroll_load_col` per
                    # band) - so this arm is taken only when the room is wide
                    # but NOT taller than the background; a taller one needs
                    # the row streaming scroll2d does and keeps its plain
                    # scroll (reported by the importer).
                    #
                    # The plain path is emitted in BOTH branches of the
                    # platform fork on purpose: only a real `if platform`
                    # keeps the scrollpx call out of a non-GB build's C (a
                    # runtime test leaves the reference live for
                    # tree-shaking). One folds away per target.
                    body += ["    " + PARALLAX_GUARD,
                             "        if scenes.px_count(rm) > 0 and mh <= %s {"
                             % _pylim(info),
                             "            arm_parallax(rm)"]
                    if info.get("tile_pal"):
                        body += ["            scrollpx.attrs = 1"]
                    body += [
                        # Setup first, then seed at the entry camera - see
                        # _roam_plain. Each BAND takes its own cursor from its
                        # own shifted camera, which is `scrollpx.seed`'s job.
                        "            player.setup_roam(PTILE, px, py, PW, PH, "
                        "WALK, maxx16, maxy16, solid_at)",
                        "            player.cam_seed()",
                        "            scrollpx.seed(player.cam_x(), %s)"
                        % _dim_w("rm", uniform),
                        "            scrollpx.refill(gatherpx)"]
                    body += _pump("            ")
                    body += [
                        # The ROAM seam, not the wide one: vm.player's roam
                        # branch calls g_stream2 and never g_stream, so
                        # registering the 1-arg seam here would simply never
                        # fire and the level would not stream at all.
                        "            player.set_scroll2d(stream2px)",
                        "        } else {"]
                    body += _roam_plain("            ")
                    body += ["        }",
                             "    } else {"]
                    body += _roam_plain("        ")
                    body += ["    }"]
                else:
                    body += _roam_plain("    ")
                body += [
                    "} else {",
                    "    player.setup(PTILE, px, py, PW, PH, WALK, solid_at)",
                    "}"]
            else:
                body = _cam_lines() + [
                    "player.setup(PTILE, px, py, PW, PH, WALK, solid_at)"]
            # POINT-AND-CLICK shares every line above (W7j): the room is set up
            # by the SAME topdown / roam / parallax fork, and only the per-frame
            # move is swapped - which is what keeps a streamed cursor room
            # working for free. Appended AFTER the setups, because every setup*
            # runs vm.player's clear_wide and that is what drops both the cursor
            # flag and the dead zone.
            if t == "pointnclick":
                body += [
                    "player.set_cursor(mw, mh)",
                    "player.set_cam_prop(0, %d)" % PNC_DEADZONE,
                    "player.set_cam_prop(1, %d)" % PNC_DEADZONE,
                    "player.set_cam_prop(2, 0)",
                    "player.set_cam_prop(3, 0)"]
                if c.has_ent:
                    body.append("entity.set_cursor(1)")
            body.append("core.set_player(tick_%s)"
                        % ("pointnclick" if t == "pointnclick" else "topdown"))
        elif t == "platform":
            cond = "scenes.scene_type_at(rm) == scenes.SCTYPE_PLATFORM"
            if wide:
                # WIDE platform rooms (wider than the 32-tile hardware
                # background) column-stream through engine.scroll instead of
                # painting once; narrow rooms in the SAME world keep the paint
                # path, so wideness is decided per ROOM, not per world.
                body = _cam_lines() + [
                    "if mw > BKG_PX {"]
                def _wide_plain(ind):
                    """The plain column-streamed setup, at indent `ind`."""
                    out = []
                    if info.get("tile_pal"):
                        out += ["%sscroll.attrs = 1" % ind]
                    # Setup first, then seed at the entry camera - see
                    # _roam_plain for why. `set_wide_vcam` moves UP here too:
                    # `cam_seed` derives the vertical camera from
                    # `cam_maxy_wide`, so a room that has one must declare it
                    # before the seed or the ring is placed at a y of 0.
                    out += [
                        "%splayer.setup_wide(PTILE, px, py, PW, PH, RUN, JUMP, "
                        "GRAV, MAXFALL, maxx16, solid_at)" % ind,
                        "%sif maxy16 > 0 {" % ind,
                        "%s    player.set_wide_vcam(maxy16)" % ind,
                        "%s}" % ind,
                        "%splayer.cam_seed()" % ind,
                        "%sscroll.seed(player.cam_x(), %s, srows(rm))"
                        % (ind, _dim_w("rm", uniform)),
                        "%sscroll.refill(gather)" % ind]
                    out += _pump(ind)
                    out += ["%splayer.set_scroll(stream)" % ind]
                    return out

                if parallax:
                    # A wide room with BANDS streams through engine.scrollpx
                    # instead: same ring, one cursor per band. The fork is per
                    # ROOM, so a world can mix parallax and plain wide rooms -
                    # and per PLATFORM, because the scanline interrupt is only
                    # real on the GB family (see PARALLAX_GUARD). The plain
                    # path is emitted in both branches: one folds away.
                    body += ["    " + PARALLAX_GUARD,
                             "        if scenes.px_count(rm) > 0 {",
                             "            arm_parallax(rm)"]
                    if info.get("tile_pal"):
                        body += ["            scrollpx.attrs = 1"]
                    body += [
                        "            player.setup_wide(PTILE, px, py, PW, PH, "
                        "RUN, JUMP, GRAV, MAXFALL, maxx16, solid_at)",
                        "            if maxy16 > 0 {",
                        "                player.set_wide_vcam(maxy16)",
                        "            }",
                        "            player.cam_seed()",
                        "            scrollpx.seed(player.cam_x(), %s)"
                        % _dim_w("rm", uniform),
                        "            scrollpx.refill(gatherpx)"]
                    body += _pump("            ")
                    body += [
                        "            player.set_scroll(streampx)",
                        "        } else {"]
                    body += _wide_plain("            ")
                    body += ["        }",
                             "    } else {"]
                    body += _wide_plain("        ")
                    body += ["    }"]
                else:
                    body += _wide_plain("    ")
                body += [
                    "} else {",
                    "    player.setup_platform(PTILE, px, py, PW, PH, RUN, "
                    "JUMP, GRAV, MAXFALL, solid_at)",
                    "}"]
            else:
                body = _cam_lines() + [
                    "player.setup_platform(PTILE, px, py, PW, PH, RUN, JUMP, GRAV, MAXFALL, solid_at)"]
            # THE SCRIPTED MOVE SPEED, and a PLATFORM room is the one arm that
            # never set it. `pspeed` is what `player.step_to` walks a scripted
            # PLAYER_MOVE_TO at, and it is the reference engine's `PLAYER.move_speed` -
            # a field separate from the platform run velocity, exactly as ours
            # is separate from `prun` (its platform handler uses the
            # `plat_walk_vel` engine field; only the scripted move reads
            # move_speed). `setup` sets it from WALK for topdown and
            # `setup_shmup` forwards its own speed, but `setup_platform` /
            # `setup_wide` take no walk argument at all - so a platform room
            # inherited whatever the last TOPDOWN room left, or 1 (step_to's
            # floor) if none had run yet. Measured on the reference-engine sample conversion's
            # walk-in room doorway: the scripted walk to the town ran
            # at 0.39 px per LCD frame against the reference ROM's 1.00
            # (`start_player_move_speed` = 32 subpx = 1 px per LCD frame), and
            # the figure moved with the ROUTE the player had taken to get
            # there. One line, once, covering BOTH arms of the fork above.
            body.append("player.set_speed(WALK)")
            # one registration covers BOTH arms of the wide/narrow fork above
            if info.get("has_platform_cells"):
                body.append("player.set_platform_cells(platform_at)")
            # LADDERS: one registration carries the probe AND the climb speed,
            # because a world that paints ladders always needs both.
            if info.get("has_ladder_cells"):
                body.append("player.set_ladder_cells(ladder_at, CLIMB)")
            if p["step_up"] > 0:
                body.append("player.set_step_up(%d)" % p["step_up"])
            if p["air_jumps"] > 0:
                body.append("player.set_air_jumps(%d)" % p["air_jumps"])
            # the variable-height jump feel (height / coyote / buffer). Emitted only
            # when authored, so a plain platformer stays byte-identical (0/0/0).
            if p["jump_hold"] or p["coyote"] or p["jump_buffer"]:
                body.append("player.set_jump_feel(%d, %d, %d)"
                            % (p["jump_hold"], p["coyote"], p["jump_buffer"]))
            # momentum horizontal run (accel/decel/air-control) -- emitted only when
            # run_accel is authored, else the classic instant run stays.
            if p["run_accel"] > 0:
                body.append("player.set_run_accel(%d, %d, %d)"
                            % (p["run_accel"], p["run_decel"], p["air_control"]))
            # the knockback impulse -- emitted only when authored, so the
            # player_knockback op stays a no-op for everyone else.
            if p["knockback_frames"] > 0:
                body.append("player.set_knockback(%d, %d, %d)"
                            % (p["knockback_x"], p["knockback_y"],
                               p["knockback_frames"]))
            # wall slide + wall jump -- emitted only when wall_slide is authored.
            if p["wall_slide"] > 0:
                body.append("player.set_wall_jump(%d, %d, %d)"
                            % (p["wall_slide"], p["wall_jump_x"], p["wall_jump_y"]))
            body.append("core.set_player(tick_platform)")
        elif t == "shmup":
            cond = "scenes.scene_type_at(rm) == scenes.SCTYPE_SHMUP"
            body = ["-- auto-scroll: a map taller than the screen scrolls UP",
                    "-- (classic vertical shmup), else it scrolls RIGHT.",
                    "var sdir: u8 = 0",
                    "var smax: u16 = 0",
                    "if mh > SCREEN_HEIGHT {",
                    "    sdir = 1",
                    "    smax = mh - SCREEN_HEIGHT",
                    "} else if mw > SCREEN_WIDTH {",
                    "    smax = mw - SCREEN_WIDTH",
                    "}"]
            tall = []
            if c.tall_shmup:
                # A shmup stage TALLER than the hardware background: one long
                # vertical map whose ROWS stream through engine.scroll2d (the
                # roam streamer) under the shmup's own auto-scroll camera, so
                # objects can be placed along the whole stage and are drawn
                # against the camera that is really scrolling. Per ROOM: the
                # 32-row stages (and the endless loop) keep their painted path.
                # `smax` is already the vertical bound here (mh > screen).
                tall += ["if mh > %s {" % _pylim(info)]
                if info.get("tile_pal"):
                    tall += ["    scroll2d.attrs = 1"]
                tall += [
                    "    player.setup_tall_shmup(PTILE, px, py, PW, PH, SH_SPEED, "
                    "SH_PACE, smax, solid_at)",
                    # Seed the ring at the BOTTOM: the auto-scroll camera starts
                    # there by definition, so it is the entry camera (the follow
                    # arms seed at cam_seed for the same reason).
                    "    scroll2d.seed2d(0, smax, %s, %s)"
                    % (_dim_w("rm", uniform), _dim_h("rm", uniform)),
                    "    scroll2d.refill2d(tile_at)"]
                tall += _pump("    ")
                tall += ["    player.set_scroll2d(stream2)"]
            if wide_shmup:
                # A shmup level WIDER than the hardware background column-streams,
                # exactly as the wide platformer does - the difference is only which
                # camera drives it (an auto-scroll instead of a follow). Per ROOM, so
                # a narrow shmup in the same world keeps the painted path.
                if tall:
                    body += tall
                body += [("} else if mw > BKG_PX {" if tall else "if mw > BKG_PX {"),
                         # The bound above is whichever axis the NARROW handler
                         # would scroll, and for a room taller than the screen
                         # that is the vertical one. A streamed room scrolls
                         # HORIZONTALLY by construction, so it needs its own -
                         # handing it the vertical bound stopped the camera dead
                         # on a 64x32 room (measured: SCX never left 0).
                         "    var wmax: u16 = 0",
                         "    if mw > SCREEN_WIDTH {",
                         "        wmax = mw - SCREEN_WIDTH",
                         "    }"]
                if info.get("tile_pal"):
                    body += ["    scroll.attrs = 1"]
                body += [
                    # This one keeps `fill` (the origin seed) on purpose, unlike
                    # the follow-camera arms above: an AUTO-SCROLL camera starts
                    # at the level's beginning by definition, so the origin IS
                    # the entry camera and there is nothing to catch up on.
                    "    scroll.fill(%s, srows(rm), gather)" % _dim_w("rm", uniform),
                    "    player.setup_wide_shmup(PTILE, px, py, PW, PH, SH_SPEED, "
                    "SH_PACE, wmax, solid_at)",
                    "    player.set_scroll(stream)",
                    "} else {",
                    "    player.setup_shmup(PTILE, px, py, PW, PH, SH_SPEED, sdir, "
                    "SH_PACE, smax, solid_at)",
                    "}"]
            elif tall:
                body += tall
                body += ["} else {",
                         "    player.setup_shmup(PTILE, px, py, PW, PH, SH_SPEED, "
                         "sdir, SH_PACE, smax, solid_at)",
                         "}"]
            else:
                body += ["player.setup_shmup(PTILE, px, py, PW, PH, SH_SPEED, sdir, "
                         "SH_PACE, smax, solid_at)"]
            body.append("core.set_player(tick_shmup)")
        elif t in ("menu", "logo"):
            cond = ("scenes.scene_type_at(rm) == scenes.SCTYPE_%s" % t.upper())
            body = ["core.clear_player()"]
            if use_player:
                body.append("player.hide()")
                # A player-less room runs NO player.setup_*, and the setups
                # are what clear the per-room view modes -- so a wide room's
                # camera / stream state used to survive into a menu opened
                # from it (the menu painted at the old scroll offset with the
                # previous room's streamed columns showing beside it).
                body.append("player.reset_view()")
                # `clips` is None for a world with no animations at all, which
                # is the documented "guard every clips read" trap - an
                # unguarded .get() here crashed generate_rooms for any world
                # with a menu/logo room and no [animations].
                if clips and clips.get("player"):
                    # ... and STOP ANIMATING IT. `core.clear_player()` only
                    # stops vm.player.update() running; vm.canim's tick_player
                    # is driven from the anim seam and re-asserts the player's
                    # metasprite EVERY frame ("re-assert every frame", the Lynx
                    # present needs it), so it put the hidden player straight
                    # back on screen the frame after hide(). Measured on the GB
                    # Studio conversion: two OAM objects (its 8x16 fan) parked
                    # at screen (0,0) for the whole Logo screen. 255 is the
                    # documented "no player clip" sentinel that tick_player
                    # already returns on, so this costs nothing new - and the
                    # next real room's `canim.set_player(pk)` above re-arms it
                    # (a per-scene player kind), or the re-bind below does.
                    body.append("canim.set_player(255)")
        else:
            continue
        branches.append((cond, body))

    # ...AND RE-ARM IT in every room that HAS a player. With per-scene player
    # kinds, load_room's `canim.set_player(pk)` does that on every load. With
    # ONE player sheet, `start()` binds the clip exactly once, so the first
    # menu or logo room unbound it for the rest of the game: the player kept
    # moving and publishing its facing, but vm.canim's tick_player returned on
    # the 255 sentinel and the fan kept whatever frame it last drew. The reference engine
    # 4 projects start on a LOGO scene often enough that this froze the player
    # in its front pose from the first room on (the adventure check project). Emitted only
    # where the unbind above is, so a world with no menu/logo room is
    # byte-identical.
    if (use_player and clips and clips.get("player") and not c.pkinds
            and any(t in ("menu", "logo") for t in types)):
        for cond, body in branches:
            if "canim.set_player(255)" not in body:
                body.insert(0, "canim.set_player(scenes.KIND_PLAYER)")

    if len(branches) == 1:
        L += ["        " + s for s in branches[0][1]]
    elif branches:
        for i, (cond, body) in enumerate(branches):
            last = i == len(branches) - 1
            if i == 0:
                L.append("        if %s {" % cond)
            elif last:
                L.append("        } else {")
            else:
                L.append("        } else if %s {" % cond)
            L += ["            " + s for s in body]
        L.append("        }")
    if c.has_scene_init:
        # THE SCENE INIT SPAWNS LAST, after one scheduler pass over the actor
        # On Init threads - the reference engine's own load ordering (activate_actor_impl
        # script_executes an actor's On Init during scene load, before the
        # scene script runs). Spawned FIRST, a converted cutscene whose init
        # opens with `lock` froze every On Init before its first instruction -
        # under a lock run_scripts runs only the owner - so all eight of
        # the platformer conversion's opening-cutscene hides never ran and its "hidden until the
        # camera settles" actors were visible from frame one, sliding against
        # the parallax bands exactly as the reference engine's own comment warns. The
        # pass also lets a short On Init END and free its context, which is
        # what fits 8 actor inits + a scene init into the 8-context pool. And
        # all of it runs BEFORE the fade-in below, so a hidden actor never
        # shows for a frame.
        if c.has_ent:
            L.append("        core.run_pending()")
        L += ["        var si: u16 = scenes.scene_init(rm)",
              "        if si != scenes.NO_SCRIPT {",
              "            core.spawn(si)",
              "        }"]
    if fade:
        # ...AND SETTLE THE CAMERA FIRST. The scroll register is written by the
        # PLAYER HANDLER, which does not run until load_room returns - so every
        # frame of the ramp below was drawn through the PREVIOUS room's scroll
        # Measured entering a small house room from the town room
        # at SCX 228 / SCY 236: sixteen LCD frames of the new room shown at the
        # old room's offset, and because the hardware tilemap is 32 cells wide
        # while a small room paints only 20 of them, the visible window
        # straddled the painted region and the stale columns either side - the
        # reported "left portion in another room's art". The tilemap, the tile
        # DATA and the CGB attribute map were all already correct.
        #
        # This is the same defect `player.reset_view()` was written for one
        # room type over ("a UI room runs NO setup at all - so a wide room's
        # camera, stream flags and scroll register survived into it"); the
        # player-less arm was fixed and the arm that HAS a camera to settle was
        # not, because its handler fixes it a few frames later - after the fade
        # has already shown it. `settle_camera` stands down on a player-less
        # room, whose camera reset_view has already zeroed.
        #
        # Emitted only where the fade is: with no fade there is no ramp to be
        # early for, and the first handler frame is the first visible one
        # anyway - so a non-fading project stays byte-identical.
        # AUTO-FADE IN: the room was painted under a black palette (set at the
        # top of load_room), so ramp it up now that everything is in place.
        # Before the shell's on_load hook, because vm.fx darkens OBP0/OBP1 as
        # well as BGP and the hook is what re-asserts a game's real sprite
        # palettes - running the ramp after it would leave them on the fade's
        # identity value. Nothing is displayed between the two, so the
        # ordering costs no visible frame.
        # The `music_pump` inside the wait is where most of the audible gap
        # went: this loop is the longest wall-clock stretch of a room load and
        # it waits a whole display frame at a time, so pumping here ticks the
        # song exactly once per elapsed frame - the same rate run() gives it.
        _pump = ["                    core.music_pump()"] if info.get("music") else []
        L += ["        " + FADE_GUARD,
              "            player.settle_camera()",
              "            var fl: u8 = 3",
              "            while fl > 0 {",
              "                for fw in 0..FADE_HOLD {",
              "                    video.wait_vblank()"]
        L += _pump
        L += ["                }",
              "                fl = fl - 1",
              "                fx.set_level(fl)",
              "            }",
              "        }"]
    if info.get("on_load_hook"):
        # LAST in load_room, so the shell's hook overrides anything the load
        # path itself wrote (see set_on_load above).
        L += ["        if g_has_on_load == 1 {",
              "            g_on_load()",
              "        }"]
    L += ["    }", ""]

    def _col_streamed():
        """Is the CURRENT room one engine.scroll streams (columns) rather than
        engine.scroll2d (rows too)? A wide PLATFORM room always; a SHMUP room
        when it is wide but no taller than the background (a TALL shmup room
        streams rows). Keyed on the platform type alone it sent a wide shmup
        room to scroll2d, which never seeded it. Emitted with the shmup half
        only when the world has a shmup room, so every other world's text is
        unchanged."""
        cond = "scenes.scene_type_at(room) == scenes.SCTYPE_PLATFORM"
        if "shmup" in types:
            cond += (" or (scenes.scene_type_at(room) == scenes.SCTYPE_SHMUP"
                     " and %s <= %s)" % (_dim_h("room", uniform), _rowlim(info)))
        return cond

    # redraw(): put the scene back where a closing dialogue box/menu blanked it.
    # SMS/GG only -- see UI_REDRAW_GUARD. A world with no UI never needs it.
    if ui_redraw:
        L += ["    " + UI_REDRAW_GUARD,
              "        -- A dialogue box or menu here is plotted INTO the scene",
              "        -- tilemap (these consoles have no window overlay to put it",
              "        -- on), so closing it leaves the box's cells blank -- vm.core",
              "        -- clears what it drew and cannot know what the scene had",
              "        -- there. core.set_redraw calls this on every close.",
              "        function redraw() {"]
        if wide or roam:
            cond = "%s > %d" % (_dim_w("room", uniform), BKG_TILES)
            if roam:
                cond += " or %s > %s" % (_dim_h("room", uniform), _rowlim(info))
            L += ["            if %s {" % cond,
                  "                -- streamed: re-seed the ring from the CURRENT",
                  "                -- camera (fill/fill2d would reset it to the map",
                  "                -- origin and teleport the level)."]
            if wide and roam:
                L += ["                if %s {" % _col_streamed(),
                      "                    scroll.refill(gather)",
                      "                } else {",
                      "                    scroll2d.refill2d(tile_at)",
                      "                }"]
            elif wide:
                L += ["                scroll.refill(gather)"]
            else:
                L += ["                scroll2d.refill2d(tile_at)"]
            L += ["                return",
                  "            }"]
        L += ["            scenes.paint(room)"]
        if info.get("tile_pal"):
            L += ["            scenes.paint_attrs(room)"]
        L += ["        }"]
        # ...and the SLICED form of the same thing. A whole-ring refill is ~24
        # display frames inside ONE game frame (measured on the SMS/GG sample conversion),
        # which is the box-close freeze; vm.core walks this a few columns at a
        # time instead. Columns are the streamer's own unit, so a slice needs
        # nothing about rows, scroll or box geometry. A room that PAINTS (small
        # enough not to stream) has no column cursor, so it does the one paint
        # on the first slice and nothing after.
        L += ["        -- The column-SLICED redraw: vm.core repaints the scene a",
              "        -- few ring columns per frame on a box close, so no single",
              "        -- game frame carries the whole ~24-display-frame refill.",
              "        function redraw_cols(from: u8, n: u8) {"]
        if wide or roam:
            cond = "%s > %d" % (_dim_w("room", uniform), BKG_TILES)
            if roam:
                cond += " or %s > %s" % (_dim_h("room", uniform), _rowlim(info))
            L += ["            if %s {" % cond]
            if wide and roam:
                L += ["                if %s {" % _col_streamed(),
                      "                    scroll.refill_cols(from, n, gather)",
                      "                } else {",
                      "                    scroll2d.refill_cols2d(from, n, tile_at)",
                      "                }"]
            elif wide:
                L += ["                scroll.refill_cols(from, n, gather)"]
            else:
                L += ["                scroll2d.refill_cols2d(from, n, tile_at)"]
            L += ["                return",
                  "            }"]
        L += ["            if from == 0 {",
              "                scenes.paint(room)"]
        if info.get("tile_pal"):
            L += ["                scenes.paint_attrs(room)"]
        L += ["            }",
              "        }"]
        # ...and the ROW-BAND form, which is what a box close actually needs.
        # The box wrote `h` rows of the ring and nothing else, so the column
        # walk above repaints 32x28 cells to undo 32x3. MEASURED on
        # vm-uiscroll's Game Gear teardown: 43 display frames, of which the
        # per-cell tile writes alone are 24 and the gather 8. A row does not
        # wrap the ring (the name table is exactly 32 wide), so the 2D arm
        # writes ONE rectangle per row where its column path cannot.
        L += ["        -- The ROW-BAND redraw: the box damaged `h` screen rows,",
              "        -- so this repaints exactly those and the close is one",
              "        -- slice instead of eight.",
              "        function redraw_rows(row: u8, h: u8) {"]
        if wide or roam:
            cond = "%s > %d" % (_dim_w("room", uniform), BKG_TILES)
            if roam:
                cond += " or %s > %s" % (_dim_h("room", uniform), _rowlim(info))
            L += ["            if %s {" % cond]
            if wide and roam:
                L += ["                if %s {" % _col_streamed(),
                      "                    scroll.refill_rows(row, h, gather)",
                      "                } else {",
                      "                    scroll2d.refill_band2d(row, h, tile_at)",
                      "                }"]
            elif wide:
                L += ["                scroll.refill_rows(row, h, gather)"]
            else:
                L += ["                scroll2d.refill_band2d(row, h, tile_at)"]
            L += ["                return",
                  "            }"]
        # A room small enough to PAINT has no ring to slice: one paint repairs
        # the band along with everything else, exactly as redraw() does.
        L += ["            scenes.paint(room)"]
        if info.get("tile_pal"):
            L += ["            scenes.paint_attrs(room)"]
        L += ["        }",
              "    }",
              ""]

