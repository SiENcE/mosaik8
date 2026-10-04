"""Profile a GB/GBC ROM: where does its frame go, and is it keeping up?

The GB-family counterpart of `emu/libretro/lynx_probe.py`. A Game Boy never
slows down - the LCD runs at 59.7 Hz whatever the game does - so what a player
reads as slowdown is the game MISSING its vblank deadline, and one GAME frame
then spans two or three LCD frames. Everything here measures that.

Two instruments, both from PyBoy:

  * `hook_register(bank, addr, cb, None)` - fire a callback when the CPU
    reaches an address. Exact, not sampled.
  * `_cycles()` - a T-cycle counter. **One LCD frame is 70,224 cycles**, which
    is the yardstick for every number this prints.

Addresses come from a `.noi`, which the normal build does NOT emit: relink with
`-Wl-j` added to the exact command the build printed, e.g.

    lcc -msm83:gb -Wm-yc -Wm-yt0x19 -Wm-yo32 -Wl-m -Wl-j -I<gbdk>/include \\
        -o _sym.gbc main.c bank1.c bank2.c ...

**A `.noi` line is `DEF _name 0x<BBAAAA>` = `(bank << 16) | address`**, not a
flat 20-bit offset. Decoding it as `>> 14` invents banks that do not exist and
`hook_register` refuses them ("ROM Bank out of range"); `--noi` here does it
correctly, so prefer passing the file over hand-computing anything.

Usage:

    # LCD frames per GAME frame (1.00 = full speed), per input regime
    python emu/gb_profile.py rate ROM --noi SYM.noi --nav start,a

    # per-stage cycle budget of vm.core.run()'s fixed frame sequence
    python emu/gb_profile.py budget ROM --noi SYM.noi --nav start,a

    # calls per game frame for any symbols you name
    python emu/gb_profile.py calls ROM --noi SYM.noi --nav start,a \\
        --sym rooms_solid_at --sym __mulint

**Driving two ROMs into the same place is the hard part of any comparison** -
the reference-engine sample and our conversion of it need different `--nav` to leave
the title screen. Pass `--shot` and LOOK at the screenshot before believing a
cross-ROM number; measuring one game's title against another's gameplay is a
mistake this tool exists to have already made once.
"""
import argparse
import os
import re
import statistics
import sys

LCD_FRAME = 70224

#: The stages of `vm.core.run()`, in the order the loop runs them. The delta
#: from one entry to the next is that stage's cost (plus the small glue after
#: it: `player_update` carries g_ui_freeze/g_proj_update, `canim_apply` carries
#: g_bkg_anim/g_proj_render). A stage the program does not link is skipped.
VM_STAGES = ["vm_core_run_scripts", "vm_player_update_platform",
             "vm_actor_step_all", "vm_actor_render", "vm_canim_tick_all",
             "vm_canim_apply", "vm_music_update", "vm_player_cam_apply",
             "gbs_wait_vblank"]

#: The once-per-game-frame anchor. `gbs_wait_vblank` for a mosaik ROM;
#: `wait_vbl_done` is the reference engine's own, so its ROMs profile with the same tool.
FRAME_ANCHORS = ["gbs_wait_vblank", "wait_vbl_done"]

REGIMES = [("idle", ()), ("walk right", ("right",)),
           ("jump (A)", ("a",)), ("jump + right", ("a", "right"))]


def read_noi(path):
    """{symbol: (bank, cpu_address)} from an sdcc `.noi`."""
    out = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
            if not m:
                continue
            name, val = m.group(1), int(m.group(2), 16)
            out[name.lstrip("_")] = (val >> 16, val & 0xFFFF)
    return out


def _pyboy(rom):
    from pyboy import PyBoy
    return PyBoy(rom, window="null", sound_emulated=False)


def _nav(pb, nav, counter=None, boot=300, hold=8, after=110):
    """Drive the ROM into gameplay.

    When `counter` is given (a one-element list the frame anchor increments)
    every phase is measured in GAME frames rather than LCD frames. That matters
    for A/B: two builds of different speed reach different points of the level
    in the same number of LCD frames, so their regimes start from DIFFERENT
    states and the comparison is meaningless. It measured a real 31% win as a
    9% loss before this was fixed."""
    def wait(n):
        if counter is None:
            for _ in range(n):
                pb.tick()
            return
        start, guard = counter[0], 0
        while counter[0] - start < n and guard < n * 12:
            pb.tick()
            guard += 1

    wait(boot)
    for btn in nav:
        pb.button_press(btn)
        wait(hold)
        pb.button_release(btn)
        wait(after)
    wait(120)


