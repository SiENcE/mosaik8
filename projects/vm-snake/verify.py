#!/usr/bin/env python3
"""vm-snake on a real Game Boy ROM (PyBoy, headless and deterministic).

    python mosaik8.py build --platform gameboy projects/vm-snake
    python projects/vm-snake/verify.py

The game is entirely bytecode, so this asserts on the HEAP the program keeps
(rows 0..19 = one 10-bit mask each, 20/21 the food, 22/23 the head, 26 SCORE,
27 LENGTH, 29 NOTE, 30 DIR, 43 BEST, 44 the board height) and on the
background map the native renderer writes from it. The heap's WRAM address
comes from a linker symbol file, so the check RELINKS the project's own C with
`-Wl-j` into `probe.gb` (a plain build writes no `.noi`) - the same relink
vm-musicroutine's verify makes, done here rather than asked of the user.

It PLAYS the game rather than poking it: a small controller reads the head and
the food out of the heap and steers with the d-pad, so "eating grows the snake"
is proved the way a player would see it. Every press is held for several LCD
frames, because the program reads the pad through INPUT_ATTACH, once per VM
frame, and a VM frame is not an LCD frame.

Every window below reads a HIGH-WATER MARK (or counts frames) rather than one
sampled frame: a move is ~75 instructions, which at the spec-minimum QUANT 16
spans five VM frames, and a frame boundary can land anywhere inside it.
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

BUILD = os.path.join(HERE, "build", "gameboy")
ROM = os.path.join(BUILD, "vm-snake.gb")

C_FX, C_FY, C_HX, C_HY, C_LIVE = 20, 21, 22, 23, 24
C_SCORE, C_LEN, C_LEVEL, C_NOTE, C_DIR = 26, 27, 28, 29, 30
C_COUNT, C_BEST, C_HEIGHT = 42, 43, 44
UP, DOWN, LEFT, RIGHT = 4, 5, 6, 7
BUTTON = {UP: "up", DOWN: "down", LEFT: "left", RIGHT: "right"}
STEP = {UP: (0, -1), DOWN: (0, 1), LEFT: (-1, 0), RIGHT: (1, 0)}

# Tile ids, mirroring what assets/gen_tiles.py emits (read back from the
# generated module below, so a reordered table cannot silently shift them).
TILE_NAMES = ("T_EMPTY", "T_WALL", "T_BODY", "T_HEAD0", "T_HEAD3", "T_FOOD",
              "G_0")
# A GB screen is 18 text rows: 16 board rows, a wall above and below.
GB_ROWS = 16

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s%s" % (label, ("  (" + detail + ")") if detail else ""))
    else:
        failed += 1
        print("  [FAILED] %s%s" % (label, ("  -- " + detail) if detail else ""))


def tile_ids():
    text = open(os.path.join(HERE, "src", "tiles.mos"), encoding="utf-8").read()
    out = {}
    for name in TILE_NAMES:
        m = re.search(r"const %s:\s*u8 = (\d+)" % name, text)
        out[name] = int(m.group(1)) if m else None
    return out


def relink_with_symbols():
    """Relink the build's own C into probe.gb WITH a `.noi` symbol file.
    Returns (rom, heap address) or None when GBDK is missing (skip politely)."""
    from mosaik8_build import gbdk_available, tool_prefix
    if not gbdk_available():
        return None
    prefix = tool_prefix()
    gbdk = None
    for cand in (os.environ.get("GBDK_HOME"),
                 os.path.join(prefix, "gbdk") if prefix else None,
                 os.path.join(ROOT, "gbdk")):
        if cand and os.path.isdir(os.path.join(cand, "include")):
            gbdk = cand
            break
    if gbdk is None:
        return None
    lcc = os.path.join(gbdk, "bin", "lcc" + (".exe" if os.name == "nt" else ""))
    c_files = sorted(f for f in os.listdir(BUILD) if f.endswith(".c"))
    out = os.path.join(BUILD, "probe.gb")
    cmd = [lcc, "-msm83:gb", "-Wl-j", "-I" + os.path.join(gbdk, "include"),
           "-o", out] + [os.path.join(BUILD, f) for f in c_files]
    r = subprocess.run(cmd, capture_output=True, text=True)
    noi = os.path.join(BUILD, "probe.noi")
    if r.returncode != 0 or not os.path.isfile(out) or not os.path.isfile(noi):
        print("SKIP: the symbol relink failed:\n%s" % (r.stderr or r.stdout)[-800:])
        return None
    m = re.search(r"DEF _vm_core_heap (0x[0-9A-Fa-f]+)",
                  open(noi, encoding="utf-8", errors="replace").read())
    return (out, int(m.group(1), 16)) if m else None


class Game:
    """A booted ROM plus the heap and background-map readers."""

    def __init__(self, rom, base):
        from pyboy import PyBoy
        self.pb = PyBoy(rom, window="null")
        self.base = base
        self.frame = 0

    def var(self, i):
        a = self.base + i * 2
        v = self.pb.memory[a] | (self.pb.memory[a + 1] << 8)
        return v - 65536 if v > 32767 else v

    def bg(self, col, row):
        return self.pb.memory[0x9800 + row * 32 + col]

    def tick(self, n=1):
        for _ in range(n):
            self.pb.tick(1, False)
            self.frame += 1

    def press(self, btn, hold=6, settle=4):
        self.pb.button_press(btn)
        self.tick(hold)
        self.pb.button_release(btn)
        self.tick(settle)

    def head(self):
        return self.var(C_HX), self.var(C_HY)

    def wait_move(self, limit=120):
        """Advance until the head moves; returns the frames it took (or None)."""
        start, n = self.head(), 0
        while n < limit:
            self.tick()
            n += 1
            if self.head() != start:
                return n
        return None

    def between_moves(self, limit=120):
        """Advance to the first frame AFTER a move has finished.

        A move sets the countdown (cell 42) to 5 - LEVEL and holds it there
        for the several VM frames the move takes; the first frame it reads one
        less, the move is done and the next one is a whole countdown away. A
        press made THEN reaches the program's wish cell before the next move
        reads it, so a turn lands on the very next cell - which the U-turn
        below depends on. (A press made mid-move turns a cell late.)"""
        full = max(1, 5 - self.var(C_LEVEL))
        seen_full = False
        for _ in range(limit):
            count = self.var(C_COUNT)
            if count == full:
                seen_full = True
            elif seen_full and count == full - 1:
                return True
            self.tick()
        return False

    def turn(self, d):
        """Turn to heading `d` on the next move; returns True once it moved."""
        self.between_moves()
        self.press(BUTTON[d], hold=3, settle=0)
        return self.wait_move() is not None and self.var(C_DIR) == d

    def body_at(self, x, y):
        """A set row bit that is not the food: a segment."""
        if (x, y) == (self.var(C_FX), self.var(C_FY)):
            return False
        return bool(self.var(y) >> x & 1)

    def safe(self, d, rows):
        hx, hy = self.head()
        dx, dy = STEP[d]
        x, y = hx + dx, hy + dy
        return 0 <= x < 10 and 0 <= y < rows and not self.body_at(x, y)

    def steer_to_food(self, rows):
        """One move of the controller: pick the heading that closes on the
        food without a reversal, a wall or a segment, press it between moves
        if it is not the current one, and wait for the move."""
        self.between_moves()
        hx, hy = self.head()
        fx, fy = self.var(C_FX), self.var(C_FY)
        cur = self.var(C_DIR)
        want = []
        if fx != hx:
            want.append(RIGHT if fx > hx else LEFT)
        if fy != hy:
            want.append(DOWN if fy > hy else UP)
        want += [cur, UP, DOWN, LEFT, RIGHT]
        for d in want:
            if d == (cur ^ 1):
                continue                      # the program refuses a reversal
            if self.safe(d, rows):
                if d != cur:
                    self.press(BUTTON[d], hold=3, settle=0)
                break
        return self.wait_move()

    def well_matches(self, ids, rows):
        """Does the drawn well equal the heap, cell for cell, right now?"""
        live = self.var(C_LIVE) != 0
        hx, hy = self.head()
        fx, fy = self.var(C_FX), self.var(C_FY)
        for r in range(rows):
            m = self.var(r)
            for c in range(10):
                t = self.bg(1 + c, 1 + r)
                want = ids["T_BODY"] if m >> c & 1 else ids["T_EMPTY"]
                if live and (c, r) == (fx, fy):
                    want = ids["T_FOOD"]
                if live and (c, r) == (hx, hy):
                    if not ids["T_HEAD0"] <= t <= ids["T_HEAD3"]:
                        return False
                    continue
                if t != want:
                    return False
        return True

    def glyphs_in_well(self, ids, rows):
        return sum(1 for r in range(rows) for c in range(10)
                   if self.bg(1 + c, 1 + r) >= ids["G_0"])

    def stop(self):
        self.pb.stop(save=False)


def main():
    if not os.path.isfile(ROM):
        print("SKIP: vm-snake.gb not built")
        return 0
    try:
        import pyboy                                      # noqa: F401
    except ImportError:
        print("SKIP: PyBoy is not installed")
        return 0
    got = relink_with_symbols()
    if got is None:
        print("SKIP: could not relink for symbols (GBDK not installed?)")
        return 0
    rom, base = got
    ids = tile_ids()
    if None in ids.values():
        print("FAILED: src/tiles.mos no longer names %s"
              % [k for k, v in ids.items() if v is None])
        return 1

    g = Game(rom, base)
    try:
        return play(g, ids)
    finally:
        g.stop()


def play(g, ids):
    rows = GB_ROWS
    g.tick(120)
    print("\n== boot: the title ==")
    check("the shell seeds the board height for an 18-row screen",
          g.var(C_HEIGHT) == rows, "cell 44 = %d" % g.var(C_HEIGHT))
    check("the well is walled on all four sides",
          all(g.bg(c, 0) == ids["T_WALL"] and g.bg(c, rows + 1) == ids["T_WALL"]
              for c in range(12))
          and all(g.bg(0, r) == ids["T_WALL"] and g.bg(11, r) == ids["T_WALL"]
                  for r in range(rows + 2)))
    labels = [g.bg(12, r) for r in (0, 3, 6, 9)]    # SCORE LENGTH LEVEL BEST
    check("the panel labels are drawn", all(t >= ids["G_0"] for t in labels),
          "tiles %s" % labels)
    # "SNAKE" + "PRESS A" = 11 glyphs; the space draws as the field tile.
    n = g.glyphs_in_well(ids, rows)
    check("the title note is drawn IN the well",
          g.var(C_NOTE) == 1 and n == 11, "note=%d glyph cells=%d"
          % (g.var(C_NOTE), n))
    check("no dialogue box: the window layer stays off",
          not g.pb.memory[0xFF40] & 0x20)
    check("no snake until a button", g.var(C_LIVE) == 0 and g.var(C_LEN) == 0)

    print("\n== START starts a game ==")
    g.press("start", settle=12)
    check("the note clears and a 3-segment snake appears",
          g.var(C_NOTE) == 0 and g.var(C_LIVE) == 1 and g.var(C_LEN) == 3,
          "note=%d live=%d len=%d" % (g.var(C_NOTE), g.var(C_LIVE), g.var(C_LEN)))
    check("the food is on the board, on a set row bit",
          0 <= g.var(C_FX) < 10 and 0 <= g.var(C_FY) < rows
          and g.var(g.var(C_FY)) >> g.var(C_FX) & 1 == 1,
          "food (%d,%d)" % (g.var(C_FX), g.var(C_FY)))

    print("\n== it moves on its own, and the renderer follows the heap ==")
    g.wait_move()                       # the first move: the game is running
    x0 = g.var(C_HX)
    hi, gaps, matches, frames = x0, [], 0, 0
    last, lf = g.head(), 0
    for f in range(1, 36):              # two moves: the wall is five away
        g.tick()
        frames += 1
        hi = max(hi, g.var(C_HX))
        if g.head() != last:
            gaps.append(f - lf)
            last, lf = g.head(), f
        matches += g.well_matches(ids, rows)
    check("the head advances right (high-water over 35 frames)", hi >= x0 + 2,
          "HX %d -> max %d" % (x0, hi))
    steady = gaps[1:]                   # the first gap is a partial countdown
    rate = sum(steady) / float(len(steady)) if steady else 0
    # The speed tripwire. Level 1 is a 5-VM-frame countdown plus the move's
    # own ~5 VM frames at QUANT 16. A much slower snake means the interpreter
    # or the renderer got dearer; a much faster one, that the countdown lost a
    # frame.
    check("level 1 moves a cell every 12..19 LCD frames", 12 <= rate <= 19,
          "%.1f frames per cell, gaps %s" % (rate, gaps))
    check("the drawn well matches the heap on most frames",
          matches >= frames // 2, "%d of %d frames" % (matches, frames))

    print("\n== the d-pad steers ==")
    y0 = g.var(C_HY)
    turned = g.turn(UP)
    lo = y0
    for _ in range(40):
        g.tick()
        lo = min(lo, g.var(C_HY))
    check("UP turns the snake upward on the next move",
          turned and lo <= y0 - 2,
          "DIR=%d HY %d -> min %d" % (g.var(C_DIR), y0, lo))
    g.between_moves()
    g.press("down", hold=3, settle=0)
    g.wait_move()
    g.tick(4)
    check("DOWN (a reversal) is refused, and the snake lives",
          g.var(C_DIR) == UP and g.var(C_NOTE) == 0,
          "DIR=%d note=%d" % (g.var(C_DIR), g.var(C_NOTE)))

    print("\n== eating grows the snake ==")
    eaten, moves = 0, 0
    while eaten < 3 and moves < 200 and g.var(C_NOTE) == 0:
        s0 = g.var(C_SCORE)
        if g.steer_to_food(rows) is None:
            break
        moves += 1
        if g.var(C_SCORE) > s0:
            eaten += 1
    g.between_moves()
    score, length = g.var(C_SCORE), g.var(C_LEN)
    check("the controller ate three pieces of food", eaten == 3 and score == 3,
          "eaten=%d score=%d in %d moves, note=%d"
          % (eaten, score, moves, g.var(C_NOTE)))
    check("each one grew the snake by a segment", length == 3 + score,
          "length %d, score %d" % (length, score))
    segs = sum(bin(g.var(r) & 0x3FF).count("1") for r in range(rows))
    check("the row masks hold exactly the body plus the food",
          segs == length + 1, "%d bits for length %d" % (segs, length))
    drew = 0
    for _ in range(30):
        g.tick()
        cells = sum(1 for r in range(rows) for c in range(10)
                    if g.bg(1 + c, 1 + r) == ids["T_BODY"]
                    or ids["T_HEAD0"] <= g.bg(1 + c, 1 + r) <= ids["T_HEAD3"])
        drew += cells == g.var(C_LEN)
    check("the longer snake is drawn segment for segment", drew > 0,
          "%d of 30 frames drew exactly %d cells" % (drew, g.var(C_LEN)))
    digits = [g.bg(12 + c, 1) - ids["G_0"] for c in range(5)]
    check("the panel shows the score x10", digits == [0, 0, 0, 3, 0],
          "digits %s" % digits)

    print("\n== running into itself ends the round ==")
    # A U-turn one cell wide: with six segments the third turn lands the head
    # on its own body. Each turn is pressed BETWEEN moves (see between_moves),
    # so each one takes effect on the very next cell. The U opens away from
    # the nearer wall.
    cur = g.var(C_DIR)
    hx, hy = g.head()
    if cur in (LEFT, RIGHT):
        side = UP if hy >= 2 else DOWN
    else:
        side = LEFT if hx >= 2 else RIGHT
    turns = (side, cur ^ 1, side ^ 1)
    for d in turns:
        g.turn(d)
        if g.var(C_NOTE) == 2:
            break
    g.tick(30)
    hx, hy = g.head()
    dx, dy = STEP.get(g.var(C_DIR), (0, 0))
    ahead = (hx + dx, hy + dy)
    inside = 0 <= ahead[0] < 10 and 0 <= ahead[1] < rows
    check("the head met its own body, not a wall: game over",
          g.var(C_NOTE) == 2 and inside and g.body_at(*ahead),
          "note=%d head (%d,%d) heading into %s, length %d"
          % (g.var(C_NOTE), hx, hy, ahead, g.var(C_LEN)))
    n = g.glyphs_in_well(ids, rows)
    # "GAME OVER" + "PRESS A" = 14 glyphs.
    check("the game-over note is drawn in the well", n == 14,
          "%d glyph cells" % n)
    check("BEST keeps the finished round's score", g.var(C_BEST) == score,
          "best=%d score=%d" % (g.var(C_BEST), score))

    print("\n== A restarts, and a wall ends a round ==")
    g.press("a", settle=40)
    check("A starts a fresh round",
          g.var(C_NOTE) == 0 and g.var(C_LEN) == 3 and g.var(C_SCORE) == 0,
          "note=%d len=%d score=%d" % (g.var(C_NOTE), g.var(C_LEN),
                                       g.var(C_SCORE)))
    bits = sum(bin(g.var(r) & 0x3FF).count("1") for r in range(rows))
    check("the restart cleared the old body: 3 segments + the food",
          bits == 4, "%d bits set" % bits)
    check("BEST survives the restart", g.var(C_BEST) == score,
          "best=%d" % g.var(C_BEST))
    g.turn(UP)
    for _ in range(rows + 2):
        if g.var(C_NOTE) == 2 or g.wait_move() is None:
            break
    g.tick(30)
    check("steering into the top wall ends the round",
          g.var(C_NOTE) == 2 and g.var(C_HY) == 0,
          "note=%d head %s" % (g.var(C_NOTE), g.head()))

    png = os.path.join(BUILD, "verify.png")
    g.pb.tick(2, True)
    g.pb.screen.image.convert("RGB").resize((480, 432)).save(png)
    print("\n  screenshot -> %s" % png)

    print("\n%d passed, %d failed" % (passed, failed))
    if failed:
        print("FAILED")
        return 1
    print("All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
