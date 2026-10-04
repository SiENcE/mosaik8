#!/usr/bin/env python3
"""Does the screen FLICKER while a dialogue box is open?

The reported bug (the RPG check conversion's shop): with a text box up the picture
shimmers. The mechanism to catch is a VBL handler running LATE - the window
sprite-cut's ISR hides sprites at WY-1 and RESTORES them in v-blank, so
anything that delays that restore past the 10-line v-blank window leaves OBJ
disabled for the first scanlines of some frames. That is invisible in a
single screenshot and obvious in a frame-to-frame diff.

The measurement is therefore: park the game in a QUIET state (box open, no
input), sample N rendered frames, and count the frames that differ from the
modal one. A correct build with a static box is pixel-stable; a flickering
one is not, and the ROW histogram says WHERE (a delayed sprite restore
differs at the TOP of the screen, an animated tile at its own row).

Usage: box_flicker_probe.py ROM.gb SYM.noi ROOM [frames]
"""
import sys
from collections import Counter

ROM, NOI, ROOM = sys.argv[1], sys.argv[2], int(sys.argv[3])
FRAMES = int(sys.argv[4]) if len(sys.argv) > 4 else 200

#: dx, dy from an actor: above, below, left, right, half a tile up, on top.
APPROACHES = [(0, 16), (0, -16), (-16, 0), (16, 0), (0, 8), (0, 0)]


def symbols(noi):
    out = {}
    for line in open(noi, encoding="utf-8", errors="replace"):
        p = line.split()
        if len(p) == 3 and p[0] == "DEF":
            try:
                out[p[1]] = int(p[2], 16) & 0xFFFF
            except ValueError:
                pass
    return out


def main():
    s = symbols(NOI)
    from pyboy import PyBoy
    pb = PyBoy(ROM, window="null")
    pb.set_emulation_speed(0)
    PX, PY = s["_vm_player_px"], s["_vm_player_py"]
    SCENE, BOX = s["_vm_core_cur_scene"], s["_vm_core_box_open"]
    A_X, A_Y, A_ACT = s["_vm_actor_a_x"], s["_vm_actor_a_y"], s["_vm_actor_a_active"]
    PEND, PA = s["_vm_core_pend_code"], s["_vm_core_pend_a"]

    def run(n=1):
        for _ in range(n):
            pb.tick()

    def u16(a):
        return pb.memory[a] | (pb.memory[a + 1] << 8)

    def w16(a, v):
        pb.memory[a], pb.memory[a + 1] = v & 0xFF, (v >> 8) & 0xFF

    def tap(btn, after=30):
        pb.button_press(btn)
        run(10)
        pb.button_release(btn)
        run(after)

    run(240)
    for _ in range(3):
        tap("start", 60)
    pb.memory[PA] = ROOM
    pb.memory[PEND] = 2
    run(200)
    if pb.memory[SCENE] != ROOM:
        print("did not reach room %d (got %d)" % (ROOM, pb.memory[SCENE]))
        return 1

    # Walk up to each live actor in turn and press A until a box opens.
    opened = None
    for slot in range(8):
        if opened is not None or not pb.memory[A_ACT + slot]:
            continue
        ax, ay = u16(A_X + slot * 2), u16(A_Y + slot * 2)
        if not (ax or ay):
            continue
        for dx, dy in APPROACHES:
            w16(PX, max(0, ax + dx))
            w16(PY, max(0, ay + dy))
            run(8)
            tap("a", 30)
            if pb.memory[BOX]:
                opened = (slot, dx, dy)
                break
    if opened is None:
        print("no actor in room %d opened a box - nothing below means anything"
              % ROOM)
        return 1
    print("box opened by slot %d from approach %s" % (opened[0], opened[1:]))
    run(90)             # let the reveal finish; the box must be STATIC

    frames = []
    for _ in range(FRAMES):
        pb.tick(1, True)
        frames.append(bytes(pb.screen.ndarray[:, :, 0].tobytes()))
    modal, n = Counter(frames).most_common(1)[0]
    rows = Counter()
    for f in frames:
        if f == modal:
            continue
        for r in range(144):
            a, b = f[r * 160:(r + 1) * 160], modal[r * 160:(r + 1) * 160]
            if a != b:
                rows[r] += 1
    print("box still open at the end: %d" % pb.memory[BOX])
    print("FLICKERING FRAMES: %d of %d (%d distinct images)"
          % (FRAMES - n, FRAMES, len(set(frames))))
    if rows:
        top = sorted(rows.items())[:6]
        print("differing screen rows (row x frames): %s%s"
              % (top, " ..." if len(rows) > 6 else ""))
    pb.stop(save=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
