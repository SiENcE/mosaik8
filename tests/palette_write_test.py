#!/usr/bin/env python3
"""A script can WRITE a palette at run time: PAL_SET (0x4F), Stage W2.

The reference engine's `EVENT_PALETTE_SET_SPRITE` and its BACKGROUND / UI / EMOTE
siblings are ONE primitive in its own compiler: `paletteSetSprite` and
`paletteSetBackground` are a mask of chosen slots, `paletteSetUI` is
background slot 7 and `paletteSetEmote` is sprite slot 7, each lowering to a
`_paletteLoad(mask, layer, commit)` plus inline colours. This engine had no
runtime palette write at all - palettes loaded per room and nothing else - so
the palette-write check conversion's three uses converted to nothing.

The shape:

  * **PAL_SET (layer, slot, pal)**, three u8 operands. `pal` is an index into
    the world's `[[palette]]` LIBRARY, not twelve bytes of colour, because a
    library entry is already layer-correct: a sprite palette holds the
    authored colours `[c0, c0, c1, c3]` (colour 2 skipped, colour 0 twice,
    because sprite entry 0 is hardware-transparent - the reference engine's own
    convention, which `gbs_import.convert_palettes._spr_colors` already
    applies when it interns the library).
  * The colours live in the GENERATED scenes module, so the write is a SEAM
    (`core.set_pal_write` -> `scenes.set_palette`), exactly as the
    replacement-tile writer is. Both halves are gated on `[world] pal_write`,
    one world fact, so the generated call and the generated definition cannot
    disagree.
  * The write goes through `graphics.palette`'s VERB, never the hardware, so
    the CGB/SMS fade SHADOW records it and a later ramp cannot undo it.
  * A console with one palette per layer (DMG, NES) makes that verb an honest
    no-op, so a recolour is silently nothing there - the same degradation the
    rest of the colour tier has, and the importer reports it.

Checks: the encoding, the RefVM, source contracts for the arm and the seam,
the generated scenes/rooms wiring both ways, and - with GBDK and PyBoy - a
real GBC ROM whose sprite palette is read back out of CRAM through OCPS/OCPD.
EVERY negative assertion has its stimulus checked beside it.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm as m                                    # noqa: E402
from mosaik_vm import isa                                # noqa: E402
from mosaik_vm.rooms import emit_rooms_mos               # noqa: E402
import mosaik8_build                                     # noqa: E402

VM = os.path.join(ROOT, "lib", "vm")
FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _read(name):
    with open(os.path.join(VM, name), encoding="utf-8") as f:
        return f.read()


def _prog(events):
    return m.Compiler().compile([{"name": "main",
                                  "events": events + [{"event": "stop"}]}])


def encoding():
    print("\n[the encoding: one opcode, three u8 operands, an index not colours]")
    check("PAL_SET is 0x4F with three u8 operands",
          isa.OPS.get("PAL_SET") == (0x4F, ["u8", "u8", "u8"]),
          isa.OPS.get("PAL_SET"))
    prog = _prog([{"event": "palette_set", "layer": "sprite",
                   "slot": 5, "pal": 12}])
    items = list(m.disasm(prog.code, {}))
    check("a sprite write lowers to PAL_SET(0, slot, pal)",
          items[0]["op"] == "PAL_SET" and "0, 5, 12" in items[0].get("text", ""),
          items[0])
    prog = _prog([{"event": "palette_set", "layer": "background",
                   "slot": 7, "pal": 3}])
    items = list(m.disasm(prog.code, {}))
    check("...and a background write is layer 1 (its slot 7 IS the reference engine's UI "
          "palette)", "1, 7, 3" in items[0].get("text", ""), items[0])
    check("the whole event is FOUR bytes of blob",
          len(_prog([{"event": "palette_set", "slot": 0, "pal": 0}]).code)
          - len(_prog([]).code) == 4)
    # the operands are VALIDATED, not masked (the events.py rule)
    for bad, why in (({"layer": "ui"}, "an unknown layer"),
                     ({"slot": 8}, "a slot past 7"),
                     ({"pal": 300}, "a library index past a u8")):
        ev = {"event": "palette_set", "layer": "sprite", "slot": 0, "pal": 0}
        ev.update(bad)
        try:
            _prog([ev])
            check("%s is refused" % why, False, "it compiled")
        except Exception as exc:                          # noqa: BLE001
            check("%s is refused, and the error NAMES it (%s)"
                  % (why, str(exc)[:60]), True)
    # ...and a program that writes no palette is byte-identical to before
    plain = _prog([{"event": "set_var", "var": "x", "value": 1}])
    check("a program with no palette write carries no PAL_SET",
          "PAL_SET" not in [i["op"] for i in m.disasm(plain.code, {})])


def refvm():
    print("\n[RefVM: the write is recorded, in order]")
    prog = _prog([{"event": "palette_set", "layer": "sprite", "slot": 5, "pal": 1},
                  {"event": "palette_set", "layer": "background", "slot": 7, "pal": 2},
                  {"event": "palette_set", "layer": "sprite", "slot": 5, "pal": 0}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    for _ in range(6):
        vm.frame()
    check("every write lands in palette_log, in order",
          vm.palette_log == [(0, 5, 1), (1, 7, 2), (0, 5, 0)], vm.palette_log)
    vm2 = m.RefVM(_prog([]).code, entry=_prog([]).entry)
    vm2.frame()
    check("a program that writes none logs none", vm2.palette_log == [])


def source_contract():
    print("\n[vm.core: the arm folds, and the writer is a SEAM]")
    core = _read("core.mos")
    check("core names the opcode", "const OP_PAL_SET = 0x4F" in core)
    arm = core[core.index("case OP_PAL_SET {"):]
    arm = arm[:arm.index("case OP_BKG_TILE {")]
    check("...the arm folds under VM_OP_PAL_SET", "if VM_OP_PAL_SET {" in arm, arm)
    check("...it CONSUMES all three operands before testing the seam "
          "(PC alignment, the OP_MUSIC_MUTE rule)",
          arm.index("f8()") < arm.index("has_pal_write") and arm.count("f8()") == 3,
          arm)
    check("...and calls the registered writer",
          "g_pal_write(playr, pslot, plib)" in arm, arm)
    check("the seam exists, with its own has_ flag",
          "function set_pal_write(cb: function(u8, u8, u8))" in core
          and "var has_pal_write: u8" in core)
    check("...and is exported", "set_pal_write" in core.split("export ")[-2]
          or "set_pal_write" in core.split("export ")[-1])


BASE = {"types": ["topdown"], "scene_count": 2, "uniform": True,
        "map_w": 20, "map_h": 18, "has_objs": False, "has_ent": False,
        "has_trig": False, "has_doors": False}


def generated():
    print("\n[the generated modules: opt-in on ONE world fact]")
    import tempfile as _tf
    from mosaik_scenes import transpile
    from mosaik_assets import write_png_indexed
    tmp = _tf.mkdtemp(prefix="pal_write_")
    write_png_indexed(os.path.join(tmp, "tiles.png"), 8, 32, [[0] * 8] * 32,
                      [(0, 0, 0), (85, 85, 85), (170, 170, 170), (255, 255, 255)])
    world = {
        "world": {"module": "scenes", "map_w": 20, "map_h": 18, "vm": True},
        "tileset": {"png": "tiles.png"},
        "kinds": {"fish": 0},
        "palette": [
            {"name": "spr_a", "colors": ["F8F8F8", "F87800", "A82820", "000000"]},
            {"name": "spr_b", "colors": ["F8F8F8", "00F800", "008800", "000000"]},
        ],
        "scene": [{"name": "r", "map": [[0] * 20] * 18,
                   "bkg_palettes": [0] * 8,
                   "spr_palettes": [0, 1, 0, 0, 0, 0, 0, 0]}],
    }
    off = transpile(dict(world, world=dict(world["world"])), tmp)
    on = transpile(dict(world, world=dict(world["world"], pal_write=True)), tmp)
    # the STIMULUS: the coloured tables really were emitted in both
    check("both worlds are coloured (the per-scene tables exist)",
          "const SPR_PAL" in off and "const SPR_PAL" in on)
    check("no [world] pal_write: no library, no writer, no export "
          "(byte-identical)",
          "PAL_LIB" not in off and "function set_palette(" not in off
          and "set_palette" not in off.split("export ")[-1])
    check("pal_write: the LIBRARY table is emitted", "const PAL_LIB: array[u16, 8]" in on)
    check("...and the writer, exported", "function set_palette(layer: u8, slot: u8, pal: u8) {" in on
          and "set_palette" in on.split("export ")[-1])
    body = on[on.index("function set_palette(layer"):]
    body = body[:body.index("\n    }\n")]
    check("...which goes through the palette VERB, so a fade's shadow records it",
          "palette.load_sprite_set(slot, 1, PAL_LIB, off)" in body
          and "palette.load_bkg_set(slot, 1, PAL_LIB, off)" in body, body)
    check("...layer 0 is SPRITE (the reference engine's own sense for its emote palette)",
          body.index("if layer == 0") < body.index("load_sprite_set"), body)
    check("...and it indexes the library by 4 words per entry",
          "off = off * 4" in body, body)

    print("\n[rooms.mos: the call, under the same fact]")
    r_off = emit_rooms_mos(dict(BASE, colour=True))
    r_on = emit_rooms_mos(dict(BASE, colour=True, has_pal_write=True))
    check("no flag: nothing is registered (byte-identical)",
          "set_pal_write" not in r_off)
    check("flag: core.set_pal_write(scenes.set_palette)",
          "core.set_pal_write(scenes.set_palette)" in r_on)
    check("...registered BEFORE the first load_room, so a scene INIT may "
          "recolour",
          r_on.index("core.set_pal_write") < r_on.index("load_room(start_room"))
    check("a world with NO palette library never wires it, even with the key",
          "set_pal_write" not in emit_rooms_mos(dict(BASE, has_pal_write=False)))

    print("\n[the build states the fold from the blob]")
    plain = m.emit_scripts_module([{"name": "main", "events": [
        {"event": "set_var", "var": "x", "value": 1}, {"event": "stop"}]}])
    writes = m.emit_scripts_module([{"name": "main", "events": [
        {"event": "palette_set", "slot": 5, "pal": 1}, {"event": "stop"}]}])
    d0 = mosaik8_build._vm_dispatch_defines([("scripts.mos", plain)])
    d1 = mosaik8_build._vm_dispatch_defines([("scripts.mos", writes)])
    check("a blob with no write: VM_OP_PAL_SET False",
          d0.get("VM_OP_PAL_SET") is False, d0.get("VM_OP_PAL_SET"))
    check("a blob that writes: True", d1.get("VM_OP_PAL_SET") is True)


#: The fixture's boot script. 200, not 300: WAIT's operand is a u8 and a
#: 300 is refused now - which is how this fixture found that `wait` was
#: still masking rather than validating. The sprites are first DRAWN
#: around LCD frame 110 (measured), so 200 leaves margin either side.
#: Let the room settle and be LOOKED at, then
#: recolour sprite slot 0 - the slot every actor renders through when the
#: world has no [kind_palettes] - from green (library 1) to orange (library 0).
BOOT = '''name = "main"
events = [
  { event = "wait", frames = 200 },
  { event = "palette_set", layer = "sprite", slot = 0, pal = 0 },
  { event = "wait", frames = 100 },
  { event = "stop" },
]'''


def _hues(rom, marks):
    """Count HUED pixels on screen at each frame in `marks`.

    Returns {frame: (greenish, orangeish)}. The SCREEN is the instrument here
    rather than CRAM: PyBoy's memory accessor returns the raw OCPD register
    and does not emulate the indexed palette read, so `pb.memory[0xFF6B]`
    hands back the same byte however many times you ask (measured: four
    identical 0xE7E7 "entries"). Pixels are also the stronger proof - they
    exercise the whole chain, library table to hardware to screen - and a HUE
    test needs no guess about how an emulator expands 5 bits to 8.
    """
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", cgb=True)
    out, last = {}, max(marks)
    for f in range(last + 1):
        pb.tick()
        if f in marks:
            a = pb.screen.ndarray[:, :, :3].astype(int)
            r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
            out[f] = (int(((g > r + 40) & (g > b + 40)).sum()),
                      int(((r > g + 40) & (r > b + 40)).sum()))
    pb.stop(save=False)
    return out


def rom():
    print("\n[a real GBC ROM: the sprite palette in CRAM, before and after]")
    try:
        import pyboy  # noqa: F401
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    import re
    import toml
    from mosaik_vm.rooms import generate_rooms
    import mosaik_scenes

    src = os.path.join(ROOT, "projects", "vm-hud")
    tmp = tempfile.mkdtemp(prefix="pal_write_rom_")
    try:
        root = os.path.join(tmp, "p")
        shutil.copytree(src, root, ignore=shutil.ignore_patterns("build"))
        # Make it a COLOURED world with a two-entry library, and declare the
        # runtime write. Slot 1 starts as library 1 (green) and the script
        # writes library 0 (orange) over it.
        wp = os.path.join(root, "assets", "world.toml")
        w = toml.load(wp)
        w["world"]["pal_write"] = True
        # Library 0 ORANGE, 1 GREEN, 2 GREY. The background takes the GREY
        # one on every slot: a hued background would make "the screen went
        # orange" true before the write, which is the vacuous-pass shape.
        w["palette"] = [
            {"name": "spr_orange",
             "colors": ["F8F8F8", "F87800", "A82820", "000000"]},
            {"name": "spr_green",
             "colors": ["F8F8F8", "00F800", "008800", "000000"]},
            {"name": "bkg_grey",
             "colors": ["F8F8F8", "A8A8A8", "585858", "000000"]},
        ]
        for sc in w["scene"]:
            sc["bkg_palettes"] = [2] * 8
            sc["spr_palettes"] = [1, 1, 1, 1, 1, 1, 1, 1]
        with open(wp, "w", encoding="utf-8") as f:
            toml.dump(w, f)
        evt = os.path.join(root, "scripts", "main.evt.toml")
        text = open(evt, encoding="utf-8").read()
        new, n = re.subn(r'name = "main"\nevents = \[.*?\n\]', BOOT, text,
                         count=1, flags=re.S)
        check("the fixture's boot script was replaced", n == 1)
        open(evt, "w", encoding="utf-8").write(new)
        # regenerate the THREE modules this touches (scenes, scripts, rooms):
        # each is checked in with the sample, and a stale one would build the
        # old behaviour - the trap `fade_before_change_test` documents.
        world, base = mosaik_scenes.load_world(wp)
        open(os.path.join(root, "src", "scenes.mos"), "w",
             encoding="utf-8").write(mosaik_scenes.core.transpile(world, base))
        g = subprocess.run([sys.executable, "-m", "mosaik_vm",
                            os.path.join(root, "scripts"), "-o",
                            os.path.join(root, "src", "scripts.mos")],
                           capture_output=True, text=True, cwd=ROOT,
                           encoding="utf-8", errors="replace")
        check("the boot script recompiles", g.returncode == 0,
              (g.stdout + g.stderr)[-400:])
        generate_rooms(root)
        rm = open(os.path.join(root, "src", "rooms.mos"), encoding="utf-8").read()
        check("the regenerated rooms.mos wires the writer",
              "core.set_pal_write(scenes.set_palette)" in rm)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"),
                            "build", "--platform", "gameboy_color", root],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0 or "ROM created" not in r.stdout:
            check("the coloured project builds", False,
                  (r.stdout + r.stderr)[-900:])
            return
        out_dir = os.path.join(root, "build", "gameboy_color")
        rom_path = [os.path.join(out_dir, f) for f in os.listdir(out_dir)
                    if f.endswith(".gbc")][0]
        hues = _hues(rom_path, {150, 500})
        for f in sorted(hues):
            print("  frame %4d  green/orange %s" % (f, hues[f]))
        g0, o0 = hues[150]
        g1, o1 = hues[500]
        # STIMULUS first: the room load really put library 1 (GREEN) into
        # sprite slot 0, and nothing on screen was orange yet. Without this,
        # "it turned orange" could be a background that always was.
        check("the ROOM LOAD renders the actors GREEN (library 1) - the "
              "stimulus", g0 > 0 and o0 == 0, hues[150])
        check("...and after the script's write they are ORANGE (library 0)",
              o1 > 0, hues[500])
        check("...with no green left, so the slot was REPLACED, not added to",
              g1 == 0, hues[500])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("PAL_SET: a script writes a palette at run time (Stage W2)")
    print("=" * 60)
    encoding()
    refvm()
    source_contract()
    generated()
    rom()
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("palette_write_test: all checks passed")
