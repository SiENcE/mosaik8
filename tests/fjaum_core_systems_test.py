#!/usr/bin/env python3
"""vm-fjaum's core systems (G1d), run in the reference VM.

G1d is game DATA only (no opcode, no RPN token the game did not use, so bank
0 does not move):

- facets: a `facets` table (the stat up, the stat down; row 0 EVEN is
  neutral, so a member saved before G1d keeps its stats), rolled 0..8 for
  the starter, a caught one and every foe (EVEN included: `rand(9)`; it was
  `1 + rand(8)` and never rolled EVEN until 2026-11-20), applied in `sp_stats`
  (+/- 1 + stat / 10), shown on the party card;
- sockets and gems: `gems` (kind, arg), `p_gem` the socket, `it_gem` the
  bag; SPARK, four element gems, MEND, LURE, WIND; a drop 1 in 8 after a
  won wild battle; SOCKET on the party card, GEMS under ITEMS;
- trainers by table: `trainers` + `tparty` (three rows a trainer), one
  `trainer_start`, the next of a party by re-entering the battle room, the
  flag on a WIN, gold / badge / gem from the row (Wren row 0, the first
  Keeper row 1);
- the Dex page (pause menu, four a page as list-item options, a card,
  OWNED off `dex_own`) and the elder's rewards at 4 / 8 / 12 / 16 owned;
- gold from wild battles (`f_lv` x 3) and the shop as tables (`wares`,
  `shops`, one `shop_run`, one `give_item`);
- a level-up learns that level's moves into a free slot.

The driver is `fjaum_battle_test.Game`.

    python tests/fjaum_core_systems_test.py [--project DIR]
"""
import sys

import fjaum_battle_test as bt

check, FAILS = bt.check, bt.FAILS
ALWAYS = lambda: 0  # noqa: E731
ONE = lambda: 1  # noqa: E731


def const(v):
    return lambda: v


