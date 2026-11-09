#!/usr/bin/env python3
"""E2: `[build] vm_heap` (a heap of up to 256 cells) and ARRAYS
(`scripts/heap.toml [arrays]`, `array_get` / `array_set`, GET_IND / SET_IND),
checked on ROMs (GB and SMS) and in the toolchain.

The ROM half is one generated world (tests/vm_fixture.py) with `vm_heap =
256` and a 220-cell array, so variables sit past cell 128 and the save is the
256-cell one. Its boot script writes state A and saves it to SLOT 1, writes
state B and saves it to slot 0, clears everything and loads slot 1; the
room's On Init sees state A, loads slot 0 and records state B. The check
reads the heap frame by frame off the `--debug` build's `.noi`:

  * array_set / array_get on the console (cell 200 of the array, a cell past
    128);
  * a 256-cell save restores every cell, the ones past 128 included (vm.sram's
    wrapping loop: a `while i < NCELLS` with a u8 counter never ends);
  * slot 1 is not inside slot 0: at 128 cells a slot is 264 B and the old
    shift formula would put slot 1 at 264, in the middle of a 520 B slot 0,
    so saving slot 0 second would corrupt it and its load would be refused.

The toolchain half: the build's defines, the compiler's cap following the
knob, and a program with no array or knob staying exactly as it was.
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import vm_fixture  # noqa: E402

FAILS = []


def check(cond, msg, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", msg,
                           ("  -- " + detail) if (detail and not cond) else ""))
    if not cond:
        FAILS.append(msg)


WORLD = """[[scene]]
name = "hall"
scene_type = "menu"
on_init = "after_load"
map = %s

[world]
module = "scenes"
map_w = 20
map_h = 18
vm = true

[tileset]
png = "tiles.png"
""" % vm_fixture.grid([[(x + y) % 2 for x in range(20)] for y in range(18)])

SCRIPTS = """[[script]]
name = "main"
events = [
  { event = "set_var", var = "mark", value = 3 },
  { event = "array_set", array = "big", index = 200, value = 1234 },
  { event = "set_var", var = "hi", value = -5 },
  { event = "save", slot = 1 },
  { event = "set_var", var = "mark", value = 9 },
  { event = "array_set", array = "big", index = 200, value = 777 },
  { event = "set_var", var = "hi", value = -6 },
  { event = "save" },
  { event = "set_var", var = "mark", value = 0 },
  { event = "array_set", array = "big", index = 200, value = 0 },
  { event = "set_var", var = "hi", value = 0 },
  { event = "load", slot = 1 },
  { event = "wait", frames = 1 },
]

