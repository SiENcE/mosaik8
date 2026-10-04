#!/usr/bin/env python3
"""Per-stage frame budget over rooms x regimes.

The one instrument every stage of the 60 fps render work landed
with: jump straight into each room the engine's own way (poke the pending
exception, RAISE 2), hook the entry of every per-frame stage, and report the
entry-to-next-entry cycle delta per game frame - idle, walking, autofire.

Every stage below must be priced against the two thresholds: one LCD frame is
70,224 cycles (60 fps), and the vsync RATCHET means a saving is invisible
until a whole-frame threshold crosses (the 2026-08-26 scan fixes saved 21k
and idle stayed 2 LCD/VM; only the 3-LCD spikes got rarer).

Usage: framebudget.py ROM.gb SYM.noi [--rooms 8,11,5] [--frames 600]

The .noi comes from a -Wl-j relink (`tools/framebudget/build_noi.py`). Room
indexes are world.toml scene order; the default is room 0 alone (the rooms
worth pricing are a fact about the project, so name them).
"""
import re
import sys

LCD = 70224

#: Stage ENTRY symbols in execution order (a missing one is skipped). The
#: delta from one entry to the next is that stage's cost plus the glue after
#: it, so read neighbours together when a number looks odd.
#:
#: **A MISSING SYMBOL SILENTLY FOLDS ITS STAGE INTO THE ONE BEFORE IT**, and
#: that is the trap this list exists to avoid (found 2026-08-26). `vm.core`'s
#: frame tail is proj_render -> the music catch-up -> the HUD diff -> present,
#: and only ONE spelling of the music tick was listed - so on an hUGEDriver
#: project (every GB conversion) `vm_projectile_render` was charged the whole
#: tail. It read as "4.6k cycles of projectile work in a room with no shot in
#: flight"; probed, the pool walk never even calls `slot_of` there. Likewise
#: the emote update sits between `actor.render` and the animator, and was
#: being charged to render. Keep BOTH driver spellings and every optional
#: per-frame hook here; a project that lacks one is skipped as before.
STAGES = [
    "_vm_core_run_scripts",
    "_vm_player_update_platform", "_vm_player_update_shmup", "_vm_player_update",
    "_engine_scroll_update",
    "_vm_player_put_player",
    # The TRIGGER SCAN sits between put_player and entity.update (the generated
    # tick_* wrapper calls it), and it is not small: 10,170 cycles a frame in
    # the town room, all of it charged to `put_player` until it was listed here
    # (2026-08-28) - which read as "put_player costs 11.8k with the move latch
    # already in" and hid the largest single idle item in that room.
    "_vm_trigger_update",
    "_vm_entity_update", "_vm_entity_contact_scan",
    "_vm_core_interact_pressed",
    "_vm_projectile_update",
    "_vm_player_cam_apply",
    "_vm_actor_step_all", "_vm_actor_render",
    "_vm_emote_update",
    # tick_all -> anim.tick -> apply* -> tick_player -> draw_player. Without
    # the last two listed, the animator's PLAYER half folds into whichever of
    # the first two ran last, which flips between builds as the apply gate
    # changes how often `apply` is called at all.
    "_vm_canim_tick_all", "_vm_canim_apply",
    "_vm_canim_tick_player", "_vm_canim_draw_player",
    "_scenes_anim_tick_at",
    "_vm_projectile_render",
    # the two music drivers: vm.music (portable) and the real hUGEDriver
    "_vm_music_update", "_vm_music_huge_update",
    "_hud_update", "_vm_hud_update",
    "_gbs_wait_vblank",
]

REGIMES = [("idle", (), 0), ("walk right", ("right",), 0),
           ("autofire A", (), 1)]


def symbols(noi):
    out = {}
    for line in open(noi, encoding="utf-8", errors="replace"):
        m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
        if m:
            out[m.group(1)] = int(m.group(2), 16)
    return out


