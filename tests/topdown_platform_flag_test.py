"""player.platform() must mean the PLATFORM handler, never just pmode.

pmode = 1 only says "draw via follow_and_render", and the TOPDOWN setup sets
it too - so keying platform() on pmode made vm.canim's platform-only idle
facing clamp (both vertical facings -> the horizontal pose, the hurt-pose
reservation) fire in topdown rooms: walking up or down in the converted
town room and releasing the pad snapped the player's idle to RIGHT
(reported from play 2026-08-25). The flag is its own byte, set only by
setup_platform (setup_wide runs through it) and cleared per room in
clear_wide.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

passed = 0
failed = 0


def check(name, ok, detail=None):
    global passed, failed
    if ok:
        passed += 1
        print("  ok: %s" % name)
    else:
        failed += 1
        print("  FAIL: %s" % name)
        if detail:
            print("    %s" % str(detail)[:400])


def _src(rel):
    with open(os.path.join(ROOT, rel), "r", encoding="utf-8") as f:
        return f.read()


def test_platform_flag():
    p = _src("lib/vm/player.mos")

    body = p.split("function platform()")[1]
    body = body[:body.index("}")]
    check("platform() reads the dedicated flag, not pmode",
          "pplat" in body and "pmode" not in body, body)

    setup = p.split("function setup(")[1]
    setup = setup[:setup.index("\n    }")]
    check("the TOPDOWN setup never raises it", "pplat = 1" not in setup)

    plat = p.split("function setup_platform(")[1]
    plat = plat[:plat.index("\n    }")]
    check("setup_platform raises it", "pplat = 1" in plat, plat)

    wide = p.split("function setup_wide(")[1]
    wide = wide[:wide.index("\n    }")]
    check("setup_wide inherits it through setup_platform",
          "setup_platform(" in wide, wide)

    cw = p.split("local function clear_wide()")[1]
    cw = cw[:cw.index("\n    }")]
    check("clear_wide clears it per room (every setup runs through it)",
          "pplat = 0" in cw)


def main():
    print("platform() vs pmode (the topdown idle facing clamp)")
    print("=" * 50)
    test_platform_flag()
    print("=" * 50)
    if failed:
        print("%d check(s) FAILED" % failed)
        return 1
    print("All platform-flag checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
