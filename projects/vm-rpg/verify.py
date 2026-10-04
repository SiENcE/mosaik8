#!/usr/bin/env python3
"""Verify vm-rpg -- the VM8 COMPOSITION proof.

Proves every kit works TOGETHER in one game:
  * RefVM (deterministic): the elder's say-once key + the flag-GATED change_scene.
  * PyBoy (on-ROM): the field renders the player + the elder + the 6-heart HUD; the
    elder's dialogue fires on A; walking the (now unlocked) door reaches the CAVE
    with its two enemies; the enemies chase + contact-damage; and a facing+B swing
    kills one. vm.entity + vm.trigger + vm.combat + engine.hud, all at once.

    python projects/vm-rpg/verify.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
PROJ = os.path.dirname(os.path.abspath(__file__))

import mosaik_vm as mv

FAILS = []


def check(cond, msg):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def refvm():
    print("[RefVM: say-once key + flag-gated door]")
    prog = mv.compile_path(os.path.join(PROJ, "scripts"))
    hk = prog.variables["has_key"]
    vm = mv.RefVM(prog.code, entry=prog.offsets["elder_talk"]); vm.run(4)
    check(vm.heap[hk] == 1, "the elder grants the key (say-once)")
    vm = mv.RefVM(prog.code, entry=prog.offsets["gate"]); vm.heap[hk] = 0; vm.run(4)
    check(len(vm.change_log) == 0, "the door is LOCKED without the key")
    vm = mv.RefVM(prog.code, entry=prog.offsets["gate"]); vm.heap[hk] = 1; vm.run(4)
    check(len(vm.change_log) == 1 and vm.change_log[0][0] == 1,
          "with the key the door OPENS -> the cave (room 1)")


def pyboy():
    print("[PyBoy: the composed loop -- dialogue -> gated cave -> combat]")
    rom = os.path.join(PROJ, "build", "gameboy", "vm-rpg.gb")
    if not os.path.exists(rom):
        subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "gameboy", PROJ], check=True, cwd=ROOT)
    try:
        from pyboy import PyBoy
    except Exception:  # noqa: BLE001
        print("  skip: PyBoy not installed")
        return

    def vis(pb, s):
        y = pb.memory[0xFE00 + s * 4]
        return 16 <= y < 150

    def hearts(pb):
        return sum(1 for s in range(9, 15) if vis(pb, s))

    def enemies(pb):
        return sum(1 for s in (0, 1) if vis(pb, s))

    def cave_tiles(pb):
        return sum(1 for cy in range(9) for cx in range(12)
                   if pb.memory[0x9800 + cy * 32 + cx] == 2)

    def box_open(pb):
        # A GB-family dialogue box is on the WINDOW layer (0x9C00), not in the BG
        # map -- `text.to_window`, bottom-anchored. This check used to read a BG
        # map row and so reported 0 glyphs against a ROM that draws the box
        # correctly; the same staleness vm-uiquest's verify records fixing for
        # itself. Both halves are needed: LCDC bit 5 says the window is SHOWING
        # (closing a box switches it off and leaves the glyphs in VRAM), the
        # distinct-glyph count says a frame plus text rather than one stale row.
        if not pb.memory[0xFF40] & 0x20:          # LCDC bit 5: window off
            return False
        return len({pb.memory[0x9C00 + r * 32 + c]
                    for r in range(18) for c in range(20)
                    if pb.memory[0x9C00 + r * 32 + c] >= 100}) >= 4

    def hold(pb, b, f):
        pb.button_press(b)
        for _ in range(f):
            pb.tick()
        pb.button_release(b)
        for _ in range(4):
            pb.tick()

    pb = PyBoy(rom, window="null")
    # boot: font_preload front-loads the GBDK font init before the tileset upload,
    # so the field is not on screen until ~frame 98 (measured). 90 read every
    # sprite slot as empty and the check failed on a ROM that comes up correctly.
    for _ in range(140):
        pb.tick()
    check(sum(1 for s in range(16) if vis(pb, s)) >= 2 and hearts(pb) == 6
          and cave_tiles(pb) == 0,
          "the FIELD renders: player + elder + a 6-heart HUD (not the cave)")

    hold(pb, "left", 20)
    hold(pb, "up", 26)
    talk = False
    for _ in range(6):
        pb.button_press("a"); pb.tick(); pb.button_release("a")
        for _ in range(8):
            pb.tick()
        if box_open(pb):
            talk = True
            break
    check(talk, "the elder's On Interact dialogue fires (grants the key)")
    pb.button_press("a"); pb.tick(); pb.button_release("a")   # dismiss
    for _ in range(6):
        pb.tick()

    hold(pb, "up", 16)
    hold(pb, "right", 14)
    hold(pb, "up", 8)
    for _ in range(30):
        pb.tick()
    check(cave_tiles(pb) > 20 and enemies(pb) == 2,
          "the unlocked door reaches the CAVE (2 enemies present)")

    pb.button_press("a"); pb.tick(); pb.button_release("a")   # dismiss "FIGHT!"
    for _ in range(8):
        pb.tick()
    ea = enemies(pb)
    h0 = hearts(pb)
    for _ in range(80):
        pb.tick()
    check(hearts(pb) < h0, "the cave enemies chase + contact-damage the player")
    killed = False
    pb.button_press("up")             # face the enemies (they come from above)
    for _ in range(50):
        pb.button_press("b"); pb.tick(); pb.button_release("b"); pb.tick()
        if enemies(pb) < ea:
            killed = True
            break
    pb.button_release("up")
    check(killed, "swinging the sword (facing + B) kills a cave enemy")
    pb.stop()


def main():
    refvm()
    pyboy()
    print("\n" + "=" * 50)
    if FAILS:
        print("vm-rpg verify FAILED (%d)" % len(FAILS))
        return 1
    print("vm-rpg verify: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
