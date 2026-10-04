#!/usr/bin/env python3
"""Verify vm-cutscene -- the LOCKed timeline + the VM8 RESET attract loop.

RefVM (deterministic, headless): the cutscene locks, both actors move
concurrently, and the closing `reset` (RAISE 1) kills the locked thread --
releasing the lock through the one §11 death path -- keeps the heap (`runs`
counts replays) and restarts `main`, forever.

    python projects/vm-cutscene/verify.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PROJ = os.path.dirname(os.path.abspath(__file__))

import mosaik_vm as mv

FAILS = []


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def main():
    print("[RefVM: locked concurrent timeline + RESET attract loop]")
    prog = mv.compile_path(os.path.join(PROJ, "scripts"))
    runs = prog.variables["runs"]

    vm = mv.RefVM(prog.code, entry=prog.offsets["main"])
    vm.run(1)
    check(vm.heap[runs] == 1, "first run counted (runs == 1)")
    check(vm.lockcount == 1, "the cutscene holds the LOCK")
    check(vm.actors[0].moving == 1 and vm.actors[1].moving == 1,
          "both actors move concurrently (non-blocking actor_move)")
    vm.run(60)
    check(vm.pos(0) != (16, 24), "native motion advances under the lock")

    # ride through several RESETs: each replay re-increments `runs` (the heap
    # SURVIVES a RESET, spec §4/§10) and re-takes the lock -- if the dying
    # thread's lock leaked, the restarted main could never run again.
    vm.run(300)
    check(len(vm.reset_log) >= 3, "the closing `reset` fired repeatedly (attract loop)")
    check(vm.heap[runs] >= 4, "the heap survives each RESET (runs keeps counting)")
    check(vm.any_active_threads() == 1, "exactly the restarted main is alive")

    if FAILS:
        print("verify vm-cutscene: %d FAILURE(S)" % len(FAILS))
        return 1
    print("verify vm-cutscene: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
