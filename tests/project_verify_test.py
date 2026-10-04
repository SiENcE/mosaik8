#!/usr/bin/env python3
"""Run the ROM-behaviour `verify.py` of the VM8 samples that have one.

`tests/run_all.py` discovers `*_test.py` here, and nothing else ran a project's
own `verify.py` -- `PROJECT_DIRS` only BUILDS projects, and only behind
`--samples`. So vm-overworld, vm-quest, vm-rpg and vm-shop failed quietly for as
long as it took someone to run them by hand (2026-09-07: all four were red, and
every failure was a stale CHECK, not a defect).

They are in the DEFAULT path, not behind a flag, for the reason they were broken
in the first place: a check nobody runs is a check that does not exist, and a
flag would reproduce that exactly. The cost is a GB build plus a PyBoy run each
(~80 s on top of a ~40 s suite). `save_test.py` already does the same thing in
the default path -- build a project with GBDK, drive the ROM under PyBoy, and
skip politely when either is missing -- and this follows it.

**The ROM is REBUILT first, never reused.** Each `verify.py` builds only when its
ROM is ABSENT, so a leftover build from an older engine would be re-verified and
report the old behaviour as current (the studio's freshness-badge lesson: the
Game panel's Run loaded a stale ROM and an engine fix read as "still broken").

**The child's output is forwarded only on FAILURE.** `run_all.py`'s FAIL_MARKERS
matches the OUTPUT as well as the exit code, so a wrapper that echoes a passing
child's check lines would be marked failed the moment one of those lines happened
to contain "FAILED".

    python tests/project_verify_test.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik8_build import gbdk_available  # noqa: E402

# The four VM8 samples whose verify.py drives a GB ROM: an actor's On Interact
# slot (vm.entity) through a dialogue box, a shop menu, a gated door and combat.
PROJECT_VERIFIES = ("vm-overworld", "vm-quest", "vm-rpg", "vm-shop",
                    # The two palette showcases read colour off the screen, so
                    # they need the COLOUR build (2026-09-17).
                    ("vm-palettes", "gameboy_color"),
                    ("palettes-lab", "gameboy_color"),
                    # W7b: the follow camera's dead zone, follow offset,
                    # per-axis lock and preventScroll, all read back from the
                    # player's own screen position (2026-09-17).
                    "vm-camprops",
                    # W7j: the POINT-AND-CLICK scene type - the cursor move,
                    # the hover pose and the click, all read off OAM
                    # (2026-09-18).
                    "vm-pointnclick",
                    # W7c: the reference engine's three save slots - three writes, a load,
                    # a clear and a peek, all read off OAM (2026-09-18).
                    "vm-saveslots",
                    # W7d: the two tenants of LYC_REG alive at once - parallax
                    # bands with a dialogue box opening and closing over them.
                    # Its two checks ARE the two whole-screen defects the old
                    # arbitration was built to avoid, so this is the one entry
                    # here that cannot be replaced by a source contract
                    # (2026-09-18).
                    "lyc-merge-lab",
                    # W7h: a hUGE `6xy` call-routine cell running a VM8 script
                    # - the fire, its argument, the per-slot busy gate and the
                    # unattached slot, all read out of the VM heap while the
                    # DRIVER's own clock advances the test (2026-09-19).
                    "vm-musicroutine",
                    # B1: a dialogue box on the window-less consoles, and the
                    # SPRITE CUT that keeps actors out of its band. This is the
                    # one project that drives an SMS/GG box on PIXELS, and it
                    # was not here - which is why a shipped feature that did
                    # nothing (`sprite.cut_y`, defeated by two render latches)
                    # went unnoticed until it was reported from play. Its own
                    # verify builds all THREE of its consoles, so the platform
                    # named here is only the wrapper's link check
                    # (2026-09-19).
                    ("vm-uiscroll", "sms"),
                    # The first-party `.v8s` / `.board` sample: a whole game
                    # as VM8 assembly - the snake moves, eats, grows, and dies
                    # on a wall, all read out of the VM heap (2026-09-22).
                    "vm-snake")

ok = True


def _gbdk_available():
    """The builder's own probe: a directory named `gbdk` is not a toolchain.
    A Windows checkout rsynced into the Linux release container has one, and
    this suite reported four samples as failing to build a ROM when it should
    have skipped."""
    return gbdk_available()


def _run(args):
    return subprocess.run(args, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=ROOT)


def run_project(entry):
    """Build the ROM (GB unless the entry names a platform), then run the
    project's own verify.py against it."""
    global ok
    name, platform = (entry, "gameboy") if isinstance(entry, str) else entry
    proj = os.path.join(ROOT, "projects", name)
    build = _run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                  "--platform", platform, proj])
    out = (build.stdout or "") + (build.stderr or "")
    if "ROM created" not in out:
        ok = False
        print("  [FAIL] %s: the %s build did not produce a ROM" % (name, platform))
        print(out[-2000:])
        return
    verify = _run([sys.executable, os.path.join(proj, "verify.py")])
    out = (verify.stdout or "") + (verify.stderr or "")
    if verify.returncode == 0:
        # A count, not the lines -- see the module docstring on FAIL_MARKERS.
        n = out.count("[PASS]")
        skipped = " (some checks skipped)" if "  skip: " in out else ""
        print("  [PASS] %s: %d checks%s" % (name, n, skipped))
        return
    ok = False
    print("  [FAIL] %s" % name)
    print(out)


def main():
    print("[project verify.py: the VM8 entity-slot samples + the palette showcases]")
    if not _gbdk_available():
        print("  skip: GBDK not installed")
        return 0
    for name in PROJECT_VERIFIES:
        run_project(name)
    print("\n%s" % ("All checks passed" if ok else "FAILURES"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
