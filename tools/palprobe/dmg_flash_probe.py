#!/usr/bin/env python3
"""Does a converted DMG sprite FLASH? (the OBP0/OBP1 half of per-frame palettes)

A Game Boy has exactly two sprite palettes, so the only way to make a sprite
flash is to draw it through OBP0 on one frame and OBP1 on the next - the reference engine
authors that as a per-metasprite-tile `objPalette`, and the
platformer conversion's "Press 'A'" prompt (in its intro
cutscene) is the worked example, with the trick
written in the sprite's own comment.

The claim is in OAM's attribute byte (bit 4 = OBP1), so that is what this reads,
once per frame, while the ROM plays itself with canned Start/A taps. Three ROMs
say the whole story: the REFERENCE alternates a run of OBP1 objects with none,
the conversion should do the same, and a build without the fix does neither
(worse, it puts sprites on OBP1 at the title screen, where the reference has
none - that is the old `slot & 1` heuristic showing).

Force DMG mode (`cgb=False`): that game's reference ROM carries the CGB flag, and in
colour mode the OBP select is not used at all.

Usage: dmg_flash_probe.py ROM.gb [label]
"""
import sys


def main():
    from pyboy import PyBoy
    rom = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else rom
    pb = PyBoy(rom, window="null", cgb=False, sound_emulated=False)

    def tap(btn, n=6):
        pb.button_press(btn)
        for _ in range(n):
            pb.tick(1, False)
        pb.button_release(btn)
        for _ in range(8):
            pb.tick(1, False)

    print("%s:" % label)
    prev = None
    for f in range(2400):
        pb.tick(1, False)
        if f in (240, 600, 900, 1200, 1500, 1800, 2100):
            tap("start" if f % 600 == 0 else "a")
        if f % 120:
            continue
        oam = pb.memory[0xFE00:0xFEA0]
        live = [i for i in range(40) if 0 < oam[i * 4] < 160]
        obp1 = sum(1 for i in live if oam[i * 4 + 3] & 0x10)
        # the BG fingerprint is only there to tell scenes apart - the frame
        # numbers do not line up between two ROMs that pace differently
        fp = (hash(bytes(pb.memory[0x9800:0x9840])) & 0xFFFF, len(live), obp1)
        if fp != prev:
            print("  f%-5d scene=%04x  sprites=%2d  on OBP1=%d"
                  % (f, fp[0], len(live), obp1))
            prev = fp
    pb.stop(save=False)


if __name__ == "__main__":
    main()
