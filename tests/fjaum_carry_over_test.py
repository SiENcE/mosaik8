#!/usr/bin/env python3
"""vm-fjaum: a Fjaum CARRIES OVER between chapters (decided 2026-11-20).

The chapters are slices of one game with one save, so every save-visible id
is APPEND-ONLY and each chapter has a LEVEL CAP its new moves sit above:

- append-only: the species, moves, gems and facets keep their row order
  (their ids are in `p_sp`, `p_mv`, `p_gem` / `it_gem`, `p_fac`), the
  trainers keep their flag bits, the pinned heap keeps its cells and arrays,
  and `save_version` stays 2 (a bump makes every saved game read as none);
- the cap: `chapter.cap` (15 in Chapter 1). Every learnset row, every wild
  and trainer level and the starter's sit at or under it, so nothing the
  chapter offers is out of reach; a won battle past the cap gives no XP and
  says so, and XP stops at 0 there (Chapter 2 raises the cap and puts its
  new learnset rows ABOVE 15, so a carried Fjaum reaches every one of them:
  `learn_new` teaches only the rows of the exact level reached);
- refraction: a stage-2 kind's moves at or under its refraction level are
  moves the stage-1 kind learned by then (a refracting Fjaum never runs the
  new kind's rows at or under that level);
- each line learns a 4th move at level 10 (its element's new move, 90
  accuracy), so "FORGET WHICH?" first shows with Chapter 2's first new move.

The fixes from the docs pass ride here too, each run in the reference VM:
the starter is SEEN and OWNED, the pause menu's TONIC heals the LEAD, a won
battle's XP reads the foe's `species.xp`, and a 90-accuracy move can miss.
"""
import hashlib
import os
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fjaum_battle_test as bt  # noqa: E402

check, FAILS = bt.check, bt.FAILS
PROJECT = bt.project_arg()

# The save-visible ids as Chapter 1 has them. APPEND to a table; never
# reorder, rename or delete a row (a retired one keeps its place).
SPECIES = ["mossling", "mossback", "thornwyrm", "emberkit", "kilnbat", "lavamole",
           "bubbun", "riptusk", "coralisk", "pebblit", "cragoat", "geodrake",
           "steamoth", "clayfern", "padling", "tessra", "burrbit", "thistlehare",
           "budwick", "petalisk", "screelet", "quarrapede"]
MOVES = ["ram", "vine_lash", "douse", "rock_toss", "cinder", "prism", "glower",
         "brace", "bramble", "undertow", "stonefall", "scorch"]
GEMS = ["none", "spark", "moss", "tide", "stone", "ember", "mend", "lure", "wind"]
FACETS = ["even", "fierce", "daring", "stout", "steady", "fleet", "eager", "keen", "wise"]
TRAINER_FLAGS = {"wren": 0, "moss_keeper": 1, "farmhand": 2, "forager": 3,
                 "pilgrim": 4, "alpinist": 5}
# heap.toml: the first HEAP_N cells, by digest (append-only; after appending,
# raise HEAP_N and the digest to the new list's).
HEAP_N = 146
HEAP_SHA1 = "939a0acff36e66a7a2a0bf8977a5c2816472fb14"
ARRAYS = {"p_sp": 3, "p_lv": 3, "p_hp": 3, "p_xp": 3, "p_st": 3, "p_mv": 12,
          "p_fac": 3, "p_gem": 3, "dex_seen": 5, "dex_own": 5, "f_mv": 4,
          "p_pu": 12, "it_gem": 9}
CAP = 15
LV4 = 10
ALWAYS = lambda: 0  # noqa: E731
BASE = {"m_atk": 5, "m_foc": 5, "f_atk": 5, "f_foc": 5, "f_def": 5, "m_def": 5,
        "f_el": 4, "m_el": 4, "f_hp": 99, "f_mhp": 99, "m_hp": 99, "m_mhp": 99}


def col(t, name):
    return t["columns"].index(name)


def append_only(g):
    print("\n[append-only ids: a save from an earlier chapter still reads]")
    for table, want in (("species", SPECIES), ("moves", MOVES), ("gems", GEMS),
                        ("facets", FACETS)):
        got = g.tab[table]["row_names"]
        check("%s: the %d Chapter 1 rows lead, in order (%d now)" % (table, len(want), len(got)),
              got[:len(want)] == want, got[:len(want)])
    tr = g.tab["trainers"]
    flags = {n: r[col(tr, "flag")] for n, r in zip(tr["row_names"], tr["rows"])}
    check("trainers keep their flag bits", all(flags.get(n) == b for n, b in TRAINER_FLAGS.items()),
          flags)
    with open(os.path.join(PROJECT, "scripts", "heap.toml"), "rb") as f:
        heap = tomllib.load(f)
    cells = heap["cells"]
    check("heap.toml: the first %d cells unchanged (append only)" % HEAP_N,
          len(cells) >= HEAP_N
          and hashlib.sha1("\n".join(cells[:HEAP_N]).encode()).hexdigest() == HEAP_SHA1)
    arrays = heap.get("arrays", {})
    check("heap.toml: every array keeps its length", all(arrays.get(k) == v for k, v in ARRAYS.items()),
          arrays)
    check("save_version stays 2 (a bump would drop every saved game)", heap["save_version"] == 2)
    with open(os.path.join(PROJECT, "scripts", "dialogue.toml"), "rb") as f:
        lists = tomllib.load(f)["lists"]
    check("the move names follow the moves, '-' (the empty slot) last",
          lists["mvn"]["items"] == [m.replace("_", " ").upper() for m in g.tab["moves"]["row_names"]]
          + ["-"], lists["mvn"]["items"])


