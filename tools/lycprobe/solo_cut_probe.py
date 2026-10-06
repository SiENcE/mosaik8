#!/usr/bin/env python3
"""Does the SOLO box sprite cut hide sprites exactly when, and only when, a box is up?

The instrument for the solo-emitter half of the DMG STAT-write fix
(2026-09-22). A program whose only `LYC_REG`
tenant is the dialogue box's sprite cut (`_emit_win_sprite_cut_solo`, which
is every VM8 project with a box and no parallax bands) used to switch the
LYC interrupt ON at every box open and OFF at every box close, by writing
STAT. On a monochrome Game Boy each of those writes can raise a SPURIOUS
LCD interrupt, and the cut's handler then did `HIDE_SPRITES` at whatever
line the beam was on. The fix enables the source ONCE at wire time and lets
the handler stand down on a flag, which means the handler now RUNS on every
frame, box or no box - so the thing to prove on a real ROM is the stand-down.

PyBoy does not emulate the quirk (see band_truth.py), so this cannot show
the DMG defect itself; that half is pinned by source contracts in
`tests/overlay_cut_test.py` / `tests/box_geometry_test.py` /
`tests/parallax_test.py`. What it does show, per frame, on both builds of an
A/B:

  CLOSED  the window was off at this V-blank and the one before it: the
          sprites must still be ON when the V-blank restore runs (no cut
          happened with no box on screen);
  OPEN    the window was on at both: the sprites must be OFF at the restore
          (the cut happened) and every entry of the handler that frame was at
          the box's line, `WY - 1` (one line of slack for dispatch latency).

It is read at the ENTRY of the cut's own V-blank handler, before it restores,
which is the one moment the frame's LCDC OBJ bit says whether a cut fired.

`--inject` arms the cut on one CLOSED frame with no box on screen (the
solo `gbs_cut_on` flag where the build has one, else STAT's LYC enable) and
the run MUST flag it: a zero from a probe that has not shown it can fail is
not a measurement.

The addresses come from a `-Wl-j` `.noi` (`tools/framebudget/build_noi.py`),
never hardcoded.

Usage:
    solo_cut_probe.py ROM.gb NOI [--dmg] [--frames N] [--inject]
    solo_cut_probe.py --fixture [--dmg] [--inject]   (builds its own subject)

A solo-cut build that ARMS the cut at run time: a project with
`[scenes] box_hides_sprites = true` and no parallax bands. A project without
the setting (`vm-quest` and most other samples) links the cut and never arms
it, so the probe has nothing to measure there and fails on the OPEN-frame
count.

The route taps A, Start and the d-pad, so it needs a game that opens and
closes boxes over and over: the cut's V-blank handler is wired by the FIRST
box, so there is no CLOSED frame to measure before one, and `--inject` needs a
box-less stretch after it. `--fixture` builds exactly that subject: a copy of
`projects/vm-uiscroll` (the first-party `box_hides_sprites` sample, which opens
ONE box and leaves it up) whose script re-opens its box in a loop, under
`.scratch/solo-cut`. Measured 2026-10-06: ~5,000 CLOSED and ~700 OPEN frames,
0 violations, `--inject` caught 5 of 5.

Exit 1 on any violation, or if too few OPEN / CLOSED frames were seen to
measure anything.
"""
import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FIXTURE = os.path.join(ROOT, ".scratch", "solo-cut")
_LOOP = """[[script]]
name = "main"
events = [
  { event = "label", name = "again" },
  { event = "wait", frames = 120 },
  { event = "text", string = "BOX AGAIN" },
  { event = "goto", name = "again" },
]
"""


