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


def first_wait(events, scripts, depth=0):
    """The first event (in run order) of `events` that waits for the screen,
    looking into bodies and called scripts; None if there is none."""
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        name = ev.get("event")
        if name in WAITS:
            return name
        if name in NO_WALK:
            continue
        for kids in _children(ev):
            hit = first_wait(kids, scripts, depth)
            if hit:
                return hit
        if name in ("call", "start_thread") and depth < MAX_DEPTH:
            hit = first_wait(scripts.get(ev.get("script"), []), scripts, depth + 1)
            if hit:
                return hit
    return None


def owns_fade(events, scripts):
    """True when the init's first waiting event, in the reference engine's
    walk, is a `fade_in`: the init shows the room itself.

    `events` is the init's event list, `scripts` every script by name (for
    `call` / `start_thread`). The first top-level event that contains a
    waiting event decides, by what that waiting event is - the same answer
    as the first waiting event of the whole walk."""
    return first_wait(events, scripts) == "fade_in"
