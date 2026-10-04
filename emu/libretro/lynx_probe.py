"""Read Lynx RAM while a ROM runs, for headless behaviour + timing checks.

    python emu/libretro/lynx_probe.py <rom.lnx> --lbl <rom.lbl> \
        --watch _vm_core_heap+58:u16 --frames 900 --press A@250-330

The libretro cores expose the Lynx's 64 KB as RETRO_MEMORY_SYSTEM_RAM, so with a
cc65 label file (relink with `-Ln <name>.lbl`) this gives the same observability
PyBoy gives on the Game Boy: named symbols read live, frame by frame. cc65 labels
DATA as well as functions, so module-level mosaik vars are reachable by name.

`--rate SYMBOL[+off]` is the timing instrument: it reports how many LCD frames
pass per change of that cell. Point it at a counter the game decrements once per
VM frame and you get the VM frame rate directly -- which is what "the Lynx runs
this 66x slower than a Game Boy" was measured with.
"""
import argparse
import os
import re
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
SYSTEM_DIR = HERE
if os.path.exists(os.path.join(SYSTEM_DIR, "lynxboot.img")):
    CORE = os.path.join(HERE, "mednafen_lynx_libretro.dll")
else:
    CORE = os.path.join(HERE, "handy_libretro.dll")

from libretro import SessionBuilder, RETRO_MEMORY_SYSTEM_RAM   # noqa: E402
from libretro.drivers.path import ExplicitPathDriver           # noqa: E402
from libretro.api.input import DeviceIdJoypad, JoypadState     # noqa: E402
from libretro.drivers.input import IterableInputDriver         # noqa: E402

# Same libretro.py NULL-frame workaround the screenshot harness needs.
import libretro.drivers.environment.composite as _comp         # noqa: E402
from libretro.drivers.video import FrameBufferSpecial          # noqa: E402

_orig = _comp.CompositeEnvironmentDriver.video_refresh


def _video_refresh(self, data, width, height, pitch):
    if getattr(data, "value", 1) is None:
        self._video.refresh(FrameBufferSpecial.DUPE, width, height, pitch)
        return
    _orig(self, data, width, height, pitch)


_comp.CompositeEnvironmentDriver.video_refresh = _video_refresh

BUTTONS = {n: getattr(DeviceIdJoypad, n)
           for n in ("A", "B", "UP", "DOWN", "LEFT", "RIGHT", "START", "SELECT")}


def load_labels(path):
    """A symbol table, in either toolchain's format.

    cc65 VICE label file (`-Ln`):  `al 00A430 ._vm_core_heap`
    sdcc / GBDK no-ICE file (`-Wl-j`): `DEF _vm_core_heap 0xC39A`

    Both are accepted because `--core` makes this probe generic: the same RAM
    watching works on the SMS / Game Gear (genesis_plus_gx) and PC Engine
    (mednafen_pce_fast), and those builds come out of sdcc and cc65
    respectively. Reading a `.noi` is what lets a GBDK-family ROM be timed here
    the way `lynx_relabel.py` + `-Ln` does for the Lynx."""
    out = {}
    with open(path) as f:
        for line in f:
            m = re.match(r"^al\s+([0-9A-Fa-f]+)\s+\.(\S+)", line)
            if m:
                out[m.group(2)] = int(m.group(1), 16)
                continue
            m = re.match(r"^DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)", line)
            if m:
                out[m.group(1)] = int(m.group(2), 16)
    return out


def resolve(spec, labels):
    """`_sym+12:u16` / `0x1234:u8` -> (addr, width, signed)."""
    body, _, kind = spec.partition(":")
    kind = kind or "u8"
    name, _, off = body.partition("+")
    base = labels.get(name)
    if base is None:
        try:
            base = int(name, 0)
        except ValueError:
            sys.exit("unknown symbol %r (not in the label file)" % name)
    addr = base + (int(off, 0) if off else 0)
    width = 2 if kind.endswith("16") else 1
    return addr, width, kind.startswith("i")


