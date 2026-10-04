#!/usr/bin/env python3
"""`wait_until` - a thread that BLOCKS in place until a condition holds.

The event language had `if` and `switch` and no backward branch at all, so the
only way to express "pause here until something happens" was to END the script
and let something else restart it. That is not the same thing, because
**vm.core busy-gates its timer and input attachments**:

    tick_timers:       if tmr_ctx[i] == 255 { tmr_ctx[i] = spawn(...) }
    tick_input_attach: if in_ctx[i] == 255  { in_ctx[i]  = spawn(...) }

which is the reference engine's own `SCRIPT_TERMINATED` check (`core/events.c`) - a tick
that arrives while the last instance is still running fires NOTHING. A script
that ends in order to wait therefore opens the gate it was holding.

Measured on the shooter conversion: its game-over screen waits for Start
*inside* the hazard-spawner's timer script. Lowered as a script split, the
timer re-fired 30 frames later and rained falling hazards behind the "Game Over" text;
the reference ROM - whose `vm_input_wait` rewinds the PC and yields, keeping
the thread alive - shows a completely static screen for as long as you leave it
(sampled 500 LCD frames, zero moving objects).

**No new opcode.** It is IF + JUMP + WAIT wired into a backward branch:

    top:  RPN(cond); IF body; JUMP end
    body: WAIT poll; JUMP top
    end:

`poll` is floored at 1, so the loop always yields and can never spin inside a
single frame however the condition is written. That is why this is
`wait_until` and not a general `while`: a `while` with an empty body is a hang,
and nothing in the event language would catch it.
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


def _compile(events):
    return mosaik_vm.Compiler().compile([{"name": "main", "events": events}])


def test_no_new_opcode():
    """The whole point of the shape: it costs the ISA nothing."""
    print("\n[wait_until introduces no opcode]")
    before = set(isa.OPS)
    prog = _compile([
        {"event": "set_var", "var": "flag", "value": 0},
        {"event": "wait_until", "cond": "flag"},
        {"event": "set_var", "var": "done", "value": 1},
        {"event": "stop"}])
    check(set(isa.OPS) == before, "the ISA table is unchanged")
    used = set(prog.code)
    for name in ("IF", "JUMP", "WAIT", "RPN"):
        check(isa.OPS[name][0] in used, "it emits %s, which already existed" % name)


def test_it_blocks_then_releases():
    print("\n[a false condition blocks; a true one falls through]")
    prog = _compile([
        {"event": "set_var", "var": "flag", "value": 0},
        {"event": "wait_until", "cond": "flag"},
        {"event": "set_var", "var": "done", "value": 1},
        {"event": "stop"}])
    vm = RefVM(prog.code, entry=prog.entry)
    done = prog.variables["done"]
    flag = prog.variables["flag"]
    for _ in range(60):
        vm.frame()
    check(vm.heap[done] == 0,
          "60 frames later the thread has NOT run past the wait")
    check(vm.any_active_threads() >= 1,
          "...and its context is still ALIVE, which is the whole point - a "
          "split lowering would have ended it and freed the busy gate that "
          "stops the timer respawning")
    vm.heap[flag] = 1                       # what an input attachment would do
    for _ in range(4):
        vm.frame()
    check(vm.heap[done] == 1, "the condition released it")


def test_an_already_true_condition_costs_no_frame():
    """The reference engine's `vm_input_wait` returns immediately when the test already
    passes (`if ((joy != last_joy) && (joy & mask)) return;`), so an event list
    whose condition is already true must not lose a frame to the poll."""
    print("\n[an already-true condition does not yield]")
    prog = _compile([
        {"event": "set_var", "var": "flag", "value": 1},
        {"event": "wait_until", "cond": "flag"},
        {"event": "set_var", "var": "done", "value": 7},
        {"event": "stop"}])
    vm = RefVM(prog.code, entry=prog.entry)
    vm.frame()
    check(vm.heap[prog.variables["done"]] == 7,
          "it ran straight through on the first frame")


def test_poll_spacing():
    """`poll` is the frames slept between tests. Floored at 1 so the loop
    always yields - a 0 would spin inside one frame forever."""
    print("\n[poll]")
    prog = _compile([{"event": "wait_until", "cond": "flag", "poll": 0},
                     {"event": "stop"}])
    wait = isa.OPS["WAIT"][0]
    i = list(prog.code).index(wait)
    check(prog.code[i + 1] == 1, "poll 0 is floored to 1 frame")
    prog = _compile([{"event": "wait_until", "cond": "flag", "poll": 5},
                     {"event": "stop"}])
    i = list(prog.code).index(wait)
    check(prog.code[i + 1] == 5, "an authored poll survives")


def test_it_loops_backwards():
    """The branch structure, read off the blob: the tail JUMP must target the
    RPN that re-evaluates the condition, not the top of the script (which
    would re-run everything before the wait)."""
    print("\n[the loop is a real backward branch]")
    prog = _compile([
        {"event": "set_var", "var": "flag", "value": 0},
        {"event": "wait_until", "cond": "flag"},
        {"event": "stop"}])
    code = list(prog.code)
    jump, wait = isa.OPS["JUMP"][0], isa.OPS["WAIT"][0]
    i = code.index(wait)
    check(code[i + 2] == jump, "the poll wait is followed by a JUMP")
    target = code[i + 3] | (code[i + 4] << 8)
    check(code[target] == isa.OPS["RPN"][0],
          "...and it lands on the condition's RPN, so the test re-runs")
    check(target > 0, "...which is INSIDE the script, not its entry")


def main():
    test_no_new_opcode()
    test_it_blocks_then_releases()
    test_an_already_true_condition_costs_no_frame()
    test_poll_spacing()
    test_it_loops_backwards()
    print()
    if _FAILED:
        print("SOME CHECKS FAILED (%d)" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
