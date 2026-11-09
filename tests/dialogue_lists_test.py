#!/usr/bin/env python3
"""E12: a string LIST and the `$list[var]$` text token.

`scripts/dialogue.toml [lists.species] items = [...]` defines a list; a text
box saying `A WILD $species[f_sp]$ APPEARED!` prints item heap[f_sp] in
place. The compiler interns a NAMED list's items as one consecutive run of
strings and encodes the token as 0x02 <first> <heap index>; the blob
renderers jump their read position into the item and back (shape B,
docs: the studio's `docs/shardlings-gen1-scale-plan.md` E12).

This checks:
  * the list reader and its refusals (name, duplicates, empty, too many,
    a line break or a token in an item, a [lists] that is not a table);
  * interning: the text first, then the list's items consecutively, and the
    index variable registered; an unused list interns nothing;
  * the token's bytes in the blob, and `$var$` beside it unchanged;
  * BYTE-IDENTICAL off: a defined-but-unused list, an undefined list name
    (stays literal text), and no lists at all emit the same module;
  * a list forces the blob form, and both blob renderers carry the arm
    (render_text with and without a `$var$`, render_text_step) - the step
    renderer resets its return point on entry;
  * a token in a MENU OPTION and under a variable-width font are refused;
  * `CompiledProgram.interpolate` mirrors it (out of range prints nothing);
  * the font recode leaves a list token whole;
  * every form compiles to C on gameboy, sms, lynx and pce.

    python tests/dialogue_lists_test.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik_vm import (Compiler, VmError, apply_font_map, emit_scripts_module,  # noqa: E402
                       load_dialogue_lists, validate_lists)
from mosaik import MosaikCompiler  # noqa: E402

CORE_STUB = ('module "vm.core" {\n'
             '    function var_get(idx: u8) -> i16 { return 0 }\n'
             '    function box_top() -> u8 { return SCREEN_ROWS - 3 }\n'
             '    function box_left() -> u8 { return 2 }\n'
             '    function set_code_window(base: addr, enter: function() -> u8, leave: function(u8)) { }\n'
             '    function set_code_window_res(base: addr) { }\n'
             '    export var_get, box_top, box_left, set_code_window, set_code_window_res\n}\n')

SPECIES = [{"name": "species", "items": ["SPROUTLE", "EMBERKIT", "BRINEFIN"]}]

WILD = [{"name": "main", "events": [
    {"event": "set_var", "var": "f_sp", "value": 1},
    {"event": "text", "string": "A WILD $species[f_sp]$\nAPPEARED!"},
    {"event": "stop"}]}]

WILD_VAR = [{"name": "main", "events": [
    {"event": "set_var", "var": "f_sp", "value": 1},
    {"event": "text", "string": "LV $lv$ $species[f_sp]$!"},
    {"event": "stop"}]}]

TYPED = [{"name": "main", "events": [
    {"event": "text_speed", "speed": 2},
    {"event": "text", "string": "A WILD $species[f_sp]$!"},
    {"event": "stop"}]}]

PLAIN = [{"name": "main", "events": [
    {"event": "text", "string": "HELLO THERE"},
    {"event": "stop"}]}]

FAILS = []


def check(label, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + label + ("" if cond else "  " + str(detail)))
    if not cond:
        FAILS.append(label)
    return cond


def refused(fn, needle):
    try:
        fn()
    except VmError as e:
        return needle in str(e)
    return False


def test_reader():
    print("[the list reader]")
    check("a good list validates", validate_lists(SPECIES) == SPECIES)
    check("a bad name is refused",
          refused(lambda: validate_lists([{"name": "my list", "items": ["A"]}]), "identifier"))
    check("a duplicate name is refused",
          refused(lambda: validate_lists(SPECIES + SPECIES), "twice"))
    check("an empty list is refused",
          refused(lambda: validate_lists([{"name": "x", "items": []}]), "non-empty"))
    check("256 items are refused",
          refused(lambda: validate_lists([{"name": "x", "items": ["A"] * 256}]), "at most 255"))
    check("an item with a line break is refused",
          refused(lambda: validate_lists([{"name": "x", "items": ["A\nB"]}]), "line break"))
    check("an item holding a $var$ is refused",
          refused(lambda: validate_lists([{"name": "x", "items": ["$hp$"]}]), "token"))
    check("an item holding a list token is refused",
          refused(lambda: validate_lists([{"name": "x", "items": ["$x[i]$"]}]), "token"))
    tmp = tempfile.mkdtemp()
    try:
        check("no dialogue.toml -> no lists", load_dialogue_lists(tmp) == [])
        with open(os.path.join(tmp, "dialogue.toml"), "w", encoding="utf-8") as f:
            f.write('[defs.hi]\ntext = "HI"\n\n[lists.species]\n'
                    'items = ["SPROUTLE", "EMBERKIT", "BRINEFIN"]\n\n'
                    '[lists.moves]\nitems = ["TACKLE"]\n')
        got = load_dialogue_lists(tmp)
        check("[lists] is read in file order beside [defs]",
              [d["name"] for d in got] == ["species", "moves"]
              and got[0]["items"] == SPECIES[0]["items"], got)
        with open(os.path.join(tmp, "dialogue.toml"), "w", encoding="utf-8") as f:
            f.write('lists = ["oops"]\n')
        check("a [lists] that is not a table is refused",
              refused(lambda: load_dialogue_lists(tmp), "table of named lists"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_interning_and_encoding():
    print("[interning and the token bytes]")
    prog = Compiler(lists=SPECIES).compile(WILD)
    check("the text first, then the items as one run",
          prog.strings == ["A WILD $species[f_sp]$\nAPPEARED!", "SPROUTLE", "EMBERKIT",
                           "BRINEFIN"], prog.strings)
    check("the list is recorded as (first id, count)", prog.lists == {"species": (1, 3)},
          prog.lists)
    check("the index variable is a heap cell", "f_sp" in prog.variables)
    blob, offs = prog._string_blob()
    tok = bytes((2, 1, prog.variables["f_sp"]))
    check("the token is 0x02 <first> <heap index>",
          blob.startswith(b"A WILD " + tok + b"\nAPPEARED!\x00"), blob[:24])
    check("each item is its own string", blob[offs[2]:offs[3]] == b"EMBERKIT\x00")
    both = Compiler(lists=SPECIES).compile(WILD_VAR)
    bblob, _o = both._string_blob()
    check("a $var$ beside it keeps its 0x01 <index>",
          bblob.startswith(b"LV " + bytes((1, both.variables["lv"])) + b" "
                           + bytes((2, 1, both.variables["f_sp"])) + b"!\x00"), bblob[:16])
    unused = Compiler(lists=SPECIES).compile(PLAIN)
    check("a list no text box names interns nothing",
          unused.strings == ["HELLO THERE"] and unused.lists == {})


def test_byte_identical_off():
    print("[byte-identical off]")
    base = emit_scripts_module(PLAIN)
    check("a defined-but-unused list emits the same module",
          emit_scripts_module(PLAIN, lists=SPECIES) == base)
    lit = [{"name": "main", "events": [
        {"event": "text", "string": "PRICE $tbl[k]$"}, {"event": "stop"}]}]
    check("an UNDEFINED list name stays literal text (and allocates nothing)",
          emit_scripts_module(lit, lists=SPECIES) == emit_scripts_module(lit)
          and "k" not in Compiler(lists=SPECIES).compile(lit).variables)
    check("no `_ret` and no ch == 2 arm without a list",
          "_ret" not in base and "ch == 2" not in base)


def test_renderers():
    print("[the renderers]")
    src = emit_scripts_module(WILD, lists=SPECIES)
    check("a list forces the blob form (a short text would be the switch form)",
          "const STRINGS:" in src and "switch id" not in src.split("function render_text(")[1]
          .split("function ")[0])
    rt = src.split("function render_text(")[1].split("\n    function ")[0]
    check("render_text (no $var$) opens with the list arm",
          "if ch == 2 {" in rt and "off = STR_OFF[lb + lk] - 1" in rt
          and "if lb + lk < 4 {" in rt and "off = _ret + 1" in rt and "_ret = 0" in rt, rt)
    rtv = emit_scripts_module(WILD_VAR, lists=SPECIES).split("function render_text(")[1] \
        .split("\n    function ")[0]
    check("render_text with a $var$ chains the list arm after it",
          "if ch == 1 {" in rtv and "} else if ch == 2 {" in rtv, rtv)
    step = emit_scripts_module(TYPED, lists=SPECIES).split("function render_text_step(")[1] \
        .split("\n    function ")[0]
    head = step.split("while ch != 0")[0]
    check("render_text_step carries the arm and resets its return point on entry",
          "} else if ch == 2 {" in step and "_ret = 0" in head and "off = _ret + 1" in step,
          step)


def test_refusals():
    print("[refusals]")
    menu = [{"name": "main", "events": [
        {"event": "menu", "var": "pick", "row": 8,
         "options": ["$species[f_sp]$", "RUN"]}, {"event": "stop"}]}]
    check("a list token in a MENU OPTION is refused",
          refused(lambda: Compiler(lists=SPECIES).compile(menu), "TEXT BOX only"))
    check("a list token under a variable-width font is refused",
          refused(lambda: emit_scripts_module(WILD, vwf=True, lists=SPECIES),
                  "variable-width"))


def test_mirror_and_font_map():
    print("[the Python mirror and the font recode]")
    prog = Compiler(lists=SPECIES).compile(WILD)
    v = prog.variables["f_sp"]
    check("interpolate() prints the item", prog.interpolate(prog.strings[0], {v: 2})
          == "A WILD BRINEFIN\nAPPEARED!")
    check("...and nothing for a value past the string TABLE",
          prog.interpolate(prog.strings[0], {v: 7}) == "A WILD \nAPPEARED!")
    check("...a negative value reads as its byte (past the table)",
          prog.interpolate(prog.strings[0], {v: -1}) == "A WILD \nAPPEARED!")
    check("the font recode leaves a list token whole",
          apply_font_map("A $species[f_sp]$ e", {"e": [101], "s": [200], "[": [201]})
          == "A $species[f_sp]$ " + chr(101))


def test_compiles_to_c():
    print("[every form compiles to C]")
    forms = (("render_text", WILD), ("render_text + $var$", WILD_VAR),
             ("render_text_step", TYPED))
    for label, scripts in forms:
        src = emit_scripts_module(scripts, lists=SPECIES)
        for plat in ("gameboy", "sms", "lynx", "pce"):
            c = MosaikCompiler().compile_program(
                [("scripts.mos", src), ("core.mos", CORE_STUB)], platform=plat)
            check("[%s/%s] compiles" % (label, plat),
                  not c.startswith("Compilation error") and "render_text" in c, c[:300])


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: The intro box of vm-dialogue, said by its room's On Init and held open
#: (nothing presses A). Each TOKEN arm must put on screen exactly what its
#: LITERAL twin does; the two literal arms must differ (else the screen read
#: could not see text at all).
ARMS = {
    "tok": ("WILD $species[a]$ AND\n$species[b]$ HAS $gold$ G", True),
    "tok_typed": ("WILD $species[a]$ AND\n$species[b]$ HAS $gold$ G", True),
    "lit": ("WILD EMBERKIT AND\nBRINEFIN HAS 10 G", False),
    "lit_typed": ("WILD EMBERKIT AND\nBRINEFIN HAS 10 G", False),
    "tok_out": ("A WILD $species[c]$!\nAPPEARED!", True),
    "lit_out": ("A WILD !\nAPPEARED!", False),
}
LIST_TOML = '\n[lists.species]\nitems = ["SPROUTLE", "EMBERKIT", "BRINEFIN"]\n'


def _arm_project(tmp, arm):
    import toml
    text, lists = ARMS[arm]
    root = os.path.join(tmp, arm, "vm-dialogue")
    shutil.copytree(os.path.join(ROOT, "projects", "vm-dialogue"), root,
                    ignore=shutil.ignore_patterns("build"))
    sd = os.path.join(root, "scripts")
    dp = os.path.join(sd, "dialogue.toml")
    with open(dp, encoding="utf-8") as f:
        body = f.read()
    old = 'text = "A NEW DAY DAWNS\\nOVER THE VILLAGE."'
    assert body.count(old) == 1
    body = body.replace(old, "text = %s" % toml.dumps({"t": text}).split("=", 1)[1].strip())
    with open(dp, "w", encoding="utf-8") as f:
        f.write(body + (LIST_TOML if lists else ""))
    mp = os.path.join(sd, "main.evt.toml")
    doc = toml.load(mp)
    main = next(s for s in doc["script"] if s["name"] == "main")
    # A "_typed" arm types its boxes (text speed 1): the step renderer, whose
    # window ends INSIDE an item on most calls.
    typed = [{"event": "text_speed", "speed": 1}] if arm.endswith("_typed") else []
    main["events"] = typed + [{"event": "set_var", "var": v, "value": n}
                              for v, n in (("a", 1), ("b", 2), ("c", 250))] + main["events"]
    with open(mp, "w", encoding="utf-8") as f:
        f.write(toml.dumps(doc))
    # A build does not regenerate scripts.mos: write it the way the CLI and
    # the studio's Save do (the path that reads scripts/dialogue.toml [lists]).
    from mosaik_vm import scripts_module_path
    with open(os.path.join(root, "src", "scripts.mos"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(scripts_module_path(sd))
    return root


def _build(root, plat):
    import subprocess
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", plat, root],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, r.stdout + r.stderr


def _box_rows(img):
    """The bottom 48 rows, where the text box sits: the rest of the screen
    differs between two builds for reasons that are not the text."""
    w, h = img.size
    return img.convert("RGB").crop((0, h - 48, w, h)).tobytes()


def _gb_screen(rom):
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", sound_emulated=False)
    pb.tick(700, True)
    shot = _box_rows(pb.screen.image)
    pb.stop(save=False)
    return shot


def _sms_screen(rom, core_file):
    """A libretro core is a process singleton: run it in a child."""
    import subprocess
    code = ("import sys; sys.path.insert(0, %r); from retro import Core\n"
            "c = Core(%r, %r); c.run(700); im = c.image()\n"
            "w, h = im.size; im = im.convert('RGB').crop((0, h - 48, w, h))\n"
            "sys.stdout.write(im.tobytes().hex())\n"
            % (os.path.join(ROOT, "emu", "libretro"),
               os.path.join(ROOT, "emu", "libretro", core_file), rom))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    return bytes.fromhex(r.stdout.strip() or "") or r.stderr[-300:]


def test_roms():
    print("[ROMs: the token's box is the literal box, pixel for pixel]")
    import mosaik8_build
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    cores = [c for c in os.listdir(os.path.join(ROOT, "emu", "libretro"))
             if c.startswith("genesis_plus_gx_libretro")]
    tmp = tempfile.mkdtemp(prefix="dlg_lists_")
    try:
        roots = {arm: _arm_project(tmp, arm) for arm in ARMS}
        for plat, ext in (("gameboy", "gb"), ("sms", "sms")):
            shots = {}
            for arm, root in roots.items():
                ok, log = _build(root, plat)
                if not check("[%s] %s builds" % (plat, arm), ok, log[-600:]):
                    continue
                rom = os.path.join(root, "build", plat, "vm-dialogue." + ext)
                if plat == "gameboy":
                    shots[arm] = _gb_screen(rom)
                elif cores:
                    shots[arm] = _sms_screen(rom, cores[0])
            if len(shots) < len(ARMS):
                continue
            check("[%s] the screen reads text (the two literal boxes differ)" % plat,
                  isinstance(shots["lit"], bytes) and shots["lit"] != shots["lit_out"])
            check("[%s] two items and a $var$ print as the literal text" % plat,
                  shots["tok"] == shots["lit"])
            check("[%s] a value past the string table prints nothing" % plat,
                  shots["tok_out"] == shots["lit_out"])
            check("[%s] TYPED, the items reveal into the same box" % plat,
                  shots["tok_typed"] == shots["lit_typed"])
        # the cc65 consoles: both arms (box and typewriter) LINK
        if mosaik8_build.cc65_available():
            for plat in ("lynx", "pce"):
                ok, log = _build(roots["tok_typed"], plat)
                check("[%s] the token arms link (cc65)" % plat, ok, log[-600:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_reader()
    test_interning_and_encoding()
    test_byte_identical_off()
    test_renderers()
    test_refusals()
    test_mirror_and_font_map()
    test_compiles_to_c()
    test_roms()
    print("\n" + ("All checks passed" if not FAILS else "%d FAILED" % len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
