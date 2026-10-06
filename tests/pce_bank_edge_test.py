"""THE PC ENGINE BANK EDGE: no instruction may span $DFFF/$E000.

A rotated HuCard maps logical $C000-$DFFF to physical bank 3 and $E000-$FFFF
to bank 0, the one logical edge whose banks are not physically adjacent.
mednafen_pce_fast (the studio preview's and this suite's PCE emulator) fetches
an instruction's operand from the page its OPCODE is on, so an instruction
that starts before $E000 and ends at or after it reads garbage: it made
vm.music's PCE envelope step on every frame in one build and not in another.

What is pinned here:

  * the HuC6280 length table (`PCE_OPLEN`) equals cc65's own disassembler;
  * the decoder and the pad choice on hand-made images;
  * THE HAZARD IS REAL: a hand-placed `lda abs,y` at $DFFE loads the wrong
    byte on the core, the same load away from the edge does not (if a core
    update ever fixes it, this check says so and the guard can be retired);
  * THE GUARD: `MosaikBuilder._link_pce_guarded`, given a program whose load
    lands on $DFFE, relinks it with a pad and the load reads right - while the
    same program linked plainly reads wrong.

    python tests/pce_bank_edge_test.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")         # the builder prints emoji

import mosaik8_build as mb                            # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- %s" % (detail,)) if detail else ""))


def _cc65_bin():
    b = mb.MosaikBuilder()
    return os.path.join(b.cc65.cc65_path or "", "bin")


def _exe(d, name):
    p = os.path.join(d, name + (".exe" if os.name == "nt" else ""))
    return p if os.path.isfile(p) else None


def _core():
    p = os.path.join(ROOT, "emu", "libretro", "mednafen_pce_fast_libretro.dll")
    return p if os.path.isfile(p) else None


def test_oplen():
    print("\n[the HuC6280 length table is cc65's own]")
    da = _exe(_cc65_bin(), "da65")
    if not mb.cc65_available() or not da:
        print("  [SKIP] cc65 (da65) not installed")
        return
    tmp = tempfile.mkdtemp(prefix="oplen_")
    bad = []
    try:
        for op in range(256):
            f = os.path.join(tmp, "o.bin")
            with open(f, "wb") as fh:
                fh.write(bytes([op, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0xEA, 0xEA, 0xEA]))
            out = subprocess.run([da, "--cpu", "huc6280", "--start-addr", "0x8000",
                                  "--comments", "4", f], capture_output=True, text=True).stdout
            m = re.search(r";\s*8000 ((?:[0-9A-F]{2} )+)", out)
            n = len(m.group(1).split()) if m else None
            if n != mb.PCE_OPLEN[op]:
                bad.append((hex(op), n, mb.PCE_OPLEN[op]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check("all 256 opcode lengths match da65 --cpu huc6280", not bad, bad[:8])


def _image(code_at):
    """A linear 32 KB image of NOPs with the given {address: bytes}."""
    img = bytearray([0xEA]) * 0x8000
    for a, bs in code_at.items():
        img[a - 0x8000:a - 0x8000 + len(bs)] = bs
    return bytes(img)


def test_decoder():
    print("\n[the decoder and the pad choice]")
    segs = {"RODATA": (0x8000, 0x8FFF), "CODE": (0x9000, 0xF000)}
    straddle = _image({0xDFFE: b"\xB9\x43\x22"})
    ins = mb.pce_edge_instructions(straddle, segs, [0x9000])
    check("an lda abs,y at $DFFE is found spanning the edge",
          (0xDFFE, 3) in ins and any(a < 0xE000 < a + n for a, n in ins), ins[-3:])
    check("its pad is 2 (1 would put the opcode at $DFFF, still spanning)",
          mb.pce_edge_pad(ins) == 2, mb.pce_edge_pad(ins))
    clean = _image({0xDFFD: b"\xB9\x43\x22"})          # ends exactly at $DFFF
    check("an instruction ENDING at $DFFF needs no pad",
          mb.pce_edge_pad(mb.pce_edge_instructions(clean, segs, [0x9000])) == 0)
    block = _image({0xDFFB: b"\x73\x01\x02\x03\x04\x05\x06"})   # TII, 7 bytes
    check("a 7-byte block move across the edge is priced (pad 5 moves it past)",
          mb.pce_edge_pad(mb.pce_edge_instructions(block, segs, [0x9000])) == 5,
          mb.pce_edge_pad(mb.pce_edge_instructions(block, segs, [0x9000])))
    data = {"RODATA": (0x8000, 0xE800), "CODE": (0xE801, 0xF000)}
    check("the edge inside DATA is no hazard (nothing decoded)",
          mb.pce_edge_instructions(straddle, data, [0xE801]) == [])
    check("the EDGEPAD segment goes right before CODE",
          "EDGEPAD:  load = ROM,             type = ro,  optional = yes;\n    CODE:"
          in (mb.pce_cfg_with_edgepad(open(os.path.join(
              os.path.dirname(_cc65_bin()), "cfg", "pce.cfg")).read()) or "")
          if os.path.isfile(os.path.join(os.path.dirname(_cc65_bin()), "cfg", "pce.cfg"))
          else True)


MAIN_C = r"""
#include <stdint.h>
extern uint8_t probe(void);
uint8_t table[4];
uint8_t mark[8];
%s
void main(void) {
    table[0] = 0x11; table[1] = 0x42; table[2] = 0x33;
    mark[2] = probe();
    mark[0] = 0xA5; mark[1] = 0x5A; mark[4] = 0xC3; mark[5] = 0x3C;
    for (;;) { }
}
"""
PROBE_S = ("        .export _probe\n        .import _table\n        .segment \"%s\"\n"
           "_probe: ldy #1\n        lda _table,y\n        ldx #0\n        rts\n")


def _labels(path):
    out = {}
    for line in open(path):
        p = line.split()
        if len(p) >= 3:
            out[p[2].lstrip(".")] = int(p[1], 16)
    return out


def _load(rom, mark):
    """What the probe loaded (run in a CHILD: a libretro core is a singleton)."""
    r = subprocess.run([sys.executable, os.path.abspath(__file__), "--run", rom, str(mark)],
                       capture_output=True, text=True)
    m = [ln for ln in r.stdout.splitlines() if ln.startswith("MARK ")]
    b = bytes.fromhex(m[0][5:]) if m else b""
    return b[2] if len(b) == 6 and b[0] == 0xA5 and b[4] == 0xC3 else None


def _run(rom, mark):
    sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
    import run_lynx                                        # noqa: F401 (the NULL-frame patch)
    from libretro import SessionBuilder, RETRO_MEMORY_SYSTEM_RAM
    from libretro.drivers.path import ExplicitPathDriver
    lib = os.path.join(ROOT, "emu", "libretro")
    core = _core()
    b = (SessionBuilder.defaults(core).with_content(rom)
         .with_paths(ExplicitPathDriver(corepath=core, system=lib, save=lib, assets=lib,
                                        playlist=lib)).with_perf(None))
    with b.build() as sess:
        for _ in range(30):
            sess.run()
        import ctypes
        buf = sess.core.get_memory_data(RETRO_MEMORY_SYSTEM_RAM)
        size = sess.core.get_memory_size(RETRO_MEMORY_SYSTEM_RAM)
        ram = bytes((ctypes.c_uint8 * size).from_address(
            ctypes.cast(buf, ctypes.c_void_p).value))
    a = mark - 0x2000
    print("MARK " + ram[a:a + 6].hex())


def _rotate(path):
    with open(path, "rb") as f:
        b = f.read()
    with open(path, "wb") as f:
        f.write(b[-0x2000:] + b[:-0x2000])


def test_hazard_and_guard():
    print("\n[the hazard on the core, and the guard that removes it]")
    cl65 = _exe(_cc65_bin(), "cl65")
    if not mb.cc65_available() or not cl65 or not _core():
        print("  [SKIP] cc65 or the mednafen_pce_fast core not installed")
        return
    try:
        import libretro  # noqa: F401
    except ImportError:
        print("  [SKIP] libretro.py not installed")
        return
    stock = open(os.path.join(os.path.dirname(_cc65_bin()), "cfg", "pce.cfg")).read()
    tmp = tempfile.mkdtemp(prefix="pce_edge_")
    try:
        # the probe's load, hand-placed: control far from the edge, then ON it
        pinned = stock.replace("    CODE:     load = ROM,             type = ro;\n",
                               "    CODE:     load = ROM,             type = ro;\n"
                               "    PROBE:    load = ROM,             type = ro,  start = $DFFC;\n")
        results = {}
        for name, seg, cfgtext in (("control", "CODE", stock), ("edge", "PROBE", pinned)):
            d = os.path.join(tmp, name)
            os.makedirs(d)
            open(os.path.join(d, "p.cfg"), "w").write(cfgtext)
            open(os.path.join(d, "main.c"), "w").write(MAIN_C % "")
            open(os.path.join(d, "probe.s"), "w").write(PROBE_S % seg)
            r = subprocess.run([cl65, "-t", "pce", "-O", "-C", "p.cfg", "-Wl",
                                "-D__CARTSIZE__=$8000", "-Ln", "p.lbl", "-o", "p.pce",
                                "main.c", "probe.s"], cwd=d, capture_output=True, text=True)
            if r.returncode:
                check("%s probe links" % name, False, (r.stdout + r.stderr)[-300:])
                return
            lbl = _labels(os.path.join(d, "p.lbl"))
            _rotate(os.path.join(d, "p.pce"))
            results[name] = (_load(os.path.join(d, "p.pce"), lbl["_mark"]), lbl["_probe"])
        print("    control load: %s   load at $%04X: %s" % (
            results["control"][0], results["edge"][1] + 2, results["edge"][0]))
        check("CONTROL: the load away from the edge reads 0x42", results["control"][0] == 0x42,
              results["control"])
        check("THE HAZARD: the same load with its operand at $DFFF/$E000 reads WRONG "
              "(if this fails, the core is fixed and the guard may go)",
              results["edge"][1] + 2 == 0xDFFE and results["edge"][0] not in (None, 0x42),
              results["edge"])

        # the guard on a normally linked program whose load lands on $DFFE:
        # size a RODATA filler (linked before CODE) from a first, unfilled link
        d = os.path.join(tmp, "guard")
        os.makedirs(d)
        open(os.path.join(d, "probe.s"), "w").write(PROBE_S % "CODE")

        def link_plain(fill, out):
            open(os.path.join(d, "main.c"), "w").write(
                MAIN_C % ("const uint8_t fill[%d] = {1};\nuint8_t use_fill(void) "
                          "{ return fill[%d]; }" % (fill, fill - 1) if fill else ""))
            r = subprocess.run([cl65, "-t", "pce", "-O", "-Wl", "-D__CARTSIZE__=$8000",
                                "-Ln", out + ".lbl", "-o", out, "main.c", "probe.s"],
                               cwd=d, capture_output=True, text=True)
            return _labels(os.path.join(d, out + ".lbl")) if not r.returncode else None

        base = link_plain(0, "base.pce")
        probe0 = base["_probe"] + 2                 # the lda, after `ldy #1`
        fill = 0
        for _ in range(4):                          # a filler also adds use_fill(): iterate
            fill += 0xDFFE - probe0
            lbl = link_plain(fill, "plain.pce")
            probe0 = lbl["_probe"] + 2
            if probe0 == 0xDFFE:
                break
        check("the fixture puts the probe's load at $DFFE in a plain link",
              probe0 == 0xDFFE, "$%04X" % probe0)
        if probe0 != 0xDFFE:
            return
        _rotate(os.path.join(d, "plain.pce"))
        plain = _load(os.path.join(d, "plain.pce"), lbl["_mark"])
        b = mb.MosaikBuilder()
        rom = os.path.join(d, "guard.pce")
        ok = b._link_pce_guarded([os.path.join(d, "main.c"), os.path.join(d, "probe.s")],
                                 rom, ["-Wl", "-D__CARTSIZE__=$8000"], True, stock,
                                 rom + ".cfg")
        check("the guarded link succeeds and pads", ok and os.path.isfile(rom + ".cfg"))
        if not ok:
            return
        glbl = _labels(rom + ".lbl")
        with open(rom, "rb") as f:
            linear = f.read()
        segs = {"CODE": (0x8000, 0xFFF5)}
        ins = mb.pce_edge_instructions(linear, segs, sorted(set(glbl.values())))
        check("no instruction spans the edge in the guarded image",
              not [a for a, n in ins if a < 0xE000 < a + n])
        mb.MosaikBuilder._fixup_pce_image(rom)
        guarded = _load(rom, glbl["_mark"])
        print("    the program linked plainly loads %s, guarded (probe at $%04X) %s"
              % (plain, glbl["_probe"] + 2, guarded))
        check("PLAIN: the program misloads (the fixture really straddles)",
              plain not in (None, 0x42), plain)
        check("GUARDED: the same program loads 0x42", guarded == 0x42, guarded)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--run"]:
        _run(sys.argv[2], int(sys.argv[3]))
        sys.exit(0)
    print("PC Engine bank-edge checks")
    print("=" * 50)
    test_oplen()
    test_decoder()
    test_hazard_and_guard()
    print("\n%d passed, %d failed" % (passed, failed))
    if failed:
        print("FAILED")
        sys.exit(1)
