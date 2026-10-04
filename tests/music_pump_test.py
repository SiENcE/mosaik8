#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""vm.music is PUMPED through the long blocking stretches of a room load.

The driver is advanced only by `vm.core.run`'s per-frame arm, which catches up
against `system.frames()`. A room load and a box-close repaint each block for
many DISPLAY frames inside ONE VM frame, so across them the song is not advanced
at all and simply stops - the reported "vm.music stops when a new scene loads,
and when a dialogue box is cleared". hUGEDriver does not have this because its
tick is a 64 Hz timer INTERRUPT and its per-frame seam call is deliberately
empty.

`core.music_pump()` is the main-loop equivalent, with no re-entrancy at all
because a pump runs where the driver already runs. Two rules:

* it ticks only GENUINELY ELAPSED display frames - run()'s arm has a floor of
  one tick (a game frame faster than the display counter still owes one), and a
  pump may be called several times inside one display frame, where the same
  floor would run the song fast;
* the arithmetic is DUPLICATED from run()'s arm rather than shared, because
  `run` is `bank(0)`-pinned and the pump is not: one shared helper would put a
  cross-bank trampoline on the per-frame path.

MEASURED on the SMS/GG sample conversion (its GB build, which uses vm.music -
the GB-family sample uses hUGEDriver), hooking `_vm_music_update` and counting
display frames with no call at all, over 2,500 frames of walking between rooms:
**longest silence 39 -> 18 display frames**. What is left is ONE unsplittable
call: a phase probe (hooks on `_rooms_load_room` / `_scenes_warm` / the pump)
puts the residue between the last pre-setup pump and the fade-in ramp, which is
the streamer's own `refill` re-seeding all 32 columns. Closing it needs either a
per-column entry point the generated loop can pump between, or the timer-ISR
tick (stage 2, music_isr_test).

Emitted ONLY for a world with songs, on the same condition `generate_glue`
wires a driver - a music-free world's rooms.mos is byte-identical.
"""

from mosaik_vm.rooms import emit_rooms_mos


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("vm.music is pumped through a room load")
    print("=" * 58)
    ok = True
    base = {"types": ["platform"], "wide_rooms": True, "parallax": True,
            "uniform": False, "has_collision": True, "fade": 1,
            "has_scene_init": True}

    quiet = emit_rooms_mos(dict(base, music=False))
    ok &= check("a world with no songs emits NO pump (byte-identical)",
                "music_pump" not in quiet)

    loud = emit_rooms_mos(dict(base, music=True))
    n = loud.count("core.music_pump()")
    ok &= check("a world with songs pumps at several phase boundaries (%d)" % n,
                n >= 4)

    # The fade-in ramp waits a whole display frame at a time, so pumping inside
    # it ticks the song at exactly the rate run() would.
    lines = loud.splitlines()
    fade = [i for i, l in enumerate(lines) if "video.wait_vblank()" in l]
    ok &= check("the fade-in ramp pumps per waited frame",
                any("core.music_pump()" in lines[i + 1] for i in fade))

    # ...and the map upload, the biggest single blocking chunk, is followed by
    # one. `paint`/`warm` are the two forms of it.
    paint = [i for i, l in enumerate(lines)
             if "scenes.paint(rm)" in l or "scenes.warm(rm)" in l]
    ok &= check("the map upload is followed by a pump",
                bool(paint) and any("core.music_pump()" in l
                                    for l in lines[min(paint):min(paint) + 12]))

    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "lib", "vm", "core.mos"), encoding="utf-8").read()
    body = src.split("function music_pump()")[1].split("\n    }")[0]
    ok &= check("the pump has NO one-tick floor (run()'s arm does)",
                "fd = 1" not in body)
    ok &= check("the pump keeps run()'s catch-up cap",
                "MUSIC_CATCHUP_MAX" in body)
    ok &= check("the pump stands down without a driver",
                "has_music_drv == 0" in body)
    ok &= check("a box-close repaint pumps too",
                src.count("music_pump()") >= 3)

    print("=" * 58)
    print("All checks passed" if ok else "Some tests failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
