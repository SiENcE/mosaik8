#!/usr/bin/env python3
"""THE OVERRIDING INPUT SCRIPT (the reference engine's `override`, events.c bit 0x80).

The reference engine's `EVENT_SET_INPUT_SCRIPT` carries an `override` checkbox. It is not
a nicety: `events.c` sets bit 7 of the slot and `events_update()` does

    if (*slot_ptr & 0x80) joy ^= key;     // reset key bit

*before* `core.c` calls `state_update()`, so an overriding attachment's button
never reaches the player's own movement handler.

The platformer conversion's title menu is built entirely on this. Its cursor IS the player, in a
TOPDOWN scene, and each menu trigger attaches up/down/a with override; the
left/right attachment has an EMPTY body and exists only to consume those
buttons. Measured on the two ROMs before the fix, holding DOWN for 100 frames:
the reference cursor moved ONE row (y 56 -> 64), ours moved nine and ran off
the bottom of the screen.

Coverage: the flag rides bit 7 of INPUT_ATTACH's button operand (so an
attachment without it is byte-identical), RefVM's consume mask, detach and
scene-change release, and the five-surface lockstep.
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


def _read_pkg(*parts):
    """Every .py of a package, concatenated - the source-grep tests below ask
    "does the generator mention X", and `rooms` is a package now."""
    d = os.path.join(ROOT, *parts)
    return "\n".join(_read(*(parts + (fn,)))
                     for fn in sorted(os.listdir(d)) if fn.endswith(".py"))


def _prog(override):
    ev = {"event": "input_attach", "button": "down", "script": "step"}
    if override:
        ev["override"] = True
    return mosaik_vm.Compiler().compile([
        {"name": "main", "events": [ev, {"event": "stop"}]},
        {"name": "step", "events": [
            {"event": "set_var", "var": "n", "expr": "n + 1"}]},
    ])


def test_byte_identical_without_override():
    print("\n[byte-identical without the flag]")
    plain = _prog(False)
    over = _prog(True)
    check(len(plain.code) == len(over.code),
          "the flag does not grow the bytecode (%d == %d bytes)"
          % (len(plain.code), len(over.code)))
    diff = [i for i, (a, b) in enumerate(zip(plain.code, over.code)) if a != b]
    check(len(diff) == 1,
          "exactly ONE byte differs (the button operand), not %d" % len(diff))
    if diff:
        i = diff[0]
        check(over.code[i] == plain.code[i] | 0x80,
              "the differing byte is the button id with bit 7 set "
              "(0x%02X -> 0x%02X)" % (plain.code[i], over.code[i]))
    # ... and the button id itself survives the flag
    check(isa.BUTTONS["down"] == plain.code[diff[0]] if diff else False,
          "the plain operand is the button id (%d)" % isa.BUTTONS["down"])


def test_refvm_consume_mask():
    print("\n[RefVM consume mask]")
    prog = _prog(True)
    vm = RefVM(prog.code, entry=prog.entry)
    check(vm.consume_mask == 0, "nothing is consumed before the attach runs")
    vm.frame()
    check(vm.consume_mask == (1 << isa.BUTTONS["down"]),
          "an overriding attach consumes exactly its own button "
          "(mask 0x%02X)" % vm.consume_mask)
    # the script still FIRES - override takes the button from the player
    # handler, it does not disable the attachment
    vm.frame(held=("down",))
    vm.frame()
    check(vm.heap[prog.variables["n"]] == 1,
          "the overriding script still fires on the press edge")

    # a NON-overriding attach consumes nothing
    plain = _prog(False)
    vm3 = RefVM(plain.code, entry=plain.entry)
    vm3.frame()
    check(vm3.consume_mask == 0,
          "a plain attach consumes nothing (mask 0x%02X)" % vm3.consume_mask)


def test_release():
    print("\n[the consume is released]")
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "input_attach", "button": "down", "script": "step",
             "override": True},
            {"event": "stop"}]},
        {"name": "step", "events": [{"event": "input_detach", "button": "down"}]},
    ])
    vm = RefVM(prog.code, entry=prog.entry)
    vm.frame()
    check(vm.consume_mask != 0, "consumed while attached")
    vm.frame(held=("down",))
    vm.frame()
    check(vm.consume_mask == 0, "input_detach gives the button back")

    # ... and so does a scene change, or an override would leak into a room
    # whose player it then cannot move.
    prog2 = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "input_attach", "button": "down", "script": "step",
             "override": True},
            {"event": "stop"}]},
        {"name": "step", "events": []},
    ])
    vm2 = RefVM(prog2.code, entry=prog2.entry)
    vm2.frame()
    check(vm2.consume_mask != 0, "consumed before the scene change")
    vm2._reset_scene_ui()
    check(vm2.consume_mask == 0, "reset_scene_ui gives the button back")


def test_lockstep():
    print("\n[five-surface lockstep]")
    check(isa.OPS["INPUT_ATTACH"] == (0x42, ["u8", "u16"]),
          "INPUT_ATTACH keeps its shape (the flag rides the operand)")
    core = _read("lib", "vm", "core.mos")
    check("in_over" in core and "publish_consume" in core,
          "core.mos carries the per-slot override flag + the publish helper")
    check("function set_input_consume" in core,
          "core.mos exposes the set_input_consume seam")
    check("set_input_consume" in core.split("export boot")[-1].splitlines()[0],
          "the seam is EXPORTED (an unexported seam is a link error, not a "
          "silent no-op)")
    player = _read("lib", "vm", "player.mos")
    # BITWISE, not logical: `and` here would make the test true for ANY
    # nonzero mask and consume every button (it did, and START stopped
    # working on the title screen).
    check("function set_input_consume" in player and "in_consume & b" in player,
          "vm.player gates its ONE held() choke point on the mask, bitwise")
    check("raw & 0x7F" in core and "m | btn_mask" in core,
          "core.mos masks and accumulates BITWISE too")
    check("set_input_consume" in player.split("export PLAYER_SLOT")[-1]
          .splitlines()[0], "vm.player exports set_input_consume")
    rooms = _read_pkg("mosaik_vm", "rooms")
    check("uses_input_consume" in rooms
          and "core.set_input_consume(player.set_input_consume)" in rooms,
          "rooms.mos wires the seam ONLY when a script overrides")
    spec = _read("docs", "vm8-spec.md")
    row = [l for l in spec.splitlines() if "| INPUT_ATTACH |" in l]
    check(bool(row) and "OVERRIDE" in row[0],
          "the spec's INPUT_ATTACH row documents the override flag")


def main():
    print("=== vm_input_override_test ===")
    test_byte_identical_without_override()
    test_refvm_consume_mask()
    test_release()
    test_lockstep()
    print()
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("vm_input_override_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
