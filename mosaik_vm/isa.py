"""mosaik_vm.isa - Bytecode ISA: opcodes (OPS), RPN expressions, STATES/BUTTONS, Instr/Label."""
import re


class VmError(Exception):
    pass


# A $var$ dialogue-interpolation token: $name$ -> the live value of heap var
# `name`, printed inline in a text box (the reference engine's "$var$"). Text boxes only
# (menu options stay literal). Resolved in CompiledProgram._emit_render_* via the
# core.var_get seam; byte-identical output when no string carries a token.
_INTERP_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)\$")


# --------------------------------------------------------------------------
# The instruction set: the VM8 opcode map (docs/vm8-spec.md §6). (opcode byte,
# [operand widths]). Kept in lockstep with lib/vm/core.mos's OP_* constants +
# f8()/f16() reads. u16 is little-endian, operands in source order.
# SHMUP_SCROLL (0x33) is a mosaik8 PACK opcode placed in VM8-free encoding
# space (spec §6 groups 0x2x+ are pack-backed). 0x47-0x49 held the turn-based
# battle pack, removed 2026-09-17; W7c took 0x47/0x48 and W7h took 0x49, so
# that block is spent. 0x5F and 0x66+ are the free encodings now.
# --------------------------------------------------------------------------
OPS = {
    "STOP":       (0x00, []),
    "JUMP":       (0x01, ["u16"]),
    "IDLE":       (0x02, []),
    "WAIT":       (0x03, ["u8"]),
    "LOCK":       (0x04, []),
    "UNLOCK":     (0x05, []),
    "THREAD":     (0x06, ["u16"]),
    "THREADN":    (0x07, ["u16", "u8"]),  # spawn with n popped args (last-pushed -> arg[n-1])
    "RPN":        (0x08, None),          # variable-length RPN token stream
    "SET_CONST":  (0x09, ["u8", "u16"]),
    "SET_VAR":    (0x0A, ["u8"]),        # heap[idx] = pop()
    "IF":         (0x0B, ["u16"]),       # pop(); if == 0 jump (branch-if-false)
    "SWITCH":     (0x0C, None),          # variable-length jump table: pop(); u8 count; count*(i16 val, u16 target)
    "CALL":       (0x0D, ["u16"]),       # call a subroutine (push return addr)
    "RET":        (0x0E, []),            # return from a subroutine
    # RAISE code,a,b,c -- the one exception mechanism (spec §10): 1 = RESET,
    # 2 = CHANGE_SCENE (room=a, x=b, y=c), 3 = LOAD_COMPLETE. Ends the thread.
    # The `change_scene` event is assembler sugar for RAISE 2.
    "RAISE":      (0x0F, ["u8", "u8", "u16", "u16"]),
    # RAISE 2 with the room/x/y taken off the expression stack (pushed room,
    # x, y -- popped y, x, room). Lets a scene change target a COMPUTED room,
    # which a literal RAISE cannot: the reference engine's scene stack (push the current
    # scene into a variable, later pop back to it) needs exactly this. Selected
    # only when an argument is a non-literal, so an all-literal change_scene
    # keeps the compact RAISE (byte-identical).
    "CHANGE_SCENE_E": (0x16, []),
    "SET_STATE":  (0x10, ["u8"]),        # pop(); write engine state id (see STATES)
    "PLAYER_SETPOS": (0x11, ["u16", "u16"]),   # engine-state write: move the player
    "PLAYER_SETPOS_E": (0x12, []),             # pops x,y
    "HANDLE":     (0x13, ["u8"]),        # bind THIS thread's liveness to heap[i] (join)
    "HANDLE_NEXT": (0x14, ["u8"]),       # one-shot latch: HANDLE the next THREAD/THREADN child
    "SELF":       (0x15, ["u8"]),        # bind the thread's SELF actor from bytecode
    # The player pack's upward impulse (the reference engine's EVENT_PLAYER_BOUNCE): 0 low
    # / 1 medium / 2 high. In the 0x1x PLAYER group rather than the actor one
    # because it is the player, and because 0x2E/0x2F are the actor group's
    # LAST two encodings and the animation pair below claims them.
    "PLAYER_BOUNCE": (0x17, ["u8"]),
    # The platformer plugin's KNOCKBACK_STATE (the reference engine's
    # EVENT_PLATFORMER_STATE_SET state="knockback"): throw the player away
    # from its facing and up, ignoring the pad for a while. NO OPERANDS -
    # the reference engine's knockback velocities are ENGINE FIELDS (globals), so they
    # are configured once through studio.toml [player] -> set_knockback and
    # the op only fires it. Unconfigured, the op is a no-op.
    "PLAYER_KNOCKBACK": (0x35, []),
    # PLAYER_MOVE_TO's `_E` twin: the same waitable walk to COMPUTED
    # coordinates (pushed x, y - popped y, x). What made the literal op easy is
    # exactly what an `_E` cannot do - re-read its operands on every rewound
    # re-entry - so this one LATCHES: on FIRST entry (vm_waiting == 0) it pops
    # the target into engine state and sets the context's waiting flag; every
    # re-entry steps toward the latched target and rewinds only the opcode +
    # mode. The A_EMOTE shape. It exists because the reference engine's
    # EVENT_ACTOR_MOVE_RELATIVE on the player is ALWAYS computed
    # (`player_x() + dx`) and its runtime WALKS it - a launch-pad room's pushback
    # that shoves the player out of a doorway - where the old teleport
    # fallback jumped, so the emote behind it had nothing to wait for.
    # 0x3C, the first genuinely free slot (0x1x is full, and 0x38 is FADE -
    # verify a "free" byte by IMPORTING this table, never by grepping it: a
    # regex expecting one space after the colon misses every aligned row and
    # reports a used opcode as free, which is a silent wrong-behaviour bug).
    "PLAYER_MOVE_TO_E": (0x3C, ["u8"]),   # mode; pops y, x
    # Two ACTOR-scoped ops that could not join the 0x2x group -- it is full
    # (A_SET_FRAME / A_SET_ANIM_SPEED took its last two encodings), so they take
    # free slots in the 0x1x player/state group. Nothing about them is
    # player-scoped; the encoding is where the space was.
    "A_STOP_UPDATE": (0x18, ["u8"]),   # kill the actor's On Update thread
    "A_PUSH":     (0x19, ["u8", "u8"]),  # slide an actor away from the player, tiles
    "A_EMOTE":    (0x1A, ["u8", "u8"]),  # waitable: pop an emote bubble over an actor
    # The `_E` variant of A_SET_FRAME (0x2E), here because the 0x2x group is
    # full. A pinned frame is a COMPUTED thing far more often than a literal
    # one - the reference engine's own EVENT_ACTOR_SET_FRAME takes a ScriptValue, and the
    # sprite-as-digit trick (a score display whose actor wears frame
    # `score % 10`) is the whole reason the event exists in the shooter conversion.
    "A_SET_FRAME_E": (0x1B, ["u8"]),   # actor inline; pops the frame off the expr stack
    # Re-activate a RETIRED actor exactly where the room left it - the reference engine's
    # own EVENT_ACTOR_ACTIVATE, which takes nothing but the actor. A_ACTIVATE
    # (0x20) is the SPAWN form and re-seeds tile + position from literals, so
    # using it for the event teleported a respawning actor back to its authored
    # tile and drew it at VRAM base 0 (the player's art).
    "A_REACTIVATE": (0x1C, ["u8"]),
    # WAIT until the actor's native auto-move (A_MOVE_START/_E) lands -- the
    # waitable-move pair: start the move (any coords, expressions included),
    # then await it. Yields + rewinds while `is_moving`; deactivating the
    # actor cancels the move, so the wait ends early on a mid-flight kill
    # (the shooter conversion's falling hazard: shot -> deactivated -> the drop thread moves on).
    # In the 0x1x group because the 0x2x actor group is full.
    "A_AWAIT_MOVE": (0x1D, ["u8"]),
    # WAITABLE PLAYER WALK - A_MOVE_TO's player twin, and the same shape: step
    # toward (x, y) once per frame, rewind + yield until it lands. The reference engine's
    # EVENT_ACTOR_MOVE_TO resolves `$self$` to the PLAYER in any script with no
    # actor of its own (a scene or trigger script), and its vm_actor_move_to is
    # a waitable instruction that rewinds exactly like this.
    # Coordinates are u16 because the player's are WORLD pixels: the converted
    # sample walks to x = 1272 in a 161-tile-wide room, which a u8 operand -
    # what the actor pool's A_MOVE_TO takes - cannot say at all.
    # `mode` is the reference engine's moveType: 0 diagonal, 1 horizontal-first,
    # 2 vertical-first.
    "PLAYER_MOVE_TO": (0x1E, ["u16", "u16", "u8"]),
    # ARM the next actor move with the reference engine's `moveType` + `useCollisions` /
    # `collideWith`. A one-shot LATCH (the PROJ_ANIM shape) rather than new
    # operands, so A_MOVE_TO / A_MOVE_START / _E keep their encodings and a game
    # that asks for neither is byte-identical - which every existing project is.
    #   mode: 0 both axes at once, 1 horizontal first, 2 vertical first
    #   coll: bit 0 stop at WALLS (clips the destination once, at move start),
    #         bit 1 stop at other ACTORS and the player (per frame; a hit ENDS
    #         the move, as the reference engine's does, so the waiting script continues)
    "A_MOVE_OPTS": (0x1F, ["u8", "u8"]),
    # POSITION OPERANDS ARE u16, because an actor's position is a WORLD pixel
    # and a room runs well past 255 (the converted town room is 448 px
    # square). They were u8, so every scripted move in a big room went to a
    # WRAPPED destination: one NPC's escape route is four legs at x 288..376
    # and another, standing at 376, was told to walk to 120 - it set off
    # leftwards forever and the await behind it never released, so its dialogue
    # never ran. The RefVM had it right all along (`& 0xFFFF`, commented "core
    # parity") while the console interpreter truncated - the exact silent drift
    # the five-in-lockstep rule exists for.
    "A_ACTIVATE": (0x20, ["u8", "u8", "u16", "u16"]),
    "A_SET_POS":  (0x21, ["u8", "u16", "u16"]),
    "A_MOVE_TO":  (0x22, ["u8", "u16", "u16"]),
    "A_SPEED":    (0x23, ["u8", "u8"]),
    "A_MOVE_START": (0x24, ["u8", "u16", "u16"]),   # non-blocking native move (cutscenes)
    "A_SET_CLIP": (0x25, ["u8", "u8"]),           # assign clip KIND to an actor (data-driven anim)
    # Expression-valued variants (the `_E` rule, spec §6): the position args come
    # from the per-thread RPN expression stack (pushed by preceding RPN instrs)
    # instead of inline literals, so a script can
    # `actor_move(self, player_x(), player_y())`. The event compiler picks these
    # ONLY when an arg is a non-literal expression -- a literal-only event keeps
    # the compact inline op above (byte-identical).
    "A_SET_POS_E":    (0x26, ["u8"]),             # actor inline; pops x,y off the expr stack
    "A_MOVE_START_E": (0x27, ["u8"]),             # actor inline; pops x,y
    "A_DEACTIVATE":   (0x28, ["u8"]),             # retire an actor (killed enemy / picked-up item)
    "A_SET_GROUP":    (0x29, ["u8", "u8"]),       # set an actor's collision group (faction)
    "A_SET_HP":       (0x2A, ["u8", "u16"]),      # set an actor's HP (per-actor HP: multi-HP foes)
    "A_DAMAGE":       (0x2B, ["u8", "u16"]),      # subtract from an actor's HP (clamped at 0)
    "A_SET_DIR":      (0x2C, ["u8", "u8"]),       # set an actor's facing (0 dn/1 up/2 lt/3 rt)
    "A_SET_COLLISION": (0x2D, ["u8", "u8"]),
    # The last two actor encodings, both animation: pin one frame (a prop a
    # script drives frame by frame) and set the advance rate. `speed` is GB
    # Studio's MASK, not a period - see the runtime's set_anim_speed.
    "A_SET_FRAME": (0x2E, ["u8", "u8"]),
    "A_SET_ANIM_SPEED": (0x2F, ["u8", "u8"]),      # 1 = the actor BLOCKS the player, 0 = walk through
    "UI_TEXT":    (0x30, ["u8"]),
    "MENU":       (0x31, None),           # variable-length: dest,row,count,ids...
    "HUD_SHOW":   (0x32, ["u8"]),         # HUD visibility (0 hide / non-0 show)
    "SHMUP_SCROLL": (0x33, ["u8", "u8"]),  # PACK op: pace (0 = pause), dir (0 h / 1 v / 2 keep / 3 endless loop)
    # Show / hide the PLAYER's sprite (1 = visible). The reference engine's
    # ACTOR_FLAG_HIDDEN on actors[0]: it stops the actor being DRAWN and
    # leaves everything else alone, so the player still exists, still collides
    # and its handler still runs. ONE op with an operand rather than a
    # hide/show pair - a second dispatch arm is resident image on GB/SMS.
    # Lives here because the 0x1x player group and the 0x2x actor group are
    # both full.
    "PLAYER_VISIBLE": (0x34, ["u8"]),
    # ... and the same flag on a POOL actor: (actor, on). The reference engine tests
    # ACTOR_FLAG_HIDDEN in `actors_render` and NOWHERE else, so a hidden actor
    # keeps moving, keeps colliding and is still found by the interact probe -
    # which is what makes an actor hidden in its On Init an invisible
    # INTERACTION HOTSPOT rather than a deleted one.
    #
    # A SEPARATE op from PLAYER_VISIBLE, and that is the opposite call from the
    # pair rule two lines up, for a reason worth keeping: dispatch PRUNING
    # compiles only the arms a blob contains. A second op is free to every
    # project that does not use it, where WIDENING a shared op's operands taxes
    # every project that uses it AT ALL. Measured both ways on the four
    # conversions: merging the two into one (actor, on) arm cost the reference-engine sample conversion
    # 31 B of bank 0 for a feature it never uses, where this costs it 0 - and
    # the platformer conversion, which hides actors 18 times and now deactivates none, comes
    # out 13 B AHEAD, because A_DEACTIVATE / A_REACTIVATE prune away with it.
    # The pair rule still holds WITHIN a feature (hide and show always ship
    # together, so they are one op); it does not hold across two callers that
    # a project uses independently.
    "A_VISIBLE": (0x3D, ["u8", "u8"]),
    # TYPEWRITER text reveal - the reference engine's `text_draw_speed`: (speed 0-7, ff).
    # Speed indexes its own ui_time_masks {0,0,1,3,7,15,31,63} counted in LCD
    # frames - one printable char whenever `frame & mask == 0` - and 0 is
    # INSTANT, the engine default, so a program that never runs this op keeps
    # today's whole-box draw. `ff` = A or B held fast-forwards the reveal
    # (their INPUT_A_OR_B_PRESSED). GLOBAL state, as theirs is: it persists
    # across boxes and scenes until set again.
    "TEXT_SPEED": (0x3E, ["u8", "u8"]),
    # The per-character BLIP beside it - the reference engine's `text_sound`: a square
    # tone (freq Hz, frames) played through the g_tone seam once per reveal
    # step that drew at least one char. freq 0 = off (their SFX_STOP_BANK).
    # Reaches nothing without vm.snd registered, like every sound op.
    "TEXT_BLIP": (0x3F, ["u16", "u8"]),
    "FADE":       (0x38, ["u8", "u8"]),   # dir (0 out / 1 in), frames
    "SHAKE":      (0x39, ["u8", "u8"]),   # frames, amplitude (px) -- start a screen shake
    # A one-shot LATCH consumed by the next SHAKE (the A_MOVE_OPTS shape), so
    # SHAKE keeps its encoding and a program that emits none is byte-identical:
    # `axis` is the reference engine's CAMERA_SHAKE_X (1) / _Y (2) mask - its own event
    # defaults to HORIZONTAL where ours has always been vertical-only - and
    # `wait` makes the op BLOCK, which its `camera_shake_frames` does by
    # setting ctx->waitable and returning FALSE until the frames run out.
    "SHAKE_OPTS": (0x4C, ["u8", "u8"]),
    "CAM_MOVE_TO": (0x3A, ["u8", "u8", "u8"]),  # x, y, step -- scripted camera pan (waitable)
    "SCROLL_BG":  (0x3B, ["u8", "u8"]),   # vx, vy signed 1/4-px per frame -- generic background scroll (0,0 = stop)
    "TIMER_SET":  (0x40, ["u8", "u16", "u16"]),   # id, period, entry
    "TIMER_STOP": (0x41, ["u8"]),         # id
    # btn_id, entry -- spawn on a button edge. Bit 7 of btn_id is the OVERRIDE
    # flag (the reference engine's `input_slots[i] & 0x80`, events.c): the button is
    # CONSUMED, so the native player handler never sees it that frame. Buttons
    # are 0..7, so the flag rides the operand rather than widening the op and
    # an attachment without it is byte-identical.
    "INPUT_ATTACH": (0x42, ["u8", "u16"]),
    "INPUT_DETACH": (0x43, ["u8"]),          # btn_id
    "SAVE":       (0x44, []),   # snapshot heap + engine state to battery SRAM (opt-in vm.save)
    "LOAD":       (0x45, []),   # restore from SRAM (a no-op with no valid save)
    # THE SAVE SLOT is NOT an operand on either of those two (W7c): widening
    # them would change the blob format for every project that already saves.
    # It rides the `save_slot` STATE, a one-shot latch the next save / load /
    # clear / peek / save_exists() consumes - the menu_cancel / box_hold /
    # A_MOVE_OPTS shape, so a project that only ever uses slot 0 emits no
    # write at all and stays byte-identical.
    #
    # CLEAR zeroes the latched slot's signature (the reference engine's `data_clear`:
    # the slot reads as empty afterwards, the bytes are not wiped).
    "DATA_CLEAR": (0x47, []),
    # PEEK reads ONE heap cell out of the latched slot WITHOUT loading it -
    # the reference engine's `data_peek(slot, idx, 1, dest)` plus the `_ifConst` wrapper
    # its lowering always emits, which writes 0 to the destination when the
    # slot holds no save. Folding that in is not a shortcut: it is what the
    # reference's three instructions add up to, and it keeps the "no save"
    # answer out of the converter.
    "DATA_PEEK":  (0x48, ["u8", "u8"]),   # src heap cell, dest heap cell
    "SET_PLAYER_HIT": (0x46, ["u16"]),   # register a script run when a projectile strikes the player (F3.5)
    # W7h - ATTACH A SCRIPT TO A hUGE `6xy` CALL-ROUTINE EFFECT (the reference engine's
    # EVENT_SET_MUSIC_ROUTINE / reference-VM `vm_music_routine`). Four slots, so the
    # operand is masked `& 3` exactly as the reference masks it. The script is
    # spawned from the MAIN LOOP, never from the driver interrupt, with the
    # effect's own parameter as `arg(0)` - see lib/vm/core.mos.
    # The registration dies with the scene (the reference VM calls `music_init_events` on
    # every CHANGE_SCENE), like SET_PLAYER_HIT above.
    "MUSIC_ROUTINE": (0x49, ["u8", "u16"]),   # slot 0..3, script entry
    # The window OVERLAY CURTAIN (the reference engine's ui.c): a solid cover on the GB
    # window layer, positioned in tile ROWS (0 = whole screen, 18 = gone) and
    # slid one row per `ui_time_masks[speed] + 1` frames. NOT a fade - the
    # palettes stay bright and the scene is revealed row by row. Real on the GB
    # family through the opt-in vm.fx pack (core.set_overlay), a no-op that
    # reports ARRIVED elsewhere so a waiting script cannot hang.
    # REPLACE A BACKGROUND TILE'S DATA (the reference engine's VM_REPLACE_TILE /
    # EVENT_REPLACE_TILE_XY): rewrite the 16 bytes of tile `dst` from the
    # world's replacement bank, so every map cell holding that index redraws
    # at once. That indirection is the point - the reference engine replaces DATA rather
    # than a map cell precisely so the change survives scrolling and repaints,
    # and it is how a game with no HUD layer draws a NUMBER on the background
    # (the RPG check conversion spells its wallet as four cells and writes a digit into
    # each). `dst` is a background TILE INDEX, resolved by whoever authored the
    # write - the map is static data, so a converted (x, y) resolves at
    # conversion time and the runtime needs no tilemap read.
    # Routed through the opt-in core.set_bkg_tile seam (a no-op unregistered),
    # like every pack op: a world with no replacement bank links none of it.
    "BKG_TILE":   (0x4D, ["u8", "u8"]),      # dst tile index, src replacement tile
    # ...and its `_E` twin, which is the COMMON case: the reference engine's own
    # `tileIndex` is a ScriptValue, and a digit readout computes it
    # (`gold % 100 / 10`). `dst` stays inline - a cell of the map is a place,
    # not a value.
    "BKG_TILE_E": (0x4E, ["u8"]),            # dst inline; pops src off the expr stack
    # A RUNTIME palette write (the reference engine's EVENT_PALETTE_SET_SPRITE and its
    # BACKGROUND / UI / EMOTE siblings, which are all the SAME primitive there:
    # `paletteSetUI` is background slot 7 and `paletteSetEmote` is sprite slot
    # 7, both via one `_paletteLoad` with mask 128). `pal` is an index into the
    # WORLD's [[palette]] library, not inline colour data - the library already
    # holds a sprite palette in the reference engine's own sprite convention
    # ([c0, c0, c1, c3], colour 2 skipped because sprite entry 0 is
    # transparent), so one byte carries what the reference VM spends twelve on.
    "PAL_SET":    (0x4F, ["u8", "u8", "u8"]),  # layer (0 spr / 1 bkg), slot 0..7, library index
    "OVERLAY_SHOW": (0x4A, ["u8"]),          # row (18 = hide)
    "OVERLAY_MOVE_TO": (0x4B, ["u8", "u8"]),  # row, speed -- WAITABLE
    # sound bank (vm.snd) -- routed through the opt-in
    # core.set_sound seam (a no-op unregistered, so a silent game links no audio).
    "SFX":        (0x50, ["u8"]),          # play a canned SFX by id (-> sound.sfx)
    "TONE":       (0x51, ["u16", "u8"]),   # play a square tone: freq_hz, frames (-> sound.beep)
    "SND_STOP":   (0x52, []),              # stop the sound channel (-> sound.stop)
    # minimal MUSIC (audio plan): a "song" is a looping script of tones; MUSIC_PLAY
    # spawns it as a TRACKED thread so MUSIC_STOP can kill it instantly (+ silence).
    "MUSIC_PLAY": (0x53, ["u16"]),         # spawn the looping song thread at `entry`, tracked
    "MUSIC_STOP": (0x54, []),              # kill the song thread + silence the music channel
    "MUSIC_TONE": (0x55, ["u16", "u8"]),   # a tone on the MUSIC voice (own channel, coexists with SFX)
    # Stage 4b: a real multi-channel MUSIC DRIVER (vm.music) plays a SONG from DATA by
    # index, routed through the opt-in core.set_music_driver seam (no-op unregistered).
    "MUSIC_SONG": (0x56, ["u8"]),          # play registered song #id through the music driver
    "MUSIC_PAUSE": (0x57, []),             # pause the driven song (silence, keep position)
    "MUSIC_RESUME": (0x58, []),            # resume a paused song
    "MUSIC_MUTE": (0x59, ["u8"]),          # per-channel MUSIC mute mask (bit c SET = ch c muted, the reference VM's
                                           # polarity); persists until the next MUSIC_PLAY clears it
    # RELEASE A_SET_ANIM_STATE's pin: the actor derives idle / walk from its
    # movement again (`actor.clear_anim_state`). The reference engine's set-state to the
    # sprite's DEFAULT state (`''`, `statesOrder[0]`) - pinning state 0 instead
    # would freeze the idle clip on a walking actor. A lodger here because the
    # actor groups are full; a second op rather than a sentinel in 0x37's
    # operand, which every existing set-state user would pay for in bank 0.
    "A_CLEAR_ANIM_STATE": (0x5A, ["u8"]),
    # END the thread watching heap cell `i` (the reference engine's VM_TERMINATE, which
    # reads a context id out of a variable). Ours keeps LIVENESS in that cell
    # (HANDLE / HANDLE_NEXT), so the op finds the live context whose handle
    # IS cell `i` and kills it; no such thread is a no-op, as a finished
    # handle is in the reference VM. An opcode rather than a state because the operand is
    # a heap INDEX, the HANDLE family's operand, which a state could only
    # carry as a pushed literal (7 bytes of blob a use against 2).
    "THREAD_STOP": (0x5B, ["u8"]),
    # Replace actor `i`'s collision BOX at run time: size (w, h) and its
    # offset inside the drawn sprite (ox, oy) - `vm.actor.set_box`, which the
    # generated rooms.mos calls at room load. The reference engine's
    # EVENT_ACTOR_SET_COLLISION_BOX (`vm_actor_set_bounds`). Five operands, so
    # no state can say it.
    "A_SET_BOX": (0x5C, ["u8", "u8", "u8", "u8", "u8"]),
    # Store actor `i`'s FACING into heap cell `v`, in this VM's numbering
    # (0 down / 1 up / 2 left / 3 right, as `player_dir()` and A_SET_DIR).
    # The reference engine's EVENT_ACTOR_GET_DIRECTION (`vm_actor_get_dir` stores
    # `actor->dir` into a variable), whose numbering a converter maps. An
    # opcode and not an `actor_dir()` RPN read, MEASURED: the token's empty
    # case grows rpn_eval's dense switch and cost every SMS/GG build 5 B of
    # bank 0 whether it used it or not; an opcode arm prunes to 0 B.
    "A_GET_DIR": (0x5D, ["u8", "u8"]),
    # START actor `i`'s On Update script when it is not running (the reference engine's
    # EVENT_ACTOR_START_UPDATE, `vm_actor_begin_update`: `if (script_update &&
    # hscript_update & SCRIPT_TERMINATED) script_execute(...)`) - fresh from
    # the top, and nothing when it runs already or the actor has none. The
    # stopping half is A_STOP_UPDATE (0x18); the thread handle lives in
    # vm.entity, reached through a seam the same way.
    "A_START_UPDATE": (0x5E, ["u8"]),
    "PROJ_LAUNCH": (0x60, ["u16", "u16", "u8", "u8", "u8", "u8"]),  # x,y,vx,vy,tile,life
    "PROJ_LAUNCH_E": (0x61, ["u8", "u8"]),  # tile,life inline; pops x,y,vx,vy off the expr stack
    # Masked variants (F3.5): a non-default collision mask (which groups the shot can
    # hit + the PLAYER bit). A default-mask shot keeps PROJ_LAUNCH/_E (byte-identical).
    "PROJ_LAUNCH_M": (0x62, ["u16", "u16", "u8", "u8", "u8", "u8", "u8"]),  # x,y,vx,vy,tile,life,mask
    "PROJ_LAUNCH_EM": (0x63, ["u8", "u8", "u8"]),  # tile,life,mask inline; pops x,y,vx,vy
    # Fire along an ANGLE instead of a velocity (reference-engine units: 0 up, 64
    # right, 256 to the turn) at `speed` in 1/16 px per frame - its direction
    # dial, its angle VARIABLE, and an aim computed with atan2(). Always the
    # expression form and always masked: an angle launch is a computed thing
    # by nature, so the four literal/masked permutations the velocity ops
    # needed would all be dead weight.
    "PROJ_LAUNCH_A": (0x64, ["u8", "u8", "u8"]),  # tile,life,mask inline; pops x,y,angle,speed
    # Animate the NEXT launch in this thread's event: `frames` frames cycled
    # every `period` display frames, each frame `stride` tiles after the last
    # (the reference engine's loopAnim + animSpeed -- the rotating shot; the converted
    # 8x16 shot cell is 2 tiles, so its stride is 2). A one-shot LATCH consumed
    # by the following PROJ_LAUNCH_*; a launch with no preceding PROJ_ANIM
    # stays a static tile (byte-identical).
    "PROJ_ANIM": (0x65, ["u8", "u8", "u8"]),      # frames, period, stride
    # The SHOT's own collision group (the reference engine's projectile def carries
    # `collision_group` beside `collision_mask`, and `projectiles.c` passes
    # it to the struck actor's script as parameter 0 - which is how one
    # script serves "hit by group 1" and "hit by group 2" separately). A
    # one-shot LATCH the next launch consumes, the shape PROJ_ANIM already
    # takes, so no launch variant gains an operand and every existing blob
    # stays byte-identical.
    "PROJ_GROUP": (0x36, ["u8"]),
    # Pin an actor's clip STATE (the reference engine's EVENT_ACTOR_SET_STATE ->
    # VM_ACTOR_SET_ANIM_SET). Its sprite carries several named STATES over
    # ONE tile sheet and the op only re-points which set of 8 animations
    # the actor draws from, so this is a clip-STATE select and not a kind
    # swap. In the 0x3x group because the actor group (0x2x) is full.
    # `st` bit 7 = play ONCE and hold the last frame; bits 0-6 the state.
    # The reference engine never asks for one-shot: its EVENT_ACTOR_SET_STATE carries
    # only {actorId, spriteStateId} and its compiler always emits
    # `VM_ACTOR_SET_FLAGS actor, 0, ACTOR_FLAG_ANIM_NOLOOP`, which CLEARS
    # that flag (`flags &= ~(mask & ~flags)` with flags = 0) - i.e. LOOP.
    # The bit is ours, for authoring.
    "A_SET_ANIM_STATE": (0x37, ["u8", "u8"]),
}

