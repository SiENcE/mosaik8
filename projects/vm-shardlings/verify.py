#!/usr/bin/env python3
"""vm-shardlings -- the in-game SAVE, checked on real ROMs (GB and SMS).

    python projects/vm-shardlings/verify.py [--platform gameboy|sms ...]

The game saves ONE journey: SAVE in the pause menu, CONTINUE on the title
(offered only when the cart holds a save). This drives that on a ROM, across
real power cycles, and reads the result out of RAM:

  1. A fresh cart: the title offers NEW GAME alone; NEW GAME starts in the
     home room; SAVE before the first shardling refuses; with a party (poked
     into the heap) and a walk in Tessel, SAVE says the journey was saved.
  2. Power off, power on: the title offers CONTINUE; CONTINUE puts the player
     back in the SAVED room at the SAVED position with every one of the 128
     heap cells as saved; a second SAVE asks before it saves over.
  3. Power off, power on, CONTINUE: the second save is the one that loads.

Why each power cycle is a CHILD process: a libretro core is a process
singleton, so the only real power cycle there is a new process with the
battery RAM carried across (the `.srm` a frontend keeps). PyBoy writes its
`<rom>.ram` on stop and reads it on start, so the Game Boy takes the same
path and both consoles share one scenario.

The symbols (the heap, the room, the player, the UI state) come from the
`.noi` of a `--debug` build of a TEMP COPY made here, never from the
project's own build folder: a plain build writes no `.noi`, and a leftover
one from an older build reads the wrong addresses without a word.

The CONTINUE check is the one that guards the title script's `wait` after
`load`: LOAD does not end the thread, so without the wait the title's
`change_scene` runs in the same frame, its request overwrites the load's
(the last one raised wins) and the game starts in the home room with the
loaded heap.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

#: The project under test: this one, or a mutated COPY (`--project`, how the
#: checks themselves are mutation-tested without touching the sample).
#: Inherited by the power-cycle children through the environment.
PROJECT = os.environ.get("SHARDLINGS_VERIFY_PROJECT") or HERE

#: Where NEW GAME starts (the title's change_scene) and where this check
#: enters Tessel (its north edge, the door from Mossway).
HOME_ROOM, HOME_X, HOME_Y = 4, 112, 96
TESSEL, TESSEL_X, TESSEL_Y = 1, 144, 16

#: The pause menu's options, in order (SAVE is the fourth).
PAUSE_OPTIONS, SAVE_ROW = 5, 3

#: What this check pokes into the heap before saving (a party of two, gold,
#: an item count and two "seen" bits), and the gold of the second save.
PARTY = (("pa_sp", 3), ("pa_lv", 7), ("pa_hp", 21), ("pa_xp", 33),
         ("pb_sp", 9), ("pb_lv", 4), ("pb_hp", 15),
         ("gold", 1234), ("it_potion", 9), ("seen", 0x0209))
GOLD2 = 4321

EXT = {"gameboy": "gb", "sms": "sms"}

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAILED] %s%s" % (label, ("  -- " + detail) if detail else ""))


# ---------------------------------------------------------------------------
# The program's own facts, asked of the COMPILER (cells are first-use order
# unless scripts/heap.toml pins them; string ids are interning order).

def program():
    from mosaik_vm import loader
    return loader.compile_path(os.path.join(PROJECT, "scripts"))


def string_id(prog, ref):
    """The string id the dialogue def `ref` compiles to, or -1 when no script
    says it (an unused def is not compiled, so no box can show it)."""
    import tomllib
    with open(os.path.join(PROJECT, "scripts", "dialogue.toml"), "rb") as f:
        text = tomllib.load(f)["defs"][ref]["text"]
    return prog.strings.index(text) if text in prog.strings else -1


# ---------------------------------------------------------------------------
# One power cycle (runs in a CHILD process).

class Machine:
    """The few things the scenario needs, over PyBoy or a libretro core."""

    def __init__(self, platform, rom):
        self.platform = platform
        if platform == "gameboy":
            from pyboy import PyBoy
            self.pb = PyBoy(rom, window="null", sound_emulated=False)
        else:
            sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
            from retro import Core
            self.core = Core(genesis_core(), rom)
            self.srm = rom + ".srm"
            if os.path.exists(self.srm):
                with open(self.srm, "rb") as f:
                    self.core.load_save_ram(f.read())
        self.held = ()

    def read(self, addr):
        if self.platform == "gameboy":
            return self.pb.memory[addr]
        return self.core.ram()[addr - 0xC000]

    def write(self, addr, v):
        if self.platform == "gameboy":
            self.pb.memory[addr] = v
        else:
            self.core.poke(addr - 0xC000, v)

    def press(self, *names):
        if self.platform == "gameboy":
            for b in self.held:
                self.pb.button_release(b)
            for b in names:
                self.pb.button_press(b)
        else:
            self.core.press(*names)
        self.held = names

    def run(self, n=1):
        for _ in range(n):
            if self.platform == "gameboy":
                self.pb.tick(1, False)
            else:
                self.core.run(1)

    def power_off(self):
        if self.platform == "gameboy":
            self.pb.stop(save=True)        # writes <rom>.ram
        else:
            with open(self.srm, "wb") as f:
                f.write(self.core.save_ram())


class Game:
    def __init__(self, m, sym, cells):
        self.m, self.sym, self.cells = m, sym, cells

    def u8(self, name):
        return self.m.read(self.sym[name])

    def u16(self, name):
        a = self.sym[name]
        return self.m.read(a) | self.m.read(a + 1) << 8

    def cell(self, name):
        return self.heap()[self.cells[name]]

    def set_cell(self, name, v):
        a = self.sym["_vm_core_heap"] + 2 * self.cells[name]
        self.m.write(a, v & 0xFF)
        self.m.write(a + 1, (v >> 8) & 0xFF)

    def heap(self):
        a = self.sym["_vm_core_heap"]
        return [self.m.read(a + 2 * i) | self.m.read(a + 2 * i + 1) << 8
                for i in range(128)]

    def where(self):
        return (self.u8("_vm_core_cur_scene"), self.u16("_vm_player_px"),
                self.u16("_vm_player_py"))

    def until(self, pred, limit=1500):
        for _ in range(limit):
            if pred():
                return True
            self.m.run(1)
        return False

    def tap(self, button, settle=8):
        self.m.press(button)
        self.m.run(4)
        self.m.press()
        self.m.run(settle)

    def menu(self):
        """The open menu's option count, or 0."""
        return self.u8("_vm_core_menu_count") if self.u8("_vm_core_menu_open") else 0

    def box(self):
        """The open text box's string id, or None."""
        return self.u8("_vm_core_box_id") if self.u8("_vm_core_box_open") else None

    def title(self):
        """Boot to the title menu; its option count (1 = NEW GAME alone)."""
        self.m.run(300)          # past the boot ROM and the engine's own init
        self.until(lambda: self.menu() in (1, 2))
        return self.menu()

    def settle_in(self, room, pos=None):
        """Wait for a room load to finish (the player placed), then a beat."""
        ok = self.until(lambda: self.where()[0] == room and self.where()[1] != 0
                        and (pos is None or self.where()[1:] == pos))
        self.m.run(60)
        return ok

    def pause(self):
        """START until the pause menu is up. The watcher thread that opens
        it is started by the room's On Init, which on the SMS / Game Gear
        runs only after the room load and its fade (~200 frames), so one
        early press can fall into nothing."""
        for _ in range(12):
            self.tap("start")
            if self.until(lambda: self.menu() == PAUSE_OPTIONS, 60):
                break
        return self.menu()

    def choose_save(self):
        """Pause menu -> SAVE; the string id of the first box it opens."""
        for _ in range(SAVE_ROW):
            self.tap("down")
        return self.confirm()

    def confirm(self):
        """A on the open menu until a text box answers; its string id. A
        press that lands while the SMS / Game Gear is still drawing the menu
        is not read, so one tap is not enough there."""
        for _ in range(12):
            self.tap("a")
            if self.until(lambda: self.box() is not None, 60):
                break
        return self.box()

    def dismiss(self):
        self.tap("a")
        self.until(lambda: self.box() is None)

    def close_pause(self):
        self.until(lambda: self.menu() == PAUSE_OPTIONS)
        self.tap("b")
        self.until(lambda: self.menu() == 0)


