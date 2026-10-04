#!/usr/bin/env python3
"""vm.sram seals a save with a check byte seeded by a BUILD ID (review V-9):

  * the generated rooms.mos of a project that saves calls sram.set_build(N)
    BEFORE core.set_save, with N derived from the authored scripts (stable
    across regenerations of unchanged scripts, non-zero);
  * vm.sram writes the check byte last and validates it on has_save/restore.

Pure Python (the room generator + a source read); no toolchain needed."""
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik_vm.rooms.generate import generate_rooms  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  ok: " if cond else "  FAIL: ") + msg)
    if not cond:
        FAILS.append(msg)


def main():
    print("[vm.sram: check byte + build id]")
    sram = open(os.path.join(ROOT, "lib", "vm", "sram.mos"), encoding="utf-8").read()
    # EVERY export statement: the module has more than one since W7c, and
    # `split("export")[-1]` reads as "not exported" when a new line is appended
    # below the old one.
    exports = [ln for ln in sram.splitlines() if ln.strip().startswith("export ")]
    check("function set_build(id: u8)" in sram
          and any("set_build" in ln for ln in exports),
          "vm.sram exports set_build")
    # `slot_off +` since W7c: every read and write carries the save SLOT's byte
    # offset, which is a compile-time constant 0 when the program has no slots.
    check("save.write_u8(slot_off + OFF_CHECK, check_of())" in sram
          and sram.index("save.write_u8(slot_off + OFF_CHECK, check_of())")
          > sram.index("save.write_u8(slot_off + off + 1, hi)"),
          "persist writes the check byte LAST (it seals the payload)")
    check("if save.read_u8(slot_off + OFF_CHECK) != check_of()" in sram,
          "has_save/restore validate the check byte (via valid())")
    check("var x: u8 = build_seed" in sram, "the check is seeded with the build id")

    # A first-party sample that saves (the reference engine's three save slots, W7c):
    # its generated rooms.mos is where the build id has to land.
    proj = os.path.join(ROOT, "projects", "vm-saveslots")
    check(os.path.isdir(os.path.join(proj, "scripts")),
          "the saving sample projects/vm-saveslots is present")
    if FAILS:
        return _finish()
    tmp = tempfile.mkdtemp()
    try:
        out1 = os.path.join(tmp, "rooms1.mos")
        out2 = os.path.join(tmp, "rooms2.mos")
        generate_rooms(proj, out_path=out1)
        generate_rooms(proj, out_path=out2)
        t1 = open(out1, encoding="utf-8").read()
        t2 = open(out2, encoding="utf-8").read()
        m = re.search(r"sram\.set_build\((\d+)\)", t1)
        check(m is not None and 1 <= int(m.group(1)) <= 255,
              "rooms.mos of a saving project calls sram.set_build(1..255)")
        check(m is not None and t1.index("sram.set_build(") < t1.index("core.set_save("),
              "...before core.set_save")
        check(t1 == t2, "the id is stable across regenerations of unchanged scripts")
        committed = open(os.path.join(proj, "src", "rooms.mos"), encoding="utf-8").read()
        check("sram.set_build(" in committed,
              "the committed rooms.mos carries it (regenerate with `python -m mosaik_vm rooms`)")
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    return _finish()


def _finish():
    if FAILS:
        print("\nsave build id checks FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("\nAll save build id checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
