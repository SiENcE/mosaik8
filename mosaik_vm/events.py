"""mosaik_vm.events - Event catalogue (EVENTS) + lowering an authored event to instructions."""

from . import isa
from .isa import (BUTTONS, Label, MIN_SCREEN_ROWS, MIN_VM_STACK, NARGS,
                  PLAYER_ACTOR, RPN, SAVE_SLOTS, SELF_ACTOR,
                  STATES, VmError, compile_expr, _rpn_max_depth)


def _i(ev, key, default=None):
    if key not in ev:
        if default is not None:
            return default
        raise VmError("event %r missing required field %r" % (ev.get("event"), key))
    return int(ev[key])


# --- OPERAND WIDTH ---------------------------------------------------------
# An instruction operand is one or two bytes, and this file used to spell that
# `& 0xFF` / `& 0xFFFF` at ~20 sites. A mask is not a check: it TRUNCATES, so an
# out-of-range authored value became a different, legal-looking value with no
# diagnostic anywhere - `actor_set_group` with group 300 emitted group 44, and
# `wait_until poll = 256` emitted a poll of 1 because the mask ran INSIDE the
# max(1, ...). Bare `.evt.toml` authoring reaches here directly (the studio
# catalogue is not in the path), so this is the source of truth, and it follows
# the one already-validating operand in the file - `_menu_blob`'s row, which
# raises rather than wrapping.
#
# SIGNED is the exception that has to be kept, not tidied away: a velocity is an
# i8 packed into a u8 operand (vx = -1 IS 255 to the runtime) and a u16 literal
# may be authored as -1 for 65535. So a signed operand accepts either encoding
# of the same byte pattern and rejects only what fits neither.
def _fit(ev, key, value, bits, signed):
    lo = -(1 << (bits - 1)) if signed else 0
    hi = (1 << bits) - 1
    if not lo <= value <= hi:
        raise VmError(
            "event %r: %s = %d does not fit a %d-bit operand (%d..%d)"
            % (ev.get("event"), key, value, bits, lo, hi))
    return value & hi


def _u8(ev, key, default=None, signed=False):
    """A one-byte operand, validated instead of truncated. `signed` also accepts
    -128..-1 and packs them two's-complement (an i8 velocity)."""
    return _fit(ev, key, _i(ev, key, default), 8, signed)


def _u16(ev, key, default=None, signed=True):
    """A two-byte operand. Signed by default: a negative literal is the authored
    spelling of a big unsigned value (`set_var v = -1` is 65535 to the VM)."""
    return _fit(ev, key, _i(ev, key, default), 16, signed)


def _actor(ev, key="actor", default=None):
    v = ev.get(key, default)
    if isinstance(v, str) and v.strip().lower() == "self":
        return SELF_ACTOR
    # "player" -> the PLAYER sentinel. Only the ops that can answer for a
    # non-pool entity accept it (A_EMOTE today); anything else resolves it
    # through resolve_actor, where it clamps to actor 0 rather than indexing
    # past the pool.
    if isinstance(v, str) and v.strip().lower() == "player":
        return PLAYER_ACTOR
    if v is None and default is None and key not in ev:
        raise VmError("event %r missing required field %r" % (ev.get("event"), key))
    i = int(v)
    # The vm.actor pool is `isa.ACTOR_POOL` slots (actor.mos ACTORS, sized by
    # `[build] actor_pool`); the runtime indexes its arrays with this literal,
    # so an out-of-range id would be an OOB write on console. The studio
    # catalogue checks the same range, but bare .evt.toml authoring reaches
    # here directly -- validate at the source of truth. Read through the module
    # so a project that raised the pool is honoured.
    if not 0 <= i < isa.ACTOR_POOL:
        raise VmError("event %r: actor %d out of range (the actor pool is "
                      "0..%d - raise [build] actor_pool to widen it)"
                      % (ev.get("event"), i, isa.ACTOR_POOL - 1))
    return i


#: `camera_release`'s axis field -> the two follow bits of state 4. The stored
#: value is a CHOICE index, so the order here IS the catalogue's option order.
_CAM_AXES = (0x03, 0x01, 0x02)          # both, x only, y only

#: Its four `preventScroll` flags -> bits 2..5, in the reference engine's own pairing:
#: left is X_MIN and right is X_MAX, because blocking a scroll to the RIGHT
#: means refusing to let the camera exceed where it already was.
_CAM_PREVENT = (("no_left", 0x04), ("no_right", 0x08),
                ("no_up", 0x10), ("no_down", 0x20))


def _cam_settings(ev):
    """`camera_release` -> the reference engine's `camera_settings` byte."""
    axis = _i(ev, "axis", 0)
    if not 0 <= axis < len(_CAM_AXES):
        raise VmError("camera_release: axis must be 0 (both), 1 (x) or 2 (y), "
                      "not %r" % (ev.get("axis"),))
    b = _CAM_AXES[axis]
    for key, bit in _CAM_PREVENT:
        if _i(ev, key, 0):
            b |= bit
    return b


#: `camera_prop`'s property field -> the offset from `camera_deadzone_x`. The
#: stored value is a CHOICE index and the order is the reference engine's own
#: (`eventCameraPropertySet.js`: deadzone x, deadzone y, offset x, offset y).
_CAM_PROPS = 4


def _cam_prop(ev):
    n = _i(ev, "prop", 0)
    if not 0 <= n < _CAM_PROPS:
        raise VmError("camera_prop: prop must be 0..3 (dead zone x/y, offset "
                      "x/y), not %r" % (ev.get("prop"),))
    return n


def _rpn_instr(expr, cc):
    return ("RPN", [compile_expr(expr, cc) + bytes([RPN["END"]])])


def _rpn_seq(exprs, cc):
    """Compile a SEQUENCE of expressions whose results stay on the thread's
    expression stack until one `_E` op pops them all (PLAYER_SETPOS_E,
    CHANGE_SCENE_E, PROJ_LAUNCH_E/_EM/_A, THREADN ...). compile_expr checks each
    expression's own peak depth against the uniform 8-cell stack, but the
    i-th expression evaluates with i values already RESIDENT beneath it, so
    the sum is what the console sees -- past the cap, vpush DROPS the push
    (spec §13.3) and the op pops the wrong cells: no crash, just silently wrong
    coordinates. Check the sum here, loudly."""
    out = []
    for i, e in enumerate(exprs):
        rpn = compile_expr(str(e), cc)
        depth = i + _rpn_max_depth(rpn)
        if depth > MIN_VM_STACK:
            raise VmError(
                "expression %r is the %s of %d pushed together; with %d value(s) "
                "already on the stack it reaches %d cells, past the uniform %d-slot "
                "per-thread expression stack (VM8 spec §14; the console would "
                "silently evaluate it wrong); split it with a temporary variable"
                % (str(e), _ordinal(i + 1), len(exprs), i, depth, MIN_VM_STACK))
        out.append(("RPN", [rpn + bytes([RPN["END"]])]))
    return out


