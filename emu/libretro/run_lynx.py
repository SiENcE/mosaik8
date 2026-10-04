"""Headless console-ROM checks via libretro.py (Lynx by default).

Usage:
    python emu/libretro/run_lynx.py <rom.lnx> [frames] [--png out.png] [--press BTN@frame[-frame]]...
    python emu/libretro/run_lynx.py <rom.pce> [frames] --core mednafen_pce_fast ...
    python emu/libretro/run_lynx.py <rom.sms> [frames] --core genesis_plus_gx ...
    python emu/libretro/run_lynx.py <rom.gg>  [frames] --core genesis_plus_gx ...
    python emu/libretro/run_lynx.py <rom.nes> [frames] --core fceumm ...

Runs the ROM for N frames (default 300) on a libretro core next to this
script (default: the Lynx cores; `--core <stem>` selects another, e.g.
`mednafen_pce_fast` for PC Engine, `genesis_plus_gx` for SMS / Game Gear,
`fceumm` for NES -- all installed by setup_tools.py), then reports the final
frame's distinct colors and can save it as a PNG. Exit code 1 if the final
frame is blank (single color).
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Beetle Lynx needs the real lynxboot.img in the system dir (it silently shows
# a blank screen without it); Handy boots homebrew BIOS-less via HLE, so it is
# the fallback when the boot ROM is absent.
SYSTEM_DIR = HERE
if os.path.exists(os.path.join(SYSTEM_DIR, "lynxboot.img")):
    CORE = os.path.join(HERE, "mednafen_lynx_libretro.dll")
else:
    CORE = os.path.join(HERE, "handy_libretro.dll")

from libretro import SessionBuilder  # noqa: E402
from libretro.drivers.path import ExplicitPathDriver  # noqa: E402
from libretro.api.input import DeviceIdJoypad  # noqa: E402

# libretro.py 0.x bug: a NULL c_void_p has .value None (not 0), so the
# frame-dupe branch of video_refresh never matches and a duped frame (core
# re-sends the previous picture) raises TypeError. Route None to DUPE.
import libretro.drivers.environment.composite as _comp  # noqa: E402
from libretro.drivers.video import FrameBufferSpecial  # noqa: E402

_orig_video_refresh = _comp.CompositeEnvironmentDriver.video_refresh


def _video_refresh(self, data, width, height, pitch):
    if getattr(data, "value", 1) is None:
        self._video.refresh(FrameBufferSpecial.DUPE, width, height, pitch)
        return
    _orig_video_refresh(self, data, width, height, pitch)


_comp.CompositeEnvironmentDriver.video_refresh = _video_refresh

BUTTONS = {name: getattr(DeviceIdJoypad, name)
           for name in ("A", "B", "UP", "DOWN", "LEFT", "RIGHT", "START", "SELECT")}


def parse_presses(specs):
    """'A@100-150' -> hold A on frames [100, 150)."""
    presses = []
    for spec in specs or []:
        btn, _, rng = spec.partition("@")
        start, _, end = rng.partition("-")
        start = int(start)
        end = int(end) if end else start + 1
        presses.append((BUTTONS[btn.upper()], start, end))
    return presses


def frame_image(session):
    """Final video frame as a PIL RGB image (None if Pillow is missing)."""
    try:
        from PIL import Image
    except ImportError:
        return None
    shot = session.video.screenshot()
    if shot is None:
        return None
    # The video driver hands back 4 bytes/pixel regardless of the core's
    # native format (shot.pixel_format reports the core-side format). The
    # memory order is R,G,B,X -- decoding as BGRX swaps red and blue
    # (verified against a TGI COLOR_RED full-screen bar, which read back
    # blue-dominant; greys, which all earlier checks used, hid the swap).
    return Image.frombuffer("RGB", (shot.width, shot.height), bytes(shot.data),
                            "raw", "RGBX", 0, 1).convert("RGB")


def run(rom, frames, presses=(), png=None, core=None):
    from libretro.drivers.input import IterableInputDriver
    from libretro.api.input import JoypadState

    core = core or CORE

    def inputs():
        frame = 0
        while True:
            kwargs = {}
            for btn, start, end in presses:
                if start <= frame < end:
                    kwargs[btn.name.lower()] = True
            yield JoypadState(**kwargs)
            frame += 1

    builder = (
        SessionBuilder.defaults(core)
        .with_content(rom)
        .with_paths(ExplicitPathDriver(corepath=core, system=SYSTEM_DIR,
                                       save=SYSTEM_DIR, assets=SYSTEM_DIR,
                                       playlist=SYSTEM_DIR))
        .with_input(IterableInputDriver(inputs))
        # libretro.py's DefaultPerfDriver crashes in ctypes; the core
        # runs fine without a perf interface.
        .with_perf(None)
    )
    with builder.build() as session:
        for _ in range(frames):
            session.run()
        img = frame_image(session)

    if img is None:
        print("Pillow not installed; no image analysis")
        return 0
    colors = img.getcolors(maxcolors=4096) or []
    print(f"final frame: {img.size[0]}x{img.size[1]}, {len(colors)} distinct colors")
    for count, color in sorted(colors, reverse=True)[:8]:
        print(f"  {color}: {count} px")
    if png:
        img.save(png)
        print(f"saved {png}")
    return 0 if len(colors) > 1 else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("frames", nargs="?", type=int, default=300)
    ap.add_argument("--png")
    ap.add_argument("--press", action="append", default=[],
                    help="BTN@frame or BTN@start-end (e.g. A@100-160)")
    ap.add_argument("--core",
                    help="libretro core next to this script (file stem, e.g. "
                         "mednafen_pce_fast); default: the Lynx cores")
    args = ap.parse_args()
    core = (os.path.join(HERE, args.core + "_libretro.dll")
            if args.core else None)
    sys.exit(run(args.rom, args.frames, parse_presses(args.press), args.png,
                 core=core))


if __name__ == "__main__":
    main()