def cycle(platform, phase, rom, noi, out):
    """One power cycle of the scenario; observations go to `out` as JSON."""
    sym = symbols(noi)
    cells = dict(program().variables)
    g = Game(Machine(platform, rom), sym, cells)
    obs = {"title": g.title()}
    if phase == "new":
        g.tap("a")                                   # NEW GAME
        obs["new_game"] = g.settle_in(HOME_ROOM) and list(g.where())
        g.pause()
        obs["no_starter_box"] = g.choose_save()
        g.dismiss()
        g.close_pause()
        # into Tessel through the engine's own scene-change request
        g.m.write(sym["_vm_core_pend_a"], TESSEL)
        for k, v in (("_vm_core_pend_b", TESSEL_X), ("_vm_core_pend_c", TESSEL_Y)):
            g.m.write(sym[k], v & 0xFF)
            g.m.write(sym[k] + 1, v >> 8)
        g.m.write(sym["_vm_core_pend_code"], 2)
        g.settle_in(TESSEL, (TESSEL_X, TESSEL_Y))
        # The party AFTER the room's On Init: a value that only the save can
        # bring back, so a room init that rewrites a saved cell on the load
        # shows up in the 128-cell comparison.
        for name, v in PARTY:
            g.set_cell(name, v)
        g.m.press("down")                            # walk somewhere new
        g.until(lambda: g.where()[2] >= TESSEL_Y + 24, 600)
        g.m.press()
        g.m.run(30)
        obs["pause_options"] = g.pause()
        obs["save_box"] = g.choose_save()
        obs["saved_where"] = list(g.where())
        obs["saved_heap"] = g.heap()
        g.dismiss()
        g.close_pause()
    elif phase == "continue":
        g.tap("a")                                   # CONTINUE (row 0)
        g.settle_in(TESSEL)
        obs["loaded_where"] = list(g.where())
        obs["loaded_heap"] = g.heap()
        g.set_cell("gold", GOLD2)
        g.pause()
        obs["over_box"] = g.choose_save()
        g.dismiss()
        g.until(lambda: g.menu() == 2)               # YES / NO
        obs["over_saved_box"] = g.confirm()          # YES
        g.dismiss()
        g.close_pause()
    else:                                            # "again"
        g.tap("a")
        g.settle_in(TESSEL)
        obs["gold"] = g.cell("gold")
    g.m.power_off()
    with open(out, "w", encoding="utf-8") as f:
        json.dump(obs, f)


