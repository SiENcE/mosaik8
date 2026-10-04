#!/usr/bin/env python3
"""The POINT-AND-CLICK scene type (W7j) - the reference VM's `src/states/pointnclick.c`.

Five things, each of which fails differently if it drifts:

  * the scene-type id is APPENDED, so every existing world's SCENE_TYPE table
    keeps its numbering;
  * `vm.player`'s cursor move is eight directions with a normalised diagonal,
    NO collision at all, and a clamp to the MAP;
  * `vm.trigger`'s cursor query spawns nothing (the reference calls
    `trigger_at_intersection`, never `trigger_activate_at_intersection`);
  * the generated tick composes the hover pose + the click, and deliberately
    omits the automatic trigger scan;
  * every arm of it is off for a world that has no such scene, which
    `_vm_dispatch_defines` states and the emitters honour.

    python tests/pointnclick_test.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mosaik8_build                                             # noqa: E402
from mosaik_vm.rooms import emit_rooms_mos                       # noqa: E402

FAILS = []


def check(ok, what):
    print("  %s: %s" % ("ok" if ok else "FAIL", what))
    if not ok:
        FAILS.append(what)


def _src(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _exports(src):
    """Every `export` statement in a module. NOT `split("export")[-1]`: a
    module can have several, and taking only the last one reads as "not
    exported" the moment a new line is appended below the old one (which is
    exactly what this stage did to `trigger_snap_test`)."""
    return [ln for ln in src.splitlines() if ln.strip().startswith("export ")]


def _body(src, sig):
    """The text of the function whose signature line is `sig`, to its closing
    brace at the same indent."""
    i = src.index(sig)
    j = src.index("\n    }\n", i)
    return src[i:j]


def main():
    print("[the scene-type id is APPENDED]")
    from mosaik_scenes.transpile import context
    import re
    types = re.search(r"SCENE_TYPES = \[(.*?)\]", _src("mosaik_scenes",
                                                       "transpile",
                                                       "context.py"),
                      re.S).group(1)
    types = [t.strip().strip('"') for t in types.replace("\n", " ").split(",")
             if t.strip()]
    check(types[:6] == ["topdown", "platform", "adventure", "shmup", "logo",
                        "menu"],
          "the first six ids are untouched, so no existing world renumbers")
    check(types[6] == "pointnclick", "...and pointnclick is id 6")
    del context

    print("\n[vm.player: the cursor move]")
    pl = _src("lib", "vm", "player.mos")
    check("function update_pointnclick()" in pl,
          "vm.player has the cursor handler")
    ex = _exports(pl)
    check(all(any(n in ln for ln in ex)
              for n in ("set_cursor", "cursor", "update_pointnclick")),
          "...and exports it with set_cursor / cursor")
    upd = _body(pl, "    function update_pointnclick()")
    check("q = 3" in upd and "var q: u16 = 4" in upd,
          "a diagonal steps 3 quarter-pixels per axis against a cardinal's 4")
    check("box_taken" not in upd and "box_solid" not in upd
          and "actor_at" not in upd,
          "NO collision test of any kind - a cursor floats over the scenery")
    check("pnc_mw" in upd and "pnc_mh" in upd,
          "...and the clamp is the MAP's own size")
    check("px = 0" in upd and "py = 0" in upd,
          "...clamped at 0 on the decreasing side, never wrapped (u16)")
    check("follow_and_render()" in upd,
          "it ends in the shared camera + draw, like every other handler")
    cw = _body(pl, "    local function clear_wide()")
    check("if VM_NO_CURSOR {" in cw and "pnc = 0" in cw,
          "clear_wide drops the cursor flag, behind the build flag")

    print("\n[vm.canim: the clip STATE is pinned, the facing is not]")
    ca = _src("lib", "vm", "canim.mos")
    i = ca.index("player.cursor() == 1")
    arm = ca[ca.rindex("if VM_NO_CURSOR {", 0, i):ca.index("p_astate = st", i)]
    check("st = 0" in arm,
          "a cursor room pins the drawn state at idle (the reference never "
          "sets a moving animation there)")
    check(ca.index("p_astate = st") > i,
          "...and it runs AFTER the airborne / blank / platform overrides")

    print("\n[vm.trigger: a QUERY, and a click that is not an entry]")
    tr = _src("lib", "vm", "trigger.mos")
    hs = _body(tr, "    function hit_script(")
    check("core.spawn" not in hs, "hit_script spawns nothing")
    check("last" not in hs, "...and does not touch the enter/leave latch")
    check("tentry[h] == NO_SCRIPT" in hs,
          "...first overlap, THEN the has-script test (the reference's order)")
    it = _body(tr, "    function interact(")
    check("core.spawn(tentry[i])" in it, "interact runs the ENTER script")
    ex = _exports(tr)
    check(all(any(n in ln for ln in ex) for n in ("hit_script", "interact")),
          "both are exported")

    print("\n[vm.entity: the probe is an OVERLAP in a cursor room]")
    en = _src("lib", "vm", "entity.mos")
    fi = _body(en, "    local function find()")
    check("if ic_cursor == 1 {" in fi and "px = bx" in fi and "py = by" in fi,
          "find() drops the facing reach in cursor mode")
    check("if VM_NO_CURSOR {\n        } else {" in fi
          or "if VM_NO_CURSOR {" in fi,
          "...behind the build flag, so the then-arm is today's code")
    check("ic_cursor = 0" in _body(en, "    function reset()"),
          "the mode is per ROOM (reset drops it)")
    check(any("set_cursor" in ln for ln in _exports(en)),
          "set_cursor is exported")

    print("\n[rooms.mos: OFF is byte-identical]")
    off = emit_rooms_mos({"types": ["topdown"], "has_triggers": True})
    check("set_cursor" not in off and "tick_pointnclick" not in off,
          "a world with no pointnclick scene emits none of it")
    check("set_cam_prop" not in off,
          "...and no camera-property write, so VM_CAM_FOLLOW_OPTS stays off")

    print("\n[rooms.mos: the arm and the tick]")
    # pointnclick FIRST, so it takes the `if` arm and names its own id - the
    # last branch of the dispatch is always a bare `else`.
    on = emit_rooms_mos({"types": ["pointnclick", "topdown"],
                         "has_triggers": True, "has_entity": True})
    check("scenes.SCTYPE_POINTNCLICK" in on, "the dispatch forks on the id")
    check("player.set_cursor(mw, mh)" in on,
          "the arm hands vm.player the room size")
    for which, val in ((0, 24), (1, 24), (2, 0), (3, 0)):
        check("player.set_cam_prop(%d, %d)" % (which, val) in on,
              "...and writes camera property %d = %d "
              "(POINT_N_CLICK_CAMERA_DEADZONE)" % (which, val))
    check("entity.set_cursor(1)" in on, "...and switches vm.entity's probe")
    tick = on[on.index("function tick_pointnclick()"):]
    tick = tick[:tick.index("\n    }\n")]
    check("player.update_pointnclick()" in tick, "the tick runs the cursor move")
    check("trigger.update(" not in tick,
          "NO automatic trigger scan - a rect fires on A, never on contact")
    check("trigger.hit_script(" in tick, "...it QUERIES the rect instead")
    check("player.set_face(3)" in tick and "player.set_face(0)" in tick,
          "hover publishes facing 3 (ANIM_CURSOR_HOVER), idle facing 0")
    check("entity.update()" in tick,
          "the ACTOR click is entity.update()'s own interact, in cursor mode")
    check("if ah == 0 and ct != 255 {" in tick,
          "...and a trigger click is skipped when an actor took the press "
          "(the reference's `else if`)")
    check("core.interact_pressed() == 1" in tick, "...on the A edge")
    # ...and a room too big to paint ROAMS, because the arm it shares is the
    # topdown one (setup_roam included) - it must not be refused or cropped.
    roam = emit_rooms_mos({"types": ["pointnclick"], "roam_rooms": True,
                           "has_triggers": True})
    i = roam.index("player.set_cursor(")
    arm = roam[roam.index("player.set_camera("):i]
    check("player.setup_roam(" in arm,
          "a wide/tall cursor room streams BOTH axes, like a topdown one")
    check(roam.index("player.set_cursor(") > roam.index("player.setup_roam("),
          "...and set_cursor comes AFTER the setup, which clears the flag")

    # the topdown arm of the SAME world must be untouched
    plain = emit_rooms_mos({"types": ["topdown"], "has_triggers": True,
                            "has_entity": True})
    td = plain[plain.index("function tick_topdown()"):]
    td = td[:td.index("\n    }\n")]
    check("trigger.update(" in td,
          "a topdown room in the same world keeps its contact scan")

    print("\n[the build flags]")
    d = mosaik8_build._vm_dispatch_defines(
        [("scripts.mos", "const CODE: array[u8, 1] = [ 0 ]\n"),
         ("rooms.mos", on)])
    check(d.get("VM_NO_CURSOR") is False,
          "a rooms.mos calling set_cursor states VM_NO_CURSOR false")
    check(d.get("VM_CAM_FOLLOW_OPTS") is True,
          "...and its set_cam_prop call turns the follow options ON, which "
          "the blob scan alone cannot see")
    d = mosaik8_build._vm_dispatch_defines(
        [("scripts.mos", "const CODE: array[u8, 1] = [ 0 ]\n"),
         ("rooms.mos", off)])
    check(d.get("VM_NO_CURSOR") is True,
          "a world without one states it true (= today's code)")
    check(d.get("VM_CAM_FOLLOW_OPTS") is False,
          "...and leaves the plain follow path alone")

    print("\n%s" % ("FAILED: %d" % len(FAILS) if FAILS else "all ok"))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
