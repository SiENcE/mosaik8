#!/usr/bin/env python3
"""A text box or menu whose owning thread is KILLED closes (the 2026-11-03
review's M6; decision D1 2026-11-07: close it).

`core.kill()` (THREAD_STOP, Stop Update, the park kill, MUSIC_PLAY replacing
a song thread) cleared the UI latches and nothing else: the GB family's
window stayed shown with its sprite cut armed, and on SMS / GG / PCE the box
cells stayed in the scene map with the scroll snap and the sprite cut held.
The reference never closes a box on its owner's death, but there a modal box
runs inside its instruction and nothing can kill the owner; our threads run
while a box is up, which is the only reason the case exists here.

The fix is a DEFERRED close: `kill()` draws nothing (it runs inside another
thread's slice), it marks `ui_orphan` (1 = box, 2 = menu); `read_input`, at
the top of the next frame, runs the EXISTING close for that kind: the box's
`box_teardown()` (now the one box close: the A dismiss, box_chain's linger
and the orphan) and the menu's `menu_close()` (factored out of the MENU arm).
`reset_scene_ui` clears the latch. Folded on `VM_UI_ORPHAN`, stated by the
build when the blob carries an external-kill op (THREAD_STOP, A_STOP_UPDATE,
MUSIC_PLAY) or `park_updates` is on, AND a UI op (UI_TEXT, MENU); not on the
Lynx, whose box is redrawn per present and heals itself.

Checks:
  - the source contract (kill, read_input, reset_scene_ui, the build flag);
  - real ROMs, from a fresh project (`tests/vm_fixture.py`): thread A opens a
    box (or a menu), the main thread stops it 30 frames later, then opens a
    box of its own 120 frames after that.
      GB: the window goes OFF after the kill and comes back for the next box
      (the UI is not stuck either).
      SMS: the screen returns to the picture it had before the box opened
      (the box cells are gone from the scene map), then the next box draws.
    A CONTROL arm with no kill shows the window never going off, so the
    "off" the check looks for is the close and nothing else. Mutation: on
    the unfixed engine both ROM checks failed (the window stayed on / the
    "HELLO" cells stayed on the SMS screen; measured 2026-11-07).
"""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik8_build  # noqa: E402
import vm_fixture  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _src(rel):
    return open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8").read()


def _function(text, name):
    """One mosaik function's text, to its closing brace at its own indent."""
    m = re.search(r"(?:bank\(0\) )?(?:hot )?(?:local )?function %s\(" % re.escape(name), text)
    if not m:
        return ""
    start = text.rfind("\n", 0, m.start()) + 1
    indent = len(text[start:m.start()]) - len(text[start:m.start()].lstrip())
    end = text.find("\n" + " " * indent + "}\n", m.end())
    return text[start:end + indent + 3]


def _code(body):
    return "\n".join(ln.split("--")[0].rstrip() for ln in body.splitlines()
                     if ln.split("--")[0].strip())


