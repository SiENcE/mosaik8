#!/usr/bin/env python3
"""vm-fjaum's core-system DATA paths (G1a), run in the reference VM.

G1a moved the party into heap ARRAYS (one per field: `p_sp`, `p_lv`, `p_hp`,
`p_xp`, `p_st`, four move slots a member in `p_mv`), a member's moves into
the `learnset` data table, the element chart into `eltab` and the Dex
into bit arrays. The game must play exactly as before. Each check below runs
ONE of the project's own compiled scripts (`compile_path`, the build's
blob and TABLES) in `RefVM`, from a heap set up the way the game would have
it, and reads the heap back:

  - learn_moves: a member's slots are its learnset rows at or under its
    level, in table order, the rest 255; no other member's slots move;
  - add_caught: the caught fjaum goes into the first free slot with
    full HP (`sp_stats`), XP 0, no status and its moves; a full party
    takes nothing;
  - heal_party: every member back to full HP and no status, an empty slot
    untouched;
  - party_load / party_store: the lead's fields, by index, both ways;
  - calc_damage: `eltab` gives the effectiveness the old formula did for
    every attacking and defending element (0..4, prism neutral);
  - battle: the ally's palette is the LEAD's species read from the arrays
    (it read the retired `pa_sp` in the first cut), the foe's the wild
    one's; `[kind_variants.ally]` and the generated rooms.mos read party
    slot 0 for the back view;
  - menu_trainer: the Dex count reads the `dex_seen` bits, OWNED the
    `dex_own` bits (G1d; it showed the party count before).

The ROM side (the elder's starter in the arrays, every heap cell through a
save) is `projects/vm-fjaum/verify.py`, run by `project_verify_test`.
"""
import os
import sys

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


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def tables():
    import tomllib
    with open(os.path.join(PROJECT, "scripts", "tables.toml"), "rb") as f:
        return {t["name"]: t for t in tomllib.load(f)["table"]}


class Game:
    """The compiled program and a heap to run its scripts against."""

    def __init__(self):
        self.prog = loader.compile_path(os.path.join(PROJECT, "scripts"))
        self.cells = dict(self.prog.variables)
        self.n = project_heap_cap(PROJECT)
        self.tab = tables()

    def run(self, script, heap, frames=400):
        """Run `script` alone (its bare RET ends the thread) from `heap`
        ({name or "array+k": value}, everything else 0); the heap after."""
        vm = RefVM(self.prog.code, entry=self.prog.offsets[script], heap=self.n,
                   tables=self.prog.tables_blob)
        for name, v in heap.items():
            vm.heap[self.at(name)] = v & 0xFFFF
        for _ in range(frames):
            vm.frame(a_pressed=True)
            if not vm.any_active_threads():
                break
        self.done = not vm.any_active_threads()
        self.vm = vm
        return vm.heap

    def at(self, name):
        base, _, off = name.partition("+")
        return self.cells[base] + int(off or 0)

    def get(self, heap, name, n=1):
        return [heap[self.at("%s+%d" % (name, k))] for k in range(n)]

    def col(self, table, row, column):
        t = self.tab[table]
        return t["rows"][row][t["columns"].index(column)]

    def max_hp(self, sp, lv):
        return self.col("species", sp, "hp") + lv * 3     # sp_stats

    def moves(self, sp, lv):
        t = self.tab["learnset"]
        c = t["columns"]
        mv = [r[c.index("mv")] for r in t["rows"]
              if r[c.index("sp")] == sp and r[c.index("lv")] <= lv][:4]
        return mv + [255] * (4 - len(mv))


def learn_moves(g):
    print("\n[learn_moves: a member's slots from the learnset table]")
    bad = []
    for sp in range(len(g.tab["species"]["rows"])):
        pre = {"p_mv+%d" % i: 77 for i in range(12)}
        h = g.run("learn_moves", dict(pre, k=1, q_sp=sp, q_lv=5))
        got = g.get(h, "p_mv", 12)
        want = [77] * 4 + g.moves(sp, 5) + [77] * 4
        if got != want or not g.done:
            bad.append((sp, got, want))
    check("every species (%d) gets its learnset rows in slot 1's four, the "
          "rest 255, slots 0 and 2 untouched" % len(g.tab["species"]["rows"]),
          not bad, bad[:2])
    h = g.run("learn_moves", {"k": 0, "q_sp": 3, "q_lv": 0})
    check("a level under every row learns nothing (four 255s)",
          g.get(h, "p_mv", 4) == [255] * 4, g.get(h, "p_mv", 4))


def add_caught(g):
    print("\n[add_caught: into the first free slot, or nothing]")
    base = {"p_sp+0": 3, "p_sp+1": 255, "p_sp+2": 255, "party_n": 1,
            "f_sp": 9, "f_lv": 4, "p_hp+0": 11}
    h = g.run("add_caught", base)
    check("slot 1 takes the caught species and level, party_n 2",
          g.get(h, "p_sp", 3) == [3, 9, 255] and g.get(h, "p_lv", 2)[1] == 4
          and h[g.at("party_n")] == 2, (g.get(h, "p_sp", 3), h[g.at("party_n")]))
    check("...with full HP (%d), XP 0, no status" % g.max_hp(9, 4),
          g.get(h, "p_hp", 2) == [11, g.max_hp(9, 4)]
          and g.get(h, "p_xp", 2)[1] == 0 and g.get(h, "p_st", 2)[1] == 0,
          (g.get(h, "p_hp", 2), g.get(h, "p_xp", 2), g.get(h, "p_st", 2)))
    check("...and its learnset's moves in slots 4..7 %s" % g.moves(9, 4),
          g.get(h, "p_mv", 8)[4:] == g.moves(9, 4), g.get(h, "p_mv", 8))
    full = dict(base, **{"p_sp+1": 6, "p_sp+2": 0, "party_n": 3})
    h = g.run("add_caught", full)
    check("a full party (3) takes nothing",
          g.get(h, "p_sp", 3) == [3, 6, 0] and h[g.at("party_n")] == 3,
          (g.get(h, "p_sp", 3), h[g.at("party_n")]))


