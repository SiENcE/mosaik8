#!/usr/bin/env python3
"""The ANGLE pack (vm.trig) + the ops it serves, all five sides in lockstep.

What this pins:

  * `atan2(y, x)` as an RPN token: the ISA entry, the expression spelling, the
    stack effect, and the SAME answers from the reference VM as from
    `lib/vm/trig.mos` (tables included -- a float here and a table there is how
    a "1 degree off" bug hides). The reference engine's units: 0 up, 64 right, 256 to the
    turn, y in SCREEN sense.
  * `PROJ_LAUNCH_A` -- fire along an angle at a 1/16-px-per-frame speed.
  * `A_SET_FRAME_E` -- pin a COMPUTED animation frame (an actor used as a
    digit: `frame = score % 10`).
  * the `player_collide` engine state (actors stop blocking the player).
  * the generated wiring is emitted ONLY when a program uses it, so a game
    that never aims links none of the tables (the byte-identical rule).
  * the vm.canim rule the score display depends on: a FORCED animator re-arm
    (any actor retiring) must NOT release another actor's frame pin.
"""
import math
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik_vm import isa                       # noqa: E402
from mosaik_vm.compiler import Compiler         # noqa: E402
from mosaik_vm.refvm import RefVM               # noqa: E402
from mosaik_vm import emit_rooms_mos            # noqa: E402

FAILS = []


def check(cond, msg):
    if not cond:
        FAILS.append(msg)


def build(events, **kw):
    """Compile one script named `main` and return (program, RefVM)."""
    cc = Compiler()
    prog = cc.compile([{"name": "main", "events": events}])
    return prog, RefVM(prog.code, entry=prog.offsets["main"], **kw)


# -- 1. the ISA entries --------------------------------------------------------
check(isa.RPN.get("ATAN2") == 0x51, "ATAN2 is not RPN token 0x51")
check(isa.OPS.get("PROJ_LAUNCH_A", (None,))[0] == 0x64,
      "PROJ_LAUNCH_A is not opcode 0x64")
check(isa.OPS.get("A_SET_FRAME_E", (None,))[0] == 0x1B,
      "A_SET_FRAME_E is not opcode 0x1B")
check(isa.STATES.get("player_collide") == 11, "player_collide is not state 11")
# a binary op: pops two, pushes one
check(isa._RPN_STACK_EFFECT[0x51] == (0, -1), "ATAN2 has the wrong stack effect")
blob = isa.compile_expr("atan2(1, 2)", Compiler())
check(blob.count(isa.RPN["ATAN2"]) == 1, "atan2() does not compile to the token")


# -- 2. the angles themselves --------------------------------------------------
# Cardinals and diagonals, in the reference engine's units (0 up, clockwise), y DOWN.
for y, x, want in ((-1, 0, 0), (0, 1, 64), (1, 0, 128), (0, -1, 192),
                   (-1, 1, 32), (1, 1, 96), (1, -1, 160), (-1, -1, 224),
                   (0, 0, 0)):
    got = RefVM._atan2(y, x)
    check(got == want, "atan2(%d, %d) = %d, expected %d" % (y, x, got, want))

# The tables in lib/vm/trig.mos ARE the reference tables (not an approximation
# of them): a drift here is a wrong angle on console and a right one in tests.
mos = open(os.path.join(ROOT, "lib", "vm", "trig.mos"), encoding="utf-8").read()


def table(name):
    m = re.search(r"const %s: array\[u8, \d+\] = \[(.*?)\]" % name, mos, re.S)
    return [int(v) for v in m.group(1).replace("\n", "").split(",") if v.strip()]


sin_q, atan_o = table("SIN_Q"), table("ATAN_O")
check(atan_o == RefVM._ATAN_O, "trig.mos ATAN_O has drifted from the RefVM's")
check(sin_q == RefVM._SIN_Q, "trig.mos SIN_Q has drifted from the RefVM's")
check(sin_q == [round(128 * math.sin(i * math.pi / 128)) for i in range(65)],
      "SIN_Q is not sin over a quarter turn in 1/128 units")
check(atan_o == [round(math.atan(i / 32) / (math.pi / 4) * 32) for i in range(33)],
      "ATAN_O is not atan over one octant in angle units")
# every angle survives a round trip through the sine table and back
worst = max(min((RefVM._atan2(-RefVM._sin128((a + 64) & 255), RefVM._sin128(a)) - a) % 256,
                (a - RefVM._atan2(-RefVM._sin128((a + 64) & 255), RefVM._sin128(a))) % 256)
            for a in range(256))
check(worst <= 1, "angle round trip is off by %d units (want <= 1)" % worst)


