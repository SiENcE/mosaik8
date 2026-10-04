#!/usr/bin/env python3
"""AN OFF-WINDOW ACTOR IS NOT RETIRED WHILE THE VM IS LOCKED (nor mid-move).

Reported from play on the reference-engine sample conversion's block-pushing room
(2026-09-07): the puzzle's second push froze the game. Root-caused on the ROM, not guessed.

`step_all` steps the LIVE list only. `[build] actor_deactivate` retires an
off-window actor OUT of that list (the reference VM's `deactivate_actor_impl`), and the
sample's shove is a 100-tile slide that carries the block clean out of the
camera window while the pushing script waits on its arrival
(`actor_push` + `actor_await_move`). So the block was retired MID-SLIDE, stopped
being stepped, and `a_moving` never cleared:

    push LEFT (long slide) -> px (76, 72)  lock=1 moving=1 active=1 n_live=0

`n_live 0` with `moving 1` is the whole bug. The waiting thread never woke, and
it holds VM_LOCK - so the player could not move, the trigger scan was frozen
and no input script could fire. A dead game, not a dropped frame, and it made
the puzzle (and the staircase behind it) unreachable.

**THE REFERENCE'S OWN RULE IS THE FIX**, and it is in its `actors_update`
(core/actor.c) - the offscreen branch reads:

    if (!VM_ISLOCKED()) { ... deactivate_actor_impl(actor); }
    else { SET_FLAG(actor->flags, ACTOR_FLAG_DISABLED); }

A locked script gets the DRAW flag and nothing more: the actor keeps its place
in every list. Our `place()` already parks the slot, so the faithful conversion
is exactly "park, but do not unlink". The push is an On Interact script, which
locks for its whole run, so the reference never retires the block at all.
Measured: with the lock guard alone the puzzle solves.

The second guard - not retiring a slot with a move IN FLIGHT - is OURS, and it
covers the one place the two engines genuinely differ: the reference VM drives
`vm_actor_move_to` from the SCRIPT instruction (it rewinds its own PC,
`THIS->PC -= INSTRUCTION_SIZE + sizeof(idx)`, and steps `actor->pos` itself),
so its move completes whether or not the actor is in a list, while ours is
driven by `step_all` walking the live list. Without it an UNLOCKED awaited move
- an On Update script's `actor_move` + `actor_await_move` - strands the same
way.

And the mirror of both: the wake scan wakes a parked slot that STARTS moving (a
script moving an already-off-window actor). Waking there rather than in
`move_start` is what keeps it duplicate-free - the wake scan is the one place
that owns the parked -> live transition and only ever visits slots that ARE in
the parked list.

Measured after the fix, same room, same pushes: every slide settles (42
frames, `lock=0 moving=0`) and the puzzle solves end to end - the block
reaches the mark at cell (15,9) and the "Success!" box opens.
Reproducer: a local probe.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def main():
    print("A moving actor is never retired out of the live list")
    print("=" * 55)
    with open(os.path.join(ROOT, "lib", "vm", "actor.mos"),
              encoding="utf-8") as f:
        src = f.read()

    # BOTH scan arms (SCAN_ALL and the phased one) retire, so both need it.
    retires = src.count("unlink(i)\n                            plink(i)")
    check("both retire sites are still there (SCAN_ALL + the phased scan)",
          retires == 2, retires)
    guarded = src.count("if off and locked == 0 and a_moving[i] == 0 {")
    check("...and BOTH carry the lock guard AND the in-flight-move guard",
          guarded == 2, guarded)
    check("the unguarded form is gone",
          "if off {\n                            unlink(i)" not in src)
    # The lock reaches the scan as an ARGUMENT: vm.actor cannot import vm.core
    # (the dependency runs the other way) and a per-frame cross-bank setter
    # call is ~1k cycles, where an argument to a call already being made is
    # free.
    check("both render() arms take the lock",
          src.count("function render(locked: u8) {") == 2)
    with open(os.path.join(ROOT, "lib", "vm", "core.mos"),
              encoding="utf-8") as f:
        core = f.read()
    check("vm.core answers it from vm_lockcount and passes it in",
          "local function lock_flag() -> u8 {" in core
          and core.count("actor.render(lock_flag())") == 2)

    # The mirror: a move started on an already-parked slot has to wake it, or
    # it is the same deadlock reached from the other side.
    # Sliced to the block's own end (`if woke == 1`), never a fixed window: a
    # [:900] drifted off `woke == 0` when a platform fork was added inside.
    wake = src.split("var woke: u8 = 0")[1].split("if woke == 1 {")[0]
    check("the wake scan wakes a PARKED slot that is moving",
          "if a_moving[i] == 1 {" in wake and "punlink(i)" in wake
          and "link(i)" in wake, wake[:200])
    check("...and the window test is skipped once it has woken (no double link)",
          "if b != NO_OAM and woke == 0 {" in wake)

    # The invariant the two guards protect. step_all walking `live` is the
    # reason they are needed at all: if this ever becomes a pool walk, the
    # guards are dead weight rather than load-bearing, and that should be a
    # deliberate edit rather than a silent one.
    step = src.split("local function step_live()")[1]
    step = step[:step.index("}")+1]
    check("step_live still walks the LIVE list (what makes the guards matter)",
          "while k < n_live" in step and "live[k]" in step)

    # `deactivate` is the SCRIPTED retire and stays exact: it cancels the move
    # rather than stranding it, which is why it needs no guard.
    dea = src.split("function deactivate(i: u8) {")[1]
    dea = dea[:dea.index("a_clip[i] = 255")]
    check("the scripted deactivate still CANCELS the move (its own rule)",
          "set_moving(i, 0)" in dea)

    print("")
    if FAILS:
        print("FAILED (%d):" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("moving_actor_never_retired_test: all passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
