#!/usr/bin/env python3
"""The platformer BLANK state - the reference engine's pit death.

`PLATFORM_BLANK_STATE` (states/platform.c) is what a pit trigger raises:
`state_enter_blank` zeroes both velocities and ungrounds the player and puts
the sprite in its named HURT set, and `state_update_blank` reads NO pad at
all - it is `plat_vel_y += plat_blank_grav` plus a Y-only collide, and
nothing else. The gravity is a SEPARATE engine field defaulting to 0, which
is the whole timing of the sample's death: the player hangs motionless in the
hurt pose while the sfx plays, the script sets the field and it drops away,
and only then does the respawn teleport run.

We lowered `EVENT_PLATFORMER_STATE_SET` to its CALLBACK alone, so the player
kept normal platform physics - it landed in the pit and stood there in the
idle pose for the callback's four seconds (reported from play with a
screenshot against the reference).

What is pinned here:

  * the two states are 14/15 and CONSECUTIVE, so `set_state` - which is
    bank(0) resident - spends one range test for the pair
  * the arm OWNS the frame and sits above the ladder check (one state at a
    time, the reference engine's FSM)
  * the update reads no pad and applies `bgrav`, not `pgrav`
  * `clear_wide()` clears both, like every other per-ROOM player fact
  * the HURT pose rides the IDLE clip's UP facing (the ladder precedent), and
    the animator falls back to the fall pose when that cell is empty
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
from mosaik_vm import isa  # noqa: E402
from mosaik_vm.refvm import RefVM  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_isa():
    check("player_blank is state 14", isa.STATES.get("player_blank") == 14,
          repr(isa.STATES.get("player_blank")))
    check("player_blank_grav is state 15",
          isa.STATES.get("player_blank_grav") == 15,
          repr(isa.STATES.get("player_blank_grav")))
    check("...and they are CONSECUTIVE, so set_state spends one range test "
          "for the pair rather than two arms in resident image",
          isa.STATES["player_blank_grav"] - isa.STATES["player_blank"] == 1)


def test_lowering():
    blob = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [{"event": "player_blank", "on": 1},
                                    {"event": "player_blank_grav",
                                     "gravity": 2},
                                    {"event": "stop"}]}])
    names = [n for _o, n, _s, _r in isa.iter_instructions(blob.code)]
    check("both events lower to SET_STATE and no new opcode",
          names.count("SET_STATE") == 2 and "STOP" in names, names)


def test_refvm():
    """The RefVM mirrors the pair (the five-in-lockstep rule)."""
    vm = RefVM(b"\x00")
    check("the RefVM defaults are 'not blank, no blank gravity'",
          vm.player_blank == 0 and vm.player_blank_grav == 0)
    vm._set_state(14, 1)
    vm._set_state(15, 3)
    check("...and a write reaches the right one of the two",
          vm.player_blank == 1 and vm.player_blank_grav == 3,
          "%r %r" % (vm.player_blank, vm.player_blank_grav))
    vm._set_state(14, 0)
    check("...and it clears", vm.player_blank == 0)


def test_engine_arm():
    p = _src("lib/vm/player.mos")
    check("vm.player keeps the state and its own gravity",
          "var pblank: u8" in p and "var bgrav: u8" in p)
    check("...both initialiser-free, so they land in BSS and not in the "
          "resident boot initializer",
          "var pblank: u8\n" in p or "var pblank: u8 " in p)
    up = p.split("function update_platform()")[1]
    up = up[:up.index("function ", 10)] if "function " in up[10:] else up
    # the arm owns the frame, and it comes BEFORE the ladder check
    check("the blank arm owns the frame (steps, renders, returns)",
          re.search(r"if pblank == 1 \{\s*blank_step\(\)\s*"
                    r"follow_and_render\(\)\s*return", up) is not None,
          up[:300])
    check("...and it sits ABOVE the ladder check (the reference engine's FSM is in "
          "exactly ONE state)",
          up.index("pblank == 1") < up.index("has_lad == 1"))
    step = p.split("local function blank_step()")[1]
    step = step[:step.index("\n    }")]
    check("blank_step applies the BLANK gravity, not the normal one",
          "bgrav" in step and "pgrav" not in step, step[:200])
    check("...and reads NO pad at all (state_update_blank takes no input)",
          "held(" not in step, step[:200])
    # THE NO-COLLISION HALF, and it is the one that was measured wrong first.
    # Its call is `move_and_collide(COL_CHECK_Y)` while every OTHER state
    # passes COL_CHECK_ALL, and the function opens
    # `if (!(mask & COL_CHECK_WALLS)) goto finally_update_y` - so the blank
    # mask skips the tile tests entirely and the player falls THROUGH the
    # floor and off the map. Colliding here left it standing in the pit,
    # because out-of-bounds is solid on both engines and a pit arrives at the
    # map floor already.
    check("...and does NOT collide - the blank mask has no COL_CHECK_WALLS, "
          "so a pit death falls off the bottom of the map",
          "box_down" not in step and "box_solid" not in step, step[:300])
    check("...clamped rather than wrapped, since a world position is u16",
          "BLANK_FLOOR" in step, step[:300])
    ent = p.split("function set_blank(on: u8)")[1]
    ent = ent[:ent.index("\n    }")]
    check("entering zeroes both velocities and ungrounds (state_enter_blank)",
          "vy = 0" in ent and "hvx = 0" in ent and "grounded = 0" in ent, ent)
    check("...and drops the other frame-owning states, since the FSM can "
          "only be in one", "on_lad = 0" in ent and "kb_frames = 0" in ent)
    clear = p.split("local function clear_wide()")[1]
    clear = clear[:clear.index("\n    }")]
    check("a room load clears the state AND its gravity",
          "pblank = 0" in clear and "bgrav = 0" in clear, clear[:400])


def test_core_arm():
    c = _src("lib/vm/core.mos")
    check("vm.core names the two states", "ST_PLAYER_BLANK = 14" in c
          and "ST_PLAYER_BLANK_GRAV = 15" in c)
    check("...behind ONE flag, so a game with no pit pays nothing",
          "if VM_ST_PLAYER_BLANK {" in c)
    arm = c.split("if VM_ST_PLAYER_BLANK {")[1][:400]
    check("...and the arm routes each id to its own setter",
          "player.set_blank(" in arm and "player.set_blank_grav(" in arm, arm)


def test_offscreen_cull():
    """A u8 OAM coordinate WRAPS, so a player fallen below the screen would
    reappear at the top. Only the blank state can get there, so the guard is
    gated on it and every other game keeps the previous code."""
    p = _src("lib/vm/player.mos")
    pp = p.split("local function put_player")[1]
    pp = pp[:pp.index(chr(10) + "    }")]
    check("put_player parks the sprite once a BLANK fall leaves the screen",
          "pblank == 1" in pp and "SCREEN_HEIGHT" in pp
          and "sprite.move(pbase, 200, 200)" in pp, pp[:400])


def test_refused_kind_is_hidden():
    """A kind the room had no sprite VRAM for keeps base 0 - the PLAYER's
    sheet - so it used to draw the player's tiles as garbage. `a_vis` is
    DRAW-ONLY, so hiding it keeps its position, collision and scripts and
    simply stops rendering it: the graceful failure. (The converted sample's
    health hearts on SMS/GG, whose room wants 156 sprite tiles against a 128
    ceiling - the importer already NAMES the room in its report.)"""
    e = _src("mosaik_vm/rooms/emit_load.py")
    check("the room-load emitter hides a kind it could not upload",
          'ind + "if kvb[fk] == 255 {"' in e
          and 'ind + "    actor.set_visible(slot, 0)"' in e, e[:0])
    # ...and it must come AFTER the activate, or the activate re-arms the slot
    check("...after activating it, so the flag is not cleared again",
          e.index("actor.activate(slot, tb,") < e.index('actor.set_visible(slot, 0)'))
    # the flag really is draw-only (pinned in full by actor_visible_test.py)
    a = _src("lib/vm/actor.mos")
    check("...and set_visible is DRAW-only, so the actor stays interactable",
          "function set_visible(i: u8, on: u8)" in a)


def test_hurt_pose():
    a = _src("lib/vm/canim.mos")
    # The blank flag rides the R2 batched player.anim_flags read (bit 3).
    check("the animator asks vm.player whether the blank state is up",
          "pf & 8 != 0" in a and "pblank == 1" in _src("lib/vm/player.mos"))
    arm = a.split("pf & 8 != 0")[1][:400]
    # the IDLE clip's UP facing - the ladder precedent (climb took walk/UP)
    check("the hurt pose rides the IDLE clip's UP cell, not a fifth state",
          "g_count(p_clip, 0, 1)" in arm and "st = 0" in arm
          and "p_face = 1" in arm, arm)
    check("...and it DEGRADES to the fall pose when that cell is empty "
          "(no art, or the room budget trimmed it)",
          "> 0 {" in arm, arm)
    # RESERVING a cell is two halves: draw it while blank, and keep the
    # ORDINARY draw out of it. `pface` is not only set by the run - a scene's
    # start direction writes it too, and mosaik_anim resolves an EMPTY facing
    # to the first NON-EMPTY one, so carrying art into `up` made it the
    # fallback for `down` as well. The parallax room arrives facing DOWN and
    # drew a player standing still in the hurt pose.
    # to the END of the block, not a fixed window: these arms carry long
    # comments and a magic slice fails a check about code that is there.
    # platform = bit 4, climbing = bit 5 of the R2 batched player.anim_flags.
    guard = a.split("pf & 16 != 0")[1]
    guard = guard[:guard.index("p_astate = st")]
    check("a platform player's IDLE never reaches the reserved cell through "
          "an ordinary facing (both vertical ones map to the horizontal pose)",
          "p_face < 2" in guard and "p_face = 3" in guard, guard[:400])
    check("...except while CLIMBING, whose UP facing is a real pose",
          "pf & 32 == 0" in guard, guard[:400])


def main():
    print("The platformer BLANK state (the reference engine's pit death)")
    print("=" * 50)
    print("[the ISA pair]")
    test_isa()
    test_lowering()
    test_refvm()
    print("[the engine arm]")
    test_engine_arm()
    test_core_arm()
    test_offscreen_cull()
    test_refused_kind_is_hidden()
    print("[the hurt pose]")
    test_hurt_pose()
    print("=" * 50)
    if failed:
        print("%d check(s) FAILED" % failed)
        return 1
    print("All blank-state checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
