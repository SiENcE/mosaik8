"""mosaik_vm.rooms.config - the constants, the studio.toml/mosaik.toml
readers and the small shape helpers the emitters share.

Split out of the former single-file rooms.py; every name is re-exported
from the package, so `mosaik_vm.rooms.<name>` is unchanged."""
import os

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None


# --------------------------------------------------------------------------
# rooms.mos -- the generated SCENE-TYPE DISPATCH + world wiring (the glue.mos
# sibling for the WORLD half). The starter shell used to carry load_room /
# solid_at / the handler pick inline, which (a) made the scene-type dispatch
# hand-edited code, and (b) statically referenced EVERY vm.player handler, so
# even a pure top-down game linked the platformer (and now shmup) code -- dead
# bytes tree-shaking can't reclaim, because the shell referenced them.
#
# rooms.mos is REGENERATED from the world (like glue.mos from the audio config):
# it emits ONLY the branches for the scene types the world actually uses, and
# imports ONLY the packs the world needs (vm.door iff doors exist, vm.trigger
# iff triggers, vm.entity iff any behavior slot, vm.canim+clips iff animations)
# -- so an unused handler / pack is never referenced and `[build] shake_exports`
# drops it. Player tuning (studio.toml [player]) is emitted as consts, so
# walk/run/jump/gravity/double-jump/box size are DATA, not shell edits.
# --------------------------------------------------------------------------

_PLAYER_DEFAULTS = {
    "walk": 1, "run": 1, "jump": 4, "gravity": 1, "max_fall": 4,
    "air_jumps": 0, "width": 8, "height": 8, "tile": 0,
    "shmup_speed": 1,
    # climb speed on a LADDER (px per VM frame). Only ever emitted for a world
    # that paints ladder cells, so a project without one is byte-identical.
    "climb": 1,
    # variable-height jump feel (all 0 = the classic fixed single jump, byte-identical):
    "jump_hold": 0,     # variable jump height: frames the jump sustains while A held
    "coyote": 0,        # ledge-jump grace (frames) after leaving the ground
    "jump_buffer": 0,   # frames an early jump press is remembered before landing
    # momentum horizontal movement (main.lua accel/air-control); run_accel 0 = the
    # classic instant run (byte-identical). run/`prun` stays the TOP speed.
    # ledge the player climbs automatically when blocked (px, 0 = off). A
    # reference-engine import sets it because slopes have no collision type here and
    # import as SOLID, which turns a rising path into a wall.
    "step_up": 0,
    # KNOCKBACK (the platformer plugin's KNOCKBACK_STATE). All zero = the
    # player_knockback op is a no-op, so a project that never configures one is
    # byte-identical. The reference engine holds these as ENGINE FIELDS, which is why they
    # are configured once here rather than passed per call.
    "knockback_x": 0,       # px/frame away from the facing
    "knockback_y": 0,       # px/frame upward
    "knockback_frames": 0,  # frames the pad stays locked out
    "run_accel": 0,     # accel toward top speed (1/16 px/frame^2); 0 = instant
    "run_decel": 0,     # decel to a stop when no input
    "air_control": 16,  # air-control fraction /16 (16 = full accel airborne)
    # wall slide + wall jump (main.lua); wall_slide 0 = off (byte-identical):
    "wall_slide": 0,    # max fall speed while wall-sliding (px/frame)
    "wall_jump_x": 0,   # wall-jump horizontal launch (px/frame)
    "wall_jump_y": 0,   # wall-jump vertical launch (px/frame)
}

# The shmup scene's DEFAULT auto-scroll pace (frames per 1px) -- a genre default in
# the generated rooms.mos, NOT a per-player tuning field (background scrolling is
# decoupled from the player: a scene retimes / pauses it with the `shmup_scroll`
# event in its On Init, exactly as any scene scrolls its backdrop with `scroll_bg`).
_SHMUP_DEFAULT_PACE = 2

# scene types with a NATIVE player handler (adventure has none -> topdown walk;
# pointnclick walks the topdown SETUP and swaps only the per-frame move for the
# cursor one, which is why it shares the dispatch arm below - W7j)
_PLAYER_TYPES = ("topdown", "platform", "adventure", "shmup", "pointnclick")

# The 32-tile hardware background: a room wider than this cannot be painted in
# one go and must COLUMN-STREAM (engine.scroll). Rows are capped by the same
# 32-tile tilemap, and only columns stream, so a taller room needs scroll2d
# (not generated yet -- generate_rooms refuses it with a clear message).
BKG_TILES = 32

# ...except in HEIGHT on the SMS / Game Gear, whose name table is 32x**28** and
# whose vertical scroll register wraps at 224 (`bkg.set_tiles` already refuses
# rows >= 28 - writing past the name table overruns the SAT). A room of 29..32
# rows therefore cannot be painted resident there and has to stream through
# engine.scroll2d like a taller one. Only the ROAM threshold moves: the
# refusals below stay at BKG_TILES, so no project that builds today is newly
# refused, and the fork is emitted only when the project actually targets one
# of those two consoles (rooms.mos stays byte-identical everywhere else).
BKG_ROWS_SMSGG = 28


