"""The room load colours the PLAYER's fan where it IS (2026-10-18).

With an emote pack the bubble owns objects 0..EMOTE_FAN-1 (an OAM index is
the draw priority on the GB family) and `player.set_base(EMOTE_FAN)` puts the
player above it. The generated room load still coloured the player at a
literal 0, both arms:

  [kind_tile_palettes]  scenes.paint_actor(0, pk, w, h)
  [kind_palettes]       sprite.set_palette(0, scenes.kind_pal_at(pk))

so it painted the BUBBLE's objects and left the player on palette 0.
Measured on the reference-engine sample conversion: the platform player
wore palette 0 on every object instead of its hair / face / body map; the
engine's own `vm-shardlings` had the same line.

Checks: `generate_rooms` on a copy of `projects/vm-shardlings` (emote pack +
a player kind palette) emits the player's colour at EMOTE_FAN in both arms,
and - with GBDK and PyBoy - a Game Boy Color ROM of each arm draws the
player's first object on the authored slot.
"""
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik8_build  # noqa: E402

FAILS = []
PROJ = os.path.join(ROOT, "projects", "vm-shardlings")
SLOT = 3     # a slot no other default would produce


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _copy(tmp, arm):
    dst = os.path.join(tmp, "vm-shardlings")
    shutil.copytree(PROJ, dst, ignore=shutil.ignore_patterns("build"))
    wp = os.path.join(dst, "world.toml")
    text = io.open(wp, encoding="utf-8").read()
    # the row under [kind_palettes] (the [kinds] table has a `player = 0` too:
    # that one is the kind's ID)
    text, n = re.subn(r"(?m)^(\[kind_palettes\]\n(?:[^\[\n].*\n)*?)player = 0$",
                      lambda m: m.group(1) + "player = %d" % (SLOT if arm == "kind" else 0),
                      text, count=1)
    if n != 1:
        raise AssertionError("vm-shardlings' [kind_palettes] player row moved")
    if arm == "cells":
        text += "\n[kind_tile_palettes]\nplayer = [%s]\n" % ", ".join([str(SLOT)] * 8)
    io.open(wp, "w", encoding="utf-8").write(text)
    import mosaik_vm
    import mosaik_scenes
    world, base = mosaik_scenes.load_world(wp)
    io.open(os.path.join(dst, "src", "scenes.mos"), "w", encoding="utf-8").write(
        mosaik_scenes.core.transpile(world, base))
    mosaik_vm.generate_rooms(dst)
    return dst


def _player_attr(rom):
    """The OAM attribute byte of the player's first object (EMOTE_FAN = 4)
    once the start room has loaded. Its palette bits are written at room
    load whether or not the player is on screen yet; the old code left them
    0 and coloured objects 0..3 (the bubble) instead."""
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", cgb=True)
    pb.tick(600, False)
    attrs = [pb.memory[0xFE00 + i * 4 + 3] for i in range(6)]
    pb.stop(save=False)
    return attrs


def main():
    print("\n[generate_rooms: the player's colour lands on its base]")
    tmp = tempfile.mkdtemp(prefix="player_paint_")
    try:
        roms = {}
        for arm, line in (("kind", "sprite.set_palette(EMOTE_FAN, scenes.kind_pal_at("),
                          ("cells", "scenes.paint_actor(EMOTE_FAN, ")):
            root = os.path.join(tmp, arm)
            os.makedirs(root)
            dst = _copy(root, arm)
            rooms = io.open(os.path.join(dst, "src", "rooms.mos"), encoding="utf-8").read()
            check("%s arm: the player is coloured at EMOTE_FAN" % arm,
                  line in rooms and "player.set_base(EMOTE_FAN)" in rooms,
                  [l.strip() for l in rooms.splitlines()
                   if "paint_actor(" in l or "set_palette(" in l][:4])
            roms[arm] = dst
        try:
            import pyboy  # noqa: F401
        except Exception:
            print("  (skip ROM: PyBoy not installed)")
            return
        if not mosaik8_build.gbdk_available():
            print("  (skip ROM: GBDK not installed)")
            return
        print("\n[a Game Boy Color ROM: the player's first object's palette]")
        for arm, dst in roms.items():
            r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                                "--platform", "gameboy_color", dst],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", cwd=ROOT)
            rom = os.path.join(dst, "build", "gameboy_color", "vm-shardlings.gbc")
            if not os.path.exists(rom):
                check("%s arm builds" % arm, False, (r.stdout + r.stderr)[-600:])
                continue
            a = _player_attr(rom)
            check("%s arm: the player's objects wear slot %d, the bubble's do not"
                  % (arm, SLOT),
                  a[4] & 7 == SLOT and a[5] & 7 == SLOT and a[0] & 7 != SLOT, a)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("room load: the player's colour at its own OAM base")
    print("=" * 60)
    main()
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("player_paint_base_test: all checks passed")
