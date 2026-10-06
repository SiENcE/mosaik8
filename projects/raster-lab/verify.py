#!/usr/bin/env python3
"""Assert the PER-SCANLINE SCROLL TABLE on real ROMs.

The lab fills the screen with vertical stripes (a 4 px bar every 8 px) and
arms `bkg.raster(1, FIRST)`: the lines above FIRST all use line 0's entry
(scroll PHASE0), and line FIRST + k is scrolled by `k / 2 + phase`. This reads
the bar's left edge off EVERY scanline of a rendered frame and compares it
with what that table says - so it proves the three things the feature claims:

  * every line really has its own scroll (the stripes lean, two lines a step);
  * the table starts on exactly the line it was armed for, not one early or
    late;
  * the lines above it are untouched;
  * the native fill `raster_curve` draws the same ramp as a table written
    line by line (the lab alternates the two, frame by frame);
  * `raster_stripes` picks a second picture, 104 lines further down the map,
    for every other 16-line band - on the Game Boy family, where a line can
    have its own VERTICAL scroll. There the bar sits 4 px to the right. On
    SMS / Game Gear the vertical scroll is latched per frame and nothing moves.

`phase` advances every frame and the frame on screen is one or two behind the
one being written, so the check accepts any single phase - but it has to be
the SAME one for every line (a torn table fails).

Game Boy / Game Boy Color run on PyBoy; Master System / Game Gear on the
Genesis Plus GX libretro core (emu/libretro/genesis_plus_gx_libretro.*),
skipped when the core is not installed.
"""
import glob
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(ROOT, "..", ".."))
sys.path.insert(0, os.path.join(REPO, "emu", "libretro"))   # retro.py, the shared frontend
FIRST, PHASE0, FRAMES = 40, 3, 100
ROMS = [("gameboy", "gb"), ("gameboy_color", "gbc"), ("gamegear", "gg"), ("sms", "sms")]
_FAILED = []


def check(cond, what):
    print(("  [PASS] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def frame(rom, frames=FRAMES):
    """A rendered frame as a PIL image, or None when no emulator is available."""
    if rom.endswith((".gb", ".gbc")):
        from pyboy import PyBoy
        pb = PyBoy(rom, window="null", sound_emulated=False)
        pb.tick(frames - 1, False)
        pb.tick(1, True)
        return pb.screen.image.convert("L")
    cores = glob.glob(os.path.join(REPO, "emu", "libretro", "genesis_plus_gx_libretro.*"))
    if not cores:
        return None
    from retro import Core
    core = Core(cores[0], rom)
    core.run(frames)
    return core.image().convert("L")


def edges(img, x0):
    """Per scanline: the bar's left edge, 0..7 (x of the first light->dark step)."""
    px = img.load()
    out = []
    for y in range(img.height):
        x = x0
        while x < img.width - 1 and not (px[x, y] > 128 and px[x + 1, y] <= 128):
            x += 1
        out.append((x + 1 - x0) & 7)
    return out


def main():
    for platform, ext in ROMS:
        rom = os.path.join(ROOT, "build", platform, "raster-lab." + ext)
        print("\n[%s]" % platform)
        if not os.path.exists(rom):
            print("  [skip] not built")
            continue
        img = frame(rom)
        if img is None:
            print("  [skip] no libretro core in emu/libretro/")
            continue
        # The Master System blanks its leftmost column; measure past it.
        gb = platform.startswith("gameboy")
        n = img.height - FIRST

        def band(k):
            """4 where raster_stripes sends line FIRST + k to the shifted copy."""
            up = n - 1 - k                  # lines above the bottom one
            return 4 if gb and ((up << 3) & 128) else 0

        def matches(e, phase):
            return all(e[FIRST + k] == (8 - (k // 2 + phase) + band(k)) & 7
                       for k in range(n))

        seen = {}
        x0 = 8 if platform == "sms" else 0      # the SMS blanks its left column
        want0 = (8 - PHASE0) & 7
        top_ok = ramp_ok = True
        for shot in range(8):               # consecutive frames: both fills show up
            if shot:
                img = frame(rom, FRAMES + shot)
            e = edges(img, x0)
            top_ok = top_ok and all(v == want0 for v in e[:FIRST])
            phase = next((ph for ph in range(256) if matches(e, ph)), None)
            if phase is None:
                ramp_ok = False
                print("    frame %d: no phase fits; first lines %s" % (shot, e[FIRST:FIRST + 12]))
            else:
                seen[phase & 1] = True
        check(top_ok, "lines 0..%d all use line 0's entry" % (FIRST - 1))
        check(ramp_ok, "every line from %d down has its own scroll%s"
              % (FIRST, " and its own picture" if gb else ""))
        check(len(seen) == 2,
              "raster_copy and raster_curve were both on screen and drew the same ramp")
    if _FAILED:
        print("\n%d check(s) FAILED" % len(_FAILED))
        sys.exit(1)
    print("\nall checks passed")


if __name__ == "__main__":
    main()
