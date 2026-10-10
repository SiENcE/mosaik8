#!/usr/bin/env python3
"""Sprite slots on wake must colour EVERY object of a woken actor's range,
and give a freed range back uncoloured.

`[build] oam_on_wake` hands an actor its OAM range when it comes on screen;
`vm.actor`'s `oam_alloc` resets the new base to a 1x1 record and then calls
the generated `rooms.paint_slot`, which gives the range the kind's sprite
palette. `sprite.set_palette` fans over the base's RECORDED metasprite, which
is 1x1 at that moment, so a paint of the base alone coloured ONE object: a
16x16 foe (two 8x16 objects) drew its left half in the kind palette and its
right half in whatever the last owner of that object wore (measured on the
studio's raid-vm8, 2026-10-10: a red mine with a cyan right half, OAM
attributes pal 1 / 0). `paint_slot` now colours each object of the fan.

The same world's FREED ranges: a shot never sets a palette, so an entry a
coloured foe gave back drew the next shot in the foe's colours (raid-vm8's
player shots on palette 1 or 0 by turns). The build states `VM_OAM_WAKE_PAL`
off the generated `actor.set_paint(` registration, and vm.actor's `oam_free`
then gives each entry back on palette 0 - the colour a static layout's shot
block has. A world without kind palettes gets neither (byte-identical).

The checks generate rooms.mos for a copy of projects/vm-tallshmup (sprite
slots on wake, a 1x1 ship beside a 2x2 beacon) with a kind palette added.
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


def _rooms(obj16, coloured=True):
    """The generated rooms.mos for vm-tallshmup (+ a kind palette)."""
    import mosaik_vm
    src = os.path.join(ROOT, "projects", "vm-tallshmup")
    with tempfile.TemporaryDirectory() as tmp:
        dst = os.path.join(tmp, "vm-tallshmup")
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("build", "__pycache__"))
        if coloured:
            with open(os.path.join(dst, "world.toml"), "a", encoding="utf-8") as f:
                f.write("\n[kind_palettes]\nbeacon = 1\n")
        if obj16:
            mt = os.path.join(dst, "mosaik.toml")
            text = open(mt, encoding="utf-8").read()
            text = text.replace("[build]\n", "[build]\nobj_8x16 = true\n", 1)
            open(mt, "w", encoding="utf-8").write(text)
        mosaik_vm.generate_rooms(dst)
        return open(os.path.join(dst, "src", "rooms.mos"), encoding="utf-8").read()


def _paint_slot(obj16):
    """The generated `paint_slot` body, sliced out of the function."""
    m = re.search(r"\n    function paint_slot\(b: u8, i: u8\) \{\n(.*?)\n    \}\n",
                  _rooms(obj16), re.S)
    return m.group(1) if m else None


def test_every_object_is_coloured():
    for obj16 in (False, True):
        tag = "8x16" if obj16 else "8x8"
        body = _paint_slot(obj16)
        check(body is not None, "%s: rooms.mos has paint_slot" % tag)
        if body is None:
            continue
        check(re.search(r"for e in 0\.\.n \{\s*sprite\.set_palette\(b \+ e, pp\)", body)
              is not None, "%s: paint_slot colours each object b + e of the fan" % tag)
        check(re.search(r"sprite\.set_palette\(b,", body) is None,
              "%s: paint_slot no longer paints the base alone" % tag)
        check("clips.meta_w(okind[i])" in body and "clips.meta_h(okind[i])" in body,
              "%s: the count is the slot kind's metasprite size" % tag)
        check(("n = n / 2" in body) == obj16,
              "%s: 8x16 objects halve the count%s" % (tag, "" if obj16 else " (not here)"))


def test_freed_range_goes_back_on_palette_0():
    import mosaik8_build
    coloured = _rooms(False)
    plain = _rooms(False, coloured=False)
    check(mosaik8_build._wants_oam_paint([("rooms.mos", coloured)]),
          "a coloured world's rooms.mos states VM_OAM_WAKE_PAL")
    check(not mosaik8_build._wants_oam_paint([("rooms.mos", plain)]),
          "a world without kind palettes does not (byte-identical)")
    check(not mosaik8_build._wants_oam_paint(
              [("x.mos", "-- actor.set_paint(paint_slot)\n")]),
          "a commented-out registration states nothing")
    src = open(os.path.join(ROOT, "lib", "vm", "actor.mos"), encoding="utf-8").read()
    m = re.search(r"\n        local function oam_free\(i: u8\) \{\n(.*?)\n        \}\n",
                  src, re.S)
    body = m.group(1) if m else ""
    check(re.search(r"if VM_OAM_WAKE_PAL \{\s*sprite\.set_palette\(b \+ e, 0\)", body)
          is not None, "vm.actor's oam_free gives each freed entry back on palette 0")
    from mosaik import compiler
    csrc = open(compiler.__file__, encoding="utf-8").read()
    check("all_defines.setdefault('VM_OAM_WAKE_PAL', False)" in csrc,
          "VM_OAM_WAKE_PAL has a compiler default (the guard folds when unstated)")


def main():
    print("oam_on_wake: paint_slot colours the whole fan; a freed range is uncoloured")
    test_every_object_is_coloured()
    test_freed_range_goes_back_on_palette_0()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        sys.exit(1)
    print("All checks passed.")


if __name__ == "__main__":
    main()
