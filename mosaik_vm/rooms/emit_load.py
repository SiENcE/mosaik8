"""mosaik_vm.rooms.emit_load - `load_room`, the ONE room-load path (and the
`core.set_change` callback).

One section of the generated `rooms` module. `emit(c, L)` appends its
lines to `L`, reading the derived world shape off the RoomsCtx `c`
(see rooms.context)."""
from .config import (BKG_TILES, _dim_h, _dim_w, _meta_h, _meta_w, _rowlim,
                     _wants_boxes)


# The two `trigger.add` forms. `vm.trigger` FORKS on whether a world binds an On
# Leave (VM_TRIG_ENTER_ONLY): the enter-only arm takes 5 arguments and the
# falling-edge arm 6, and ONE world fact picks the arm and the call - see
# `lib/vm/trigger.mos` and `mosaik8_build._vm_dispatch_defines`.
_TRIG_ADD = ("                trigger.add(ttx * 8, tty * 8, tws, ths, "
             "scenes.trigger_enter(i))")
_TRIG_ADD_LEAVE = ("                trigger.add(ttx * 8, tty * 8, tws, ths, "
                   "scenes.trigger_enter(i), scenes.trigger_leave(i))")


def emit(c, L):
    """The load_room function: paint, doors/triggers/slots, sprite residency."""
    info = c.info
    use_player, playerless, has_objs, has_ent = c.use_player, c.playerless, c.has_objs, c.has_ent
    has_doors, has_trig, has_scene_init, has_pk = c.has_doors, c.has_trig, c.has_scene_init, c.has_pk
    has_trig_leave = c.has_trig_leave
    clips, dyn_oam, obj16, OBJ16_GUARD = c.clips, c.dyn_oam, c.obj16, c.OBJ16_GUARD
    ACTOR_OAM_TOP, tile_top, fade, FADE_GUARD = c.ACTOR_OAM_TOP, c.tile_top, c.fade, c.FADE_GUARD
    CLEAR_GUARD, PARALLAX_GUARD, res, pkinds = c.CLEAR_GUARD, c.PARALLAX_GUARD, c.res, c.pkinds
    _pk, _pk_box, cfocus, _cf_var = c._pk, c._pk_box, c.cfocus, c._cf_var
    _cf_x1, boffs, _bo_var, _bo_x0 = c._cf_x1, c.boffs, c._bo_var, c._bo_x0
    sel, wide, roam, parallax = c.sel, c.wide, c.roam, c.parallax
    uniform, emote = c.uniform, c.emote

    L += ["    -- Load room `rm`, place the player at (px,py), wire the room's",
          "    -- doors/triggers/actors, and pick the handler for its scene TYPE.",
          "    function load_room(rm: u8, px: u16, py: u16) {",
          "        room = rm"]
    if parallax:
        # DISARM first, on every room load. The scanline interrupt is hardware
        # state that outlives the room that armed it, so a room with no bands
        # would otherwise keep drawing the previous room's - and the paint
        # below would be scrolled per band while it is written. Inside the
        # platform fork like every other scrollpx call site, or the reference
        # keeps the module resident on a console that never arms it.
        L.append("        " + PARALLAX_GUARD)
        L.append("            scrollpx.arm(0)")
        L.append("        }")
    if boffs and use_player:
        # The box offset follows the room's player sprite, so it is (re-)set
        # on every load - the room after a platform room wears different art.
        if _bo_var:
            if not _bo_x0:
                L.append("        BOX = PBOX[rm]")
            L.append("        BOY = PBOY[rm]")
        L.append("        player.set_box_offset(%s, BOY)"
                 % ("0" if _bo_x0 else "BOX"))
    if cfocus and use_player:
        if _cf_var:
            if not _cf_x1:
                L.append("        CFX = PCFX[rm]")
            L.append("        CFY = PCFY[rm]")
        L.append("        player.set_cam_focus(CFX, CFY)")
    if fade:
        # AUTO-FADE: black out BEFORE anything is painted, so the room builds
        # unseen and the ramp at the end of load_room reveals it finished.
        # core.mos sets the level back to 0 just before calling this, so this
        # write is what actually holds the screen dark.
        L += ["        " + FADE_GUARD,
              "            fx.set_level(3)",
              "        }"]
    if info.get("colour"):
        # COLOUR: this room's own hardware palette SET (8 background + 8 sprite
        # slots), out of the concatenated per-scene table. Loaded BEFORE the
        # paint so the art never appears under the previous room's colours; a
        # graceful no-op on a console with one palette per layer, so this needs
        # no `if platform` fork. The offset is a WORD index into the table.
        # The tables + the two load calls live INSIDE scenes.mos
        # (scenes.load_palettes), so engine code banking can co-locate ~2 KB of
        # palette words into that module's ROM bank instead of pinning them in
        # the GB's resident image.
        L += ["        scenes.load_palettes(rm)"]
    if wide:
        # A WIDE room must NOT be painted: paint() uploads the whole map with
        # one bkg.set_tiles, and a map wider than the 32-tile hardware
        # background overruns it (writing past the tilemap and corrupting
        # what the column streamer just seeded). engine.scroll owns the
        # background for those rooms -- scroll.fill seeds the 32 visible
        # columns below and scroll.update streams the rest.
        # With [world] stream + paint_table the transpiler emits `warm(rm)`:
        # everything paint() does EXCEPT the map upload (the per-scene tileset
        # + the range windows map_tile/collision_at read). A wide room MUST
        # call it -- the window would otherwise stay cold and the level would
        # stream as empty, and its tileset would never reach VRAM.
        # With ROAM rooms in the same world the test must cover HEIGHT too:
        # a tall-but-narrow room is not "wide", but painting it overruns the
        # tilemap just the same.
        cond = "%s > %d" % (_dim_w("rm", uniform), BKG_TILES)
        if roam:
            cond += " or %s > %s" % (_dim_h("rm", uniform), _rowlim(info))
        L += ["        if %s {" % cond,
              "            -- streamed: engine.scroll/scroll2d paints it",
              "            -- (paint would overrun the hardware tilemap)"]
        if info.get("warm_fn"):
            L += ["            scenes.warm(rm)"]
        L += ["        } else {",
              "            scenes.paint(rm)"]
        if info.get("tile_pal"):
            L += ["            scenes.paint_attrs(rm)"]
        L += ["        }"]
    elif roam:
        # Same rule on either axis: paint() uploads the whole map with one
        # bkg.set_tiles, which a room past 32 tiles WIDE or TALL overruns.
        # engine.scroll2d owns those backgrounds (fill2d seeds the visible
        # window, update2d streams the crossed column/row).
        L += ["        if %s > %d or %s > %s {"
              % (_dim_w("rm", uniform), BKG_TILES,
                 _dim_h("rm", uniform), _rowlim(info)),
              "            -- roam: engine.scroll2d paints it (paint would overrun)"]
        if info.get("warm_fn"):
            L += ["            scenes.warm(rm)"]
        L += ["        } else {",
              "            scenes.paint(rm)"]
        if info.get("tile_pal"):
            L += ["            scenes.paint_attrs(rm)"]
        L += ["        }"]
    else:
        L.append("        scenes.paint(rm)")
        if info.get("tile_pal"):
            L.append("        scenes.paint_attrs(rm)")
    if info.get("music"):
        # The map + per-scene tileset upload is the biggest single blocking
        # chunk of a room load, and the whole load runs inside ONE VM frame -
        # so the driven song is not advanced across it at all and simply stops.
        # `core.music_pump` catches it up by the DISPLAY frames that really
        # elapsed (never more, so several pumps in one frame cannot run the song
        # fast) and is a no-op for a project with no driver. The fade-in loop at
        # the end of load_room pumps per frame; this is the one big gap before
        # it. Emitted only for a world with songs - byte-identical without.
        L.append("        core.music_pump()")
    if info.get("letterbox"):
        # THE LETTERBOX VIEW (`studio.toml [scenes] letterbox`): a room smaller
        # than the screen (an 18-row room on the SMS's 24 rows or the PC
        # Engine's 28, a 20-column room on their 32) is shown CENTRED rather
        # than in the top-left corner. video.set_view hands the prelude the
        # offset (tile-aligned, so the box's tile snap still lines up), which
        # it adds at the scroll commit and at every sprite placement; everything
        # the VM computes stays in room-view space. core.set_view anchors the
        # dialogue box and the menu to the ROOM's bottom and clamps the box to
        # its width. Called on EVERY console: where the screen is never bigger
        # than a room (the GB family) the offset is 0 and the view is the
        # screen, which is what the box must read there. A runtime test, so one
        # target-neutral rooms.mos is right everywhere.
        L += ["        var vw: u16 = %s" % _dim_w("rm", uniform),
              "        var vh: u16 = %s" % _dim_h("rm", uniform),
              "        var vcols: u8 = SCREEN_COLS",
              "        var vrows: u8 = SCREEN_ROWS",
              "        var vox: u8 = 0",
              "        var voy: u8 = 0",
              "        if vw < SCREEN_COLS {",
              "            vcols = vw",
              "            vox = ((SCREEN_COLS - vcols) / 2) * 8",
              "        }",
              "        if vh < SCREEN_ROWS {",
              "            vrows = vh",
              "            voy = ((SCREEN_ROWS - vrows) / 2) * 8",
              "        }",
              "        video.set_view(vox, voy)",
              "        core.set_view(vcols, vrows)"]
    if info.get("clear_outside"):
        # CELLS THE SCENE DOES NOT COVER. A room smaller than the console's
        # SCREEN leaves the rest of the tilemap holding whatever was there --
        # the previous room, or tile 0 of the new tileset, which is blank only
        # by luck (on the reference-engine import 8 of 17 scene tilesets have no blank
        # tile at all, and the parallax room's tile 0 is a solid block, which is
        # what the bottom six rows of the SMS screen showed).
        #
        # The Game Boy never sees this: its 20x18 screen is the smallest any
        # scene can be. The SMS's is 32x24, so an imported 20x18 room leaves 12
        # columns and 6 rows of the old picture on screen. Both tests are
        # RUNTIME comparisons against the per-console SCREEN_COLS/ROWS, so one
        # target-neutral rooms.mos is right everywhere and the consoles that
        # cannot hit it pay two compares.
        #
        # It blanks through the TEXT layer because that is where a guaranteed
        # blank tile lives: `clear_area` plots the font's SPACE glyph, which is
        # resident (or, in glyph-buffer mode, pinned to band slot 0 and never
        # evicted) for any project that draws text at all. Hence the emission
        # condition -- see `clear_outside` in generate_rooms.
        #
        # The PC Engine is in: its conio clear is a BAT write, and the cc65
        # prelude copies it into the BAT's scroll replicas. The Lynx, a
        # framebuffer console, is excluded: there `clear_area` is a TGI
        # operation that would fight the Lynx strip engine rather than clear a
        # tilemap, and its bkg engine needs its own fill. Reported, not faked.
        if info.get("letterbox"):
            # Letterboxed, the TOP margin shows the name-table rows that wrap
            # round from the bottom (the SMS's 25..27, the PC Engine's 27..31),
            # so the clear runs to the background's full height, not the
            # screen's. The column clear already spans the 32-wide table.
            rows_lines = ["            var bkr: u8 = 32",
                          '            if platform == "sms" or platform == "gamegear" {',
                          "                bkr = 28",
                          "            }",
                          "            var ch: u16 = %s" % _dim_h("rm", uniform),
                          "            if ch < bkr {",
                          "                var ch8: u8 = ch",
                          "                text.clear_area(0, ch8, SCREEN_COLS, bkr - ch8)",
                          "            }"]
        else:
            rows_lines = ["            var ch: u16 = %s" % _dim_h("rm", uniform),
                          "            if ch < SCREEN_ROWS {",
                          "                var ch8: u8 = ch",
                          "                text.clear_area(0, ch8, SCREEN_COLS, SCREEN_ROWS - ch8)",
                          "            }"]
        L += ["        " + CLEAR_GUARD,
              "            var cw: u16 = %s" % _dim_w("rm", uniform),
              "            if cw < SCREEN_COLS {",
              "                var cw8: u8 = cw",
              "                text.clear_area(cw8, 0, SCREEN_COLS - cw8, SCREEN_ROWS)",
              "            }"] + rows_lines + ["        }"]
    if info.get("bkg4"):
        # 16-colour tileset: load its authored palette (a no-op on a 2bpp
        # console; on PCE/SMS/GG it fills the native bkg palette). Idempotent, so
        # loading it per room-load is harmless (one tileset palette per game).
        L.append("        palette.load_bkg16(scenes.BKG_PALETTE16)")
    if use_player or playerless:
        # Reset any generic background scroll (scroll_bg) + camera lock from the
        # PREVIOUS scene, so a scene's scroll doesn't leak into the next room's
        # camera (a leftover cam_lock hijacks the follow / shmup scroll). The new
        # scene's On Init re-arms its own scroll_bg after this returns.
        L.append("        player.scroll_bg_off()")
    if has_ent:
        L.append("        entity.reset()")     # kill the old room's On Update threads
    if emote:
        # A live bubble is anchored to an actor SLOT, and the pool is about to
        # be re-filled with this room's actors -- so it would follow whoever
        # lands in that slot. Drop it, exactly as the UI reset drops a box.
        L.append("        emote.reset()")
    if has_objs:
        # Clear the PREVIOUS room's actors from the pool -- else a scene change
        # leaves a slot active (its sprite lingers in the new room) and its clip/
        # move state dangling. Each room re-activates only the objects placed in
        # IT (the loop below filters on OBJ_SCENE), so this must run every load.
        L.append("        actor.reset()")
        if dyn_oam:
            # Bases are re-packed per room, so a slot covered by a fan in the
            # OLD room may belong to nobody in the new one -- and an unowned
            # OAM entry keeps showing its stale tile at its stale position.
            # Sweep the whole table once per room load (OAM_SLOTS = the
            # console's hardware sprite count; out-of-range slots are no-ops).
            if info.get("targets_pce"):
                # The GB park y (200) is ON the PC Engine's 224-line screen.
                L += ["        for s in 0..OAM_SLOTS {",
                      "            if platform == \"pce\" {",
                      "                sprite.move(s, 200, SCREEN_HEIGHT)",
                      "            } else {",
                      "                sprite.move(s, 200, 200)",
                      "            }",
                      "        }"]
            else:
                L += ["        for s in 0..OAM_SLOTS {",
                      "            sprite.move(s, 200, 200)",
                      "        }"]
    if has_doors:
        L += ["        door.clear()",
              "        for i in 0..scenes.DOOR_COUNT {",
              "            if scenes.door_from_at(i) == rm {",
              "                var dtx: u16 = scenes.door_tx_at(i)",
              "                var dty: u16 = scenes.door_ty_at(i)",
              "                door.add(dtx * 8, dty * 8, 8, 16, scenes.door_to_at(i), scenes.door_ex_at(i), scenes.door_ey_at(i))",
              "            }",
              "        }"]
    if has_trig:
        if c.tsnap:
            # Reference-engine topdown-grid parity: this room's trigger hit test
            # snaps the player box to the nearest 8 px stop (or stays
            # pixel-exact, per scene - see the TSNAP note in the prelude).
            if c._ts_all:
                L.append("        trigger.set_snap(%d)" % c.tsnap[0])
            else:
                L.append("        trigger.set_snap(TSNAP[rm])")
        if c.tforce:
            # Reference-engine PLATFORM parity: this room's trigger scan re-fires the
            # standing trigger's enter script on the button's rising edge
            # (255 = off). set_force also re-masks the edge, because a button
            # held across a scene change is not a press in the new room.
            if c._tf_all:
                L.append("        trigger.set_force(%d)" % c.tforce[0])
            else:
                L.append("        trigger.set_force(TFORCE[rm])")
        L += ["        trigger.clear()",
              "        for i in 0..scenes.TRIG_COUNT {",
              "            if scenes.trig_from_at(i) == rm {",
              "                var ttx: u16 = scenes.trig_tx_at(i)",
              "                var tty: u16 = scenes.trig_ty_at(i)",
              "                var ttw: u16 = scenes.trig_tw_at(i)",
              "                var tth: u16 = scenes.trig_th_at(i)",
              "                ttw = ttw * 8",
              "                tth = tth * 8",
              "                if ttw > 248 {",
              "                    ttw = 248",
              "                }",
              "                if tth > 248 {",
              "                    tth = 248",
              "                }",
              "                var tws: u8 = ttw",
              "                var ths: u8 = tth",
              (_TRIG_ADD_LEAVE if has_trig_leave else _TRIG_ADD),
              "            }",
              "        }"]
        if info.get("music"):
            # The trigger scan walks EVERY trigger in the world (108 of them on
            # the reference-engine conversion) through banked accessors, so it is a
            # real slice of the load - see the pump after the map upload.
            L.append("        core.music_pump()")
    # NOTE: the scene's own init script is spawned at the END of load_room
    # (emit_dispatch), after the actor On Init threads have had one scheduler
    # pass - see the run_pending block there.
    if has_objs:
        if pkinds:
            # This room's player SPRITE kind - read once, used for the OAM fan
            # size, the VRAM upload, the collision box and the clip binding.
            L.append("        var pk: u8 = PKIND[rm]")
            if _pk_box:
                # box = the drawn rectangle down to the FEET (see the PW/PH
                # note above), so they land on the floor whatever art this
                # room's player wears
                L += ["        PW = PBW[rm]",
                      "        PH = PBH[rm]"]
        if dyn_oam:
            # The room's OAM allocator: the player fan owns slots 0..pfan-1
            # (player.set_base(0) in start()), each actor's fan packs above it
            # in placement order, and an actor whose fan would cross the
            # 40-object table gets NO_OAM (it exists for scripting, draws
            # nothing) -- set_meta would no-op the whole fan anyway, and its
            # sprite.move would then write out of bounds.
            # THE PLAYER'S DRAWN HEIGHT, for the emote bubble's lift. Only
            # this file knows it: vm.player's own fan is 1x1 under external
            # animation (vm.canim draws the player), and vm.canim's per-kind
            # selectors answer its `m_h` default unless a project registers
            # `set_clip_size`, which a conversion does not - it reads
            # `clips.meta_h` here instead. Without it the bubble lifted 8 px
            # and sat inside the player's head.
            if emote:
                if clips.get("per_kind_size") and has_pk:
                    L.append("        emote.set_player_h(clips.meta_h(%s) * 8)"
                             % _pk)
                elif has_pk:
                    L.append("        emote.set_player_h(%d)"
                             % (clips["meta_h"] * 8))
            if clips.get("per_kind_size"):
                if has_pk:
                    if clips.get("desc"):
                        # clips.fan answers in 8x8-object units for desc AND
                        # dense kinds alike, so the obj16 halving below stays
                        # one rule.
                        L.append("        var ob: u8 = clips.fan(%s)" % _pk)
                    else:
                        L += ["        var ob: u8 = clips.meta_w(%s)" % _pk,
                              "        ob = ob * clips.meta_h(%s)" % _pk]
                    if obj16:
                        L += ["        " + OBJ16_GUARD,
                              "            ob = ob / 2   -- 8x16 OBJ: half the objects",
                              "        }"]
                else:
                    L.append("        var ob: u8 = 0")
            else:
                pfan = clips["meta_w"] * clips["meta_h"] if has_pk else 0
                L.append("        var ob: u8 = %d" % pfan)
                if obj16 and pfan:
                    L += ["        " + OBJ16_GUARD,
                          "            ob = %d   -- 8x16 OBJ: half the objects" % (pfan // 2),
                          "        }"]
            if emote:
                # ...above the emote bubble's reserved objects. It sits at the
                # BOTTOM of the table (EMOTE_OAM = 0) because an OAM index IS
                # the draw priority on the GB family and the reference engine renders its
                # emote before everything else - a bubble at the top draws
                # behind the very actor it belongs to (RPG check bug 6). Added
                # AFTER the 8x16 halving above, which is about the PLAYER's
                # fan; the reservation is a flat four objects either way.
                L.append("        ob += EMOTE_FAN")
            if info.get("uses_projectile"):
                # The player fan's END, before the actor loop moves the
                # cursor: the dynamic projectile block's FLOOR (P1)
                # - the block may drop below the
                # static base onto parked actors' entries, but never onto
                # the player.
                L.append("        var pob0: u8 = ob")
        if info.get("kind_tpal") and has_pk:
            # COLOUR, per CELL: the player's metasprite wears several palettes
            # (the reference engine's has hair, face and body on three). Its fan starts at
            # OAM 0; re-applied per room load because the room's palette SET was
            # just re-loaded and a per-scene player sprite may be another kind.
            L.append("        scenes.paint_actor(0, %s, %s, %s)"
                     % (_pk, _meta_w(info, _pk), _meta_h(info, _pk)))
        elif info.get("kind_pal") and has_pk:
            # COLOUR: the player's own sprite palette. Its fan starts at OAM 0
            # (player.set_base(0)); re-applied per room load because the room's
            # palette SET was just re-loaded and a per-scene player sprite may
            # be a different kind.
            L.append("        sprite.set_palette(0, scenes.kind_pal_at(%s))" % _pk)
        if res:
            # The room's VRAM allocator, the tile mirror of `ob` above: the
            # player's sheet owns VRAM 0 and is uploaded HERE, each OTHER
            # placed kind uploads ONCE per room at the next free tile (two
            # actors of one kind share its sheet), and a kind that would cross
            # the 128-tile OBJ area stays non-resident -- its actors draw the
            # player's tiles rather than garbage, and the importer reports any
            # room that can overflow.
            #
            # The player upload MUST be emitted here, not left to the shell's
            # boot: a shell that (correctly) drops its boot upload so the sheet
            # can co-locate into a ROM bank would otherwise leave VRAM 0
            # holding whatever was there, and the player renders as garbage --
            # measured on the reference-engine sample conversion as a one-tile-wide sliver where the
            # character should be. Doing it here keeps the sheet's ONLY reader
            # inside `rooms`, so it still banks (that was the point of dropping
            # the boot upload) AND the tiles actually reach VRAM.
            L += ["        for kk in 0..NKIND {",
                  "            kvb[kk] = 255",
                  "        }"]
            if pkinds:
                L += ["        kvb[pk] = 0",
                      "        upload_kind(pk, 0)",
                      "        var vt: u16 = KT[pk]"]
                if info.get("music"):
                    # The PLAYER's sheet is the one VRAM upload outside the
                    # actor loop (which pumps per actor), and it is the biggest
                    # single one - a 16x32 platform player is 8 tiles a frame.
                    L.append("        core.music_pump()")
                if clips.get("player"):
                    # re-bind the player's CLIPS to this room's sprite kind
                    # (start() binds it once only for a single-sprite world)
                    L.append("        canim.set_player(pk)")
            elif res.get("player_stem"):
                L += ["        kvb[scenes.KIND_PLAYER] = 0",
                      "        upload_kind(scenes.KIND_PLAYER, 0)",
                      "        var vt: u16 = %s_tile_count" % res["player_stem"]]
            else:
                L.append("        var vt: u16 = 0")
        L += ["        var slot: u8 = 0",
              "        for i in 0..scenes.OBJ_COUNT {",
              "            if scenes.obj_scene_at(i) == rm {"]
        ind = "                "
        if has_pk:
            L.append(ind + "if scenes.obj_kind_at(i) != scenes.KIND_PLAYER {")
            ind += "    "
        # `fk` is also what the COLOUR calls below read (paint_actor /
        # kind_pal_at): a uniform-size world with no residency used them
        # without declaring it and could not compile once it coloured a kind.
        if dyn_oam and (res or clips.get("per_kind_size")
                        or info.get("kind_tpal") or info.get("kind_pal")):
            L.append(ind + "var fk: u8 = scenes.obj_kind_at(i)")
            if info.get("variants"):
                # world.toml [kind_variants]: a placeholder becomes the kind
                # its heap variable picks, BEFORE the upload / clip / palette
                # reads below (all of which key on fk).
                L.append(ind + "fk = variant_kind(fk)")
        if res:
            L += [ind + "if kvb[fk] == 255 {",
                  ind + "    if KT[fk] > 0 {",
                  ind + "        var need: u16 = vt + KT[fk]",
                  ind + "        if need <= %s {" % tile_top,
                  ind + "            var vb8: u8 = vt",
                  ind + "            upload_kind(fk, vb8)",
                  ind + "            kvb[fk] = vb8",
                  ind + "            vt = need",
                  ind + "        }",
                  ind + "    }",
                  ind + "}",
                  ind + "var tb: u8 = 0",
                  ind + "if kvb[fk] != 255 {",
                  ind + "    tb = kvb[fk]",
                  ind + "}",
                  ind + "actor.activate(slot, tb, scenes.obj_x_at(i), scenes.obj_y_at(i))",
                  # A kind the room had no VRAM for keeps base 0, which is the
                  # PLAYER's sheet - so it used to draw the player's tiles as
                  # garbage rather than fail visibly (the converted sample's
                  # health hearts on SMS/GG, whose room wants 156 tiles against
                  # a 128 ceiling). Hide it instead: `a_vis` is DRAW-ONLY, so
                  # the actor keeps its position, its collision and its
                  # scripts and simply is not rendered - the graceful failure,
                  # and the importer already NAMES the room in its report.
                  ind + "if kvb[fk] == 255 {",
                  ind + "    actor.set_visible(slot, 0)",
                  ind + "}"]
        else:
            L.append(ind + "actor.activate(slot, 1, scenes.obj_x_at(i), scenes.obj_y_at(i))")
        if dyn_oam:
            if clips.get("per_kind_size"):
                if clips.get("desc"):
                    L.append(ind + "var fan: u8 = clips.fan(fk)")
                else:
                    L += [ind + "var fan: u8 = clips.meta_w(fk)",
                          ind + "fan = fan * clips.meta_h(fk)"]
                if obj16:
                    L += [ind + OBJ16_GUARD,
                          ind + "    fan = fan / 2   -- 8x16 OBJ: half the objects",
                          ind + "}"]
            else:
                fan_n = clips["meta_w"] * clips["meta_h"]
                L.append(ind + "var fan: u8 = %d" % fan_n)
                if obj16:
                    L += [ind + OBJ16_GUARD,
                          ind + "    fan = %d   -- 8x16 OBJ: half the objects" % (fan_n // 2),
                          ind + "}"]
            L += [ind + "if ob + fan > %s {" % ACTOR_OAM_TOP,
                  ind + "    actor.set_base(slot, actor.NO_OAM)",
                  ind + "} else {",
                  ind + "    actor.set_base(slot, ob)"]
            if info.get("uses_projectile"):
                # Tell vm.actor the fan SIZE beside its base, so the dynamic
                # projectile block's watermark knows where each drawn fan
                # ends (a slot never told answers UNKNOWN and the block
                # falls back to the static layout).
                L.append(ind + "    actor.set_fan(slot, fan)")
            if info.get("kind_tpal"):
                L += [ind + "    scenes.paint_actor(ob, fk, %s, %s)"
                            % (_meta_w(info, "fk"), _meta_h(info, "fk"))]
            elif info.get("kind_pal"):
                # COLOUR: the kind's sprite palette, applied to the fan's BASE
                # (set_palette fans it over the metasprite and set_prop now
                # preserves it, so a facing flip cannot reset it).
                L += [ind + "    sprite.set_palette(ob, scenes.kind_pal_at(fk))"]
            L += [ind + "    ob += fan",
                  ind + "}"]
        elif info.get("kind_tpal"):
            # No dynamic OAM layout: vm.actor's default stride puts the actor's
            # base at its pool index.
            L += [ind + "var ck: u8 = scenes.obj_kind_at(i)",
                  ind + "scenes.paint_actor(slot, ck, %s, %s)"
                        % (_meta_w(info, "ck"), _meta_h(info, "ck"))]
        elif info.get("kind_pal"):
            L += [ind + "sprite.set_palette(slot, scenes.kind_pal_at(scenes.obj_kind_at(i)))"]
        if clips:
            L.append(ind + "actor.set_clip(slot, %s)"
                     % ("fk" if info.get("variants") else "scenes.obj_kind_at(i)"))
        if info.get("has_obj_pin"):
            L.append(ind + "actor.set_pinned(slot, scenes.obj_pinned(i))")
        if has_ent:
            args = []
            for k, fn in (("has_obj_init", "obj_init"),
                          ("has_obj_update", "obj_update"),
                          ("has_obj_interact", "obj_interact")):
                args.append("scenes.%s(i)" % fn if sel[k] else "scenes.NO_SCRIPT")
            L.append(ind + "entity.add(slot, %s)" % ", ".join(args))
            # On Hit (F3) rides a SEPARATE bind_hit so add() stays the 4-arg call
            # every existing shell / generated rooms.mos makes (backward-compatible,
            # byte-identical for a world with no On Hit slot).
            if sel["has_obj_hit"]:
                L.append(ind + "entity.bind_hit(slot, scenes.obj_hit(i))")
            # `clips` is None for a world with no [animations] (line 175), and
            # every other read here is behind `dyn_oam`, which is already
            # `bool(clips) and ...`. This one was not, so a world that has an On
            # Interact slot but no clips module crashed rooms generation
            # outright (projects/vm-showcase).
            # ...AND A WORLD THAT AUTHORS ACTOR HITBOXES does too, whatever its
            # sizes (every reference-engine import authors them all). Its player box is
            # OFFSET from the sprite (the reference engine's `bounds`: a 16x8 box on the
            # feet row), so the point test measured a box origin against a
            # sprite corner - 16 px apart for a player standing flush beside a
            # 16x16 NPC, past INTERACT_R - and no NPC in an all-16x16 import
            # could be talked to (the adventure check project). The box test is the
            # reference's own `actor_with_script_in_front_of_player`.
            if sel["has_obj_interact"] and ((clips or {}).get("per_kind_size")
                                            or (clips and info.get("kind_boxes"))):
                # A world that MIXES sprite sizes registers each entity's
                # interact BOX (px), switching vm.entity to the reference engine's
                # box-overlap test. The default point test compares TOP-LEFT
                # anchors, and a 32 px platform player standing beside a 16 px
                # NPC has anchors 16 px apart -- past its radius -- so On
                # Interact could not fire while GROUNDED (and did fire while
                # falling past, the anchor sweeping through range). A uniform
                # world with no hitboxes never calls set_box and keeps the
                # point test byte-identical. The size has ONE decider
                # (`_meta_w`/`_meta_h`): a uniform world has no `clips.meta_w`.
                L += [ind + "var bw: u8 = %s" % _meta_w(info, "fk"),
                      ind + "bw = bw * 8",
                      ind + "var bh: u8 = %s" % _meta_h(info, "fk"),
                      ind + "bh = bh * 8",
                      ind + "entity.set_box(slot, bw, bh)"]
        if _wants_boxes(info):
            # The actor's BOX - the reference engine's authored `bounds`, per kind, from
            # studio.toml [hitbox.<kind>]. A world that authors none falls back
            # to the drawn rectangle, which is what a hand-made game means by
            # "solid" anyway; an import authors them all and is exact.
            # It is the ONE box the runtime has: it decides player BLOCKING
            # (solid_actors), what a PROJECTILE hits, and what a pushed actor
            # probes with. So a projectile game registers it whether or not its
            # actors block - without that a 16x16 enemy is only shootable
            # through the 8x8 cell at its top-left corner.
            if info.get("kind_boxes"):
                L.append(ind + "actor.set_box(slot, KBW[fk], KBH[fk], "
                               "KBX[fk], KBY[fk])")
            elif clips:
                # THE SIZE COMES FROM `_meta_w`/`_meta_h`, NEVER FROM A
                # HAND-WRITTEN `clips.meta_w(...)`. `mosaik_anim` emits those
                # accessors ONLY for a world that MIXES sprite sizes (one
                # `if mixed or has_desc:` guards the definition and the export
                # together), so on a UNIFORM world this branch used to name a
                # symbol the clips module does not export and rooms generation
                # produced a module that cannot compile:
                #     module "clips" has no module-level symbol "meta_w"
                # The two helpers are the one place that knows which form to
                # use - the accessor when `per_kind_size`, the baked literal
                # otherwise - and its sibling above (the On Interact box) was
                # already gated that way. Mixed worlds emit the identical call
                # text, so every project that builds today is byte-identical.
                #
                # LATENT: every world in the repo with clips AND actor boxes
                # happens to MIX sizes, so none reaches it. It was found in
                # 2026-09-16 by an experiment that briefly produced a uniform
                # multi-kind world (8 kinds all 16x16) and was then removed
                # for unrelated reasons. So the trap is
                # still armed for the next uniform world, which is why the
                # test builds one rather than naming a project.
                L += [ind + "var sbw: u8 = %s" % _meta_w(info, "fk"),
                      ind + "sbw = sbw * 8",
                      ind + "var sbh: u8 = %s" % _meta_h(info, "fk"),
                      ind + "sbh = sbh * 8",
                      ind + "actor.set_box(slot, sbw, sbh, 0, 0)"]
            else:
                L.append(ind + "actor.set_box(slot, 8, 8, 0, 0)")
        L.append(ind + "slot += 1")
        if info.get("music"):
            # PER PLACED ACTOR, not once after the loop. Measured on
            # the SMS/GG sample conversion: the actor pass is where the room load's longest
            # silence lived (18 consecutive display frames with no music tick),
            # because each placed kind uploads a sprite SHEET into VRAM and the
            # whole pass runs inside one VM frame. A pump costs one banked call
            # per actor on a load frame; it is the cheapest thing in this loop.
            L.append(ind + "core.music_pump()")
        if has_pk:
            L.append("                }")
        L += ["            }", "        }"]
        if dyn_oam and info.get("uses_projectile"):
            # The projectile block sits ABOVE the room's fans (its fixed 16
            # would land in the middle of them, and its inactive-slot parking
            # would stomp the fan children there). Re-based per room; reset
            # first so a shot flying through the door does not keep an OAM
            # entry at the OLD base.
            L += ["        projectile.reset()",
                  "        projectile.set_base(ob)",
                  # DYNAMIC block (plan P1): per frame the pool re-bases onto
                  # the parked-actor watermark, floored at the player fan's
                  # end - shots re-assert everything per frame, so a moving
                  # base is safe for them, and parked fans' entries are free
                  # to borrow. The static set_base above stays the fallback
                  # (and the behaviour of any shell that never calls set_dyn).
                  "        projectile.set_dyn(pob0)"]
        if res and info.get("proj_tiles"):
            # PROJECTILE ART. A launch sprite is not PLACED by any scene, so
            # the per-room allocator never saw it and a shot drew tile 0 - the
            # PLAYER's sheet under residency. It is uploaded LAST, after every
            # placed kind, so a full room simply goes without it rather than
            # evicting art something on screen is wearing.
            L += ["        var pn: u16 = vt + %d" % info["proj_tiles"],
                  "        if pn <= %s {" % tile_top,
                  "            var pvb: u8 = vt",
                  "            sprite.set_data(pvb, %s_tile_count, %s_tiles)"
                  % (info["proj_stem"], info["proj_stem"]),
                  "            projectile.set_tile_base(pvb)",
                  "            vt = pn",
                  "        } else {",
                  "            projectile.set_tile_base(0)",
                  "        }"]

