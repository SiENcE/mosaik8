#!/usr/bin/env python3
"""THE TYPEWRITER TEXT REVEAL (`TEXT_SPEED` 0x3E) + the per-char BLIP
(`TEXT_BLIP` 0x3F) - Stage F of the reference-engine event-lowering plan.

The reference engine's model, from its own ui.c: `text_draw_speed` 0..7 indexes
`ui_time_masks` {0,0,1,3,7,15,31,63}, one printable char is drawn whenever
`game_time & mask == 0` (LCD frames), 0 is instant, A or B HELD fast-forwards
(INPUT_A_OR_B_PRESSED), and after each drawn char it plays `text_sound` if one
is set. Ours mirrors all of it on the elapsed-display-frame clock the overlay
curtain established, through two seams that keep non-users byte-identical:

  * every reader of the reveal state folds away with the two op DEFINES
    (dispatch pruning), so a program that never sets a speed compiles none of
    it - measured bit-for-bit on the reference-engine sample conversion;
  * the per-char plotting lives in the GENERATED scripts module
    (`render_text_step`), emitted only when the blob sets a speed and reached
    through the `core.set_text_step` seam the generated rooms.mos wires from
    the same scan (the set_save pattern).

Measured live on the platformer conversion's cutscene (the source project that uses
both events): seven boxes reveal at exactly 1 char per 2 display frames
(speed 2), the blip state switches 0 -> 300 -> 400 Hz as the cutscene characters' custom
events run, holding A fast-forwards without dismissing, and a short tap after
completion dismisses.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm
from mosaik_vm import isa
from mosaik_vm.refvm import RefVM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def test_refvm_state():
    print("\n[RefVM: the two ops are state writes]")
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "text_speed", "speed": 2, "fastforward": True},
            {"event": "text_blip", "freq": 400, "frames": 2},
            {"event": "text_blip", "freq": 0},
            {"event": "text_speed", "speed": 9, "fastforward": False},
            {"event": "stop"}]},
    ])
    vm = RefVM(prog.code, entry=prog.entry)
    check(vm.text_speed == 0 and vm.blip_freq == 0,
          "defaults: speed 0 (instant) and blip off, mirroring vm.core's BSS")
    vm.frame()
    check(vm.text_speed == 7 and vm.text_ff == 0,
          "a speed past 7 CLAMPS to 7 in the event lowering (min, not mask: "
          "9 & 7 would be 1, a fast reveal nobody asked for); the arm's & 7 "
          "is only the belt for hand-authored bytecode")
    check(vm.blip_freq == 0,
          "freq 0 turns the blip back off (the reference engine's SFX_STOP_BANK)")


def test_speed_semantics():
    print("\n[the masks are the reference engine's ui_time_masks]")
    # (1 << (idx-1)) - 1 for idx >= 1 IS their table {0,0,1,3,7,15,31,63}.
    theirs = [0, 0, 1, 3, 7, 15, 31, 63]
    ours = [0] + [(1 << (i - 1)) - 1 for i in range(1, 8)]
    check(ours == theirs,
          "the derived mask matches their ui_time_masks row for row: %s" % ours)


def test_step_renderer_emission():
    print("\n[the step renderer is emitted by USE, and forces the blob form]")
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "text_speed", "speed": 1},
            {"event": "text", "string": "hi"},
            {"event": "stop"}]},
    ])
    src = prog.to_scripts_mos()
    check("function render_text_step" in src,
          "a blob that sets a speed gets the step renderer")
    check("STRINGS" in src and "STR_OFF" in src,
          "...and the BLOB string form, even under the size threshold (a "
          "per-char walk has no shape over the switch form's print_string "
          "literals)")
    exports = [l for l in src.splitlines() if l.strip().startswith("export")][0]
    check("render_text_step" in exports,
          "...and exports it, so rooms.mos can register the seam")

    prog2 = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [
            {"event": "text", "string": "hi"},
            {"event": "stop"}]},
    ])
    src2 = prog2.to_scripts_mos()
    check("render_text_step" not in src2,
          "a blob that never sets a speed emits none of it (byte-identical)")
    check("STRINGS" not in src2,
          "...and keeps the small-text switch form it always had")


def test_engine_lockstep():
    print("\n[lockstep across the surfaces]")
    check(isa.OPS["TEXT_SPEED"] == (0x3E, ["u8", "u8"]),
          "TEXT_SPEED is 0x3E (speed, ff)")
    check(isa.OPS["TEXT_BLIP"] == (0x3F, ["u16", "u8"]),
          "TEXT_BLIP is 0x3F (freq16, frames)")
    core = _read("lib", "vm", "core.mos")
    check("const OP_TEXT_SPEED = 0x3E" in core
          and "const OP_TEXT_BLIP = 0x3F" in core,
          "core.mos's opcode consts match the ISA bytes")
    check("if VM_OP_TEXT_SPEED {" in core and "if VM_OP_TEXT_BLIP {" in core,
          "both arms carry their dispatch-pruning guards")
    check("function set_text_step" in core,
          "the step-renderer seam exists")
    # The read_input dismiss guard: an A press mid-reveal belongs to the
    # fast-forward, and read_input runs BEFORE run_scripts - so the gate is
    # what stops one press ending a box whose text has not appeared yet.
    ri = core.split("local function read_input")[1][:1400]
    check("tw_run == 1" in ri,
          "read_input's dismiss stands down while a reveal runs")
    check("if VM_OP_TEXT_SPEED {" in ri,
          "...and that gate folds away with the op define")
    # A scene change mid-reveal must clear the flag, or the gate above blocks
    # the NEXT room's first box from ever dismissing.
    # Sliced to the END of the function, not to a byte count: a `[:700]` window
    # silently becomes a different check every time a line is added above the
    # one it looks for (adding the UI-latch clear on 2026-09-21 pushed
    # `tw_run = 0` out of the window and failed this).
    rs = core.split("local function reset_scene_ui")[1]
    rs = rs[:rs.index("bank(0) local function run_context")]
    check("tw_run = 0" in rs,
          "reset_scene_ui clears the reveal flag")
    # The reveal is paced on the DISPLAY clock, not on calls: a VM frame is
    # 1..3 LCD frames depending on the room (the curtain/music rule).
    tw = core.split("local function tw_step")[1][:1600]
    check("system.frames()" in tw,
          "tw_step paces on elapsed display frames")
    check("el = 8" in tw,
          "...capped like the music catch-up, so a blocked frame cannot "
          "dump the whole text")
    spec = _read("docs", "vm8-spec.md")
    check(any("| TEXT_SPEED |" in l for l in spec.splitlines())
          and any("| TEXT_BLIP |" in l for l in spec.splitlines()),
          "the spec documents both rows")


def test_rooms_wiring():
    print("\n[rooms.mos wires the seam from the same scan (set_save pattern)]")
    gen = _read("mosaik_vm", "rooms", "generate.py")
    check('_uses(evs, ("text_speed",))' in gen,
          "generate.py scans for the event")
    start = _read("mosaik_vm", "rooms", "emit_start.py")
    check("core.set_text_step(scripts.render_text_step)" in start,
          "emit_start registers the generated renderer")


def test_byte_identical_off():
    print("\n[byte-identical off]")
    prog = mosaik_vm.Compiler().compile([
        {"name": "main", "events": [{"event": "text", "string": "plain"},
                                    {"event": "stop"}]}])
    names = {n for _o, n, _s, _r in isa.iter_instructions(prog.code)}
    check("TEXT_SPEED" not in names and "TEXT_BLIP" not in names,
          "a program that never sets a speed or blip emits neither op, so "
          "both arms and every reveal reader prune away")


if __name__ == "__main__":
    print("=== typewriter_test: TEXT_SPEED / TEXT_BLIP ===")
    test_refvm_state()
    test_speed_semantics()
    test_step_renderer_emission()
    test_engine_lockstep()
    test_rooms_wiring()
    test_byte_identical_off()
    print()
    if _FAILED:
        print("FAILED (%d):" % len(_FAILED))
        for f in _FAILED:
            print("  - " + f)
        sys.exit(1)
    print("typewriter_test: all passed")
