#!/usr/bin/env python3
"""vm.music's longest NO-TICK gap on the SMS / Game Gear.

On the Game Boy the same quantity is read by hooking `_vm_music_update` in
PyBoy and counting display frames with no call. genesis_plus_gx exposes no CPU hooks through
libretro, so the z80 pair needs a different instrument for the SAME quantity:
watch the driver's own row state in work RAM and count frames in which NOTHING
moved.

`(m_row, m_left, m_tick)` is the right triple and no single one of them will
do: an update either spends a frame of the current row (m_left--, m_tick++) or
loads the next one (m_row++, m_left reloads, m_tick resets to 0), so each
member can sit still across a real tick while the triple never does.

The stimulus is the same as the GB probe's - room changes forced by poking
vm.core's pending exception (RAISE 2), which is what makes the two consoles'
numbers comparable.

NO `--hold` CONTROL HERE, deliberately: `gbs_mdrv_hold` is checked inside
`gbs_mdrv_pump`, which is the MAIN LOOP's tick as well as the watchdog's, so
pinning it silences the driver outright instead of isolating the interrupt.
(That is exactly what makes it the right control for the parallax probe, where
only the interrupt's raster cost is under test.) The control for THIS number
is a build made without the feature.

Reading the rate: "frames in which the driver advanced" is not 1.0 on a
healthy build, and should not be. The main loop catches up several ticks in
ONE iteration, so a room running at ~3 display frames per VM frame services
the driver on about a third of frames - which is why the GAP, not the rate, is
the number that means something.

Usage: sms_music_gap_probe.py ROM.sms SYM.noi [frames-per-room] [--rooms 0,1]

`--rooms` lists the scene indexes to cycle through (default 0,1; give the
rooms whose loads are worth pricing - the heaviest ones). The list is visited
four times, so every room is entered from the one before it more than once.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(os.path.dirname(HERE))      # the mosaik8 checkout
sys.path.insert(0, os.path.join(ENGINE, "emu", "libretro"))

import lynx_probe as lp                                        # noqa: E402
from libretro import SessionBuilder                            # noqa: E402
from libretro.drivers.path import ExplicitPathDriver           # noqa: E402
from libretro.api.input import JoypadState                     # noqa: E402
from libretro.drivers.input import IterableInputDriver         # noqa: E402

ROM, NOI = sys.argv[1], sys.argv[2]
PER_ROOM = int(sys.argv[3]) if len(sys.argv) > 3 and not sys.argv[3].startswith("-") else 150
_ROOM_LIST = [int(v) for v in (sys.argv[sys.argv.index("--rooms") + 1]
                               if "--rooms" in sys.argv else "0,1").split(",")]
if len(_ROOM_LIST) < 2:
    raise SystemExit("--rooms needs at least two scenes, or nothing changes room")
ROOMS = _ROOM_LIST * 4
CORE = os.path.join(ENGINE, "emu", "libretro",
                    "genesis_plus_gx_libretro.dll")
SYSDIR = os.path.join(ENGINE, "emu", "libretro")


def main():
    labels = lp.load_labels(NOI)
    g = lambda n: labels[n]

    def inputs():
        while True:
            yield JoypadState()

    builder = (SessionBuilder.defaults(CORE)
               .with_content(ROM)
               .with_paths(ExplicitPathDriver(corepath=CORE, system=SYSDIR,
                                              save=SYSDIR, assets=SYSDIR,
                                              playlist=SYSDIR))
               .with_input(IterableInputDriver(inputs))
               # genesis_plus_gx emits silence without the AV mask (the studio
               # preview needs the same); harmless here, and it keeps the core
               # running the audio path a music probe is about.
               .with_perf(None))
    with builder.build() as session:
        ram = lp.Ram(session)

        def rd(sym):
            return ram.read(g(sym))

        def wr(sym, v):
            ram.mem[ram._at(g(sym))] = v & 0xFF

        def state():
            return (ram.read(g("_vm_music_m_row"), 2), rd("_vm_music_m_left"),
                    rd("_vm_music_m_tick"))

        for _ in range(240):            # boot + the title screen's own load
            session.run()
        # Prove the poke lands before trusting anything measured after it: on
        # a read-only memory view every number below would be a boot trace.
        wr("_vm_core_pend_a", ROOMS[0])
        wr("_vm_core_pend_code", 2)
        for _ in range(PER_ROOM):
            session.run()
        if rd("_vm_core_cur_scene") != ROOMS[0]:
            print("the RAISE-2 poke did not change the scene (cur_scene=%d):"
                  " this core's SYSTEM_RAM is not writable, so no room-load"
                  " gap can be forced here" % rd("_vm_core_cur_scene"))
            return 1

        trace, started = [], False
        # THE STIMULUS HAS TO PROVE ITSELF. A trace with no room change in it
        # reports the same tiny gap on every build, which reads as "the fix
        # changes nothing" when it really means "nothing was measured" - so
        # the scene actually reached is recorded per segment and printed.
        reached = []
        prev = state()
        for room in ROOMS[1:]:
            wr("_vm_core_pend_a", room)
            wr("_vm_core_pend_code", 2)
            for _ in range(PER_ROOM):
                session.run()
                now = state()
                trace.append(now != prev)
                prev = now
            reached.append((room, rd("_vm_core_cur_scene")))
        gap = longest = moved = 0
        for changed in trace:
            if changed:
                started, moved = True, moved + 1
                longest, gap = max(longest, gap), 0
            elif started:
                gap += 1
        longest = max(longest, gap)
        asked = [a for a, _g in reached]
        got = [g for _a, g in reached]
        landed = sum(1 for a, g in reached if a == g)
        print("room changes: %d asked, %d LANDED  (scenes reached: %s)"
              % (len(reached), landed, got if landed != len(reached) else "all"))
        if not landed:
            print("  NOTHING LOADED - the gap below is a static-room trace,"
                  " not a room-load measurement")
        print("frames traced: %d" % len(trace))
        print("frames in which the driver advanced: %.3f"
              % (moved / max(1, len(trace))))
        print("LONGEST NO-TICK GAP: %d display frames" % longest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
