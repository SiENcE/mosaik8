"""Per-opcode SEMANTIC tests for the opcodes the RefVM suite only smoke-ran
(review V-11, 2026-09-06). `test_every_opcode_executes` proves an opcode does
not crash; these prove each one does what the ISA says, through the event
lowering that emits it, so a regression in either the compiler or the
reference interpreter fails here. One function per opcode group; RPN tokens
NE / LE / ACTOR_MOVING / ATAN2 at the end.

The first thing this found: PROJ_GROUP (0x36) had NO RefVM handler - it fell
through to the unknown-opcode END and killed the thread that set a group.
"""
import os

from vmtest.common import ROOT, check, m


def _vm(scripts, frames=2, **kw):
    prog = m.Compiler().compile(scripts)
    vm = m.RefVM(prog.code, entry=prog.entry)
    for k, v in kw.items():
        setattr(vm, k, v)
    vm.run(frames)
    return prog, vm


def _main(events):
    return [{"name": "main", "events": events + [{"event": "stop"}]}]


def test_op_actor_await_move():
    print("[A_AWAIT_MOVE: the waitable form of a non-blocking move]")
    prog = m.Compiler().compile(_main([
        {"event": "actor_activate", "actor": 0, "tile": 0, "x": 0, "y": 0},
        {"event": "actor_set_speed", "actor": 0, "speed": 2},
        {"event": "actor_move", "actor": 0, "x": 8, "y": 0},
        {"event": "actor_await_move", "actor": 0},
        {"event": "set_var", "var": "done", "value": 1}]))
    check(0x1D in list(prog.code), "emits A_AWAIT_MOVE (0x1D)")
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(1)
    d = prog.variables["done"]
    check(vm.actors[0].moving == 1 and vm.heap[d] == 0,
          "the thread waits while the auto-move is in flight")
    vm.run(8)
    check(vm.pos(0) == (8, 0) and vm.heap[d] == 1,
          "...and continues once the actor has landed")


def test_op_actor_move_opts():
    print("[A_MOVE_OPTS: the one-shot move-options latch]")
    prog, vm = _vm(_main([
        {"event": "actor_activate", "actor": 1, "tile": 0, "x": 0, "y": 0},
        {"event": "actor_move", "actor": 1, "x": 16, "y": 16,
         "mode": "horizontal", "collide_with": ["walls"]}]), frames=1)
    check(0x1F in list(prog.code), "emits A_MOVE_OPTS (0x1F) before the move")
    a = vm.actors[1]
    check(a.mopt == (1 | 4), "the move in flight carries axis order 1 + stop-at-walls (got %d)" % a.mopt)
    check(vm.pend_mopt == 0, "the latch is consumed by the launch (one shot)")
    prog2, vm2 = _vm(_main([
        {"event": "actor_activate", "actor": 1, "tile": 0, "x": 0, "y": 0},
        {"event": "actor_move", "actor": 1, "x": 16, "y": 16}]), frames=1)
    check(0x1F not in list(prog2.code), "a default move emits no latch (byte-identical)")


def test_op_actor_set_anim_state():
    print("[A_SET_ANIM_STATE: state in the low seven bits, play-once in bit 7]")
    prog, vm = _vm(_main([
        {"event": "actor_set_anim_state", "actor": 2, "state": 3},
        {"event": "actor_set_anim_state", "actor": 4, "state": 2, "loop": False}]))
    check(0x37 in list(prog.code), "emits A_SET_ANIM_STATE (0x37)")
    check(vm.actor_anim_state.get(2) == (3, False), "state 3, looping")
    check(vm.actor_anim_state.get(4) == (2, True), "state 2 with loop=false plays once (bit 7)")


