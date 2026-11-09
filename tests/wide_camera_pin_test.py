#!/usr/bin/env python3
"""A scripted camera in a WIDE room addresses the whole room (the 2026-11-03
review's M16; decisions D2 / D3 2026-11-07).

`vm.player`'s scripted camera (the `camera_x` / `camera_y` / `camera_lock`
state writes, the CAM_MOVE_TO pan, `scroll_bg`) kept its pin in a u8 pair,
`cam_lx` / `cam_ly`. In a narrow room (at most 256 px a side) that is the
whole camera, and the u8 wrap is a FEATURE there (a `scroll_bg` over a
wrapping 32-column map scrolls seamlessly). In a WIDE room the camera runs to
the level width, and every writer narrowed it: `camera_lock x=1100` pinned at
1100 mod 256 = 76, `cam_hold` and `scroll_bg` seeded the pin from the camera
mod 256 (a pan or a scroll started up to 255 px away from the picture), and
CAM_MOVE_TO's operands are u8, so `camera_pan x=900` was MASKED to 132 at
encode without a word.

The fix:
  - a u16 pin for WIDE rooms only (`cam_wx` / `cam_wy`): the state writes,
    `cam_hold` (from `camx16` / `camy16` whole), `scroll_bg`'s seed, the pan
    step and `cam_apply`'s wide arm use it. Folded on `VM_CAM_WIDE_PIN`,
    stated when the world has a wide room AND the blob writes the camera,
    pans or scrolls the background; a narrow room keeps the u8 path as is;
  - `CAM_MOVE_TO16` (0x67, u16 x, u16 y, u8 step), emitted by `camera_pan`
    only when a target does not fit a byte (D2: every existing blob stays
    byte-identical);
  - `scroll_bg` in a wide room CLAMPS to the room (D3), as the follow camera
    and the pin are clamped there: the streamed map does not wrap;
  - `camera_pan` / `camera_lock` REFUSE a coordinate outside 0..65535.

Checks: the lowering (compact form when it fits, the wide form when not, the
refusals), refvm's semantics for the new opcode, the build flag, and real
ROMs from a fresh project (`tests/vm_fixture.py`): a 150 x 18 topdown room
(1,200 px wide), the camera pinned at x 600, panned to x 900, then scrolled
right into the edge; `camera_x()` read into heap cells after each step and
SCX checked against them (GB, PyBoy), the heap on SMS (retro.py). Mutation:
on the unfixed engine the pin read 88 (600 mod 256) and the pan's target was
masked; see the record.
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik8_build  # noqa: E402
import mosaik_vm as m  # noqa: E402
import vm_fixture  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))
    return cond


def _compile(events):
    return m.Compiler().compile([{"name": "main", "events": events + [{"event": "stop"}]}])


def lowering():
    print("\n[the lowering]")
    ops = getattr(m, "OPS", {})
    check("CAM_MOVE_TO16 is opcode 0x67 with (u16 x, u16 y, u8 step)",
          ops.get("CAM_MOVE_TO16") == (0x67, ["u16", "u16", "u8"]), ops.get("CAM_MOVE_TO16"))
    small = bytes(_compile([{"event": "camera_pan", "x": 200, "y": 40, "step": 3}]).code)
    check("a pan that fits a byte keeps the compact CAM_MOVE_TO (byte-identical)",
          bytes([0x3A, 200, 40, 3]) in small and 0x67 not in small[:1], small.hex())
    big = bytes(_compile([{"event": "camera_pan", "x": 900, "y": 40, "step": 3}]).code)
    check("a pan past 255 emits CAM_MOVE_TO16 with the whole coordinates",
          bytes([0x67, 900 & 0xFF, 900 >> 8, 40, 0, 3]) in big, big.hex())
    for ev in ({"event": "camera_pan", "x": 70000, "y": 0},
               {"event": "camera_pan", "x": -1, "y": 0},
               {"event": "camera_lock", "x": 70000, "y": 0},
               {"event": "camera_lock", "x": 0, "y": -5}):
        try:
            _compile([ev])
            check("%s %r refused" % (ev["event"], (ev["x"], ev["y"])), False)
        except m.VmError:
            check("%s %r refused" % (ev["event"], (ev["x"], ev["y"])), True)


def refvm_semantics():
    print("\n[refvm: CAM_MOVE_TO16]")
    prog = _compile([{"event": "camera_pan", "x": 900, "y": 300, "step": 100},
                     {"event": "set_var", "var": "cx", "expr": "camera_x()"},
                     {"event": "set_var", "var": "cy", "expr": "camera_y()"}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(5)
    check("the pan is still on its way after 5 frames (100 px a frame)",
          vm.cam_x == 500 and vm.heap[prog.variables["cx"]] == 0, (vm.cam_x, vm.cam_y))
    vm.run(10)
    check("it arrives at (900, 300) and the camera reads back whole",
          vm.heap[prog.variables["cx"]] == 900 and vm.heap[prog.variables["cy"]] == 300,
          (vm.heap[prog.variables["cx"]], vm.heap[prog.variables["cy"]]))
    lock = _compile([{"event": "camera_lock", "x": 1100, "y": 0},
                     {"event": "set_var", "var": "cx", "expr": "camera_x()"}])
    vm2 = m.RefVM(lock.code, entry=lock.entry)
    vm2.run(2)
    check("camera_lock x=1100 reads back 1100 (the reference VM's camera is u16)",
          vm2.heap[lock.variables["cx"]] == 1100, vm2.heap[lock.variables["cx"]])


def build_flag():
    print("\n[the build flag]")
    want = getattr(mosaik8_build, "_wants_cam_wide_pin", None)
    if not check("mosaik8_build._wants_cam_wide_pin exists", want is not None):
        return
    wide = [("rooms.mos", "        player.setup_roam(PTILE, px, py, PW, PH, WALK, mx, my, solid_at)")]
    narrow = [("rooms.mos", "        player.setup(PTILE, px, py, PW, PH, WALK, solid_at)")]

    def d(**kw):
        return dict(kw)
    check("a wide room + a camera_x write -> on", want(d(VM_ST_CAMERA_X=True), wide))
    check("a wide room + a pan -> on", want(d(VM_OP_CAM_MOVE_TO=True), wide))
    check("a wide room + the wide pan -> on", want(d(VM_OP_CAM_MOVE_TO16=True), wide))
    check("a wide room + scroll_bg -> on", want(d(VM_OP_SCROLL_BG=True), wide))
    check("a wide room + camera_lock -> on", want(d(VM_ST_CAMERA_LOCK=True), wide))
    check("a wide room, no scripted camera -> off", not want(d(), wide))
    check("a scripted camera, no wide room -> off",
          not want(d(VM_ST_CAMERA_X=True, VM_OP_SCROLL_BG=True), narrow))
    src = open(os.path.join(ROOT, "mosaik", "compiler.py"), encoding="utf-8").read()
    check("compile_program always supplies VM_CAM_WIDE_PIN = False",
          "setdefault('VM_CAM_WIDE_PIN', False)" in src)


W, H = 150, 18
WORLD = """[[scene]]
name = "long"
scene_type = "topdown"
map_w = %d
map = %s
[[scene.object]]
kind = "player"
x = 80
y = 72

