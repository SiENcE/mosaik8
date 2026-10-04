#!/usr/bin/env python3
"""Verify vm-quest -- the VM8 migration proof.

Two layers:
  * RefVM (deterministic): the say-once elder dialogue sets the key flag ONCE, and
    the gate script GATES change_scene on that flag -- the composed toll-door,
    reproduced on the VM. This is the parity claim, checked without input timing.
  * PyBoy (on-ROM): the GB build boots, renders the player + elder, and the elder's
    On Interact dialogue fires on an A-press near it.

    python projects/vm-quest/verify.py
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


def refvm_logic():
    print("[RefVM: say-once key + flag-gated transition (VM8 bit-flags)]")
    prog = mv.compile_path(os.path.join(PROJ, "scripts"))
    fl = prog.variables["flags"]

    # elder_talk, first time (bit 0 clear): grants the key (flags |= 1) --
    # and only touches bit 0 (an unrelated bit rides along untouched).
    vm = mv.RefVM(prog.code, entry=prog.offsets["elder_talk"])
    vm.heap[fl] = 4                    # an unrelated flag bit is already set
    vm.run(4)
    check(vm.heap[fl] == 5, "talking to the elder ORs in the key bit (4 -> 5)")

    # gate with the key LOCKED (bit 0 clear): NO scene change.
    vm = mv.RefVM(prog.code, entry=prog.offsets["gate"])
    vm.heap[fl] = 2                    # other bits set, key bit clear
    vm.run(4)
    check(len(vm.change_log) == 0, "gate is LOCKED without the key bit (no change_scene)")

    # gate with the key (bit 0 set): change to the vault (room 1).
    vm = mv.RefVM(prog.code, entry=prog.offsets["gate"])
    vm.heap[fl] = 1
    vm.run(4)
    check(len(vm.change_log) == 1 and vm.change_log[0][0] == 1,
          "gate OPENS with the key bit -> change_scene to the vault (room 1)")

    # vault_init marks the vault visited (flags |= 2), keeping the key bit.
    vm = mv.RefVM(prog.code, entry=prog.offsets["vault_init"])
    vm.heap[fl] = 1
    vm.run(4)
    check(vm.heap[fl] == 3, "entering the vault ORs in the visited bit (1 -> 3)")


def pyboy_rom():
    print("[PyBoy: the GB ROM boots, renders, and the elder talks]")
    rom = os.path.join(PROJ, "build", "gameboy", "vm-quest.gb")
    if not os.path.exists(rom):
        subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "gameboy", PROJ], check=True, cwd=ROOT)
    try:
        from pyboy import PyBoy
    except Exception:  # noqa: BLE001
        print("  skip: PyBoy not installed")
        return
    pb = PyBoy(rom, window="null")
    for _ in range(140):   # boot: font_preload front-loads the font init (~30 extra frames)
        pb.tick()
    sprites = sum(1 for i in range(40) if pb.memory[0xFE00 + 4 * i] != 0)
    check(sprites >= 2, "boots + renders the player + the elder (%d sprites)" % sprites)

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

    # Walk up to the elder (start 48,56 -> elder 24,24) and press A a few times.
    def hold(btn, f):
        pb.button_press(btn)
        for _ in range(f):
            pb.tick()
        pb.button_release(btn)
        for _ in range(4):
            pb.tick()

    hold("left", 20)
    hold("up", 26)
    talked = False
    for _ in range(6):
        pb.button_press("a")
        pb.tick()
        pb.button_release("a")
        for _ in range(8):
            pb.tick()
        if box_open(pb):
            talked = True
            break
    check(talked, "the elder's On Interact dialogue box renders on A-near")
    pb.stop()


def main():
    refvm_logic()
    pyboy_rom()
    print("\n" + "=" * 50)
    if FAILS:
        print("vm-quest verify FAILED (%d)" % len(FAILS))
        return 1
    print("vm-quest verify: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
