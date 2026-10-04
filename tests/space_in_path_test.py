#!/usr/bin/env python3
"""A Game Boy build must link from a path with a space in it.

GBDK's `lcc` splices its paths into the sdcc / sdas / sdld command lines it
builds WITHOUT QUOTING them, so a source at

    C:\\Users\\me\\Documents\\MosaiK8 Projects\\x\\build\\gameboy\\x.c

reaches sdcc as two arguments ("don't know what to do with file
'...\\MosaiK8'"). The installed studio's default project folder IS
`Documents\\MosaiK8 Projects`, so every Game Boy build of a project made there
failed (2026-09-23). cc65's cl65 quotes correctly and was never affected.

`mosaik8_build.gbdk_spaceless_command` rewrites the argv. Rules pinned here:

  1. A command with no space in it is returned UNCHANGED (and runs where it
     always ran), so no existing build line moves.
  2. A spaced FOLDER becomes its 8.3 short form and the file keeps its name.
  3. Without a short name, a path inside the build directory goes RELATIVE
     and the link runs in that directory.
  4. A ROM NAME with a space (a project called "My Game") links under a
     spaceless STAND-IN and is renamed back, siblings (.map) included: lcc
     names the .map after -o, so the ROM's name is never 8.3-shortened.
  5. What still has a space is refused with a message naming it (Windows).
  6. The real thing: ROMs link from a spaced folder, one of them with a
     spaced NAME and a -Wl-m map (skips without GBDK).

    python tests/space_in_path_test.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mosaik8_build as mb  # noqa: E402

# The build log carries emoji; a cp1252 console must not turn a FAIL into a
# UnicodeEncodeError.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

ok = True


def check(label, cond):
    global ok
    ok = ok and bool(cond)
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))


def _build(src, *extra):
    return subprocess.run(
        [sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
         "--platform", "gameboy", *extra, src],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=ROOT)


def main():
    print("space_in_path_test")
    base = tempfile.mkdtemp(prefix="mk8 space ")
    try:
        build = os.path.join(base, "my proj", "build", "gameboy")
        os.makedirs(build)
        src = os.path.join(build, "game.c")
        open(src, "w").close()
        rom = os.path.join(build, "game.gb")

        print("\n  1. no space, no change")
        plain = ["lcc", "-msm83:gb", "-Wl-m", "-Iinc", "-o", "a.gb", "a.c"]
        cmd, run_in, standin = mb.gbdk_spaceless_command(list(plain), "/tmp")
        check("a spaceless argv comes back unchanged", cmd == plain)
        check("...and runs where it always ran", run_in is None)
        check("...under its own name", standin is None)

        print("\n  2. short folders, long file names")
        short = mb._short_path(build)
        if os.name == "nt" and short and " " not in short:
            argv = ["lcc", "-msm83:gb", "-I" + base, "-o", rom, src]
            cmd, run_in, standin = mb.gbdk_spaceless_command(argv, build)
            check("no argument carries a space",
                  not any(" " in a for a in cmd))
            check("the -o target keeps its file name",
                  os.path.basename(cmd[cmd.index("-o") + 1]) == "game.gb")
            check("an input keeps its file name",
                  os.path.basename(cmd[-1]) == "game.c")
            check("the short form names the same file",
                  os.path.samefile(cmd[-1], src))
            check("flags pass through", cmd[1] == "-msm83:gb")

            print("\n  4. a ROM NAME with a space links under a stand-in")
            spaced_src = os.path.join(build, "my game.c")
            open(spaced_src, "w").close()
            spaced_rom = os.path.join(build, "my game.gb")
            cmd, run_in, standin = mb.gbdk_spaceless_command(
                ["lcc", "-o", spaced_rom, spaced_src], build)
            check("no argument carries a space",
                  not any(" " in a for a in cmd))
            check("the ROM links as %s.gb" % mb.GBDK_LINK_STANDIN,
                  os.path.basename(cmd[2]) == mb.GBDK_LINK_STANDIN + ".gb")
            # Never its 8.3 form: `~` breaks -debug's assembler symbols and
            # an upper-case `X.C` is not compiled as C (both measured).
            check("the spaced input links from a spaceless copy",
                  os.path.basename(cmd[3])
                  == mb.GBDK_LINK_STANDIN + "_my_game.c")
            check("the plan remembers the real ROM",
                  standin["rom"] and os.path.normcase(standin["rom"][1])
                  == os.path.normcase(os.path.abspath(spaced_rom)))
            with open(spaced_src, "w") as f:
                f.write("int x;")
            mb.begin_gbdk_standin(standin)
            copy = standin["copies"][0][1]
            check("begin makes the copy",
                  os.path.isfile(copy) and open(copy).read() == "int x;")
            for ext in (".gb", ".map"):
                with open(os.path.join(build, mb.GBDK_LINK_STANDIN + ext),
                          "w") as f:
                    f.write(ext)
            mb.finish_gbdk_standin(standin, linked=True)
            check("the ROM and its .map are renamed back",
                  open(spaced_rom).read() == ".gb"
                  and open(os.path.join(build, "my game.map")).read()
                  == ".map")
            check("no stand-in ROM is left behind",
                  not mb._gbdk_standin_files(standin["rom"][0]))
            check("the input copy is deleted", not os.path.exists(copy))
            check("the real input is untouched",
                  open(spaced_src).read() == "int x;")
        else:
            print("  (skip: no 8.3 names here)")

        print("\n  3. no short name: relative inside the build dir")
        real = mb._short_path
        mb._short_path = lambda p: None
        try:
            cmd, run_in, standin = mb.gbdk_spaceless_command(
                ["lcc", "-o", rom, src], build)
            check("the target goes relative", cmd[2] == "game.gb")
            check("the input goes relative", cmd[3] == "game.c")
            check("...and the link runs in the build dir", run_in == build)

            print("\n  5. what cannot be fixed is refused, by name")
            outside = os.path.join(base, "elsewhere dir", "x.o")
            if os.name == "nt":
                try:
                    mb.gbdk_spaceless_command(["lcc", "-o", rom, outside],
                                              build)
                    check("a spaced path outside the build dir is refused",
                          False)
                except ValueError as e:
                    check("a spaced path outside the build dir is refused",
                          "elsewhere dir" in str(e))
        finally:
            mb._short_path = real

        print("\n  6. ROMs link from a spaced folder")
        if not mb.gbdk_available():
            print("  (skip: no GBDK)")
        else:
            proj = os.path.join(base, "My Projects")
            os.makedirs(proj)
            hello = os.path.join(ROOT, "samples", "hello.mos")
            shutil.copy(hello, proj)
            r = _build(os.path.join(proj, "hello.mos"))
            out = os.path.join(proj, "build", "gameboy", "hello.gb")
            linked = r.returncode == 0 and os.path.isfile(out)
            check("hello.gb links from '%s'" % proj, linked)
            if not linked:
                print(r.stdout[-1500:], r.stderr[-800:])
            # A spaced NAME, with a map (-debug adds -Wl-m): both must land
            # under the real name, and the ROM must be the same program.
            shutil.copy(hello, os.path.join(proj, "my hello.mos"))
            r = _build(os.path.join(proj, "my hello.mos"), "--debug")
            gb_dir = os.path.join(proj, "build", "gameboy")
            named = os.path.join(gb_dir, "my hello.gb")
            linked = r.returncode == 0 and os.path.isfile(named)
            check("'my hello.gb' links under its real name", linked)
            if not linked:
                print(r.stdout[-1500:], r.stderr[-800:])
            else:
                check("...with its .map beside it",
                      os.path.isfile(os.path.join(gb_dir, "my hello.map")))
                check("...and no stand-in left behind",
                      not any(n.startswith(mb.GBDK_LINK_STANDIN)
                              for n in os.listdir(gb_dir)))
    finally:
        shutil.rmtree(base, ignore_errors=True)

    print("\n%s" % ("All checks passed" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