def level_cap(g):
    print("\n[the chapter's level cap and its learnsets]")
    cap = g.tab["chapter"]["rows"][0][0]
    check("chapter.cap is %d" % CAP, cap == CAP and g.tab["chapter"]["columns"] == ["cap"], cap)
    ls = g.tab["learnset"]["rows"]
    check("every learnset row is at or under the cap (nothing out of reach)",
          all(r[1] <= cap for r in ls), [r for r in ls if r[1] > cap])
    w = g.tab["wild"]
    top = max(r[col(w, "lv")] + r[col(w, "span")] - 1 for r in w["rows"])
    tp = [r[1] for r in g.tab["tparty"]["rows"] if r[0] != 255]
    check("every wild (to L%d) and trainer (to L%d) level is under the cap" % (top, max(tp)),
          top <= cap and max(tp) <= cap)
    sp = g.tab["species"]
    el, evo_lv, evo_to = col(sp, "el"), col(sp, "evo_lv"), col(sp, "evo_to")
    moves = g.tab["moves"]
    bad4 = []
    for s, row in enumerate(sp["rows"]):
        mine = [r for r in ls if r[0] == s]
        fourth = [r[2] for r in mine if r[1] == LV4]
        want = 6 if row[el] == 4 else 8 + row[el]
        if fourth != [want] or len({r[2] for r in mine}) != 4:
            bad4.append((SPECIES[s], mine))
    check("every kind learns a 4th, NEW move at L%d (its element's; PRISM: GLOWER)" % LV4,
          not bad4, bad4[:3])
    new = moves["rows"][8:12]
    check("the four new moves: elements 0..3, power 24, accuracy 90",
          [r[col(moves, "el")] for r in new] == [0, 1, 2, 3]
          and all(r[col(moves, "pow")] == 24 and r[col(moves, "acc")] == 90 for r in new))
    trap = []
    for s, row in enumerate(sp["rows"]):
        to, at = row[evo_to], row[evo_lv]
        if at == 0 or to == 255:
            continue
        had = {r[2] for r in ls if r[0] == s and r[1] <= at}
        need = {r[2] for r in ls if r[0] == to and r[1] <= at}
        if not need <= had:
            trap.append((SPECIES[s], SPECIES[to], sorted(need - had)))
    check("refraction: a stage-2 kind's moves at or under its refraction level "
          "were learned by the stage-1 kind", not trap, trap)


def battle_won(g, sp, lv, xp, foe, flv, frames=4000):
    heap = bt.mon(sp, lv)
    heap.update({"p_xp+0": xp, "p_mv+0": 0, "p_mv+1": 255, "p_mv+2": 255, "p_mv+3": 255,
                 "f_sp": foe, "f_lv": flv, "ret_room": 1})
    return g.run("battle", heap, picks=[0, 0] * 8, rnd=ALWAYS, frames=frames)


def xp_and_cap(g):
    print("\n[a won battle: XP from species.xp, and the cap]")
    sp = g.tab["species"]
    xpc = col(sp, "xp")
    steamoth, pebblit, mossback = 12, 9, 1      # steamoth never refracts
    vm = battle_won(g, steamoth, 10, 0, pebblit, 3)
    want = sp["rows"][pebblit][xpc] * 3 // 10 + 4
    check("a 60-xp kind at L3 gives %d XP (the old f_lv * 6 + 4)" % want,
          g.said(vm, "battle_foe_fainted") and g.h(vm, "p_xp+0") == want == 3 * 6 + 4,
          g.h(vm, "p_xp+0"))
    vm = battle_won(g, steamoth, 10, 0, mossback, 4)
    want = sp["rows"][mossback][xpc] * 4 // 10 + 4
    check("a 90-xp kind at L4 gives %d (it read f_lv * 6 + 4 = 28 before)" % want,
          g.said(vm, "battle_foe_fainted") and g.h(vm, "p_xp+0") == want != 28,
          g.h(vm, "p_xp+0"))
    vm = battle_won(g, steamoth, CAP, 0, pebblit, 3)
    check("at the cap (L%d): no XP, 'CAN'T GROW PAST L%d YET', the level stays" % (CAP, CAP),
          g.said(vm, "battle_at_cap") and not g.said(vm, "battle_gained_xp")
          and g.h(vm, "p_lv+0") == CAP and g.h(vm, "p_xp+0") == 0,
          (g.h(vm, "p_lv+0"), g.h(vm, "p_xp+0")))
    vm = battle_won(g, steamoth, CAP - 1, (CAP - 1) * 12 - 1, pebblit, 3)
    check("one under it, a win reaches L%d, no further, and XP is held at 0" % CAP,
          g.said(vm, "battle_level_up_now") and g.h(vm, "p_lv+0") == CAP
          and g.h(vm, "p_xp+0") == 0, (g.h(vm, "p_lv+0"), g.h(vm, "p_xp+0")))
    vm = battle_won(g, steamoth, CAP - 1, 20 * 12 * 6, pebblit, 3)
    check("...even with XP for many levels (stops at L%d)" % CAP,
          g.h(vm, "p_lv+0") == CAP and g.h(vm, "p_xp+0") == 0, (g.h(vm, "p_lv+0"), g.h(vm, "p_xp+0")))


