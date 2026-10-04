#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""THE PLAYER KEEPS DRAWING UNDER A CUTSCENE LOCK.

The reference engine's player is actors[0], drawn by `actors_render`, and its
`camera_update` runs beside it - BOTH outside its `!VM_ISLOCKED()` gate; only
`state_update` (input, physics, the trigger scan) freezes. Ours placed the
sprite from the tail of the gated handler, so a locked `PLAYER_MOVE_TO` walked
the position VARIABLE while the sprite stood still.

Found from play the day trigger scripts started taking the reference engine's own lock:
the sample's doorway into the town room
trigger - lock, walk the player 118 px to the door, change scene - read as
"waits, then teleports". Measured on the ROM: px walked 1154 -> 1254 with the
sprite's OAM x frozen at 80 the whole way, and the camera frozen with it.
After: the camera follows to its clamp (SCX 58 -> 104), then the sprite walks
across the screen (OAM x 80 -> 142), which is the reference's picture exactly.

What this test pins (source shape, the wide_cam_lock_test pattern):

  * `vm.core.run` calls the player-draw SEAM (`g_pdraw`, = `vm.player.draw`,
    registered by `core.set_player`) on a LOCKED frame, AFTER `cam_apply`
    (the camera-settles-before-render rule) and gated on `has_player`;
  * `player.draw()` is `follow_and_render` - so a scripted PAN still wins,
    because its `cam_lock` arm places against the camera `cam_apply`
    published instead of following;
  * a SHMUP room stands down (`pmode != 1`): its camera is the gated
    handler's own `sh_cam`, and the reference engine's shmup scroll is inside its gated
    state update too - position and camera freeze TOGETHER there, so the
    frozen picture is the reference's;
  * every setup entry states its mode, and the playerless reset clears it.

Cost: resident span identical both ways (15,872 B on the reference-engine sample
conversion's GB build; the draw body lives in vm.player, which banks under
[build] code_banks).
"""

import re

HERE = os.path.dirname(os.path.abspath(__file__))
PLAYER = open(os.path.join(HERE, '..', 'lib', 'vm', 'player.mos'),
              encoding='utf-8').read()
CORE = open(os.path.join(HERE, '..', 'lib', 'vm', 'core.mos'),
            encoding='utf-8').read()

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def _strip_comments(src):
    """The CODE only - the arms here carry long comments that name the calls
    they explain, and a bare substring search finds the prose first."""
    return chr(10).join(l.split("--")[0] for l in src.splitlines())


def _body(src, name):
    m = re.search(r"function %s\([^)]*\)[^{]*\{" % name, src)
    assert m, name
    depth, i = 1, m.end()
    while depth:
        c = src[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return src[m.start():i]


def main():
    print("=== player_locked_draw_test ===")

    # core.run: the locked-frame draw, after cam_apply, before actor.render
    run = _body(CORE, "run")
    check("core.run draws the player under a lock",
          re.search(r"vm_lockcount > 0 \{[^}]*?has_player == 1 \{\s*"
                    r"g_pdraw\(\)", run, re.S) is not None)
    # ...THROUGH THE SEAM, never as a direct `player.draw()`. A direct call
    # makes follow_and_render's whole chain (put_player, focus_x/y,
    # clamp_bound, room_maxy, calc_wide_cam, camera.axis/follow) reachable
    # from core.run in EVERY project, so a game with no player linked ~1.2 KB
    # it can never run - measured on the falling-block assembly sample, whose
    # Lynx MAIN it pushed 779 B over. set_player is the one registrar, so the
    # pointer and has_player move together.
    check("...through the g_pdraw seam, not a direct player.draw()",
          "player.draw()" not in _strip_comments(run))
    check("core.set_player registers the seam",
          "g_pdraw = player.draw" in _strip_comments(_body(CORE, "set_player")))
    # Match the CALLS, not prose about them: these arms carry long comments
    # (the UI-freeze block above cam_apply explains why it sits where it does
    # and names player.draw() while doing so), and a bare substring search
    # finds the comment first and fails a check about code that is correct.
    ca = run.index("\n            player.cam_apply()")
    dr = run.index("\n                    g_pdraw()")
    rd = run.index("\n            actor.render(lock_flag())")
    check("...AFTER cam_apply and BEFORE actor.render", ca < dr < rd)

    # player.draw: follow_and_render behind the mode gate
    draw = _body(PLAYER, "draw")
    check("player.draw is follow_and_render behind the mode gate",
          "follow_and_render()" in draw and "pmode != 1" in draw)

    # the scripted-pan arm survives: follow_and_render defers to cam_apply's
    # camera when a script pinned it, so the locked draw cannot fight a pan
    far = _body(PLAYER, "follow_and_render")
    check("a scripted pan still wins (the cam_lock arm places, not follows)",
          "if cam_lock == 1 {" in far)

    # every entry point states its mode; the playerless reset clears it
    check("setup (topdown) arms the draw",
          "pmode = 1" in _body(PLAYER, "setup"))
    check("setup_platform arms the draw",
          "pmode = 1" in _body(PLAYER, "setup_platform"))
    check("setup_shmup stands the draw down",
          "pmode = 2" in _body(PLAYER, "setup_shmup"))
    check("setup_wide_shmup stands the draw down",
          "pmode = 2" in _body(PLAYER, "setup_wide_shmup"))
    check("reset_view (playerless room) clears the mode",
          "pmode = 0" in _body(PLAYER, "reset_view"))
    check("pmode is initialiser-free (BSS - vm.player links everywhere)",
          re.search(r"var pmode: u8\s*$", PLAYER, re.M) is not None)

    print("%d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
