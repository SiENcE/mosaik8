"""Actor/player/camera/world ops on the RefVM (moves, fx, projectiles, threads).

Split out of tests/vm_test.py (2026-08-26); run via tests/vm_test.py."""
import os
import sys

from .common import *  # noqa: F401,F403 - FAILS/check/m + shared helpers
from .common import FAILS, _run_expr, check, m
from .common import SPIKE_BLOB, SPIKE_SCRIPTS, ROOT



def test_player_move_to_computed():
    """PLAYER_MOVE_TO_E (0x3C): the waitable player walk to COMPUTED coords.

    The reference engine's EVENT_ACTOR_MOVE_RELATIVE on the player is always computed
    (`player_x() + dx`) and its runtime WALKS it, blocking the script - which
    is what makes the event AFTER it wait. The literal form (0x1E) could not
    serve, so this one LATCHES the popped target on first entry and rewinds
    only opcode + mode.
    """
    print("[PLAYER_MOVE_TO_E: the computed waitable walk]")
    p = m.Compiler().compile([{"name": "main", "events": [
        {"event": "player_move_to", "x": "player_x() + 0",
         "y": "player_y() + 16", "mode": "horizontal"},
        {"event": "set_var", "var": "after", "value": 9},
        {"event": "stop"}]}])
    code = list(p.code)
    check(0x3C in code, "emits PLAYER_MOVE_TO_E for computed coords")
    vm = m.RefVM(p.code, entry=p.entry)
    vm.player_x, vm.player_y, vm.player_speed = 100, 40, 2
    vm.run(3)
    check(vm.player_y > 40 and vm.player_y < 56, "the player is WALKING (mid-step)")
    check(vm.heap[p.variables["after"]] == 0,
          "the event after it has NOT run - the walk blocks, as the reference engine's does")
    vm.run(12)
    check(vm.player_y == 56, "it lands exactly on the target")
    check(vm.heap[p.variables["after"]] == 9, "and then the script continues")

    # LITERAL coords keep the compact 0x1E (byte-identical for every game that
    # was already using it).
    p2 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "player_move_to", "x": 40, "y": 40}, {"event": "stop"}]}])
    c2 = list(p2.code)
    check(0x1E in c2 and 0x3C not in c2,
          "literal coordinates still take the compact PLAYER_MOVE_TO")

    # THE OPCODE TABLE HAS NO DUPLICATES. 0x3C was first assigned 0x38, which
    # is FADE - found only because the fade test broke. A regex over isa.py
    # said 0x38 was free because it expected ONE space after the colon and
    # every aligned row has several, so verify a free byte by IMPORTING the
    # table, never by grepping it.
    from mosaik_vm import isa
    seen, dupes = {}, []
    for name, (op, _ops) in isa.OPS.items():
        if op in seen:
            dupes.append("0x%02X: %s and %s" % (op, seen[op], name))
        seen[op] = name
    check(not dupes,
          "every opcode byte is claimed ONCE%s"
          % ("" if not dupes else " - collisions: " + "; ".join(dupes)))


def test_fade():
    print("[FADE: waitable timing + end level]")
    p = m.Compiler().compile([{"name": "main", "events": [
        {"event": "fade_out", "frames": 4},
        {"event": "set_var", "var": "d", "value": 1},
        {"event": "stop"}]}])
    vm = m.RefVM(p.code, entry=p.entry)
    vm.run(3)
    check(vm.heap[p.variables["d"]] == 0, "fade_out blocks the thread while fading")
    vm.run(1)
    check(vm.heap[p.variables["d"]] == 1 and vm.fade_level == 3,
          "after 4 frames: continues, screen black")
    p2 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "fade_in", "frames": 3}, {"event": "stop"}]}])
    vm2 = m.RefVM(p2.code, entry=p2.entry)
    vm2.run(3)
    check(vm2.fade_level == 0, "fade_in ends at normal (level 0)")


