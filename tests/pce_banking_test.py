"""PC Engine ROM banking (`[build] code_banks` on the cc65 PCE backend).

The GB family banks through sdcc's `__banked` convention; the PCE reuses the
same analysis and lowers it to cc65's `#pragma wrapped-call` hook, one 16 KB
window at $4000-$7FFF (MPR2 + MPR3) and an assembly trampoline
(mosaik/codegen/cc65_bank.py). Pinned here:

  * opt-in: no code_banks on the PCE is the old linear image (no pragma, no
    trampoline, no assembly unit), and a bare bank(N) stays ignored there;
  * the lowering: a banked function's PROTOTYPE sits under the wrapped-call
    hook with its LOGICAL bank, its BODY under its bank's code + rodata
    segment (literals local), still ONE translation unit (no bank TUs, no
    BANKED keyword on a signature);
  * a const only banked code reads is defined in that bank's segment; an
    address-taken function keeps a resident stub;
  * the build: the ld65 config is the toolchain's own pce.cfg plus one area
    per bank; the HuCard is the rotated 32 KB resident image + the banks;
  * the ROM (when cc65 + the Beetle PCE core are installed): a fixture whose
    banked code calls ACROSS banks and reads its own bank's data after the
    call prints exactly what the same program prints unbanked.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik import MosaikCompiler  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


DEEP = '''
module "deep" {
    function twice(a: u8) -> u16 {
        return a * 2 + 1000
    }
    export twice
}
'''

COLD = '''
module "cold" {
    import "deep"
    const SEED: array[u8, 4] = [7, 11, 13, 17]
    var hits: u8 = 0
    function add(a: u8, b: u8) -> u8 {
        return a + b + SEED[2]
    }
    -- calls into ANOTHER bank, then reads its OWN bank's const: the
    -- trampoline must have restored this bank on the way back
    function nested(a: u8) -> u16 {
        var t: u16 = deep.twice(a)
        return t + SEED[0]
    }
    function tick() {
        hits += 1
    }
    export add, nested, tick
}
'''

MAIN = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    import "platform.assets"
    import "cold"
    const BIG: array[u8, 6] = [21, 22, 23, 24, 25, 26]
    var cb: function() = cold.tick
    function main() {
        video.enable_lcd()
        text.print_string(1, 1, "PCE BANKING")
        assets.use(BIG)
        var b1: u8 = BIG[1]
        var r1: u8 = cold.add(3, 4)
        var b2: u8 = BIG[2]
        var r2: u16 = cold.nested(5)
        cb()
        text.print_number(1, 3, b1)
        text.print_number(1, 4, r1)
        text.print_number(1, 5, b2)
        text.print_number(1, 6, r2)
        loop {
            video.wait_vblank()
        }
    }
}
'''

SOURCES = [("deep.mos", DEEP), ("cold.mos", COLD), ("main.mos", MAIN)]
BANKS = ["cold", "deep"]


def compile_program(sources, platform="pce", **kw):
    compiler = MosaikCompiler()
    c = compiler.compile_program(sources, platform=platform, **kw)
    assert not c.startswith("Compilation error"), c
    return c, compiler.code_generator


def body_segment(c, name):
    """The code segment a function's DEFINITION is emitted under (a thunked
    body is defined as `<name>__bk`)."""
    stack = []
    for line in c.splitlines():
        s = line.strip()
        if s.startswith('#pragma code-name (push, "'):
            stack.append(s.split('"')[1])
        elif s == '#pragma code-name (pop)':
            stack.pop()
        elif s.endswith("{") and ((" " + name + "(") in s
                                  or (" " + name + "__bk(") in s):
            return stack[-1] if stack else "CODE"
    return None


def has_thunk(asm, name, bank):
    """The resident thunk `_name: jsr gbs_thunk / .word _name__bk / .byte bank`."""
    return ("_%s:\n        jsr     gbs_thunk\n        .word   _%s__bk\n"
            "        .byte   %d" % (name, name, bank)) in asm


def test_codegen():
    print("codegen")
    c, g = compile_program(SOURCES, code_banks=BANKS)
    check("banking is active on the PCE under code_banks", g.banking_active)
    check("ONE translation unit (no bank TUs)", not g.bank_units)
    asm = g.asm_units.get("bank", "")
    check("the thunk entry + the mapper are an assembly unit",
          "gbs_thunk:" in asm and "tam     #%00000100" in asm)
    bank_cold, bank_deep = g._code_bank_of["cold"], g._code_bank_of["deep"]
    lines = c.splitlines()
    check("a banked function keeps a plain prototype, its body is <name>__bk, "
          "and its name is a resident thunk with its bank",
          "uint8_t cold_add(uint8_t a, uint8_t b);" in lines
          and "uint8_t cold_add__bk(uint8_t a, uint8_t b) {" in lines
          and has_thunk(asm, "cold_add", bank_cold)
          and has_thunk(asm, "deep_twice", bank_deep)
          and "wrapped-call" not in c)
    check("a banked body sits in its bank's code segment",
          body_segment(c, "cold_add") == "BK%d" % bank_cold
          and body_segment(c, "deep_twice") == "BK%d" % bank_deep)
    check("main stays resident", body_segment(c, "main") == "CODE")
    check("no BANKED keyword on any signature (the macro only)",
          all(not ln.rstrip(";{ ").endswith("BANKED")
              for ln in lines if not ln.startswith("#define")))
    check("the GB names are the PCE helpers",
          "#define SWITCH_ROM(b) gbs_bank_map(b)" in c
          and "#define CURRENT_BANK gbs_bank" in c)
    # The co-located const is DEFINED in its bank's rodata segment.
    k = c.find("const uint8_t cold_SEED[4]")
    seg_line = c.rfind('#pragma rodata-name (push, "', 0, k)
    check("a const only banked code reads is defined in that bank",
          k > 0 and c[seg_line:k].startswith(
              '#pragma rodata-name (push, "BK%d")' % bank_cold)
          and "extern const uint8_t cold_SEED" not in c)
    check("an address-taken function keeps a resident stub",
          body_segment(c, "cold_tick") == "CODE"
          and body_segment(c, "cold_tick__bimpl") == "BK%d" % bank_cold)
    check("a streamed const banks with its reads switching the window",
          "main_BIG" in g.streamed and "SWITCH_ROM(%d)" % g.streamed["main_BIG"] in c)
    check("the build sizes the cart from the highest bank",
          g.cc65_max_bank == max(g.streamed["main_BIG"], bank_cold, bank_deep))

    # --- opt-in: nothing of it without code_banks ---------------------------
    plain, gp = compile_program(SOURCES)
    again, ga = compile_program(SOURCES, code_banks=[])
    check("no code_banks: byte-identical, no banking", plain == again
          and not gp.banking_active and not gp.asm_units
          and "wrapped-call" not in plain and "gbs_bank" not in plain)
    banked_src = [("m.mos", '''
module "m" {
    import "platform.video"
    bank(2) function f() -> u8 { return 3 }
    function main() { var v: u8 = f()  v = v  video.enable_lcd() }
}
''')]
    bc, bg = compile_program(banked_src)
    check("a bare bank(N) stays ignored on the PCE (the linear image)",
          not bg.banking_active and "wrapped-call" not in bc)
    lc, lg = compile_program(SOURCES, platform="lynx", code_banks=BANKS)
    check("the Lynx does not take the PCE path",
          "SWITCH_ROM" not in lc and "tam" not in lg.asm_units.get("bank", ""))


def test_build_cfg():
    print("build config")
    from mosaik8_build import MosaikBuilder, cc65_available
    if not cc65_available():
        print("  [SKIP] cc65 not installed")
        return
    b = MosaikBuilder.__new__(MosaikBuilder)
    from mosaik8_build import Cc65Interface
    b.cc65 = Cc65Interface()
    cfg = b.pce_banked_cfg(3)
    stock = open(os.path.join(b.cc65.cc65_path, "cfg", "pce.cfg"),
                 encoding="utf-8").read()
    rom_line = [ln for ln in stock.splitlines() if ln.strip().startswith("ROM:")][0]
    check("the resident ROM area is the toolchain's own line", rom_line in cfg)
    check("one 16 KB area per bank at $4000, own file",
          all(('ROMBK%d: file = "%%O.bk%d", start = $4000, size = $4000' % (k, k)) in cfg
              for k in (1, 2, 3)) and "ROMBK4" not in cfg)
    check("one segment per bank", all(("BK%d:" % k) in cfg for k in (1, 2, 3)))


def _core():
    p = os.path.join(ROOT, "emu", "libretro", "mednafen_pce_fast_libretro.dll")
    return p if os.path.isfile(p) else None


def _build_and_shoot(tmp, banked):
    proj = os.path.join(tmp, "banked" if banked else "plain")
    os.makedirs(os.path.join(proj, "src"))
    for name, text in SOURCES:
        with open(os.path.join(proj, "src", name), "w", encoding="utf-8") as f:
            f.write(text)
    build = '\n[build]\noutput_dir = "build"\n'
    if banked:
        build += 'code_banks = ["cold", "deep"]\n'
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "pcebank"\nversion = "0.1.0"\n'
                'target_platforms = ["pce"]\n\n[source]\nfolder = "src/"\n'
                + build)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "pce", proj], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    rom = os.path.join(proj, "build", "pce", "pcebank.pce")
    if r.returncode or not os.path.isfile(rom):
        return None, (r.stdout + r.stderr)[-1500:], 0
    png = os.path.join(tmp, ("b" if banked else "p") + ".png")
    subprocess.run([sys.executable, os.path.join(ROOT, "emu", "libretro", "run_lynx.py"),
                    rom, "90", "--core", "mednafen_pce_fast", "--png", png],
                   capture_output=True, cwd=ROOT)
    return (png if os.path.isfile(png) else None), r.stdout, os.path.getsize(rom)


def test_rom():
    print("ROM (Beetle PCE Fast)")
    from mosaik8_build import cc65_available
    if not cc65_available() or not _core():
        print("  [SKIP] cc65 or the mednafen_pce_fast core not installed")
        return
    from PIL import Image, ImageChops
    tmp = tempfile.mkdtemp(prefix="pcebank_")
    try:
        plain, _out, plain_size = _build_and_shoot(tmp, False)
        banked, out, size = _build_and_shoot(tmp, True)
        check("the plain fixture builds and runs", plain is not None)
        check("the banked fixture builds and runs", banked is not None, out)
        if not (plain and banked):
            return
        check("the banked HuCard is past the 32 KB linear image",
              plain_size == 0x8000 and size >= 0x10000 and size & (size - 1) == 0,
              "%d / %d" % (plain_size, size))
        a, b = Image.open(plain).convert("L"), Image.open(banked).convert("L")
        ink = sum(1 for p in a.getdata() if p > 128)
        check("the fixture prints its rows (%d ink pixels)" % ink, ink > 300)
        check("banked output == plain output (cross-bank call, own-bank "
              "read after it, banked data, a stub)",
              ImageChops.difference(a, b).getbbox() is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_codegen()
    test_build_cfg()
    test_rom()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
