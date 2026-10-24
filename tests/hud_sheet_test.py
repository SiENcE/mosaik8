#!/usr/bin/env python3
"""HUD elements per console, and a panel's own sprite sheet (2026-10-24).

An element's `consoles` list says where it is drawn (empty = everywhere), so one
panel can carry a full status strip for the window-layer consoles and a compact
sprite one for the SMS / Game Gear, whose VDP draws 8 sprites a scanline and has
no window. A panel `sheet` lends its cells to icons, numbers (`<prefix>0..9`) and
bars (`<prefix>0..N`), placed in pixels; the panel's show uploads the sheet below
the glyph band at the top of the SMS/GG sprite area, its sprites take the
table's last slots, and the generated room load keeps the actors below both.

Checks: the generated module (guards, the sheet base, digits, bar, the parking
in reshow, the refusals, the per-line count); the room generator's caps; and a
copy of projects/vm-shmup with a sheet panel built for the Game Gear, the Master
System and the Game Boy (the guarded code compiles where it is used and folds
away where it is not). A HUD without the new keys is emitted unchanged: every
sample's hud.mos and rooms.mos regenerate identical (tools/regen_generated.py).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik_vm import emit_hud_mos  # noqa: E402
from mosaik_vm import hud as H  # noqa: E402
from mosaik_vm.isa import VmError  # noqa: E402
import mosaik8_build  # noqa: E402

FAILS = []


def check(cond, what, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond or not detail else "  [%s]" % detail))
    if not cond:
        FAILS.append(what)
    return cond


def _sheet():
    names = ["d%d" % i for i in range(10)] + ["life"] + ["bar%d" % i for i in range(5)]
    cells = {n: (i, 1, 1) for i, n in enumerate(names)}       # 8x8 cells, one tile each
    return {"cells": cells, "order": names, "tiles": len(names)}


GG = ["gamegear", "sms"]
PANEL = {"name": "status", "rows": 1, "sheet": "hudsheet", "sheet_slot": 1, "element": [
    {"id": 1, "kind": "label", "x": 1, "y": 0, "text": "HP", "consoles": ["gameboy"]},
    {"id": 2, "kind": "icon", "sprite": "life", "px": 2, "py": 2, "consoles": GG},
    {"id": 3, "kind": "var", "var": "hp", "sprite": "d0", "width": 2, "pad": "0",
     "px": 10, "py": 2, "consoles": GG},
    {"id": 4, "kind": "gauge", "var": "hp", "max": 6, "sprite": "bar0", "width": 2,
     "px": 2, "py": 120, "consoles": GG},
]}


def generated():
    print("\n[the generated module]")
    src = emit_hud_mos([PANEL], {"hp": 0}, sprite_text=("sms", "gamegear"), reshow=True,
                       sheets={"hudsheet": _sheet()})
    check('if platform == "gameboy" {' in src and 'text.print_string(1, SCREEN_ROWS - 1 + 0, "HP")' in src,
          "a Consoles list guards the element where it is drawn (the label: Game Boy only)")
    check("const HUD_SHEET_BASE: u8 = %d" % (252 - 16) in src,
          "the sheet sits at the top of the SMS/GG sprite area, below the bubble's 252")
    check("const HUD_C0: array[u8, 1] = [10]" in src and
          "const HUD_C1: array[u8, 10] = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]" in src and
          "const HUD_C2: array[u8, 5] = [11, 12, 13, 14, 15]" in src,
          "each cell series is a table of tile offsets (icon, digits 0..9, bar states 0..4)")
    check("sprite.set_tile(63, HUD_SHEET_BASE + 10)" in src and "sprite.move(63, 2, 2)" in src,
          "the icon takes the table's last slot and its pixel position")
    check("sprite.set_tile(61, HUD_SHEET_BASE + HUD_C1[d0])" in src
          and "sprite.move(62, 10 + 8, 2)" in src,
          "the number draws its digits from the sheet, right-aligned, one sprite a cell")
    check("fill = fill * 8 / 6" in src and "if k1 > 4 { k1 = 4 }" in src
          and "sprite.set_tile(60, HUD_SHEET_BASE + HUD_C2[c1])" in src,
          "the bar spreads the value over 2 sprites x 4 fill steps")
    check("palette.set_2bpp(0x7650)" in src and "sprite.set_data(HUD_SHEET_BASE, hudsheet_tile_count, hudsheet_tiles)" in src,
          "show uploads the sheet in its SMS/GG palette slot (slot 1 = 0x7650)")
    i = src.index("function reshow()")
    body = src[i:src.index("    }\n", src.index("show(active)", i))]
    check("if core.hud_on() == 0 {" in body and "for hs in 59..64 {" in body,
          "reshow parks the sheet's sprites while the HUD is hidden (HUD_SHOW 0)")
    for bad, what in ((["gameboy"], "a Game Boy console"), ([], "every console")):
        p = dict(PANEL, element=[dict(PANEL["element"][1], consoles=bad)])
        try:
            emit_hud_mos([p], {"hp": 0}, sheets={"hudsheet": _sheet()})
            ok = False
        except VmError as e:
            ok = "sms / gamegear" in str(e)
        check(ok, "a sheet element on %s is refused with the reason" % what)
    p = dict(PANEL, element=[dict(PANEL["element"][2], sprite="life")])
    try:
        emit_hud_mos([p], {"hp": 0}, sheets={"hudsheet": _sheet()})
        ok = False
    except VmError as e:
        ok = "<prefix>0 .. <prefix>9" in str(e)
    check(ok, "a number naming a cell with no 0..9 series is refused")
    wide = dict(PANEL, element=[dict(PANEL["element"][2], width=3, id=10 + k, px=24 * k)
                                for k in range(3)])
    check(H.sheet_line_load([wide], {"hudsheet": _sheet()}, "sms")[0] == 9,
          "the per-line count adds every sheet sprite on a scanline (3 numbers x 3 = 9)")
    plain = emit_hud_mos([dict(PANEL, sheet=None,
                               element=[dict(PANEL["element"][0], consoles=[])])], {"hp": 0})
    check("HUD_SHEET" not in plain and "if platform == \"gameboy\" {" not in plain,
          "no sheet and no Consoles list: nothing of either is emitted")


def core_hides_sprites():
    print("\n[HUD_SHOW 0 reaches the HUD's sprites]")
    src = open(os.path.join(ROOT, "lib", "vm", "core.mos"), encoding="utf-8").read()
    i = src.index("case OP_HUD_SHOW {")
    arm = src[i:src.index("case OP_", i + 10)]
    off = arm[arm.index("} else {"):]
    check("hud_visible = 0" in off and "g_hud_show()" in off,
          "HUD_SHOW 0 calls the HUD's re-show hook after hiding the band (it parks its sprites)")
    j = src.index("function hud_on() -> u8 {")
    check("return hud_visible" in src[j:j + 80] and re.search(r"export .*\bhud_on\b", src),
          "core.hud_on() reports the state the hook reads")


def _fixture(dst):
    shutil.copytree(os.path.join(ROOT, "projects", "vm-shmup"), dst,
                    ignore=shutil.ignore_patterns("build", "__pycache__"))
    from PIL import Image
    names = ["d%d" % i for i in range(10)] + ["life"] + ["bar%d" % i for i in range(5)]
    im = Image.new("P", (8 * len(names), 8), 0)
    im.putpalette([255, 0, 255, 255, 255, 255, 160, 160, 160, 0, 0, 0] + [0] * (252 * 3))
    for i in range(len(names)):
        for y in range(8):
            for x in range(8):
                if (x + y + i) % 3 == 0:
                    im.putpixel((8 * i + x, y), 1 + (i % 3))
    im.save(os.path.join(dst, "assets", "hudsheet.png"))
    with open(os.path.join(dst, "assets", "hudsheet.sprites.toml"), "w", encoding="utf-8") as f:
        for i, n in enumerate(names):
            f.write('[[sprite]]\nname = "%s"\nrect = [%d, 0, 8, 8]\n\n' % (n, 8 * i))
    p = os.path.join(dst, "mosaik.toml")
    t = open(p, encoding="utf-8").read()
    t = t.replace('sprites = [ "assets/sprites.png" ]',
                  'sprites = [ "assets/sprites.png", "assets/hudsheet.png" ]')
    open(p, "w", encoding="utf-8").write(t)
    p = os.path.join(dst, "scripts", "hud.toml")
    t = open(p, encoding="utf-8").read()
    others = '\nconsoles = [ "gameboy", "gameboy_color", "lynx", "pce" ]\n'
    t = t.replace('text = "HP"\n', 'text = "HP"' + others)
    t = t.replace('style = "hearts"\n', 'style = "hearts"' + others)
    t = t.replace('rows = 1\n', 'rows = 1\nsheet = "hudsheet"\nsheet_slot = 1\n', 1)
    t += ('\n[[panel.element]]\nid = 20\nkind = "icon"\nsprite = "life"\npx = 2\npy = 2\n'
          'consoles = [ "gamegear", "sms" ]\n'
          '\n[[panel.element]]\nid = 21\nkind = "var"\nvar = "php"\nsprite = "d0"\nwidth = 1\n'
          'px = 10\npy = 2\nconsoles = [ "gamegear", "sms" ]\n'
          '\n[[panel.element]]\nid = 22\nkind = "gauge"\nvar = "php"\nmax = 6\nsprite = "bar0"\n'
          'width = 2\nstyle = "bar"\npx = 2\npy = 120\nconsoles = [ "gamegear", "sms" ]\n')
    open(p, "w", encoding="utf-8").write(t)
    import mosaik_vm
    mosaik_vm.generate_hud(dst)
    mosaik_vm.generate_rooms(dst)


def rooms_and_builds(tmp):
    print("\n[the room load keeps clear; GG / SMS / GB build]")
    dst = os.path.join(tmp, "vm-shmup")
    _fixture(dst)
    res = H.smsgg_reservation(dst)
    # the glyph band is ONE list for the sprite-text consoles (SMS, GG, PCE):
    # the PCE's "HP" label puts H and P in it, so the sheet sits 2 lower
    check(res == {"oam": 4, "tile_lo": 252 - 2 - 16},
          "the reservation: 4 sprites from the top, the tiles under the sheet + glyph band", res)
    rooms = open(os.path.join(dst, "src", "rooms.mos"), encoding="utf-8").read()
    check("const ACT_OAM_TOP: u8 = 60" in rooms and "projectile.set_top(ACT_OAM_TOP)" in rooms,
          "rooms.mos stops the actors and the shots below the HUD's 4 slots on SMS/GG")
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    for plat in ("gamegear", "sms", "gameboy"):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                            "--platform", plat, dst], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        check(r.returncode == 0, "%s: the sheet panel builds" % plat, (r.stdout + r.stderr)[-400:])
        c = os.path.join(dst, "build", plat, "vm-shmup.c")
        if r.returncode == 0 and os.path.isfile(c):
            text = open(c, encoding="utf-8", errors="replace").read()
            # the sheet is a registered asset (in every build, as any sheet);
            # what folds away off SMS/GG is the HUD code that draws from it
            on = re.search(r"HUD_SHEET_BASE|hudsheet_tile_count\)", text) is not None
            check(on == (plat != "gameboy"),
                  "%s: the HUD %s the sheet" % (plat, "draws from" if plat != "gameboy" else "never touches"))


def main():
    print("=" * 60)
    print("HUD: per-console elements + a panel's own sprite sheet")
    print("=" * 60)
    generated()
    core_hides_sprites()
    tmp = tempfile.mkdtemp(prefix="hud_sheet_")
    try:
        rooms_and_builds(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("hud_sheet_test: all passed" if not FAILS else "hud_sheet_test: %d FAILED" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
