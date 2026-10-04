"""mosaik_scenes.transpile.core - `transpile()`: analyse the world, then
emit the five sections in order."""
from .context import analyse
from . import (emit_entities, emit_head, emit_maps, emit_palettes,
               emit_tail)
from .forked import _transpile_scene_forked


def transpile(world, base_dir, _mark=False, _force=None):
    """world: parsed TOML dict; base_dir: dir paths in the TOML are relative to.
    Returns the mosaik module source string (deterministic).

    `_mark`/`_force` are the internal hooks for per-console SCENE filtering (review
    2.1, Stage 2): when a `[[scene]]` carries a `platforms` allow-list the module body
    is FORKED per console (`_transpile_scene_forked`). `_mark` brackets the forkable
    body with a sentinel line so the driver can slice it; `_force` overrides the flags
    that must stay uniform across branches (else the module `export` list, which can't
    be conditional, would differ). A normal call (both None/False) is byte-identical."""
    if _force is None and not _mark:
        forked = _transpile_scene_forked(world, base_dir)
        if forked is not None:
            return _hot(forked, base_dir)
    c = analyse(world, base_dir, _mark, _force)
    L = []
    emit_head.emit(c, L)        # header, consts, kinds, tileset
    emit_maps.emit(c, L)        # maps, paint(), collision
    emit_palettes.emit(c, L)    # the colour tier
    emit_entities.emit(c, L)    # objects / doors / triggers
    emit_tail.emit(c, L)        # anim_tick() + the export list
    text = "\n".join(L) + "\n"
    return text if (_mark or _force is not None) else _hot(text, base_dir)


def _hot(text, base_dir):
    """A Lynx overlay build keeps the per-frame accessors resident (`hot`,
    mosaik.hotmark); every other project's text is unchanged."""
    from mosaik.hotmark import mark_hot
    return mark_hot(text, "scenes", base_dir)
