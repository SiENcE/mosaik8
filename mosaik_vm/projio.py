"""mosaik_vm.projio - Project-file helpers (locate world.toml, read kinds, sprite sheets)."""
import os
import re

from .isa import decode_blob


_CODE_ARRAY_RE = re.compile(
    r"const\s+CODE\s*:\s*array\s*\[\s*u8\s*,\s*(\d+)\s*\]\s*=\s*\[(.*?)\]", re.S)


def scan_scripts_module(text):
    """The opcodes + RPN tokens + engine-state ids a generated `scripts.mos` can
    execute.

    Returns ``(opcode_names, rpn_token_names, state_ids)``, or
    ``(None, None, None)`` when the module has no readable blob or a blob fails
    to decode. The build turns the result into compile-time flags so
    `lib/vm/core.mos` folds away the dispatch arms this program cannot reach;
    None means "assume everything", which is the byte-identical fallback.

    A per-console filtered project emits SEVERAL `CODE` arrays inside
    `if platform` forks. We do not try to pick the live one -- the union over
    all of them is always a superset of what any single target runs, so the
    fold stays safe (it just keeps a few arms a given console would not need).
    """
    ops, toks, states = set(), set(), set()
    found = False
    for m in _CODE_ARRAY_RE.finditer(text or ""):
        body = m.group(2)
        try:
            blob = bytes(int(v, 0) for v in
                         (p.strip() for p in body.split(",")) if v)
        except ValueError:
            return None, None, None
        if len(blob) != int(m.group(1)):     # declared length must match
            return None, None, None
        o, t, s = decode_blob(blob)
        if o is None:
            return None, None, None
        ops |= o
        toks |= t
        states |= s
        found = True
    return (ops, toks, states) if found else (None, None, None)

try:
    import toml
except ImportError:  # pragma: no cover
    toml = None


def _find_world(root):
    """The project's world path (single-file or split dir), or None."""
    for p in (os.path.join(root, "world.toml"),
              os.path.join(root, "assets", "world.toml"),
              os.path.join(root, "world"),
              os.path.join(root, "assets", "world")):
        if os.path.isfile(p) or (os.path.isdir(p) and
                                 os.path.isfile(os.path.join(p, "world.toml"))):
            return p
    return None
def _project_toml(name):
    return ('[project]\n'
            'name = "%s"\n'
            'version = "0.1.0"\n'
            '# A VM8 project: a static shell (src/main.mos) + event lists\n'
            '# (scripts/*.evt.toml) compiled to src/scripts.mos by mosaik_vm.\n'
            'target_platforms = ["gameboy", "gameboy_color", "lynx"]\n\n'
            '[source]\n'
            'folder = "src/"\n\n'
            '[build]\n'
            'output_dir = "build"\n'
            '# On by default for VM8 projects: every VM game links the whole\n'
            '# lib/vm surface, and declaration-level tree-shaking is what fits the\n'
            '# Lynx samples. It only\n'
            '# changes the generated C, so composed output stays behaviour-identical.\n'
            'shake_exports = true\n' % name)


def _read_world_kinds(root):
    """{kind name -> id} from the project's world (single-file or split dir;
    the studio scaffold puts the world under assets/)."""
    for wp in (os.path.join(root, "world.toml"),
               os.path.join(root, "assets", "world.toml"),
               os.path.join(root, "world", "world.toml"),
               os.path.join(root, "assets", "world", "world.toml")):
        if os.path.isfile(wp):
            return dict(toml.load(wp).get("kinds", {}))
    return {}


def _project_sprite_pngs(root):
    """Absolute paths of the project's registered sprite sheets ([assets] sprites)."""
    mt = os.path.join(root, "mosaik.toml")
    if not os.path.isfile(mt):
        return []
    sprites = toml.load(mt).get("assets", {}).get("sprites", [])
    return [os.path.join(root, p) for p in sprites]