def _ordinal(n):
    return "%d%s" % (n, {1: "st", 2: "nd", 3: "rd"}.get(n if n < 20 else n % 10, "th"))


def _is_expr(v):
    """A numeric param is an EXPRESSION (compiled to RPN, evaluated on the thread
    stack) rather than a compact inline literal when it is not a bare, optionally
    signed integer: `40` / 40 / `-3` are literals; `player_x()` / `gold + 4` are
    expressions. A literal-only event keeps its compact inline op (byte-identical);
    only a real expression selects the stack-arg `_E` opcode variant (F1)."""
    if isinstance(v, bool) or isinstance(v, int):
        return False
    try:
        int(str(v).strip())
        return False
    except (TypeError, ValueError):
        return True


def _ev_actor_pos(ev, cc, op_lit, op_e):
    """actor_set_pos / actor_move: a literal x AND y -> the compact inline op; an
    EXPRESSION in either -> push both (x then y) via RPN, then the `_E` variant
    which pops them. The actor id stays an inline operand either way."""
    a = _actor(ev)
    if _is_expr(ev.get("x")) or _is_expr(ev.get("y")):
        return _rpn_seq([ev["x"], ev["y"]], cc) + [(op_e, [a])]
    return [(op_lit, [a, _i(ev, "x"), _i(ev, "y")])]


def _ev_change_scene(ev, cc):
    """change_scene: assembler sugar for RAISE 2, room, x, y (spec §6/§10).

    An all-literal change keeps the compact inline RAISE (byte-identical). If
    the ROOM (or a coordinate) is an EXPRESSION, push room, x, y and use
    CHANGE_SCENE_E, which pops them -- that is what a scene STACK needs (GB
    Studio's push/pop state: remember `scene()` in a variable, later return to
    it), since a literal RAISE can only name a fixed room."""
    if any(_is_expr(ev.get(k)) for k in ("room", "x", "y")):
        return _rpn_seq([ev.get("room", 0), ev.get("x", 0), ev.get("y", 0)], cc) + [
                ("CHANGE_SCENE_E", [])]
    return [("RAISE", [2, _i(ev, "room"), _i(ev, "x"), _i(ev, "y")])]


# The reference engine's `moveType` -> the axis order PLAYER_MOVE_TO walks in. Its
# "horizontal" closes X first then Y; "vertical" closes Y first; anything else
# (its default) moves both axes at once.
_MOVE_MODES = {"diagonal": 0, "horizontal": 1, "vertical": 2}


def _move_opts(ev):
    """The A_MOVE_OPTS latch an actor move needs, or [] when it is plain.

    The reference engine carries `moveType` and `useCollisions`/`collideWith` on every
    move; ours are optional params on the same event, prepended as a one-shot
    latch. Emitting nothing for the default is what keeps every existing
    project byte-identical.
    """
    mode = _MOVE_MODES.get(str(ev.get("mode", "diagonal")), 0)
    with_ = ev.get("collide_with")
    if with_ is None:
        coll = 0
    elif isinstance(with_, int):
        coll = with_ & 3
    else:
        coll = ((1 if "walls" in with_ else 0) | (2 if "actors" in with_ else 0))
    if not mode and not coll:
        return []
    return [("A_MOVE_OPTS", [mode, coll])]


def _ev_actor_move_to(ev, cc):
    return _move_opts(ev) + [("A_MOVE_TO", [_actor(ev), _i(ev, "x"), _i(ev, "y")])]


def _ev_player_move_to(ev, cc):
    mode = _MOVE_MODES.get(str(ev.get("mode", "diagonal")), 0)
    if _is_expr(ev.get("x")) or _is_expr(ev.get("y")):
        return _rpn_seq([ev["x"], ev["y"]], cc) + [("PLAYER_MOVE_TO_E", [mode])]
    return [("PLAYER_MOVE_TO", [_i(ev, "x"), _i(ev, "y"), mode])]


def _ev_player_setpos(ev, cc):
    if _is_expr(ev.get("x")) or _is_expr(ev.get("y")):
        return _rpn_seq([ev["x"], ev["y"]], cc) + [("PLAYER_SETPOS_E", [])]
    return [("PLAYER_SETPOS", [_i(ev, "x"), _i(ev, "y")])]


# The default collision mask (F3.5): hit all enemy groups, NOT the player (bit 0).
# A default-mask shot keeps the compact PROJ_LAUNCH/_E op (byte-identical); a
# non-default mask (a faction shot / an enemy shot masking the player) selects the
# _M/_EM variants. Kept in lockstep with vm.core's MASK_DEFAULT.
_PROJ_MASK_DEFAULT = 0xFE


def _ev_projectile(ev, cc):
    """Launch a projectile. Any expression in x/y/vx/vy -> push all four via RPN +
    an `_E` variant (tile/life[/mask] stay inline). A non-default `mask` selects the
    `_M`/`_EM` masked variants; a default mask keeps the compact op (byte-identical),
    with the i8 velocity packed into a u8 operand as before.

    Optional `frames` (> 1) + `anim` (period, frames per step) animate the shot:
    The reference engine's loopAnim + animSpeed, the rotating particle. They prefix a
    PROJ_ANIM latch the launch consumes; absent, the ops are byte-identical."""
    keys = ("x", "y", "vx", "vy")
    has_expr = any(_is_expr(ev.get(k)) for k in keys)
    mask = _u8(ev, "mask", _PROJ_MASK_DEFAULT)
    tile, life = _i(ev, "tile", 0), _i(ev, "life", 60)
    pre = []
    frames = _i(ev, "frames", 0)
    if frames > 1:
        pre.append(("PROJ_ANIM", [_fit(ev, "frames", frames, 8, False),
                                  _u8(ev, "anim", 8),
                                  max(1, _u8(ev, "stride", 1))]))
    # ...and the shot's OWN group, so the actor it strikes can tell which
    # weapon hit it. Absent = 0, which the runtime reads as "ungrouped" and
    # reports as its sentinel - 0 itself means the PLAYER'S BODY.
    group = _u8(ev, "group", 0)
    if group:
        pre.append(("PROJ_GROUP", [group]))
    # ...and its sprite PALETTE. Absent is not 0: a launch with no palette
    # emits nothing, so a program that never colours a shot is unchanged.
    if ev.get("palette") is not None:
        pre.append(("PROJ_PAL", [_u8(ev, "palette", 0)]))
    if ev.get("angle") is not None:
        # ANGLE form (the reference engine's direction dial / angle variable / an atan2
        # aim): always expression-valued and always masked - see the ISA note.
        # `speed` is 1/16 px per frame, so its fractional speeds survive.
        out = pre + _rpn_seq([ev.get(k, 0) for k in ("x", "y", "angle")]
                             + [ev.get("speed", 16)], cc)
        out.append(("PROJ_LAUNCH_A", [tile, life, mask]))
        return out
    if has_expr:
        out = pre + _rpn_seq([ev.get(k, 0) for k in keys], cc)
        if mask == _PROJ_MASK_DEFAULT:
            out.append(("PROJ_LAUNCH_E", [tile, life]))
        else:
            out.append(("PROJ_LAUNCH_EM", [tile, life, mask]))
        return out
    # vx/vy are i8 velocities packed into u8 operands - signed on purpose.
    xyv = [_i(ev, "x"), _i(ev, "y"), _u8(ev, "vx", signed=True),
           _u8(ev, "vy", signed=True), tile, life]
    if mask == _PROJ_MASK_DEFAULT:
        return pre + [("PROJ_LAUNCH", xyv)]
    return pre + [("PROJ_LAUNCH_M", xyv + [mask])]


