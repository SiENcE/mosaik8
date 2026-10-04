"""Expressions, variables/state, conditionals, menu/call/switch, params.

Split out of tests/vm_test.py (2026-08-26); run via tests/vm_test.py."""
import os
import sys

from .common import *  # noqa: F401,F403 - FAILS/check/m + shared helpers
from .common import FAILS, _run_expr, check, m
from .common import SPIKE_BLOB, SPIKE_SCRIPTS, ROOT



def test_expressions():
    print("[RPN: expression evaluation]")
    cases = {
        "2 + 3 * 4": 14,            # precedence: * before +
        "(2 + 3) * 4": 20,         # parens
        "10 - 2 - 3": 5,           # left-assoc
        "10 % 3": 1,
        "-7 / 2": -3,              # truncate toward zero
        "max(4, 9)": 9,
        "min(4, 9)": 4,
        "abs(-8)": 8,
        "-5 + 2": -3,              # unary neg
        "not 0": 1,
        "3 >= 3 and 1 < 2": 1,
        "0 or 5 == 5": 1,
        "1 + 2 == 3": 1,           # +- binds tighter than ==
    }
    for expr, want in cases.items():
        got = _run_expr(expr)
        check(got == want, "%s == %d (got %d)" % (expr, want, got))


def test_expr_variables_and_state():
    print("[RPN: variables + engine-state reads]")
    got = _run_expr("gold + 5", setup=[{"event": "set_var", "var": "gold", "value": 10}])
    check(got == 15, "reads a heap variable (gold+5 == 15)")
    # actor_x / actor_y engine-state bridge
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_activate", "actor": 0, "tile": 0, "x": 77, "y": 33},
        {"event": "set_var", "var": "ax", "expr": "actor_x(0) + actor_y(0)"},
        {"event": "stop"},
    ]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(2)
    check(vm.heap[prog.variables["ax"]] == 110, "actor_x(0)+actor_y(0) == 110")


def test_conditionals():
    print("[IF: then/else branching + nesting]")
    def branch(cond_val):
        prog = m.Compiler().compile([{"name": "main", "events": [
            {"event": "set_var", "var": "g", "value": cond_val},
            {"event": "if", "cond": "g >= 10",
             "then": [{"event": "set_var", "var": "r", "value": 1}],
             "else": [{"event": "set_var", "var": "r", "value": 9}]},
            {"event": "stop"},
        ]}])
        vm = m.RefVM(prog.code, entry=prog.entry)
        vm.run(2)
        return vm.heap[prog.variables["r"]]
    check(branch(12) == 1, "true condition takes the then-branch")
    check(branch(3) == 9, "false condition takes the else-branch")

    # nested if inside a then-branch
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "set_var", "var": "a", "value": 5},
        {"event": "if", "cond": "a > 0",
         "then": [{"event": "if", "cond": "a == 5",
                   "then": [{"event": "set_var", "var": "hit", "value": 7}]}]},
        {"event": "stop"},
    ]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(2)
    check(vm.heap[prog.variables["hit"]] == 7, "nested if resolves inner then-branch")


def test_menu():
    print("[MENU: modal choice writes the picked index + branches]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "menu", "var": "choice", "row": 15, "options": ["A", "B", "C"]},
        {"event": "if", "cond": "choice == 1",
         "then": [{"event": "set_var", "var": "r", "value": 9}],
         "else": [{"event": "set_var", "var": "r", "value": 1}]},
        {"event": "stop"}]}])

    def pick(downs):
        vm = m.RefVM(prog.code, entry=prog.entry)
        vm.frame()                       # open (armed)
        for _ in range(downs):
            vm.frame(down=True)
            vm.frame(down=False)         # one nav step per tap
        vm.frame(a_pressed=True)         # confirm
        vm.run(2)
        return (vm.menu_log, vm.heap[prog.variables["choice"]], vm.heap[prog.variables["r"]])

    check(pick(0) == ([0], 0, 1), "select option 0 -> choice 0, else branch")
    check(pick(1) == ([1], 1, 9), "select option 1 -> choice 1, then branch")
    check(pick(2) == ([2], 2, 1), "select option 2 (nav clamps at count)")
    # arm(): an A held on the opening frame must NOT confirm (release required)
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame(a_pressed=True)
    check(vm.menu_open == 1 and not vm.menu_log, "arm() ignores the opening A press")