[world]
module = "scenes"
map_w = 20
map_h = 18
vm = true

[tileset]
png = "tiles.png"

[kinds]
player = 0
""" % (W, vm_fixture.grid([[(x // 4 + y) % 2 for x in range(W)] for y in range(H)]))

SCRIPT = """[[script]]
name = "main"
events = [
  { event = "wait", frames = 30 },
  { event = "camera_lock", x = 600, y = 0 },
  { event = "wait", frames = 10 },
  { event = "set_var", var = "c1", expr = "camera_x()" },
  { event = "wait", frames = 10 },
  { event = "camera_pan", x = 900, y = 0, step = 8 },
  { event = "wait", frames = 2 },
  { event = "set_var", var = "c2", expr = "camera_x()" },
  { event = "wait", frames = 10 },
  { event = "scroll_bg", vx = 100, vy = 0 },
  { event = "wait", frames = 60 },
  { event = "set_var", var = "c3", expr = "camera_x()" },
  { event = "set_var", var = "done", value = 1 },
  { event = "wait_until", cond = "0" },
]
"""

CELLS = ("c1", "c2", "c3", "done")


def _syms(noi):
    out = {}
    for line in open(noi, encoding="utf-8", errors="replace"):
        g = re.match(r"DEF (\S+) 0x([0-9A-Fa-f]+)", line)
        if g:
            out[g.group(1)] = int(g.group(2), 16)
    return out


def _variables(root):
    return m.Compiler().compile(m.load_scripts(os.path.join(root, "scripts"))).variables


def _debug_build(root, plat):
    return subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                           "--debug", "--platform", plat, root],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def _expect(label, got, maxx):
    check("[%s] the pin at x 600 reads back 600" % label, got["c1"] == 600, got)
    check("[%s] the pan to x 900 arrives at 900" % label, got["c2"] == 900, got)
    check("[%s] scroll_bg into the right edge CLAMPS at the room's camera "
          "maximum (%d)" % (label, maxx), got["c3"] == maxx, got)


def gb_rom():
    print("\n[GB ROM: pin, pan and scroll past 255 px in a 1,200 px room]")
    try:
        import pyboy  # noqa: F401
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    fx = vm_fixture.build(WORLD, SCRIPT)
    try:
        r = _debug_build(fx.root, "gameboy")
        if not check("the GB fixture builds", r.returncode == 0 and fx.rom,
                     (r.stdout + r.stderr)[-800:]):
            return
        from pyboy import PyBoy
        syms = _syms(fx.rom[:-3] + ".noi")
        heap = syms["_vm_core_heap"]
        var = _variables(fx.root)
        pb = PyBoy(fx.rom, window="null", cgb=False)
        scx = {}
        got = {}
        for f in range(1500):
            pb.tick()
            got = {}
            for name in CELLS:
                a = heap + 2 * var[name]
                v = pb.memory[a] | pb.memory[a + 1] << 8
                got[name] = v - 0x10000 if v & 0x8000 else v
            for name in ("c1", "c2"):
                if got[name] and name not in scx:
                    scx[name] = pb.memory[0xFF43]
            if got["done"] == 1:
                scx["c3"] = pb.memory[0xFF43]
                break
        pb.stop(save=False)
        check("[GB] the script reached its end", got.get("done") == 1, got)
        _expect("GB", got, W * 8 - 160)
        check("[GB] SCX is the camera mod 256 at each reading (the streamer "
              "follows the u16 camera)",
              all(scx.get(k) == (got[k] & 0xFF) for k in ("c1", "c2", "c3")),
              (scx, got))
    finally:
        shutil.rmtree(fx.tmp, ignore_errors=True)


SMS_CHILD = r"""
import sys, json
sys.path.insert(0, sys.argv[1])
from retro import Core
c = Core(sys.argv[2], sys.argv[3])
heap, cells = int(sys.argv[4]), json.loads(sys.argv[5])
got = {}
for f in range(1500):
    c.run(1)
    ram = c.ram()
    got = {}
    for name, idx in cells.items():
        a = heap + 2 * idx - 0xC000
        v = ram[a] | ram[a + 1] << 8
        got[name] = v - 0x10000 if v & 0x8000 else v
    if got.get("done") == 1:
        break
