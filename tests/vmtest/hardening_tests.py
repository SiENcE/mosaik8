"""The P0/P1/P2 hardening suites + save/load.

Split out of tests/vm_test.py (2026-08-26); run via tests/vm_test.py."""
import os
import sys

from .common import *  # noqa: F401,F403 - FAILS/check/m + shared helpers
from .common import FAILS, _run_expr, check, m
from .common import SPIKE_BLOB, SPIKE_SCRIPTS, ROOT



def test_p2_hardening():
    print("[P2 hardening (review 2026-07-19): UI latch + music-under-lock + assembler guards]")

    # R7/1.7: the box/menu globals are single, so two threads both opening a box
    # would clobber each other + a single A-dismiss would close both. The UI-owner
    # latch SERIALIZES them: the 2nd thread's UI_TEXT yields until the 1st box is
    # dismissed.
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "start_thread", "script": "a"},
            {"event": "start_thread", "script": "b"},
            {"event": "stop"}]},
        {"name": "a", "events": [{"event": "text", "string": "AAA"}, {"event": "stop"}]},
        {"name": "b", "events": [{"event": "text", "string": "BBB"}, {"event": "stop"}]},
    ])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()                                   # main spawns a+b; a opens, b waits
    check(vm.box_open == 1 and len(vm.text_log) == 1,
          "only ONE box opens; the 2nd thread waits on the UI latch")
    first = vm.ui_owner
    vm.frame(a_pressed=True)                      # dismiss a's box -> b opens its box
    check(len(vm.text_log) == 2 and vm.box_open == 1,
          "the 2nd box opens only after the 1st is dismissed")
    check(vm.ui_owner != 255 and vm.ui_owner != first,
          "the 2nd thread now owns the UI latch")

    # R9: a thread KILLED while owning the box/menu releases the UI latch, so it
    # never sticks busy (and the Lynx present hook stops redrawing a dead menu).
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()                                   # thread a opens a box, owns the UI
    owner = vm.ui_owner
    check(vm.box_open == 1 and owner != 255, "a thread owns the open box (setup)")
    vm.kill(owner)
    check(vm.ui_owner == 255 and vm.box_open == 0,
          "killing the UI-owning thread releases the latch (R9)")

    # R8: a script-driven `music_play` song keeps ADVANCING under a cutscene lock
    # (like the multi-channel driver), so cutscene music doesn't hang on its last
    # tone. `song` increments `beat`; `cut` holds a lock.
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "music_play", "script": "song"},
            {"event": "start_thread", "script": "cut"},
            {"event": "stop"}]},
        {"name": "song", "loop": True, "events": [
            {"event": "set_var", "var": "beat", "expr": "beat + 1"},
            {"event": "wait", "frames": 1}]},
        {"name": "cut", "events": [
            {"event": "lock"}, {"event": "wait", "frames": 60}]},
    ])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(3)                                     # boot: song running, cut locks
    check(vm.lockcount > 0, "the cutscene lock is held")
    beat0 = vm.heap[prog.variables["beat"]]
    vm.run(10)
    check(vm.heap[prog.variables["beat"]] > beat0,
          "the song thread keeps advancing under the cutscene lock (R8)")

    # A9: a music_song with a blank song lowers to song 0 instead of crashing on
    # int("") (a node that never got a song chosen).
    p = m.Compiler().compile([{"name": "main", "events": [
        {"event": "music_song", "song": ""}, {"event": "stop"}]}])
    check(p.code[1] == 0, "music_song with a blank song lowers to index 0 (no crash)")

    # A7: a menu row outside the u8 range is a clean VmError, not a raw bytes()
    # ValueError.
    try:
        m.Compiler().compile([{"name": "main", "events": [
            {"event": "menu", "var": "x", "row": 300, "options": ["A", "B"]}]}])
        check(False, "menu row 300 rejected (u8 operand)")
    except m.VmError:
        check(True, "menu row 300 rejected (u8 operand)")


