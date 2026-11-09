#!/usr/bin/env python3
"""E3: strings past 256 and text past one ROM bank.

A program with more than 256 strings is PAGED: the compiler puts every menu
option and every list item in page 0 and emits `STR_PAGE hi` before a text box
whose string id is past 255 (UI_TEXT keeps its u8 operand); vm.core keeps the
open box's page and the box renderers read it through `core.text_page()`.
The per-string INDEX (STR_OFF + STR_LINES, 3 B a string of resident data) is
read through `assets.code_byte` where the build banks such blobs (the GBDK
consoles under `[build] code_banks`, the build's `VM_STR_FAR`), so the module
carries both forms in a fold; past one ROM bank of text the far arm CHUNKS
STRINGS (whole strings) behind one `sbyte` reader.

This checks:
  * a program under STR_FAR_MIN emits no fold (the module it always did);
  * the fold's NEAR arm is the old renderer text, line for line;
  * paging: the box renderers read the page, render_choice does not;
  * chunking: every chunk fits a bank and holds whole strings, and STR_IX +
    the chunks give back every string's bytes; str_ck's thresholds;
  * every form compiles to C on gameboy / sms (both arms), lynx and pce;
  * ROMs (GB PyBoy, SMS genesis_plus_gx): a box on page 2 in the third chunk,
    with two list items jumping back into chunk 0 and a `$var$`, prints what
    its literal twin prints, typed and untyped, banked (far arm) and not
    (paged near arm).

    python tests/strings_past_256_test.py
"""
import os
import re
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik_vm import CompiledProgram, Compiler, emit_scripts_module  # noqa: E402
from mosaik import MosaikCompiler  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CORE_STUB = ('module "vm.core" {\n'
             '    function var_get(idx: u8) -> i16 { return 0 }\n'
             '    function text_page() -> u8 { return 0 }\n'
             '    function box_top() -> u8 { return SCREEN_ROWS - 3 }\n'
             '    function box_left() -> u8 { return 2 }\n'
             '    function set_code_window(base: addr, enter: function() -> u8, leave: function(u8)) { }\n'
             '    function set_code_window_res(base: addr) { }\n'
             '    export var_get, text_page, box_top, box_left, set_code_window, set_code_window_res\n}\n')

SPECIES = [{"name": "species", "items": ["SPROUTLE", "EMBERKIT", "BRINEFIN"]}]
FILLER = "F%04d THE QUICK BROWN\nFOX JUMPS OVER THE\nLAZY DOG AGAIN. %04d"

FAILS = []


def check(label, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + label + ("" if cond else "  " + str(detail)))
    if not cond:
        FAILS.append(label)
    return cond


def _prog(n, typed=False, menu=True, lists=None):
    ev = [{"event": "text_speed", "speed": 1}] if typed else []
    ev.append({"event": "if", "cond": "0", "then": [
        {"event": "text", "string": FILLER % (k, k)} for k in range(n)]})
    ev.append({"event": "text", "string": "WILD $species[a]$ AND\n$species[b]$ HAS $gold$ G"
               if lists else "THE LAST LINE"})
    if menu:
        ev.append({"event": "menu", "var": "pick", "options": ["YES", "NO"]})
    ev.append({"event": "stop"})
    return [{"name": "main", "events": ev}]


def _module(scripts, lists=None, far_min=None):
    old = CompiledProgram.STR_FAR_MIN
    if far_min is not None:
        CompiledProgram.STR_FAR_MIN = far_min
    try:
        return emit_scripts_module(scripts, lists=lists)
    finally:
        CompiledProgram.STR_FAR_MIN = old


def _fn(src, name):
    """The body of `function name(` up to its closing brace at the same indent."""
    m = re.search(r"^( *)(?:local )?function %s\(.*?^\1\}" % name, src, re.S | re.M)
    return m.group(0) if m else ""


def _arms(src):
    """(far arm, near arm) of the VM_STR_FAR fold, each dedented one level."""
    a = src.index("    if VM_STR_FAR {\n")
    b = src.index("\n    } else {\n", a)
    c = src.index("\n    }\n", b + 10)
    while src[c + 6:c + 7] not in ("", "\n") or "    if VM_STR_FAR" in src[b:c]:
        c = src.index("\n    }\n", c + 1)
    ded = lambda t: "\n".join(l[4:] if l.startswith("    ") else l for l in t.split("\n"))
    return ded(src[a + len("    if VM_STR_FAR {\n"):b]), ded(src[b + len("\n    } else {\n"):c])