# ---------------------------------------------------------------------------
# The parent: build, run three power cycles, check.

def genesis_core():
    return os.path.join(ROOT, "emu", "libretro", "genesis_plus_gx_libretro"
                        + (".dll" if os.name == "nt" else ".so"))


def symbols(noi):
    sym = {}
    with open(noi, encoding="utf-8", errors="replace") as f:
        for line in f:
            p = line.split()
            if len(p) == 3 and p[0] == "DEF":
                sym[p[1]] = int(p[2], 16)
    return sym


def debug_build(platform, tmp):
    """A `--debug` build of a temp copy: (rom, noi), or None to skip."""
    from mosaik8_build import gbdk_available
    if not gbdk_available():
        print("SKIP %s: GBDK is not installed" % platform)
        return None
    root = os.path.join(tmp, platform, "vm-shardlings")
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns("build"))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--debug", "--platform", platform, root],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    bdir = os.path.join(root, "build", platform)
    rom = os.path.join(bdir, "vm-shardlings." + EXT[platform])
    noi = os.path.join(bdir, "vm-shardlings.noi")
    if r.returncode != 0 or not (os.path.isfile(rom) and os.path.isfile(noi)):
        check("%s: the --debug build links" % platform, False,
              ((r.stdout or "") + (r.stderr or ""))[-1500:])
        return None
    return rom, noi