# portable button ids (kept in lockstep with lib/vm/core.mos's BTN_* consts).
BUTTONS = {"a": 0, "b": 1, "start": 2, "select": 3,
           "up": 4, "down": 5, "left": 6, "right": 7}
_VARLEN = ("RPN", "MENU")                 # opcodes whose operand is a raw byte blob

# Curated engine-state ids (GET_STATE reads, SET_STATE writes) -- kept in lockstep
# with lib/vm/core.mos's ST_* consts. The safe, pointer-free replacement for
# the reference VM's raw far-memory ops: every field is an id, so
# bytecode never carries a raw address. Player x/y are ALSO reachable via the
# dedicated PLAYER_X/PLAYER_Y RPN tokens + PLAYER_SETPOS (kept byte-identical);
# camera + scene are new here.
STATES = {
    "player_x": 0, "player_y": 1,
    "camera_x": 2, "camera_y": 3,
    # the reference engine's `camera_settings` BYTE, flag for flag and number for number
    # (the reference VM's `include/camera.h`): LOCK_X 0x01 and LOCK_Y 0x02 mean the camera
    # FOLLOWS on that axis, and X_MIN 0x04 / X_MAX 0x08 / Y_MIN 0x10 /
    # Y_MAX 0x20 are its `preventScroll` directions (left / right / up / down),
    # which clamp the camera against its own previous position. `camera_init`
    # writes 0x03, so following both axes is the default and that is ours too.
    #
    # READ THE POLARITY TWICE. This state used to be a single bool spelled the
    # OTHER WAY ROUND - "write 0 = release (resume follow), 1 = hold here" -
    # which is the same WORD as the reference engine's flag with the opposite SENSE, and
    # the importer only got it right by accident of shape. Under the parity
    # byte, `0` is the full pin (follow neither axis) and `3` resumes following
    # both. The authoring EVENTS keep their own verbs: `camera_lock` still pins
    # at (x, y) and `camera_release` still resumes - only the byte behind them
    # changed.
    "camera_lock": 4,
    "scene": 5,
    "save_exists": 6,       # read-only: 1 when a valid battery save is present
    "player_dir": 7,        # the player's facing (0 down/1 up/2 left/3 right); WRITABLE
    # Read-only, and answered by the CONSOLE rather than by the game: 1 on
    # colour hardware. It has to be a runtime state read and not a compile-time
    # `if platform` fork, because one bytecode blob serves every target - GB
    # Studio's own EVENT_IF_COLOR_SUPPORTED tests its `_is_CGB` the same way.
    "is_color": 8,
    # WRITE-only: reseed the host RNG (`rand()`'s stream), the reference engine's
    # EVENT_RNG_SEED. A state write rather than an opcode because the value is
    # already an expression and SET_STATE is in every game's blob that reads one.
    "rand_seed": 9,
    # READ-only: a free-running u16 VM-FRAME counter (the reference engine's `sys_time`),
    # the clock a bytecode-level rate limit / cooldown compares against. Note it
    # counts VM frames, not display frames (spec R3) -- the two differ per
    # console, so it measures game time, not wall time.
    "game_time": 10,
    # WRITE-only: 1 = other actors block the player, 0 = the player passes
    # through them. The reference engine keeps the player at actors[0], so its
    # EVENT_ACTOR_COLLISIONS_DISABLE aimed at the player clears that actor's
    # ACTOR_FLAG_COLLISION; ours is not a pool actor, so the actor op (whose
    # operand is a pool slot) cannot say it and a state does.
    "player_collide": 11,
    # WRITE-only: the player's movement speed in px per VM frame, and its
    # animation MASK. The reference engine keeps the player at actors[0], so
    # EVENT_ACTOR_SET_MOVEMENT_SPEED / _SET_ANIMATION_SPEED may aim at it, and
    # `vm_actor_set_move_speed` / `vm_actor_set_anim_tick` (core/vm_actor.c)
    # write the field with no PLAYER special case at all - unlike deactivate,
    # which forks. Ours are states for the same reason player_collide is: the
    # actor opcode's operand is a POOL SLOT and the player is not in the pool.
    "player_speed": 12,
    "player_anim_speed": 13,
    # WRITE-only: the reference engine's platformer BLANK state and the gravity it falls
    # under (`states/platform.c` `state_update_blank`, engine field
    # `plat_blank_grav`). Blank is the state a pit trigger raises: the pad is
    # ignored, the horizontal is zeroed, the sprite wears its HURT set and the
    # player falls under a gravity the script chooses - which is how the
    # sample's pit death plays its fall before the respawn teleport. Ours
    # lowered the state to its CALLBACK alone, so the player kept normal
    # platform physics, landed in the pit and stood there in the idle pose for
    # the callback's four seconds (reported from play, 2026-08-24).
    # States rather than an opcode for the `player_speed` reason - the reference engine
    # writes engine FIELDS here, which have no actor operand at all - and two
    # CONSECUTIVE ids so `set_state` spends one range test for the pair.
    "player_blank": 14,
    "player_blank_grav": 15,
    # 16+ PACK-defined (spec 5.3). Ours: the input pack
    # publishes "is this button still HELD" (what input_attach's rising edge
    # cannot answer, so a DAS / auto-repeat loop can live in bytecode) and the
    # UI pack publishes "a box or menu is up".
    # WRITE-only: the follow camera's clamp RECTANGLE in world pixels
    # (the reference engine's `scroll_x_min` / `_max` / `scroll_y_min` / `_max`, which its
    # EVENT_CAMERA_SET_BOUNDS writes and its `camera_update` clamps into).
    # Ours had a MAX only, so a scene that PINS the camera - the platformer conversion's
    # `tutorial_(0)` fixes scroll y at the level's bottom - scrolled up when
    # the player jumped where the reference never does. Four states rather
    # than an opcode, the `player_speed` precedent; four CONSECUTIVE ids, so
    # the interpreter spends one range test and one call for all of them, and
    # they fold away together under VM_ST_CAMERA_MIN_X.
    "camera_min_x": 25, "camera_max_x": 26,
    "camera_min_y": 27, "camera_max_y": 28,
    "held_a": 16, "held_b": 17, "held_start": 18, "held_select": 19,
    "held_up": 20, "held_down": 21, "held_left": 22, "held_right": 23,
    "ui_open": 24,
    # READ-only: every held button in ONE bitmask (bit = portable button id,
    # so bit 0 = a .. bit 7 = right). An await-any-input poll is one
    # GET_STATE + one B_AND where the per-button spelling was eight reads
    # OR'd - 15 RPN tokens re-evaluated every lap (the platformer conversion's
    # room 2).
    "pad_held": 29,
    # WRITE-only, ONE-SHOT: arm the NEXT menu so B cancels it. The reference engine's
    # `.UI_MENU_CANCEL_B` (core/ui.c ui_run_menu returns 0 on B) is a per-menu
    # flag of its VM_CHOICE; ours is a latch consumed when the MENU op opens,
    # the SHAKE_OPTS / PROJ_GROUP shape, so the blob format is unchanged and a
    # menu that never arms it is byte-identical. A cancelled menu writes -1 to
    # its variable (the confirm writes the 0-based cursor), so `pick + 1` is
    # the reference engine's own 0 = cancelled / 1..n = option numbering in one add.
    # Folds away under VM_ST_MENU_CANCEL for a blob that never writes it.
    "menu_cancel": 30,
    # WRITE-only, ONE-SHOT: hold the NEXT text box on screen for n VM frames
    # and close it WITHOUT a key press, instead of waiting for the A dismiss.
    # The reference engine's two non-key close modes are both this shape, read off its own
    # compiler (scriptBuilder.ts textDialogue): `closeWhen: "text"` emits an
    # `_overlayWait` carrying NO button flag and then `wait_frames(n)`, and
    # `closeWhen: "notModal"` skips the wait AND the close, leaving the box up
    # until a later EVENT_DIALOGUE_CLOSE_NONMODAL - so a box that stands for a
    # while and closes on its own covers both. Consumed when the UI_TEXT op
    # opens, the menu_cancel / SHAKE_OPTS shape, so the blob format is
    # unchanged and a box that never arms it is byte-identical. The countdown
    # runs AFTER the typewriter reveal finishes, which is the order the reference engine's
    # own `.UI_WAIT_TEXT`-then-`wait_frames` gives. Folds away under
    # VM_ST_BOX_HOLD.
    "box_hold": 31,
    # WRITE-only: which way a fade goes - the reference engine's own `fade_style` field
    # and its numbering, 0 = towards WHITE, 1 = towards BLACK. Persistent (not
    # a one-shot latch): every later FADE, the room-load auto-fade included,
    # ramps that way until the next write. The engine's default is black, so
    # a blob that never writes it is byte-identical; a project's DEFAULT is a
    # `studio.toml [scenes] fade_style` the generated rooms.mos applies at
    # boot. The reference engine's default is 0, white (the reference VM's fade_manager.c,
    # `DMGFadeToWhiteStep` / `CGBFadeToWhiteStep`, measured on its sample
    # ROM: BGP walks E4 -> 90 -> 40 -> 00). Folds away under VM_ST_FADE_STYLE;
    # the fx ramp's white arm under VM_FADE_STYLE, which the build states
    # from this OR the rooms.mos default.
    "fade_style": 32,
    # WRITE-only: restart timer `v`'s countdown (0..3) - the reference engine's
    # EVENT_TIMER_RESTART, `vm_timer_reset`: `remains = value`. It does NOT
    # start a stopped timer (the active flag is left alone), which is exactly
    # what `timer_set` cannot say. Folds away under VM_ST_TIMER_RESET.
    "timer_reset": 33,
    # WRITE-only: 1 hides EVERY sprite, 0 shows them again - the reference engine's
    # VM_HIDE_SPRITES / VM_SHOW_SPRITES (`hide_sprites`, persistent across
    # scenes). Goes through `video.hide_sprites` / `show_sprites`, whose GBDK
    # form records the program's wish so the box's sprite cut restores it.
    # Folds away under VM_ST_SPRITES_HIDDEN.
    "sprites_hidden": 34,
    # WRITE-only: 1 PAUSES the scene type's update (the player handler, the
    # trigger scan and the entity update), 0 resumes it - the reference engine's
    # EVENT_SCENE_UPDATE_PAUSE / _RESUME (`pause_state_update`, which gates
    # `state_update()` alone: actors, projectiles, the camera and music keep
    # running, and the player keeps DRAWING). A scene load clears it, as
    # core.c's does. Folds away under VM_ST_SCENE_UPDATE_PAUSED.
    "scene_update_paused": 35,
    # WRITE-only: the reference engine's four `EVENT_CAMERA_PROPERTY_SET` fields, in its
    # own order and its own units (`eventCameraPropertySet.js`,
    # `scriptBuilder.ts cameraSetPropertyToScriptValue`). The DEAD ZONE is a
    # follow tolerance in pixels - the camera does not move at all while the
    # focus stays within it, which is how a follow camera stops jittering - and
    # the reference engine clamps it to 0..40. The OFFSET shifts the followed point, so a
    # positive x offset draws the player right of centre.
    #
    # Their LIFETIMES DIFFER, and a one-room probe cannot see it: the reference VM calls
    # `camera_reset()` (which zeroes the deadzones and nothing else) from
    # `load_scene`'s `if (init_data)`, while the offsets are written only by
    # `camera_init`. So a dead zone is PER SCENE and an offset is GLOBAL.
    #
    # Four states rather than an opcode (the `player_speed` precedent: GB
    # Studio writes an engine FIELD with no actor operand), four CONSECUTIVE
    # ids so `set_state` spends one range test for all of them, and the whole
    # block folds away under VM_CAM_PROPS for a blob that writes none.
    "camera_deadzone_x": 36, "camera_deadzone_y": 37,
    "camera_offset_x": 38, "camera_offset_y": 39,
    # WRITE-only ONE-SHOT LATCH: which of the three save slots the NEXT save /
    # load / clear / peek / `save_exists()` uses (W7c). The reference engine gives every
    # one of those events its own `saveSlot` field (0..2), and this is how the
    # value reaches an op whose operands may not change - the same latch shape
    # as `menu_cancel`, `box_hold` and `A_MOVE_OPTS`, consumed by the operation
    # so the default is always slot 0 and a project that never writes it emits
    # nothing. Folds away with the whole slot machinery under VM_SAVE_SLOTS.
    "save_slot": 40,
    # WRITE-only, PERSISTENT: the scanline at which the WINDOW OVERLAY stops -
    # the reference engine's `overlay_cut_scanline` (W7d phase 2), whose default is 150
    # and whose event clamps 0..150. At that line the window layer goes off and
    # sprites come back unless `sprites_hidden` says otherwise, which is what
    # lets an overlay (the curtain, a box) cover only the TOP of the screen
    # with the room playing below it - the reference VM's `simple_LCD_isr` does exactly
    # that, and 150 is off-screen, so its default means "no cut".
    #
    # A state rather than an opcode for the `fade_style` reason: the reference engine
    # writes a global engine variable with no operand of its own, and the value
    # is a ScriptValue, so SET_STATE's expression already carries it. Persistent
    # (not a latch): every later overlay is cut there until the next write.
    # GB FAMILY ONLY - the cut is `LYC_REG` plus a window layer, and neither
    # exists on SMS/GG/NES or the cc65 consoles, where the verb behind it is an
    # honest no-op. Folds away under VM_ST_OVERLAY_CUT.
    "overlay_cut": 41,
}

