#!/usr/bin/env python3
"""Offscreen On Update PARKING (`[build] park_updates`) - the reference engine's model.

`vm_core_run_scripts` was the biggest item in the converted Space Battle's
frame (82,969 cycles, 118% of an LCD frame): 15 actors' On Update scripts ran
every frame whether or not anyone could see them. The reference engine does not do that -
`core/actor.c`'s `actors_update` DEACTIVATES an offscreen actor and terminates
its update script (`script_terminate(actor->hscript_update)`), and the
reactivation path is `scroll.c`: as the camera streams a newly revealed
column/row of background, `activate_actors_in_col/row` walks the inactive list
and `activate_actor_impl` restarts the On Update script FROM THE TOP
(`hscript_update = SCRIPT_TERMINATED; script_execute(...)`).

Ours mirrors that as an OPT-IN, because it changes what a live actor may
assume about its own script (an NPC patrolling off screen freezes where the
camera left it - the reference engine's own semantics, but samples like vm-quest were not
written against it): `[build] park_updates` states the compile-time flag
VM_UPDATE_ALWAYS = false, and `vm.entity` then kills the On Update thread of
any actor whose sprite is PARKED off the visible window (or retired), and
respawns the script from the top - SELF-bound again - when it comes back.
The scan rides `entity.update()`, which only runs UNLOCKED frames, so a
cutscene never has its choreography killed mid-flight (the reference engine's
VM_ISLOCKED guard for free). The parked verdict is last frame's `render()`
decision (`actor.parked_of`), so no camera math is repeated.

Two hazards this test pins the answers to:

  * A STALE HANDLE must never kill a stranger. An On Update that runs once
    and ENDS is a reference-engine idiom; its context goes back to the pool and the
    next spawn reuses it. The park kill therefore goes through
    `core.kill_gen(ctx, gen)` - `spawn()` stamps a per-context GENERATION and
    the kill is a no-op unless the generation still matches the one captured
    at spawn time.
  * A wake whose spawn finds the CONTEXT POOL FULL must retry, not give up:
    en_wpk stays 1 until a spawn succeeds. With a full pool of parked
    updates, contexts are exactly what the parking frees.

Also pinned: the actor-index clamps in vm.core (`resolve_actor`, `set_self`,
OP_SELF) bound against VM_ACTOR_POOL, not a literal 8. The `[build]
actor_pool` knob grows the pool to 15 in the conversion, and the hardcoded
clamp silently redirected every script op on slots 8+ to actor 0 - the RefVM
had the pool-wide clamp all along (`len(self.actors)`), exactly the drift the
five-in-lockstep rule exists for. The wake respawn self-binds slots 8+, so
parking depends on the fix.

Byte-identical off: an absent flag keeps vm.entity's `then` arms, which are
the previous code verbatim - compiled here both ways to prove no park symbol
exists in a default build.
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

import re

from mosaik import MosaikCompiler
from mosaik8_build import MosaikBuilder

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _vm_sources():
    """The real import closure of a small VM8 project (vm-quest), so the test
    compiles the actual lib/vm modules rather than a mock."""
    builder = MosaikBuilder()
    entry = os.path.join(ROOT, "projects", "vm-quest", "src", "main.mos")
    closure = builder._resolve_import_closure(entry,
                                              [os.path.join(ROOT, "lib")])
    assert closure, "vm-quest import closure failed to resolve"
    out = []
    for path in closure:
        with open(path, encoding="utf-8") as f:
            out.append((path, f.read()))
    return out


def _compile(defines=None):
    c = MosaikCompiler().compile_program(_vm_sources(), platform="gameboy",
                                         defines=defines)
    assert not c.startswith("Compilation error:"), c
    return c


def _src(mod):
    return open(os.path.join(ROOT, "lib", "vm", "%s.mos" % mod),
                encoding="utf-8").read()


def test_off_is_byte_identical():
    print("\n[an absent flag compiles no parking at all]")
    c = _compile()
    check("park_scan" not in c and "en_wpk" not in c and "en_update" not in c,
          "default build has no park symbols (byte-identical then arm)")
    check("vm_gen[s] += 1;" in c.replace("vm_core_", ""),
          "the spawn generation stamp is unconditional (hardening: it also "
          "closes the pre-existing stale-handle kill in stop_update/reset)")


def test_on_compiles_the_scan():
    print("\n[the flag compiles the park/wake scan]")
    c = _compile({"VM_UPDATE_ALWAYS": False})
    check("park_scan" in c, "park_scan exists under the flag")
    check("en_wpk" in c and "en_update" in c and "en_ugen" in c,
          "the parking state arrays exist under the flag")
    check("kill_gen" in c, "the park kill goes through kill_gen")


def test_scan_shape():
    print("\n[the scan's load-bearing choices, in the source]")
    src = _src("entity")
    check("if VM_UPDATE_ALWAYS {" in src,
          "the fork is MODULE-level (an absent define keeps the then arm; a "
          "statement-level bare define would emit an undefined C symbol)")
    m = re.search(r"local function park_scan\(\) \{", src)
    check(m is not None, "park_scan is a local of the else arm")
    body = ""
    if m:
        i, depth = m.end(), 1
        while i < len(src) and depth:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        body = src[m.end():i]
    check("core.kill_gen(en_uthread[i], en_ugen[i])" in body,
          "park kills via kill_gen, never a plain kill by stale handle")
    check("var asleep: u8 = 1" in body and "actor.a_active[a] == 1" in body
          and "asleep = actor.a_parked[a]" in body,
          "a RETIRED actor counts as parked (the reference engine's deactivate "
          "terminates hscript_update too)")
    wake = re.search(r"if uh != 255 \{(.*?)\}", body, re.S)
    check(wake is not None and "en_wpk[i] = 0" in wake.group(1),
          "en_wpk clears only on a SUCCESSFUL spawn - a full context pool "
          "retries next frame")
    check("core.set_self(uh, a)" in body,
          "the respawned script is SELF-bound again")
    upd = re.search(r"\} else \{.*?function update\(\) \{\s*park_scan\(\)",
                    src, re.S)
    check(upd is not None,
          "the scan rides update(), which runs UNLOCKED frames only (a "
          "cutscene never has its choreography killed)")


def test_core_gen_stamp():
    print("\n[the spawn generation, in vm.core]")
    src = open(os.path.join(ROOT, "lib", "vm", "core.mos"),
               encoding="utf-8").read()
    check("var vm_gen: array[u8, 8]" in src, "per-context generation array")
    check("vm_gen[s] += 1" in src, "spawn() stamps the new occupant")
    check("function kill_gen(ctx: u8, gen: u8)" in src and
          "if vm_gen[ctx] == gen {" in src,
          "kill_gen kills only while the generation still matches")
    check(re.search(r"export .*\bgen_of\b.*\bkill_gen\b", src) is not None,
          "gen_of / kill_gen are exported")


def test_actor_clamps_follow_the_pool():
    print("\n[actor-index clamps follow [build] actor_pool]")
    src = open(os.path.join(ROOT, "lib", "vm", "core.mos"),
               encoding="utf-8").read()
    check("const VM_ACTORS = VM_ACTOR_POOL" in src,
          "core aliases the pool define")
    check("if i >= VM_ACTORS {" in src,
          "resolve_actor clamps against the pool, not a literal 8")
    check("if actor < VM_ACTORS {" in src,
          "set_self accepts every pool slot (a wake respawn on slot 8+ must "
          "not silently bind SELF to actor 0)")
    check("if a < VM_ACTORS {" in src, "OP_SELF binds every pool slot")
    # and the emitted value really tracks the knob
    c = _compile({"VM_ACTOR_POOL": 15, "VM_UPDATE_ALWAYS": False})
    check("#define VM_CORE_VM_ACTORS (15)" in c.upper().replace(" ", " ")
          or re.search(r"#define \w*VM_ACTORS \(15\)", c) is not None,
          "VM_ACTORS folds to the configured pool size (15)")


def test_actor_scan_off_is_byte_identical():
    print("\n[actor_scan: an absent flag keeps the every-frame test]")
    c = _compile()
    check("scan_tick" not in c and "SCAN_MASK" not in c,
          "default build has no amortisation state (byte-identical then arm)")
    src = _src("actor")
    check("if VM_ACTOR_SCAN_ALL {" in src,
          "the fork is MODULE-level, both arms defining render()")


def test_actor_scan_on():
    print("\n[actor_scan: the amortised arm]")
    c = _compile({"VM_ACTOR_SCAN_ALL": False, "VM_ACTOR_SCAN_MASK": 3})
    check(re.search(r"#define \w*SCAN_MASK \(3\)", c) is not None,
          "the mask folds to N-1 (the reference engine's & 0x3 at N = 4)")
    check("scan_tick += 1" in c, "the frame phase advances once per render")


def test_only_parked_slots_are_amortised():
    print("\n[a VISIBLE actor is always tested - the divergence that matters]")
    src = _src("actor")
    # Anchor on the amortised arm's own const, then take the render() that
    # follows it -- actor.mos has many unrelated `} else {` before the fork.
    anchor = src.find("const SCAN_MASK")
    m = re.search(r"function render\(locked: u8\) \{", src[anchor:]) if anchor >= 0 else None
    body = ""
    if m:
        src = src[anchor:]
        i, depth = m.end(), 1
        while i < len(src) and depth:
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
            i += 1
        body = src[m.end():i]
    check(body != "", "the amortised render() is found")
    # The skip is an early `continue` INSIDE `if a_parked[i] == 1` (it was a
    # `look` flag until 2026-08-31, when the flag turned out to cost ~1,000
    # cycles per skipped slot at sdcc's register cliff - see the note in
    # actor.mos). What matters is unchanged and is what is pinned: the gate is
    # the PARKED test, so a visible slot can never be skipped.
    gate = body.find("if a_parked[i] == 1 {")
    check(gate >= 0 and "& SCAN_MASK) != 0 {" in body[gate:]
          and "continue" in body[gate:gate + 520],   # the u8 phase temp + its note sit between
          "the skip is GATED on the slot being parked, so a visible slot "
          "always runs off_window")
    # This is what the gate buys, and why we do not copy the reference engine's blanket
    # one-in-four: a stale VISIBLE verdict would keep calling place(), which
    # moves the sprite by `wx - cx` - a u8 OAM coordinate that WRAPS back onto
    # the screen. activate() marks a slot visible-until-proven-otherwise, so
    # a room-load actor placed 300 px right of the camera would flash at a
    # wrapped position for the frames before its turn came.
    check("scan_tick + i" in body,
          "the phase keys on the SLOT, not the live-list position (the list "
          "compacts on retire, which would re-phase every survivor)")
    check("base_of(i)" in body and 0 <= gate < body.index("base_of(i)"),
          "a skipped slot costs nothing at all - not even the base_of multiply")


def main():
    print("=" * 50)
    print("Offscreen On Update parking")
    print("=" * 50)
    test_off_is_byte_identical()
    test_on_compiles_the_scan()
    test_scan_shape()
    test_core_gen_stamp()
    test_actor_clamps_follow_the_pool()
    test_actor_scan_off_is_byte_identical()
    test_actor_scan_on()
    test_only_parked_slots_are_amortised()
    print("\n" + "=" * 50)
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        return 1
    print("All offscreen update parking checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