def test_call_ret():
    print("[CALL/RET: subroutines + nesting]")
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "call", "script": "bump"},
            {"event": "call", "script": "bump"}, {"event": "stop"}]},
        {"name": "bump", "sub": True, "events": [
            {"event": "set_var", "var": "n", "expr": "n + 1"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(3)
    check(vm.heap[prog.variables["n"]] == 2, "two CALLs to a sub each return to main")
    p2 = m.Compiler().compile([
        {"name": "main", "events": [{"event": "call", "script": "a"}, {"event": "stop"}]},
        {"name": "a", "sub": True, "events": [
            {"event": "set_var", "var": "x", "value": 1},
            {"event": "call", "script": "b"}]},
        {"name": "b", "sub": True, "events": [
            {"event": "set_var", "var": "y", "value": 2}]}])
    vm2 = m.RefVM(p2.code, entry=p2.entry)
    vm2.run(3)
    check(vm2.heap[p2.variables["x"]] == 1 and vm2.heap[p2.variables["y"]] == 2,
          "nested CALL unwinds correctly")


def test_player_state():
    print("[engine-state: player_setpos + player_x/y reads]")
    p = m.Compiler().compile([{"name": "main", "events": [
        {"event": "player_setpos", "x": 100, "y": 40},
        {"event": "set_var", "var": "s", "expr": "player_x() + player_y()"},
        {"event": "stop"}]}])
    vm = m.RefVM(p.code, entry=p.entry)
    vm.run(2)
    check(vm.heap[p.variables["s"]] == 140, "player_setpos then player_x()+player_y() == 140")


def test_rand():
    print("[RAND: rand(n) stays in [0, n)]")
    p = m.Compiler().compile([{"name": "main", "events": [
        {"event": "set_var", "var": "r", "expr": "rand(6)"}, {"event": "stop"}]}])
    vals = set()
    for seed in range(40):
        vm = m.RefVM(p.code, entry=p.entry)
        vm._rng = seed * 7 + 1
        vm.run(2)
        vals.add(vm.heap[p.variables["r"]])
    check(all(0 <= v < 6 for v in vals), "rand(6) always in [0, 6)")
    check(len(vals) > 1, "rand(6) produces variety")


def test_switch():
    print("[SWITCH: jump table dispatch + default fall-through]")
    def dispatch(sel):
        prog = m.Compiler().compile([{"name": "main", "events": [
            {"event": "set_var", "var": "sel", "value": sel},
            {"event": "switch", "value": "sel", "cases": [
                {"value": 0, "then": [{"event": "set_var", "var": "r", "value": 10}]},
                {"value": 1, "then": [{"event": "set_var", "var": "r", "value": 11}]},
                {"value": 2, "then": [{"event": "set_var", "var": "r", "value": 12}]},
            ], "default": [{"event": "set_var", "var": "r", "value": 99}]},
            {"event": "stop"}]}])
        vm = m.RefVM(prog.code, entry=prog.entry)
        vm.run(4)
        return vm.heap[prog.variables["r"]]
    check(dispatch(0) == 10, "value 0 -> case 0")
    check(dispatch(2) == 12, "value 2 -> case 2")
    check(dispatch(7) == 99, "no match -> default (fall-through)")
    # SWITCH with an expression selector + a negative case value
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "set_var", "var": "g", "value": 4},
        {"event": "switch", "value": "g - 6", "cases": [
            {"value": -2, "then": [{"event": "set_var", "var": "r", "value": 5}]}]},
        {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(4)
    check(vm.heap[prog.variables["r"]] == 5, "expression selector + negative i16 case value")


def test_engine_state_bridge():
    print("[GET_STATE/SET_STATE: curated engine-state bridge]")
    # camera_lock pins camera x/y; camera_x()/camera_y() read them back
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "camera_lock", "x": 48, "y": 24},
        {"event": "set_var", "var": "cx", "expr": "camera_x()"},
        {"event": "set_var", "var": "cy", "expr": "camera_y()"},
        {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(2)
    check(vm.heap[prog.variables["cx"]] == 48, "camera_x() reads the pinned x")
    check(vm.heap[prog.variables["cy"]] == 24, "camera_y() reads the pinned y")
    check(vm.cam_lock == 1, "writing camera x/y locks the scripted camera")
    # camera_release clears the lock
    prog2 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "camera_lock", "x": 8, "y": 8},
        {"event": "camera_release"},
        {"event": "stop"}]}])
    vm2 = m.RefVM(prog2.code, entry=prog2.entry)
    vm2.run(2)
    check(vm2.cam_lock == 0, "camera_release clears the lock")
    # set_state via a computed expr; scene() read after a change_scene
    prog3 = m.Compiler().compile([
        {"name": "main", "events": [{"event": "stop"}]},
        {"name": "door", "events": [
            {"event": "set_state", "state": "player_x", "expr": "20 + 30"},
            {"event": "set_var", "var": "px", "expr": "player_x()"},
            {"event": "change_scene", "room": 3, "x": 0, "y": 0}]}])
    vm3 = m.RefVM(prog3.code, entry=prog3.offsets["door"])
    vm3.run(2)
    check(vm3.heap[prog3.variables["px"]] == 50, "set_state player_x from an expression")
    check(vm3.cur_scene == 3, "scene tracks the CHANGE_SCENE room (ST_SCENE read source)")


