"""A fresh VM8 project in a temp dir, for ROM tests that need a GENERATED
world (rooms.mos) rather than a sample's hand-wired shell.

`build(world, scripts, platform)` writes the scaffold, a slim shell (scenes +
rooms, no glue), the given `world.toml` and `scripts/main.evt.toml`, runs the
three generators (scripts, scenes, rooms) and builds one console. Returns a
`Fixture` with the temp dir (the caller removes it), the three generator
results and the ROM path (None when the build failed).

Not a test module (no `_test` suffix): `run_all.py` does not run it.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from collections import namedtuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

Fixture = namedtuple("Fixture", "tmp root scripts scenes build rom")

ROM_EXT = {"gameboy": ".gb", "gameboy_color": ".gbc", "sms": ".sms", "gamegear": ".gg"}

SHELL = """module "main" {
    import "platform.video"
    import "graphics.sprite"
    import "graphics.bkg"
    import "scenes"
    import "scripts"
    import "vm.core"
    import "rooms"

    const SPRITE: array[u8, 16] = [
        0x00, 0x00, 0x7E, 0x7E, 0x7E, 0x7E, 0x7E, 0x7E,
        0x7E, 0x7E, 0x7E, 0x7E, 0x7E, 0x7E, 0x00, 0x00
    ]

    function main() {
        core.font_preload()
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        sprite.set_data(0, 1, SPRITE)
        core.boot(scripts.fetch, scripts.render_text, scripts.ENTRY_main)
        rooms.start(rooms.START)
        video.enable_lcd()
        video.show_background()
        video.show_sprites()
        core.run()
    }

    export main
}
"""


def grid(rows):
    """A TOML array-of-arrays for a `map` / `collision` layer."""
    return "[" + ", ".join("[" + ", ".join(map(str, r)) + "]" for r in rows) + "]"


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _run(*args):
    return subprocess.run([sys.executable] + list(args), capture_output=True, text=True,
                          cwd=ROOT, encoding="utf-8", errors="replace")


def build(world, scripts, platform="gameboy", extra_build="", files=None, debug=False):
    """Scaffold, generate and build one console. `extra_build` is appended
    under mosaik.toml's `[build]` table (one `key = value` per line);
    `files` ({project-relative path: text}) are written before the
    generators run (a `scripts/heap.toml`, say); `debug` builds with
    `--debug`, so the build folder gets a fresh `.noi` symbol file."""
    import mosaik_vm
    from mosaik_vm.rooms import generate_rooms
    tmp = tempfile.mkdtemp(prefix="vm_fixture_")
    root = os.path.join(tmp, "p")
    os.makedirs(root)
    mosaik_vm.scaffold_project(root, "fixture")
    if extra_build:
        toml = os.path.join(root, "mosaik.toml")
        with open(toml, encoding="utf-8") as f:
            text = f.read()
        _write(toml, text.replace("[build]\n", "[build]\n" + extra_build.strip() + "\n", 1))
    _write(os.path.join(root, "src", "main.mos"), SHELL)
    _write(os.path.join(root, "world.toml"), world)
    _write(os.path.join(root, "scripts", "main.evt.toml"), scripts)
    for rel, text in (files or {}).items():
        _write(os.path.join(root, *rel.split("/")), text)
    shutil.copy(os.path.join(ROOT, "projects", "vm-clipdemo", "tiles.png"),
                os.path.join(root, "tiles.png"))
    g = _run("-m", "mosaik_vm", os.path.join(root, "scripts"),
             "-o", os.path.join(root, "src", "scripts.mos"))
    sc = _run("-m", "mosaik_scenes", os.path.join(root, "world.toml"),
              "-o", os.path.join(root, "src", "scenes.mos"))
    generate_rooms(root)
    r = _run(os.path.join(ROOT, "mosaik8.py"), "build", "--platform", platform,
             *(["--debug"] if debug else []), root)
    out = os.path.join(root, "build", platform)
    ext = ROM_EXT.get(platform, ".gb")
    roms = [os.path.join(out, f) for f in os.listdir(out) if f.endswith(ext)] \
        if os.path.isdir(out) else []
    return Fixture(tmp, root, g, sc, r, roms[0] if roms else None)