def test_timers():
    print("[TIMER: fires a thread every N frames; stop halts it]")
    p = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "timer_set", "timer": 0, "period": 3, "script": "beep"},
            {"event": "stop"}]},
        {"name": "beep", "events": [
            {"event": "set_var", "var": "n", "expr": "n + 1"}, {"event": "stop"}]}])
    vm = m.RefVM(p.code, entry=p.entry)
    for _ in range(10):
        vm.frame()
    check(vm.heap[p.variables["n"]] == 3, "period-3 timer fired 3x in 10 frames")
    p2 = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "timer_set", "timer": 0, "period": 2, "script": "t"},
            {"event": "wait", "frames": 5},
            {"event": "timer_stop", "timer": 0}, {"event": "stop"}]},
        {"name": "t", "events": [
            {"event": "set_var", "var": "n", "expr": "n + 1"}, {"event": "stop"}]}])
    vm2 = m.RefVM(p2.code, entry=p2.entry)
    for _ in range(20):
        vm2.frame()
    check(vm2.heap[p2.variables["n"]] == 2, "timer_stop halts further fires")


def test_actor_move_nonblocking():
    print("[actor_move: non-blocking native move -- concurrency under a lock]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_activate", "actor": 0, "tile": 0, "x": 0, "y": 0},
        {"event": "actor_activate", "actor": 1, "tile": 0, "x": 100, "y": 0},
        {"event": "actor_set_speed", "actor": 0, "speed": 2},
        {"event": "actor_set_speed", "actor": 1, "speed": 2},
        {"event": "lock"},
        {"event": "actor_move", "actor": 0, "x": 40, "y": 0},   # non-blocking
        {"event": "actor_move", "actor": 1, "x": 40, "y": 0},
        {"event": "wait", "frames": 40},
        {"event": "unlock"},
        {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(6)
    check(vm.lockcount == 1, "cutscene locked")
    check(vm.pos(0) != (0, 0) and vm.pos(1) != (100, 0),
          "both actors move at once while the thread waits (non-blocking)")
    vm.run(40)
    check(vm.pos(0) == (40, 0) and vm.pos(1) == (40, 0),
          "both reach the target (native step_all runs under the lock)")


def test_camera_pan():
    print("[camera_pan: CAM_MOVE_TO scripted pan (waitable)]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "lock"},
        {"event": "camera_pan", "x": 40, "y": 0, "step": 4},
        {"event": "set_var", "var": "done", "value": 1},
        {"event": "unlock"},
        {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(5)
    check(0 < vm.cam_x < 40 and vm.heap[prog.variables["done"]] == 0,
          "pan is in progress, script blocked mid-pan")
    vm.run(20)
    check(vm.cam_x == 40 and vm.cam_lock == 1, "pan arrives at the target + locks")
    check(vm.heap[prog.variables["done"]] == 1, "script continues after the pan")


def test_shake():
    print("[shake: SHAKE counts down over N frames]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "shake", "frames": 10, "amp": 3},
        {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(1)
    check(vm.shk_frames == 9 and vm.shk_amp == 3, "shake started (9 frames left after 1)")
    vm.run(9)
    check(vm.shk_frames == 0, "shake ended after 10 frames")


def test_shmup_scroll():
    print("[shmup_scroll: set the auto-scroll pace / pause]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "shmup_scroll", "pace": 4, "dir": 1},
        {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(2)
    check(vm.shmup_pace == 4 and vm.shmup_dir == 1, "shmup_scroll set pace 4 / dir vertical")
    prog2 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "shmup_scroll", "pace": 0},   # 0 = pause; dir defaults to 2 (keep)
        {"event": "stop"}]}])
    vm2 = m.RefVM(prog2.code, entry=prog2.entry)
    vm2.shmup_dir = 1
    vm2.run(2)
    check(vm2.shmup_pace == 0 and vm2.shmup_dir == 1, "shmup_scroll pace 0 pauses, keeps dir")


def test_scroll_bg():
    print("[scroll_bg: generic signed background scroll velocity]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "scroll_bg", "vx": 2, "vy": -3},
        {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(2)
    check(vm.scroll_vx == 2 and vm.scroll_vy == -3 and vm.scroll_on == 1,
          "scroll_bg set signed velocity (2, -3), active")
    prog2 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "scroll_bg", "vx": 0, "vy": 0},
        {"event": "stop"}]}])
    vm2 = m.RefVM(prog2.code, entry=prog2.entry)
    vm2.run(2)
    check(vm2.scroll_on == 0, "scroll_bg (0,0) stops the scroll")


