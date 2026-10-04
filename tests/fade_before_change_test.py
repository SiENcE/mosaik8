#!/usr/bin/env python3
"""A scene change raised on an ALREADY BLACK screen must not flash (2026-09-16).

vm.core ramps the room being left to black at its pending-exception service
point (`fade_out()`, the `[scenes] fade` auto-fade). It always started from
level 1, so a script that had ALREADY faded out - the reference engine's scene-pop
shape, `scenePopState` fades at the event's own `fadeSpeed` and THEN pops -
got the ramp a second time from black: the screen brightened to level 1 and
2 for `fade_hold` frames each before going black again. On a DMG that is BGP
FF -> F9 -> FE -> FF, a two-step flash in the middle of every scripted
fade-then-change.

The reference VM does not: its `fade_out()` returns at once when `fade_timer` is already
`FADED_OUT_FRAME` (fade_manager.c), so a faded screen stays faded across the
raise and the arrival fade-in is the only ramp. vm.fx now REMEMBERS the level
`set_level` last wrote and vm.core's auto-fade skips its ramp when that is 3.

Both live under VM_OP_FADE: only a program that carries the FADE op can
arrive at a raise already black, so a non-user's fx has no memo cell, its
core no check, and its image is byte-identical (the dispatch-pruning shape).

Checks: SOURCE-CONTRACT for the fold on both sides; the modules compile with
the define on and off on every console class; and - with GBDK and PyBoy
present - a real ROM's BGP trajectory across a scripted fade-out followed by
a scene change. EVERY negative check asserts its stimulus first: a trail with
no scripted fade in it would pass "no flash" vacuously.
"""
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

from mosaik import MosaikCompiler  # noqa: E402
import mosaik8_build  # noqa: E402

VM = os.path.join(ROOT, "lib", "vm")
FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _read(name):
    with open(os.path.join(VM, name), encoding="utf-8") as f:
        return f.read()


def source_contract():
    print("\n[vm.fx: the level memo, inside the VM_OP_FADE fold]")
    fx = _read("fx.mos")
    check("the memo cell is declared INSIDE the fold (no BSS shift for a non-user)",
          "if VM_OP_FADE {\n        var cur: u8\n    }" in fx)
    lvl = fx[fx.index("function set_level("):]
    lvl = lvl[:lvl.index("\n    }\n")]
    check("set_level records the level it writes, under the same fold",
          "if VM_OP_FADE {\n            cur = l\n        }" in lvl, lvl)
    check("faded_out() answers 1 only at level 3, 0 for a non-user",
          "function faded_out() -> u8 {" in fx
          and "if cur == 3 {" in fx.split("function faded_out()")[1]
          and "faded_out" in fx.split("export ")[-1])

    print("\n[vm.core: the auto-fade skips an already black screen]")
    core = _read("core.mos")
    body = core[core.index("local function fade_out()"):]
    body = body[:body.index("\n    }\n")]
    check("the check sits at the top of the ramp, under VM_OP_FADE",
          "if VM_OP_FADE {\n                if fx.faded_out() == 1 {\n"
          "                    return\n                }\n            }" in body,
          body)
    check("... before the first held frame",
          "fx.faded_out()" in body
          and body.index("fx.faded_out()") < body.index("video.wait_vblank()"))

    for plat in ("gameboy", "gameboy_color", "lynx", "sms", "gamegear", "nes"):
        for on in (False, True):
            try:
                out = MosaikCompiler().compile_program(
                    [("fx.mos", fx)], platform=plat,
                    defines={"VM_OP_FADE": on, "VM_FADE_STYLE": False})
                if out.startswith("Compilation error"):
                    raise RuntimeError(out)
            except Exception as exc:                    # pragma: no cover
                check(False, "vm.fx compiles on %s with VM_OP_FADE=%s (%s)"
                      % (plat, on, exc))
                return
    check("vm.fx compiles on every console class, the fold on and off", True)

    # The THREE states of the define, because two comments in
    # `mosaik/compiler.py`'s setdefault block disagree about what an
    # unresolvable name does (one says it keeps the `then` arm, the other
    # that it survives as a runtime test on an undeclared symbol). MEASURED
    # on the generated C 2026-09-16: ABSENT keeps the arm, exactly like TRUE.
    # That is what `_vm_dispatch_defines` returning {} is contracted to mean
    # ("keep everything, byte-identical"), so VM_OP_FADE must NOT join the
    # setdefault list - defaulting it False would fold the memo away in the
    # one case that asks for everything to stay.
    def _c(d):
        return MosaikCompiler().compile_program([("fx.mos", fx)],
                                                platform="gameboy", defines=d)
    absent, on, off = _c({}), _c({"VM_OP_FADE": True}), _c({"VM_OP_FADE": False})
    check("the define ABSENT keeps the memo (the {} 'keep everything' contract)",
          "uint8_t cur;" in absent and "cur = l;" in absent)
    check("... TRUE keeps it too", "uint8_t cur;" in on and "cur = l;" in on)
    check("... and FALSE folds the cell AND its write away (byte-identical "
          "for a program with no FADE op)",
          "uint8_t cur;" not in off and "cur = l;" not in off
          and "faded_out(void) {" in off)


