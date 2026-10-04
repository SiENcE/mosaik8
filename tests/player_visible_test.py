#!/usr/bin/env python3
"""SHOW / HIDE THE PLAYER (`PLAYER_VISIBLE` 0x34).

The reference engine's Hide/Show Actor aimed at `$self$` in a scene or trigger script is
aimed at the PLAYER (it keeps the player at `actors[0]` and resolves `$self$`
there). Its `ACTOR_FLAG_HIDDEN` stops the actor being DRAWN and leaves
everything else alone - position, collision, and the movement handler all keep
running - which is why this is a draw flag and NOT the three-call player-less
room teardown (`core.clear_player()` + `player.hide()` +
`canim.set_player(255)`), which a script could not undo.

The platformer conversion's title screen opens with it: the menu cursor IS the player, hidden while
the logo is up and shown at the menu. Dropped, the cursor sat in the top-left
corner of the title screen from boot (measured: two OAM objects at screen
(0,-7) and (0,1) where the reference ROM has none).

The rule both halves of the engine must obey: `vm.canim.tick_player` ends in
"re-assert the metasprite every frame" (the Lynx present needs that), so it has
to stand down too - otherwise it puts a hidden player straight back on screen
the next frame, which is exactly the failure a player-less room hit before it
learnt to call `set_player(255)`.
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


def test_refvm():
    print("\n[RefVM]")
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "player_visible", "on": 0},
            {"event": "wait", "frames": 2},
            {"event": "player_visible", "on": 1},
            {"event": "stop"}]},
    ])
    vm = RefVM(prog.code, entry=prog.entry)
    check(vm.player_hidden == 0, "visible before anything runs")
    vm.frame()
    check(vm.player_hidden == 1, "hide takes")
    for _ in range(4):
        vm.frame()
    check(vm.player_hidden == 0, "show puts it back")


def test_it_is_a_draw_flag_only():
    print("\n[a DRAW flag, not a teardown]")
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "player_setpos", "x": 40, "y": 24},
            {"event": "player_visible", "on": 0},
            {"event": "stop"}]},
    ])
    vm = RefVM(prog.code, entry=prog.entry)
    vm.frame()
    check(vm.player_hidden == 1, "hidden")
    check((vm.player_x, vm.player_y) == (40, 24),
          "the player keeps its position while hidden (%d,%d)"
          % (vm.player_x, vm.player_y))


def test_engine_lockstep():
    print("\n[five-surface lockstep]")
    check(isa.OPS["PLAYER_VISIBLE"] == (0x34, ["u8"]),
          "the ISA row is 0x34, one u8 operand")
    core = _read("lib", "vm", "core.mos")
    check("const OP_PLAYER_VISIBLE = 0x34" in core,
          "core.mos's opcode const matches the ISA byte")
    check("if VM_OP_PLAYER_VISIBLE {" in core,
          "the arm carries its dispatch-pruning guard, spelled as the ISA "
          "names it")
    check("function set_player_vis" in core,
          "core.mos exposes the seam (it must not import vm.player)")
    player = _read("lib", "vm", "player.mos")
    check("var p_hidden" in player and "function set_hidden" in player,
          "vm.player owns the flag")
    check("if p_hidden == 1 {" in player.split("local function put_player")[1]
          .split("}")[0] + "}",
          "put_player stands down while hidden")
    canim = _read("lib", "vm", "canim.mos")
    tick = canim.split("local function tick_player")[1][:1600]
    # hidden = bit 0 of the R2 batched player.anim_flags read.
    check("pf & 1 != 0" in tick and "return" in tick.split("pf & 1 != 0")[1][:80],
          "tick_player stands down too (it re-asserts EVERY frame, so without "
          "this the player comes straight back)")
    check("p_hidden = 0" in player.split("local function clear_wide")[1][:1400],
          "the flag is cleared on every room setup (it must not leak into the "
          "next room)")
    rooms = _read_pkg("mosaik_vm", "rooms")
    check("uses_player_visible" in rooms
          and "core.set_player_vis(player.set_hidden)" in rooms,
          "rooms.mos wires the seam ONLY when a script uses it")
    spec = _read("docs", "vm8-spec.md")
    row = [l for l in spec.splitlines() if "| PLAYER_VISIBLE |" in l]
    check(bool(row) and "ACTOR_FLAG_HIDDEN" in row[0],
          "the spec documents it against the reference engine's own flag")


def test_byte_identical_off():
    print("\n[byte-identical off]")
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [{"event": "wait", "frames": 1},
                                    {"event": "stop"}]}])
    check(0x34 not in prog.code,
          "a program that never shows or hides the player emits no 0x34")


def main():
    print("=== player_visible_test ===")
    test_refvm()
    test_it_is_a_draw_flag_only()
    test_engine_lockstep()
    test_byte_identical_off()
    print()
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("player_visible_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