def test_input_attach():
    print("[input_attach: a button edge spawns a script; detach halts it]")
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "input_attach", "button": "b", "script": "onb"},
            {"event": "stop"}]},
        {"name": "onb", "events": [
            {"event": "set_var", "var": "hits", "expr": "hits + 1"}, {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()
    for _ in range(3):
        vm.frame(held=["b"])
        vm.frame()                    # release between presses
    vm.run(2)
    check(vm.heap[prog.variables["hits"]] == 3, "three B edges -> three spawns")
    vm2 = m.RefVM(prog.code, entry=prog.entry)
    vm2.frame()
    for _ in range(5):
        vm2.frame(held=["b"])         # held, never released
    vm2.run(2)
    check(vm2.heap[prog.variables["hits"]] == 1, "a held button fires once (edge only)")
    prog3 = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "input_attach", "button": "a", "script": "f"},
            {"event": "input_detach", "button": "a"}, {"event": "stop"}]},
        {"name": "f", "events": [
            {"event": "set_var", "var": "n", "expr": "n + 1"}, {"event": "stop"}]}])
    vm3 = m.RefVM(prog3.code, entry=prog3.entry)
    vm3.frame()
    for _ in range(3):
        vm3.frame(a_pressed=True)
        vm3.frame()
    vm3.run(2)
    check(vm3.heap[prog3.variables["n"]] == 0, "input_detach stops the attachment")


def test_projectiles():
    print("[projectile: native flight + life despawn + pool cap]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "projectile", "x": 50, "y": 60, "vx": 4, "vy": 0, "tile": 1, "life": 10},
        {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()
    check(len(vm.proj) == 1 and vm.proj[0]["x"] == 54, "launched + flew one step (x 50->54)")
    vm.run(20)
    check(len(vm.proj) == 0, "despawned after its 10-frame life")
    # negative velocity + pool cap (>8 launches keep 8)
    prog2 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "projectile", "x": 100, "y": 60, "vx": -3, "vy": 2, "tile": 1, "life": 60},
        {"event": "stop"}]}])
    vm2 = m.RefVM(prog2.code, entry=prog2.entry)
    vm2.run(4)
    check(vm2.proj[0]["x"] < 100 and vm2.proj[0]["y"] > 60, "negative i8 velocity flies up-left")
    evs = [{"event": "projectile", "x": 10 * i, "y": 10, "vx": 1, "vy": 0, "tile": 1, "life": 60}
           for i in range(10)] + [{"event": "stop"}]
    prog3 = m.Compiler().compile([{"name": "main", "events": evs}])
    vm3 = m.RefVM(prog3.code, entry=prog3.entry)
    vm3.frame()
    check(len(vm3.proj) == 8, "pool caps at 8 (10 launches, 8 live)")


