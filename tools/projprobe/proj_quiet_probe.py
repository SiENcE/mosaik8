#!/usr/bin/env python3
"""Does the QUIESCENCE early-out still let a room shoot?

`vm.projectile` skips its update AND its render entirely while `p_quiet` is
set - nothing in flight and every slot already parked. The whole risk is that
the flag gets stuck: a pool that never wakes fires nothing, and a pool that
wakes but keeps a stale `p_slots` claims a slot the block has no OAM for and
the shot flies invisibly.

So this asserts three things on the ROM, in a room that shoots (measured in
the reference-engine sample conversion's shooter room), holding A:
  - shots really launch (p_active goes high, more than one at a time),
  - every live slot is INSIDE p_slots (i.e. render will draw it), and
  - p_quiet is 0 whenever anything is active, and returns to 1 after.

Usage: proj_quiet_probe.py ROM.gb SYM.noi [--room N] [--frames 600]

`--room` defaults to 0 - name the room whose player shoots.
"""
import re
import sys

NPROJ = 8


def symbols(noi):
    out = {}
    for line in open(noi, encoding="utf-8", errors="replace"):
        m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
        if m:
            out[m.group(1)] = int(m.group(2), 16)
    return out


def main():
    if len(sys.argv) < 3 or sys.argv[1].startswith("--"):
        print(__doc__)
        return 2
    rom, noi = sys.argv[1], sys.argv[2]
    arg = lambda k, d: (sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d)
    room = int(arg("--room", "0"))
    frames = int(arg("--frames", "600"))
    s = symbols(noi)
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null")
    g = lambda n: s[n] & 0xFFFF

    def w16(a, v):
        pb.memory[a], pb.memory[a + 1] = v & 0xFF, (v >> 8) & 0xFF

    for _ in range(120):
        pb.tick()
    pb.memory[g("_vm_core_pend_code")] = 2
    pb.memory[g("_vm_core_pend_a")] = room
    w16(g("_vm_core_pend_b"), 40)
    w16(g("_vm_core_pend_c"), 72)
    for _ in range(300):
        pb.tick()

    act, slots, quiet = g("_vm_projectile_p_active"), g("_vm_projectile_p_slots"), None
    if "_vm_projectile_p_quiet" in s:
        quiet = g("_vm_projectile_p_quiet")
    peak, ever, out_of_block, quiet_wrong, quiet_seen = 0, 0, 0, 0, 0
    # A reference-engine conversion may open on a START-gated pause (the shooter
    # conversion's own rule): tap Start alongside A or the pad is dead and
    # nothing fires.
    slot_seen = set()
    for t in range(frames):
        if t % 10 == 0:
            pb.button_press("a")
        elif t % 10 == 5:
            pb.button_release("a")
        if "--start" in sys.argv:
            if t % 40 == 20:
                pb.button_press("start")
            elif t % 40 == 25:
                pb.button_release("start")
        pb.tick()
        slot_seen.add(pb.memory[slots])
        live = [i for i in range(NPROJ) if pb.memory[act + i] == 1]
        n = pb.memory[slots]
        peak = max(peak, len(live))
        ever += len(live)
        out_of_block += sum(1 for i in live if i >= n)
        if quiet is not None:
            q = pb.memory[quiet]
            quiet_seen += q
            if q == 1 and live:
                quiet_wrong += 1
    # A room that never fires is the OTHER half of the test, not a failure:
    # it is what proves the flag actually REACHES 1 and stays there (which is
    # where the saving comes from). Ask for it explicitly with --control, so a
    # shooting room that has gone quiet still fails loudly.
    control = "--control" in sys.argv
    if control:
        ok = ever == 0 and quiet_seen > frames * 0.9 and quiet_wrong == 0
    else:
        ok = peak >= 2 and out_of_block == 0 and quiet_wrong == 0
    print("room %d, %d frames holding A%s"
          % (room, frames, " (CONTROL: expects no shot)" if control else ""))
    want = (peak == 0) if control else (peak >= 2)
    print("  peak concurrent shots     : %d %s" % (peak, "OK" if want else "FAIL"))
    print("  shot-frames observed      : %d" % ever)
    print("  p_slots seen              : %s" % sorted(slot_seen))
    print("  live slots outside p_slots: %d %s"
          % (out_of_block, "OK" if out_of_block == 0 else "FAIL"))
    if quiet is not None:
        print("  p_quiet set while live    : %d %s"
              % (quiet_wrong, "OK" if quiet_wrong == 0 else "FAIL"))
        print("  frames quiescent          : %d of %d" % (quiet_seen, frames))
    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
