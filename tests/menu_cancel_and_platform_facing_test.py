#!/usr/bin/env python3
"""Two reference-engine parity rules found on a tutorial conversion
(the DMG-palette check conversion, 2026-09-06).

1. **A menu can be CANCELLED with B** (state 30, `menu_cancel`). The reference engine's
   textMenu carries `.UI_MENU_CANCEL_B` and its ui_run_menu returns 0 on B;
   ours had A-confirm only, so the tutorial's "Back" flows were unreachable
   from B. The state is a ONE-SHOT latch the MENU open consumes (the
   SHAKE_OPTS / PROJ_GROUP shape): the blob format is untouched, a cancelled
   menu writes -1 where a pick writes the 0-based cursor, and the whole arm
   folds away under VM_ST_MENU_CANCEL for a blob that never writes it.

2. **A platform player facing UP/DOWN wears the JUMP pose** (DOWN mirrored,
   i.e. facing LEFT) until its state update first runs. The reference engine's
   platform_player type exports the 8 direction-indexed animations with DOWN
   filled from JUMP LEFT and UP from JUMP RIGHT (`animationMapBySpriteType` +
   `toEngineOrder`), and `platform_init` rewrites dir to RIGHT without
   re-picking the animation - the ground update does that on the first
   UNLOCKED frame. The tutorial's menu room starts facing `down` under a
   scene-init lock that never lifts, so the reference stands in the mirrored
   jump pose for the whole scene; ours drew idle-right.

RefVM checks for (1); SOURCE-CONTRACT checks (the blank_state_test shape) for
the core arm and for (2).
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

import mosaik_vm as m  # noqa: E402
from mosaik_vm import isa  # noqa: E402

VM = os.path.join(ROOT, "lib", "vm")
FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _read(name):
    with open(os.path.join(VM, name), encoding="utf-8") as f:
        return f.read()


def _menu_prog(cancel_b, n=3, last_cancels=False):
    ev = {"event": "menu", "var": "c", "options": ["O%d" % i for i in range(n)]}
    if cancel_b:
        ev["cancel_b"] = 1
    expr = "(c + 1)" if not last_cancels else "((c + 1) %% %d)" % n
    return m.Compiler().compile([{"name": "main", "events": [
        {"event": "set_var", "var": "c", "value": 9},
        ev,
        {"event": "set_var", "var": "gbs", "expr": expr},   # the importer's numbering
        {"event": "stop"}]}])


def refvm_cancel():
    print("RefVM: B cancels an ARMED menu, and only an armed one")
    prog = _menu_prog(True)
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame(b_pressed=True)                  # opens with B HELD: armed, must release
    check("the B that opens the menu does not cancel it (armed like A)",
          vm.menu_open == 1 and not vm.menu_log)
    vm.frame()                                # release
    vm.frame(b_pressed=True)                  # the edge
    vm.run(2)
    c, gbs = vm.heap[prog.variables["c"]], vm.heap[prog.variables["gbs"]]
    check("a B edge closes the menu and writes -1", vm.menu_log == [-1] and c == -1,
          (vm.menu_log, c))
    check("...so `pick + 1` is the reference engine's 0 = cancelled", gbs == 0, gbs)
    check("the latch is consumed by the open (menu_cancel back to 0)",
          vm.menu_cancel == 0)

    prog = _menu_prog(True)
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()
    vm.frame(down=True)
    vm.frame()
    vm.frame(a_pressed=True)
    vm.run(2)
    check("A still confirms an armed menu (cursor 1 -> the reference engine's 2)",
          vm.heap[prog.variables["c"]] == 1 and vm.heap[prog.variables["gbs"]] == 2)

    prog = _menu_prog(False)
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()
    vm.frame()
    vm.frame(b_pressed=True)
    vm.frame()
    check("without the arm, B does nothing (the menu stays up)",
          vm.menu_open == 1 and not vm.menu_log)
    vm.frame(a_pressed=True)
    vm.run(2)
    check("...and A picks option 0 -> the reference engine's 1",
          vm.heap[prog.variables["gbs"]] == 1)

    prog = _menu_prog(True, n=3, last_cancels=True)
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.frame()
    for _ in range(2):
        vm.frame(down=True)
        vm.frame()
    vm.frame(a_pressed=True)
    vm.run(2)
    check("cancelOnLastOption's `% n` folds the last pick onto 0",
          vm.heap[prog.variables["c"]] == 2 and vm.heap[prog.variables["gbs"]] == 0)

    # A scene load clears an UNCONSUMED latch (spec §5.3 id 30). The arm and
    # its MENU are adjacent in a lowering, but the MENU op REWINDS for as long
    # as another thread owns the UI, so the two can be frames apart - and a
    # scene change inside that window left the latch standing, which made the
    # NEXT room's first menu B-cancellable (and its cancel write -1).
    prog = m.Compiler().compile([
        {"name": "main", "events": [{"event": "change_scene",
                                     "room": 1, "x": 0, "y": 0}]},
        {"name": "other", "events": [{"event": "stop"}]}])
    vm = m.RefVM(prog.code, entry=prog.entry)
    vm.menu_cancel = 1              # armed; the MENU it was armed for never ran
    for _ in range(4):
        vm.frame()
    check("a scene change clears an unconsumed menu_cancel latch",
          vm.menu_cancel == 0, vm.menu_cancel)

    # the blob scan sees the state, so VM_ST_MENU_CANCEL folds the arm by use
    ops, toks, states = isa.decode_blob(_menu_prog(True).code)
    check("decode_blob reports state 30 for an armed menu", 30 in states, states)
    ops, toks, states = isa.decode_blob(_menu_prog(False).code)
    check("...and not for a plain one (the arm folds away)", 30 not in states, states)
    check("ISA names it menu_cancel = 30", m.STATES.get("menu_cancel") == 30)
    check("a say_choose carries the same arm",
          any(op == "SET_STATE" for op, _a in
              m.events.EVENTS["say_choose"]({"string": "hi", "var": "v",
                                             "options": ["a"], "cancel_b": 1},
                                            _cc())))


def _cc():
    """A compiler context the event lowerings can intern against."""
    comp = m.Compiler()
    return comp.context() if hasattr(comp, "context") else comp


def core_contract():
    print("vm.core: the arm is additive and folds under VM_ST_MENU_CANCEL")
    core = _read("core.mos")
    check("ST_MENU_CANCEL = 30 in core matches the ISA",
          "const ST_MENU_CANCEL = 30" in core)
    arm = core.split("case OP_MENU {")[1]
    arm = arm[:arm.index("case OP_FADE {")]
    check("the open consumes the latch under the flag",
          "if VM_ST_MENU_CANCEL {" in arm and "menu_cancel_live = menu_cancel" in arm
          and "menu_prev_b = 1" in arm)
    check("the B edge is its own latch and writes -1",
          "if menu_prev_b == 0 {" in arm and "heap[dest] = 0 - 1" in arm)
    check("A's confirm path is the same close (one `go`)",
          "if menu.confirmed() {\n                        go = 1" in arm
          and "if go != 0 {" in arm)
    sst = core.split("bank(0) local function set_state(")[1]
    sst = sst[:sst.index("-- Resolve an actor-index operand")]
    check("set_state's arm sits behind VM_ST_MENU_CANCEL",
          "if VM_ST_MENU_CANCEL {" in sst and "menu_cancel = v" in sst)
    # ANCHORED TO THE FUNCTION. This was a substring search over the whole
    # file, which boot()'s copy satisfied - so it passed while reset_scene_ui
    # did NOT clear the latch (the defect it claimed to cover, found
    # 2026-09-21): an armed menu_cancel survived the room change and made the
    # NEXT room's first menu B-cancellable.
    reset = core[core.index("local function reset_scene_ui()"):]
    reset = reset[:reset.index("bank(0) local function run_context")]
    check("a scene load clears an unconsumed latch, IN reset_scene_ui",
          "menu_cancel = 0" in reset, reset[:400])


def confirm_spends_the_press():
    """A menu confirmed with A must not ALSO dismiss the box that follows.

    vm.core samples A once at the top of the VM frame (read_input) while
    engine.menu's confirm reads the pad LIVE inside the MENU op. A press that
    lands between the two confirmed the menu with prev_a still 0, so the next
    frame read the same held A as a new edge and closed the text box the
    script opened right after - the adventure check project's "Save Game" skipped
    "Game progress has been saved." on a 6-frame press (measured in PyBoy:
    box open at frame 1, closed at frame 4 with A still held). The RefVM reads
    one sample per frame and cannot race, so the SOURCE contract is the pin;
    the RefVM run checks the mirrored rule keeps the sequence right."""
    print("MENU: the confirming press is spent for read_input too")
    arm = _read("core.mos").split("case OP_MENU {")[1]
    arm = arm[:arm.index("case OP_FADE {")]
    close = arm[arm.index("if go != 0 {"):]
    check("the close marks A seen before it writes the pick",
          close.index("prev_a = 1") < close.index("heap[dest] = menu.cursor")
          and "if go == 1 {" in close[:close.index("prev_a = 1")])
    prog = m.Compiler().compile([{"name": "main", "events": [
        {"event": "menu", "var": "c", "options": ["Yes", "No"]},
        {"event": "text", "string": "Saved."},
        {"event": "set_var", "var": "done", "value": 1},
        {"event": "stop"}]}])
    from mosaik_vm.refvm import RefVM
    vm = RefVM(prog.code, entry=prog.entry)
    done = prog.variables["done"]
    vm.frame()
    vm.frame()
    for _ in range(4):
        vm.frame(a_pressed=True)            # confirm, then keep holding A
    check("a held confirm leaves the next box open",
          vm.box_open == 1 and vm.heap[done] == 0)
    vm.frame()
    vm.frame(a_pressed=True)
    vm.frame()
    check("...and a NEW press closes it", vm.heap[done] == 1)


def platform_facing_contract():
    print("vm.canim / vm.player: a vertical platform facing wears the JUMP pose")
    canim = _read("canim.mos")
    guard = canim.split("pf & 16 != 0")[1]
    guard = guard[:guard.index("p_astate = st")]
    check("DOWN -> jump LEFT, UP -> jump RIGHT (facing + 2)",
          "var jf: u8 = p_face + 2" in guard and "p_face = jf" in guard
          and "st = 2" in guard, guard[:300])
    check("...only when the jump cell has art, else the old RIGHT idle",
          "if g_count(p_clip, 2, jf) > 0 {" in guard and "p_face = 3" in guard)
    # NOT gated on `st == 0`: a room's player spawns with grounded = 0, so the
    # airborne override has already picked FALL by the time this runs, and the
    # reference is not in a state machine here at all (the drawn anim is the
    # one the DIRECTION chose at scene load). Gated, the arm never fired in
    # the one-frame-window it exists for and the pose drew unmirrored.
    check("the vertical facing OVERRIDES the state, idle or not",
          "if st == 0 {" not in guard and "st = 2" in guard)
    check("...never while climbing (its UP is a real pose)",
          "pf & 32 == 0" in guard)
    player = _read("player.mos")
    upd = player.split("function update_platform() {")[1]
    upd = upd[:upd.index("-- HORIZONTAL (sub-pixel platformer model)")]
    check("update_platform refreshes a vertical pface to RIGHT (platform_init's "
          "dir = RIGHT, applied on the first unlocked frame)",
          "if pface < 2 {\n            pface = 3\n        }" in upd)
    check("...below the blank and ladder arms (a climbing UP is a real pose)",
          upd.index("lad_step()") < upd.index("if pface < 2 {"))
    # the source-contract half of the RefVM checks: the canim guard is reached
    # through the R2 batched flags, so the platform bit must still be bit 4
    check("the platform bit is still bit 4 of anim_flags",
          "platform<<4" in canim or "platform << 4" in canim)


def vwf_menu_contract():
    """Under VWF the menu OPTIONS go through the pen, as the reference engine's do. The
    fixed-width glyph cache keeps only 8 tiles of the band under the partition
    and a three-option menu has ~20 distinct glyphs - measured on the tutorial
    conversion, every row drew the letters of the rows before it."""
    print("compiler: VWF menus composite through the pen, fixed-width ones stay cells")
    scripts = [{"name": "main", "events": [
        {"event": "text", "string": "hi"},
        {"event": "menu", "var": "c", "options": ["Basics", "Cycling", "Fade In/Out"]},
        {"event": "stop"}]}]
    vw = m.emit_scripts_module(scripts, vwf=True)
    rc = vw.split("function render_choice(")[1]
    check("VWF: the first option of a redraw restarts the ring at column 2 "
          "(right after the cursor, the reference's textCodeGoto(3, 2))",
          "text.vwf_start(2, row)" in rc and "if row == _mrow + 1 {" in rc)
    check("VWF: the next options continue the ring (vwf_nl)",
          "text.vwf_nl(2, row)" in rc)
    check("VWF: the options are composited, not cached cells",
          "n += text.vwf_glyph(ch)" in rc and "print_string(3, row" not in rc)
    check("VWF: the cursor stays a cell plot",
          'text.print_string(1, row, ">")' in rc)
    rt = vw.split("function render_text(")[1].split("function render_choice(")[0]
    check("VWF: a text box resets the menu-row latch (a ring rewound between menus)",
          "_mrow = 255" in rt and "var _mrow: u8 = 255" in vw.split("function render_text(")[0])
    fw = m.emit_scripts_module(scripts, vwf=False)
    fc = fw.split("function render_choice(")[1]
    check("fixed-width: unchanged (cells through the glyph cache at column 3)",
          "text.print_string(3, row, " in fc and "vwf_" not in fc)


def main():
    print("Menu B-cancel (state 30) + the platform jump-pose facing")
    print("=" * 60)
    refvm_cancel()
    core_contract()
    confirm_spends_the_press()
    platform_facing_contract()
    vwf_menu_contract()
    print("=" * 60)
    if FAILS:
        print("FAILED: %d" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
