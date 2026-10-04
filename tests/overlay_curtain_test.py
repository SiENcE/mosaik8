#!/usr/bin/env python3
"""The window OVERLAY CURTAIN (the reference engine's ui.c), not a fade.

The reference engine's "overlay" is the GB WINDOW layer filled with one tile, positioned in
tile CELLS and slid by `ui_update`. It is what reveals a scene row by row with
the palettes at FULL BRIGHTNESS - the conversion used to lower it to a fade,
which gets the timing right and the picture wrong.

Three rules this pins, each of which was measured wrong first:

  * **The motion is in PIXELS.** `win_pos_y += 1` per (mask + 1) frames; only
    the DESTINATION is authored in tile cells (`ui_move_to` multiplies by 8).
    Read off the reference ROM: WY walks 0, 4, 9, 14, 19, 24 - not a multiple of
    8 anywhere. Stepping whole rows is a shutter, not a curtain.
  * **The step is driven by ELAPSED DISPLAY FRAMES**, not by how often the op
    re-enters. A VM frame is 1 to 3 LCD frames depending on the room, so
    counting calls made the same authored speed reveal at 1.7x the reference in
    one cutscene. `system.frames()` makes it frame-rate independent, which is
    also why the importer passes the reference engine's speed index straight through where
    it scales nearly every other duration by `_VM_FRAMES_PER_LCD`.
  * **The body lives in the PACK, not in the interpreter.** vm.core's opcode
    arms are resident image and cannot bank (the rule `actor_push` documents),
    so the interpreter keeps a two-line arm behind ONE seam pointer and vm.fx -
    which already banks and already carries the per-console fork - owns the
    loop. Measured: 114 B of bank 0 on a GB conversion, all in.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik_vm import isa
from mosaik_vm.compiler import Compiler
from mosaik_vm.refvm import RefVM
from mosaik_vm.rooms import emit_rooms_mos

_FAILED = []
_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "lib", "vm")


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def test_lockstep():
    print("\n[five-in-lockstep]")
    check(isa.OPS.get("OVERLAY_SHOW") == (0x4A, ["u8"]),
          "isa: OVERLAY_SHOW = 0x4A (row)")
    check(isa.OPS.get("OVERLAY_MOVE_TO") == (0x4B, ["u8", "u8"]),
          "isa: OVERLAY_MOVE_TO = 0x4B (row, speed)")
    core = open(os.path.join(_LIB, "core.mos"), encoding="utf-8").read()
    check("const OP_OVERLAY_SHOW = 0x4A" in core
          and "const OP_OVERLAY_MOVE_TO = 0x4B" in core,
          "core.mos: the same two opcode bytes")
    check("if VM_OP_OVERLAY_SHOW {" in core
          and "if VM_OP_OVERLAY_MOVE_TO {" in core,
          "... each arm behind its own dispatch-pruning flag")
    arm = core.split("case OP_OVERLAY_MOVE_TO")[1].split("\n            }")[0]
    check("pcr -= 3" in arm and "ST_YIELD" in arm,
          "the move is WAITABLE: rewind opcode + 2 operands, yield, re-enter")
    check(arm.count("g_overlay(") == 1 and "has_overlay == 1" in arm,
          "... through ONE seam pointer, skipped when unregistered")


def test_pack_semantics():
    print("\n[vm.fx owns the motion]")
    fx = open(os.path.join(_LIB, "fx.mos"), encoding="utf-8").read()
    check("function overlay(op: u8, a: u8, b: u8) -> u8" in fx,
          "one entry point (op, a, b), so the interpreter spends one pointer")
    body = fx.split("function overlay(op: u8")[1]
    check("system.frames()" in body,
          "the step is driven by ELAPSED DISPLAY FRAMES, not by call count")
    check("CATCHUP" in body,
          "... capped, so a room load cannot fast-forward the whole reveal")
    check("window.move(7, r)" in fx and "* 8" not in fx.split("window.move(7, r)")[0][-200:],
          "the window is positioned in PIXELS (rows are converted once, in ypx)")
    check("const MASK: array[u8, 8] = [0, 0, 1, 3, 7, 15, 31, 63]" in fx,
          "the reference engine's own ui_time_masks table")
    check("var depth: u8" in fx and "= OFF" not in fx.split("var depth")[0][-400:],
          "the state is BSS-zero (depth), not an initialized `= OFF` global - "
          "an initializer costs bank 0 even from a banked module")
    # Off the GB family there is no window layer: the no-op must report ARRIVED
    # or a script waiting on the slide hangs forever.
    tail = fx.split("} else {")[-1]
    check("function overlay(op: u8, a: u8, b: u8) -> u8" in tail
          and "return 1" in tail,
          "off the GB family it is a no-op that reports ARRIVED (no hang)")


def _run(events, frames=400):
    """Compile one script and run it, returning the RefVM."""
    prog = Compiler().compile([{"name": "main", "events": list(events)}])
    vm = RefVM(prog.code, entry=prog.entry)
    vm.run(frames)
    return vm


def test_refvm():
    print("\n[RefVM parity]")
    vm = _run([{"event": "overlay_show", "row": 0},
               {"event": "overlay_move_to", "row": 18, "speed": 2},
               {"event": "set_var", "var": "done", "expr": "1"}])
    check(vm.curtain_y == 144,
          "the curtain slid all the way off (y 0 -> 144)")
    check(vm.heap[0] == 1,
          "... and the script continued only AFTER it arrived (waitable)")
    # speed 2 = one pixel per 2 frames, so 144 px cannot be done in 200.
    vm2 = _run([{"event": "overlay_show", "row": 0},
                {"event": "overlay_move_to", "row": 18, "speed": 2},
                {"event": "set_var", "var": "done", "expr": "1"}], frames=200)
    check(vm2.heap[0] == 0 and 0 < vm2.curtain_y < 144,
          "at speed 2 it is still sliding after 200 frames (1 px / 2 frames)")
    vm3 = _run([{"event": "overlay_show", "row": 9}])
    check(vm3.curtain_y == 72, "a row operand is 8 px (row 9 -> y 72)")
    vm4 = _run([{"event": "overlay_hide"}])
    check(vm4.curtain_y == 144, "overlay_hide is show(row 18) - no third opcode")


def test_rooms_wiring():
    print("\n[rooms.mos wiring]")
    base = {"types": ["topdown"], "uniform": True, "has_collision": True}
    on = emit_rooms_mos(dict(base, uses_curtain=True))
    off = emit_rooms_mos(dict(base, uses_curtain=False))
    check("core.set_curtain(fx.overlay)" in on,
          "the seam is wired when a script raises a curtain")
    check('import "vm.fx"' in on, "... and vm.fx is imported for it")
    check("set_curtain" not in off,
          "a world that never raises one wires nothing (byte-identical)")


def main():
    print("=" * 60)
    print("the window overlay curtain")
    print("=" * 60)
    test_lockstep()
    test_pack_semantics()
    test_refvm()
    test_rooms_wiring()
    print("\n" + "=" * 60)
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All overlay-curtain checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
