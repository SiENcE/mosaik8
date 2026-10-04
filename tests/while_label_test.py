#!/usr/bin/env python3
"""`while`, `label` and `goto` - loops that stay INSIDE their script.

`wait_until` was the event language's one backward branch, and it always sleeps.
The reference engine has three more, all compiled as a label and a VM_JUMP inside ONE
script (`scriptBuilder.ts`): EVENT_LOOP (`labelDefine` + body + `labelGoto`),
EVENT_LOOP_WHILE (`whileScriptValue`: label, RPN, `_ifConst .EQ 0 end`, body,
jump) and EVENT_LOOP_FOR, plus the deprecated EVENT_DEFINE_LABEL /
EVENT_GOTO_LABEL whose names live in the script builder's `labelLookup`.

The importer used to lower EVENT_LOOP to a separate looping THREAD and end the
caller. That broke two things a script-local loop keeps: the LOCK (a scene init
that loops a menu is locked in the reference engine, so attached Start/Select menus cannot
fire - the adventure check project's title) and everything AFTER the loop.

**No new opcode.** `while` is IF + JUMP, `label` an anchor, `goto` a JUMP. A
loop whose body never waits is not a hang: a thread runs QUANT (16) instructions
a frame and yields, which is the reference VM's `INSTRUCTIONS_PER_QUANT` for the same loop.
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

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _run(scripts, frames=4):
    prog = mosaik_vm.Compiler().compile(scripts)
    vm = RefVM(prog.code, entry=prog.entry)
    vm.run(frames)
    return prog, vm


def _main(events):
    return [{"name": "main", "events": events}]


def test_while_runs_until_false_then_falls_through():
    print("\n[while: test, body, jump back; what follows still runs]")
    prog, vm = _run(_main([
        {"event": "set_var", "var": "i", "value": 0},
        {"event": "set_var", "var": "s", "value": 0},
        {"event": "while", "cond": "i < 5", "then": [
            {"event": "set_var", "var": "s", "expr": "s + i"},
            {"event": "set_var", "var": "i", "expr": "i + 1"}]},
        {"event": "set_var", "var": "after", "value": 1}]))
    h, v = vm.heap, prog.variables
    check(h[v["s"]] == 10 and h[v["i"]] == 5, "0+1+2+3+4 = 10, i stops at 5")
    check(h[v["after"]] == 1, "the event after the loop runs")
    _, vm2 = _run(_main([
        {"event": "while", "cond": "0 == 1", "then": [
            {"event": "set_var", "var": "ran", "value": 1}]},
        {"event": "set_var", "var": "after", "value": 1}]))
    check(vm2.heap[0] == 0, "a false condition skips the body entirely")


def test_forever_drops_the_test_and_yields():
    print("\n[while with no cond = EVENT_LOOP: no RPN, and it yields]")
    prog, vm = _run([
        {"name": "main", "events": [
            {"event": "start_thread", "script": "other"},
            {"event": "while", "then": [
                {"event": "set_var", "var": "spins", "expr": "spins + 1"}]}]},
        {"name": "other", "events": [
            {"event": "wait", "frames": 2},
            {"event": "set_var", "var": "other_ran", "value": 1}]}], frames=6)
    body = prog.lowered["main"]
    check(not any(getattr(it, "mnem", "") == "IF" for it in body),
          "no IF is emitted for a constant-true loop")
    check(vm.heap[prog.variables["other_ran"]] == 1,
          "another thread still runs: the spinning loop is preempted")
    check(0 < vm.heap[prog.variables["spins"]] <= 16 * 6,
          "the spin is bounded by the quantum (16 a frame)")
    code = list(prog.code)
    jump = isa.OPS["JUMP"][0]
    check(jump in code, "the loop closes with a JUMP")


def test_label_and_goto_both_directions():
    print("\n[label / goto: backwards and forwards, scoped per script]")
    prog, vm = _run([
        {"name": "main", "events": [
            {"event": "set_var", "var": "n", "value": 0},
            {"event": "label", "name": "top"},
            {"event": "set_var", "var": "n", "expr": "n + 1"},
            {"event": "if", "cond": "n < 3", "then": [
                {"event": "goto", "name": "top"}]},
            {"event": "goto", "name": "out"},
            {"event": "set_var", "var": "n", "value": 99},
            {"event": "label", "name": "out"}]},
        {"name": "second", "events": [
            {"event": "label", "name": "top"},
            {"event": "stop"}]}])
    check(vm.heap[prog.variables["n"]] == 3,
          "goto top re-runs until n == 3, goto out skips the 99")
    check("second" in prog.offsets,
          "another script may define a label of the same name")


def test_label_errors_are_named():
    print("\n[a goto to nowhere and a doubled label are compile errors]")
    for events, needle in (
            ([{"event": "goto", "name": "nope"}], "nope"),
            ([{"event": "label", "name": "a"}, {"event": "label", "name": "a"}],
             "defined twice"),
            ([{"event": "goto", "name": ""}], "needs a label name")):
        try:
            mosaik_vm.Compiler().compile(_main(events))
            check(False, "refused: %s" % needle)
        except mosaik_vm.VmError as e:
            check(needle in str(e), "refused and named: %s" % e)
    # a label another script defines is not visible here
    try:
        mosaik_vm.Compiler().compile([
            {"name": "a", "events": [{"event": "label", "name": "x"}]},
            {"name": "b", "events": [{"event": "goto", "name": "x"}]}])
        check(False, "a goto cannot reach another script's label")
    except mosaik_vm.VmError:
        check(True, "a goto cannot reach another script's label")


def test_goto_ends_a_script():
    print("\n[a script ending in goto gets no dead STOP]")
    prog = mosaik_vm.Compiler().compile(_main([
        {"event": "label", "name": "top"},
        {"event": "idle"},
        {"event": "goto", "name": "top"}]))
    ops = [it.mnem for it in prog.lowered["main"] if hasattr(it, "mnem")]
    check(ops[-1] == "JUMP", "the script ends on the goto's JUMP, not a STOP "
          "(got %s)" % ops)


def test_no_new_opcode():
    print("\n[no opcode: while / label / goto use what the ISA has]")
    prog = mosaik_vm.Compiler().compile(_main([
        {"event": "while", "cond": "k < 2", "then": [
            {"event": "set_var", "var": "k", "expr": "k + 1"},
            {"event": "goto", "name": "done"}]},
        {"event": "label", "name": "done"}]))
    used = {it.mnem for it in prog.lowered["main"] if hasattr(it, "mnem")}
    check(used <= {"RPN", "IF", "JUMP", "SET_VAR", "STOP"},
          "only RPN / IF / JUMP / SET_VAR / STOP (got %s)" % sorted(used))


def main():
    test_while_runs_until_false_then_falls_through()
    test_forever_drops_the_test_and_yields()
    test_label_and_goto_both_directions()
    test_label_errors_are_named()
    test_goto_ends_a_script()
    test_no_new_opcode()
    print()
    if _FAILED:
        print("FAILED: %d check(s)" % len(_FAILED))
        return 1
    print("All while/label/goto checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
