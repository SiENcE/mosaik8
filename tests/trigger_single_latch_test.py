#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""The player is in AT MOST ONE trigger, and re-entering it needs the hit to
CHANGE first - the reference engine's model (`core/trigger.c`), not a simplification.

Its `trigger_at_intersection` returns the FIRST rect the player box hits, and
ONE `last_trigger` byte suppresses re-firing until the hit trigger changes;
`trigger_reset()` clears it on scene load.

`vm.trigger` used to keep a per-trigger rising-edge latch and fire EVERY rect
the box overlapped. Those differ whenever rects sit closer together than the box
is wide - which a reference-engine conversion does on purpose. Its town room places two
ONE-TILE triggers side by side at tile x 12 and 13 (the converted
triggers 5 and 6), shoving the player +3 and -3 tiles: an
invisible two-way wall. The converted player box is 16 px (`[player] width`), so
it spans BOTH columns, both scripts spawned, and each shove dragged the box off
one rect and back onto the other - the room was an inescapable loop.

What the single latch must NOT break, and is checked below: walking from rect A
straight into rect B still fires B (the hit changed), and stepping out of a rect
and back in still fires it (the hit went to NO_TRIG and back).

The sequencing is exercised against a transcription of `update()` (below) and
the module source is then checked to BE that shape; the ROM-level proof is
walking the town room in the reference-engine sample conversion.
"""



SRC = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        '..', 'lib', 'vm', 'trigger.mos'), encoding='utf-8').read()


# ---------------------------------------------------------------- the model
# A transcription of vm.trigger's update(), so the sequencing rules can be
# exercised without a ROM. The SOURCE checks below are what tie it to the real
# module; this is here because the interesting part is the sequence, not one
# line of overlap arithmetic.
NO_TRIG = 255


class Triggers:
    def __init__(self, rects):
        self.rects = rects              # [(x, y, w, h)]
        self.last = NO_TRIG             # clear() -> the reference engine's trigger_reset
        self.fired = []

    def hit_at(self, px, py, pw, ph):
        for i, (x, y, w, h) in enumerate(self.rects):
            if px < x + w and x < px + pw and py < y + h and y < py + ph:
                return i                # FIRST match wins
        return NO_TRIG

    def update(self, px, py, pw, ph):
        hit = self.hit_at(px, py, pw, ph)
        if hit != self.last:
            self.last = hit
            if hit != NO_TRIG:
                self.fired.append(hit)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("vm.trigger: one hit, one latch (the reference engine's trigger.c)")
    print("=" * 58)
    ok = True

    # --- the town-room pair: two 1-tile rects, a 16 px box ---------------
    # tile x 12 and 13, tile y 46, 1 wide and 4 tall, in world pixels.
    pair = [(12 * 8, 46 * 8, 8, 32), (13 * 8, 46 * 8, 8, 32)]
    t = Triggers(pair)
    # Walk in from the left. The box spans x 96..111, so it overlaps BOTH.
    t.update(96, 46 * 8, 16, 8)
    ok &= check("a 16 px box over two 1-tile rects fires ONE script",
                t.fired == [0])
    # The +3 tile shove its script performs lands the box past both.
    t.update(96 + 24, 46 * 8, 16, 8)
    ok &= check("the shove out of both rects fires nothing more",
                t.fired == [0])
    # Walking back left onto the SECOND rect fires that one - which is the
    # authored behaviour of the pair, and what the old code could not express.
    t.update(13 * 8, 46 * 8, 16, 8)
    ok &= check("coming back onto the other rect fires it once",
                t.fired == [0, 1])
    # Standing still inside it must not re-fire.
    for _ in range(4):
        t.update(13 * 8, 46 * 8, 16, 8)
    ok &= check("standing inside a rect does not re-fire it",
                t.fired == [0, 1])

    # --- what must keep working -----------------------------------------
    apart = [(0, 0, 16, 16), (64, 0, 16, 16)]
    t = Triggers(apart)
    t.update(0, 0, 16, 8)
    t.update(64, 0, 16, 8)
    ok &= check("A then B (no gap between them) fires both",
                t.fired == [0, 1])
    t = Triggers(apart)
    t.update(0, 0, 16, 8)
    t.update(32, 0, 16, 8)          # out
    t.update(0, 0, 16, 8)           # and back into the SAME rect
    ok &= check("leaving a rect and returning fires it again",
                t.fired == [0, 0])

    # --- a room load must forget the previous room's latch ---------------
    t = Triggers(apart)
    t.update(0, 0, 16, 8)
    t.last = NO_TRIG                # clear()
    t.update(0, 0, 16, 8)
    ok &= check("clear() forgets the latch, so a room load can spawn into a "
                "trigger", t.fired == [0, 0])

    # --- and the module really is written that way -----------------------
    ok &= check("vm.trigger keeps ONE latch, not one per rect",
                "var last: u8 = NO_TRIG" in SRC and "tin[" not in SRC)
    ok &= check("the overlap scan returns the FIRST hit",
                "local function hit_at(" in SRC and "return i" in SRC)
    ok &= check("update spawns only when the hit CHANGES",
                "if hit != last {" in SRC)
    ok &= check("clear() resets the latch (the reference engine's trigger_reset)",
                "last = NO_TRIG" in SRC.split("function clear()")[1]
                .split("function add")[0])
    ok &= check("NO_TRIG is the 255 sentinel",
                "const NO_TRIG = 255" in SRC)
    ok &= check("update still takes the player BOX, not a tile",
                "function update(px: u16, py: u16, pw: u8, ph: u8)" in SRC)

    print("=" * 58)
    print("All checks passed" if ok else "Some tests failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
