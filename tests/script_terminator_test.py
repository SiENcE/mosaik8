#!/usr/bin/env python3
"""Every compiled script must END.

Scripts concatenate into ONE bytecode blob, so a script whose events simply
run out does not stop -- the thread walks straight into whatever script was
laid down after it and keeps executing. `loop` scripts got a trailing JUMP and
`sub` scripts a RET, but a PLAIN script got nothing.

Found converting the reference-engine sample (2026-08-05): the town room's init ended
with MUSIC_SONG, fell through the next room's init (re-assigning another room's
actor clips and re-attaching its input script) and into the logo room's init, whose
`wait 30` + change-scene threw the player back to the title screen a second
after arriving. Every scene On Init did the same into whatever followed it,
which is what "the scenes flip and change all the time" was.

The implicit STOP is only appended when the script does not already end
itself, so an already-terminated script stays byte-identical.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik_vm import Compiler, RefVM  # noqa: E402
from mosaik_vm.isa import OPS  # noqa: E402

_STOP = OPS["STOP"][0]          # OPS maps a mnemonic to (opcode, operands)

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def build(scripts):
    return Compiler().compile(scripts)


def test_unterminated_script_gets_stop():
    prog = build([
        {"name": "main", "events": [{"event": "stop"}]},
        # No terminator: this is the shape that fell through.
        {"name": "first", "events": [{"event": "set_var", "var": "a",
                                      "expr": "1"}]},
        {"name": "second", "events": [{"event": "set_var", "var": "b",
                                       "expr": "2"}]},
    ])
    code, labels = prog.code, prog.offsets
    end = labels["second"]
    check("an unterminated script ends with STOP before the next one",
          code[end - 1] == _STOP,
          "byte before 'second' = %d" % code[end - 1])


def test_terminated_scripts_are_unchanged():
    """A script that already ends itself gets no extra byte (byte-identical)."""
    for ev in ({"event": "stop"},
               {"event": "change_scene", "room": 1, "x": 0, "y": 0},
               {"event": "reset"}):
        one = build([{"name": "main", "events": [ev]},
                     {"name": "next", "events": [{"event": "stop"}]}])
        two = build([{"name": "main", "events": [ev, ev]},
                     {"name": "next", "events": [{"event": "stop"}]}])
        # The single-event script's length is exactly its own lowering: adding
        # a second copy grows by exactly one more lowering, no padding.
        grow = two.offsets["next"] - one.offsets["next"]
        check("a script ending in %r needs no implicit STOP"
              % ev["event"], grow == one.offsets["next"] - one.offsets["main"],
              "grew %d" % grow)


def test_no_fallthrough_at_runtime():
    """RefVM: the first script must not execute the second one's body."""
    prog = build([
        {"name": "main", "events": [{"event": "set_var", "var": "ran_main",
                                     "expr": "1"}]},
        {"name": "other", "events": [{"event": "set_var", "var": "ran_other",
                                      "expr": "1"}]},
    ])
    vm = RefVM(prog.code, entry=prog.entry)
    vm.run(30)
    a = vm.heap[prog.variables["ran_main"]]
    b = vm.heap[prog.variables["ran_other"]]
    check("the boot script runs", a == 1)
    check("it does NOT fall into the next script", b == 0,
          "ran_other = %d" % b)


def test_conditional_terminator_still_stops():
    """A change_scene nested in an `if` is SKIPPED when the condition is
    false, so such a script still needs the implicit STOP."""
    prog = build([
        {"name": "main", "events": [
            {"event": "if", "cond": "0",
             "then": [{"event": "change_scene", "room": 2, "x": 0, "y": 0}]}]},
        {"name": "after", "events": [{"event": "set_var", "var": "leaked",
                                      "expr": "1"}]},
    ])
    check("a script whose only terminator is inside an `if` still gets STOP",
          prog.code[prog.offsets["after"] - 1] == _STOP)
    vm = RefVM(prog.code, entry=prog.entry)
    vm.run(30)
    check("and does not leak into the next script",
          vm.heap[prog.variables["leaked"]] == 0)


def test_change_scene_expression_room():
    """change_scene over a COMPUTED room (CHANGE_SCENE_E, spec op 0x16).

    A literal room keeps the compact RAISE (byte-identical); an expression
    pushes room/x/y and uses the stack-arg op. This is what a scene STACK
    needs -- the reference engine's push/pop state stores `scene()` in a cell and later
    returns to it, which a literal RAISE cannot express."""
    lit = build([{"name": "main",
                  "events": [{"event": "change_scene", "room": 3,
                              "x": 8, "y": 16}]}])
    check("an all-literal change_scene stays one RAISE",
          len(lit.code) == 7, "%d bytes" % len(lit.code))

    prog = build([{"name": "main", "events": [
        {"event": "set_var", "var": "scene_stack", "expr": "7"},
        {"event": "change_scene", "room": "scene_stack", "x": 0, "y": 0}]}])
    vm = RefVM(prog.code, entry=prog.entry)
    vm.run(20)
    check("an expression room changes to the COMPUTED scene",
          vm.change_log == [(7, 0, 0)], "%r" % (vm.change_log,))


def main():
    print("Script terminator (no fall-through between scripts)")
    print("=" * 50)
    test_unterminated_script_gets_stop()
    test_terminated_scripts_are_unchanged()
    test_no_fallthrough_at_runtime()
    test_conditional_terminator_still_stops()
    test_change_scene_expression_room()
    print("=" * 50)
    if failed:
        print("%d FAILED, %d passed" % (failed, passed))
        return 1
    print("All script-terminator checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
