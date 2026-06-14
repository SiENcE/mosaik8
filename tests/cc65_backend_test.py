#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""cc65 backend (Atari Lynx) code generation.

Verifies that a cc65 console selects the cc65 prelude + stdlib lowering, that the
portable Tier-1 calls (text/input/timing) lower to the cc65/TGI helpers, that
the Lynx-only graphics.draw primitives map to tgi_*, that paradigm-specific
Game Boy calls raise a clear "not supported" error, and that GBDK output is
unaffected by the backend split.
"""

from mosaik import (MosaikCompiler, framework_for_platform,
                               canonical_platform)

PORTABLE = '''
module "main" {
    import "platform.video"
    import "platform.input"
    import "platform.system"
    import "graphics.text"
    function main() {
        video.enable_lcd()
        text.print_string(2, 3, "HI")
        loop {
            if input.pressed(INPUT_A) { text.print_number(2, 5, 42) }
            system.delay(16)
            video.wait_vblank()
        }
    }
    export main
}
'''

DRAW = '''
module "main" {
    import "platform.video"
    function main() {
        video.enable_lcd()
        draw.set_color(2)
        draw.bar(0, 0, 10, 10)
        draw.present()
    }
    export main
}
'''

SPRITE = '''
module "main" {
    import "platform.video"
    import "graphics.sprite"
    const TILE: array[u8, 16] = [255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255]
    function main() {
        video.enable_lcd()
        sprite.set_data(0, 1, TILE)
        sprite.set_tile(0, 0)
        video.show_sprites()
        sprite.move(0, 10, 20)
    }
    export main
}
'''

BKG = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    const TILES: array[u8, 16] = [255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255]
    const MAP: array[u8, 4] = [0,0,0,0]
    function main() {
        bkg.set_data(0, 1, TILES)
        bkg.set_tiles(0, 0, 2, 2, MAP)
        bkg.move(5, 9)
        bkg.scroll(1, 0)
        video.wait_vblank()
    }
    export main
}
'''

WINDOW = '''
module "main" {
    import "graphics.window"
    function main() { window.move(7, 100) }
    export main
}
'''

# A background + a *static* sprite (a fixed HUD-style sprite that never moves).
# On the Lynx this is the case that broke once: when the bkg present was made
# single-buffered, the bkg was drawn then the sprite straight to the visible
# page, so a static sprite over a static camera could be torn off the displayed
# frame (the colorlab gems / the zelda HUD vanished). The fix is to keep the
# present double-buffered so every shown frame is complete.
BKG_SPR = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    import "graphics.sprite"
    const TILES: array[u8, 16] = [255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255]
    const MAP: array[u8, 4] = [0,0,0,0]
    function main() {
        bkg.set_data(0, 1, TILES)
        bkg.set_tiles(0, 0, 2, 2, MAP)
        sprite.set_data(0, 1, TILES)
        sprite.set_tile(0, 0)
        sprite.move(0, 40, 40)
        video.show_background()
        video.show_sprites()
        loop { video.wait_vblank() }
    }
    export main
}
'''


def compile_for(src, platform):
    return MosaikCompiler().compile(src.strip(), platform=platform)


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


def main():
    print("cc65 backend (Atari Lynx + PC Engine)")
    print("=" * 50)
    ok = True

    # Platform -> framework routing.
    ok &= check("lynx maps to cc65 framework",
                framework_for_platform("lynx") == "cc65"
                and framework_for_platform("atari_lynx") == "cc65")
    ok &= check("gameboy stays on gbdk framework",
                framework_for_platform("gameboy") == "gbdk")

    # cc65 prelude + Tier-1 lowering.
    ly = compile_for(PORTABLE, "lynx")
    ok &= check("lynx selects cc65 prelude",
                "cc65 C backend" in ly and "#include <tgi.h>" in ly)
    ok &= check("text lowers to TGI helper", "tgi_outtextxy" in ly)
    ok &= check("input lowers to joy_read", "joy_read(0)" in ly)
    ok &= check("delay lowers to clock-based helper",
                "gbs_delay" in ly and "clock()" in ly)
    ok &= check("print_string call lowered",
                "gbs_print_string(2, 3" in ly)

    # Lynx-only graphics.draw primitives.
    dr = compile_for(DRAW, "lynx")
    ok &= check("draw.* maps to tgi_*",
                "tgi_setcolor(2)" in dr and "tgi_bar(0, 0, 10, 10)" in dr
                and "tgi_updatedisplay()" in dr)

    # Sprites: on Lynx they map to the Suzy hardware sprite engine (SCB +
    # tgi_sprite), keeping the same gbs_* API names as the old software engine.
    sp = compile_for(SPRITE, "lynx")
    ok &= check("sprite.* maps to Suzy hardware engine on lynx",
                "gbs_set_sprite_data(0, 1" in sp and "gbs_move_sprite(0, 10, 20)" in sp
                and "SCB_REHV_PAL gbs_scb" in sp and "tgi_sprite(&gbs_scb[s])" in sp
                and "gbs_show_sprites" in sp)

    # graphics.bkg on the Lynx: the Suzy background engine composites only the
    # visible window of the GB tilemap into one small literal background sprite
    # that present() recomposites on tile-boundary crossings and re-blits at the
    # sub-tile scroll offset (the windowed scrolling technique).
    bkg_ly = compile_for(BKG, "lynx")
    ok &= check("bkg.* maps to the Suzy background engine on lynx",
                "Suzy background-tilemap engine" in bkg_ly
                and "gbs_set_bkg_data(0, 1" in bkg_ly
                and "gbs_set_bkg_tiles(0, 0, 2, 2" in bkg_ly
                and "gbs_move_bkg(5, 9)" in bkg_ly
                and "TYPE_BACKNONCOLL" in bkg_ly
                and "gbs_bkg_scb" in bkg_ly
                and "GBS_BKG_WIN_W" in bkg_ly)
    ok &= check("lynx present blits the single windowed background sprite",
                "tgi_sprite(&gbs_bkg_scb)" in bkg_ly
                and "gbs_bkg_compose(tx0, ty0)" in bkg_ly)
    # ...but only for programs that import graphics.bkg: everything else
    # keeps the engine (and its ~21 KB of RAM) out of the binary.
    ok &= check("lynx programs without graphics.bkg omit the engine",
                "gbs_bkg_sprite" not in compile_for(SPRITE, "lynx"))

    # REGRESSION GUARD: the Lynx present must stay DOUBLE-buffered even with
    # graphics.bkg active, so a static sprite over a static camera (a fixed HUD,
    # the colorlab gems) is never torn off the displayed frame. A past change
    # single-buffered the bkg present (drew bkg then sprites to the *visible*
    # page, no flip) and made those sprites vanish on the Lynx. The windowed
    # composite is cheap enough to repaint + flip every frame, so the flip must
    # be unconditional -- do not re-introduce a single-buffer-when-bkg path.
    bkg_spr_ly = compile_for(BKG_SPR, "lynx")
    ok &= check("lynx bkg+sprite present is double-buffered (flips every frame)",
                "tgi_updatedisplay();" in bkg_spr_ly
                and "if (!gbs_spr_db) { tgi_setdrawpage(1)" in bkg_spr_ly)
    ok &= check("lynx bkg present has no single-buffer-when-bkg path",
                "single-buffered: pace, no flip" not in bkg_spr_ly
                and "!gbs_bkg_used && !gbs_spr_db" not in bkg_spr_ly
                and "if (gbs_bkg_used) {" not in bkg_spr_ly)

    # The window layer still has no cc65 equivalent -> clear compile error.
    err = compile_for(WINDOW, "lynx")
    ok &= check("window.* unsupported on lynx gives clear error",
                err.startswith("Compilation error:")
                and "not supported on target 'lynx'" in err)

    # The same sprite program still compiles on Game Boy (screen-pixel coords
    # via the gbs_move_sprite wrapper).
    gb_sprite = compile_for(SPRITE, "gameboy")
    ok &= check("sprite.* still works on gameboy",
                "gbs_move_sprite(0, 10, 20)" in gb_sprite
                and "DEVICE_SPRITE_PX_OFFSET_X" in gb_sprite)

    # GBDK output unaffected by the split (no cc65 artefacts leak in).
    gb = compile_for(PORTABLE, "gameboy")
    ok &= check("gameboy uses GBDK prelude, no cc65 leakage",
                "GBDK C backend" in gb and "tgi_" not in gb
                and "#include <gbdk/platform.h>" in gb)

    # Second cc65 console (PC Engine) reuses the backend with a conio text
    # profile -- proving the cc65 backend is data-driven across consoles.
    ok &= check("pce maps to cc65 framework",
                framework_for_platform("pce") == "cc65"
                and framework_for_platform("pc_engine") == "cc65")
    pce = compile_for(PORTABLE, "pce")
    ok &= check("pce uses conio profile, not TGI",
                "#include <conio.h>" in pce and "#include <pce.h>" in pce
                and "gotoxy(x, y); cputs(s)" in pce and "tgi_" not in pce)
    ok &= check("pce shares the same input lowering",
                "joy_read(0)" in pce and "gbs_print_string(2, 3" in pce)
    # graphics.draw (TGI) stays unavailable on the tile-based PC Engine.
    pce_draw = compile_for(DRAW, "pce")
    ok &= check("draw.* unsupported on pce gives clear error",
                pce_draw.startswith("Compilation error:")
                and "not supported on target 'pce'" in pce_draw)
    # Sprites on the PCE map to the VDC hardware engine (tile patterns in
    # VRAM + a SATB mirror flushed on gbs_present), same gbs_* API names.
    pce_sprite = compile_for(SPRITE, "pce")
    ok &= check("sprite.* maps to the VDC hardware engine on pce",
                "VDC hardware sprite engine" in pce_sprite
                and "gbs_set_sprite_data(0, 1" in pce_sprite
                and "gbs_move_sprite(0, 10, 20)" in pce_sprite
                and "GBS_VRAM_SATB" in pce_sprite
                and "gbs_vreg(19, GBS_VRAM_SATB)" in pce_sprite)
    ok &= check("pce sprite engine keeps screen-pixel coords (SATB +32/+64)",
                "(uint16_t)(64u + y)" in pce_sprite
                and "(uint16_t)(32u + x)" in pce_sprite)
    ok &= check("pce present flushes the SATB mirror then waits for vblank",
                "waitvsync();" in pce_sprite
                and "GBS_VDC_DH = (uint8_t)(gbs_satb[i] >> 8);" in pce_sprite)
    # graphics.bkg on the PCE: real tilemap hardware -- BAT entries pointing
    # at 4bpp characters above the sprite patterns, scrolled via BXR/BYR.
    bkg_pce = compile_for(BKG, "pce")
    ok &= check("bkg.* maps to the VDC background engine on pce",
                "VDC background-tilemap engine" in bkg_pce
                and "GBS_VRAM_BKG 0x4000u" in bkg_pce
                and "gbs_vreg(7, x);  /* BXR */" in bkg_pce
                and "gbs_vreg(8, y);  /* BYR */" in bkg_pce)
    ok &= check("pce programs without graphics.bkg omit the engine",
                "GBS_VRAM_BKG" not in compile_for(SPRITE, "pce"))

    print()
    if ok:
        print("All cc65 backend checks passed")
        return 0
    print("cc65 backend checks FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
