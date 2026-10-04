"""Regenerate the vm-bganim world: a 4-colour topdown room with an ANIMATED
background water tile. The Stage-2 (targeted strip invalidation) profiling fixture
for the Lynx row-strip background engine. Writes the tileset + world.toml, then
re-transpiles scenes.mos + rooms.mos.

Why 4 colours, not the bkg16 tier: the engine REFUSES a >4-colour (4bpp) tileset
combined with `[[animated_tile]]` on ANY 4bpp-capable console (PCE/SMS/GG always,
the Lynx under `lynx_bkg16`) -- see `mosaik_scenes/transpile.py`. Animated bkg
tiles and the 16-colour tier are mutually exclusive by design. Stage 2 is a Lynx
row-strip lever whose cost (the strip invalidation + recompose) lives in the 2bpp
engine anyway, so a plain <=4-colour tileset is the correct fixture.

The animated water pond sits in map rows 4-7 only. Every `[[animated_tile]]` frame
step calls `bkg.set_data`, which TODAY marks ALL 16 row strips stale and forces a
full recompose (`cc65_bkg.py`), even though just 4 rows use the tile. That
over-invalidation is what Stage 2 targets: after it, only the strips whose map row
contains the changed tile recompose (~4 here, not 16). The pond spanning few rows
makes the win measurable.

    python projects/vm-bganim/assets/gen_world.py

The shell (src/main.mos + the core.set_anim(scenes.anim_tick) fixture wiring),
events (scripts/, src/scripts.mos) and audio wiring (src/glue.mos) are the New
Project scaffold copied from vm-bkg16.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.abspath(os.path.join(PROJ, "..", ".."))
sys.path.insert(0, ROOT)

from mosaik_assets import write_png_indexed  # noqa: E402
import mosaik_vm  # noqa: E402

# 4 colours (a <=4-colour tileset -> plain 2bpp, no 4bpp refusal): dark, grass
# green, water blue, stone grey. Indices 0..3 are literal GB colours.
PALETTE = [
    (24, 20, 16),     # 0 dark (mortar / ripple shadow)
    (70, 170, 70),    # 1 grass green
    (60, 110, 230),   # 2 water blue
    (170, 170, 180),  # 3 stone grey / edge
]

# Tiles as palette-index grids: 0 grass floor, 1 flowers, 2 water (frame 0),
# 3 stone wall, then 4 water-frame-1, 5 water-frame-2 -- the two extra water
# looks are the animation SOURCES (never placed in the map; anim_tick swaps their
# DATA into slot 2).
FLOOR = [[1 if (x + y) % 4 else 0 for x in range(8)] for y in range(8)]
FLOWERS = [row[:] for row in FLOOR]
for (fy, fx) in ((1, 2), (2, 5), (4, 1), (5, 6), (6, 3)):
    FLOWERS[fy][fx] = 3        # a stone-grey flower head over grass


def _water(phase):
    """A water tile whose ripple pattern is shifted by `phase` so each animation
    frame is a genuinely different 16-byte block (a real recompose, not a no-op
    copy). Blue 2 / dark 0 diagonal ripple with grey-3 edges."""
    w = [[2 if (x + y + phase) % 3 else 0 for x in range(8)] for y in range(8)]
    for y in range(8):
        w[y][0] = 3
        w[y][7] = 3
    return w


WATER0 = _water(0)
WATER1 = _water(1)
WATER2 = _water(2)
WALL = [[3 if (x + y) % 2 else 0 for x in range(8)] for y in range(8)]
# Index order MUST keep the map's tile ids stable: 0 floor, 1 flowers, 2 water,
# 3 wall; 4/5 are the extra water animation frames.
TILES = [FLOOR, FLOWERS, WATER0, WALL, WATER1, WATER2]
TILE_ROWS = len(TILES) * 8       # 6 tiles stacked 8 wide

# Animated water: the map paints the HIDDEN index the studio reserves for an
# animated clip (just above the real tileset, so the editor's two index spaces
# stay separate); anim_tick cycles its DATA through the three water looks
# (tileset sources 2 -> 4 -> 5). A short period keeps the recompose frequent so
# the profile is bkg-compose bound (fast water is realistic; Stage 2 is what
# makes each recompose cheap).
#
# JUST ABOVE the tileset, not at the top of the 256-tile table: only the GB
# family addresses background tiles 0..255. On the SMS / Game Gear the
# background stops at 191 and ids 192..255 ARE the name table + SAT
# (docs/vram-layout.md), so the old `tile = 255` never drew there -- the water
# block was garbage that flickered while the GB build was correct. The engine
# now refuses that placement at build time.
ANIM_TILE = len(TILES)          # = 6, the first index past the real tileset
ANIM_FRAMES = [2, 4, 5]
ANIM_PERIOD = 2


def _write_tileset():
    rows = []
    for t in TILES:
        rows.extend(t)
    write_png_indexed(os.path.join(HERE, "tiles.png"), 8, TILE_ROWS, rows, PALETTE)


def _map():
    """A 20x18 decorated room: stone border, grass floor, a water pond (rows 4-7),
    flower patches. Returns (map, collision) as row lists (tile ids / 0-open
    1-solid)."""
    W, H = 20, 18
    m = [[0] * W for _ in range(H)]
    col = [[0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if x == 0 or y == 0 or x == W - 1 or y == H - 1:
                m[y][x] = 3
                col[y][x] = 1
    # a water pond (solid) mid-right, map rows 4-7 -> strip slots 4-7. The cells
    # carry ANIM_TILE (the hidden animated index), not the tileset's own water
    # tile 2 -- tile 2 is only an animation SOURCE.
    for y in range(4, 8):
        for x in range(12, 17):
            m[y][x] = ANIM_TILE
            col[y][x] = 1
    # flower patches (walkable decoration)
    for (fy, fx) in ((3, 3), (3, 4), (4, 3), (10, 6), (11, 6), (12, 14), (13, 15), (14, 4)):
        m[fy][fx] = 1
    return m, col


def main():
    _write_tileset()
    m, col = _map()

    def rows(a):
        return "[ " + ", ".join(
            "[ " + ", ".join(str(v) for v in r) + ",]" for r in a) + ",]"

    with open(os.path.join(HERE, "world.toml"), "w") as f:
        f.write('[[scene]]\nname = "room"\nscene_type = "topdown"\n')
        f.write("map = %s\n" % rows(m))
        f.write("collision = %s\n" % rows(col))
        f.write('\n[[scene.object]]\nkind = "player"\nx = 80\ny = 72\nid = 0\n')
        f.write('\n[[scene.object]]\nkind = "npc"\nx = 32\ny = 32\nid = 1\n')
        # The animated water tile: DATA swapped on a timer, index fixed in the map.
        f.write('\n[[animated_tile]]\nname = "water"\n')
        f.write('tile = %d\ncount = 1\nperiod = %d\n' % (ANIM_TILE, ANIM_PERIOD))
        f.write('frames = [ %s,]\n' % ", ".join(str(s) for s in ANIM_FRAMES))
        f.write('\n[world]\nmodule = "scenes"\nmap_w = 20\nmap_h = 18\nvm = true\n')
        f.write('next_object_id = 2\n')
        f.write('\n[tileset]\npng = "tiles.png"\n')
        f.write('\n[kinds]\nplayer = 0\nnpc = 1\n')

    import mosaik_scenes
    world, base = mosaik_scenes.load_world(os.path.join(HERE, "world.toml"))
    open(os.path.join(PROJ, "src", "scenes.mos"), "w",
         encoding="utf-8").write(mosaik_scenes.transpile(world, base))
    mosaik_vm.generate_rooms(PROJ)
    print("vm-bganim: wrote tileset (%d tiles) + world.toml with an animated "
          "water tile (period %d), regenerated scenes.mos + rooms.mos"
          % (len(TILES), ANIM_PERIOD))


if __name__ == "__main__":
    main()
