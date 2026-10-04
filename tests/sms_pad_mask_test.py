#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""Asking a z80 pad for a button it does not have must answer NO - and the SMS
and the Game Gear differ by exactly ONE real button.

Read it off GBDK's own `joypad()` routines, not off the shared `J_*` names,
which are GB-compat aliases:

* **SMS** (`lib/sms`): `in a,($DC)` ... `and $3F`, then the port $3F TH-line
  dance. Bits 6/7 of $DC are PLAYER-2 up/down and do NOT read as "not pressed"
  for a 1-player pad. Start there is the console's Pause, a separate NMI line,
  not a joypad bit. So both `J_START` (0x40) and `J_SELECT` (0x80) are phantom:
  measured on genesis_plus_gx with the reference-engine import, one press of button 2
  made both `input.held(INPUT_A)` and `input.held(INPUT_SELECT)` true.
* **Game Gear** (`lib/gg`): the same `$DC` read masked to `$3F`, then
  `and $20; rlca; rlca`, which MIRRORS button 2 (`J_A`) onto bit 7 - so
  `J_SELECT` is the phantom here - and then `in a,($00); cpl; and $80; rrca`:
  **port $00 bit 7 IS the Game Gear's own Start button** (active low), rotated
  into bit 6 = `J_START`. So on this console Start is REAL and only Select is
  fake, which is why the mask keeps 0x40 and drops 0x80 instead of being
  removed. (smspower.org/Development/StartButton; GBDK exposes it as
  `J_START`.)

