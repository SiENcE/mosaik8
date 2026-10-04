"""The trigger hit test's reference-engine topdown GRID SNAP.

The reference engine's topdown state moves the player on an 8 px grid, so its trigger
test only ever SAMPLES the box at grid stops - the player walks a full step
INTO a trigger before it can fire (the launch-pad room's door: the reference
sprite ends up inside the door art). Ours moves per pixel, so the equivalent
is snapping the box to the NEAREST grid stop before the test.

It is PER SCENE, not global: the reference engine's ADVENTURE state runs the same test at
pixel positions (the shooter conversion's aiming quadrants), and both convert to `topdown`.
Absent -> pixel-exact everywhere and byte-identical.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik_vm.rooms import emit_rooms_mos                       # noqa: E402

FAILS = []


def check(ok, what):
    print("  %s: %s" % ("ok" if ok else "FAIL", what))
    if not ok:
        FAILS.append(what)


def main():
    print("[vm.trigger: the snap is opt-in and rounds to the NEAREST stop]")
    src = open(os.path.join(ROOT, "lib", "vm", "trigger.mos"),
               encoding="utf-8").read()
    check("function set_snap" in src, "vm.trigger exposes set_snap")
    # EVERY export statement, not `split("export")[-1]`: the module has more
    # than one now (W7j appended `hit_script` / `interact`), and taking only
    # the last one read as "set_snap is not exported" the moment a second line
    # was added below it.
    exports = [ln for ln in src.splitlines() if ln.strip().startswith("export ")]
    check(any("set_snap" in ln for ln in exports), "...and exports it")
    # round to NEAREST (+4 then mask down), not floor: a floor would fire a
    # trigger up to 7 px EARLY on the way in, which is the opposite error.
    check(re.search(r"qx = \(px \+ 4\) & 0xFFF8", src)
          and re.search(r"qy = \(py \+ 4\) & 0xFFF8", src),
          "it rounds to the NEAREST 8 px stop")
    check(re.search(r"if t_snap != 0 \{", src),
          "and 0 keeps the pixel-exact test (the default)")
    check("var t_snap: u8" in src,
          "the flag is initialiser-free (BSS), so it costs no resident image")

    print("\n[rooms.mos: emitted per SCENE, and only when some scene asks]")
    base = {"types": ["topdown"], "has_triggers": True}
    off = emit_rooms_mos(dict(base))
    check("set_snap" in off is False or "set_snap" not in off,
          "a world with no trigger_snap emits NO set_snap (byte-identical)")
    check("TSNAP" not in off, "...and no TSNAP table")

    # every scene snapping -> one literal, no table
    allsnap = emit_rooms_mos(dict(base, tsnap=[8, 8, 8]))
    check("trigger.set_snap(8)" in allsnap,
          "when every scene agrees it passes the literal")
    check("TSNAP" not in allsnap, "...and still emits no table")

    # a MIXED world (a reference-engine conversion: TOPDOWN snaps, ADVENTURE does not)
    mixed = emit_rooms_mos(dict(base, tsnap=[8, 0, 8]))
    check("const TSNAP: array[u8, 3] = [ 8, 0, 8 ]" in mixed,
          "a mixed world emits the per-scene table")
    check("trigger.set_snap(TSNAP[rm])" in mixed,
          "...and load_room indexes it by room")

    print("\n[the snap is applied to the TEST, never to the player]")
    check("hit_at(qx, qy" in src,
          "the snapped coords go to hit_at only - the player keeps its own "
          "pixel position, or a scripted walk would jitter onto the grid")

    if FAILS:
        print("\ntrigger-snap tests FAILED (%d)" % len(FAILS))
        return 1
    print("\nAll trigger-snap tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
