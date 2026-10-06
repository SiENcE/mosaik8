#!/usr/bin/env python3
"""Section 6.4: does a screen shake move the picture, and does it move it
around the camera that actually OWNS the scroll register?

The shake used to end in `bkg.move(camera.camx, camy + j)`. `camera.camx` is
engine.camera's u8 mirror, and in a WIDE (streamed) room that is not the camera
at all - so every shaken frame slammed SCX to an unrelated value. In a room
with PARALLAX bands armed the same call is a no-op (`gbs_px_move` stands down),
so there the shake did nothing whatsoever.

The instrument is PER-SCANLINE SCX/SCY (`screen.tilemap_position_list`), not the
registers: once bands are armed the LYC chain rewrites SCX/SCY at every band
boundary, so a mid-frame read of `FF42`/`FF43` reports whichever band the beam
happened to be in.

Two verdicts, per shaken frame:

  * `dX` - the shake's horizontal error against the room's real camera. Stage A
    shakes the vertical axis only (the axis MASK is stage B), so a correct
    shake reads 0 on EVERY scanline and the old one did not.
  * `dY` - the vertical displacement of the picture against the settled frame.
    That is the shake itself; a shake that does nothing reads a constant 0.

The shake is POKED through the `.noi` rather than triggered in-game: a
script that fires one is usually rooms and interactions away, and poking is
what makes two builds comparable at the same pixel. The ROOM is poked the same
way (vm.core's pending exception, RAISE 2), then the player optionally walks
right so a wide room's camera is somewhere other than its left edge.

Usage:
    python tools/shakeprobe/shake_probe.py ROM.gb SYM.noi --room N [--walk 250]
        [--title] [--control]

`--room` defaults to the start scene (no poke). `--title` taps Start then A
first, for a ROM that boots into a title screen. Use a room with parallax
bands for the band case and a wide (streamed) room for the camera case.
"""
import argparse
import re
import sys

from pyboy import PyBoy


def read_noi(path):
    out = {}
    for ln in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)", ln.strip())
        if m:
            out[m.group(1)] = int(m.group(2), 16) & 0xFFFF
    return out


def u16(pb, a):
    return pb.memory[a] | (pb.memory[a + 1] << 8)


def scanlines(pb):
    """[(scx, scy)] per visible scanline, as the beam saw them."""
    return [(e[0], e[1]) for e in pb.screen.tilemap_position_list]


def s8(v):
    return ((v + 128) & 0xFF) - 128


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rom")
    ap.add_argument("noi")
    ap.add_argument("--boot", type=int, default=400)
    ap.add_argument("--walk", type=int, default=0, help="frames of RIGHT")
    ap.add_argument("--room", type=int, default=None,
                    help="poke this room (RAISE 2); default: the start scene")
    ap.add_argument("--title", action="store_true",
                    help="tap Start then A first (a ROM with a title screen)")
    ap.add_argument("--frames", type=int, default=24)
    ap.add_argument("--amp", type=int, default=4)
    ap.add_argument("--control", action="store_true",
                    help="poke nothing - the ambient dX/dY of a still room")
    a = ap.parse_args()

    sym = read_noi(a.noi)
    # A one-scene wide world has no `rooms` module; both reads are reported,
    # never required.
    ROOM, WIDE = sym.get("_rooms_room"), sym.get("_vm_player_wide")
    CAMX16 = sym["_vm_player_camx16"]
    CCAMX = sym["_engine_camera_camx"]
    NB = sym.get("_engine_scrollpx_nb")

    pb = PyBoy(a.rom, window="null", sound_emulated=False)
    for _ in range(a.boot):
        pb.tick()
    if a.title:
        for btn in ("start", "a"):              # logo -> title -> gameplay
            pb.button_press(btn)
            for _ in range(10):
                pb.tick()
            pb.button_release(btn)
            for _ in range(80):
                pb.tick()
    if a.room is not None:
        pb.memory[sym["_vm_core_pend_a"]] = a.room
        pb.memory[sym["_vm_core_pend_b"]] = 40
        pb.memory[sym["_vm_core_pend_b"] + 1] = 0
        pb.memory[sym["_vm_core_pend_c"]] = 72
        pb.memory[sym["_vm_core_pend_c"] + 1] = 0
        pb.memory[sym["_vm_core_pend_code"]] = 2
        for _ in range(300):
            pb.tick()
    pb.button_press("right")
    for _ in range(a.walk):
        pb.tick()
    pb.button_release("right")
    for _ in range(90):                         # let the run momentum die out
        pb.tick()

    room = pb.memory[ROOM] if ROOM is not None else "-"
    wide = pb.memory[WIDE] if WIDE is not None else "-"
    bands = pb.memory[NB] if NB else 0
    camx = u16(pb, CAMX16)
    print("room %s  wide %s  bands %d  camx16 %d  camera.camx %d (the u8 mirror "
          "the old shake wrote)" % (room, wide, bands, camx, pb.memory[CCAMX]))

    # A settled reference: the same picture two frames running, or the room is
    # still moving and every delta below would be the camera, not the shake.
    base = scanlines(pb)
    pb.tick()
    if scanlines(pb) != base:
        print("  NOT SETTLED - the camera is still moving; raise the settle wait")
        pb.stop(save=False)
        return 2

    if not a.control:
        pb.memory[sym["_vm_player_shk_amp"]] = a.amp
        pb.memory[sym["_vm_player_shk_frames"]] = a.frames

    seen_dx, seen_dy, rows = set(), set(), []
    for _ in range(a.frames + 4):
        pb.tick()
        cur = scanlines(pb)
        dxs = {s8(c[0] - b[0]) for c, b in zip(cur, base)}
        dys = {s8(c[1] - b[1]) for c, b in zip(cur, base)}
        seen_dx |= dxs
        seen_dy |= dys
        rows.append((sorted(dxs), sorted(dys)))

    print("  per-frame (dX set | dY set), first 10 frames:")
    for r in rows[:10]:
        print("    %-22s | %s" % (r[0], r[1]))
    print("VERDICT")
    print("  dX over all scanlines/frames: %s  (0 only = the shake left the "
          "camera's X alone)" % sorted(seen_dx))
    print("  dY over all scanlines/frames: %s  (spread ~ +-%d = the shake is "
          "visible)" % (sorted(seen_dy), a.amp))
    pb.stop(save=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
