#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""THE BOX IS NEVER SEEN HALF-BUILT (B2, 2026-09-19).

A box on the GB window layer is ASSEMBLED - `text.to_window` blanks the band,
`g_box_draw` draws the 9-slice frame, `g_text` writes the glyphs - and all of it
runs inside ONE VM frame, which is more than one LCD frame. `to_window` used to
switch the layer on at the START of that, so the half-built states were
DISPLAYED. Measured on the reference-engine sample conversion (GBC) with
a local probe, band pixels still differing from the
settled box per display frame:

    reveal BEFORE the draw:   989 -> 227 -> 116 -> 0
    reveal AFTER  the draw:     0

So `to_window` prepares and `text.win_reveal()` shows, and every caller reveals
after it has drawn.

WHY THIS IS A SOURCE CONTRACT AND NOT A PROJECT CHECK. The half-built states
are only visible where the draw outlasts an LCD frame, which in practice means
a FRAMED box (the 9-slice ring is drawn cell by cell) with a page of glyphs -
a reference-engine conversion. `vm-uiscroll`, which gates everything else about the
box, has no frame art: MEASURED, its box reads 0 differing pixels on the first
window frame whether the reveal comes before or after the draw, and so does a
three-line version of it. A check there would have passed on a broken engine,
so it was deleted rather than shipped green, and the ROM evidence lives in the
probe. What is pinned here is the ORDER, which is what broke.
"""

from mosaik import MosaikCompiler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "lib", "vm", "core.mos")

SRC = """
module "main" {
    import "graphics.text"
    function main() {
        text.to_window(14, 4)
        text.print_string(1, 15, "HI")
        text.win_reveal()
    }
    export main
}
"""


def check(label, cond, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", label,
                           ("  -- " + detail) if detail else ""))
    return cond


def _body(c, name):
    """The emitted body of `void <name>(...)`, to its closing brace."""
    at = c.index("void %s(" % name)
    at = c.index("{", at)
    depth, i = 0, at
    while i < len(c):
        if c[i] == "{":
            depth += 1
        elif c[i] == "}":
            depth -= 1
            if depth == 0:
                return c[at:i + 1]
        i += 1
    raise AssertionError(name)


def main():
    print("the box is never seen half-built (win_reveal)")
    print("=" * 50)
    ok = True

    # -- the GB family: to_window PREPARES, win_reveal SHOWS -----------------
    gb = MosaikCompiler().compile_program([("m.mos", SRC)], platform="gameboy")
    ok &= check("gameboy: to_window does NOT switch the layer on",
                "SHOW_WIN" not in _body(gb, "gbs_text_to_window"))
    ok &= check("...and win_reveal does", "SHOW_WIN" in _body(gb, "gbs_text_win_reveal"))
    ok &= check("...and to_window still prepares the band (blank + move_win)",
                "move_win(" in _body(gb, "gbs_text_to_window")
                and "set_win_tiles(" in _body(gb, "gbs_text_to_window"))

    # -- everywhere else it is an honest no-op -------------------------------
    for plat in ("sms", "gamegear", "nes", "lynx", "pce"):
        c = MosaikCompiler().compile_program([("m.mos", SRC)], platform=plat)
        if "Compilation error" in c:
            ok &= check("%s: compiles" % plat, False, c.splitlines()[0])
            continue
        body = _body(c, "gbs_text_win_reveal")
        ok &= check("%s: win_reveal is an honest no-op (no window layer)" % plat,
                    "SHOW_WIN" not in body and len(body.strip()) <= 4,
                    body.strip())

    # -- the ORDER, which is the thing that regressed ------------------------
    # Both UI sites in vm.core must reveal AFTER the last thing that writes
    # into the band: the box after `g_text(id)`, the menu after the option loop
    # (its frame and its options are drawn in two different places).
    core = open(CORE, encoding="utf-8").read()
    ok &= check("vm.core reveals in BOTH UI sites (box and menu)",
                core.count("text.win_reveal()") == 2,
                "%d call(s)" % core.count("text.win_reveal()"))
    box_at = core.index("text.to_window(box_row(), box_h)")
    menu_at = core.index("text.to_window(row - 1, count + 2)")
    rev = [i for i in range(len(core))
           if core.startswith("text.win_reveal()", i)]
    box_rev = min(r for r in rev if r > box_at)
    text_at = core.index("g_text(id)", box_at)
    ok &= check("the BOX reveals after its text is drawn, not at to_window",
                box_rev > text_at,
                "reveal at %d, g_text at %d" % (box_rev, text_at))
    menu_rev = min(r for r in rev if r > menu_at)
    loop_at = core.index("g_menu(id, row + i, sel)", menu_at)
    ok &= check("the MENU reveals after its option loop, not at to_window",
                menu_rev > loop_at,
                "reveal at %d, g_menu at %d" % (menu_rev, loop_at))

    # -- the overlay HUD is the other in-repo caller, and it had the same bug
    hud = open(os.path.join(ROOT, "mosaik_vm", "hud.py"), encoding="utf-8").read()
    i = hud.index('"        text.to_window(%s, %d)" % (origin, rows)')
    ok &= check("the generated HUD reveals after ITS draw too",
                hud.index('"        text.win_reveal()"', i)
                > hud.index('"        draw%d()" % pi', i))

    print("=" * 50)
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