def _meta_w(info, kind_expr):
    """The metasprite WIDTH in 8x8 tiles for `kind_expr`, as mosaik source.

    A world that mixes sprite sizes gets it per kind from the clips module;
    a uniform world bakes the one size the whole pool shares. Used by the
    per-cell sprite palette, which has to know the fan's shape."""
    clips = info.get("clips") or {}
    if clips.get("per_kind_size"):
        return "clips.meta_w(%s)" % kind_expr
    return str(clips.get("meta_w", 1))


def _meta_h(info, kind_expr):
    clips = info.get("clips") or {}
    if clips.get("per_kind_size"):
        return "clips.meta_h(%s)" % kind_expr
    return str(clips.get("meta_h", 1))


#: `bank(0)` on the generated collision probes: PIN them to the home bank.
#: `[build] code_banks` banks a whole MODULE, but hotness is per FUNCTION -
#: these three run 30+ times a frame (vm.player's box scan) while the rest of
#: `rooms` is cold room-load code that MUST bank or bank 0 overflows by ~6.5 KB.
#: Banked, each probe pays two extra cross-bank trampolines; measured at
#: 2,440 cycles per probe, which is 28.8 probes to an LCD frame.
#: A no-op without code banking, so a project that does not opt in is
#: byte-identical.
HOT_PIN = "bank(0) "


def _dim_w(idx, uniform):
    """The scene-width expression: one const on a uniform world, an indexed
    table otherwise (`SCENE_W` is only emitted when scenes differ).

    These stay DIRECT array reads, unlike the OBJ/DOOR/TRIG tables that went
    through `scenes` accessors (bank0-optimization-plan O2). Measured: routing
    them through an accessor - and caching the current room's dims here, which
    would also drop an index from the per-collision-test path - moved ZERO bytes
    of resident image, because `scenes.paint` and `scenes.warm` read the same
    two arrays and are themselves RESIDENT (they hold the streaming seam reads,
    and `SWITCH_ROM` cannot execute from a switchable bank). The arrays are
    pinned by their own module; nothing rooms.mos does can unpin them.
    """
    return "scenes.MAP_W" if uniform else "scenes.SCENE_W[%s]" % idx


def _dim_h(idx, uniform):
    return "scenes.MAP_H" if uniform else "scenes.SCENE_H[%s]" % idx


def _rowlim(info):
    """The room-height limit in TILES: the per-console `BKG_ROWS` const when the
    project targets the SMS/Game Gear, the flat 32 otherwise (byte-identical)."""
    return "BKG_ROWS" if info.get("smsgg_rows") else str(BKG_TILES)


def _pylim(info):
    """The same limit in PIXELS, for the runtime setup fork."""
    return "BKG_PY" if info.get("smsgg_rows") else "BKG_PX"


def _wants_boxes(info):
    """Does this world need per-actor collision BOXES registered at room load?

    An actor has ONE box (`vm.actor.set_box`), the reference engine's authored sprite
    `bounds`, and three things collide against it: the player-blocking test
    (`[scenes] solid_actors`), the native projectile hit test, and `actor_push`.
    Either use is enough to want it - a shooter whose actors are walk-through
    still needs its 16x16 enemies to be hittable over their whole body, and
    without the registration `vm.projectile` falls back to the 8x8 default that
    made only the corner tile of a big sprite shootable. Registering a box does
    NOT make an actor solid: `blocked()` is wired only under `solid_actors`.
    """
    return bool(info.get("solid_actors") or info.get("uses_projectile"))


# The VM8 runtime slot pools, and the sizes `lib/vm` ships with. A project
# raises them with `[build] actor_pool` / `trigger_pool` (mosaik.toml), which
# feeds the same numbers to the compiler as integer defines -- so the refusal
# below and the actual array sizes cannot drift.
DEFAULT_ACTOR_POOL = 8
DEFAULT_TRIGGER_POOL = 8


# The VISIBLE screen in TILE CELLS per console -- what `SCREEN_COLS` /
# `SCREEN_ROWS` resolve to in the generated C. A room SMALLER than this leaves
# the rest of the picture holding the previous one, which is what
# `clear_outside` blanks; the table is here so the decision (emit the clear at
# all) can be made from the project's own `target_platforms` while the emitted
# code stays target-neutral.
_SCREEN_CELLS = {
    "gameboy": (20, 18), "gameboy_color": (20, 18),
    "megaduck": (20, 18), "analogue_pocket": (20, 18),
    "gamegear": (20, 18),          # 160x144 visible out of a 32x28 name table
    "sms": (32, 24),               # 256x192 -- the one that bites
    "nes": (32, 30),
    "lynx": (20, 12), "pce": (32, 28),
}
# Consoles whose background is a tilemap this can blank (see CLEAR_GUARD).
_CLEAR_CONSOLES = set(_SCREEN_CELLS) - {"lynx"}


