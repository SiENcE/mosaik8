#!/usr/bin/env python3
"""A room load must colour EVERY object of a coloured actor's fan.

`load_room` gives each placed actor its OAM range (the static layout: `ob`,
then `ob += fan`) and, for a world with `[kind_palettes]`, the kind's sprite
palette. `sprite.set_palette` fans over the record LATCHED at the base, and at
that point it is not yet the new kind's, so a paint of the base alone coloured
ONE object on the GBDK consoles: projects/vm-shardlings' Mom (16x16, two 8x16
objects) drew her right half in the player's palette, and the 40x32 back view
of the battle (ten objects) all but one object in the foe's (measured on the
OAM attribute bytes, 2026-10-19: pal 1 / 0 / 0 ...). The load now resets each
object of the fan to a 1x1 record and colours it, the rule `paint_slot`
(sprite slots on wake) and `oam_free` already follow. The cc65 consoles colour
the whole fan in their prelude, so they keep the one call; a world without
kind palettes gets neither (byte-identical).

The checks generate rooms.mos for a copy of projects/vm-shardlings.
"""
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(cond, msg):
    print("  [%s] %s" % ("ok" if cond else "FAIL", msg))
    if not cond:
        FAILS.append(msg)


def _rooms(coloured=True):
    import mosaik_vm
    src = os.path.join(ROOT, "projects", "vm-shardlings")
    with tempfile.TemporaryDirectory() as tmp:
        dst = os.path.join(tmp, "vm-shardlings")
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("build", "__pycache__"))
        if not coloured:
            wp = os.path.join(dst, "world.toml")
            text = open(wp, encoding="utf-8").read()
            text = re.sub(r"\n\[kind_palettes\]\n(?:[^\[\n][^\n]*\n)*", "\n", text)
            open(wp, "w", encoding="utf-8").write(text)
        mosaik_vm.generate_rooms(dst)
        return open(os.path.join(dst, "src", "rooms.mos"), encoding="utf-8").read()


def _placement(text):
    """The static layout's `ob + fan` arm of load_room's object loop."""
    m = re.search(r"\n(\s*)if ob \+ fan > \w+ \{\n(.*?)\n\1\}\n", text, re.S)
    return m.group(2) if m else None


def test_every_object_is_coloured():
    arm = _placement(_rooms())
    check(arm is not None, "rooms.mos has the static layout's placement arm")
    if arm is None:
        return
    gbdk = re.search(r"\} else \{\s*var pp: u8 = scenes\.kind_pal_at\(fk\)\s*"
                     r"for pe in 0\.\.fan \{\s*sprite\.set_meta\(ob \+ pe, 0, 1, 1\)\s*"
                     r"sprite\.set_palette\(ob \+ pe, pp\)", arm)
    check(gbdk is not None,
          "GBDK consoles: each object ob + pe of the fan is reset to 1x1, then coloured")
    cc65 = re.search(r'if platform == "lynx" or platform == "pce" \{\s*'
                     r"sprite\.set_palette\(ob, scenes\.kind_pal_at\(fk\)\)\s*\} else", arm)
    check(cc65 is not None, "cc65 consoles: one set_palette on the base (their prelude fans it)")
    base, loop = arm.find("actor.set_base(slot, ob)"), arm.find("for pe in 0..fan")
    check(0 <= base < loop, "the fan is coloured after the base is set")


def test_uncoloured_world_is_untouched():
    text = _rooms(coloured=False)
    check("kind_pal_at" not in text and "for pe in 0..fan" not in text,
          "a world without [kind_palettes] colours nothing at room load")


def main():
    print("room_load_paint_test")
    test_every_object_is_coloured()
    test_uncoloured_world_is_untouched()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
