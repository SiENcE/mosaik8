#!/usr/bin/env python3
"""`[build] proj_under_lock` -- a shot keeps flying under a cutscene lock.

WHY IT EXISTS. The reference VM's `src/core/core.c` runs
`projectiles_update()` OUTSIDE its `!VM_ISLOCKED()` block: a reference-engine lock
freezes input, the player state machine, timers and music EVENTS, and nothing
else. So a shot already in flight when a cutscene starts keeps travelling in
the reference and keeps colliding; ours stops dead in the air.

Ours was also inconsistent with its own frame. Three lines above and below the
gate, `actor.step_all`, the emote timer, the background animation and the music
driver all run under the lock - each with a comment saying so. Projectiles were
the one moving thing that did not, and only because `g_proj_update` used to sit
beside `g_player()`, sharing its lock gate for scheduling reasons rather than
semantic ones. The 2026-08-30 frame-order fix moved the call to the reference VM's position
but deliberately left the gate alone, because changing it changes what a script
may assume: a cutscene opened while an enemy shot is on screen can now be
interrupted by that shot landing. Hence a knob, not a silent fix.

The contract this pins:
  - OFF (absent) is BYTE-IDENTICAL: the else arm is the original statement
    verbatim. Measured on the shooter conversion (a project that fires): the GB ROM is
    md5 5089b34b8d10fe66a99538e7b48b5ef7 both before this feature existed and
    with it present and the flag absent, and 0176a1f04321143f651c6a3e25633775
    with the flag on -- so the A/B proves itself in both directions.
  - the guard is a STATEMENT-level define, so it must be supplied ALWAYS or it
    survives as a runtime test on an undeclared symbol (the VM_OBJ16 rule);
  - RefVM mirrors it (the five-in-lockstep rule), which is what lets the
    BEHAVIOUR be measured headlessly rather than asserted;
  - update still precedes render within the frame either way, which is what
    `p_quiet` and the dynamic-OAM re-base depend on.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm as m                       # noqa: E402
from mosaik8_build import BuildConfig       # noqa: E402

FAILS = []


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    if not cond:
        FAILS.append(label)


SRC = open(os.path.join(ROOT, "lib", "vm", "core.mos"), encoding="utf-8").read()


def cfg(value):
    c = BuildConfig.__new__(BuildConfig)
    c.config = {"build": {}} if value is None else {"build": {"proj_under_lock": value}}
    return c


def _flight(under_lock):
    """Fire one shot, take a LOCK on the next frame, and return the x positions
    the shot occupied over the following frames.

    The lock is taken by a SECOND thread, so the shot is already in flight when
    it lands - which is the situation the divergence is about. A cutscene that
    locks before anything is fired cannot tell the two behaviours apart."""
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "projectile", "x": 20, "y": 40, "vx": 4, "vy": 0,
             "tile": 0, "life": 200},
            {"event": "start_thread", "script": "cut"},
            {"event": "stop"}]},
        # The cutscene: lock, sit there, never unlock inside the window.
        {"name": "cut", "events": [
            {"event": "lock"},
            {"event": "wait", "frames": 60},
            {"event": "unlock"},
            {"event": "stop"}]},
    ])
    vm = m.RefVM(prog.code, entry=prog.entry, proj_under_lock=under_lock)
    xs = []
    for _ in range(12):
        vm.frame()
        xs.append(vm.proj[0]["x"] if vm.proj else None)
    return xs, vm


def main():
    # 1. The knob.
    check("proj_under_lock absent -> False (today's behaviour)",
          cfg(None).get_proj_under_lock() is False)
    check("proj_under_lock = true reads True", cfg(True).get_proj_under_lock() is True)

    # 2. The lib contract: both arms present, the OFF arm unchanged.
    check("the gate forks on the define",
          re.search(r"if VM_PROJ_UNDER_LOCK \{\s*\n\s*g_proj_update\(\)", SRC) is not None)
    check("the OFF arm is the original lock-gated statement, verbatim",
          re.search(r"\} else \{\s*\n\s*if vm_lockcount == 0 \{\s*\n\s*g_proj_update\(\)",
                    SRC) is not None)
    # ...and render still follows it, on both arms. Anchored INSIDE the gate's
    # own block rather than on the file's first `g_proj_render()`, which belongs
    # to the room-load path hundreds of lines earlier.
    _after = SRC.split("if VM_PROJ_UNDER_LOCK {", 1)[1][:600]
    check("update still precedes render on BOTH arms (p_quiet + the dyn-OAM "
          "re-base depend on it)",
          "g_proj_render()" in _after
          and _after.index("g_proj_render()") > _after.index("} else {"))

    # 3. The define must ALWAYS be supplied, or a statement-level `if` survives
    # as a runtime test on an undeclared symbol (the VM_OBJ16 rule).
    csrc = open(os.path.join(ROOT, "mosaik", "compiler.py"), encoding="utf-8").read()
    check("compile_program always supplies VM_PROJ_UNDER_LOCK",
          "setdefault('VM_PROJ_UNDER_LOCK'" in csrc)
    bsrc = open(os.path.join(ROOT, "mosaik8_build.py"), encoding="utf-8").read()
    check("the build states it only when opting IN (byte-identical off)",
          "defines['VM_PROJ_UNDER_LOCK'] = True" in bsrc
          and "= False" not in bsrc.split("get_proj_under_lock()")[1][:400])
    check("'proj_under_lock' is a KNOWN [build] key (or every build warns)",
          "'proj_under_lock'" in bsrc.split("APPLIED_KEYS")[1][:900])

    # 4. The BEHAVIOUR, measured on the reference interpreter rather than
    # asserted - and with the control beside it, because "the shot moved" only
    # means something if the same program's shot really does freeze when the
    # flag is off.
    off, vm_off = _flight(False)
    on, vm_on = _flight(True)
    check("the lock really was held during the window",
          vm_off.lockcount > 0, "lockcount %d" % vm_off.lockcount)
    moved_off = len({x for x in off if x is not None})
    moved_on = len({x for x in on if x is not None})
    check("OFF: the shot freezes in the air (one position for the whole window)",
          moved_off == 1, off)
    check("ON: the shot keeps flying (the reference VM's semantics)", moved_on > 1, on)
    check("...and it flies at its authored speed, not a catch-up burst",
          all(b - a == 4 for a, b in zip(on, on[1:]) if a is not None and b is not None),
          on)

    if FAILS:
        print("\n%d FAILED" % len(FAILS))
        return 1
    print("\nAll projectile-under-lock checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
