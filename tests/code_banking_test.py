"""Cold-code ROM banking (`[build] code_banks`, gbs-import item 1D).

Pins the contracts of the build-driven engine-code banking:
  * opt-in: no code_banks -> byte-identical C (and ignored off has_banking)
  * per-module banks: each listed module gets its own bank TU
  * stub split: an address-taken (callback-registered) function keeps a
    resident stub; its body moves to the bank as <name>__bimpl BANKED
  * seam pinning: a function that switches the ROM window (a GB streamed-seam
    read) stays home and gets the bank-neutrality wrapper
  * code_byte blobs: the first-seen (bytecode fetch) blob stays resident,
    later blobs bank; the data threshold drops to 0 with code banking on
  * bank-local consts: a const array read only by its module's banked code is
    defined in that module's bank TU; one read from home keeps it home
  * sprite sheets: `[assets]` art read only by `sprite.set_data` goes into a
    DATA bank and is uploaded through the far read, so it never competes with
    a module's code for one 16 KB window
  * refused modules: vm.core / scripts are hard errors
"""
import os
import re
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
module "cold" {
    const TABLE: array[u8, 4] = [1, 2, 3, 4]
    var latest: u8 = 0
    function tick() {
        latest = TABLE[latest & 3]
    }
    function helper(n: u8) -> u8 {
        return n + TABLE[0]
    }
    export tick, helper
}
'''

MAIN = '''
module "main" {
    import "cold"
    var cb: function() = cold.tick
    function main() {
        cold.tick()
        cb()
        var v: u8 = cold.helper(3)
        v = v
    }
}
'''


def test_optin_and_stub():
    plain, _g = compile_program([("lib.mos", LIB), ("main.mos", MAIN)])
    again, g = compile_program([("lib.mos", LIB), ("main.mos", MAIN)],
                               code_banks=[])
    check("no code_banks is byte-identical", plain == again)
    check("no bank TUs without the opt-in", not g.bank_units)

    banked, g = compile_program([("lib.mos", LIB), ("main.mos", MAIN)],
                                code_banks=["cold"])
    check("a bank TU is emitted for the listed module", 1 in g.bank_units)
    bank1 = g.bank_units.get(1, "")
    # tick is address-taken (cb = cold.tick): resident stub + banked body.
    check("address-taken function keeps a resident stub",
          "void cold_tick(void) {" in banked
          and "cold_tick__bimpl();" in banked)
    check("its body moves to the bank as __bimpl BANKED",
          "void cold_tick__bimpl(void) BANKED {" in bank1)
    check("a plain function banks under its own name",
          "uint8_t cold_helper(uint8_t n) BANKED {" in bank1
          and "uint8_t cold_helper(uint8_t n) BANKED {" not in banked)
    # TABLE is read by tick's banked body and helper only -> bank-local.
    check("module const read only by banked code co-locates in the bank",
          "const uint8_t cold_TABLE[4] = {1, 2, 3, 4};" in bank1
          and "extern const uint8_t cold_TABLE[4];" in banked)

    # Off a console that banks (the Mega Duck: no verified cart mapper) the
    # request is ignored, byte for byte. (The Lynx and the PC Engine no longer
    # ignore it: there `code_banks` means cart overlays / MPR banking - pinned
    # by lynx_overlay_test.py and pce_banking_test.py.)
    duck, _g = compile_program([("lib.mos", LIB), ("main.mos", MAIN)],
                               platform="megaduck", code_banks=["cold"])
    duck_plain, _g = compile_program([("lib.mos", LIB), ("main.mos", MAIN)],
                                     platform="megaduck")
    check("ignored (byte-identical) off has_banking", duck == duck_plain)


def test_per_module_banks():
    lib2 = LIB.replace('"cold"', '"cold2"').replace("cold_", "cold2_")
    main2 = '''