def test_p1_hardening():
    print("[P1 hardening (review 2026-07-19): scene-change teardown + RPN depth + escape]")

    # R5: a CHANGE_SCENE tears down the old room's open UI + timers + input
    # attachments, so none of it leaks into the new room (a timer/other thread
    # firing a change while a box is open used to carry the box + input block
    # + the old timers into the destination).
    vm = m.RefVM(bytes([0x0F, 2, 1, 0, 0, 0, 0, 0x00]), entry=0)   # RAISE 2 (CHANGE_SCENE room 1); STOP
    vm.tmr_active[0] = 1
    vm.tmr_period[0] = 60
    vm.tmr_count[0] = 60        # far from firing this frame (a bare count 0 would fire)
    vm.in_active[0] = 1
    vm.box_open = 1
    vm.menu_open = 1
    vm.frame()
    check(vm.change_log == [(1, 0, 0)], "the scene change was raised")
    check(all(t == 0 for t in vm.tmr_active) and all(a == 0 for a in vm.in_active),
          "scene change clears the old room's timers + input attachments")
    check(vm.box_open == 0 and vm.menu_open == 0,
          "scene change closes a lingering box / menu")

    # A scene change also TERMINATES the old room's threads (2026-08-09), which
    # is the reference engine's own model ("kill all threads, but don't clear variables" -
    # its core.c EXCEPTION_CHANGE_SCENE arm calls script_runner_init(FALSE)).
    # They used to survive, and a survivor is bound to a room that no longer
    # exists: the reference-engine sample's path->town doorway script is a waitable
    # walk-to followed by the change, so its surviving copies finished their
    # walk against the NEXT room's actors and re-raised the change - Sample
    # Town "reset" itself moments after every arrival.
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "start_thread", "script": "walker"},
            {"event": "start_thread", "script": "walker"},
            {"event": "change_scene", "room": 3, "x": 8, "y": 8}]},
        # a long waitable that would outlive the change and then re-raise it
        {"name": "walker", "events": [
            {"event": "wait", "frames": 40},
            {"event": "change_scene", "room": 9, "x": 0, "y": 0}]},
    ])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()
    check(vm.change_log == [(3, 8, 8)], "the doorway raised its scene change")
    check(sum(vm.active) <= 1,
          "a scene change kills the old room's threads (%d still active)"
          % sum(vm.active))
    vm.run(90)                       # well past the walkers' 40-frame wait
    check(vm.change_log == [(3, 8, 8)],
          "no surviving thread re-raises a change into the new room "
          "(log=%r)" % (vm.change_log,))

    # ... except the tracked SONG thread, which plays across rooms (spec 3).
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "music_play", "script": "song"},
            {"event": "change_scene", "room": 4, "x": 0, "y": 0}]},
        {"name": "song", "loop": True, "events": [
            {"event": "set_var", "var": "beat", "expr": "beat + 1"},
            {"event": "wait", "frames": 1}]},
    ])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()
    beat0 = vm.heap[prog.variables["beat"]]
    vm.run(8)
    check(vm.heap[prog.variables["beat"]] > beat0,
          "the exempt song thread survives a scene change (music plays on)")

    # R6: a `load` (SRAM restore raises a scene change) runs the restored heap
    # under a FRESH UI/timer/input state, not the pre-load pause menu's.
    vm = m.RefVM(bytes([0x45, 0x00]), entry=0)                  # LOAD; STOP
    # `saved` is one entry per SLOT since W7c; slot 0 is the default.
    vm.saved[0] = {"heap": [0] * 128, "scene": 2, "px": 10, "py": 20}
    vm.tmr_active[0] = 1
    vm.tmr_period[0] = 60
    vm.tmr_count[0] = 60
    vm.in_active[0] = 1
    vm.box_open = 1
    vm.frame()
    check(vm.change_log == [(2, 10, 20)], "load repositions to the saved scene")
    check(all(t == 0 for t in vm.tmr_active) and all(a == 0 for a in vm.in_active)
          and vm.box_open == 0, "load tears down the pre-load UI/timers/input")

    # A5: the RefVM input-attach pool is 8 (lockstep with core.mos NIN), so a
    # program attaching the 5th-8th button is modelled faithfully.
    check(vm.NIN == 8 and len(vm.in_active) == 8, "RefVM NIN is 8 (core parity)")

    # A4: the assembler REJECTS an expression deeper than the 8-slot Lynx
    # expression stack (vpush silently drops past it -> wrong-on-Lynx). A
    # right-nested chain of N additions peaks at depth N.
    def _nest(n):
        e = "1"
        for _ in range(n - 1):
            e = "1+(%s)" % e
        return e
    m.Compiler().compile([{"name": "main", "events": [
        {"event": "set_var", "var": "x", "expr": _nest(8)}, {"event": "stop"}]}])
    check(True, "an 8-deep expression compiles (fits the Lynx stack)")
    try:
        m.Compiler().compile([{"name": "main", "events": [
            {"event": "set_var", "var": "x", "expr": _nest(9)}, {"event": "stop"}]}])
        check(False, "a 9-deep expression rejected (past the Lynx stack)")
    except m.VmError:
        check(True, "a 9-deep expression rejected (past the Lynx stack)")

    # A6: _escape neutralises a stray newline (a HUD label) + non-ASCII, so
    # neither breaks the generated mosaik "..." literal.
    esc = m._escape("HI\nTHERE\xe9")
    check("\n" not in esc and all(ord(c) < 0x80 for c in esc),
          "_escape folds newlines + ASCII-replaces non-ASCII")
    check(m._escape("SCORE") == "SCORE", "_escape leaves plain ASCII byte-identical")


