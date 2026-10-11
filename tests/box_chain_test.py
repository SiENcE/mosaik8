#!/usr/bin/env python3
"""`[build] box_chain`: a text box followed AT ONCE by another of the same size
keeps the box and only turns the page (2026-10-11).

Two boxes in a row used to close the first completely and open the second:
measured on the studio's SMS / Game Gear shooter, the box vanished for ~4
display frames and the next one was redrawn row by row; on the Game Boy family
the window was off for one display frame. With the knob, a dismissed box
LINGERS for the rest of its frame (vm.core `box_linger`); if the same thread's
next op is UI_TEXT of the same size, `box_next_page` redraws only the paper and
the text, and the op waits as for any open box. Anything else closes the
lingering box one frame later (`box_teardown`, from read_input), a MENU first
closes it, and a scene change or boot clears the latch.

The contract this pins:
  - the knob: absent = off, a bool only, an APPLIED key;
  - the build states VM_BOX_CHAIN on the GBDK consoles only (the Lynx / PC
    Engine box redraws every present), and compile_program defaults it False;
  - the vm.core arms, sliced per function (a whole-file match finds copies);
  - a real ROM (GBDK + PyBoy present): two held boxes in a row - the window
    is never off between them with the knob, and IS off at least one frame
    without it (the A/B is what proves the check can fail).
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik8_build  # noqa: E402
from mosaik8_build import BuildConfig  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _src(rel):
    return open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8").read()


def _function(text, name):
    """One mosaik function's text, to its closing brace at its own indent."""
    m = re.search(r"(?:bank\(0\) )?(?:local )?function %s\(" % re.escape(name), text)
    if not m:
        return ""
    start = text.rfind("\n", 0, m.start()) + 1
    indent = m.start() - start
    end = text.find("\n" + " " * indent + "}\n", m.end())
    return text[start:end + indent + 3]


def cfg(value):
    c = BuildConfig.__new__(BuildConfig)
    c.config = {"build": {}} if value is None else {"build": {"box_chain": value}}
    return c


def knob():
    print("\n[the knob and the define]")
    check("absent -> off", cfg(None).get_box_chain() is False)
    check("true reads true", cfg(True).get_box_chain() is True)
    for bad in (1, "yes"):
        try:
            cfg(bad).get_box_chain()
            check("%r refused" % (bad,), False)
        except ValueError:
            check("%r refused" % (bad,), True)
    check("an APPLIED build key", "box_chain" in BuildConfig.APPLIED_KEYS["build"])
    build = _src("mosaik8_build.py")
    m = re.search(r"if self\.config\.get_box_chain\(\):(.*?)\n            if ", build, re.S)
    arm = m.group(1) if m else ""
    check("the build states VM_BOX_CHAIN under the knob ...",
          "defines['VM_BOX_CHAIN'] = True" in arm, arm[:200])
    check("... on the GBDK consoles only",
          "framework_for_platform(platform) == 'gbdk'" in arm)
    check("compile_program always supplies VM_BOX_CHAIN = False",
          "setdefault('VM_BOX_CHAIN', False)" in _src("mosaik/compiler.py"))


def source_contract():
    print("\n[the vm.core arms]")
    core = _src("lib/vm/core.mos")
    check("the latch is declared INSIDE its fold",
          re.search(r"\n    if VM_BOX_CHAIN \{\n        var box_linger: u8\n    \}", core)
          is not None)
    ri = _function(core, "read_input")
    dismiss = ri[ri.find("if a_edge == 1 {"):]
    check("read_input: the dismiss LINGERS under the fold (before the old teardown)",
          re.search(r"if a_edge == 1 \{\s*\n\s*if VM_BOX_CHAIN \{(?:\s*--.*\n)*\s*box_open = 0"
                    r"\s*\n\s*box_linger = 1\s*\n\s*a_edge = 0\s*\n\s*return", dismiss)
          is not None)
    head = ri[:ri.find("var a: u8")]
    check("read_input: a lingering box with no next page is torn down first",
          "if box_linger == 1 {" in head and "box_teardown()" in head)
    td = _function(core, "box_teardown")
    check("box_teardown: SMS / GG stage the close (ui_close), the rest hide "
          "the window and release the latch",
          "box_linger = 0" in td and "ui_close = 1" in td and "ui_owner = 255" in td
          and "text.to_bkg()" in td)
    np = _function(core, "box_next_page")
    check("box_next_page: same owner AND same size turns the page",
          "if ui_owner == c {" in np and "size_box(id)" in np
          and "if box_h == keep_h {" in np and "box_open = 1" in np)
    check("... SMS / GG redraw border + paper, then the text (the staged open)",
          "ui_stage = 1" in np)
    check("... any other case closes the old box first (and restores box_h)",
          "box_h = keep_h" in np and np.rstrip().endswith("box_teardown()\n        }")
          or "box_teardown()" in np[np.find("box_h = keep_h"):])
    step = core[core.find("case OP_UI_TEXT {"):core.find("case OP_MENU {")]
    check("OP_UI_TEXT: a lingering box goes to box_next_page before any open",
          step.find("box_next_page(c, id)") != -1
          and step.find("box_next_page(c, id)") < step.find("ui_owner = c"))
    menu = core[core.find("case OP_MENU {"):]
    menu = menu[:menu.find("ui_owner = c")]
    check("OP_MENU: a lingering box closes before a menu opens",
          "if box_linger == 1 {" in menu and "box_teardown()" in menu)
    run = _function(core, "run")
    pub = run[:run.find("g_ui_freeze(uib)")]
    pub = pub[pub.rfind("var uib: u8 = 0"):]
    check("run(): the UI freeze holds while a box lingers (SMS / GG keep the "
          "scroll snapped under it)",
          re.search(r"if VM_BOX_CHAIN \{(?:\s*--.*\n)*\s*if box_linger == 1 \{\s*\n\s*uib = 1",
                    pub) is not None, pub[-400:])
    check("reset_scene_ui and boot clear the latch",
          "box_linger = 0" in _function(core, "reset_scene_ui")
          and "box_linger = 0" in _function(core, "boot"))


