#!/usr/bin/env python3
"""`[build] actor_deactivate`: are the two lists CONSISTENT, every frame?

Splitting the live list in two is the kind of change an A/B cannot prove: the
whole point is that a parked actor stops moving, so the OAM stream is SUPPOSED
to differ. What can be proved is the invariant, and it is the one that matters:

  * every ACTIVE slot is in EXACTLY ONE of the two lists (live or parked) -
    never both, never neither. A slot in neither is an actor that has silently
    vanished; a slot in both is one that renders twice and can be woken while
    already awake.
  * every INACTIVE slot is in NEITHER. A retired actor left in the parked list
    is one `wake_scan` can RESURRECT when the camera next reaches it - the
    failure this probe exists for.
  * `a_parked[i]` agrees with which list the slot is in.
  * neither list holds a duplicate.

Checked on EVERY game frame, hooked at the frame's first stage so the lists are
read at a stable point (mid-render they are legitimately in flux).

Usage: deact_lists_probe.py ROM.gb SYM.noi [--rooms 0] [--frames 900]
"""
import re
import sys


def symbols(noi):
    out = {}
    for line in open(noi, encoding="utf-8", errors="replace"):
        m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
        if m:
            out[m.group(1)] = int(m.group(2), 16)
    return out


def main():
    rom, noi = sys.argv[1], sys.argv[2]
    arg = lambda k, d: (sys.argv[sys.argv.index(k) + 1] if k in sys.argv else d)
    rooms = [int(v) for v in arg("--rooms", "0").split(",")]
    frames = int(arg("--frames", "900"))
    s = symbols(noi)
    need = ("_vm_actor_live", "_vm_actor_n_live", "_vm_actor_plist",
            "_vm_actor_n_plist", "_vm_actor_a_active", "_vm_actor_a_parked",
            "_vm_core_run_scripts")
    missing = [n for n in need if n not in s]
    if missing:
        print("!! not in the .noi (is actor_deactivate on?):", missing)
        return 1

    from pyboy import PyBoy
    pb = PyBoy(rom, window="null")
    m = pb.memory
    g = lambda n: s[n] & 0xFFFF
    POOL = 16

    bad = []
    seen = [0]
    rec = [False]
    room_now = [None]

    def sample(_c):
        if not rec[0]:
            return
        seen[0] += 1
        nl, npk = m[g("_vm_actor_n_live")], m[g("_vm_actor_n_plist")]
        live = [m[g("_vm_actor_live") + k] for k in range(nl)]
        park = [m[g("_vm_actor_plist") + k] for k in range(npk)]
        act = [m[g("_vm_actor_a_active") + i] for i in range(POOL)]
        pkd = [m[g("_vm_actor_a_parked") + i] for i in range(POOL)]
        why = None
        if nl > POOL or npk > POOL:
            why = "list length out of range (%d/%d)" % (nl, npk)
        elif len(set(live)) != len(live):
            why = "duplicate in the live list %s" % live
        elif len(set(park)) != len(park):
            why = "duplicate in the parked list %s" % park
        else:
            for i in range(POOL):
                inl, inp = i in live, i in park
                if act[i] == 1 and (inl == inp):
                    why = ("slot %d is active but in %s" %
                           (i, "BOTH lists" if inl else "NEITHER list"))
                    break
                if act[i] == 0 and (inl or inp):
                    why = ("slot %d is INACTIVE but still in the %s list"
                           % (i, "live" if inl else "parked"))
                    break
                if act[i] == 1 and inp and pkd[i] != 1:
                    why = "slot %d is in the parked list but a_parked == 0" % i
                    break
                if act[i] == 1 and inl and pkd[i] == 1:
                    why = "slot %d is in the live list but a_parked == 1" % i
                    break
        if why and len(bad) < 6:
            bad.append("room %s frame %d: %s  (live=%s parked=%s)"
                       % (room_now[0], seen[0], why, live, park))

    a = s["_vm_core_run_scripts"]
    pb.hook_register(a >> 16, a & 0xFFFF, sample, None)

    def w16(x, v):
        m[x], m[x + 1] = v & 0xFF, (v >> 8) & 0xFF

    for _ in range(120):
        pb.tick()
    for room in rooms:
        m[g("_vm_core_pend_code")] = 2
        m[g("_vm_core_pend_a")] = room
        w16(g("_vm_core_pend_b"), 40)
        w16(g("_vm_core_pend_c"), 72)
        for _ in range(300):
            pb.tick()
        room_now[0] = room
        for label, held, fire in (("idle", (), 0), ("walk", ("right",), 0),
                                  ("fire", (), 1)):
            for b in held:
                pb.button_press(b)
            rec[0] = True
            for t in range(frames):
                if fire:
                    if t % 20 == 0:
                        pb.button_press("a")
                    elif t % 20 == 10:
                        pb.button_release("a")
                pb.tick()
            rec[0] = False
            for b in held:
                pb.button_release(b)
            pb.button_release("a")
        print("room %d: checked, %d game frames so far" % (room, seen[0]))

    print()
    if bad:
        print("INVARIANT BROKEN (%d sample(s) shown):" % len(bad))
        for b in bad:
            print("  " + b)
        return 1
    print("the two lists are consistent on all %d sampled game frames" % seen[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
