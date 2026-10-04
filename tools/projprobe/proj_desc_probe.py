#!/usr/bin/env python3
"""What a SHOT actually draws: vm.projectile's live pool state beside the OAM
objects its base owns (plan item 1.3, the per-object descriptor).

The pool banks (`[build] code_banks`), so its globals are NOT in the link map -
relink with `-Wl-j` and pass the resulting `.noi`, or every read below is a
guess. That is what blocked the first pass at this.

Per LCD frame while a shot is live it prints the pool's own view of the shot
(`p_tile`, the flight step `p_fidx`, the VRAM base `g_tbase`) and the OAM block
at `slot_of(i)`, so the DESCRIPTOR the data says should be drawn can be
compared against what reached the hardware.

Usage: proj_desc_probe.py ROM.gb SYM.noi [--frames N] [--desc CLIPS.mos]
"""
import re
import sys

NPROJ = 8


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


def clips_tables(path):
    """The generated clips module's descriptor tables, so the probe can say
    what the data ASKS for at a given frame index (the GB arm of the obj_8x16
    fork - the first copy in the file)."""
    s = open(path, encoding="utf-8").read()
    out = {}
    for name in ("F_OFF", "D_OFF", "D_CNT", "DESC", "D_KIND", "D_FAN"):
        m = re.search(r"const %s: array\[[^\]]+\] = \[(.*?)\]" % name, s, re.S)
        if m:
            out[name] = [int(v) for v in m.group(1).split(",") if v.strip()]
    return out


def main():
    rom, noi = sys.argv[1], sys.argv[2]
    frames = 900
    if "--frames" in sys.argv:
        frames = int(sys.argv[sys.argv.index("--frames") + 1])
    tabs = {}
    if "--desc" in sys.argv:
        tabs = clips_tables(sys.argv[sys.argv.index("--desc") + 1])

    s = symbols(noi)
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null")
    OAM = 0xFE00

    def u8(a):
        return pb.memory[a]

    def run(n=1):
        for _ in range(n):
            pb.tick()

    def tap(btn, after=20):
        pb.button_press(btn)
        run(8)
        pb.button_release(btn)
        run(after)

    P = {k: s["_vm_projectile_" + k] for k in (
        "p_active", "p_tile", "p_fidx", "p_frames", "p_period", "p_stride",
        "p_toff", "g_tbase", "has_pdesc", "g_pkind", "p_fan", "proj_slot",
        "p_slots", "p_x", "p_y")}

    run(620)
    tap("start", 60)
    print("pool: has_pdesc=%d kind=%d fan=%d base=%d slots=%d tbase=%d"
          % (u8(P["has_pdesc"]), u8(P["g_pkind"]), u8(P["p_fan"]),
             u8(P["proj_slot"]), u8(P["p_slots"]), u8(P["g_tbase"])))
    tap("a", 0)

    seen = 0
    for f in range(frames):
        run(1)
        fan = u8(P["p_fan"]) or 1
        base0 = u8(P["proj_slot"])
        for i in range(NPROJ):
            if u8(P["p_active"] + i) != 1:
                continue
            b = base0 + i * fan
            tile = u8(P["p_tile"] + i)
            fidx = u8(P["p_fidx"] + i)
            tbase = u8(P["g_tbase"])
            objs = []
            # read a whole descriptor-sized window, not just p_fan: a fan that
            # overruns its block is one of the things this is looking for
            for k in range(max(fan, 4)):
                o = OAM + (b + k) * 4
                objs.append((u8(o), u8(o + 1), u8(o + 2), u8(o + 3)))
            line = ("f%-4d slot%d base%-3d p_tile=%-3d fidx=%d frames=%d "
                    "tbase=%d | " % (f, i, b, tile, fidx,
                                     u8(P["p_frames"] + i), tbase))
            line += " ".join("%d:(y%d x%d t%d p%02X)" % (b + k, o[0], o[1],
                                                         o[2], o[3])
                             for k, o in enumerate(objs))
            if tabs:
                # what clips.draw ACTUALLY indexes: its own
                # F_OFF[kind * 16 + state * 4 + facing] + the `i` it is handed
                i_arg = tile + fidx
                fi = tabs["F_OFF"][u8(P["g_pkind"]) * 16] + i_arg
                line += "\n      data: i=%d -> frame index %d" % (i_arg, fi)
                if fi < len(tabs.get("D_OFF", [])):
                    off, cnt = tabs["D_OFF"][fi], tabs["D_CNT"][fi]
                    ents = [tuple(tabs["DESC"][off + 4 * e: off + 4 * e + 4])
                            for e in range(cnt)]
                    line += " -> %d obj %s" % (cnt, ents)
                else:
                    line += " -> OUT OF RANGE (D_OFF has %d)" % len(
                        tabs.get("D_OFF", []))
            print(line)
            seen += 1
        if seen > 24:
            break
    if not seen:
        print("no live shot in %d frames" % frames)
    pb.stop(save=False)


if __name__ == "__main__":
    main()
