#!/usr/bin/env python3
"""Verify vm-shop -- the VM8 SHOP proof.

Two layers:
  * RefVM (deterministic): drive the LOOPING shop menu (open -> nav -> confirm ->
    dismiss) and assert the ECONOMY -- buying a potion spends 5 gold + stocks one,
    buying an ether spends 8, being broke blocks the buy, selling a potion returns
    half price, and Leave unlocks + ends the thread. This is the "a shop is
    expressible as event scripts over the menu op + heap vars" claim, no new engine.
  * PyBoy (on-ROM): the GB build boots, renders the player + keeper, and the keeper's
    On Interact opens the menu box (its option glyphs render) on an A-press near it.

    python projects/vm-shop/verify.py
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


# ---- RefVM menu-driving helpers (engine.menu edge semantics) ----
def _nav(vm, n):
    for _ in range(n):
        vm.frame(down=True)
        vm.frame(down=False)


def _confirm(vm):
    vm.frame()                 # A released (reset the arm/confirm edge)
    vm.frame(a_pressed=True)   # A rising edge -> the menu confirms
    vm.frame()                 # let the picked case (+ its call) run


def _dismiss(vm):
    vm.frame()                 # A released
    vm.frame(a_pressed=True)   # A edge dismisses the open text box
    vm.frame()                 # the loop re-opens the menu


def _open(prog, gold=20, potions=0):
    vm = mv.RefVM(prog.code, entry=prog.offsets["shop"])
    vm.heap[prog.variables["gold"]] = gold
    vm.heap[prog.variables["potions"]] = potions
    vm.frame()                 # spawn: guard-lock + open the menu
    return vm


def refvm_economy():
    print("[RefVM: the looping shop economy over `menu` + heap vars]")
    prog = mv.compile_path(os.path.join(PROJ, "scripts"))
    gold = prog.variables["gold"]
    pot = prog.variables["potions"]
    eth = prog.variables["ethers"]
    lock = prog.variables["shop_lock"]

    # Opening the shop locks the player ONCE (the guard var).
    vm = _open(prog, gold=20)
    check(vm.menu_open == 1 and vm.lockcount == 1 and vm.heap[lock] == 1,
          "talking to the keeper opens the menu + locks the player once")

    # BUY POTION (index 0): spends 5 gold, stocks one potion.
    _confirm(vm)
    check(vm.heap[gold] == 15 and vm.heap[pot] == 1,
          "BUY POTION spends 5 gold -> 15 and stocks 1 potion")
    _dismiss(vm)               # dismiss "BOUGHT A POTION!" -> menu re-opens
    check(vm.menu_open == 1 and vm.lockcount == 1,
          "the menu LOOPS: it re-opens after a purchase (lock still held)")

    # BUY ETHER (index 1): spends 8 gold.
    _nav(vm, 1)
    _confirm(vm)
    check(vm.heap[gold] == 7 and vm.heap[eth] == 1,
          "BUY ETHER spends 8 gold -> 7 and stocks 1 ether")
    _dismiss(vm)

    # LEAVE (index 3): "COME AGAIN!" then unlock + reset + stop.
    _nav(vm, 3)
    _confirm(vm)
    _dismiss(vm)               # dismiss "COME AGAIN!" -> the default's unlock+stop run
    check(vm.any_active_threads() == 0 and vm.lockcount == 0 and vm.heap[lock] == 0,
          "LEAVE unlocks the player + ends the shop thread")

    # BROKE: 3 gold can't buy a 5-gold potion (no spend, no stock).
    vm = _open(prog, gold=3)
    _confirm(vm)
    check(vm.heap[gold] == 3 and vm.heap[pot] == 0,
          "being broke BLOCKS the buy (gold + stock unchanged)")

    # SELL a potion: -1 potion, +2 gold (half of the 5-gold price, floored to 2).
    vm = _open(prog, gold=0, potions=2)
    _nav(vm, 2)
    _confirm(vm)
    check(vm.heap[pot] == 1 and vm.heap[gold] == 2,
          "SELL POTION returns half price (2 gold) and consumes one potion")


def pyboy_rom():
    print("[PyBoy: the GB ROM boots, renders, and the keeper opens the shop menu]")
    rom = os.path.join(PROJ, "build", "gameboy", "vm-shop.gb")
    if not os.path.exists(rom):
        subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "gameboy", PROJ], check=True, cwd=ROOT)
    try:
        from pyboy import PyBoy
    except Exception:  # noqa: BLE001
        print("  skip: PyBoy not installed")
        return
    def box_open(pb):
        # The shop MENU is a GB-family UI box, so it draws on the WINDOW layer
        # (0x9C00), not in the BG map -- `text.to_window`, bottom-anchored. This
        # check used to read BG row 4 (the menu's authored `row`) and so reported
        # 0 glyphs against a ROM that opens the menu correctly; the same
        # staleness vm-uiquest's verify records fixing for itself. Both halves
        # are needed: LCDC bit 5 says the window is SHOWING (closing a box
        # switches it off and leaves the glyphs in VRAM), the distinct-glyph
        # count says a frame plus option text rather than one stale row.
        if not pb.memory[0xFF40] & 0x20:          # LCDC bit 5: window off
            return False
        return len({pb.memory[0x9C00 + r * 32 + c]
                    for r in range(18) for c in range(20)
                    if pb.memory[0x9C00 + r * 32 + c] >= 100}) >= 4

    pb = PyBoy(rom, window="null")
    # boot: font_preload front-loads the GBDK font init before the tileset upload,
    # so the field is not on screen until ~frame 98 (measured). 90 read every
    # sprite slot as empty and the check failed on a ROM that comes up correctly.
    for _ in range(140):
        pb.tick()
    sprites = sum(1 for i in range(40) if pb.memory[0xFE00 + 4 * i] != 0)
    check(sprites >= 2, "boots + renders the player + the keeper (%d sprites)" % sprites)

    # Walk up to the keeper (player 48,60 -> keeper 48,24) and press A near it.
    def hold(btn, f):
        pb.button_press(btn)
        for _ in range(f):
            pb.tick()
        pb.button_release(btn)
        for _ in range(4):
            pb.tick()

    hold("up", 18)             # from the start (48,48) up to just below the keeper (48,24)
    opened = False
    for _ in range(6):
        pb.button_press("a")
        pb.tick()
        pb.button_release("a")
        for _ in range(8):
            pb.tick()
        if box_open(pb):
            opened = True
            break
    check(opened, "the keeper's On Interact opens the shop menu (option glyphs render)")
    pb.stop()


def main():
    refvm_economy()
    pyboy_rom()
    print("\n" + "=" * 50)
    if FAILS:
        print("vm-shop verify FAILED (%d)" % len(FAILS))
        return 1
    print("vm-shop verify: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