#: How many save slots the engine has - the reference engine's own three
#: (`eventDataSave.js`'s togglebuttons). `vm.sram`'s blob is 264 B, so three
#: slots are 792 B of the one 8 KB cart-RAM bank `platform.save` maps.
SAVE_SLOTS = 3

#: The dead zone's ceiling, the reference engine's own (`clampScriptValueConst(value, 0,
#: 40)`). Half the screen is 80 px, so 40 is "the focus may wander a quarter of
#: the screen before the camera answers".
CAMERA_DEADZONE_MAX = 40

#: Where the window overlay stops by DEFAULT, and the highest line the event
#: may name - the reference VM's `LYC_SYNC_VALUE` and `eventOverlaySetScanlineCutoff.js`'s
#: `max: 150`. The screen is 144 lines, so the default is off-screen: it means
#: "no cut", which is why a project that never writes `overlay_cut` pays
#: nothing. Anything from 144 up disarms the cut here for the same reason.
OVERLAY_CUT_OFF = 150

#: `camera_settings` (state 4) bit for bit, as `camera.h` spells them.
CAM_LOCK_X = 0x01       # the camera FOLLOWS on x
CAM_LOCK_Y = 0x02       # the camera FOLLOWS on y
CAM_NO_LEFT = 0x04      # preventScroll left  (reference-VM CAMERA_LOCK_X_MIN)
CAM_NO_RIGHT = 0x08     # preventScroll right (CAMERA_LOCK_X_MAX)
CAM_NO_UP = 0x10        # preventScroll up    (CAMERA_LOCK_Y_MIN)
CAM_NO_DOWN = 0x20      # preventScroll down  (CAMERA_LOCK_Y_MAX)
CAM_FOLLOW_BOTH = CAM_LOCK_X | CAM_LOCK_Y       # camera_init's 0x03
CAM_PINNED = 0                                  # follow neither axis

