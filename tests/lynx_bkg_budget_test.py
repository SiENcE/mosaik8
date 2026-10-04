#!/usr/bin/env python3
"""Auto-sizing the cc65 Lynx ROW-strip bkg engine's BSS from the world's scenes.

The Lynx bkg engine allocates two big resident BSS arrays in the scarce ~46.6 KB
MAIN: `gbs_bkg_tileset[N][16]` (256 -> 4 KB) and the strip ring
`gbs_bkg_strip[STRIPS][8*(STRIP_W*2+2)+1]` (STRIP_W 52 -> ~13.5 KB). For a VM8
game (whose vm.player camera provably CLAMPS horizontal scroll to the scene
bounds) the generator derives both from the scenes module (TILE_COUNT + the
widest scene) when the `[build]` knobs are unset -- see
CodeGenerator._resolve_lynx_bkg_budgets.

Contract pinned here:
  * VM8 + scenes -> BOTH shrink (tile table to TILE_COUNT, strip to the widest
    scene's coverage), UNLESS the world has animated tiles (high indices).
  * a NON-VM scenes program stays the full 256 / 52 (byte-identical) -- a
    hand-written game may continuously scroll a small map (it must keep the full
    scroll-period strip).
  * explicit `[build] bkg_max_tiles` / `bkg_strip_w` are honoured verbatim.
  * animated tiles (a non-empty scenes.anim_tick) keep the 256-tile table.
  * the GBDK console is untouched (Lynx-only).
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


def _prog(vm=True, tile_count=4, map_w=20, scene_w=None, animated=False,
          extra_scene_ts=None):
    """A minimal scenes + shell program. `vm` adds `import "vm.core"` (the gate);
    `scene_w` is an optional SCENE_W array (a non-uniform world); `animated`
    gives anim_tick a body (background animated tiles present);
    `extra_scene_ts` = (name, count) adds a PER-SCENE tileset uploaded from
    inside the scenes module (its own <NAME> count over the same table)."""
    sw = ""
    if scene_w is not None:
        sw = "    const SCENE_W: array[u8, %d] = [%s]\n" % (
            len(scene_w), ", ".join(str(v) for v in scene_w))
    # `animated` = a non-empty anim_tick (the "world has animated tiles" signal);
    # any statement suffices, so keep it import-free.
    anim_body = "        var t: u8 = 0\n" if animated else ""
    # The VM gate is just the presence of `import "vm.core"`; a tiny stub module
    # resolves it without the real runtime lib.
    vm_import = '    import "vm.core"\n' if vm else ""
    vm_mod = ('\nmodule "vm.core" {\n    export ping\n'
              '    function ping() { }\n}\n') if vm else ""
    ts_import, ts_decls, ts_paint = "", "", ""
    if extra_scene_ts is not None:
        name, count = extra_scene_ts
        ts_import = '    import "graphics.bkg"\n'
        ts_decls = ("    const %s: u8 = %d\n"
                    "    const %s_TS: array[u8, 16] = "
                    "[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]\n" % (name, count, name))
        ts_paint = ("    function paint(scene: u8) {\n"
                    "        bkg.set_data(0, %s, %s_TS)\n    }\n" % (name, name))
    return '''
module "scenes" {
%s    const TILE_COUNT: u8 = %d
    const MAP_W: u8 = %d
%s%s    const TILESET: array[u8, 16] = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]
%s    function anim_tick() {
%s    }
    export TILE_COUNT, MAP_W, TILESET, anim_tick
}
%s
module "prog" {
    import "graphics.bkg"
    import "scenes"
%s
    function main() {
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        loop { }
    }
}
''' % (ts_import, tile_count, map_w, sw, ts_decls, ts_paint, anim_body, vm_mod,
       vm_import)


def _noscenes(vm=True, upload=None):
    """A VM8 shell with NO scenes module (a script-only game). `upload` is an
    optional background statement in main()."""
    vm_import = '    import "vm.core"\n' if vm else ""
    vm_mod = ('\nmodule "vm.core" {\n    export ping\n'
              '    function ping() { }\n}\n') if vm else ""
    return '''
module "prog" {
    import "graphics.bkg"
%s
    const TILES: array[u8, 16] = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]
    const MAP: array[u8, 1] = [0]
    var n: u8 = 3

    function main() {
        %s
        loop { }
    }
}
%s''' % (vm_import, upload or "", vm_mod)


def _artmod(count=50):
    """A VM8 shell whose tile table lives in its OWN generated data module, the
    natural shape once the art is generated (projects/vm-snake). The upload
    count is then an IMPORTED const -- which the budget scan has to resolve, or
    the table falls back to the worst-case 256."""
    return '''
module "art" {
    const TILE_COUNT: u8 = %d
    const TILES: array[u8, 16] = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]
    export TILE_COUNT, TILES
}

module "prog" {
    import "graphics.bkg"
    import "vm.core"
    import "art"

    function main() {
        bkg.set_data(0, art.TILE_COUNT, art.TILES)
        loop { }
    }
}

module "vm.core" {
    export ping
    function ping() { }
}
''' % count


def _wide_prog(tile_count=6):
    """A VM8 wide world: importing engine.scroll selects the per-COLUMN strip
    engine, whose strip width is fixed (only the tile table can shrink)."""
    return '''
module "scenes" {
    const TILE_COUNT: u8 = %d
    const MAP_W: u8 = 64
    const TILESET: array[u8, 16] = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]
    function anim_tick() {
    }
    export TILE_COUNT, MAP_W, TILESET, anim_tick
}

module "vm.core" {
    export ping
    function ping() { }
}

module "engine.scroll" {
    export ping
    function ping() { }
}

module "prog" {
    import "graphics.bkg"
    import "engine.scroll"
    import "scenes"
    import "vm.core"

    function main() {
        bkg.set_data(0, scenes.TILE_COUNT, scenes.TILESET)
        loop { }
    }
}
''' % tile_count


def _defs(c):
    assert not c.startswith("Compilation error"), c[:400]
    mt = re.search(r"#define GBS_BKG_MAX_TILES\s+(\d+)", c)
    sw = re.search(r"#define GBS_BKG_STRIP_W\s+(\d+)", c)
    return (int(mt.group(1)) if mt else None,
            int(sw.group(1)) if sw else None)


def _c(prog, platform="lynx", **kw):
    return MosaikCompiler().compile_program([("p.mos", prog)],
                                            platform=platform, **kw)


def check(label, cond):
    print(("[PASS] " if cond else "[FAIL] ") + label)
    return cond


def main():
    ok = True

    # --- VM8 + scenes: BOTH derive (screen-sized rooms) --------------------
    mt, sw = _defs(_c(_prog(vm=True, tile_count=4, map_w=20)))
    ok &= check("[vm] 4 tiles / 20-wide -> table 4, strip 21", mt == 4 and sw == 21)

    # widest scene bounds the strip (a 32-wide non-uniform world -> strip 33)
    mt, sw = _defs(_c(_prog(vm=True, tile_count=8, map_w=20, scene_w=[32, 20, 32, 20])))
    ok &= check("[vm] widest scene 32 -> table 8, strip 33", mt == 8 and sw == 33)

    # animated tiles (non-empty anim_tick) keep the full 256 table; strip still derives
    mt, sw = _defs(_c(_prog(vm=True, tile_count=4, map_w=20, animated=True)))
    ok &= check("[vm] animated tiles keep the 256 table", mt == 256)
    ok &= check("[vm] animated tiles still shrink the strip", sw == 21)

    # --- NON-VM scenes program: full 256 / 52 (byte-identical) ---------------
    nonvm = _c(_prog(vm=False, tile_count=4, map_w=20))
    mt, sw = _defs(nonvm)
    ok &= check("[non-vm] scenes program stays full 256 / 52", mt == 256 and sw == 52)
    ok &= check("[non-vm] default emits the exact original 52 strip line",
                "#define GBS_BKG_STRIP_W   52  /* strip width in tiles" in nonvm)

    # --- explicit knobs override the auto-derive -----------------------------
    mt, sw = _defs(_c(_prog(vm=True, tile_count=4, map_w=20),
                      bkg_max_tiles=64, bkg_strip_w=40))
    ok &= check("[vm] explicit knobs honoured (64 / 40)", mt == 64 and sw == 40)

    # a shrunk strip carries the annotated define
    shrunk = _c(_prog(vm=True, tile_count=4, map_w=20))
    ok &= check("[vm] shrunk strip is annotated",
                "shrunk bkg_strip_w" in shrunk)

    # --- VM8 with NO scenes module: derive from the program's own bkg calls ---
    # A dialogue / menu / audio demo never touches the background layer, so the
    # 4 KB tile table is dead RAM -- but the engine still links (engine.camera
    # imports graphics.bkg), so the table must shrink, not vanish.
    mt, sw = _defs(_c(_noscenes()))
    ok &= check("[vm/no-scenes] no upload + no compose -> table 1", mt == 1)
    mt, _ = _defs(_c(_noscenes(upload="bkg.set_data(0, 12, TILES)")))
    ok &= check("[vm/no-scenes] upload of 12 tiles -> table 12", mt == 12)
    mt, _ = _defs(_c(_noscenes(upload="bkg.set_tiles(0, 0, 1, 1, MAP)")))
    ok &= check("[vm/no-scenes] a compose with no upload keeps 256", mt == 256)
    mt, _ = _defs(_c(_noscenes(upload="bkg.set_data(0, n, TILES)")))
    ok &= check("[vm/no-scenes] an unresolvable upload keeps 256", mt == 256)
    mt, _ = _defs(_c(_noscenes(vm=False)))
    ok &= check("[non-vm/no-scenes] stays the full 256 (byte-identical)", mt == 256)
    mt, _ = _defs(_c(_noscenes(), bkg_max_tiles=7))
    ok &= check("[vm/no-scenes] explicit bkg_max_tiles wins", mt == 7)

    # An IMPORTED const bounds the table too. A hand-written game keeps its tile
    # table in its own generated data module, so the count reaches set_data as
    # `art.TILE_COUNT`; that used to read as unresolvable and the table stayed
    # at 256 -- 8 KB of MAIN at 4bpp for 50 tiles, which is what overflowed the
    # 16-colour Lynx build of the falling-block assembly sample (and did so as a
    # link error a whole feature away, since a budget falling back to
    # worst-case says nothing).
    mt, _ = _defs(_c(_artmod(50)))
    ok &= check("[vm/no-scenes] an IMPORTED TILE_COUNT bounds the table", mt == 50)

    # --- per-scene tilesets bound the table, not just TILE_COUNT -------------
    # `[[scene]] tileset = "..."` uploads its own table over the same array, so
    # a scene tileset BIGGER than the shared TILE_COUNT sets the bound.
    mt, _ = _defs(_c(_prog(vm=True, tile_count=4, map_w=20,
                           extra_scene_ts=("TITLE_TC", 30))))
    ok &= check("[vm] a 30-tile per-scene tileset raises the table to 30", mt == 30)

    # --- the WIDE column engine derives the TABLE (its strip_w is fixed) -----
    wide = _c(_wide_prog(tile_count=6))
    mtw = re.search(r"#define GBS_BKG_MAX_TILES\s+(\d+)", wide)
    ok &= check("[vm/wide] the column engine still shrinks the tile table",
                mtw is not None and int(mtw.group(1)) == 6)
    ok &= check("[vm/wide] the column engine has no strip_w to shrink",
                "GBS_BKG_STRIP_W" not in wide)

    # --- GBDK console is untouched (Lynx-only) -------------------------------
    gb = _c(_prog(vm=True, tile_count=4, map_w=20), platform="gameboy")
    ok &= check("[gameboy] never emits the Lynx bkg strip define",
                "GBS_BKG_STRIP_W" not in gb)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