def test_op_actor_clear_anim_state():
    print("[A_CLEAR_ANIM_STATE: release the pin, and only that actor's]")
    prog, vm = _vm(_main([
        {"event": "actor_set_anim_state", "actor": 2, "state": 3},
        {"event": "actor_set_anim_state", "actor": 4, "state": 2},
        {"event": "actor_clear_anim_state", "actor": 2}]))
    check(bytes([0x5A, 2]) in bytes(prog.code), "emits A_CLEAR_ANIM_STATE (0x5A) actor 2")
    check(2 not in vm.actor_anim_state, "actor 2 is back to its movement-derived state")
    check(vm.actor_anim_state.get(4) == (2, False), "actor 4 keeps its pin")
    # the console half: the arm releases through the ONE unpin writer
    core = open(os.path.join(ROOT, "lib", "vm", "core.mos"), encoding="utf-8").read()
    arm = core[core.index("case OP_A_CLEAR_ANIM_STATE"):]
    arm = arm[:arm.index("return ST_CONT")]
    check("actor.clear_anim_state(resolve_actor(f8()))" in arm,
          "core's arm calls actor.clear_anim_state on the resolved slot")
    actor = open(os.path.join(ROOT, "lib", "vm", "actor.mos"), encoding="utf-8").read()
    body = actor[actor.index("function clear_anim_state"):]
    body = body[:body.index("}")]
    check("a_spin[i] = 0" in body and "a_moved[i] |= 2" in body,
          "clear_anim_state zeroes the pin AND raises the animator edge (else a "
          "QUIET slot would never re-derive)")


def test_op_actor_set_collision_and_dir():
    print("[A_SET_COLLISION / A_SET_DIR]")
    prog, vm = _vm(_main([
        {"event": "actor_set_collision", "actor": 0, "on": False},
        {"event": "actor_set_dir", "actor": 1, "dir": 3},
        {"event": "actor_set_dir", "actor": 2, "dir": 5}]))
    ops = list(prog.code)
    check(0x2D in ops and 0x2C in ops, "emits A_SET_COLLISION (0x2D) + A_SET_DIR (0x2C)")
    check(vm.actors[0].solid == 0 and vm.actors[1].solid == 1,
          "collision off clears solid on that actor only")
    check(vm.actors[1].dir == 3, "dir 3 (right)")
    check(vm.actors[2].dir == 1, "dir is masked to 0..3 (5 -> 1)")


def test_op_actor_set_frame_e_and_pos_e():
    print("[A_SET_FRAME_E / A_SET_POS_E: the computed forms pop the stack]")
    prog, vm = _vm(_main([
        {"event": "set_var", "var": "v", "value": 50},
        {"event": "actor_set_frame", "actor": 0, "frame": "v / 10"},
        {"event": "actor_set_frame", "actor": 1, "frame": "v * 10"},
        {"event": "actor_set_pos", "actor": 2, "x": "v * 2", "y": "v + 1"}]))
    ops = list(prog.code)
    check(0x1B in ops and 0x26 in ops, "emits A_SET_FRAME_E (0x1B) + A_SET_POS_E (0x26)")
    check(vm.actors[0].frame_pin == 6, "frame 5 pins as 5 + 1")
    check(vm.actors[1].frame_pin == 255, "a computed 500 clamps to 254 (stored 255)")
    check(vm.pos(2) == (100, 51), "x then y are popped in push order")


def test_op_actor_visible_and_player_visible():
    print("[A_VISIBLE / PLAYER_VISIBLE: a DRAW flag, not a retire]")
    prog, vm = _vm(_main([
        {"event": "actor_activate", "actor": 3, "tile": 0, "x": 8, "y": 8},
        {"event": "actor_visible", "actor": 3, "on": False},
        {"event": "player_visible", "on": False}]))
    ops = list(prog.code)
    check(0x3D in ops and 0x34 in ops, "emits A_VISIBLE (0x3D) + PLAYER_VISIBLE (0x34)")
    a = vm.actors[3]
    check(a.visible == 0 and a.active == 1, "a hidden actor stays ACTIVE (only the draw stops)")
    check(vm.player_hidden == 1, "the player's flag is HIDDEN for operand 0")
    _, vm2 = _vm(_main([{"event": "player_visible", "on": True}]))
    check(vm2.player_hidden == 0, "...and clear for operand 1")


def test_op_bkg_tile():
    print("[BKG_TILE / BKG_TILE_E: a tile-DATA write]")
    prog, vm = _vm(_main([
        {"event": "set_var", "var": "g", "value": 37},
        {"event": "bkg_tile", "tile": 5, "src": 9},
        {"event": "bkg_tile", "tile": 6, "src": "g % 10"}]))
    ops = list(prog.code)
    check(0x4D in ops and 0x4E in ops, "a literal src is BKG_TILE (0x4D), a computed one BKG_TILE_E (0x4E)")
    check(vm.bkg_tiles.get(5) == 9, "literal: tile 5 <- 9")
    check(vm.bkg_tiles.get(6) == 7, "computed: tile 6 <- 37 % 10")