def test_threshold():
    print("[no fold under STR_FAR_MIN]")
    few = [{"name": "main", "events": [{"event": "text", "string": "T%d" % k} for k in range(8)]
            + [{"event": "text_speed", "speed": 1}, {"event": "stop"}]}]
    src = emit_scripts_module(few)
    check("8 strings: no VM_STR_FAR fold, the resident index as before",
          "VM_STR_FAR" not in src and "STR_OFF[id]" in src and "str_off" not in src)
    src = emit_scripts_module(_prog(23))
    check("26 strings: still no fold (the far form would cost more)",
          "VM_STR_FAR" not in src)
    src = emit_scripts_module(_prog(24))
    check("27 strings: the fold", "if VM_STR_FAR {" in src)


def test_near_arm_is_the_old_form():
    print("[the fold's near arm is the old renderer text]")
    for label, scripts, lists in (("plain", _prog(30), None),
                                  ("lists", _prog(30, lists=True), SPECIES),
                                  ("typed lists", _prog(30, typed=True, lists=True), SPECIES)):
        folded = _module(scripts, lists)
        plain = _module(scripts, lists, far_min=10 ** 9)
        far, near = _arms(folded)
        check("[%s] the near arm is the unfolded module's text section" % label,
              near.strip() in plain and "if VM_STR_FAR" not in plain,
              near[:200])
        check("[%s] the far arm reads the index through str_off" % label,
              "STR_OFF" not in far and "str_off(" in far
              and "assets.code_byte(STR_LINES" in far)


def test_paging():
    print("[paged: box renderers read the page, render_choice does not]")
    src = _module(_prog(300, typed=True, lists=True), SPECIES)
    far, near = _arms(src)
    for arm, text in (("far", far), ("near", near)):
        for fn in ("render_text", "render_text_step", "text_lines"):
            check("[%s] %s reads core.text_page()" % (arm, fn),
                  "core.text_page()" in _fn(text, fn), _fn(text, fn)[:200])
        check("[%s] render_choice does not" % arm,
              "text_page" not in _fn(text, "render_choice") and _fn(text, "render_choice"))
    unpaged = _module(_prog(200, typed=True, lists=True), SPECIES)
    check("at most 256 strings: no renderer reads the page", "text_page" not in unpaged)


def test_chunks():
    print("[chunks: whole strings, one bank each, the index gives every string back]")
    prog = Compiler(lists=SPECIES).compile(_prog(700, lists=True))
    firsts = prog._str_chunks()
    check("~40 KB of text is 3 chunks: %s" % firsts, len(firsts) == 3)
    blob, offs = prog._string_blob()
    enc = [prog._encode_blob_string(s) + b"\x00" for s in prog.strings]
    src = _module(_prog(700, lists=True), SPECIES)
    far, near = _arms(src)

    def arr(name, text):
        m = re.search(r"const %s: array\[u8, (\d+)\] = \[(.*?)\]" % name, text, re.S)
        return [int(v) for v in re.findall(r"\d+", m.group(2))] if m else None

    chunks = [arr("STRINGS" if k == 0 else "STRINGS%d" % (k + 1), far)
              for k in range(len(firsts))]
    check("every chunk is emitted and fits a bank: %s" % [len(c or []) for c in chunks],
          all(c and len(c) <= CompiledProgram.STR_CHUNK for c in chunks))
    ix = arr("STR_IX", far)
    lines = arr("STR_LINES", far)
    back = []
    for sid in range(len(prog.strings)):
        k = max(j for j in range(len(firsts)) if firsts[j] <= sid)
        o = ix[2 * sid] | ix[2 * sid + 1] << 8
        # by length: a token's heap-cell operand may itself be a 0 byte
        back.append(bytes(chunks[k][o:o + len(enc[sid])]))
    check("STR_IX + the chunks give back every string's bytes", back == enc)
    check("STR_LINES has a line count per string", lines and len(lines) == len(prog.strings))
    ck = _fn(far, "str_ck")
    check("str_ck tests each chunk's first id",
          all(("sid >= %d" % f) in ck for f in firsts[1:]), ck)
    check("render_text reads through sbyte in the chunked arm",
          "sbyte(off)" in _fn(far, "render_text")
          and "code_byte(STRINGS, off)" not in _fn(far, "render_text"))
    check("the near arm keeps ONE STRINGS and STR_OFF",
          "STRINGS2" not in near and "STR_OFF[" in near)
    check("the list jump saves and restores the chunk",
          "_rck = _sck" in far and "_sck = _rck" in far)


