#!/usr/bin/env python3
"""A FLIP_X change must re-issue the sprite's position on the same frame.

A metasprite's CELL LAYOUT is reversed from the latched sprite property inside
`gbs_move_sprite` (`cc = (prop & FLIP_X) ? w - 1 - c : c`), NOT inside the tile
fan - so the move and the property have to agree. `vm.core.run` orders a frame

    g_player()      -> vm.player.follow_and_render -> put_player -> sprite.move
    actor.render()  -> the same shape for the pool
    g_anim()        -> vm.canim.tick_all -> draw_player / apply -> set_prop

i.e. both sprites are MOVED before the animator sets the property. On the frame
a facing flips, the tiles were therefore mirrored while the columns kept the
PREVIOUS facing's order, for exactly one frame. Reported from play on
the reference-engine sample conversion: turning right->left, up->left, down->left, left->up and
left->down each showed one wrong frame, while down->right->up (all flip 0) was
correct - LEFT being the only facing `mosaik_anim`'s `flip_left` derives with
hardware FLIP_X is the whole diagnosis.

The fix re-issues the move from inside the branch that CHANGES the flip, so it
costs one `sprite.move` per turn and nothing while a facing holds. It lives in
`lib/vm` rather than the GBDK backend on purpose: the backend alternative
(remember x/y per meta base inside `gbs_set_sprite_prop`) costs BSS on every
console and only fixes the GB family, while the Lynx and PCE meta layers have
the same ordering exposure.

SOURCE-CONTRACT test: it pins that each flip branch re-places its sprite, and
that the two repositioning entry points exist and are exported.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

VM = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "lib", "vm")


def check(label, cond):
    print("[%s] %s" % ("PASS" if cond else "FAIL", label))
    return bool(cond)


def _read(name):
    with open(os.path.join(VM, name), encoding="utf-8") as f:
        return f.read()


def _body(src, name):
    """The text of function `name` (to its closing brace at the same indent)."""
    m = re.search(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    indent = m.group(1)
    end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
    return src[m.start():end.end() if end else len(src)]


def _flip_branch(fn, prev):
    """The `if f != <prev> { ... }` block inside a draw function."""
    m = re.search(r"if f != %s \{" % re.escape(prev), fn)
    if not m:
        return ""
    depth, i = 0, m.end() - 1
    while i < len(fn):
        if fn[i] == "{":
            depth += 1
        elif fn[i] == "}":
            depth -= 1
            if depth == 0:
                return fn[m.start():i + 1]
        i += 1
    return ""


def main():
    canim = _read("canim.mos")
    player = _read("player.mos")
    actor = _read("actor.mos")
    ok = True

    # --- vm.canim: both draw paths re-place inside the flip branch -----------
    # `apply` is TWO compile-time arms since the batched selector
    # (VM_CLIP_SEL, 2026-09-01): the batched arm inlines draw_frame over the
    # selector's is_desc bit (draw_meta / g_draw), the original arm is kept
    # character for character. Every ordering rule here holds PER ARM, so the
    # body is split at the fork and each half checked on its own.
    ap_full = _body(canim, "apply")
    _split = ap_full.find("if VM_CLIP_SEL {} else {")
    ok &= check("apply() carries the VM_CLIP_SEL fork", _split >= 0)
    ap_a, ap_b = ap_full[:max(0, _split)], ap_full[max(0, _split):]
    # ...and `draw_player` carries the same fork since 2026-09-03 (the player
    # half of the batched selector), so it splits into two arms exactly as
    # apply does: the batched one inlines draw_frame over the selector's
    # is_desc bit, the original keeps the draw_frame call.
    dp_full = _body(canim, "draw_player")
    _dsplit = dp_full.find("if VM_CLIP_SEL {} else {")
    ok &= check("draw_player() carries the VM_CLIP_SEL fork", _dsplit >= 0)
    dp_a, dp_b = dp_full[:max(0, _dsplit)], dp_full[max(0, _dsplit):]
    for label, fn, prev, prop_call, repos_call, meta_call, sites in (
            # `draw_frame` is the ONE upload entry point (it picks the dense /
            # blank-masked rectangle or, for a descriptor kind, the per-object
            # list); what this test pins is its ORDER against the flip branch.
            ("draw_player[batched]", dp_a, "p_pflip",
             "sprite.set_prop(p_base", "player.repos()",
             "draw_meta(p_base", 2),
            ("draw_player[original]", dp_b, "p_pflip",
             "sprite.set_prop(p_base", "player.repos()",
             "draw_frame(p_base", 2),
            ("apply[batched]", ap_a, "a_pflip[i]", "sprite.set_prop(base",
             "actor.repos(i)", "draw_meta(base", 3),
            ("apply[original]", ap_b, "a_pflip[i]", "sprite.set_prop(base",
             "actor.repos(i)", "draw_frame(base", 3)):
        ok &= check("%s() exists" % label, bool(fn))
        br = _flip_branch(fn, prev)
        ok &= check("%s() has an `f != %s` branch" % (label, prev), bool(br))
        ok &= check("%s(): the branch sets the property" % label,
                    prop_call in br)
        # Gated ON the change, not every frame: an unconditional extra move per
        # sprite per frame is real GB vblank budget (see actor.render's note).
        # BOTH draw paths gained a SECOND repos site 2026-08-26: the
        # draw-change branch forces the move that the same-position latch
        # would skip (a mask change must park/unpark its columns, and a
        # DESCRIPTOR frame's objects are positioned by the move and by
        # nothing else). Both sites are gated on a CHANGE, so the per-frame
        # budget note still holds.
        #
        # `apply` SPELLS the draw-change site twice from 2026-08-28 - hence 3
        # occurrences for 2 sites. Only a change of LAYOUT needs the re-lay
        # (base, clip kind and mask decide the fan's geometry; a clip step
        # that changes only the tile re-tiles objects already in place), and
        # the Lynx/PCE arm keeps the unconditional call VERBATIM under the
        # compile-time platform fork, because their present model re-asserts
        # every frame and a folded `if relay == 1` is not something cc65
        # reliably removes. tests/render_quiescent_test.py pins that shape;
        # the bound here is still what stops an UNGATED per-frame move.
        ok &= check("%s(): the re-place is inside that branch" % label,
                    repos_call in br and fn.count(repos_call) <= sites)
        # The metasprite UPLOAD comes FIRST: it is what (re)writes the
        # dimensions the move reads (canim puts the pool in external-animation
        # mode, so nothing else calls it). It goes through `draw_meta`, which
        # picks the plain or the blank-masked form at compile time.
        ok &= check("%s(): the metasprite upload runs BEFORE the flip branch" % label,
                    fn.index(meta_call) < fn.index(prop_call))
        # ...and the property write and the re-place must be ADJACENT. The OAM
        # DMA fires from the vblank interrupt, so it can snapshot the shadow
        # part-way through: measured on the reference conversion, moving set_meta out from
        # between them took the wrong-frame count from 1 in 472 to 0 (and the
        # pre-fix run showed a TORN snapshot, the two halves of one metasprite
        # carrying different flip bits).
        tail = br[br.index(prop_call):]
        gap = tail[:tail.index(repos_call)]
        ok &= check("%s(): nothing costly sits between them" % label,
                    "draw_frame" not in gap and "draw_meta" not in gap
                    and "sprite.set_meta" not in gap
                    and "sprite.move" not in gap)

    # --- vm.player: repos() replays the LAST screen position ----------------
    pp = _body(player, "put_player")
    ok &= check("put_player() caches the screen position it wrote",
                "lsx = ax" in pp and "lsy = ay" in pp)
    ok &= check("the cache is written BEFORE the move",
                pp.index("lsx = ax") < pp.index("sprite.move(pbase"))
    pr = _body(player, "repos")
    ok &= check("vm.player.repos() re-issues the cached move",
                "sprite.move(pbase, lsx, lsy)" in pr)
    ok &= check("vm.player exports repos",
                re.search(r"^\s*export .*\brepos\b", player, re.M) is not None)
    # BSS, not an initializer: an initialized global is resident image on
    # GB/SMS even when its module banks (the SMS conversion has ~10 B spare).
    ok &= check("lsx/lsy are BSS (no initializer)",
                re.search(r"^\s*var lsx: u16\s*$", player, re.M) is not None and
                re.search(r"^\s*var lsy: u16\s*$", player, re.M) is not None)

    # --- vm.player: the SAME-POSITION MOVE LATCH and every site that drops it
    # An idle player re-issued an identical `sprite.move` fan every frame, and
    # OAM shadow state PERSISTS - so the move bought nothing (measured on
    # the reference conversion: put_player 10.4k -> 7.3k cycles idle in room 5, 14.4k ->
    # 11.8k in the town room). The latch is only safe while every site that
    # changes what the hardware holds under it drops it, so those sites are
    # pinned here the way actor.render's are pinned by their own comments.
    LATCH = "pmok"
    ok &= check("put_player() latches on the pair it last MOVED to",
                "if pmok == 1 and pmx == ax and pmy == ay {" in pp)
    ok &= check("...and it is a SEPARATE shadow from lsx/lsy",
                # lsx/lsy record the LOGICAL position (the pblank park keeps
                # them while the hardware holds the park, and the emote reads
                # them), so they cannot double as the move shadow.
                "pmx = ax" in pp and "pmy = ay" in pp)
    ok &= check("the pblank PARK drops the latch (the fan holds the park now)",
                "sprite.move(pbase, 200, 200)" in pp
                and pp.index(LATCH) < pp.index("sprite.move(pbase, 200, 200)"))
    for site in ("set_base", "set_hidden"):
        fn = _body(player, site)
        ok &= check("vm.player.%s() drops the latch" % site,
                    "%s = 0" % LATCH in fn)
    ok &= check("repos() re-seeds the latch with what it just wrote",
                "pmx = lsx" in pr and "pmy = lsy" in pr)
    # Folded off the Lynx/PCE with the other R2 caches: their present model
    # re-pushes the sprite every frame, and the Lynx MAIN must not pay BSS
    # (or an extra repos) for a path that never runs. Verified at the ROM
    # level - all five Lynx and all five PCE sample builds are md5-identical.
    ok &= check("the latch is folded off the Lynx/PCE",
                re.search(r'if platform == "lynx" or platform == "pce" \{\s*\}'
                          r' else \{\s*var pmx: u16', player) is not None)
    ok &= check("draw_player()'s DRAW-change branch forces the move too",
                dp_a.count("player.repos()") == 2
                and dp_b.count("player.repos()") == 2)

    # --- vm.actor: repos(i) re-places one slot through the same path --------
    pl = _body(actor, "place")
    # The world->screen placement is SIGNED (sprite.move_world): `sprite.move`
    # takes a u8, so an actor whose top-left has scrolled past the left edge
    # would arrive already wrapped and the fan would draw its leading columns
    # at the far right of the screen. Measured on the GB conversion: the
    # 9-column big animated actor at screen x -11 drew seven columns at OAM 5..53 and a
    # ghost at 253. What is pinned here is that there is still exactly ONE
    # placement path, whatever it is spelled as.
    ok &= check("actor.place(i, base) is the shared world->screen placement",
                "sprite.move_world(base, sx, sy)" in pl
                and "var sx: i16 = wx - cx" in pl
                and "var sy: i16 = wy - cy" in pl)
    rd = _body(actor, "render")
    # `place` grew parameters (the off-window decision + the camera, both of
    # which every caller has already computed - 2026-08-15's off-window
    # animation gate), so match the CALL rather than an exact argument list.
    # What is pinned is that there is still exactly ONE placement path.
    ok &= check("render() places through it rather than inlining the move",
                "place(i, base," in rd and
                "sprite.move(base, wx - cx" not in rd)
    ar = _body(actor, "repos")
    ok &= check("actor.repos(i) skips an inactive slot",
                "if a_active[i] == 0 {" in ar)
    ok &= check("actor.repos(i) skips a slot with no OAM",
                "NO_OAM" in ar)
    ok &= check("actor.repos(i) places the slot", "place(i, base," in ar)
    ok &= check("vm.actor exports repos",
                re.search(r"^\s*export .*\brepos\b", actor, re.M) is not None)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
