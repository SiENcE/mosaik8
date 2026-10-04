#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""ROM banking (`bank(N)` function placement).

Verifies the parser (annotation, range/`main()` errors, `bank` staying a
valid identifier), the GBDK lowering (BANKED prototypes in the main TU, one
`#pragma bank N` translation unit per used bank with shared declarations,
bodies removed from the main TU), the multi-module mangling, and the
ignored-annotation behaviour on consoles without banked-ROM support.
"""

from mosaik import MosaikCompiler, PLATFORM_CAPS

BANKED = '''
module "main" {
    import "platform.video"
    import "graphics.text"

    const GREETING_LEN: u8 = 5
    const TABLE: array[u8, 3] = [1, 2, 3]
    var hits: u16 = 0

    bank(2) function score(base: u16) -> u16 {
        return base + TABLE[1] + GREETING_LEN
    }

    bank(2) local function helper() -> u8 {
        return 7
    }

    bank(3) function wave(step: u8) -> u8 {
        return step * helper()
    }

    function main() {
        video.enable_lcd()
        hits = score(100)
        text.print_number(0, 0, hits)
        text.print_number(0, 1, wave(3))
        loop { video.wait_vblank() }
    }

    export main
}
'''

BANK_IS_AN_IDENTIFIER = '''
module "main" {
    var bank: u8 = 1
    function main() {
        bank += 1
    }
    export main
}
'''

BANKED_MAIN = '''
module "main" {
    bank(2) function main() {
    }
    export main
}
'''

# `bank(0)` PINS a function to the home bank. It used to be an error ("just
# omit the annotation"), which stopped being true with `[build] code_banks`:
# there an unannotated function in a listed module BANKS, so saying "keep this
# one resident, it is hot" needs a way to say it. The generated collision
# probes use it (mosaik_vm/rooms.py HOT_PIN).
BANK_ZERO = '''
module "hot" {
    bank(0) function probe() -> u8 {
        return 1
    }
    function cold() -> u8 {
        return 2
    }
    export probe, cold
}
'''

BANK_ZERO_MAIN = '''
module "main" {
    import "hot"
    function main() {
        var a: u8 = hot.probe() + hot.cold()
    }
    export main
}
'''

MULTI_A = '''
module "main" {
    import "logic"
    function main() {
        logic.think()
    }
    export main
}
'''
MULTI_B = '''
module "logic" {
    bank(4) function think() -> u8 {
        return 1
    }
    export think
}
'''


def compile_for(src, platform):
    compiler = MosaikCompiler()
    out = compiler.compile(src.strip(), platform=platform)
    return out, compiler.code_generator


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("ROM banking (bank(N))")
    print("=" * 50)
    ok = True

    ok &= check("registry: banking on the GB family + SMS/Game Gear + NES",
                all(PLATFORM_CAPS[p]['has_banking'] is (p in (
                    'gameboy', 'gameboy_color', 'analogue_pocket',
                    'sms', 'gamegear', 'nes'))
                    for p in PLATFORM_CAPS))

    # --- GBDK lowering on a banking console -------------------------------
    out, gen = compile_for(BANKED, 'gameboy')
    ok &= check("banked program compiles on gameboy",
                not out.startswith("Compilation error:"))
    ok &= check("main TU keeps BANKED prototypes",
                "uint16_t score(uint16_t base) BANKED;" in out
                and "uint8_t wave(uint8_t step) BANKED;" in out)
    ok &= check("main TU drops the banked bodies",
                "uint16_t score(uint16_t base) BANKED {" not in out
                and "uint8_t wave(uint8_t step) BANKED {" not in out)
    ok &= check("one bank TU per used bank", sorted(gen.bank_units) == [2, 3])
    bank2, bank3 = gen.bank_units[2], gen.bank_units[3]
    ok &= check("bank TU opens with the file-scoped pragma",
                bank2.splitlines()[2] == "#pragma bank 2"
                and bank3.splitlines()[2] == "#pragma bank 3")
    ok &= check("bank TU holds its bodies (both bank-2 functions together)",
                "uint16_t score(uint16_t base) BANKED {" in bank2
                and "uint8_t helper(void) BANKED {" in bank2
                and "uint8_t wave(uint8_t step) BANKED {" in bank3)
    ok &= check("bank TU declares main-TU data extern",
                "extern const uint8_t TABLE[3];" in bank2
                and "extern uint16_t hits;" in bank2
                and "#define GREETING_LEN (5)" in bank2)
    ok &= check("bank TU has every prototype (banked->banked calls work)",
                "uint8_t helper(void) BANKED;" in bank3
                and "void main(void);" in bank3)
    ok &= check("bank TU declares the gbs_ helpers extern",
                "void gbs_wait_vblank(void);" in bank2
                and "void gbs_print_string" in bank2)
    ok &= check("call sites are unchanged (placement, not semantics)",
                "hits = score(100)" in out and "wave(3)" in out)

    # --- ignored on consoles without banked-ROM support -------------------
    # (SMS/Game Gear/NES now have real banking; megaduck stays off, lynx/pce cc65.)
    for platform in ('megaduck', 'lynx', 'pce'):
        out, gen = compile_for(BANKED, platform)
        ok &= check(f"{platform}: compiles with bank() ignored",
                    not out.startswith("Compilation error:")
                    and not gen.bank_units and "BANKED" not in out
                    and "#pragma bank" not in out)

    # --- SMS / Game Gear (Sega mapper) + NES (UNROM): real banking ---------
    for platform in ('sms', 'gamegear', 'nes'):
        out, gen = compile_for(BANKED, platform)
        ok &= check(f"{platform}: bank() places functions in bank TUs (real banking)",
                    not out.startswith("Compilation error:")
                    and sorted(gen.bank_units) == [2, 3] and "BANKED" in out
                    and "#pragma bank 2" in gen.bank_units[2])

    # --- non-banked programs are untouched --------------------------------
    out, gen = compile_for(BANK_IS_AN_IDENTIFIER, 'gameboy')
    ok &= check("`bank` still works as a variable name",
                not out.startswith("Compilation error:")
                and "bank += 1" in out and not gen.bank_units)

    # --- clear errors ------------------------------------------------------
    out, _ = compile_for(BANKED_MAIN, 'gameboy')
    ok &= check("banked main() is a clear compile error",
                out.startswith("Compilation error:")
                and "main() cannot be placed in a ROM bank" in out)
    # bank(0) PINS: under code_banks the unannotated `cold` banks and the
    # annotated `probe` stays in the home bank (see BANK_ZERO above).
    compiler = MosaikCompiler()
    out = compiler.compile_program(
        [("main.mos", BANK_ZERO_MAIN.strip()),
         ("hot.mos", BANK_ZERO.strip())],
        platform='gameboy', code_banks=["hot"])
    banked = "\n".join(compiler.code_generator.bank_units.values())
    # The bank TU legitimately carries a PROTOTYPE for every module function,
    # so the check is on the DEFINITION (the body with its opening brace).
    ok &= check("bank(0) pins a function to the home bank under code_banks",
                "uint8_t hot_probe(void) {" in out
                and "uint8_t hot_probe(void) {" not in banked
                and "hot_probe(void) BANKED {" not in banked)
    ok &= check("...and an UNannotated function in the same module still banks",
                "uint8_t hot_cold(void) BANKED {" in banked
                and "uint8_t hot_cold(void) BANKED {" not in out)

    # --- multi-module mangling ---------------------------------------------
    compiler = MosaikCompiler()
    out = compiler.compile_program(
        [("main.mos", MULTI_A.strip()), ("logic.mos", MULTI_B.strip())],
        platform='gameboy')
    gen = compiler.code_generator
    ok &= check("multi-module: banked function is mangled + BANKED",
                "uint8_t logic_think(void) BANKED;" in out
                and 4 in gen.bank_units
                and "uint8_t logic_think(void) BANKED {" in gen.bank_units[4])

    print()
    if ok:
        print("All banking tests passed")
        return 0
    print("Some tests failed")
    return 1


if __name__ == '__main__':
    sys.exit(main())
