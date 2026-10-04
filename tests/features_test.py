#!/usr/bin/env python3
"""Tests for the language features added for larger programs:

  * const array data emission (real C `const` tables, not #define)
  * raw hardware register access (hw.read / hw.write)
  * switch / case (multi-value labels, default arm)
  * while loops with break / continue
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

from mosaik import MosaikCompiler


def compile_ok(src):
    out = MosaikCompiler().compile(src)
    assert not out.startswith("Compilation error:"), out
    return out


def test_const_arrays():
    src = '''
    module "m" {
        const tiles: array[u8, 8] = [0,1,2,3,4,5,6,7]
        const MAX: u8 = 200
        function main() { tiles[0] }
        export main
    }
    '''
    out = compile_ok(src)
    assert "const uint8_t tiles[8] = {0, 1, 2, 3, 4, 5, 6, 7};" in out, out
    # Scalar consts stay #defines.
    assert "#define MAX (200)" in out, out
    print("[ok] const array data")


def test_hardware_access():
    src = '''
    module "m" {
        import "platform.hardware"
        function main() {
            hw.write(REG_BGP, 228)
            var d: u8 = hw.read(REG_DIV)
            hw.write(65351, d)
        }
        export main
    }
    '''
    out = compile_ok(src)
    assert "gbs_hw_write(uint16_t addr, uint8_t value)" in out, out
    assert "gbs_hw_read(uint16_t addr)" in out, out
    assert "gbs_hw_write(REG_BGP, 228);" in out, out
    assert "gbs_hw_read(REG_DIV)" in out, out
    assert "#define REG_BGP" in out, out
    print("[ok] hardware register access")


def test_switch():
    src = '''
    module "m" {
        var x: u8 = 0
        function main() {
            switch x {
                case 0 { x = 1 }
                case 1, 2, 3 { x = 2 }
                default { x = 9 }
            }
        }
        export main
    }
    '''
    out = compile_ok(src)
    assert "switch (x) {" in out, out
    assert "case 0: {" in out, out
    # Multi-value labels fall through to a single shared body.
    assert "case 1:\n" in out, out
    assert "case 2:\n" in out, out
    assert "case 3: {" in out, out
    assert "} break;" in out, out
    assert "default: {" in out, out
    print("[ok] switch / case")


def test_hex_literals():
    src = '''
    module "m" {
        const DATA: array[u8, 3] = [0x00, 0xE4, 0b1010]
        function main() { DATA[0] }
        export main
    }
    '''
    out = compile_ok(src)
    # 0xE4 == 228, 0b1010 == 10
    assert "const uint8_t DATA[3] = {0, 228, 10};" in out, out
    print("[ok] hex / binary literals")


def test_graphics_stdlib():
    src = '''
    module "m" {
        import "platform.video"
        import "platform.system"
        import "graphics.sprite"
        import "graphics.bkg"
        import "graphics.window"
        const TILES: array[u8, 16] = [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]
        var map: array[u8, 4]
        function main() {
            sprite.set_data(0, 1, TILES)
            sprite.set_prop(0, FLIP_X)
            sprite.move(0, 80, 72)
            var t: u8 = sprite.get_tile(0)
            bkg.set_data(0, 1, TILES)
            bkg.set_tiles(0, 0, 2, 2, map)
            bkg.scroll(1, 0)
            window.set_tiles(0, 0, 2, 2, map)
            window.move(7, 128)
            video.show_sprites()
            video.show_window()
            system.seed_random(7)
            map[0] = system.random()
            system.delay(4)
        }
        export main
    }
    '''
    out = compile_ok(src)
    # Calls lower to the underlying GBDK functions / helpers.
    for needle in ["set_sprite_data(0, 1, TILES);", "set_sprite_prop(0, FLIP_X);",
                   "move_sprite(0, 80, 72);", "get_sprite_tile(0)",
                   "set_bkg_data(0, 1, TILES);", "set_bkg_tiles(0, 0, 2, 2, map);",
                   "scroll_bkg(1, 0);", "set_win_tiles(0, 0, 2, 2, map);",
                   "move_win(7, 128);", "gbs_show_sprites();", "gbs_show_win();",
                   "initrand(7);", "rand()", "delay(4);"]:
        assert needle in out, (needle, out)
    # Sprite flag constants and the rand.h include are present.
    assert "#define FLIP_X S_FLIPX" in out, out
    assert "#include <rand.h>" in out, out
    print("[ok] graphics / sprite / window / system stdlib")


def test_while_break_continue():
    src = '''
    module "m" {
        var x: u8 = 0
        var n: u8 = 0
        function main() {
            while x < 10 {
                x += 1
                if x == 5 { continue }
                if x == 8 { break }
                n += x
            }
        }
        export main
    }
    '''
    out = compile_ok(src)
    assert "while ((x < 10)) {" in out, out
    assert "continue;" in out, out
    assert "break;" in out, out
    print("[ok] while / break / continue")


def main():
    test_const_arrays()
    test_hardware_access()
    test_hex_literals()
    test_graphics_stdlib()
    test_switch()
    test_while_break_continue()
    print("All feature tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