ST_HELD_BASE = 16       # 16 + button id (spec 5.4) = that button held
ST_UI_OPEN = 24

# RPN token bytes: the VM8 token map (docs/vm8-spec.md §7), kept in lockstep
# with lib/vm/core.mos's rpn_eval. All values i16; binary ops pop right first.
RPN = {
    "PUSH": 0x01, "VAR": 0x02, "ACTOR_X": 0x03, "ACTOR_Y": 0x04,
    "PLAYER_X": 0x05, "PLAYER_Y": 0x06, "GET_STATE": 0x07,
    "ACTOR_HP": 0x08, "SELF_SLOT": 0x09,   # per-actor HP + the bound actor's slot
    "ARG": 0x0A,                           # thread argument n (THREADN spawns)
    # is the actor's native auto-move (A_MOVE_START) still in flight? 1/0.
    # The waitable-move idiom: `actor_move` + `while actor_moving(n) { wait 1 }`
    # is arrival-timed at ANY speed and ends early when the actor is
    # deactivated (deactivate cancels the move) -- what a fixed-frames wait
    # after a move gets wrong the moment the actor's speed changes.
    "ACTOR_MOVING": 0x0B,       # (also see the A_AWAIT_MOVE op, the waitable form)
    "ADD": 0x10, "SUB": 0x11, "MUL": 0x12, "DIV": 0x13, "MOD": 0x14,
    # the bitwise group (core in VM8 -- flag variables are ubiquitous);
    # shift counts are masked to 0..15, SHR is a logical shift on the u16 bits
    "B_AND": 0x15, "B_OR": 0x16, "B_XOR": 0x17, "SHL": 0x18, "SHR": 0x19,
    "EQ": 0x20, "NE": 0x21, "LT": 0x22, "GT": 0x23, "LE": 0x24, "GE": 0x25,
    "AND": 0x30, "OR": 0x31, "NOT": 0x32,
    "MIN": 0x40, "MAX": 0x41, "NEG": 0x42, "ABS": 0x43, "B_NOT": 0x44,
    "RAND": 0x50,
    # atan2(y, x) -> a reference-engine angle (0 up, 64 right, 256 to the turn), the
    # aim behind a projectile launched at a variable angle. Its implementation
    # is a PACK (vm.trig) reached through a seam, so the tables cost only the
    # games that aim.
    "ATAN2": 0x51,
    "END": 0xFF,
}
# infix operator -> (precedence, RPN token). Higher precedence binds tighter;
# C's ordering (or < and < | < ^ < & < eq < cmp < shift < +- < */%).
_BINOPS = {
    "or": (1, "OR"), "and": (2, "AND"),
    "|": (3, "B_OR"), "^": (4, "B_XOR"), "&": (5, "B_AND"),
    "==": (6, "EQ"), "!=": (6, "NE"),
    "<": (7, "LT"), ">": (7, "GT"), "<=": (7, "LE"), ">=": (7, "GE"),
    "<<": (8, "SHL"), ">>": (8, "SHR"),
    "+": (9, "ADD"), "-": (9, "SUB"),
    "*": (10, "MUL"), "/": (10, "DIV"), "%": (10, "MOD"),
}
_FUNCS = {"actor_x": "ACTOR_X", "actor_y": "ACTOR_Y", "actor_hp": "ACTOR_HP",
          "actor_moving": "ACTOR_MOVING",
          "min": "MIN", "max": "MAX", "abs": "ABS",
          "player_x": "PLAYER_X", "player_y": "PLAYER_Y", "self_slot": "SELF_SLOT",
          "rand": "RAND", "arg": "ARG",
          "atan2": "ATAN2"}
