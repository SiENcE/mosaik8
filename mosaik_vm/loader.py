"""mosaik_vm.loader - Load an event-script dir (expanding dialogue/sound/song refs) + compile it."""
import os

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None

from .compiler import Compiler
from .dialogue import dlg_expand_scripts, load_dialogue_defs
from .emotes import emote_expand_scripts, emote_sheets
from .isa import VmError
from .songs import song_expand_scripts, song_names
from .sound import load_sound_defs, snd_expand_scripts


def _emote_names(scripts_dir):
    """The project's emote stems in id order, read from studio.toml (which sits
    beside scripts/). Empty - and so a no-op - for a project with no emotes, or
    when the file is missing or unreadable: an emote NAME that cannot be
    resolved falls back to index 0, exactly as an unknown song name does, and a
    broken studio.toml is already a hard error on the path that matters
    (`rooms._load_studio`)."""
    root = os.path.dirname(os.path.abspath(scripts_dir))
    sp = os.path.join(root, "studio.toml")
    if not os.path.isfile(sp):
        return []
    try:
        return emote_sheets(toml.load(sp))
    except Exception:
        return []


def _apply_actor_pool(root):
    """Point `isa.ACTOR_POOL` at `root`'s `[build] actor_pool` (default 8).

    Always sets it -- including back to the default -- so compiling project B
    after project A in one process never inherits A's wider pool.
    """
    from . import isa
    n = 8
    mp = os.path.join(root, "mosaik.toml")
    if os.path.isfile(mp):
        try:
            n = int((toml.load(mp).get("build", {}) or {}).get("actor_pool", 8))
        except Exception:
            n = 8
    isa.set_actor_pool(n)


def load_scripts(path):
    """Load one *.evt.toml, or every *.evt.toml under a folder, into the ordered
    script list the Compiler wants. A file may hold several [[script]] tables.
    A sibling `dialogue.toml` (the library) is loaded too and its `say` refs are
    expanded to plain UI ops (so a library-authored project builds directly); a
    sibling `sounds.toml` (the SFX library) likewise expands `sound` refs, and a
    sibling `songs.toml` (the song library) resolves `music_song` NAME refs to
    indices."""
    if toml is None:
        raise VmError("the `toml` package is required")
    files = []
    scripts_dir = path if os.path.isdir(path) else os.path.dirname(path)
    # A literal actor id is validated against the pool the ROM will actually
    # have, so pick up this project's `[build] actor_pool` before lowering any
    # event. scripts/ sits directly under the project root.
    _apply_actor_pool(os.path.dirname(os.path.abspath(scripts_dir)))
    if os.path.isdir(path):
        for fn in sorted(os.listdir(path)):
            if fn.endswith(".evt.toml"):
                files.append(os.path.join(path, fn))
    else:
        files = [path]
    scripts = []
    for fn in files:
        data = toml.load(fn)
        for s in data.get("script", []):
            scripts.append(s)
    # Keep 'main' first (the boot entry) if present; else declaration order.
    scripts.sort(key=lambda s: 0 if s.get("name") == "main" else 1)
    scripts = dlg_expand_scripts(scripts, load_dialogue_defs(scripts_dir))
    scripts = snd_expand_scripts(scripts, load_sound_defs(scripts_dir))
    scripts = song_expand_scripts(scripts, song_names(scripts_dir))
    # ...and an `actor_emote` may NAME its bubble instead of carrying the raw
    # index into `[emotes] sheets`. The list lives in studio.toml, one level up
    # from scripts/, and a project with no emote art resolves nothing.
    return emote_expand_scripts(scripts, _emote_names(scripts_dir))


def compile_path(path):
    import os
    prog = Compiler().compile(load_scripts(path))
    # VWF rides the project's own font (see compiler.project_uses_vwf): `path`
    # is the scripts FOLDER, so the project root is its parent.
    from .compiler import project_font_map, project_uses_vwf
    root = os.path.dirname(os.path.abspath(path))
    prog._vwf = project_uses_vwf(root)
    # ...and so does its recode table (`<font>.json` `mapping`).
    prog.apply_font_map(project_font_map(root))
    return prog