def test_op_change_scene_e():
    print("[CHANGE_SCENE_E: room and coordinates off the stack]")
    prog, vm = _vm(_main([
        {"event": "set_var", "var": "r", "value": 3},
        {"event": "change_scene", "room": "r", "x": 40, "y": "r + 1"},
        {"event": "set_var", "var": "after", "value": 1}]), frames=1)
    check(0x16 in list(prog.code), "emits CHANGE_SCENE_E (0x16)")
    check((vm.pend_a, vm.pend_b, vm.pend_c) == (3, 40, 4) and vm.pend_code == 0,
          "RAISE 2 with room 3 at (40, 4), serviced at the frame step")
    check(vm.heap[prog.variables["after"]] == 0 and vm.any_active_threads() == 0,
          "the raising thread ends at once")


def test_op_hud_show():
    print("[HUD_SHOW]")
    prog, vm = _vm(_main([{"event": "hud_show", "on": False}]))
    check(0x32 in list(prog.code) and vm.hud_show == 0, "hud_show off clears the visibility flag")
    _, vm2 = _vm(_main([{"event": "hud_show", "on": True}]))
    check(vm2.hud_show == 1, "...and on sets it")


def test_op_overlay():
    print("[OVERLAY_SHOW / OVERLAY_MOVE_TO: the window curtain]")
    prog, vm = _vm(_main([{"event": "overlay_show", "row": 5}]), frames=1)
    check(0x4A in list(prog.code) and vm.curtain_y == 40, "overlay_show row 5 puts the curtain at 40 px")
    _, vm2 = _vm(_main([{"event": "overlay_show", "row": 5}, {"event": "overlay_hide"}]), frames=1)
    check(vm2.curtain_y == 144, "overlay_hide is row 18 = off the bottom")
    prog3 = m.Compiler().compile(_main([
        {"event": "overlay_show", "row": 18},
        {"event": "overlay_move_to", "row": 10, "speed": 0},
        {"event": "set_var", "var": "done", "value": 1}]))
    check(0x4B in list(prog3.code), "emits OVERLAY_MOVE_TO (0x4B)")
    vm3 = m.RefVM(prog3.code, entry=prog3.entry)
    d = prog3.variables["done"]
    vm3.run(3)
    check(vm3.curtain_y < 144 and vm3.curtain_y > 80 and vm3.heap[d] == 0,
          "the slide is in flight and the thread WAITS (y=%d)" % vm3.curtain_y)
    vm3.run(60)
    check(vm3.curtain_y == 80 and vm3.heap[d] == 1,
          "...arrives at row 10 (80 px) and the thread continues")


def test_op_player_knockback_and_move_to():
    print("[PLAYER_KNOCKBACK / PLAYER_MOVE_TO]")
    prog, vm = _vm(_main([{"event": "player_knockback"}]))
    check(0x35 in list(prog.code) and vm.player_knockback == 1, "knockback records one impulse")
    prog2 = m.Compiler().compile(_main([
        {"event": "player_move_to", "x": 5, "y": 3, "mode": "horizontal"},
        {"event": "set_var", "var": "done", "value": 1}]))
    check(0x1E in list(prog2.code), "a literal walk emits PLAYER_MOVE_TO (0x1E)")
    vm2 = m.RefVM(prog2.code, entry=prog2.entry)
    d = prog2.variables["done"]
    vm2.run(2)
    check((vm2.player_x, vm2.player_y) == (2, 0) and vm2.heap[d] == 0,
          "horizontal-first holds Y until X lands; the thread waits")
    vm2.run(10)
    check((vm2.player_x, vm2.player_y) == (5, 3) and vm2.heap[d] == 1
          and vm2.player_dir == 0, "...arrives at (5, 3) facing down, then continues")


