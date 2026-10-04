#!/usr/bin/env python3
"""A font's RECODE table: the reference engine's `<font>.json` `mapping`, applied to the
string table when `scripts.mos` is emitted.

The reference engine lets a font say which glyph a typed character prints
(`{"mapping": {"ä": 228, "<3": [128]}}`, `shared/lib/helpers/fonts.ts`
`encodeString`, longest key wins). The studio's font editor authors the same
file beside `[assets] font`, so a Cyrillic or accented font, or a heart in a
spare cell, is reachable from ordinary dialogue text. What this pins:

  * parsing keeps only entries whose codes are glyphs the engine carries
    (32..255), a list value is several codes, a broken file is {};
  * recoding is longest-match, and leaves `$var$` tokens and newlines alone;
  * the blob (VWF) and the literal switch form (fixed width) both carry the
    recoded codes, and an EMPTY map is byte-identical;
  * `compile_path` (the CLI) derives it from the project, as it does VWF.
"""

import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik_vm as m  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        f.write(data if isinstance(data, str) else json.dumps(data))


def parsing(tmp):
    print("load_font_map: the reference engine's format, engine-carried codes only")
    png = os.path.join(tmp, "font.png")
    check("no sidecar = {}", m.load_font_map(png) == {})
    _write_json(os.path.join(tmp, "font.json"), {
        "name": "Test", "mapping": {
            "ä": 228, "<3": [128, 129], "nl": 10, "big": 300,
            "flag": True, "text": "x", "": 65, "€": 164}})
    fm = m.load_font_map(png)
    check("int and list values are kept as code lists",
          fm.get("ä") == [228] and fm.get("<3") == [128, 129], fm)
    check("a control code, a code past 255, a bool, a string, an empty key are dropped",
          set(fm) == {"ä", "<3", "€"}, sorted(fm))
    _write_json(os.path.join(tmp, "font.json"), "{not json")
    check("a broken file = {}", m.load_font_map(png) == {})
    with open(os.path.join(tmp, "font.json"), "w", encoding="utf-8-sig") as f:
        f.write('{"mapping": {"<3": 128}}')
    check("a file saved with a UTF-8 byte-order mark still reads",
          m.load_font_map(png) == {"<3": [128]})
    check("an entry that outputs $ (36) is dropped: it could forge a $var$ token",
          m.font_map_entries({"#": 36, "x": [65, 36], "y": 65}) == {"y": [65]})


def recoding():
    print("apply_font_map: longest match, tokens and newlines untouched")
    fm = {"<": [60], "<3": [128], "$": [36], "a": [200]}
    check("the longest key wins", m.apply_font_map("x<3<", fm) == "x\x80<")
    check("a $var$ token is not recoded (its name holds an 'a')",
          m.apply_font_map("a $gold$ a", fm) == "\xc8 $gold$ \xc8")
    check("newlines survive", m.apply_font_map("a\na", fm) == "\xc8\n\xc8")
    check("no mapping = the same string", m.apply_font_map("a<3", {}) == "a<3")


def _scripts(text):
    return [{"name": "main", "events": [
        {"event": "text", "string": text}, {"event": "stop"}]}]


def emission():
    print("emit_scripts_module: both renderers carry the recoded bytes")
    fm = {"♥": [128]}
    prog = m.Compiler().compile(_scripts("I ♥ you"))
    prog._vwf = True
    before = prog._string_blob()[0]
    prog.apply_font_map(fm)
    after = prog._string_blob()[0]
    check("unmapped, a heart past Latin-1 encodes as ?", b"I ? you" in before, before)
    check("mapped, it is glyph 128 in the blob", b"I \x80 you" in after, after)

    plain = m.emit_scripts_module(_scripts("I ♥ you"), vwf=True)
    check("an EMPTY map is byte-identical",
          m.emit_scripts_module(_scripts("I ♥ you"), vwf=True, font_map={}) == plain)
    check("a map that matches nothing is byte-identical",
          m.emit_scripts_module(_scripts("I ♥ you"), vwf=True,
                                font_map={"ß": [200]}) == plain)
    mapped = m.emit_scripts_module(_scripts("I ♥ you"), vwf=True, font_map=fm)
    check("a matching map changes the module", mapped != plain)

    # Fixed width, small text: the literal print_string switch form.
    lit = m.emit_scripts_module(_scripts("Käse"), vwf=False,
                                font_map={"ä": [123]})
    check("the switch form prints the recoded glyph ('ä' drawn in cell '{')",
          'print_string(core.box_left(), core.box_top(), "K{se")' in lit)


def cli_path(tmp):
    print("compile_path: the project's font sidecar is picked up")
    root = os.path.join(tmp, "proj")
    os.makedirs(os.path.join(root, "scripts"))
    os.makedirs(os.path.join(root, "assets"))
    with open(os.path.join(root, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "t"\n[assets]\nfont = "assets/font.png"\n')
    with open(os.path.join(root, "scripts", "main.evt.toml"), "w", encoding="utf-8") as f:
        f.write('[[script]]\nname = "main"\nevents = [ { event = "text", '
                'string = "hi <3" }, { event = "stop" } ]\n')
    check("no sidecar: the text is untouched",
          m.compile_path(os.path.join(root, "scripts")).strings == ["hi <3"])
    _write_json(os.path.join(root, "assets", "font.json"), {"mapping": {"<3": 128}})
    check("project_font_map reads [assets] font's sidecar",
          m.project_font_map(root) == {"<3": [128]})
    check("with it, compile_path recodes the string table",
          m.compile_path(os.path.join(root, "scripts")).strings == ["hi \x80"])
    with open(os.path.join(root, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "t"\n[assets]\nfont = 7\n')
    check("a [assets] font that is not a path is no mapping, never a raise",
          m.project_font_map(root) == {})


def main():
    print("Font recode mapping (the reference engine <font>.json)")
    print("=" * 60)
    tmp = tempfile.mkdtemp(prefix="fontmap_")
    try:
        parsing(tmp)
        recoding()
        emission()
        cli_path(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 60)
    if FAILS:
        print("FAILED: %d" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