def test_p0_hardening():
    print("[P0 hardening (review 2026-07-19): central thread-death cleanup + operand bounds]")

    # R1: `lock ... stop` must RELEASE the lock. The 1.3 fix covered only
    # CHANGE_SCENE/kill; a locking thread ending via STOP (or RET-with-no-
    # caller) stranded vm_lockcount > 0 forever -- the scheduler then ran
    # nobody (a hard, authorable hang). The cleanup is now CENTRAL in
    # run_context's ST_END path, covering every in-step death.
    for death, label in (([{"event": "stop"}], "STOP"),
                         ([{"event": "ret"}], "RET-with-no-caller")):
        prog = m.Compiler().compile([
            {"name": "main", "events": [
                {"event": "start_thread", "script": "cut"},
                {"event": "wait", "frames": 4},
                {"event": "set_var", "var": "after", "value": 7},
                {"event": "stop"}]},
            {"name": "cut", "events": [{"event": "lock"}] + death},  # no unlock
        ])
        vm = m.RefVM(prog.code, entry=prog.entry)
        vm.run(10)
        check(vm.lockcount == 0,
              "lock -> %s releases the cutscene lock (central ST_END cleanup)" % label)
        check(vm.heap[prog.variables["after"]] == 7,
              "the other thread kept running after the %s death" % label)

    # R3: a tracked song thread dying via a NON-STOP path clears music_ctx --
    # a stale handle would make a later MUSIC_PLAY/MUSIC_STOP kill whatever
    # unrelated thread spawn() reused that context for.
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "music_play", "script": "song"},
            {"event": "wait", "frames": 4},
            {"event": "stop"}]},
        {"name": "song", "events": [{"event": "ret"}]},
    ])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(10)
    check(vm.music_ctx == 255, "a song thread dying via RET clears music_ctx")

    # R2: heap operands from bytecode are GUARDED (hand-authored blobs are
    # first-class): an OOB write is dropped, an OOB read pushes 0.
    code = bytes([0x09, 1, 5, 0,            # SET_CONST heap[1] = 5
                  0x09, 200, 0x34, 0x12,    # SET_CONST heap[200] -- OOB, dropped
                  0x08, 0x02, 200, 0xFF,    # RPN: PUSH heap[200] -- OOB, pushes 0
                  0x0A, 0,                  # SET_VAR heap[0] = pop() (= 0)
                  0x00])                    # STOP
    vm = m.RefVM(code, entry=0)
    vm.run(2)
    check(len(vm.heap) == 128 and vm.heap[1] == 5 and vm.heap[0] == 0,
          "OOB heap operands are guarded (write dropped, read pushes 0)")

    # R4: an out-of-range literal actor id clamps to slot 0 in resolve_actor
    # (the pool arrays index with it -- OOB would corrupt RAM on console).
    # (position operands are u16 little-endian)
    code = bytes([0x21, 9, 50, 0, 60, 0, 0x00])   # A_SET_POS actor 9 -> (50, 60); STOP
    vm = m.RefVM(code, entry=0)
    vm.run(1)
    check(vm.actors[0].x == 50 and vm.actors[0].y == 60,
          "an out-of-range actor literal clamps to slot 0 (core parity)")

    # A2: the ASSEMBLER rejects an out-of-range actor id too (bare .evt.toml
    # authoring has no studio catalogue check in front of it).
    try:
        m.Compiler().compile([{"name": "main", "events": [
            {"event": "actor_set_pos", "actor": 9, "x": 0, "y": 0}]}])
        check(False, "actor 9 rejected (the pool is 0..7)")
    except m.VmError:
        check(True, "actor 9 rejected (the pool is 0..7)")
    m.Compiler().compile([{"name": "main", "events": [
        {"event": "actor_set_pos", "actor": "self", "x": 0, "y": 0},
        {"event": "stop"}]}])
    check(True, 'actor = "self" still compiles')

    # A3: a switch with > 255 cases is rejected -- the count byte would wrap
    # to 0 and the jump table's bytes would execute as opcodes.
    big = {"event": "switch", "value": "0",
           "cases": [{"value": i, "then": []} for i in range(256)]}
    try:
        m.Compiler().compile([{"name": "main", "events": [big, {"event": "stop"}]}])
        check(False, "256-case switch rejected (u8 count)")
    except m.VmError:
        check(True, "256-case switch rejected (u8 count)")

    # A8: a u8/u16 operand is VALIDATED, not truncated. `events.py` used to
    # spell every operand width as `& 0xFF` / `& 0xFFFF`, which is not a check:
    # `actor_set_group group = 300` emitted group 44, and `wait_until poll = 256`
    # emitted a poll of 1 because the mask ran INSIDE the max(1, ...). Bare
    # .evt.toml authoring reaches the lowering with no studio catalogue in
    # front of it, so this is where it has to be caught.
    for ev, field in (({"event": "actor_set_group", "actor": 0, "group": 300}, "group"),
                      ({"event": "wait_until", "cond": "1", "poll": 256}, "poll"),
                      ({"event": "actor_set_anim_speed", "actor": 0, "speed": 300}, "speed"),
                      ({"event": "overlay_show", "row": 900}, "row"),
                      ({"event": "set_var", "var": "v", "value": 70000}, "value"),
                      # ...and `wait`, which the original A8 pass missed: it
                      # kept `_i` where every neighbour moved to `_u8`, so
                      # `frames = 300` emitted a 44-frame wait (found
                      # 2026-09-16 by a test fixture that asked for 300).
                      ({"event": "wait", "frames": 300}, "frames")):
        try:
            m.Compiler().compile([{"name": "main", "events": [ev, {"event": "stop"}]}])
            check(False, "%s %s out of range is refused" % (ev["event"], field))
        except m.VmError as e:
            check(field in str(e) and "does not fit" in str(e),
                  "%s %s out of range is refused, and the error NAMES it (%s)"
                  % (ev["event"], field, e))

    # ...and the SIGNED exception survives, because it is real encoding rather
    # than an accident: an i8 velocity IS its two's-complement byte, and a u16
    # literal may be authored as -1 for 65535.
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "projectile", "x": 1, "y": 1, "vx": -2, "vy": -3},
        {"event": "scroll_bg", "vx": -1, "vy": 0},
        {"event": "set_var", "var": "neg", "value": -1},
        {"event": "stop"}]}])
    check(bytes([254, 253]) in prog.code,
          "an i8 velocity still packs two's-complement (vx -2, vy -3 -> 254, 253)")
    check(bytes([0xFF, 0xFF]) in prog.code,
          "a negative u16 literal still emits 65535 (SET_CONST -1)")
    # ...and that byte pair still reads back as -1 through the i16 heap
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.run(2)
    check(vm.heap[prog.variables["neg"]] == -1,
          "the same operand round-trips as i16 -1 (test_negative_var's contract)")

    # A1: generate_rooms refuses a room with more placed objects than the
    # 8-slot vm.actor pool (the emitted slot loop would run the pool over --
    # actor.activate now drops extras, but silently-missing NPCs are a bug
    # the author must hear about). Per-console filtering counts exactly.
    import tempfile
    import mosaik_assets as MA

    def _world(nobjs, platforms=None):
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "src"), exist_ok=True)
        MA.write_png_indexed(
            os.path.join(d, "tiles.png"), 16, 16,
            [[(x + y) % 4 for x in range(16)] for y in range(16)],
            [(i * 60, i * 60, i * 60) for i in range(4)])
        with open(os.path.join(d, "world.toml"), "w") as f:
            f.write('[world]\nvm = true\nstart_scene = "room"\n\n'
                    '[kinds]\nplayer = 0\nnpc = 1\n\n'
                    '[tileset]\npng = "tiles.png"\n\n[[scene]]\nname = "room"\n'
                    'scene_type = "topdown"\nmap_w = 4\nmap_h = 4\nmap = [%s]\n'
                    % ", ".join(str(i % 4) for i in range(16)))
            f.write('\n[[scene.object]]\nkind = "player"\nx = 16\ny = 16\n')
            for i in range(nobjs):
                f.write('\n[[scene.object]]\nkind = "npc"\nx = %d\ny = 16\n'
                        % (8 * (i + 1)))
                if platforms and i < len(platforms):
                    f.write('platforms = [%s]\n'
                            % ", ".join('"%s"' % p for p in platforms[i]))
        open(os.path.join(d, "src", "rooms.mos"), "w").close()
        return d

    check(m.generate_rooms(_world(8)) is not None,
          "8 objects in a room generate fine (the pool is 8)")
    try:
        m.generate_rooms(_world(9))
        check(False, "9 objects in one room rejected (actor pool overflow)")
    except m.VmError:
        check(True, "9 objects in one room rejected (actor pool overflow)")
    plats = [["gameboy"]] * 5 + [["lynx"]] * 5
    check(m.generate_rooms(_world(10, platforms=plats)) is not None,
          "10 objects split 5/5 across consoles pass (per-console bound is exact)")


