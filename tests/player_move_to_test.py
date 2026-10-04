#!/usr/bin/env python3
"""PLAYER_MOVE_TO (0x1E) -- the WAITABLE player walk, A_MOVE_TO's twin.

The reference engine's `EVENT_ACTOR_MOVE_TO` resolves `$self$` to the PLAYER in any script
with no actor of its own (a scene or trigger script), and its own
`vm_actor_move_to` is a WAITABLE instruction: it marks the context waitable and
re-enters each frame until the actor arrives. The conversion had no player
equivalent, so `path/Path to Sample Town`'s last trigger - walk the player to
(1272, 88), then switch scene - snapped there in one frame.

What this pins:
  * the op steps once per frame and REWINDS 6 (opcode + u16 + u16 + u8) until it
    lands, so the script that follows it runs only on arrival;
  * the step CLAMPS to the target, so a speed that does not divide the distance
    still lands - without which the rewind never ends and the thread hangs;
  * `mode` is the reference engine's moveType axis order (0 both / 1 horizontal first /
    2 vertical first);
  * the FACING follows the step, which is what makes an animated player walk
    rather than slide (vm.canim reads the facing, and derives the walk state
    from the position delta);
  * the stepping body lives in `vm.player`, NOT in the interpreter - vm.core's
    arms are resident image on GB/SMS while the player pack banks, the rule the
    actor push already documents;
  * a game that never walks the player is byte-identical (the arm folds away
    with its VM_OP_ dispatch flag).
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm
from mosaik_vm import isa, refvm

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- " + detail) if detail else ""))


def _run(x0, y0, tx, ty, mode, speed, frames=400):
    p = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "player_move_to", "x": tx, "y": ty, "mode": mode},
            {"event": "set_var", "var": "arrived", "value": 1},
            {"event": "stop"}]}])
    vm = refvm.RefVM(p.code, entry=p.entry)
    vm.player_x, vm.player_y = x0, y0
    vm.player_speed = speed
    trail = []
    for f in range(frames):
        vm.frame()
        trail.append((vm.player_x, vm.player_y, vm.player_dir))
        if vm.heap[0]:
            return f, trail, vm
    return None, trail, vm


def main():
    # --- the ISA + the operand widths ---------------------------------------
    code, ops = isa.OPS["PLAYER_MOVE_TO"]
    check("PLAYER_MOVE_TO is 0x1E", code == 0x1E, "0x%02X" % code)
    check("its coords are u16 (the player's are WORLD pixels)",
          ops == ["u16", "u16", "u8"], repr(ops))

    # --- it WAITS, and the script continues only on arrival ------------------
    # the converted trigger: (1168, 104) -> (1272, 88) at the sample's walk 3
    f, trail, vm = _run(1168, 104, 1272, 88, "vertical", 3)
    check("the script waits for the walk", f is not None and f > 30,
          "arrived on VM frame %s" % f)
    check("it lands exactly on the target",
          (vm.player_x, vm.player_y) == (1272, 88),
          "(%d,%d)" % (vm.player_x, vm.player_y))
    check("one step per frame, no teleport",
          all(abs(trail[i][0] - trail[i - 1][0]) <= 3 and
              abs(trail[i][1] - trail[i - 1][1]) <= 3
              for i in range(1, len(trail))))

    # --- the axis order is the reference engine's moveType ------------------------------
    check("vertical first closes Y before X",
          trail[0][1] != 104 and trail[0][0] == 1168,
          "first step %r" % (trail[0],))
    _f, htrail, _v = _run(1168, 104, 1272, 88, "horizontal", 3)
    check("horizontal first closes X before Y",
          htrail[0][0] != 1168 and htrail[0][1] == 104,
          "first step %r" % (htrail[0],))
    _f, dtrail, _v = _run(1168, 104, 1272, 88, "diagonal", 3)
    check("diagonal moves both axes at once",
          dtrail[0][0] != 1168 and dtrail[0][1] != 104,
          "first step %r" % (dtrail[0],))

    # --- the CLAMP: a speed that does not divide the distance still lands ----
    for sp in (3, 5, 7, 16):
        f, _t, vm = _run(0, 0, 100, 0, "diagonal", sp)
        check("speed %d lands exactly (no overshoot, no hang)" % sp,
              f is not None and (vm.player_x, vm.player_y) == (100, 0),
              "frame %s at (%d,%d)" % (f, vm.player_x, vm.player_y))

    # --- the FACING follows the step (what animates the walk) ---------------
    _f, t, _v = _run(100, 0, 0, 0, "diagonal", 2)
    check("walking left faces LEFT", t[0][2] == 2, "dir %d" % t[0][2])
    _f, t, _v = _run(0, 0, 0, 100, "diagonal", 2)
    check("walking down faces DOWN", t[0][2] == 0, "dir %d" % t[0][2])

    # --- an already-arrived walk is a no-op, not a hang ---------------------
    f, _t, vm = _run(64, 64, 64, 64, "diagonal", 2, frames=10)
    check("a zero-length walk completes immediately", f == 0, "frame %s" % f)

    # --- the body is in the PACK, the arm in the interpreter ----------------
    with open(os.path.join(ROOT, "lib", "vm", "player.mos"), encoding="utf-8") as fh:
        player = fh.read()
    with open(os.path.join(ROOT, "lib", "vm", "core.mos"), encoding="utf-8") as fh:
        core = fh.read()
    check("vm.player owns the stepping body",
          re.search(r"function step_to\(tx: u16, ty: u16, mode: u8\) -> bool",
                    player) is not None)
    check("vm.player exports step_to",
          re.search(r"^\s*export .*\bstep_to\b", player, re.M) is not None)
    arm = re.search(r"case OP_PLAYER_MOVE_TO \{(.*?)\n            \}", core, re.S)
    check("vm.core has the arm", arm is not None)
    if arm:
        body = arm.group(1)
        check("the arm is guarded by its dispatch flag",
              "if VM_OP_PLAYER_MOVE_TO {" in body)
        check("the arm CALLS the pack rather than inlining the walk",
              "player.step_to(" in body)
        check("the arm rewinds 6 (opcode + u16 + u16 + u8)", "pcr -= 6" in body)
        check("the arm is thin (a fetch, a call, a rewind)",
              len([ln for ln in body.splitlines()
                   if ln.strip() and not ln.strip().startswith("--")]) <= 14)

    # --- byte-identical off: no PLAYER_MOVE_TO, no dispatch flag ------------
    p = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [{"event": "player_setpos", "x": 8, "y": 8},
                                    {"event": "stop"}]}])
    flags = isa.decode_blob(p.code)[0]
    check("a game that never walks the player folds the arm away",
          "VM_OP_PLAYER_MOVE_TO" not in flags,
          "%d dispatch flags, none of them this one" % len(flags))

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
