"""`sprite.move_world`'s list clip is a ONE-SHOT, both bounds (2026-10-18).

A list-shaped metasprite (`sprite.set_meta_list`) clips per object against
two dx thresholds, `gbs_clip_dlo` / `gbs_clip_dhi`, which `move_world`
resolves, uses and clears. It cleared only `dhi`. The fan's fast path (no
flip, `dhi == 0`) never reads them, but a FLIPPED list base takes the
clipping path on EVERY move, so a `dlo` left by any actor clipped at the
LEFT edge parked the column of a facing-left player whose mirrored offset
fell under it. Measured on the reference-engine sample conversion: a rock
scrolled off the left edge left dlo = 8, and the player walking left then
drew as one 8x16 object (the town's florist did the same while she talked).

Checks: the C on a GBDK console and a cc65 one (both bounds cleared after the
list move), and - with GBDK and PyBoy present - a Game Boy ROM where one list
actor is clipped at the left edge and a second, flipped, must still draw both
of its objects.
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


# Base 0 is moved past the LEFT edge (x -16: the GB draws down to -8, so its
# first object is clipped and dlo = 8); base 4 is then flipped
# and moved in the middle of the screen.
SRC = '''
module "t" {
    import "graphics.sprite"
    import "platform.video"
    const D: array[u8, 8] = [0, 0, 0, 0, 0, 8, 0, 0]
    function main() {
        sprite.set_meta(0, 0, 2, 2)
        sprite.set_meta_list(0, 16, 0, D, 0, 2)
        sprite.set_meta(4, 0, 2, 2)
        sprite.set_meta_list(4, 16, 0, D, 0, 2)
        sprite.move_world(0, -16, 40)
        sprite.set_prop(4, FLIP_X)
        sprite.move(4, 60, 60)
        while true {
            video.wait_vblank()
        }
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
    print("\n[the C: both list bounds are cleared after the move]")
    for plat in ("gameboy", "lynx"):
        c = MosaikCompiler().compile_program([("t.mos", SRC)], platform=plat)
        if c.startswith("Compilation error"):
            check("%s compiles" % plat, False, c[:300])
            continue
        fn = _c_function(c, "gbs_move_sprite_world(uint8_t nb, int16_t x, int16_t y) {")
        i = fn.find("if (w == 0xFF) {")
        arm = fn[i:fn.find("return;", i)]
        mv = arm.find("gbs_move_sprite(nb")
        check("%s: the list arm clears dlo AND dhi after its move" % plat,
              i >= 0 and 0 <= mv < arm.rfind("gbs_clip_dlo = 0;")
              and mv < arm.rfind("gbs_clip_dhi = 0;"), arm)
        if plat == "gameboy":
            disp = _c_function(c, "static void gbs_move_sprite(uint8_t nb, uint8_t x, uint8_t y) {")
            check("gameboy: the list FAST path needs both bounds clear (a left-only "
                  "clip has dhi 0)",
                  "gbs_clip_dlo == 0 && gbs_clip_dhi == 0" in disp, disp)


def rom():
    print("\n[a Game Boy ROM: a flipped list after a left-edge clip]")
    try:
        from pyboy import PyBoy
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    tmp = tempfile.mkdtemp(prefix="meta_clip_oneshot_")
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
        pb.tick(400, False)
        objs = [tuple(pb.memory[0xFE00 + i * 4 + k] for k in range(4)) for i in range(6)]
        pb.stop(save=False)
        check("the left-edge actor parks only its off-screen object",
              objs[0][0] >= 160 and 0 < objs[1][0] < 160, objs[:2])
        check("the flipped actor draws BOTH objects, mirrored",
              objs[4][0] == objs[5][0] == 60 + 16
              and objs[4][1] == 60 + 8 + 8 and objs[5][1] == 60 + 8, objs[4:6])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("move_world: the list clip is a one-shot")
    print("=" * 60)
    c_contract()
    rom()
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("meta_list_clip_oneshot_test: all checks passed")
