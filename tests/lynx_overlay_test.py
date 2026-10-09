"""Lynx code OVERLAYS (`[build] code_banks` on the cc65 Lynx backend) and the
`hot` function annotation.

The Lynx has no mapper: a listed module's cold functions become one cart
overlay, linked at a RAM window at $0200 (MAIN moves up above it) and read from
the cart when a call finds another overlay loaded (mosaik/codegen/cc65_bank.py).
Pinned here:

  * `hot function` parses (alone and with `local` / `bank(N)`), a variable
    named `hot` still works, and `hot` changes nothing off the Lynx;
  * on the Lynx a `hot` or `bank(0)` function stays resident, the rest of a
    listed module goes to its overlay segment, its prototype under the
    resident thunk (6 B, the overlay id inline); no `local-strings` (a literal must
    survive a reload); no code_banks = the byte-identical old program;
  * the ld65 config: the toolchain's lynx.cfg with MAIN above the window and
    one area per overlay;
  * the ROM (cc65 + a Lynx core): a fixture whose overlay code calls into ANOTHER
    overlay and then reads its OWN overlay's const prints exactly what the
    plain build prints, and a steady loop loads no overlay.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mosaik import MosaikCompiler  # noqa: E402
import pce_banking_test as fixture  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


HOT_COLD = fixture.COLD.replace(
    "    function tick() {",
    "    hot function frame_step(v: u8) -> u8 {\n"
    "        return v + 1\n"
    "    }\n"
    "    hot local function frame_local(v: u8) -> u8 {\n"
    "        return v + 2\n"
    "    }\n"
    "    function tick() {").replace(
    "export add, nested, tick", "export add, nested, tick, frame_step")
SOURCES = [("deep.mos", fixture.DEEP), ("cold.mos", HOT_COLD),
           ("main.mos", fixture.MAIN)]
BANKS = ["cold", "deep"]


def compile_program(sources, platform="lynx", **kw):
    compiler = MosaikCompiler()
    c = compiler.compile_program(sources, platform=platform, **kw)
    assert not c.startswith("Compilation error"), c
    return c, compiler.code_generator


def test_language():
    print("language")
    from mosaik.lexer import Lexer
    from mosaik.parser import Parser
    src = '''
module "m" {
    var hot: u8 = 1
    hot function a() { hot = hot + 1 }
    hot local function b() { }
    hot bank(0) function c() { }
    function main() { a() b() c() }
}
'''
    prog = Parser(Lexer(src).tokenize()).parse()
    funcs = {d.name: d for m in prog.modules for d in m.declarations
             if hasattr(d, 'body') and hasattr(d, 'hot')}
    check("`hot function` / `hot local` / `hot bank(0)` parse",
          funcs['a'].hot and funcs['b'].hot and funcs['b'].is_local
          and funcs['c'].hot and funcs['c'].bank_pinned and not funcs['main'].hot)
    c1, _ = compile_program([("m.mos", src)], platform="gameboy")
    c2, _ = compile_program([("m.mos", src.replace("hot function", "function")
                              .replace("hot local", "local")
                              .replace("hot bank", "bank"))], platform="gameboy")
    check("`hot` changes nothing on the GB (and `hot` as a var works)", c1 == c2)
    g1, _ = compile_program(SOURCES, platform="gameboy", code_banks=BANKS)
    g2, _ = compile_program([("deep.mos", fixture.DEEP),
                             ("cold.mos", HOT_COLD.replace("hot function", "function")
                              .replace("hot local", "local")),
                             ("main.mos", fixture.MAIN)],
                            platform="gameboy", code_banks=BANKS)
    check("`hot` changes nothing under GB code banking", g1 == g2)
    p1, _ = compile_program(SOURCES, platform="pce", code_banks=BANKS)
    p2, _ = compile_program([("deep.mos", fixture.DEEP),
                             ("cold.mos", HOT_COLD.replace("hot function", "function")
                              .replace("hot local", "local")),
                             ("main.mos", fixture.MAIN)],
                            platform="pce", code_banks=BANKS)
    check("`hot` changes nothing under PCE banking", p1 == p2)


def test_codegen():
    print("codegen")
    c, g = compile_program(SOURCES, code_banks=BANKS)
    check("overlay banking is active", g.banking_active and g._overlay_banking())
    seg = fixture.body_segment
    bank_cold = g._code_bank_of["cold"]
    check("a cold function goes to its module's overlay",
          seg(c, "cold_add") == "BK%d" % bank_cold
          and seg(c, "deep_twice") == "BK%d" % g._code_bank_of["deep"])
    check("a `hot` function stays resident",
          seg(c, "cold_frame_step") == "CODE" and seg(c, "cold_frame_local") == "CODE")
    asm = g.asm_units.get("bank", "")
    lines = c.splitlines()
    check("an overlay function's name is a resident thunk with its overlay id",
          "uint8_t cold_add(uint8_t a, uint8_t b);" in lines
          and "uint8_t cold_add__bk(uint8_t a, uint8_t b) {" in lines
          and fixture.has_thunk(asm, "cold_add", bank_cold)
          and not fixture.has_thunk(asm, "cold_frame_step", bank_cold))
    check("no local-strings in an overlay (literals stay resident)",
          "local-strings" not in c)
    check("the overlay loader and the load counter are emitted",
          "void __fastcall__ gbs_ovl_load(uint8_t id)" in c
          and "++gbs_ovl_loads;" in c and "uint16_t gbs_ovl_loads = 0;" in c)
    check("the thunk entry restores the caller's overlay",
          "gbs_thunk:" in asm and asm.count("jsr     _gbs_ovl_load") == 2
          and "__OV%d_FILEOFFS__" % bank_cold in asm)
    check("no PCE mapper on the Lynx", "tam" not in asm and "SWITCH_ROM" not in c)
    plain, gp = compile_program(SOURCES)
    again, _ = compile_program(SOURCES, code_banks=[])
    check("no code_banks: byte-identical, no overlays", plain == again
          and not gp.banking_active and not gp.asm_units
          and "wrapped-call" not in plain and "gbs_ovl" not in plain)


def test_build_cfg():
    print("build config")
    from mosaik8_build import MosaikBuilder, Cc65Interface, cc65_available
    if not cc65_available():
        print("  [SKIP] cc65 not installed")
        return
    b = MosaikBuilder.__new__(MosaikBuilder)
    b.cc65 = Cc65Interface()
    cfg = b.lynx_overlay_cfg(2)
    check("MAIN moves up above the window, the stack top stays",
          "start = $0200 + __OVERLAYSIZE__, size = $BE38 - __OVERLAYSIZE__ - __STACKSIZE__;" in cfg)
    check("one area + one segment per overlay at $0200",
          all(("OV%d:    file = %%O, define = yes, start = $0200" % k) in cfg
              and ("BK%d:      load = OV%d" % (k, k)) in cfg for k in (1, 2))
          and "OV3" not in cfg)


def _lynx_core():
    for stem in ("mednafen_lynx", "handy"):
        if os.path.isfile(os.path.join(ROOT, "emu", "libretro", stem + "_libretro.dll")):
            return True
    return False


def _build(tmp, banked):
    proj = os.path.join(tmp, "b" if banked else "p")
    os.makedirs(os.path.join(proj, "src"))
    for name, text in SOURCES:
        with open(os.path.join(proj, "src", name), "w", encoding="utf-8") as f:
            f.write(text)
    build = '\n[build]\noutput_dir = "build"\n'
    if banked:
        build += 'code_banks = ["cold", "deep"]\n'
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "ovl"\nversion = "0.1.0"\n'
                'target_platforms = ["lynx"]\n\n[source]\nfolder = "src/"\n' + build)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--debug", "--platform", "lynx", proj], capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    rom = os.path.join(proj, "build", "lynx", "ovl.lnx")
    return (rom if r.returncode == 0 and os.path.isfile(rom) else None), r.stdout + r.stderr


def _shot(rom, frames, png):
    subprocess.run([sys.executable, os.path.join(ROOT, "emu", "libretro", "run_lynx.py"),
                    rom, str(frames), "--png", png], capture_output=True, cwd=ROOT)
    return png if os.path.isfile(png) else None


def test_rom():
    print("ROM (Lynx core)")
    from mosaik8_build import cc65_available
    if not cc65_available() or not _lynx_core():
        print("  [SKIP] cc65 or a Lynx core not installed")
        return
    from PIL import Image, ImageChops
    tmp = tempfile.mkdtemp(prefix="lynxovl_")
    try:
        plain, _o = _build(tmp, False)
        banked, out = _build(tmp, True)
        check("the plain fixture builds", plain is not None)
        check("the overlay fixture builds", banked is not None, out[-1500:])
        if not (plain and banked):
            return
        check("the build sized the overlay window", "Lynx code overlays: 2" in out)
        a = _shot(plain, 100, os.path.join(tmp, "p.png"))
        b = _shot(banked, 100, os.path.join(tmp, "b.png"))
        ia, ib = Image.open(a).convert("L"), Image.open(b).convert("L")
        ink = sum(1 for p in ia.getdata() if p > 128)
        check("the fixture prints its rows (%d ink pixels)" % ink, ink > 200)
        check("overlay output == plain output (cross-overlay call, own-overlay "
              "read after it, a stub)", ImageChops.difference(ia, ib).getbbox() is None)
        # A steady loop loads nothing: the counter stops after the start-up.
        sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
        import lynx_probe as lp
        from libretro import SessionBuilder
        from libretro.drivers.path import ExplicitPathDriver
        labels = lp.load_labels(banked + ".lbl")
        addr = labels.get("_gbs_ovl_loads")
        builder = (SessionBuilder.defaults(lp.CORE).with_content(banked)
                   .with_paths(ExplicitPathDriver(corepath=lp.CORE, system=lp.SYSTEM_DIR,
                                                  save=lp.SYSTEM_DIR, assets=lp.SYSTEM_DIR,
                                                  playlist=lp.SYSTEM_DIR))
                   .with_perf(None))
        with builder.build() as session:
            ram = lp.Ram(session)
            reads = []
            for f in range(160):
                session.run()
                if f in (80, 159):
                    reads.append(ram.read(addr, 2))
        check("overlays were loaded at start-up (%s)" % reads, reads[0] >= 2)
        check("a steady loop loads no overlay (%s)" % reads, reads[0] == reads[1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


TURNS = [
    ("main.mos", 'module "main" {\n'
     '    import "platform.video"\n    import "ea"\n    import "eb"\n'
     '    var n: u8\n'
     '    function main() {\n'
     '        video.enable_lcd()\n'
     '        eb.once()            -- overlay B is in the window now\n'
     '        loop {\n'
     '            n = ea.tick(n)   -- a RESIDENT caller, overlay A, every frame\n'
     '            video.wait_vblank()\n'
     '        }\n'
     '    }\n'
     '    export main\n}\n'),
    ("ea.mos", 'module "ea" {\n    var k: u8\n'
     '    function tick(v: u8) -> u8 {\n        k = k + 1\n        return v + k\n    }\n'
     '    export tick\n}\n'),
    ("eb.mos", 'module "eb" {\n    var k: u8\n'
     '    function once() {\n        k = 7\n    }\n'
     '    export once\n}\n'),
]


def test_turns():
    """Two overlays taking turns, both called from RESIDENT code: the window
    is reloaded only when the callee is not in it. The trampoline used to
    restore the window's previous tenant after every resident call (it took
    "the overlay loaded now" for "the caller's overlay"), so a resident loop
    calling overlay A after one call into B loaded A and B again every call:
    the Lynx shooter's play ran at a seventh of its speed."""
    print("ROM: two overlays taking turns from resident code (Lynx core)")
    from mosaik8_build import cc65_available
    if not cc65_available() or not _lynx_core():
        print("  [SKIP] cc65 or a Lynx core not installed")
        return
    tmp = tempfile.mkdtemp(prefix="lynxovlt_")
    try:
        proj = os.path.join(tmp, "t")
        os.makedirs(os.path.join(proj, "src"))
        for name, text in TURNS:
            with open(os.path.join(proj, "src", name), "w", encoding="utf-8") as f:
                f.write(text)
        with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
            f.write('[project]\nname = "ovt"\nversion = "0.1.0"\n'
                    'target_platforms = ["lynx"]\n\n[source]\nfolder = "src/"\n'
                    '\n[build]\noutput_dir = "build"\ncode_banks = ["ea", "eb"]\n')
        r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                            "--debug", "--platform", "lynx", proj], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        rom = os.path.join(proj, "build", "lynx", "ovt.lnx")
        check("the fixture builds with two overlays",
              os.path.isfile(rom) and "Lynx code overlays: 2" in r.stdout,
              (r.stdout + r.stderr)[-1500:])
        if not os.path.isfile(rom):
            return
        sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
        import lynx_probe as lp
        from libretro import SessionBuilder
        from libretro.drivers.path import ExplicitPathDriver
        labels = lp.load_labels(rom + ".lbl")
        loads, n = labels.get("_gbs_ovl_loads"), labels.get("_main_n")
        builder = (SessionBuilder.defaults(lp.CORE).with_content(rom)
                   .with_paths(ExplicitPathDriver(corepath=lp.CORE, system=lp.SYSTEM_DIR,
                                                  save=lp.SYSTEM_DIR, assets=lp.SYSTEM_DIR,
                                                  playlist=lp.SYSTEM_DIR))
                   .with_perf(None))
        with builder.build() as session:
            ram = lp.Ram(session)
            reads, ns = [], []
            for f in range(160):
                session.run()
                if f in (80, 159):
                    reads.append(ram.read(loads, 2))
                    ns.append(ram.read(n))
        check("the loop runs (main.n moved: %s)" % ns, ns[0] != ns[1])
        check("B then A were loaded once each (%s loads)" % reads, reads[0] == 2)
        check("a resident loop into A loads nothing more (%s)" % reads, reads[0] == reads[1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_language()
    test_codegen()
    test_build_cfg()
    test_rom()
    test_turns()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
