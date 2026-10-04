#!/usr/bin/env python3
"""Whole-bank-0 FUNCTION-ENTRY profile: who is spending the frame (PyBoy).

Between `framebudget.py` (a handful of stage spans) and `addr_profile.py` (every
instruction of a few named functions) there was nothing that answers "what is
this frame actually running" without being told where to look. This hooks the
ENTRY of every bank-0 ROM symbol in the .noi and charges the cycles between two
hooked entries to whichever function was entered last, so a callee's time lands
on the callee. One hook per CALL, not per instruction, so it profiles hundreds
of functions in seconds where addr_profile takes minutes for one.

It is what found the VWF pen's cost (2026-09-07): the stage table
said 60,520 T-cycles a game frame inside `vm_core_run_scripts` and nothing more,
and this named `set_bkg_1bpp_data` at 13,909 of them.

TWO TRAPS, both real:

  * **A charge extends until the next HOOKED entry**, so a function that returns
    into BANKED code (unhooked) absorbs everything that follows. Trust a number
    when the callee's own callees are hooked too (the GBDK library and the
    prelude are bank 0, so the text/VRAM path is well covered); treat
    `wait_vbl_done` and the ISRs as buckets, not measurements.
  * **PyBoy hooks by patching the opcode at the address**, so hooking a bank-0
    DATA symbol CORRUPTS that data. Most .noi bank-0 symbols are functions and
    this is fine, but if the ROM stops behaving (the game-frame count collapses
    to 1), that is what happened - narrow the set with --only, or name the
    data symbols with --skip (2026-09-07: the VWF pen's two const mask tables
    are bank-0 ROM data, and hooking them turned every box into no box).

Usage:
  entry_profile.py ROM.gb SYM.noi [--room 0] [--frames 600] [--fire] [--hold right]
      [--anchor _vm_core_run_scripts] [--top 20] [--only substr] [--skip a,b]
"""
import re
import sys
from collections import defaultdict


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    rom, noi = sys.argv[1], sys.argv[2]
    room = arg("--room")
    frames = int(arg("--frames", 600))
    top = int(arg("--top", 20))
    only = arg("--only")
    skip = [x for x in (arg("--skip", "") or "").split(",") if x]
    anchor = arg("--anchor", "_vm_core_run_scripts")
    fire = "--fire" in sys.argv
    hold = arg("--hold")

    syms = {}
    for line in open(noi, encoding="utf-8", errors="replace"):
        m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
        if m:
            syms[m.group(1)] = int(m.group(2), 16)
    # `s__AREA` / `l__AREA` are the linker's section START and LENGTH symbols,
    # never code: hooking `s__LIT` patches the first byte of the literal area
    # (2026-09-07: the VWF pen's mask table, and every box vanished), and
    # `l__CODE_15` is a length that happens to look like an address.
    funcs = {n: v for n, v in syms.items()
             if (v >> 16) == 0 and 0x150 <= (v & 0xFFFF) < 0x4000
             and not n.startswith(("s__", "l__"))
             and (only is None or only in n)
             and not any(x in n for x in skip)}
    if anchor not in syms:
        print("!! no anchor %s in %s" % (anchor, noi))
        return 1
    print("hooking %d bank-0 entries" % len(funcs))

    from pyboy import PyBoy
    pb = PyBoy(rom, window="null")
    pb.set_emulation_speed(0)
    rec, cur, gf = [False], [None, 0], [0]
    incl, calls = defaultdict(int), defaultdict(int)

    def mk(n):
        def hit(_c):
            if rec[0]:
                c = pb._cycles()
                if cur[0] is not None:
                    incl[cur[0]] += c - cur[1]
                cur[0], cur[1] = n, c
                calls[n] += 1
        return hit

    for n, v in funcs.items():
        try:
            pb.hook_register(v >> 16, v & 0xFFFF, mk(n), None)
        except ValueError:      # already hooked (two symbols, one address)
            pass

    def anchor_hit(_c):
        if rec[0]:
            gf[0] += 1

    if anchor not in funcs:     # the anchor may already carry a mk() hook
        v = syms[anchor]
        pb.hook_register(v >> 16, v & 0xFFFF, anchor_hit, None)

    g = lambda n: syms[n] & 0xFFFF

    def w16(a, v):
        pb.memory[a], pb.memory[a + 1] = v & 0xFF, (v >> 8) & 0xFF

    for _ in range(120):
        pb.tick()
    if room is not None:
        pb.memory[g("_vm_core_pend_code")] = 2
        pb.memory[g("_vm_core_pend_a")] = int(room)
        w16(g("_vm_core_pend_b"), 40)
        w16(g("_vm_core_pend_c"), 72)
        for _ in range(300):
            pb.tick()
    if hold:
        pb.button_press(hold)
        for _ in range(30):
            pb.tick()

    rec[0] = True
    for _ in range(frames):
        if fire:
            k = gf[0] or calls[anchor]
            if k % 10 == 0:
                pb.button_press("a")
            elif k % 10 == 5:
                pb.button_release("a")
        pb.tick()
    rec[0] = False
    for b in ("a", hold):
        if b:
            pb.button_release(b)

    n = max(gf[0], calls[anchor], 1)
    print("\nroom %s %s: %d game frames in %d LCD (%.2f LCD per game frame)"
          % (room, "fire" if fire else hold or "idle", n, frames, frames / float(n)))
    print("   %9s %8s  function" % ("cyc/frame", "calls"))
    for name, c in sorted(incl.items(), key=lambda t: -t[1])[:top]:
        print("   %9.0f %8.2f  %s" % (c / n, calls[name] / n, name))
    pb.stop(save=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