def test_death_hook():
    # F4: the authored On Death hook -- an On Hit script that retires the struck foe
    # (actor_deactivate self), banks a reward (a heap counter), and fires a WIN branch
    # when the last foe falls. No new opcode: it composes actor_deactivate (F2) + a
    # SELF binding (F3's projectile-hit spawns the script self-bound) + set_var + if.
    print("[F4: authored On Death hook (deactivate self + reward + win)]")
    prog = m.Compiler().compile([{"name": "enemy_hit", "events": [
        {"event": "actor_deactivate", "actor": "self"},
        {"event": "set_var", "var": "kills", "expr": "kills + 1"},
        {"event": "if", "cond": "kills >= foes", "then": [
            {"event": "set_var", "var": "won", "value": 1}]},
        {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    ki, fi, wi = prog.variables["kills"], prog.variables["foes"], prog.variables["won"]
    vm.heap[fi] = 2
    vm.actors[3].active = 1                       # first foe struck: SELF -> actor 3
    vm.set_self(0, 3)
    vm.run(2)
    check(vm.actors[3].active == 0, "the struck foe is retired (actor_deactivate self)")
    check(vm.heap[ki] == 1 and vm.heap[wi] == 0, "reward counted, not yet won")
    vm.actors[4].active = 1                       # last foe struck on a fresh thread
    h = vm._spawn(prog.entry)
    vm.set_self(h, 4)
    vm.run(2)
    check(vm.actors[4].active == 0 and vm.heap[ki] == 2 and vm.heap[wi] == 1,
          "the last foe fires the WIN branch (kills >= foes)")


def test_collision_groups():
    # F3.5: collision groups + masks (the reference engine's model), enemy->player projectiles,
    # and byte-identical opcode selection. The re-fire debounce lives in vm.entity
    # (not modelled by RefVM); it is verified by build + the sample.
    print("[F3.5: collision groups + masks + enemy->player projectiles]")
    # byte-identical: a default-mask shot keeps PROJ_LAUNCH; a mask -> PROJ_LAUNCH_M
    d = m.Compiler().compile([{"name": "main", "events": [
        {"event": "projectile", "x": 10, "y": 10, "vx": 1, "vy": 0}, {"event": "stop"}]}])
    check(0x60 in list(d.code) and 0x62 not in list(d.code),
          "default-mask projectile keeps PROJ_LAUNCH (byte-identical)")
    mk = m.Compiler().compile([{"name": "main", "events": [
        {"event": "projectile", "x": 10, "y": 10, "vx": 1, "vy": 0, "mask": 1},
        {"event": "stop"}]}])
    check(0x62 in list(mk.code), "a non-default mask selects PROJ_LAUNCH_M (0x62)")
    # group/mask filtering: a shot hits an actor only when group & mask
    def _shot_hits(group, mask):
        prog = m.Compiler().compile([{"name": "main", "events": [
            {"event": "actor_activate", "actor": 0, "tile": 0, "x": 50, "y": 50},
            {"event": "actor_set_group", "actor": 0, "group": group},
            {"event": "projectile", "x": 48, "y": 50, "vx": 2, "vy": 0, "mask": mask},
            {"event": "stop"}]}])
        vm = m.RefVM(prog.code, entry=prog.entry)
        hits = []
        vm.proj_hit = lambda s: hits.append(s)
        vm.run(3)
        return hits == [0]
    check(not _shot_hits(4, 2), "faction 4 vs mask 2 = NO hit")
    check(_shot_hits(4, 4), "faction 4 vs mask 4 = hit")
    check(_shot_hits(2, 0xFE), "default enemy group (2) vs default mask (0xFE) = hit")
    # enemy -> player: a player-masked shot fires the registered player-hit script
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "set_state", "state": "player_x", "value": 60},
            {"event": "set_state", "state": "player_y", "value": 60},
            {"event": "set_player_hit", "script": "hurt"},
            {"event": "projectile", "x": 58, "y": 60, "vx": 2, "vy": 0, "mask": 1},
            {"event": "stop"}]},
        {"name": "hurt", "events": [
            {"event": "set_var", "var": "php", "expr": "php - 1"}, {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(6)
    check(len(vm.player_hit_log) == 1 and vm.heap[prog.variables["php"]] == -1,
          "a player-masked shot ran the player-hit script (php -= 1)")


def test_actor_hp():
    # Per-actor HP (multi-HP foes): actor_set_hp seeds it, actor_damage subtracts
    # (clamped), actor_hp(self) reads it, self_slot() reads the bound actor's slot.
    print("[per-actor HP: actor_set_hp / actor_damage / actor_hp() / self_slot()]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_activate", "actor": 0, "tile": 0, "x": 10, "y": 10},
        {"event": "actor_set_hp", "actor": 0, "hp": 3},
        {"event": "set_var", "var": "h0", "expr": "actor_hp(0)"},
        {"event": "actor_damage", "actor": 0, "amount": 1},
        {"event": "actor_damage", "actor": 0, "amount": 1},
        {"event": "set_var", "var": "h1", "expr": "actor_hp(0)"},
        {"event": "actor_damage", "actor": 0, "amount": 5},
        {"event": "set_var", "var": "h2", "expr": "actor_hp(0)"},
        {"event": "stop"}]}])
    check(0x2A in list(prog.code) and 0x2B in list(prog.code), "emits A_SET_HP + A_DAMAGE")
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(2)
    V = prog.variables
    check(vm.heap[V["h0"]] == 3, "actor_set_hp(3) -> actor_hp reads 3")
    check(vm.heap[V["h1"]] == 1, "two 1-damage -> HP 1 (a multi-HP foe survives)")
    check(vm.heap[V["h2"]] == 0, "over-damage clamps HP at 0")
    # self_slot() -> the SELF-bound actor's pool slot (per-actor state indexing)
    p2 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "set_var", "var": "s", "expr": "self_slot()"}, {"event": "stop"}]}])
    vm2 = m.RefVM(p2.code, entry=p2.entry)
    vm2.set_self(0, 5)
    vm2.run(2)
    check(vm2.heap[p2.variables["s"]] == 5, "self_slot() reads the bound actor's slot")



