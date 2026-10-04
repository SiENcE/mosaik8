#!/usr/bin/env python3
"""The reference engine's THREE save slots (W7c) - the reference VM's `src/core/load_save.c`.

The slot reaches an op through a ONE-SHOT LATCH (`save_slot`, state 40) rather
than through a wider `SAVE` / `LOAD`, because widening those would change the
blob format for every project that already saves. Everything here exists to
keep that decision honest:

  * the two ops' operand lists are UNCHANGED and the latch defaults to slot 0,
    so a project that never names one is byte-identical;
  * the latch is CONSUMED by every operation - save, load, clear, peek and
    `save_exists()` alike - so nothing leaks into the next one;
  * `vm.sram` adds the slot offset in exactly ONE place, and that place folds
    to a constant zero when the program has no slots;
  * the second half of the save seam is ADDITIVE: `core.set_save` still takes
    its three arguments, so every shell that registers a save today is
    untouched.

    python tests/save_slots_test.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mosaik8_build                                             # noqa: E402
import mosaik_vm as m                                            # noqa: E402
from mosaik_vm import isa                                        # noqa: E402
from mosaik_vm.rooms import emit_rooms_mos                       # noqa: E402

FAILS = []


def check(ok, what):
    print("  %s: %s" % ("ok" if ok else "FAIL", what))
    if not ok:
        FAILS.append(what)


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _run(events, frames=6):
    prog = m.Compiler().compile([{"name": "main", "events": events}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(frames)
    return vm, prog


def main():
    print("[the ISA: three slots, two ops, one latch]")
    check(isa.SAVE_SLOTS == 3, "the engine has the reference engine's three slots")
    check(isa.STATES["save_slot"] == 40, "the latch is state 40")
    check(isa.OPS["SAVE"] == (0x44, []) and isa.OPS["LOAD"] == (0x45, []),
          "SAVE and LOAD keep their EMPTY operand lists (the blob format of "
          "every project that already saves)")
    check(isa.OPS["DATA_CLEAR"] == (0x47, []),
          "DATA_CLEAR takes no operand - the slot is the latch")
    check(isa.OPS["DATA_PEEK"] == (0x48, ["u8", "u8"]),
          "DATA_PEEK takes (src cell, dest cell), the slot being the latch")

    print("\n[lowering: slot 0 emits NOTHING]")
    plain = m.Compiler().compile([{"name": "main", "events": [
        {"event": "save"}, {"event": "load"}, {"event": "stop"}]}])
    zero = m.Compiler().compile([{"name": "main", "events": [
        {"event": "save", "slot": 0}, {"event": "load", "slot": 0},
        {"event": "stop"}]}])
    check(bytes(plain.code) == bytes(zero.code),
          "an explicit slot 0 compiles to the same bytes as no slot at all")
    one = m.Compiler().compile([{"name": "main", "events": [
        {"event": "save", "slot": 1}, {"event": "stop"}]}])
    check(len(one.code) > len(plain.code),
          "...and a real slot prefixes the latch write")
    try:
        m.Compiler().compile([{"name": "main", "events": [
            {"event": "save", "slot": 7}, {"event": "stop"}]}])
        bad = False
    except Exception:
        bad = True
    check(bad, "an out-of-range slot is REFUSED, not masked onto a real one")

    print("\n[the RefVM: three slots really are three]")
    vm, prog = _run([
        {"event": "set_var", "var": "mark", "value": 11},
        {"event": "save", "slot": 0},
        {"event": "set_var", "var": "mark", "value": 22},
        {"event": "save", "slot": 1},
        {"event": "set_var", "var": "mark", "value": 33},
        {"event": "save", "slot": 2},
        {"event": "set_var", "var": "mark", "value": 99},
        {"event": "load", "slot": 1},
        {"event": "stop"}])
    mi = prog.variables["mark"]
    check(vm.heap[mi] == 22,
          "loading slot 2 gives back slot 2 (a one-slot engine reads 33)")
    check([s is not None for s in vm.saved] == [True] * 3,
          "...and all three slots hold a save")

    print("\n[the latch is CONSUMED by every operation]")
    vm, prog = _run([
        {"event": "set_var", "var": "mark", "value": 7},
        {"event": "save", "slot": 2},      # -> slot 3
        {"event": "set_var", "var": "mark", "value": 1},
        {"event": "save"},                 # no slot: must be slot 1 again
        {"event": "stop"}])
    check(vm.saved[0] is not None and vm.saved[0]["heap"][prog.variables["mark"]] == 1,
          "a save with no slot goes to slot 1 even after one that named slot 3")
    check(vm.saved[2]["heap"][prog.variables["mark"]] == 7,
          "...and slot 3 still holds what it was given")
    check(vm.save_slot == 0, "the latch reads 0 again")

    print("\n[peek: no load, and 0 for an empty slot]")
    vm, prog = _run([
        {"event": "set_var", "var": "mark", "value": 5},
        {"event": "save", "slot": 1},
        {"event": "set_var", "var": "mark", "value": 9},
        {"event": "save_peek", "var": "probe", "src": "mark", "slot": 1},
        {"event": "stop"}])
    check(vm.heap[prog.variables["probe"]] == 5,
          "peek reads the SAVED value out of the slot")
    check(vm.heap[prog.variables["mark"]] == 9,
          "...and does NOT load: the live variable is untouched")
    vm, prog = _run([
        {"event": "set_var", "var": "probe", "value": 42},
        {"event": "save_peek", "var": "probe", "src": "probe", "slot": 2},
        {"event": "stop"}])
    check(vm.heap[prog.variables["probe"]] == 0,
          "an EMPTY slot peeks 0 (the reference engine's own `_ifConst` tail)")

    print("\n[clear: one slot, and only the signature]")
    vm, prog = _run([
        {"event": "set_var", "var": "mark", "value": 4},
        {"event": "save", "slot": 0},
        {"event": "save", "slot": 1},
        {"event": "save_clear", "slot": 1},
        {"event": "set_var", "var": "mark", "value": 6},
        {"event": "load", "slot": 1},
        {"event": "stop"}])
    check(vm.heap[prog.variables["mark"]] == 6,
          "loading a cleared slot is a no-op")
    check(vm.saved[0] is not None, "...and the other slots are untouched")
    check(vm.saved[1] is None, "...while the cleared one reads empty")

    print("\n[save_exists reads the LATCHED slot]")
    vm, prog = _run([
        {"event": "set_var", "var": "mark", "value": 1},
        {"event": "save", "slot": 2},
        {"event": "set_state", "state": "save_slot", "value": 1},
        {"event": "if", "cond": "save_exists()",
         "then": [{"event": "set_var", "var": "got", "value": 1}],
         "else": [{"event": "set_var", "var": "got", "value": 2}]},
        {"event": "stop"}])
    check(vm.heap[prog.variables["got"]] == 2,
          "an empty slot answers 0 even though another slot holds a save")

    print("\n[vm.sram: the offset is added in ONE place, and it folds]")
    sram = _src("lib", "vm", "sram.mos")
    check("const STRIDE = OFF_HEAP + NCELLS * 2" in sram,
          "the stride is DERIVED from the layout, not a hardcoded 264")
    check("const slot_off = 0" in sram,
          "the off arm makes the offset a compile-time CONSTANT, so "
          "`save.read_u8(slot_off + i)` folds back to `save.read_u8(i)`")
    check("save.read_u8(slot_off" in sram and "save.write_u8(slot_off" in sram,
          "...and every read and write goes through it")
    check("rd8(" not in sram and "wr8(" not in sram,
          "no wrapper FUNCTION: the first cut used one and cost vm-save 96 B "
          "of ROM for a feature it does not use")
    body = sram[sram.index("function set_slot(n: u8) {"):]
    body = body[:body.index("\n            }")]
    check("if v >= SLOTS" in body,
          "an out-of-range slot is validated, not masked onto a real one")
    for fn in ("has_save", "persist", "restore", "clear", "peek"):
        seg = sram[sram.index("function %s(" % fn):]
        seg = seg[:seg.index("\n        }")]
        check("consume()" in seg, "%s() consumes the latch" % fn)

    print("\n[vm.core: the seam is ADDITIVE and both arms are guarded]")
    core = _src("lib", "vm", "core.mos")
    check("function set_save(persist: function(), restore: function(), "
          "has: function() -> u8)" in core,
          "core.set_save keeps its three arguments, so every existing shell "
          "and generated rooms.mos is untouched")
    check("function set_save_slots(slot: function(u8), clear: function()," in core,
          "...and the slot half is a SECOND registration")
    check(core.count("function set_save_slots(") == 2,
          "...defined in both arms of its fold, because an export list cannot "
          "be conditional")
    check("case OP_DATA_CLEAR {\n                if VM_OP_DATA_CLEAR {" in core,
          "the CLEAR arm is guarded by its own VM_OP_ flag (the pruning rule)")
    check("case OP_DATA_PEEK {\n                if VM_OP_DATA_PEEK {" in core,
          "...and so is PEEK")
    check("if sid == ST_SAVE_SLOT {" in core, "the latch has a SET_STATE arm")

    print("\n[rooms.mos + the build flag]")
    off = emit_rooms_mos({"types": ["topdown"]})
    check("set_save" not in off,
          "a project that never saves registers nothing (unchanged)")
    d = mosaik8_build._vm_dispatch_defines([("scripts.mos", plain.to_scripts_mos())])
    check(d.get("VM_SAVE_SLOTS") is False,
          "a blob that saves to slot 0 only states VM_SAVE_SLOTS false")
    peek = m.Compiler().compile([{"name": "main", "events": [
        {"event": "save_peek", "var": "a", "src": "b"}, {"event": "stop"}]}])
    d = mosaik8_build._vm_dispatch_defines([("scripts.mos", peek.to_scripts_mos())])
    check(d.get("VM_SAVE_SLOTS") is True,
          "...and a blob carrying PEEK states it true even with no latch write")
    d = mosaik8_build._vm_dispatch_defines([("scripts.mos", one.to_scripts_mos())])
    check(d.get("VM_SAVE_SLOTS") is True,
          "...as does one that only writes the latch")

    print("\n%s" % ("FAILED: %d" % len(FAILS) if FAILS else "all ok"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
