"""mosaik_scenes.cli - `python -m mosaik_scenes world.toml -o scenes.mos`."""
import argparse
import os
import sys

from .base import SceneError
from .loaders import load_world, load_world_dir
from .transpile import transpile


def main(argv=None):
    ap = argparse.ArgumentParser(description="Transpile a world TOML to a mosaik scenes module.")
    ap.add_argument("world", help="the world .toml, or a split-per-resource "
                                  "world directory (world.toml + scenes/ + doors.toml)")
    ap.add_argument("-o", "--output", help="output .mos (default: stdout)")
    args = ap.parse_args(argv)
    try:
        world, base_dir = load_world(args.world)
        src = transpile(world, base_dir)
    except (SceneError, KeyError, FileNotFoundError) as e:
        print("scene transpile error: %s" % e, file=sys.stderr)
        return 1
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(src)
        print("wrote %s (%d scenes)" % (args.output, len(world.get("scene", []))))
    else:
        sys.stdout.write(src)
    return 0
