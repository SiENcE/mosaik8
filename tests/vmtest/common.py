"""Shared state + helpers for the VM8 toolchain test suite.

Split out of tests/vm_test.py (2026-08-26). tests/vm_test.py is still the one
entry point run_all.py discovers; the topic modules under tests/vmtest/ hold
the test functions and all share this module's FAILS/check.
"""

import os
import sys

# tests/vmtest/common.py -> the repo root is three levels up.
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import mosaik_vm as m  # noqa: E402

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("  FAIL:", msg)
    else:
        print("  ok:", msg)


# The Stage-0 hand-assembled spike blob -- the golden the compiler must reproduce.
# Hand-derived from the VM8 ISA table (mosaik_vm/isa.py OPS): A_ACTIVATE 0x20
# (i,tile,x,y), A_SPEED 0x23 (i,s), THREAD 0x06 (t:u16), WAIT 0x03 (n:u8),
# LOCK 0x04, A_MOVE_TO 0x22 (i,x,y), UI_TEXT 0x30 (id), UNLOCK 0x05, STOP 0x00,
# JUMP 0x01 (t:u16).
#
# The POSITION operands are u16 little-endian (2026-08-14): an actor's position
# is a WORLD pixel and a room runs past 255, so a u8 sent every scripted move in
# a big room to a wrapped destination. A_ACTIVATE is 7 B and A_MOVE_TO 6 B, so
# the layout is main @0 (39 B), route0 @39 (27 B), route1 @66 (27 B), total 93.
SPIKE_BLOB = [
    # main
    0x20, 0, 0, 20, 0, 40, 0,          # A_ACTIVATE 0 tile 0 -> (20, 40)
    0x20, 1, 1, 120, 0, 40, 0,         # A_ACTIVATE 1 tile 1 -> (120, 40)
    0x23, 0, 2, 0x23, 1, 1,            # A_SPEED
    0x06, 39, 0, 0x06, 66, 0,          # THREAD route0 / route1
    0x03, 120, 0x04,                   # WAIT 120, LOCK
    0x22, 0, 72, 0, 72, 0,             # A_MOVE_TO 0 -> (72, 72)
    0x30, 0, 0x05, 0x00,               # UI_TEXT 0, UNLOCK, STOP
    # route0 @39
    0x22, 0, 20, 0, 40, 0, 0x22, 0, 20, 0, 110, 0,
    0x22, 0, 60, 0, 110, 0, 0x22, 0, 60, 0, 40, 0, 0x01, 39, 0,
    # route1 @66
    0x22, 1, 120, 0, 40, 0, 0x22, 1, 120, 0, 110, 0,
    0x22, 1, 80, 0, 110, 0, 0x22, 1, 80, 0, 40, 0, 0x01, 66, 0,
]

SPIKE_SCRIPTS = [
    {"name": "main", "events": [
        {"event": "actor_activate", "actor": 0, "tile": 0, "x": 20, "y": 40},
        {"event": "actor_activate", "actor": 1, "tile": 1, "x": 120, "y": 40},
        {"event": "actor_set_speed", "actor": 0, "speed": 2},
        {"event": "actor_set_speed", "actor": 1, "speed": 1},
        {"event": "start_thread", "script": "route0"},
        {"event": "start_thread", "script": "route1"},
        {"event": "wait", "frames": 120},
        {"event": "lock"},
        {"event": "actor_move_to", "actor": 0, "x": 72, "y": 72},
        {"event": "text", "string": "HELLO FROM VM8\nPRESS A"},
        {"event": "unlock"},
        {"event": "stop"},
    ]},
    {"name": "route0", "loop": True, "events": [
        {"event": "actor_move_to", "actor": 0, "x": 20, "y": 40},
        {"event": "actor_move_to", "actor": 0, "x": 20, "y": 110},
        {"event": "actor_move_to", "actor": 0, "x": 60, "y": 110},
        {"event": "actor_move_to", "actor": 0, "x": 60, "y": 40},
    ]},
    {"name": "route1", "loop": True, "events": [
        {"event": "actor_move_to", "actor": 1, "x": 120, "y": 40},
        {"event": "actor_move_to", "actor": 1, "x": 120, "y": 110},
        {"event": "actor_move_to", "actor": 1, "x": 80, "y": 110},
        {"event": "actor_move_to", "actor": 1, "x": 80, "y": 40},
    ]},
]


def _run_expr(expr, setup=None):
    """Compile `set_var out = <expr>` (with optional setup events) + run RefVM,
    returning heap[out]."""
    events = list(setup or [])
    events.append({"event": "set_var", "var": "out", "expr": expr})
    events.append({"event": "stop"})
    prog = m.Compiler().compile([{"name": "main", "events": events}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(2)
    return vm.heap[prog.variables["out"]]