def test_actor_reactivate():
    # The reference engine's EVENT_ACTOR_ACTIVATE takes nothing but the actor: it re-arms
    # the one the scene placed, WHERE IT IS and wearing its own sprite. Routing
    # it through the spawn form (A_ACTIVATE) re-seeded tile + position from
    # bytecode literals, which snapped a respawning actor back to its authored
    # tile and drew it from VRAM base 0 - under per-room residency, the
    # PLAYER's sheet.
    print("[actor_reactivate: respawn in place, keeping art + clip]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_activate", "actor": 0, "tile": 40, "x": 10, "y": 20},
        {"event": "actor_set_clip", "actor": 0, "kind": 3},
        {"event": "actor_deactivate", "actor": 0},
        {"event": "actor_set_pos", "actor": 0, "x": 88, "y": 4},
        {"event": "actor_reactivate", "actor": 0},
        {"event": "stop"}]}])
    check(0x1C in list(prog.code), "emits A_REACTIVATE (0x1C)")
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(2)
    a = vm.actors[0]
    check(a.active == 1, "the retired actor is live again")
    check((a.x, a.y) == (88, 4), "reactivate keeps the position it was moved to")
    check(a.tile == 40, "reactivate keeps the room's VRAM tile base")
    check(a.clip == 3, "reactivate restores the clip deactivate cleared")


def test_script_driven_animation_and_bounce():
    # Stage D of the reference-engine fidelity plan: the four lowerings that closed 23
    # of the conversion's 66 dropped event uses. All are things the reference engine does
    # from a SCRIPT rather than from an animator or a build flag.
    print("[Stage D: actor_set_frame / actor_set_anim_speed / player_set_dir /"
          " player_bounce / is_color()]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_set_frame", "actor": 1, "frame": 3},
        {"event": "actor_set_anim_speed", "actor": 1, "speed": 15},
        {"event": "actor_set_anim_speed", "actor": 2, "speed": 255},
        {"event": "player_set_dir", "dir": 2},
        {"event": "player_bounce", "height": 2},
        {"event": "stop"}]}])
    ops = list(prog.code)
    check(0x2E in ops and 0x2F in ops and 0x17 in ops,
          "emits A_SET_FRAME + A_SET_ANIM_SPEED + PLAYER_BOUNCE")
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(2)
    # The pin is stored +1 so an all-zero BSS pool means "animate normally" --
    # a 255 sentinel would need a boot INITIALIZER, i.e. resident bank-0 image
    # in every VM8 game (vm.actor links everywhere).
    check(vm.actors[1].frame_pin == 4, "actor_set_frame(3) pins frame 3 (stored +1)")
    # the reference engine's `speed` is a MASK, not a period: advance when time & s == 0.
    check(vm.actors[1].anim_speed == 16, "speed mask 15 -> a 16-frame period")
    check(vm.actors[2].anim_speed == 255,
          "mask 255 (never) -> 255 frames, the closest a u8 period holds")
    check(vm.player_dir == 2, "player_set_dir writes the player_dir STATE (no new opcode)")
    check(vm.player_bounce == 2, "player_bounce records its height")

    # is_color() is a RUNTIME state read, not an `if platform` fork: one VM8
    # blob serves the DMG and the CGB, so the branch has to survive into the
    # bytecode. The reference engine's EVENT_IF_COLOR_SUPPORTED tests _is_CGB the same way.
    p2 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "if", "cond": "is_color()",
         "then": [{"event": "set_var", "var": "c", "value": 1}],
         "else": [{"event": "set_var", "var": "c", "value": 9}]},
        {"event": "stop"}]}])
    seen = {}
    for colour in (False, True):
        v = m.RefVM(p2.code, entry=p2.entry)
        v.is_color = colour
        v.run(2)
        seen[colour] = v.heap[p2.variables["c"]]
    check(seen[False] == 9 and seen[True] == 1,
          "one blob takes the DMG arm on a DMG and the colour arm on a CGB")


