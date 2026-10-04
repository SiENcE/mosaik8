"""mosaik_vm.cli - Command-line entry (`python -m mosaik_vm new <dir>`)."""
import argparse
import json
import os
import sys

from .instruments import generate_instruments, load_instrument_defs
from .loader import compile_path
from .scaffold import scaffold_project
from .songs import generate_songs, load_song_defs


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _main_new(argv):
    ap = argparse.ArgumentParser(prog="python -m mosaik_vm new",
                                 description="Scaffold a new VM8 project.")
    ap.add_argument("dir", help="the project directory to create")
    ap.add_argument("--name", help="project name (default: the dir name)")
    args = ap.parse_args(argv)
    written = scaffold_project(args.dir, args.name)
    print("scaffolded VM8 project at %s:" % args.dir)
    for p in written:
        print("  +", os.path.relpath(p, args.dir))
    print("build it: python mosaik8.py build %s" % args.dir)
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "new":
        return _main_new(argv[1:])

    ap = argparse.ArgumentParser(description="Compile VM8 event lists to bytecode.")
    ap.add_argument("input", help="a *.evt.toml file or a folder of them")
    ap.add_argument("-o", "--output", help="the scripts.mos to write (default: stdout)")
    ap.add_argument("--vms", help="also write the .vms text assembly here")
    ap.add_argument("--map", help="also write the scripts.map.json debug map here")
    args = ap.parse_args(argv)

    prog = compile_path(args.input)
    mos = prog.to_scripts_mos()
    if args.output:
        with open(args.output, "w") as f:
            f.write(mos)
        print("wrote %s (%d code bytes, %d scripts, %d strings)"
              % (args.output, len(prog.code), len(prog.order), len(prog.strings)))
        # A sibling songs.toml -> the driver's src/songs.mos, beside scripts.mos.
        scripts_dir = args.input if os.path.isdir(args.input) else os.path.dirname(args.input)
        root = os.path.dirname(scripts_dir)
        songs = generate_songs(root, os.path.join(os.path.dirname(args.output), "songs.mos"))
        if songs:
            print("wrote %s (%d song(s))" % (songs, len(load_song_defs(scripts_dir))))
        # A sibling instruments.toml -> the driver's src/instruments.mos (Tier 1).
        insts = generate_instruments(root, os.path.join(os.path.dirname(args.output),
                                                        "instruments.mos"))
        if insts:
            print("wrote %s (%d instrument(s))" % (insts, len(load_instrument_defs(scripts_dir))))
    else:
        print(mos)
    if args.vms:
        with open(args.vms, "w") as f:
            f.write(prog.to_vms())
    if args.map:
        with open(args.map, "w") as f:
            json.dump(prog.to_map(), f, indent=2)
    return 0