def fourth_move(g):
    print("\n[the 4th move at L10]")
    mossling = 0
    heap = {"m_sp": mossling, "m_lv": LV4, "lead": 0,
            "p_mv+0": 0, "p_mv+1": 1, "p_mv+2": 7, "p_mv+3": 255}
    vm = g.run("learn_new", heap, frames=600)
    check("a mossling reaching L10 learns BRAMBLE into its free 4th slot",
          [g.h(vm, "p_mv+%d" % i) for i in range(4)] == [0, 1, 7, 8] and g.said(vm, "battle_learned")
          and not g.said(vm, "battle_forget_ask"), [g.h(vm, "p_mv+%d" % i) for i in range(4)])
    full, short = [], []
    for s in range(len(g.tab["species"]["rows"])):
        vm = g.run("learn_moves", {"k": 0, "q_sp": s, "q_lv": LV4}, frames=600)
        mv = [g.h(vm, "p_mv+%d" % i) for i in range(4)]
        if 255 in mv or len(set(mv)) != 4:
            full.append((SPECIES[s], mv))
        vm = g.run("learn_moves", {"k": 0, "q_sp": s, "q_lv": LV4 - 1}, frames=600)
        if [g.h(vm, "p_mv+%d" % i) for i in range(4)][3] != 255:
            short.append(SPECIES[s])
    check("every kind met at L10 knows 4 different moves", not full, full[:3])
    check("...and at L9 three (the 4th slot free)", not short, short[:3])


def misses(g):
    print("\n[accuracy: a 90-accuracy move can miss]")
    high = lambda: 95  # noqa: E731  rand(100) -> 95
    vm = g.run("do_move", dict(BASE, side=0, mv=8), rnd=high, frames=600)
    check("BRAMBLE (90) with rand(100) = 95: BUT IT MISSED!, no damage",
          g.said(vm, "battle_missed") and g.h(vm, "f_hp") == 99, g.h(vm, "f_hp"))
    vm = g.run("do_move", dict(BASE, side=0, mv=0), rnd=high, frames=600)
    check("RAM (100) with the same roll hits", not g.said(vm, "battle_missed") and g.h(vm, "f_hp") < 99)


def starter_dex(g):
    print("\n[the starter is SEEN and OWNED]")
    for pick, sp in ((0, 0), (1, 3), (2, 6)):
        vm = g.run("elder_talk", {"party_n": 0}, picks=[pick], frames=900)
        bit = 1 << (sp & 15)
        check("starter %s: dex_seen and dex_own have its bit" % SPECIES[sp],
              g.h(vm, "p_sp+0") == sp and g.h(vm, "dex_own+%d" % (sp // 16)) & bit
              and g.h(vm, "dex_seen+%d" % (sp // 16)) & bit,
              (g.h(vm, "dex_own+0"), g.h(vm, "dex_seen+0")))


def tonic(g):
    print("\n[the pause menu's TONIC heals the lead]")
    heap = {"party_n": 2, "lead": 1, "it_tonic": 1,
            "p_sp+0": 3, "p_lv+0": 5, "p_hp+0": 3,
            "p_sp+1": 9, "p_lv+1": 5, "p_hp+1": 4}
    vm = g.run("menu_items", heap, picks=[0, 3], frames=1500)
    check("lead 1 goes 4 -> 24 HP, slot 0 stays at 3, one TONIC spent",
          g.h(vm, "p_hp+1") == 24 and g.h(vm, "p_hp+0") == 3 and g.h(vm, "it_tonic") == 0,
          (g.h(vm, "p_hp+0"), g.h(vm, "p_hp+1"), g.h(vm, "it_tonic")))


def main():
    g = bt.Game(PROJECT)
    append_only(g)
    level_cap(g)
    xp_and_cap(g)
    fourth_move(g)
    misses(g)
    starter_dex(g)
    tonic(g)
    print()
    if FAILS:
        print("%d FAILED" % len(FAILS))
        for f in FAILS:
            print("  -", f)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
