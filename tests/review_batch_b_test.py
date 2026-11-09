#!/usr/bin/env python3
"""The 2026-11-03 engine review's BATCH B: the reference VM (`refvm.py`)
agrees with the console (plan `docs/engine-review-open-items-plan.md` in the
studio, 2026-11-07). Harness only: no ROM byte changes.

  H4   DATA_PEEK wrote `heap[dst]` unguarded (an IndexError past the heap).
  H5   INPUT_ATTACH with a button id >= 8 indexed past the 8-entry table.
  #26  PAL_SET returned the STRING "CONT", which the scheduler read as a
       yield: the thread lost its slice.
  #27  SHAKE under the wait latch was counted down twice (the op and frame()).
  #28  A_STOP_UPDATE killed EVERY thread bound to the actor, the executing
       one included; the console kills the actor's registered On Update only
       (refvm: `set_update`).
  #29  state 35 (`scene_update_paused`) survived a scene change.
  LOW  A_ACTIVATE reset less than vm.actor.activate (facing, visibility, box);
       the byte latches armed on any nonzero (console: menu_cancel on exactly
       1, fade_style by its low byte); the routine ring was unbounded (the
       hUGE pack's holds 3, the oldest dropped); TBL_GET past a row's columns
       crashed; CHANGE_SCENE_E kept full-width parameters; the harness's
       `held=` did not reach MENU navigation.

Mutation: against the pre-batch refvm every check here but the controls fails.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm as m  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _vm(code, entry=0):
    return m.RefVM(bytes(code), entry=entry)


def _runs(label, fn):
    """fn() must run without raising; returns its value, or None."""
    try:
        return fn()
    except Exception as e:                      # noqa: BLE001 - a crash IS the defect
        check(label, False, "%s: %s" % (type(e).__name__, e))
        return None


def h4_h5():
    print("\n[H4 / H5: no index past a table]")
    vm = _vm([m.OPS["DATA_PEEK"][0], 0, 200, m.OPS["STOP"][0]])
    if _runs("DATA_PEEK into heap cell 200 runs", lambda: (vm.run(2), True)[1]):
        check("DATA_PEEK into heap cell 200 is a no-op (the heap stays %d cells)"
              % len(vm.heap), len(vm.heap) == m.Compiler.HEAP_CAP)
    attach = [m.OPS["INPUT_ATTACH"][0], 8, 0, 0, m.OPS["WAIT"][0], 30, m.OPS["STOP"][0]]
    vm = _vm(attach)
    ok = _runs("INPUT_ATTACH on button 8 runs", lambda: [vm.frame(held=("a",))
                                                         for _ in range(4)])
    if ok is not None:
        check("INPUT_ATTACH on button 8 never fires (core's btn_held answers 0)",
              sum(vm.active) <= 1)


def pal_set():
    print("\n[#26: PAL_SET continues]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "palette_set", "layer": "background", "slot": 0, "pal": 1},
        {"event": "set_var", "var": "after", "value": 7},
        {"event": "stop"}]}])
    vm = _vm(prog.code, prog.entry)
    vm.frame()
    check("the op after PAL_SET runs in the SAME frame",
          vm.heap[prog.variables["after"]] == 7, vm.heap[prog.variables["after"]])


def shake():
    print("\n[#27: a waiting SHAKE blocks N frames, not N/2]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "shake", "frames": 10, "amp": 2, "wait": True},
        {"event": "set_var", "var": "done", "value": 1},
        {"event": "stop"}]}])
    vm = _vm(prog.code, prog.entry)
    n = 0
    while vm.heap[prog.variables["done"]] == 0 and n < 40:
        vm.frame()
        n += 1
    check("the thread resumes after 10 frames of shake (11th frame)", n == 11, n)
    check("...and the one-shot latch is spent", vm.shk_wait == 0 and vm.shk_axis == 0)


def stop_update():
    print("\n[#28: A_STOP_UPDATE kills the registered On Update only]")
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "actor_stop_update", "actor": 0},
            {"event": "set_var", "var": "main_after", "value": 1},
            {"event": "wait", "frames": 2},
            {"event": "actor_stop_update", "actor": 2},
            {"event": "wait", "frames": 3},
            {"event": "stop"}]},
        {"name": "patrol", "loop": True, "events": [
            {"event": "set_var", "var": "ticks", "expr": "ticks + 1"},
            {"event": "idle"}]},
        {"name": "talk", "loop": True, "events": [
            {"event": "set_var", "var": "talks", "expr": "talks + 1"},
            {"event": "idle"}]}])
    vm = _vm(prog.code, prog.entry)
    up = vm._spawn(prog.offsets["patrol"])
    talk = vm._spawn(prog.offsets["talk"])
    reg = getattr(vm, "set_update", None)
    if reg is None:
        check("RefVM.set_update registers an On Update", False)
        return
    reg(up, 2)
    vm.set_self(talk, 2)                     # an On Interact bound to the SAME actor
    vm.frame()
    check("the main thread (SELF 0) stopping actor 0 does NOT kill itself",
          vm.heap[prog.variables["main_after"]] == 1)
    for _ in range(4):
        vm.frame()
    check("the registered On Update is dead", vm.active[up] == 0)
    check("...and the other thread bound to the actor lives on", vm.active[talk] == 1)


def scene_pause():
    print("\n[#29: state 35 is per scene]")
    vm = _vm([m.OPS["STOP"][0]])
    vm._set_state(m.STATES["scene_update_paused"], 1)
    vm._reset_scene_ui()
    check("reset_scene_ui clears scene_update_paused", vm.scene_update_paused == 0)


def activate():
    print("\n[A_ACTIVATE resets what vm.actor.activate resets]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_activate", "actor": 1, "tile": 0, "x": 8, "y": 8},
        {"event": "stop"}]}])
    vm = _vm(prog.code, prog.entry)
    a = vm.actors[1]
    a.visible, a.dir, a.box = 0, 3, (24, 24, 2, 2)
    vm.frame()
    check("a placed actor is drawn, faces down and has the 8x8 fallback box",
          (a.visible, a.dir, a.box) == (1, 0, (8, 8, 0, 0)), (a.visible, a.dir, a.box))


def latches():
    print("\n[byte latches arm as the console's do]")
    vm = _vm([m.OPS["STOP"][0]])
    vm._set_state(m.STATES["menu_cancel"], 2)
    check("menu_cancel = 2 is stored as the console's u8 (2), which is not armed",
          vm.menu_cancel == 2, vm.menu_cancel)
    vm._set_state(m.STATES["fade_style"], 256)
    check("fade_style = 256 is the u8 0 (towards white), as fx.set_style reads it",
          vm.fade_style == 0, vm.fade_style)


def routine_ring():
    print("\n[the routine ring holds 3, the oldest dropped]")
    vm = _vm([m.OPS["STOP"][0]])
    push = getattr(vm, "push_routine", None)
    if push is None:
        check("RefVM.push_routine exists", False)
        return
    for p in (1, 2, 3, 4, 5):
        push(p)
    check("five pushes leave the newest three", vm.music_routine_q == [3, 4, 5],
          vm.music_routine_q)


def tables():
    print("\n[TBL_GET past a row's columns]")
    from mosaik_vm.tables import table_cell
    blob = bytes([6, 0, 1, 0, 2, 1, 7, 9])      # one table: 1 row, stride 2, u8
    v = _runs("a column past the row runs", lambda: table_cell(blob, 0, 0, 5))
    check("a column past the row reads 0", v == 0, v)
    check("an in-range cell still reads (control)", table_cell(blob, 0, 0, 1) == 9)


def change_scene_e():
    print("\n[CHANGE_SCENE_E takes the console's widths]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "set_var", "var": "r", "value": 300},
        {"event": "change_scene", "room": "r", "x": "0 - 1", "y": 8},
        {"event": "stop"}]}])
    vm = _vm(prog.code, prog.entry)
    vm.frame()
    got = vm.change_log[-1] if vm.change_log else None
    check("room 300 arrives as the u8 44, x = -1 as the u16 65535",
          got == (44, 65535, 8), got)


def held_menu():
    print("\n[the harness's held= reaches MENU navigation]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "menu", "var": "pick", "options": ["A", "B", "C"]},
        {"event": "stop"}]}])
    vm = _vm(prog.code, prog.entry)
    vm.frame()
    vm.frame()
    vm.frame(held=("down",))
    vm.frame()
    vm.frame(a_pressed=True)
    vm.frame()
    check("a held DOWN moved the cursor: the pick is 1",
          vm.heap[prog.variables["pick"]] == 1, vm.heap[prog.variables["pick"]])


def main():
    print("=== review_batch_b_test ===")
    for fn in (h4_h5, pal_set, shake, stop_update, scene_pause, activate, latches,
               routine_ring, tables, change_scene_e, held_menu):
        try:
            fn()
        except Exception as e:                  # noqa: BLE001 - report and go on
            check("%s ran" % fn.__name__, False, "%s: %s" % (type(e).__name__, e))
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
