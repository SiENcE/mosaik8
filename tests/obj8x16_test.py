#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""8x16 OBJ sprite mode (`[build] obj_8x16`, the fidelity programme's G4).

The reference engine runs LCDC bit 2 set: every hardware sprite is 8x16, so a WxH-tile
metasprite costs W*(H/2) OAM objects instead of W*H - which is what lets its
7x6-tile big animated actor (42 objects at 8x8, over the GB's 40) draw at 21.

The mode is NOT just the LCDC bit: in 8x16 the hardware pairs tile N with N+1
as its VERTICAL neighbour, while a row-major frame stores the HORIZONTAL
neighbour there - flipping the bit alone scrambles every sprite. The worked
ordering rule: store each frame's tiles COLUMN-major.
Then vertical pairs are consecutive, the data stays fully contiguous, and the
fan is simply w*(h/2) objects taking tiles t, t+2, t+4, ... with every object
index even (h even). `mosaik_assets.reorder_tiles_8x16` does the permutation
per MANIFESTED sub-sprite, so entry offsets (the `<name>_tile` defines and the
clips module's frame bases) never move and the PNGs stay readable.

Scope: honoured on the GB family only (it is an LCDC bit); honestly ignored
elsewhere - and the tile reorder is gated per TARGET in the build, because
reordered tiles under the Lynx/SMS row-major fans would scramble those
consoles. `sprite.set_meta` keeps 8x8-TILE units; only the fan math, the move
layout and generate_rooms' OAM packing know about the mode. Odd tile heights
are REFUSED at asset conversion (a global mode cannot round one sprite).
Off = byte-identical.
"""

from mosaik import MosaikCompiler
import mosaik_assets

META = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
    const T: array[u8, 64] = [0]
    function main() {
        video.enable_lcd()
        sprite.set_data(0, 4, T)
        sprite.set_meta(0, 0, 2, 2)
        sprite.move(0, 40, 40)
        loop { video.wait_vblank() }
    }
    export main
}
'''


def compile_for(src, platform, obj16=False):
    return MosaikCompiler().compile_program([("m.mos", src.strip())],
                                            platform=platform,
                                            obj_8x16=obj16)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("8x16 OBJ sprite mode ([build] obj_8x16)")
    print("=" * 50)
    ok = True

    on = compile_for(META, "gameboy", obj16=True)
    ok &= check("gameboy(on): LCDC bit set at enable_lcd",
                "SPRITES_8x16; DISPLAY_ON" in on)
    ok &= check("gameboy(on): the fan is w*(h/2) objects stepping tiles by 2",
                "n = (uint8_t)(w * (uint8_t)(h >> 1));" in on
                and "++s; t += 2;" in on)
    # The child loop WALKS the grid (the pitch is hoisted out of it, since it
    # is the frame's biggest single cost -- see the emitter's note), so the
    # 8x16 mode shows up as a 16 px row pitch rather than a `pp * 16` product.
    ok &= check("gameboy(on): move lays out 8x16 cells (16 px row pitch)",
                "ys = 16, cy;" in on and "cy = (uint8_t)(cy + ys);" in on)
    ok &= check("gameboy(on): flips reverse columns and 16px pairs",
                "if (prop & FLIP_X) { cx = (uint8_t)(cx + (w - 1) * 8); "
                "xs = (uint8_t)-8; }" in on
                and "if (prop & FLIP_Y) { y0 = (uint8_t)(y0 + (h2 - 1) * 16); "
                "ys = (uint8_t)-16; }" in on)

    off = compile_for(META, "gameboy", obj16=False)
    ok &= check("gameboy(off): byte-identical 8x8 layer (no mode traces)",
                "SPRITES_8x16" not in off and "h >> 1" not in off
                and "ys = 8;" in off)

    # The SMS and the Game Gear have the mode too - VDP R1's sprite-size bit,
    # which pairs consecutive patterns vertically exactly as LCDC bit 2 does -
    # so the same fan serves them. What must NOT come with it is the flip
    # arm: neither console can mirror a sprite in hardware, and reversing the
    # cell order without mirroring the pixels garbles the block.
    for plat in ("sms", "gamegear"):
        c = compile_for(META, plat, obj16=True)
        ok &= check(f"{plat}(on): VDP sprite-size bit set at enable_lcd",
                    "SPRITES_8x16; DISPLAY_ON" in c)
        ok &= check(f"{plat}(on): the fan is w*(h/2) objects stepping tiles by 2",
                    "n = (uint8_t)(w * (uint8_t)(h >> 1));" in c
                    and "++s; t += 2;" in c)
        ok &= check(f"{plat}(on): 64 hardware sprites, not the GB's 40",
                    "#define GBS_META_SLOTS 64" in c)
        ok &= check(f"{plat}(on): the 8x16 fan never reverses cells",
                    "if (prop & FLIP_X) { cx =" not in c
                    and "no hardware sprite flip on this console" in c)
        off = compile_for(META, plat, obj16=False)
        ok &= check(f"{plat}(off): byte-identical 8x8 layer (no mode traces)",
                    "SPRITES_8x16" not in off and "h >> 1" not in off)

    # ...and nowhere else. The Lynx and the PCE size sprites their own way and
    # nothing here drives it, so the flag is honestly ignored rather than
    # half-applied - which would reorder tiles nothing reads that way.
    for plat in ("nes", "lynx"):
        c = compile_for(META, plat, obj16=True)
        ok &= check(f"{plat}: the flag is ignored (no 8x16 traces)",
                    "SPRITES_8x16" not in c and "t += 2" not in c)

    # The data permutation: row-major -> column-major per frame, offsets fixed.
    block = b''.join(bytes([i]) * 16 for i in range(4))       # tiles 0,1,2,3
    out = mosaik_assets.reorder_tiles_8x16(block, 2, 2)
    order = [out[i * 16] for i in range(4)]
    ok &= check("reorder_tiles_8x16: 2x2 row-major -> columns (0,2,1,3)",
                order == [0, 2, 1, 3])
    block = b''.join(bytes([i]) * 16 for i in range(42))      # a big 7x6 actor
    out = mosaik_assets.reorder_tiles_8x16(block, 7, 6)
    ok &= check("reorder_tiles_8x16: 7x6 column 0 is tiles 0,7,14,21,28,35",
                [out[i * 16] for i in range(6)] == [0, 7, 14, 21, 28, 35])

    print("=" * 50)
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
