#!/usr/bin/env python3
import os, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""mosaik_vm.hud -- the overlay-HUD generator (src/hud.mos from scripts/hud.toml).

The studio HUD editor authors overlay PANELS; this generator lowers them to a
`hud` module drawn on the GB window band: show(id) plots a panel's labels + var fields, update() refreshes a var field
only when its heap cell changed, hide() drops the band. This pins the emitted
shape + the byte-identical-when-absent rule (no hud.toml -> no module). The end to
end render (window map 0x9C00, live update) is proven on PyBoy by projects/vm-hud.
"""

from mosaik_vm import emit_hud_mos, generate_hud, load_hud_panels

# a two-element panel + a var field whose cell is NOT in the heap map (skipped)
PANELS = [{
    "name": "main-hud",
    "rows": 1,
    "element": [
        {"id": 1, "kind": "label", "x": 1, "y": 0, "text": "SCORE"},
        {"id": 2, "kind": "var", "x": 7, "y": 0, "var": "score", "width": 3},
        {"id": 3, "kind": "var", "x": 0, "y": 0, "var": "ghost"},   # unresolved
    ],
}]
VARIABLES = {"score": 0}          # `ghost` intentionally absent


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("mosaik_vm.hud -- overlay-HUD codegen")
    print("=" * 50)
    ok = True

    src = emit_hud_mos(PANELS, VARIABLES)
    ok &= check("emits a `hud` module importing vm.core (has var fields) + text",
                'module "hud" {' in src and 'import "vm.core"' in src
                and 'import "graphics.text"' in src)
    ok &= check("public show/update/hide exported",
                "export show, update, draw, hide" in src
                and "function show(id: u8) {" in src)
    ok &= check("the label is plotted at its cell",
                'text.print_string(1, SCREEN_ROWS - 1 + 0, "SCORE")' in src)
    ok &= check("the var field reads its heap cell + prints the number",
                "core.var_get(0)" in src and "text.print_number(7 + off," in src)
    ok &= check("the var field pad-fills its width + right-aligns (no junk tile)",
                'text.print_string(7, SCREEN_ROWS - 1 + 0, "   ")' in src
                and "if nd < 3 { off = 3 - nd }" in src)
    ok &= check("update() diffs a cached last value (auto-redraw)",
                "var last_p0_e2: i16 = -1" in src
                and "if core.var_get(0) != last_p0_e2 {" in src)
    ok &= check("an unresolved var (no heap cell) is SKIPPED, not emitted",
                "last_p0_e3" not in src and "ghost" not in src)

    # tiles (static plot_tile stamp) + gauge (a var-driven tile STRIP)
    tg = emit_hud_mos([{"name": "hp", "rows": 1, "element": [
        {"id": 1, "kind": "gauge", "x": 3, "y": 0, "var": "hp", "max": 5, "base_tile": 100},
        {"id": 2, "kind": "tiles", "x": 9, "y": 0, "base_tile": 100, "w": 2, "h": 1},
    ]}], {"hp": 0})
    ok &= check("a gauge emits a full/empty tile strip over its max cells",
                "for gi in 0..5 {" in tg and "if v > gi { gt = 100 }" in tg
                and "text.plot_tile(3 + gi," in tg)
    ok &= check("a gauge is dynamic (cached + diffed like a var field)",
                "var last_p0_e1: i16 = -1" in tg
                and "if core.var_get(0) != last_p0_e1 {" in tg)
    ok &= check("tiles emit a raw w*h plot_tile stamp of consecutive ids",
                "for tj2 in 0..1 {" in tg and "for ti2 in 0..2 {" in tg
                and "text.plot_tile(9 + ti2," in tg and "100 + tj2 * 2 + ti2)" in tg)
    ok &= check("a gauge with no max is skipped",
                "gi in" not in emit_hud_mos(
                    [{"name": "g", "element": [
                        {"id": 1, "kind": "gauge", "var": "hp"}]}], {"hp": 0}))
    # max_var -> a PROPORTIONAL fill (fill = value*max/max_var), replacing v>gi
    mv = emit_hud_mos([{"name": "g", "rows": 1, "element": [
        {"id": 1, "kind": "gauge", "x": 2, "y": 0, "var": "hp", "max": 8,
         "base_tile": 100, "max_var": "hpmax"}]}], {"hp": 0, "hpmax": 1})
    ok &= check("a max_var gauge computes a proportional fill = value*max/max_var",
                "var fill: u8 = vv * 8 / mv" in mv and "if gi < fill {" in mv
                and "if v > gi" not in mv)
    # F5: a max_var gauge redraws when the MAX changes too (not only the value),
    # so a growing max HP / a full MP bar refreshes -- tracks a second cached cell.
    ok &= check("a max_var gauge caches + diffs the MAX cell (redraw on max change)",
                "last_p0_e1_m = core.var_get(" in mv
                and "!= last_p0_e1 or core.var_get(" in mv
                and "!= last_p0_e1_m {" in mv)
    # a gauge with a `sprite_tile` forks: tile strip on GB, sprite hearts on Lynx/PCE
    gspr = emit_hud_mos([{"name": "p", "rows": 1, "element": [
        {"id": 1, "kind": "gauge", "x": 13, "y": 0, "var": "hp", "max": 5,
         "base_tile": 100, "sprite_tile": 3}]}], {"hp": 0})
    ok &= check("a sprite-backed gauge forks at MODULE level (if platform, foldable)",
                'if platform == "lynx" or platform == "pce" {' in gspr
                and gspr.count("local function draw_p0_e1() {") == 2)
    ok &= check("the Lynx gauge draws sprite hearts (shown/parked per cell)",
                "sprite.set_tile(35 + gi, 3)" in gspr    # 5 cells reserved 35..39
                and "sprite.move(35 + gi, 0, SCREEN_HEIGHT)" in gspr
                and "text.plot_tile(13 + gi," in gspr)   # the GB tile arm too

    # attached event scripts: On Show (once) + On Change (edge, on a var change)
    slots = emit_hud_mos([{"name": "p", "rows": 1, "element": [
        {"id": 1, "kind": "var", "x": 0, "y": 0, "var": "hp",
         "on_show": "on_start", "on_change": "on_hurt", "on_x": "ghost"},
    ]}], {"hp": 0}, {"on_start", "on_hurt"})
    ok &= check("an attached-event HUD imports scripts + vm.core",
                'import "scripts"' in slots and 'import "vm.core"' in slots)
    ok &= check("On Show spawns its script once in show()",
                "core.spawn(scripts.ENTRY_on_start)" in slots
                and "-- On Show" in slots)
    ok &= check("On Change spawns its script inside the value-change branch",
                "core.spawn(scripts.ENTRY_on_hurt)" in slots
                and "-- On Change" in slots)
    # a slot bound to a NON-existent script is skipped (no dangling ENTRY ref)
    dangle = emit_hud_mos([{"name": "p", "element": [
        {"id": 1, "kind": "label", "text": "X", "on_show": "nope"}]}], {}, set())
    ok &= check("a dangling slot ref is skipped (no scripts import, no spawn)",
                'import "scripts"' not in dangle and "spawn" not in dangle)

    # icon: a pinned OAM sprite at a reserved high slot (39 down), on GB + Lynx
    ic = emit_hud_mos([{"name": "p", "rows": 2, "element": [
        {"id": 1, "kind": "icon", "x": 18, "y": 0, "base_tile": 2},
        {"id": 2, "kind": "icon", "x": 0, "y": 1, "base_tile": 3},
    ]}], {})
    ok &= check("an icon panel imports graphics.sprite",
                'import "graphics.sprite"' in ic)
    ok &= check("icons take reserved high OAM slots (39, 38) so they miss the actors",
                "sprite.set_tile(39, 2)" in ic and "sprite.set_tile(38, 3)" in ic)
    ok &= check("an icon is positioned in screen pixels within the band",
                "sprite.move(39, 144, (SCREEN_ROWS - 2 + 0) * 8)" in ic)
    ok &= check("hide() parks every icon offscreen",
                ic.count("sprite.move(39, 0, SCREEN_HEIGHT)") >= 1
                and "sprite.move(38, 0, SCREEN_HEIGHT)" in ic)

    # a labels-only panel does NOT import vm.core (nothing reads the heap)
    labels_only = emit_hud_mos(
        [{"name": "t", "rows": 1, "element": [
            {"id": 1, "kind": "label", "x": 0, "y": 0, "text": "HP"}]}], {})
    ok &= check("a labels-only panel drops the vm.core import (byte-cleanliness)",
                'import "vm.core"' not in labels_only
                and 'import "graphics.text"' in labels_only)

    # ---- the SPRITE-TEXT backend (a band over a SCROLLING room) --------------
    # SMS/GG/PCE have a persistent tilemap and no window layer, so plotted text
    # is a MAP cell and slides away with the level. Those consoles draw the
    # band's text with sprites instead; every other target keeps the plot.
    panel = [{"name": "p", "rows": 1, "element": [
        {"id": 1, "kind": "label", "x": 1, "y": 0, "text": "HP"},
        {"id": 2, "kind": "var", "x": 4, "y": 0, "var": "score", "width": 3, "pad": "0"},
        {"id": 3, "kind": "gauge", "x": 9, "y": 0, "var": "hp", "max": 5,
         "base_tile": 100, "sprite_tile": 3}]}]
    plain = emit_hud_mos(panel, {"score": 1, "hp": 0})
    st = emit_hud_mos(panel, {"score": 1, "hp": 0},
                      sprite_text=("sms", "gamegear", "pce"))
    guard = 'if platform == "sms" or platform == "gamegear" or platform == "pce" {'
    ok &= check("no sprite-text consoles -> the identical module as before",
                "HUD_CHARS" not in plain and guard not in plain
                and "font_glyph" not in plain
                and 'text.print_string(1, SCREEN_ROWS - 1 + 0, "HP")' in plain)
    # The module carries CHARACTER CODES, never a font's pixels: the glyphs
    # come from the console font the toolchain links (sprite.font_glyph).
    ok &= check("the band's characters are codes, forked, so no other console carries them",
                guard in st and "const HUD_CHARS: array[u8, 12] = [" in st  # 0-9 H P
                and "72,   -- 'H'" in st and "80   -- 'P'" in st
                and "0x66" not in st and "HUD_GLYPHS" not in st)
    ok &= check("the glyph base counts DOWN from each console's sprite tile top",
                'if platform == "sms" or platform == "gamegear" {' in st
                and "const HUD_GLYPH_BASE: u8 = 176" in st       # 188 - 12 glyphs
                and '} else if platform == "pce" {' in st
                and "const HUD_GLYPH_BASE: u8 = 28" in st)       # 40 - 12
    ok &= check("a label draws one sprite per character, and keeps the text arm",
                "sprite.set_tile(33, HUD_GLYPH_BASE + 10)" in st   # 'H' after 0-9
                and "sprite.move(33, 8, (SCREEN_ROWS - 1 + 0) * 8)   -- 'H'" in st
                and 'text.print_string(1, SCREEN_ROWS - 1 + 0, "HP")' in st)
    ok &= check("a var field draws its cells right-aligned out of the digit glyphs",
                "var d0: u8 = v / 100 % 10" in st and "var d2: u8 = v % 10" in st
                and "if off > 0 {" in st
                and "sprite.set_tile(30, HUD_GLYPH_BASE + 0)" in st   # zero-pad cell
                and "text.print_number(4 + off," in st)              # the plotted arm
    ok &= check("show() uploads each glyph from the linked console font, under the guard",
                "for gi in 0..12 {" in st
                and "sprite.font_glyph(HUD_GLYPH_BASE + gi, HUD_CHARS[gi])" in st)
    ok &= check("hide() parks the band's text sprites too",
                "-- park the band's text" in st)
    # the gauge joins the Lynx/PCE sprite arm on SMS/GG only when they scroll
    ok &= check("a scrolling SMS/GG gauge switches to the sprite hearts",
                ('if platform == "lynx" or platform == "pce" or platform == "sms" '
                 'or platform == "gamegear" {') in st
                and 'if platform == "lynx" or platform == "pce" {' in plain)
    # per console: a world that fits the SMS but not the Game Gear forks only GG
    gg_only = emit_hud_mos(panel, {"score": 1, "hp": 0}, sprite_text=("gamegear",))
    ok &= check("the backend is resolved PER CONSOLE, not for the whole family",
                'if platform == "gamegear" {' in gg_only
                and 'platform == "sms"' not in gg_only)

    # generate_hud on a temp project: with a hud.toml -> file; without -> None
    tmp = tempfile.mkdtemp(prefix="hudgen_")
    try:
        os.makedirs(os.path.join(tmp, "scripts"))
        with open(os.path.join(tmp, "scripts", "main.evt.toml"), "w") as f:
            f.write('[[script]]\nname = "main"\n'
                    'events = [ { event = "set_var", var = "score", value = 1 }, '
                    '{ event = "stop" } ]\n')
        ok &= check("no hud.toml -> generate_hud returns None (byte-identical)",
                    generate_hud(tmp) is None
                    and not os.path.isfile(os.path.join(tmp, "src", "hud.mos")))
        with open(os.path.join(tmp, "scripts", "hud.toml"), "w") as f:
            f.write('[[panel]]\nname = "hud"\n'
                    '[[panel.element]]\nid = 1\nkind = "var"\nx = 0\ny = 0\n'
                    'var = "score"\nwidth = 2\n')
        ok &= check("load_hud_panels reads the authored panel",
                    len(load_hud_panels(os.path.join(tmp, "scripts"))) == 1)
        p = generate_hud(tmp)
        gen = open(p, encoding="utf-8").read()
        ok &= check("generate_hud writes src/hud.mos resolving `score` to its cell",
                    p and os.path.isfile(p) and "core.var_get(" in gen
                    and "export show, update, draw, hide" in gen)
        # THE RE-SHOW HOOK: emitted only for a shell that registers it, so
        # every other HUD project's module is byte-identical.
        ok &= check("no core.set_hud_show in the shell -> no reshow (byte-identical)",
                    "reshow" not in gen)
        os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
        shell = os.path.join(tmp, "src", "main.mos")
        with open(shell, "w", encoding="utf-8") as f:
            f.write('module "main" {\n    -- core.set_hud_show(hud.reshow) (a comment)\n}\n')
        ok &= check("...a commented-out call does not count",
                    "reshow" not in open(generate_hud(tmp), encoding="utf-8").read())
        with open(shell, "w", encoding="utf-8") as f:
            f.write('module "main" {\n    function main() {\n'
                    '        core.set_hud_show(hud.reshow)\n    }\n}\n')
        gen2 = open(generate_hud(tmp), encoding="utf-8").read()
        ok &= check("a shell that registers it gets hud.reshow (re-shows the active panel)",
                    "function reshow()" in gen2 and "show(active)" in gen2
                    and "export show, update, draw, hide, reshow" in gen2)
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import mosaik8_build as mb
        ok &= check("the build states VM_HUD_RESHOW off the same call (and only it)",
                    mb._wants_hud_reshow([("main.mos", "core.set_hud_show(hud.reshow)")])
                    and not mb._wants_hud_reshow([("main.mos", "-- core.set_hud_show(x)")])
                    and not mb._wants_hud_reshow([("main.mos", "core.set_hud(a, b)")]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # The backend reads the world WHEREVER the project keeps it: a scaffolded
    # project's world is assets/world.toml, which a root-only probe missed, so
    # its HUD plotted into the scrolling map on SMS / GG / PCE (2026-10-09).
    from mosaik_vm.hud import sprite_text_consoles
    tmp = tempfile.mkdtemp(prefix="hudworld_")
    try:
        ok &= check("no world anywhere -> no sprite-text console",
                    sprite_text_consoles(tmp) == ())
        tall = ('[world]\nmap_w = 20\nmap_h = 18\n'
                '[[scene]]\nname = "sky"\nmap_h = 32\n')
        for where in (("world.toml",), ("assets", "world.toml"),
                      ("world", "world.toml"), ("assets", "world", "world.toml")):
            path = os.path.join(tmp, *where)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(tall)
            ok &= check("a 20x32 room in %s scrolls on SMS, GG and PCE" % "/".join(where),
                        sprite_text_consoles(tmp) == ("sms", "gamegear", "pce"))
            os.remove(path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 50)
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
