#!/usr/bin/env python3
"""E4: the script blob across more than one ROM bank.

A blob past 16,384 bytes cannot be one banked array, so it is read in CHUNKS
of 16,384 bytes of PC, one ROM bank each. The interpreter's fetch stays
`code_base + pc` (code_base is the mapped chunk's address minus its first
PC) and the chunk is tested BETWEEN instructions, so the compiler lets no
instruction span a cut: one that would is moved to the cut behind
`JUMP <cut>` and STOP filler, and the names that pointed at it follow. The
generated scripts module carries both forms in a `VM_CODE_MULTI` fold (the
chunks, and the one array every console that does not bank the blob reads);
the build states the define where `[build] bank_bytecode` banks the blob.

This checks:
  * a blob that fits one chunk is laid out and emitted exactly as before;
  * the layout rule: no instruction spans a cut, the filler is `JUMP <cut>`
    plus STOPs, a waiting label and a script entry move to the cut, the debug
    map skips the filler, and the reference VM computes the same heap;
  * the module: the fold, the chunk arrays (each one bank, together the
    blob), the pack order code_map names them in, three chunks; a blob with
    an instruction ACROSS a cut (hand-assembled bytecode) gets no fold;
  * the build's rule (`code_multi`) and the checks that refuse a chunk that
    is not banked or not first in its bank;
  * every form compiles to C (gameboy, sms, pce chunked; lynx, gameboy one
    array);
  * ROMs (GB PyBoy, SMS genesis_plus_gx): a text box whose three values are
    computed by a CALL / RET across the cut, a thread started across it and a
    loop that spans it prints what its literal twin prints, and a menu whose
    option ids are read out of the second chunk draws what its twin draws; a
    GBDK build that does not bank such a blob is refused with the reason; the
    Lynx links the one-array arm.

    python tests/blob_chunks_test.py
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

from mosaik_vm import Compiler, RefVM, emit_scripts_module  # noqa: E402
from mosaik_vm.compiler import code_cuts_clean, code_multi, code_must_bank  # noqa: E402
from mosaik_vm.isa import OPS, iter_instructions  # noqa: E402
from mosaik import MosaikCompiler  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CUT = Compiler.CODE_CHUNK

CORE_STUB = ('module "vm.core" {\n'
             '    function var_get(idx: u8) -> i16 { return 0 }\n'
             '    function text_page() -> u8 { return 0 }\n'
             '    function box_top() -> u8 { return SCREEN_ROWS - 3 }\n'
             '    function box_left() -> u8 { return 2 }\n'
             '    function set_code_window(base: addr, enter: function() -> u8, leave: function(u8)) { }\n'
             '    function set_code_window_res(base: addr) { }\n'
             '    function set_code_multi(base: addr, map: function(u8) -> u8, leave: function(u8)) { }\n'
             '    export var_get, text_page, box_top, box_left, set_code_window, set_code_window_res, set_code_multi\n}\n')

FAILS = []


def check(label, cond, detail=""):
    print(("[PASS] " if cond else "[FAIL] ") + label + ("" if cond else "  " + str(detail)))
    if not cond:
        FAILS.append(label)
    return cond


# ---- programs ----------------------------------------------------------------

def _fill(k, name="e4_fill"):
    """A script nothing runs: k set_var events, 4 bytes each, and its STOP."""
    return {"name": name, "events": [{"event": "set_var", "var": "z", "value": j % 30000}
                                     for j in range(k)]}


#: What `e4_run` computes ACROSS the cut: `b` in a loop whose body holds the
#: cut (its back jump crosses, twice), then, past the cut, `a` in a sub that
#: sits before it (CALL and RET each cross) and `gold` in a thread started
#: across it.
CROSS = [
    {"event": "set_var", "var": "a", "value": 0},
    {"event": "set_var", "var": "b", "value": 0},
    {"event": "set_var", "var": "i", "value": 0},
    {"event": "set_var", "var": "gold", "value": 0},
    {"event": "label", "name": "e4_top"},
    {"event": "set_var", "var": "b", "expr": "b + 1"},
] + [{"event": "set_var", "var": "z", "value": j} for j in range(8)] + [
    {"event": "set_var", "var": "i", "expr": "i + 1"},
    {"event": "if", "cond": "i < 2", "then": [{"event": "goto", "name": "e4_top"}]},
    {"event": "call", "script": "e4_sub"},
    {"event": "start_thread", "script": "e4_thr"},
    {"event": "wait", "frames": 2},         # the thread runs before what follows
]
SUB = {"name": "e4_sub", "sub": True,
       "events": [{"event": "set_var", "var": "a", "value": 1}]}
THR = {"name": "e4_thr", "events": [{"event": "set_var", "var": "gold", "value": 10}]}
RUN = {"name": "e4_run", "events": CROSS + [{"event": "stop"}]}


def _cross_scripts(k, tail=()):
    return [SUB, THR, _fill(k), RUN] + list(tail)


def _compile(scripts, chunk=None):
    """Compile with the default chunk, or with one no blob reaches (the layout
    before E4)."""
    old = Compiler.CODE_CHUNK
    if chunk is not None:
        Compiler.CODE_CHUNK = chunk
    try:
        return Compiler().compile(scripts)
    finally:
        Compiler.CODE_CHUNK = old


def _loop(prog, script="e4_run"):
    """(top, end) of the CROSS loop in `script`: the instruction after its
    four opening SET_CONSTs, and the CALL behind it (a label is an anonymous
    anchor, so read the debug map)."""
    d = [e for e in prog.debug if e["script"] == script]
    return d[4]["off"], next(e["off"] for e in d if e["op"] == "CALL")


def _tuned(tail=()):
    """`_cross_scripts` with the filler sized so the cut falls inside
    e4_run's loop body: (scripts, k)."""
    flat = _compile(_cross_scripts(0, tail), chunk=1 << 30)
    k = (CUT - sum(_loop(flat)) // 2) // 4
    return _cross_scripts(k, tail), k


def _pad(code, cut=CUT):
    """(offset of the filler's JUMP, its target, the filler's other mnemonics),
    or None when the instruction stream simply ends on the cut."""
    ins = [(o, n, s, r) for o, n, s, r in iter_instructions(code) if o < cut]
    j = len(ins) - 1
    while j >= 0 and ins[j][1] == "STOP":
        j -= 1
    o, n, s, _rpn = ins[j]
    if n != "JUMP":
        return None
    return o, code[o + 1] | code[o + 2] << 8, [x[1] for x in ins[j + 1:]]


# ---- the layout ---------------------------------------------------------------

def test_one_chunk_is_unchanged():
    print("[a blob that fits one chunk]")
    scripts = _cross_scripts(40)
    a, b = _compile(scripts), _compile(scripts, chunk=1 << 30)
    check("the same bytes as with no cut at all", a.code == b.code and len(a.code) < CUT)
    check("the same offsets and debug map", a.offsets == b.offsets and a.debug == b.debug)
    src = emit_scripts_module(scripts)
    check("the module has no fold", "VM_CODE_MULTI" not in src and "CODE2" not in src
          and "set_code_multi" not in src)
    old = Compiler.CODE_CHUNK
    Compiler.CODE_CHUNK = 1 << 30
    try:
        check("...and is the module of the layout before E4, character for character",
              src == emit_scripts_module(scripts))
    finally:
        Compiler.CODE_CHUNK = old


def test_layout_rule():
    print("[no instruction spans a cut]")
    scripts, k = _tuned()
    prog, flat = _compile(scripts), _compile(scripts, chunk=1 << 30)
    check("the fixture's cut is inside e4_run's loop (before the rule)",
          _loop(flat)[0] < CUT < _loop(flat)[1] and len(flat.code) > CUT,
          (_loop(flat), len(flat.code)))
    check("without the rule an instruction spans the cut (the check can fail)",
          not code_cuts_clean(flat.code))
    check("with it none does", code_cuts_clean(prog.code))
    pad = _pad(prog.code)
    check("the filler is JUMP <cut> and STOPs up to the cut",
          pad is not None and pad[1] == CUT and CUT - pad[0] >= 3
          and len(pad[2]) == CUT - pad[0] - 3, pad)
    check("the blob grew by the filler alone",
          pad is not None and len(prog.code) - len(flat.code) == CUT - pad[0],
          (len(prog.code), len(flat.code)))
    check("the cut has its own label", prog.offsets.get("__cut_%d" % CUT) == CUT)
    check("the loop still spans the cut: its top before, its end after",
          _loop(prog)[0] < CUT < _loop(prog)[1])
    check("names before the cut did not move",
          all(prog.offsets[n] == flat.offsets[n] for n in ("e4_sub", "e4_thr", "e4_fill",
                                                            "e4_run"))
          and _loop(prog)[0] == _loop(flat)[0])
    strip = lambda d: [(e["script"], e["event"], e["op"]) for e in d]     # noqa: E731
    check("the debug map lists the same events (the filler is not one)",
          strip(prog.debug) == strip(flat.debug))
    check("no debug entry points into the filler",
          pad is not None and not any(pad[0] <= e["off"] < CUT for e in prog.debug))
    for label, p in (("with the filler", prog), ("without (the reference)", flat)):
        vm = RefVM(p.code, entry=p.offsets["e4_run"])
        vm.run(12)
        got = {v: vm.heap[p.variables[v]] for v in ("a", "b", "gold", "i")}
        check("the reference VM, %s: b 2 (loop), a 1 (sub), gold 10 (thread)" % label,
              got == {"a": 1, "b": 2, "gold": 10, "i": 2}, got)


def test_names_move_to_the_cut():
    print("[a label and a script entry waiting at a cut move with the instruction]")
    # the filler script is 4 k + 1 bytes; `tgt` opens with a 4-byte SET_CONST
    for k, why in ((4095, "the JUMP alone fills the gap"), (4094, "one instruction still fits"),
                   (4093, "the same, four bytes earlier")):
        scripts = [_fill(k), {"name": "tgt", "events": [
            {"event": "label", "name": "here"},
            {"event": "set_var", "var": "z", "value": 7},
            {"event": "set_var", "var": "z", "value": 8},
            {"event": "set_var", "var": "z", "value": 9},
            {"event": "stop"}]}]
        prog = _compile(scripts)
        off = [o for o, n, s, r in iter_instructions(prog.code)]
        clean = code_cuts_clean(prog.code)
        # a user label is an anonymous anchor (`__L<n>`): this program has one
        here = [v for n, v in prog.offsets.items() if n.startswith("__L")]
        if 4 * k + 1 + 4 > CUT - 3:
            check("k=%d (%s): the entry and its label sit on the cut" % (k, why),
                  clean and prog.offsets["tgt"] == CUT and here == [CUT]
                  and CUT in off, (prog.offsets["tgt"], here))
        else:
            check("k=%d (%s): the entry stays, a later instruction moves" % (k, why),
                  clean and prog.offsets["tgt"] == 4 * k + 1 and CUT in off,
                  prog.offsets["tgt"])
    # an instruction that ENDS on the cut stays: the next one starts the chunk
    fill = _fill(4094)
    fill["events"] += [{"event": "idle"}] * 3            # 4 * 4094 + 3 + STOP = 16380
    scripts = [fill, {"name": "tgt", "events": [
        {"event": "set_var", "var": "z", "value": 7},
        {"event": "set_var", "var": "z", "value": 8}, {"event": "stop"}]}]
    prog, flat = _compile(scripts), _compile(scripts, chunk=1 << 30)
    check("an instruction that ends exactly on the cut is not moved, nothing is filled",
          prog.offsets["tgt"] == CUT - 4 and prog.code == flat.code
          and _pad(prog.code) is None and code_cuts_clean(prog.code),
          (prog.offsets["tgt"], len(prog.code), len(flat.code)))


# ---- the module ---------------------------------------------------------------

def _arrays(src):
    out = {}
    for name, n, body in re.findall(r"const (CODE\d*): array\[u8, (\d+)\] = \[(.*?)\]", src, re.S):
        out.setdefault(name, []).append(
            bytes(int(x) for x in body.replace("\n", " ").split(",") if x.strip()))
    return out


def _arms(src):
    """(chunked arm, one-array arm) of the VM_CODE_MULTI fold."""
    a = src.index("    if VM_CODE_MULTI {\n")
    b = src.index("\n    } else {\n", a)
    c = src.index("    function fetch(off: u16) -> u8 {", b)
    c = src.index("\n    }\n\n", src.index("bank(0) function code_window()", c))
    return src[a:b], src[b:c]


def test_module():
    print("[the scripts module]")
    scripts, k = _tuned()
    prog = _compile(scripts)
    src = emit_scripts_module(scripts)
    check("a blob past one chunk gets the fold", src.count("    if VM_CODE_MULTI {\n") == 1)
    multi, single = _arms(src)
    arrs = _arrays(multi)
    check("the chunked arm: CODE is one full bank, CODE2 the rest",
          sorted(arrs) == ["CODE", "CODE2"] and len(arrs["CODE"][0]) == CUT
          and arrs["CODE"][0] + arrs["CODE2"][0] == prog.code,
          {n: len(v[0]) for n, v in arrs.items()})
    one = _arrays(single)
    check("the other arm is the blob as ONE array", one.get("CODE") == [prog.code]
          and "CODE2" not in one)
    order = re.findall(r"assets\.bank_enter\((CODE\d*)\)", multi)
    check("code_map names the chunks in pack order, CODE first", order == ["CODE", "CODE2"], order)
    check("the chunked window registers the multi form, once",
          multi.count("core.set_code_multi(assets.address(CODE), code_map, code_leave)") == 1
          and "set_code_multi" not in single)
    check("its fetch maps the chunk, reads inside it and restores",
          "code_map(h & 0xC0)" in multi and "(off & 0x3FFF)" in multi
          and "assets.bank_leave(saved)" in multi)
    check("the one-array arm is today's window",
          "core.set_code_window(assets.address(CODE), code_enter, code_leave)" in single
          and "return assets.code_byte(CODE, off)" in single)
    check("every ENTRY const is there once",
          all(src.count("const ENTRY_%s: u16 = " % n) == 1 for n in prog.order))
    # three chunks
    big = _cross_scripts(k + 4200)
    p3 = _compile(big)
    m3, _s3 = _arms(emit_scripts_module(big))
    a3 = _arrays(m3)
    check("a blob past two chunks: three arrays, the third mapped by its PC bits",
          len(p3.code) > 2 * CUT and sorted(a3) == ["CODE", "CODE2", "CODE3"]
          and b"".join(a3[n][0] for n in ("CODE", "CODE2", "CODE3")) == p3.code
          and "if k == 64 { return assets.bank_enter(CODE2) }" in m3
          and "return assets.bank_enter(CODE3)" in m3 and code_cuts_clean(p3.code))
    # hand-laid-out bytecode: an instruction across the cut -> no fold
    flat = _compile(scripts, chunk=1 << 30)
    check("a blob with an instruction across a cut is not chunked",
          not flat.code_chunked() and prog.code_chunked()
          and "VM_CODE_MULTI" not in flat.to_scripts_mos())


def test_rule():
    print("[where a build reads the chunks]")
    banks = ["vm.core"]
    rows = (("gameboy", banks, True, True), ("gameboy_color", banks, True, True),
            ("sms", banks, True, True), ("gamegear", banks, True, True),
            ("pce", banks, True, True), ("lynx", banks, True, False),
            ("nes", banks, True, False), ("gameboy", [], True, False),
            ("gameboy", banks, False, False), ("pce", [], True, False))
    for plat, cb, bb, want in rows:
        check("%s code_banks=%s bank_bytecode=%s -> %s" % (plat, bool(cb), bb, want),
              code_multi(plat, cb, bb) is want)
    check("only the GBDK consoles but the NES need the bank",
          [p for p in ("gameboy", "gameboy_color", "sms", "gamegear", "nes", "lynx", "pce")
           if code_must_bank(p)] == ["gameboy", "gameboy_color", "sms", "gamegear"])
    with open(os.path.join(ROOT, "mosaik8_build.py"), encoding="utf-8") as f:
        bt = f.read()
    i = bt.index("'core.set_code_multi(' in text")
    seg = bt[i:i + 1100]
    check("mosaik8_build states VM_CODE_MULTI by that rule, off the module's own line",
          "code_multi(platform, self.config.get_code_banks()," in seg
          and "defines['VM_CODE_MULTI'] = True" in seg)
    check("...and refuses a GBDK build that does not bank the blob",
          "elif code_must_bank(platform):" in seg and "raise RuntimeError(" in seg
          and "bank_bytecode" in seg)


def test_compiles_to_c():
    print("[every form compiles to C]")
    scripts, _k = _tuned()
    src = emit_scripts_module(scripts)
    srcs = [("scripts.mos", src), ("core.mos", CORE_STUB)]

    def comp(plat, multi, banked=True, text=src):
        return MosaikCompiler().compile_program(
            [("scripts.mos", text), ("core.mos", CORE_STUB)], platform=plat,
            bank_bytecode=banked, code_banks=["vm.core"] if banked else None,
            defines={"VM_CODE_MULTI": multi, "VM_CODE_BANKED": banked, "VM_STR_FAR": False})

    for plat in ("gameboy", "sms", "pce"):
        c = comp(plat, True)
        check("[%s] the chunked arm compiles" % plat,
              not c.startswith("Compilation error") and "scripts_CODE2" in c
              and "scripts_code_map" in c, c[:300])
    for plat in ("lynx", "gameboy"):
        c = comp(plat, False, banked=False)
        check("[%s] the one-array arm compiles" % plat,
              not c.startswith("Compilation error") and "scripts_CODE2" not in c, c[:300])
    c = comp("gameboy", True, banked=False)
    check("chunks nothing banks are refused, with the reason",
          c.startswith("Compilation error") and "is not in a ROM bank" in c, c[:300])
    a = src.index("    bank(0) local function code_map(")
    b = src.index("    function fetch(", a)
    swapped = src[:a] + src[a:b].replace(
        "if k == 0 { return assets.bank_enter(CODE) }",
        "if k == 64 { return assets.bank_enter(CODE2) }").replace(
        "return assets.bank_enter(CODE2)\n", "return assets.bank_enter(CODE)\n", 1) + src[b:]
    check("the swap is a different module", swapped != src and swapped.count("bank_enter(CODE2)") == 1)
    # something in front of the small chunk: a second banked blob named first
    lead = swapped.replace(
        "    bank(0) local function code_map(",
        "    const LEAD: array[u8, 4] = [1, 2, 3, 4]\n"
        "    function lead(off: u16) -> u8 { return assets.code_byte(LEAD, off) }\n"
        "    bank(0) local function code_map(", 1)
    c = comp("gameboy", True, text=lead)
    check("a chunk that is not first in its bank is refused, with the reason",
          c.startswith("Compilation error") and "is not the first array of ROM bank" in c, c[:400])
    del srcs


# ---- ROMs --------------------------------------------------------------------

TOKEN = "WILD $species[a]$ AND\n$species[b]$ HAS $gold$ G"
LITERAL = "WILD EMBERKIT AND\nBRINEFIN HAS 10 G"
MENU = ["ALPHA", "BRAVO", "CHARLIE"]
LIST_TOML = '\n[lists.species]\nitems = ["SPROUTLE", "EMBERKIT", "BRINEFIN"]\n'
ARMS = {
    # name: (intro text, lists, cross the cut, menu instead of the intro,
    #        the shell registers the code window)
    "cut": (TOKEN, True, True, False, True),
    "cut_menu": (TOKEN, True, True, True, True),
    # vm-dialogue's own shell never calls scripts.code_window(): the VM reads
    # every byte through `fetch`, whose chunked form maps for itself
    "cut_nowin": (TOKEN, True, True, False, False),
    "lit": (LITERAL, False, False, False, False),
    "lit_out": ("A WILD !\nAPPEARED!", False, False, False, False),
    "lit_menu": (LITERAL, False, False, True, False),
}
BOOT = "        core.boot(scripts.fetch, scripts.render_text, scripts.ENTRY_main)\n"


def _arm_project(tmp, arm, banked=True):
    import toml
    from mosaik_vm import scripts_module_path
    text, lists, cross, menu, window = ARMS[arm]
    root = os.path.join(tmp, arm + ("" if banked else "_res"), "vm-dialogue")
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
    by = {s["name"]: s for s in doc["script"]}
    if menu:
        by["field_init"]["events"] = [{"event": "menu", "var": "pick", "options": MENU},
                                      {"event": "stop"}]
    if cross:
        # The room's On Init does the work (a thread `main` starts at boot is
        # killed by the first room load): field_init sits behind the filler,
        # ACROSS the cut, its loop holding the cut and its box or menu behind
        # it in the second chunk. `main` leaves gold for the thread to set.
        by["main"]["events"] = [{"event": "set_var", "var": "gold", "value": 0},
                                {"event": "stop"}]
        by["field_init"]["events"] = CROSS + by["field_init"]["events"]
        rest = [x for x in doc["script"] if x["name"] not in ("main", "field_init")]
        doc["script"] = [by["main"], SUB, THR, _fill(0), by["field_init"]] + rest
        with open(mp, "w", encoding="utf-8") as f:
            f.write(toml.dumps(doc))
        # size the filler so the cut is inside e4_run's loop, on the real program
        from mosaik_vm import compile_path
        old_chunk = Compiler.CODE_CHUNK
        Compiler.CODE_CHUNK = 1 << 30
        try:
            flat = compile_path(sd)
        finally:
            Compiler.CODE_CHUNK = old_chunk
        assert doc["script"][3]["name"] == "e4_fill"
        doc["script"][3] = _fill((CUT - sum(_loop(flat, "field_init")) // 2) // 4)
    with open(mp, "w", encoding="utf-8") as f:
        f.write(toml.dumps(doc))
    mt = os.path.join(root, "mosaik.toml")
    conf = toml.load(mt)
    if banked:
        conf.setdefault("build", {})["code_banks"] = ["vm.core", "vm.actor", "vm.entity", "scenes"]
        conf["build"]["bank_bytecode"] = True
    with open(mt, "w", encoding="utf-8") as f:
        f.write(toml.dumps(conf))
    with open(os.path.join(root, "src", "scripts.mos"), "w", encoding="utf-8",
              newline="\n") as f:
        f.write(scripts_module_path(sd))
    if window:
        sp = os.path.join(root, "src", "main.mos")
        with open(sp, encoding="utf-8") as f:
            shell = f.read()
        assert shell.count(BOOT) == 1 and "code_window" not in shell
        with open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(shell.replace(BOOT, BOOT + "        scripts.code_window()\n"))
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
    """PyBoy in a child, with a time limit: a ROM that runs wild can keep
    PyBoy inside one frame for ever, and that must read as a failure."""
    import subprocess
    code = ("import sys, os, warnings; warnings.filterwarnings('ignore')\n"
            "from pyboy import PyBoy\n"
            "pb = PyBoy(%r, window='null', sound_emulated=False)\n"
            "pb.tick(700, True); im = pb.screen.image\n"
            "w, h = im.size; im = im.convert('RGB').crop((0, h - 48, w, h))\n"
            "sys.stdout.write(im.tobytes().hex()); sys.stdout.flush(); os._exit(0)\n" % rom)
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           timeout=180)
    except subprocess.TimeoutExpired:
        return "the ROM did not get through 700 frames (it runs wild)"
    return bytes.fromhex(r.stdout.strip() or "") or r.stderr[-300:]


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
    print("[ROMs: what crosses the cut reads like its literal twin]")
    import mosaik8_build
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    cores = [c for c in os.listdir(os.path.join(ROOT, "emu", "libretro"))
             if c.startswith("genesis_plus_gx_libretro")]
    tmp = tempfile.mkdtemp(prefix="blobcut_")
    try:
        roots = {arm: _arm_project(tmp, arm) for arm in ARMS}
        from mosaik_vm import compile_path
        for arm in ("cut", "cut_menu", "cut_nowin"):
            prog = compile_path(os.path.join(roots[arm], "scripts"))
            src = _scripts_of(roots[arm])
            check("the %s fixture is chunked and its loop spans the cut" % arm,
                  "core.set_code_multi(" in src
                  and _loop(prog, "field_init")[0] < CUT < _loop(prog, "field_init")[1]
                  and prog.offsets["e4_sub"] < CUT and prog.offsets["e4_thr"] < CUT,
                  (_loop(prog, "field_init"), prog.offsets["e4_sub"]))
        mi = [o for o, n, s, r in iter_instructions(
            compile_path(os.path.join(roots["cut_menu"], "scripts")).code) if n == "MENU"]
        check("the menu's option ids are in the second chunk", bool(mi) and min(mi) > CUT, mi)
        for plat, ext in (("gameboy", "gb"), ("sms", "sms")):
            shots = {}
            for arm, root in roots.items():
                ok, log = _build(root, plat)
                if not check("[%s] %s builds" % (plat, arm), ok, log[-600:]):
                    continue
                rom = os.path.join(root, "build", plat, "vm-dialogue." + ext)
                if arm.startswith("cut"):
                    with open(os.path.join(root, "build", plat, "vm-dialogue.c"),
                              encoding="utf-8", errors="replace") as f:
                        csrc = f.read()
                    reg = "scripts_code_window();" in csrc
                    check("[%s] %s: the chunked window %s" % (
                        plat, arm, "is registered" if ARMS[arm][4] else "is NOT registered"),
                        reg == ARMS[arm][4] and "scripts_CODE2" in csrc)
                if plat == "gameboy":
                    shots[arm] = _gb_screen(rom)
                elif cores:
                    shots[arm] = _sms_screen(rom, cores[0])
            if len(shots) < len(ARMS):
                continue
            check("[%s] the screen reads text (the two literal boxes differ)" % plat,
                  isinstance(shots["lit"], bytes) and shots["lit"] != shots["lit_out"])
            check("[%s] ...and a menu is not a text box" % plat,
                  shots["lit_menu"] != shots["lit"])
            check("[%s] a loop, CALL / RET and a thread across the cut: the box of the "
                  "literal twin" % plat, shots["cut"] == shots["lit"])
            check("[%s] a menu whose option ids sit in the second chunk: the twin's menu"
                  % plat, shots["cut_menu"] == shots["lit_menu"])
            check("[%s] a shell with no code window (every byte through the chunked "
                  "fetch): the same box" % plat, shots["cut_nowin"] == shots["lit"])
        # a GBDK build that does not bank the blob says what is missing
        res = _arm_project(tmp, "cut", banked=False)
        ok, log = _build(res, "gameboy")
        check("[gameboy] a blob past one bank that is not banked is refused, with the reason",
              not ok and "larger than one 16 KB ROM bank" in log and "bank_bytecode" in log,
              log[-400:])
        if mosaik8_build.cc65_available():
            ok, log = _build(res, "lynx")
            check("[lynx] the one-array arm links (cc65), the blob streamed from the cart",
                  ok, log[-600:])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    test_one_chunk_is_unchanged()
    test_layout_rule()
    test_names_move_to_the_cut()
    test_module()
    test_rule()
    test_compiles_to_c()
    test_roms()
    print("\n" + ("All checks passed" if not FAILS else "%d FAILED" % len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
