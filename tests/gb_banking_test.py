#!/usr/bin/env python3
"""GB/GBC banked-`const` asset residency (the directly-mapped
residency path).

The `platform.assets` seam (assets.use/ptr) is reused unchanged from the Lynx
streaming path; only the per-console lowering differs. On the **GB family** the
cart IS CPU-addressable through the MBC, so a streamed const stays in ROM, placed
in a switchable bank, and the seam bank-switches to read it:

    assets.use(X)   -> SWITCH_ROM(bank[X])      (map X's bank)
    assets.ptr(X)   -> (SWITCH_ROM(bank[X]), X) (map, then the symbol -- a comma
                                                 expr; never held across a switch)
    X[idx]          -> (SWITCH_ROM(bank[X]), X)[idx]  (switch-then-read per access)

Banking only triggers when the streamed data is big enough to need it
(> GB_BANK_DATA_THRESHOLD = one 16 KB bank): below it the seam stays the
byte-identical Stage A no-op, so a SMALL GB world pays no MBC/bank-switch cost
(the zero-cost-on-directly-mapped-consoles principle). Non-banking consoles
(sms/nes/megaduck/pce) and the Lynx are unaffected.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def scenes_module(n_maps, map_bytes, seam=True):
    """A `scenes`-like module with `n_maps` map consts, optionally driven through
    the asset-residency seam (assets.use/ptr) vs a direct setter call."""
    L = ['module "scenes" {', '    import "graphics.bkg"']
    if seam:
        L.append('    import "platform.assets"')
    L.append("    const MAP_W: u8 = 8")
    names = []
    for i in range(n_maps):
        nm = "M%d" % i
        names.append(nm)
        row = ", ".join("%d" % ((i + j) & 0xFF) for j in range(map_bytes))
        L.append("    const %s: array[u8, %d] = [%s]" % (nm, map_bytes, row))
    # map_tile indexes each array (the per-cell read path).
    L.append("    function map_tile(scene: u8, idx: u16) -> u8 {")
    for i, nm in enumerate(names):
        L.append("        if scene == %d { return %s[idx] }" % (i, nm))
    L.append("        return 0")
    L.append("    }")
    # paint uploads each map (the use + ptr path).
    L.append("    function paint(scene: u8) {")
    for i, nm in enumerate(names):
        L.append("        if scene == %d {" % i)
        if seam:
            L.append("            assets.use(%s)" % nm)
            L.append("            bkg.set_tiles(0, 0, MAP_W, MAP_W, assets.ptr(%s))" % nm)
        else:
            L.append("            bkg.set_tiles(0, 0, MAP_W, MAP_W, %s)" % nm)
        L.append("        }")
    L.append("    }")
    L.append("    export MAP_W, map_tile, paint, " + ", ".join(names))
    L.append("}")
    return "\n".join(L)


GAME = '''
module "main" {
    import "graphics.bkg"
    import "scenes"
    var room: u8 = 0
    function main() {
        scenes.paint(room)
        room = scenes.map_tile(room, 0)
    }
    export main
}
'''


def compile_world(scenes, platform):
    comp = MosaikCompiler()
    c = comp.compile_program(
        [("main.mos", GAME), ("scenes.mos", scenes)], platform=platform)
    return c, comp.code_generator


def main():
    print("GB/GBC banked-const asset residency")
    print("=" * 56)
    ok = True

    # A LARGE streamed world (20 x 1024 B = 20 KB > one 16 KB bank) banks on every
    # has_banking console: the const arrays move into per-bank TUs, the main TU
    # declares them extern + bank-switches (SWITCH_ROM) to read them. The codegen is
    # console-generic (SWITCH_ROM lowers per console in GBDK); only the cart-header
    # flags differ (mosaik8.py GBDK_MAPPER). SMS/Game Gear ride the SAME path
    # (Sega mapper).
    big = scenes_module(20, 1024)
    for platform, mbc in (("gameboy", True), ("gameboy_color", True),
                          ("sms", False), ("gamegear", False), ("nes", False)):
        c, cg = compile_world(big, platform)
        ok &= check("[%s] banks the large world (_stream_mode == 'gb')" % platform,
                    cg._stream_mode == "gb")
        ok &= check("[%s] >1 ROM bank TU emitted" % platform,
                    len(cg.bank_units) >= 2
                    and all("#pragma bank" in u for u in cg.bank_units.values()))
        ok &= check("[%s] assets.ptr -> (SWITCH_ROM(b), sym) comma expr" % platform,
                    "(SWITCH_ROM(" in c and ", scenes_M0))" in c)
        ok &= check("[%s] assets.use -> SWITCH_ROM(b) statement" % platform,
                    "SWITCH_ROM(1);" in c)
        ok &= check("[%s] per-cell read -> (SWITCH_ROM(b), sym)[idx]" % platform,
                    "(SWITCH_ROM(1), scenes_M0)[idx]" in c)
        ok &= check("[%s] main TU declares the banked array extern" % platform,
                    "extern const uint8_t scenes_M0[1024];" in c)
        ok &= check("[%s] main TU does NOT define the banked array" % platform,
                    "const uint8_t scenes_M0[1024] = {" not in c)
        # The definition lives in exactly one bank TU.
        defined = sum("const uint8_t scenes_M0[1024] = {" in u
                      for u in cg.bank_units.values())
        ok &= check("[%s] the banked array is defined in exactly one bank TU"
                    % platform, defined == 1)

    # A SMALL streamed world (2 x 16 B) stays RESIDENT on the GB family -- the seam
    # is the byte-identical Stage A no-op (zero MBC cost it does not need). Compare
    # against the same world with the const handed straight to the setter.
    small_seam = scenes_module(2, 16, seam=True)
    small_direct = scenes_module(2, 16, seam=False)
    for platform in ("gameboy", "gameboy_color"):
        c_seam, cg = compile_world(small_seam, platform)
        c_direct, _ = compile_world(small_direct, platform)
        ok &= check("[%s] small streamed world is NOT banked" % platform,
                    cg._stream_mode is None and not cg.bank_units)
        ok &= check("[%s] small streamed world is byte-identical to the direct call"
                    % platform, c_seam == c_direct and "SWITCH_ROM" not in c_seam)

    # Non-banking consoles never bank, even for a large world: the seam stays the
    # byte-identical Stage A no-op (Mega Duck banking not wired yet; pce is cc65).
    # (the data stays resident -- the linker, not the
    # codegen, reports an overflow if it is too big for that console).
    for platform in ("megaduck", "pce"):
        c_seam, cg = compile_world(big, platform)
        c_direct, _ = compile_world(scenes_module(20, 1024, seam=False), platform)
        ok &= check("[%s] never banks (no SWITCH_ROM, byte-identical to direct)"
                    % platform,
                    cg._stream_mode is None and "SWITCH_ROM" not in c_seam
                    and c_seam == c_direct)

    # The Lynx still STREAMS (cart archive), not banks -- the other residency path.
    c_lynx, cg_lynx = compile_world(big, "lynx")
    ok &= check("[lynx] streams to a cart archive (not banks)",
                cg_lynx._stream_mode == "lynx"
                and len(cg_lynx.streamed_archive) > 0
                and not cg_lynx.bank_units
                and "gbs_asset_ptr" in c_lynx)

    # Data banks are assigned ABOVE any bank(N) function placement (no collision):
    # a banked function in bank 2 forces the streamed data into bank 3+.
    banked_game = GAME.replace(
        "    function main() {",
        "    function main() {\n        room = far()\n    }\n"
        "    bank(2) function far() -> u8 { return 7 }\n    function _unused() {")
    comp = MosaikCompiler()
    comp.compile_program(
        [("main.mos", banked_game), ("scenes.mos", big)], platform="gameboy")
    cg = comp.code_generator
    data_banks = set(cg.data_bank_syms)
    ok &= check("data banks start above the bank(N) function bank (no collision)",
                bool(data_banks) and min(data_banks) >= 3 and 2 not in data_banks)

    print("=" * 56)
    print("All GB banking checks passed" if ok else "Some tests failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
