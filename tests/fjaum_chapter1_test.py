#!/usr/bin/env python3
"""vm-fjaum's Chapter 1 data (C1d), run in the reference VM.

C1d is game DATA only (no opcode, no RPN token the game did not use):

- the encounter tables: `zones` (each zone's first row) and `wild` (sp, lv,
  span, w: `w` the cumulative weight out of 100); `encounter_tick` rolls
  r = rand(100) and walks the zone's rows while r >= w, the level is
  lv + rand(240) % span. Zone 1 Mossway's grass, 2 Mosswood's ferns, 3 the
  Ridgeway's heath grass;
- the grass is COUNTED (grass_in_N +1 and sets the zone, grass_out -1, never
  below 0): the engine spawns the next rect's enter before the last one's
  leave, so a 1 / 0 latch read "out" after a step from one rect into a
  touching one;
- the four Chapter 1 trainers (`trainers` rows 2..5, flag bits 2..5, three
  `tparty` rows each) and the Moss Keeper (row 1), each with a talk script:
  no party, the challenge and the battle, the line after a win;
- the six new species (16..21, the species art step): their rows and
  learnsets, "?????" after them in the `species` list, an 11-letter name
  spelled in the battle HUD (four packed `names` cells), and each line's
  refraction at 12, its Dex bits in the second cell.

The driver is `fjaum_battle_test.Game`.

    python tests/fjaum_chapter1_test.py [--project DIR]
"""
import sys

import fjaum_battle_test as bt

check, FAILS = bt.check, bt.FAILS
TALKS = {1: "keeper", 2: "farmhand", 3: "forager", 4: "pilgrim", 5: "alpinist"}


def seq(*vals):
    """A PRNG that answers `vals` in order, then 0."""
    it = iter(vals)
    return lambda: next(it, 0)