module "main" {
    import "cold"
    import "cold2"
    function main() {
        cold.tick()
        cold2.tick()
    }
}
'''
    _c, g = compile_program([("a.mos", LIB), ("b.mos", lib2),
                             ("main.mos", main2)],
                            code_banks=["cold", "cold2"])
    check("one bank per listed module", 1 in g.bank_units and 2 in g.bank_units)
    # Prototypes for every function appear in every TU; the DEFINITION
    # (`... BANKED {`) must be in the module's own bank only.
    check("each module's code is in its own bank",
          "void cold_tick(void) BANKED {" in g.bank_units[1]
          and "void cold2_tick(void) BANKED {" in g.bank_units[2]
          and "void cold2_tick(void) BANKED {" not in g.bank_units[1])


SEAM = '''
module "world" {
    import "platform.assets"
    const MAP: array[u8, %d] = [%s]
    function paint() {
        assets.use(MAP)
        var t: u8 = MAP[0]
        t = t
    }
    function tile(i: u16) -> u8 {
        return MAP[i]
    }
    function cold_math(n: u8) -> u8 {
        return n * 2
    }
    export paint, tile, cold_math
}
''' % (64, ", ".join(["7"] * 64))

SEAM_MAIN = '''
module "main" {
    import "world"
    function main() {
        world.paint()
        var t: u8 = world.tile(3)
        t = world.cold_math(t)
    }
}
'''


PTR_WORLD = '''
module "world" {
    import "platform.assets"
    import "platform.hardware"
    import "cold"
    const MAP: array[u8, %d] = [%s]
    function safe() -> u8 {
        var p: addr = assets.ptr(MAP)
        return hw.peek(p)
    }
    function unsafe() -> u8 {
        return cold.first(assets.ptr(MAP))
    }
    export safe, unsafe
}
''' % (64, ", ".join(["7"] * 64))

PTR_COLD = '''
module "cold" {
    import "platform.hardware"
    function first(p: addr) -> u8 {
        return hw.peek(p)
    }
    export first
}
'''


def test_streamed_ptr_into_banked_callee():
    """Review E-6: a streamed pointer handed to a callee of another bank is a
    compile-time error; read in the home bank and it compiles."""
    safe = PTR_WORLD.replace("        return cold.first(assets.ptr(MAP))\n",
                             "        return safe()\n")
    main = '''
