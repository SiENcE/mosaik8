#!/usr/bin/env python3
"""`[build] bkg_max_tiles` -- the cc65 Lynx bkg tile-table budget knob.

The Lynx bkg engine allocates a resident `gbs_bkg_tileset[N][16]` in the scarce
~46.6 KB MAIN (256 -> 4 KB of BSS). A project that uses fewer tiles can lower N
via `[build] bkg_max_tiles` to reclaim (256-N)*16 bytes -- the fix that let the
VM samples fit the Lynx again.

This checks the knob is:
  * DEFAULT byte-identical -- a program built WITHOUT the knob (bkg_max_tiles
    None or 256) emits the exact original `#define GBS_BKG_MAX_TILES 256` line
    and the exact original (unguarded) upload loop, on the Lynx.
  * effective when SET      -- bkg_max_tiles=N (N<256) emits the smaller table
    define + the defensive upload guard.
  * Lynx-only               -- the GBDK (gameboy) build is byte-identical
    regardless of the knob (only the two cc65 Lynx bkg engines read it).
  * range-validated         -- BuildConfig.get_bkg_max_tiles rejects out-of-range.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler

# A minimal program that pulls in the cc65 Lynx bkg engine (imports graphics.bkg
# and calls a bkg verb, so cc65_bkg_imported fires and the engine is emitted).
BKG_PROG = '''
module "prog" {
    import "graphics.bkg"

    const TILES: array[u8, 16] = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]
    const MAP: array[u8, 4] = [0, 0, 0, 0]

    function main() {
        bkg.set_data(0, 1, TILES)
        bkg.set_tiles(0, 0, 2, 2, MAP)
        loop { }
    }
}
'''


def _compile(platform, bkg_max_tiles=None):
    return MosaikCompiler().compile_program(
        [("prog.mos", BKG_PROG)], platform=platform, bkg_max_tiles=bkg_max_tiles)


def check(label, cond):
    print(("[PASS] " if cond else "[FAIL] ") + label)
    return cond


def main():
    ok = True

    # --- Lynx: default (unset) is byte-identical to explicit 256 --------------
    lynx_default = _compile("lynx", None)
    lynx_256 = _compile("lynx", 256)
    ok &= check("[lynx] unset == 256 (byte-identical default)",
                lynx_default == lynx_256)
    ok &= check("[lynx] default emits the exact original 256 define",
                "#define GBS_BKG_MAX_TILES 256\n" in lynx_default)
    ok &= check("[lynx] default upload loop is unguarded",
                "no table overrun" in lynx_default
                and "shrunk bkg_max_tiles" not in lynx_default)

    # --- Lynx: shrunk table takes effect + adds the defensive guard ----------
    lynx_16 = _compile("lynx", 16)
    ok &= check("[lynx] bkg_max_tiles=16 shrinks the table define",
                "#define GBS_BKG_MAX_TILES 16" in lynx_16)
    ok &= check("[lynx] shrunk table adds the upload guard",
                "shrunk bkg_max_tiles" in lynx_16
                and ">= GBS_BKG_MAX_TILES) continue" in lynx_16)
    ok &= check("[lynx] shrunk build still emits the tileset table",
                "gbs_bkg_tileset[GBS_BKG_MAX_TILES][16]" in lynx_16)

    # --- GBDK console is untouched by the knob (Lynx-only) -------------------
    gb_default = _compile("gameboy", None)
    gb_16 = _compile("gameboy", 16)
    ok &= check("[gameboy] knob is a no-op (byte-identical)",
                gb_default == gb_16)
    ok &= check("[gameboy] never emits the Lynx bkg table",
                "GBS_BKG_MAX_TILES" not in gb_default)

    # --- BuildConfig range validation ---------------------------------------
    from mosaik8 import BuildConfig
    with tempfile.TemporaryDirectory() as d:
        def cfg(body):
            p = os.path.join(d, "mosaik.toml")
            with open(p, "w", encoding="utf-8") as f:
                f.write(body)
            return BuildConfig(p)

        ok &= check("[config] unset -> None",
                    cfg("[build]\noutput_dir='build'\n").get_bkg_max_tiles() is None)
        ok &= check("[config] valid 16 -> 16",
                    cfg("[build]\nbkg_max_tiles=16\n").get_bkg_max_tiles() == 16)

        def rejects(body):
            try:
                cfg(body).get_bkg_max_tiles()
                return False
            except ValueError:
                return True
        ok &= check("[config] rejects 0", rejects("[build]\nbkg_max_tiles=0\n"))
        ok &= check("[config] rejects 257", rejects("[build]\nbkg_max_tiles=257\n"))

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
