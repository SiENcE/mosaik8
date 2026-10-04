#!/usr/bin/env python3
"""REPLACE A BACKGROUND TILE'S DATA (`BKG_TILE` 0x4D / `BKG_TILE_E` 0x4E).

The reference engine's `EVENT_REPLACE_TILE_XY` -> `vm_replace_tile_xy`, which reads the
map's tile INDEX at (x, y) and does `SetBankedBkgData(target_tile, 1, ...)`
(the reference engine's generated runtime, `core/vm_gameboy.c`). It is a
tile-DATA write, not a map write, and that indirection is the point: a changed
map cell is undone by the next scroll or room warm-up, where changed PIXELS
survive both - and every cell holding that index redraws at once. It is how a
game with no HUD layer draws a NUMBER on the background (the RPG check conversion spells
its wallet across four cells of row 1 and writes a digit under each; before
this the event was dropped and the conversion showed the artist's baked
placeholder "0123" for ever).

The destination is resolved where the write is AUTHORED - the map is static
data - so the runtime never reads a tilemap back and the operand is a plain
tile index. The `_E` twin is the common case: the reference engine's own `tileIndex` is a
ScriptValue and a digit readout computes it.
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


def _run(events, frames=40):
    prog = mosaik_vm.Compiler().compile([{"name": "main", "events": events}])
    vm = RefVM(prog.code, entry=prog.entry)
    for _ in range(frames):
        vm.frame()
    return vm, prog


def test_literal_and_computed():
    print("\n[both arms]")
    vm, prog = _run([
        {"event": "bkg_tile", "tile": 6, "src": 3},
        {"event": "set_var", "var": "gold", "value": 120},
        {"event": "bkg_tile", "tile": 7, "src": "gold % 1000 / 100"},
        {"event": "bkg_tile", "tile": 8, "src": "gold % 100 / 10"},
        {"event": "bkg_tile", "tile": 9, "src": "gold % 10"},
        {"event": "stop"}])
    check(vm.bkg_tiles.get(6) == 3, "a LITERAL src writes that replacement tile")
    check([vm.bkg_tiles.get(t) for t in (7, 8, 9)] == [1, 2, 0],
          "a COMPUTED src spells the digits of 120 across three cells - the "
          "readout the reference engine's own wallet script is")
    check(0x4D in prog.code and 0x4E in prog.code,
          "the literal arm keeps the compact 0x4D, the computed one takes 0x4E")


def test_literal_stays_literal():
    print("\n[the compact form is kept where it can be]")
    _vm, prog = _run([{"event": "bkg_tile", "tile": 1, "src": 2},
                      {"event": "stop"}])
    check(0x4E not in prog.code,
          "an all-literal write emits NO `_E` - the RPN arm is not paid for")
    i = prog.code.index(0x4D)
    check(list(prog.code[i:i + 3]) == [0x4D, 1, 2],
          "operands are (dst, src), both u8 and inline")


def test_engine_lockstep():
    print("\n[lockstep across the surfaces]")
    check(isa.OPS["BKG_TILE"] == (0x4D, ["u8", "u8"]),
          "the ISA row is 0x4D, (dst, src)")
    check(isa.OPS["BKG_TILE_E"] == (0x4E, ["u8"]),
          "...and 0x4E, (dst; pops src)")
    core = _read("lib", "vm", "core.mos")
    check("const OP_BKG_TILE = 0x4D" in core
          and "const OP_BKG_TILE_E = 0x4E" in core,
          "core.mos's opcode consts match the ISA bytes")
    check("if VM_OP_BKG_TILE {" in core and "if VM_OP_BKG_TILE_E {" in core,
          "both arms carry their dispatch-pruning guards")
    check("var g_bkg_tile: function(u8, u8, u8)" in core
          and "function set_bkg_tile(" in core
          and "set_bkg_tile," in core,
          "the writer is an OPT-IN seam (room, dst, src) and is exported - a "
          "world with no replacement bank links none of it")
    check("if has_bkg_tile == 1 {" in core,
          "...and both arms stand down when nothing registered one")
    spec = _read("docs", "vm8-spec.md")
    rows = [l for l in spec.splitlines() if l.startswith("| 4D | BKG_TILE |")
            or l.startswith("| 4E | BKG_TILE_E |")]
    check(len(rows) == 2 and "VM_REPLACE_TILE" in rows[0],
          "the spec documents both against the reference engine's own instruction")


def test_scenes_module():
    print("\n[the world's replacement bank]")
    import mosaik_scenes
    base = os.path.join(ROOT, "projects", "vm-bganim", "assets")
    world, _b = mosaik_scenes.load_world(os.path.join(base, "world.toml"))
    plain = mosaik_scenes.transpile(world, base)
    check("replace_tile" not in plain,
          "a world with NO [[replace_tile]] emits none of it (byte-identical)")
    world["replace_tile"] = [{"name": "swap", "png": "tiles.png"}]
    src = mosaik_scenes.transpile(world, base)
    check("const RTILES:" in src and "function replace_tile(room: u8, "
          "dst: u8, src: u8)" in src,
          "a world WITH one bakes the bank + the writer")
    check("if src >= RTILE_COUNT {" in src,
          "an out-of-bank src is a no-op, not a wild read - the value comes "
          "off the expression stack, so a script's arithmetic decides it")
    check("RTILE_COUNT, replace_tile" in src.split("export ")[-1],
          "...and exports them, so the generated core.set_bkg_tile call and "
          "this definition are decided by the same world fact")


def test_byte_identical_off():
    print("\n[byte-identical off]")
    _vm, prog = _run([{"event": "wait", "frames": 1}, {"event": "stop"}])
    check(0x4D not in prog.code and 0x4E not in prog.code,
          "a program that writes no background tile emits neither opcode, so "
          "both arms prune away and the resident image is unchanged")


if __name__ == "__main__":
    print("=== bkg_tile_test: BKG_TILE / BKG_TILE_E ===")
    test_literal_and_computed()
    test_literal_stays_literal()
    test_engine_lockstep()
    test_scenes_module()
    test_byte_identical_off()
    print()
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        sys.exit(1)
    print("bkg_tile_test: all passed")
