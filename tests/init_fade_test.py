#!/usr/bin/env python3
"""A scene whose On Init fades the room in itself gets no room-load ramp (2026-10-23).

The reference engine fades a room in with a Fade In event its compiler
injects into the scene's init, before the first event that must wait for the
screen, so everything an init does before it (a replaced tile, a hidden actor)
happens under black; an init whose first waiting event IS a Fade In is
"manual" and the room stays black until it. Ours ramped inside `load_room`
and ran the init only after, so an init's first tile write showed up after
the fade (a pop), and an init with its own fade_in faded the room in twice.

`mosaik_vm/rooms/init_fade.py` is the reference engine's walk; the rooms
generator holds such a room black instead of ramping. A world with no such
init generates the ramp exactly as before.

Checks: the rule (including the shape that must NOT own the fade: a text box
before the fade_in, as raid-vm8's stage cards); the generated source with and
without an owning init (the unowned one is the repo's own rooms.mos); and, with
GBDK and PyBoy, a Game Boy ROM where the tile the init replaces changes BEFORE
the first palette step of the fade.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik_vm.rooms.init_fade import first_wait, owns_fade  # noqa: E402

FAILS = []


def check(cond, what):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        FAILS.append(what)


def test_rule():
    print("the reference engine's walk")
    E = lambda n, **k: dict(event=n, **k)  # noqa: E731
    check(owns_fade([E("bkg_tile"), E("set_var"), E("fade_in")], {}), "instant events, then fade_in: owns")
    check(not owns_fade([E("set_var"), E("text"), E("fade_out"), E("fade_in")], {}),
          "a text box before the fade_in (raid-vm8's stage card): does NOT own")
    check(not owns_fade([E("bkg_tile")], {}), "no waiting event at all: the room ramps")
    check(not owns_fade([], {}), "an empty init")
    check(owns_fade([E("if", then=[E("fade_in")], **{"else": []})], {}), "a fade_in in an if body is the first wait")
    check(not owns_fade([E("if", then=[E("wait")]), E("fade_in")], {}), "a wait in an earlier if body decides")
    scripts = {"setup": [E("set_var"), E("wait")], "quick": [E("set_var")], "show": [E("fade_in")]}
    check(not owns_fade([E("call", script="setup"), E("fade_in")], scripts), "a called script that waits decides")
    check(owns_fade([E("call", script="quick"), E("fade_in")], scripts), "a called script that does not wait")
    check(owns_fade([E("start_thread", script="show")], scripts), "a thread's body is looked into, as the reference's is")
    check(owns_fade([E("input_attach", script="setup"), E("fade_in")], scripts),
          "an input handler's script runs later: not looked into")
    loop = {"a": [E("call", script="a")]}
    check(first_wait([E("call", script="a")], loop) is None, "a call cycle stops at the depth limit")
    check(owns_fade([E("switch", cases=[{"then": [E("fade_in")]}])], {}), "a switch case")
    check(owns_fade([E("camera_lock"), E("camera_release"), E("fade_in")], {}),
          "camera_lock / camera_release are instant state writes: they do not stop the walk")


def _regen(proj):
    import mosaik_vm
    mosaik_vm.generate_rooms(proj)
    with open(os.path.join(proj, "src", "rooms.mos"), encoding="utf-8") as f:
        return f.read()


def _ramp_guard(src):
    i = src.find("player.settle_camera()")
    return src[i:i + 200] if i >= 0 else ""


def test_generated(tmp):
    print("the generated room load")
    sample = os.path.join(ROOT, "projects", "vm-shardlings")
    proj = os.path.join(tmp, "vm-shardlings")
    shutil.copytree(sample, proj, ignore=shutil.ignore_patterns("build"))
    with open(os.path.join(sample, "src", "rooms.mos"), encoding="utf-8") as f:
        repo = f.read()
    plain = _regen(proj)
    check(plain.replace("\r\n", "\n") == repo.replace("\r\n", "\n"),
          "no owning init: rooms.mos is the repo's own, byte for byte")
    check("rm != " not in _ramp_guard(plain), "...and its ramp is unconditional")
    evt = os.path.join(proj, "scripts", "field.evt.toml")
    with open(evt, encoding="utf-8") as f:
        s = f.read()
    head = 'name = "title"\n'
    check(head in s, "the title init is where the fixture expects it")
    s = s.replace(head, head + '[[script.events]]\nevent = "bkg_tile"\ntile = 3\nsrc = 0\n\n'
                  '[[script.events]]\nevent = "fade_in"\nframes = 16\n\n', 1)
    with open(evt, "w", encoding="utf-8") as f:
        f.write(s)
    owned = _regen(proj)
    guard = _ramp_guard(owned)
    check("if rm != 0 {" in guard, "the title (room 0) owns its fade: the ramp skips it")
    check("if rm != 0 {" in guard and "while fl > 0" in guard
          and guard.index("if rm != 0 {") < guard.index("while fl > 0"), "...the guard wraps the ramp")
    return proj


def test_rom(proj):
    print("a Game Boy ROM")
    import mosaik8_build
    try:
        import pyboy  # noqa: F401
    except ImportError:
        print("  skip (no PyBoy)")
        return
    if not mosaik8_build.gbdk_available():
        print("  skip (no GBDK toolchain)")
        return
    import subprocess
    r = subprocess.run([sys.executable, "-m", "mosaik_vm", os.path.join(proj, "scripts"), "-o",
                        os.path.join(proj, "src", "scripts.mos")], cwd=ROOT, capture_output=True, text=True)
    check(r.returncode == 0, "the fixture's scripts compile")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build", "--platform", "gameboy", proj],
                       cwd=ROOT, capture_output=True, text=True)
    rom = os.path.join(proj, "build", "gameboy", "vm-shardlings.gb")
    check(r.returncode == 0 and os.path.isfile(rom), "the fixture builds for the Game Boy")
    if not os.path.isfile(rom):
        return
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null")
    tiles, bgps, shows = [], [], []
    try:
        for _f in range(300):
            pb.tick(1, False)
            tiles.append(bytes(pb.memory[0x9030:0x9040]))   # background tile 3 (0x8800 mode: 0x9000 + 16n)
            bgps.append(pb.memory[0xFF47])
            lcdc = pb.memory[0xFF40]
            # the background is ON SCREEN: LCD on (bit 7), BG on (bit 0), and a
            # palette that is not one flat shade (a fade's hold is all white or black)
            shows.append(bool(lcdc & 0x80) and bool(lcdc & 0x01) and pb.memory[0xFF47] not in (0x00, 0xFF))
    finally:
        pb.stop(save=False)
    changes = [f for f in range(1, len(tiles)) if tiles[f] != tiles[f - 1]]
    check(len(changes) >= 2, "tile 3 was painted, then rewritten by the init (changes at %s)" % changes[:4])
    if len(changes) < 2:
        return
    at = changes[-1]
    check(not shows[at], "the init rewrote the tile while the screen was blank (frame %d, BGP %02X)"
          % (at, bgps[at]))
    # The tile write and the init's own fade_in black out in ONE frame, so the
    # line above cannot see the old order; this one can: the room-load ramp ran
    # BEFORE the init, revealing the room between its paint and the rewrite.
    seen = [f for f in range(changes[-2], at) if shows[f]]
    check(not seen, "the room stayed blank from its paint (frame %d) to the init's rewrite (%d); "
          "shown at %s" % (changes[-2], at, seen[:3]))
    shown = [f for f in range(at, len(shows)) if shows[f]]
    check(bool(shown), "...and the room was revealed after it (first shown at frame %s)"
          % (shown[0] if shown else None))


def main():
    tmp = tempfile.mkdtemp(prefix="init_fade_")
    try:
        test_rule()
        proj = test_generated(tmp)
        test_rom(proj)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        return 1
    print("init_fade_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
