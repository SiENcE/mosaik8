#!/usr/bin/env python3
"""THE W7d GATE: the recorded LYC defects, re-run on a real ROM.

`LYC_REG` has THREE tenants on the GB family - the scanline parallax bands,
the dialogue box's sprite cut and (since phase 2) the OVERLAY CUT - and before
the merge the first two kept the peace by standing down for each other. Each
of those guards was added after a whole-screen defect found by PLAYING a ROM,
and neither is provable offscreen or by a green suite:

  1. a box OPENING in a parallax room. GBDK chains LCD handlers, so the cut's
     ISR fired at EVERY band boundary, line 0 included, and its HIDE_SPRITES
     blanked every sprite for the whole visible frame;
  2. a box CLOSING there. `STAT_REG &= ~STATF_LYC` disables the LYC interrupt
     for the whole machine, so the band chain died PERMANENTLY while the bands
     stayed armed in software and every one of them drew at the full camera.

Four phases, and every check reads a rendered frame or the stop list the ROM
actually holds:

  [A] bands only     - three marker columns at three x, no sprite ever cut;
  [B] box open       - the cut adds exactly ONE stop, at the window's own
                       line, and the bands still draw at three scrolls;
  [C] box closed     - the cut's stop is gone, the chain is still alive;
  [D] overlay cut    - a THIRD tenant, armed INSIDE the open box: one more
                       stop, sorted into the same list, and past it the box's
                       paper stops, the room draws and the sprite comes back.

Defect 1 is "[B] blanks the sprite for the whole frame"; defect 2 is "[C] has
one scroll, not three". Both are now unexpressible: one handler owns the
register and a feature that disarms contributes no stop rather than reaching
for `STATF_LYC`. [D]'s own mutation is "the ISR ignores GBS_LYC_WINOFF", which
leaves the box whole below the cut and fails three of its checks.

Usage: verify.py [--rom PATH] [--sym PATH]
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
ROM = os.path.join(HERE, "build", "gameboy", "lyc-merge-lab.gb")
SYM = os.path.join(HERE, "build", "gameboy", "lyc-merge-lab.noi")

SPRITE_Y = 128          # where main.mos parks the one object
BOX_ROWS = 4            # ...and how tall the box is
CUT_LINE = 128          # ...and where START arms the OVERLAY CUT (gen_main.py)
# The overlay cut takes hold on the line AFTER the one LYC names, and that is
# PARITY, not a defect: the handler spins for H-blank before writing LCDC (as
# the reference VM's own simple_LCD_isr does), which is the END of the named line. So the
# window is still whole on CUT_LINE itself and gone from CUT_LINE + 1.
CUT_EFFECT = CUT_LINE + 1

_ok = [True]


def check(cond, what, detail=""):
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", what,
                           ("  -- " + detail) if detail else ""))
    _ok[0] = _ok[0] and bool(cond)


def symbols(noi):
    out = {}
    for line in open(noi, encoding="utf-8", errors="replace"):
        p = line.split()
        if len(p) == 3 and p[0] == "DEF":
            try:
                out[p[1]] = int(p[2], 16)
            except ValueError:
                pass
    return out


def build(proj):
    """Build the ROM and relink it with -Wl-j so the .noi carries every symbol.

    A plain build's .noi is written without local symbols AND is not rewritten
    when only the link changes, so reusing one silently hooks the wrong
    address. REBUILD, never reuse: a verify that only builds when its ROM is
    absent reports old behaviour as current.
    """
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "gameboy", proj],
                       capture_output=True, text=True, cwd=ROOT)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:])
        return False
    line = [l for l in r.stdout.splitlines() if "lcc" in l and "-o" in l]
    if line:
        import shlex
        argv = shlex.split(line[-1].split(": ", 1)[1], posix=False)
        subprocess.run(argv + ["-Wl-j"], capture_output=True, text=True, cwd=ROOT)
    return True


def phases(rom, sym):
    """Drive the four phases against one built ROM."""
    from pyboy import PyBoy

    s = symbols(sym)
    for need in ("_gbs_lyc_isr", "_gbs_lyc_vbl", "_gbs_lyc_n",
                 "_gbs_lyc_line", "_gbs_lyc_act", "_gbs_cut_on",
                 "_gbs_ocut_on", "_gbs_ocut_line", "_gbs_win_want"):
        if need not in s:
            print("FAILED: %s is not in the .noi - this ROM was not built with "
                  "the merged LYC owner" % need)
            _ok[0] = False
            return

    pb = PyBoy(rom, window="null")
    g = lambda n: s[n] & 0xFFFF
    fires, vbl_obj, rec = [], [], [False]

    def on_isr(_c):
        if rec[0]:
            fires.append(pb.memory[0xFF44])                 # LY_REG

    def on_vbl(_c):
        if rec[0]:
            vbl_obj.append((pb.memory[0xFF40] >> 1) & 1)    # LCDC OBJ enable

    for name, cb in (("_gbs_lyc_isr", on_isr), ("_gbs_lyc_vbl", on_vbl)):
        a = s[name]
        pb.hook_register(a >> 16, a & 0xFFFF, cb, None)

    def stops():
        n = pb.memory[g("_gbs_lyc_n")]
        ln, ac = g("_gbs_lyc_line"), g("_gbs_lyc_act")
        return [(pb.memory[ln + i], pb.memory[ac + i]) for i in range(n)]

    def sample(frames=60):
        fires.clear()
        vbl_obj.clear()
        rec[0] = True
        for _ in range(frames):
            pb.tick()
        rec[0] = False
        # Per-scanline SCX: three bands means three scrolls on ONE frame,
        # which a single scroll register cannot produce.
        scx = [r[0] for r in pb.screen.tilemap_position_list[:144]]
        # The ISR entry hook lands a line or two after the coincidence, so
        # fold neighbours together before counting distinct stops.
        seen = sorted(set(fires))
        merged = []
        for v in seen:
            if not merged or v - merged[-1] > 2:
                merged.append(v)
        return merged, sum(1 for b in vbl_obj if b == 0), scx

    def rows(y0, y1):
        """The set of distinct screen COLOURS in scanlines [y0, y1).

        Colours, not shades: PyBoy renders RGB and the four DMG greys are not
        worth naming here. Every claim below is a COMPARISON between two
        ranges of the same frame, so an absolute value is never needed - and
        the lab is drawn so that the comparison is decisive: the box's paper
        is one colour, the field another, a band marker a third, and the
        sprite wears a fourth that nothing else on screen does.
        """
        px = pb.screen.ndarray
        out = set()
        for y in range(y0, y1):
            for p in px[y]:
                out.add(tuple(int(v) for v in p[:3]))
        return out

    def press_until(btn, want, limit=180, flag="_gbs_cut_on"):
        """HOLD `btn` until the ROM's own `flag` reaches `want`.

        Not a fixed-length tap. A press has to survive into the game loop's
        own edge test, and how many ticks that takes depends on where in the
        frame the press lands - a 6-frame tap opened the box in one session
        and was missed entirely in the next, which is a probe that reports a
        working ROM as broken. Holding until the ROM says it saw it removes
        the timing from the measurement; the check then has a real subject.
        """
        addr = g(flag)
        pb.button_press(btn)
        for _ in range(limit):
            pb.tick()
            if pb.memory[addr] == want:
                break
        pb.button_release(btn)
        for _ in range(10):
            pb.tick()
        return pb.memory[addr] == want

    for _ in range(120):
        pb.tick()

    # ---- [A] the bands, with no box ----------------------------------
    stops_a, hidden_a, scx_a = sample()
    print("\n[A] three parallax bands, no box")
    print("    stop list: %s" % stops())
    print("    the ISR fires at %s" % stops_a)
    check(len(stops_a) >= 3, "the band chain fires once per band",
          "fired at %s" % stops_a)
    check(len(set(scx_a)) >= 3, "three bands draw at three different scrolls",
          "%d distinct SCX down the screen" % len(set(scx_a)))
    check(hidden_a == 0, "nothing cuts the sprite while no box is open",
          "%d frames had OBJ off at v-blank" % hidden_a)

    # ---- [B] a box open over them ------------------------------------
    # The expected cut line comes from the LAB'S OWN GEOMETRY, not from a
    # register read: main.mos opens a BOX_ROWS-tall box on the window layer,
    # which puts WY at SCREEN_HEIGHT - BOX_ROWS*8, and the reference engine's rule (which
    # gbs_text_win_cut copies) is to fire one line ABOVE that.
    cut_line = 144 - BOX_ROWS * 8 - 1
    armed = press_until("a", 1)
    stops_b, hidden_b, scx_b = sample()
    lst = stops()
    print("\n[B] the box is open (a %d-row box, so the cut belongs at line %d)"
          % (BOX_ROWS, cut_line))
    print("    stop list: %s" % lst)
    print("    the ISR fires at %s" % stops_b)
    check(armed and (pb.memory[0xFF40] & 0x20) != 0,
          "the box is up (LCDC window enable) and the cut is armed",
          "LCDC=0x%02X cut_on=%d" % (pb.memory[0xFF40], pb.memory[g("_gbs_cut_on")]))
    hides = [ln for ln, act in lst if act & 0x10]
    check(len(hides) == 1,
          "DEFECT 1: the cut is ONE stop, not one per band boundary",
          "stops that hide: %s" % hides)
    check(hides == [cut_line],
          "...and it is at the window's own line", "%s vs %d" % (hides, cut_line))
    check(hidden_b > 0, "the sprite IS cut under the box now",
          "%d of %d frames" % (hidden_b, 60))
    check(len(set(scx_b)) >= 3,
          "DEFECT 1: the bands keep drawing while the box is up",
          "%d distinct SCX" % len(set(scx_b)))
    above = scx_b[:cut_line]
    check(len(set(above)) >= 2, "...including above the cut line",
          "%d distinct SCX above line %d" % (len(set(above)), cut_line))

    # ---- [C] and closed again ----------------------------------------
    closed = press_until("b", 0)
    stops_c, hidden_c, scx_c = sample()
    lst_c = stops()
    print("\n[C] the box has closed")
    print("    stop list: %s" % lst_c)
    print("    the ISR fires at %s" % stops_c)
    check(closed and not [1 for _l, a in lst_c if a & 0x10],
          "the cut's stop went with the box", "list=%s" % lst_c)
    check(len(stops_c) >= 3,
          "DEFECT 2: the band chain SURVIVED the box closing",
          "fired at %s" % stops_c)
    check(len(set(scx_c)) >= 3,
          "DEFECT 2: the bands still draw at three scrolls, not one",
          "%d distinct SCX" % len(set(scx_c)))
    check(sorted(set(scx_c)) == sorted(set(scx_a)) or len(set(scx_c)) >= 3,
          "...the same three the room had before the box")
    check(hidden_c == 0, "and the sprite is back for good",
          "%d frames had OBJ off at v-blank" % hidden_c)

    # ---- [D] the OVERLAY CUT, a THIRD tenant (W7d phase 2) ------------
    #
    # Re-open the box and arm the cut INSIDE it. Everything here is read off
    # the rendered frame by COMPARING two ranges of it: an OBJ-enable bit
    # sampled at v-blank cannot see this feature at all, because the cut takes
    # the sprites away at line 111 and gives them back at CUT_LINE, both
    # within one frame, and LCDC is whatever the last write left.
    reopened = press_until("a", 1)
    over_box = rows(CUT_EFFECT, 144)          # the box, with no cut yet
    armed = press_until("start", 1, flag="_gbs_ocut_on")
    stops_d, _hidden_d, scx_d = sample()
    lst_d = stops()
    print("\n[D] the overlay cut is armed at line %d" % CUT_LINE)
    print("    stop list: %s" % lst_d)
    print("    the ISR fires at %s" % stops_d)
    check(reopened and armed, "the box is open again and the cut is armed",
          "cut_on=%d ocut_on=%d" % (pb.memory[g("_gbs_cut_on")],
                                    pb.memory[g("_gbs_ocut_on")]))
    winoff = [ln for ln, act in lst_d if act & 0x20]
    check(winoff == [CUT_LINE],
          "the overlay cut is ONE stop, at the line it was given",
          "%s vs [%d]" % (winoff, CUT_LINE))
    check(lst_d == sorted(lst_d),
          "...merged into the SAME list, in scanline order", "%s" % lst_d)
    check(len(lst_d) == 5 and [ln for ln, a in lst_d if a & 0x10] == [111],
          "...beside the box's own cut, which still has its own stop",
          "%s" % lst_d)
    # The box's paper is one colour and covers rows 112..143 with no cut.
    # Past the cut the WINDOW is off, so the scrolling field shows through -
    # more than one colour, and different ones.
    under = rows(CUT_EFFECT, 144)
    check(len(over_box) == 1,
          "with no cut the box's paper reaches the bottom of the screen",
          "%d colour(s) below line %d" % (len(over_box), CUT_EFFECT))
    check(len(under) >= 2 and under != over_box,
          "the window goes OFF at the cut: the room draws below it",
          "%d colour(s), %s" % (len(under), sorted(under)))
    check(len(rows(112, CUT_LINE + 1)) >= 1
          and rows(112, CUT_LINE + 1) >= over_box,
          "...and the box is untouched ABOVE it",
          "%s" % sorted(rows(112, CUT_LINE + 1)))
    # ...and the SPRITES come back there. The object is parked at SPRITE_Y in
    # a colour nothing else on screen wears, so "the extra colour appears only
    # in the object's own eight rows" is the whole claim in one comparison.
    at_sprite = rows(CUT_EFFECT, SPRITE_Y + 8)
    below = rows(SPRITE_Y + 8, 144)
    check(at_sprite > below,
          "DEFECT 3: the sprite comes BACK below the cut",
          "%s over %s" % (sorted(at_sprite - below), sorted(below)))
    check(len(set(scx_d)) >= 3,
          "the third tenant did not disturb the other two",
          "%d distinct SCX" % len(set(scx_d)))

    # Disarming puts the box back whole, and takes the stop with it.
    off = press_until("select", 0, flag="_gbs_ocut_on")
    sample(20)
    lst_e = stops()
    check(off and not [1 for _l, a in lst_e if a & 0x20],
          "disarming the cut removes its stop", "list=%s" % lst_e)
    check(rows(CUT_EFFECT, 144) == over_box,
          "...and the box covers the bottom of the screen again",
          "%s" % sorted(rows(CUT_EFFECT, 144)))
    check((pb.memory[0xFF40] & 0x20) != 0,
          "...with the window layer given back, not left off",
          "LCDC=0x%02X" % pb.memory[0xFF40])

    pb.stop(save=False)


def placement(sym, banked):
    """Where the LYC machinery LINKED (W7d phase 2).

    The handlers vector from hardware and cannot be banked; the state is WRAM,
    which is not banked at all; the arbiter runs on an arm or a disarm and
    should leave bank 0 wherever the build can take it. A `.noi` address is
    `(bank << 16) | addr`, so the bank is the top half.

    This is not decoration. The first version of the banked arm left
    `gbs_lyc_rebuild` spelled `static` inside the bank TU: sdcc dropped the
    unused static, the linker called it an "Undefined Global" WARNING, exited
    0, and wrote a ROM whose box-open path jumps to address 0. The phases
    below would have caught it here, because the lab opens a box - but only
    here, and only by crashing.
    """
    s = symbols(sym)
    bank = lambda n: s[n] >> 16
    for n in ("_gbs_lyc_isr", "_gbs_lyc_vbl"):
        check(n in s and bank(n) == 0,
              "%s is in the home bank (a hardware vector cannot be banked)" % n,
              "bank %s" % (bank(n) if n in s else "?"))
    for n in ("_gbs_lyc_n", "_gbs_lyc_line", "_gbs_lyc_act", "_gbs_cut_on",
              "_gbs_ocut_on", "_gbs_ocut_line", "_gbs_win_want"):
        check(n in s and 0xC000 <= (s[n] & 0xFFFF) < 0xE000,
              "%s is the one WRAM copy of the stop list" % n,
              "0x%04X" % (s[n] & 0xFFFF) if n in s else "absent")
    for n in ("_gbs_lyc_rebuild", "_gbs_lyc_wire"):
        got = bank(n) if n in s else None
        if banked:
            # Banked, they have external linkage (the resident verbs call
            # them across the boundary), so they ARE in the .noi.
            check(n in s and got > 0, "%s is in a ROM bank" % n,
                  "bank %s" % got)
        else:
            # Resident, they are file-STATIC, and a static symbol is absent
            # from the .noi even with -Wl-j. Absent is the expected reading;
            # present-and-banked is the failure this arm exists to exclude.
            check(n not in s or got == 0,
                  "%s is not in a ROM bank (it is the resident form)" % n,
                  "static, not in the .noi" if n not in s else "bank %s" % got)


def build_banked(dst):
    """A COPY of this project that banks, so the banked arm has a subject.

    The lab itself stays a flat cart: it is a defect-hardened gate and the
    RESIDENT form of the merge is what shipped, so that arm must keep its own
    ROM. The only difference here is one `[build] code_banks` line, which is
    all `banking_active` needs - and `banking_active` is what decides whether
    `_prelude_data_bank` will take the arbiter.
    """
    import shutil
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    shutil.copytree(HERE, dst, ignore=shutil.ignore_patterns("build"))
    toml = os.path.join(dst, "mosaik.toml")
    text = open(toml, encoding="utf-8").read()
    marker = '[build]\noutput_dir = "build"'
    if marker not in text:
        return None, None
    open(toml, "w", encoding="utf-8").write(
        text.replace(marker, marker + '\ncode_banks = [ "lyc_merge_lab",]'))
    if not build(dst):
        return None, None
    return (os.path.join(dst, "build", "gameboy", "lyc-merge-lab.gb"),
            os.path.join(dst, "build", "gameboy", "lyc-merge-lab.noi"))


def main():
    rom, sym = ROM, SYM
    if "--rom" in sys.argv:
        rom = sys.argv[sys.argv.index("--rom") + 1]
    if "--sym" in sys.argv:
        sym = sys.argv[sys.argv.index("--sym") + 1]
    given = rom != ROM
    if not given and not build(HERE):
        print("FAILED: could not build lyc-merge-lab")
        return 1
    if not os.path.exists(rom):
        print("SKIP: no ROM (GBDK not installed?)")
        return 0
    try:
        import pyboy                                          # noqa: F401
    except ImportError:
        print("SKIP: PyBoy is not installed")
        return 0

    print("\n=== ARM 1: the merge RESIDENT (a flat cart) ===")
    placement(sym, banked=False)
    phases(rom, sym)

    # ARM 2 is skipped when a ROM was handed in: the caller is driving one
    # specific build (a mutation run), not the project.
    if not given:
        tmp = os.path.join(ROOT, ".scratch", "lyc-merge-lab-banked")
        brom, bsym = build_banked(tmp)
        if brom is None or not os.path.exists(brom):
            print("\n=== ARM 2: SKIPPED (the banked copy did not build) ===")
            _ok[0] = False
        else:
            print("\n=== ARM 2: the arbiter BANKED (W7d phase 2) ===")
            print("    the same program with one [build] code_banks line, so")
            print("    gbs_lyc_rebuild / gbs_lyc_wire leave the resident image")
            placement(bsym, banked=True)
            phases(brom, bsym)

    print("\n%s" % ("All LYC merge checks passed"
                    if _ok[0] else "LYC MERGE CHECKS FAILED"))
    return 0 if _ok[0] else 1


if __name__ == "__main__":
    sys.exit(main())