def _ev_actor_set_frame(ev, cc):
    """Pin one animation frame. An EXPRESSION frame pushes it via RPN and takes
    the `_E` variant; a literal keeps the compact inline op (byte-identical).
    255 is the runtime's "no pin" sentinel, so a literal clamps to 0..254 - the
    computed form clamps in the runtime instead, where the value is known."""
    fr = ev.get("frame", 0)
    if _is_expr(fr):
        return [_rpn_instr(str(fr), cc), ("A_SET_FRAME_E", [_actor(ev)])]
    return [("A_SET_FRAME", [_actor(ev), min(254, max(0, _i(ev, "frame", 0)))])]


def _ev_bkg_tile(ev, cc):
    """Rewrite a background TILE's pixels from the world's replacement bank
    (the reference engine's EVENT_REPLACE_TILE_XY / VM_REPLACE_TILE).

    `tile` is the DESTINATION background tile index and `src` the replacement.
    An expression `src` takes the `_E` variant - which is the usual case, since
    a background digit readout computes it (`gold % 100 / 10`) - and a literal
    keeps the compact inline op, so a world that only ever writes fixed art
    stays byte-identical.

    The DESTINATION stays a literal on both arms deliberately: a map cell is a
    PLACE, the map is static data, and resolving (x, y) where the write is
    authored means the runtime never has to read a tilemap back."""
    dst = _u8(ev, "tile", 0)
    src = ev.get("src", 0)
    if _is_expr(src):
        return [_rpn_instr(str(src), cc), ("BKG_TILE_E", [dst])]
    return [("BKG_TILE", [dst, _u8(ev, "src", 0)])]


def _song_idx(ev):
    """The song index for `music_song`. A missing / blank `song` (a music_song
    node that never got a song chosen -- e.g. reaching the engine before the
    studio's songref->index pass) lowers to song 0 rather than crashing on
    ``int("")``."""
    v = ev.get("song", 0)
    if v is None or (isinstance(v, str) and not v.strip()):
        return 0
    return int(v)


def _ev_set_var(ev, cc):
    idx = cc.var_index(ev["var"])
    if "expr" in ev:                       # computed: RPN result -> heap
        return [_rpn_instr(ev["expr"], cc), ("SET_VAR", [idx])]
    return [("SET_CONST", [idx, _u16(ev, "value")])]          # plain literal (compact)


def _ev_if(ev, cc):
    # RPN(cond); IF else; <then>; JUMP end; else: <else>; end:
    else_l, end_l = cc.new_label(), cc.new_label()
    out = [_rpn_instr(ev["cond"], cc), ("IF", [Label(else_l)])]
    for sub in ev.get("then", []):
        out += lower_event(sub, cc)
    out.append(("JUMP", [Label(end_l)]))
    out.append(("__ANCHOR__", [else_l]))
    for sub in ev.get("else", []):
        out += lower_event(sub, cc)
    out.append(("__ANCHOR__", [end_l]))
    return out


def _ev_wait_until(ev, cc):
    """`wait_until` -- BLOCK this thread until `cond` is true.

    top: RPN(cond); IF body; JUMP end; body: WAIT n; JUMP top; end:

    No new opcode: it is the IF/JUMP/WAIT the language already has, wired into
    a backward branch. What it buys is a thread that STAYS ALIVE while it
    waits, which nothing else in the event language could express - `wait`
    guesses a duration and `input_attach` splits the script in two.

    That distinction is load-bearing rather than stylistic. vm.core's timer and
    input attachments are BUSY-GATED (`tmr_ctx` / `in_ctx`, the reference engine's own
    `SCRIPT_TERMINATED` check): a tick that arrives while the last instance is
    still running fires nothing. A script that ends in order to wait therefore
    OPENS that gate. Measured on the shooter conversion: its game-over screen
    waits for Start inside the hazard-spawner's timer script, and with the wait
    lowered as a split the timer re-fired 30 frames later and rained hazards
    behind the "Game Over" text - which the reference ROM, whose `vm_input_wait`
    rewinds the PC and yields, never does.

    `poll` is how many frames to sleep between tests (default 1 = every frame,
    the reference engine's own cadence). It is always at least 1, so the loop can never
    spin inside a single frame however the condition is written - the reason
    this is `wait_until` rather than a general `while`.
    """
    top_l, body_l, end_l = cc.new_label(), cc.new_label(), cc.new_label()
    poll = max(1, _u8(ev, "poll", 1))
    return [
        ("__ANCHOR__", [top_l]),
        _rpn_instr(ev["cond"], cc),
        ("IF", [Label(body_l)]),        # branch-if-FALSE -> sleep and retry
        ("JUMP", [Label(end_l)]),       # true -> fall out of the loop
        ("__ANCHOR__", [body_l]),
        ("WAIT", [poll]),
        ("JUMP", [Label(top_l)]),
        ("__ANCHOR__", [end_l]),
    ]


def _ev_while(ev, cc):
    """`while` -- run `then` for as long as `cond` holds, INSIDE this script.

    top: RPN(cond); IF end; <then>; JUMP top; end:

    The reference engine's `whileScriptValue` shape, and no opcode: `wait_until`'s IF and
    JUMP without its WAIT. That difference is the point. A loop that stays in
    ONE script keeps that script's lock and its place (a scene init that loops
    a menu is still the init, still locked, and what follows the loop still
    runs), where the older lowering - a separate looping thread - could do
    neither.

    It may spin. A body with no waiting event runs until the thread's
    instruction quantum is spent and resumes next frame, exactly as the reference VM's
    `INSTRUCTIONS_PER_QUANT` treats the same loop, so it costs frame time but
    never hangs the frame.

    A constant true `cond` (absent, or a non-zero literal) is the reference engine's
    plain `EVENT_LOOP` and drops the test: body + JUMP."""
    top_l, end_l = cc.new_label(), cc.new_label()
    cond = str(ev.get("cond", "1")).strip()
    out = [("__ANCHOR__", [top_l])]
    forever = cond.isdigit() and int(cond) != 0
    if not forever:
        out += [_rpn_instr(cond, cc), ("IF", [Label(end_l)])]
    for sub in ev.get("then", []):
        out += lower_event(sub, cc)
    out.append(("JUMP", [Label(top_l)]))
    out.append(("__ANCHOR__", [end_l]))
    return out