module "main" {
    import "world"
    function main() {
        var t: u8 = world.safe()
        t = world.unsafe()
    }
}
'''
    ok, g = compile_program([("w.mos", PTR_WORLD.replace("        return cold.first(assets.ptr(MAP))\n", "        var p: addr = assets.ptr(MAP)\n        return hw.peek(p)\n")),
                             ("c.mos", PTR_COLD), ("m.mos", main)], code_banks=["cold"])
    check("a streamed pointer read in the home bank compiles",
          "world_MAP" in g.streamed and "SWITCH_ROM" in ok)
    out = MosaikCompiler().compile_program([("w.mos", PTR_WORLD), ("c.mos", PTR_COLD), ("m.mos", main)],
                                           platform="gameboy", code_banks=["cold"])
    check("...and the same pointer into a banked callee is refused",
          out.startswith("Compilation error") and "cold.first" in out and "wrong" in out,
          out[:200])
    out2 = MosaikCompiler().compile_program([("w.mos", PTR_WORLD), ("c.mos", PTR_COLD), ("m.mos", main)],
                                            platform="gameboy")
    check("without banking the call is fine (the pointer is the const)",
          not out2.startswith("Compilation error"), out2[:200])
    out3 = MosaikCompiler().compile_program([("w.mos", PTR_WORLD), ("c.mos", PTR_COLD), ("m.mos", main)],
                                            platform="lynx", code_banks=["cold"])
    check("...and on the Lynx too (its streaming returns a RAM cache pointer)",
          not out3.startswith("Compilation error"), out3[:200])


def test_seam_pinning_and_threshold():
    # Without code banking the 64 B of streamed data is far below the 16 KB
    # threshold: resident, seam a no-op.
    plain, g = compile_program([("w.mos", SEAM), ("m.mos", SEAM_MAIN)])
    check("below-threshold world stays resident without code banking",
          not g.streamed and "SWITCH_ROM" not in plain)
    banked, g = compile_program([("w.mos", SEAM), ("m.mos", SEAM_MAIN)],
                                code_banks=["world"])
    check("code banking drops the data threshold to 0",
          "world_MAP" in g.streamed)
    check("seam-switching functions stay home",
          "uint8_t world_tile(uint16_t i) {" in banked
          and "world_tile" not in g.bank_units.get(1, "")
          .replace("world_tile(uint16_t i);", ""))
    check("seam functions get the bank-neutrality wrapper",
          "uint8_t __gbs_bank_entry = CURRENT_BANK;" in banked
          and "SWITCH_ROM(__gbs_bank_entry);" in banked)
    check("a non-seam function of the same module still banks",
          "world_cold_math" in g.bank_units.get(1, ""))


BLOBS = '''
module "scriptsish" {
    import "platform.assets"
    const CODE: array[u8, 8] = [1, 2, 3, 4, 5, 6, 7, 8]
    const STRINGS: array[u8, 8] = [9, 9, 9, 9, 9, 9, 9, 9]
    function fetch(off: u16) -> u8 {
        return assets.code_byte(CODE, off)
    }
    function text(off: u16) -> u8 {
        return assets.code_byte(STRINGS, off)
    }
    export fetch, text
}
'''

BLOBS_MAIN = '''
module "main" {
    import "scriptsish"
    function main() {
        var a: u8 = scriptsish.fetch(0)
        a = scriptsish.text(1)
    }
}
'''


def test_code_byte_blobs():
    banked, g = compile_program([("s.mos", BLOBS), ("m.mos", BLOBS_MAIN)],
                                code_banks=["scriptsish"])
    check("the first-seen (fetch) blob stays resident",
          "scriptsish_CODE" not in g.streamed
          and "const uint8_t scriptsish_CODE[8]" in banked)
    check("a later code_byte blob banks",
          "scriptsish_STRINGS" in g.streamed)
    check("its read is a switch-then-read",
          "(SWITCH_ROM(" in banked and "scriptsish_STRINGS)[off]" in banked)


def test_bank_bytecode():
    """`[build] bank_bytecode` -- the GB-family mirror of `lynx_code_resident`
    (bank0-optimization-plan O3). It moves the FIRST-seen code_byte blob (the VM8
    bytecode) into a ROM bank as well, which is the biggest single resident symbol
    a VM8 game has. Opt-in, because it costs a SWITCH_ROM per fetched byte."""
    src = [("s.mos", BLOBS), ("m.mos", BLOBS_MAIN)]
    off, goff = compile_program(src, code_banks=["scriptsish"])
    on, g = compile_program(src, code_banks=["scriptsish"], bank_bytecode=True)
    check("the bytecode blob banks when asked", "scriptsish_CODE" in g.streamed)
    check("its fetch becomes a switch-then-read",
          "scriptsish_CODE)[off]" in on and "(SWITCH_ROM(" in on)
    # Assert on the PLACEMENT, not on the generated text: both modes leave the
    # main TU an `extern const uint8_t scriptsish_CODE[8];` here (unbanked, this
    # fixture's blob still co-locates into its own module's bank), so grepping
    # the C cannot tell them apart -- the same trap the O2 accessor work hit.
    # What changes is that the blob becomes banked DATA the seam switches to.
    check("the blob is placed as banked data",
          "scriptsish_CODE" in [s for syms in g.data_bank_syms.values()
                                for s in syms]
          and "scriptsish_CODE" not in [s for syms in goff.data_bank_syms.values()
                                        for s in syms])
    check("off keeps it resident (the default is unchanged)",
          "scriptsish_CODE" not in goff.streamed and off != on)
    # ... and it must not reach a console with no banking, nor a build with no
    # code_banks to bank INTO -- there is no bank to switch to in either case.
    flat, gf = compile_program(src, bank_bytecode=True)
    check("ignored without code_banks", "scriptsish_CODE" not in gf.streamed)
    lynx, gl = compile_program(src, platform="lynx", code_banks=["scriptsish"],
                               bank_bytecode=True)
    check("the Lynx path is untouched (it has its own knob)",
          "SWITCH_ROM" not in lynx)


def test_refused_modules():
    """`scripts` is refused (fetch() runs per instruction byte, so its blob and
    reader must stay resident). `vm.core` is NOT: it pins its own hot path with
    `bank(0)` and lets the cold half (boot, the seam setters, spawn/kill) bank
    -- ~880 B of GB bank 0. See lib/vm/core.mos's header comment."""
    scripts = 'module "scripts" { function fetch() { } export fetch }'
    main = ('module "main" { import "scripts" '
            'function main() { scripts.fetch() } }')
    compiler = MosaikCompiler()
    out = compiler.compile_program([("s.mos", scripts), ("m.mos", main)],
                                   platform="gameboy",
                                   code_banks=["scripts"])
    check("scripts is refused",
          out.startswith("Compilation error") and "code_banks" in out)

    # vm.core banks, and a bank(0) function inside it stays home.
    core = ('module "vm.core" { bank(0) function step() { } '
            'function boot() { } export step, boot }')
    main = ('module "main" { import "vm.core" '
            'function main() { core.step() core.boot() } }')
    out = MosaikCompiler().compile_program(
        [("c.mos", core), ("m.mos", main)],
        platform="gameboy", code_banks=["vm.core"])
    check("vm.core is accepted (it pins its own hot path)",
          not out.startswith("Compilation error"))
    check("its cold function banks",
          "vm_core_boot" in out and "BANKED" in out)
    check("its bank(0) hot function stays resident",
          re.search(r'void vm_core_step\(void\)\s*\{', out) is not None)


def test_home_read_keeps_const_home():
    lib = '''
