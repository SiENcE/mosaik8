#!/usr/bin/env python3
"""Count banked-call trampolines per game frame, split same-bank vs cross.

The 2026-08-28 census instrument, checked in: every call to a BANKED function
goes through sdcc's ___sdcc_bcall_ehl (register E = target bank), including a
call whose target bank is ALREADY mapped - ~164 cycles a call that a direct
`call` would spend 24 on. The codegen change (emit a module-private local
whose callers all share its bank without BANKED) exists to remove exactly the
same-bank share, and this probe is what says whether it did.

Same-bank = E equals the mapped bank at call time. GBDK keeps the mapped bank
in _current_bank (the .noi names it `__current_bank`; read it from there - do
not hardcode 0xFF90).

A ROM with NO banked code does not link ___sdcc_bcall_ehl at all (nothing
calls it, so the linker never pulls it in; measured on `projects/vm-snake`).
Then the census is ZERO trampolines by construction, and the probe says so
and exits 0 without running the ROM (it used to die on a KeyError). The .noi
is still checked for the VM frame symbol, so a wrong or empty file is not
mistaken for "no banked code".

Usage: trampoline_census.py --rom ROM.gb --noi SYM.noi [--room N]
       [--frames 400] [--hold right]

Without `--room` the ROM's own start scene is measured.
"""
import re
import sys

TRAMP_SYM = "___sdcc_bcall_ehl"
#: GBDK's `_current_bank` is `__current_bank` in the .noi (one C underscore
#: plus the linker's). The triple-underscore spelling this probe used to ask
#: for never matched, so the 0xFF90 fallback always won.
CURB_SYMS = ("__current_bank", "___current_bank")
FRAME_SYM = "_vm_core_run_scripts"


def load_noi(path):
    """{symbol: (bank << 16) | address} from a GBDK .noi."""
    s = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
        if m:
            s[m.group(1)] = int(m.group(2), 16)
    return s


def current_bank_addr(s):
    for name in CURB_SYMS:
        if name in s:
            return s[name] & 0xFFFF
    return 0xFF90


def census(rom, s, room=None, frames=400, hold=None):
    """Run the ROM and count trampoline entries. Returns a dict."""
    from pyboy import PyBoy
    tramp = s[TRAMP_SYM] & 0xFFFF
    curb = current_bank_addr(s)

    pb = PyBoy(rom, window="null")
    pb.set_emulation_speed(0)
    g = lambda n: s[n] & 0xFFFF

    rec = [False]
    nframes = [0]
    calls = [0, 0]      # total, same-bank

    def on_tramp(_c):
        if rec[0]:
            calls[0] += 1
            if pb.register_file.E == pb.memory[curb]:
                calls[1] += 1

    def on_frame(_c):
        if rec[0]:
            nframes[0] += 1

    pb.hook_register(0, tramp, on_tramp, None)
    v = s[FRAME_SYM]
    pb.hook_register(v >> 16, v & 0xFFFF, on_frame, None)

    def w16(a, val):
        pb.memory[a], pb.memory[a + 1] = val & 0xFF, (val >> 8) & 0xFF

    for _ in range(120):
        pb.tick()
    if room is not None:
        pb.memory[g("_vm_core_pend_code")] = 2
        pb.memory[g("_vm_core_pend_a")] = room
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
        pb.tick()
    rec[0] = False
    pb.stop(save=False)
    return {"frames": nframes[0], "calls": calls[0], "same": calls[1]}


def main(argv):
    def opt(name, conv=str, default=None):
        return conv(argv[argv.index(name) + 1]) if name in argv else default

    rom, noi = opt("--rom"), opt("--noi")
    if not (rom and noi):
        print(__doc__)
        print("!! --rom ROM.gb and --noi SYM.noi are required (a -Wl-j pair: "
              "tools/framebudget/build_noi.py <project>)")
        return 2
    room = opt("--room", int)
    frames = opt("--frames", int, 400)
    hold = opt("--hold")

    s = load_noi(noi)
    if FRAME_SYM not in s:
        print("!! %s names no %s - not a VM8 ROM's .noi (or not this ROM's)"
              % (noi, FRAME_SYM))
        return 2
    where = "room %s  %s" % ("start" if room is None else room, hold or "idle")
    if TRAMP_SYM not in s:
        print("%s  no banked code: %s is not linked" % (where, TRAMP_SYM))
        print("  banked calls/frame: 0.00  same-bank: 0.00 (0%) "
              "- zero trampolines by construction, ROM not run")
        return 0

    r = census(rom, s, room, frames, hold)
    n = max(r["frames"], 1)
    print("%s  %d game frames in %d LCD" % (where, n, frames))
    print("  banked calls/frame: %.2f  same-bank: %.2f (%.0f%%)"
          % (r["calls"] / n, r["same"] / n,
             100.0 * r["same"] / max(r["calls"], 1)))
    print("  est. trampoline cyc/frame: %.0f (same-bank share %.0f)"
          % (r["calls"] / n * 144.0, r["same"] / n * 144.0))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