def test_stop_update_push_seed_clock():
    # Stage D, the second batch: the lowerings that took the reference-engine
    # conversion's dropped event uses from 43 down. Two are new opcodes in the
    # 0x1x group (the 0x2x actor group is full) and two are engine STATES, which
    # is the cheaper answer wherever one fits.
    print("[Stage D: actor_stop_update / actor_push / rng_seed / game_time()]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_activate", "actor": 1, "tile": 0, "x": 40, "y": 40},
        {"event": "actor_set_speed", "actor": 1, "speed": 8},
        {"event": "actor_push", "actor": 1, "tiles": 3},
        {"event": "stop"}]}])
    check(0x19 in list(prog.code), "emits A_PUSH")

    # A push travels along the PLAYER's facing and stops BEFORE the first solid
    # cell -- 3 tiles of room here, but a wall at x >= 64 leaves only 2.
    def run_push(dir_, solid):
        vm = m.RefVM(prog.code, entry=prog.entry)
        vm.player_dir = dir_
        vm.solid_at = solid
        vm.run(12)
        return vm.actors[1]
    a = run_push(3, lambda x, y: x >= 64)
    check((a.tx, a.ty) == (56, 40), "push right stops before the wall (40 -> 56, not 64)")
    check((a.x, a.y) == (56, 40), "the pushed actor SLIDES to its target (native auto-move)")
    a = run_push(3, None)
    check((a.tx, a.ty) == (64, 40), "an open room lets it travel the full 3 tiles")
    a = run_push(2, None)
    check((a.tx, a.ty) == (16, 40), "push LEFT mirrors it")
    a = run_push(1, None)
    check((a.tx, a.ty) == (40, 16), "push UP moves on y (40 -> 16)")
    a = run_push(1, lambda x, y: True)
    check((a.tx, a.ty) == (40, 40), "a wall on the first cell means the actor does not move")

    # actor_stop_update kills the actor's On Update thread and nothing else. The
    # console resolves it through vm.entity's handle; the reference VM kills the
    # thread bound to that actor, which is the same thread.
    p2 = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "wait", "frames": 3},
            {"event": "actor_stop_update", "actor": 2},
            {"event": "stop"}]},
        {"name": "patrol", "loop": True, "events": [
            {"event": "set_var", "var": "ticks", "expr": "ticks + 1"},
            {"event": "idle"}]}])
    check(0x18 in list(p2.code), "emits A_STOP_UPDATE")
    vm = m.RefVM(p2.code, entry=p2.entry)
    uh = vm._spawn(p2.offsets["patrol"])
    vm.set_self(uh, 2)
    for _ in range(3):
        vm.frame()
    before = vm.heap[p2.variables["ticks"]]
    check(before > 0, "the On Update thread runs while it is alive")
    for _ in range(6):
        vm.frame()
    check(vm.heap[p2.variables["ticks"]] == before,
          "actor_stop_update stops it advancing (the thread is gone)")
    check(vm.actors[2].updating == 0, "and the actor is marked not-updating")

    # game_time() is a free-running VM-FRAME clock (the reference engine's sys_time) and
    # rand_seed is a WRITE-only state -- neither needed an opcode. Together they
    # are the rate limit: run the body only once the deadline has passed.
    p3 = m.Compiler().compile([{"name": "main", "loop": True, "events": [
        {"event": "if", "cond": "game_time() - gate >= 0",
         "then": [{"event": "set_var", "var": "gate", "expr": "game_time() + 5"},
                  {"event": "set_var", "var": "runs", "expr": "runs + 1"}]},
        {"event": "idle"}]}])
    vm = m.RefVM(p3.code, entry=p3.entry)
    for _ in range(20):
        vm.frame()
    runs = vm.heap[p3.variables["runs"]]
    check(runs == 4, "a 5-frame rate limit fires 4 times in 20 frames (got %d)" % runs)
    check(vm.game_time == 20, "game_time counts VM frames")

    p4 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "rng_seed"}, {"event": "stop"}]}])
    vm = m.RefVM(p4.code, entry=p4.entry)
    vm.run(2)
    check(0 < vm.rand_seed <= vm.game_time,
          "rng_seed with no argument seeds from game_time (the entropy source)")