class Ram:
    def __init__(self, session):
        self.buf = session.core.get_memory_data(RETRO_MEMORY_SYSTEM_RAM)
        self.size = session.core.get_memory_size(RETRO_MEMORY_SYSTEM_RAM)
        if not self.buf or not self.size:
            sys.exit("this core exposes no SYSTEM_RAM")
        import ctypes
        self.mem = (ctypes.c_uint8 * self.size).from_address(
            ctypes.cast(self.buf, ctypes.c_void_p).value)

    def _at(self, addr):
        """Symbol address -> offset inside SYSTEM_RAM.

        The Lynx exposes its whole 64 KB, so a link address IS the offset. The
        SMS / Game Gear expose only their 8 KB work RAM, which the z80 sees at
        0xC000 - so an sdcc `.noi` address like 0xCC6C is 0x0C6C here. Masking
        by the (power-of-two) RAM size does both: it is the identity on the
        Lynx and the window offset on the Sega consoles. Without it every
        SMS/GG symbol read raised IndexError, which is why nothing had ever
        probed one despite the docstring offering it."""
        return addr & (self.size - 1) if addr >= self.size else addr

    def read(self, addr, width=1, signed=False):
        addr = self._at(addr)
        v = self.mem[addr] if width == 1 else (
            self.mem[addr] | (self.mem[addr + 1] << 8))
        if signed:
            top = 1 << (width * 8 - 1)
            if v >= top:
                v -= top * 2
        return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("--lbl", help="cc65 -Ln label file (for symbol names)")
    ap.add_argument("--frames", type=int, default=900)
    ap.add_argument("--press", action="append", default=[],
                    help="BTN@start-end (frames)")
    ap.add_argument("--watch", action="append", default=[],
                    help="SYMBOL[+off][:u8|u16|i16] -- print when it changes")
    ap.add_argument("--rate", help="SYMBOL[+off][:...] -- LCD frames per change")
    ap.add_argument("--core", default=None)
    args = ap.parse_args()

    labels = load_labels(args.lbl) if args.lbl else {}
    presses = []
    for spec in args.press:
        btn, _, win = spec.partition("@")
        start, _, end = win.partition("-")
        start = int(start or 0)
        presses.append((BUTTONS[btn.upper()], start, int(end) if end else start + 1))

    def inputs():
        f = 0
        while True:
            kw = {}
            for btn, s, e in presses:
                if s <= f < e:
                    kw[btn.name.lower()] = True
            yield JoypadState(**kw)
            f += 1

    core = args.core or CORE
    builder = (SessionBuilder.defaults(core)
               .with_content(args.rom)
               .with_paths(ExplicitPathDriver(corepath=core, system=SYSTEM_DIR,
                                              save=SYSTEM_DIR, assets=SYSTEM_DIR,
                                              playlist=SYSTEM_DIR))
               .with_input(IterableInputDriver(inputs))
               .with_perf(None))

    watches = [(s, resolve(s, labels)) for s in args.watch]
    rate = resolve(args.rate, labels) if args.rate else None

    with builder.build() as session:
        ram = Ram(session)
        print("SYSTEM_RAM: %d bytes" % ram.size)
        prev = {}
        gaps, last_val, last_f = [], None, 0
        for f in range(args.frames):
            session.run()
            for name, (addr, w, sg) in watches:
                v = ram.read(addr, w, sg)
                if prev.get(name) != v:
                    print("  f%-5d %-28s = %d" % (f, name, v))
                    prev[name] = v
            if rate:
                v = ram.read(*rate)
                if last_val is None:
                    last_val, last_f = v, f
                elif v != last_val:
                    gaps.append(f - last_f)
                    last_val, last_f = v, f
        if rate:
            core_gaps = [g for g in gaps if g < 60]
            print("\n%s: %d changes in %d frames" % (args.rate, len(gaps), args.frames))
            print("  LCD frames per change: %s" % Counter(gaps).most_common(6))
            if core_gaps:
                print("  mean (excluding long excursions): %.2f"
                      % (sum(core_gaps) / len(core_gaps)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
