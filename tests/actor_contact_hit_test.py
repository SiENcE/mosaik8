#!/usr/bin/env python3
"""A collision-group actor's script is its ON HIT, and the player's BODY fires it.

The reference engine gives an actor ONE script and decides what it is from the collision
group - the two arms are MUTUALLY EXCLUSIVE (`states/platform.c:1448`, and the
same in `topdown.c` / `shmup.c`):

    if (hit_actor->collision_group & COLLISION_GROUP_MASK)
        player_register_collision_with(hit_actor);        // -> ON HIT
    else if (INPUT_PRESSED(INTERACT)) { ... run script }  // -> ON INTERACT

`actors_handle_player_collision` (`core/actor.c:551`) then runs it on a plain
per-frame box OVERLAP - no button, no facing, no movement - and arms
`PLAYER_HURT_IFRAMES` (20).

Two independent misses compounded here, which is why this survived four
conversions: the studio's importer routed `script -> on_interact`
unconditionally, AND `vm.entity`'s `en_hit` had exactly one caller
(`on_projectile_hit`), so the engine had no player-body-vs-actor test at all.
The long walk-in room's stompable enemies therefore lost their whole mechanic -
a platformer stomp whose `then` arm explodes and deactivates them and whose `else` arm is
the knockback (sfx, `health - 1`, `shake 30 @ 5`).

ONE slot, dispatched on ARG 0 - because that is what the reference engine's own compiler
emits. It folds the per-group On Hit tabs into `script` behind `if (ARG0 == n)`
(read the shooter room's generated `..._p_hit1.s`: `VM_GET_TLOCAL
.LOCAL_TMP0_PARAM0_VALUE, 0` / `VM_IF_CONST .EQ, ..., 2`), `actor.c` runs it
with **0** for player contact and `projectiles.c` with the shot's own collision
group. Measured before choosing: **19 of the sample's 23 group-carrying actors
have BOTH** a contact script and a projectile On Hit, so picking one and
dropping the other would have been wrong for nearly all of them.

MEASURED on the ROM (the reference-engine sample conversion, GB), same nav into
the long walk-in room, counting rising edges of `en_hitcool[]` (the per-actor On
Hit debounce, which exists in BOTH builds - the room has no projectiles, so an
edge there is a contact hit and nothing else):

    BEFORE  0 On Hit scripts fired        AFTER  4
    and with the fix the knockback's `shake 30 @ 5` shows: SCY over the i-frame
    window spans 251..255 and 0..5, a two-sided +-5.

Cost: 47 B of bank 0 (GB 1,294 -> 1,247 / GBC 349 -> 302 spare).

A FAILED instrument worth remembering: counting per-scanline SCY jumps as "the
shake" read 36 before and 50 after - no signal. The probe has to press A to
clear the room's ledges, and a JUMP moves the camera further per frame than a
+-5 shake does, so it was measuring the camera. Anchor the instrument to the
thing that fires, not to the picture it happens to move.
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_FAILED = []


def check(cond, what):
    print(("  [ok] " if cond else "  [FAIL] ") + what)
    if not cond:
        _FAILED.append(what)


def _read(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def _code(src):
    return "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("--"))


def _body(src, name):
    m = re.search(r"function %s\([^)]*\)[^{]*\{" % re.escape(name), src)
    if not m:
        return ""
    i, depth = m.end(), 1
    while i < len(src) and depth:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    return src[m.end():i - 1]


def main():
    print("a collision-group actor's script is an ON HIT, fired by contact")
    print("=" * 64)

    entity = _read("lib", "vm", "entity.mos")
    actor = _read("lib", "vm", "actor.mos")
    core = _read("lib", "vm", "core.mos")

    # --- the scan itself --------------------------------------------------
    cs = _code(_body(entity, "contact_scan"))
    check(bool(cs), "vm.entity has contact_scan()")
    check("actor.overlap_at(player.px, player.py)" in cs,
          "it asks vm.actor which actor overlaps the player's box (the box "
          "top-left IS the position, read direct since 2026-09-03)")
    check("hurt_if" in cs and "HURT_IFRAMES" in cs,
          "gated by a PLAYER-side i-frame counter, not a per-actor one")
    check("core.fire_player_hit()" in cs,
          "the player's own hit script runs too, as the reference engine's does")
    calls = [ln for ln in _code(entity).splitlines()
             if "contact_scan()" in ln and "function" not in ln]
    check(len(calls) == 2,
          "called from BOTH update() arms - VM_UPDATE_ALWAYS and the park-scan "
          "one (%d call site(s))" % len(calls))
    check("n_hit == 0" in cs,
          "skipped whole in a room with no On Hit slot bound")

    # --- one scan, two callers -------------------------------------------
    ov = _code(_body(actor, "overlap_at"))
    bl = _code(_body(actor, "blocked"))
    check("return 255" in ov and "return i" in ov,
          "vm.actor.overlap_at returns the SLOT (255 = none)")
    check("overlap_at(" in bl and "a_bw" not in bl,
          "blocked() delegates to it rather than keeping a second copy")

    # The double-add that predated this: box_x()/box_y() ARE pos_x()/pos_y().
    check("player.pos_x() + player.box_x()" not in _code(actor),
          "actor_hit no longer adds the box origin to itself")

    # --- the ARG 0 dispatch ----------------------------------------------
    fh = _code(_body(entity, "fire_hit"))
    check("core.set_arg(" in fh, "fire_hit seeds thread argument 0 with the cause")
    check("cause != 0" in fh,
          "player CONTACT passes 0 by leaving spawn()'s zeroed args alone")
    check("PROJ_ARG" in _code(_body(entity, "on_projectile_hit")),
          "a projectile reports a NON-zero cause, so it cannot land in the "
          "contact arm")
    check("function set_arg" in core and "set_arg," in core,
          "vm.core.set_arg exists and is exported")

    # --- (c) the knockback IMPULSE ---------------------------------------
    print()
    player = _read("lib", "vm", "player.mos")
    kb = _code(_body(player, "knockback"))
    check("kb_len == 0" in kb, "knockback() is a no-op until configured")
    check("pface == 3" in kb and "kb_dir" in kb,
          "thrown AWAY from the facing, as state_enter_knockback does")
    check("vy = 0 - j" in kb and "grounded = 0" in kb,
          "...and upward, ungrounded")
    upd = _code(_body(player, "update_platform"))
    check("if kb_frames > 0 {" in upd and upd.index("kb_frames > 0")
          < upd.index("hacc == 0"),
          "the knockback owns the horizontal BEFORE the pad is read, which is "
          "how the reference engine locks input out (it stays in KNOCKBACK_STATE)")
    conf = _read("mosaik_vm", "rooms", "config.py")
    check('"knockback_frames": 0' in conf,
          "[player] knockback_frames defaults to 0 (byte-identical off)")

    # --- (d) the shot's own GROUP ----------------------------------------
    print()
    proj = _read("lib", "vm", "projectile.mos")
    check("var p_group" in proj and "function set_group" in proj,
          "vm.projectile tracks a shot's own group beside its mask")
    check("g_hit(a, p_group[i])" in _code(proj),
          "...and hands it to the On Hit seam")
    check("l_group = 0" in _code(proj),
          "the group is a ONE-SHOT latch, like the animation one")
    from mosaik_vm import isa
    check("PROJ_GROUP" in isa.OPS and isa.OPS["PROJ_GROUP"][1] == ["u8"],
          "PROJ_GROUP is a one-operand latch op")
    check("PLAYER_KNOCKBACK" in isa.OPS
          and isa.OPS["PLAYER_KNOCKBACK"][1] == [],
          "PLAYER_KNOCKBACK takes no operands (the impulse is an engine field)")
    prog2 = None
    from mosaik_vm import compiler as _c
    prog2 = _c.Compiler().compile([{"name": "main", "events": [
        {"event": "player_knockback"},
        {"event": "projectile", "x": 0, "y": 0, "vx": 1, "vy": 0,
         "tile": 0, "life": 30, "mask": 1, "group": 2}]}])
    check(len(prog2.code) > 0, "both new events compile")

    # --- (b) the sprite STATE ---------------------------------------------
    print()
    actor = _read("lib", "vm", "actor.mos")
    check("var a_spin" in actor and "function set_anim_state" in actor,
          "vm.actor carries a script-pinned clip STATE, offset by one")
    canim = _code(_read("lib", "vm", "canim.mos"))
    # The pin rides the R2 batched actor.anim_in read - bits 6..4 now, since
    # bit 7 became the QUIET verdict (tests/actor_anim_edge_test.py). Three
    # bits is the whole state pin: four clip states plus the unset zero.
    check("(ai >> 4) & 0x07" in canim,
          "vm.canim honours the pin over the movement-derived state")
    i_pin = canim.index("(ai >> 4) & 0x07")
    i_fb = canim.index("if g_count(k, st, a_face[i]) == 0")
    check(i_pin < i_fb,
          "...and the pin still falls back to idle when that state has no "
          "frames for this facing")
    check("A_SET_ANIM_STATE" in isa.OPS
          and isa.OPS["A_SET_ANIM_STATE"][1] == ["u8", "u8"],
          "A_SET_ANIM_STATE takes (actor, state)")

    # --- play-once: the authoring bit ------------------------------------
    print()
    # the reference engine's set-state LOOPS unless play-once is asked for (its
    # `VM_ACTOR_SET_FLAGS actor, 0, ACTOR_FLAG_ANIM_NOLOOP` CLEARS the bit).
    check("ANIM_ONCE" in actor and "anim_state_once" in actor,
          "play-once rides bit 7 of the pin byte - no second array")
    check("anim.play_once(" in canim and "anim.set(" in canim,
          "vm.canim finally chooses between engine.anim's two registrations")
    from mosaik_vm import compiler as _c2
    enc = {}
    for lp in (1, 0, None):
        e = {"event": "actor_set_anim_state", "actor": 0, "state": 2}
        if lp is not None:
            e["loop"] = lp
        enc[lp] = _c2.Compiler().compile(
            [{"name": "main", "events": [e]}]).code[2]
    check(enc[1] == 0x02 and enc[0] == 0x82 and enc[None] == 0x02,
          "loop is the DEFAULT (0x02); play-once sets bit 7 (0x82), so a "
          "conversion stays byte-identical")

    # --- and it compiles as an event condition ---------------------------
    from mosaik_vm import compiler
    prog = compiler.Compiler().compile([{"name": "main", "events": [
        {"event": "if", "cond": "arg(0) == 0",
         "then": [{"event": "wait", "frames": 1}],
         "else": [{"event": "wait", "frames": 2}]}]}])
    check(len(prog.code) > 0, "arg(0) is a legal event condition (it compiles)")

    print("=" * 64)
    print("All checks passed" if not _FAILED else "FAILED: %d" % len(_FAILED))
    return 1 if _FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
