#!/usr/bin/env python3
"""Generate the bigworld-paint fixture: tiles.png + world.toml + src/gen_data.mos.

The paint-interpreter sibling of `projects/bigworld`, extended to push EVERY
per-console hard cap at once alongside the many-room residency lever: a Lynx-targeted world with `[world] stream = true`
AND `[world] paint_table = true` (the stream + paint-table compose), while
ALSO exercising:

  * up to 255 distinct background tiles (the u8-upload-count ceiling; "256" in the
    docs is the nominal cap, 255 is the actual hard limit -- see mosaik_scenes.py),
    each tile's first row bit-encodes its own id so all 255 are provably distinct;
  * up to 40 distinct sprite tiles (the Lynx GBS_MAX_TILES ceiling: 1 player + 1
    shopkeeper + 38 actor kinds, same row0 bit-encoding trick);
  * up to 4 placed actor objects per room (one room's first actor is the
    shopkeeper; every other room's actors are plain wandering-flavour NPCs);
  * up to 256 unique on-screen strings: N_SCENES per-room "sign" lines (shown on
    A-press) + a "codex" browser (reachable from the pause menu, Up/Down cycles
    entries) filling the rest, so sign+codex together approach 256 total;
  * a MENU (a B-button pause menu: Resume / Codex / Quit-to-start) and a SHOP (in
    the designated shop room: Buy Potion / Leave, spending a `gold` counter),
    both over the shared `engine.menu` + `engine.box` kits.

All of this costs RESIDENT CODE regardless of paint_table (only the per-room
map/collision DISPATCH is O(1) now) -- so where `bigworld-paint` alone reached
120 rooms, adding background/sprite/string/menu/shop breadth pushes back against
the Lynx's ~46.6 KB MAIN, and the room count must give way. Tune with
`BWP_SCENES` (env) to find the current fit; this file's default is the last
value verified to build + boot on the Lynx (see mosaik.toml for the measured
numbers). If it still overflows, LOWER `BWP_SCENES` -- the per-room cost (map,
doors, 4 actor objects) is now O(1) code (paint_table), so trimming rooms trims
DATA, not the fixed feature cost that must fit regardless of room count.

Run, then transpile:
    python projects/bigworld-paint/assets/gen_world.py
    python mosaik_scenes.py projects/bigworld-paint/world.toml -o projects/bigworld-paint/src/scenes.mos
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from mosaik_assets import write_png_indexed
import toml

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)

PAL = [(248, 248, 248), (168, 168, 168), (96, 96, 96), (0, 0, 0)]
FLOOR, DECO, DOOR, WALL = 0, 1, 2, 3


def _luma(rgb):
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def encode_ui_tiles(png_path, max_tiles=None):
    """Encode a <=4-colour indexed PNG to GB 2bpp, BRIGHTNESS-RANKED (lightest
    palette colour -> 2bpp 0 = paper, darkest -> 3 = ink), spreading the used
    colours across the full 0..3 range. This is how the studio's custom font +
    9-slice frame are emitted INLINE (brightness ranking, not the sprite
    pipeline's global luma thresholds) so they render with contrast under ANY
    background palette. Byte-identical to ui-quest's composed FONT_TILES/FRAME
    tiles (validated). Returns a flat byte list (16 per 8x8 tile, row-major)."""
    from PIL import Image
    im = Image.open(png_path).convert("P")
    pal = im.getpalette()
    w, h = im.size
    px = im.load()
    used = sorted({px[x, y] for y in range(h) for x in range(w)})
    light = sorted(used, key=lambda idx: _luma(pal[idx * 3:idx * 3 + 3]), reverse=True)
    n = len(light)
    rank = {idx: (round(i * 3 / (n - 1)) if n > 1 else 0) for i, idx in enumerate(light)}
    out, cnt = [], 0
    for ty in range(h // 8):
        for tx in range(w // 8):
            if max_tiles and cnt >= max_tiles:
                break
            for row in range(8):
                b0 = b1 = 0
                for col in range(8):
                    v = rank[px[tx * 8 + col, ty * 8 + row]]
                    b0 |= (v & 1) << (7 - col)
                    b1 |= ((v >> 1) & 1) << (7 - col)
                out += [b0, b1]
            cnt += 1
    return out

# Screen-sized rooms (no scroll needed): a small range-cache window, and MANY
# rooms fit the u16 65,535-cell concatenated-array cap.
W, H = 20, 18

# ---- The four caps being pushed at once -----------------------------------
N_TILES = 255                  # background tiles the PNG HOLDS (u8-upload ceiling)
# The background tiles the maps may actually REFERENCE + upload are capped well
# below 255 by the per-console VRAM layout (docs/vram-layout.md). The BINDING
# console is SMS/GG: their GB-compat layout gives the background only 192
# pattern slots (tiles 0..191 -- the name table lives at VRAM 0x1800 = tiles
# 192..247, the SAT at 0x1F00 = 248..255), and the demo's custom FONT (96
# glyphs, relocated to 96..191 via text.set_font_at) + 9-slice FRAME (87..95)
# leave exactly 87 for the tileset. (The GB family could reference 128 -- font
# in block 1 ~id 139, frame at 247 -- but the maps are shared across consoles;
# the Lynx has no aliasing at all, there all 255 render.)
VRAM_BG_TILES = 87             # bg tile ids the maps may reference (SMS/GG budget)
N_SPRITE_TILES = 40            # Lynx GBS_MAX_TILES cap (1 player + 1 shop + 38 actors)
N_ACTOR_KINDS = N_SPRITE_TILES - 2   # 38 distinct actor sprite kinds (ids 2..39)
MAX_ACTORS_PER_ROOM = 4        # placed actor objects per room (the OAM/room budget)
SHOP_ROOM = 0                  # the room whose first actor is the shopkeeper

# How many rooms. 44 is the CROSS-CONSOLE default: it builds with the full
# feature set on gb / gbc / sms / gamegear (paint_table+stream banks the two
# concatenated arrays into 2 ROM banks -- the ceiling is 45, where one array
# would exceed a single 16 KB bank) AND on the Lynx (cart archive). The Lynx
# ALONE reaches 63 rooms (its cart archive has no per-bank cap) -- rebuild with
# `BWP_SCENES=63` + `target_platforms = ["lynx"]` for that max-caps run. The PC
# Engine (no banking yet -> resident 32 KB HuCard) caps ~24 rooms; the NES is
# excluded (its printf overflows NROM with this much text). See mosaik.toml for
# the full per-console ceiling table. Override with $BWP_SCENES to re-sweep.
N_SCENES = int(os.environ.get("BWP_SCENES", "44"))
# Unique on-screen strings: N_SCENES per-room signs + a codex filling the rest.
# 37 codex (=> 81 total strings at 44 rooms) is the cross-console fit once the
# custom FONT + 9-slice FRAME + a 4-grey palette + the Lynx's TGI overlay box
# (text.fill_box, a crisp pixel-bordered menu vs. the ASCII frame, with the
# present-then-draw freeze so it appears on open + stays flicker-free) are added
# -- they cost resident code on the tight Lynx MAIN, which is the binding target.
# Override with $BWP_CODEX to re-sweep (the Lynx alone still reaches far more with
# the feature set trimmed -- see mosaik.toml).
N_CODEX = int(os.environ.get("BWP_CODEX", "37"))


def _tile_row0(i):
    """8 pixel-index values (0 or 3) bit-encoding `i` (0..255) -- the top row of
    tile `i`'s data, guaranteeing every tile in 0..N_TILES-1 is byte-distinct."""
    return [3 if (i >> (7 - b)) & 1 else 0 for b in range(8)]


def make_tiles_png():
    """A 255-tile shared background tileset (8 x (255*8) px, one tile per 8-row
    band). Tiles 0..3 (FLOOR/DECO/DOOR/WALL) are SOLID palette shades -- the
    same look as projects/bigworld, so rooms render readably (light floor, dark
    walls) instead of a uniform grey mush. Every variety tile 4.. keeps row 0
    bit-encoding its own id over a DECO-grey body, so all 255 tiles stay
    byte-distinct by construction (the u8-upload capacity proof): the solids are
    distinct colours, and every id >= 4 has a set bit, so its row 0 contains a
    WALL-dark pixel no solid tile's row shares."""
    idx = []
    for i in range(N_TILES):
        if i < 4:
            idx.extend([i] * 64)
        else:
            idx.extend(_tile_row0(i))
            for _ in range(7):
                idx.extend([1] * 8)
    write_png_indexed(os.path.join(PROJ, "tiles.png"), 8, N_TILES * 8, idx, PAL)


def _sprite_tile_bytes(i):
    """16 raw GB-2bpp bytes for sprite tile `i` (0..39): row0 = (i, i) (low ==
    high plane -> a two-tone bit-pattern of `i`'s own byte, so all 40 tiles are
    distinct), rows 1-7 = a shared 14-byte 'blob' body (cosmetic only)."""
    b = i & 0xFF
    body14 = [0x7E, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
             0xFF, 0xDB, 0xDB, 0x7E, 0x3C, 0x3C]
    return [b, b] + body14


def room(index, left_door, right_door):
    """A walled room; left/right doorways connect neighbours. Interior floor
    cells cycle through the DECORATIVE tile-id range (4..N_TILES-1) via a
    GLOBAL running counter (closed over `_variety`), so across the whole world
    the full 251-tile variety range gets referenced, not just the 4 special ids."""
    door_rows = (H // 2 - 1, H // 2)
    m = []
    for y in range(H):
        row_vals = []
        for x in range(W):
            border = x == 0 or y == 0 or x == W - 1 or y == H - 1
            if left_door and x == 0 and y in door_rows:
                row_vals.append(DOOR)
            elif right_door and x == W - 1 and y in door_rows:
                row_vals.append(DOOR)
            elif border:
                row_vals.append(WALL)
            elif (x + y + index) % 7 == 0:
                # every 7th interior cell: the next variety tile in rotation,
                # kept within the VRAM background budget so it never aliases the
                # font/frame region on the GB family.
                vid = 4 + (_variety[0] % (VRAM_BG_TILES - 4))
                _variety[0] += 1
                row_vals.append(vid)
            else:
                row_vals.append(FLOOR)
        m.append(row_vals)
    return m


_variety = [0]   # global counter closed over by room() (module-level, mutable)


def collision_for(m):
    return [[1 if c == WALL else 0 for c in row_vals] for row_vals in m]


def _actor_kinds_for_room(i):
    """The MAX_ACTORS_PER_ROOM kind ids placed in room `i`: SHOP_ROOM's first
    actor is the shopkeeper (kind 1); every other actor cycles through the 38
    plain actor kinds (ids 2..39), varied per room so the roster gets exercised."""
    kinds = []
    for slot in range(MAX_ACTORS_PER_ROOM):
        if i == SHOP_ROOM and slot == 0:
            kinds.append(1)                       # the shopkeeper
        else:
            k = 2 + ((i * MAX_ACTORS_PER_ROOM + slot) % N_ACTOR_KINDS)
            kinds.append(k)
    return kinds


def _actor_positions():
    """Fixed interior positions (pixels) for up to 4 actors, spread so they
    don't overlap the player's spawn or each other (screen-sized 20x18 room)."""
    return [(48, 96), (112, 96), (48, 136), (112, 136)]


def make_gen_data_mos():
    """The generated companion module: SPRITE_TILES (40 tiles' raw bytes) +
    sign_text(id, c, r) / codex_line(id, c, r) -- one switch case per unique
    string, so the compiler's real C `switch` dispatches in O(1), not per-case
    comparisons (mirrors the composer-era shop_label dedup registry)."""
    lines = []
    lines.append("-- gen_data.mos -- GENERATED by assets/gen_world.py; do not edit by hand.")
    lines.append("-- Sprite tile data (40 distinct tiles) + the sign/codex string tables --")
    lines.append("-- moved out of main.mos so the hand-written game loop stays readable.")
    lines.append("")
    lines.append('module "gen_data" {')
    lines.append('    import "graphics.text"')
    lines.append("")
    lines.append("    const CODEX_COUNT: u8 = %d" % N_CODEX)
    lines.append("")
    all_bytes = []
    for i in range(N_SPRITE_TILES):
        all_bytes.extend(_sprite_tile_bytes(i))
    lines.append("    const SPRITE_TILES: array[u8, %d] = [" % len(all_bytes))
    for i in range(0, len(all_bytes), 16):
        chunk = ", ".join(str(b) for b in all_bytes[i:i + 16])
        tail = "," if i + 16 < len(all_bytes) else ""
        lines.append("        %s%s" % (chunk, tail))
    lines.append("    ]")
    lines.append("")

    # Custom FONT (96 glyphs, ASCII 32..127) + 9-slice FRAME (3x3), inline +
    # brightness-ranked (colour 0 = paper) so they render under any bkg palette --
    # the ui-quest / slice9-frame feature, ported to this hand-written demo.
    font = encode_ui_tiles(os.path.join(HERE, "font.png"), max_tiles=96)
    frame = encode_ui_tiles(os.path.join(HERE, "frame.png"))

    def _emit_array(name, data, indent="    "):
        lines.append("%sconst %s: array[u8, %d] = [" % (indent, name, len(data)))
        for i in range(0, len(data), 16):
            chunk = ", ".join(str(b) for b in data[i:i + 16])
            tail = "," if i + 16 < len(data) else ""
            lines.append("%s    %s%s" % (indent, chunk, tail))
        lines.append("%s]" % indent)

    # The GB family + SMS/GG use the custom font + 9-slice frame; the Lynx/PCE keep
    # their own font + the ASCII box, so emit only TINY stubs there (the full 1.7 KB
    # of tiles would overflow the tight Lynx MAIN). Module-level `if platform`
    # conditional-compiles to keep just the matching branch.
    lines.append("    -- Custom 96-glyph font (ASCII 32..) + 9-slice frame (3x3),")
    lines.append("    -- inline brightness-ranked 2bpp. Lynx/PCE get stubs (unused there).")
    lines.append('    if platform == "lynx" or platform == "pce" {')
    _emit_array("FONT_TILES", [0] * 16, indent="        ")
    _emit_array("FRAME_TILES", [0] * 16, indent="        ")
    lines.append("    } else {")
    _emit_array("FONT_TILES", font, indent="        ")
    _emit_array("FRAME_TILES", frame, indent="        ")
    lines.append("    }")
    lines.append("")
    lines.append("    -- One unique short string per room (%d total), shown on A-press." % N_SCENES)
    lines.append("    -- BANKED (bank 1): the ~%d string literals + switch code are ~2.5 KB;" % (N_SCENES + N_CODEX))
    lines.append("    -- resident they overflow the 16 KB GB home bank past 0x4000, where the")
    lines.append("    -- linker SILENTLY overlaps them with the banked MAPS data = a runtime")
    lines.append("    -- crash (LCD off). bank(1) far-calls switch + restore around the call, so")
    lines.append("    -- the data-seam SWITCH_ROM interplay is safe. Ignored on the Lynx/PCE.")
    lines.append("    bank(1) function sign_text(id: u8, c: u8, r: u8) {")
    lines.append("        switch id {")
    for i in range(N_SCENES):
        lines.append('            case %d { text.print_string(c, r, "S%03d") }' % (i, i))
    lines.append('            default { text.print_string(c, r, "S---") }')
    lines.append("        }")
    lines.append("    }")
    lines.append("")
    lines.append("    -- The codex browser's %d entries (Up/Down cycles; from the pause menu)." % N_CODEX)
    lines.append("    bank(1) function codex_line(id: u8, c: u8, r: u8) {")
    lines.append("        switch id {")
    for i in range(N_CODEX):
        lines.append('            case %d { text.print_string(c, r, "L%03d") }' % (i, i))
    lines.append('            default { text.print_string(c, r, "L---") }')
    lines.append("        }")
    lines.append("    }")
    lines.append("")
    lines.append("    export CODEX_COUNT, SPRITE_TILES, FONT_TILES, FRAME_TILES, sign_text, codex_line")
    lines.append("}")
    out = os.path.join(PROJ, "src", "gen_data.mos")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return out


def main():
    make_tiles_png()
    door_row = (H // 2) * 8
    positions = _actor_positions()

    scenes, doors = [], []
    for i in range(N_SCENES):
        has_left = i > 0
        has_right = i < N_SCENES - 1
        m = room(i, has_left, has_right)
        objects = []
        if i == 0:
            objects.append({"kind": "player", "x": 80, "y": door_row})
        for slot, kind_id in enumerate(_actor_kinds_for_room(i)):
            px, py = positions[slot]
            name = "shopkeeper" if kind_id == 1 else "k%d" % kind_id
            objects.append({"kind": name, "x": px, "y": py})
        scenes.append({
            "name": "room%03d" % i,
            "map": m,
            "collision": collision_for(m),
            "object": objects,
        })
        if has_right:
            doors.append({"from": "room%03d" % i, "tx": W - 1, "ty": H // 2,
                          "to": "room%03d" % (i + 1), "ex": 16, "ey": door_row})
        if has_left:
            doors.append({"from": "room%03d" % i, "tx": 0, "ty": H // 2,
                          "to": "room%03d" % (i - 1), "ex": (W - 3) * 8, "ey": door_row})

    kinds = {"player": 0, "shopkeeper": 1}
    for k in range(2, N_SPRITE_TILES):
        kinds["k%d" % k] = k

    world = {
        "world": {"module": "scenes", "map_w": W, "map_h": H,
                  "stream": True, "paint_table": True},
        "tileset": {"png": "tiles.png"},
        "kinds": kinds,
        "scene": scenes,
        "door": doors,
    }
    out = os.path.join(PROJ, "world.toml")
    with open(out, "w", encoding="utf-8") as f:
        toml.dump(world, f)
    gen_data_path = make_gen_data_mos()
    print("wrote %s (%d scenes, %d doors, %d cells/array, %d bg tiles, "
          "%d sprite tiles, %d actors, %d signs, %d codex entries)"
          % (out, len(scenes), len(doors), N_SCENES * W * H, N_TILES,
             N_SPRITE_TILES, N_SCENES * MAX_ACTORS_PER_ROOM, N_SCENES, N_CODEX))
    print("wrote %s" % gen_data_path)


if __name__ == "__main__":
    main()
