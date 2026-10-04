#!/usr/bin/env python3
"""Per-INSTRUCTION profile of named functions on the Game Boy (PyBoy).

The stage and inner-call splits (`framebudget.py`, `window_split.py`) charge
a span between two hooks to whatever call precedes the unhooked work, and
three measuring sessions in a row were misled by exactly that. This tool hooks EVERY instruction address of the functions you name,
so every cycle inside them is attributed to the instruction that spent it -
and, through the assembler listing, to the C source line it came from:

  self   T-cycles the instruction itself is listed at (sdasgb's `[n]`) times
         its hit count - the function's own work;
  gap    T-cycles between this hit and the next hooked one beyond the listed
         cost - a CALL out of the function (or an interrupt landing here).

How the addresses are found: the TU defining the function is compiled to
assembly with the build's own `lcc -S`, assembled with `sdasgb -l` for the
listing, and the listing's area-relative offsets are rebased on the label's
address from the `.noi` (`.noi` = `(bank << 16) | addr`). That is exact
within one function and needs no relink.

ONLY USE IT ON HOME-BANK (bank 0) FUNCTIONS. Instruction hooks on addresses
inside a SWITCHABLE bank become pathological once the scene switches banks
heavily - PyBoy has to re-resolve them across every SWITCH_ROM, and a busy
room does thousands per frame. Measured 2026-09-06 on the reference-engine sample
conversion's shooter room:
`vm_core_rpn_eval` (bank 0, 353 hooked instructions) profiles 40 game frames
in 8 s, while `vm_canim_draw_player` (bank 14, 452 instructions) does not
finish in 300 s - and the SAME banked function over the title screen, which
barely switches banks, finishes in 5 s. It is not the frame count and not
the function size. For a BANKED function use source-level markers with
`window_split.py --seq` instead.

Usage:
  addr_profile.py --build <project>/build/gameboy
      --funcs _vm_projectile_render__bimpl,_vm_canim_draw_player
      [--room N] [--frames 600] [--hold right] [--fire] [--top 25]
      [--rom ...] [--noi ...]

`--build` is REQUIRED (the directory holding the generated .c files and the
ROM + .noi pair; `tools/framebudget/build_noi.py <project>` writes both).
`--rom` / `--noi` default to the `-noi.gb` / `-noi.noi` pair in it, else the
single `.gb` / `.noi` there. Without `--room` the ROM's own start scene is
profiled.
"""
import glob
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict


