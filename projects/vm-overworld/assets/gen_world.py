#!/usr/bin/env python3
"""Generate vm-overworld -- a BIGGER VM8 map: a full 32x32 (256x256 px) scrolling
top-down overworld with a VARIED 8-tile background tileset. It is the rich sibling of
`vm-scroll` (which scrolls a 32x28 room of only 3 flat tiles + the lone player) -- a
real overworld: grass / path / trees / a lake / flowers / rocks, a follow camera that
scrolls the whole map as the native player walks, and three interactable NPCs (a
villager, a dog, a sign) whose On Interact slots (vm.entity) open a dialogue box.

The player + NPCs are 16x16 METASPRITES from the imported sheet `assets/sprites.png`
(+ `assets/sprites.sprites.toml`, registered in mosaik.toml `[assets] sprites`) -- the
shell uploads it + draws via vm.player/vm.actor's opt-in metasprite support. This
script only generates the MAP (tiles.png + world.toml + the dialogue scripts); it does
NOT touch the sprite sheet. Targets gb/gbc/sms/gg/pce (the Lynx is excluded -- the
16x16 sheet + scrolling bkg engine overflow its tight MAIN).

A non-wide room is capped at the 32-tile hardware tilemap (it scrolls via SCX/SCY
wrap, one `scenes.paint` upload), so 32x32 is the biggest single scrolling room the
follow-camera path allows on BOTH the GB family and the Lynx. Author it as DATA:
world.toml (the map + collision + the placed NPCs) + scripts (the NPC dialogue), then
the transpilers emit src/scenes.mos + src/scripts.mos. Run:
  python projects/vm-overworld/assets/gen_world.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
ROOT = os.path.dirname(os.path.dirname(PROJ))
sys.path.insert(0, ROOT)           # so `import mosaik_assets` resolves

W, H = 32, 32                      # tiles per room (256 x 256 px, > every screen)

# ---- the 8-tile tileset, each an 8x8 grid of palette indices (0..3) ----
# palette: 0 = lightest, 3 = darkest. Patterns must read distinctly in 4 greys.
GRASS = ["00000000", "00010000", "00000000", "00000100",
         "00000000", "01000000", "00000000", "00000010"]   # 0 sparse dither
PATH  = ["11111111", "11211121", "11111111", "12111112",
         "11111111", "11211121", "11111111", "12111112"]   # 1 light speckle
TREE  = ["00300300", "03333330", "33333333", "33333333",
         "03333330", "00033000", "00033000", "00333300"]   # 2 canopy + trunk (SOLID)
WATER = ["11111111", "22222222", "11111111", "22122212",
         "11111111", "22222222", "11211121", "22222222"]   # 3 ripples (SOLID)
FLOWER= ["00000000", "00030000", "00303000", "00030000",
         "00001000", "00001000", "00011000", "00000000"]   # 4 a bloom
ROCK  = ["00000000", "00022000", "00233200", "02333320",
         "02333320", "00233200", "00000000", "00000000"]   # 5 boulder (SOLID)
SAND  = ["01000010", "00000000", "10000001", "00001000",
         "00100000", "00000010", "01000000", "00010000"]   # 6 shore speckle
BUSH  = ["00022000", "00222200", "02222220", "22222222",
         "02222220", "00222200", "00022000", "00000000"]   # 7 shrub (SOLID)
TILES = [GRASS, PATH, TREE, WATER, FLOWER, ROCK, SAND, BUSH]
SOLID = {2, 3, 5, 7}               # tree / water / rock / bush block the player


def build_map():
    """Paint the overworld: grass base, a tree border + forest, a lake ringed by
    sand, crossing paths, and scattered flowers/rocks."""
    m = [[0] * W for _ in range(H)]
    # tree border (solid frame so the player stays on the map)
    for x in range(W):
        m[0][x] = m[H - 1][x] = 2
    for y in range(H):
        m[y][0] = m[y][W - 1] = 2
    # a lake (water) ringed by sand, top-right
    for y in range(4, 11):
        for x in range(20, 28):
            m[y][x] = 3
    for y in range(3, 12):
        for x in range(19, 29):
            if m[y][x] == 0:
                m[y][x] = 6                    # sand shore
    # a forest cluster, bottom-left
    for (y, x) in [(24, 4), (24, 5), (25, 4), (26, 6), (27, 5), (25, 7),
                   (28, 4), (26, 4), (23, 6), (27, 8), (28, 7)]:
        m[y][x] = 2
    # crossing paths (a + through the middle)
    for x in range(1, W - 1):
        m[16][x] = 1
    for y in range(1, H - 1):
        if m[y][15] == 0:
            m[y][15] = 1
    # scattered flowers + rocks on the grass
    for (y, x) in [(6, 6), (9, 10), (12, 5), (20, 22), (22, 9), (5, 13), (27, 20)]:
        if m[y][x] == 0:
            m[y][x] = 4
    for (y, x) in [(8, 24), (19, 6), (21, 25), (13, 26), (29, 12)]:
        if m[y][x] == 0:
            m[y][x] = 5
    return m


def rows(grid):
    return "[" + ", ".join("[" + ",".join(str(v) for v in r) + "]" for r in grid) + "]"


def main():
    m = build_map()

    # ---- tileset PNG (8 tiles stacked vertically: 8 x 64 px) ----
    from mosaik_assets import write_png_indexed
    pal = [(248, 248, 248), (168, 168, 168), (88, 88, 88), (8, 8, 8)]
    pixels = []
    for tile in TILES:
        for r in tile:
            pixels.extend(int(ch) for ch in r)
    os.makedirs(os.path.join(PROJ, "assets"), exist_ok=True)
    write_png_indexed(os.path.join(PROJ, "assets", "tiles.png"), 8, 8 * len(TILES),
                      pixels, pal)

    # ---- world.toml (the map + collision + the placed NPCs) ----
    world = []
    world.append("# vm-overworld -- GENERATED by assets/gen_world.py (edit that + rerun).")
    world.append('[[scene]]')
    world.append('name = "overworld"')
    world.append("map = %s" % rows(m))
    world.append('[[scene.object]]\nkind = "player"\nid = 0\nx = 120\ny = 128')
    # three NPCs on/near the paths, each with an On Interact dialogue
    world.append('[[scene.object]]\nkind = "villager"\nid = 1\nx = 96\ny = 128'
                 '\non_interact = "villager_talk"')
    world.append('[[scene.object]]\nkind = "dog"\nid = 2\nx = 160\ny = 96'
                 '\non_interact = "dog_talk"')
    world.append('[[scene.object]]\nkind = "sign"\nid = 3\nx = 56\ny = 184'
                 '\non_interact = "sign_talk"')
    world.append("")
    world.append('[world]\nmodule = "scenes"\nmap_w = %d\nmap_h = %d\nvm = true'
                 '\nnext_object_id = 4' % (W, H))
    world.append('[tileset]\npng = "assets/tiles.png"')
    # TILE-BASED collision (a solid-tile set, not a per-cell array) -- the per-cell
    # 32x32 layer is ~1 KB of RODATA that overflows cc65's single Lynx MAIN. The set
    # exports scenes.is_solid(tid); solid_at() reads the visual tile + tests it.
    world.append('[collision]\nsolid = [%s]' % ", ".join(str(t) for t in sorted(SOLID)))
    world.append('[kinds]\nplayer = 0\nvillager = 1\ndog = 2\nsign = 3')
    with open(os.path.join(PROJ, "world.toml"), "w", encoding="utf-8") as f:
        f.write("\n".join(world) + "\n")

    # ---- scripts/*.evt.toml (the NPC dialogue) ----
    os.makedirs(os.path.join(PROJ, "scripts"), exist_ok=True)
    # Each NPC dialogue LOCKs the player first: the map SCROLLS, and UI_TEXT is
    # non-freezing, so without a lock the camera would slide the (fixed-cell) box
    # while it is open. Lock freezes the camera -> the box stays put while reading.
    events = '''# vm-overworld logic -- the NPC On Interact dialogues (framed text boxes).
[[script]]
name = "main"
events = [ { event = "stop" } ]

[[script]]
name = "villager_talk"
events = [
  { event = "lock" },
  { event = "text", string = "WELCOME TO\\nTHE OVERWORLD!" },
  { event = "text", string = "MIND THE LAKE\\nTO THE EAST." },
  { event = "unlock" },
  { event = "stop" },
]

[[script]]
name = "dog_talk"
events = [ { event = "lock" }, { event = "text", string = "WOOF!" }, { event = "unlock" }, { event = "stop" } ]

[[script]]
name = "sign_talk"
events = [ { event = "lock" }, { event = "text", string = "FOREST -- KEEP\\nOUT (TREES)." }, { event = "unlock" }, { event = "stop" } ]
'''
    with open(os.path.join(PROJ, "scripts", "main.evt.toml"), "w",
              encoding="utf-8") as f:
        f.write(events)

    # ---- transpile ----
    import subprocess
    subprocess.run([sys.executable, os.path.join(ROOT, "mosaik_scenes.py"),
                    os.path.join(PROJ, "world.toml"),
                    "-o", os.path.join(PROJ, "src", "scenes.mos")], check=True, cwd=ROOT)
    subprocess.run([sys.executable, os.path.join(ROOT, "mosaik_vm.py"),
                    os.path.join(PROJ, "scripts"),
                    "-o", os.path.join(PROJ, "src", "scripts.mos")], check=True, cwd=ROOT)
    print("vm-overworld generated (32x32 map, %d tiles, 3 NPCs)" % len(TILES))


if __name__ == "__main__":
    main()
