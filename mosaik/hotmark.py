"""`hot` on the per-frame functions of a GENERATED module, for a Lynx overlay
build.

On the Lynx, `[build] code_banks` makes each listed module's functions a cart
OVERLAY, read from the cart when a call finds another one loaded (~13 ms a
KB). A function the frame calls must therefore stay resident, which the source
says with `hot` (FunctionDecl.hot). The runtime under `lib/` carries its own
annotations; the modules the TOOLCHAIN writes (`rooms`, `scenes`, `clips`) get
theirs here, from one list per generator, measured on the showcase RPG (the
functions a steady walking / dialogue / battle frame calls).

Emitted ONLY for a project that targets the Lynx AND lists `[build]
code_banks` -- the one case where `hot` changes the build -- so every other
project's generated text stays byte-identical (and `hot` is a no-op on every
other console anyway).
"""
import os
import re

try:
    import tomllib as _toml_reader
except ImportError:          # pragma: no cover - Python < 3.11
    _toml_reader = None

#: Per generated module: the functions a steady frame calls. A regex per name
#: (the rooms tick handlers are one per scene TYPE).
GENERATED_HOT = {
    "rooms": (r"tick_\w+", "tile_at", "stream2", "stream2px"),
    "scenes": ("map_tile", "collision_at", "tile_pal", "replace_tile",
               "rt_pal", "rt_pal_off", "anim_tick"),
    "clips": ("sel", "frame", "count", "period", "flip"),
}


def lynx_overlays(project_dir) -> bool:
    """Does the project owning `project_dir` (a project root, or a world at
    most two levels below it) build Lynx code overlays?"""
    d = os.path.abspath(project_dir or ".")
    for _ in range(3):
        mp = os.path.join(d, "mosaik.toml")
        if os.path.isfile(mp):
            if _toml_reader is None:
                return False
            try:
                with open(mp, "rb") as f:
                    cfg = _toml_reader.load(f)
            except Exception:   # noqa: BLE001 - a broken toml is the build's error
                return False
            targets = {str(p).strip().lower() for p in
                       ((cfg.get("project") or {}).get("target_platforms") or [])}
            return ("lynx" in targets
                    and bool((cfg.get("build") or {}).get("code_banks")))
        d = os.path.dirname(d)
    return False


def mark_hot(text, module, project_dir):
    """`text` with `hot ` in front of the module's per-frame function
    declarations, when the project builds Lynx overlays; `text` unchanged
    otherwise. A declaration already pinned (`bank(N)`) or already `hot` is
    left alone."""
    names = GENERATED_HOT.get(module)
    if not names or not lynx_overlays(project_dir):
        return text
    alt = "|".join("(?:%s)" % n for n in names)
    rx = re.compile(r"^(\s*)((?:local\s+)?function\s+(?:%s)\s*\()" % alt, re.M)
    return rx.sub(r"\1hot \2", text)