#: Two HELD boxes back to back after the room is up (the uiscroll sample's
#: main script replaced): the switch from ONE to TWO is the thing measured.
BOOT = '''[[script]]
name = "main"
events = [
  { event = "wait", frames = 40 },
  { event = "text", string = "ONE", hold = 40 },
  { event = "text", string = "TWO", hold = 40 },
  { event = "wait", frames = 200 },
  { event = "stop" },
]
'''


def _window_trail(rom, frames):
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", cgb=False)
    trail = []
    for _ in range(frames):
        pb.tick()
        on = bool(pb.memory[0xFF40] & 0x20) and pb.memory[0xFF4A] < 144
        trail.append(on)
    pb.stop(save=False)
    return trail


def _build(chain):
    src = os.path.join(ROOT, "projects", "vm-uiscroll")
    from mosaik_vm.rooms import generate_rooms
    tmp = tempfile.mkdtemp(prefix="box_chain_")
    root = os.path.join(tmp, "p")
    shutil.copytree(src, root, ignore=shutil.ignore_patterns("build"))
    if chain:
        toml = os.path.join(root, "mosaik.toml")
        with open(toml, encoding="utf-8") as f:
            text = f.read()
        text = text.replace("[build]\n", "[build]\nbox_chain = true\n", 1)
        with open(toml, "w", encoding="utf-8") as f:
            f.write(text)
    evt = os.path.join(root, "scripts", "main.evt.toml")
    with open(evt, encoding="utf-8") as f:
        n = f.read().count('name = "main"')     # the sample has the one script
    with open(evt, "w", encoding="utf-8") as f:
        f.write(BOOT)
    g = subprocess.run([sys.executable, "-m", "mosaik_vm", os.path.join(root, "scripts"),
                        "-o", os.path.join(root, "src", "scripts.mos")],
                       capture_output=True, text=True, cwd=ROOT, encoding="utf-8", errors="replace")
    generate_rooms(root)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "gameboy", root],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = os.path.join(root, "build", "gameboy")
    roms = [os.path.join(out, f) for f in os.listdir(out) if f.endswith(".gb")] \
        if os.path.isdir(out) else []
    return tmp, n, g, r, (roms[0] if roms else None)


def rom():
    print("\n[a real ROM: the window across a box -> box switch]")
    try:
        import pyboy  # noqa: F401
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    gaps = {}
    for chain in (False, True):
        tmp, n, g, r, path = _build(chain)
        try:
            label = "on" if chain else "off"
            check("[%s] the fixture's boot script was replaced and recompiled" % label,
                  n == 1 and g.returncode == 0 and "2 strings" in g.stdout,
                  (g.stdout + g.stderr)[-300:])
            if not path:
                check("[%s] the project builds" % label, False, (r.stdout + r.stderr)[-600:])
                return
            trail = _window_trail(path, 600)
            first = trail.index(True) if True in trail else -1
            last = len(trail) - 1 - trail[::-1].index(True) if True in trail else -1
            between = trail[first:last + 1] if first >= 0 else []
            # STIMULUS: two boxes of 40 VM frames each, so the window must be
            # up for a long stretch in total, and come down at the end.
            check("[%s] the boxes were up (the window shown %d frames)" % (label, sum(between)),
                  sum(between) > 60, sum(between))
            check("[%s] ... and closed at the end" % label, 0 <= last < len(trail) - 20, last)
            gaps[chain] = between.count(False)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print("  window-off frames between the first and the last box frame: off %s, on %s"
          % (gaps.get(False), gaps.get(True)))
    check("WITHOUT the knob the switch shows a frame with no box (the check can fail)",
          gaps.get(False, 0) >= 1, gaps)
    check("WITH the knob the box never leaves the screen between the two pages",
          gaps.get(True, -1) == 0, gaps)


if __name__ == "__main__":
    print("=" * 60)
    print("[build] box_chain: a box followed by a box keeps the box")
    print("=" * 60)
    knob()
    source_contract()
    rom()
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("box_chain_test: all checks passed")