def source_contract():
    print("\n[the source contract]")
    core = _src("lib/vm/core.mos").replace("\r\n", "\n")
    check("the latch is declared INSIDE its fold",
          re.search(r"\n    if VM_UI_ORPHAN \{\n(?:\s*--.*\n)*\s*var ui_orphan: u8\n", core)
          is not None)
    kill = _code(_function(core, "kill"))
    own = kill[kill.find("if ctx == ui_owner {"):]
    m = re.search(r"if VM_UI_ORPHAN \{(.*?)\n                \}", own, re.S)
    arm = m.group(1) if m else ""
    check("kill(): an owned OPEN box marks ui_orphan = 1, an open menu 2",
          "if box_open == 1 {" in arm and "ui_orphan = 1" in arm
          and "menu_open == 1" in arm and "ui_orphan = 2" in arm, own[:700])
    check("kill(): it DRAWS nothing (no close, no repaint, no to_bkg)",
          not any(w in kill for w in ("box_teardown(", "menu_close(", "to_bkg(",
                                      "g_redraw(", "clear_area(")), kill)
    check("kill(): the latches are still released (box_open, menu_open, ui_owner)",
          "box_open = 0" in own and "menu_open = 0" in own and "ui_owner = 255" in own)
    ri = _code(_function(core, "read_input"))
    head = ri[:ri.find("var a: u8")]
    check("read_input: the orphan is closed at the top of the frame, under the fold",
          re.search(r"if VM_UI_ORPHAN \{\s*if ui_orphan != 0 \{\s*ui_orphan_close\(\)",
                    head) is not None, head[:900])
    oc = _code(_function(core, "ui_orphan_close"))
    check("ui_orphan_close: a box runs box_teardown, a menu menu_close",
          "box_teardown()" in oc and "menu_close()" in oc and "ui_orphan = 0" in oc, oc)
    check("ui_orphan_close is not pinned home (code_banks banks it)",
          re.search(r"bank\(0\) (?:local )?function ui_orphan_close\(", core) is None
          and "function ui_orphan_close(" in core)
    dismiss = ri[ri.find("if a_edge == 1 {"):]
    check("the A dismiss closes through box_teardown (one box close)",
          "box_teardown()" in dismiss and "text.to_bkg()" not in dismiss, dismiss[:900])
    td = _code(_function(core, "box_teardown"))
    check("box_teardown lifts the PC Engine's sprite cut too",
          'if platform == "pce" { if VM_BOX_CUT { ui_sprite_cut(255) } }' in td, td)
    check("box_teardown is not pinned home",
          re.search(r"bank\(0\) (?:local )?function box_teardown\(", core) is None)
    menu = core[core.find("case OP_MENU {"):core.find("case OP_FADE {")]
    check("the MENU arm closes through menu_close()",
          "menu_close()" in menu and "text.to_bkg()" not in menu)
    mc = _code(_function(core, "menu_close"))
    check("menu_close: clears row - 1 .. row + count from the STORED geometry",
          "text.clear_area(0, menu_row - 1, SCREEN_COLS, menu_count + 2)" in mc, mc)
    rs = _code(_function(core, "reset_scene_ui"))
    check("reset_scene_ui clears the latch (the scene change needs no special case)",
          re.search(r"if VM_UI_ORPHAN \{\s*ui_orphan = 0", rs) is not None)
    build = _src("mosaik8_build.py")
    m = re.search(r"def _wants_ui_orphan\(.*?\n(?=def |\Z)", build, re.S)
    fn = m.group(0) if m else ""
    check("the build states VM_UI_ORPHAN from a kill op AND a UI op",
          all(k in fn for k in ("VM_OP_THREAD_STOP", "VM_OP_A_STOP_UPDATE",
                                "VM_OP_MUSIC_PLAY", "VM_OP_UI_TEXT", "VM_OP_MENU")), fn)
    check("compile_program always supplies VM_UI_ORPHAN = False",
          "setdefault('VM_UI_ORPHAN', False)" in _src("mosaik/compiler.py"))


def flag_cases():
    print("\n[the build flag]")
    want = getattr(mosaik8_build, "_wants_ui_orphan", None)
    if want is None:
        check("mosaik8_build._wants_ui_orphan exists", False)
        return

    def d(*ops):
        return {"VM_OP_" + o: True for o in ops}
    check("THREAD_STOP + UI_TEXT -> on", want(d("THREAD_STOP", "UI_TEXT"), False, "gameboy"))
    check("A_STOP_UPDATE + MENU -> on", want(d("A_STOP_UPDATE", "MENU"), False, "sms"))
    check("MUSIC_PLAY + UI_TEXT -> on", want(d("MUSIC_PLAY", "UI_TEXT"), False, "pce"))
    check("park_updates + UI_TEXT -> on", want(d("UI_TEXT"), True, "gameboy"))
    check("a UI op alone -> off", not want(d("UI_TEXT", "MENU"), False, "gameboy"))
    check("a kill op alone -> off", not want(d("THREAD_STOP"), True, "gameboy"))
    check("the Lynx heals itself -> off", not want(d("THREAD_STOP", "UI_TEXT"), False, "lynx"))


# A room that fills the SMS screen (32 x 24), checkered, so a box drawn into
# the scene map covers scene tiles; a MENU room: no player, a still picture.
WORLD = """[[scene]]
name = "hall"
scene_type = "menu"
map = %s

[world]
module = "scenes"
map_w = 32
map_h = 24
vm = true

[tileset]
png = "tiles.png"
""" % vm_fixture.grid([[(x + y) % 2 for x in range(32)] for y in range(24)])


def script(kind, kill=True):
    ui = ('{ event = "text", string = "HELLO" }' if kind == "box" else
          '{ event = "menu", var = "pick", options = ["ONE", "TWO"] }')
    stop = '  { event = "thread_stop", var = "h" },\n' if kill else ""
    return """[[script]]
name = "main"
events = [
  { event = "wait", frames = 20 },
  { event = "start_thread", script = "talker" },
  { event = "wait", frames = 30 },
%s  { event = "wait", frames = 120 },
  { event = "text", string = "AFTER" },
  { event = "stop" },
]

[[script]]
name = "talker"
events = [
  { event = "handle", var = "h" },
  %s,
  { event = "stop" },
]
""" % (stop, ui)