def test_actor_emote():
    # The emote bubble (the reference engine's EVENT_ACTOR_EMOTE): one at a time, 60
    # frames, and WAITABLE - the event after it must not run until it clears.
    print("[Stage D: actor_emote (the waitable emote bubble)]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_activate", "actor": 1, "tile": 0, "x": 40, "y": 40},
        {"event": "actor_emote", "actor": 1, "emote": 2},
        {"event": "set_var", "var": "after", "value": 7},
        {"event": "stop"}]}])
    check(0x1A in list(prog.code), "emits A_EMOTE")
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(10)
    check(vm.emote is not None and vm.emote["id"] == 2 and vm.emote["actor"] == 1,
          "the bubble is up, on the right actor, with the right art")
    check(vm.heap[prog.variables["after"]] == 0,
          "the event AFTER the emote has NOT run (it is waitable)")
    vm.run(60)
    check(vm.emote is None, "the bubble clears itself after %d frames" % vm.EMOTE_FRAMES)
    check(vm.heap[prog.variables["after"]] == 7, "and then the script continues")
    check(vm.emote_log == [(1, 2)], "exactly one bubble was started")

    # ...and it may hang over the PLAYER. The reference engine keeps the player at its
    # actors[0], so EVENT_ACTOR_EMOTE aims there as readily as at an NPC; ours
    # is not a pool slot, so it travels as the PLAYER_ACTOR sentinel and must
    # arrive UNRESOLVED - resolve_actor would clamp 0xFF to actor 0 and put the
    # bubble over whoever is standing in slot 0.
    pp = m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_emote", "actor": "player", "emote": 1},
        {"event": "stop"}]}])
    vp = m.RefVM(pp.code, entry=pp.entry)
    vp.run(10)
    check(vp.emote is not None and vp.emote["actor"] == 0xFF,
          "an emote aimed at the player keeps the PLAYER sentinel (got %r)"
          % (vp.emote or {}).get("actor"))
    check(vp.emote["id"] == 1, "and the right art")

    # An `emote` may be a NAME instead of the raw index into [emotes] sheets -
    # the same courtesy `music_song` gets, and for the same reason: the op
    # carries a POSITION, which is not something a human can read or re-order
    # safely. The loader resolves it; an int is left alone, so every reference-engine
    # conversion (which emits indices) stays byte-identical.
    from mosaik_vm.emotes import emote_expand_events
    names = ["shock", "question", "love"]
    got = emote_expand_events(
        [{"event": "actor_emote", "actor": 1, "emote": "love"},
         {"event": "actor_emote", "actor": 2, "emote": 5},
         {"event": "if", "cond": "q", "then": [
             {"event": "actor_emote", "actor": 3, "emote": "question"}]},
         {"event": "actor_emote", "actor": 4, "emote": "nosuch"}], names)
    check(got[0]["emote"] == 2, "a named emote resolves to its sheet position")
    check(got[1]["emote"] == 5, "an INDEX is left alone (conversions emit those)")
    check(got[2]["then"][0]["emote"] == 1, "...and it recurses into if bodies")
    check(got[3]["emote"] == 0, "an unknown name falls back to 0, as a song does")

    # A scene change drops a live bubble: it is anchored to an actor SLOT, and
    # the pool is about to be refilled with the next room's actors.
    p2 = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "start_thread", "script": "emoter"},
            {"event": "wait", "frames": 4},
            {"event": "change_scene", "room": 1, "x": 8, "y": 8}]},
        {"name": "emoter", "events": [
            {"event": "actor_emote", "actor": 1, "emote": 0},
            {"event": "stop"}]}])
    vm = m.RefVM(p2.code, entry=p2.entry)
    vm.run(3)
    check(vm.emote is not None, "the bubble is up before the scene change")
    vm.run(4)
    check(vm.emote is None, "a scene change drops it (the slot is about to be reused)")


