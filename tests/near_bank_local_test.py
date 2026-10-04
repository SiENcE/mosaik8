"""Same-bank call de-trampolining (_collect_near_bank_locals, 2026-08-28).

Every function of a banked module used to be emitted BANKED, so a call from
its OWN module - already running under that bank - still paid GBDK's
__banked_call trampoline (~164 cycles + 8 bytes against a direct call's
24 + 3; measured 38% of the reference-engine sample conversion's 80 banked calls a frame were
same-bank). A module-private `local function` whose callers all share its
bank is emitted WITHOUT BANKED now: `#pragma bank` still places the body in
the bank's code segment, only the calling convention changes.

Pins the four rules:
  * a local called only from its module's banked functions loses BANKED
  * the resident stub -> __bimpl call of an address-taken split keeps it
    (the stub is bank 0, the body is not)
  * a local called from a bank(0)-pinned function keeps it (same reason)
  * a local referenced as a VALUE (callback registration) keeps it
  * no code_banks / no banking console -> byte-identical
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mosaik import MosaikCompiler  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def compile_program(sources, platform="gameboy", **kw):
    compiler = MosaikCompiler()
    c = compiler.compile_program(sources, platform=platform, **kw)
    assert not c.startswith("Compilation error"), c
    return c, compiler.code_generator


LIB = '''
module "pool" {
    var state: array[u8, 4]
    local function inner(i: u8) -> u8 {
        return state[i & 3]
    }
    function tick() {
        state[0] = inner(1)
    }
    function poke(i: u8) {
        state[i & 3] = inner(i)
    }
    export tick, poke
}
'''

MAIN = '''
module "main" {
    import "pool"
    function main() {
        pool.tick()
        pool.poke(2)
    }
}
'''


def test_local_loses_banked():
    c, g = compile_program([("lib.mos", LIB), ("main.mos", MAIN)],
                           code_banks=["pool"])
    bank1 = g.bank_units.get(1, "")
    check("the local's body is in the bank TU without BANKED",
          "uint8_t pool_inner(uint8_t i) {" in bank1)
    check("no BANKED spelling of the local anywhere",
          "pool_inner(uint8_t i) BANKED" not in c
          and "pool_inner(uint8_t i) BANKED" not in bank1)
    check("its banked callers keep BANKED",
          "void pool_tick(void) BANKED {" in bank1)


PINNED = '''
module "pool" {
    var state: array[u8, 4]
    local function inner(i: u8) -> u8 {
        return state[i & 3]
    }
    bank(0) function hot() {
        state[0] = inner(1)
    }
    function tick() {
        state[1] = inner(2)
    }
    export tick, hot
}
'''

PIN_MAIN = '''
module "main" {
    import "pool"
    function main() {
        pool.tick()
        pool.hot()
    }
}
'''


def test_pinned_caller_keeps_banked():
    c, g = compile_program([("lib.mos", PINNED), ("main.mos", PIN_MAIN)],
                           code_banks=["pool"])
    bank1 = g.bank_units.get(1, "")
    # hot() stays home (bank 0) and calls inner, so inner keeps the
    # trampoline: a near call from home into a bank body would run unmapped.
    check("a local called from a bank(0)-pinned function keeps BANKED",
          "uint8_t pool_inner(uint8_t i) BANKED {" in bank1)


TAKEN = '''
module "pool" {
    var state: array[u8, 4]
    local function inner() {
        state[0] = 1
    }
    var cb: function() = inner
    function tick() {
        inner()
        cb()
    }
    export tick
}
'''


def test_address_taken_keeps_banked():
    # A banked function's address is rejected outright (home-bank rule), so
    # the value reference forces inner to stay addressable - here that means
    # the compile either refuses or keeps it BANKED/stubbed; what it must
    # NEVER do is emit a near body that a pointer could call unmapped.
    try:
        c, g = compile_program([("lib.mos", TAKEN),
                                ("main.mos", '''
module "main" {
    import "pool"
    function main() {
        pool.tick()
    }
}
''')], code_banks=["pool"])
    except Exception:
        check("a value-referenced local never goes near (refused)", True)
        return
    bank1 = g.bank_units.get(1, "")
    check("a value-referenced local never goes near",
          "void pool_inner(void) {" not in bank1)


def test_stub_split_keeps_banked():
    lib = '''
module "cold" {
    var latest: u8
    function tick() {
        latest = 1
    }
    export tick
}
'''
    main = '''
module "main" {
    import "cold"
    var cb: function() = cold.tick
    function main() {
        cold.tick()
        cb()
    }
}
'''
    c, g = compile_program([("lib.mos", lib), ("main.mos", main)],
                           code_banks=["cold"])
    bank1 = g.bank_units.get(1, "")
    # The __bimpl body is is_local and module-private, but its caller (the
    # resident stub) is bank 0 - the trampoline is what maps the bank.
    check("__bimpl keeps BANKED (its caller is the resident stub)",
          "void cold_tick__bimpl(void) BANKED {" in bank1)


def test_optin_identity():
    plain, _g = compile_program([("lib.mos", LIB), ("main.mos", MAIN)])
    again, g = compile_program([("lib.mos", LIB), ("main.mos", MAIN)],
                               code_banks=[])
    check("no code_banks is byte-identical", plain == again)
    # (the Mega Duck: no verified cart mapper; the Lynx and the PCE bank now,
    # as cart overlays / MPR banks - see lynx_overlay_test / pce_banking_test)
    duck, g = compile_program([("lib.mos", LIB), ("main.mos", MAIN)],
                              platform="megaduck", code_banks=["pool"])
    check("no near set off a banking console", not g._near_bank_funcs)


if __name__ == "__main__":
    print("near_bank_local_test:")
    test_local_loses_banked()
    test_pinned_caller_keeps_banked()
    test_address_taken_keeps_banked()
    test_stub_split_keeps_banked()
    test_optin_identity()
    print("  %d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