def _runs(trail):
    """Collapse a per-frame trail into [(value, first_frame, length)]."""
    out = []
    for i, v in enumerate(trail):
        if out and out[-1][0] == v:
            out[-1][2] += 1
        else:
            out.append([v, i, 1])
    return out


def _gb_window_trail(rom, frames):
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", cgb=False)
    trail = []
    for _ in range(frames):
        pb.tick()
        trail.append(bool(pb.memory[0xFF40] & 0x20) and pb.memory[0xFF4A] < 144)
    pb.stop(save=False)
    return trail


def gb_rom():
    print("\n[GB ROMs: the window across the kill]")
    try:
        import pyboy  # noqa: F401
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    for kind, kill in (("box", True), ("menu", True), ("box", False)):
        label = "%s%s" % (kind, "" if kill else " (control: no kill)")
        fx = vm_fixture.build(WORLD, script(kind, kill))
        try:
            if not fx.rom:
                check("[%s] the fixture builds" % label, False,
                      (fx.scripts.stderr + fx.build.stdout + fx.build.stderr)[-800:])
                continue
            runs = [r for r in _runs(_gb_window_trail(fx.rom, 900))]
            shape = [(v, n) for v, _f, n in runs]
            # Window ON runs that last (a 1-frame blip is not a box).
            on = [r for r in runs if r[0] and r[2] >= 8]
            if kill:
                check("[%s] the window opens, goes OFF after the kill, and opens "
                      "again for the next box" % label,
                      len(on) >= 2 and any((not v) and n >= 20 and f > on[0][1]
                                           and f < on[-1][1] for v, f, n in runs),
                      shape)
            else:
                check("[%s] the window opens and STAYS on (nothing closes an owned "
                      "box but its own dismiss)" % label,
                      len(on) == 1 and on[0][1] + on[0][2] == 900, shape)
        finally:
            shutil.rmtree(fx.tmp, ignore_errors=True)


SMS_CHILD = r"""
import sys, json, hashlib
sys.path.insert(0, sys.argv[1])
from retro import Core
c = Core(sys.argv[2], sys.argv[3])
seen = []
for f in range(700):
    c.run(1)
    seen.append(hashlib.md5(c.image().convert("RGB").tobytes()).hexdigest())
print(json.dumps(seen))
"""


def held_pictures(frames, hold=10):
    """The pictures that stay on screen for `hold` frames or more, in order,
    consecutive repeats collapsed: a fade step or a staged draw is shorter."""
    out = []
    for v, _f, n in _runs(frames):
        if n >= hold and (not out or out[-1] != v):
            out.append(v)
    return out


def sms_rom():
    print("\n[SMS ROM: the box cells leave the scene map]")
    lr = os.path.join(ROOT, "emu", "libretro")
    cores = [c for c in os.listdir(lr) if c.startswith("genesis_plus_gx_libretro")] \
        if os.path.isdir(lr) else []
    if not cores:
        print("  (skip: genesis_plus_gx not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    fx = vm_fixture.build(WORLD, script("box"), "sms")
    try:
        if not fx.rom:
            check("the SMS fixture builds", False, (fx.build.stdout + fx.build.stderr)[-800:])
            return
        # A libretro core is a process singleton: one ROM per child.
        p = subprocess.run([sys.executable, "-c", SMS_CHILD, lr,
                            os.path.join(lr, cores[0]), fx.rom],
                           capture_output=True, text=True, timeout=600)
        if p.returncode != 0:
            check("the SMS child ran", False, p.stderr[-800:])
            return
        seen = json.loads(p.stdout.strip().splitlines()[-1])
        held = held_pictures(seen)
        names = {}
        shape = [names.setdefault(h, "P%d" % len(names)) for h in held]
        # (blank) -> the ROOM -> HELLO -> the ROOM AGAIN -> AFTER. Unfixed it
        # was (blank) -> ROOM -> HELLO -> AFTER (drawn over HELLO's cells): no
        # picture came back.
        back = [i for i in range(len(held)) for k in range(i + 2, len(held))
                if held[i] == held[k]]
        check("the room comes back after the killed box, then the next box draws",
              bool(back) and held[-1] != held[back[0]], shape)
    finally:
        shutil.rmtree(fx.tmp, ignore_errors=True)


def main():
    print("=== ui_orphan_close_test ===")
    source_contract()
    flag_cases()
    gb_rom()
    sms_rom()
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
