#!/usr/bin/env python3
"""Auto-sizing the cc65 Lynx Suzy sprite tile table from the uploaded tiles.

The Lynx sprite engine allocates a resident `gbs_tiles[GBS_MAX_TILES][33]` in
the scarce MAIN (~1.3 KB of BSS at 40). A program that uploads fewer sprite
tiles pays for all 40 anyway. `generator._resolve_sprite_max_tiles` shrinks the
table to `max(first + count)` over every resolvable `sprite.set_data(first,
count, ...)` (an integer literal or an asset `<name>_tile_count`), reclaiming
(40-N)*33 bytes - the sprite sibling of `bkg_max_tiles`.

Contract pinned here (Handy-verified behavior-identical on vm-quest / vm-danim):
  * inline-const uploads shrink to first+count.
  * an asset `_tile_count` upload resolves via the registered PNG.
  * an UNRESOLVABLE upload (runtime count) or NO upload keeps the full 40.
  * an explicit `[build] sprite_max_tiles` wins.
  * the shrink is Lynx-only + guarded (an out-of-range tile op is a no-op, never
    OOB), so it is byte-identical on every other console.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler

_TILE = ",".join(["0"] * 16)
# The auto-derive is gated to a VM8 game (import "vm.core"), so hand-written
# Lynx samples stay byte-identical. A tiny stub resolves the gate import.
_VM_STUB = '\nmodule "vm.core" {\n    export ping\n    function ping() { }\n}\n'


def _prog(body, imports="", vm=True):
    return ('module "m" {\n    import "graphics.sprite"\n%s%s'
            '    const S: array[u8, 48] = [%s,%s,%s]\n'
            '    function main() {\n%s        loop { }\n    }\n}\n%s'
            % ('    import "vm.core"\n' if vm else "", imports,
               _TILE, _TILE, _TILE, body, _VM_STUB if vm else ""))


def _max_tiles(c):
    assert not c.startswith("Compilation error"), c[:400]
    m = re.search(r"#define GBS_MAX_TILES\s+(\d+)", c)
    return int(m.group(1)) if m else None


def _c(prog, platform="lynx", **kw):
    return MosaikCompiler().compile_program([("m.mos", prog)],
                                            platform=platform, **kw)


def check(label, cond):
    print(("[PASS] " if cond else "[FAIL] ") + label)
    return cond


def main():
    ok = True

    # inline-const upload of 2 tiles -> table of 2
    ok &= check("[lynx] set_data(0,2) -> GBS_MAX_TILES 2",
                _max_tiles(_c(_prog("        sprite.set_data(0, 2, S)\n"))) == 2)

    # the MAX over several uploads
    ok &= check("[lynx] set_data(0,2)+set_data(5,3) -> 8",
                _max_tiles(_c(_prog("        sprite.set_data(0, 2, S)\n"
                                    "        sprite.set_data(5, 3, S)\n"))) == 8)

    # an asset `<name>_tile_count` upload resolves via the registered PNG
    asset = ("sprites", bytes(16 * 6), 2)   # 6 tiles
    prog_asset = _prog("        sprite.set_data(0, sprites_tile_count, sprites_tiles)\n")
    ok &= check("[lynx] set_data(0, sprites_tile_count) resolves to 6",
                _max_tiles(_c(prog_asset, assets=[asset])) == 6)

    # an UNRESOLVABLE upload count (a runtime var) keeps the full 40
    unres = _prog("        sprite.set_data(0, n, S)\n",
                  imports="    var n: u8 = 3\n")
    ok &= check("[lynx] runtime-count upload bails to 40",
                _max_tiles(_c(unres)) == 40)

    # no upload at all -> keep the safe 40
    ok &= check("[lynx] no set_data -> keeps 40",
                _max_tiles(_c(_prog("        sprite.move(0, 8, 8)\n"))) == 40)

    # a NON-VM program stays the full 40 (byte-identical; bounce/pong goldens)
    ok &= check("[lynx] non-VM program keeps 40 (byte-identical)",
                _max_tiles(_c(_prog("        sprite.set_data(0, 2, S)\n",
                                    vm=False))) == 40)

    # explicit knob wins even on a non-VM program
    ok &= check("[lynx] explicit sprite_max_tiles=10 honoured (non-VM)",
                _max_tiles(_c(_prog("        sprite.set_data(0, 2, S)\n", vm=False),
                              sprite_max_tiles=10)) == 10)

    # a shrunk table is annotated
    ok &= check("[lynx] shrunk table is annotated",
                "shrunk sprite_max_tiles"
                in _c(_prog("        sprite.set_data(0, 2, S)\n")))

    # --- the sprite SLOT table (`[build] sprite_max_slots`) -------------------
    # Explicit only (slot ids are runtime values), Lynx-only, and annotated when
    # shrunk. Each slot costs ~27 B of MAIN (an SCB + the tile/meta side tables).
    def _max_slots(c):
        m = re.search(r"#define GBS_MAX_SPRITES\s+(\d+)", c)
        return int(m.group(1)) if m else None

    ok &= check("[lynx] sprite_max_slots unset -> the full 40 (byte-identical)",
                _max_slots(_c(_prog("        sprite.set_data(0, 2, S)\n"))) == 40)
    shrunk = _c(_prog("        sprite.set_data(0, 2, S)\n"), sprite_max_slots=9)
    ok &= check("[lynx] explicit sprite_max_slots=9 honoured",
                _max_slots(shrunk) == 9)
    ok &= check("[lynx] shrunk slot table is annotated",
                "shrunk sprite_max_slots" in shrunk)
    ok &= check("[lynx] a shrunk slot table keeps the OOB slot guard",
                "nb < GBS_MAX_SPRITES" in shrunk)

    # Lynx-only: the GBDK console never emits the Lynx sprite table define
    gb = _c(_prog("        sprite.set_data(0, 2, S)\n"), platform="gameboy")
    ok &= check("[gameboy] never emits GBS_MAX_TILES", "GBS_MAX_TILES" not in gb)

    # the PCE (VRAM-backed tiles) keeps the full 40 -- only the Lynx BSS shrinks
    pce = _c(_prog("        sprite.set_data(0, 2, S)\n"), platform="pce")
    ok &= check("[pce] keeps GBS_MAX_TILES 40 (VRAM, not MAIN BSS)",
                _max_tiles(pce) == 40)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