def _label_name(ev):
    name = str(ev.get("name", "")).strip()
    if not name:
        raise VmError("event %r needs a label name" % ev.get("event"))
    return name


def _ev_label(ev, cc):
    """`label` -- name this point in the script for a `goto` (the reference engine's
    EVENT_DEFINE_LABEL). Scoped to the script; emits no byte."""
    return [("__ANCHOR__", [cc.define_label(_label_name(ev))])]


def _ev_goto(ev, cc):
    """`goto` -- jump to a `label` of the SAME script, forwards or back (GB
    Studio's EVENT_GOTO_LABEL). A name the script does not define is a
    compile error, not a jump to nowhere."""
    return [("JUMP", [Label(cc.goto_label(_label_name(ev)))])]


def _ev_switch(ev, cc):
    # RPN(value); SWITCH n (v0->L0 ..); <default>; JUMP end; L0: <case0> JUMP end; ..; end:
    cases = ev.get("cases", [])
    if not cases:
        raise VmError("switch needs at least one case")
    if len(cases) > 255:
        # the SWITCH count operand is one byte; a bigger table would wrap the
        # count to 0 and the interpreter would execute the table bytes as code
        raise VmError("switch has %d cases (max 255)" % len(cases))
    end_l = cc.new_label()
    labels = [cc.new_label() for _ in cases]
    out = [_rpn_instr(ev["value"], cc)]
    sw = [len(cases)]
    for cs, lab in zip(cases, labels):
        sw.append(_fit(ev, "case value", int(cs["value"]), 16, True))
        sw.append(Label(lab))
    out.append(("SWITCH", sw))
    for sub in ev.get("default", []):        # no match -> fall through here
        out += lower_event(sub, cc)
    out.append(("JUMP", [Label(end_l)]))
    for cs, lab in zip(cases, labels):
        out.append(("__ANCHOR__", [lab]))
        for sub in cs.get("then", []):
            out += lower_event(sub, cc)
        out.append(("JUMP", [Label(end_l)]))
    out.append(("__ANCHOR__", [end_l]))
    return out


def _ev_set_state(ev, cc):
    name = ev["state"]
    if name not in STATES:
        raise VmError("unknown engine state %r" % name)
    sid = STATES[name]
    src = ev["expr"] if "expr" in ev else str(_i(ev, "value"))
    return [_rpn_instr(src, cc), ("SET_STATE", [sid])]


def _button_id(ev):
    b = str(ev["button"]).lower()
    if b not in BUTTONS:
        raise VmError("unknown button %r (a/b/start/select/up/down/left/right)" % b)
    return BUTTONS[b]


def _ev_input_attach(ev, cc):
    # `override` CONSUMES the button (the reference engine's overriding input script): the
    # native player handler does not see it while the attachment is live, which
    # is the only thing that stops a menu cursor built on the PLAYER from also
    # walking on the held d-pad. Rides bit 7 of the button id; absent, the
    # emitted bytes are unchanged.
    btn = _button_id(ev)
    if ev.get("override"):
        btn |= 0x80
    return [("INPUT_ATTACH", [btn, Label(ev["script"])])]


def _ev_input_detach(ev, cc):
    return [("INPUT_DETACH", [_button_id(ev)])]


def _menu_arm(ev, cc):
    """The B-cancel arm a `menu` / `say_choose` with `cancel_b` set emits before
    its MENU: push 1, SET_STATE menu_cancel (a ONE-SHOT latch the open consumes,
    so the blob format is untouched and a menu without it is byte-identical).
    A cancelled menu writes -1 to `var` where a confirm writes the 0-based
    cursor - `pick + 1` is then the reference engine's own 0 / 1..n numbering."""
    if not ev.get("cancel_b"):
        return []
    return [_rpn_instr("1", cc), ("SET_STATE", [STATES["menu_cancel"]])]


def _slot_arm(ev, cc):
    """The SAVE SLOT arm every save event emits before its own op (W7c): push
    the slot, SET_STATE save_slot. A ONE-SHOT latch the operation consumes -
    the `_menu_arm` / `_box_arm` shape - so `SAVE` and `LOAD` keep their empty
    operand lists and a project that only ever uses slot 0 emits nothing at all
    and stays byte-identical.

    Absent or 0 = slot 0, which is what the engine already defaults to. The
    value is VALIDATED against the slot count rather than masked (the operand
    rule): a `slot = 7` is an authoring mistake, and masking it would pick a
    different real slot without a word."""
    n = int(ev.get("slot", 0) or 0)
    if n == 0:
        return []
    if not 0 <= n < SAVE_SLOTS:
        raise VmError("save slot %d is out of range (0..%d)"
                      % (n, SAVE_SLOTS - 1))
    return [_rpn_instr(str(n), cc), ("SET_STATE", [STATES["save_slot"]])]


def _box_arm(ev, cc):
    """The HOLD arm a `text` with `hold` set emits before its UI_TEXT: push n,
    SET_STATE box_hold (a ONE-SHOT latch the open consumes, the `_menu_arm`
    shape, so the blob format is untouched and a box without it is
    byte-identical). The box then stands for n VM frames and closes itself,
    which is the reference engine's `closeWhen: "text"` (an `_overlayWait` with no button
    flag, then `wait_frames`) and the tail of its non-modal box."""
    if not ev.get("hold"):
        return []
    n = _u8(ev, "hold")      # VALIDATED, never masked (the operand rule)
    return [_rpn_instr(str(n), cc), ("SET_STATE", [STATES["box_hold"]])]


def _ev_menu(ev, cc):
    return _menu_arm(ev, cc) + [("MENU", [_menu_blob(ev, cc)])]


def _ev_say_choose(ev, cc):
    # "say then choose" (the reference engine): show the text box, then a modal menu whose
    # picked index lands in `var` -- the paired UI_TEXT + MENU as one event.
    return ([("UI_TEXT", [cc.intern_text(ev["string"])])] + _menu_arm(ev, cc)
            + [("MENU", [_menu_blob(ev, cc)])])


#: Timer slots per program (lockstep vm.core's NTIMERS).
N_TIMERS = 4


