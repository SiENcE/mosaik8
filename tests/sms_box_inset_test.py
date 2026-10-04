#!/usr/bin/env python3
"""The SMS dialogue/menu box INSETS one column while the left-column blank is up.

A column-streamed SMS room arms `bkg.edge_mask` (VDP R0 bit 5), which hides
screen column 0 - the whole 32-column name table is on screen there, so the
seam column has nowhere else to go. A full-width box (the reference engine's
`set_box_metrics(0, SCREEN_COLS)`) then drew its left BORDER under the mask:
measured on the SMS/GG sample conversion's long walk-in room, the frame's left outline was
simply missing (columns 0..7 showed the backdrop).

The fix: vm.core draws the box at an EFFECTIVE geometry - while the mask is
armed (`player.edge_masked()`, which tracks `wide` because the same setups arm
and clear both) a box that reaches column 0 starts at column 1 and clamps its
width to the 31 visible columns, so both borders land on visible cells. A
painted SMS room (mask off) and every other console keep the authored span
verbatim: the helpers, `edge_masked` and every call are gated `if platform ==
"sms"`, so they FOLD AWAY elsewhere (the reference-engine sample conversion's gameboy C is byte-identical
with and without this feature - checked by hash at the time; here the fold
mechanism itself is pinned on a synthetic module).
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

_FAILED = []
_LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "lib", "vm")


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def test_source_shape():
    print("\n[vm.core: effective box geometry, SMS-gated]")
    core = open(os.path.join(_LIB, "core.mos"), encoding="utf-8").read()
    check("local function eff_box_col()" in core
          and "local function eff_box_w()" in core,
          "the effective col/w helpers exist")
    gated = core.split("local function eff_box_col()")[0].rsplit("if platform", 1)
    check(gated[1].lstrip().startswith('== "sms"'),
          "... and are defined under `if platform == \"sms\"`")
    check("player.edge_masked()" in core,
          "... keyed on the mask state, not on a per-room guess")
    check('if platform == "sms" {\n            return eff_box_col() + 1' in core,
          "box_left() (the TEXT origin) follows the inset")
    # THREE live SMS draw sites now: the dialogue box's unstaged path (the
    # console that draws it inline), the SMS/GG STAGED path (which draws the
    # border in its own frame - see ui_stage in vm.core), and the menu.
    check(core.count("g_box_draw(eff_box_col(), ") == 3,
          "every live SMS draw site (dialogue inline + staged + menu) uses "
          "the effective geometry")
    lynx = re.split(r'if platform == "lynx" \{\n    (?:hot )?local function draw_open_ui',
                    core)[1]
    check("g_box_draw(box_col, " in lynx,
          "the Lynx redraw path stays verbatim (no mask there)")

    print("\n[vm.player: the mask-state read]")
    player = open(os.path.join(_LIB, "player.mos"), encoding="utf-8").read()
    check("function edge_masked()" in player
          and player.split("function edge_masked()")[0]
                    .rstrip().endswith('if platform == "sms" {'),
          "edge_masked() exists, gated to the SMS")
    body = player.split("function edge_masked()")[1].split("}")[0]
    check("wide > 0" in body,
          "... and tracks `wide` (the setups arm the mask, clear_wide clears both)")
    check("export edge_masked" in player, "... and is exported for vm.core")


def test_gated_export_folds():
    """The mechanism the fix rides on: an exported function defined under
    `if platform` folds away - definition AND name - on the other consoles."""
    print("\n[toolchain: a platform-gated exported function folds away]")
    from mosaik import MosaikCompiler

    mod = '''
module "mod_a" {
    var wide: u8
    if platform == "sms" {
    function edge_masked() -> u8 {
        if wide > 0 { return 1 }
        return 0
    }
    }
    function poke() { wide = 1 }
    export edge_masked, poke
}
'''
    main = '''
module "main" {
    import "mod_a"
    function main() {
        mod_a.poke()
        var v: u8 = 0
        if platform == "sms" { v = mod_a.edge_masked() }
        while true {
            if v == 255 { mod_a.poke() }
        }
    }
}
'''
    srcs = [("mod_a.mos", mod), ("main.mos", main)]
    sms = MosaikCompiler().compile_program(srcs, platform="sms")
    gb = MosaikCompiler().compile_program(srcs, platform="gameboy")
    check("edge_masked" in sms, "sms: the gated function exists and is called")
    check("edge_masked" not in gb,
          "gameboy: definition, prototype and call are all gone")


def main():
    test_source_shape()
    test_gated_export_folds()
    print()
    if _FAILED:
        print("Some tests failed:")
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