def run_cycle(platform, phase, rom, noi):
    out = rom + "." + phase + ".json"
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--cycle",
                        platform, phase, rom, noi, out],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if r.returncode != 0 or not os.path.isfile(out):
        check("%s: power cycle '%s' ran" % (platform, phase), False,
              ((r.stdout or "") + (r.stderr or ""))[-1500:])
        return None
    with open(out, encoding="utf-8") as f:
        return json.load(f)


def verify(platform, tmp):
    if platform == "gameboy":
        try:
            import pyboy  # noqa: F401
        except ImportError:
            print("SKIP gameboy: PyBoy is not installed")
            return
    elif not os.path.isfile(genesis_core()):
        print("SKIP sms: the Genesis Plus GX core is not installed")
        return
    built = debug_build(platform, tmp)
    if not built:
        return
    rom, noi = built
    prog = program()
    done = string_id(prog, "save_done")
    p = "%s: " % platform

    a = run_cycle(platform, "new", rom, noi)
    if a is None:
        return
    check(p + "a fresh cart's title offers NEW GAME alone", a["title"] == 1,
          "%d options" % a["title"])
    check(p + "NEW GAME starts in the home room",
          bool(a["new_game"]) and a["new_game"][0] == HOME_ROOM,
          "at %s" % (a["new_game"],))
    check(p + "SAVE with no shardling refuses",
          a["no_starter_box"] == string_id(prog, "menu_no_starter"),
          "box %s" % a["no_starter_box"])
    check(p + "the pause menu offers %d options" % PAUSE_OPTIONS,
          a["pause_options"] == PAUSE_OPTIONS, "%d" % a["pause_options"])
    check(p + "SAVE says the journey was saved", a["save_box"] == done,
          "box %s" % a["save_box"])
    saved = tuple(a["saved_where"])
    check(p + "the save was made away from where NEW GAME starts",
          saved[0] == TESSEL and saved[1:] != (HOME_X, HOME_Y)
          and saved[2] > TESSEL_Y, "saved at %s" % (saved,))

    b = run_cycle(platform, "continue", rom, noi)
    if b is None:
        return
    check(p + "after a power cycle the title offers CONTINUE", b["title"] == 2,
          "%d options" % b["title"])
    check(p + "CONTINUE returns to the saved room and position",
          tuple(b["loaded_where"]) == saved,
          "loaded %s, saved %s" % (tuple(b["loaded_where"]), saved))
    diff = [i for i in range(128) if b["loaded_heap"][i] != a["saved_heap"][i]]
    check(p + "CONTINUE restores all 128 heap cells", not diff,
          "cells differ: %s" % diff[:12])
    check(p + "SAVE over a journey asks first",
          b["over_box"] == string_id(prog, "save_overwrite"),
          "box %s" % b["over_box"])
    check(p + "...and YES saves", b["over_saved_box"] == done,
          "box %s" % b["over_saved_box"])

    c = run_cycle(platform, "again", rom, noi)
    if c is None:
        return
    check(p + "the second save is the one that loads", c["gold"] == GOLD2,
          "gold %s" % c["gold"])


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--cycle":
        cycle(*sys.argv[2:7])
        return 0
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", nargs="+", default=["gameboy", "sms"],
                    choices=sorted(EXT))
    ap.add_argument("--project", help="check a copy of this project instead")
    args = ap.parse_args()
    if args.project:
        global PROJECT
        PROJECT = os.environ["SHARDLINGS_VERIFY_PROJECT"] = os.path.abspath(args.project)
    tmp = tempfile.mkdtemp(prefix="shardlings_save_")
    try:
        for platform in args.platform:
            print("[%s]" % platform)
            verify(platform, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 50)
    if failed:
        print("%d check(s) FAILED, %d passed" % (failed, passed))
        return 1
    print("All vm-shardlings save checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