# -- 3. the ops on the reference VM -------------------------------------------
# An aim computed with atan2, fired as a projectile: 3 px/frame (48 sixteenths)
# straight to the RIGHT.
prog, vm = build([
    {"event": "set_var", "var": "ang", "expr": "atan2(0, 40)"},
    {"event": "projectile", "x": 10, "y": 20, "angle": "ang", "speed": 48,
     "tile": 7, "life": 30, "mask": 2},
    {"event": "stop"},
])
for _ in range(3):
    vm.frame()
check(vm.heap[prog.variables["ang"]] == 64,
      "atan2 to the right should be 64, got %d" % vm.heap[prog.variables["ang"]])
check(len(vm.proj) == 1, "the angle launch fired no projectile")
if vm.proj:
    p = vm.proj[0]
    check((p["vx16"], p["vy16"]) == (48, 0),
          "a 48/16-px shot at angle 64 should be vx16=48 vy16=0, got %r"
          % ((p["vx16"], p["vy16"]),))
    check((p["tile"], p["mask"]) == (7, 2) and p["life"] <= 30,
          "the angle launch lost its tile/life/mask")

# straight UP is the same speed on the other axis, negative (screen y grows down)
_p, vm = build([{"event": "projectile", "x": 0, "y": 0, "angle": 0,
                 "speed": 16, "life": 10}, {"event": "stop"}])
vm.frame()
check(vm.proj and (vm.proj[0]["vx16"], vm.proj[0]["vy16"]) == (0, -16),
      "angle 0 should fire straight up at 16/16 px, got %r"
      % (vm.proj and (vm.proj[0]["vx16"], vm.proj[0]["vy16"]),))

# A LITERAL angle still takes the same op (it is always the expression form),
# and an event with NO angle keeps the compact velocity opcode - byte-identical
# for every project that never aims.
plain = Compiler().compile([{"name": "main", "events": [
    {"event": "projectile", "x": 1, "y": 2, "vx": 3, "vy": 0}, {"event": "stop"}]}])
check(isa.OPS["PROJ_LAUNCH_A"][0] not in plain.code,
      "a vx/vy projectile emitted the ANGLE opcode")

# A_SET_FRAME_E: the frame is an expression (an actor used as a digit).
prog, vm = build([
    {"event": "set_var", "var": "score", "value": 7},
    {"event": "actor_set_frame", "actor": 3, "frame": "score % 10"},
    {"event": "stop"},
])
for _ in range(3):
    vm.frame()
check(vm.actors[3].frame_pin == 8,        # stored +1, so 0 can mean "unpinned"
      "a computed frame pin should be 7 (+1), got %d" % vm.actors[3].frame_pin)
lit = Compiler().compile([{"name": "main", "events": [
    {"event": "actor_set_frame", "actor": 3, "frame": 7}, {"event": "stop"}]}])
check(isa.OPS["A_SET_FRAME_E"][0] not in lit.code,
      "a LITERAL frame should keep the compact A_SET_FRAME (byte-identical)")

# player_collide: the state a reference-engine EVENT_ACTOR_COLLISIONS_DISABLE aimed at
# the player lowers to.
_p, vm = build([{"event": "player_set_collision", "on": False}, {"event": "stop"}])
for _ in range(2):
    vm.frame()
check(vm.player_collide == 0, "player_set_collision off did not clear the state")

# player_speed / player_anim_speed: the states a reference-engine
# EVENT_ACTOR_SET_MOVEMENT_SPEED / _SET_ANIMATION_SPEED aimed at the PLAYER
# lowers to. Its vm_actor_set_move_speed / vm_actor_set_anim_tick write the
# field on whichever actor they are handed, player included (no PLAYER fork,
# unlike deactivate), and our actor OPS take a pool slot the player has none of.
_p, vm = build([{"event": "player_set_speed", "speed": 4},
                {"event": "player_set_anim_speed", "speed": 31},
                {"event": "stop"}])
for _ in range(3):
    vm.frame()
check(vm.player_speed == 4, "player_set_speed did not reach the state")
check(vm.player_anim_speed == 31, "player_set_anim_speed did not reach the state")

# ...and the speed a script sets is the one a scripted WALK steps by, which is
# the whole point of writing vm.player's own pspeed rather than a side field.
_p, vm = build([{"event": "player_set_speed", "speed": 8},
                {"event": "player_move_to", "x": 40, "y": 0},
                {"event": "stop"}])
vm.player_x, vm.player_y = 0, 0
for _ in range(40):
    vm.frame()
check(vm.player_x == 40,
      "a scripted walk did not land after a scripted speed change (x=%r)"
      % vm.player_x)

# A speed of 0 is a player that can never move; the state clamps it, as
# vm.player.set_speed does on the console.
_p, vm = build([{"event": "player_set_speed", "speed": 0}, {"event": "stop"}])
for _ in range(2):
    vm.frame()
