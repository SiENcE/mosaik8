#!/usr/bin/env python3
"""Per-GAME-FRAME distribution of the stage table (60fps plan).

`framebudget.py` reports AVERAGES, which is the right instrument while a
regime is far from a threshold. It is the wrong one for the last step to a
LOCKED 1.00 LCD/frame: there the average already fits and what is left is a
MINORITY of frames that cross. This groups the same hook stream by game frame,
buckets the frames by how many LCD frames they took, and prints the mean stage
cost in each bucket - so the question "what do the crossing frames DO" has an
answer instead of a suspicion.

Usage: frame_hist.py ROM.gb SYM.noi [--room 11] [--frames 600] [--hold right]
"""
import re
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from framebudget import STAGES, LCD, symbols

arg = lambda n, d=None: (sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d)


def main():
    rom, noi = sys.argv[1], sys.argv[2]
    room = int(arg("--room", 11))
    frames = int(arg("--frames", 600))
    hold = arg("--hold")
    extra = [x if x.startswith("_") else "_" + x
             for x in (arg("--count", "") or "").split(",") if x]
    s = symbols(noi)
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null")
    present = [(n, s[n]) for n in STAGES if n in s]
    counted = [(n, s[n]) for n in extra if n in s]
    for n in extra:
        if n not in s:
            print("!! not in the .noi:", n)
    ev, rec = [], [False]

    def mk(tag):
        def hit(_c):
            if rec[0]:
                ev.append((pb._cycles(), tag))
        return hit

    for tag, val in present + counted:
        pb.hook_register(val >> 16, val & 0xFFFF, mk(tag), None)
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
    if hold:
        pb.button_press(hold)
    for _ in range(30):
        pb.tick()
    rec[0] = True
    for _ in range(frames):
        pb.tick()
    rec[0] = False

    first = present[0][0]
    # split the event stream into game frames at every `first` entry
    starts = [i for i, (_c, t) in enumerate(ev) if t == first]
    rows = []
    for a, b in zip(starts, starts[1:]):
        wall = ev[b][0] - ev[a][0]
        st, ct = {}, {}
        for i in range(a, b):
            st[ev[i][1]] = st.get(ev[i][1], 0) + (ev[i + 1][0] - ev[i][0])
            ct[ev[i][1]] = ct.get(ev[i][1], 0) + 1
        rows.append((wall, st, ct))
    if not rows:
        print("no frames recorded")
        return
    split = arg("--split-on")
    if split and not split.startswith("_"):
        split = "_" + split
    buckets = {}
    for wall, st, ct in rows:
        n = max(1, int(round(wall / LCD)))
        key = n if not split else (n, 1 if ct.get(split) else 0)
        buckets.setdefault(key, []).append((wall, st, ct))
    print("room %d  %s  %d game frames" % (room, hold or "idle", len(rows)))
    order = sorted(buckets)
    print("  LCD/frame buckets: " + ", ".join(
        "%s x%d" % (n, len(buckets[n])) for n in order))
    names = [t for t, _ in present]
    hdr = "  %-28s" % "stage" + "".join("%10s" % str(n) for n in order)
    print(hdr)
    for tag in names:
        vals = []
        for n in order:
            grp = buckets[n]
            vals.append(sum(r[1].get(tag, 0) for r in grp) / len(grp))
        if max(vals) < 50:
            continue
        print("  %-28s" % tag.lstrip("_") + "".join("%10.0f" % v for v in vals))
    for lbl, f in (("WALL", lambda w, st: w),
                   ("work (wall - vblank)",
                    lambda w, st: w - st.get("_gbs_wait_vblank", 0))):
        vals = []
        for n in order:
            grp = buckets[n]
            vals.append(sum(f(w, st) for w, st, _c in grp) / len(grp))
        print("  %-28s" % lbl + "".join("%10.0f" % v for v in vals))
    if counted:
        print("  -- CALLS per frame in each bucket --")
        for tag, _v in counted:
            vals = [sum(r[2].get(tag, 0) for r in buckets[n]) / len(buckets[n])
                    for n in order]
            print("  %-28s" % tag.lstrip("_")
                  + "".join("%10.2f" % v for v in vals))


if __name__ == "__main__":
    main()