def _target_screens(root):
    """The visible screen sizes of the project's target consoles, in cells."""
    mp = os.path.join(root, "mosaik.toml")
    names = []
    if toml is not None and os.path.isfile(mp):
        try:
            proj = toml.load(mp).get("project", {}) or {}
            names = [str(p).strip().lower()
                     for p in (proj.get("target_platforms") or [])]
            # The build accepts aliases (`pc_engine`, `tg16`, ...): read the
            # console they name, or that target was silently left out.
            from mosaik.platforms import PLATFORM_ALIASES
            names = [PLATFORM_ALIASES.get(n, n) for n in names]
        except Exception:
            names = []
    if not names:
        names = ["gameboy"]
    return [_SCREEN_CELLS[n] for n in names
            if n in _SCREEN_CELLS and n in _CLEAR_CONSOLES]


def _targets_smsgg(root):
    """Whether `mosaik.toml [project] target_platforms` includes the Master
    System or Game Gear.

    rooms.mos is target-NEUTRAL, so per-console work sits behind an `if
    platform` fork and costs the other consoles nothing -- but a project that
    will never build for those two should not carry the fork at all, which is
    what keeps every existing project's rooms.mos byte-identical.
    """
    mp = os.path.join(root, "mosaik.toml")
    if toml is None or not os.path.isfile(mp):
        return False
    try:
        proj = toml.load(mp).get("project", {}) or {}
    except Exception:
        return False
    return bool({"sms", "gamegear"}
                & {str(p).strip().lower() for p in
                   (proj.get("target_platforms") or [])})


def _targets_pce(root):
    """Whether `mosaik.toml [project] target_platforms` names the PC Engine
    (under any of its aliases). Same rule as `_targets_smsgg`: the PCE forks
    in rooms.mos are carried only by a project that builds for it, so every
    other project's rooms.mos stays byte-identical."""
    mp = os.path.join(root, "mosaik.toml")
    if toml is None or not os.path.isfile(mp):
        return False
    try:
        proj = toml.load(mp).get("project", {}) or {}
    except Exception:
        return False
    from mosaik.platforms import PLATFORM_ALIASES
    return "pce" in {PLATFORM_ALIASES.get(str(p).strip().lower())
                     for p in (proj.get("target_platforms") or [])}


def load_pool_config(root):
    """`mosaik.toml [build] actor_pool` / `trigger_pool`, defaulted.

    Read here (rather than passed in) so every caller -- the studio, the GB
    Studio importer, a bare `python -m mosaik_vm` -- sees the same limits the
    build will compile, without threading a parameter through all of them.
    """
    pools = {"actor": DEFAULT_ACTOR_POOL, "trigger": DEFAULT_TRIGGER_POOL}
    mp = os.path.join(root, "mosaik.toml")
    if toml is not None and os.path.isfile(mp):
        try:
            build = toml.load(mp).get("build", {}) or {}
        except Exception:
            build = {}
        for key, name in (("actor", "actor_pool"), ("trigger", "trigger_pool")):
            if name in build:
                try:
                    pools[key] = max(1, int(build[name]))
                except (TypeError, ValueError):
                    pass
    return pools


def _load_studio(root):
    """`studio.toml` as a dict, or {} when the project has none.

    A MALFORMED one is a hard error, not an empty dict. Every room-load knob
    lives in here - the player tuning, the colour tier, the fades, per-room
    sprite residency, the clip table - so swallowing a parse failure silently
    regenerates a rooms.mos with all of them at their DEFAULTS: a 6 KB-smaller
    module, a player at walk speed 1, no fade, no palettes, no residency, and
    nothing anywhere saying why. (Found by rewriting a studio.toml with
    PowerShell's `Set-Content -Encoding utf8`, which prepends a BOM that
    tomllib rejects.)"""
    sp = os.path.join(root, "studio.toml")
    if not os.path.isfile(sp):
        return {}
    try:
        return toml.load(sp) or {}
    except Exception as exc:
        from ..isa import VmError
        raise VmError(
            "%s could not be parsed (%s). Every room-load knob lives in it, so "
            "generating rooms.mos from the defaults would silently drop the "
            "player tuning, colour, fades and sprite residency. Note a UTF-8 "
            "BOM is a parse error here - write the file without one."
            % (sp, exc))


def load_player_config(root):
    """studio.toml [player] over the defaults (the movement-tuning DATA the
    generated rooms.mos bakes into consts)."""  # noqa: D401
    cfg = dict(_PLAYER_DEFAULTS)
    raw = _load_studio(root).get("player", {}) or {}
    for k in cfg:
        if k in raw:
            try:
                cfg[k] = max(0, int(raw[k]))
            except (TypeError, ValueError):
                pass
    return cfg


def _cells(collision):
    """A scene's painted collision as a flat cell sequence (it is a list of
    ROWS in world.toml, but may already be flat)."""
    if not collision:
        return ()
    if isinstance(collision[0], (list, tuple)):
        return [v for row in collision for v in row]
    return collision

