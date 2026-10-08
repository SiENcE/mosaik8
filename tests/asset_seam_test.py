#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Asset residency seam (asset-streaming / large-game groundwork).

`platform.assets` adds two codegen-lowered verbs that introduce an indirection
between a call site and the *source* of an asset's bytes, so that source can
become a per-console detail (Stage B streams it from the Lynx cart):

  assets.use(id)   -- ensure the asset is resident
  assets.ptr(id)   -- the pointer to hand to a setter (bkg.set_data, ...)

On the directly-mapped consoles the seam is the byte-identical no-op/passthrough
(`assets.ptr(X)` -> X's bare const symbol, `assets.use(X)` -> nothing), so a
program using it generates C *identical* to one passing the const array straight
to the setter -- the load-bearing guarantee. On the **Lynx** the same seam triggers
Stage B streaming: the referenced const leaves the resident image for a cart
archive, loaded on demand (so its output legitimately differs from the resident
build). Both behaviours are checked here.
"""

from mosaik import MosaikCompiler


def compile_for(src, platform='gameboy'):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


# Same program two ways: through the seam, and passing the const array directly.
WITH_SEAM = '''
module "m" {
    import "graphics.bkg"
    import "platform.assets"
    const TILES: array[u8, 16] = [
        0x3C, 0x3C, 0x7E, 0x7E, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xDB, 0xDB, 0x7E, 0x7E, 0x3C, 0x3C
    ]
    function main() {
        assets.use(TILES)
        bkg.set_data(0, 1, assets.ptr(TILES))
    }
    export main
}
'''

DIRECT = '''
module "m" {
    import "graphics.bkg"
    const TILES: array[u8, 16] = [
        0x3C, 0x3C, 0x7E, 0x7E, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xDB, 0xDB, 0x7E, 0x7E, 0x3C, 0x3C
    ]
    function main() {
        bkg.set_data(0, 1, TILES)
    }
    export main
}
'''


def main():
    ok = True

    # On the directly-mapped consoles the seam stays the Stage A no-op/passthrough
    # (use = nothing, ptr = the const symbol) -- byte-identical to the direct call.
    for platform in ('gameboy', 'gameboy_color', 'sms', 'nes', 'pce'):
        try:
            seam = compile_for(WITH_SEAM, platform)
            direct = compile_for(DIRECT, platform)
        except Exception as e:  # noqa: BLE001
            ok = check("%s: both programs compile" % platform, False)
            print("    ERROR: %s" % e)
            continue
        ok &= check("%s: seam output is BYTE-IDENTICAL to the direct setter call"
                    % platform, seam == direct)
        ok &= check("%s: assets.ptr(TILES) lowered to the const symbol" % platform,
                    "TILES)" in seam.replace(" ", ""))
        ok &= check("%s: no cart-streaming runtime (mapped console)" % platform,
                    "gbs_asset_ptr" not in seam and "@assets_" not in seam)

    # On the Lynx the seam triggers Stage B streaming: the referenced const leaves
    # the resident image for the cart archive, so the build is NOT the same as the
    # resident-const direct build (that is the whole point on the Lynx).
    seam_lynx = compile_for(WITH_SEAM, "lynx")
    direct_lynx = compile_for(DIRECT, "lynx")
    ok &= check("lynx: seam STREAMS (differs from the resident-const direct build)",
                seam_lynx != direct_lynx)
    ok &= check("lynx: emits the cart loader/cache (lseek/read + gbs_asset_ptr)",
                "gbs_asset_ptr" in seam_lynx and "lseek(1" in seam_lynx)
    ok &= check("lynx: the streamed const left the resident image",
                "TILES[16] = {" not in seam_lynx)
    # The LRU is sized to what can actually thrash: one streamed asset can't, so
    # it gets ONE slot -- the second would be GBS_STREAM_MAXLEN bytes of MAIN
    # that never holds anything (a 300-tile wide level's map is KBs of it).
    ok &= check("lynx: a single streamed asset gets a 1-slot cache",
                "#define GBS_STREAM_SLOTS 1" in seam_lynx)
    two_assets = WITH_SEAM.replace(
        "    function main() {",
        "    const MORE: array[u8, 16] = [\n"
        "        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15\n"
        "    ]\n    function main() {").replace(
        "        bkg.set_data(0, 1, assets.ptr(TILES))",
        "        bkg.set_data(0, 1, assets.ptr(TILES))\n"
        "        assets.use(MORE)\n"
        "        bkg.set_data(1, 1, assets.ptr(MORE))")
    ok &= check("lynx: two streamed assets keep the 2-slot LRU",
                "#define GBS_STREAM_SLOTS 2" in compile_for(two_assets, "lynx"))

    # BOTH SEAMS IN ONE PROGRAM (a per-scene tileset loads WHOLE while the
    # concatenated maps WINDOW -- a many-scene studio world). The runtime used
    # to emit the whole-asset cache OR the range cache, never both, so the
    # other's helper was left undeclared at compile (a cc65 link error found
    # converting a 17-scene reference-engine project). Each cache is now emitted on
    # its own seam, and the WHOLE cache's slots are sized over whole-LOADED
    # assets only -- sizing them to the big concatenated array would cost
    # kilobytes of the scarce Lynx MAIN.
    both_seams = '''
module "m" {
    import "graphics.bkg"
    import "platform.assets"
    const TS: array[u8, 16] = [
        0x3C, 0x3C, 0x7E, 0x7E, 0xFF, 0xFF, 0xFF, 0xFF,
        0xFF, 0xFF, 0xDB, 0xDB, 0x7E, 0x7E, 0x3C, 0x3C
    ]
    const MAPS: array[u8, 64] = [
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
        0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
    ]
    function paint(scene: u8) {
        assets.use(TS)
        bkg.set_data(0, 1, assets.ptr(TS))
        assets.range_base(MAPS, 16)
        assets.use_range(MAPS, 0, 16)
        bkg.set_tiles(0, 0, 4, 4, assets.ptr_range(MAPS, 0, 16))
    }
    function main() { paint(0) }
    export main
}
'''
    bs = compile_for(both_seams, "lynx")
    ok &= check("lynx both seams: whole-asset helpers declared",
                "gbs_asset_load(unsigned char id)" in bs
                and "gbs_asset_ptr(unsigned char id)" in bs)
    # only the range helpers the program calls are linked (cc65_prune)
    ok &= check("lynx both seams: the range helpers it calls are defined, the others not",
                "void gbs_asset_load_range(" in bs and "gbs_asset_ptr_range(" in bs
                and "gbs_asset_find_range(" not in bs)
    # One offsets table, shared: the range cache reads the whole cache's
    # gbs_asset_off (same id space) instead of emitting a duplicate.
    ok &= check("lynx both seams: ONE shared offsets table",
                "gbs_range_off[" not in bs and "gbs_asset_off[" in bs)
    # The whole cache's slot is the 16-byte tileset, NOT the 64-byte MAPS array
    # (which is only ever windowed 16 bytes at a time by the range cache).
    ok &= check("lynx both seams: whole cache sized to the whole-LOADED asset",
                "#define GBS_STREAM_MAXLEN 16" in bs
                and "#define GBS_RANGE_MAXWIN 16" in bs)

    # -- Background tileset LOAD-THROUGH (Lynx) -------------------------------
    # A per-scene TILESET is only ever `assets.use(X)` + `bkg.set_data(.., ..,
    # assets.ptr(X))`: set_bkg_data copies it out immediately and nothing reads
    # it again. Holding it in the whole-asset cache sizes EVERY slot to it (the
    # slots are one shared size), so a big tileset costs slots x tileset bytes
    # of the scarce Lynx MAIN for data that is dead the moment set_data returns.
    # It is streamed STRAIGHT into the tile table instead, and drops out of the
    # slot sizing -- which is what fits a big-tileset room on the Lynx.
    def loadthrough_src(tiles, extra_main="", extra_decl=""):
        return '''
module "m" {
    import "graphics.bkg"
    import "platform.assets"
    const TS: array[u8, %d] = [%s]
    const MAP: array[u8, 64] = [%s]
%s
    function main() {
        assets.use(TS)
        bkg.set_data(0, %d, assets.ptr(TS))
        assets.use(MAP)
        bkg.set_tiles(0, 0, 8, 8, assets.ptr(MAP))
%s
    }
    export main
}
''' % (tiles * 16, ", ".join(["1"] * (tiles * 16)),
       ", ".join(["2"] * 64), extra_decl, tiles, extra_main)

    # 100 tiles = 1,600 B of tileset vs a 64 B map: without load-through BOTH
    # slots are 1,600 B; with it they fit the map, the only RETAINED asset.
    lt = compile_for(loadthrough_src(100), "lynx")
    ok &= check("lynx load-through: the tileset streams into the tile table",
                "gbs_bkg_data_stream(0, 100, " in lt)
    ok &= check("lynx load-through: slots sized to the RETAINED asset, not the tileset",
                "#define GBS_STREAM_MAXLEN 64" in lt)
    # Stream ids are assigned in sorted symbol order, so MAP is 0 and TS is 1.
    # TS's `assets.use` drops (there is no slot to warm -- the set_data below
    # reads the cart itself); the MAP's prefetch is untouched.
    ok &= check("lynx load-through: the tileset's prefetch hint drops",
                "gbs_asset_load(1)" not in lt)
    ok &= check("lynx load-through: the RETAINED asset keeps its prefetch",
                "gbs_asset_load(0)" in lt)
    ok &= check("lynx load-through: unpacks IN PLACE (no scratch buffer)",
                "read(1, gbs_bkg_tileset[first]" in lt)
    # An asset that is also INDEXED must stay in the cache: map_tile's `MAP[idx]`
    # rewrite reads through it, so streaming it past the cache would break the
    # read. Adding one index of TS anywhere disqualifies it.
    lt_indexed = compile_for(
        loadthrough_src(100, extra_main="        peek = TS[3]",
                        extra_decl="    var peek: u8 = 0"), "lynx")
    ok &= check("lynx load-through: an INDEXED asset stays in the cache",
                "gbs_bkg_data_stream(" not in lt_indexed
                and "#define GBS_STREAM_MAXLEN 1600" in lt_indexed)
    # Below the gain threshold the switch would cost more MAIN (its reader) than
    # the slot it removes, so a small tileset keeps the plain cache. This is what
    # keeps the seam tests above -- and the small streamed samples -- unchanged.
    small = compile_for(loadthrough_src(2), "lynx")
    ok &= check("lynx load-through: a SMALL tileset keeps the plain cache",
                "gbs_bkg_data_stream(" not in small)

    # A program that never touches the seam stays exactly as before (the verbs
    # are inert unless called) -- importing platform.assets alone changes nothing.
    just_import = '''
module "m" {
    import "graphics.bkg"
    import "platform.assets"
    const TILES: array[u8, 1] = [0]
    function main() { bkg.set_data(0, 1, TILES) }
    export main
}
'''
    no_import = just_import.replace('    import "platform.assets"\n', '')
    ok &= check("importing platform.assets alone is byte-identical (no prelude)",
                compile_for(just_import) == compile_for(no_import))

    print("\n%s" % ("All tests passed!" if ok else "Some tests failed"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