def test_save_load():
    print("[save/load: heap snapshot + restore + save_exists gating]")
    # save with gold=7, then change gold to 99, then (gated on save_exists) load it back
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "set_var", "var": "gold", "value": 7},
        {"event": "save"},
        {"event": "set_var", "var": "gold", "value": 99},
        {"event": "if", "cond": "save_exists()", "then": [{"event": "load"}]},
        {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.player_x, vm.player_y, vm.cur_scene = 40, 56, 2
    vm.run(3)
    gi = prog.variables["gold"]
    check(vm.heap[gi] == 7, "load restored the heap (gold 7, not the post-save 99)")
    check(vm.change_log == [(2, 40, 56)],
          "load repositions via request_change (saved scene + player pos)")
    check(vm.saved[0] is not None, "the save snapshot persists")

    # load with NO save present is a safe no-op (a fresh cart -> Continue = new game)
    prog2 = m.Compiler().compile([{"name": "main", "events": [
        {"event": "set_var", "var": "hp", "value": 5},
        {"event": "if", "cond": "save_exists()",
         "then": [{"event": "load"}], "else": [{"event": "set_var", "var": "hp", "value": 3}]},
        {"event": "stop"}]}])
    vm2 = m.RefVM(prog2.code, entry=prog2.entry)
    vm2.run(3)
    check(vm2.saved[0] is None and vm2.change_log == [],
          "load with no save is a no-op (save_exists() = 0 took the else branch)")
    check(vm2.heap[prog2.variables["hp"]] == 3, "the else (new-game) branch ran")


def test_review_fixes_2026_09():
    print("[review 2026-09-02: SHAKE wait path, re-attach, cross-op stack depth]")
    # V-2: a BLOCKING shake (SHAKE_OPTS wait=1) rewinds the thread's own pc
    # each frame; the reference VM used to crash there (`self.pc -= 3` on the
    # per-context list) and no test reached it.
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "shake", "frames": 3, "amp": 2, "axis": 1, "wait": 1},
        {"event": "set_var", "var": "done", "expr": "1"},
        {"event": "stop"}]}])
    check(list(prog.code)[:3] == [0x4C, 1, 1], "shake axis/wait lowers to SHAKE_OPTS")
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()
    check(vm.heap[prog.variables["done"]] == 0 and vm.active[0] == 1,
          "a blocking SHAKE holds the thread (still alive, not past the shake)")
    for _ in range(3):
        vm.frame()
    check(vm.heap[prog.variables["done"]] == 1 and vm.shk_wait == 0,
          "released after `frames` display frames; the latch is consumed")

    # V-4: re-attaching a button that is already bound REPLACES its slot even
    # when a lower slot has since become free. The first-free-or-bound search
    # took the free slot and left two live slots on one button -> two spawns
    # per edge.
    prog = m.Compiler().compile([
        {"name": "main", "events": [
            {"event": "input_attach", "button": "a", "script": "f"},
            {"event": "input_attach", "button": "b", "script": "f"},
            {"event": "input_detach", "button": "a"},       # frees slot 0
            {"event": "input_attach", "button": "b", "script": "f"},   # re-attach
            {"event": "stop"}]},
        {"name": "f", "events": [
            {"event": "set_var", "var": "n", "expr": "n + 1"}, {"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()
    bound = [i for i in range(vm.NIN) if vm.in_active[i] and vm.in_btn[i] == 1]
    check(bound == [1], "one live slot for B after a re-attach (got %r)" % bound)
    for _ in range(2):
        vm.frame(held=["b"])
        vm.frame()
    vm.run(2)
    check(vm.heap[prog.variables["n"]] == 2, "two B edges -> two spawns, not four")

    # V-3: the values an `_E` op pops are pushed by SEPARATE RPN instructions,
    # so the i-th expression runs with i values already resident. Each on its
    # own fits the 8-cell stack; together they do not, and the console's
    # vpush silently DROPS the overflow. The compiler must refuse the sum.
    deep = "((((((1+2)+3)+4)+5)+6)+7)"        # peak depth 2: fine alone
    m.Compiler().compile([{"name": "main", "events": [
        {"event": "start_thread", "script": "main", "args": [deep, deep, deep, deep]},
        {"event": "stop"}]}])
    seven = "1+(2+(3+(4+(5+(6+(7+8))))))"      # peak depth 8: fine alone
    m.Compiler().compile([{"name": "main", "events": [
        {"event": "player_setpos", "x": seven, "y": "1"}, {"event": "stop"}]}])
    try:
        m.Compiler().compile([{"name": "main", "events": [
            {"event": "player_setpos", "x": "1", "y": seven}, {"event": "stop"}]}])
        check(False, "a depth-8 expression pushed on top of one resident value is refused")
    except m.VmError as e:
        check("2nd of 2" in str(e) and "9 cells" in str(e),
              "...with a message naming the position and the total (%s)" % e)
    try:
        m.Compiler().compile([{"name": "main", "events": [
            {"event": "projectile", "x": "1", "y": "2", "vx": "3",
             "vy": "1+(2+(3+(4+(5+6))))", "tile": 1, "life": 5}, {"event": "stop"}]}])
        check(False, "a projectile whose 4th expression overflows the stack is refused")
    except m.VmError as e:
        check("4th of 4" in str(e), "...naming the 4th expression (%s)" % e)

    # V-7: one RPN instruction had no token cap, so a stream with no 0xFF
    # terminator (hand-authored, or a corrupt blob) ran until the next unknown
    # byte -- and the u16 pc wraps, so nothing bounded it; spec R3's "no byte
    # sequence can stall a frame" was not true. 255 tokens now END the thread
    # (the runaway-PC guard's answer), and a terminated stream is unchanged.
    fine = bytes([0x08] + [0x01, 1, 0] * 6 + [0x10] * 5 + [0xFF, 0x0A, 0, 0x00])
    vm = m.RefVM(fine, entry=0, heap=4)
    vm.frame()
    check(vm.heap[0] == 6 and vm.active[0] == 0,
          "a terminated 12-token stream evaluates as before (six 1s summed)")
    runaway = bytes([0x08] + [0x01, 1, 0] * 300 + [0xFF, 0x09, 0, 7, 0, 0x00])
    vm = m.RefVM(runaway, entry=0, heap=4)
    vm.frame()
    check(vm.active[0] == 0 and vm.heap[0] == 0,
          "a 300-token stream ends the thread at the cap (the SET_CONST after it never ran)")