def test_compiles_to_c():
    print("[every form compiles to C]")
    forms = (("paged+chunked", _prog(700, lists=True), SPECIES),
             ("paged+chunked typed", _prog(700, typed=True, lists=True), SPECIES),
             ("far one chunk", _prog(40), None))
    for label, scripts, lists in forms:
        src = emit_scripts_module(scripts, lists=lists)
        for plat, far in (("gameboy", True), ("gameboy", False), ("sms", True),
                          ("lynx", False), ("pce", False)):
            if label.startswith("paged+chunked") and not far and plat == "gameboy":
                continue          # 40 KB of text cannot be one resident array
            c = MosaikCompiler().compile_program(
                [("scripts.mos", src), ("core.mos", CORE_STUB)], platform=plat,
                defines={"VM_STR_FAR": far, "VM_CODE_BANKED": False})
            check("[%s/%s far=%s] compiles" % (label, plat, far),
                  not c.startswith("Compilation error") and "render_text" in c, c[:300])


# ---- ROMs ------------------------------------------------------------------

#: The intro box of vm-dialogue, held open (nothing presses A). Each TOKEN arm
#: must put on screen exactly what the LITERAL arm does; `lit` and `lit_out`
#: must differ (else the screen read could not see text at all).
TOKEN = "WILD $species[a]$ AND\n$species[b]$ HAS $gold$ G"
LITERAL = "WILD EMBERKIT AND\nBRINEFIN HAS 10 G"
ARMS = {
    # name: (intro text, lists, fillers, code_banks, typed)
    "far": (TOKEN, True, 700, True, False),
    "far_typed": (TOKEN, True, 700, True, True),
    "near": (TOKEN, True, 300, False, False),
    "lit": (LITERAL, False, 0, False, False),
    "lit_typed": (LITERAL, False, 0, False, True),
    "lit_out": ("A WILD !\nAPPEARED!", False, 0, False, False),
}
LIST_TOML = '\n[lists.species]\nitems = ["SPROUTLE", "EMBERKIT", "BRINEFIN"]\n'


def _arm_project(tmp, arm):
    import toml
    text, lists, fillers, banks, typed = ARMS[arm]
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
    pre = [{"event": "text_speed", "speed": 1}] if typed else []
    if fillers:
        # never run, but interned FIRST: the intro lands on page 2, in the
        # far arm's third chunk, while the list items sit in page 0 / chunk 0
        # (short ones where nothing is banked: the text is resident there)
        fill = FILLER if banks else "F%04d"
        pre.append({"event": "if", "cond": "0", "then": [
            {"event": "text", "string": fill % ((k, k) if banks else k)}
            for k in range(fillers)]})
    main["events"] = pre + [{"event": "set_var", "var": v, "value": n}
                            for v, n in (("a", 1), ("b", 2))] + main["events"]
    with open(mp, "w", encoding="utf-8") as f:
        f.write(toml.dumps(doc))
    if banks:
        mt = os.path.join(root, "mosaik.toml")
        conf = toml.load(mt)
        conf.setdefault("build", {})["code_banks"] = ["vm.core", "vm.actor", "vm.entity", "scenes"]
        conf["build"]["bank_bytecode"] = True      # 700 texts are ~3 KB of bytecode
        with open(mt, "w", encoding="utf-8") as f:
            f.write(toml.dumps(conf))
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


def _scripts_of(root):
    with open(os.path.join(root, "src", "scripts.mos"), encoding="utf-8") as f:
        return f.read()


def test_roms():
    print("[ROMs: a box on page 2 in chunk 3 prints its literal twin]")
    import mosaik8_build
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    cores = [c for c in os.listdir(os.path.join(ROOT, "emu", "libretro"))
             if c.startswith("genesis_plus_gx_libretro")]
    tmp = tempfile.mkdtemp(prefix="str256_")
    try:
        roots = {arm: _arm_project(tmp, arm) for arm in ARMS}
        if "far" in roots:
            far_src = _scripts_of(roots["far"])
            check("the far fixture is paged and chunked (3 chunks)",
                  "STRINGS3" in far_src and "core.text_page()" in far_src)
        if "near" in roots:
            check("the near fixture is paged",
                  "core.text_page()" in _scripts_of(roots["near"]))
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
            for arm, twin, label in (
                    ("far", "lit", "banked: page 2, chunk 3, two items in chunk 0 and a $var$"),
                    ("far_typed", "lit_typed", "banked and TYPED, the same box"),
                    ("near", "lit", "not banked: the paged resident index, the same box")):
                if arm in shots and twin in shots:
                    check("[%s] %s" % (plat, label), shots[arm] == shots[twin])
        if mosaik8_build.cc65_available() and "near" in roots:
            ok, log = _build(roots["near"], "lynx")
            check("[lynx] the paged near arm links (cc65)", ok, log[-600:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_threshold()
    test_near_arm_is_the_old_form()
    test_paging()
    test_chunks()
    test_compiles_to_c()
    test_roms()
    print("\n" + ("All checks passed" if not FAILS else "%d FAILED" % len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