def test_op_projectile_forms():
    print("[PROJ_ANIM / PROJ_GROUP / PROJ_LAUNCH_A / PROJ_LAUNCH_EM]")
    prog, vm = _vm(_main([
        {"event": "set_var", "var": "px", "value": 20},
        {"event": "projectile", "x": "px", "y": 30, "vx": -2, "vy": 1,
         "tile": 4, "life": 50, "mask": 5, "frames": 4, "anim": 8, "stride": 2,
         "group": 3},
        {"event": "set_var", "var": "after", "value": 1}]), frames=1)
    ops = list(prog.code)
    check(0x65 in ops and 0x36 in ops and 0x63 in ops,
          "emits PROJ_ANIM (0x65) + PROJ_GROUP (0x36) + PROJ_LAUNCH_EM (0x63)")
    check(len(vm.proj) == 1, "one shot in flight")
    s = vm.proj[0] if vm.proj else {}
    # one frame has run, so the shot has flown one step and aged one frame
    check((s.get("x"), s.get("y"), s.get("vx"), s.get("vy")) == (18, 31, -2, 1),
          "x (computed), y, vx, vy pop in push order (and flew one step)")
    check((s.get("tile"), s.get("life"), s.get("mask")) == (4, 49, 5), "tile/life/mask ride inline")
    check((s.get("frames"), s.get("period"), s.get("stride")) == (4, 8, 2),
          "the PROJ_ANIM latch was consumed by the launch")
    check(s.get("group") == 3, "the PROJ_GROUP latch stamps the shot's own group")
    check(vm.proj_latch is None and vm.proj_group == 0, "both latches are one-shot")
    check(vm.heap[prog.variables["after"]] == 1,
          "the thread survives PROJ_GROUP (it had no RefVM handler and ENDED the thread)")
    # the angle form: 0 = up, 64 = right, in sixteenths of a pixel
    _, vm2 = _vm(_main([
        {"event": "projectile", "x": 10, "y": 10, "angle": "0 + 64", "speed": 16, "tile": 1}]), frames=1)
    s2 = vm2.proj[0] if vm2.proj else {}
    check(0x64 in list(m.Compiler().compile(_main([
        {"event": "projectile", "x": 10, "y": 10, "angle": "0 + 64", "speed": 16, "tile": 1}])).code),
          "an angle launch emits PROJ_LAUNCH_A (0x64)")
    check((s2.get("vx16"), s2.get("vy16")) == (16, 0), "angle 64 at speed 16 flies right (vx16 16)")
    _, vm3 = _vm(_main([
        {"event": "projectile", "x": 10, "y": 10, "angle": "0", "speed": 32, "tile": 1}]), frames=1)
    s3 = vm3.proj[0] if vm3.proj else {}
    check((s3.get("vx16"), s3.get("vy16")) == (0, -32), "angle 0 at speed 32 flies UP (vy16 -32)")


def test_op_self():
    print("[SELF: bind the thread's actor]")
    prog, vm = _vm(_main([
        {"event": "set_self", "actor": 2},
        {"event": "actor_set_dir", "actor": "self", "dir": 1},
        {"event": "actor_set_collision", "actor": "self", "on": False}]))
    check(0x15 in list(prog.code), "emits SELF (0x15)")
    check(vm.actors[2].dir == 1 and vm.actors[2].solid == 0
          and vm.actors[0].dir == 0 and vm.actors[0].solid == 1,
          "`self` resolves to the bound actor, not slot 0")


def test_op_shake_opts():
    print("[SHAKE_OPTS: axis + wait through the one-shot latch]")
    prog = m.Compiler().compile(_main([
        {"event": "shake", "frames": 3, "amp": 2, "axis": 1, "wait": 1},
        {"event": "set_var", "var": "done", "value": 1}]))
    check(0x4C in list(prog.code), "emits SHAKE_OPTS (0x4C)")
    vm = m.RefVM(prog.code, entry=prog.entry)
    d = prog.variables["done"]
    vm.run(1)
    check(vm.shk_axis == 1 and vm.heap[d] == 0, "axis 1 (X) and the thread BLOCKS on wait=1")
    vm.run(6)
    check(vm.heap[d] == 1 and vm.shk_wait == 0 and vm.shk_axis == 0,
          "...continues when the shake ends, and the latch is cleared (one shot)")
    prog2 = m.Compiler().compile(_main([{"event": "shake", "frames": 3, "amp": 2}]))
    check(0x4C not in list(prog2.code), "the historical shake emits no latch (byte-identical)")


