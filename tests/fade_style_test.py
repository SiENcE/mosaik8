#!/usr/bin/env python3
"""The fade DIRECTION (state 32, `fade_style`) - the reference engine fades to WHITE by
default (Stage W3 of the studio's gbs-event-lowering plan, 2026-09-15).

The reference VM's `fade_manager.c` ramps the DMG palette registers one shade per step,
and its `fade_style` engine field decides which way: 0 subtracts a shade per
2-bit field (`DMGFadeToWhiteStep`, BGP E4 -> 90 -> 40 -> 00), 1 adds one
(`DMGFadeToBlackStep`, E4 -> F9 -> FE -> FF). On a CGB the same field picks
`CGBFadeToWhiteStep` / `CGBFadeToBlackStep`. Its default is 0, WHITE, and 7
of the 8 reference-engine projects on disk keep it - MEASURED on the sample's
reference ROM before this was built (`tools/projprobe/fade_style_probe.py`):
the reference walks E4 -> 90 -> 40 -> 00, ours walked E4 -> F9 -> FE -> FF.

`vm.fx` only ever darkened. Now:
  * STATE 32 `fade_style`, WRITE-only and PERSISTENT (not a one-shot latch),
    in the reference engine's numbering (0 white / 1 black), handed to `fx.set_style`.
  * `fx.ramp` gains the white arm (a shade LIGHTER per level, floor 0) and
    `set_level` passes the direction to the colour drawer in BIT 7 of the
    level, so the one registered callback carries both.
  * `graphics.palette`'s fade honours that bit on the CGB class and SMS/GG
    (every channel scaled towards FULL instead of 0).
  * a project DEFAULT is `studio.toml [scenes] fade_style = "white"|"black"`,
    which the generated rooms.mos applies with `fx.set_style` before the
    first room load.
  * everything folds under VM_FADE_STYLE, which the build states from the
    blob (VM_ST_FADE_STYLE) OR the rooms.mos default; a project that never
    asks for a direction is byte-identical (measured on the carried
    conversions before their regeneration).

RefVM checks for the state, SOURCE-CONTRACT checks for the arms (the
box_hold_test shape), generated-C checks for the palette scaler, rooms-wiring
checks for the default, and - with GBDK and PyBoy present - a real ROM's BGP
trajectory in both directions.
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

import mosaik_vm as m  # noqa: E402
from mosaik_vm import isa  # noqa: E402
from mosaik import MosaikCompiler  # noqa: E402
from mosaik_vm.rooms import emit_rooms_mos  # noqa: E402
import mosaik8_build  # noqa: E402

VM = os.path.join(ROOT, "lib", "vm")
FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _read(name):
    with open(os.path.join(VM, name), encoding="utf-8") as f:
        return f.read()


def _prog(style=None):
    evs = []
    if style is not None:
        evs.append({"event": "set_state", "state": "fade_style", "value": style})
    evs += [{"event": "fade_out", "frames": 4},
            {"event": "set_var", "var": "after", "value": 1},
            {"event": "stop"}]
    return m.Compiler().compile([{"name": "main", "events": evs}])


def encoding():
    print("\n[the encoding: a persistent state, no new opcode, no blob change]")
    check("state 32 is `fade_style`", isa.STATES.get("fade_style") == 32,
          isa.STATES.get("fade_style"))
    prog = _prog(0)
    ops = [i["op"] for i in m.disasm(prog.code, {})]
    check("a style write is RPN + SET_STATE before the fade",
          ops[:3] == ["RPN", "SET_STATE", "FADE"], ops[:3])
    txt = "\n".join(i.get("text", "") for i in m.disasm(prog.code, {}))
    check("it pushes the style and writes state 32",
          "PUSH 0" in txt and "SET_STATE  32" in txt, txt.replace("\n", " | "))
    plain = [i["op"] for i in m.disasm(_prog().code, {})]
    check("a fade WITHOUT a style is byte-identical to before the feature",
          plain[0] == "FADE" and "SET_STATE" not in plain, plain)


def refvm():
    print("\n[RefVM: the state is persistent and in the reference engine's numbering]")
    vm = m.RefVM(_prog().code, entry=_prog().entry)
    check("the host default is BLACK (1)", vm.fade_style == 1, vm.fade_style)
    prog = _prog(0)
    vm = m.RefVM(prog.code, entry=prog.entry)
    for _ in range(8):
        vm.frame()
    check("a write of 0 flips it to WHITE and it stays",
          vm.fade_style == 0 and vm.heap[prog.variables["after"]] == 1,
          (vm.fade_style, vm.heap[prog.variables["after"]]))
    prog = _prog(1)
    vm = m.RefVM(prog.code, entry=prog.entry)
    for _ in range(8):
        vm.frame()
    check("a write of 1 is black again", vm.fade_style == 1, vm.fade_style)
    prog = _prog(7)
    vm = m.RefVM(prog.code, entry=prog.entry)
    for _ in range(8):
        vm.frame()
    check("any non-zero is black (the reference VM tests the field for truth)",
          vm.fade_style == 1, vm.fade_style)


def source_contract():
    print("\n[vm.core: the SET_STATE arm]")
    core = _read("core.mos")
    check("core names the state", "const ST_FADE_STYLE = 32" in core)
    arm = core[core.index("if VM_ST_FADE_STYLE {"):]
    arm = arm[:arm.index("\n        }\n")]
    check("... and its arm folds under VM_ST_FADE_STYLE and hands the value to fx",
          "if sid == ST_FADE_STYLE {" in arm and "fx.set_style(v)" in arm, arm)

    print("\n[vm.fx: both ramps, one drawer]")
    fx = _read("fx.mos")
    check("set_style exists and is exported",
          "function set_style(style: u8)" in fx
          and "set_style" in fx.split("export ")[-1])
    check("the direction is in the reference engine's numbering (0 = white)",
          "if style == 0 {\n                to_white = 1" in fx)
    check("the direction cell lives INSIDE the fold (no BSS shift for a non-user)",
          "if VM_FADE_STYLE {\n        var to_white: u8\n    }" in fx)
    ramp = fx[fx.index("local function ramp("):]
    ramp = ramp[:ramp.index("return out")]
    check("the DMG ramp's white arm subtracts a shade per level, floor 0",
          "if f < l {" in ramp and "f -= l" in ramp and "if VM_FADE_STYLE {" in ramp)
    check("... and the towards-black arm is kept character for character "
          "in BOTH the else of the fold and the else of the style",
          ramp.count("f += l\n") == 2 and ramp.count("if f > 3 {") == 2, ramp)
    lvl = fx[fx.index("function set_level("):]
    lvl = lvl[:lvl.index("\n    }\n")]
    check("set_level hands the direction to the colour drawer in BIT 7",
          "g_pal(l | 0x80)" in lvl and lvl.count("g_pal(l)") == 2, lvl)
    check('vm.fx still does NOT import graphics.palette',
          'import "graphics.palette"' not in fx)
    for plat in ("gameboy", "gameboy_color", "lynx", "sms", "gamegear", "nes"):
        for on in (False, True):
            try:
                out = MosaikCompiler().compile_program(
                    [("fx.mos", fx)], platform=plat,
                    defines={"VM_FADE_STYLE": on})
                if out.startswith("Compilation error"):
                    raise RuntimeError(out)
            except Exception as exc:                    # pragma: no cover
                check(False, "vm.fx compiles on %s with VM_FADE_STYLE=%s (%s)"
                      % (plat, on, exc))
                return
    check(True, "vm.fx compiles on every console class, style on and off")


PAL_PROG = '''
module "main" {
    import "platform.video"
    import "graphics.palette"
    const PAL: array[u16, 8] = [0,1,2,3,4,5,6,7]
    function main() {
        palette.load_bkg_set(0, 2, PAL, 0)
        palette.load_sprite_set(0, 2, PAL, 0)
        palette.fade(3)
    }
}
'''
NATIVE_PROG = '''
module "main" {
    import "platform.video"
    import "graphics.palette"
    var BUF: array[u8, 32]
    function main() {
        palette.load_native(0, 2, BUF)
        palette.fade(3)
    }
}
'''


def _gen(platform, src, on):
    out = MosaikCompiler().compile_program([("m.mos", src)], platform=platform,
                                           defines={"VM_FADE_STYLE": on})
    if out.startswith("Compilation error"):
        raise RuntimeError(out)
    return out


def generated_c():
    print("\n[graphics.palette: the white scale is gated on the build define]")
    for plat, src, full in (("gameboy_color", PAL_PROG, "0x7FFF"),
                            ("sms", NATIVE_PROG, "0x3F"),
                            ("gamegear", NATIVE_PROG, "0xFFF")):
        off = _gen(plat, src, False)
        on = _gen(plat, src, True)
        check("%s: no define, no white text (byte-identical)" % plat,
              "gbs_fade_white" not in off and "level >> 7" not in off)
        check("%s: with the define, bit 7 selects the white scale" % plat,
              "gbs_fade_white" in on and "level >> 7" in on
              and "level &= 0x7F" in on)
        check("%s: ... whose full-white word is %s" % (plat, full),
              ("return %s;" % full) in on)
        check("%s: ... and a level change alone still re-issues the palettes"
              % plat, "w == gbs_fade_white) return;" in on)
        check("%s: the black scale is unchanged by the define" % plat,
              "if (gbs_fade_lvl == 3) return 0;" in on
              if plat == "gameboy_color" else "if (n == 0) return 0;" in on)


BASE = {"types": ["topdown"], "scene_count": 2, "uniform": True,
        "map_w": 20, "map_h": 18, "has_objs": False, "has_ent": False,
        "has_trig": False, "has_doors": False}


def rooms_wiring():
    print("\n[rooms.mos: the project default]")
    off = emit_rooms_mos(dict(BASE, fade=2, colour=True))
    check("no [scenes] fade_style: no set_style (byte-identical)",
          "fx.set_style" not in off)
    white = emit_rooms_mos(dict(BASE, fade=2, colour=True, fade_style="white"))
    check("white: start() writes 0 (the reference engine's numbering) before the first load",
          "fx.set_style(0)" in white
          and white.index("fx.set_style(0)") < white.index("load_room(start_room"))
    black = emit_rooms_mos(dict(BASE, fade=2, fade_style="black"))
    check("black: declared, so a script can flip it - start() writes 1",
          "fx.set_style(1)" in black)
    alone = emit_rooms_mos(dict(BASE, fade_style="white"))
    check("a style with NO auto-fade still imports vm.fx (scripted fades use it)",
          'import "vm.fx"' in alone and "fx.set_style(0)" in alone)

    print("\n[the build: VM_FADE_STYLE from the blob OR the rooms default]")
    scripts = m.emit_scripts_module(
        [{"name": "main", "events": [{"event": "fade_out"}, {"event": "stop"}]}])
    styled = m.emit_scripts_module(
        [{"name": "main", "events": [
            {"event": "set_state", "state": "fade_style", "value": 0},
            {"event": "fade_out"}, {"event": "stop"}]}])
    d = mosaik8_build._vm_dispatch_defines([("scripts.mos", scripts),
                                            ("rooms.mos", off)])
    check("neither: off", d.get("VM_FADE_STYLE") is False
          and d.get("VM_ST_FADE_STYLE") is False, d.get("VM_FADE_STYLE"))
    d = mosaik8_build._vm_dispatch_defines([("scripts.mos", styled),
                                            ("rooms.mos", off)])
    check("the blob writes the state: on", d.get("VM_FADE_STYLE") is True
          and d.get("VM_ST_FADE_STYLE") is True)
    d = mosaik8_build._vm_dispatch_defines([("scripts.mos", scripts),
                                            ("rooms.mos", white)])
    check("only the rooms default: on (the per-use scan cannot see a CALL)",
          d.get("VM_FADE_STYLE") is True and d.get("VM_ST_FADE_STYLE") is False)
    # THE LEAK THE FIRST A/B FOUND: the interpreter itself calls fx.set_style
    # from its SET_STATE arm, and the build scans EVERY source - so a scan for
    # the call matched every VM8 game and all 13 A/B ROMs differed. The marker
    # is rooms.mos's declaration comment, which no library file carries.
    core = _read("core.mos")
    check("core.mos itself calls fx.set_style (the arm)", "fx.set_style(v)" in core)
    d = mosaik8_build._vm_dispatch_defines([("scripts.mos", scripts),
                                            ("rooms.mos", off),
                                            ("core.mos", core),
                                            ("fx.mos", _read("fx.mos"))])
    check("... and the library sources alone do NOT turn the fold on",
          d.get("VM_FADE_STYLE") is False, d.get("VM_FADE_STYLE"))


def _bgp_trail(rom, frames=240):
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", cgb=False)
    seen = []
    last = None
    for _ in range(frames):
        pb.tick()
        v = pb.memory[0xFF47]
        if v != last:
            seen.append(v)
            last = v
    pb.stop(save=False)
    return seen


def rom():
    print("\n[a real ROM: the boot fade's BGP trajectory, both directions]")
    try:
        import pyboy  # noqa: F401
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    src = os.path.join(ROOT, "projects", "vm-hud")
    from mosaik_vm.rooms import generate_rooms
    for style, want, forbid in (("white", {0x90, 0x40}, {0xF9, 0xFE}),
                                ("black", {0xF9, 0xFE}, {0x90, 0x40})):
        tmp = tempfile.mkdtemp(prefix="fade_style_")
        try:
            root = os.path.join(tmp, "p")
            shutil.copytree(src, root, ignore=shutil.ignore_patterns("build"))
            with open(os.path.join(root, "studio.toml"), "a", encoding="utf-8") as f:
                f.write('\n[scenes]\nfade = 2\nfade_style = "%s"\n' % style)
            generate_rooms(root)
            r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"),
                                "build", "--platform", "gameboy", root],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0 or "ROM created" not in r.stdout:
                check("%s: the styled project builds" % style, False,
                      (r.stdout + r.stderr)[-800:])
                continue
            rom_path = os.path.join(root, "build", "gameboy", "p.gb")
            if not os.path.isfile(rom_path):
                found = [f for f in os.listdir(os.path.join(root, "build", "gameboy"))
                         if f.endswith(".gb")]
                rom_path = os.path.join(root, "build", "gameboy", found[0])
            trail = _bgp_trail(rom_path)
            hexes = " ".join("%02X" % v for v in trail)
            check("%s: the boot ramp passes through %s"
                  % (style, " / ".join("%02X" % v for v in sorted(want))),
                  want & set(trail) == want, hexes)
            check("%s: ... and never through the other ramp's values" % style,
                  not (forbid & set(trail)), hexes)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    encoding()
    refvm()
    source_contract()
    generated_c()
    rooms_wiring()
    rom()
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("fade_style_test: all checks passed")
