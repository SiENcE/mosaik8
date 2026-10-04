#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""SMS / Game Gear name-table clear (the z80 ports leave VRAM uninitialised).

Unlike the Game Boy crt0, GBDK's SMS/GG port does not blank the VDP name table
before main(), so every cell a program never paints showed power-on VRAM garbage
-- which, once a font/tileset was loaded, rendered as scattered stray glyphs (the
"wrong tiles / mixed up with font" symptom on text_simple, the side-scroller sample, ...).

The fix (see codegen/gbdk.py + codegen/generator.py): emit a gbs_sms_clear_bkg()
helper on SMS/GG and call it as the FIRST statement of main(), mirroring the GB
crt0. For TEXT programs the helper first runs gbs_text_init() *before* the clear,
because the SMS stdio console (printf/gotoxy) re-garbles the name table if it
initialises after the fill -- so the console must be set up ahead of the wipe.

Because that text init now runs EAGERLY (before any bkg.set_data), the SMS/GG
font init must NOT use the GB-family trick of two throwaway font_loads (to push
the active font past tile ~132): on the z80 port those extra loads corrupt the
subsequent set_bkg_data so the font glyphs bleed across the whole background
(the side-scroller sample / colors showed scattered glyphs). SMS/GG load the font once
(font_set(font_load(font_ibm))); NES keeps the padded path (it uses lazy init).

Only SMS/GG codegen changes; every other console's output is byte-identical.
"""

from mosaik import MosaikCompiler

TEXT = '''
module "main" {
    import "platform.video"
    import "graphics.text"
    function main() {
        video.enable_lcd()
        text.print_string(5, 5, "HELLO")
        loop { video.wait_vblank() }
    }
    export main
}
'''

NOTEXT = '''
module "main" {
    import "platform.video"
    import "graphics.bkg"
    const TILES: array[u8, 16] = [255,255,255,255,255,255,255,255,255,255,255,255,255,255,255,255]
    var MAP: array[u8, 1024]
    function main() {
        bkg.set_data(0, 1, TILES)
        bkg.set_tiles(0, 0, 32, 32, MAP)
        video.enable_lcd()
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


def _main_body(c):
    """The lines of main()'s body (between its opening brace and the function's
    closing brace at column 0)."""
    lines = c.splitlines()
    start = next(i for i, ln in enumerate(lines)
                 if ln.startswith("void main(") and ln.rstrip().endswith("{"))
    # the body opens on the same line ("void main(void) {")
    body = []
    for ln in lines[start + 1:]:
        if ln == "}":
            break
        body.append(ln)
    return body


def main():
    print("SMS/GG name-table clear")
    print("=" * 50)
    ok = True

    for plat in ("sms", "gamegear"):
        t = compile_for(TEXT, plat)
        ok &= check(f"[{plat}] text: helper defined + clears full 32x28 map",
                    "void gbs_sms_clear_bkg(void)" in t
                    and "fill_bkg_rect(0, 0, 32, 28, 0)" in t)
        ok &= check(f"[{plat}] text: console inits eagerly before the clear",
                    "gbs_text_init();\n    fill_bkg_rect(0, 0, 32, 28, 0);" in t)
        body = _main_body(t)
        ok &= check(f"[{plat}] text: clear is the first statement of main()",
                    bool(body) and body[0].strip() == "gbs_sms_clear_bkg();")

        # SMS/GG must load the font ONCE (no GB-style throwaway pad loads),
        # else the eager init corrupts set_bkg_data (font glyphs in the bkg).
        ok &= check(f"[{plat}] text: font loaded once (no throwaway pad loads)",
                    t.count("font_load(") == 1 and "font_load(font_min)" not in t)

        n = compile_for(NOTEXT, plat)
        ok &= check(f"[{plat}] non-text: clear present but no eager text init",
                    "void gbs_sms_clear_bkg(void)" in n
                    and "gbs_text_init" not in n)
        # bkg.set_tiles must route through the 28-row clamp wrapper (a 32-row GB
        # map otherwise overruns the 32x28 name table into the SAT and corrupts
        # the displayed background -- projects/background's roof).
        ok &= check(f"[{plat}] bkg.set_tiles clamps to the 28-row name table",
                    "void gbs_set_bkg_tiles(" in n
                    and "if ((uint8_t)(y + h) > 28)" in n
                    and "gbs_set_bkg_tiles(0, 0," in n)
        nbody = _main_body(n)
        ok &= check(f"[{plat}] non-text: clear is the first statement of main()",
                    bool(nbody) and nbody[0].strip() == "gbs_sms_clear_bkg();")

    # Gated: the Game Boy (crt0 already blanks the map) gets neither the helper
    # nor the injected call -- its output is unchanged.
    gb = compile_for(TEXT, "gameboy")
    ok &= check("gameboy: no name-table clear emitted (crt0 handles it)",
                "gbs_sms_clear_bkg" not in gb)

    # --- The background tile RANGE (the other half of the same VRAM map) ---
    # The SMS/GG background addresses tiles 0..191 only: "tile 192..247"
    # pattern data IS the name table and "248..255" IS the SAT. A GB-family
    # idiom that parks something at the TOP of a 256-tile table therefore does
    # not draw here, it corrupts the map + sprite attributes -- which is how
    # projects/vm-bganim lost its animated water tile (the studio's hidden
    # animated index used to be allocated from 255 downward) while the GB build
    # stayed correct. A resolvable upload past 191 is now a build error.
    print("\nSMS/GG background tile range")
    HIGH = TEXT.replace('import "graphics.text"',
                        'import "graphics.text"\n    import "graphics.bkg"'
                        '\n    const ANIM_TILE: u8 = 255'
                        '\n    const ANIM_COUNT: u8 = 1'
                        '\n    const FRAME: array[u8, 16] = ['
                        '1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16]')
    HIGH = HIGH.replace("video.enable_lcd()",
                        "bkg.set_data(ANIM_TILE, ANIM_COUNT, FRAME)\n"
                        "        video.enable_lcd()")
    LOW = HIGH.replace("const ANIM_TILE: u8 = 255", "const ANIM_TILE: u8 = 6")
    for plat in ("sms", "gamegear"):
        out = compile_for(HIGH, plat)
        ok &= check(f"[{plat}] an upload past tile 191 is refused",
                    "Compilation error:" in out and "0..191" in out)
        ok &= check(f"[{plat}] an upload inside the range still compiles",
                    "Compilation error:" not in compile_for(LOW, plat))
    ok &= check("the GB family still allows the top of the table",
                all("Compilation error:" not in compile_for(HIGH, p)
                    for p in ("gameboy", "gameboy_color", "nes",
                              "lynx", "pce")))

    print("=" * 50)
    print("PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
