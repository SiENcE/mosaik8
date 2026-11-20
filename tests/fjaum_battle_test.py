#!/usr/bin/env python3
"""vm-fjaum's battle loop (G1b), run in the reference VM.

G1b made the battle a loop over DATA: SPD decides who moves first (a tie is
a coin flip; CATCH / ITEM / RUN still come before the foe), a move's `cat`
column picks ATK vs DEF or FOC vs FOC, the move menu is the lead's four
`p_mv` slots (an empty one reads "-" and asks again), ONE `do_move` runs any
move from its `moves` row, and the foe learns its moves from `learnset`
(`learn_moves` with k = 3 into `f_mv`) and picks one at random. G1c made a
move's effect symmetric (one `apply_fx` for both sides) and added status
moves, stages, capture tiers, Refraction and uses; those are
`fjaum_battle_systems_test.py`, which drives the game with this
module's `Game`.

The project's own compiled blob and TABLES (`compile_path`) run in `RefVM`.
A driver dismisses every text box with an A edge and answers each menu from
a queue of picks (down edges, then A). The ROM side (the move menu's items
on screen, "YOU USED CINDER!") is `projects/vm-fjaum/verify.py`.

    python tests/fjaum_battle_test.py [--project DIR]

`--project` runs the checks on another copy (a mutated one).
"""
import os
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik_vm import loader  # noqa: E402
from mosaik_vm.heap import project_heap_cap  # noqa: E402
from mosaik_vm.refvm import RefVM  # noqa: E402

PROJECT = os.path.join(ROOT, "projects", "vm-fjaum")
FAILS = []


def project_arg():
    """`--project DIR` from the command line, else the sample."""
    if "--project" in sys.argv:
        return os.path.abspath(sys.argv[sys.argv.index("--project") + 1])
    return PROJECT


def s16(v):
    return ((int(v) + 0x8000) & 0xFFFF) - 0x8000

def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


class Game:
    def __init__(self, project=PROJECT):
        self.prog = loader.compile_path(os.path.join(project, "scripts"))
        self.cells = dict(self.prog.variables)
        self.n = project_heap_cap(project)
        with open(os.path.join(project, "scripts", "dialogue.toml"), "rb") as f:
            defs = tomllib.load(f)["defs"]
        self.sid = {k: self.prog.strings.index(v["text"]) for k, v in defs.items()
                    if v.get("text") and v["text"] in self.prog.strings}
        with open(os.path.join(project, "scripts", "tables.toml"), "rb") as f:
            self.tab = {t["name"]: t for t in tomllib.load(f)["table"]}

    def at(self, name):
        base, _, off = name.partition("+")
        return self.cells[base] + int(off or 0)

    def run(self, script, heap, picks=(), rnd=None, frames=6000, stop=False, snap=()):
        """Run `script` from `heap`; answer menus from `picks`. Returns the VM.

        A menu is NEW when `vm.menu_log` (one entry per CLOSED menu) has
        grown since the last one was answered, not on a closed -> open edge:
        a menu that opens in the frame the last one closed (CATCH -> the
        shard menu) shows no edge. A pick of -1 answers with B (the menu's
        cancel). `snap` names texts whose heap is kept (in `self.snaps`) the
        frame they are said."""
        vm = RefVM(self.prog.code, entry=self.prog.offsets[script], heap=self.n,
                   tables=self.prog.tables_blob)
        if rnd is not None:
            vm.random = rnd
        for name, v in heap.items():
            vm.heap[self.at(name)] = v & 0xFFFF
        picks = list(picks)
        want = {self.sid[r] for r in snap}
        self.menus, self.snaps = [], []
        target, menu_id, nlog = None, None, 0
        self.frames = 0
        for f in range(frames):
            if len(vm.text_log) != nlog:
                self.snaps += [list(vm.heap) for s in vm.text_log[nlog:] if s in want]
                nlog = len(vm.text_log)
            a = down = b = False
            if vm.menu_open:
                if menu_id != len(vm.menu_log):
                    menu_id = len(vm.menu_log)
                    if stop and not picks:
                        self.menus.append(None)
                        break
                    target = picks.pop(0) if picks else 0
                    self.menus.append(target)
                if f % 2 == 0:
                    if target < 0:
                        b = True
                    elif vm.m_cursor < target:
                        down = True
                    else:
                        a = True
            elif vm.box_open:
                a = f % 2 == 0
            vm.frame(a_pressed=a, down=down, b_pressed=b)
            self.frames = f + 1
            if not vm.any_active_threads():
                break
        self.done = not vm.any_active_threads()
        self.left = picks
        return vm

    def h(self, vm, name):
        """A heap cell, signed (the heap is i16: a stage is -2..+2)."""
        return s16(vm.heap[self.at(name)])

    def hs(self, snap, name):
        return s16(snap[self.at(name)])

    def said(self, vm, ref):
        return [i for i, s in enumerate(vm.text_log) if s == self.sid[ref]]