# no-arg engine-state READS -> a GET_STATE token + the curated state id.
_STATE_FUNCS = {"camera_x": STATES["camera_x"], "camera_y": STATES["camera_y"],
                "scene": STATES["scene"], "save_exists": STATES["save_exists"],
                "player_dir": STATES["player_dir"], "is_color": STATES["is_color"],
                "game_time": STATES["game_time"]}
# The PACK-defined reads (spec 5.3) as expression functions: `held_a()` etc.
# answer "is this button STILL down", which input_attach's rising edge cannot,
# and `ui_open()` says whether a box or menu owns the screen. The runtime and
# STATES have published these ids all along; without an expression spelling
# they were unreachable from an event script (a DAS / auto-repeat loop or a
# reference-engine EVENT_IF_INPUT had nothing to test).
_STATE_FUNCS.update({name: STATES[name] for name in (
    "held_a", "held_b", "held_start", "held_select",
    "held_up", "held_down", "held_left", "held_right", "ui_open",
    "pad_held")})

# The expression-stack cap. VM8 (§14) pins VM_STACK at a UNIFORM 8 cells per
# thread on every console -- one size set means one blob, one compiler check,
# and no "works on GB, corrupts on Lynx" bug class. `vpush` SILENTLY DROPS a
# push past it, so the assembler must verify each stream's peak depth here;
# the runtime guard is a safety net, never a path reached by compiled content.
MIN_VM_STACK = 8

