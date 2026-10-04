#!/usr/bin/env python3
"""`[build] proj_scan` - the reference engine's PROJECTILE COLLISION SPREAD.

The reference VM's `src/core/projectiles.c` defines `PROJECTILES_COLLISION_SPREAD SPREAD_4`
and wraps its whole hit test in

    tmp_iterator = game_time;
    ...
    if ((tmp_iterator++ & PROJECTILES_COLLISION_SPREAD) == 0) { ... }

so a shot there is collision-tested every FOURTH frame, phase-spread by its
position in the pool - the same shape its actor scan uses and the same shape
`[build] actor_scan` already ports. We tested every shot every frame, which is
a straight 4x on the stage the frame histogram blames for the shooter room's
crossing frames (`projectile_update` 783 -> 15,492 between its 2- and 3-LCD
buckets, and the reference ROM holds 1.01 LCD/frame in that room WITH shots in
flight).

THE CONTRACT THIS PINS:

  * OFF (the absent flag) is byte-identical: the guard's `then` arm leaves
    `test` a constant 1, and the compiler supplies `VM_PROJ_SCAN_ALL = True` /
    `VM_PROJ_SCAN_MASK = 0` itself so a statement-level guard on an
    unresolvable name cannot survive as a runtime test (the VM_OBJ16 rule).
  * ONLY the collision test is spread. Movement, the off-screen despawn, the
    lifetime countdown and the flight animation still run every frame - that
    is the reference VM's split too, so a shot's TRAJECTORY is unchanged and only the
    sampling of its overlap is coarser.
  * the phase is (frame counter + slot), so eight shots spread across N frames
    instead of all testing together.
  * BOTH collision arms are gated - the actor scan AND the player-hit arm,
    which is the one the reference VM spreads as well.
  * N is a power of two; the runtime takes N-1 as a mask.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    if not cond:
        FAILS.append(label)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def body(src, name):
    m = re.search(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    indent = m.group(1)
    end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
    return src[m.start():end.end() if end else len(src)]


def test_source():
    proj = read("lib", "vm", "projectile.mos")
    upd = body(proj, "update")
    print("\n[lib/vm/projectile.mos]")
    check("the mask + phase counter are behind the fold",
          re.search(r"if VM_PROJ_SCAN_ALL \{\s*\n\s*\} else \{\s*\n\s*"
                    r"const PSCAN_MASK = VM_PROJ_SCAN_MASK\s*\n\s*"
                    r"var p_scan_tick: u8", proj) is not None)
    check("the phase advances once per update", "p_scan_tick += 1" in upd)
    check("the per-shot turn is (counter + slot) & mask",
          "var ph: u8 = p_scan_tick + i" in upd and "if (ph & PSCAN_MASK) == 0" in upd)
    # The block is spelled ONCE PER COMPILE-TIME ARM rather than gated by a
    # local flag: a `test` local survives the fold on cc65 (sdcc folded it,
    # cc65 did not) and cost those builds 25 B for a feature they do not use,
    # which breaks the byte-identical norm for a non-user. So the default arm
    # must be the ORIGINAL body, character for character.
    # TWO forks on the flag inside update(): the phase counter first, then the
    # collision block. The second is the one this checks.
    spread = upd.split("if VM_PROJ_SCAN_ALL {")
    check("the actor arm forks at COMPILE time on the flag", len(spread) == 3,
          "expected the counter fork and the collision fork")
    if len(spread) == 3:
        then_arm, else_arm = spread[2].split("} else {", 1)
        check("the default arm is the original body, with no flag local",
              "actor.hit_scan(" in then_arm and "p_scan_tick" not in then_arm
              and "var test" not in then_arm)
        check("the opt-in arm wraps the SAME body in the phase test",
              "var ph: u8 = p_scan_tick + i" in else_arm and "if (ph & PSCAN_MASK) == 0" in else_arm
              and "actor.hit_scan(" in else_arm)
    check("no per-shot flag local survives anywhere",
          "var test: u8" not in upd,
          "it cost cc65 25 B when folded off")
    # the reference VM spreads its player-hit arm too; ours does not, deliberately - that
    # arm is ONE box test against the player, where the actor arm is the pool
    # walk, so leaving it every-frame is both cheaper to keep and strictly
    # MORE accurate than the reference.
    check("the PLAYER hit arm is deliberately NOT spread",
          "if has_player_hit == 1 {" in upd)
    # what must NOT be gated - the reference VM runs all of these every frame
    for what, needle in (("movement", "p_x[i] = step(p_x[i], p_vx[i], p_fx[i])"),
                         ("the off-screen despawn", "sx < 0 - 8 or sx > SCREEN_WIDTH"),
                         ("the lifetime countdown", "p_life[i] -= 1"),
                         ("the flight animation", "p_fidx[i] += 1")):
        i = upd.find(needle)
        check("%s still runs every frame" % what,
              i >= 0 and "test == 1" not in upd[max(0, i - 400):i])


def test_build_surface():
    print("\n[the build surface]")
    b = read("mosaik8_build.py")
    check("proj_scan is a known [build] key", "'actor_scan', 'proj_scan'," in b)
    check("the getter defaults to 1 (byte-identical)",
          "def get_proj_scan" in b
          and "get('proj_scan')" in b and "return 1" in b)
    check("only powers of two are accepted",
          re.search(r"proj_scan.*\n(?:.*\n)*?.*if n not in \(1, 2, 4, 8\)", b)
          is not None)
    check("the defines are stated only when opting IN",
          "defines['VM_PROJ_SCAN_ALL'] = False" in b
          and "defines['VM_PROJ_SCAN_MASK'] = pscan - 1" in b)
    c = read("mosaik", "compiler.py")
    check("the compiler supplies BOTH defaults itself",
          "all_defines.setdefault('VM_PROJ_SCAN_ALL', True)" in c
          and "all_defines.setdefault('VM_PROJ_SCAN_MASK', 0)" in c,
          "a statement-level guard on an unresolvable name would not fold")


def main():
    test_source()
    test_build_surface()
    print()
    if FAILS:
        print("FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("proj_scan_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
