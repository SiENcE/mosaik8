"""Regenerate the vm-bkg16 world: a 16-colour topdown tilemap with the Lynx 4bpp
opt-in. Writes the tileset + world.toml, then re-transpiles scenes.mos + rooms.mos.

vm-bkg16 is a VM8 game (player walks a decorated room). Because the tileset PNG
carries > 4 colours, on the PC Engine / Master System / Game Gear it renders at
native 16-colour depth automatically, and on the Atari Lynx too via the per-project
opt-in `[world] lynx_bkg16` (the generated rooms.mos calls palette.load_bkg16 on
room load; the Lynx bkg RAM is auto-sized because this is a VM8 game). GB/GBC
down-tier to 2bpp, byte-identical.

    python projects/vm-bkg16/assets/gen_world.py

The shell (src/main.mos), events (scripts/, src/scripts.mos) and audio wiring
(src/glue.mos) were laid down by New Project and are committed as-is.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.abspath(os.path.join(PROJ, "..", ".."))
sys.path.insert(0, ROOT)

from mosaik_assets import write_png_indexed  # noqa: E402
import mosaik_vm  # noqa: E402

# 16 well-separated colours (a >4-colour tileset -> the 4bpp background tier).
PALETTE = [
    (24, 20, 16),    (56, 140, 56),   (110, 205, 96),  (230, 60, 60),
    (245, 210, 60),  (245, 130, 190), (60, 110, 230),  (95, 205, 235),
    (120, 120, 130), (175, 175, 185), (105, 78, 52),   (242, 242, 246),
    (205, 120, 60),  (150, 95, 205),  (60, 185, 150),  (30, 40, 92),
]

# Four 8x8 tiles as palette-index grids: 0 grass floor, 1 flowers, 2 water,
# 3 stone wall. Each mixes several of the 16 colours so the map is genuinely
# 16-colour, not 4.
_G, _g = 1, 2                    # grass, light grass
FLOOR = [[_G if (x + y) % 3 else _g for x in range(8)] for y in range(8)]
FLOWERS = [row[:] for row in FLOOR]
for (fy, fx, c) in ((1, 2, 3), (2, 5, 4), (4, 1, 5), (5, 6, 3), (6, 3, 4)):
    FLOWERS[fy][fx] = c
    FLOWERS[fy - 1][fx] = 2     # a light-green stem highlight above
WATER = [[6 if (x + y) % 4 else 7 for x in range(8)] for y in range(8)]
for y in range(8):
    WATER[y][0] = 15
    WATER[y][7] = 15
WALL = [[8 if (x + y) % 2 else 9 for x in range(8)] for y in range(8)]
for y in range(8):
    WALL[y][0] = 0             # dark mortar lines
    if y in (0, 4):
        for x in range(8):
            WALL[y][x] = 10 if x % 2 else 0
TILES = [FLOOR, FLOWERS, WATER, WALL]


def _write_tileset():
    # 4 tiles stacked 8 wide x 32 tall (the New Project starter layout).
    rows = []
    for t in TILES:
        rows.extend(t)
    write_png_indexed(os.path.join(HERE, "tiles.png"), 8, 32, rows, PALETTE)


def _map():
    """A 20x18 decorated room: stone border, grass floor, a water pond, flower
    patches. Returns (map, collision) as row lists (tile ids / 0-open 1-solid)."""
    W, H = 20, 18
    m = [[0] * W for _ in range(H)]
    col = [[0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if x == 0 or y == 0 or x == W - 1 or y == H - 1:
                m[y][x] = 3
                col[y][x] = 1
    # a water pond (solid) mid-right
    for y in range(4, 8):
        for x in range(12, 17):
            m[y][x] = 2
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
        f.write('\n[world]\nmodule = "scenes"\nmap_w = 20\nmap_h = 18\nvm = true\n')
        f.write('next_object_id = 2\n')
        # The Lynx 4bpp 16-colour background opt-in (doubles Suzy strip RAM;
        # auto-sized here because this is a VM8 game).
        f.write('lynx_bkg16 = true\n')
        f.write('\n[tileset]\npng = "tiles.png"\n')
        f.write('\n[kinds]\nplayer = 0\nnpc = 1\n')

    # Regenerate the scene module + the world wiring (rooms.mos now emits
    # palette.load_bkg16 for the 16-colour tileset).
    import mosaik_scenes
    world, base = mosaik_scenes.load_world(os.path.join(HERE, "world.toml"))
    open(os.path.join(PROJ, "src", "scenes.mos"), "w",
         encoding="utf-8").write(mosaik_scenes.transpile(world, base))
    mosaik_vm.generate_rooms(PROJ)
    print("vm-bkg16: wrote 16-colour tileset + world.toml, regenerated "
          "scenes.mos + rooms.mos")


if __name__ == "__main__":
    main()