# Text ROWS on the SHORTEST screen any target console has (Lynx / PC Engine
# 102 px = 12 cells; the GB family is 18, SMS/GG 28-ish). Bytecode is
# target-neutral, so any screen coordinate the toolchain has to CHOOSE -- as
# opposed to one the author gave -- must fit here or it addresses off the
# bottom of the small screens. Same rule as MIN_VM_STACK above: hold to the
# smallest. Used for the default `menu` row (see events._menu_blob).
MIN_SCREEN_ROWS = 12

# VM8 sizing (spec §14) the toolchain enforces: thread args per context.
NARGS = 4

# RPN token -> (operand bytes that follow, stack delta). A push-class token
# grows the stack by 1, a binary op shrinks it by 1, a unary op leaves it. The
# peak is always reached at a push, so tracking the running depth finds the max.
_RPN_STACK_EFFECT = {
    0x01: (2, +1),   # PUSH const (u16)
    0x02: (1, +1),   # VAR (heap idx)
    0x03: (1, +1),   # ACTOR_X (actor id)
    0x04: (1, +1),   # ACTOR_Y (actor id)
    0x05: (0, +1),   # PLAYER_X
    0x06: (0, +1),   # PLAYER_Y
    0x07: (1, +1),   # GET_STATE (state id)
    0x08: (1, +1),   # ACTOR_HP (actor id)
    0x09: (0, +1),   # SELF_SLOT
    0x0A: (1, +1),   # ARG (thread argument index)
    0x0B: (1, +1),   # ACTOR_MOVING (actor id)
    0x10: (0, -1), 0x11: (0, -1), 0x12: (0, -1), 0x13: (0, -1), 0x14: (0, -1),  # ADD..MOD
    0x15: (0, -1), 0x16: (0, -1), 0x17: (0, -1),                                # B_AND B_OR B_XOR
    0x18: (0, -1), 0x19: (0, -1),                                               # SHL SHR
    0x20: (0, -1), 0x21: (0, -1), 0x22: (0, -1),                                # EQ NE LT
    0x23: (0, -1), 0x24: (0, -1), 0x25: (0, -1),                                # GT LE GE
    0x30: (0, -1), 0x31: (0, -1),   # AND OR (binary)
    0x32: (0, 0),                   # NOT (unary)
    0x40: (0, -1), 0x41: (0, -1),   # MIN MAX (binary)
    0x42: (0, 0), 0x43: (0, 0), 0x44: (0, 0),   # NEG ABS B_NOT (unary)
    0x50: (2, +1),   # RAND (u16 bound baked in -> pushes the result)
    0x51: (0, -1),   # ATAN2 (binary: pops y, x -> pushes the angle)
}


