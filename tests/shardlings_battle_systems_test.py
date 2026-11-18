#!/usr/bin/env python3
"""vm-shardlings' battle systems (G1c), run in the reference VM.

G1c is game DATA only (no opcode, no RPN token the game did not use, so bank
0 does not move; record `docs/done/shardlings-g1c-battle-systems.md` in the
studio):

- status both ways: one `apply_fx` serves both sides (the target's status is
  copied into `x_*` and back), your sleep (`m_slp`, `my_act`), crack (status
  bit 4: the defender's DEF halved, not FOC; ROCK TOSS 1 in 5);
- stat stages -2..+2 per side (power `_sa`, guard `_sd`, x(2 + max(0, s)) /
  (2 - min(0, s))), reset at every battle start; GLARE / HARDEN are move ROWS
  with no power; the learnset is grouped by species (`species.ls`);
- capture tiers: SHARD / GLINT SHARD / PRISM SHARD, the chance from HP left,
  `rate`, the tier and the foe's status; the shop sells all three;
- Refraction: `evo_lv` / `evo_to`, after the level-ups of a won battle;
- uses: `p_pu` counts uses SPENT (an old save reads full), a spent slot asks
  again, every slot spent tackles for free, healing refills.

The driver is `shardlings_battle_test.Game` (the project's own blob and
TABLES in `RefVM`, text boxes dismissed, menus answered from a queue).

    python tests/shardlings_battle_systems_test.py [--project DIR]
"""
import os
import sys

import shardlings_battle_test as bt

check, FAILS = bt.check, bt.FAILS


def lcg(seed):
    st = [seed * 7919 + 1]

    def rnd():
        st[0] = (st[0] * 1103515245 + 12345) & 0x7FFFFFFF
        return st[0] >> 8
    return rnd


ALWAYS = lambda: 0  # noqa: E731  every 1-in-N roll hits
ONE = lambda: 1  # noqa: E731  every 1-in-N roll misses
BASE = {"m_atk": 5, "m_foc": 5, "f_atk": 5, "f_foc": 5, "f_def": 5, "m_def": 5,
        "f_el": 4, "m_el": 4, "f_hp": 99, "f_mhp": 99, "m_hp": 99, "m_mhp": 99}


def mon(sp, lv, **kw):
    d = {"p_sp+0": sp, "p_lv+0": lv, "p_hp+0": 200, "party_n": 1, "lead": 0}
    d.update(kw)
    return d


