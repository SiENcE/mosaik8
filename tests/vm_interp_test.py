#!/usr/bin/env python3
"""VM8 $var$ dialogue interpolation (the reference engine's inline "$var$").

A text-box string may embed `$name$`, which render_text expands to the LIVE value
of heap variable `name` (printed inline via text.print_number, reading the heap
through the core.var_get seam). It works in BOTH render forms (the per-string
switch form and the compact STRINGS-blob form), registers a referenced-only var
as a heap cell, and is BYTE-IDENTICAL when no string carries a token (existing
samples untouched).

This checks:
  * a $var$ reference allocates a heap cell (even with no set_var);
  * a MENU OPTION interpolates too (the reference engine writes prices into its choices);
  * the switch form emits print_number(col, ..., core.var_get(idx)) + imports vm.core;
  * the blob form encodes a 0x01<idx> token + the token-aware scanner;
  * NO token -> no vm.core import + the plain print_string line (byte-identical);
  * CompiledProgram.interpolate mirrors it for headless assertions;
  * both forms COMPILE to C on gameboy AND lynx (with a core.var_get stub).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik_vm import Compiler, CompiledProgram
from mosaik import MosaikCompiler

# a tiny stand-in for vm.core so the generated scripts.mos compiles standalone
# render_text asks core where the open box's text goes (the box is sized from
# the string's line count before the frame is drawn), so the stub answers those
# too - the real ones live in lib/vm/core.mos.
CORE_STUB = ('module "vm.core" {\n'
             '    function var_get(idx: u8) -> i16 { return 0 }\n'
             '    function box_top() -> u8 { return SCREEN_ROWS - 3 }\n'
             '    function box_left() -> u8 { return 2 }\n'
             '    function set_code_window(base: addr, enter: function() -> u8, leave: function(u8)) { }\n'
             '    function set_code_window_res(base: addr) { }\n'
             '    export var_get, box_top, box_left, set_code_window, set_code_window_res\n}\n')


def compile_scripts(scripts):
    return Compiler().compile(scripts)


def check(label, cond):
    print(("[PASS] " if cond else "[FAIL] ") + label)
    return cond


INTERP = [{"name": "main", "events": [
    {"event": "set_var", "var": "keys", "value": 1},
    {"event": "text", "string": "GOT $keys$ OF $total$"},   # total is ref-only
    {"event": "stop"},
]}]

MENU = [{"name": "main", "events": [
    {"event": "set_var", "var": "gold", "value": 2},
    {"event": "menu", "var": "pick", "row": 8,
     "options": ["Rest ($gold$ Gold)", "Cancel"]},
    {"event": "stop"},
]}]

PLAIN = [{"name": "main", "events": [
    {"event": "text", "string": "GOT THE KEY"},
    {"event": "stop"},
]}]


def main():
    ok = True

    prog = compile_scripts(INTERP)
    src = prog.to_scripts_mos()

    ok &= check("a $var$ reference allocates a heap cell (referenced-only)",
                "keys" in prog.variables and "total" in prog.variables)
    ok &= check("switch form: imports vm.core + print_number via var_get",
                'import "vm.core"' in src
                and ("core.var_get(%d)" % prog.variables["keys"]) in src
                and "text.print_number(" in src)

    # interpolate() mirror
    ok &= check("interpolate() expands tokens from a heap",
                prog.interpolate("GOT $keys$ OF $total$", {prog.variables["keys"]: 4,
                                 prog.variables["total"]: 9}) == "GOT 4 OF 9")

    # A MENU OPTION is a UI string like any other: its $var$ token registers a
    # heap cell and render_choice prints the LIVE value. It used to intern
    # through the plain intern_string and render through a raw byte copy, so
    # the 0x01 token + heap index reached the screen as two junk glyphs -
    # the RPG check conversion's inn read "Rest (  Gold)" against the reference's
    # "Rest (2 Gold)". Both render forms.
    mprog = compile_scripts(MENU)
    msrc = mprog.to_scripts_mos()
    ok &= check("menu option: a $var$ token allocates a heap cell",
                "gold" in mprog.variables)
    ok &= check("switch form render_choice: prints the live value",
                "text.print_number(c, row, v)" in msrc
                and ("core.var_get(%d)" % mprog.variables["gold"]) in msrc)
    saved = CompiledProgram.STRING_BLOB_THRESHOLD
    try:
        CompiledProgram.STRING_BLOB_THRESHOLD = 0
        mbsrc = mprog.to_scripts_mos()
    finally:
        CompiledProgram.STRING_BLOB_THRESHOLD = saved
    ok &= check("blob form render_choice: token-aware scanner, not a raw copy",
                "text.print_number(col, row, vv)" in mbsrc
                and "if vv > 9999 { col += 1 }" in mbsrc)

    # PLAIN text: no $var$ token, so no var_get read and no INTERP machinery.
    # It still imports vm.core and asks it for the text origin - EVERY string
    # does now, because core sizes the box from the string's line count before
    # drawing the frame, and a baked row would disagree with a resized box.
    plain = compile_scripts(PLAIN)
    psrc = plain.to_scripts_mos()
    ok &= check("no token: no var_get read, text origin from core",
                "core.var_get" not in psrc
                and 'text.print_string(core.box_left(), core.box_top(), "GOT THE KEY")' in psrc
                and not plain._has_interp())

    # ...and a menu with NO token keeps the plain literal / raw-copy forms.
    nprog = compile_scripts([{"name": "main", "events": [
        {"event": "menu", "var": "pick", "row": 8, "options": ["LEFT", "RIGHT"]},
        {"event": "stop"}]}])
    nsrc = nprog.to_scripts_mos()
    ok &= check("token-free menu: literal option, no var_get",
                'text.print_string(3, row, "LEFT")' in nsrc
                and "core.var_get" not in nsrc)

    # ...and a token that lives only in a TEXT BOX must not build the choice
    # renderer's scanner. `_has_interp` is true of the whole string table, so
    # gating on it made every dialogue game pay ~263 B of resident image for a
    # menu renderer that could never use it (measured on the reference-engine sample conversion, whose
    # only options are "Save Game" / "Cancel"). The rule is per FEATURE.
    tprog = compile_scripts([{"name": "main", "events": [
        {"event": "set_var", "var": "keys", "value": 1},
        {"event": "text", "string": "GOT $keys$"},
        {"event": "menu", "var": "pick", "row": 8, "options": ["LEFT", "RIGHT"]},
        {"event": "stop"}]}])
    tsrc = tprog.to_scripts_mos()
    choice = tsrc.split("function render_choice")[1]
    ok &= check("a token only a TEXT BOX carries builds no choice scanner",
                tprog._has_interp() and not tprog._has_choice_interp()
                and "var_get" not in choice and "print_number" not in choice)
    saved2 = CompiledProgram.STRING_BLOB_THRESHOLD
    try:
        CompiledProgram.STRING_BLOB_THRESHOLD = 0
        tb = tprog.to_scripts_mos()
    finally:
        CompiledProgram.STRING_BLOB_THRESHOLD = saved2
    ok &= check("...the blob form too",
                "print_number(col, row" not in tb)

    # blob form: force the threshold low so INTERP takes the blob path
    saved = CompiledProgram.STRING_BLOB_THRESHOLD
    try:
        CompiledProgram.STRING_BLOB_THRESHOLD = 0
        bsrc = prog.to_scripts_mos()
        blob, offs = prog._string_blob()
    finally:
        CompiledProgram.STRING_BLOB_THRESHOLD = saved
    ok &= check("blob form: token-aware scanner (col tracker + var_get(vi))",
                "const STRINGS:" in bsrc and "var col: u8" in bsrc
                and "core.var_get(vi)" in bsrc)
    # The cursor advances by the printed value's ACTUAL digit count, never a
    # fixed reserve -- a fixed 5 left a 4-col gap after a 1-digit value and
    # clipped the line's tail at the 20-col window ("there is 3    lef" where
    # the reference engine prints "there is 3 left"). Both render forms.
    ok &= check("blob form: cursor advances by the value's digit count",
                "if vv > 9 { col += 1 }" in bsrc
                and "if vv > 9999 { col += 1 }" in bsrc
                and "col += 5" not in bsrc)
    ok &= check("switch form: cursor advances by the value's digit count",
                "if v > 9 { c += 1 }" in src and "c += 5" not in src)
    ok &= check("blob encodes a 0x01<idx> token marker",
                1 in blob and (prog.variables["keys"] in blob))

    # both forms compile to C on gameboy AND lynx (with a core.var_get stub)
    for label, threshold in (("switch", saved), ("blob", 0)):
        CompiledProgram.STRING_BLOB_THRESHOLD = threshold
        try:
            s = prog.to_scripts_mos()
        finally:
            CompiledProgram.STRING_BLOB_THRESHOLD = saved
        for plat in ("gameboy", "lynx"):
            c = MosaikCompiler().compile_program(
                [("scripts.mos", s), ("core.mos", CORE_STUB)], platform=plat)
            ok &= check("[%s/%s] interp scripts.mos compiles to C" % (label, plat),
                        not c.startswith("Compilation error") and "render_text" in c)

    print("\n" + ("All checks passed" if ok else "SOME CHECKS FAILED"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