def test_expr_params():
    # F1: numeric params (actor_move/actor_set_pos/player_setpos/projectile x/y/vx/vy)
    # accept EXPRESSIONS via the stack-arg _E opcode variants; a literal-only event
    # keeps its compact inline op (byte-identical).
    print("[F1: expression-valued numeric params (chase / shoot)]")
    # literal-only stays byte-identical (compact ops, no _E variant)
    lit = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_move", "actor": 0, "x": 40, "y": 40},
        {"event": "actor_set_pos", "actor": 1, "x": 7, "y": 9},
        {"event": "player_setpos", "x": 80, "y": 72},
        {"event": "projectile", "x": 20, "y": 30, "vx": 0, "vy": -4, "tile": 2, "life": 60},
        {"event": "stop"}]}])
    code = list(lit.code)
    check(0x24 in code and 0x21 in code and 0x11 in code and 0x60 in code,
          "literal-only keeps the compact ops")
    check(not any(op in code for op in (0x26, 0x27, 0x12, 0x61)),
          "literal-only emits NO _E variant (byte-identical)")
    # chase: actor_move(0, player_x(), player_y()) captures the player + walks there
    chase = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_activate", "actor": 0, "tile": 0, "x": 10, "y": 10},
        {"event": "set_state", "state": "player_x", "value": 40},
        {"event": "set_state", "state": "player_y", "value": 40},
        {"event": "actor_move", "actor": 0, "x": "player_x()", "y": "player_y()"},
        {"event": "stop"}]}])
    check(0x27 in list(chase.code), "an expr arg selects A_MOVE_START_E (0x27)")
    vm = m.RefVM(chase.code, entry=chase.entry)
    vm.run(40)
    check(vm.pos(0) == (40, 40), "actor chased to the player (40,40)")
    # shoot: projectile from the player position (expr x/y)
    shoot = m.Compiler().compile([{"name": "main", "events": [
        {"event": "set_state", "state": "player_x", "value": 48},
        {"event": "set_state", "state": "player_y", "value": 56},
        {"event": "projectile", "x": "player_x()", "y": "player_y()", "vx": 0, "vy": -4},
        {"event": "stop"}]}])
    check(0x61 in list(shoot.code), "an expr arg selects PROJ_LAUNCH_E (0x61)")
    vm = m.RefVM(shoot.code, entry=shoot.entry)
    vm.frame()
    p = vm.proj[0] if vm.proj else {}
    check(p.get("x") == 48 and p.get("y") == 52 and p.get("vy") == -4,
          "projectile launched at the player, flew v=-4")
    # player_setpos with a computed point
    ps = m.Compiler().compile([{"name": "main", "events": [
        {"event": "set_var", "var": "gx", "value": 33},
        {"event": "player_setpos", "x": "gx + 1", "y": "100"},
        {"event": "stop"}]}])
    check(0x12 in list(ps.code), "an expr arg selects PLAYER_SETPOS_E (0x12)")
    vm = m.RefVM(ps.code, entry=ps.entry)
    vm.run(2)
    check((vm.player_x, vm.player_y) == (34, 100), "player moved to (gx+1, 100)")