def _rpn_max_depth(rpn_bytes):
    """Peak expression-stack depth of an RPN token stream (no 0xFF terminator)."""
    depth = maxd = 0
    i, n = 0, len(rpn_bytes)
    while i < n:
        skip, delta = _RPN_STACK_EFFECT.get(rpn_bytes[i], (0, 0))
        i += 1 + skip
        depth += delta
        if depth > maxd:
            maxd = depth
    return maxd


def _op_size(name):
    _, operands = OPS[name]
    return 1 + sum(2 if w == "u16" else 1 for w in operands)


# --------------------------------------------------------------------------
# Blob decoding: which opcodes / RPN tokens does a compiled program contain?
#
# A blob is a PURE instruction stream (the compiler lays scripts out
# back-to-back, advancing by each instruction's own size, and interns strings
# separately), so it decodes linearly with no entry-point walk. The build uses
# the result to fold `lib/vm/core.mos`'s dispatch down to the arms a given game
# can actually reach. Anything unexpected returns None, and the caller then
# compiles with no defines -- i.e. every arm kept, byte-identical.
# --------------------------------------------------------------------------
_OP_BY_BYTE = {code: name for name, (code, _w) in OPS.items()}
_RPN_BY_BYTE = {v: k for k, v in RPN.items()}
# Operand bytes carried by an RPN token (mirrors fmt._disasm_rpn).
_RPN_OPERAND = {RPN["PUSH"]: 2, RPN["RAND"]: 2, RPN["VAR"]: 1,
                RPN["ACTOR_X"]: 1, RPN["ACTOR_Y"]: 1, RPN["ACTOR_HP"]: 1,
                RPN["ACTOR_MOVING"]: 1,
                RPN["GET_STATE"]: 1, RPN["ARG"]: 1}


def iter_instructions(blob):
    """Walk a compiled bytecode blob linearly, one record per instruction.

    Yields ``(off, name, size, rpn)``: the instruction's byte offset, its
    mnemonic, its total size in bytes (so ``blob[off:off + size]`` is the whole
    instruction), and -- for an ``RPN`` instruction -- its token stream as
    ``[(token_name, operand or None), ...]``; every other opcode yields ``[]``.

    This is the ONE stepping implementation (spec 15: one ISA table, and the
    decoder/disassembler generated from it). `decode_blob` (dispatch pruning),
    `fmt.disasm` (the listing) and the assembler's heap scan all walk with it,
    so an operand width can never be right in one and wrong in another.

    Raises ``VmError`` on a stream that does not decode cleanly (an unknown
    opcode or token, or an operand running off the end). Callers that must not
    fail catch it and fall back to "assume everything is used".
    """
    blob = bytes(blob)
    i, n = 0, len(blob)
    while i < n:
        off = i
        name = _OP_BY_BYTE.get(blob[i])
        if name is None:
            raise VmError("offset %d: unknown opcode 0x%02X" % (i, blob[i]))
        i += 1
        rpn = []
        if name == "RPN":
            while True:
                if i >= n:
                    raise VmError("offset %d: RPN stream runs off the end" % off)
                tk = blob[i]
                i += 1
                if tk == RPN["END"]:
                    break
                tname = _RPN_BY_BYTE.get(tk)
                if tname is None:
                    raise VmError("offset %d: unknown RPN token 0x%02X" % (off, tk))
                width = _RPN_OPERAND.get(tk, 0)
                if i + width > n:
                    raise VmError("offset %d: RPN %s operand runs off the end"
                                  % (off, tname))
                val = None
                if width == 1:
                    val = blob[i]
                elif width == 2:
                    val = blob[i] | (blob[i + 1] << 8)
                i += width
                rpn.append((tname, val))
        elif name == "MENU":
            # dest, row, count, id0..idN
            if i + 2 >= n:
                raise VmError("offset %d: MENU blob runs off the end" % off)
            i += 3 + blob[i + 2]
        elif name == "SWITCH":
            # u8 count, then count * (i16 value, u16 target)
            if i >= n:
                raise VmError("offset %d: SWITCH table runs off the end" % off)
            i += 1 + blob[i] * 4
        else:
            i += _op_size(name) - 1
        if i > n:
            raise VmError("offset %d: %s operands run off the end" % (off, name))
        yield off, name, i - off, rpn


def decode_blob(blob):
    """Walk a compiled bytecode blob linearly.

    Returns ``(opcode_names, rpn_token_names, state_ids)`` as sets, or
    ``(None, None, None)`` if the stream does not decode cleanly (an unknown
    opcode/token, or an operand running off the end). Callers MUST treat None as
    "assume everything is used" -- a mis-decode that silently dropped a live
    opcode would strand the interpreter on an instruction it no longer
    implements.

    ``state_ids`` is every engine-state id the blob READS (a ``GET_STATE`` token
    operand) or WRITES (a ``SET_STATE`` opcode operand). It is what lets the
    runtime fold away a state whose upkeep costs something per frame even when
    nobody reads it -- ``game_time``'s counter is the first such state.
    """
    blob = bytes(blob)
    ops, toks, states = set(), set(), set()
    try:
        for off, name, _size, rpn in iter_instructions(blob):
            ops.add(name)
            for t, v in rpn:
                toks.add(t)
                if t == "GET_STATE" and v is not None:
                    states.add(v)
            if name == "SET_STATE":
                states.add(blob[off + 1])
    except VmError:
        return None, None, None
    return ops, toks, states


class Label:
    """A symbolic u16 operand (a script name OR an intra-script label), resolved
    to a byte offset at layout."""
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return "Label(%r)" % self.name


class Anchor:
    """A zero-size marker: names the byte offset of the next instruction (an
    intra-script jump target, e.g. an if/else branch end). Emits nothing."""
    def __init__(self, name):
        self.name = name

    def size(self):
        return 0


# --------------------------------------------------------------------------
# Expression compiler: an infix mini-language ("gold + 5", "crystals >= 3 and
# has_key") -> an RPN token-byte stream vm.core's rpn_eval consumes. Variable
# names resolve to heap indices (project-wide); actor_x(n)/actor_y(n) are the
# engine-state reads. Precedence-climbing recursive descent.
# --------------------------------------------------------------------------
import re as _re

_TOKEN_RE = _re.compile(r"\s*(==|!=|<=|>=|<<|>>|[-+*/%()<>,&|^~]|\d+|[A-Za-z_]\w*)")


def _tokenize_expr(s):
    toks, i = [], 0
    while i < len(s):
        mt = _TOKEN_RE.match(s, i)
        if not mt:
            if s[i:].strip() == "":
                break
            raise VmError("bad expression char %r in %r" % (s[i], s))
        toks.append(mt.group(1))
        i = mt.end()
    return toks


def _rpn_bytes(*parts):
    out = bytearray()
    for p in parts:
        out.append(p)
    return out