def heal_party(g):
    print("\n[heal_party: every member, by index]")
    h = g.run("heal_party", {"party_n": 2, "p_sp+0": 0, "p_lv+0": 7, "p_hp+0": 3,
                             "p_st+0": 2, "p_sp+1": 15, "p_lv+1": 12, "p_hp+1": 1,
                             "p_st+1": 1, "p_sp+2": 255, "p_hp+2": 99})
    check("both members at full HP (%d, %d) and no status, slot 2 untouched"
          % (g.max_hp(0, 7), g.max_hp(15, 12)),
          g.get(h, "p_hp", 3) == [g.max_hp(0, 7), g.max_hp(15, 12), 99]
          and g.get(h, "p_st", 2) == [0, 0], (g.get(h, "p_hp", 3), g.get(h, "p_st", 2)))


def party_lead(g):
    print("\n[party_load / party_store: the lead by index]")
    h = g.run("party_load", {"lead": 1, "p_sp+1": 10, "p_lv+1": 8, "p_hp+1": 20,
                             "p_xp+1": 33, "p_st+1": 2, "p_sp+0": 1})
    check("party_load reads slot `lead` into m_*",
          [h[g.at(n)] for n in ("m_sp", "m_lv", "m_hp", "m_xp", "m_st")]
          == [10, 8, 20, 33, 2], [h[g.at(n)] for n in ("m_sp", "m_lv", "m_hp")])
    check("...and its stats from the species table (max HP %d)" % g.max_hp(10, 8),
          h[g.at("m_mhp")] == g.max_hp(10, 8)
          and h[g.at("m_el")] == g.col("species", 10, "el"))
    h = g.run("party_store", {"lead": 2, "m_sp": 7, "m_lv": 9, "m_hp": 5,
                              "m_xp": 6, "m_st": 1, "p_sp+1": 4})
    check("party_store writes m_* into slot `lead` only",
          g.get(h, "p_sp", 3) == [0, 4, 7] and g.get(h, "p_lv", 3)[2] == 9
          and g.get(h, "p_hp", 3)[2] == 5 and g.get(h, "p_xp", 3)[2] == 6
          and g.get(h, "p_st", 3)[2] == 1, g.get(h, "p_sp", 3))


def chart(g):
    print("\n[calc_damage: the eltab chart is the old rule]")
    bad = []
    for a in range(5):
        for d in range(5):
            r = (d - a + 4) % 4 if a < 4 and d < 4 else None
            want = 1 if r == 1 else (2 if r == 3 else 0)
            h = g.run("calc_damage", {"a_atk": 20, "pow": 16, "d_def": 8,
                                      "mel": a, "d_el": d})
            if h[g.at("eff")] != want:
                bad.append((a, d, h[g.at("eff")], want))
    check("eff for all 25 attacker / defender pairs (super, not very, normal; "
          "prism neutral)", not bad, bad[:4])


def lead_view(g):
    print("\n[the battle shows the LEAD: party slot 0 of the arrays]")
    import tomllib
    with open(os.path.join(PROJECT, "world.toml"), "rb") as f:
        world = tomllib.load(f)
    lib = {p["name"]: i for i, p in enumerate(world["palette"])}
    names = g.tab["species"]["row_names"]
    g.run("battle", {"p_sp+0": 5, "p_lv+0": 5, "p_hp+0": 20, "party_n": 1,
                     "lead": 0, "f_sp": 9, "f_lv": 3}, frames=3)
    log = g.vm.palette_log
    check("the ally's palette (sprite slot 1) is the lead's species (%s)" % names[5],
          (0, 1, lib["spr_mon_" + names[5]]) in log, log[:4])
    check("...and the foe's (slot 0) is the wild one's (%s)" % names[9],
          (0, 0, lib["spr_mon_" + names[9]]) in log, log[:4])
    var = world["kind_variants"]["ally"]["var"]
    with open(os.path.join(PROJECT, "src", "rooms.mos"), encoding="utf-8") as f:
        rooms = f.read()
    check("the ally's back view ([kind_variants.ally]) reads party slot 0 "
          "(p_sp, heap cell %d)" % g.cells["p_sp"],
          var == "p_sp" and "core.var_get(%d)" % g.cells["p_sp"] in rooms, var)


def dex(g):
    print("\n[menu_trainer: the Dex count reads the dex_seen / dex_own bits]")
    h = g.run("menu_trainer", {"dex_seen+0": 0x0209, "dex_own+0": 0x0001, "party_n": 2})
    check("three seen bits count 3, one owned bit counts 1 (not the party's 2)",
          h[g.at("q_n")] == 3 and h[g.at("q_own")] == 1,
          (h[g.at("q_n")], h[g.at("q_own")]))


def main():
    print("=== fjaum_core_data_test ===")
    g = Game()
    learn_moves(g)
    add_caught(g)
    heal_party(g)
    party_lead(g)
    chart(g)
    lead_view(g)
    dex(g)
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