def mon(sp, lv):
    return {"p_sp+0": sp, "p_lv+0": lv, "p_hp+0": 200, "party_n": 1, "lead": 0}


def order(g):
    print("\n[turn order: SPD]")
    # emberkit (spd 9) vs pebblit (spd 5): +lv each, same level -> emberkit first
    for mine, foe, want in ((3, 9, "you"), (9, 3, "foe")):
        heap = dict(mon(mine, 5), f_sp=foe, f_lv=5)
        heap["p_mv+0"], heap["p_mv+1"] = 0, 255     # RAM only
        vm = g.run("battle", heap, picks=[0, 0, 0, 0], rnd=lambda: 1, frames=900)
        you = g.said(vm, "battle_you_used")
        them = g.said(vm, "battle_foe_used_move")
        first = "you" if you and (not them or you[0] < them[0]) else "foe"
        check("species %d (spd %d) vs %d (spd %d): %s moves first"
              % (mine, g.tab["species"]["rows"][mine][4], foe,
                 g.tab["species"]["rows"][foe][4], want),
              first == want and you and them, (you[:2], them[:2]))
        check("...and each side moves once a turn (%d / %d moves)" % (len(you), len(them)),
              abs(len(you) - len(them)) <= 1 and len(you) >= 2, (len(you), len(them)))
    # a tie: both orders over seeds
    seen = set()
    for seed in range(40):
        state = [seed * 7919 + 1]

        def rnd(state=state):
            state[0] = (state[0] * 1103515245 + 12345) & 0x7FFFFFFF
            return state[0] >> 8
        heap = dict(mon(6, 5), f_sp=6, f_lv=5)      # bubbun vs bubbun
        vm = g.run("battle", heap, picks=[0, 0], rnd=rnd, frames=500)
        you, them = g.said(vm, "battle_you_used"), g.said(vm, "battle_foe_used_move")
        if you and them:
            seen.add("you" if you[0] < them[0] else "foe")
        elif you or them:
            seen.add("you" if you else "foe")
    check("a SPD tie goes either way over 40 seeds", seen == {"you", "foe"}, seen)


def focus(g):
    print("\n[do_move: FOC for element moves, ATK for RAM]")
    base = {"side": 0, "m_atk": 5, "m_foc": 20, "f_def": 5, "f_foc": 10,
            "f_el": 4, "f_hp": 50, "f_mhp": 50, "m_hp": 9, "m_mhp": 9}
    for mv, st, want, what in ((1, 0, 18, "VINE LASH: FOC 20 vs FOC 10"),
                               (0, 0, 5, "RAM: ATK 5 vs DEF 5"),
                               (0, 1, 2, "RAM while burned: ATK halved"),
                               (1, 1, 18, "VINE LASH while burned: FOC is not halved")):
        vm = g.run("do_move", dict(base, mv=mv, m_st=st), rnd=lambda: 1, frames=400)
        check("%s -> %d damage" % (what, want),
              g.done and g.h(vm, "dmg") == want and g.h(vm, "f_hp") == 50 - want,
              (g.h(vm, "dmg"), g.h(vm, "f_hp")))
    vm = g.run("do_move", dict(base, side=1, mv=0, f_atk=11, m_def=3),
               rnd=lambda: 1, frames=400)
    check("the foe's RAM hits YOUR HP (ATK 11 vs DEF 3 -> 13)",
          g.h(vm, "m_hp") == 0 and g.h(vm, "f_hp") == 50 and g.h(vm, "dmg") == 13,
          (g.h(vm, "dmg"), g.h(vm, "m_hp")))


def effects(g):
    print("\n[do_move: a move's effect row, for both sides since G1c]")
    base = {"m_atk": 5, "m_foc": 5, "f_atk": 5, "f_foc": 5, "f_def": 5, "m_def": 5,
            "f_el": 4, "m_el": 4, "f_hp": 99, "f_mhp": 99, "m_hp": 99, "m_mhp": 99}
    always = lambda: 0                               # noqa: E731  every chance hits
    for mv, var, val, what in ((4, "f_st", 1, "CINDER burns"),
                               (1, "f_st", 2, "VINE LASH poisons"),
                               (2, "f_slp", 2, "DOUSE puts to sleep")):
        vm = g.run("do_move", dict(base, side=0, mv=mv), rnd=always, frames=600)
        check("%s the foe" % what, g.h(vm, var) == val, g.h(vm, var))
    vm = g.run("do_move", dict(base, side=0, mv=4), rnd=lambda: 1, frames=600)
    check("...not when the 1-in-N roll misses", g.h(vm, "f_st") == 0, g.h(vm, "f_st"))
    vm = g.run("do_move", dict(base, side=1, mv=4), rnd=always, frames=600)
    check("the foe's CINDER burns YOU (G1c: one apply_fx for both sides)",
          g.h(vm, "m_st") == 1 and g.h(vm, "f_st") == 0, (g.h(vm, "m_st"), g.h(vm, "f_st")))


