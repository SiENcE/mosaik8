#!/usr/bin/env python3
"""A trigger with NO on-enter script must never be spawned.

The bug this pins (found converting the reference-engine sample, 2026-08-05): an
authored trigger may carry no script -- `scenes.trigger_enter(i)` then returns
the `NO_SCRIPT` sentinel (0xFFFF). `rooms.mos` registered it anyway, and
`vm.trigger.update` spawned it on contact, so the interpreter started
executing at bytecode offset 65535: past the blob, `fetch` returns whatever
the ROM holds there, so the VM ran GARBAGE and raised random exceptions --
including RAISE 2 (change scene). Walking over such a trigger flung the game
into an arbitrary room, which reads as "scenes change at random".

`vm.entity` already guarded its four spawn sites against NO_SCRIPT; the
trigger pack was the one spawn path that did not. Two halves are checked
here:

  * the DATA side  -- the transpiler emits NO_SCRIPT for a scriptless trigger
  * the RUNTIME side -- `vm.trigger.add` drops a NO_SCRIPT entry rather than
    storing it (so a hand-written shell is protected too, and the slot stays
    free for a trigger that actually does something)
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm  # noqa: E402
from mosaik_scenes import transpile  # noqa: E402
from mosaik_assets import write_png_indexed  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


WORLD = {
    "world": {"module": "scenes", "map_w": 4, "map_h": 4, "vm": True},
    "tileset": {"png": "tiles.png"},
    "kinds": {"player": 0},
    "scene": [{"name": "room",
               "map": [[0, 0, 0, 0], [0, 0, 0, 0],
                       [0, 0, 0, 0], [0, 0, 0, 0]]}],
    # One trigger WITH a script and one WITHOUT (the bug's shape).
    "trigger": [{"from": "room", "tx": 1, "ty": 1, "on_enter": "hello"},
                {"from": "room", "tx": 2, "ty": 2}],
}


def test_transpiler_emits_no_script(tmpdir):
    """A trigger with no `on_enter` gets the NO_SCRIPT sentinel, and one with
    a script gets its ENTRY_."""
    pal = [(255, 255, 255), (170, 170, 170), (85, 85, 85), (0, 0, 0)]
    write_png_indexed(os.path.join(tmpdir, "tiles.png"), 8, 8, [0] * 64, pal)
    src = transpile(WORLD, tmpdir)
    body = src[src.index("function trigger_enter"):]
    body = body[:body.index("\n    }")]
    check("scriptless trigger falls through to NO_SCRIPT",
          "return NO_SCRIPT" in body and "ENTRY_hello" in body, body[:200])
    check("NO_SCRIPT is the 0xFFFF sentinel",
          "const NO_SCRIPT: u16 = 0xFFFF" in src)


def test_runtime_drops_no_script():
    """vm.trigger.add ignores a NO_SCRIPT entry (the runtime half)."""
    lib = os.path.join(ROOT, "lib", "vm", "trigger.mos")
    with open(lib, encoding="utf-8") as f:
        mos = f.read()
    check("vm.trigger defines the NO_SCRIPT sentinel",
          "const NO_SCRIPT = 0xFFFF" in mos)
    add = mos[mos.index("function add("):]
    add = add[:add.index("\n    }")]
    check("vm.trigger.add returns early on NO_SCRIPT",
          re.search(r"if\s+entry\s*==\s*NO_SCRIPT\s*\{\s*return", add) is not None,
          add[:200])
    # The guard must come BEFORE the slot is taken, so a scriptless trigger
    # also stops consuming one of the 8 slots.
    check("the guard precedes the slot store",
          add.index("NO_SCRIPT") < add.index("tn < MAXT"))


def test_rooms_still_registers_triggers():
    """The generated rooms.mos still wires real triggers (the guard is at the
    runtime, so the world wiring is unchanged)."""
    src = mosaik_vm.emit_rooms_mos({"types": ["topdown"], "has_triggers": True})
    check("rooms.mos still registers triggers",
          "trigger.add(" in src and "scenes.trigger_enter(i)" in src)


def main():
    import tempfile
    print("Trigger NO_SCRIPT guard")
    print("=" * 50)
    with tempfile.TemporaryDirectory() as tmp:
        test_transpiler_emits_no_script(tmp)
    test_runtime_drops_no_script()
    test_rooms_still_registers_triggers()
    print("=" * 50)
    if failed:
        print("%d FAILED, %d passed" % (failed, passed))
        return 1
    print("All trigger-guard checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