module "cold" {
    const TABLE: array[u8, 2] = [1, 2]
    function pick(n: u8) -> u8 {
        return TABLE[n & 1]
    }
    export pick, TABLE
}
'''
    main = '''
module "main" {
    import "cold"
    function main() {
        var v: u8 = cold.pick(1)
        v = cold.TABLE[0]
    }
}
'''
    banked, g = compile_program([("l.mos", lib), ("m.mos", main)],
                                code_banks=["cold"])
    check("a const read from home stays home",
          "const uint8_t cold_TABLE[2] = {1, 2};" in banked
          and "cold_TABLE[2] = {1, 2}" not in g.bank_units.get(1, ""))


def test_sprite_sheets_bank_far():
    """A SPRITE SHEET GOES INTO A DATA BANK AND IS UPLOADED THROUGH A FAR READ.

    `[assets]` sheets are emitted by the CODEGEN, not declared in a module, so
    they have no module bank of their own. They used to CO-LOCATE into the one
    bank their readers sit in, which is free (no switch) but makes the art
    compete with that module's code for a single 16 KB window:
    the SMS/GG sample conversion's `rooms` bank reached 16,445 B, 61 past the window, and
    the link refused the ROM (SMS/GG have no hardware flip, so the soft-flip
    mirrors are real baked art).

    `sprite.set_data` is the ONE verb that reads such an array (repo-wide), so
    a sheet read only that way needs no co-location: `gbs_spr_data_far` maps
    the sheet's bank, uploads and restores the caller's, which frees the code
    bank entirely and works from HOME code too (an upload inline in `main()`
    used to pin the whole sheet RESIDENT). A sheet read some other way keeps
    the old co-location rule, byte for byte.
    """
    print("\n[sprite sheets in data banks]")
    sheet = [("sprites", bytes(range(16)) * 4, 2)]     # 4 tiles = 64 bytes

    inline = '''
module "main" {
    import "graphics.sprite"
    function main() {
        sprite.set_data(0, sprites_tile_count, sprites_tiles)
    }
}
'''
    helper = '''