def arg(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BUILD = arg("--build")
if not BUILD or not os.path.isdir(BUILD):
    print(__doc__)
    print("!! --build <project>/build/gameboy is required (got %r)" % BUILD)
    sys.exit(2)


def _pick(ext):
    # Prefer the relinked pair (`-noi`): an ordinary build's symbol file may
    # be stale against its ROM (see build_noi.py).
    pair = sorted(glob.glob(os.path.join(BUILD, "*-noi" + ext)))
    one = sorted(glob.glob(os.path.join(BUILD, "*" + ext)))
    return (pair or one or [None])[0]


ROM = arg("--rom") or _pick(".gb")
NOI = arg("--noi") or _pick(".noi")
if not (ROM and NOI):
    print("!! no ROM / .noi pair in %s - pass --rom and --noi" % BUILD)
    sys.exit(2)
ROOM = arg("--room", "none")
# `--room none` (the default): profile the ROM's own start scene (a program
# with one room has no room-change seam worth poking).
ROOM = None if ROOM == "none" else int(ROOM)
FRAMES = int(arg("--frames", 600))
HOLD = arg("--hold")
FIRE = "--fire" in sys.argv
TOP = int(arg("--top", 25))
FUNCS = [f for f in (arg("--funcs", "") or "").split(",") if f]
GBDK = os.environ.get("GBDK_HOME") or os.path.join(ROOT, "gbdk")
if not os.path.exists(os.path.join(GBDK, "bin", "lcc.exe")) and not os.path.exists(os.path.join(GBDK, "bin", "lcc")):
    GBDK = os.path.join(ROOT, "gbdk")

syms = {}
for line in open(NOI, encoding="utf-8", errors="replace"):
    m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
    if m:
        syms[m.group(1)] = int(m.group(2), 16)


def tu_of(func):
    """The .c file DEFINING func (a body, not a prototype)."""
    cname = func.lstrip("_")
    # a definition line: the name, an argument list and the opening brace on
    # ONE line with no semicolon (a prototype ends in one). Line by line - a
    # multi-line regex over a 200 KB TU backtracks for minutes.
    pat = re.compile(r"\b" + re.escape(cname) + r"\s*\(.*\)\s*(BANKED\s*)?\{\s*$")
    for c in sorted(glob.glob(os.path.join(BUILD, "*.c"))):
        for line in open(c, encoding="utf-8", errors="replace"):
            if ";" not in line and pat.search(line):
                return c
    raise SystemExit("no TU defines %s" % func)


_listings = {}


def listing_of(c):
    """Assemble the TU and return the parsed listing: a list of
    (offset, cycles, text, srcline) for every instruction, plus a dict of
    label -> offset."""
    if c in _listings:
        return _listings[c]
    td = tempfile.mkdtemp(prefix="addrprof_")
    asm = os.path.join(td, "tu.asm")
    lcc = os.path.join(GBDK, "bin", "lcc")
    cmd = [lcc, "-msm83:gb", "-S", "-Wm-yt0x1B",
           "-I" + os.path.join(ROOT, "vendor", "hugedriver"),
           "-I" + os.path.join(GBDK, "include"), "-o", asm, c]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not os.path.exists(asm):
        raise SystemExit("lcc -S failed:\n" + r.stderr[-2000:])
    rel = os.path.join(td, "tu.rel")
    r = subprocess.run([os.path.join(GBDK, "bin", "sdasgb"), "-plosgff", "-l",
                        "-o", rel, asm], capture_output=True, text=True)
    lst = os.path.join(td, "tu.lst")
    if not os.path.exists(lst):
        raise SystemExit("sdasgb -l failed:\n" + r.stderr[-2000:])
    instrs, labels = [], {}
    src = ""
    for line in open(lst, encoding="utf-8", errors="replace"):
        # "   00000F7D E8 F6            [16] 3473 \tadd\tsp, #-10"
        m = re.match(r"\s+([0-9A-F]{8})\s+(.+?)\s+\[\s*(\d+)\]\s+\d+\s+(.*)$", line)
        if m:
            instrs.append((int(m.group(1), 16), int(m.group(3)), m.group(4).strip(), src))
            continue
        m = re.match(r"\s+([0-9A-F]{8})\s+\d+\s+([A-Za-z_][A-Za-z0-9_$]*):{1,2}\s*$", line)
        if m:
            labels[m.group(2)] = int(m.group(1), 16)
            continue
        m = re.match(r"\s+\d+\s+;.*?\.c:(\d+):\s?(.*)$", line)
        if m:
            src = "%s: %s" % (m.group(1), m.group(2).strip()[:70])
    _listings[c] = (instrs, labels)
    return _listings[c]


# --- build the address map -------------------------------------------------
addr_info = {}          # (bank, addr) -> (func, cycles, text, src)
for spec in FUNCS:
    # `name` = a global (in the .noi); `static@global` = a STATIC helper in
    # the TU that defines `global` (per-TU prelude helpers such as
    # gbs_set_metasprite_mask exist once per TU and have no .noi symbol),
    # rebased from that global's address: same area, so the offset
    # difference in the listing is the address difference in ROM.
    func, anchor = (spec.split("@", 1) + [None])[:2] if "@" in spec else (spec, spec)
    if anchor not in syms:
        print("!! no symbol", anchor)
        continue
    c = tu_of(anchor)
    instrs, labels = listing_of(c)
    if func not in labels or anchor not in labels:
        print("!! label %s / %s not in the listing of %s" % (func, anchor, os.path.basename(c)))
        continue
    base_off = labels[func]
    # the function ends at the next label after it
    ends = sorted(o for o in labels.values() if o > base_off)
    end_off = ends[0] if ends else base_off + 4096
    abs_base = (syms[anchor] & 0xFFFF) + (base_off - labels[anchor])
    bank = syms[anchor] >> 16
    n = 0
    for off, cyc, text, src in instrs:
        if base_off <= off < end_off:
            addr_info[(bank, abs_base + (off - base_off))] = (spec, cyc, text, src)
            n += 1
    print("%s: %d instructions at bank %d 0x%04X..0x%04X (%s)"
          % (spec, n, bank, abs_base, abs_base + end_off - base_off, os.path.basename(c)))

_banked = sorted({addr_info[k][0]: k[0] for k in addr_info}.items(),
                 key=lambda t: t[0])
_hot = [(f, b) for f, b in _banked if b != 0]
if _hot:
    print("!! %s in ROM bank(s) %s - see the module docstring: instruction "
          "hooks in a SWITCHABLE bank are pathologically slow in a room that "
          "switches banks (room 8 does not finish; the title screen does). "
          "Profile a home-bank function, or split a banked one with "
          "window_split.py --seq."
          % (", ".join(f for f, _b in _hot),
             ", ".join(str(b) for _f, b in _hot)))

from pyboy import PyBoy
pb = PyBoy(ROM, window="null")
pb.set_emulation_speed(0)
g = lambda n: syms[n] & 0xFFFF

rec = [False]
gf = [0]
nev = [0]
# AGGREGATE AS THE HITS ARRIVE. The first cut appended every hooked hit to a
# list and attributed afterwards; at 120 game frames of a function stepping
# every frame that list reached 20 GB per run (2026-09-06) and the runs
# never finished. Every span only needs the PREVIOUS hooked event, so it is
# charged the moment the next one lands.
prev = [None, 0]        # (key, cycles) of the previous hooked event
hits = defaultdict(int)
selfc = defaultdict(int)
gapc = defaultdict(int)
isr_in = defaultdict(int)


def _event(key):
    c = pb._cycles()
    nev[0] += 1
    pk, pc = prev[0], prev[1]
    if pk is not None and pk in addr_info:
        cyc = addr_info[pk][1]
        hits[pk] += 1
        selfc[pk] += cyc
        extra = (c - pc) - cyc
        if extra > 0:
            if key in ISRS:
                isr_in[pk] += extra
            else:
                gapc[pk] += extra
    prev[0] = key
    prev[1] = c


def mk(key):
    def hit(_c):
        if rec[0]:
            _event(key)
    return hit


for (bank, addr) in addr_info:
    pb.hook_register(bank, addr, mk((bank, addr)), None)
# frame counter + ISR entries (so an interrupt landing inside a span is named)
ISRS = [n for n in ("_gbs_huge_isr", "_gbs_win_vbl_isr", "_gbs_px_vbl_isr",
                    "_gbs_px_lcd_isr", "_gbs_win_lcd_isr") if n in syms]
for n in ISRS:
    v = syms[n]
    pb.hook_register(v >> 16, v & 0xFFFF, mk(n), None)


def on_frame(_c):
    if rec[0]:
        gf[0] += 1
        _event("frame")


v = syms["_vm_core_run_scripts"]
pb.hook_register(v >> 16, v & 0xFFFF, on_frame, None)


def w16(a, val):
    pb.memory[a], pb.memory[a + 1] = val & 0xFF, (val >> 8) & 0xFF


for _ in range(120):
    pb.tick()
if ROOM is not None:
    pb.memory[g("_vm_core_pend_code")] = 2
    pb.memory[g("_vm_core_pend_a")] = ROOM
    w16(g("_vm_core_pend_b"), 40)
    w16(g("_vm_core_pend_c"), 72)
for _ in range(300):
    pb.tick()
if HOLD:
    pb.button_press(HOLD)
for _ in range(30):
    pb.tick()
rec[0] = True
for t in range(FRAMES):
    if FIRE:
        k = gf[0]
        if k % 10 == 0:
            pb.button_press("a")
        elif k % 10 == 5:
            pb.button_release("a")
    pb.tick()
rec[0] = False
pb.stop(save=False)
print("events: %d over %d game frames" % (nev[0], gf[0]))
_top = sorted(hits.items(), key=lambda t: -t[1])[:3]
print("top addresses:", [("%X" % k[1], n) for k, n in _top])

# --- report ----------------------------------------------------------------
n = max(gf[0], 1)
print("\nroom %s %s: %d game frames" % (ROOM, "fire" if FIRE else HOLD or "idle", n))
for func in FUNCS:
    keys = [k for k in addr_info if addr_info[k][0] == func]
    tot_self = sum(selfc[k] for k in keys)
    tot_gap = sum(gapc[k] for k in keys)
    tot_isr = sum(isr_in[k] for k in keys)
    print("\n== %s: self %.0f + calls %.0f (+ isr landing %.0f) = %.0f T-cycles per game frame"
          % (func, tot_self / n, tot_gap / n, tot_isr / n, (tot_self + tot_gap) / n))
    # by source line
    by_src = defaultdict(lambda: [0, 0, 0])
    for k in keys:
        s = addr_info[k][3] or "(no source)"
        by_src[s][0] += selfc[k]
        by_src[s][1] += gapc[k]
        by_src[s][2] = max(by_src[s][2], hits[k])
    rows = sorted(by_src.items(), key=lambda t: -(t[1][0] + t[1][1]))[:TOP]
    print("   %8s %8s %6s  source line" % ("self", "calls", "hits"))
    for s, (sc, gc, h) in rows:
        print("   %8.0f %8.0f %6.2f  %s" % (sc / n, gc / n, h / n, s))
    # call sites
    calls = sorted(((gapc[k], k) for k in keys if gapc[k] > 0), reverse=True)[:8]
    if calls:
        print("   call sites (gap = cycles spent in the callee):")
        for gc, k in calls:
            _f, cyc, text, src = addr_info[k]
            print("   %8.0f  x%.2f  %-28s  <- %s" % (gc / n, hits[k] / n, text[:28], src[:60]))