def _run(pb, args, counter):
    """Tick a regime. Returns (lcd_frames, game_frames).

    With --game-frames the regime ends after exactly N GAME frames, which is
    what makes two builds comparable (see the flag's help)."""
    if args.game_frames:
        start = counter[0]
        lcd = 0
        limit = args.game_frames * 12          # safety net for a hung ROM
        while counter[0] - start < args.game_frames and lcd < limit:
            pb.tick()
            lcd += 1
        return lcd, counter[0] - start
    start = counter[0]
    for _ in range(args.frames):
        pb.tick()
    return args.frames, counter[0] - start


def _frame_anchor(syms, explicit):
    if explicit:
        if explicit not in syms:
            sys.exit("no symbol '%s' in the .noi" % explicit)
        return explicit, syms[explicit]
    for name in FRAME_ANCHORS:
        if name in syms:
            return name, syms[name]
    sys.exit("no per-frame anchor found (looked for %s); pass --frame-sym"
             % ", ".join(FRAME_ANCHORS))


def cmd_rate(args, syms):
    name, (bank, addr) = _frame_anchor(syms, args.frame_sym)
    pb = _pyboy(args.rom)
    n = [0]
    pb.hook_register(bank, addr, lambda _c: n.__setitem__(0, n[0] + 1), None)
    try:
        _nav(pb, [], n if args.game_frames else None, boot=300,
             hold=0, after=0)
        lcd, gf = _run(pb, args, n)
        print("  %-14s %.2f LCD frames per game frame"
              % ("title", lcd / max(1, gf)))
        _nav(pb, args.nav, n if args.game_frames else None, boot=0)
        if args.shot:
            pb.screen.image.convert("RGB").save(args.shot)
            print("  (screenshot at the measurement point -> %s)" % args.shot)
        for label, btns in REGIMES:
            for b in btns:
                pb.button_press(b)
            lcd, gf = _run(pb, args, n)
            for b in btns:
                pb.button_release(b)
            print("  %-14s %.2f LCD frames per game frame  (%d game frames)"
                  % (label, lcd / max(1, gf), gf))
            for _ in range(30):
                pb.tick()
    finally:
        pb.stop(save=False)


def cmd_budget(args, syms):
    stages = [(s, syms[s]) for s in VM_STAGES if s in syms]
    if len(stages) < 2:
        sys.exit("this ROM links none of the vm.core stages; is it a VM8 game?")
    pb = _pyboy(args.rom)
    ev, rec = [], [False]

    first = stages[0][0]
    gfc = [0]
    fname, fba = _frame_anchor(syms, args.frame_sym)

    def mk(tag, counts_frame):
        def hit(_c):
            if counts_frame:
                gfc[0] += 1
            if rec[0]:
                ev.append((pb._cycles(), tag))
        return hit

    # ONE hook per address: the frame anchor is usually also a stage, and
    # registering it twice is a hard error in PyBoy.
    for tag, (bank, addr) in stages:
        pb.hook_register(bank, addr, mk(tag, tag == fname), None)
    if fname not in [t for t, _x in stages]:
        pb.hook_register(fba[0], fba[1],
                         lambda _c: gfc.__setitem__(0, gfc[0] + 1), None)
    try:
        _nav(pb, args.nav, gfc if args.game_frames else None)
        if args.shot:
            pb.screen.image.convert("RGB").save(args.shot)
        for label, btns in REGIMES:
            for b in btns:
                pb.button_press(b)
            ev.clear()
            rec[0] = True
            if args.game_frames:
                limit = args.game_frames * 12
                lcd = 0
                # count off the event list's tail rather than re-scanning it
                seen, scanned = 0, 0
                while seen < args.game_frames and lcd < limit:
                    pb.tick()
                    lcd += 1
                    while scanned < len(ev):
                        if ev[scanned][1] == first:
                            seen += 1
                        scanned += 1
            else:
                for _ in range(args.frames):
                    pb.tick()
            rec[0] = False
            for b in btns:
                pb.button_release(b)
            total, frames, i = {}, 0, 0
            while i < len(ev) - 1:
                if ev[i][1] != first:
                    i += 1
                    continue
                j = i + 1
                while j < len(ev) and ev[j][1] != first:
                    j += 1
                if j >= len(ev):
                    break
                span = ev[i:j + 1]
                for k in range(len(span) - 1):
                    total[span[k][1]] = (total.get(span[k][1], 0)
                                         + span[k + 1][0] - span[k][0])
                frames += 1
                i = j
            if not frames:
                print("\n=== %s === no frames captured" % label)
                continue
            print("\n=== %s === %d game frames" % (label, frames))
            work = 0
            for tag, _ba in stages:
                c = total.get(tag, 0) / frames
                if "wait" not in tag:
                    work += c
                print("   %-26s %8.0f cycles  (%5.1f%% of an LCD frame)"
                      % (tag, c, 100.0 * c / LCD_FRAME))
            print("   %-26s %8.0f cycles  = %.2f LCD frames of WORK"
                  % ("--> total work", work, work / LCD_FRAME))
            for _ in range(30):
                pb.tick()
    finally:
        pb.stop(save=False)


