"""mosaik_vm.rooms.context - the world-shape dict -> the derived flags
every emitter reads.

`emit_rooms_mos` used to open with ~200 lines turning `info` into fifty
locals, then emit 1,500 more against them. The derivation is unchanged
and lives here; `derive()` captures its locals into a plain namespace so
the code above stays exactly what it was rather than being rewritten
into fifty `self.x =` lines."""
from .config import (_PLAYER_DEFAULTS, _PLAYER_TYPES)


class RoomsCtx(object):
    """The derived world shape: an attribute per flag `derive()` computes.

    Deliberately dumb - it carries no behaviour, and `info` (the raw world
    dict `generate_rooms` builds) stays reachable for the handful of
    one-off keys an emitter reads directly."""

    def __init__(self, **fields):
        self.__dict__.update(fields)


def derive(info):
    """The world-shape dict -> a RoomsCtx of derived flags."""
    types = list(info.get("types") or ["topdown"])
    p = dict(_PLAYER_DEFAULTS)
    p.update(info.get("player") or {})
    uniform = info.get("uniform", True)
    has_col = info.get("has_collision", False)
    has_doors = info.get("has_doors", False)
    has_trig = info.get("has_triggers", False)
    has_trig_leave = info.get("has_trig_leave", False)
    has_ent = info.get("has_entity", False)
    has_scene_init = info.get("has_scene_init", False)
    has_pk = info.get("has_player_kind", False)
    has_objs = info.get("has_objects", False)
    clips = info.get("clips") or None      # {"meta_w","meta_h","flip","player"}
    # Multi-tile metasprites need a real OAM LAYOUT: vm.actor's default is one
    # slot per pool index (stride 1 -- right for single-tile sprites, which is
    # all the older generated worlds used), so a 2x2+ fan would overlap its
    # neighbours' slots and every owner re-asserts per frame = flicker. The
    # generated load_room then packs each actor's fan at a cumulative base
    # (player fan first at 0), parks what does not fit at actor.NO_OAM, and
    # moves the projectile block above the room's watermark.
    dyn_oam = bool(clips) and (clips.get("per_kind_size")
                               or clips["meta_w"] * clips["meta_h"] > 1)
    # 8x16 OBJ mode (`mosaik.toml [build] obj_8x16`): where the hardware has
    # it, a WxH TILE metasprite is W*(H/2) hardware OBJECTS, so the room's
    # cumulative OAM packing must count HALF or every fan base past the player
    # is double-spaced (holes in OAM, and the object refusal fires early).
    # rooms.mos is target-NEUTRAL, so the halving is emitted under an
    # `if platform` fork over `platforms.OBJ16_CONSOLES` -- a console without
    # the mode keeps the full w*h fan its metasprite layer actually uses.
    # Off = byte-identical.
    obj16 = bool(info.get("obj_8x16"))
    from mosaik.platforms import obj16_guard
    OBJ16_GUARD = obj16_guard()
    # The ceiling the per-room VRAM allocator packs sprite kinds up to: the Game
    # Boy's 128 exclusive OBJ tiles (0x8000..0x87FF; past it collides with BG
    # data in 0x8800 mode). It is a GB number, but the allocator applies it
    # everywhere - conservative, and left alone here.
    #
    # Where the EMOTE's four reserved tiles come from is per console, and it has
    # to be, because taking them off this ceiling evicts art on a console that
    # has room elsewhere. The GB has nowhere else, so its reservation comes off
    # the top of the 128. The SMS/GG sprite patterns are a SEPARATE VDP area
    # (tile ids 256..447 = 192 slots, docs/vram-layout.md), so theirs sits ABOVE
    # the allocator's range and costs the rooms nothing.
    #
    # Measured, and the reason this is a fork rather than one number: the SMS/GG
    # parallax room needs 126 sprite tiles (its platform player is 80
    # with the soft-flip bake, plus a 4-tile sign and a 42-tile animated actor).
    # A flat 124 ceiling refused that actor, which then drew at VRAM base 0 -
    # the player's sheet - as garbage.
    SMSGG_OBJ_TILES = 192
    emote_tiles = 4 if info.get("uses_emote") else 0
    OBJ_TILE_CAP = 128 - emote_tiles          # GB family (and every other target)
    # ...and the SMS/GG allocator gets the WHOLE 192-slot pattern area, less
    # whatever the emote took off the top of it.
    #
    # It used to be a flat 128 - the GB's number, inherited by consoles that do
    # not share the GB's reason for it. The GB is capped at 128 because tile
    # 128+ collides with background data in 0x8800 mode; SMS/GG sprite patterns
    # are a SEPARATE VDP area (tile ids 256..447, docs/vram-layout.md) with
    # nothing above them but the emote's four, so 60 slots simply went unused.
    # Measured on the SMS/GG sample conversion: a long walk-in room wants 156 sprite
    # tiles and the parallax room 132, so both were over a 128 ceiling and the
    # allocator refused their last kinds - which is why that room's four health
    # HEARTS were missing entirely on the SMS (ROM A/B at 128 vs 188: absent,
    # then drawn). At 188 both rooms fit with room to spare.
    SMSGG_TILE_CAP = SMSGG_OBJ_TILES - emote_tiles
    # ... and the OAM mirror: the actor packer refuses a fan that would cross
    # this. It is the WHOLE table now, emote or not - the bubble reserves its
    # four objects at the BOTTOM (see emit_prelude: an OAM index is the draw
    # priority, so a bubble at the top draws behind the actor it belongs to),
    # and the packer starts above them rather than stopping below them. The
    # usable count is unchanged either way, so no room's capacity moves.
    ACTOR_OAM_TOP = "OAM_SLOTS"
    # The allocator reads the LITERAL 128 only when the per-console ceiling
    # cannot differ from it: no emote has taken tiles off the top AND the
    # project will never build for the SMS/GG (whose area is 192, above).
    # That keeps every GB-only world byte-identical with the ones generated
    # before either fork existed, and is the same "carry the fork only where it
    # can matter" rule `_targets_smsgg` states for the row limit.
    # ...and the SMS/GG arm is worth carrying only where something READS the
    # ceiling, which is the per-room VRAM allocator (`residency`) and nothing
    # else - so a world that uploads its sprites once at boot emits no fork and
    # a 20x18 room stays byte-identical whichever consoles it targets.
    obj_top_fork = bool(info.get("uses_emote")
                        or (info.get("targets_smsgg") and info.get("residency")))
    tile_top = "OBJ_TILE_TOP" if obj_top_fork else "128"
    # The auto-fade runs on the consoles that can actually DARKEN: the GB
    # family through `vm.fx`'s BGP/OBP registers, and the SMS/Game Gear by
    # scaling CRAM (see PAL_FADE_GUARD below - the CGB scales its palettes for
    # the same reason, its registers being ignored in CGB mode).
    #
    # It stays OFF the Lynx and PC Engine, and not merely because there is
    # nothing wired to darken there: each step calls `video.wait_vblank()`,
    # which on the Lynx is a full `gbs_present()` INSIDE load_room -- and those
    # presents consume the present's change-detection redraw budget
    # (`gbs_force` -> `gbs_redraw`) while the room is still being built, after
    # which the detector sees an unchanged state and never re-blits, so the
    # composited background never reaches the screen and the room stays BLANK.
    # (Measured: a longer fade does not help, so it is a latch, not a
    # shortfall.) The z80 pair's wait_vblank is an ordinary vblank wait.
    #
    # The PC Engine's present has no such latch (it flushes the SATB mirror
    # and waits for vblank), and since 2026-10-03 it scales its VCE colours
    # through a shadow (`palette.fade`, like the SMS's CRAM): a COLOURED world
    # that targets it fades there too, which is what hides a room being built.
    # Only such a world carries the clause, so no other rooms.mos moves.
    pce_fade = bool(info.get("targets_pce") and info.get("colour")
                    and info.get("fade"))
    _pce_or = ' or platform == "pce"' if pce_fade else ""
    FADE_GUARD = ('if platform == "gameboy" or platform == "gameboy_color" '
                  'or platform == "megaduck" or platform == "analogue_pocket" '
                  'or platform == "sms" or platform == "gamegear"%s {' % _pce_or)
    # `core.set_redraw` -- the seam vm.core calls when a dialogue box or menu
    # CLOSES. On the GB family the box lived on the window overlay and the scene
    # was never touched, so the seam is not needed; on SMS/GG there is no window
    # layer, the box is plotted straight into the scene tilemap, and closing it
    # left a blank band (core clears the box's own cells and has no way to know
    # what the scene had there). The seam had in fact NEVER been wired by any
    # generator, so `has_redraw` was always 0 and the repaint branch was dead
    # code. Emitted under the same kind of platform fork as the fade, so every
    # other console's rooms.mos is byte-identical.
    UI_REDRAW_GUARD = 'if platform == "sms" or platform == "gamegear" {'
    # SCANLINE PARALLAX: real only where `bkg.parallax*` is real, which is the
    # GB register model. Everywhere else those verbs are honest no-op stubs -
    # and that is precisely why the path must be FORKED rather than left
    # target-neutral: `engine.scrollpx` publishes each band's scroll THROUGH
    # the stubs and never calls `bkg.move`, so on SMS/GG the columns streamed
    # at their per-band offsets while nothing ever wrote the scroll register.
    # Measured on the SMS/GG sample conversion after parallax shipped: `path/Parallax
    # Example` mis-rendered, where before it was correct. So those consoles
    # take the plain single-scroll streamer again - the pre-parallax path,
    # which is what the importer's note already claims ("Real on the Game Boy
    # family only ... the background scrolls as one there").
    #
    # SMS/GG parallax IS hardware-possible (the VDP line interrupt can rewrite
    # the horizontal scroll per line; only the VERTICAL scroll is latched once
    # a frame) - it is a z80 ISR pass of its own, not built yet.
    PARALLAX_GUARD = ('if platform == "gameboy" or platform == "gameboy_color" '
                      'or platform == "megaduck" '
                      'or platform == "analogue_pocket" {')
    # `clear_outside` (below): the TILE-PLOTTING consoles, where blanking a
    # cell means writing a tile id into the map/name table. The Lynx draws its
    # background from its own strip engine and `clear_area` there is a TGI
    # pixel operation, which would fight the strip engine rather than clear
    # anything -- a fill for it is its own job. The PC Engine's conio IS a BAT
    # write (cclearxy plots the font's space into the very table the VDC
    # displays), and its background engine copies a background-space clear
    # into the BAT's scroll replicas, so it joins -- in a project that
    # targets it, so no other project's rooms.mos moves.
    CLEAR_GUARD = ('if platform == "gameboy" or platform == "gameboy_color" '
                   'or platform == "megaduck" or platform == "analogue_pocket" '
                   'or platform == "sms" or platform == "gamegear" '
                   'or platform == "nes"%s {'
                   % (' or platform == "pce"' if info.get("targets_pce") else ""))
    # The COLOUR fade (pal_fade below): the consoles that fade by scaling their
    # PALETTES rather than a palette register - the CGB class (which ignores
    # BGP/OBP0/OBP1 entirely) and the SMS/Game Gear (which have no such
    # register at all, only CRAM). The GB and Mega Duck fade through vm.fx's
    # DMG registers, and the Lynx through native.lynx, so the wrapper would be
    # dead weight there.
    PAL_FADE_GUARD = ('if platform == "gameboy_color" '
                      'or platform == "analogue_pocket" '
                      'or platform == "sms" or platform == "gamegear"%s {' % _pce_or)
    # Per-room sprite residency (see generate_rooms): {"nk","kt","stems",
    # "player_stem"} or None. Implies the dynamic OAM layout.
    res = info.get("residency")
    if res:
        dyn_oam = True
    # SPRITE SLOTS ON WAKE (`mosaik.toml [build] oam_on_wake`): instead of a
    # fixed OAM range per placed actor at room load (the rest NO_OAM, never
    # drawn), an actor takes a range when it comes on screen and gives it back
    # when it leaves (vm.actor's oam_alloc / oam_free under VM_OAM_WAKE). It
    # rides the dynamic layout, so it needs one; off = byte-identical.
    oam_wake = bool(info.get("oam_wake")) and dyn_oam
    # PER-SCENE PLAYER SPRITE (the reference engine's `defaultPlayerSprites`, keyed by
    # scene TYPE): a platformer's player is different ART from the topdown
    # one - taller (16x32 vs 16x16) with its own walk cycle - so the sheet,
    # the metasprite size and the clip kind are all per ROOM. `res["pkinds"]`
    # is the player KIND id per scene; absent (a world with one player
    # sprite) everything keeps naming `scenes.KIND_PLAYER` and the output is
    # byte-identical.
    #
    # Note this is only the player's SPRITE. The world still places its spawn
    # marker as kind `player`, so the OBJ-loop skips and the start-position
    # scan below keep testing `scenes.KIND_PLAYER` - those ask "is this the
    # player marker", not "what art does the player wear here".
    pkinds = (res or {}).get("pkinds")
    _pk = "pk" if pkinds else "scenes.KIND_PLAYER"
    # The box follows the sprite, from the AUTHORED per-scene-type box (the
    # drawn rect minus the blank rows under the feet - the metasprite size
    # alone would count that padding and float the character).
    pboxes = (res or {}).get("pboxes")
    _pk_box = bool(pkinds) and bool(pboxes)
    # The TRIGGER box, which is not the collision box - see the note where it
    # is collected. `None` = use the collision box (byte-identical).
    # Where the collision box sits inside the drawn sprite (see the note
    # where it is collected). `None` = at its top-left (byte-identical).
    cfocus = info.get("cfocus")
    _cf_var = bool(cfocus) and len(set(cfocus)) > 1
    # The x focus is uniform in practice (the reference engine's is a fixed +8), so its
    # column would be a table of one repeated value.
    _cf_x1 = bool(cfocus) and len(set(f[0] for f in cfocus)) == 1
    # Per-scene TRIGGER GRID SNAP (8 = the reference engine's topdown grid, 0 = pixel-
    # exact). None = no scene snaps, nothing is emitted (byte-identical).
    tsnap = info.get("tsnap")
    _ts_all = bool(tsnap) and len(set(tsnap)) == 1
    # Per-scene TRIGGER FORCE RE-FIRE (a portable BTN_* id, 255 = off). None =
    # no scene forces, nothing is emitted (byte-identical).
    tforce = info.get("tforce")
    _tf_all = bool(tforce) and len(set(tforce)) == 1
    boffs = info.get("boffs")
    _bo_var = bool(boffs) and len(set(boffs)) > 1
    # Every reference-engine player sprite's bounds are full-width, so the x offset
    # is 0 and both the const and its per-room column are dead weight.
    _bo_x0 = bool(boffs) and all(o[0] == 0 for o in boffs)
    sel = {k: info.get(k, False)
           for k in ("has_obj_init", "has_obj_update", "has_obj_interact", "has_obj_hit")}
    # WIDE rooms: at least one scene is wider than the 32-tile hardware
    # background, so it must COLUMN-STREAM (engine.scroll) rather than be
    # painted once. Derived from the scene dimensions by generate_rooms -- it
    # is a property of the DATA, not a flag, and it is per ROOM: a world may
    # mix wide and narrow rooms and each takes its own path.
    wide = bool(info.get("wide_rooms", False))
    # A wide SHMUP room specifically (see generate_rooms): the shmup handler
    # forks wide/narrow only when one exists.
    wide_shmup = bool(info.get("wide_shmup", False))
    # A shmup room TALLER than the background (setup_tall_shmup + scroll2d).
    tall_shmup = bool(info.get("tall_shmup", False))
    roam = bool(info.get("roam_rooms", False))
    # SCANLINE PARALLAX: some scene declares bands. Only meaningful on a WIDE
    # room here - a band's columns have to stream at its own offset, which is
    # what engine.scrollpx does; a narrow parallax room needs no streaming at
    # all but also no room-load path of its own, so generate_rooms reports it
    # rather than half-supporting it.
    parallax = bool(info.get("parallax", False)) and wide
    # AUTO-FADE on room load (`studio.toml [scenes] fade` = LCD frames per
    # darkness step, 0/absent = off and byte-identical). The reference engine fades every
    # scene transition (`autoFadeSpeed`); ours cut. No new engine capability -
    # `vm.fx` and the FADE opcode already implement the 0..3 ramp, they were
    # just never driven from the room-load path.
    fade = int(info.get("fade", 0) or 0)
    # ... and its DIRECTION (`[scenes] fade_style`): "" = the engine's black,
    # "white" / "black" = declared, applied by start() through fx.set_style in
    # the reference engine's numbering (0 white, 1 black). A declared style is also what
    # makes the build state VM_FADE_STYLE, which is the fold the white arms
    # live under - so "black" declared is a project that wants a script to be
    # able to flip it, and pays for the arms; absent is byte-identical.
    fade_style = str(info.get("fade_style") or "")
    # THE OVERLAY CUT's project default (`[scenes] overlay_cut`, W7d phase 2):
    # the scanline at which the window overlay stops, the reference engine's
    # `overlay_cut_scanline`. Its own default 150 is off the bottom of a
    # 144-line screen and therefore means NO cut, so 0/absent here is
    # byte-identical - start() emits nothing at all. A script's
    # `set_state overlay_cut` may move it later.
    overlay_cut = int(info.get("overlay_cut", 0) or 0)

    ptypes = [t for t in types if t in _PLAYER_TYPES]
    playerless = [t for t in types if t in ("menu", "logo")]
    use_player = bool(ptypes)
    # the handler each used player type registers (adventure walks like topdown)
    ticks = []
    if any(t in ("topdown", "adventure") for t in ptypes):
        ticks.append("topdown")
    if "platform" in ptypes:
        ticks.append("platform")
    if "shmup" in ptypes:
        ticks.append("shmup")
    # ...and the CURSOR tick (W7j). Its own, not topdown's: the reference's
    # POINTNCLICK state runs no automatic trigger scan at all (a rect fires
    # only when A is pressed over it) and picks the hover pose from what the
    # cursor overlaps, so the composition differs even though the room SETUP
    # is the topdown one.
    if "pointnclick" in ptypes:
        ticks.append("pointnclick")
    # Read by more than one emitter, so derived here with the rest rather
    # than mid-emission (each is a pure function of `info`).
    emote = info.get("uses_emote")
    cgb_fade = bool(fade and info.get("colour"))
    ui_redraw = bool(info.get("has_text") or info.get("has_menu"))
    # Everything above is the context, captured as it stands (see the
    # module docstring): a new derived flag is just a new local.
    return RoomsCtx(**{k: v for k, v in locals().items()
                       if not k.startswith("__")})
