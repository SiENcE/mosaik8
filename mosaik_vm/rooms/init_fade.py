"""Does a scene's On Init own the room's fade-in? The reference engine's rule.

The reference engine fades a room in with an ordinary Fade In event that its
compiler INJECTS into the scene's init: before the first top-level event that
is, or contains at any depth (if / switch bodies, a called script, a thread's
body, at most five scripts deep), an event that must wait until the screen is
visible - a wait, a move, a text box, a menu, a shake, a fade. Everything
before that point runs under black, so an instant change an init makes (a
replaced tile, a hidden actor, a moved player) is never seen happening. When
the first such event IS a Fade In, the init is MANUAL: nothing is injected and
the room stays black until that Fade In runs.

Our room load ramps the fade in itself, inside `load_room`, and the init's
first instruction runs only after it, so an init that fades the room in
itself got the ramp AND its own fade (a flash), and an importer could not
place the reference engine's fade where the reference engine puts it. A
scene whose init owns the fade (this module says so) gets no ramp: the room
is held black until the init's `fade_in`. Every other scene ramps as before,
so a world with no such init is byte-identical.

`WAITS` is the reference engine's "wait until after the init fade" set, named
in our events and judged by what OUR event does: `camera_lock` and
`camera_release` are instant state writes here (the reference's flagged Camera
Lock pans first; its unflagged Camera Set Lock lowers to the same write), so
neither stops the walk. `NO_WALK` are the events whose script runs LATER (an
input or timer handler, a music routine), which the reference engine does not
look into either.

One thing the reference's walk does not ask and ours must: whether the
`fade_in` is REACHED. A `call` returns only from a `sub` script; a plain one
ends in STOP (the compiler's implicit terminator), so it ends the CALLER's
thread, and so does a `stop` / `change_scene` / `reset` / `raise`. A fade_in
after such an event never runs, and a room held black for it would stay black
for good (an imported init that invokes an actor's On Interact and then fades,
2026-10-24). The walk ends there: no fade is owned and the room ramps.
"""

WAITS = frozenset({
    "wait", "idle", "wait_until",
    "actor_move", "actor_move_to", "actor_await_move", "player_move_to",
    "actor_push", "actor_emote",
    "camera_pan", "shake",
    "fade_in", "fade_out",
    "text", "say_choose", "menu",
    "overlay_move_to", "projectile",
})
NO_WALK = frozenset({"input_attach", "timer_set", "music_routine"})
MAX_DEPTH = 5


def _children(ev):
    for key in ("then", "else", "default"):
        kids = ev.get(key)
        if isinstance(kids, list):
            yield kids
    for case in ev.get("cases") or []:
        if isinstance(case, dict) and isinstance(case.get("then"), list):
            yield case["then"]


#: Events that end the thread on every path through them.
ENDS = frozenset({"stop", "change_scene", "reset", "raise"})
_END = object()   # walk result: the thread ends before any waiting event


def _walk(events, scripts, returns, depth):
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        name = ev.get("event")
        if name in WAITS:
            return name
        if name in NO_WALK:
            continue
        if name in ENDS:
            return _END
        bodies = list(_children(ev))
        hits = [_walk(kids, scripts, returns, depth) for kids in bodies]
        for hit in hits:
            if hit is not None and hit is not _END:
                return hit
        # Every path ends only when there is no fall-through: an else / default.
        covered = isinstance(ev.get("else"), list) or isinstance(ev.get("default"), list)
        if hits and covered and all(h is _END for h in hits):
            return _END
        if name == "start_thread" and depth < MAX_DEPTH:
            hit = _walk(scripts.get(ev.get("script"), []), scripts, returns, depth + 1)
            if hit is not None and hit is not _END:
                return hit   # a new thread: its end is not the caller's
        if name == "call":
            target = ev.get("script")
            if depth < MAX_DEPTH:
                hit = _walk(scripts.get(target, []), scripts, returns, depth + 1)
                if hit is not None:
                    return hit
            if target not in returns:
                return _END   # a plain script's STOP ends the caller's thread
    return None


def first_wait(events, scripts, returns=()):
    """The first event (in run order) of `events` that waits for the screen,
    looking into bodies and called scripts; None if there is none, or if the
    thread ends before one (see the module note). `returns` names the `sub`
    scripts, the only ones a `call` comes back from."""
    hit = _walk(events, scripts, frozenset(returns), 0)
    return None if hit is _END else hit


def owns_fade(events, scripts, returns=()):
    """True when the init's first waiting event, in the reference engine's
    walk, is a `fade_in` the thread REACHES: the init shows the room itself.

    `events` is the init's event list, `scripts` every script by name (for
    `call` / `start_thread`), `returns` the names of the `sub` scripts. The
    first top-level event that contains a waiting event decides, by what that
    waiting event is - the same answer as the first waiting event of the whole
    walk."""
    return first_wait(events, scripts, returns) == "fade_in"
