#!/usr/bin/env python3
"""Every degraded lowering the build NAMES is one the backend really has.

Review E-7. An unsupported verb is already a compile-time error (PLATFORM_CAPS
prunes the stdlib map), so what is left is the honest-but-degraded set: a verb
that lowers to a `(void)` no-op, a constant that is literally 0, an edge that
is really a level. Those compile, link and run, and the only way an author
found out was by watching the ROM do the wrong thing.

`mosaik.platforms.degraded_uses` is the table and the generator prints from it
per build, for the verbs the program actually uses. The risk a table like this
carries is that it drifts from the emitters and starts either lying or going
quiet, so this checks it against the GENERATED C for every console rather than
against itself:

  * `bkg.set_attrs` is claimed degraded exactly where its body is a `(void)`
    cast chain;
  * `FLIP_X`/`FLIP_Y` are claimed dead exactly where `has_sprite_flip` is
    false - and that cap is what `gbs_move_sprite` reads for its flip arms;
  * `INPUT_START`/`INPUT_SELECT` are claimed dead exactly where the prelude
    defines them as 0 (the generator reads that back off the port table, so
    the note cannot restate it wrongly);
  * `input.pressed` is claimed degraded on every console, and every console
    really does map it to the same helper as `input.held`.
"""
import io
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
from mosaik.platforms import PLATFORM_CAPS
from mosaik.codegen.gbdk import GbdkBackend
from mosaik.codegen.cc65 import Cc65Backend

ok = True
CONSOLES = ("gameboy", "gameboy_color", "sms", "gamegear", "nes", "lynx",
            "pce", "megaduck", "analogue_pocket")


def check(cond, label, detail=""):
    global ok
    ok = ok and bool(cond)
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- " + detail) if detail else ""))


PROG = '''
module "main" {
    import "platform.video"
    import "platform.input"
    import "graphics.bkg"
    import "graphics.sprite"
    var A: array[u8, 4]
    function main() {
        video.enable_lcd()
        bkg.set_attrs(0, 0, 2, 2, A)
        sprite.set_prop(0, FLIP_X)
        if input.pressed(INPUT_A) { }
        if input.held(INPUT_START) { }
    }
}
'''


def gen(platform, **kw):
    """Compile, capturing the notes the generator prints."""
    buf = io.StringIO()
    real, sys.stdout = sys.stdout, buf
    try:
        c = MosaikCompiler().compile_program([("m.mos", PROG)],
                                             platform=platform, **kw)
    finally:
        sys.stdout = real
    assert not c.startswith("Compilation error"), c
    return c, buf.getvalue()


def test_set_attrs_matches_the_body():
    print("\n[bkg.set_attrs: claimed no-op == emitted no-op]")
    for p in CONSOLES:
        c, notes = gen(p)
        m = re.search(r"void gbs_bkg_attrs\([^)]*\)[^{]*\{(.{0,120})", c, re.S)
        body = m.group(1) if m else ""
        # No helper at all means it lowered onto the port's own inline
        # (the CGB class uses GBDK's set_bkg_attributes): that is REAL.
        is_noop = bool(m) and "(void)x;" in body
        claimed = "bkg.set_attrs is an honest no-op" in notes
        check(is_noop == claimed,
              "%-16s no-op=%s claimed=%s" % (p, is_noop, claimed))


def test_flip_matches_the_cap():
    print("\n[FLIP_X/FLIP_Y: claimed dead == has_sprite_flip false]")
    for p in CONSOLES:
        _c, notes = gen(p)
        claimed = "FLIP_X is ignored" in notes
        check(claimed == (not PLATFORM_CAPS[p]["has_sprite_flip"]),
              "%-16s flip=%s claimed=%s"
              % (p, PLATFORM_CAPS[p]["has_sprite_flip"], claimed))


def test_dead_constants_match_the_defines():
    print("\n[INPUT_START: claimed dead == defined as 0]")
    for p in CONSOLES:
        c, notes = gen(p)
        m = re.search(r"#define INPUT_START\s+(\S+)", c)
        dead = bool(m) and m.group(1).strip() == "0"
        claimed = "INPUT_START is 0 on this console" in notes
        check(dead == claimed, "%-16s define=%s claimed=%s"
              % (p, m.group(1) if m else "?", claimed))


def test_pressed_is_held_everywhere():
    print("\n[input.pressed: claimed on every console, and true on every one]")
    for p in CONSOLES:
        _c, notes = gen(p)
        check("input.pressed is input.held" in notes, "%-16s named" % p)
        stdlib = (Cc65Backend.STDLIB_CALLS_CC65_CORE
                  if PLATFORM_CAPS[p]["framework"] == "cc65"
                  else GbdkBackend.STDLIB_CALLS_GBDK)
        check(stdlib[("input", "pressed")] == stdlib[("input", "held")],
              "%-16s ... and both verbs really are one helper" % p)


def test_a_program_that_uses_none_of_it_says_nothing():
    print("\n[silence when nothing is degraded]")
    quiet = '''
module "main" {
    import "platform.video"
    function main() { video.enable_lcd() }
}
'''
    buf = io.StringIO()
    real, sys.stdout = sys.stdout, buf
    try:
        MosaikCompiler().compile_program([("m.mos", quiet)], platform="sms")
    finally:
        sys.stdout = real
    check("Note:" not in buf.getvalue(),
          "a program using none of the degraded verbs gets no notes",
          buf.getvalue().strip()[:80])


def test_the_sms_start_choice_is_named():
    print("\n[the per-project one: sms_start_button]")
    _c, off = gen("sms")
    _c, on = gen("sms", sms_start_button=True)
    check("sms_start_button" not in off, "silent with the flag off")
    check("fires twice" in on, "named with the flag on")


def main():
    print("Degraded lowerings are named, and only the real ones")
    print("=" * 50)
    test_set_attrs_matches_the_body()
    test_flip_matches_the_cap()
    test_dead_constants_match_the_defines()
    test_pressed_is_held_everywhere()
    test_a_program_that_uses_none_of_it_says_nothing()
    test_the_sms_start_choice_is_named()
    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
