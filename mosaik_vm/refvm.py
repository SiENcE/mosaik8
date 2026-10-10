"""mosaik_vm.refvm - RefVM: the Python reference interpreter (headless semantics tests)."""

from .isa import (BUTTONS, OVERLAY_CUT_OFF, PLAYER_ACTOR, SAVE_SLOTS,
                  SELF_ACTOR, ST_HELD_BASE, ST_UI_OPEN)


# --------------------------------------------------------------------------
# RefVM -- the Python reference interpreter for the VM8 machine
# (docs/vm8-spec.md; mirrors lib/vm/core.mos exactly, so event semantics are
# unit-tested headless, no ROM). Positions/heap/text are the observable state;
# drive it with frame().
#
# Fidelity rules (spec §17 conformance):
#   - the expression stack is BOUNDED at VM_STACK=8 (a push at capacity drops,
#     exactly like the console vpush) and the call stack at CALL_DEPTH=4 (a
#     CALL at depth skips entirely) -- content that misbehaves on console
#     misbehaves here too;
#   - exceptions (RAISE) are serviced at frame step 4, AFTER run_scripts, never
#     inline, so sibling threads observe scene state exactly as on console;
#   - the PRNG is host-injectable (`vm.random = callable`) so a test / ROM
#     harness can share one source.
# --------------------------------------------------------------------------
class RefActor:
    __slots__ = ("active", "x", "y", "tile", "speed", "moving", "tx", "ty", "clip",
                 "hclip", "group", "hp", "dir", "solid", "frame_pin",
                 "anim_speed", "updating", "box", "mopt", "visible")

    def __init__(self):
        self.active = 0
        self.x = self.y = self.tile = 0
        self.speed = 1
        self.moving = 0
        self.tx = self.ty = 0
        self.clip = 255
        # The clip a retired actor HAD, so A_REACTIVATE can put it back
        # (lockstep vm.actor's a_hclip).
        self.hclip = 255
        self.group = 2                # GROUP_ENEMY (default, lockstep vm.actor)
        self.hp = 1                   # per-actor HP (multi-HP foes)
        self.dir = 0                  # facing (0 down/1 up/2 left/3 right)
        self.solid = 1                # ACTOR_FLAG_COLLISION: blocks the player
        # The collision BOX (w, h, ox, oy), the reference engine's authored sprite
        # `bounds` -- what a projectile hits, what a push probes, what blocks
        # the player. It is not an opcode: the generated rooms.mos calls
        # vm.actor.set_box at room load, so a HOST sets it here to model a
        # world with big actors. (8, 8, 0, 0) is the runtime's own fallback for
        # a room that registers none.
        self.box = (8, 8, 0, 0)
        # Script-driven animation, mirroring vm.actor's a_fpin / a_aspd: the
        # pinned frame is stored +1 so 0 means "animate normally", and the
        # speed is a PERIOD (0 = the clip's own), converted from the reference engine's
        # mask by the op.
        self.frame_pin = 0
        self.anim_speed = 0
        # The move options the move in flight carries (A_MOVE_OPTS): bits 0-1
        # the axis order, bit 2 stop at walls, bit 3 stop at actors.
        self.mopt = 0
        # the reference engine's ACTOR_FLAG_HIDDEN (A_VISIBLE): 0 = live but not DRAWN.
        # Its runtime tests the flag in `actors_render` and nowhere else, so a
        # hidden actor still moves, still collides and still answers the
        # interact probe - which is what makes an invisible interaction hotspot
        # work. Orthogonal to `active` (its ACTOR_FLAG_DISABLED): reactivating
        # does not un-hide, exactly as `vm_actor_activate` does not clear
        # HIDDEN.
        self.visible = 1
        # 1 while the actor's On Update thread is meant to be running.
        # A_STOP_UPDATE clears it; the console's vm.entity clears its stored
        # thread handle at the same moment, so this is what a test asserts on.
        self.updating = 1


