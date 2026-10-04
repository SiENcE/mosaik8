"""mosaik_vm.dialogue - Dialogue library loader: the studio-only `say {ref}` -> plain text ops."""
import os

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None


# --------------------------------------------------------------------------
# The DIALOGUE LIBRARY (a sibling `dialogue.toml`): reusable named text +
# choices, referenced from a script by the `say` event. `say` is NOT a bytecode
# op (EVENTS is untouched, so the lockstep holds) -- it is LOWERED here, at load
# time, to a plain UI op (`text`, or `say_choose`/`menu` for a choice def). So a
# project authored with the studio's dialogue library builds with bare mosaik8,
# and the studio + the CLI share one expander.
# --------------------------------------------------------------------------
def _dlg_ident(s):
    out = "".join(c if (c.isalnum() or c == "_") else "_" for c in str(s)).strip("_").lower()
    if not out or out[0].isdigit():
        out = "d_" + out
    return out


def dlg_say_events(entry, ref, hold=None):
    """The UI event(s) that SHOW one library def. A plain def -> a `text` op (its
    text may carry `$var$`); a CHOICE def (it has `choices`) -> a `say_choose`
    (text + a modal menu writing the picked index to the def's result var) or a
    bare `menu` when it has no text. A missing/empty def -> [] (a dangling ref is
    skipped).

    `hold` is the SAY SITE's own timed-box frame count (§5.3 id 31), not a
    property of the def: the same line of writing can be a normal box in one
    script and a self-closing card in another, so it rides the `say` node and
    is copied onto the `text` this builds. A CHOICE def ignores it - a menu
    waits for a pick, and there is nothing to time out."""
    if not entry:
        return []
    text = str(entry.get("text", "")).rstrip("\n")
    choices = [str(c) for c in (entry.get("choices") or []) if str(c).strip()]
    if choices:
        var = entry.get("var") or (_dlg_ident(ref) + "_choice")
        ev = {"event": "say_choose" if text else "menu",
              "var": var, "options": choices}
        if text:
            ev["string"] = text
        return [ev]
    if not text:
        return []
    ev = {"event": "text", "string": text}
    if hold:
        ev["hold"] = hold
    return [ev]


def dlg_expand_events(events, defs):
    """Resolve every `say` node (a library ref) to a plain UI op, recursing into
    if/switch bodies. New lists/dicts are returned (the authored scripts, which
    carry the `say` ref, are left intact)."""
    out = []
    for ev in (events or []):
        if not isinstance(ev, dict):
            out.append(ev)
            continue
        if ev.get("event") == "say":
            out.extend(dlg_say_events(defs.get(str(ev.get("ref", "")).strip()),
                                      ev.get("ref"), ev.get("hold")))
            continue
        ev = dict(ev)
        for key in ("then", "else", "default"):
            if isinstance(ev.get(key), list):
                ev[key] = dlg_expand_events(ev[key], defs)
        if isinstance(ev.get("cases"), list):
            ev["cases"] = [dict(c, then=dlg_expand_events(c.get("then", []), defs))
                           if isinstance(c, dict) else c for c in ev["cases"]]
        out.append(ev)
    return out


def dlg_expand_scripts(scripts, defs):
    """Apply :func:`dlg_expand_events` to every script (a copy)."""
    if not defs:
        return scripts
    return [dict(s, events=dlg_expand_events(s.get("events", []), defs))
            for s in (scripts or [])]


def load_dialogue_defs(scripts_dir):
    """Load a `dialogue.toml` (the library) from a scripts dir, or {} if absent.
    Each def: {text, choices, var}."""
    if toml is None:
        return {}
    path = os.path.join(scripts_dir, "dialogue.toml")
    if not os.path.isfile(path):
        return {}
    data = toml.load(path)
    out = {}
    for name, d in (data.get("defs", {}) or {}).items():
        if isinstance(d, dict):
            out[str(name)] = {"text": str(d.get("text", "")),
                              "choices": list(d.get("choices", []) or []),
                              "var": str(d.get("var", "")) or None}
    return out
