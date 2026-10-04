#!/usr/bin/env python3
"""An engine.anim slot can outlive the actor that armed it - it must never draw.

The reference-engine sample conversion's room-to-room hard crash (2026-08-16): an
actor's animator is armed by `vm.canim.tick_all`'s walk,
which covers VISIBLE slots only - so a slot that was armed and then stayed
off-window until the next room load arrives at `sweep_retired` with
`a_pstate == 255` (the sweep's own sentinel, set for every slot, re-set only
by the walk). The sweep's anim.clear used to be guarded on `a_pstate != 255`,
which skipped exactly that slot; if the NEW room left it EMPTY (clip 255),
engine.anim kept firing `apply(i)` against clip 255 forever. `apply` had no
clip-255 guard (only tick_all's walk did), so the clip-size selectors read
`META_W[255]` / `META_H[255]` PAST the clips const tables and handed
`sprite.set_meta` garbage - measured w=248 h=255 - whose u8-TRUNCATED product
(248 * 127 & 0xFF = 8) slipped the prelude bounds guard and fanned ~31k OAM
objects across WRAM: RST 38, SP running away, LCD off.

Chain on the sample: menu (6 armed checkbox slots) -> a block-puzzle room
(pushable blocks off-window from the stairs spawn, slots 1..4 never walked) ->
the room below it (1 actor, slots 1+ empty) -> crash on the load frame.
Any room with FEWER actors than the armed set reproduces it; another room
(6 actors) masked it, which is why it survived every playthrough.

Three layers, each pinned here:
1. `apply()` returns on clip 255 - the orphaned callback draws nothing.
2. `sweep_retired` clears a clip-255 slot's animator UNCONDITIONALLY.
3. The GBDK prelude's metasprite bounds guards compare the w*h product WIDE
   (uint16_t), so garbage arguments from any future bug return instead of
   overrunning the meta tables + shadow OAM.
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

from mosaik import MosaikCompiler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANIM = os.path.join(ROOT, "lib", "vm", "canim.mos")

META = '''
module "m" {
    import "platform.video"
    import "graphics.sprite"
    const T: array[u8, 64] = [
        255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
        255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
        255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,
        255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255]
    function main() {
        video.enable_lcd()
        sprite.set_data(0, 4, T)
        sprite.set_meta(0, 0, 2, 2)
        sprite.move(0, 40, 40)
        video.show_sprites()
        loop { video.wait_vblank() }
    }
    export main
}
'''

MASKED = META.replace("sprite.set_meta(0, 0, 2, 2)",
                      "sprite.set_meta_mask(0, 0, 2, 2, 1)")


def check(label, cond):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    return bool(cond)


def _body(src, name):
    m = re.search(r"^(\s*)(?:hot )?(?:local )?function %s\(" % re.escape(name),
                  src, re.M)
    if not m:
        return ""
    indent = m.group(1)
    end = re.compile(r"^%s\}" % indent, re.M).search(src, m.end())
    return src[m.start():end.end() if end else len(src)]


def compile_for(src, platform, obj16=False):
    return MosaikCompiler().compile_program([("m.mos", src.strip())],
                                            platform=platform,
                                            obj_8x16=obj16)


def main():
    ok = True
    src = open(CANIM, encoding="utf-8").read()

    print("layer 1: apply() guards the no-clip sentinel")
    ap = _body(src, "apply")
    m = re.search(r"if k == 255 \{\s*(?:--[^\n]*\n\s*)*return", ap)
    ok &= check("apply() returns on clip 255", m is not None)
    if m:
        ok &= check("...BEFORE the draw_frame upload",
                    m.start() < ap.index("draw_frame(base,"))
        ok &= check("...BEFORE the clip-selector reads (g_frame)",
                    m.start() < ap.index("g_frame(k,"))

    print("layer 2: sweep_retired clears EVERY slot's animator, unguarded")
    sw = _body(src, "sweep_retired")
    sw_code = re.sub(r"--[^\n]*", "", sw)     # the old guards live on in prose
    ok &= check("the sweep calls anim.clear(i)", "anim.clear(i)" in sw_code)
    # TWO guards have been tried here and BOTH leaked, in opposite directions:
    #   * `a_pstate[i] != 255` reads as "only if it was ever armed" and is
    #     false for exactly the slot that matters (a sweep parks pstate at 255
    #     for every slot);
    #   * `actor.clip_of(i) == 255` clears only where the NEW room leaves the
    #     slot un-clipped - but a slot the new room DOES clip keeps the old
    #     room's animator until tick_all re-arms it, and tick_all walks the
    #     VISIBLE subset only. An actor off-window where the new room starts is
    #     never walked, so the PREVIOUS room's animator fires apply(i) for
    #     ever. Measured on the reference conversion: town room -> long walk-in room
    #     (the normal route in play) left two slots armed and cost 11,062
    #     cycles a frame, taking the room from 1.07 to 2.00 LCD/frame.
    # So the clear is UNCONDITIONAL, and the re-arm is what a_pstate = 255
    # guarantees for every visible slot on the same pass.
    for guard in ("a_pstate[i] != 255", "actor.clip_of(i) == 255"):
        ok &= check("no `%s` guard survives in the sweep" % guard,
                    guard not in sw_code)
    body_lines = [l.strip() for l in sw_code.splitlines() if l.strip()]
    idx = [n for n, l in enumerate(body_lines) if l == "anim.clear(i)"]
    ok &= check("...and the call sits in the walk, not inside an `if`",
                bool(idx) and not body_lines[idx[0] - 1].startswith("if "))

    print("layer 3: the GBDK prelude product guards are WIDE")
    for name, srcprog, obj16 in (("set_meta 8x8", META, False),
                                 ("set_meta 8x16", META, True),
                                 ("set_meta_mask 8x8", MASKED, False),
                                 ("set_meta_mask 8x16", MASKED, True)):
        c = compile_for(srcprog, "gameboy", obj16=obj16)
        fn = ("gbs_set_metasprite_mask" if "mask" in name
              else "gbs_set_metasprite")
        i = c.index("void %s(" % fn)
        body = c[i:c.index("\n}", i)]
        wide = re.search(r"\(uint16_t\)\(w \* ", body)
        narrow = re.search(r"\(uint8_t\)\(w \* [^)]*\)\s*>", body)
        ok &= check("%s: guard compares (uint16_t)(w * ...)" % name,
                    wide is not None and narrow is None)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