class RefVM:
    ST_CONT, ST_YIELD, ST_END = 0, 1, 2
    EMOTE_FRAMES = 60                 # lockstep with vm.emote.TOTAL
    VM_STACK = 8                      # uniform per-thread expr cells (spec §14)
    CALL_DEPTH = 4                    # uniform per-thread return addresses
    NARGS = 4                         # thread arguments (THREADN)
    #: the reference engine's `ui_time_masks`: frames between the overlay curtain's 1-row
    #: steps, indexed by `speed`. Lockstep with vm.fx's own MASK table.
    _UI_TIME_MASKS = (0, 0, 1, 3, 7, 15, 31, 63)

    def __init__(self, code, entry=0, ctxs=8, quant=16, heap=128, actors=8,
                 proj_under_lock=False):
        self.code = bytes(code)
        # `[build] proj_under_lock`: the reference VM runs
        # projectiles_update() OUTSIDE its !VM_ISLOCKED() block, so a shot
        # in flight keeps flying through a cutscene there. Mirrored here
        # because the five-in-lockstep rule includes this file - a
        # divergence between RefVM and vm.core is a silent wrong answer in
        # every headless test. Default False = today's frozen flight.
        self.proj_under_lock = bool(proj_under_lock)
        self.entry = entry            # boot entry (RESET re-activates ctx 0 here)
        self.NC = ctxs
        self.QUANT = quant
        self.active = [0] * ctxs
        self.pc = [0] * ctxs
        self.waiting = [0] * ctxs
        # PLAYER_MOVE_TO_E's latched target (one pair - there is one player)
        self.pmt_x = 0
        self.pmt_y = 0
        self.scratch = [0] * ctxs
        self.lockcount = 0
        self.lockowner = 0
        self.cur = 0
        # 1 while _run_context has a slice live for `cur` (core parity: the
        # native interpreter keeps that thread's pc/sp in registers, so the
        # pool must never hand `cur` out again inside the slice -- a self-kill
        # followed by a spawn would otherwise land the child in the dying
        # thread's context and lose it to the register write-back)
        self.slice_live = 0
        self.heap = [0] * heap
        self.stack = [[] for _ in range(ctxs)]     # per-thread expression stack
        self.actors = [RefActor() for _ in range(actors)]
        self.box_open = 0
        self.box_hold = 0         # ST_BOX_HOLD: the one-shot timed-box latch
        self.box_hold_live = 0    # ... and the OPEN box's countdown of it
        self.fade_style = 1       # ST_FADE_STYLE: 0 = towards white, 1 = black
        self.sprites_hidden = 0   # ST_SPRITES_HIDDEN: 1 = every sprite hidden
        self.scene_update_paused = 0  # ST_SCENE_UPDATE_PAUSED (cleared per scene)
        # ST_OVERLAY_CUT: the scanline the window overlay stops at (W7d phase
        # 2). Headless there is no window and no LYC, so this is a value the
        # host reads back rather than a behaviour - the console engine's
        # `text.win_overlay_cut` is what acts on it. OVERLAY_CUT_OFF (150) is
        # the reference VM's own default and is off-screen, i.e. no cut.
        self.overlay_cut = OVERLAY_CUT_OFF
        self.update_starts = []   # A_START_UPDATE requests, in order (actor slots)
        #                           (the reference engine's numbering; the host default
        #                           is black, a rooms.mos default may flip it)
        self.ui_owner = 255       # the context owning the open box/menu (255 = free);
        #                           a 2nd thread's UI_TEXT/MENU yields until free
        self.prev_a = 0
        self.text_id = None       # the string id currently shown
        self.text_log = []        # every string id opened, in order
        self.change_log = []      # every scene change (room, x, y) serviced, in order
        self.reset_log = []       # every serviced RESET (RAISE 1), in order
        self.load_log = []        # every serviced LOAD_COMPLETE (RAISE 3), in order
        # battery-SRAM snapshots (vm.sram), one per SLOT - the reference engine's three
        # (W7c). None = that slot holds no save. `save_slot` is the one-shot
        # LATCH state: every save / load / clear / peek / save_exists consumes
        # it, so the default is always slot 0.
        self.saved = [None] * SAVE_SLOTS
        self.save_slot = 0
        self.sound_log = []       # every sound op raised: ("sfx", id) / ("tone", freq, frames) / ("stop",) / ("music_play", entry) / ("music_stop",)
        self.exempt_ctx = 255     # context allowed to run under the lock (the
        #                           tracked song thread; spec §3 exempt_ctx)
        self.menu_log = []        # every menu selection (chosen index), in order
        # current-frame inputs + menu cursor state (engine.menu mirror)
        self.cur_a = self.cur_up = self.cur_down = 0
        self.cur_b = 0
        # B-cancel (state 30, ST_MENU_CANCEL): the one-shot latch a script arms
        # before a MENU, the open menu's copy of it, and B's held-last latch.
        self.menu_cancel = 0
        self.menu_cancel_live = 0
        self.m_prevb = 1
        self.m_cursor = 0
        self.m_prev = 0           # nav edge latch
        self.m_preva = 0          # confirm edge latch
        self.menu_open = 0
        self.menu_last = 255
        self.fade_level = 0
        self.hud_show = 1         # HUD visibility (HUD_SHOW op)
        self.player_x = 0         # engine-state (PLAYER_X/Y reads, PLAYER_SETPOS)
        self.player_y = 0
        # The player's collision box (w, h) -- vm.player's position IS its box
        # top-left, and a shot masking the player is tested against it. Set by
        # the shell's player.setup on the console, so a host sets it here.
        self.player_box = (8, 8)
        self.player_dir = 0       # player facing (ST_PLAYER_DIR, read AND written)
        # Pixels per frame a SCRIPTED walk (PLAYER_MOVE_TO) advances. On the
        # console this is vm.player's own `pspeed`, set by the shell's
        # player.setup; a host running the reference VM sets it to match.
        self.player_speed = 1
        # The CONSOLE answers is_color (ST_IS_COLOR); a host running the
        # reference VM sets it to say which machine it is standing in for.
        self.is_color = False
        # The last PLAYER_BOUNCE height requested (None = never). There is no
        # platform physics here to apply it to, so it is recorded, not simulated.
        self.player_bounce = None
        # How many times PLAYER_KNOCKBACK fired (no operand to record).
        self.player_knockback = 0
        # {actor: (state, play_once)} the last A_SET_ANIM_STATE pinned.
        self.actor_anim_state = {}
        # A free-running VM-FRAME counter (ST_GAME_TIME, the reference engine's sys_time),
        # and the last value written to ST_RAND_SEED. game_time wraps at 16 bits
        # exactly as the console's u16 does, so a rate limit's wrapped compare
        # behaves identically here.
        self.game_time = 0
        self.rand_seed = None
        # ST_PLAYER_COLLIDE: 1 = actors block the player (the engine default),
        # 0 = the player passes through them. No actor physics here either, so
        # it is recorded for a test to assert, like player_bounce above.
        self.player_collide = 1
        # ST_PLAYER_ANIM_SPEED (the reference engine's anim_tick mask). There is no
        # animator here, so it is recorded for a test to assert, like
        # player_bounce above; None = never written, which is what tells "the
        # script set it back to the default" from "it never set it at all".
        # (ST_PLAYER_SPEED needs no field of its own - it writes `player_speed`
        # above, which is already what a scripted PLAYER_MOVE_TO steps by.)
        self.player_anim_speed = None
        # The follow camera's CLAMP RECTANGLE in world pixels (ST_CAM_MIN_X ..
        # ST_CAM_MAX_Y = the reference engine's scroll_x_min / _max / scroll_y_min / _max).
        # None = unset, which is the console's `cam_bset == 0`: the room's own
        # bounds stand. There is no follow camera here to clamp, so like
        # player_collide these are recorded for a test to assert - what matters
        # for parity is that a write reaches the right one of the four.
        self.cam_bounds = [None, None, None, None]
        # the reference engine's platformer BLANK state (ST_PLAYER_BLANK) and the gravity
        # it falls under (ST_PLAYER_BLANK_GRAV = its `plat_blank_grav` engine
        # field). There is no platform handler here to suspend, so like
        # player_collide these are recorded for a test to assert - what matters
        # for parity is that the pair are separate ids and that the gravity
        # defaults to 0 (a blank player HANGS until a script drops it, which is
        # the whole timing of a pit death).
        self.player_blank = 0
        self.player_blank_grav = 0
        # The single live emote bubble (A_EMOTE), mirroring vm.emote's one-at-a-
        # time model: {"actor", "id", "timer"} or None. `emote_log` records every
        # one started, so a test can assert WHICH emote fired without simulating
        # OAM.
        self.emote = None
        self.emote_log = []
        # curated engine-state bridge (GET_STATE / SET_STATE, mirrors vm.core).
        self.cam_x = 0            # camera scroll (camera_x/y state)
        self.cam_y = 0
        self.cam_lock = 0         # 1 = something else owns the camera (a pin,
        #                           a pan, the generic scroll). NOT the state-4
        #                           value: that is the reference engine's camera_settings
        #                           byte below, and a pin wins over its bits.
        self.cam_set = 3          # camera_settings (state 4): bit 0 follow x,
        #                           bit 1 follow y, bits 2..5 preventScroll
        #                           left/right/up/down. camera_init's 0x03.
        self.cam_deadzone = [0, 0]   # x, y - per SCENE (the reference VM's camera_reset)
        self.cam_offset = [0, 0]     # x, y - GLOBAL (only camera_init clears)
        self.cur_scene = 0        # current room (scene state, set on RAISE 2/3 service)
        self.shk_frames = 0       # screen shake frames remaining (SHAKE op)
        self.shk_amp = 0
        # The one-shot SHAKE_OPTS latch: the reference engine's CAMERA_SHAKE_X (1) /
        # _Y (2) mask and whether the op BLOCKS. 0 = unlatched = this engine's
        # historical vertical, non-waiting shake. There is no camera to jitter
        # here, so like player_collide these are recorded for a test to assert
        # - what matters for parity is the LATCH being one-shot and the wait
        # actually holding the thread.
        self.shk_axis = 0
        self.shk_wait = 0
        # Background tile REPLACEMENTS (BKG_TILE / _E, the reference engine's
        # VM_REPLACE_TILE): {destination tile index -> replacement tile index}.
        # There are no pixels here, so what a test asserts is the MAPPING - the
        # digit a wallet readout puts under each of its four cells.
        self.bkg_tiles = {}
        self.shmup_pace = 1       # shmup auto-scroll pace (SHMUP_SCROLL pack op; 0 = paused)
        self.shmup_dir = 0        # shmup scroll direction (0 h / 1 v)
        self.scroll_vx = 0        # generic background scroll velocity (SCROLL_BG op, 1/4-px/frame)
        self.scroll_vy = 0
        self.scroll_on = 0        # 1 = generic bg-scroll active
        self.call_stack = [[] for _ in range(ctxs)]   # per-thread return addresses
        self.self_actor = [0] * ctxs   # per-thread bound actor (SELF); default slot 0
        self.gen = [0] * ctxs          # per-context spawn generation (kill_gen)
        self.args = [[0] * self.NARGS for _ in range(ctxs)]   # THREADN thread arguments
        self.handle = [255] * ctxs     # heap index mirroring this thread's liveness
        self.hnext = [255] * ctxs      # HANDLE_NEXT one-shot latch
        # pending exception (RAISE): code 0 = none; serviced at frame step 4
        self.pend_code = 0
        self.pend_a = self.pend_b = self.pend_c = 0
        # Host-injectable terrain probe: callable(x, y) -> bool, the reference
        # counterpart of the generated room's `solid_at`. Read by A_PUSH only;
        # None = nothing is solid.
        self.solid_at = None
        # The A_MOVE_OPTS one-shot latch, consumed by the next actor move.
        self.pend_mopt = 0
        self.random = None        # host-injectable PRNG: callable() -> u16 (spec §12.1);
        self._rng = 0x1234        # fallback deterministic LCG when none is injected
        self.NTIMERS = 4
        self.tmr_active = [0] * self.NTIMERS
        self.tmr_period = [0] * self.NTIMERS
        self.tmr_count = [0] * self.NTIMERS
        self.tmr_entry = [0] * self.NTIMERS
        # The busy gate (the reference engine's SCRIPT_TERMINATED check): the context each
        # timer / input slot last spawned, 255 = none. A tick / press while it
        # still runs fires nothing; released in _death_cleanup.
        self.tmr_ctx = [255] * self.NTIMERS
        self.NIN = 8              # one per portable button (lockstep with core.mos NIN)
        self.in_ctx = [255] * self.NIN
        self.in_active = [0] * self.NIN
        self.in_btn = [0] * self.NIN
        self.in_entry = [0] * self.NIN
        self.in_prev = [0] * self.NIN
        self.in_over = [0] * self.NIN
        #: Buttons an OVERRIDING attachment consumes (bit b). The native player
        #: handler must not see them - the reference engine clears the bit out of `joy` in
        #: events_update(), which runs before its state_update().
        self.consume_mask = 0
        # current-frame button state by BTN_* id (set in frame()); index = button id
        self.buttons = [0] * 8
        self.NPROJ = 8
        self.PLAYER_GROUP = 1     # collision groups (lockstep vm.projectile)
        self.MASK_DEFAULT = 0xFE
        self.proj = []            # active projectiles: {x,y,vx,vy,tile,life,mask}
        # the PROJ_ANIM one-shot latch: (frames, period) applied to the next
        # launch (lockstep vm.projectile.set_anim; None = static tile)
        self.proj_latch = None
        # PROJ_GROUP: the next shot's own group (one-shot, consumed by the
        # launch like the PROJ_ANIM latch; lockstep vm.projectile.l_group).
        # The op had NO handler here until 2026-09-06 - it fell through to
        # the unknown-opcode END and silently killed any thread that set a
        # projectile group (review V-11 found it by executing every opcode
        # with a semantic check, not only for a crash).
        self.proj_group = 0
        # PROJ_PAL: the next shot's sprite palette (one-shot, consumed by the
        # launch; lockstep vm.projectile.l_pal - an unlatched launch is 0)
        self.proj_pal = 0
        self.proj_hit = None      # on-hit hook (mirror vm.projectile.set_hit): called
        #                           with the actor slot a shot overlapped (spends it)
        self.proj_hits = []       # every (slot) a projectile struck, in order
        #: The TYPEWRITER reveal (TEXT_SPEED 0x3E / TEXT_BLIP 0x3F): GB
        #: Studio's text_draw_speed model. The RefVM has no frame renderer, so
        #: these are STATE a test asserts on - the console reveal itself is
        #: measured on the ROM. Defaults mirror vm.core's BSS: speed 0 =
        #: instant, blip off.
        self.text_speed = 0
        self.text_ff = 0
        self.blip_freq = 0
        self.blip_frames = 0
        #: PLAYER_VISIBLE: the reference engine's ACTOR_FLAG_HIDDEN on actors[0] - the
        #: DRAW stops and nothing else does. Cleared on scene load.
        self.player_hidden = 0
        self.player_hit_entry = None   # SET_PLAYER_HIT registration (spawned on a
        self.player_hit_log = []       # player-masked shot overlapping the player)
        # W7h - the hUGE `6xy` CALL-ROUTINE slots (the reference engine's music_events[4]).
        # `music_routine_entry[n]` is the attached script, None = unattached.
        # `music_routine_q` is the driver's 4-deep RING: a headless RefVM has no
        # driver, so a test (or a project's verify) pushes a parameter BYTE onto
        # it exactly as `hUGETrackerRoutine` would, and the drain below is the
        # engine's. Dropping the OLDEST on overflow is the reference's rule.
        self.music_routine_entry = [None] * 4
        self.music_routine_ctx = [255] * 4      # the busy gate (handle + gen)
        self.music_routine_gen = [0] * 4
        self.music_routine_q = []
        self.music_routine_log = []    # (slot, arg) pairs actually spawned
        # The window OVERLAY CURTAIN (the reference engine's ui.c): the row its top edge
        # sits on (0 = whole screen covered, 18 = gone) and how long since the
        # last row step. Mirrored here so a project's verify.py can assert the
        # reveal without a ROM, exactly as it asserts a fade.
        # PIXELS, like the reference engine's win_pos_y (only the DESTINATION is authored
        # in tile rows; ui_move_to multiplies by 8). 144 = off the bottom.
        self.curtain_y = 144
        self.curtain_tick = 0
        self.curtain_log = []          # every y the curtain came to rest on
        #: every runtime palette write, in order: (layer, slot, library index)
        #: with layer 0 = sprite / 1 = background. A host writes the hardware;
        #: the reference records, exactly as it does for a sound op.
        self.palette_log = []
        self.active[0] = 1
        self.pc[0] = entry

    # kept for older callers/tests: the exempt context IS the tracked song thread
    @property
    def music_ctx(self):
        return self.exempt_ctx

    @music_ctx.setter
    def music_ctx(self, v):
        self.exempt_ctx = v

    def _f8(self):
        # the fetch seam (spec R2): a u16 cursor; a read past the blob returns 0
        # (0 = STOP, so a runaway PC ends its thread instead of crashing)
        p = self.pc[self.cur] & 0xFFFF
        b = self.code[p] if p < len(self.code) else 0
        self.pc[self.cur] = (p + 1) & 0xFFFF
        return b

    def _f16(self):
        lo = self._f8()
        hi = self._f8()
        return (hi << 8) | lo

    #: Push direction by PLAYER facing (0 down / 1 up / 2 left / 3 right) --
    #: an actor is shoved AWAY from the player, so it travels the way the
    #: player is looking. Lockstep with vm.core's OP_A_PUSH table.
    _PUSH_DELTA = ((0, 1), (0, -1), (-1, 0), (1, 0))

    def _push_target(self, x, y, tiles):
        """Where a pushed actor comes to rest: up to `tiles` whole tiles along
        the player's facing, stopping BEFORE the first solid cell.

        Terrain is the host's answer (`vm.solid_at = callable(x, y) -> bool`,
        the reference-VM counterpart of the room's generated `solid_at`); with
        none injected nothing is solid and the actor slides the full distance.
        The probe is the two LEADING-EDGE corners of the 8x8 destination, the
        same rule the engine's own collision probes follow."""
        dx, dy = self._PUSH_DELTA[self.player_dir & 3]
        for _ in range(tiles):
            nx, ny = x + dx * 8, y + dy * 8
            if nx < 0 or ny < 0:
                break
            if self.solid_at is not None:
                cx = nx + (7 if dx > 0 else 0)
                cy = ny + (7 if dy > 0 else 0)
                ox, oy = (cx, cy + 7) if dx else (cx + 7, cy)
                if self.solid_at(cx, cy) or self.solid_at(ox, oy):
                    break
            x, y = nx, ny
        return x & 0xFFFF, y & 0xFFFF

    @staticmethod
    def _edge_span(span):
        """The offsets vm.actor's edge_solid probes across a leading edge: 0,
        then every 8 px, and ALWAYS the far corner (a span that is not a whole
        number of tiles would otherwise stop short)."""
        out = [0] + list(range(8, span - 1, 8))
        if span - 1 not in out:
            out.append(span - 1)
        return out

    def _actor_hit(self, i, x, y):
        """Would actor `i`'s box at (x, y) overlap the player or another solid
        actor? Mirrors vm.actor.actor_hit - the reference engine walks the same list, and
        it has the player at actors[0]."""
        a = self.actors[i]
        w, h, ox, oy = a.box
        ax, ay = x + ox, y + oy
        pw, ph = self.player_box
        if pw and ax < self.player_x + pw and self.player_x < ax + w \
                and ay < self.player_y + ph and self.player_y < ay + h:
            return True
        for j, b in enumerate(self.actors):
            if j == i or not b.active or not b.solid:
                continue
            jw, jh, jox, joy = b.box
            jx, jy = b.x + jox, b.y + joy
            if ax < jx + jw and jx < ax + w and ay < jy + jh and jy < ay + h:
                return True
        return False

    def _clip_walls(self, i, tx, ty, mode):
        """Clip a move's destination to just before the first solid tile, once,
        at move start - the reference engine's check_collision_horizontal/_vertical. A host
        models the room by setting `solid_at`; unset, nothing is solid."""
        probe = self.solid_at
        if probe is None:
            return tx, ty
        a = self.actors[i]
        w, h, ox, oy = a.box

        # On a hit the clip snaps to the blocking TILE's edge, not to the last
        # 8-px probe: the sweep starts wherever the actor stands, so probing
        # alone leaves it short of a wall it should touch (and the per-frame
        # re-clip would creep instead of settling).
        def clip_x(fx, ay, target):
            x = fx
            while x != target:
                fwd = target > x
                if not fwd and x < 8:
                    return x
                nx = min(x + 8, target) if fwd else max(x - 8, target)
                edge = (nx + ox + w - 1) if fwd else (nx + ox)
                if any(probe(edge, ay + oy + o) for o in self._edge_span(h)):
                    stop = ((edge >> 3) << 3) - ox - w if fwd \
                        else ((edge >> 3) << 3) + 8 - ox
                    return x if (stop < x if fwd else stop > x) else stop
                x = nx
            return target

        def clip_y(ax, fy, target):
            y = fy
            while y != target:
                fwd = target > y
                if not fwd and y < 8:
                    return y
                ny = min(y + 8, target) if fwd else max(y - 8, target)
                edge = (ny + oy + h - 1) if fwd else (ny + oy)
                if any(probe(ax + ox + o, edge) for o in self._edge_span(w)):
                    stop = ((edge >> 3) << 3) - oy - h if fwd \
                        else ((edge >> 3) << 3) + 8 - oy
                    return y if (stop < y if fwd else stop > y) else stop
                y = ny
            return target

        if mode == 1:
            tx = clip_x(a.x, a.y, tx)
            ty = clip_y(tx, a.y, ty)
        else:
            ty = clip_y(a.x, a.y, ty)
            tx = clip_x(a.x, ty, tx)
        return tx, ty

    def _step_once(self, i, tx, ty, opt):
        """One frame of an actor's move toward (tx, ty) under `opt`; True when
        the move is OVER (arrived, or aborted on an actor). Mirrors
        vm.actor.step_once - target and options are PARAMETERS because the
        blocking op must stay stateless (two threads can have a move in flight
        on the same actor)."""
        a = self.actors[i]
        sp, x, y = a.speed, a.x, a.y
        mode = opt & 3
        movex = not (mode == 2 and y != ty)
        movey = not (mode == 1 and x != tx)
        if movex:
            if x < tx:
                x = tx if tx - x < sp else x + sp
            elif x > tx:
                x = tx if x - tx < sp else x - sp
        if movey:
            if y < ty:
                y = ty if ty - y < sp else y + sp
            elif y > ty:
                y = ty if y - ty < sp else y - sp
        if opt & 8 and self._actor_hit(i, x, y):
            return True                     # blocked: the move ends, in place
        a.x, a.y = x, y
        return x == tx and y == ty

    def _move_start(self, i, tx, ty):
        a = self.actors[i]
        opt, self.pend_mopt = self.pend_mopt, 0     # one-shot latch
        a.mopt = opt
        if opt & 4:
            tx, ty = self._clip_walls(i, tx, ty, opt & 3)
        a.tx, a.ty, a.moving = tx, ty, 1

    def _step_to(self, i, tx, ty):
        """The BLOCKING move op: STATELESS, re-supplied every frame, so two
        threads can drive the same actor. Walls are re-clipped from the current
        position each frame and a clip that cannot move the actor at all ends
        the move (it is against the wall)."""
        a = self.actors[i]
        # CAPTURE the latch on the frame it is set and keep it for this move -
        # re-reading the global every frame lets ANOTHER thread's move consume
        # it mid-flight, which turned an authored vertical-first leg diagonal.
        if self.pend_mopt:
            a.mopt, self.pend_mopt = self.pend_mopt, 0
        opt = a.mopt
        if opt & 4:
            tx, ty = self._clip_walls(i, tx, ty, opt & 3)
            if tx == a.x and ty == a.y:
                a.mopt = 0
                return True
        done = self._step_once(i, tx, ty, opt)
        if done:
            a.mopt = 0                      # released with the move
        return done

    def _player_step_to(self, tx, ty, mode):
        """One frame of the scripted player walk -- mirrors vm.player.step_to.

        `mode` is the reference engine's moveType axis order (0 diagonal / 1 horizontal
        first / 2 vertical first). Collision is not consulted, as its own
        move-to defaults `useCollisions` off. The step CLAMPS to the target so
        a speed that does not divide the distance still lands exactly -
        otherwise the caller's rewind never sees `done`.
        """
        sp = max(1, int(self.player_speed))
        x, y = self.player_x, self.player_y
        movex, movey = True, True
        if mode == 1:                       # horizontal first: hold Y until X lands
            movey = (x == tx)
        elif mode == 2:                     # vertical first
            movex = (y == ty)
        if movex:
            if x < tx:
                x = tx if tx - x <= sp else x + sp
                self.player_dir = 3
            elif x > tx:
                x = tx if x - tx <= sp else x - sp
                self.player_dir = 2
        if movey:
            if y < ty:
                y = ty if ty - y <= sp else y + sp
                self.player_dir = 0
            elif y > ty:
                y = ty if y - ty <= sp else y - sp
                self.player_dir = 1
        self.player_x, self.player_y = x, y
        return x == tx and y == ty

    @staticmethod
    def _s16(v):
        return ((int(v) + 0x8000) & 0xFFFF) - 0x8000     # i16 wraparound (mirror C)

    @staticmethod
    def _approach(cur, target, step):                    # mirror engine.camera.approach
        if cur < target:
            return target if target - cur < step else cur + step
        return target if cur - target < step else cur - step

    def _get_state(self, sid):
        if sid == 0:
            return self.player_x
        if sid == 1:
            return self.player_y
        if sid == 2:
            return self.cam_x
        if sid == 3:
            return self.cam_y
        if sid == 4:
            # While something else owns the camera NEITHER axis follows, so the
            # two LOCK bits read 0 and the preventScroll bits survive - the
            # console's `player.cam_settings()` exactly.
            return (self.cam_set & 0x3C) if self.cam_lock else self.cam_set
        if sid == 5:
            return self.cur_scene
        if sid == 6:                                # save_exists
            return 1 if self._take_slot_save() is not None else 0
        if sid == 7:                                # player_dir (facing)
            return self.player_dir
        if sid == 8:                                # is_color (the CONSOLE answers)
            return 1 if self.is_color else 0
        if sid == 10:                               # game_time (free-running VM frames)
            return self._s16(self.game_time)
        # 16+ are pack-defined (spec 5.3): held(btn) and "a box/menu is up".
        if ST_HELD_BASE <= sid < ST_UI_OPEN:
            return self.buttons[sid - ST_HELD_BASE]
        if sid == ST_UI_OPEN:
            return 1 if (self.box_open or self.menu_open) else 0
        if sid == 29:                               # pad_held: bit = button id
            m = 0
            for b in range(8):
                if self.buttons[b]:
                    m |= 1 << b
            return m
        return 0

    def _set_state(self, sid, v):
        if sid == 0:
            self.player_x = v
        elif sid == 1:
            self.player_y = v
        elif sid == 2:                        # writing camera x/y pins (locks) it
            self.cam_x = v
            self.cam_lock = 1
        elif sid == 3:
            self.cam_y = v
            self.cam_lock = 1
        elif sid == 4:                        # camera_settings, the reference engine's byte
            self.cam_set = v & 0xFF
            # Following NEITHER axis IS the full pin, which is what cam_hold
            # does on the console; anything else hands the camera back.
            self.cam_lock = 0 if (v & 3) else 1
        elif sid == 7:
            self.player_dir = v & 3
        elif sid == 9:                        # rand_seed: reseed the PRNG stream
            self.rand_seed = v & 0xFFFF
            self._rng = (v & 0xFFFF) or 0x1234
        elif sid == 11:                       # player_collide: do actors block?
            self.player_collide = 1 if v else 0
        elif sid == 12:                       # player_speed: px per VM frame
            self.player_speed = max(1, v & 0xFF)
        elif sid == 13:                       # player_anim_speed: the reference engine's mask
            self.player_anim_speed = v & 0xFF
        elif sid == 14:                       # player_blank: the pit/death state
            self.player_blank = 1 if v else 0
        elif sid == 15:                       # player_blank_grav: its own gravity
            self.player_blank_grav = v & 0xFF
        elif sid == 30:                       # menu_cancel: arm the NEXT menu's B
            self.menu_cancel = 1 if v else 0
        elif sid == 31:                       # box_hold: arm the NEXT box's timer
            self.box_hold = v & 0xFF
        elif sid == 32:                       # fade_style: 0 white / 1 black, persistent
            self.fade_style = 0 if v == 0 else 1
        elif sid == 33:                       # timer_reset: restart a countdown
            if 0 <= v < self.NTIMERS:         # (a stopped timer stays stopped)
                self.tmr_count[v] = self.tmr_period[v]
        elif sid == 34:                       # sprites_hidden: persistent
            self.sprites_hidden = 1 if v else 0
        elif sid == 35:                       # scene_update_paused
            self.scene_update_paused = 1 if v else 0
        elif sid == 41:                       # overlay_cut: persistent scanline
            self.overlay_cut = v & 0xFF
        elif 25 <= sid <= 28:                 # the camera clamp rectangle
            self.cam_bounds[sid - 25] = v & 0xFFFF
        elif sid == 40:                       # save_slot: the one-shot latch
            self.save_slot = v if 0 <= v < SAVE_SLOTS else 0
        elif 36 <= sid <= 39:                 # the four camera properties
            if sid < 38:                      # dead zone: the reference engine's 0..40
                self.cam_deadzone[sid - 36] = max(0, min(40, v))
            else:                             # follow offset: its signed byte
                self.cam_offset[sid - 38] = self._s16(v)
        # sid 5 (scene) is read-only here (RAISE 2 is the write path);
        # sid 8 (is_color) is the console's answer, set by the host;
        # sid 10 (game_time) is the engine's own counter

    def _slot(self):
        """CONSUME the save-slot latch and answer which slot it named.

        Every save operation calls this exactly once, so the latch is spent
        whether or not the slot held anything - the console engine's
        `consume()` does the same thing at the same moments."""
        n = self.save_slot
        self.save_slot = 0
        return n

    def _take_slot_save(self):
        """The snapshot in the latched slot (consuming the latch), or None."""
        return self.saved[self._slot()]

    def _push(self, v):
        # spec §13.3: a push at capacity is DROPPED (mirror the console vpush,
        # so a too-deep stream misbehaves identically headless and on ROM)
        st = self.stack[self.cur]
        if len(st) >= self.VM_STACK:
            return
        st.append(self._s16(v))

    def _pop(self):
        # Clamp an empty pop to 0 (mirrors vm.core.vpop's underflow guard);
        # a malformed/stale-state script must not crash the interpreter.
        if not self.stack[self.cur]:
            return 0
        return self.stack[self.cur].pop()

    # vm.trig.SIN_Q: sin over a quarter turn in 1/128 units. Reflected into
    # the other three quadrants by _sin128, exactly as trig.mos does.
    _SIN_Q = [
                0, 3, 6, 9, 13, 16, 19, 22, 25, 28, 31, 34, 37,
                40, 43, 46, 49, 52, 55, 58, 60, 63, 66, 68, 71, 74,
                76, 79, 81, 84, 86, 88, 91, 93, 95, 97, 99, 101, 103,
                105, 106, 108, 110, 111, 113, 114, 116, 117, 118, 119, 121, 122,
                122, 123, 124, 125, 126, 126, 127, 127, 127, 128, 128, 128, 128
    ]

    @classmethod
    def _sin128(cls, a):
        a &= 255
        if a <= 64:
            return cls._SIN_Q[a]
        if a <= 128:
            return cls._SIN_Q[128 - a]
        if a <= 192:
            return -cls._SIN_Q[a - 128]
        return -cls._SIN_Q[256 - a]

    # The same integer atan2 lib/vm/trig.mos computes, table and all, so the
    # reference answer is the CONSOLE's answer and not a float rounded
    # differently: reference-engine angle units (0 up, 64 right, 256 to the turn),
    # y in screen sense (growing down).
    _ATAN_O = [0, 1, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 15, 16, 17, 18, 19,
               20, 21, 22, 23, 24, 25, 25, 26, 27, 28, 29, 29, 30, 31, 31, 32]

    @classmethod
    def _atan2(cls, y, x):
        a, b = x, -y                      # right positive, UP positive
        na, nb = 1 if a < 0 else 0, 1 if b < 0 else 0
        a, b = abs(a), abs(b)
        while a > 1023 or b > 1023:       # keep a*32 inside i16, ratio intact
            a, b = a // 2, b // 2
        if a == 0 and b == 0:
            return 0
        if a <= b:                        # nearer the vertical
            inner = cls._ATAN_O[(a * 32) // b]
            if nb == 0:
                return inner if na == 0 else (-inner) & 255
            return (128 - inner) if na == 0 else (128 + inner) & 255
        inner = cls._ATAN_O[(b * 32) // a]
        if na == 0:
            return (64 - inner) if nb == 0 else (64 + inner)
        return (192 + inner) & 255 if nb == 0 else (192 - inner)

    def _rand16(self):
        if self.random is not None:
            return int(self.random()) & 0xFFFF
        self._rng = (self._rng * 1103515245 + 12345) & 0xFFFFFFFF
        return (self._rng >> 16) & 0xFFFF

    def _rpn(self):
        """Evaluate one RPN stream. Returns True when it ended (0xFF or an
        unknown token), False past the 255-token cap (spec §8.2, core parity):
        a stream with no terminator must not run until the u16 pc wraps."""
        ntk = 0
        while True:
            if ntk == 255:
                return False
            ntk += 1
            tk = self._f8()
            if tk == 0xFF:
                return True
            if tk == 0x01:                        # PUSH const (u16 bits -> i16)
                self._push(self._s16(self._f16()))
            elif tk == 0x02:                      # PUSH var (OOB idx -> 0, core parity)
                idx = self._f8()
                self._push(self.heap[idx] if idx < len(self.heap) else 0)
            elif tk == 0x03:                      # PUSH actor x (literal id or SELF)
                self._push(self.actors[self._resolve_actor(self._f8())].x)
            elif tk == 0x04:                      # PUSH actor y
                self._push(self.actors[self._resolve_actor(self._f8())].y)
            elif tk == 0x05:                      # PUSH player x
                self._push(self.player_x)
            elif tk == 0x06:                      # PUSH player y
                self._push(self.player_y)
            elif tk == 0x07:                      # GET_STATE (curated engine state)
                self._push(self._get_state(self._f8()))
            elif tk == 0x08:                      # PUSH actor HP (literal id or SELF)
                self._push(self.actors[self._resolve_actor(self._f8())].hp)
            elif tk == 0x09:                      # SELF_SLOT (the bound actor's slot)
                self._push(self.self_actor[self.cur])
            elif tk == 0x0A:                      # ARG n (THREADN thread argument)
                n = self._f8()
                self._push(self.args[self.cur][n] if n < self.NARGS else 0)
            elif tk == 0x0B:                      # ACTOR_MOVING (auto-move in flight?)
                self._push(self.actors[self._resolve_actor(self._f8())].moving)
            elif tk == 0x50:                      # RAND [0, mod)
                md = self._f16()
                self._push((self._rand16() % md) if md else 0)
            elif tk == 0x51:                      # ATAN2(y, x) -> 0..255
                x = self._pop()
                y = self._pop()
                self._push(self._atan2(y, x))
            elif tk in (0x10, 0x11, 0x12, 0x13, 0x14,
                        0x15, 0x16, 0x17, 0x18, 0x19,
                        0x20, 0x21, 0x22, 0x23, 0x24, 0x25,
                        0x30, 0x31, 0x40, 0x41):
                b = self._pop()
                a = self._pop()
                if tk == 0x10:
                    self._push(a + b)
                elif tk == 0x11:
                    self._push(a - b)
                elif tk == 0x12:
                    self._push(a * b)
                elif tk == 0x13:                  # / truncates toward zero (C)
                    self._push(0 if b == 0 else int(a / b))
                elif tk == 0x14:                  # % (C remainder)
                    self._push(0 if b == 0 else a - int(a / b) * b)
                elif tk == 0x15:                  # B_AND (bitwise, i16)
                    self._push(a & b)
                elif tk == 0x16:                  # B_OR
                    self._push(a | b)
                elif tk == 0x17:                  # B_XOR
                    self._push(a ^ b)
                elif tk == 0x18:                  # SHL (count masked 0..15)
                    self._push(a << (b & 15))
                elif tk == 0x19:                  # SHR: LOGICAL shift on the u16 bits
                    self._push((a & 0xFFFF) >> (b & 15))
                elif tk == 0x20:
                    self._push(1 if a == b else 0)
                elif tk == 0x21:
                    self._push(1 if a != b else 0)
                elif tk == 0x22:
                    self._push(1 if a < b else 0)
                elif tk == 0x23:
                    self._push(1 if a > b else 0)
                elif tk == 0x24:
                    self._push(1 if a <= b else 0)
                elif tk == 0x25:
                    self._push(1 if a >= b else 0)
                elif tk == 0x30:
                    self._push(1 if (a != 0 and b != 0) else 0)
                elif tk == 0x31:
                    self._push(1 if (a != 0 or b != 0) else 0)
                elif tk == 0x40:
                    self._push(a if a < b else b)
                elif tk == 0x41:
                    self._push(a if a > b else b)
            elif tk == 0x32:                      # not
                a = self._pop()
                self._push(1 if a == 0 else 0)
            elif tk == 0x42:                      # neg
                self._push(-self._pop())
            elif tk == 0x43:                      # abs
                a = self._pop()
                self._push(a if a >= 0 else -a)
            elif tk == 0x44:                      # b_not (bitwise complement)
                self._push(~self._pop())
            else:
                return True                       # unknown token: end evaluation (§13.5)

    def _alloc(self):
        for i in range(self.NC):
            if self.active[i] == 0 and not (self.slice_live and i == self.cur):
                return i
        return 255

    def _music_events_update(self):
        """Drain the hUGE call-routine queue, `vm.core.music_events_update`
        step for step (which is the reference VM's `music_events_update` step for step).

        A headless RefVM has no driver, so nothing fills `music_routine_q` on
        its own: a test pushes the effect's PARAMETER BYTE onto it exactly as
        `hUGETrackerRoutine` would, and everything after that is the engine's.

          * `d & 3` is the slot, `d >> 4` the argument (arg(0));
          * BUSY-GATED per slot: a spawn only when the previous instance ended;
          * an UNATTACHED slot ABORTS the drain (the reference VM's `return`, not
            `continue`) - the item is consumed and the rest waits a frame.
        """
        while self.music_routine_q:
            d = self.music_routine_q.pop(0) & 0xFF
            slot = d & 3
            entry = self.music_routine_entry[slot]
            if entry is None:
                return                  # the reference VM's `return`, deliberately
            ctx = self.music_routine_ctx[slot]
            alive = (ctx < len(self.active) and self.active[ctx] == 1
                     and self.gen[ctx] == self.music_routine_gen[slot])
            if alive:
                continue
            s = self._spawn(entry, argv=[d >> 4])
            if s != 255:
                self.music_routine_ctx[slot] = s
                self.music_routine_gen[slot] = self.gen[s]
                self.music_routine_log.append((slot, d >> 4))

    def _spawn(self, entry, argv=None, spawner=None):
        s = self._alloc()
        if s != 255:
            self.gen[s] = (self.gen[s] + 1) & 0xFF  # core parity: kill_gen's stamp
            self.pc[s] = entry
            self.active[s] = 1
            self.waiting[s] = 0
            self.scratch[s] = 0
            # Reset ALL per-context state (spec §8.3) so a script that died
            # mid-RPN / mid-`call` can't leak a stale value, return address,
            # self binding or handle into the next script reusing the context.
            self.stack[s] = []
            self.call_stack[s] = []
            self.self_actor[s] = 0
            self.handle[s] = 255
            self.args[s] = list(argv[:self.NARGS]) + [0] * (self.NARGS - len(argv[:self.NARGS])) if argv else [0] * self.NARGS
            # HANDLE_NEXT latch transfer: the spawner watches this child
            if spawner is not None and self.hnext[spawner] != 255:
                self.handle[s] = self.hnext[spawner]
                if self.handle[s] < len(self.heap):
                    self.heap[self.handle[s]] = 1
                self.hnext[spawner] = 255
        return s

    def _death_cleanup(self, c):
        """The ONE shared thread-death path (spec §11), run for every death --
        STOP, bare RET, RAISE, unknown opcode, external kill()."""
        if self.lockcount > 0 and c == self.lockowner:
            self.lockcount = 0
            self.lockowner = 0
        if c == self.exempt_ctx:
            self.exempt_ctx = 255
        if c == self.ui_owner:            # dies owning the box/menu -> release
            self.box_open = 0             # the UI latch (never sticks busy)
            self.menu_open = 0
            self.ui_owner = 255
        if self.handle[c] < len(self.heap):   # §11.4: the join cell reads 0 = done
            self.heap[self.handle[c]] = 0
        self.handle[c] = 255
        self.hnext[c] = 255               # a pending HANDLE_NEXT dies with it
        # release any timer / input busy gate waiting on this instance
        for i in range(self.NTIMERS):
            if self.tmr_ctx[i] == c:
                self.tmr_ctx[i] = 255
        for i in range(self.NIN):
            if self.in_ctx[i] == c:
                self.in_ctx[i] = 255

    def kill(self, ctx):
        """Terminate a thread by context handle (mirrors vm.core.kill); runs the
        shared §11 cleanup so an external teardown can't strand a lock, the
        exempt song thread, an owned UI, or a live join handle."""
        if ctx < self.NC and self.active[ctx]:
            self.active[ctx] = 0
            self._death_cleanup(ctx)

    def gen_of(self, ctx):
        """The spawn GENERATION of context `ctx` (mirrors vm.core.gen_of):
        captured after a spawn so kill_gen can prove the handle still names
        the same thread."""
        return self.gen[ctx] if ctx < self.NC else 0

    def kill_gen(self, ctx, gen):
        """kill(), but only while the context still holds the thread the
        caller spawned (mirrors vm.core.kill_gen) - a stale handle whose
        context the pool reused is a no-op, never a kill of the newcomer."""
        if ctx < self.NC and self.gen[ctx] == gen:
            self.kill(ctx)

    def set_self(self, ctx, actor):
        """Bind context `ctx` to `actor` -- the SELF operand in that thread's script
        then resolves to it (the native counterpart is vm.core.set_self, called by
        vm.entity; here it lets a test drive a self-bound slot script)."""
        if ctx < self.NC and actor < len(self.actors):
            self.self_actor[ctx] = actor

    def request_change(self, room, x, y):
        """Native scene-change request (spec §10) -- doors need no bytecode."""
        self.pend_code = 2
        self.pend_a, self.pend_b, self.pend_c = room & 0xFF, x & 0xFFFF, y & 0xFFFF

    def _resolve_actor(self, i):
        """SELF_ACTOR (0xFE) -> the executing thread's bound actor, else the
        literal actor index (mirrors vm.core.resolve_actor)."""
        if i == SELF_ACTOR:
            s = self.self_actor[self.cur]
            return s if s < len(self.actors) else 0
        return i if i < len(self.actors) else 0     # clamp an OOB literal (core parity)

    def _proj_add(self, d):
        """Claim a projectile slot, consuming the PROJ_ANIM latch (lockstep
        vm.projectile.launch16: the latch clears whether or not a slot was
        free, and an unarmed launch is a static tile)."""
        if len(self.proj) < self.NPROJ:
            if self.proj_latch is not None:
                d["frames"], d["period"], d["stride"] = self.proj_latch
                d["acnt"], d["fidx"], d["toff"] = d["period"], 0, 0
            d["group"] = self.proj_group
            d["palette"] = self.proj_pal
            self.proj.append(d)
        self.proj_latch = None
        self.proj_group = 0
        self.proj_pal = 0

    PROJ_SIZE = 8         # a shot's own box (lockstep vm.projectile)

    def _proj_overlaps(self, p, tx, ty, tw, th):
        """The shot's 8x8 against a target RECTANGLE (lockstep
        `vm.projectile.overlaps`): half-open AABB, not the old anchor-distance
        test, which was 8x8-vs-8x8 and left a 16x16 actor hittable only through
        the tile at its top-left corner."""
        s = self.PROJ_SIZE
        return (p["x"] + s > tx and tx + tw > p["x"]
                and p["y"] + s > ty and ty + th > p["y"])

    def _update_projectiles(self):
        survivors = []
        for p in self.proj:
            # Sub-pixel flight, exactly as vm.projectile does it: velocities are
            # SIXTEENTHS of a pixel per frame with a per-axis remainder, so an
            # angle launch is not quantised to whole-pixel slopes. A plain
            # vx/vy launch scaled by 16 moves identically to before.
            for ax, vk, fk in (("x", "vx16", "fx"), ("y", "vy16", "fy")):
                v = p.get(vk, p.get("v" + ax, 0) * 16)
                acc = p.get(fk, 0) + abs(v)
                p[ax] += (acc // 16) * (-1 if v < 0 else 1)
                p[fk] = acc % 16
            mask = p.get("mask", self.MASK_DEFAULT)
            spent = False
            # the PLAYER is a hit target when the shot masks its group
            if (self.player_hit_entry is not None and (mask & self.PLAYER_GROUP)
                    and self._proj_overlaps(p, self.player_x, self.player_y,
                                            self.player_box[0], self.player_box[1])):
                self.player_hit_log.append(self.player_hit_entry)
                self._spawn(self.player_hit_entry)
                spent = True
            # native actor collision (mirror vm.projectile's has_hit path): the FIRST
            # live actor whose GROUP the mask matches spends the shot + fires the hook.
            if not spent and self.proj_hit is not None:
                for i, a in enumerate(self.actors):
                    bw, bh, ox, oy = a.box
                    if (a.active == 1 and (a.group & mask)
                            and self._proj_overlaps(p, a.x + ox, a.y + oy, bw, bh)):
                        self.proj_hits.append(i)
                        self.proj_hit(i)
                        spent = True
                        break
            if spent:
                continue                         # the shot is spent
            if p["life"] > 0:
                p["life"] -= 1
                if p["life"] == 0:
                    continue                     # despawn on life expiry
            # flight animation: cycle fidx among `frames` every `period` frames
            # (toff = the tile offset actually drawn, fidx * stride)
            if p.get("frames", 0) > 1:
                p["acnt"] -= 1
                if p["acnt"] == 0:
                    p["acnt"] = p["period"]
                    p["fidx"] = (p["fidx"] + 1) % p["frames"]
                    p["toff"] = p["fidx"] * p["stride"]
            survivors.append(p)
        self.proj = survivors

    def _refresh_consume(self):
        mask = 0
        for i in range(self.NIN):
            if self.in_active[i] and self.in_over[i]:
                mask |= 1 << self.in_btn[i]
        self.consume_mask = mask

    def _tick_input_attach(self):
        for i in range(self.NIN):
            if self.in_active[i]:
                h = self.buttons[self.in_btn[i]]
                if h and self.in_prev[i] == 0 and self.in_ctx[i] == 255:
                    self.in_ctx[i] = self._spawn(self.in_entry[i])
                self.in_prev[i] = h

    def _tick_timers(self):
        for i in range(self.NTIMERS):
            if self.tmr_active[i]:
                if self.tmr_count[i] > 0:
                    self.tmr_count[i] -= 1
                if self.tmr_count[i] == 0:
                    self.tmr_count[i] = self.tmr_period[i]
                    if self.tmr_ctx[i] == 255:      # busy gate (the reference engine
                        self.tmr_ctx[i] = self._spawn(self.tmr_entry[i])  # timers_update)

    def _step(self, c):
        self.cur = c
        op = self._f8()
        if op == 0x00:                              # STOP (§11 cleanup runs in
            self.active[c] = 0                      # _run_context's ST_END path)
            return self.ST_END
        if op == 0x01:                              # JUMP
            self.pc[c] = self._f16()
            return self.ST_CONT
        if op == 0x02:                              # IDLE
            return self.ST_YIELD
        if op == 0x03:                              # WAIT (§9.1)
            frames = self._f8()
            if self.waiting[c] == 0:
                self.waiting[c] = 1
                self.scratch[c] = frames
            # test BEFORE decrementing: WAIT n yields on n frames (the reference VM's
            # wait_frames seeds n+1 - same timing, u8-safe this way)
            if self.scratch[c] > 0:
                self.scratch[c] -= 1
                self.pc[c] -= 2
                return self.ST_YIELD
            self.waiting[c] = 0
            return self.ST_CONT
        if op == 0x04:                              # LOCK
            self.lockcount += 1
            self.lockowner = c
            return self.ST_CONT
        if op == 0x05:                              # UNLOCK
            if self.lockcount > 0:
                self.lockcount -= 1
            return self.ST_CONT
        if op == 0x06:                              # THREAD (args zeroed)
            t = self._f16()
            self._spawn(t, spawner=c)               # ONE context-init path (clears
                                                    # the reused context's state too)
            return self.ST_CONT
        if op == 0x07:                              # THREADN (spawn with n args)
            t = self._f16()
            n = self._f8()
            argv = [0] * self.NARGS
            for k in range(n, 0, -1):               # last-pushed -> arg[n-1];
                v = self._pop()                     # extras past NARGS popped+dropped;
                if k <= self.NARGS:                 # pops happen even if the pool
                    argv[k - 1] = v                 # is full (spec §6 op 0x07)
            self._spawn(t, argv=argv, spawner=c)
            return self.ST_CONT
        if op == 0x08:                              # RPN
            if not self._rpn():                     # runaway token stream: END
                self.active[c] = 0
                return self.ST_END
            return self.ST_CONT
        if op == 0x09:                              # SET_CONST
            idx = self._f8()
            v = self._f16()
            if v >= 0x8000:
                v -= 0x10000                        # i16 heap cell
            if idx < len(self.heap):                # OOB write guard (core parity)
                self.heap[idx] = v
            return self.ST_CONT
        if op == 0x0A:                              # SET_VAR
            idx = self._f8()
            v = self._pop()                         # always pop, then guard the
            if idx < len(self.heap):                # write (core parity)
                self.heap[idx] = v
            return self.ST_CONT
        if op == 0x0B:                              # IF (branch if false)
            t = self._f16()
            if self._pop() == 0:
                self.pc[c] = t
            return self.ST_CONT
        if op == 0x0C:                              # SWITCH (jump table; read ALL
            v = self._pop()                         # entries so pc lands past it)
            n = self._f8()
            target = None
            for _ in range(n):
                cv = self._f16()
                ct = self._f16()
                if cv >= 0x8000:
                    cv -= 0x10000                   # i16 case value
                if target is None and cv == v:
                    target = ct
            if target is not None:
                self.pc[c] = target
            return self.ST_CONT
        if op == 0x0D:                              # CALL (push return addr)
            entry = self._f16()
            if len(self.call_stack[c]) < self.CALL_DEPTH:
                self.call_stack[c].append(self.pc[c])
                self.pc[c] = entry
            # else: skipped ENTIRELY -- no push, no jump (spec §13.4)
            return self.ST_CONT
        if op == 0x0E:                              # RET
            if self.call_stack[c]:
                self.pc[c] = self.call_stack[c].pop()
                return self.ST_CONT
            self.active[c] = 0
            return self.ST_END
        if op == 0x0F:                              # RAISE (spec §10): record the
            code = self._f8()                       # pending exception (last-writer-
            a = self._f8()                          # wins) and END this thread;
            b = self._f16()                         # frame step 4 services it
            d = self._f16()
            self.pend_code = code
            self.pend_a, self.pend_b, self.pend_c = a, b, d
            self.active[c] = 0
            return self.ST_END
        if op == 0x16:                              # CHANGE_SCENE_E: RAISE 2 with
            y = self._pop()                         # room/x/y off the stack
            x = self._pop()                         # (pushed room, x, y)
            room = self._pop()
            self.pend_code = 2
            self.pend_a, self.pend_b, self.pend_c = room, x, y
            self.active[c] = 0
            return self.ST_END
        if op == 0x10:                              # SET_STATE (curated engine state)
            sid = self._f8()
            self._set_state(sid, self._pop())
            return self.ST_CONT
        if op == 0x11:                              # PLAYER_SETPOS
            self.player_x, self.player_y = self._f16(), self._f16()
            return self.ST_CONT
        if op == 0x12:                              # PLAYER_SETPOS_E (pops x,y)
            y = self._pop()
            x = self._pop()
            self.player_x, self.player_y = x, y
            return self.ST_CONT
        if op == 0x13:                              # HANDLE (bind this thread's
            i = self._f8()                          # liveness to heap[i])
            if i < len(self.heap):
                self.handle[c] = i
                self.heap[i] = 1
            else:
                self.handle[c] = 255
            return self.ST_CONT
        if op == 0x14:                              # HANDLE_NEXT (one-shot latch)
            i = self._f8()
            self.hnext[c] = i if i < len(self.heap) else 255
            return self.ST_CONT
        if op == 0x15:                              # SELF (bind the thread's actor)
            a = self._f8()
            if a < len(self.actors):
                self.self_actor[c] = a
            return self.ST_CONT
        if op == 0x17:                              # PLAYER_BOUNCE (0 low/1 med/2 high)
            # The console scales the impulse off the scene's own jump strength;
            # the reference VM has no platform physics, so it records the
            # request for a verify.py to assert on.
            self.player_bounce = min(2, self._f8())
            return self.ST_CONT
        if op == 0x37:                              # A_SET_ANIM_STATE
            # The console pins the actor's clip state; the reference VM has no
            # animation model, so it records the last request per actor.
            a = self._resolve_actor(self._f8())
            v = self._f8()
            # bit 7 = play once; the state itself is the low seven bits.
            self.actor_anim_state[a] = (v & 0x7F, bool(v & 0x80))
            return self.ST_CONT
        if op == 0x5A:                              # A_CLEAR_ANIM_STATE
            # the release: no entry = the movement-derived state
            self.actor_anim_state.pop(self._resolve_actor(self._f8()), None)
            return self.ST_CONT
        if op == 0x5B:                              # THREAD_STOP (by join handle)
            hs = self._f8()
            if hs < len(self.heap):
                for k in range(self.NC):
                    if self.active[k] and self.handle[k] == hs:
                        if k == c:                  # itself: the central ST_END
                            self.active[c] = 0
                            return self.ST_END
                        self.kill(k)
            return self.ST_CONT
        if op == 0x5D:                              # A_GET_DIR (i, var)
            a = self._resolve_actor(self._f8())
            v = self._f8()
            if v < len(self.heap):
                self.heap[v] = self.actors[a].dir
            return self.ST_CONT
        if op == 0x5C:                              # A_SET_BOX (i, w, h, ox, oy)
            a = self._resolve_actor(self._f8())
            self.actors[a].box = (self._f8(), self._f8(), self._f8(), self._f8())
            return self.ST_CONT
        if op == 0x35:                              # PLAYER_KNOCKBACK
            # Same shape as PLAYER_BOUNCE above: the console throws the player
            # away from its facing using the scene's configured impulse, and
            # the reference VM records the request for a verify.py to assert.
            self.player_knockback += 1
            return self.ST_CONT
        if op == 0x5E:                              # A_START_UPDATE
            # The console restarts the actor's On Update through the
            # vm.entity seam when it is not running. The reference VM has no
            # entity registry: it records the request and marks the actor
            # updating again (A_STOP_UPDATE's mirror).
            i = self._resolve_actor(self._f8())
            self.update_starts.append(i)
            self.actors[i].updating = 1
            return self.ST_CONT
        if op == 0x18:                              # A_STOP_UPDATE
            # The console kills the actor's On Update thread through the
            # vm.entity seam. The reference VM has no entity registry, so it
            # kills every live thread whose SELF is bound to that actor --
            # INCLUDING the one executing this op, as the console does when an
            # On Update script stops its own actor (the slice then ends; see
            # slice_live).
            i = self._resolve_actor(self._f8())
            for k in range(self.NC):
                if self.active[k] and self.self_actor[k] == i:
                    self.kill(k)
            self.actors[i].updating = 0
            return self.ST_CONT
        if op == 0x19:                              # A_PUSH (non-blocking slide)
            i, tiles = self._resolve_actor(self._f8()), self._f8()
            a = self.actors[i]
            a.tx, a.ty = self._push_target(a.x, a.y, tiles)
            a.moving = 1                            # the native auto-move slides it
            return self.ST_CONT
        if op == 0x1A:                              # A_EMOTE (waitable bubble)
            # PLAYER_ACTOR passes through UNRESOLVED: the bubble can hang over
            # the player, which is not a pool slot, so resolving would clamp it
            # to actor 0 and put the bubble over whoever is in slot 0.
            raw = self._f8()
            i = raw if raw == PLAYER_ACTOR else self._resolve_actor(raw)
            eid = self._f8()
            if not self.waiting[c]:
                self.waiting[c] = 1
                self.emote = {"actor": i, "id": eid, "timer": self.EMOTE_FRAMES}
                self.emote_log.append((i, eid))
            if self.emote and self.emote["timer"] > 0:
                self.pc[c] -= 3                     # rewind: still on screen
                return self.ST_YIELD
            self.waiting[c] = 0
            return self.ST_CONT
        if op == 0x20:                              # A_ACTIVATE
            # u16 coords: an actor's position is a WORLD pixel (see isa.OPS)
            i, tile = self._resolve_actor(self._f8()), self._f8()
            x, y = self._f16(), self._f16()
            a = self.actors[i]
            a.active, a.tile, a.x, a.y = 1, tile, x, y
            a.group, a.hp, a.solid = 2, 1, 1        # pack defaults (spec §12.3)
            if a.speed == 0:
                a.speed = 1
            return self.ST_CONT
        if op == 0x21:                              # A_SET_POS
            i, x, y = self._resolve_actor(self._f8()), self._f16(), self._f16()
            self.actors[i].x, self.actors[i].y = x, y
            return self.ST_CONT
        if op == 0x22:                              # A_MOVE_TO (waitable walk)
            i, x, y = self._resolve_actor(self._f8()), self._f16(), self._f16()
            if not self._step_to(i, x, y):
                self.pc[c] -= 6             # opcode + u8 + u16 + u16
                return self.ST_YIELD
            return self.ST_CONT
        if op == 0x23:                              # A_SPEED
            i, s = self._resolve_actor(self._f8()), self._f8()
            self.actors[i].speed = s
            return self.ST_CONT
        if op == 0x24:                              # A_MOVE_START (non-blocking)
            i, x, y = self._resolve_actor(self._f8()), self._f16(), self._f16()
            self._move_start(i, x, y)
            return self.ST_CONT
        if op == 0x25:                              # A_SET_CLIP (data-driven anim)
            i, kind = self._resolve_actor(self._f8()), self._f8()
            self.actors[i].clip = kind
            self.actors[i].hclip = kind
            return self.ST_CONT
        if op == 0x26:                              # A_SET_POS_E (pops x,y off the stack)
            i = self._resolve_actor(self._f8())
            y = self._pop()
            x = self._pop()
            self.actors[i].x, self.actors[i].y = x & 0xFFFF, y & 0xFFFF  # u16 world (core parity)
            return self.ST_CONT
        if op == 0x27:                              # A_MOVE_START_E (non-blocking; pops x,y)
            i = self._resolve_actor(self._f8())
            y = self._pop()
            x = self._pop()
            self._move_start(i, x & 0xFFFF, y & 0xFFFF)
            return self.ST_CONT
        if op == 0x28:                              # A_DEACTIVATE (retire an actor)
            i = self._resolve_actor(self._f8())
            a = self.actors[i]
            a.active, a.moving, a.clip = 0, 0, 255
            return self.ST_CONT
        if op == 0x1F:                              # A_MOVE_OPTS (one-shot latch)
            mode, coll = self._f8(), self._f8()
            self.pend_mopt = (mode & 3) | ((coll & 3) << 2)
            return self.ST_CONT
        if op == 0x1E:                              # PLAYER_MOVE_TO (waitable walk)
            x, y, mode = self._f16(), self._f16(), self._f8()
            if not self._player_step_to(x, y, mode):
                self.pc[c] -= 6                     # opcode + 2 + 2 + 1 operands
                return self.ST_YIELD
            return self.ST_CONT
        if op == 0x3C:                              # PLAYER_MOVE_TO_E (computed walk)
            # The `_E` twin: a rewound re-entry cannot re-read popped operands,
            # so the target LATCHES on first entry (the A_EMOTE waiting shape)
            # and the rewind covers only opcode + mode. One latch pair, not per
            # context - there is one player (mirrors core.mos pmt_x/pmt_y).
            mode = self._f8()
            if not self.waiting[c]:
                self.waiting[c] = 1
                self.pmt_y = self._pop() & 0xFFFF   # pushed x then y: y on top
                self.pmt_x = self._pop() & 0xFFFF
            if not self._player_step_to(self.pmt_x, self.pmt_y, mode):
                self.pc[c] -= 2                     # opcode + mode
                return self.ST_YIELD
            self.waiting[c] = 0
            return self.ST_CONT
        if op == 0x1D:                              # A_AWAIT_MOVE (waitable move)
            i = self._resolve_actor(self._f8())
            if self.actors[i].moving:
                self.pc[c] -= 2                     # rewind: re-enter next frame
                return self.ST_YIELD
            return self.ST_CONT
        if op == 0x1C:                              # A_REACTIVATE (respawn in place)
            i = self._resolve_actor(self._f8())
            a = self.actors[i]
            a.active = 1
            a.clip = a.hclip                        # the clip it was given
            if a.speed == 0:
                a.speed = 1
            return self.ST_CONT
        if op == 0x29:                              # A_SET_GROUP (faction)
            i = self._resolve_actor(self._f8())
            self.actors[i].group = self._f8()
            return self.ST_CONT
        if op == 0x2A:                              # A_SET_HP (per-actor HP)
            i = self._resolve_actor(self._f8())
            self.actors[i].hp = self._f16()
            return self.ST_CONT
        if op == 0x2B:                              # A_DAMAGE (subtract, clamp 0)
            i = self._resolve_actor(self._f8())
            amt = self._f16()
            a = self.actors[i]
            a.hp = a.hp - amt if a.hp > amt else 0
            return self.ST_CONT
        if op == 0x2C:                              # A_SET_DIR (facing 0..3)
            i = self._resolve_actor(self._f8())
            self.actors[i].dir = self._f8() & 3
            return self.ST_CONT
        if op == 0x2D:                              # A_SET_COLLISION (blocks the player)
            i = self._resolve_actor(self._f8())
            self.actors[i].solid = 1 if self._f8() else 0
            return self.ST_CONT
        if op == 0x1B:                              # A_SET_FRAME_E (pops the frame)
            i = self._resolve_actor(self._f8())
            fr = max(0, min(254, self._pop()))
            self.actors[i].frame_pin = (fr + 1) & 0xFF
            return self.ST_CONT
        if op == 0x2E:                              # A_SET_FRAME (pin one frame)
            i = self._resolve_actor(self._f8())
            self.actors[i].frame_pin = (self._f8() + 1) & 0xFF
            return self.ST_CONT
        if op == 0x2F:                              # A_SET_ANIM_SPEED (reference-engine MASK)
            i = self._resolve_actor(self._f8())
            s = self._f8()
            # 255 means "never" there; a u8 period cannot hold 256, so it
            # becomes 255 frames - the same approximation vm.actor makes.
            self.actors[i].anim_speed = 255 if s == 255 else s + 1
            return self.ST_CONT
        if op == 0x30:                              # UI_TEXT (§9.3)
            sid = self._f8()
            if self.waiting[c] == 0:
                ow = self.ui_owner
                if ow != 255 and ow != c:       # UI busy: retry (core parity - a
                    #                             dead owner cannot exist: every
                    #                             death path releases the UI)
                    self.pc[c] -= 2
                    return self.ST_YIELD
                self.ui_owner = c
                self.waiting[c] = 1
                self.box_open = 1
                self.box_hold_live = self.box_hold   # consume the one-shot latch
                self.box_hold = 0
                self.text_id = sid
                self.text_log.append(sid)
            if self.box_open == 1:
                self.pc[c] -= 2
                return self.ST_YIELD
            self.waiting[c] = 0
            if self.ui_owner == c:
                self.ui_owner = 255
            return self.ST_CONT
        if op == 0x31:                              # MENU (modal choice)
            op_pc = self.pc[c] - 1
            dest, row, count = self._f8(), self._f8(), self._f8()
            if self.waiting[c] == 0:
                ow = self.ui_owner
                if ow != 255 and ow != c:       # UI busy: retry (core parity - a
                    #                             dead owner cannot exist: every
                    #                             death path releases the UI)
                    self.pc[c] = op_pc
                    return self.ST_YIELD
                self.ui_owner = c
                self.waiting[c] = 1
                self.m_cursor = 0
                self.m_prev = self.m_preva = 1      # arm(): opening button must release
                self.m_prevb = 1
                self.menu_cancel_live = self.menu_cancel   # consume the one-shot latch
                self.menu_cancel = 0
                self.menu_open = 1
                self.menu_last = 255
            if self.cur_up:                         # nav (engine.menu)
                if self.m_prev == 0 and self.m_cursor > 0:
                    self.m_cursor -= 1
                self.m_prev = 1
            elif self.cur_down:
                if self.m_prev == 0 and self.m_cursor + 1 < count:
                    self.m_cursor += 1
                self.m_prev = 1
            else:
                self.m_prev = 0
            for _ in range(count):                  # advance past option ids
                self._f8()
            go = 0
            if self.cur_a:                          # confirm edge
                if self.m_preva == 0:
                    go = 1
                self.m_preva = 1
            else:
                self.m_preva = 0
            if self.menu_cancel_live:               # B CANCELS an armed menu: the
                if self.cur_b:                      # same close, writing -1
                    if self.m_prevb == 0:
                        go = 2
                    self.m_prevb = 1
                else:
                    self.m_prevb = 0
            if go:
                if go == 1:
                    # core parity: the confirming press is spent for the
                    # frame-top A sample too (core.mos reads the pad live in
                    # MENU, so there it can land after read_input's sample)
                    self.prev_a = 1
                pick = self.m_cursor if go == 1 else -1
                if dest < len(self.heap):           # OOB write guard (core parity)
                    self.heap[dest] = pick
                self.waiting[c] = 0
                self.menu_open = 0
                self.ui_owner = 255                 # release the UI latch
                self.menu_log.append(pick)
                return self.ST_CONT
            self.pc[c] = op_pc
            return self.ST_YIELD
        if op == 0x32:                              # HUD_SHOW (visibility)
            self.hud_show = 1 if self._f8() else 0
            return self.ST_CONT
        if op == 0x33:                              # SHMUP_SCROLL (pack op: pace, dir)
            pace, dir_ = self._f8(), self._f8()
            self.shmup_pace = pace                  # 0 = pause the auto-scroll
            if dir_ < 2:                            # 2 = keep the current direction
                self.shmup_dir = dir_
            return self.ST_CONT
        if op == 0x34:                              # PLAYER_VISIBLE (1 = visible)
            # the reference engine's ACTOR_FLAG_HIDDEN on actors[0]: the DRAW stops and
            # nothing else does, so the player keeps its position and handler.
            self.player_hidden = 0 if self._f8() else 1
            return self.ST_CONT
        if op == 0x3D:                              # A_VISIBLE (actor, 1 = visible)
            # The same flag on a POOL actor. It is ONLY the draw: the actor
            # stays in every scan, so a hidden actor still collides and still
            # answers the interact probe - which is what makes an actor hidden
            # in its On Init an invisible interaction hotspot.
            who = self._f8()
            self.actors[self._resolve_actor(who)].visible = 1 if self._f8() else 0
            return self.ST_CONT
        if op == 0x3E:                              # TEXT_SPEED (speed, ff)
            self.text_speed = self._f8() & 7
            self.text_ff = self._f8()
            return self.ST_CONT
        if op == 0x3F:                              # TEXT_BLIP (freq16, frames)
            self.blip_freq = self._f16()
            self.blip_frames = self._f8()
            return self.ST_CONT
        if op == 0x38:                              # FADE (waitable)
            dir_, frames = self._f8(), self._f8()
            if self.waiting[c] == 0:
                self.waiting[c] = 1
                self.scratch[c] = frames
            if self.scratch[c] > 0:
                self.scratch[c] -= 1
            prog = frames - self.scratch[c]
            lvl = min(3, (prog * 4 // frames) if frames else 0)
            self.fade_level = (3 - lvl) if dir_ == 1 else lvl
            if self.scratch[c] > 0:
                self.pc[c] -= 3
                return self.ST_YIELD
            self.waiting[c] = 0
            self.fade_level = 0 if dir_ == 1 else 3
            return self.ST_CONT
        if op == 0x39:                              # SHAKE (waitable under the latch)
            frames, amp = self._f8(), self._f8()
            if self.waiting[c] == 0:
                self.waiting[c] = 1
                self.shk_frames, self.shk_amp = frames, amp
            if self.shk_wait and self.shk_frames > 0:
                self.shk_frames -= 1                # one display frame a step
                if self.shk_frames == 0:
                    self.shk_axis = self.shk_wait = 0    # the latch is ONE shot
                self.pc[c] -= 3                     # opcode + frames + amp
                return self.ST_YIELD
            self.waiting[c] = 0
            return self.ST_CONT
        if op == 0x4C:                              # SHAKE_OPTS (one-shot latch)
            self.shk_axis, self.shk_wait = self._f8(), self._f8()
            return self.ST_CONT
        if op == 0x4F:                              # PAL_SET (layer, slot, pal)
            layer, slot, pal = self._f8(), self._f8(), self._f8()
            self.palette_log.append((layer, slot, pal))
            return "CONT"
        if op == 0x4D:                              # BKG_TILE (dst, src)
            dst = self._f8()
            self.bkg_tiles[dst] = self._f8()
            return self.ST_CONT
        if op == 0x4E:                              # BKG_TILE_E (dst; pops src)
            dst = self._f8()
            self.bkg_tiles[dst] = self._pop() & 0xFF
            return self.ST_CONT
        if op == 0x3A:                              # CAM_MOVE_TO (waitable pan)
            x, y, step = self._f8(), self._f8(), self._f8()
            if self.waiting[c] == 0:
                self.waiting[c] = 1
                self.cam_lock = 1                   # cam_hold: pin from the current view
            self.cam_x = self._approach(self.cam_x, x, step)
            self.cam_y = self._approach(self.cam_y, y, step)
            if not (self.cam_x == x and self.cam_y == y):
                self.pc[c] -= 4
                return self.ST_YIELD
            self.waiting[c] = 0
            return self.ST_CONT
        if op == 0x3B:                              # SCROLL_BG (vx, vy signed 1/4-px)
            vx, vy = self._f8(), self._f8()
            if vx > 127:
                vx -= 256
            if vy > 127:
                vy -= 256
            self.scroll_vx, self.scroll_vy = vx, vy
            self.scroll_on = 0 if (vx == 0 and vy == 0) else 1
            if vx == 0 and vy == 0:
                self.cam_lock = 0                   # (0,0) = off + camera release
            return self.ST_CONT
        if op == 0x40:                              # TIMER_SET
            tid, period, entry = self._f8(), self._f16(), self._f16()
            if tid < self.NTIMERS:
                self.tmr_active[tid] = 1
                self.tmr_period[tid] = period
                self.tmr_count[tid] = period
                self.tmr_entry[tid] = entry
            return self.ST_CONT
        if op == 0x41:                              # TIMER_STOP
            tid = self._f8()
            if tid < self.NTIMERS:
                self.tmr_active[tid] = 0
            return self.ST_CONT
        if op == 0x42:                              # INPUT_ATTACH
            btn, entry = self._f8(), self._f16()
            over = btn >> 7                         # bit 7 = CONSUME the button
            btn = btn & 0x7F
            # the slot already bound to btn wins over a free one (a re-attach
            # REPLACES; core parity -- a free slot below the bound one must not
            # leave two live slots on one button)
            slot = 255
            for i in range(self.NIN):
                if self.in_active[i] and self.in_btn[i] == btn:
                    slot = i
            if slot == 255:
                for i in range(self.NIN):
                    if self.in_active[i] == 0:
                        slot = i
                        break
            if slot != 255:
                self.in_active[slot] = 1
                self.in_btn[slot] = btn
                self.in_entry[slot] = entry
                self.in_over[slot] = over
                self.in_prev[slot] = self.buttons[btn]   # mask a button already held
                self._refresh_consume()
            return self.ST_CONT
        if op == 0x43:                              # INPUT_DETACH
            btn = self._f8() & 0x7F
            for i in range(self.NIN):
                if self.in_active[i] and self.in_btn[i] == btn:
                    self.in_active[i] = 0
                    self.in_over[i] = 0
            self._refresh_consume()
            return self.ST_CONT
        if op == 0x44:                              # SAVE (snapshot heap + engine state)
            self.saved[self._slot()] = {
                "heap": list(self.heap), "scene": self.cur_scene,
                "px": self.player_x, "py": self.player_y}
            return self.ST_CONT
        if op == 0x45:                              # LOAD: restore, then the save pack
            got = self._take_slot_save()
            if got is not None:                     # raises LOAD_COMPLETE (spec §10
                self.heap = list(got["heap"])                       # code 3); no valid
                self.player_x = got["px"]                           # save -> no-op
                self.player_y = got["py"]
                self.pend_code = 3
                self.pend_a = got["scene"]
                self.pend_b = got["px"]
                self.pend_c = got["py"]
            return self.ST_CONT
        if op == 0x47:                              # DATA_CLEAR (the latched slot)
            # The SIGNATURE only, as the reference VM's data_clear does - the payload is
            # simply never trusted again.
            self.saved[self._slot()] = None
            return self.ST_CONT
        if op == 0x48:                              # DATA_PEEK (src cell -> dst cell)
            src, dst = self._f8(), self._f8()
            got = self._take_slot_save()
            v = 0
            if got is not None and src < len(got["heap"]):
                v = got["heap"][src]
            self.heap[dst] = v                      # 0 when the slot is empty
            return self.ST_CONT
        if op == 0x46:                              # SET_PLAYER_HIT (register the script)
            self.player_hit_entry = self._f16()
            return self.ST_CONT
        if op == 0x49:                              # MUSIC_ROUTINE (attach a script)
            slot = self._f8() & 3                   # the reference VM masks it the same way
            self.music_routine_entry[slot] = self._f16()
            self.music_routine_ctx[slot] = 255      # "no instance yet", explicitly
            return self.ST_CONT
        if op == 0x4A:                              # OVERLAY_SHOW (curtain at row)
            self.curtain_y = min(144, self._f8() * 8)
            self.curtain_tick = 0
            self.curtain_log.append(self.curtain_y)
            return self.ST_CONT
        if op == 0x4B:                              # OVERLAY_MOVE_TO (waitable slide)
            row, speed = self._f8(), self._f8()
            dst = min(144, row * 8)
            if self.curtain_y != dst:
                # The reference engine's ui_update: one PIXEL per (mask + 1) frames, and
                # speed 0 steps TWO. Rewind + yield until it arrives, the
                # A_AWAIT_MOVE shape.
                mask = self._UI_TIME_MASKS[min(7, speed)]
                if self.curtain_tick < mask:
                    self.curtain_tick += 1
                else:
                    self.curtain_tick = 0
                    step = 2 if speed == 0 else 1
                    if self.curtain_y < dst:
                        self.curtain_y = min(dst, self.curtain_y + step)
                    else:
                        self.curtain_y = max(dst, self.curtain_y - step)
                if self.curtain_y != dst:
                    self.pc[c] -= 3     # rewind: re-enter next frame
                    return self.ST_YIELD
            self.curtain_log.append(self.curtain_y)
            return self.ST_CONT
        if op == 0x50:                              # SFX (canned effect id -> sound.sfx)
            self.sound_log.append(("sfx", self._f8()))
            return self.ST_CONT
        if op == 0x51:                              # TONE (freq u16, frames u8 -> sound.beep)
            freq = self._f16()
            frames = self._f8()
            self.sound_log.append(("tone", freq, frames))
            return self.ST_CONT
        if op == 0x52:                              # SND_STOP (-> sound.stop)
            self.sound_log.append(("stop",))
            return self.ST_CONT
        if op == 0x53:                              # MUSIC_PLAY (spawn the exempt song
            entry = self._f16()                     # thread -- runs under the lock)
            if self.exempt_ctx != 255:
                self.kill(self.exempt_ctx)          # replace any current song
            self.exempt_ctx = self._spawn(entry)
            self.sound_log.append(("music_play", entry))
            return self.ST_CONT
        if op == 0x54:                              # MUSIC_STOP (kill song thread + silence)
            if self.exempt_ctx != 255:
                self.kill(self.exempt_ctx)
                self.exempt_ctx = 255
            self.sound_log.append(("music_stop",))
            return self.ST_CONT
        if op == 0x55:                              # MUSIC_TONE (a tone on the music voice)
            freq = self._f16()
            frames = self._f8()
            self.sound_log.append(("mtone", freq, frames))
            return self.ST_CONT
        if op == 0x56:                              # MUSIC_SONG (play a driven song by index)
            song = self._f8()
            self.sound_log.append(("music_song", song))
            return self.ST_CONT
        if op == 0x57:                              # MUSIC_PAUSE
            self.sound_log.append(("music_pause",))
            return self.ST_CONT
        if op == 0x58:                              # MUSIC_RESUME
            self.sound_log.append(("music_resume",))
            return self.ST_CONT
        if op == 0x59:                              # MUSIC_MUTE (per-channel mask)
            self.sound_log.append(("music_mute", self._f8()))
            return self.ST_CONT
        if op == 0x60:                              # PROJ_LAUNCH (default mask)
            x, y = self._f16(), self._f16()
            vx8, vy8, tile, life = self._f8(), self._f8(), self._f8(), self._f8()
            vx = vx8 - 256 if vx8 > 127 else vx8
            vy = vy8 - 256 if vy8 > 127 else vy8
            self._proj_add({"x": x, "y": y, "vx": vx, "vy": vy,
                            "tile": tile, "life": life, "mask": self.MASK_DEFAULT})
            return self.ST_CONT
        if op == 0x61:                              # PROJ_LAUNCH_E (tile,life inline; pops x,y,vx,vy)
            tile, life = self._f8(), self._f8()
            vy, vx, y, x = self._pop(), self._pop(), self._pop(), self._pop()
            self._proj_add({"x": x, "y": y, "vx": vx, "vy": vy,
                            "tile": tile, "life": life, "mask": self.MASK_DEFAULT})
            return self.ST_CONT
        if op == 0x62:                              # PROJ_LAUNCH_M (x,y,vx,vy,tile,life,mask)
            x, y = self._f16(), self._f16()
            vx8, vy8, tile, life, mask = (self._f8(), self._f8(), self._f8(),
                                          self._f8(), self._f8())
            vx = vx8 - 256 if vx8 > 127 else vx8
            vy = vy8 - 256 if vy8 > 127 else vy8
            self._proj_add({"x": x, "y": y, "vx": vx, "vy": vy,
                            "tile": tile, "life": life, "mask": mask})
            return self.ST_CONT
        if op == 0x63:                              # PROJ_LAUNCH_EM (tile,life,mask inline; pops x,y,vx,vy)
            tile, life, mask = self._f8(), self._f8(), self._f8()
            vy, vx, y, x = self._pop(), self._pop(), self._pop(), self._pop()
            self._proj_add({"x": x, "y": y, "vx": vx, "vy": vy,
                            "tile": tile, "life": life, "mask": mask})
            return self.ST_CONT
        if op == 0x36:                              # PROJ_GROUP (one-shot latch)
            self.proj_group = self._f8()
            return self.ST_CONT
        if op == 0x66:                              # PROJ_PAL (one-shot latch)
            self.proj_pal = self._f8()
            return self.ST_CONT
        if op == 0x65:                              # PROJ_ANIM (frames, period, stride) latch
            frames, period, stride = self._f8(), self._f8(), self._f8()
            self.proj_latch = (frames, period or 1, stride or 1)
            return self.ST_CONT
        if op == 0x64:                              # PROJ_LAUNCH_A (pops x,y,angle,speed)
            tile, life, mask = self._f8(), self._f8(), self._f8()
            speed, ang, y, x = (self._pop(), self._pop() & 255,
                                self._pop(), self._pop())
            # the same 1/16-px velocity vm.projectile.launch_angle computes
            vx = (self._sin128(ang) * speed) // 128
            vy = (-self._sin128((ang + 64) & 255) * speed) // 128
            # already in SIXTEENTHS (the unit `speed` is given in), so it
            # goes straight into the sub-pixel fields the flight reads
            self._proj_add({"x": x, "y": y, "vx16": vx, "vy16": vy,
                            "vx": vx // 16, "vy": vy // 16,
                            "angle": ang, "speed": speed,
                            "tile": tile, "life": life, "mask": mask})
            return self.ST_CONT
        self.active[c] = 0                          # unknown opcode: END (§13.5,
        return self.ST_END                          # the runaway-PC guard)

    def _run_context(self, c):
        n = 0
        self.slice_live = 1
        try:
            while n < self.QUANT:
                st = self._step(c)
                if st != self.ST_END and self.active[c] == 0:
                    return                          # the op killed its own thread
                    #                                 (kill() ran the §11 cleanup)
                if st == self.ST_END:
                    self._death_cleanup(c)          # the ONE shared §11 path
                    return
                if st != self.ST_CONT:
                    return
                n += 1
        finally:
            self.slice_live = 0

    def _run_scripts(self):
        for c in range(self.NC):
            if self.active[c] == 1:
                if self.lockcount > 0:
                    if c == self.lockowner or c == self.exempt_ctx:
                        self._run_context(c)     # the exempt song thread advances
                        #                          under a cutscene lock too (§8.2)
                else:
                    self._run_context(c)

    def _reset_scene_ui(self):
        """Tear down open UI + per-scene timers/input on a scene change / reset /
        load (spec §10 reset_scene_ui): the old room's box/menu, timers and
        button attachments must not leak into the new room."""
        self.box_open = 0
        self.menu_open = 0
        self.menu_cancel = 0      # a latch armed for a menu the room change killed
        self.box_hold = 0         # ... and one armed for a box that never opened
        self.box_hold_live = 0
        self.ui_owner = 255
        self.text_id = None
        self.tmr_active = [0] * self.NTIMERS
        self.in_active = [0] * self.NIN
        self.in_over = [0] * self.NIN         # ... nor a consumed button, or the
        self.consume_mask = 0                 # new room's player cannot be driven
        self.tmr_ctx = [255] * self.NTIMERS   # a fresh room must not inherit a
        self.in_ctx = [255] * self.NIN        # busy gate (the instances are killed)
        self.player_hidden = 0          # ... and the player is visible again
        self.player_hit_entry = None    # a scene's On Init re-registers it
        # ...and the music routines with it (the reference VM: music_init_events(FALSE)
        # on every CHANGE_SCENE clears the scripts AND their handles).
        self.music_routine_entry = [None] * 4
        self.music_routine_ctx = [255] * 4
        self.music_routine_gen = [0] * 4
        self.music_routine_q = []
        self.proj_latch = None          # a thread killed between PROJ_ANIM and its
        #                                 launch must not arm the next room's shot
        self.emote = None               # a live bubble belongs to the OLD room's actor

    def _service_exception(self):
        """Frame step 4 (spec §8.1/§10): service the pending RAISE, if any."""
        code = self.pend_code
        if code == 0:
            return
        self.pend_code = 0
        if code == 1:                               # RESET: kill ALL threads, keep
            for c in range(self.NC):                # the heap, restart context 0
                self.kill(c)
            self._reset_scene_ui()
            self.reset_log.append(1)
            self._spawn(self.entry)
        elif code in (2, 3):                        # CHANGE_SCENE / LOAD_COMPLETE
            # A scene change TERMINATES the old room's threads (the reference engine's
            # model); only the exempt music thread plays on. A survivor is
            # bound to a room that no longer exists -- the reference-engine sample's
            # doorway script (walk-to + this raise) left copies alive in the
            # NEXT room, each re-raising the change when its walk finished.
            for c in range(self.NC):
                if c != self.exempt_ctx:
                    self.kill(c)
            self.cur_scene = self.pend_a & 0xFF
            self.fade_level = 0
            self._reset_scene_ui()
            self.change_log.append((self.pend_a & 0xFF, self.pend_b, self.pend_c))
            if code == 3:
                self.load_log.append((self.pend_a & 0xFF, self.pend_b, self.pend_c))
        # unknown codes: cleared, never looped (§13.11)

    def _read_input(self):
        a = self.cur_a
        a_edge = 1 if (a == 1 and self.prev_a == 0) else 0
        self.prev_a = a
        if self.box_open == 1 and self.box_hold_live > 0:
            # A TIMED box closes itself and ignores the pad (core parity: GB
            # Studio's non-key close modes carry no button flag).
            self.box_hold_live -= 1
            if self.box_hold_live > 0:
                return
            a_edge = 1
        if self.box_open == 1 and a_edge == 1:
            self.box_open = 0
            self.ui_owner = 255            # release the UI latch (a waiting thread may open)
            self.text_id = None

    def frame(self, a_pressed=False, up=False, down=False, held=(), b_pressed=False):
        """Advance one game frame (spec §8.1 order). `held` = button names down
        this frame (for input attachments): a/b/start/select/up/down/left/right.
        `b_pressed` is B held this frame (the menu cancel edge reads it)."""
        # The free-running VM-frame clock (ST_GAME_TIME) ticks FIRST, before any
        # script runs, so every thread in one frame reads the same value - the
        # console increments it at the top of run() for the same reason.
        self.game_time = (self.game_time + 1) & 0xFFFF
        self.cur_a = 1 if a_pressed else 0
        self.cur_b = 1 if (b_pressed or "b" in held) else 0
        self.cur_up = 1 if up else 0
        self.cur_down = 1 if down else 0
        self.buttons = [0] * 8
        if a_pressed:
            self.buttons[BUTTONS["a"]] = 1
        if b_pressed:
            self.buttons[BUTTONS["b"]] = 1
        if up:
            self.buttons[BUTTONS["up"]] = 1
        if down:
            self.buttons[BUTTONS["down"]] = 1
        for name in held:
            self.buttons[BUTTONS[name]] = 1
        self._read_input()
        if self.lockcount == 0:
            # Timers + input attachments both freeze under a cutscene lock, so a
            # short-period timer can't exhaust the context pool with frozen threads.
            self._tick_timers()
            self._tick_input_attach()
            # ...and so do the music routines: the reference VM runs music_events_update()
            # inside the same `if (!VM_ISLOCKED())` block, after the timers.
            self._music_events_update()
        self._run_scripts()
        self._service_exception()               # step 4: the ONLY service point
        if self.proj_under_lock or self.lockcount == 0:
            self._update_projectiles()          # native flight (frozen under a lock
                                                # unless proj_under_lock, the reference VM's rule)
        for i, a in enumerate(self.actors):     # native actor auto-move (runs under a lock)
            if a.moving:
                if self._step_once(i, a.tx, a.ty, a.mopt):   # vm.actor.step_all
                    a.moving = 0
        if self.emote is not None and self.emote["timer"] > 0:
            # mirrors vm.emote.update(): counts down under a cutscene lock too,
            # like the actors it sits on
            self.emote["timer"] -= 1
            if self.emote["timer"] == 0:
                self.emote = None
        if self.shk_frames > 0:                 # mirror player.cam_apply()'s shake tick
            self.shk_frames -= 1

    def run(self, frames, a_at=()):
        """Run `frames` frames; `a_at` = a set/list of frame indices to press A."""
        a_at = set(a_at)
        for f in range(frames):
            self.frame(a_pressed=(f in a_at))

    def pos(self, i):
        return (self.actors[i].x, self.actors[i].y)

    def any_active_threads(self):
        return sum(self.active)
