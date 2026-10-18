"""A LIST metasprite keeps each object's OWN flip (2026-10-18).

`sprite.set_meta_list` draws a frame as authored objects, and an object may
carry its own props: a symmetric 16 px sprite is ONE 8 px cell drawn twice,
the right copy with FLIP_X (the reference engine's own tile dedupe). Two
writers fan one prop byte over a metasprite AFTER the upload and erased it:

  - `sprite.set_prop(base, ...)`, which vm.canim calls on every first draw
    (its flip latch starts at 255) and on every facing change;
  - `sprite.set_palette(base, ...)`, which a per-frame sprite palette calls
    after every frame upload.

Measured on the reference-engine sample conversion: its save point and its
hidden stairs drew two LEFT halves (OAM: the same tile twice, no FLIP_X on
either). The fix keeps each child's authored prop in `gbs_meta_own` and has
both fans XOR it back in; the upload XORs too (GBDK's metasprite rule and the
reference engine's: a mirrored half of a FLIPPED frame draws unmirrored),
where it used to OR.

Checks: the C (the table and the three writers; none of it for a program that
draws no list), and - with GBDK and PyBoy present - a Game Boy ROM whose OAM
attribute bytes follow a set_prop, a flip, a palette and a re-upload.
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler  # noqa: E402
import mosaik8_build  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


# Object 0 plain, object 1 the same cell mirrored (props 32 = FLIP_X). The
# phases are display frames; the ROM check reads OAM inside each one.
SRC = '''
module "t" {
    import "graphics.sprite"
    import "platform.video"
    const D: array[u8, 8] = [0, 0, 0, 0, 0, 8, 0, 32]
    function main() {
        var n: u16 = 0
        sprite.set_meta(0, 0, 2, 2)
        sprite.set_meta_list(0, 16, 0, D, 0, 2)
        sprite.set_prop(0, 0)
        sprite.move(0, 40, 40)
        while true {
            video.wait_vblank()
            n += 1
            if n == 60 {
                sprite.set_prop(0, FLIP_X)
                sprite.move(0, 40, 40)
            }
            if n == 120 {
                sprite.set_palette(0, 1)
            }
            if n == 180 {
                sprite.set_meta_list(0, 16, 0, D, 0, 2)
                sprite.move(0, 40, 40)
            }
            if n == 240 {
                sprite.set_prop(0, 0)
                sprite.move(0, 40, 40)
            }
        }
    }
    export main
}
'''

DENSE = '''
module "t" {
    import "graphics.sprite"
    function main() {
        sprite.set_meta(0, 0, 2, 2)
        sprite.set_prop(0, FLIP_X)
        sprite.set_palette(0, 1)
        sprite.move(0, 40, 40)
    }
    export main
}
'''


def _c_function(text, sig):
    i = text.find(sig)
    if i < 0:
        return ""
    j = text.find("\n}\n", i)
    return text[i:j + 3]


def c_contract():
    print("\n[the C]")
    for plat in ("gameboy", "gameboy_color"):
        c = MosaikCompiler().compile_program([("t.mos", SRC)], platform=plat)
        if c.startswith("Compilation error"):
            check("%s compiles" % plat, False, c[:300])
            continue
        check("%s: a list program carries the per-child prop table" % plat,
              "uint8_t gbs_meta_own[GBS_META_SLOTS];" in c)
        up = _c_function(c, "static void gbs_set_metasprite_list(")
        check("%s: the upload records the child's own prop and XORs it" % plat,
              "pp = gbs_meta_own[s] = *d++;" in up and "pp ^= prop;" in up
              and "prop | *d" not in up, up)
        sp = _c_function(c, "static void gbs_set_sprite_prop(")
        check("%s: set_prop re-applies each list child's own prop" % plat,
              "if (w == 0xFF) {" in sp and "prop ^ gbs_meta_own[nb + s]" in sp, sp)
        fp = _c_function(c, "static void gbs_fan_prop(")
        check("%s: the palette fan re-applies it too" % plat,
              "if (w == 0xFF) {" in fp and "prop ^ gbs_meta_own[nb + s]" in fp, fp)
    dense = MosaikCompiler().compile_program([("t.mos", DENSE)], platform="gameboy")
    check("a program that draws no list carries none of it",
          not dense.startswith("Compilation error") and "gbs_meta_own" not in dense,
          dense[:300])


def rom():
    print("\n[a Game Boy ROM: the OAM attribute bytes]")
    try:
        from pyboy import PyBoy
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    tmp = tempfile.mkdtemp(prefix="meta_own_flip_")
    try:
        path = os.path.join(tmp, "t.mos")
        with open(path, "w", encoding="utf-8") as f:
            f.write(SRC)
        r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                            "--platform", "gameboy", path],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        rom_path = os.path.join(tmp, "build", "gameboy", "t.gb")
        if not os.path.exists(rom_path):
            check("the sample builds", False, (r.stdout + r.stderr)[-600:])
            return
        pb = PyBoy(rom_path, window="null", cgb=False)
        # sync on the program's own start (the first move puts object 0 on
        # screen), then sample each 60-frame phase in its middle
        for _ in range(600):
            pb.tick(1, False)
            if pb.memory[0xFE00] == 40 + 16:
                break
        pb.tick(30, False)

        def attrs():
            return pb.memory[0xFE03] & 0x67, pb.memory[0xFE07] & 0x67

        def xs():
            return pb.memory[0xFE01], pb.memory[0xFE05]

        seen = {}
        for label, frames in (("set_prop 0", 0), ("FLIP_X", 60), ("palette", 60),
                              ("re-upload", 60), ("set_prop 0 again", 60)):
            pb.tick(frames, False)
            seen[label] = (attrs(), xs())
        pb.stop(save=False)
        check("after set_prop(base, 0) the mirrored half keeps its FLIP_X",
              seen["set_prop 0"][0] == (0x00, 0x20), seen["set_prop 0"])
        check("a flipped frame swaps the halves AND their flips (XOR)",
              seen["FLIP_X"][0] == (0x20, 0x00)
              and seen["FLIP_X"][1][0] > seen["FLIP_X"][1][1], seen["FLIP_X"])
        check("set_palette changes the colour and keeps both flips",
              seen["palette"][0] == (0x21, 0x01), seen["palette"])
        check("a re-upload under the flipped base XORs (it used to OR)",
              seen["re-upload"][0] == (0x21, 0x01), seen["re-upload"])
        check("turning back restores the authored flips, palette kept",
              seen["set_prop 0 again"][0] == (0x01, 0x21), seen["set_prop 0 again"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("set_meta_list: each object keeps its own flip")
    print("=" * 60)
    c_contract()
    rom()
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("meta_list_own_flip_test: all checks passed")