def status(g):
    print("\n[status both ways, your sleep, crack]")
    for mv, var, want, ref, what in ((4, "m_st", 1, "st_burned", "CINDER burns you"),
                                     (1, "m_st", 2, "st_poisoned", "VINE LASH poisons you"),
                                     (2, "m_slp", None, "st_asleep", "SPLASH puts you to sleep"),
                                     (3, "m_st", 4, "st_cracked", "ROCK TOSS cracks you")):
        vm = g.run("do_move", dict(BASE, side=1, mv=mv), rnd=ALWAYS, frames=600)
        v = g.h(vm, var)
        ok = (v in (2, 3)) if want is None else v == want
        check("the foe's %s (%s = %d), says it, tgt 1" % (what, var, v),
              ok and g.said(vm, ref) and g.h(vm, "tgt") == 1 and g.h(vm, "f_st") == 0
              and g.h(vm, "f_slp") == 0, (v, g.h(vm, "tgt")))
    vm = g.run("do_move", dict(BASE, side=0, mv=3), rnd=ALWAYS, frames=600)
    check("your ROCK TOSS cracks the foe (f_st 4, tgt 0)",
          g.h(vm, "f_st") == 4 and g.h(vm, "m_st") == 0 and g.h(vm, "tgt") == 0)
    vm = g.run("do_move", dict(BASE, side=0, mv=4, f_hp=1), rnd=ALWAYS, frames=600)
    check("a fainted foe takes no status", g.h(vm, "f_hp") == 0 and g.h(vm, "f_st") == 0,
          (g.h(vm, "f_hp"), g.h(vm, "f_st")))
    vm = g.run("do_move", dict(BASE, side=1, mv=4), rnd=ONE, frames=600)
    check("...nor when the 1-in-N roll misses", g.h(vm, "m_st") == 0)
    d = {}
    for st in (0, 4):
        for mv in (0, 1):
            vm = g.run("do_move", dict(BASE, side=0, mv=mv, f_st=st, f_el=7), rnd=ONE, frames=600)
            d[st, mv] = g.h(vm, "dmg")
    check("crack halves DEF: TACKLE %d -> %d" % (d[0, 0], d[4, 0]),
          d[4, 0] > d[0, 0] and d[4, 0] == 5 * 12 // (5 // 2 + 8) + 1, d)
    check("...but not FOC: VINE LASH %d == %d" % (d[0, 1], d[4, 1]), d[0, 1] == d[4, 1], d)
    vm = g.run("my_act", dict(BASE, m_slp=2, pm=0, pick=0, pu=255), rnd=ONE, frames=600)
    check("asleep: my_act sleeps (m_slp 2 -> 1), no move",
          g.h(vm, "m_slp") == 1 and g.said(vm, "battle_yours_fast_asleep")
          and not g.said(vm, "battle_you_used"))
    vm = g.run("my_act", dict(BASE, m_slp=0, pm=0, pick=0, pu=255), rnd=ONE, frames=600)
    check("awake: my_act moves", g.said(vm, "battle_you_used") and g.h(vm, "f_hp") < 99)
    hits = 0
    for seed in range(12):
        heap = mon(9, 9, **{"p_mv+0": 0, "p_mv+1": 255, "p_mv+2": 255, "p_mv+3": 255})
        heap.update(f_sp=0, f_lv=6)                      # foe sproutle: VINE LASH
        vm = g.run("battle", heap, picks=[0, 0] * 30, rnd=lcg(seed), frames=20000)
        if g.said(vm, "battle_yours_hurt_by"):
            hits += 1
    check("over 12 battles the foe's poison ticks on you in some (%d)" % hits, hits > 0)


def stages(g):
    print("\n[stat stages -2..+2]")
    vm = g.run("do_move", dict(BASE, side=0, mv=6), rnd=ONE, frames=600)
    check("your GLARE: foe power -1, no damage",
          g.h(vm, "f_sa") == -1 and g.h(vm, "f_hp") == 99 and g.said(vm, "st_power_fell"),
          (g.h(vm, "f_sa"), g.h(vm, "f_hp")))
    vm = g.run("do_move", dict(BASE, side=0, mv=6, f_sa=-2), rnd=ONE, frames=600)
    check("...at -2 nothing happens", g.h(vm, "f_sa") == -2 and g.said(vm, "st_no_effect"))
    vm = g.run("do_move", dict(BASE, side=1, mv=7), rnd=ONE, frames=600)
    check("the foe's HARDEN raises ITS guard",
          g.h(vm, "f_sd") == 1 and g.h(vm, "m_sd") == 0 and g.h(vm, "tgt") == 0)
    vm = g.run("do_move", dict(BASE, side=0, mv=7, m_sd=2), rnd=ONE, frames=600)
    check("your HARDEN at +2: nothing", g.h(vm, "m_sd") == 2 and g.said(vm, "st_no_effect"))
    want = (-2, -1, 0, 1, 2)
    got = {}
    for s in want:
        vm = g.run("do_move", dict(BASE, side=0, mv=0, m_atk=20, m_sa=s, f_el=7), rnd=ONE, frames=600)
        got[s] = g.h(vm, "a_atk")
    check("power stage scales ATK 20: %s" % got,
          all(got[s] == 20 * (2 + max(0, s)) // (2 - min(0, s)) for s in want), got)
    got = {}
    for s in want:
        vm = g.run("do_move", dict(BASE, side=0, mv=0, f_def=20, f_sd=s, f_el=7), rnd=ONE, frames=600)
        got[s] = g.h(vm, "d_def")
    check("guard stage scales DEF 20: %s" % got,
          all(got[s] == 20 * (2 + max(0, s)) // (2 - min(0, s)) for s in want), got)
    heap = mon(3, 5, **{"p_mv+0": 0, "p_mv+1": 255})
    heap.update(f_sp=9, f_lv=2, m_sa=2, f_sd=-2, m_slp=3)
    vm = g.run("battle", heap, picks=[], frames=300, stop=True)
    check("a battle starts with every stage at 0 and you awake",
          [g.h(vm, v) for v in ("m_sa", "m_sd", "f_sa", "f_sd", "m_slp")] == [0] * 5)
    print("\n[learnset grouped by species]")
    for sp, lv, want_mv in ((3, 5, [0, 4, 6, 255]), (3, 3, [0, 4, 255, 255]),
                            (9, 4, [0, 3, 7, 255]), (15, 1, [0, 5, 255, 255])):
        vm = g.run("learn_moves", {"q_sp": sp, "q_lv": lv, "k": 0}, frames=600)
        got = [g.h(vm, "p_mv+%d" % j) & 0xFF for j in range(4)]
        check("species %d lv %d learns %s (%d frames)" % (sp, lv, want_mv, g.frames),
              got == want_mv and g.h(vm, "mv") == sum(m != 255 for m in want_mv), got)
    vm = g.run("learn_moves", {"q_sp": 9, "q_lv": 4, "k": 3}, frames=600)
    check("the foe (k 3) into f_mv",
          [g.h(vm, "f_mv+%d" % j) & 0xFF for j in range(4)] == [0, 3, 7, 255])


def chance(g, s, rate, tier):
    """The catch roll's `r`, from the foe's cells at the moment it is rolled."""
    hp, mhp = g.hs(s, "f_hp"), g.hs(s, "f_mhp")
    return ((100 - hp * 66 // max(1, mhp)) * rate // 200 * (tier + 2) // 2
            + (g.hs(s, "f_st") != 0) * 10 + (g.hs(s, "f_slp") > 0) * 20)


def capture(g):
    print("\n[capture tiers]")
    sp = g.tab["species"]
    rate = sp["rows"][0][sp["columns"].index("rate")]
    rolled = ["battle_broke_free", "battle_caught"]
    for tier, cnt in ((0, "it_shard"), (1, "it_shard2"), (2, "it_shard3")):
        heap = mon(3, 5, **{"p_mv+0": 0, "p_mv+1": 255})
        heap.update(f_sp=0, f_lv=3, it_shard=2, it_shard2=2, it_shard3=2)
        vm = g.run("battle", heap, picks=[1, tier], rnd=ONE, frames=900, snap=rolled)
        r = g.hs(g.snaps[0], "r") if g.snaps else None
        want = (100 - 66) * rate // 200 * (tier + 2) // 2
        counts = [g.h(vm, c) for c in ("it_shard", "it_shard2", "it_shard3")]
        check("tier %d at full HP: r = %d (rate %d), only %s spent %s"
              % (tier, want, rate, cnt, counts),
              r == want and counts == [2 - (tier == 0), 2 - (tier == 1), 2 - (tier == 2)],
              (r, counts))
    # a foe put to sleep (SPLASH, every roll hits) and hurt: both bonuses
    heap = mon(6, 9, **{"p_mv+0": 2, "p_mv+1": 255})
    heap.update(f_sp=0, f_lv=9, it_shard=2)
    vm = g.run("battle", heap, picks=[0, 0, 1, 0], rnd=ALWAYS, frames=2000, snap=rolled)
    s = g.snaps[0] if g.snaps else None
    check("asleep and hurt: r = %s, the formula with +20 asleep (f_slp %s, f_hp %s/%s)"
          % (s and g.hs(s, "r"), s and g.hs(s, "f_slp"), s and g.hs(s, "f_hp"),
             s and g.hs(s, "f_mhp")),
          s is not None and g.hs(s, "f_slp") > 0 and g.hs(s, "f_hp") < g.hs(s, "f_mhp")
          and g.hs(s, "r") == chance(g, s, rate, 0)
          and g.hs(s, "r") >= 20 + (100 - 66) * rate // 200)
    heap = mon(3, 5, **{"p_mv+0": 0, "p_mv+1": 255})
    heap.update(f_sp=0, f_lv=3, it_shard=2, it_shard2=0, it_shard3=0)
    vm = g.run("battle", heap, picks=[1, 1], frames=600, stop=True)
    check("GLINT with none: 'none of those', the command menu again, nothing spent",
          g.said(vm, "battle_no_shards_left") and g.menus == [1, 1, None]
          and g.h(vm, "it_shard") == 2, (g.menus, g.h(vm, "it_shard")))
    caught = caught0 = 0
    for seed in range(60):
        vm = g.run("battle", dict(heap, it_shard3=1), picks=[1, 2, 0, 0, 0, 0],
                   rnd=lcg(seed), frames=2000)
        caught += bool(g.said(vm, "battle_caught"))
        vm = g.run("battle", dict(heap, it_shard=1), picks=[1, 0, 0, 0, 0, 0],
                   rnd=lcg(seed), frames=2000)
        caught0 += bool(g.said(vm, "battle_caught"))
    check("PRISM catches more often than SHARD over 60 seeds (%d > %d)" % (caught, caught0),
          caught > caught0)
    vm = g.run("buy_glint", {"gold": 200}, frames=300)
    check("the shop: GLINT for 120G", g.h(vm, "gold") == 80 and g.h(vm, "it_shard2") == 1)
    vm = g.run("buy_prism", {"gold": 200}, frames=300)
    check("...PRISM 250G refused at 200G", g.h(vm, "gold") == 200 and g.h(vm, "it_shard3") == 0
          and g.said(vm, "shop_not_enough_gold"))


def refraction(g):
    print("\n[Refraction]")
    sp = g.tab["species"]
    col = sp["columns"]
    heap = {"m_sp": 0, "m_lv": 12, "m_hp": 10, "m_mhp": 56, "p_sp+0": 0}
    vm = g.run("refract", heap, frames=900)
    hp1 = sp["rows"][1][col.index("hp")] + 12 * 3
    check("SPROUTLE -> MOSSBACK: species, max HP %d, HP up by the gain" % hp1,
          g.h(vm, "m_sp") == 1 and g.h(vm, "m_mhp") == hp1 and g.h(vm, "m_hp") == 10 + hp1 - 56
          and g.said(vm, "battle_refract_start") and g.said(vm, "battle_refract_done"),
          (g.h(vm, "m_sp"), g.h(vm, "m_mhp"), g.h(vm, "m_hp")))
    check("...owned and seen in the Shardex",
          g.h(vm, "dex_own+0") & 2 and g.h(vm, "dex_seen+0") & 2)
    heap = mon(0, 11, **{"p_mv+0": 0, "p_mv+1": 255, "p_xp+0": 11 * 12 - 1, "p_hp+0": 99})
    heap.update(f_sp=9, f_lv=2)
    vm = g.run("battle", heap, picks=[0, 0] * 30, rnd=lcg(3), frames=20000)
    check("a won battle that reaches lv 12 refracts; p_sp stores MOSSBACK",
          g.said(vm, "battle_foe_fainted") and g.said(vm, "battle_refract_done")
          and g.h(vm, "p_sp+0") == 1 and g.h(vm, "p_lv+0") == 12,
          (g.h(vm, "p_sp+0"), g.h(vm, "p_lv+0")))
    heap = mon(15, 30, **{"p_mv+0": 0, "p_mv+1": 255, "p_hp+0": 200})
    heap.update(f_sp=9, f_lv=2)
    vm = g.run("battle", heap, picks=[0, 0] * 30, rnd=lcg(3), frames=20000)
    check("TESSRA (evo_lv 0) never refracts", g.said(vm, "battle_foe_fainted")
          and not g.said(vm, "battle_refract_start") and g.h(vm, "p_sp+0") == 15)
    heap = mon(2, 30, **{"p_mv+0": 0, "p_mv+1": 255, "p_hp+0": 200})
    heap.update(f_sp=9, f_lv=2)
    vm = g.run("battle", heap, picks=[0, 0] * 30, rnd=lcg(3), frames=20000)
    check("THORNWYRM (last stage) never refracts", g.said(vm, "battle_foe_fainted")
          and not g.said(vm, "battle_refract_start") and g.h(vm, "p_sp+0") == 2)


def uses(g):
    print("\n[uses: SPENT per slot]")
    col = g.tab["moves"]["columns"].index("uses")
    u_cinder = g.tab["moves"]["rows"][4][col]
    heap = mon(3, 5, **{"p_mv+0": 4, "p_mv+1": 0, "p_mv+2": 255, "p_mv+3": 255,
                        "p_pu+0": u_cinder, "p_pu+1": 0})
    heap.update(f_sp=9, f_lv=2)
    vm = g.run("battle", heap, picks=[0, 0], frames=600, stop=True)
    check("CINDER spent (%d of %d): 'no uses left', asks again, no move" % (u_cinder, u_cinder),
          g.said(vm, "battle_no_uses") and g.menus == [0, 0, None]
          and not g.said(vm, "battle_you_used"))
    vm = g.run("battle", heap, picks=[0, 1, 0], frames=900, stop=True, rnd=ONE)
    check("TACKLE (slot 1) spends one use: p_pu+1 = 1",
          g.h(vm, "p_pu+1") == 1 and g.h(vm, "p_pu+0") == u_cinder, (g.h(vm, "p_pu+1"),))
    vm = g.run("battle", dict(heap, **{"p_pu+1": 99}), picks=[0, 0, 0], frames=900,
               stop=True, rnd=ONE)
    check("every slot spent: a free TACKLE ('no uses left at all'), nothing spent",
          g.said(vm, "battle_struggle") and g.said(vm, "battle_you_used")
          and g.h(vm, "pm") == 0 and g.h(vm, "p_pu+1") == 99 and g.h(vm, "p_pu+0") == u_cinder)
    vm = g.run("my_act", dict(BASE, m_slp=2, pm=0, pick=1, pu=3, lead=0, **{"p_pu+1": 3}),
               frames=600)
    check("asleep: no use spent", g.h(vm, "p_pu+1") == 3)
    h = {"party_n": 2, "p_sp+0": 3, "p_lv+0": 5, "p_sp+1": 0, "p_lv+1": 5}
    h.update({"p_pu+%d" % j: 7 for j in range(12)})
    vm = g.run("heal_party", h, frames=900)
    check("heal_party refills both members' uses",
          [g.h(vm, "p_pu+%d" % j) for j in range(8)] == [0] * 8 and g.h(vm, "p_pu+8") == 7)
    vm = g.run("learn_moves", {"q_sp": 3, "q_lv": 5, "k": 1,
                               **{"p_pu+%d" % j: 7 for j in range(12)}}, frames=600)
    check("a new member (k 1) starts with full uses",
          [g.h(vm, "p_pu+%d" % j) for j in range(4, 8)] == [0] * 4 and g.h(vm, "p_pu+0") == 7)


def whole(g):
    print("\n[whole battles with all four moves still end]")
    ends = 0
    for seed in range(10):
        heap = mon(3, 8, **{"p_mv+0": 0, "p_mv+1": 4, "p_mv+2": 6, "p_mv+3": 7, "p_hp+0": 50})
        heap.update(f_sp=9, f_lv=4)
        vm = g.run("battle", heap, picks=[0, seed % 4] * 60, rnd=lcg(seed + 100), frames=40000)
        ends += g.done and (bool(g.said(vm, "battle_foe_fainted"))
                            or bool(g.said(vm, "battle_shardling_fainted")))
    check("10 of 10 battles end (%d)" % ends, ends == 10)


def main():
    print("=== shardlings_battle_systems_test ===")
    g = bt.Game(bt.project_arg())
    status(g)
    stages(g)
    capture(g)
    refraction(g)
    uses(g)
    whole(g)
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
