#!/usr/bin/env python3
"""SMS / Game Gear sprite tiles 192..255 are real sprite tiles (2026-10-24).

GBDK's compat VDP layout (R2 = F7, R5 = BF, R6 = FF) keeps the name table and
the sprite table at 0x1800-0x1FFF, inside the BACKGROUND half, so sprite tile n
is at 0x2000 + 32n for every n = 0..255. The engine used to pack only 0..191
(0x2000-0x37FF) and parked the emote bubble at 188; 0x3800-0x3FFF was never
written after the boot-time VRAM clear. Now the per-room allocator packs up to
255 (tile 255 is `kvb`'s "not uploaded" mark, so no kind may start there) and
the bubble sits at 252.

Checks:
  1. a hand-written program uploads three shapes at tiles 5, 200 and 250 and
     genesis_plus_gx draws each sprite with ITS shape (SMS and GG);
  2. a copy of vm-shardlings with a 152-tile invisible "filler" kind placed
     before mom in the room New Game opens (home): mom's art goes to tiles
     184..211, past the old 188 ceiling. It builds for SMS and GG, the RAM says
     mom was uploaded at 184, and the screen is pixel-equal to the same copy
     WITHOUT the filler (where she sits at tile 32). With the old ceiling she
     is refused and hidden: both checks fail.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import mosaik8_build  # noqa: E402

LIBRETRO = os.path.join(ROOT, "emu", "libretro")
FAILS = []


def check(cond, what, detail=""):
    print(("  ok   " if cond else "  FAIL ") + what + ("" if cond or not detail else "  [%s]" % detail))
    if not cond:
        FAILS.append(what)
    return cond


def _core():
    cores = [c for c in os.listdir(LIBRETRO) if c.startswith("genesis_plus_gx_libretro")]
    return os.path.join(LIBRETRO, cores[0]) if cores else None


def _build(root, plat):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build", "--debug",
                        "--platform", plat, root],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stdout + r.stderr)[-600:]


# A libretro core is a process singleton: every run is a child process, which
# prints {"w", "h", "px": hex of the RGB frame, "ram": hex of system RAM}.
_CHILD = r'''
import json, sys
sys.path.insert(0, %(lib)r)
from retro import Core
c = Core(%(core)r, %(rom)r)
ran = 0
for f, keys, hold in %(press)r:
    c.run(f - ran); c.press(*keys); c.run(hold); c.press(); ran = f + hold
c.run(%(frames)d - ran)
img = c.image().convert("RGB")
sys.stdout.write(json.dumps({"w": img.size[0], "h": img.size[1], "px": img.tobytes().hex(),
                             "ram": c.ram().hex()}))
'''


def _run(rom, frames, press=()):
    code = _CHILD % {"lib": LIBRETRO, "core": _core(), "rom": rom, "press": list(press), "frames": frames}
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    try:
        d = json.loads(r.stdout)
    except ValueError:
        return None, r.stderr[-400:]
    d["px"] = bytes.fromhex(d["px"])
    d["ram"] = bytes.fromhex(d["ram"])
    return d, ""


def _pixel(d, x, y):
    i = 3 * (y * d["w"] + x)
    return d["px"][i:i + 3]


PROBE = '''module "tiles_probe" {
    import "platform.video"
    import "graphics.sprite"

    const FULL: array[u8, 16] = [
        0xFF,0xFF, 0xFF,0xFF, 0xFF,0xFF, 0xFF,0xFF, 0xFF,0xFF, 0xFF,0xFF, 0xFF,0xFF, 0xFF,0xFF
    ]
    const LEFT: array[u8, 16] = [
        0xF0,0xF0, 0xF0,0xF0, 0xF0,0xF0, 0xF0,0xF0, 0xF0,0xF0, 0xF0,0xF0, 0xF0,0xF0, 0xF0,0xF0
    ]
    const TOP: array[u8, 16] = [
        0xFF,0xFF, 0xFF,0xFF, 0xFF,0xFF, 0xFF,0xFF, 0x00,0x00, 0x00,0x00, 0x00,0x00, 0x00,0x00
    ]

    function main() {
        sprite.set_data(5, 1, FULL)
        sprite.set_data(200, 1, LEFT)
        sprite.set_data(250, 1, TOP)
        sprite.set_tile(0, 5)
        sprite.set_tile(1, 200)
        sprite.set_tile(2, 250)
        sprite.move(0, 16, 16)
        sprite.move(1, 40, 16)
        sprite.move(2, 64, 16)
        video.enable_lcd()
        video.show_sprites()
        loop {
            video.wait_vblank()
        }
    }
}
'''

# The mask each sprite must draw, row by row ('#' = not the backdrop).
SHAPES = {16: ["########"] * 8, 40: ["####...."] * 8, 64: ["########"] * 4 + ["........"] * 4}


def high_tiles_draw(tmp):
    print("\n[a sprite drawn from tile 200 / 250 shows that tile]")
    src = os.path.join(tmp, "tiles.mos")
    with open(src, "w", encoding="utf-8") as f:
        f.write(PROBE)
    for plat, ext in (("sms", "sms"), ("gamegear", "gg")):
        ok, log = _build(src, plat)
        if not check(ok, "%s: the probe builds" % plat, log):
            continue
        d, err = _run(os.path.join(tmp, "build", plat, "tiles." + ext), 122)
        if not check(d is not None, "%s: the probe runs" % plat, err):
            continue
        bg = _pixel(d, d["w"] - 1, d["h"] - 1)
        for x0, rows in SHAPES.items():
            got = ["".join("#" if _pixel(d, x0 + x, 16 + y) != bg else "." for x in range(8))
                   for y in range(8)]
            tile = {16: 5, 40: 200, 64: 250}[x0]
            check(got == rows, "%s: the sprite on tile %d draws its own shape" % (plat, tile), got)


def _shardlings(dst, filler):
    """A copy of vm-shardlings; with `filler`, a 152-tile transparent kind placed
    before mom in `home` (the room New Game opens), so she uploads at 32 + 152."""
    shutil.copytree(os.path.join(ROOT, "projects", "vm-shardlings"), dst,
                    ignore=shutil.ignore_patterns("build", "__pycache__"))
    if not filler:
        return
    from PIL import Image
    ref = Image.open(os.path.join(dst, "assets", "mom.png"))
    im = Image.new("P", (16 * 38, 16), 0)          # 38 frames of 2x2 = 152 tiles, all index 0
    im.putpalette(ref.getpalette())
    im.save(os.path.join(dst, "assets", "filler.png"))
    with open(os.path.join(dst, "assets", "filler.sprites.toml"), "w", encoding="utf-8") as f:
        for i in range(38):
            f.write('[[sprite]]\nname = "filler%d"\nrect = [%d, 0, 16, 16]\n\n' % (i, 16 * i))
    p = os.path.join(dst, "mosaik.toml")
    t = open(p, encoding="utf-8").read()
    t = t.replace('"assets/kid.png",', '"assets/kid.png", "assets/filler.png",', 1)
    open(p, "w", encoding="utf-8").write(t)
    p = os.path.join(dst, "studio.toml")
    t = open(p, encoding="utf-8").read()
    t = t.replace('kid = "kid"\n', 'kid = "kid"\nfiller = "filler"\n', 1)
    open(p, "w", encoding="utf-8").write(t)
    p = os.path.join(dst, "world.toml")
    t = open(p, encoding="utf-8").read()
    t = t.replace("ally = 42\n", "ally = 42\nfiller = 43\n", 1)
    # the filler object goes BEFORE mom in home: the allocator packs in object
    # order
    i = t.index('kind = "mom"')
    j = t.rindex("[[scene.object]]", 0, i)
    t = t[:j] + '[[scene.object]]\nkind = "filler"\nx = 8\ny = 8\nid = 900\n\n' + t[j:]
    open(p, "w", encoding="utf-8").write(t)


def _regen(root):
    """The world changed: regenerate the COUPLED pair (scenes + rooms), the way
    tools/regen_generated.py does for a sample in place."""
    try:
        import mosaik_scenes
        import mosaik_vm
        world, wbase = mosaik_scenes.load_world(os.path.join(root, "world.toml"))
        text = mosaik_scenes.core.transpile(world, wbase)
        with open(os.path.join(root, "src", "scenes.mos"), "w", encoding="utf-8", newline="") as f:
            f.write(text)
        mosaik_vm.generate_rooms(root)
        return True, ""
    except Exception as e:      # noqa: BLE001 - reported as the check's detail
        return False, repr(e)


def _sym(noi, name):
    for line in open(noi, encoding="utf-8", errors="replace"):
        m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
        if m and m.group(1) == name:
            return int(m.group(2), 16) & 0xFFFF
    return None


def room_past_192(tmp):
    print("\n[a room whose kinds need tiles past 192 draws them]")
    base = os.path.join(tmp, "base")
    fill = os.path.join(tmp, "fill")
    _shardlings(base, False)
    _shardlings(fill, True)
    ok, log = _regen(fill)
    if not check(ok, "the filler world regenerates", log):
        return
    rooms = open(os.path.join(fill, "src", "rooms.mos"), encoding="utf-8").read()
    check("const OBJ_TILE_TOP: u16 = 252" in rooms and "const EMOTE_TILE: u8 = 252" in rooms,
          "SMS/GG: the allocator packs to 252, the bubble sits at 252")
    check(re.search(r"const KT: array\[u8, 44\] = \[ 32, 28, 28, 32,.* 152 \]", rooms) is not None,
          "the filler is kind 43, 152 tiles")
    press = [(700, ["a"], 8)]
    for plat, ext in (("gamegear", "gg"), ("sms", "sms")):
        shots = {}
        for name, root in (("base", base), ("fill", fill)):
            ok, log = _build(root, plat)
            if not check(ok, "%s %s: builds" % (plat, name), log):
                break
            rom = os.path.join(root, "build", plat, "vm-shardlings." + ext)
            d, err = _run(rom, 1200, press)
            if not check(d is not None, "%s %s: runs" % (plat, name), err):
                break
            kvb = _sym(rom[:-len(ext) - 1] + ".noi", "_rooms_kvb")
            d["mom_vb"] = d["ram"][kvb - 0xC000 + 4] if kvb else None   # kind 4 = mom
            shots[name] = d
        if len(shots) < 2:
            continue
        check(shots["base"]["mom_vb"] == 32, "%s: without the filler mom uploads at 32" % plat,
              shots["base"]["mom_vb"])
        check(shots["fill"]["mom_vb"] == 184,
              "%s: with it mom uploads at 184 (tiles 184..211, past 191)" % plat,
              shots["fill"]["mom_vb"])
        a, b = shots["base"], shots["fill"]
        same = a["w"] == b["w"] and a["px"] == b["px"]
        if not same:
            diff = [i // 3 for i in range(0, len(a["px"]), 3) if a["px"][i:i + 3] != b["px"][i:i + 3]]
            detail = "%d px differ, first at %s" % (len(diff), divmod(diff[0], a["w"])[::-1] if diff else None)
        else:
            detail = ""
        check(same, "%s: the room draws pixel for pixel as without the filler" % plat, detail)


def main():
    print("=" * 60)
    print("SMS / GG sprite tiles 192..255")
    print("=" * 60)
    if not mosaik8_build.gbdk_available() or not _core():
        print("  (skip: GBDK or genesis_plus_gx not installed)")
        return 0
    tmp = tempfile.mkdtemp(prefix="smsgg_tiles_")
    try:
        high_tiles_draw(tmp)
        room_past_192(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("smsgg_sprite_tiles_test: all passed" if not FAILS else
                  "smsgg_sprite_tiles_test: %d FAILED" % len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    raise SystemExit(main())