def test_op_text_speed_and_blip():
    print("[TEXT_SPEED / TEXT_BLIP]")
    prog, vm = _vm(_main([
        {"event": "text_speed", "speed": 3, "fastforward": False},
        {"event": "text_blip", "freq": 440, "frames": 3}]))
    ops = list(prog.code)
    check(0x3E in ops and 0x3F in ops, "emits TEXT_SPEED (0x3E) + TEXT_BLIP (0x3F)")
    check(vm.text_speed == 3 and vm.text_ff == 0, "speed 3, fast-forward off")
    check(vm.blip_freq == 440 and vm.blip_frames == 3, "blip 440 Hz for 3 frames")
    _, vm2 = _vm(_main([{"event": "text_speed", "speed": 9}]))
    check(vm2.text_speed == 7 and vm2.text_ff == 1, "speed clamps to 7; fast-forward defaults on")


def test_rpn_ne_le_moving_atan2():
    print("[RPN: NE / LE / ACTOR_MOVING / ATAN2]")
    prog, vm = _vm(_main([
        {"event": "set_var", "var": "v", "value": 3},
        {"event": "set_var", "var": "ne", "expr": "v != 3"},
        {"event": "set_var", "var": "ne2", "expr": "v != 4"},
        {"event": "set_var", "var": "le", "expr": "v <= 3"},
        {"event": "set_var", "var": "le2", "expr": "v <= 2"},
        {"event": "set_var", "var": "a_right", "expr": "atan2(0, 1)"},
        {"event": "set_var", "var": "a_down", "expr": "atan2(1, 0)"},
        {"event": "set_var", "var": "a_up", "expr": "atan2(0 - 1, 0)"},
        {"event": "actor_activate", "actor": 0, "tile": 0, "x": 0, "y": 0},
        {"event": "actor_move", "actor": 0, "x": 40, "y": 0},
        {"event": "set_var", "var": "mv", "expr": "actor_moving(0)"},
        {"event": "set_var", "var": "mv1", "expr": "actor_moving(1)"}]), frames=3)
    # frames=3: the script is longer than one QUANT slice, and the 40 px move
    # at speed 1 is still in flight when the read lands
    v = lambda n: vm.heap[prog.variables[n]]
    blob = list(prog.code)
    check(0x21 in blob and 0x24 in blob and 0x0B in blob and 0x51 in blob,
          "the blob carries NE, LE, ACTOR_MOVING and ATAN2 tokens")
    check(v("ne") == 0 and v("ne2") == 1, "!= answers 0 for equal, 1 for different")
    check(v("le") == 1 and v("le2") == 0, "<= answers 1 at the bound, 0 past it")
    check(v("a_right") == 64 and v("a_down") == 128 and v("a_up") == 0,
          "atan2(y, x): right 64, down 128, up 0 (got %d/%d/%d)" % (v("a_right"), v("a_down"), v("a_up")))
    check(v("mv") == 1 and v("mv1") == 0, "actor_moving reads the auto-move flag of that actor")


def _core():
    return open(os.path.join(ROOT, "lib", "vm", "core.mos"), encoding="utf-8").read()


def test_op_thread_stop():
    print("[THREAD_STOP: end the thread a join handle watches]")
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "handle_next", "var": "h"},
            {"event": "start_thread", "script": "loop"},
            {"event": "handle_next", "var": "other"},
            {"event": "start_thread", "script": "loop"},
            {"event": "wait", "frames": 3},
            {"event": "thread_stop", "var": "h"},
            {"event": "thread_stop", "var": "never"},
            {"event": "set_var", "var": "done", "value": 1},
            {"event": "stop"}]},
        {"name": "loop", "loop": True,
         "events": [{"event": "set_var", "var": "ticks", "expr": "ticks + 1"},
                    {"event": "wait", "frames": 1}]}])
    check(bytes([0x5B, prog.variables["h"]]) in bytes(prog.code),
          "emits THREAD_STOP (0x5B) with the handle's heap index")
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(2)
    h, o = prog.variables["h"], prog.variables["other"]
    check(vm.heap[h] == 1 and vm.heap[o] == 1, "both watched threads run (stimulus)")
    vm.run(6)
    check(vm.heap[prog.variables["done"]] == 1,
          "a handle no thread holds is a no-op, not an error")
    check(vm.heap[h] == 0, "the stopped thread's handle reads 0 (the §11 cleanup)")
    check(vm.heap[o] == 1, "the other thread keeps running")
    # a thread stopping ITSELF ends like STOP
    prog2 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "handle", "var": "me"},
        {"event": "thread_stop", "var": "me"},
        {"event": "set_var", "var": "after", "value": 1},
        {"event": "stop"}]}])
    vm2 = m.RefVM(prog2.code, entry=prog2.entry)
    vm2.run(3)
    check(vm2.heap[prog2.variables["after"]] == 0
          and vm2.heap[prog2.variables["me"]] == 0,
          "stopping itself ends the thread before the next event")
    core = _core()
    arm = core[core.index("case OP_THREAD_STOP {"):]
    arm = arm[:arm.index("case OP_A_SET_BOX {")]
    helper = core[core.index("local function stop_handle("):]
    helper = helper[:helper.index("return self_hit")]
    check("if VM_OP_THREAD_STOP {" in arm and "stop_handle(hs, c) == 1" in arm
          and "return ST_END" in arm and "vm_handle[k] == hs" in helper
          and "kill(k)" in helper and "bank(0)" not in
          core[core.index("local function stop_handle(") - 12:
               core.index("local function stop_handle(")],
          "core's arm folds and ends itself through ST_END; the banked "
          "stop_handle finds the context by its handle and kills the others")


