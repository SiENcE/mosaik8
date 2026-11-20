#!/usr/bin/env python3
"""vm-fjaum -- the in-game SAVE and the starter, checked on real ROMs
(GB and SMS).

    python projects/vm-fjaum/verify.py [--platform gameboy|sms ...]

The game saves ONE journey: SAVE in the pause menu, CONTINUE on the title
(offered only when the cart holds a save). This drives that on a ROM, across
real power cycles, and reads the result out of RAM:

  1. A fresh cart: the title offers NEW GAME alone; NEW GAME starts in the
     home room; SAVE before the first fjaum refuses; with a party (poked
     into the heap) and a walk in Tessel, SAVE says the journey was saved.
  2. Power off, power on: the title offers CONTINUE; CONTINUE puts the player
     back in the SAVED room at the SAVED position with every heap cell (all
     `[build] vm_heap` of them) as saved; a second SAVE asks before it saves
     over.
  3. Power off, power on, CONTINUE: the second save is the one that loads.
  4. A fresh cart, NEW GAME, the elder, EMBERKIT (G1a, 2026-11-17): the
     party lives in heap ARRAYS and a member's moves come from the
     `learnset` data table, so the starter must land in slot 0 of every
     array (species, level 5, its full HP from the `species` table), with
     RAM and CINDER in its first two move slots and the other two empty,
     `party_n` 1 and slots 1 and 2 still empty.
  5. (G1b) a battle, entered from the title with a poked party: the HUD's
     script-written tiles (names, level, HP bar) survive the first box close
     (on the SMS that close used to reload the tileset over them), FIGHT
     opens the move menu on the lead's four slots (options that ARE list
     items, `$mvn[mv0]$`), and slot 1 says "YOU USED CINDER!" first.
  6. (Chapter 1, C1c) the ways between the rooms: the player is put beside
     every exit of the zone-1 rooms (each door and exit trigger), walks into
     it, and must arrive in the room behind it where that door puts the player
     (Mossway <-> Tessel, the Ridgeway and Mosswood; Mosswood <-> the
     shrine's hall; the Ridgeway <-> its top).
  7. (Chapter 1, C1d) the people: with a party, the player walks up to each
     Chapter 1 trainer and the Moss Keeper where the world places them,
     presses A, reads their lines, and must stand in the battle room against
     that trainer (`trainer` = its row + 1).

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
PROJECT = os.environ.get("FJAUM_VERIFY_PROJECT") or HERE

#: Where NEW GAME starts (the title's change_scene) and where this check
#: enters Tessel (its north edge, the door from Mossway).
HOME_ROOM, HOME_X, HOME_Y = 4, 112, 96
TESSEL, TESSEL_X, TESSEL_Y = 1, 144, 16

#: The pause menu's options, in order (PARTY, ITEMS, DEX, TRAINER, SAVE,
#: CLOSE since G1d: SAVE is the fifth).
PAUSE_OPTIONS, SAVE_ROW = 6, 4

#: What this check pokes into the heap before saving (a party of two, gold,
#: an item count and two "seen" bits), and the gold of the second save.
PARTY = (("p_sp", 3), ("p_lv", 7), ("p_hp", 21), ("p_xp", 33),
         ("p_sp+1", 9), ("p_lv+1", 4), ("p_hp+1", 15), ("party_n", 2),
         ("gold", 1234), ("it_tonic", 9), ("dex_seen", 0x0209))
GOLD2 = 4321

#: The elder (G1a): the house's room, a spot inside its door straight below
#: the elder (who stands at x 80, y 56), the y the walk up stops at (right
#: under the elder: an actor does not block the player, so a TIMED walk
#: passes through it and faces the wall), the starter menu's row for
#: EMBERKIT and the level every starter is given.
ELDER_ROOM, ELDER_X, ELDER_Y, ELDER_FACE_Y = 5, 80, 112, 74
STARTER_ROW, STARTER_LV = 1, 5

#: The battle (G1b): the room, and what this check pokes before requesting
#: it from the title - EMBERKIT lv 5 knowing RAM and CINDER with two empty
#: slots, against a wild PEBBLIT lv 3 (slower: SPD 9 + 5 against 5 + 3, so
#: YOUR move comes first).
BATTLE_ROOM = 3
BATTLE = (("p_sp", 3), ("p_lv", 5), ("p_hp", 34), ("p_mv", 0), ("p_mv+1", 4),
          ("p_mv+2", 255), ("p_mv+3", 255), ("party_n", 1), ("lead", 0),
          ("f_sp", 9), ("f_lv", 3), ("ret_room", HOME_ROOM), ("ret_x", HOME_X),
          ("ret_y", HOME_Y), ("grace", 0))

#: The ways between the rooms (C1c): (label, room, start x, y, the button
#: held, the room it leads to, where the player arrives). Each start is
#: where the way back puts the player, so every arrival is also a start.
#: Rooms: 1 Tessel, 2 Mossway, 7 the Ridgeway, 8 Mosswood, 9 the shrine's
#: hall, 10 the Ridgeway top.
DOOR_WALKS = (
    ("Mossway south -> Tessel", 2, 168, 224, "down", 1, 144, 16),
    ("Mossway north -> the Ridgeway", 2, 136, 24, "up", 7, 16, 104),
    ("the Ridgeway west -> Mossway", 7, 16, 104, "left", 2, 136, 24),
    ("Mossway west -> Mosswood", 2, 16, 112, "left", 8, 288, 112),
    ("Mosswood east -> Mossway", 8, 288, 112, "right", 2, 16, 112),
    ("Mosswood shrine -> the hall", 8, 80, 96, "up", 9, 72, 112),
    ("the hall's doormat -> Mosswood", 9, 72, 112, "down", 8, 80, 88),
    ("the Ridgeway east -> its top", 7, 480, 80, "right", 10, 96, 112),
    ("the Ridgeway top -> the Ridgeway", 10, 96, 112, "down", 7, 480, 80),
)
#: The people (C1d): (label, room, start x, y, the button held, the axis it
#: walks (1 x, 2 y) and where it stops (an actor does not block the player:
#: stop beside it, see ELDER_FACE_Y), the `trainer` value of their battle).
#: Rooms: 2 Mossway, 7 the Ridgeway, 8 Mosswood, 9 the shrine's hall.
PEOPLE_TALKS = (
    ("the farmhand (Mossway)", 2, 176, 160, "up", 2, 146, 3),
    ("the forager (Mosswood)", 8, 240, 192, "up", 2, 178, 4),
    ("the pilgrim (Mosswood)", 8, 64, 160, "up", 2, 146, 5),
    ("the Moss Keeper (the hall)", 9, 72, 96, "up", 2, 66, 2),
    ("the alpinist (the Ridgeway)", 7, 176, 80, "right", 1, 190, 6),
)
EXT = {"gameboy": "gb", "sms": "sms"}


def hud_ink(img):
    """Dark pixels in the battle HUD's top band (the foe's name, level and
    HP bar: room rows 1..2), on a screen the room may be letterboxed into.
    Every one of those cells is a script-written tile (`bkg_tile`)."""
    w, h = img.size
    top = (h - 144) // 2 + 8
    px = img.crop((0, top, w, top + 16)).getdata()
    return sum(1 for r, g_, b in px if r * 3 + g_ * 6 + b < 1000)


def heap_cells():
    """The heap's size: the project's `[build] vm_heap`."""
    from mosaik_vm.heap import project_heap_cap
    return project_heap_cap(PROJECT)


def starter_expected(pick):
    """What the elder's `pick` must leave in the party, from the game's own
    DATA (scripts/tables.toml): the species the starter menu row names, its
    HP at STARTER_LV by `sp_stats` (hp + level * 3), and its move slots from
    `learnset` (every row of that species at or under the level, in table
    order, the rest 255)."""
    import tomllib
    with open(os.path.join(PROJECT, "scripts", "dialogue.toml"), "rb") as f:
        names = tomllib.load(f)["defs"]["elder_starter_pick"]["choices"]
    with open(os.path.join(PROJECT, "scripts", "tables.toml"), "rb") as f:
        tables = {t["name"]: t for t in tomllib.load(f)["table"]}
    sp_t = tables["species"]
    sp = sp_t["row_names"].index(names[pick].lower())
    hp = sp_t["rows"][sp][sp_t["columns"].index("hp")] + STARTER_LV * 3
    cols = tables["learnset"]["columns"]
    moves = [r[cols.index("mv")] for r in tables["learnset"]["rows"]
             if r[cols.index("sp")] == sp and r[cols.index("lv")] <= STARTER_LV][:4]
    return sp, hp, moves + [255] * (4 - len(moves))

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

    def image(self):
        """The screen after one more (rendered) frame, as an RGB image."""
        if self.platform == "gameboy":
            self.pb.tick(1, True)
            return self.pb.screen.image.convert("RGB")
        self.core.run(1)
        return self.core.image().convert("RGB")

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

    def _at(self, name):
        base, _, off = name.partition('+')
        return self.cells[base] + int(off or 0)

    def cell(self, name):
        return self.heap()[self._at(name)]

    def set_cell(self, name, v):
        a = self.sym["_vm_core_heap"] + 2 * self._at(name)
        self.m.write(a, v & 0xFF)
        self.m.write(a + 1, (v >> 8) & 0xFF)

    def heap(self):
        a = self.sym["_vm_core_heap"]
        return [self.m.read(a + 2 * i) | self.m.read(a + 2 * i + 1) << 8
                for i in range(heap_cells())]

    def array(self, name, n):
        """The first `n` cells of the heap array `name`."""
        return [self.cell("%s+%d" % (name, k)) for k in range(n)]

    def goto(self, room, x, y):
        """Into `room` at (x, y) through the engine's own scene-change
        request, then settled there."""
        self.m.write(self.sym["_vm_core_pend_a"], room)
        for k, v in (("_vm_core_pend_b", x), ("_vm_core_pend_c", y)):
            self.m.write(self.sym[k], v & 0xFF)
            self.m.write(self.sym[k] + 1, v >> 8)
        self.m.write(self.sym["_vm_core_pend_code"], 2)
        return self.settle_in(room, (x, y))

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
        """Pause menu -> SAVE; the string id of the first box it opens. DOWN
        until the menu's cursor is on SAVE: a press that lands while the SMS /
        Game Gear is still drawing the menu is not read (one was lost after
        CONTINUE, 2026-11-19), so a fixed count of taps was phase luck. A
        menu whose SAVE is not on SAVE_ROW still opens another box."""
        cur = self.sym["_engine_menu_cursor"]
        for _ in range(3 * SAVE_ROW):
            if self.m.read(cur) == SAVE_ROW:
                break
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
        g.goto(TESSEL, TESSEL_X, TESSEL_Y)
        # The party AFTER the room's On Init: a value that only the save can
        # bring back, so a room init that rewrites a saved cell on the load
        # shows up in the whole-heap comparison.
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
    elif phase == "starter":                         # a fresh cart
        g.tap("a")                                   # NEW GAME
        g.settle_in(HOME_ROOM)
        obs["in_house"] = g.goto(ELDER_ROOM, ELDER_X, ELDER_Y)
        g.m.press("up")                              # walk up to the elder
        obs["below_elder"] = g.until(lambda: g.where()[2] <= ELDER_FACE_Y, 400)
        g.m.press()
        g.m.run(10)
        picked = False
        for _ in range(60):                          # the boxes, the menu
            if g.cell("party_n") and g.box() is None and g.menu() == 0:
                break
            if g.menu() == 3 and not picked:
                for _ in range(STARTER_ROW):
                    g.tap("down")
                picked = True
            g.tap("a", settle=20)
        g.m.run(30)
        obs["picked"] = picked
        obs["party_n"] = g.cell("party_n")
        for name, n in (("p_sp", 3), ("p_lv", 1), ("p_hp", 1), ("p_xp", 1),
                        ("p_st", 1), ("p_mv", 8)):
            obs[name] = g.array(name, n)
    elif phase == "battle":                          # no save involved
        for name, v in BATTLE:
            g.set_cell(name, v)
        g.m.write(sym["_vm_core_pend_a"], BATTLE_ROOM)
        g.m.write(sym["_vm_core_pend_code"], 2)
        obs["in_battle"] = g.until(lambda: g.u8("_vm_core_cur_scene") == BATTLE_ROOM, 600)
        obs["first_box"] = g.until(lambda: g.box() is not None, 900)
        g.m.run(30)
        obs["hud_ink_open"] = hud_ink(g.m.image())
        g.tap("a")                                   # close the first box
        g.until(lambda: g.box() is None, 300)
        g.m.run(40)
        obs["hud_ink_closed"] = hud_ink(g.m.image())
        for _ in range(10):                          # any further box, then
            if g.until(lambda: g.menu() == 4, 300):  # the command menu
                break
            g.dismiss()
        g.tap("a")                                   # FIGHT
        g.until(lambda: g.menu() == 0, 120)
        obs["move_menu"] = g.until(lambda: g.menu() == 4, 300) and g.menu()
        g.m.run(20)
        obs["mv"] = [g.cell("mv%d" % j) for j in range(4)]
        g.tap("down")
        g.tap("a")                                   # slot 1
        g.until(lambda: g.box() is not None, 300)
        obs["used_box"] = g.box()
        obs["pm"] = g.cell("pm")
    elif phase == "doors":                           # no save involved
        obs["walks"] = []
        for _, room, x, y, button, to, ex, ey in DOOR_WALKS:
            at = g.goto(room, x, y)
            g.m.press(button)
            moved = g.until(lambda to=to: g.u8("_vm_core_cur_scene") == to, 600)
            g.m.press()
            arrived = moved and g.settle_in(to, (ex, ey))
            obs["walks"].append([bool(at), bool(moved), bool(arrived), list(g.where())])
    elif phase == "people":                          # no save involved
        g.tap("a")                                   # NEW GAME
        g.settle_in(HOME_ROOM)
        obs["talks"] = []
        for _, room, x, y, button, axis, stop, _t in PEOPLE_TALKS:
            at = g.goto(room, x, y)
            for name, v in PARTY:                    # a party (after the room's On Init)
                g.set_cell(name, v)
            g.set_cell("flags", 0)
            g.m.press(button)
            near = g.until(lambda axis=axis, stop=stop, button=button: (
                g.where()[axis] <= stop if button == "up" else g.where()[axis] >= stop), 400)
            g.m.press()
            g.m.run(10)
            g.tap("a")
            fought = False
            for _ in range(40):                      # the lines, then the battle
                if g.u8("_vm_core_cur_scene") == BATTLE_ROOM:
                    fought = True
                    break
                if g.box() is not None:
                    g.tap("a", settle=20)
                else:
                    g.m.run(20)
            obs["talks"].append([bool(at), bool(near), fought, g.cell("trainer"), list(g.where())])
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
    root = os.path.join(tmp, platform, "vm-fjaum")
    shutil.copytree(PROJECT, root, ignore=shutil.ignore_patterns("build"))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--debug", "--platform", platform, root],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    bdir = os.path.join(root, "build", platform)
    rom = os.path.join(bdir, "vm-fjaum." + EXT[platform])
    noi = os.path.join(bdir, "vm-fjaum.noi")
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
    check(p + "SAVE with no fjaum refuses",
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
    n = heap_cells()
    diff = [i for i in range(n) if b["loaded_heap"][i] != a["saved_heap"][i]]
    check(p + "CONTINUE restores all %d heap cells" % n,
          not diff and len(b["loaded_heap"]) == n,
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

    # A FRESH cart (a copy of the ROM, so no battery RAM comes along).
    fresh = rom + ".fresh." + EXT[platform]
    shutil.copyfile(rom, fresh)
    d = run_cycle(platform, "starter", fresh, noi)
    if d is None:
        return
    sp, hp, moves = starter_expected(STARTER_ROW)
    check(p + "the elder's starter menu was answered (row %d)" % STARTER_ROW,
          d["in_house"] and d["below_elder"] and d["picked"],
          "in house %s, below the elder %s, picked %s"
          % (d["in_house"], d["below_elder"], d["picked"]))
    check(p + "the starter is party slot 0 and the only member (party_n 1)",
          d["party_n"] == 1 and d["p_sp"] == [sp, 255, 255],
          "party_n %s, p_sp %s" % (d["party_n"], d["p_sp"]))
    check(p + "...at level %d with its full HP from the species table (%d)"
          % (STARTER_LV, hp), d["p_lv"] == [STARTER_LV] and d["p_hp"] == [hp]
          and d["p_xp"] == [0] and d["p_st"] == [0],
          "lv %s hp %s xp %s st %s" % (d["p_lv"], d["p_hp"], d["p_xp"], d["p_st"]))
    check(p + "...knowing its learnset's moves %s" % moves,
          d["p_mv"] == moves + [0, 0, 0, 0],
          "p_mv %s" % d["p_mv"])

    f = run_cycle(platform, "doors", fresh, noi)
    if f is None:
        return
    for (label, room, _x, _y, _, to, ex, ey), (at, moved, arrived, where) in zip(DOOR_WALKS, f["walks"]):
        check(p + "%s (room %d -> %d, arriving at %d, %d)" % (label, room, to, ex, ey),
              at and moved and arrived,
              "put at the start %s, left the room %s, now %s" % (at, moved, where))

    h = run_cycle(platform, "people", fresh, noi)
    if h is None:
        return
    for (label, room, *_rest, trainer), (at, near, fought, got, where) in zip(PEOPLE_TALKS, h["talks"]):
        check(p + "%s: A beside them, their lines, the battle (room %d -> %d, trainer %d)"
              % (label, room, BATTLE_ROOM, trainer),
              at and near and fought and got == trainer,
              "put at the start %s, beside them %s, in battle %s, trainer %s, now %s"
              % (at, near, fought, got, where))

    e = run_cycle(platform, "battle", fresh, noi)
    if e is None:
        return
    check(p + "a battle starts from the poked party (room %d, a first box)" % BATTLE_ROOM,
          e["in_battle"] and e["first_box"], "in %s, box %s" % (e["in_battle"], e["first_box"]))
    check(p + "the battle HUD (names, level, HP bar) survives the first box close "
          "(%d -> %d dark pixels)" % (e["hud_ink_open"], e["hud_ink_closed"]),
          e["hud_ink_open"] >= 40 and e["hud_ink_closed"] * 10 >= e["hud_ink_open"] * 9,
          "the SMS close reloaded the tileset over the script-written tiles")
    check(p + "FIGHT opens the move menu on the lead's four slots "
          "(RAM, CINDER, -, -: mv0..3 [0, 4, 8, 8], '-' the list's last item)",
          e["move_menu"] == 4 and e["mv"] == [0, 4, 8, 8],
          "menu %s, mv %s" % (e["move_menu"], e["mv"]))
    check(p + "...slot 1 is CINDER, and the faster EMBERKIT moves first "
          "('YOU USED CINDER!')",
          e["pm"] == 4 and e["used_box"] == string_id(prog, "battle_you_used"),
          "pm %s, box %s" % (e["pm"], e["used_box"]))


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
        PROJECT = os.environ["FJAUM_VERIFY_PROJECT"] = os.path.abspath(args.project)
    tmp = tempfile.mkdtemp(prefix="fjaum_save_")
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
    print("All vm-fjaum save checks passed (%d)" % passed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
