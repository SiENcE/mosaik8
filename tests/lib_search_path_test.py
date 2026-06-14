#!/usr/bin/env python3
"""Shared lib/ search path for framework imports (game-framework-plan.md).

The framework modules in `lib/game/` can be `import`ed without vendoring: an
`import "game.camera"` that isn't found next to the importing file resolves
under the library search roots (a project's `[lib] paths`, the `MOSAIK_LIB`
env var, and the default `lib/` next to the tool). Vendored copies still win
(resolved relative to the importer first). This test drives the resolver in
mosaik8.py directly (no toolchain needed).
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik8 import MosaikBuilder

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIB_GAME = os.path.join(ROOT, "lib", "game")


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# A minimal game that imports a framework module by its library name.
GAME = (
    'module "main" {\n'
    '    import "platform.input"\n'
    '    import "game.pad"\n'
    '    function main() { game.pad.update() }\n'
    '    export main\n'
    '}\n'
)


def main():
    print("Shared lib/ search path")
    print("=" * 50)
    ok = True
    builder = MosaikBuilder()

    # The default root is lib/ next to the tool; it exists and holds game/.
    roots = builder._lib_search_roots(ROOT)
    ok &= check("default lib/ root is discovered",
                any(os.path.basename(r) == "lib" for r in roots))

    with tempfile.TemporaryDirectory() as tmp:
        # (1) A standalone game that imports "game.pad" with NO vendored copy
        # resolves to lib/game/pad.mos via the default root, transitively
        # pulling nothing else (pad has no non-stdlib imports).
        main_mos = os.path.join(tmp, "main.mos")
        write(main_mos, GAME)
        closure = builder._resolve_import_closure(main_mos, roots)
        ok &= check("import resolves to lib/game/pad.mos without vendoring",
                    closure is not None
                    and any(os.path.abspath(c)
                            == os.path.join(LIB_GAME, "pad.mos") for c in closure))

        # (2) Transitive library imports are followed: the topdown template
        # pulls pad + camera + collision + topdown from lib/.
        tmpl = os.path.join(tmp, "g.mos")
        with open(os.path.join(LIB_GAME, "topdown_template.mos"),
                  encoding="utf-8") as f:
            write(tmpl, f.read())
        closure = builder._resolve_import_closure(tmpl, roots)
        names = {os.path.basename(c) for c in (closure or [])}
        ok &= check("transitive lib imports are followed (template -> 4 modules)",
                    {"pad.mos", "camera.mos", "collision.mos",
                     "topdown.mos"} <= names)

        # (3) A vendored copy WINS over the lib root (resolved relative to the
        # importer first), so existing vendored projects are unaffected.
        vend_dir = os.path.join(tmp, "proj")
        write(os.path.join(vend_dir, "main.mos"), GAME)
        write(os.path.join(vend_dir, "game", "pad.mos"),
              '-- local copy\nmodule "game.pad" {\n'
              '    import "platform.input"\n'
              '    function update() { }\n    export update\n}\n')
        closure = builder._resolve_import_closure(
            os.path.join(vend_dir, "main.mos"), roots)
        lib_pad = os.path.join(LIB_GAME, "pad.mos")
        ok &= check("vendored copy wins over the lib root",
                    closure is not None
                    and any(os.path.abspath(c)
                            == os.path.join(vend_dir, "game", "pad.mos")
                            for c in closure)
                    and not any(os.path.abspath(c) == lib_pad for c in closure))

        # (4) MOSAIK_LIB adds a root. Point it at a temp lib holding a
        # differently-named module and resolve it.
        extra_lib = os.path.join(tmp, "extralib")
        write(os.path.join(extra_lib, "game", "widget.mos"),
              'module "game.widget" { function w() { } export w }\n')
        user = os.path.join(tmp, "u.mos")
        write(user, 'module "main" { import "game.widget"\n'
                    '    function main() { game.widget.w() } export main }\n')
        old = os.environ.get("MOSAIK_LIB")
        os.environ["MOSAIK_LIB"] = extra_lib
        try:
            roots2 = builder._lib_search_roots(ROOT)
            closure = builder._resolve_import_closure(user, roots2)
        finally:
            if old is None:
                del os.environ["MOSAIK_LIB"]
            else:
                os.environ["MOSAIK_LIB"] = old
        ok &= check("MOSAIK_LIB env adds a search root",
                    closure is not None
                    and any(os.path.basename(c) == "widget.mos"
                            for c in closure))

        # (5) A missing import is still a clear error (returns None), not a
        # silent fallthrough.
        bad = os.path.join(tmp, "bad.mos")
        write(bad, 'module "main" { import "game.nope"\n'
                   '    function main() { } export main }\n')
        ok &= check("missing import fails cleanly",
                    builder._resolve_import_closure(bad, roots) is None)

    print("=" * 50)
    print("All lib search-path checks passed" if ok else "SOME CHECKS FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