def test_op_actor_set_box():
    print("[A_SET_BOX: replace an actor's collision box]")
    prog, vm = _vm(_main([
        {"event": "actor_set_box", "actor": 3, "w": 16, "h": 8, "ox": 2, "oy": 8}]))
    check(bytes([0x5C, 3, 16, 8, 2, 8]) in bytes(prog.code),
          "emits A_SET_BOX (0x5C) i, w, h, ox, oy")
    check(vm.actors[3].box == (16, 8, 2, 8) and vm.actors[2].box == (8, 8, 0, 0),
          "that actor's box only")
    try:
        m.Compiler().compile(_main([{"event": "actor_set_box", "actor": 0, "w": 300}]))
        check(False, "a box past a u8 compiled")
    except Exception as exc:                                 # noqa: BLE001
        check("w = 300" in str(exc), "a box past a u8 is refused, not masked")
    arm = _core()
    arm = arm[arm.index("case OP_A_SET_BOX {"):]
    arm = arm[:arm.index("return ST_CONT")]
    check("if VM_OP_A_SET_BOX {" in arm and arm.count("f8()") == 5
          and "actor.set_box(bi, bw, bh, bx, f8())" in arm,
          "core's arm consumes five operands and calls actor.set_box")


def test_states_timer_reset_and_sprites_hidden():
    print("[states 33 timer_reset / 34 sprites_hidden]")
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "timer_set", "timer": 1, "period": 10, "script": "t"},
            {"event": "wait", "frames": 6},
            {"event": "set_var", "var": "before", "expr": "0"},
            {"event": "timer_reset", "timer": 1},
            {"event": "timer_reset", "timer": 2},
            {"event": "sprites_visible", "on": 0},
            {"event": "stop"}]},
        {"name": "t", "events": [{"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(4)
    check(vm.tmr_count[1] < 10, "the timer counted down before the reset (stimulus)")
    vm.run(4)
    check(vm.tmr_count[1] >= 8, "timer_reset put the countdown back near its period "
          "(got %d)" % vm.tmr_count[1])
    check(vm.tmr_active[2] == 0, "resetting a STOPPED timer does not start it")
    check(vm.sprites_hidden == 1, "sprites_visible on=0 writes sprites_hidden = 1")
    try:
        m.Compiler().compile(_main([{"event": "timer_reset", "timer": 4}]))
        check(False, "timer 4 compiled")
    except Exception as exc:                                 # noqa: BLE001
        check("timer = 4" in str(exc), "a timer past the four slots is refused")
    core = _core()
    arm = core[core.index("if VM_ST_TIMER_RESET {"):]
    arm = arm[:arm.index("if VM_ST_SPRITES_HIDDEN {")]
    helper = core[core.index("local function timer_restart("):]
    helper = helper[:helper.index("local function stop_handle(")]
    check("timer_restart(v)" in arm and "tmr_count[ti] = tmr_period[ti]" in helper
          and "tmr_active" not in helper,
          "core's state calls the banked timer_restart, which writes the count "
          "only, never the active flag")
    arm = core[core.index("if VM_ST_SPRITES_HIDDEN {"):]
    arm = arm[:arm.index("video.hide_sprites()") + 30]
    check("video.hide_sprites()" in arm and "video.show_sprites()" in arm,
          "core's state goes through the video verbs")


def test_op_actor_get_dir():
    print("[A_GET_DIR: an actor's facing into a variable]")
    prog, vm = _vm(_main([
        {"event": "actor_set_dir", "actor": 1, "dir": 2},
        {"event": "actor_get_dir", "actor": 1, "var": "d1"},
        {"event": "actor_get_dir", "actor": 0, "var": "d0"}]))
    check(bytes([0x5D, 1, prog.variables["d1"]]) in bytes(prog.code),
          "emits A_GET_DIR (0x5D) actor, heap index")
    v = lambda n: vm.heap[prog.variables[n]]
    check(v("d1") == 2 and v("d0") == 0, "A_GET_DIR stores that actor's facing")
    core = _core()
    arm = core[core.index("case OP_A_GET_DIR {"):]
    arm = arm[:arm.index("return ST_CONT")]
    helper = core[core.index("local function get_dir_into("):]
    helper = helper[:helper.index("bank(0) local function set_state")]
    check("if VM_OP_A_GET_DIR {" in arm and "get_dir_into(gi, f8())" in arm
          and "heap[v] = actor.dir_of(i)" in helper and "v < VM_HEAP" in helper,
          "core's arm folds; the banked helper bounds the cell and writes "
          "actor.dir_of")
    check("VM_RPN_ACTOR_DIR" not in core,
          "no ACTOR_DIR RPN token: its empty case cost every SMS/GG build 5 B")

def test_op_actor_start_update_and_scene_pause():
    print("[A_START_UPDATE + state 35 scene_update_paused]")
    prog, vm = _vm(_main([
        {"event": "actor_stop_update", "actor": 2},
        {"event": "actor_start_update", "actor": 2},
        {"event": "scene_update_pause", "on": 1}]))
    check(bytes([0x5E, 2]) in bytes(prog.code), "emits A_START_UPDATE (0x5E) actor 2")
    check(vm.update_starts == [2] and vm.actors[2].updating == 1,
          "the start is recorded and the actor updates again")
    check(vm.scene_update_paused == 1, "scene_update_pause writes state 35")
    core = _core()
    arm = core[core.index("case OP_A_START_UPDATE {"):]
    arm = arm[:arm.index("return ST_CONT")]
    check("if VM_OP_A_START_UPDATE {" in arm and "g_start_update(su)" in arm,
          "core's arm folds and goes through the seam")
    run = core[core.index("scene_paused == 0 {") - 400:]
    run = run[:run.index("g_pdraw()") + 12]
    check("if scene_paused == 0 {" in run and "g_pdraw()" in run,
          "a paused frame skips g_player and still draws the player")
    reset = core[core.index("local function reset_scene_ui()"):]
    reset = reset[:reset.index("bank(0) local function run_context")]
    check("scene_paused = 0" in reset, "a scene load clears the pause")
    ent = open(os.path.join(ROOT, "lib", "vm", "entity.mos"), encoding="utf-8").read()
    check("core.alive_gen(en_uthread[i], en_sgen[i]) == 1" in ent
          and "core.alive_gen(en_uthread[i], en_ugen[i]) == 1" in ent,
          "entity starts the update only when the tracked thread is not alive, "
          "in both forks")


ALL = (test_op_actor_await_move, test_op_actor_move_opts, test_op_actor_set_anim_state,
       test_op_actor_clear_anim_state,
       test_op_actor_set_collision_and_dir, test_op_actor_set_frame_e_and_pos_e,
       test_op_actor_visible_and_player_visible, test_op_bkg_tile, test_op_change_scene_e,
       test_op_hud_show, test_op_overlay, test_op_player_knockback_and_move_to,
       test_op_projectile_forms, test_op_self, test_op_shake_opts,
       test_op_text_speed_and_blip, test_rpn_ne_le_moving_atan2,
       test_op_thread_stop, test_op_actor_set_box,
       test_states_timer_reset_and_sprites_hidden, test_op_actor_get_dir,
       test_op_actor_start_update_and_scene_pause)
