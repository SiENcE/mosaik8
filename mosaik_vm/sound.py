"""mosaik_vm.sound - SFX library loader: the studio-only `sound {ref}` -> beep/tone ops."""
import os

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None


# --------------------------------------------------------------------------
# The SOUND-EFFECT LIBRARY (a sibling `sounds.toml`): reusable NAMED effects
# ({freq, frames}), referenced from a script by the studio-only `sound` event
# (audio plan Stage 1). Like `say`, `sound` is NOT a bytecode
# op (EVENTS is untouched, so the lockstep holds) -- it is LOWERED here, at load
# time, to a plain `sound_tone` op. So a project authored with the studio's SFX
# LIBRARY builds with bare mosaik8, and the studio + CLI share one expander. The 5
# CANNED effects stay the separate `sound_sfx(id)` event; this library is the
# game's OWN named effects.
# --------------------------------------------------------------------------
def snd_play_events(entry):
    """The op(s) that PLAY one library effect. A def -> a `sound_tone` (freq,
    frames). A missing/empty def (or freq <= 0) -> [] (a dangling ref is skipped)."""
    if not entry:
        return []
    freq = int(entry.get("freq", 0))
    frames = int(entry.get("frames", 0))
    if freq <= 0:
        return []
    return [{"event": "sound_tone", "freq": freq, "frames": frames}]


def snd_expand_events(events, defs):
    """Resolve every `sound` node (a library ref) to a plain `sound_tone` op,
    recursing into if/switch bodies. New lists/dicts are returned (the authored
    scripts, which carry the `sound` ref, are left intact)."""
    out = []
    for ev in (events or []):
        if not isinstance(ev, dict):
            out.append(ev)
            continue
        if ev.get("event") == "sound":
            out.extend(snd_play_events(defs.get(str(ev.get("ref", "")).strip())))
            continue
        ev = dict(ev)
        for key in ("then", "else", "default"):
            if isinstance(ev.get(key), list):
                ev[key] = snd_expand_events(ev[key], defs)
        if isinstance(ev.get("cases"), list):
            ev["cases"] = [dict(c, then=snd_expand_events(c.get("then", []), defs))
                           if isinstance(c, dict) else c for c in ev["cases"]]
        out.append(ev)
    return out


def snd_expand_scripts(scripts, defs):
    """Apply :func:`snd_expand_events` to every script (a copy)."""
    if not defs:
        return scripts
    return [dict(s, events=snd_expand_events(s.get("events", []), defs))
            for s in (scripts or [])]


def load_sound_defs(scripts_dir):
    """Load a `sounds.toml` (the SFX library) from a scripts dir, or {} if absent.
    Each def: {freq, frames}."""
    if toml is None:
        return {}
    path = os.path.join(scripts_dir, "sounds.toml")
    if not os.path.isfile(path):
        return {}
    data = toml.load(path)
    out = {}
    for name, d in (data.get("defs", {}) or {}).items():
        if isinstance(d, dict):
            out[str(name)] = {"freq": int(d.get("freq", 0)),
                              "frames": int(d.get("frames", 0))}
    return out
