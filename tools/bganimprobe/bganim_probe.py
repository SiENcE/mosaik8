#!/usr/bin/env python3
"""Do a scene's animated background tiles actually ANIMATE?

`[[scene]] animated_tile` in the world data becomes `scenes.anim_tick_at`,
which rewrites a tile's PIXELS on a timer. So the check is on VRAM, not on
the screen: read the 16 bytes of each animated tile's DATA every frame and
count how many distinct frames appear, and how many frames apart they change.

The room is reached the way a script would reach it - poke vm.core's pending
exception (RAISE 2 = CHANGE_SCENE) - so the real room load runs.

Usage: bganim_probe.py ROM.gb SYM.noi [--scene N] [--tiles 41,74,62]

`--scene` defaults to 0. Without `--tiles` the probe DISCOVERS them: it
reports every background tile id whose data changed during the window, which
is also how to find the ids to pass on an A/B. With `--tiles`, exit 1 if any
named tile never changed.
"""
import sys

VRAM = 0x8000


def symbols(noi):
    out = {}
    for line in open(noi, encoding="utf-8", errors="replace"):
        p = line.split()
        if len(p) == 3 and p[0] == "DEF":
            try:
                out[p[1]] = int(p[2], 16)
            except ValueError:
                pass
    return out


def main():
    rom, noi = sys.argv[1], sys.argv[2]
    scene = 0
    if "--scene" in sys.argv:
        scene = int(sys.argv[sys.argv.index("--scene") + 1])
    tiles = None
    if "--tiles" in sys.argv:
        tiles = [int(t) for t in sys.argv[sys.argv.index("--tiles") + 1].split(",")]

    s = symbols(noi)
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null")
    CUR, PCODE = s["_vm_core_cur_scene"], s["_vm_core_pend_code"]
    PA, PB, PC = s["_vm_core_pend_a"], s["_vm_core_pend_b"], s["_vm_core_pend_c"]

    def run(n=1):
        for _ in range(n):
            pb.tick()

    def tap(b, after=20):
        pb.button_press(b)
        run(8)
        pb.button_release(b)
        run(after)

    def w16(a, v):
        pb.memory[a], pb.memory[a + 1] = v & 0xFF, (v >> 8) & 0xFF

    def tile_bytes(n):
        # WHICH VRAM ADDRESS A BACKGROUND TILE LIVES AT IS AN LCDC DECISION.
        # Bit 4 picks the addressing method: set = unsigned from 0x8000, clear
        # = SIGNED, where 0..127 live at 0x9000 and 128..255 at 0x8800. This
        # engine runs with it clear (LCDC 0xC7), so reading 0x8000 + n * 16
        # samples sprite tiles and every animated tile reads "static".
        if pb.memory[0xFF40] & 0x10:
            base = 0x8000 + n * 16
        else:
            base = (0x9000 + n * 16) if n < 128 else (0x8800 + (n - 128) * 16)
        return bytes(pb.memory[base:base + 16])

    run(300)
    for _ in range(10):
        if pb.memory[CUR] != 0:
            break
        tap("start")
        tap("a")
    pb.memory[PA] = scene
    w16(PB, 20 * 8)
    w16(PC, 20 * 8)
    pb.memory[PCODE] = 2
    run(180)
    if pb.memory[CUR] != scene:
        print("FAILED to reach scene %d; nothing measured." % scene)
        pb.stop(save=False)
        return 1
    print("in scene %d" % pb.memory[CUR])

    if tiles is None:
        # DISCOVERY: every background tile id whose data moved in the window.
        first = [tile_bytes(t) for t in range(256)]
        moved = set()
        for _ in range(240):
            run(1)
            moved.update(t for t in range(256)
                         if t not in moved and tile_bytes(t) != first[t])
        pb.stop(save=False)
        if not moved:
            print("NO background tile changed in 240 LCD frames")
            return 1
        print("background tiles that changed: %s"
              % ",".join(str(t) for t in sorted(moved)))
        print("(pass them as --tiles to measure each one)")
        return 0

    seen = {t: [] for t in tiles}
    for _ in range(240):
        run(1)
        for t in tiles:
            seen[t].append(tile_bytes(t))

    # a CONTROL tile: one nothing animates, so "VRAM changes" cannot be read
    # off some unrelated upload
    control = next(n for n in range(1, 120) if n not in tiles)
    ctrl = {tile_bytes(control)}

    ok = True
    for t in tiles:
        runs = seen[t]
        distinct = []
        for b in runs:
            if not distinct or b != distinct[-1]:
                distinct.append(b)
        uniq = len({bytes(b) for b in runs})
        # frames between changes
        gaps, last = [], 0
        for i in range(1, len(runs)):
            if runs[i] != runs[i - 1]:
                gaps.append(i - last)
                last = i
        gap = round(sum(gaps[1:]) / len(gaps[1:])) if len(gaps) > 2 else 0
        print("tile %3d: %d distinct frames over 240 LCD frames, changing every "
              "~%d frames" % (t, uniq, gap))
        ok &= uniq >= 2
    print("control tile %d: %d distinct (1 = static, as it should be)"
          % (control, len(ctrl)))
    print("")
    print("ALL BACKGROUND ANIMATIONS RUN" if ok else "SOME TILES NEVER CHANGED")
    pb.stop(save=False)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