def lcg(seed):
    st = [seed * 7919 + 1]

    def rnd():
        st[0] = (st[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return st[0] >> 8
    return rnd


def mon(sp, lv, **kw):
    d = {"p_sp+0": sp, "p_lv+0": lv, "p_hp+0": 200, "party_n": 1, "lead": 0,
         "p_mv+0": 0, "p_mv+1": 255, "p_mv+2": 255, "p_mv+3": 255}
    d.update(kw)
    return d


def base_stats(g, sp, lv):
    t = g.tab["species"]
    row, col = t["rows"][sp], t["columns"]
    return {"q_atk": row[col.index("atk")] + lv, "q_def": row[col.index("def")] + lv,
            "q_spd": row[col.index("spd")] + lv, "q_foc": row[col.index("foc")] + lv,
            "q_mhp": row[col.index("hp")] + lv * 3}


def facets(g):
    print("\n[facets]")
    ft = g.tab["facets"]
    stats = ["q_atk", "q_def", "q_spd", "q_foc"]
    ok = True
    for f in range(1, 9):
        up, dn = ft["rows"][f]
        vm = g.run("sp_stats", {"q_sp": 9, "q_lv": 20, "q_fac": f}, frames=200)
        want = base_stats(g, 9, 20)
        for j, s in enumerate(stats):
            v = want[s]
            want[s] = v + ((j == up) - (j == dn)) * (1 + v // 10)
        got = {s: g.h(vm, s) for s in want}
        if got != want:
            ok = False
            print("     facet %d: %s != %s" % (f, got, want))
    check("each of the 8 facets: + (1 + s/10) on `up`, - on `dn`, HP untouched (pebblit lv 20)", ok)
    vm = g.run("sp_stats", {"q_sp": 9, "q_lv": 20, "q_fac": 0}, frames=200)
    check("EVEN (0, every member saved before G1d) changes nothing",
          {s: g.h(vm, s) for s in stats} == {s: base_stats(g, 9, 20)[s] for s in stats})
    vm = g.run("sp_stats", {"q_sp": 9, "q_lv": 20, "q_fac": 255}, frames=200)
    check("an out-of-range facet changes nothing",
          {s: g.h(vm, s) for s in stats} == {s: base_stats(g, 9, 20)[s] for s in stats})
    vm = g.run("add_caught", {"f_sp": 9, "f_lv": 4, "f_fac": 5, "party_n": 1}, frames=600)
    check("a caught one keeps the foe's facet (p_fac+1 = 5)", g.h(vm, "p_fac+1") == 5)
    vm = g.run("party_load", {"p_sp+0": 9, "p_lv+0": 20, "p_fac+0": 3, "lead": 0}, frames=200)
    want = base_stats(g, 9, 20)
    check("party_load applies the lead's facet (STOUT: DEF up, ATK down)",
          g.h(vm, "m_def") == want["q_def"] + 1 + want["q_def"] // 10
          and g.h(vm, "m_atk") == want["q_atk"] - 1 - want["q_atk"] // 10 and g.h(vm, "m_fac") == 3,
          (g.h(vm, "m_def"), g.h(vm, "m_atk")))
    heap = mon(3, 5, f_sp=9, f_lv=20)
    vm = g.run("battle", heap, rnd=const(7), frames=300, stop=True)
    want = base_stats(g, 9, 20)
    check("a foe rolls a facet (rand(9) -> 7 = KEEN) and fights with it",
          g.h(vm, "f_fac") == 7 and g.h(vm, "f_foc") == want["q_foc"] + 1 + want["q_foc"] // 10
          and g.h(vm, "f_atk") == want["q_atk"] - 1 - want["q_atk"] // 10,
          (g.h(vm, "f_fac"), g.h(vm, "f_foc"), g.h(vm, "f_atk")))
    vm = g.run("battle", heap, rnd=const(0), frames=300, stop=True)
    check("...and EVEN is rolled too (rand(9) -> 0, f_fac 0, stats untouched)",
          g.h(vm, "f_fac") == 0 and g.h(vm, "f_foc") == want["q_foc"]
          and g.h(vm, "f_atk") == want["q_atk"], (g.h(vm, "f_fac"), g.h(vm, "f_foc")))
    vm = g.run("battle", heap, rnd=const(8), frames=300, stop=True)
    check("...and WISE, the last (rand(9) -> 8)", g.h(vm, "f_fac") == 8, g.h(vm, "f_fac"))
    vm = g.run("elder_talk", {"party_n": 0}, picks=[1], rnd=const(4), frames=900)
    check("the starter rolls one (rand(9) -> 4: p_fac+0 = 4)", g.h(vm, "p_fac+0") == 4 and g.h(vm, "party_n") == 1,
          (g.h(vm, "p_fac+0"),))
    vm = g.run("elder_talk", {"party_n": 0}, picks=[1], rnd=const(0), frames=900)
    check("...EVEN included (rand(9) -> 0: p_fac+0 = 0)", g.h(vm, "p_fac+0") == 0 and g.h(vm, "party_n") == 1,
          (g.h(vm, "p_fac+0"),))
    vm = g.run("menu_party", {"party_n": 1, "p_sp+0": 3, "p_lv+0": 5, "p_fac+0": 4}, picks=[0], frames=900)
    check("the party card's second box shows it (the lead's: its second box is menu_card2_lead)",
          g.said(vm, "menu_card2_lead") and g.h(vm, "q_fac") == 4)
    # a level-up recomputes with the facet and refreshes SPD / FOC
    heap = mon(9, 5, **{"p_xp+0": 5 * 12 - 1, "p_fac+0": 5})
    heap.update(f_sp=0, f_lv=2)
    vm = g.run("battle", heap, picks=[0, 0] * 30, rnd=lcg(4), frames=20000)
    want = base_stats(g, 9, 6)
    check("after a level-up (lv 6, FLEET) the lead's SPD is the facet's",
          g.h(vm, "m_lv") == 6 and g.h(vm, "m_spd") == want["q_spd"] + 1 + want["q_spd"] // 10,
          (g.h(vm, "m_lv"), g.h(vm, "m_spd")))


def gems(g):
    print("\n[sockets and gems]")
    vm = g.run("party_load", {"p_sp+0": 3, "p_lv+0": 5, "p_gem+0": 5, "lead": 0}, frames=200)
    check("party_load reads the socket (EMBER GEM: kind 2, fire)",
          g.h(vm, "m_gem") == 5 and g.h(vm, "gk") == 2 and g.h(vm, "ga") == 3)
    base = {"a_atk": 20, "a_st": 0, "pow": 16, "d_def": 4, "d_el": 4, "side": 0}
    vm = g.run("calc_damage", dict(base, mel=3, gk=0, ga=0), rnd=ONE, frames=200)
    plain = g.h(vm, "dmg")
    vm = g.run("calc_damage", dict(base, mel=3, gk=2, ga=3), rnd=ONE, frames=200)
    check("an element gem: +25 %% on its element (%d -> %d)" % (plain, g.h(vm, "dmg")),
          g.h(vm, "dmg") == plain * 5 // 4 and plain > 4)
    vm = g.run("calc_damage", dict(base, mel=0, gk=2, ga=3), rnd=ONE, frames=200)
    check("...not on another", g.h(vm, "dmg") == plain)
    vm = g.run("calc_damage", dict(base, mel=3, gk=2, ga=3, side=1), rnd=ONE, frames=200)
    check("...and never for the foe's move", g.h(vm, "dmg") == plain)
    vm = g.run("calc_damage", dict(base, mel=4, gk=1), rnd=const(2), frames=200)
    c1 = g.h(vm, "crit")
    vm = g.run("calc_damage", dict(base, mel=4, gk=0), rnd=const(2), frames=200)
    check("SPARK: a roll of 2 in 16 crits with it, not without",
          c1 == 1 and g.h(vm, "crit") == 0, (c1, g.h(vm, "crit")))
    for gem, want in ((6, True), (0, False)):
        heap = mon(3, 9, **{"p_gem+0": gem, "p_hp+0": 30})
        heap.update(f_sp=9, f_lv=2)
        vm = g.run("battle", heap, picks=[0, 0], rnd=ONE, frames=900, stop=True)
        check("%s: the end of a turn %s" % ("MEND" if want else "no gem",
                                            "heals (says it)" if want else "heals nothing"),
              bool(g.said(vm, "battle_gem_mend")) == want and g.said(vm, "battle_foe_used_move"))
        if want:
            mend_hp = g.h(vm, "m_hp")
        else:
            check("...MEND left %d HP, none %d: + max(1, 46 / 16) = 2" % (mend_hp, g.h(vm, "m_hp")),
                  mend_hp == g.h(vm, "m_hp") + 2)
    heap = mon(3, 5, **{"p_gem+0": 7})
    heap.update(f_sp=0, f_lv=3, it_shard=2)
    vm = g.run("battle", heap, picks=[1, 0], rnd=ONE, frames=900,
               snap=["battle_broke_free", "battle_caught"])
    rate = g.tab["species"]["rows"][0][g.tab["species"]["columns"].index("rate")]
    r = g.hs(g.snaps[0], "r") if g.snaps else None
    check("LURE: the catch chance +15 (r %s)" % r, r == (100 - 66) * rate // 200 + 15)
    for gem, want in ((8, True), (0, False)):
        heap = mon(3, 5, **{"p_gem+0": gem})
        heap.update(f_sp=0, f_lv=3)
        vm = g.run("battle", heap, picks=[3], rnd=ALWAYS, frames=900)
        check("RUN on a roll that fails: %s" % ("WIND gets away" if want else "no gem stays"),
              bool(g.said(vm, "battle_got_away_safely")) == want)
    heap = mon(3, 9, **{"p_mv+0": 4})
    heap.update(f_sp=9, f_lv=2)
    vm = g.run("battle", heap, picks=[0, 0] * 20, rnd=ALWAYS, frames=20000)
    check("a won wild battle drops a gem on a 1-in-8 roll (rand 0 -> SPARK)",
          g.said(vm, "battle_gem_drop") and g.h(vm, "it_gem+1") == 1)
    heap = mon(3, 9, **{"p_mv+0": 4})
    heap.update(f_sp=9, f_lv=2)
    vm = g.run("battle", heap, picks=[0, 0] * 20, rnd=ONE, frames=20000)
    check("...and none on another roll", not g.said(vm, "battle_gem_drop")
          and g.said(vm, "battle_foe_fainted"))
    h = {"k": 0, "q_sp": 3, "p_gem+0": 0, "it_gem+2": 1, "it_gem+5": 1}
    vm = g.run("socket", h, picks=[2], frames=600)
    check("SOCKET a MOSS GEM: p_gem 2, the bag 1 -> 0",
          g.h(vm, "p_gem+0") == 2 and g.h(vm, "it_gem+2") == 0 and g.said(vm, "gem_socketed"))
    vm = g.run("socket", dict(h, **{"p_gem+0": 2, "it_gem+2": 0}), picks=[5], frames=600)
    check("...swap for an EMBER GEM: the MOSS GEM goes back to the bag",
          g.h(vm, "p_gem+0") == 5 and g.h(vm, "it_gem+2") == 1 and g.h(vm, "it_gem+5") == 0)
    vm = g.run("socket", dict(h, **{"p_gem+0": 2, "it_gem+2": 0}), picks=[0], frames=600)
    check("...REMOVE: empty socket, the gem in the bag",
          g.h(vm, "p_gem+0") == 0 and g.h(vm, "it_gem+2") == 1 and g.said(vm, "gem_removed"))
    vm = g.run("socket", dict(h, **{"it_gem+3": 0}), picks=[3], frames=600)
    check("...a gem you do not have: 'none of those', nothing moves",
          g.h(vm, "p_gem+0") == 0 and g.said(vm, "battle_no_shards_left"))
    vm = g.run("menu_items", {"it_gem+1": 2, "it_gem+6": 1}, picks=[2, 3], frames=900)
    check("ITEMS > GEMS: one box a gem you have (2)",
          len(g.said(vm, "menu_gem_count")) == 2 and not g.said(vm, "menu_no_gems"))


def trainers(g):
    print("\n[trainers by table]")
    vm = g.run("trainer_start", {"trn": 0, "rv_sp": 9}, frames=300)
    check("Wren (row 0): f_sp = the rival's species, lv 5, trainer 1, the battle room",
          g.h(vm, "f_sp") == 9 and g.h(vm, "f_lv") == 5 and g.h(vm, "trainer") == 1
          and vm.change_log and vm.change_log[-1][0] == 3, (g.h(vm, "f_sp"), vm.change_log))
    vm = g.run("rival_talk", {"party_n": 1, "rv_sp": 9, "flags": 0}, frames=900)
    check("rival_talk starts it WITHOUT setting the flag (a WIN sets it)",
          g.h(vm, "trainer") == 1 and g.h(vm, "flags") == 0 and vm.change_log)
    vm = g.run("rival_talk", {"party_n": 1, "rv_sp": 9, "flags": 1}, frames=900)
    check("...after a win, the after-battle line", g.said(vm, "wren_after_battle") and not vm.change_log)

    def keeper_fight(tslot, sp, lv, seed=5):
        heap = mon(2, 30, **{"p_mv+0": 2, "p_hp+0": 200})
        heap.update(f_sp=sp, f_lv=lv, trainer=2, trn=1, tslot=tslot, gold=0, flags=0, badges=0)
        return g.run("battle", heap, picks=[0, 0] * 40, rnd=lcg(seed), frames=30000)
    vm = keeper_fight(0, 0, 9)
    check("the Keeper's first falls: 'about to send out', the battle room again, tslot 1, f_sp 13",
          g.said(vm, "battle_trainer_next") and vm.change_log and vm.change_log[-1][0] == 3
          and g.h(vm, "tslot") == 1 and g.h(vm, "f_sp") == 13 and g.h(vm, "flags") == 0,
          (g.h(vm, "tslot"), g.h(vm, "f_sp"), vm.change_log[-1:]))
    check("...no reward yet", g.h(vm, "gold") == 0 and g.h(vm, "badges") == 0)
    vm = keeper_fight(2, 1, 12)
    check("the last falls: 300 gold, flag bit 1, badge 0, a MOSS GEM",
          g.h(vm, "gold") == 300 and g.h(vm, "flags") == 2 and g.h(vm, "badges") == 1
          and g.h(vm, "it_gem+2") == 1 and g.said(vm, "battle_got_badge")
          and g.said(vm, "battle_trainer_paid") and vm.change_log[-1][0] != 3,
          (g.h(vm, "gold"), g.h(vm, "flags"), g.h(vm, "badges"), g.h(vm, "it_gem+2")))
    heap = mon(2, 30, **{"p_mv+0": 2})
    heap.update(f_sp=3, f_lv=5, trainer=1, trn=0, tslot=0, gold=0, flags=0)
    vm = g.run("battle", heap, picks=[0, 0] * 40, rnd=lcg(5), frames=30000)
    check("Wren: 50 gold, flag bit 0, no badge, no gem",
          g.h(vm, "gold") == 50 and g.h(vm, "flags") == 1 and g.h(vm, "badges") == 0
          and not g.said(vm, "battle_got_badge"))
    heap = mon(3, 2, **{"p_hp+0": 1})
    heap.update(f_sp=11, f_lv=30, trainer=2, trn=1, tslot=0, flags=0)
    vm = g.run("battle", heap, picks=[0, 0] * 40, rnd=lcg(5), frames=30000)
    check("a lost trainer battle sets no flag (a rematch)",
          g.said(vm, "battle_fjaum_fainted") and g.h(vm, "flags") == 0)
    vm = g.run("menu_trainer", {"badges": 0b101, "gold": 7}, frames=300)
    check("the trainer card counts badges (2)", g.h(vm, "q_b") == 2)


def dex(g):
    print("\n[the Dex]")
    vm = g.run("menu_trainer", {"dex_seen+0": 0x0209, "dex_own+0": 0x0008, "party_n": 2}, frames=300)
    check("the trainer card: OWNED is dex_own (1), not the party (2)",
          g.h(vm, "q_n") == 3 and g.h(vm, "q_own") == 1, (g.h(vm, "q_n"), g.h(vm, "q_own")))
    h = {"dex_seen+0": 0b1000000000001001, "dex_own+0": 0b1000}
    vm = g.run("menu_dex", h, picks=[5], frames=600)
    unk = len(g.tab["species"]["rows"])          # ????? follows the last species
    check("page 1: d0..d3 = MOSSLING, ?????, ?????, EMBERKIT (????? = %d)" % unk,
          [g.h(vm, "d%d" % j) for j in range(4)] == [0, unk, unk, 3],
          [g.h(vm, "d%d" % j) for j in range(4)])
    vm = g.run("menu_dex", h, picks=[3, 5], frames=900, snap=["dex_card"])
    s = g.snaps[0] if g.snaps else None
    check("EMBERKIT's card: No.4, FIRE, CAUGHT",
          s is not None and g.hs(s, "q_nr") == 4 and g.hs(s, "q_el") == 3 and g.hs(s, "q_n") == 1)
    vm = g.run("menu_dex", h, picks=[1, 5], frames=900)
    check("an unseen one: 'not seen yet'", g.said(vm, "dex_unknown") and not g.said(vm, "dex_card"))
    vm = g.run("menu_dex", h, picks=[4, 4, 4, 5], frames=1200)
    check("MORE pages to page 4 (TESSRA last: d3 = 15)",
          g.h(vm, "dx_pg") == 3 and g.h(vm, "d3") == 15, (g.h(vm, "dx_pg"), g.h(vm, "d3")))
    pages = (unk + 3) // 4
    vm = g.run("menu_dex", h, picks=[4] * (pages - 1) + [5], frames=3000)
    last = (g.h(vm, "dx_pg"), [g.h(vm, "d%d" % j) for j in range(4)])
    vm = g.run("menu_dex", h, picks=[4] * pages + [5], frames=3000)
    check("%d pages: the last holds the last species (unseen: ?????), one more MORE wraps to page 1" % pages,
          last[0] == pages - 1 and all(d == unk for d in last[1]) and g.h(vm, "dx_pg") == 0, (last, g.h(vm, "dx_pg")))
    vm = g.run("pause_menu", {"dex_seen+0": 1}, picks=[2, 5, 5], frames=1200)
    check("the pause menu: DEX is option 2 (its menu opens), then CLOSE",
          g.menus[:3] == [2, 5, 5] and g.done, g.menus)
    vm = g.run("dex_reward", {"dex_own+0": 0b1111, "it_shard2": 0, "dex_rw": 0}, frames=900)
    check("4 owned: the elder gives 3 GLINT SHARDS once",
          g.h(vm, "it_shard2") == 3 and g.h(vm, "dex_rw") == 1 and len(g.said(vm, "elder_dex_reward")) == 1)
    vm = g.run("dex_reward", {"dex_own+0": 0b1111, "it_shard2": 3, "dex_rw": 1}, frames=900)
    check("...and not twice", g.h(vm, "it_shard2") == 3 and not g.said(vm, "elder_dex_reward"))
    vm = g.run("dex_reward", {"dex_own+0": 0xFFFF, "gold": 10, "dex_rw": 0}, frames=1500)
    check("16 owned at once: all four (1010 gold, a LURE GEM, 2 PRISM)",
          g.h(vm, "dex_rw") == 15 and g.h(vm, "gold") == 1010 and g.h(vm, "it_gem+7") == 1
          and g.h(vm, "it_shard3") == 2 and len(g.said(vm, "elder_dex_reward")) == 4)


def economy(g):
    print("\n[gold and the shop table]")
    heap = mon(3, 9, **{"p_mv+0": 4})
    heap.update(f_sp=9, f_lv=4, gold=10)
    vm = g.run("battle", heap, picks=[0, 0] * 20, rnd=ONE, frames=20000)
    check("a won wild battle pays f_lv * 3 (12)", g.h(vm, "gold") == 22 and g.said(vm, "battle_found_gold"),
          g.h(vm, "gold"))
    vm = g.run("shop_run", {"shop": 0, "gold": 200}, picks=[2, 4], frames=1500)
    check("GLINT for 120G, then LEAVE", g.h(vm, "gold") == 80 and g.h(vm, "it_shard2") == 1
          and g.said(vm, "shop_bought_shard"))
    check("...the menu's wares are the shop's row (w0..w3 = 0..3)",
          [g.h(vm, "w%d" % j) for j in range(4)] == [0, 1, 2, 3])
    vm = g.run("shop_run", {"shop": 0, "gold": 200}, picks=[3, 4], frames=1500)
    check("PRISM 250G refused at 200G", g.h(vm, "gold") == 200 and g.h(vm, "it_shard3") == 0
          and g.said(vm, "shop_not_enough_gold"))
    vm = g.run("shop_run", {"shop": 0, "gold": 200}, picks=[0, 0, 4], frames=2000)
    check("two TONICS", g.h(vm, "gold") == 140 and g.h(vm, "it_tonic") == 2)


def learning(g):
    print("\n[a level-up learns]")
    heap = mon(0, 3, **{"p_xp+0": 3 * 12 - 1, "p_mv+0": 0, "p_mv+1": 1})
    heap.update(f_sp=9, f_lv=2)
    vm = g.run("battle", heap, picks=[0, 0] * 30, rnd=lcg(2), frames=20000)
    check("MOSSLING reaching lv 4 learns BRACE into slot 2 (uses full)",
          g.h(vm, "p_lv+0") == 4 and g.h(vm, "p_mv+2") == 7 and g.h(vm, "p_pu+2") == 0
          and g.said(vm, "battle_learned"), (g.h(vm, "p_lv+0"), g.h(vm, "p_mv+2")))
    heap.update({"p_mv+2": 5, "p_mv+3": 3})
    vm = g.run("battle", heap, picks=[0, 0] * 30, rnd=lcg(2), frames=20000)
    check("...a full set asks which to forget (C1e; the next pick, 0, forgets RAM for BRACE)",
          g.said(vm, "battle_forget_ask") and g.said(vm, "battle_forgot")
          and [g.h(vm, "p_mv+%d" % j) for j in range(4)] == [7, 1, 5, 3],
          [g.h(vm, "p_mv+%d" % j) for j in range(4)])
    heap = mon(0, 5, **{"p_xp+0": 5 * 12 - 1})
    heap.update(f_sp=9, f_lv=2)
    vm = g.run("battle", heap, picks=[0, 0] * 30, rnd=lcg(2), frames=20000)
    check("lv 6 has no move to learn: nothing said", g.h(vm, "p_lv+0") == 6
          and not g.said(vm, "battle_learned") and not g.said(vm, "battle_cant_learn"))


def whole(g):
    print("\n[whole battles still end]")
    ends = 0
    for seed in range(10):
        heap = mon(3, 8, **{"p_mv+1": 4, "p_mv+2": 6, "p_hp+0": 50, "p_gem+0": 1 + seed % 8,
                            "p_fac+0": 1 + seed % 8})
        heap.update(f_sp=9, f_lv=4)
        vm = g.run("battle", heap, picks=[0, seed % 3] * 60, rnd=lcg(seed + 100), frames=40000)
        ends += g.done and bool(g.said(vm, "battle_foe_fainted") or g.said(vm, "battle_fjaum_fainted"))
    check("10 of 10 battles with a facet and a gem end (%d)" % ends, ends == 10)


SECTIONS = {"facets": facets, "gems": gems, "trainers": trainers, "dex": dex,
            "economy": economy, "learning": learning, "whole": whole}


def main():
    print("=== fjaum_core_systems_test ===")
    g = bt.Game(bt.project_arg())
    for fn in SECTIONS.values():
        fn(g)
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
