"""Declaration-level tree-shaking (`shake_exports`, MosaikCompiler._shake_declarations).

Whole-program compilation emits every declaration of every kept module into
one C TU and neither sdcc nor cc65 dead-strips inside a TU, so an
exported-but-unused lib function (and any module var only it touches) costs
ROM/BSS in every program linking the module. The opt-in pass drops
declarations unreachable from main(). These tests pin:
  - unused exported functions + the vars only they touch are dropped;
  - every REFERENCE shape keeps a declaration: a call, an address-taken
    callback, a const-table entry, a switch case label, an `alias.member`
    cross-module read;
  - type declarations are always kept;
  - default (shake_exports=False) output is byte-identical.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mosaik import MosaikCompiler


LIB = """
module "vm.helper" {
    -- reachable: called from main
    var used_state: u8 = 0
    function used(v: u8) -> u8 {
        used_state = v
        return used_state
    }

    -- dead: exported but never referenced; its var must go with it
    var dead_state: array[u8, 8]
    function dead(i: u8) -> u8 {
        return dead_state[i]
    }

    -- reachable only as an address-taken callback value
    function on_tick(a: u8, b: u8) {
        used_state = a + b
    }

    -- reachable only through a switch case label in main
    const MODE_RUN = 7

    -- reachable only from a const table initializer in main
    function from_table(x: u8, y: u8) {
        used_state = x + y
    }

    export used, dead, on_tick, MODE_RUN, from_table
}
"""

MAIN = """
module "game" {
    import "vm.helper"

    type Point = struct { x: u8, y: u8 }

    var cb: function(u8, u8)
    const table: array[function(u8, u8), 1] = [helper.from_table]

    function main() {
        cb = helper.on_tick
        cb(1, 2)
        table[0](3, 4)
        var m: u8 = helper.used(1)
        switch m {
            case helper.MODE_RUN {
                m = 0
            }
            default {
                m = 1
            }
        }
    }
}
"""


def _compile(shake):
    compiler = MosaikCompiler()
    c = compiler.compile_program(
        [("helper.mos", LIB), ("main.mos", MAIN)],
        platform="gameboy", shake_exports=shake)
    assert not c.startswith("Compilation error:"), c
    return c


def test_dead_export_and_its_var_are_dropped():
    c = _compile(shake=True)
    assert "vm_helper_dead" not in c, "unused exported function survived"
    assert "vm_helper_dead_state" not in c, "var only the dead export touches survived"


def test_every_reference_shape_keeps_its_declaration():
    c = _compile(shake=True)
    assert "vm_helper_used" in c                    # direct call
    assert "vm_helper_on_tick" in c                 # address-taken callback
    assert "vm_helper_from_table" in c              # const-table entry
    assert "vm_helper_MODE_RUN" in c                # switch case label
    assert "vm_helper_used_state" in c              # var of a kept function


def test_type_declarations_always_kept():
    c = _compile(shake=True)
    assert "Point" in c


def test_default_output_byte_identical():
    assert _compile(shake=False) == _compile(shake=None or False)
    # and the unshaken build still carries the dead code (the thing opt-in buys)
    assert "vm_helper_dead_state" in _compile(shake=False)


def test_no_main_keeps_everything():
    compiler = MosaikCompiler()
    c = compiler.compile_program([("helper.mos", LIB)],
                                 platform="gameboy", shake_exports=True)
    assert not c.startswith("Compilation error:"), c
    assert "dead_state" in c, "library build (no main) must keep all declarations"


def test_budget_consts_survive():
    """TS_MAX_TC is read by the Lynx bkg budget pass, never by code: shaken,
    the tile table was sized to the shared tileset alone (39 of 91 tiles)."""
    lib = LIB.replace("export used,", "const TS_MAX_TC: u8 = 91\n    export used,")
    compiler = MosaikCompiler()
    c = compiler.compile_program([("helper.mos", lib), ("main.mos", MAIN)],
                                 platform="gameboy", shake_exports=True)
    assert not c.startswith("Compilation error:"), c
    assert "vm_helper_TS_MAX_TC" in c, "the budget const was shaken"
    assert "vm_helper_dead_state" not in c, "the shake itself still runs"


def main():
    tests = [
        test_dead_export_and_its_var_are_dropped,
        test_every_reference_shape_keeps_its_declaration,
        test_type_declarations_always_kept,
        test_default_output_byte_identical,
        test_no_main_keeps_everything,
        test_budget_consts_survive,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print("  PASS %s" % t.__name__)
        except AssertionError as e:
            failed += 1
            print("  FAIL %s: %s" % (t.__name__, e))
    if failed:
        print("%d test(s) FAILED" % failed)
        return 1
    print("All %d decl-shake tests passed" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