def _ev_timer_reset(ev, cc):
    """`timer_reset` -> push the timer id, SET_STATE timer_reset. The id is
    VALIDATED against the four slots rather than masked (the A8 rule)."""
    t = _i(ev, "timer", 0)
    if not 0 <= t < N_TIMERS:
        raise VmError("event 'timer_reset': timer = %d is not a timer slot "
                      "(0..%d)" % (t, N_TIMERS - 1))
    return [_rpn_instr(str(t), cc), ("SET_STATE", [STATES["timer_reset"]])]


def _ev_start_thread(ev, cc):
    """Spawn a thread. With `args` (a list of up to NARGS expressions) the child
    is spawned via THREADN: each arg is pushed in order (arg[0] first) and the
    op pops them in reverse into the child's arg[] cells (VM8 spec §6 op 0x07);
    the child reads them with `arg(n)` in expressions. Without `args` the
    compact THREAD op is kept (byte-identical)."""
    args = ev.get("args")
    if not args:
        return [("THREAD", [Label(ev["script"])])]
    if len(args) > NARGS:
        raise VmError("start_thread passes %d args (max %d, VM8 NARGS)"
                      % (len(args), NARGS))
    out = _rpn_seq(args, cc)
    out.append(("THREADN", [Label(ev["script"]), len(args)]))
    return out


#: `palette_set` layer NAMES, accepted beside the canonical 0/1 so a
#: hand-authored .evt.toml can say what it means. Two layers, because GB
#: Studio's four palette events are two: its UI palette is BACKGROUND slot 7
#: and its emote palette is SPRITE slot 7.
PAL_LAYERS = {"sprite": 0, "background": 1}


def _ev_palette_set(ev, cc):
    """Write one library palette into one hardware palette slot.

    The colours come from the world's [[palette]] library through the
    generated `scenes.set_palette` seam, so the operand is a library INDEX -
    and a library entry is already layer-correct (an importer interns a GB
    Studio palette separately as background and as sprite, because a sprite
    palette maps the authored colours [c0, c0, c1, c3]).

    `layer` is an INT (0 sprite / 1 background), which is what the studio
    catalogue's CHOICE parameters all store; the two NAMES are accepted too,
    the way `actor = "self"` is accepted beside a slot number.
    """
    raw = ev.get("layer", 0)
    if isinstance(raw, str):
        name = raw.strip().lower()
        if name not in PAL_LAYERS:
            raise VmError("palette_set layer must be %s or 0/1, not %r"
                          % (" / ".join(sorted(PAL_LAYERS)), raw))
        layer = PAL_LAYERS[name]
    else:
        layer = _u8(ev, "layer", 0)
        if layer > 1:
            raise VmError("palette_set layer must be 0 (sprite) or 1 "
                          "(background), got %d" % layer)
    slot = _u8(ev, "slot", 0)
    if slot > 7:
        raise VmError("palette_set slot must be 0..7 (got %d)" % slot)
    return [("PAL_SET", [layer, slot, _u8(ev, "pal", 0)])]