def menu(g):
    print("\n[the move menu: the LEAD's four slots]")
    heap = dict(mon(3, 5), f_sp=9, f_lv=2, lead=1)
    heap.update({"p_sp+1": 3, "p_lv+1": 5, "p_hp+1": 200, "party_n": 2})
    heap.update({"p_mv+4": 4, "p_mv+5": 0, "p_mv+6": 255, "p_mv+7": 255})
    # FIGHT, slot 2 (empty -> asks again), FIGHT, slot 0 (CINDER)
    vm = g.run("battle", heap, picks=[0, 2, 0, 0], rnd=lambda: 1, frames=900)
    check("mv0..mv3 show the lead's slots, an empty one as item 8 ('-', last)",
          [g.h(vm, "mv%d" % j) for j in range(4)] == [4, 0, 8, 8],
          [g.h(vm, "mv%d" % j) for j in range(4)])
    check("FIGHT then slot 0 uses it (pm 4, CINDER)",
          g.menus[:4] == [0, 2, 0, 0] and g.h(vm, "pm") == 4,
          (g.menus[:5], g.h(vm, "pm")))
    vm = g.run("battle", heap, picks=[0, 2], rnd=lambda: 1, frames=400, stop=True)
    check("an empty slot (2) asks again: a third menu opens, nobody has moved",
          g.menus == [0, 2, None] and not g.said(vm, "battle_you_used")
          and not g.said(vm, "battle_foe_used_move"),
          (g.menus, g.said(vm, "battle_you_used")))


def foe_moves(g):
    print("\n[the foe's moves: its learnset, picked at random]")
    heap = dict(mon(0, 5), f_sp=3, f_lv=4)          # foe emberkit: RAM, CINDER, GLOWER
    heap["p_mv+0"], heap["p_mv+1"] = 0, 255
    vm = g.run("battle", heap, picks=[0, 0], frames=300)
    fmv = [g.h(vm, "f_mv+%d" % j) for j in range(4)]
    check("f_mv is the foe's learnset [0, 4, 6, 255] (GLOWER at lv 4), f_mn 3",
          fmv == [0, 4, 6, 255] and g.h(vm, "f_mn") == 3, (fmv, g.h(vm, "f_mn")))
    used = set()
    for seed in range(30):
        st = [seed + 1]

        def rnd(st=st):
            st[0] = (st[0] * 1103515245 + 12345) & 0x7FFFFFFF
            return st[0] >> 8
        vm = g.run("foe_act", {"f_mv+0": 0, "f_mv+1": 4, "f_mv+2": 255, "f_mv+3": 255,
                               "f_mn": 2, "m_hp": 99, "f_hp": 99, "f_mhp": 99,
                               "m_mhp": 99, "f_atk": 5, "m_def": 5, "f_foc": 5,
                               "m_foc": 5, "m_el": 4}, rnd=rnd, frames=400)
        used.add(g.h(vm, "mv"))
    check("over 30 seeds it uses both and nothing else", used == {0, 4}, used)


def whole(g):
    print("\n[a whole wild battle, FIGHT slot 0 every turn]")
    heap = dict(mon(3, 8), f_sp=9, f_lv=2)
    heap["p_mv+0"], heap["p_mv+1"] = 0, 4
    heap["p_hp+0"] = 50
    state = [12345]

    def rnd():
        state[0] = (state[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return state[0] >> 8
    vm = g.run("battle", heap, picks=[0, 1] * 40, rnd=rnd, frames=20000)
    check("it ends (change_scene) with the foe fainted",
          g.done and g.h(vm, "f_hp") == 0 and g.said(vm, "battle_foe_fainted"),
          (g.done, g.h(vm, "f_hp"), g.h(vm, "m_hp")))
    check("the lead's HP went back into p_hp (party_store)",
          g.h(vm, "p_hp+0") == g.h(vm, "m_hp"), (g.h(vm, "p_hp+0"), g.h(vm, "m_hp")))


def main():
    print("=== fjaum_battle_test ===")
    g = Game(project_arg())
    order(g)
    focus(g)
    effects(g)
    menu(g)
    foe_moves(g)
    whole(g)
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
