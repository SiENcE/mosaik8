#!/usr/bin/env python3
"""The TIMED text box (state 31, `box_hold`) - the reference engine's two non-key close
modes (Stage M of the studio's gbs-event-lowering plan, 2026-09-08).

VM8 had exactly one text primitive, `UI_TEXT`, a modal box that waits for the
A dismiss. The reference engine has two boxes that close with NO key press, and its own
compiler is where the shape came from (`scriptBuilder.ts` textDialogue, read
2026-09-08 rather than reasoned from the event names):

  * `closeWhen: "text"` emits an `_overlayWait` carrying **no button flag** and
    then `wait_frames(closeDelayFrames)` - the box stands for a while and
    closes itself, and no press can dismiss it early.
  * `closeWhen: "notModal"` (`isModal = false`) emits no wait AND no close, so
    the box stays up while the script runs on, until a later
    `EVENT_DIALOGUE_CLOSE_NONMODAL`. The RPG check conversion's battle uses exactly one
    pattern: TEXT(notModal) -> WAIT -> MUSIC_STOP -> CLOSE -> SWITCH_SCENE.

Both are "a box on screen for n frames, no key", so ONE state covers them.
`box_hold` is a WRITE-only ONE-SHOT latch the `UI_TEXT` open consumes (the
`menu_cancel` / SHAKE_OPTS shape, so the blob format is untouched), and the
countdown starts only after a typewriter reveal has finished - the order GB
Studio's own `.UI_WAIT_TEXT`-then-`wait_frames` gives.

Measured price: **0 B**. The reference-engine sample conversion's GBC ROM is byte-identical with the
state added, because every arm folds under VM_ST_BOX_HOLD for a blob that
never writes state 31.

RefVM checks for the behaviour; SOURCE-CONTRACT checks (the blank_state_test
shape) for the core arm, which no RefVM run can see.
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

import mosaik_vm as m  # noqa: E402
from mosaik_vm import isa  # noqa: E402

VM = os.path.join(ROOT, "lib", "vm")
FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _read(name):
    with open(os.path.join(VM, name), encoding="utf-8") as f:
        return f.read()


def _prog(hold=None, second=True):
    """A box (optionally held) then a marker write, then a plain modal box."""
    box = {"event": "text", "string": "timed"}
    if hold is not None:
        box["hold"] = hold
    evs = [box, {"event": "set_var", "var": "after", "value": 1}]
    if second:
        evs += [{"event": "text", "string": "modal"},
                {"event": "set_var", "var": "after", "value": 2}]
    return m.Compiler().compile([{"name": "main", "events": evs + [{"event": "stop"}]}])


def _run(prog, frames, a_on=()):
    vm = m.RefVM(prog.code, entry=prog.entry)
    for f in range(frames):
        vm.frame(a_pressed=(f in a_on))
    return vm


def encoding():
    print("\n[the encoding: a one-shot state, no new opcode, no blob change]")
    check("state 31 is `box_hold`", isa.STATES.get("box_hold") == 31,
          isa.STATES.get("box_hold"))
    held = _prog(hold=5)
    plain = _prog()
    ops_h = [i["op"] for i in m.disasm(held.code, {})]
    ops_p = [i["op"] for i in m.disasm(plain.code, {})]
    check("a held box arms the state before its UI_TEXT",
          ops_h[:3] == ["RPN", "SET_STATE", "UI_TEXT"], ops_h[:3])
    # The arm is RPN + SET_STATE, both of which the ISA already had: the
    # feature adds a STATE, not an opcode (the 1.5a ladder).
    check("... built from opcodes the ISA already had, not a new one",
          set(ops_h) <= {"RPN", "SET_STATE", "UI_TEXT", "SET_CONST", "SET_VAR", "STOP"},
          set(ops_h))
    check("a box WITHOUT hold is byte-identical to before the feature",
          ops_p[0] == "UI_TEXT" and "SET_STATE" not in ops_p, ops_p)
    # the SET_STATE operand is the state id, and the pushed value the frames
    txt = "\n".join(i.get("text", "") for i in m.disasm(held.code, {}))
    check("it pushes the frame count and writes state 31",
          "PUSH 5" in txt and "SET_STATE  31" in txt, txt.replace("\n", " | "))


def refvm():
    print("\n[RefVM: a held box closes ITSELF; an ordinary one still needs A]")
    prog = _prog(hold=5)
    # NO input at any point in this run.
    vm = _run(prog, 5)
    check("the box is still up before the hold elapses",
          vm.box_open == 1 and vm.heap[prog.variables["after"]] == 0,
          (vm.box_open, vm.heap[prog.variables["after"]]))
    vm = _run(prog, 6)
    check("it closes on its own once the hold elapses (no key pressed)",
          vm.heap[prog.variables["after"]] == 1,
          vm.heap[prog.variables["after"]])
    check("the SECOND box is modal again - the latch was ONE-SHOT",
          vm.box_open == 1 and vm.heap[prog.variables["after"]] == 1)
    vm2 = _run(prog, 40)
    check("... and no amount of waiting dismisses that modal box",
          vm2.heap[prog.variables["after"]] == 1,
          vm2.heap[prog.variables["after"]])
    vm3 = _run(prog, 40, a_on=(8,))
    check("an A press does dismiss it", vm3.heap[prog.variables["after"]] == 2,
          vm3.heap[prog.variables["after"]])

    print("\n[RefVM: a held box IGNORES the pad, as the reference engine's does]")
    # the reference engine's timed/non-modal boxes carry no button flag in their
    # _overlayWait, so a press cannot close one early.
    early = _run(_prog(hold=20), 6, a_on=(2, 3, 4))
    check("A during the hold does not close it early",
          early.box_open == 1 and early.heap[_prog(hold=20).variables["after"]] == 0,
          early.box_open)

    print("\n[RefVM: hold 0 is 'no hold', not 'close immediately']")
    zero = _run(_prog(hold=0), 30)
    check("an unheld box (hold 0) still waits for A",
          zero.heap[_prog(hold=0).variables["after"]] == 0)

    print("\n[RefVM: a scene change clears a latch armed for a box that never opened]")
    prog2 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "text", "string": "armed", "hold": 3},
        {"event": "change_scene", "room": 1, "x": 0, "y": 0}]},
        {"name": "other", "events": [{"event": "stop"}]}])
    vm4 = m.RefVM(prog2.code, entry=prog2.entry)
    for _ in range(8):
        vm4.frame()
    check("the room change left no armed latch behind",
          vm4.box_hold == 0 and vm4.box_hold_live == 0,
          (vm4.box_hold, vm4.box_hold_live))


def core_source():
    print("\n[core.mos: the arm exists, is guarded, and reuses the close path]")
    core = _read("core.mos")
    check("the state id is declared", "const ST_BOX_HOLD = 31" in core)
    check("the write arm is behind its own fold flag",
          "if VM_ST_BOX_HOLD {" in core and "box_hold = v" in core)
    check("the UI_TEXT open CONSUMES the one-shot latch",
          "box_hold_live = box_hold" in core and "box_hold = 0" in core)
    check("the countdown lives in read_input, where the A-dismiss does",
          "box_hold_live -= 1" in core)
    # THE contract: one close path. Two copies of a dozen lines of window /
    # sprite-cut / repaint work would drift the moment either is touched.
    seg = core[core.index("bank(0) local function read_input()"):]
    seg = seg[:seg.index("box_hold_live -= 1") + 4000]
    check("reaching the end of the hold takes the A-dismiss path (a_edge = 1), "
          "rather than copying the close",
          "a_edge = 1" in seg)
    # EVERY mention of the state must sit inside a `if VM_ST_BOX_HOLD {` block,
    # or a project that never arms it stops being byte-identical - which is the
    # whole reason the measured price is 0 B. Checked by brace depth rather
    # than by eye: the guard opens a block, and the mention has to be inside it.
    depth, guard_at, outside = 0, None, []
    for ln, line in enumerate(core.splitlines(), 1):
        if "if VM_ST_BOX_HOLD {" in line and guard_at is None:
            guard_at = depth
        depth += line.count("{") - line.count("}")
        if guard_at is not None and depth <= guard_at:
            guard_at = None
        code = line.lstrip()
        if ("box_hold" in line and guard_at is None
                and not code.startswith("--")
                # The two BSS declarations are exempt, exactly as
                # menu_cancel's three are: an UNINITIALISED global costs RAM,
                # not resident image, and the byte-identical reference-conversion ROM
                # measured above is what proves it. Everything that READS or
                # WRITES the state has to be guarded, or a non-user stops
                # being byte-identical.
                and not code.startswith("var box_hold")):
            outside.append(ln)
    check("every box_hold READ/WRITE is inside a VM_ST_BOX_HOLD guard "
          "(what makes a non-user byte-identical; the BSS vars are free)",
          not outside, "unguarded at lines %s" % outside)
    # ANCHORED TO THE FUNCTION. This check used to be a substring search over
    # the whole file, which the copy in boot() satisfied - so it passed for as
    # long as reset_scene_ui did NOT clear the latch (the defect it claimed to
    # cover, found 2026-09-21). A clear at boot is not a clear per room.
    reset = core[core.index("local function reset_scene_ui()"):]
    reset = reset[:reset.index("bank(0) local function run_context")]
    check("a scene change clears an unconsumed latch, IN reset_scene_ui",
          "box_hold = 0" in reset and "box_hold_live = 0" in reset, reset[:400])


def spec_lockstep():
    print("\n[lockstep: the spec's state table names it]")
    with open(os.path.join(ROOT, "docs", "vm8-spec.md"), encoding="utf-8") as f:
        spec = f.read()
    check("the spec documents state 31", "| 31 | box_hold" in spec)
    check("... and the UI_TEXT row says the open consumes it",
          "box_hold" in spec[spec.index("| 30 | UI_TEXT"):
                             spec.index("| 30 | UI_TEXT") + 600])


def main():
    print("=" * 60)
    print("box_hold - the timed text box (state 31)")
    print("=" * 60)
    encoding()
    refvm()
    core_source()
    spec_lockstep()
    print("=" * 60)
    if FAILS:
        print("FAILED: %d" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