EVENTS = {
    "actor_activate":  lambda ev, cc: [("A_ACTIVATE", [_actor(ev), _i(ev, "tile"),
                                                        _i(ev, "x"), _i(ev, "y")])],
    "actor_set_pos":   lambda ev, cc: _ev_actor_pos(ev, cc, "A_SET_POS", "A_SET_POS_E"),
    "actor_set_speed": lambda ev, cc: [("A_SPEED", [_actor(ev), _i(ev, "speed")])],
    "actor_move_to":   _ev_actor_move_to,
    "actor_move":      lambda ev, cc: _move_opts(ev) + _ev_actor_pos(
        ev, cc, "A_MOVE_START", "A_MOVE_START_E"),
    "actor_set_clip":  lambda ev, cc: [("A_SET_CLIP", [_actor(ev), _i(ev, "kind")])],
    # `loop` false sets bit 7 of the state operand: play the state ONCE and hold
    # its last frame. The reference engine never asks for it (see the ISA note), so the
    # default keeps every converted blob byte-identical.
    "actor_set_anim_state": lambda ev, cc: [
        ("A_SET_ANIM_STATE", [_actor(ev), ((_i(ev, "state", 0) & 0x7F)
                                           | (0 if _i(ev, "loop", 1)
                                              else 0x80))])],
    # ...and its release: back to the movement-derived state.
    "actor_clear_anim_state": lambda ev, cc: [
        ("A_CLEAR_ANIM_STATE", [_actor(ev)])],
    # Replace an actor's collision BOX at run time (the reference engine's
    # EVENT_ACTOR_SET_COLLISION_BOX): size and offset inside the drawn
    # sprite, all u8 and validated. w = 0 retires the box.
    "actor_set_box": lambda ev, cc: [
        ("A_SET_BOX", [_actor(ev), _u8(ev, "w", 8), _u8(ev, "h", 8),
                       _u8(ev, "ox", 0), _u8(ev, "oy", 0)])],
    # End the thread whose join HANDLE is `var` (the reference engine's
    # EVENT_THREAD_STOP): the handle a `handle_next` / `handle` bound.
    "thread_stop": lambda ev, cc: [("THREAD_STOP", [cc.var_index(ev["var"])])],
    # Store an actor's facing (0 down / 1 up / 2 left / 3 right) in a
    # variable (the reference engine's EVENT_ACTOR_GET_DIRECTION).
    "actor_get_dir": lambda ev, cc: [
        ("A_GET_DIR", [_actor(ev), cc.var_index(ev["var"])])],
    # Restart a timer's countdown without starting a stopped one (the reference engine's
    # EVENT_TIMER_RESTART) - a state write, `timer_set` is not it.
    "timer_reset": _ev_timer_reset,
    # Hide or show EVERY sprite (the reference engine's EVENT_HIDE_SPRITES /
    # EVENT_SHOW_SPRITES). Persistent across scenes, as the reference VM's flag is.
    # Start an actor's On Update again (the reference engine's EVENT_ACTOR_START_UPDATE).
    "actor_start_update": lambda ev, cc: [("A_START_UPDATE", [_actor(ev)])],
    # Pause / resume the scene type's update (the reference engine's
    # EVENT_SCENE_UPDATE_PAUSE / _RESUME).
    "scene_update_pause": lambda ev, cc: [
        _rpn_instr("1" if _i(ev, "on", 1) else "0", cc),
        ("SET_STATE", [STATES["scene_update_paused"]])],
    "sprites_visible": lambda ev, cc: [
        _rpn_instr("0" if _i(ev, "on", 1) else "1", cc),
        ("SET_STATE", [STATES["sprites_hidden"]])],
    # Bring a retired actor back IN PLACE (no tile/x/y): the reference engine's own
    # EVENT_ACTOR_ACTIVATE. `actor_activate` above is the SPAWN form a
    # hand-written shell uses to place a slot.
    "actor_reactivate": lambda ev, cc: [("A_REACTIVATE", [_actor(ev)])],
    # WAIT for an actor's native auto-move to land (or be cancelled by a
    # deactivate) -- pairs with actor_move for the waitable-move idiom.
    "actor_await_move": lambda ev, cc: [("A_AWAIT_MOVE", [_actor(ev)])],
    "actor_deactivate": lambda ev, cc: [("A_DEACTIVATE", [_actor(ev)])],
    "actor_set_group": lambda ev, cc: [("A_SET_GROUP", [_actor(ev), _u8(ev, "group", 2)])],
    "actor_set_hp":  lambda ev, cc: [("A_SET_HP", [_actor(ev), _u16(ev, "hp", 1)])],
    "actor_damage":  lambda ev, cc: [("A_DAMAGE", [_actor(ev), _u16(ev, "amount", 1)])],
    "actor_set_dir": lambda ev, cc: [("A_SET_DIR", [_actor(ev), _i(ev, "dir", 0) & 3])],
    # the reference engine's ACTOR_FLAG_COLLISION: an actor BLOCKS the player unless it is
    # cleared (its EVENT_ACTOR_COLLISIONS_DISABLE / _ENABLE pair).
    "actor_set_collision": lambda ev, cc: [
        ("A_SET_COLLISION", [_actor(ev), 1 if _i(ev, "on", 1) else 0])],
    # Animation the SCRIPT drives rather than the animator: pin one frame, and
    # set the advance rate. 255 is a reserved "no pin" sentinel in the runtime,
    # so a frame operand is clamped to 0..254.
    "actor_set_frame": _ev_actor_set_frame,
    "actor_set_anim_speed": lambda ev, cc: [
        ("A_SET_ANIM_SPEED", [_actor(ev), _u8(ev, "speed", 15)])],
    # The player's facing is engine STATE (the id `player_dir()` reads), so this
    # is a push + SET_STATE rather than an opcode of its own.
    "player_set_dir": lambda ev, cc: [
        _rpn_instr(str(_i(ev, "dir", 0) & 3), cc),
        ("SET_STATE", [STATES["player_dir"]])],
    "player_bounce": lambda ev, cc: [
        ("PLAYER_BOUNCE", [min(2, max(0, _i(ev, "height", 0)))])],
    "player_knockback": lambda ev, cc: [("PLAYER_KNOCKBACK", [])],
    # Show / hide the PLAYER's sprite - the reference engine's Hide/Show Actor aimed at
    # `$self$` in a scene or trigger script, where `$self$` IS the player. Its
    # flag stops the DRAW only, so this is not the player-less room teardown
    # (which a script could not undo). `actor_visible` below is the same flag
    # on a POOL actor, and a separate op - see the ISA note for why.
    "player_visible": lambda ev, cc: [
        ("PLAYER_VISIBLE", [1 if _i(ev, "on", 1) else 0])],
    # ... and the same for a POOL actor (the reference engine's EVENT_ACTOR_HIDE / _SHOW
    # aimed at a named actor). Hiding does NOT retire it: it keeps updating,
    # keeps colliding and keeps answering On Interact, which is what makes an
    # invisible INTERACTION HOTSPOT work - the reference engine's usual way to put a
    # script on a spot on the floor.
    "actor_visible": lambda ev, cc: [
        ("A_VISIBLE", [_actor(ev), 1 if _i(ev, "on", 1) else 0])],
    # TYPEWRITER reveal speed (the reference engine's EVENT_TEXT_SET_ANIMATION_SPEED):
    # 0 = instant (the engine default), 1..7 = one char per 1/1/2/4/8/16/32/64
    # display frames. `fastforward` = A/B held draws the rest at once.
    "text_speed": lambda ev, cc: [
        ("TEXT_SPEED", [min(7, max(0, _i(ev, "speed", 1))),
                        1 if ev.get("fastforward", True) else 0])],
    # The per-character blip (its EVENT_TEXT_SET_SOUND_EFFECT): a square tone
    # per reveal step. freq 0 turns it off.
    "text_blip": lambda ev, cc: [
        ("TEXT_BLIP", [_u16(ev, "freq", 0),
                       min(255, max(1, _i(ev, "frames", 2)))])],
    # Kill the actor's long-lived On Update thread (the reference engine's
    # EVENT_ACTOR_STOP_UPDATE): a guard that has finished patrolling, an NPC
    # frozen for a cutscene. Reaches vm.entity through the core.set_stop_update
    # seam, so a game with no attached On Update slots links none of it.
    "actor_stop_update": lambda ev, cc: [("A_STOP_UPDATE", [_actor(ev)])],
    # Shove an actor `tiles` tiles away from the player, along the PLAYER's
    # facing, stopping at the first solid cell (the reference engine's EVENT_ACTOR_PUSH:
    # 2 tiles for a nudge, 100 for "slide until it hits something"). The op
    # resolves the landing cell and hands the actor to the native auto-move, so
    # a pushed block SLIDES; waitable, like the actor move-to it mirrors.
    "actor_push": lambda ev, cc: [
        ("A_PUSH", [_actor(ev), min(255, max(1, _i(ev, "tiles", 2)))])],
    # Pop an emote bubble over an actor and WAIT for it (the reference engine's
    # EVENT_ACTOR_EMOTE is waitable, so `emote; say "..."` reads in that order).
    # `emote` is an index into the project's emote art, in the order the
    # generated `emotes` module uploads it.
    "actor_emote": lambda ev, cc: [
        ("A_EMOTE", [_actor(ev), _u8(ev, "emote", 0)])],
    # Reseed the host RNG (the reference engine's EVENT_RNG_SEED). A state WRITE, not an
    # opcode: the seed is an expression, and its default (`game_time()`) is the
    # entropy source -- how many frames the player took to reach this point,
    # which is what the reference engine's own DIV-register randomize approximates.
    "rng_seed": lambda ev, cc: [
        _rpn_instr(ev.get("expr") or "game_time()", cc),
        ("SET_STATE", [STATES["rand_seed"]])],
    # Do other actors stop the player? The reference engine's
    # EVENT_ACTOR_COLLISIONS_DISABLE / _ENABLE aimed at the PLAYER (its
    # actors[0]); a state write for the same reason rng_seed is one - the actor
    # opcode's operand is a pool slot and the player is not in the pool.
    "player_set_collision": lambda ev, cc: [
        _rpn_instr("1" if _i(ev, "on", 1) else "0", cc),
        ("SET_STATE", [STATES["player_collide"]])],
    # The player's movement speed (px per VM frame) and animation MASK. State
    # writes rather than opcodes, for the same reason the two above are: GB
    # Studio aims its actor events at the player (its actors[0]) and our actor
    # ops take a pool slot the player does not occupy.
    "player_set_speed": lambda ev, cc: [
        _rpn_instr(str(max(1, _i(ev, "speed", 2))), cc),
        ("SET_STATE", [STATES["player_speed"]])],
    "player_set_anim_speed": lambda ev, cc: [
        _rpn_instr(str(_u8(ev, "speed", 15)), cc),
        ("SET_STATE", [STATES["player_anim_speed"]])],
    # the reference engine's platformer BLANK state and its own gravity - what a PIT
    # raises. State writes for the same reason as the pair above: the reference engine
    # writes ENGINE FIELDS here, which have no actor operand at all.
    "player_blank": lambda ev, cc: [
        _rpn_instr("1" if _i(ev, "on", 1) else "0", cc),
        ("SET_STATE", [STATES["player_blank"]])],
    "player_blank_grav": lambda ev, cc: [
        _rpn_instr(str(_u8(ev, "gravity", 0)), cc),
        ("SET_STATE", [STATES["player_blank_grav"]])],
    "hud_show":      lambda ev, cc: [("HUD_SHOW", [1 if _i(ev, "on", 1) else 0])],
    # VALIDATED, not masked (the A8 rule): WAIT's operand is a u8 and
    # `frames = 300` used to emit a WAIT of 44 - a different legal-looking
    # value, silently. Nothing in the repo authors one that long today
    # (measured 2026-09-16: the largest is 255), but a converted the reference engine
    # wait is `seconds * 60 / VM_FRAMES_PER_LCD`, so a ten-second pause at
    # scale 2 is 300 and would have converted to a 44-frame one.
    "wait":            lambda ev, cc: [("WAIT", [_u8(ev, "frames")])],
    "wait_until":      _ev_wait_until,
    "idle":            lambda ev, cc: [("IDLE", [])],
    "lock":            lambda ev, cc: [("LOCK", [])],
    "unlock":          lambda ev, cc: [("UNLOCK", [])],
    "stop":            lambda ev, cc: [("STOP", [])],
    "start_thread":    _ev_start_thread,
    "call":            lambda ev, cc: [("CALL", [Label(ev["script"])])],
    "ret":             lambda ev, cc: [("RET", [])],
    # joins (VM8 §3/§11): heap[var] mirrors a thread's liveness (1 = running,
    # 0 = done). `handle` binds the CURRENT thread; `handle_next` arms a one-shot
    # latch applied to this thread's next start_thread child (parent watches child).
    "handle":          lambda ev, cc: [("HANDLE", [cc.var_index(ev["var"])])],
    "handle_next":     lambda ev, cc: [("HANDLE_NEXT", [cc.var_index(ev["var"])])],
    "set_self":        lambda ev, cc: [("SELF", [_actor(ev)])],
    "player_setpos":   _ev_player_setpos,
    # The waitable player WALK (A_MOVE_TO's twin). `mode` is the reference engine's
    # moveType, which decides the AXIS ORDER, not the destination. COMPUTED
    # coordinates take the `_E` variant, which latches the popped target on
    # first entry (see the ISA note) - so a relative walk
    # (`player_x() + 16`) blocks until it lands exactly as a literal one.
    "player_move_to":  _ev_player_move_to,
    "text":            lambda ev, cc: (_box_arm(ev, cc)
                                       + [("UI_TEXT", [cc.intern_text(ev["string"])])]),
    # change_scene is assembler sugar for RAISE 2, room, x, y (VM8 spec §6/§10)
    "change_scene":    _ev_change_scene,
    # reset (RAISE 1): kill every thread, reset scene UI, restart `main`;
    # the heap survives (spec §10) -- the bytecode-reachable game-over path.
    "reset":           lambda ev, cc: [("RAISE", [1, 0, 0, 0])],
    "raise":           lambda ev, cc: [("RAISE", [_i(ev, "code"), _i(ev, "a", 0),
                                                 _i(ev, "b", 0), _i(ev, "c", 0)])],
    "menu":            _ev_menu,
    "fade_out":        lambda ev, cc: [("FADE", [0, _i(ev, "frames", 16)])],
    "fade_in":         lambda ev, cc: [("FADE", [1, _i(ev, "frames", 16)])],
    "timer_set":       lambda ev, cc: [("TIMER_SET", [_i(ev, "timer"), _i(ev, "period"),
                                                      Label(ev["script"])])],
    "timer_stop":      lambda ev, cc: [("TIMER_STOP", [_i(ev, "timer")])],
    "set_var":         _ev_set_var,
    "if":              _ev_if,
    "switch":          _ev_switch,
    "while":           _ev_while,
    "label":           _ev_label,
    "goto":            _ev_goto,
    "set_state":       _ev_set_state,
    "say_choose":      _ev_say_choose,
    "camera_lock":     lambda ev, cc: [_rpn_instr(str(_i(ev, "x")), cc), ("SET_STATE", [STATES["camera_x"]]),
                                       _rpn_instr(str(_i(ev, "y")), cc), ("SET_STATE", [STATES["camera_y"]])],
    # RESUME FOLLOWING, and optionally on one axis only / with backtracking
    # blocked. The value is the reference engine's `camera_settings` byte (state 4): bits
    # 0/1 are "follow x" / "follow y" and bits 2..5 are its `preventScroll`
    # directions. Every field absent = 3, follow both, which is what this event
    # has always emitted (it used to spell the same instruction as a `0` in the
    # OPPOSITE polarity, so the byte changed and the instruction count did not).
    #
    # The author picks an AXIS and ticks directions; nobody composes a mask by
    # hand, and the four preventScroll bits are deliberately not one field -
    # the reference engine's own event has them as four independent checkboxes.
    "camera_release":  lambda ev, cc: [_rpn_instr(str(_cam_settings(ev)), cc),
                                       ("SET_STATE", [STATES["camera_lock"]])],
    # ONE camera property (the reference engine's EVENT_CAMERA_PROPERTY_SET): the dead
    # zone and the follow offset, per axis, as four consecutive states. The
    # VALUE is an expression, as the reference engine's is a ScriptValue.
    "camera_prop":     lambda ev, cc: [
        _rpn_instr(str(ev.get("value", 0)), cc),
        ("SET_STATE", [STATES["camera_deadzone_x"] + _cam_prop(ev)])],
    # The follow camera's CLAMP RECTANGLE, in world PIXELS, as the reference engine's
    # EVENT_CAMERA_SET_BOUNDS computes it (`cameraSetBoundsToScriptValues`):
    # the min pair is the rectangle's own origin and the max pair is
    # `origin + size - screen`, so a rectangle exactly one screen tall PINS
    # that axis. The importer does that arithmetic (it knows the screen and
    # the units); this is four plain state writes, and each takes an
    # EXPRESSION, so a bound may be computed.
    "camera_bounds":   lambda ev, cc: [
        _rpn_instr(str(_i(ev, "x0")), cc), ("SET_STATE", [STATES["camera_min_x"]]),
        _rpn_instr(str(_i(ev, "x1")), cc), ("SET_STATE", [STATES["camera_max_x"]]),
        _rpn_instr(str(_i(ev, "y0")), cc), ("SET_STATE", [STATES["camera_min_y"]]),
        _rpn_instr(str(_i(ev, "y1")), cc), ("SET_STATE", [STATES["camera_max_y"]])],
    "camera_pan":      lambda ev, cc: [("CAM_MOVE_TO", [_i(ev, "x"), _i(ev, "y"), _i(ev, "step", 2)])],
    # `axis` is the reference engine's CAMERA_SHAKE_X (1) / _Y (2) mask and `wait` makes
    # the op BLOCK, both through the one-shot SHAKE_OPTS latch - emitted only
    # when the event asks for something other than this engine's historical
    # vertical, non-blocking shake, so an existing program is byte-identical.
    # `frames` counts DISPLAY frames (the reference engine measures against sys_time), the
    # same clock the curtain and the typewriter use.
    "shake":           lambda ev, cc: (
        ([("SHAKE_OPTS", [_i(ev, "axis", 0), _i(ev, "wait", 0)])]
         if (_i(ev, "axis", 0) or _i(ev, "wait", 0)) else [])
        + [("SHAKE", [_i(ev, "frames", 20), _i(ev, "amp", 2)])]),
    "shmup_scroll":    lambda ev, cc: [("SHMUP_SCROLL", [_i(ev, "pace", 1), _i(ev, "dir", 2)])],
    "scroll_bg":       lambda ev, cc: [("SCROLL_BG", [_u8(ev, "vx", 0, signed=True),
                                                      _u8(ev, "vy", 0, signed=True)])],
    "input_attach":    _ev_input_attach,
    "input_detach":    _ev_input_detach,
    "set_player_hit":  lambda ev, cc: [("SET_PLAYER_HIT", [Label(ev["script"])])],
    # W7h - a hUGE `6xy` cell runs a script. `slot` is the reference engine's routine
    # number 0..3; the effect's own parameter arrives as `arg(0)`. hUGE ONLY
    # (`[audio] gb = "huge"`); the portable songs.toml path has no effect
    # column at all, which is D9 and a separate question.
    "music_routine":   lambda ev, cc: [("MUSIC_ROUTINE",
                                        [_u8(ev, "slot", 0),
                                         Label(ev["script"])])],
    # The window OVERLAY CURTAIN (the reference engine's ui.c): `row` 0 covers the whole
    # screen, 18 is off the bottom. `overlay_show` puts it there at once;
    # `overlay_move_to` slides it and WAITS, one row per ui_time_masks[speed]+1
    # frames. Not a fade - the palettes stay bright and the scene is revealed
    # row by row.
    "overlay_show":    lambda ev, cc: [("OVERLAY_SHOW", [_u8(ev, "row", 0)])],
    "bkg_tile":        _ev_bkg_tile,
    "palette_set":     _ev_palette_set,
    "overlay_hide":    lambda ev, cc: [("OVERLAY_SHOW", [18])],
    "overlay_move_to": lambda ev, cc: [("OVERLAY_MOVE_TO",
                                        [_u8(ev, "row", 18),
                                         _u8(ev, "speed", 1)])],
    "projectile":      _ev_projectile,
    "save":            lambda ev, cc: _slot_arm(ev, cc) + [("SAVE", [])],
    "load":            lambda ev, cc: _slot_arm(ev, cc) + [("LOAD", [])],
    # the reference engine's EVENT_CLEAR_DATA / EVENT_PEEK_DATA. `peek` reads ONE heap
    # cell out of a slot without loading it, and writes 0 into `var` when that
    # slot holds no save (its lowering's `_ifConst` tail, folded into the op).
    "save_clear":      lambda ev, cc: _slot_arm(ev, cc) + [("DATA_CLEAR", [])],
    "save_peek":       lambda ev, cc: _slot_arm(ev, cc) + [
        ("DATA_PEEK", [cc.var_index(ev["src"]), cc.var_index(ev["var"])])],
    "sound_sfx":       lambda ev, cc: [("SFX", [_i(ev, "id")])],
    "sound_tone":      lambda ev, cc: [("TONE", [_i(ev, "freq"), _i(ev, "frames")])],
    "sound_stop":      lambda ev, cc: [("SND_STOP", [])],
    "music_play":      lambda ev, cc: [("MUSIC_PLAY", [Label(ev["script"])])],
    "music_stop":      lambda ev, cc: [("MUSIC_STOP", [])],
    "music_tone":      lambda ev, cc: [("MUSIC_TONE", [_i(ev, "freq"), _i(ev, "frames")])],
    "music_song":      lambda ev, cc: [("MUSIC_SONG", [_song_idx(ev)])],
    "music_pause":     lambda ev, cc: [("MUSIC_PAUSE", [])],
    "music_resume":    lambda ev, cc: [("MUSIC_RESUME", [])],
    "music_mute":      lambda ev, cc: [("MUSIC_MUTE", [_i(ev, "mask", 0)])],
}