def cmd_calls(args, syms):
    fname, (fbank, faddr) = _frame_anchor(syms, args.frame_sym)
    want = []
    for s in args.sym:
        if s not in syms:
            print("  (no symbol '%s' in the .noi, skipped)" % s)
            continue
        want.append((s, syms[s]))
    if not want:
        sys.exit("none of the requested symbols are in the .noi")
    pb = _pyboy(args.rom)
    counts = {s: 0 for s, _ba in want}
    frames = [0]

    def mk(s):
        def hit(_c):
            counts[s] += 1
        return hit

    pb.hook_register(fbank, faddr,
                     lambda _c: frames.__setitem__(0, frames[0] + 1), None)
    for s, (bank, addr) in want:
        pb.hook_register(bank, addr, mk(s), None)
    try:
        _nav(pb, args.nav, frames if args.game_frames else None)
        for label, btns in REGIMES:
            for b in btns:
                pb.button_press(b)
            for k in counts:
                counts[k] = 0
            frames[0] = 0
            for _ in range(args.frames):
                pb.tick()
            for b in btns:
                pb.button_release(b)
            gf = frames[0] or 1
            print("\n=== %s === %d game frames (%.2f LCD/game)"
                  % (label, gf, args.frames / gf))
            for s, _ba in want:
                if counts[s]:
                    print("   %-28s %8.1f calls / game frame"
                          % (s, counts[s] / gf))
            for _ in range(30):
                pb.tick()
    finally:
        pb.stop(save=False)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("rate", "budget", "calls"))
    ap.add_argument("rom")
    ap.add_argument("--noi", required=True,
                    help="the .noi from a -Wl-j relink (see the module docstring)")
    ap.add_argument("--nav", default="",
                    help="comma-separated buttons that reach gameplay, "
                         "e.g. start,a (the reference engine's own sample needs start,a,a)")
    ap.add_argument("--frames", type=int, default=240,
                    help="LCD frames per regime")
    ap.add_argument("--game-frames", type=int, default=0,
                    help="run each regime for exactly N GAME frames instead of "
                         "N LCD frames. USE THIS FOR A/B COMPARISONS: two "
                         "builds that differ in speed cover different ground "
                         "in the same number of LCD frames, so they simulate "
                         "DIFFERENT trajectories and the numbers are not "
                         "comparable. Same game frames = same inputs, same "
                         "physics, same path.")
    ap.add_argument("--sym", action="append", default=[],
                    help="symbol to count (mode=calls), repeatable")
    ap.add_argument("--frame-sym", default=None,
                    help="override the once-per-game-frame anchor symbol")
    ap.add_argument("--shot", default=None,
                    help="save a screenshot at the measurement point -- LOOK at "
                         "it before trusting a cross-ROM comparison")
    args = ap.parse_args(argv)
    args.nav = [b for b in args.nav.split(",") if b]
    if not os.path.isfile(args.rom):
        sys.exit("no such ROM: %s" % args.rom)
    if not os.path.isfile(args.noi):
        sys.exit("no such .noi: %s" % args.noi)
    syms = read_noi(args.noi)
    print("%s  (%d symbols)" % (os.path.basename(args.rom), len(syms)))
    {"rate": cmd_rate, "budget": cmd_budget, "calls": cmd_calls}[args.mode](
        args, syms)


if __name__ == "__main__":
    main()