def main():
    if len(sys.argv) < 3 or sys.argv[1].startswith("--"):
        print(__doc__)
        return 2
    rom, noi = sys.argv[1], sys.argv[2]
    rooms = [0]
    if "--rooms" in sys.argv:
        rooms = [int(v) for v in
                 sys.argv[sys.argv.index("--rooms") + 1].split(",")]
    frames = 600
    if "--frames" in sys.argv:
        frames = int(sys.argv[sys.argv.index("--frames") + 1])

    def arg_int(name, default):
        return (int(sys.argv[sys.argv.index(name) + 1])
                if name in sys.argv else default)

    s = symbols(noi)
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null")
    present = [(n, s[n]) for n in STAGES if n in s]
    ev, rec, gf_seen = [], [False], [0]

    def mk(tag):
        def hit(_c):
            if rec[0]:
                ev.append((pb._cycles(), tag))
        return hit

    def mk_first(tag):
        """The frame's FIRST stage also counts GAME frames, so the autofire
        regime can pace its taps on them (see the loop below)."""
        def hit(_c):
            if rec[0]:
                ev.append((pb._cycles(), tag))
                gf_seen[0] += 1
        return hit

    for i, (tag, val) in enumerate(present):
        cb = mk_first(tag) if i == 0 else mk(tag)
        pb.hook_register(val >> 16, val & 0xFFFF, cb, None)

    g = lambda n: s[n] & 0xFFFF

    def w16(a, v):
        pb.memory[a], pb.memory[a + 1] = v & 0xFF, (v >> 8) & 0xFF

    def goto(room):
        pb.memory[g("_vm_core_pend_code")] = 2
        pb.memory[g("_vm_core_pend_a")] = room
        w16(g("_vm_core_pend_b"), 40)
        w16(g("_vm_core_pend_c"), 72)
        for _ in range(300):
            pb.tick()

    for _ in range(120):
        pb.tick()
    first = present[0][0]
    for room in rooms:
        goto(room)
        print("\n==== room %d ====" % room)
        for label, held, fire in REGIMES:
            # `--reset-regimes`: RE-ENTER THE ROOM BEFORE EACH REGIME.
            #
            # The regimes run back to back, so by default each one starts where
            # the previous one left the WORLD - and a faster build got further
            # in the same 600/1200 LCD frames. In a calm town that is nothing;
            # in a shooter room, whose cost RISES as the level scrolls and more
            # enemies wake, it means the second and third regimes of a faster
            # build are measured deeper into the level than the slower build's
            # (2026-08-31: a change that cut idle 2.10 -> 1.95 at 1,200 frames
            # made idle run 42 MORE game frames, and the walk regime that
            # inherited that position then read 2.67 -> 2.76 while every stage
            # it does not touch went up together). Resetting costs the
            # comparability of the numbers against every earlier session's
            # table, which is why it is a FLAG and not the default: use it to
            # answer "is this regime's move real", quote the default form.
            if "--reset-regimes" in sys.argv:
                for b in ("right", "a"):
                    pb.button_release(b)
                goto(room)
            for b in held:
                pb.button_press(b)
            for _ in range(30):
                pb.tick()
            ev.clear()
            gf_seen[0] = 0
            rec[0] = True
            # THE AUTOFIRE PATTERN IS PACED IN GAME FRAMES, NOT LCD FRAMES
            # (2026-08-26). An optimisation changes how many LCD frames a VM
            # frame takes, so an LCD-paced tap reaches the fire script at a
            # different point in a faster build - which made a shooter room's
            # autofire regime swing by +-0.07 LCD/frame between builds for a
            # reason that is not the change. Counting the frame's first stage
            # aligns the input with the game, so two builds see the same taps.
            # `--game-frames N`: CLOSE THE WINDOW ON A GAME-FRAME COUNT, not
            # an LCD one.
            #
            # The default window is LCD frames, which is what makes
            # `LCD/frame` mean anything - but it also means two builds cover
            # DIFFERENT amounts of the game, and in a room whose per-frame cost
            # depends on how far the level has scrolled (a shooter room: enemies
            # wake, shots accumulate) the two effects are inseparable. Windowing
            # on GAME frames makes the two runs cover the same world - the
            # `oam_trace.py` streams are byte-identical per game frame, so
            # frame N really is the same frame - and the stage table is then a
            # like-for-like cost comparison. It cannot report LCD/frame as a
            # rate for the same reason, so read `work` from it, not the ratio.
            gf_cap = int(arg_int("--game-frames", 0))
            t = 0
            while (gf_seen[0] < gf_cap) if gf_cap else (t < frames):
                if fire:
                    k = gf_seen[0]
                    if k % 10 == 0:
                        pb.button_press("a")
                    elif k % 10 == 5:
                        pb.button_release("a")
                pb.tick()
                t += 1
                if gf_cap and t > gf_cap * 12:
                    break
            rec[0] = False
            for b in held:
                pb.button_release(b)
            pb.button_release("a")
            total, gf = {}, 0
            for i in range(len(ev) - 1):
                c, tag = ev[i]
                total[tag] = total.get(tag, 0) + (ev[i + 1][0] - c)
                if tag == first:
                    gf += 1
            work = sum(total.values())
            # under --game-frames the window closed on a GAME-frame count, so
            # the LCD count is whatever it took - report the real one, or the
            # rate is a fixed 600/450 on every build and reads as "no change"
            used = t if gf_cap else frames
            print("-- %-12s %d game frames in %d LCD (%.2f LCD/frame; "
                  "frame work %.0f cyc = %.2f LCD)"
                  % (label, gf, used, used / max(1, gf),
                     work / max(1, gf), work / max(1, gf) / LCD))
            for tag, _v in present:
                if tag in total:
                    v = total[tag] / max(1, gf)
                    print("   %-28s %7.0f" % (tag.lstrip("_"), v))


if __name__ == "__main__":
    main()