def _bgp_trail(rom, frames):
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", cgb=False)
    seen = []
    last = None
    for _ in range(frames):
        pb.tick()
        v = pb.memory[0xFF47]
        if v != last:
            seen.append(v)
            last = v
    pb.stop(save=False)
    return seen


#: The boot script: let the boot ramp-in finish, fade OUT by script (the FADE
#: op, 8 VM frames = 4 LCD frames per level at the calibrated scale), then
#: raise a scene change - the pop-state shape. Room 0 is the only room.
BOOT = '''name = "main"
events = [
  { event = "wait", frames = 60 },
  { event = "fade_out", frames = 8 },
  { event = "change_scene", room = 0, x = 80, y = 80 },
  { event = "stop" },
]'''


def rom():
    print("\n[a real ROM: BGP across a scripted fade-out + scene change]")
    try:
        import pyboy  # noqa: F401
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    src = os.path.join(ROOT, "projects", "vm-hud")
    from mosaik_vm.rooms import generate_rooms
    tmp = tempfile.mkdtemp(prefix="fade_change_")
    try:
        root = os.path.join(tmp, "p")
        shutil.copytree(src, root, ignore=shutil.ignore_patterns("build"))
        with open(os.path.join(root, "studio.toml"), "a", encoding="utf-8") as f:
            f.write("\n[scenes]\nfade = 2\n")
        evt = os.path.join(root, "scripts", "main.evt.toml")
        with open(evt, encoding="utf-8") as f:
            text = f.read()
        new, n = re.subn(r'name = "main"\nevents = \[.*?\n\]', BOOT, text,
                         count=1, flags=re.S)
        check("the fixture's boot script was replaced", n == 1)
        with open(evt, "w", encoding="utf-8") as f:
            f.write(new)
        # scripts.mos is checked in with the sample: recompile it from the
        # edited event file, or the build runs the OLD boot script (the
        # stimulus check below is what said so on the first run).
        g = subprocess.run([sys.executable, "-m", "mosaik_vm",
                            os.path.join(root, "scripts"), "-o",
                            os.path.join(root, "src", "scripts.mos")],
                           capture_output=True, text=True, cwd=ROOT,
                           encoding="utf-8", errors="replace")
        check("the boot script recompiles", g.returncode == 0,
              (g.stdout + g.stderr)[-400:])
        generate_rooms(root)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"),
                            "build", "--platform", "gameboy", root],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 or "ROM created" not in r.stdout:
            check("the project builds", False, (r.stdout + r.stderr)[-800:])
            return
        out_dir = os.path.join(root, "build", "gameboy")
        rom_path = [os.path.join(out_dir, f) for f in os.listdir(out_dir)
                    if f.endswith(".gb")][0]
        trail = _bgp_trail(rom_path, 420)
        hexes = " ".join("%02X" % v for v in trail)
        print("  trail: " + hexes)
        pairs = list(zip(trail, trail[1:]))
        # STIMULUS 1: the boot ramp-in, then the SCRIPTED fade to black.
        boot_in = (0xFF, 0xFE) in pairs and (0xF9, 0xE4) in pairs
        scripted = (0xE4, 0xF9) in pairs and (0xFE, 0xFF) in pairs
        check("the boot ramp-in ran (FF -> FE ... F9 -> E4)", boot_in, hexes)
        check("the script's own fade-out ran (E4 -> F9 ... FE -> FF)",
              scripted, hexes)
        # STIMULUS 2: the arrival ramp-in AFTER the scripted fade - a second
        # FF -> FE, i.e. the scene change was served and its room revealed.
        first_black = trail.index(0xFF, trail.index(0xE4)) if 0xE4 in trail \
            and 0xFF in trail[trail.index(0xE4):] else -1
        after = trail[first_black:] if first_black >= 0 else []
        check("the scene change revealed the room again (a ramp-in after the "
              "scripted black)", (0xFF, 0xFE) in list(zip(after, after[1:])),
              hexes)
        # THE DEFECT: the auto-fade re-ramping from black is FF -> F9 (level 1
        # over the identity base). The ramp-in never produces that pair - it
        # is FF -> FE -> F9 -> E4 - so its presence is the flash and nothing
        # else. Measured before the fix: FF F9 FE FF in the middle of the
        # trail, one step per fade_hold (2 LCD frames).
        check("... and the screen never brightened from black before the "
              "reveal (no FF -> F9 step: the auto-fade skipped the ramp)",
              (0xFF, 0xF9) not in pairs, hexes)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("fade before a scene change: the auto-fade must not re-ramp from black")
    print("=" * 60)
    source_contract()
    rom()
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("fade_before_change_test: all checks passed")
