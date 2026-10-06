#!/usr/bin/env python3
"""Assert the BATCH SPRITE VERBS on real ROMs.

The lab (src/main.mos) drifts, plots and scans a small pool once and leaves
the answers in RAM behind a magic word. This checks, per console:

  * `sprite.drift` added the velocities (and a count of 0 did nothing);
  * `sprite.hit` found the right entry for four boxes - inside, nowhere,
    past the scanned count, wrapping past x = 255 - and 255 for an empty scan;
  * `sprite.plot` put every sprite where it was told to: read off the sprite
    table on the Game Boy family, and off the rendered screen (a solid 8x8
    block at each position) on SMS / Game Gear, for one, two and three
    sprites per entry.

Game Boy / Game Boy Color run on PyBoy; SMS / Game Gear on Genesis Plus GX
and the PC Engine (the portable C version) on Beetle PCE Fast, looked up in
emu/libretro/ and skipped when missing.
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(ROOT, "..", ".."))
sys.path.insert(0, ROOT)
MAGIC = bytes([0xBA, 0x7C, 0x48, 0x21])
ROMS = [("gameboy", "gb", None), ("gameboy_color", "gbc", None),
        ("gamegear", "gg", "genesis_plus_gx"), ("sms", "sms", "genesis_plus_gx"),
        ("pce", "pce", "mednafen_pce_fast")]
# what main.mos leaves behind: xs after the drift, then where things were plotted
XS = [11, 22, 29, 40, 50, 60, 70, 80]
YS = [10, 20, 30, 40, 50, 60, 70, 224]
WANT = {4: 11, 5: 22, 6: 29, 7: 11, 8: 2, 9: 255, 10: 3, 11: 255, 12: 0, 13: 255, 14: 0x5A}
# (x, y) of every sprite that should be visible, in screen pixels
SPOTS = [(XS[i], YS[i]) for i in range(7)]                      # entry 7 is parked
SPOTS += [(100, 90), (108, 90), (120, 110), (128, 110)]         # two pairs
SPOTS += [(16, 120), (24, 120), (32, 120)]                      # one triple
_FAILED = []


def check(cond, what):
    print(("  [PASS] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def run(rom, core_name):
    """-> (ram bytes, screen image, sprite table or None), or None if no emulator."""
    if core_name is None:
        from pyboy import PyBoy
        pb = PyBoy(rom, window="null", sound_emulated=False)
        pb.tick(119, False)       # (PyBoy runs the boot logo first)
        pb.tick(1, True)
        oam = [tuple(pb.memory[0xFE00 + i * 4:0xFE00 + i * 4 + 2]) for i in range(16)]
        return bytes(pb.memory[0xC000:0xE000]), pb.screen.image.convert("L"), oam
    cores = glob.glob(os.path.join(REPO, "emu", "libretro", core_name + "_libretro.*"))
    if not cores:
        return None
    from retro import Core
    core = Core(cores[0], rom)
    core.run(60)
    return core.ram(), core.image().convert("L"), None


def main():
    for platform, ext, core_name in ROMS:
        rom = os.path.join(ROOT, "build", platform, "batch-lab." + ext)
        print("\n[%s]" % platform)
        if not os.path.exists(rom):
            print("  [skip] not built")
            continue
        got = run(rom, core_name)
        if got is None:
            print("  [skip] no %s core in emu/libretro/" % core_name)
            continue
        ram, img, oam = got
        i = ram.find(MAGIC)
        check(i >= 0, "the program ran (its result block is in RAM)")
        if i < 0:
            continue
        res = ram[i:i + 16]
        check([res[k] for k in (4, 5, 6)] == [11, 22, 29], "drift added each velocity (255 is -1)")
        check(res[7] == 11, "a drift of zero entries changed nothing")
        check(res[8] == 2 and res[10] == 3, "hit returns the first entry inside the box")
        check(res[9] == 255, "hit returns 255 when nothing is inside")
        check(res[11] == 255, "hit stops at the count it was given")
        check(res[12] == 0, "a box that wraps past x = 255 still works")
        check(res[13] == 255, "a scan of zero entries returns 255")
        check(res[14] == 0x5A, "... and the program got to the end")
        if oam is not None:
            want = [(y + 16, x + 8) for x, y in zip(XS, YS)]
            check(oam[:8] == want, "plot, one sprite per entry: the sprite table matches")
            check(oam[8:12] == [(106, 108), (106, 116), (126, 128), (126, 136)],
                  "plot, two sprites per entry: side by side, 8 px apart")
            check(oam[12:15] == [(136, 24), (136, 32), (136, 40)],
                  "plot, three sprites per entry")
        if platform != "pce":
            # a solid block at every spot, and none where nothing was plotted
            px = img.load()
            bg = px[150, 70]
            seen = [px[x + 3, y + 3] != bg for x, y in SPOTS]
            check(all(seen), "every plotted sprite is on screen where it was told to be"
                  + ("" if all(seen) else " (missing: %s)" % [s for s, ok in zip(SPOTS, seen) if not ok]))
            check(px[80 + 3, 80 + 3] == bg, "the parked entry is not")
    if _FAILED:
        print("\n%d check(s) FAILED" % len(_FAILED))
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