def compile_expr(expr, cc):
    """Return the RPN token bytes (WITHOUT the 0xFF terminator) for `expr`."""
    toks = _tokenize_expr(str(expr))
    pos = [0]

    def peek():
        return toks[pos[0]] if pos[0] < len(toks) else None

    def nxt():
        t = toks[pos[0]]
        pos[0] += 1
        return t

    out = bytearray()

    def atom():
        t = peek()
        if t is None:
            raise VmError("unexpected end of expression %r" % expr)
        if t.isdigit():
            nxt()
            v = int(t) & 0xFFFF
            out.extend([RPN["PUSH"], v & 0xFF, (v >> 8) & 0xFF])
        elif t == "(":
            nxt()
            expr_prec(1)
            if peek() != ")":
                raise VmError("missing ) in %r" % expr)
            nxt()
        elif t[0].isalpha() or t[0] == "_":
            name = nxt()
            if name in ("and", "or", "not"):
                raise VmError("unexpected operator %r" % name)
            if peek() == "(":                       # function call
                nxt()
                if name in _STATE_FUNCS:            # engine-state read -> GET_STATE
                    if peek() != ")":
                        raise VmError("%s() takes no arguments" % name)
                    nxt()
                    out.extend([RPN["GET_STATE"], _STATE_FUNCS[name] & 0xFF])
                    return
                if name not in _FUNCS:
                    raise VmError("unknown function %r" % name)
                fn = _FUNCS[name]
                if fn in ("ACTOR_X", "ACTOR_Y", "ACTOR_HP", "ACTOR_MOVING"):  # one arg (literal id or self) -> token+u8
                    arg = nxt()
                    if arg == "self":               # SELF operand in an expression:
                        aid = SELF_ACTOR             # the runtime resolves it to the
                    elif arg.isdigit():             # thread's bound actor (vm_self)
                        aid = int(arg) & 0xFF
                    else:
                        raise VmError("%s expects a literal actor id or self" % name)
                    if peek() != ")":
                        raise VmError("missing ) after %s(" % name)
                    nxt()
                    out.extend([RPN[fn], aid])
                elif fn in ("PLAYER_X", "PLAYER_Y", "SELF_SLOT"):  # no args -> token only
                    if peek() != ")":
                        raise VmError("%s() takes no arguments" % name)
                    nxt()
                    out.append(RPN[fn])
                elif fn == "RAND":                  # one numeric arg -> token+u16
                    arg = nxt()
                    if not arg.isdigit():
                        raise VmError("rand expects a literal bound")
                    if peek() != ")":
                        raise VmError("missing ) after rand(")
                    nxt()
                    v = int(arg) & 0xFFFF
                    out.extend([RPN[fn], v & 0xFF, (v >> 8) & 0xFF])
                elif fn == "ARG":                   # thread argument -> token+u8
                    arg = nxt()
                    if not arg.isdigit() or int(arg) >= NARGS:
                        raise VmError("arg expects a literal index 0..%d" % (NARGS - 1))
                    if peek() != ")":
                        raise VmError("missing ) after arg(")
                    nxt()
                    out.extend([RPN[fn], int(arg) & 0xFF])
                else:                               # min/max/abs -> arg exprs + op
                    expr_prec(1)
                    n = 1
                    while peek() == ",":
                        nxt()
                        expr_prec(1)
                        n += 1
                    if peek() != ")":
                        raise VmError("missing ) after %s(" % name)
                    nxt()
                    if fn == "ABS" and n != 1 or fn in ("MIN", "MAX") and n != 2:
                        raise VmError("%s arity" % name)
                    out.append(RPN[fn])
            else:                                   # variable -> heap index
                out.extend([RPN["VAR"], cc.var_index(name) & 0xFF])
        else:
            raise VmError("unexpected token %r in %r" % (t, expr))

    def unary():
        t = peek()
        if t == "not":
            nxt()
            unary()
            out.append(RPN["NOT"])
        elif t == "-":
            nxt()
            unary()
            out.append(RPN["NEG"])
        elif t == "~":
            nxt()
            unary()
            out.append(RPN["B_NOT"])
        else:
            atom()

    def expr_prec(minprec):
        unary()
        while True:
            t = peek()
            if t not in _BINOPS:
                break
            prec, tok = _BINOPS[t]
            if prec < minprec:
                break
            nxt()
            expr_prec(prec + 1)     # left-associative
            out.append(RPN[tok])

    expr_prec(1)
    if pos[0] != len(toks):
        raise VmError("trailing tokens in expression %r" % expr)
    depth = _rpn_max_depth(out)
    if depth > MIN_VM_STACK:
        raise VmError(
            "expression %r nests %d values deep, past the uniform %d-slot "
            "per-thread expression stack (VM8 spec §14; it would silently "
            "evaluate wrong on console); split it with a temporary variable"
            % (expr, depth, MIN_VM_STACK))
    return bytes(out)


class Instr:
    def __init__(self, mnem, operands):
        if mnem not in OPS:
            raise VmError("unknown opcode %r" % mnem)
        self.mnem = mnem
        self.operands = list(operands)

    def size(self):
        if self.mnem == "SWITCH":
            # op + u8 count + count*(i16 val, u16 target)
            return 2 + int(self.operands[0]) * 4
        if self.mnem in _VARLEN:
            return 1 + len(self.operands[0])
        return _op_size(self.mnem)

    def encode(self, offsets):
        code, widths = OPS[self.mnem]
        if self.mnem == "SWITCH":
            # operands = [count, val0, Label0, val1, Label1, ...]
            out = bytearray([code, int(self.operands[0]) & 0xFF])
            rest = self.operands[1:]
            for k in range(0, len(rest), 2):
                val = int(rest[k]) & 0xFFFF
                tgt = rest[k + 1]
                if isinstance(tgt, Label):
                    if tgt.name not in offsets:
                        raise VmError("switch target %r is not a label" % tgt.name)
                    tgt = offsets[tgt.name]
                tgt = int(tgt)
                out.append(val & 0xFF)
                out.append((val >> 8) & 0xFF)
                out.append(tgt & 0xFF)
                out.append((tgt >> 8) & 0xFF)
            return bytes(out)
        if self.mnem in _VARLEN:
            return bytes([code]) + bytes(self.operands[0])
        if len(self.operands) != len(widths):
            raise VmError("%s expects %d operands, got %d"
                          % (self.mnem, len(widths), len(self.operands)))
        out = bytearray([code])
        for value, width in zip(self.operands, widths):
            if isinstance(value, Label):
                if value.name not in offsets:
                    raise VmError("jump/thread target %r is not a script" % value.name)
                value = offsets[value.name]
            value = int(value)
            if width == "u16":
                out.append(value & 0xFF)
                out.append((value >> 8) & 0xFF)
            else:
                out.append(value & 0xFF)
        return bytes(out)
# The "self" actor operand (the reference engine's SELF): an actor field of "self" -> the
# SELF_ACTOR sentinel byte (0xFE, lockstep with vm.core), which the runtime
# resolves to the executing thread's BOUND actor (a per-instance slot script's own
# actor). A numeric actor id passes through unchanged.
SELF_ACTOR = 0xFE

# The PLAYER as an actor operand (0xFF). The reference engine keeps the player at its
# actors[0], so any actor event may aim at it; our pool has no slot for it, so
# the ops that CAN answer for the player take this sentinel instead. Only
# `A_EMOTE` reads it today - everything else that needed the player turned out
# to be an engine STATE (player_dir / player_collide / player_speed /
# player_anim_speed) or an op that already existed (player_visible), which is
# the order of preference to keep. An op that does NOT handle it must go on
# resolving through resolve_actor, where 0xFF clamps to actor 0.
PLAYER_ACTOR = 0xFF

# How many actor slots the RUNTIME this blob will be linked against actually
# has -- `[build] actor_pool`, default 8 (lib/vm/actor.mos ACTORS). A literal
# actor id in an event is validated against it, because the runtime indexes its
# arrays with that byte and an out-of-range id would be an OOB write on
# console. It is module state rather than a parameter because the id check sits
# deep in event lowering, far from any project context; `loader.load_scripts`
# sets it from the project being compiled, so the toolchain and the compiled
# arrays cannot disagree. Capped below SELF_ACTOR, which is not an index.
ACTOR_POOL = 8


def set_actor_pool(n):
    """Set the actor-slot cap literal ids are validated against (see ACTOR_POOL)."""
    global ACTOR_POOL
    ACTOR_POOL = max(1, min(int(n), SELF_ACTOR - 1))
    return ACTOR_POOL