def build_fixture():
    """vm-uiscroll with its one box re-opened in a loop -> (rom, noi)."""
    shutil.rmtree(FIXTURE, ignore_errors=True)
    shutil.copytree(os.path.join(ROOT, "projects", "vm-uiscroll"), FIXTURE,
                    ignore=shutil.ignore_patterns("build"))
    with open(os.path.join(FIXTURE, "scripts", "main.evt.toml"), "w",
              encoding="utf-8", newline="\n") as f:
        f.write(_LOOP)
    for cmd in ([sys.executable, "-m", "mosaik_vm",
                 os.path.join(FIXTURE, "scripts"),
                 "-o", os.path.join(FIXTURE, "src", "scripts.mos")],
                [sys.executable, os.path.join(ROOT, "tools", "framebudget",
                                              "build_noi.py"), FIXTURE]):
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT,
                           encoding="utf-8", errors="replace")
        if r.returncode != 0:
            raise SystemExit("fixture build failed:\n%s%s"
                             % (r.stdout[-2000:], r.stderr[-2000:]))
    out = os.path.join(FIXTURE, "build", "gameboy")
    return (os.path.join(out, "vm-uiscroll.gb"),
            os.path.join(out, "vm-uiscroll.noi"))

LCDC, STAT, LY, LYC, WY = 0xFF40, 0xFF41, 0xFF44, 0xFF45, 0xFF4A
OBJ_ON, WIN_ON = 0x02, 0x20


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom", nargs="?")
    ap.add_argument("noi", nargs="?")
    ap.add_argument("--fixture", action="store_true",
                    help="build and measure the looping vm-uiscroll copy")
    ap.add_argument("--dmg", action="store_true")
    ap.add_argument("--frames", type=int, default=6000)
    ap.add_argument("--inject", action="store_true",
                    help="arm the cut on one box-less frame; the run MUST flag it")
    a = ap.parse_args()

    try:
        from pyboy import PyBoy
    except ImportError:
        print("PyBoy not installed -- skipping")
        return 0

    if a.fixture:
        a.rom, a.noi = build_fixture()
    elif not (a.rom and a.noi):
        ap.error("ROM and NOI are required (or --fixture)")
    s = symbols(a.noi)
    for need in ("_gbs_win_lcd_isr", "_gbs_win_vbl_isr", "_gbs_spr_want"):
        if need not in s:
            raise SystemExit("no %s in %s: not a solo-cut build, or a .noi "
                             "without -Wl-j" % (need, a.noi))
    cut_on = s.get("_gbs_cut_on")
    pb = PyBoy(a.rom, window="null", sound_emulated=False, cgb=not a.dmg)

    state = {"f": 0, "lcd": [], "vbl": None}

    def on_lcd(_ctx):
        state["lcd"].append((pb.memory[LY], pb.memory[WY]))

    def on_vbl(_ctx):
        # First entry this frame only (the chain calls it once per V-blank).
        if state["vbl"] is None:
            state["vbl"] = (pb.memory[LCDC], pb.memory[s["_gbs_spr_want"]],
                            pb.memory[WY])

    for name, cb in (("_gbs_win_lcd_isr", on_lcd), ("_gbs_win_vbl_isr", on_vbl)):
        addr = s[name]
        # A `.noi` carries the bank in the high word for BANKED code only. A
        # home-bank label past 0x4000 is a flat 32 KB ROM (no MBC), whose
        # upper half PyBoy calls bank 1 - hooking it as bank 0 patches the
        # wrong byte and the ROM wanders off.
        bank = addr >> 16 or (1 if addr & 0xFFFF >= 0x4000 else 0)
        pb.hook_register(bank, addr & 0xFFFF, cb, None)

    # The MASH route: a game opens its boxes from the title,
    # from intros and from interactions, so A / Start on co-prime periods plus
    # a wandering d-pad reaches both kinds of frame many times without a
    # per-project script. The totals are printed and gated below, so a route
    # that never reached a box FAILS rather than reading clean.
    pads = ("right", "down", "left", "up")
    prev_win = None
    win_at = {}
    closed = opened = lcd_on_closed = 0
    bad = []
    injections = []       # frames an inject landed on (classified CLOSED)
    closed_run = 0        # consecutive CLOSED frames: where a box is NOT near
    for f in range(a.frames):
        if f % 23 == 0:
            pb.button("a")
        if f % 97 == 0:
            pb.button("start")
        if f % 61 == 0:
            pb.button_press(pads[(f // 61) % 4])
        elif f % 61 == 30:
            pb.button_release(pads[(f // 61) % 4])

        armed_here = False
        if (a.inject and len(injections) < 5 and opened >= 20
                and closed_run >= 60):
            # Arm the cut with no box on screen, for exactly one frame, once
            # the handler has been wired (a box has been up) and only DEEP in
            # a box-less stretch: the game arms the cut itself a few frames
            # before each box shows (see `early` below), and an inject landing
            # there would be "caught" by the ROM's own arm - a self-check
            # that proves nothing. Up to five, each judged on its own.
            if cut_on is not None:
                pb.memory[cut_on] = 1
            else:
                pb.memory[STAT] = pb.memory[STAT] | 0x40
            armed_here = True

        state["lcd"], state["vbl"] = [], None
        pb.tick(1, True)
        if armed_here:
            if cut_on is not None:
                pb.memory[cut_on] = 0
            else:
                pb.memory[STAT] = pb.memory[STAT] & ~0x40

        v = state["vbl"]
        if v is None:
            continue
        lcdc, want, wy = v
        win = bool(lcdc & WIN_ON) and wy < 144
        win_at[f] = win
        if injections and f - injections[-1] < 30 and not armed_here:
            # The inject's RESTORE writes the flag back to 0, which also kills
            # an arm the game made itself in that frame: the frames after it
            # are the probe's own doing, not the ROM's.
            prev_win = win
            closed_run = 0
            continue
        if prev_win is not None and want:
            if not win and not prev_win:
                closed += 1
                closed_run += 1
                lcd_on_closed += len(state["lcd"])
                if armed_here:
                    injections.append(f)
                    closed_run = 0
                if not lcdc & OBJ_ON:
                    bad.append((f, "CLOSED", "sprites hidden with no box up",
                                state["lcd"]))
            elif win and prev_win:
                opened += 1
                if lcdc & OBJ_ON:
                    bad.append((f, "OPEN", "box up but the sprites were never cut",
                                state["lcd"]))
                for ly, wyv in state["lcd"]:
                    line = (wyv - 1) & 0xFF if wyv else 0
                    if ly not in (line, (line + 1) & 0xFF):
                        bad.append((f, "OPEN", "handler ran at LY %d, box line %d"
                                    % (ly, line), state["lcd"]))
                        break
        if win or prev_win:
            closed_run = 0
        prev_win = win

    pb.stop(save=False)

    # KNOWN AND SEPARATE: the cut is armed at `text.to_window`, but the window
    # is SHOWN only by `text.win_reveal()` after the box is drawn (the box
    # staging fix), so for the frames in
    # between the sprites below the box line are hidden with no box on screen.
    # Pre-existing and present identically before the STAT fix; counted apart
    # so it neither hides a new defect nor fails this probe's own question.
    def early(b):
        f, kind, _msg, lcd = b
        return (kind == "CLOSED" and f not in injections
                and any(win_at.get(g) for g in range(f + 1, f + 8))
                and all(wyv and ly in ((wyv - 1) & 0xFF, wyv & 0xFF)
                        for ly, wyv in lcd))
    known = [b for b in bad if early(b)]
    new = [b for b in bad if not early(b)]

    print("%s (%s)" % (os.path.basename(a.rom), "dmg" if a.dmg else "cgb"))
    print("  CLOSED frames measured : %d  (handler entries on them: %d, "
          "i.e. %.2f per frame)" % (closed, lcd_on_closed,
                                    lcd_on_closed / closed if closed else 0))
    print("  OPEN frames measured   : %d" % opened)
    print("  known: cut armed before the window was revealed : %d frames" % len(known))
    print("  violations             : %d" % len(new))
    for b in new[:12]:
        print("     f=%d  %s  %s  lcd=%s" % b)
    if closed < 200 or opened < 200:
        print("  [FAIL] too few frames of one kind to measure anything")
        return 1
    if a.inject:
        # Only an inject with NO box in the frames after it counts: next to a
        # box the ROM's own early arm would have been flagged anyway.
        clean = [i for i in injections
                 if not any(win_at.get(g) for g in range(i + 1, i + 8))]
        flagged = {b[0] for b in new}
        missed = [i for i in clean if i not in flagged]
        ok = bool(clean) and not missed
        print("  [%s] the probe can REPORT: %d clean injections %s, %d caught%s"
              % ("PASS" if ok else "FAIL", len(clean), clean,
                 len(clean) - len(missed),
                 "" if not missed else ", MISSED %s" % missed))
        return 0 if ok else 1
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
