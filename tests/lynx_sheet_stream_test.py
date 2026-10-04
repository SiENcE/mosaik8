"""Lynx sprite SHEETS streamed from the cart under `[world] stream`.

A sheet (`[assets] sprites`) whose every read is `sprite.set_data(first,
count, <stem>_tiles)` is packed into the Lynx cart archive and uploaded tile by
tile through `gbs_spr_data_stream`, so it never occupies MAIN (the Lynx
converter reads its source only during the upload). Pinned here:

  * the trigger: the Lynx AND the program streams world assets; without the
    stream (or on any other console) the sheet stays where it was,
    byte-identical;
  * a sheet read any other way stays resident;
  * the sheets go LAST in the archive, so no streamed asset moves;
  * the ROM (when cc65 + a Lynx core are installed): the streamed program
    draws exactly what the resident one draws.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mosaik import MosaikCompiler  # noqa: E402

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [PASS] %s" % label)
    else:
        failed += 1
        print("  [FAIL] %s%s" % (label, ("  -- " + detail) if detail else ""))


def program(stream, other_read=False):
    use = "assets.use(MAP)" if stream else "var unused: u8 = 0"
    extra = "var peek: u8 = hero_tiles[3]\n        peek = peek" if other_read else ""
    return '''
module "main" {
    import "platform.video"
    import "platform.assets"
    import "graphics.sprite"
    const MAP: array[u8, 8] = [1, 2, 3, 4, 5, 6, 7, 8]
    function main() {
        %s
        var m: u8 = MAP[1]
        m = m
        %s
        sprite.set_data(0, hero_tile_count, hero_tiles)
        sprite.set_meta(0, 0, 2, 2)
        sprite.move(0, 60, 40)
        video.enable_lcd()
        video.show_sprites()
        loop {
            video.wait_vblank()
        }
    }
}
''' % (use, extra)


def sheet_bytes():
    """A 16x16 four-shade sheet as GB 2bpp, 4 tiles (64 B): a ring."""
    out = bytearray()
    for ty in range(2):
        for tx in range(2):
            for row in range(8):
                lo = hi = 0
                for col in range(8):
                    x, y = tx * 8 + col, ty * 8 + row
                    d = abs(x - 7.5) + abs(y - 7.5)
                    v = 3 if d < 4 else 2 if d < 6 else 1 if d < 8 else 0
                    lo |= (v & 1) << (7 - col)
                    hi |= ((v >> 1) & 1) << (7 - col)
                out += bytes([lo, hi])
    return bytes(out)


def compile_for(src, platform="lynx"):
    compiler = MosaikCompiler()
    c = compiler.compile_program([("main.mos", src)], platform=platform,
                                 assets=[("hero", sheet_bytes(), 2)])
    assert not c.startswith("Compilation error"), c
    return c, compiler.code_generator


def test_codegen():
    print("codegen")
    c, g = compile_for(program(True))
    check("the sheet streams when the world streams",
          g.sheet_stream == {"hero_tiles": g.sheet_stream.get("hero_tiles")}
          and "gbs_spr_data_stream(0, hero_tile_count, %d)"
          % g.sheet_stream.get("hero_tiles", -1) in c)
    check("its array is not in MAIN", "const uint8_t hero_tiles[" not in c
          and "#define hero_tile_count 4" in c)
    check("it is in the archive, after the world's assets",
          g.sheet_stream["hero_tiles"] * 1024 >= max(g.stream_offsets)
          and g.streamed_archive[g.sheet_stream["hero_tiles"] * 1024:][:64]
          == sheet_bytes())
    check("the loader converts through the engine's own tile converter",
          "gbs_conv_tile((uint8_t)(first + i), gbs_sheet_buf);" in c)
    plain, gp = compile_for(program(False))
    check("no world stream: the sheet stays resident, no loader",
          not gp.sheet_stream and "const uint8_t hero_tiles[64]" in plain
          and "gbs_spr_data_stream" not in plain)
    oc, og = compile_for(program(True, other_read=True))
    check("a sheet read another way stays resident",
          not og.sheet_stream and "const uint8_t hero_tiles[64]" in oc)
    for plat in ("pce", "gameboy"):
        pc, pg = compile_for(program(True), plat)
        check("%s: never streams a sheet" % plat,
              not pg.sheet_stream and "gbs_spr_data_stream" not in pc)


def _lynx_core():
    for stem in ("mednafen_lynx", "handy"):
        p = os.path.join(ROOT, "emu", "libretro", stem + "_libretro.dll")
        if os.path.isfile(p):
            return p
    return None


def _build_and_shoot(tmp, stream):
    from PIL import Image
    proj = os.path.join(tmp, "s" if stream else "p")
    os.makedirs(os.path.join(proj, "src"))
    os.makedirs(os.path.join(proj, "assets"))
    # The sheet as an indexed PNG the asset pipeline converts (index = shade).
    img = Image.new("P", (16, 16))
    img.putpalette([255, 255, 255, 170, 170, 170, 85, 85, 85, 0, 0, 0] + [0] * 756)
    for y in range(16):
        for x in range(16):
            d = abs(x - 7.5) + abs(y - 7.5)
            img.putpixel((x, y), 3 if d < 4 else 2 if d < 6 else 1 if d < 8 else 0)
    img.save(os.path.join(proj, "assets", "hero.png"))
    with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
        f.write(program(stream))
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "sheet"\nversion = "0.1.0"\n'
                'target_platforms = ["lynx"]\n\n[source]\nfolder = "src/"\n\n'
                '[assets]\nsprites = ["assets/hero.png"]\n\n'
                '[build]\noutput_dir = "build"\n')
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--platform", "lynx", proj], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    rom = os.path.join(proj, "build", "lynx", "sheet.lnx")
    if r.returncode or not os.path.isfile(rom):
        return None, (r.stdout + r.stderr)[-1500:]
    # TWO consecutive frames: a static sprite program shows its sprite on
    # one of the two flip pages only (pre-existing, every core), and the
    # cart reads shift WHICH page by a frame - so the pictures are compared
    # as a pair, not frame for frame.
    pngs = []
    for fr in (120, 121):
        png = os.path.join(tmp, "%s%d.png" % ("s" if stream else "p", fr))
        subprocess.run([sys.executable, os.path.join(ROOT, "emu", "libretro", "run_lynx.py"),
                        rom, str(fr), "--png", png], capture_output=True, cwd=ROOT)
        if not os.path.isfile(png):
            return None, r.stdout
        pngs.append(png)
    return pngs, r.stdout


def test_rom():
    print("ROM (Lynx core)")
    from mosaik8_build import cc65_available
    if not cc65_available() or not _lynx_core():
        print("  [SKIP] cc65 or a Lynx core not installed")
        return
    from PIL import Image, ImageChops
    tmp = tempfile.mkdtemp(prefix="lynxsheet_")
    try:
        plain, _o = _build_and_shoot(tmp, False)
        streamed, out = _build_and_shoot(tmp, True)
        check("the resident fixture builds and runs", plain is not None)
        check("the streamed fixture builds and runs", streamed is not None, out)
        if not (plain and streamed):
            return
        check("the build appended the archive", "Lynx asset archive" in out, out[-400:])
        def drawn(img):
            bg = Image.new("RGB", img.size, img.getpixel((0, 0)))
            box = ImageChops.difference(img, bg).getbbox()
            return box is not None and box[2] - box[0] >= 12 and box[3] - box[1] >= 12

        def sprite_frame(pngs):
            imgs = [Image.open(p).convert("RGB") for p in pngs]
            shown = [im for im in imgs if drawn(im)]
            return shown[0] if shown else None

        a, b = sprite_frame(plain), sprite_frame(streamed)
        check("the resident sheet is drawn", a is not None)
        check("the streamed sheet is drawn", b is not None)
        check("streamed == resident, pixel for pixel",
              a is not None and b is not None
              and ImageChops.difference(a, b).getbbox() is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_codegen()
    test_rom()
    print("\n%d passed, %d failed" % (passed, failed))
    sys.exit(1 if failed else 0)
