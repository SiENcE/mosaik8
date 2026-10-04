#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""vm.music gets a WATCHDOG interrupt on the consoles that have one
(stage 2 of the room-load silence work).

Stage 1 (music_pump) cut the longest room-load silence 39 -> 18 display
frames and could go no further: the residue was ONE unsplittable call (the
streamer's refill), and any main-loop scheme dies the same way.

THE INTERRUPT IS A SAFETY NET, NOT THE OWNER. Handing it the tick outright
(the first cut) put a ~2k-cycle handler at an arbitrary raster position every
frame, and an interrupt that runs long displaces every scanline-locked effect
in the machine: measured in the converted parallax room as 40 of 300 frames
with moved band boundaries (several by 6 lines - a strip of the room drawn at
another band's scroll, the reported "tiles that are not part of the scene"),
plus the same delay landing on the window sprite-cut line with a box open.
Moving it from the VBL chain to the timer helped and did not fix it; what
fixes it is not ticking while the raster is live at all.

So the MAIN LOOP keeps the tick, at the same point in the frame it always had
it, and the interrupt only fires the driver when the loop has not serviced it
for GBS_MDRV_LATE display frames - never during normal play, exactly during a
blocking room load. Measured: band jitter back to the pre-feature baseline
(0 of 300 on the SMS/GG sample conversion, 1 of 300 on the platformer
conversion = what baseline scores),
with the worst silence 54 -> 6 display frames.

The rules pinned here:

* Everything keys on the ONE always-supplied `VM_MUSIC_ISR` define (the
  VM_OBJ16 pattern): stated TRUE by the build only when the program wires
  `core.set_music_driver(music.play, ...)` AND the target is in
  `platforms.MUSIC_ISR_CONSOLES`. Absent/False keeps the main-loop catch-up
  arms verbatim - a guarded call site folds OUT, so a non-user's C is
  byte-identical.
* With the ISR armed, run()'s arm and music_pump() still tick - they delegate
  to the ONE prelude pacing owner the watchdog shares, so the two callers
  cannot double-tick (gbs_mdrv_busy is a test-and-set inside a CRITICAL).
* The ISR stands down on `gbs_mdrv_hold`, which the driver's play() raises
  around its init (stop()/pause() need no latch - they write their
  stand-down flag FIRST) and the SMS/GG beep raises around its PSG
  latch/data pair (a split pair would retune whatever register the ISR last
  latched).
* hUGEDriver projects never match (they wire music_huge.play; their tick is
  already a timer ISR), and Lynx/PCE keep the catch-up (no add_VBL there).
"""

from mosaik.compiler import MosaikCompiler
from mosaik8_build import _wants_music_isr


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _compile(sources, platform="gameboy", defines=None):
    out = MosaikCompiler().compile_program(sources, platform=platform,
                                           defines=defines)
    assert not out.startswith("Compilation error"), out
    return out


# A minimal shell exercising exactly the two new stdlib verbs the way the
# lib does: behind statement-level `if VM_MUSIC_ISR` guards.
SHELL = '''
module "main" {
    var g_tick: function()
    local function tick() { }
    function main() {
        g_tick = tick
        if VM_MUSIC_ISR {
            system.music_isr(g_tick)
        }
        if VM_MUSIC_ISR {
            system.music_hold(1)
        }
        if VM_MUSIC_ISR {
            system.music_hold(0)
        }
        sound.beep(440, 30)
        loop { }
    }
}
'''


def test_define_folds_the_feature():
    ok = True
    on = _compile([("m.mos", SHELL)], defines={"VM_MUSIC_ISR": True})
    ok &= check("armed: the GB family arms the TIMER, NEVER the VBL chain "
                "(the wire runs at boot, so a chained handler would run FIRST "
                "- ahead of the parallax commit and the sprite-cut restore)",
                "void gbs_mdrv_isr(void)" in on
                and "add_TIM(gbs_mdrv_isr)" in on
                and "add_VBL(gbs_mdrv_isr)" not in on)
    ok &= check("armed: the interrupt is a WATCHDOG - it only ticks when the "
                "main loop is GBS_MDRV_LATE frames behind",
                "gbs_mdrv_pump(GBS_MDRV_LATE)" in on
                and "#define GBS_MDRV_LATE" in on)
    ok &= check("armed: the MAIN LOOP still has its own tick, and both share "
                "ONE pacing owner (no double-tick)",
                "void gbs_music_tick(void)" in on
                and "gbs_mdrv_pump(1)" in on
                and "gbs_mdrv_last" in on and "gbs_mdrv_busy" in on)
    ok &= check("armed: the pacing keeps the catch-up cap",
                "(uint8_t)sys_time - gbs_mdrv_last" in on and "fd = 8" in on)
    ok &= check("armed: the watchdog POLLS at 16 Hz, not hUGE's 64 - its rate "
                "is how fast a stall is noticed, and every firing is a chance "
                "to displace a band write (measured 3/300 at 64 Hz, 0 at 16)",
                "TMA_REG = 0x00u" in on)
    ok &= check("armed: the wire + hold helpers are emitted",
                "gbs_music_isr_wire(" in on and "gbs_music_hold(" in on)
    ok &= check("armed: the GB family ORs the interrupt mask, never assigns",
                "set_interrupts(IE_REG | VBL_IFLAG | TIM_IFLAG)" in on)

    off = _compile([("m.mos", SHELL)])          # no defines: default False
    bare = _compile([("m.mos", SHELL.replace("system.music_isr(g_tick)", "")
                      .replace("system.music_hold(1)", "")
                      .replace("system.music_hold(0)", ""))])
    ok &= check("absent: nothing of the feature reaches the C",
                "gbs_mdrv" not in off and "gbs_music_" not in off)
    ok &= check("absent: byte-identical to a program without the calls",
                off == bare)
    return ok


def test_sms_variant():
    ok = True
    on = _compile([("m.mos", SHELL)], platform="sms",
                  defines={"VM_MUSIC_ISR": True})
    ok &= check("sms: the ISR is on the VBL chain (no timer interrupt there; "
                "nothing scanline-critical rides its chain)",
                "add_VBL(gbs_mdrv_isr)" in on
                and "add_TIM" not in on)
    ok &= check("sms: set_interrupts is NOT touched (the VDP owns it and the "
                "frame interrupt is already on)",
                "set_interrupts(IE_REG" not in on)
    ok &= check("sms: the beep's PSG latch/data pair raises the hold",
                "gbs_mdrv_hold = 1;" in on and "gbs_mdrv_hold = 0;" in on)
    off = _compile([("m.mos", SHELL)], platform="sms")
    ok &= check("sms absent: the beep keeps its plain pair (byte-identical)",
                "gbs_mdrv_hold" not in off)
    return ok


def test_build_derivation():
    ok = True
    glue = [("glue.mos", "core.set_music_driver(music.play, music.update, "
                         "music.stop)")]
    huge = [("glue.mos", "core.set_music_driver(music_huge.play, "
                         "music_huge.update, music_huge.stop)")]
    quiet = [("m.mos", "module \"main\" { function main() { } }")]
    ok &= check("a vm.music project arms on the GB family",
                _wants_music_isr(glue, "gameboy"))
    ok &= check("...and on the z80 pair",
                _wants_music_isr(glue, "sms")
                and _wants_music_isr(glue, "gamegear"))
    ok &= check("...but NOT on the Lynx (no add_VBL; catch-up stays)",
                not _wants_music_isr(glue, "lynx"))
    ok &= check("a hUGEDriver project never matches (its tick is already "
                "a timer ISR)", not _wants_music_isr(huge, "gameboy"))
    ok &= check("a music-free program never matches",
                not _wants_music_isr(quiet, "gameboy"))
    # The two traps that changed a hUGE project's ROM bytes on the first cut:
    # vm.music's own header documents the wiring line in a COMMENT, and a hUGE
    # project's generated glue carries BOTH wirings in an `if platform` fork
    # a text scan cannot see folded.
    comment = [("music.mos", "--     core.set_music_driver(music.play, "
                             "music.update, music.stop)")]
    ok &= check("a COMMENTED wiring line never matches (the quoted-import "
                "trap)", not _wants_music_isr(comment, "gameboy"))
    fork = [("glue.mos", "core.set_music_driver(music.play, music.update, "
                         "music.stop)\ncore.set_music_driver(music_huge.play, "
                         "music_huge.update, music_huge.stop)")]
    ok &= check("a hUGE glue's GB-family arm WINS on the GB family "
                "(vm.music's arm folds away there)",
                not _wants_music_isr(fork, "gameboy"))
    ok &= check("...while the same glue's vm.music arm is the live one on "
                "SMS/GG", _wants_music_isr(fork, "sms"))
    return ok


def test_lib_guards():
    """The library sources carry the guards this feature depends on."""
    ok = True
    core = open(os.path.join(ROOT, "lib", "vm", "core.mos"),
                encoding="utf-8").read()
    music = open(os.path.join(ROOT, "lib", "vm", "music.mos"),
                 encoding="utf-8").read()
    ok &= check("set_music_driver arms the ISR under the define",
                "system.music_isr(update)" in core)
    ok &= check("run()'s arm STILL TICKS under the define (the main loop owns "
                "it; the interrupt is only the net)",
                "} else if has_music_drv == 1 {" in core
                and core.count("system.music_tick()") == 2)
    pump = core.split("function music_pump()")[1].split("\n    }")[0]
    ok &= check("music_pump still pumps under the define (stage 1 is KEPT, "
                "not folded away - it covers the splittable phases)",
                "if VM_MUSIC_ISR {" in pump and "system.music_tick()" in pump)
    ok &= check("the driver's play() brackets its init with the hold "
                "(both variants)", music.count("system.music_hold(1)") >= 2
                and music.count("system.music_hold(0)")
                == music.count("system.music_hold(1)"))
    ok &= check("stop()/pause() write their stand-down flag FIRST "
                "(no latch needed)",
                "m_playing = 0" in music and "m_paused = 1" in music)
    return ok


def main():
    print("vm.music watchdog interrupt tick (6.6 stage 2)")
    print("=" * 58)
    ok = True
    ok &= test_define_folds_the_feature()
    ok &= test_sms_variant()
    ok &= test_build_derivation()
    ok &= test_lib_guards()
    print("=" * 58)
    print("All checks passed" if ok else "Some tests failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
