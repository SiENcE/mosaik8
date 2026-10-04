#!/usr/bin/env python3
"""Which way does a fade go - to WHITE or to BLACK? (2026-09-15)

The reference VM's `fade_manager.c` ramps the DMG palette REGISTERS per step: `fade_style`
0 (the engine.json default) subtracts a shade per field (`DMGFadeToWhiteStep`,
so the register walks 0xE4 -> 0x00, the screen goes WHITE), 1 adds one
(`DMGFadeToBlackStep`, 0xE4 -> 0xFF). vm.fx only had the black ramp. This
probe reads BGP (0xFF47) and OBP0/OBP1 every LCD frame in DMG mode and prints
the distinct values in order, so the direction of every fade in the window is
visible without a symbol file - the reference ROM has none.

Usage: fade_style_probe.py ROM.gb [--frames N] [--start F] [--taps a,b,...]

`--start F` presses Start at frame F (a title screen). `--taps` is a list of
`frame:button` pairs pressed for 8 frames each, e.g. `700:right,900:up`.
"""
import sys


def main():
    rom = sys.argv[1]
    arg = lambda n, d=None: (sys.argv[sys.argv.index(n) + 1]
                             if n in sys.argv else d)
    n = int(arg("--frames", 900))
    start = arg("--start")
    taps = arg("--taps", "")
    presses = {}
    if start:
        presses[int(start)] = "start"
    for t in filter(None, taps.split(",")):
        f, b = t.split(":")
        presses[int(f)] = b

    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", cgb=False)

    trail = []                  # (frame, BGP, OBP0, OBP1) on every change
    last = None
    held = None
    for f in range(n):
        if f in presses:
            pb.button_press(presses[f])
            held = (presses[f], f + 8)
        if held and f == held[1]:
            pb.button_release(held[0])
            held = None
        pb.tick()
        regs = (pb.memory[0xFF47], pb.memory[0xFF48], pb.memory[0xFF49])
        if regs != last:
            trail.append((f, regs))
            last = regs
    pb.stop(save=False)

    print("%s: %d register changes in %d frames" % (rom, len(trail), n))
    for f, (b, o0, o1) in trail:
        print("  frame %4d  BGP %02X  OBP0 %02X  OBP1 %02X" % (f, b, o0, o1))


if __name__ == "__main__":
    main()