[[script]]
name = "after_load"
events = [
  { event = "if", cond = "mark == 3", then = [
      { event = "array_get", var = "got_a", array = "big", index = "mark * 66 + 2" },
      { event = "wait", frames = 30 },
      { event = "load" },
      { event = "wait", frames = 1 } ] },
  { event = "if", cond = "mark == 9", then = [
      { event = "array_get", var = "got_b", array = "big", index = 200 },
      { event = "set_var", var = "done", value = 1 } ] },
]
"""

HEAP = "[arrays]\nbig = 220\n"


def symbols(noi):
    sym = {}
    with open(noi, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.split()
            if len(p) == 3 and p[0] == "DEF":
                sym[p[1]] = int(p[2], 16)
    return sym


def toolchain_checks():
    print("[toolchain: the knob, the cap, the arrays]")
    import mosaik_vm
    from mosaik_vm import heap as heapmod
    from mosaik_vm.compiler import Compiler
    import toml
    evs = toml.loads(SCRIPTS)["script"]
    try:
        Compiler(arrays={"big": 220}).compile(evs)
        check(False, "227 cells do not compile into the default 128-cell heap")
    except mosaik_vm.VmError as e:
        check("vm_heap" in str(e), "227 cells do not compile into the default "
              "128-cell heap, and the error names vm_heap", str(e))
    prog = Compiler(arrays={"big": 220}, heap_cap=256).compile(evs)
    v = prog.variables
    check(prog.arrays["big"] == (1, 220) and v["hi"] == 221 and max(v.values()) > 128,
          "with vm_heap = 256 the array is cells 1..220 and the variables follow",
          "%s %s" % (prog.arrays, v))
    for bad in (0, 257, True, "128"):
        try:
            heapmod.check_heap_cap(bad)
            check(False, "vm_heap %r is refused" % (bad,))
        except mosaik_vm.VmError:
            check(True, "vm_heap %r is refused" % (bad,))
    # an arrays-only heap.toml is not a pin; cells or save_version are
    lay = heapmod.validate_heap({"arrays": {"big": 3}})
    check(not heapmod.is_pinned(lay) and heapmod.heap_seed(lay) is None,
          "a heap.toml with only [arrays] pins nothing")
    check(heapmod.is_pinned(heapmod.validate_heap({"save_version": 2})),
          "a heap.toml with save_version alone still pins (E10 as before)")
    try:
        heapmod.validate_heap({"cells": ["a", "big"], "arrays": {"big": 128}})
        check(False, "a pinned list counts an array's whole length against the cap")
    except mosaik_vm.VmError:
        check(True, "a pinned list counts an array's whole length against the cap")
    # the build's defines
    import mosaik8_build
    cfg = mosaik8_build.BuildConfig.__new__(mosaik8_build.BuildConfig)
    for value, want in ((None, {}),
                        (256, {"VM_HEAP_CELLS": 256, "VM_HEAP_FULL": True,
                               "VM_HEAP_STD": False}),
                        (128, {"VM_HEAP_CELLS": 128, "VM_HEAP_FULL": False,
                               "VM_HEAP_STD": True}),
                        (200, {"VM_HEAP_CELLS": 200, "VM_HEAP_FULL": False,
                               "VM_HEAP_STD": False})):
        cfg.config = {"build": {} if value is None else {"vm_heap": value}}
        got = {k: v for k, v in cfg.get_pool_defines().items() if k.startswith("VM_HEAP")}
        check(got == want, "vm_heap %s -> %s" % (value, want), str(got))
    from mosaik.compiler import POOL_DEFINE_DEFAULTS
    check(POOL_DEFINE_DEFAULTS.get("VM_HEAP_CELLS") == heapmod.DEFAULT_HEAP == 128
          and POOL_DEFINE_DEFAULTS.get("VM_HEAP_STD") is True
          and POOL_DEFINE_DEFAULTS.get("VM_HEAP_FULL") is False,
          "the compiler's defaults are today's 128-cell heap")


def rom_check(platform):
    print("[%s: a 256-cell heap, an array, two save slots]" % platform)
    from mosaik8_build import gbdk_available
    if not gbdk_available():
        print("  skip: GBDK not installed")
        return
    if platform == "gameboy":
        try:
            from pyboy import PyBoy
        except ImportError:
            print("  skip: PyBoy not installed")
            return
    else:
        core = os.path.join(ROOT, "emu", "libretro", "genesis_plus_gx_libretro"
                            + (".dll" if os.name == "nt" else ".so"))
        if not os.path.isfile(core):
            print("  skip: the Genesis Plus GX core is not installed")
            return
    fx = vm_fixture.build(WORLD, SCRIPTS, platform, extra_build="vm_heap = 256",
                          files={"scripts/heap.toml": HEAP}, debug=True)
    try:
        if not fx.rom:
            check(False, "%s: the fixture builds" % platform,
                  (fx.build.stdout + fx.build.stderr)[-1500:])
            return
        noi = os.path.splitext(fx.rom)[0] + ".noi"
        sym = symbols(noi)
        heap = sym["_vm_core_heap"]
        # the cells, asked of the compiler (first-use order, arrays included)
        from mosaik_vm.loader import compile_path
        prog = compile_path(os.path.join(fx.root, "scripts"))
        V = prog.variables
        big = prog.arrays["big"][0]
        if platform == "gameboy":
            pb = PyBoy(fx.rom, window="null", sound_emulated=False)

            def read(a):
                return pb.memory[a]

            def step():
                pb.tick(1, False)
        else:
            sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
            from retro import Core
            c = Core(core, fx.rom)

            def read(a):
                return c.ram()[a - 0xC000]

            def step():
                c.run(1)

        def cell(i):
            v = read(heap + 2 * i) | read(heap + 2 * i + 1) << 8
            return v - 65536 if v >= 32768 else v

        seen_a = None
        final = None
        for _ in range(1500):
            step()
            if cell(V["got_a"]) == 1234 and seen_a is None:
                seen_a = (cell(V["mark"]), cell(big + 200), cell(V["hi"]),
                          cell(V["got_a"]))
            if cell(V["done"]) == 1:
                final = (cell(V["mark"]), cell(big + 200), cell(V["hi"]),
                         cell(V["got_b"]))
                break
        if platform == "gameboy":
            pb.stop(save=False)
        check(seen_a == (3, 1234, -5, 1234),
              "%s: slot 1 loads state A: mark, array cell 200 (heap cell 201) and a "
              "variable past cell 128" % platform, "saw %s" % (seen_a,))
        check(final == (9, 777, -6, 777),
              "%s: slot 0 then loads state B, and array_get reads its cell 200"
              % platform, "final %s" % (final,))
    finally:
        shutil.rmtree(fx.tmp, ignore_errors=True)


def main():
    toolchain_checks()
    for platform in ("gameboy", "sms"):
        rom_check(platform)
    print()
    if FAILS:
        print("FAILED (%d): %s" % (len(FAILS), FAILS))
        return 1
    print("All vm_heap / array checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