print(json.dumps(got))
"""


def sms_rom():
    print("\n[SMS ROM: the same, through retro.py]")
    lr = os.path.join(ROOT, "emu", "libretro")
    cores = [c for c in os.listdir(lr) if c.startswith("genesis_plus_gx_libretro")] \
        if os.path.isdir(lr) else []
    if not cores:
        print("  (skip: genesis_plus_gx not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    fx = vm_fixture.build(WORLD, SCRIPT, "sms")
    try:
        r = _debug_build(fx.root, "sms")
        if not check("the SMS fixture builds", r.returncode == 0 and fx.rom,
                     (r.stdout + r.stderr)[-800:]):
            return
        heap = _syms(fx.rom[:-4] + ".noi")["_vm_core_heap"]
        var = _variables(fx.root)
        p = subprocess.run([sys.executable, "-c", SMS_CHILD, lr, os.path.join(lr, cores[0]),
                            fx.rom, str(heap), json.dumps({k: var[k] for k in CELLS})],
                           capture_output=True, text=True, timeout=600)
        if not check("the SMS child ran", p.returncode == 0, p.stderr[-800:]):
            return
        got = json.loads(p.stdout.strip().splitlines()[-1])
        check("[SMS] the script reached its end", got.get("done") == 1, got)
        _expect("SMS", got, W * 8 - 256)
    finally:
        shutil.rmtree(fx.tmp, ignore_errors=True)


def main():
    print("=== wide_camera_pin_test ===")
    lowering()
    refvm_semantics()
    build_flag()
    gb_rom()
    sms_rom()
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
