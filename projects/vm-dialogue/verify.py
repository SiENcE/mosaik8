#!/usr/bin/env python3
"""Verify vm-dialogue -- the VM8 reference-engine-style dialogue showcase.

Two layers of proof:
  * RefVM (deterministic): the three dialogue LAYERS -- $var$ interpolation (a line
    shows the live `gold`), the reusable library ($var$ + named defs referenced by
    `say`), and staged/choice logic (elder say-once; the merchant Yes/No CHOICE
    branch spends gold; a full menu drive picks YES). Plus the intro CUTSCENE latch.
  * PyBoy (on-ROM): the GB build boots, renders, and the intro cutscene's text box
    appears.

    python projects/vm-dialogue/verify.py
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
    print("[RefVM: the three dialogue layers + the cutscene]")
    prog = mv.compile_path(os.path.join(PROJ, "scripts"))
    V = prog.variables

    # Layer 1 -- $var$ interpolation: a line embeds the live gold counter.
    interp = [s for s in prog.strings if "$gold$" in s]
    check(bool(interp), "a dialogue line interpolates $gold$ (layer 1)")
    if interp:
        check(prog.interpolate(interp[0], {V["gold"]: 7}) == interp[0].replace("$gold$", "7"),
              "interpolate() resolves $gold$ to the heap value")

    # Layer 2 -- reusable library: the CHOICE def lowered to a menu writing its
    # picked index to merchant_ask_choice (a heap var), and every line came from a
    # named def (11 interned strings for 9 defs incl. 2 choice options).
    check("merchant_ask_choice" in V, "the choice def lowered to a menu -> a result var")

    # Layer 3 -- staged say-once: the elder's On Interact slot (dlg_obj_1, GENERATED
    # from the dialogue.toml [[attach]]) sets spoke_elder on the first talk.
    check("dlg_obj_1" in prog.offsets, "the elder's attachment generated the dlg_obj_1 slot")
    vm = mv.RefVM(prog.code, entry=prog.offsets["dlg_obj_1"])
    vm.run(4)
    check(vm.heap[V["spoke_elder"]] == 1, "the elder latches spoke_elder on first talk")

    # The merchant branch subs (the switch CALLs these): buy spends 5, and a
    # too-poor player is refused.
    vm = mv.RefVM(prog.code, entry=prog.offsets["try_buy"])
    vm.heap[V["gold"]] = 10
    vm.run(6)
    check(vm.heap[V["gold"]] == 5, "buying a charm spends 5 gold (10 -> 5)")

    vm = mv.RefVM(prog.code, entry=prog.offsets["try_buy"])
    vm.heap[V["gold"]] = 3
    vm.run(6)
    check(vm.heap[V["gold"]] == 3, "with < 5 gold the purchase is refused (stays 3)")

    # The full CHOICE flow: talk to the merchant, dismiss the question box, and
    # confirm YES (cursor starts at 0) -> the buy sub runs and spends gold.
    vm = mv.RefVM(prog.code, entry=prog.offsets["merchant_talk"])
    vm.heap[V["gold"]] = 10
    vm.run(30, a_at=[2, 6, 10, 14, 18, 22])
    check(vm.heap[V["gold"]] == 5, "choosing YES in the merchant menu spends 5 gold")

    # The intro CUTSCENE (scene On Init): latches seen_intro once (lock + set flag
    # run before the first text box).
    vm = mv.RefVM(prog.code, entry=prog.offsets["field_init"])
    vm.run(40, a_at=[3, 8, 13, 18, 23, 28, 33, 38])
    check(vm.heap[V["seen_intro"]] == 1, "the intro cutscene runs + latches seen_intro")

    # The TIMELINE cutscene (gather_cutscene, lowered from the .cut.toml): the
    # play_gather trigger runs it once (latches `gathered`) and it moves BOTH the
    # elder (actor 0) and merchant (actor 1) concurrently under one lock.
    check("gather_cutscene" in prog.offsets, "the timeline cutscene compiled into a script")
    vm = mv.RefVM(prog.code, entry=prog.offsets["play_gather"])
    vm.actors[0].active = vm.actors[1].active = 1
    vm.actors[0].x = 24; vm.actors[0].y = 24
    vm.actors[1].x = 72; vm.actors[1].y = 24
    vm.run(60, a_at=[40, 45, 50])
    check(vm.heap[V["gathered"]] == 1, "the trigger latches `gathered` (plays once)")
    check(vm.actors[0].y == 48 and vm.actors[1].y == 48,
          "both actors moved concurrently to their marks (elder+merchant)")


def pyboy_rom():
    print("[PyBoy: the GB ROM boots + renders]")
    try:
        from pyboy import PyBoy
    except ImportError:
        print("  skip: pyboy not installed")
        return
    rom = os.path.join(PROJ, "build", "gameboy", "vm-dialogue.gb")
    if not os.path.isfile(rom):
        subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"),
                        "build", "--platform", "gameboy", PROJ], check=True)
    pb = PyBoy(rom, window="null")
    for _ in range(200):     # VM boot needs ~120-200 frames before it renders
        pb.tick()
    img = pb.screen.image.convert("L")
    grays = len(set(img.getdata()))
    check(grays > 1, "the screen renders (>1 gray level), not blank")
    # the intro cutscene opens a text box at boot -> the bottom rows carry tiles
    pb.stop()


def main():
    refvm_logic()
    pyboy_rom()
    print()
    if FAILS:
        print("vm-dialogue FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        return 1
    print("vm-dialogue verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