check(vm.player_speed == 1, "player_set_speed 0 was not clamped to 1")


# -- 4. the console arms + the generated wiring --------------------------------
core = open(os.path.join(ROOT, "lib", "vm", "core.mos"), encoding="utf-8").read()
for flag in ("VM_RPN_ATAN2", "VM_OP_PROJ_LAUNCH_A", "VM_OP_A_SET_FRAME_E"):
    check(flag in core, "core.mos has no %s guard (dispatch pruning)" % flag)
check("ST_PLAYER_COLLIDE = 11" in core, "core.mos ST_PLAYER_COLLIDE drifted")
check("player.set_actor_collide" in core,
      "core.mos does not route player_collide into vm.player")
check("ST_PLAYER_SPEED = 12" in core, "core.mos ST_PLAYER_SPEED drifted")
check("ST_PLAYER_ANIM_SPEED = 13" in core,
      "core.mos ST_PLAYER_ANIM_SPEED drifted")
check("player.set_speed" in core and "player.set_anim_speed" in core,
      "core.mos does not route the player speed states into vm.player")
check("PLAYER_ACTOR = 0xFF" in core, "core.mos PLAYER_ACTOR drifted")
check(re.search(r"if raw != PLAYER_ACTOR \{\s*\n\s*i = resolve_actor\(raw\)",
                core),
      "A_EMOTE must NOT resolve the PLAYER sentinel - resolve_actor clamps an "
      "out-of-pool id to 0, which hangs the bubble over whoever is in slot 0")
emote_mos = open(os.path.join(ROOT, "lib", "vm", "emote.mos"),
                 encoding="utf-8").read()
check("PLAYER_EMOTE = 0xFF" in emote_mos, "vm.emote has no PLAYER sentinel")
check("player.screen_x()" in emote_mos and "player.screen_y()" in emote_mos,
      "the player's bubble must anchor to the position the player was DRAWN "
      "at (vm.player's screen_*), not to a world position it converts itself "
      "- put_player is handed already-converted coordinates, so a second "
      "converter using cam_x/cam_y disagrees with it (measured: cam_y "
      "answered 65520 while the player drew as if the camera were 0, which "
      "cancelled the lift exactly and put the bubble on the player's head)")
player_mos = open(os.path.join(ROOT, "lib", "vm", "player.mos"),
                  encoding="utf-8").read()
check("var p_aspd: u8\n" in player_mos,
      "vm.player's animation mask must be initialiser-free (BSS), or it costs "
      "resident bank-0 image in every VM8 game")

# The pin-release rule the score display depends on (see the canim comment): a
# forced re-arm must not clear another actor's pin.
canim = open(os.path.join(ROOT, "lib", "vm", "canim.mos"), encoding="utf-8").read()
guard = re.search(r"if a_pstate\[i\] != 255 \{\s*\n\s*actor\.clear_frame\(i\)", canim)
check(bool(guard),
      "canim releases the frame pin on a FORCED re-arm - one actor retiring "
      "then clears every other actor's script-set frame")
check("function set_player_h" in emote_mos and "e_plh" in emote_mos,
      "the PLAYER's emote lift is told to vm.emote per ROOM LOAD - neither "
      "vm.player's fan (1x1 under external animation) nor vm.canim's per-kind "
      "selectors (m_h = 1 without set_clip_size) answer it, and an 8 px lift "
      "leaves the bubble inside the player's head")
check("if i == 255" not in canim,
      "...so canim.actor_px_h stays purely about ACTORS (the player sentinel "
      "would only re-introduce the wrong answer)")
# The speed rides the R2 batched player.anim_flags read (its high byte).
check(re.search(r"var pper: u8 = pf >> 8.*\n\s*if pper == 0 \{",
                canim),
      "canim's tick_player must prefer a SCRIPT-set animation speed over the "
      "clip's authored period, with 0 meaning 'never set' (the same shape the "
      "actor arm uses for actor.anim_speed)")

src = emit_rooms_mos({"types": ["topdown"], "uses_projectile": True,
                      "uses_proj_angle": True, "uses_atan2": True})
check("core.set_atan2(trig.atan2)" in src, "rooms.mos does not wire atan2")
check("core.set_proj_angle(projectile.launch_angle)" in src,
      "rooms.mos does not wire the angle launcher")
check('import "vm.trig"' in src, "rooms.mos does not import vm.trig")
off = emit_rooms_mos({"types": ["topdown"]})
check("vm.trig" not in off and "set_atan2" not in off,
      "rooms.mos wires vm.trig into a game that never aims (not byte-identical)")


if FAILS:
    print("\n".join("  FAILED: %s" % f for f in FAILS))
    print("\n%d check(s) FAILED" % len(FAILS))
    sys.exit(1)
print("vm.trig: atan2 + angle launch + computed frame pin + player_collide OK")
