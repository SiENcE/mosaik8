#!/usr/bin/env python3
"""Generate vm-metasprite -- what the PER-OBJECT metasprite descriptor buys,
and what VM8 can already do without it.

Four counters/props in one room, all drawing from ONE 28-tile sheet:

  A  DESCRIPTOR counter   one actor, 100 frames (00..99), 10 pooled digit
                          cells = 20 tiles. Dense this is 100 x 4 = 400 tiles,
                          three times the GB's whole 128-tile OBJ table.
  B  TWO-ACTOR counter    the NATIVE VM8 alternative: two actors of ONE `digit`
                          kind, 10 frames each. Also 20 tiles - a placed kind's
                          sheet uploads once however many actors wear it - and
                          it needs no descriptor at all. It costs one extra
                          actor slot and one extra `actor_set_frame` per tick.
  C  DESCRIPTOR stack     head + body 11 px apart: rows OVERLAP, which no
                          rectangle and no second actor can express within one
                          sprite (a metasprite's rows are 8 px apart, period).
  D  DENSE stack          the same two cells drawn 16 px apart, which is what a
                          padded rectangle forces. Side by side with C, that
                          5 px is the "feet gap".

Run:  python projects/vm-metasprite/assets/gen.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PROJ))
sys.path.insert(0, ROOT)

from mosaik_assets import (write_png_indexed, write_sprite_manifest,   # noqa: E402
                           append_frame_descs)
from mosaik_scenes import transpile, load_world                        # noqa: E402
from mosaik_vm import compile_path, generate_clips                     # noqa: E402

PAL = [(224, 248, 207), (136, 192, 112), (48, 104, 80), (8, 24, 32)]
SPR = [(255, 0, 255), (232, 232, 240), (120, 160, 200), (16, 24, 40)]

# ---------------------------------------------------------------- digit art
# A 3x5 dot font in an 8x16 cell, drawn at shade 1 with a shade-3 outline.
GLYPH = {
    0: ("###", "# #", "# #", "# #", "###"),
    1: (" # ", "## ", " # ", " # ", "###"),
    2: ("###", "  #", "###", "#  ", "###"),
    3: ("###", "  #", "###", "  #", "###"),
    4: ("# #", "# #", "###", "  #", "  #"),
    5: ("###", "#  ", "###", "  #", "###"),
    6: ("###", "#  ", "###", "# #", "###"),
    7: ("###", "  #", "  #", "  #", "  #"),
    8: ("###", "# #", "###", "# #", "###"),
    9: ("###", "# #", "###", "  #", "###"),
}


def digit_cell(n):
    """An 8x16 cell: the glyph doubled to 6x10 px, centred."""
    g = [[0] * 8 for _ in range(16)]
    rows = GLYPH[n]
    for r, line in enumerate(rows):
        for c, ch in enumerate(line):
            if ch != "#":
                continue
            for dy in (0, 1):
                for dx in (0, 1):
                    g[3 + r * 2 + dy][1 + c * 2 + dx] = 1
    # a dark rim so the glyph reads on any background
    out = [row[:] for row in g]
    for r in range(16):
        for c in range(8):
            if g[r][c]:
                continue
            near = any(g[r + dr][c + dc]
                       for dr in (-1, 0, 1) for dc in (-1, 0, 1)
                       if 0 <= r + dr < 16 and 0 <= c + dc < 8)
            if near:
                out[r][c] = 3
    return out


def head_cell():
    g = [[0] * 8 for _ in range(16)]
    for r in range(3, 13):
        for c in range(1, 7):
            g[r][c] = 1
    for c in (2, 5):                      # eyes
        g[6][c] = 3
        g[7][c] = 3
    for r in (3, 12):
        for c in range(1, 7):
            g[r][c] = 3
    return g


def body_cell():
    g = [[0] * 8 for _ in range(16)]
    for r in range(0, 14):
        for c in range(1, 7):
            g[r][c] = 2
    for r in range(0, 14):
        g[r][1] = 3
        g[r][6] = 3
    for c in range(1, 7):
        g[13][c] = 3
    return g


def rows(cells):
    """Stack 8-wide cells top to bottom into one pixel-row list."""
    out = []
    for cell in cells:
        out.extend(cell)
    return out


def main():
    assets = os.path.join(PROJ, "assets")
    os.makedirs(assets, exist_ok=True)

    # ---- the sheet: 10 digit cells + head + body + the DENSE stack ---------
    cells = [digit_cell(n) for n in range(10)] + [head_cell(), body_cell()]
    # D's art is its own 8x32 block: a dense frame's cells are CONSECUTIVE
    # tiles, so the 16 px spacing has to be baked into the picture. That is
    # the difference the descriptor removes - C reuses the two cells above.
    dense = head_cell() + body_cell()
    png = os.path.join(assets, "sprites.png")
    write_png_indexed(png, 8, 16 * 12 + 32, rows(cells) + dense, SPR)

    manifest = [("d%d" % n, [0, n * 16, 8, 16]) for n in range(10)]
    manifest.append(("head", [0, 160, 8, 16]))
    manifest.append(("body", [0, 176, 8, 16]))
    manifest.append(("stackd_0", [0, 192, 8, 32]))
    write_sprite_manifest(png, manifest)

    # ---- the DESCRIPTOR frames -------------------------------------------
    # A: 100 two-digit readouts over the ten pooled cells. Each frame is two
    #    objects; only the CELL INDICES differ, so the art is 10 cells flat.
    frames = [("counter_f%d" % n, 16, 16,
               [(0, 0, n // 10, 0), (0, 8, n % 10, 0)])
              for n in range(100)]
    # C: head + body ELEVEN px apart. Rows overlap; a rectangle cannot.
    frames.append(("stack_f0", 8, 27, [(0, 0, 10, 0), (11, 0, 11, 0)]))
    append_frame_descs(png, frames)

    # ---- background tiles -------------------------------------------------
    tile0 = [0] * 64
    tile1 = [1 if (r + c) % 8 == 0 else 0 for r in range(8) for c in range(8)]
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, 16,
                      [[tile0[r * 8 + c] for c in range(8)] for r in range(8)] +
                      [[tile1[r * 8 + c] for c in range(8)] for r in range(8)],
                      PAL)

    # ---- world + studio ---------------------------------------------------
    W, H = 20, 18
    world = ["# vm-metasprite -- GENERATED by assets/gen.py.",
             "[[scene]]", 'name = "room"', 'scene_type = "topdown"',
             "map = [" + ", ".join(
                 "[" + ",".join("1" if (r == H - 1) else "0"
                                for _ in range(W)) + "]"
                 for r in range(H)) + "]",
             "", "[world]", 'module = "scenes"',
             "map_w = %d" % W, "map_h = %d" % H, "vm = true",
             "", "[tileset]", 'png = "tiles.png"',
             "", "[kinds]",
             "counter = 0", "digit = 1", "stack = 2", "stackd = 3"]
    _write(os.path.join(PROJ, "world.toml"), "\n".join(world) + "\n")

    studio = ["# vm-metasprite -- GENERATED by assets/gen.py.",
              "",
              "# A: ONE actor, 100 frames -- each names a [[frame]] DESCRIPTOR",
              "#    over the ten pooled digit cells (20 tiles, not 400).",
              "[animations.counter.idle]", "period = 255",
              "frames = [ %s ]" % ", ".join('"counter_f%d"' % n
                                            for n in range(100)),
              "",
              "# B: the NATIVE alternative -- a plain 10-frame strip. Two",
              "#    actors wear it (tens + units); one sheet upload serves both.",
              "[animations.digit.idle]", "period = 255",
              "frames = [ %s ]" % ", ".join('"d%d"' % n for n in range(10)),
              "",
              "# C: a DESCRIPTOR whose two rows sit 11 px apart (overlapping).",
              "[animations.stack.idle]", "period = 255",
              'frames = [ "stack_f0" ]',
              "",
              "# D: the same picture as a DENSE rectangle -- the 16 px row",
              "#    pitch is baked into the art and costs 4 tiles.",
              "[animations.stackd.idle]", "period = 255",
              'frames = [ "stackd_0" ]', ""]
    _write(os.path.join(PROJ, "studio.toml"), "\n".join(studio))

    # ---- generate scenes.mos / clips.mos / scripts.mos ---------------------
    w, base = load_world(os.path.join(PROJ, "world.toml"))
    _write(os.path.join(PROJ, "src", "scenes.mos"), transpile(w, base))
    generate_clips(PROJ, os.path.join(PROJ, "src", "clips.mos"))
    prog = compile_path(os.path.join(PROJ, "scripts"))
    _write(os.path.join(PROJ, "src", "scripts.mos"), prog.to_scripts_mos())
    print("vm-metasprite regenerated at", PROJ)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    main()