def test_say_choose():
    print("[say_choose: say-then-choose = UI_TEXT + MENU]")
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "say_choose", "string": "PICK", "var": "ans", "row": 14,
         "options": ["YES", "NO"]},
        {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()                                   # the text box opens first
    check(vm.box_open == 1 and vm.text_log == [0], "say_choose opens the text box")
    check(not vm.menu_log, "menu not yet open while text is up")
    vm.frame(a_pressed=True)
    vm.frame()                                   # A dismisses text -> menu opens (armed)
    vm.frame(down=True)
    vm.frame(down=False)                         # nav to option 1
    vm.frame(a_pressed=True)
    vm.run(2)
    check(vm.menu_log == [1] and vm.heap[prog.variables["ans"]] == 1,
          "then the menu confirms -> ans = the picked index")


def test_change_scene():
    print("[CHANGE_SCENE: raises the change + aborts the thread]")
    prog = m.Compiler().compile([
        {"name": "main", "events": [{"event": "stop"}]},
        {"name": "door", "events": [
            {"event": "change_scene", "room": 2, "x": 40, "y": 56},
            {"event": "stop"}]},
    ])
    vm = m.RefVM(prog.code, entry=prog.offsets["door"])
    vm.run(2)
    check(vm.change_log == [(2, 40, 56)], "CHANGE_SCENE recorded (room, x, y)")
    check(vm.active[0] == 0, "the door thread aborts after CHANGE_SCENE")


def test_thread_lock_lifecycle():
    print("[P0: thread + lock lifecycle fixes (review 2026-07-12, 1.1-1.5)]")

    # 1.3: `lock ... change_scene` must RELEASE the lock. Otherwise the aborting
    # cutscene thread strands vm_lockcount > 0 and the new room freezes forever.
    prog = m.Compiler().compile([
        {"name": "main", "events": [{"event": "stop"}]},
        {"name": "cut", "events": [
            {"event": "lock"},
            {"event": "change_scene", "room": 1, "x": 0, "y": 0}]},
    ])
    vm = m.RefVM(prog.code, entry=prog.offsets["cut"])
    vm.run(2)
    check(vm.change_log == [(1, 0, 0)], "the cutscene raised the scene change")
    check(vm.lockcount == 0, "lock -> change_scene releases the cutscene lock")

    # 1.3: kill() releases the lock ONLY when the killed thread owned it.
    # (VM8: kill() of an INACTIVE context is a no-op, so activate the fixtures.)
    vm = m.RefVM(prog.code)
    vm.active[2] = vm.active[3] = 1
    vm.lockcount, vm.lockowner = 1, 2
    vm.kill(3)
    check(vm.lockcount == 1, "kill(non-owner) leaves the lock held")
    vm.kill(2)
    check(vm.lockcount == 0, "kill(lock owner) releases the lock")

    # 1.1/1.2: a REUSED context must start with EMPTY expr + call stacks, so a
    # script that died mid-`call` / mid-RPN can't leak a stale return address or
    # value into the next script spawned into that context.
    vm = m.RefVM(prog.code)
    vm.active[0] = vm.active[1] = 1          # force _alloc to hand out ctx 2
    vm.call_stack[2] = [999]                 # stale return addr from a dead script
    vm.stack[2] = [42]                       # stale RPN value
    s = vm._spawn(0)
    check(s == 2 and vm.call_stack[2] == [] and vm.stack[2] == [],
          "spawn clears the reused context's call + expr stacks (no stale RET/value)")

    # 1.4: a pop on an EMPTY expression stack clamps to 0 (vm_sp is u16, so a bare
    # decrement would wrap to 65535 and index out of the stack array on console).
    vm = m.RefVM(prog.code)
    vm.cur = 0
    check(vm._pop() == 0, "vpop on an empty stack clamps to 0 (no underflow)")

    # 1.5: timers FREEZE under a cutscene lock, so a short-period timer during a
    # long cutscene can't keep spawning frozen threads and exhaust the pool.
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "timer_set", "timer": 0, "period": 1, "script": "t"},
            {"event": "lock"},
            {"event": "wait", "frames": 30},
            {"event": "unlock"}, {"event": "stop"}]},
        {"name": "t", "events": [
            {"event": "set_var", "var": "n", "expr": "n + 1"}, {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(10)
    check(vm.lockcount >= 1, "still inside the cutscene lock")
    check(vm.any_active_threads() == 1,
          "no frozen timer threads spawned under the lock (context pool intact)")
    check(vm.heap[prog.variables["n"]] == 0, "the period-1 timer never fired under the lock")