def _menu_blob(ev, cc):
    """(dest_var, row, count, id0, id1, ...) -- a modal choice writing the picked
    0-based index to `var`. Options are strings interned like textbox text -
    intern_text, so a `$var$` token in an option resolves to a heap cell and
    render_choice prints the live value (the reference engine's "Rest ($gold$ Gold)")."""
    ids = [cc.intern_option(s) for s in ev["options"]]
    if not 1 <= len(ids) <= 255:
        raise VmError("menu needs 1..255 options")
    # No authored row: bottom-anchor the menu inside the SHORTEST screen any
    # target may have. A blob is target-neutral, so this has to hold to the
    # smallest console exactly like the RPN depth check holds to the smallest
    # stack (MIN_SCREEN_ROWS is the Lynx/PCE 12; the GB family has 18). The old
    # fixed 15 was a GB number: on a 12-row screen the whole menu -- box, frame
    # and options -- landed BELOW the display and silently drew nothing, and
    # the row is the one piece of box geometry the engine does not own (the
    # dialogue box is engine-positioned at SCREEN_ROWS - 4, which is why only
    # menus were affected). Costs the GB family nothing: it re-anchors the menu
    # onto the bottom of the WINDOW overlay and ignores this row entirely.
    default_row = max(1, MIN_SCREEN_ROWS - 1 - len(ids))
    try:
        row = int(ev.get("row", default_row))
    except (TypeError, ValueError):
        raise VmError("menu row must be a number, got %r" % ev.get("row"))
    if not 0 <= row <= 255:                      # the row is a u8 operand; a bare
        raise VmError("menu row %d out of range (0..255)" % row)   # bytes() would raise
    dest = cc.var_index(ev["var"])
    return bytes([dest, row, len(ids)] + ids)


def lower_event(ev, cc):
    name = ev.get("event")
    if name not in EVENTS:
        raise VmError("unknown event %r" % name)
    return EVENTS[name](ev, cc)