A phantom button is not cosmetic. A VM8 `input_attach` fires on a rising EDGE,
so a script attached to both `a` and `select` - which is exactly what GB
Studio's "await any input" converts to - ran TWICE on one press and spawned two
copies of itself. In the SMS/GG sample conversion that opened the title menu
twice: once on the title screen, and again inside the first room, because the
second copy sat parked on the UI latch and resumed after the scene change.
(Proved by watching `vm_core_vm_active` and `vm_core_in_prev` live through
`emu/libretro/lynx_probe.py --core genesis_plus_gx`: two thread slots went
active on one press, and the SELECT attachment's edge latch was one of them.)

Masking the GAME GEAR to 0x3F as well - which this did until 2026-08-12 - does
the opposite harm: it throws a real button away. A reference-engine conversion
attaches its quest menu to exactly that button, so `menu_page_1`/`menu_page_2`
were converted into the ROM and then unreachable on that console.

**Masking is only half the truth on the SMS, though, and the other half took
until 2026-08-17 to connect.** The console HAS a Start - the PAUSE button -
and it is wired to the z80's non-maskable interrupt rather than to a pad bit,
so `joypad()` can never report it. The reference engine binds its title screens to Start
by construction, so masking alone left every converted SMS ROM stuck on its
own title screen: measured, 600 frames with Start held,
the SMS/GG sample conversion's `.sms` still showed PRESS START while the
SAME project's `.gg` reached the New Game / Continue menu, and the shooter conversion's `.sms`
never cleared its Press Start banner. (That was also the whole of the "no actor
animation on SMS" report - you never reached a scene with an actor in it.)

So the SMS answers `J_START` two ways, because the console offers two:

* the console's **PAUSE button** is a real Start no pad bit can report, so it
  is latched from the NMI. ALWAYS on - it costs nothing and collides with
  nothing.
* the pad's **button 1 is LABELLED "1 START"** and is what an SMS title screen
  is started with, so it can also set the bit - but only under
  **`[build] sms_start_button`, which is OFF by default**. The two are ONE bit
  to a program: a script attached to BOTH `a` and `start` fires TWICE on one
  press, the phantom-button hazard `J_SELECT` documents, and
  the SMS/GG sample conversion does exactly that (it opens its title menu twice). So it
  is a per-project choice - `--sms-start-button` on an import, the Build dock
  "SMS Start" checkbox on an authored project - and the shooter conversion, whose title
  waits on Start alone, turns it on.

The NMI half:

* the handler's name must be exactly `NMI_ISR` - `lib/sms/nmi.o` defines it as
  a two-byte RETN stub and the linker drops that module the moment something
  else defines the symbol (`gbdk/examples/sms/pause_button`);
* the latch is HELD for `GBS_SMS_PAUSE_FRAMES` display frames rather than
  cleared on read, because an NMI is an EDGE and our readers want a LEVEL. A
  VM8 `input_attach` computes its rising edge from two consecutive polls and
  several readers poll inside one frame, so a clear-on-read latch would be
  consumed by whoever asked first and the edge would never be seen.

Every non-z80 console is untouched and byte-identical, and so is the Game Gear
(hash-checked: only the SMS output changes).
"""

from mosaik import MosaikCompiler

SRC = '''
module "main" {
    import "platform.video"
    import "platform.input"
    function main() {
        video.enable_lcd()
        loop {
            if input.held(INPUT_A) {
                video.wait_vblank()
            }
            video.wait_vblank()
        }
    }
    export main
}
'''

PLAIN = "uint8_t gbs_input_pressed(uint8_t button) { return (uint8_t)(joypad() & button); }"
MASKED = ("uint8_t gbs_input_pressed(uint8_t button) "
          "{ return (uint8_t)(joypad() & button & GBS_PAD_MASK); }")

#: The mask each console's own joypad() can actually produce.
EXPECT = {"sms": "0x3F", "gamegear": "0x7F"}


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("z80 pad mask (no phantom buttons, and no lost real ones)")
    print("=" * 58)
    ok = True
    for plat, mask in EXPECT.items():
        c = MosaikCompiler().compile(SRC.strip(), platform=plat)
        ok &= check("%s: the joypad read is masked to the real bits" % plat,
                    ("GBS_PAD_MASK)" in c) or (MASKED in c))
        ok &= check("%s: the mask is %s" % (plat, mask),
                    ("#define GBS_PAD_MASK %s" % mask) in c)
        ok &= check("%s: the unmasked read is gone" % plat, PLAIN not in c)
    # The one that regressed: the Game Gear HAS Start (port $00 bit 7), so it
    # must not be masked away like the SMS's phantom player-2 bit. It also has
    # no pause NMI, so it keeps the one-line masked read - byte-identical.
    gg = MosaikCompiler().compile(SRC.strip(), platform="gamegear")
    ok &= check("gamegear: START survives the mask (0x40 & 0x7F)",
                "#define GBS_PAD_MASK 0x3F" not in gg)
    ok &= check("gamegear: keeps the one-line masked read (no NMI latch)",
                MASKED in gg and "NMI_ISR" not in gg)
    sms = MosaikCompiler().compile(SRC.strip(), platform="sms")
    ok &= check("sms: the pad itself still masks START off (it is not a bit)",
                "#define GBS_PAD_MASK 0x3F" in sms)
    # ...but the console's PAUSE button IS its Start, so it must reach the
    # program. Without this the converted title screens are unreachable.
    ok &= check("sms: the PAUSE NMI is claimed (the name must be NMI_ISR)",
                "void NMI_ISR(void) CRITICAL INTERRUPT {" in sms)
    ok &= check("sms: button 1 is NOT aliased by default",
                "if (p & J_A) p |= J_START;" not in sms)
    on = MosaikCompiler().compile_program([("m.mos", SRC.strip())],
                                          platform="sms", sms_start_button=True)
    ok &= check("sms: [build] sms_start_button aliases button 1 to START",
                "if (p & J_A) p |= J_START;" in on)
    ok &= check("sms: ...and the PAUSE latch is there either way",
                "NMI_ISR" in sms and "NMI_ISR" in on)
    ok &= check("gamegear: button 1 is never aliased (it has a real Start)",
                "p |= J_START" not in gg)
    ok &= check("sms: the latch is presented as J_START",
                "p |= J_START;" in sms)
    ok &= check("sms: the latch is HELD for a few display frames, not "
                "cleared on read", "GBS_SMS_PAUSE_FRAMES" in sms
                and "sys_time - gbs_sms_pause_t0" in sms)
    # Every console with real Start/Select keeps the plain read - byte-identical.
    for plat in ("gameboy", "gameboy_color", "nes"):
        c = MosaikCompiler().compile(SRC.strip(), platform=plat)
        ok &= check("%s: unchanged (has the buttons)" % plat,
                    PLAIN in c and "GBS_PAD_MASK" not in c)
    print("=" * 58)
    print("All checks passed" if ok else "Some tests failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
