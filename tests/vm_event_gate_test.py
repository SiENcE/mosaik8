#!/usr/bin/env python3
"""THE EVENT BUSY GATE, THE WAITABLE MOVE, AND THE ROTATING SHOT.

Three reference-engine semantics the shooter conversion exposed as missing, played
against its reference build and the reference engine's own generated runtime:

1. **The busy gate** (events.c `SCRIPT_TERMINATED`): a timer tick or a button
   press fires NOTHING while the instance it last spawned is still running.
   Ours spawned unconditionally - the shooter's 30-frame timer piled a new
   many-second falling-hazard thread every 30 frames (lives lost at random), and
   holding A autofired straight through the shoot script's cooldown `wait`.

2. **The waitable move** (`A_AWAIT_MOVE` 0x1D): `actor_move` +
   `actor_await_move` is arrival-timed at ANY speed, where the importer's old
   `move + wait <fixed frames>` was right at exactly one. Deactivating the
   actor cancels its move, so a mid-flight kill (a shot falling hazard) releases the
   waiting thread the same frame.

3. **Projectile flight animation** (`PROJ_ANIM` 0x65, a one-shot latch): GB
   Studio's loopAnim + animSpeed - the rotating particle. `frames` frames,
   each `stride` tiles apart (a converted 8x16 cell is 2), every `period`
   display frames. A launch with no latch is a static tile, byte-identical.

Coverage: RefVM behaviour + the five-surface lockstep (ISA/spec/core.mos).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm
from mosaik_vm import isa
from mosaik_vm.refvm import RefVM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_timer_busy_gate():
    print("\n[timer busy gate]")
    # a 2-frame timer whose script runs 10 frames: ungated this spawns ~12
    # instances in 25 frames; gated, one at a time (~2-3).
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "timer_set", "timer": 0, "period": 2, "script": "tick"},
            {"event": "stop"}]},
        {"name": "tick", "events": [
            {"event": "set_var", "var": "n", "expr": "n + 1"},
            {"event": "wait", "frames": 10}]},
    ])
    vm = RefVM(prog.code, entry=prog.entry)
    for _ in range(25):
        vm.frame()
    n = vm.heap[prog.variables["n"]]
    check(n <= 3, "one instance at a time (%d spawns in 25 frames, not ~12)" % n)
    # ... and once an instance ENDS, the next tick fires again
    for _ in range(30):
        vm.frame()
    n2 = vm.heap[prog.variables["n"]]
    check(n2 > n, "the gate releases on instance death (count still grows)")


def test_input_busy_gate():
    print("\n[input busy gate]")
    # an A script with a 10-frame cooldown: pressing A every 4 frames used to
    # stack a thread per press; gated, presses inside the cooldown are ignored.
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "input_attach", "button": "a", "script": "shoot"},
            {"event": "stop"}]},
        {"name": "shoot", "events": [
            {"event": "set_var", "var": "shots", "expr": "shots + 1"},
            {"event": "wait", "frames": 10}]},
    ])
    vm = RefVM(prog.code, entry=prog.entry)
    vm.frame()
    for burst in range(8):                       # 8 presses over 32 frames
        vm.frame(held=("a",)); vm.frame(held=("a",))
        vm.frame(); vm.frame()
    shots = vm.heap[prog.variables["shots"]]
    check(1 <= shots <= 3, "cooldown really limits fire rate (%d shots from 8 "
                           "presses in 32 frames: >= 1, not 8)" % shots)


def test_await_move():
    print("\n[actor_await_move]")
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "actor_activate", "actor": 1, "tile": 1, "x": 0, "y": 0},
            {"event": "actor_move", "actor": 1, "x": 0, "y": 32},
            {"event": "actor_await_move", "actor": 1},
            {"event": "set_var", "var": "landed", "value": 1},
            {"event": "stop"}]},
    ])
    vm = RefVM(prog.code, entry=prog.entry)
    lc = prog.variables["landed"]
    fr = 0
    while vm.heap[lc] == 0 and fr < 100:
        vm.frame(); fr += 1
    check(vm.heap[lc] == 1 and vm.actors[1].y == 32,
          "waits for arrival (landed after %d frames at y=32)" % fr)
    check(fr >= 30, "...and really WAITED (32 px at speed 1 is >= 32 frames)")

    # mid-flight deactivate releases the waiter (a shot-down falling hazard)
    prog2 = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "actor_activate", "actor": 1, "tile": 1, "x": 0, "y": 0},
            {"event": "start_thread", "script": "killer"},
            {"event": "actor_move", "actor": 1, "x": 0, "y": 200},
            {"event": "actor_await_move", "actor": 1},
            {"event": "set_var", "var": "released", "value": 1},
            {"event": "stop"}]},
        {"name": "killer", "events": [
            {"event": "wait", "frames": 5},
            {"event": "actor_deactivate", "actor": 1},
            {"event": "stop"}]},
    ])
    vm2 = RefVM(prog2.code, entry=prog2.entry)
    rc = prog2.variables["released"]
    fr = 0
    while vm2.heap[rc] == 0 and fr < 100:
        vm2.frame(); fr += 1
    check(fr <= 10, "a mid-flight deactivate releases the wait (%d frames, "
                    "not the full 200-px flight)" % fr)

    # a faster actor arrives sooner - the thing a fixed wait got wrong
    prog3 = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "actor_activate", "actor": 1, "tile": 1, "x": 0, "y": 0},
            {"event": "actor_set_speed", "actor": 1, "speed": 4},
            {"event": "actor_move", "actor": 1, "x": 0, "y": 32},
            {"event": "actor_await_move", "actor": 1},
            {"event": "set_var", "var": "landed", "value": 1},
            {"event": "stop"}]},
    ])
    vm3 = RefVM(prog3.code, entry=prog3.entry)
    lc = prog3.variables["landed"]
    fr = 0
    while vm3.heap[lc] == 0 and fr < 100:
        vm3.frame(); fr += 1
    check(fr < 15, "arrival-timed: at speed 4 it lands ~4x sooner (%d frames)" % fr)


def test_proj_anim():
    print("\n[projectile flight animation]")
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "projectile", "x": 10, "y": 10, "vx": 0, "vy": 0,
             "tile": 4, "life": 60, "frames": 4, "anim": 2, "stride": 2},
            {"event": "projectile", "x": 40, "y": 40, "vx": 0, "vy": 0,
             "tile": 0, "life": 60},
            {"event": "stop"}]},
    ])
    blob = bytes(prog.code)
    check(0x65 in blob, "an animated launch emits the PROJ_ANIM latch (0x65)")
    vm = RefVM(prog.code, entry=prog.entry)
    toffs = set()
    for _ in range(20):
        vm.frame()
        if vm.proj:
            toffs.add(vm.proj[0].get("toff", 0))
    check(toffs == {0, 2, 4, 6},
          "4 frames at stride 2 cycle tile offsets 0/2/4/6 (got %s)" % sorted(toffs))
    check(len(vm.proj) == 2 and "frames" not in vm.proj[1],
          "the SECOND launch (no frames) is static - the latch is one-shot")

    # byte-identical off: no frames param -> no 0x65 anywhere
    plain = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "projectile", "x": 10, "y": 10, "vx": 0, "vy": -1,
             "tile": 0, "life": 30},
            {"event": "stop"}]},
    ])
    check(0x65 not in bytes(plain.code),
          "a plain launch carries no latch (byte-identical)")


def test_lockstep():
    print("\n[five-surface lockstep]")
    check(isa.OPS.get("A_AWAIT_MOVE") == (0x1D, ["u8"]),
          "isa: A_AWAIT_MOVE = 0x1D (actor)")
    check(isa.OPS.get("PROJ_ANIM") == (0x65, ["u8", "u8", "u8"]),
          "isa: PROJ_ANIM = 0x65 (frames, period, stride)")
    check(isa.RPN.get("ACTOR_MOVING") == 0x0B, "isa: ACTOR_MOVING RPN = 0x0B")
    core = _read("lib", "vm", "core.mos")
    for name in ("A_AWAIT_MOVE", "PROJ_ANIM"):
        check(("case OP_%s {" % name) in core and ("if VM_OP_%s {" % name) in core,
              "core.mos: %s arm behind its dispatch-pruning guard" % name)
    check("case 0x0B {" in core and "if VM_RPN_ACTOR_MOVING {" in core,
          "core.mos: the ACTOR_MOVING RPN arm (behind its dispatch-pruning guard)")
    check("tmr_ctx" in core and "in_ctx" in core and "event_release(" in core,
          "core.mos: the busy gate + its central-death release")
    spec = _read("docs", "vm8-spec.md")
    check("| 1D | A_AWAIT_MOVE |" in spec and "| 65 | PROJ_ANIM |" in spec
          and "| 0B | ACTOR_MOVING |" in spec,
          "spec carries all three rows")


def main():
    test_timer_busy_gate()
    test_input_busy_gate()
    test_await_move()
    test_proj_anim()
    test_lockstep()
    print()
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("vm_event_gate_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