module "main" {
    import "graphics.sprite"
    function upload() {
        sprite.set_data(0, sprites_tile_count, sprites_tiles)
    }
    function main() {
        upload()
    }
}
'''
    # An INDEXED read is not an upload, so this sheet keeps the old rule.
    indexed = '''
module "main" {
    import "graphics.sprite"
    var seen: u8 = 0
    function peek() {
        seen = sprites_tiles[0]
    }
    function main() {
        sprite.set_data(0, sprites_tile_count, sprites_tiles)
        peek()
    }
}
'''
    defn = "const uint8_t sprites_tiles[64] = {"
    ext = "extern const uint8_t sprites_tiles[64];"
    helper_def = "void gbs_spr_data_far(uint8_t first, uint8_t count, uint8_t bank, const uint8_t *data) {"
    helper_proto = "void gbs_spr_data_far(uint8_t first, uint8_t count, uint8_t bank, const uint8_t *data);"

    for label, src in (("from main()", inline), ("from a banked helper", helper)):
        c, g = compile_program([("m.mos", src)], assets=sheet,
                               code_banks=["main"])
        moved = [b for b, u in g.bank_units.items() if defn in u]
        check("%s: the sheet leaves the resident image" % label,
              len(moved) == 1 and defn not in c,
              "banks holding it: %s" % moved)
        check("%s: its bank is the one the lowering names" % label,
              g.asset_far_bank.get("sprites_tiles") == (moved or [None])[0],
              "asset_far_bank=%s" % g.asset_far_bank)
        check("%s: the upload lowers to the far read" % label,
              ("gbs_spr_data_far(0, sprites_tile_count, %d, sprites_tiles)"
               % g.asset_far_bank["sprites_tiles"]) in
              (c + "".join(g.bank_units.values())))
        check("%s: the main TU keeps an extern view" % label, ext in c)
        check("%s: the helper is RESIDENT" % label,
              helper_def in c and not any(helper_def in u
                                          for u in g.bank_units.values()),
              "the caller may be banked, so the helper must not be")
        # LOCKSTEP: a gated prelude helper needs its prototype in every bank
        # TU, or sdcc reads the call as an implicit int-returning function
        # and rejects the arguments.
        code_tus = [u for b, u in g.bank_units.items()
                    if "gbs_spr_data_far(" in u and helper_def not in u]
        check("%s: every calling bank TU carries the prototype" % label,
              all(helper_proto in u for u in code_tus),
              "%d bank TU(s) call it" % len(code_tus))

    c, g = compile_program([("m.mos", indexed)], assets=sheet,
                           code_banks=["main"])
    check("a sheet read some OTHER way keeps the old rule",
          "sprites_tiles" not in g.asset_far_bank
          and "gbs_spr_data_far" not in c,
          "asset_far_bank=%s" % g.asset_far_bank)

    # Byte-identical rule: no banking at all -> the sheet is emitted exactly
    # as before, wherever it is read from.
    c0, g0 = compile_program([("m.mos", helper)], assets=sheet)
    check("no code_banks: the sheet stays resident (byte-identical)",
          defn in c0 and not g0.bank_units and "gbs_spr_data_far" not in c0)

    # ...and on a console with no banked ROM at all.
    c1, g1 = compile_program([("m.mos", helper)], assets=sheet,
                             platform="lynx", code_banks=["main"])
    check("no banked ROM on the console: unchanged",
          "gbs_spr_data_far" not in c1)


if __name__ == "__main__":
    print("Cold-code banking checks (code_banks)")
    print("=" * 50)
    test_optin_and_stub()
    test_per_module_banks()
    test_seam_pinning_and_threshold()
    test_code_byte_blobs()
    test_bank_bytecode()
    test_streamed_ptr_into_banked_callee()
    test_refused_modules()
    test_home_read_keeps_const_home()
    test_sprite_sheets_bank_far()
    print("=" * 50)
    if failed:
        print("%d FAILED, %d passed" % (failed, passed))
        sys.exit(1)
    print("All code-banking checks passed (%d)" % passed)