def lcg(seed):
    st = [seed * 7919 + 1]

    def rnd():
        st[0] = (st[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return st[0] >> 8
    return rnd


def tick(g, zone, r, lv_roll, **kw):
    """encounter_tick once: the 1-in-10 roll hits (0), then r, then the level roll."""
    heap = {"in_grass": 1, "party_n": 1, "zone": zone, "grace": 0, "lx": 1, "f_sp": 255, "f_lv": 0}
    heap.update(kw)
    return g.run("encounter_tick", heap, rnd=seq(0, r, lv_roll), frames=200)


def encounters(g):
    print("\n[encounters: the zone tables]")
    wild, zones = g.tab["wild"], g.tab["zones"]
    check("zone 0 is no zone; zones 1..3 (Mossway, Mosswood, the Ridgeway)",
          zones["rows"][0] == [255] and len(zones["rows"]) == 4, zones["rows"])
    for z in range(1, len(zones["rows"])):
        i, lo, seen = zones["rows"][z][0], 0, []
        while True:
            sp, lv, span, w = wild["rows"][i]
            for r in sorted({lo, w - 1}):
                for roll in sorted({0, span - 1, 239}):
                    vm = tick(g, z, r, roll)
                    want = lv + roll % span
                    ok = (g.h(vm, "f_sp") == sp and g.h(vm, "f_lv") == want
                          and vm.change_log and vm.change_log[-1][0] == 3)
                    seen.append(ok)
                    if not ok:
                        check("zone %d, r %d, level roll %d: species %d lv %d, the battle room"
                              % (z, r, roll, sp, want), False,
                              (g.h(vm, "f_sp"), g.h(vm, "f_lv"), vm.change_log))
            lo = w
            if w >= 100:
                break
            i += 1
        check("zone %d: every row's first and last r, every level of its span (%d runs)" % (z, len(seen)),
              all(seen))
    vm = tick(g, 1, 0, 0, in_grass=2)
    check("two rects at once (in_grass 2) still roll", vm.change_log and vm.change_log[-1][0] == 3)
    vm = tick(g, 1, 0, 0, in_grass=0)
    check("out of the grass (in_grass 0): nothing", not vm.change_log and g.h(vm, "f_sp") == 255)
    heap = {"in_grass": 1, "party_n": 1, "zone": 2, "grace": 0, "lx": 1}
    vm = g.run("encounter_tick", heap, rnd=seq(10), frames=200)
    check("the 1-in-10 roll missing (10): nothing", not vm.change_log)
    counts = {}
    for seed in range(300):
        vm = g.run("encounter_tick", {"in_grass": 1, "party_n": 1, "zone": 1, "grace": 0, "lx": 1},
                   rnd=lcg(seed), frames=200)
        if vm.change_log:
            counts[g.h(vm, "f_sp")] = counts.get(g.h(vm, "f_sp"), 0) + 1
    want = {wild["rows"][i][0] for i in range(zones["rows"][1][0], zones["rows"][2][0])}
    check("Mossway draws only its own species (%s)" % sorted(counts), set(counts) <= want and counts, counts)


def grass(g):
    print("\n[the grass is counted]")
    for z in (1, 2, 3):
        vm = g.run("grass_in_%d" % z, {"in_grass": 0, "zone": 0}, frames=20)
        a = (g.h(vm, "in_grass"), g.h(vm, "zone"))
        vm = g.run("grass_in_%d" % z, {"in_grass": 1, "zone": 0}, frames=20)
        check("grass_in_%d: in (0 -> 1, 1 -> 2), zone %d" % (z, z),
              a == (1, z) and (g.h(vm, "in_grass"), g.h(vm, "zone")) == (2, z), (a, g.h(vm, "in_grass")))
    vm = g.run("grass_in_3", {"in_grass": 1, "zone": 2}, frames=20)
    check("the next rect's enter first: 2, zone 3", g.h(vm, "in_grass") == 2 and g.h(vm, "zone") == 3)
    vm = g.run("grass_out", {"in_grass": 2}, frames=20)
    check("...then the last one's leave: still in (1)", g.h(vm, "in_grass") == 1)
    vm = g.run("grass_out", {"in_grass": 0}, frames=20)
    check("a leave with nothing in never goes below 0", g.h(vm, "in_grass") == 0)


def trainers(g):
    print("\n[the Chapter 1 trainers and the Keeper]")
    tr, tp = g.tab["trainers"], g.tab["tparty"]
    names = tr["row_names"]
    check("rows: wren, moss_keeper, farmhand, forager, pilgrim, alpinist",
          names == ["wren", "moss_keeper", "farmhand", "forager", "pilgrim", "alpinist"], names)
    col = {c: i for i, c in enumerate(tr["columns"])}
    flags = [tr["rows"][k][col["flag"]] for k in range(len(names))]
    check("each its own flag bit (%s)" % flags, flags == list(range(len(names))))
    for k in range(2, len(names)):
        first = tp["rows"][tr["rows"][k][col["tp"]]]
        vm = g.run("trainer_start", {"trn": k}, frames=300)
        check("%s (row %d): first %s, trainer %d, the battle room" % (names[k], k, first, k + 1),
              [g.h(vm, "f_sp"), g.h(vm, "f_lv")] == first and g.h(vm, "trainer") == k + 1
              and vm.change_log and vm.change_log[-1][0] == 3, (g.h(vm, "f_sp"), g.h(vm, "f_lv")))
    for k, n in TALKS.items():
        bit = 1 << tr["rows"][k][col["flag"]]
        vm = g.run(n + "_talk", {"party_n": 1, "flags": 0}, frames=900)
        check("%s_talk: the challenge, then the battle (trainer %d)" % (n, k + 1),
              g.said(vm, n + "_hi") and g.h(vm, "trainer") == k + 1 and vm.change_log
              and vm.change_log[-1][0] == 3 and g.h(vm, "flags") == 0)
        vm = g.run(n + "_talk", {"party_n": 1, "flags": bit}, frames=900)
        check("...beaten: the after line, no battle", g.said(vm, n + "_after") and not vm.change_log)
        vm = g.run(n + "_talk", {"party_n": 0, "flags": 0}, frames=900)
        check("...no party: come back", g.said(vm, "trainer_no_party") and not vm.change_log)
    # a won battle against the farmhand's last member: gold, the flag, no badge
    last = [r for r in tp["rows"][tr["rows"][2][col["tp"]]:][:3] if r[0] != 255]
    heap = {"p_sp+0": 2, "p_lv+0": 30, "p_hp+0": 200, "party_n": 1, "lead": 0,
            "p_mv+0": 2, "p_mv+1": 255, "p_mv+2": 255, "p_mv+3": 255,
            "f_sp": last[-1][0], "f_lv": last[-1][1], "trainer": 3, "trn": 2,
            "tslot": len(last) - 1, "gold": 0, "flags": 0, "badges": 0}
    vm = g.run("battle", heap, picks=[0, 0] * 40, rnd=lcg(5), frames=30000)
    gold = tr["rows"][2][col["gold"]]
    check("the farmhand's last falls: %d gold, flag bit 2, no badge" % gold,
          g.h(vm, "gold") == gold and g.h(vm, "flags") == 4 and g.h(vm, "badges") == 0
          and g.said(vm, "battle_trainer_paid"), (g.h(vm, "gold"), g.h(vm, "flags")))


NEW = ["burrbit", "thistlehare", "budwick", "petalisk", "screelet", "quarrapede"]
LETTERS = " ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def new_species(g):
    print("\n[the six new species]")
    sp, ls = g.tab["species"], g.tab["learnset"]
    col = {c: i for i, c in enumerate(sp["columns"])}
    check("22 species, the new six at 16..21 in line order",
          len(sp["rows"]) == 22 and sp["row_names"][16:] == NEW, sp["row_names"][16:])
    for i in range(16, 22):
        at = sp["rows"][i][col["ls"]]
        rows = ls["rows"][at:at + 3]
        check("%s: learnset rows %d.. are its own (RAM at 1, its element at 1, a 4th at 4)" % (NEW[i - 16], at),
              [r[0] for r in rows] == [i] * 3 and [r[1] for r in rows] == [1, 1, 4] and rows[0][2] == 0, rows)
    check("the learnset ends with the last species' rows (the walks read to its length)",
          max(r[0] for r in ls["rows"]) == 21 and len(ls["rows"]) == 66)
    # a foe's moves (learn_moves, k = 3 -> f_mv): the walk past a species'
    # rows jumps to the table's end, so it must END for every species
    for q, want in ((0, [0, 1, 7, 255]), (15, [0, 5, 7, 255]), (21, [0, 3, 6, 255])):
        vm = g.run("learn_moves", {"q_sp": q, "q_lv": 5, "k": 3}, frames=600)
        got = [g.h(vm, "f_mv+%d" % j) & 0xFF for j in range(4)]
        check("learn_moves ends for species %d at lv 5: f_mv %s" % (q, want), g.done and got == want, (g.done, got))
    import tomllib
    with open(bt.os.path.join(bt.project_arg(), "scripts", "dialogue.toml"), "rb") as f:
        items = tomllib.load(f)["lists"]["species"]["items"]
    check("the species list: the names in id order, then ????? (the Dex's unknown entry, 22)",
          [x.lower() for x in items[16:22]] == NEW and items.index("?????") == 22, items[14:])
    vm = g.run("draw_names", {"f_sp": 21, "m_sp": 17}, frames=200)
    cells = list(vm.bkg_tiles.values())
    base = cells[10]                    # QUARRAPEDE is 10 letters: its 11th cell is the blank
    spell = lambda vs: "".join(LETTERS[v - base] if 0 <= v - base < len(LETTERS) else "#" for v in vs)  # noqa: E731
    check("the HUD spells the foe QUARRAPEDE and the lead THISTLEHARE (11 cells each)",
          len(cells) == 22 and spell(cells[:11]) == "QUARRAPEDE " and spell(cells[11:]) == "THISTLEHARE",
          (spell(cells[:11]), spell(cells[11:])))
    for a in (16, 18, 20):
        b = sp["rows"][a][col["evo_to"]]
        lv = sp["rows"][a][col["evo_lv"]]
        vm = g.run("refract", {"m_sp": a, "m_lv": 12, "m_hp": 10, "m_mhp": 50, "p_sp+0": a}, frames=900)
        check("%s refracts at %d into %s, owned and seen (Dex cell 1, bit %d)"
              % (NEW[a - 16].upper(), lv, (sp["row_names"][b] if b < len(sp["row_names"]) else str(b)).upper(), b & 15),
              lv == 12 and g.h(vm, "m_sp") == b and g.h(vm, "dex_own+1") & (1 << (b & 15))
              and g.h(vm, "dex_seen+1") & (1 << (b & 15)), (g.h(vm, "m_sp"), g.h(vm, "dex_own+1")))
    check("the stage 2s refract with Chapter 2 (evo_lv 0, evo_to 255)",
          all(sp["rows"][i][col["evo_lv"]] == 0 and sp["rows"][i][col["evo_to"]] == 255 for i in (17, 19, 21)))


def main():
    print("=== fjaum_chapter1_test ===")
    g = bt.Game(bt.project_arg())
    for fn in (encounters, grass, trainers, new_species):
        fn(g)
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
