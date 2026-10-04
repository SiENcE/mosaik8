#!/usr/bin/env python3
"""Everything that renders a WORLD position must read the u16 camera.

`camera.camx` / `camera.camy` are the **hardware scroll registers** and they are
u8. A VM8 actor's or projectile's position is a u16 WORLD pixel. In a narrow
room the two agree, so `p - camera.camx` looks correct forever; in a WIDE
(column-streamed) room the camera passes 255 and wraps, and the subtraction
silently produces a garbage screen coordinate.

`vm.player.cam_x()/cam_y()` is the accessor that answers correctly in both:
it returns the u16 `camx16` in a wide room and the widened hardware register
otherwise. `vm.actor` has always used it. **`vm.projectile` did not**, and
nothing caught it because no sample combined a wide room with projectiles until
the reference-engine conversion kept its 255-wide SHMUP room (2026-08-14):

  * measured on the ROM, every shot rendered at the RIGHT SCREEN EDGE instead
    of at the ship, and the off-screen despawn test - which recomputes the same
    screen position - then killed it within the frame. The player could fire
    and nothing usable came out.
  * after the fix the shot spawns at the ship (x 127 vs the player's 120),
    flies right at its authored vx, and cycles its 2 animation frames.

So this pins the RULE at the source, for every module that renders a world
position. A behavioural pin would need a wide room that also shoots, which no
first-party sample has; the reference-engine sample conversion's Space Battle (local only) is the live
proof
and `vm-combat` / `vm-shmup` / the shooter conversion cover the narrow-room path on ROM.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(cond, msg):
    print(("  [ok] " if cond else "  [FAIL] ") + msg)
    if not cond:
        FAILS.append(msg)


#: Modules that place something at a WORLD position on screen. `vm.player`
#: itself is excluded: it OWNS the accessor and reads the registers to build it.
WORLD_RENDERERS = ("actor", "projectile", "canim", "entity", "emote")


def _strip_comments(src):
    """Drop `--` line comments so a rule quoted in prose is not a hit."""
    return "\n".join(l.split("--", 1)[0] for l in src.splitlines())


def test_no_world_renderer_reads_the_hardware_register():
    print("\n[the u8 hardware register is never the camera]")
    for mod in WORLD_RENDERERS:
        path = os.path.join(ROOT, "lib", "vm", "%s.mos" % mod)
        if not os.path.exists(path):
            continue
        code = _strip_comments(open(path, encoding="utf-8").read())
        hits = re.findall(r"camera\.cam[xy]", code)
        check(not hits,
              "vm.%s does not read camera.camx/camy directly (%d hit(s))"
              % (mod, len(hits)))


def test_the_projectile_uses_the_accessor():
    print("\n[vm.projectile goes through player.cam_x/cam_y]")
    code = open(os.path.join(ROOT, "lib", "vm", "projectile.mos"),
                encoding="utf-8").read()
    body = _strip_comments(code)
    # Both sites: the render, and the off-screen despawn test that has to agree
    # with it (they disagreed for as long as one used the wrong camera).
    check(body.count("player.cam_x()") >= 2,
          "both the render and the despawn test read player.cam_x()")
    check(body.count("player.cam_y()") >= 2,
          "both the render and the despawn test read player.cam_y()")
    check('import "vm.player"' in code,
          "vm.projectile imports vm.player for it")


def test_the_accessor_still_forks_on_wide():
    print("\n[the accessor is what makes it correct]")
    code = _strip_comments(open(os.path.join(ROOT, "lib", "vm", "player.mos"),
                                encoding="utf-8").read())
    for fn in ("cam_x", "cam_y"):
        m = re.search(r"function %s\(\) -> u16 \{(.*?)\n    \}" % fn, code, re.S)
        check(m is not None, "vm.player.%s() exists and returns u16" % fn)
        if m:
            check("wide" in m.group(1) and "cam" in m.group(1),
                  "%s() forks on `wide` (the u16 camera) rather than always "
                  "reading the register" % fn)


def main():
    print("=" * 50)
    print("World-position rendering reads the u16 camera")
    print("=" * 50)
    test_no_world_renderer_reads_the_hardware_register()
    test_the_projectile_uses_the_accessor()
    test_the_accessor_still_forks_on_wide()
    print("\n" + "=" * 50)
    if FAILS:
        print("FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("All wide-camera render checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
