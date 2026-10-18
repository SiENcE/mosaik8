"""The LIST metasprite layer against a MODEL, over random op sequences.

Three bugs in one week came from the same small layer (`sprite.set_meta_list`
and the writers that touch a list base after it), each found by a user in a
converted game rather than by a test (records in the studio's
`docs/done/list-object-flips-and-shmup-facing.md`):

  - a fan writer (`set_prop`, `set_palette`) wrote ONE prop byte over every
    child and erased a mirrored half's own FLIP_X;
  - the upload ORed an object's own flip into a flipped base (GBDK and the
    reference engine XOR);
  - `move_world` left its left-edge clip bound set, and the next FLIPPED list
    parked a column.

A test per bug pins that bug. This one pins the RULES, so a writer or a
one-shot state added later is caught too: a generated program steps through
a random script of every list operation (upload frames with mirrored and
Y-flipped objects and different object counts, flip, whole-sprite palette,
per-cell palettes, plain moves, edge-clipped moves) on three bases, one op
per handshake, and after every op the SHADOW OAM is compared with a Python
model of the documented rules:

  tile      = upload tile + the object's dtile
  flips     = the base's FLIP_X XOR the object's own props (FLIP_X / FLIP_Y)
  palette   = the last palette written to that OAM SLOT (set_palette,
              set_meta_palettes); an upload and a flip keep it
  position  = base + dx, mirrored about the frame width under FLIP_X; a
              hidden base (y >= SCREEN_HEIGHT) parks every object; a
              move_world parks exactly the objects whose screen x does not
              fit the hardware's u8, and nothing it computes outlives it

Both OBJ modes (8x8 and the [build] obj_8x16 the conversions run), several
seeds. Needs GBDK + PyBoy; skips politely without them.
"""
import os
import random
import re
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

import mosaik8_build  # noqa: E402

FAILS = []
FLIP_X, FLIP_Y, PAL_BITS = 0x20, 0x40, 0x17     # CGB palette 0-2 + DMG OBP1
SCREEN_H = 144
PARK = (200, 0)                                 # (y, x) as written to OAM
BASES = (0, 8, 16)
OPS_PER_RUN = 400
SEEDS = (1, 2, 3)

# Frames: (pixel width, [(dy, dx, dtile, own props)]). Every frame has a
# mirrored object; some a Y-flipped or both-flipped one; counts 1..4.
FRAMES = {
    False: [   # 8x8 objects
        (16, [(0, 0, 0, 0), (0, 8, 0, FLIP_X)]),
        (16, [(0, 0, 2, 0), (0, 8, 3, 0), (8, 0, 4, FLIP_X), (8, 8, 4, 0)]),
        (16, [(0, 0, 5, FLIP_Y), (4, 8, 6, FLIP_X), (10, 4, 7, FLIP_X | FLIP_Y)]),
        (8, [(0, 0, 1, FLIP_X)]),
    ],
    True: [    # 8x16 objects (even tiles)
        (16, [(0, 0, 0, 0), (0, 8, 0, FLIP_X)]),
        (16, [(0, 0, 2, 0), (12, 0, 4, 0), (0, 8, 6, FLIP_X), (12, 8, 6, 0)]),
        (16, [(0, 0, 8, FLIP_Y), (4, 8, 10, FLIP_X), (10, 4, 12, FLIP_X | FLIP_Y)]),
        (8, [(0, 0, 2, FLIP_X)]),
    ],
}
TILE0 = 20      # the upload's base tile


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _cell_shape(n, obj16):
    """set_meta_palettes' (w, h) in 8x8 cells covering exactly n objects."""
    w = {1: 1, 2: 2, 3: 3, 4: 2}[n]
    objs_per_col = n // w
    return w, objs_per_col * (2 if obj16 else 1)


# ---------------------------------------------------------------- the script
OP_UPLOAD, OP_PROP, OP_PAL, OP_MOVE, OP_MOVEW, OP_MPAL = range(6)


def make_script(seed, obj16):
    """Random ops, encoded 6 bytes each: op, base index, a, b, c, d. Like
    vm.canim, an upload or a flip is followed by a move of that base, so a
    position is always defined when it is compared."""
    rnd = random.Random(seed)
    ops, maps = [], []
    frame_of = {}
    for b in range(len(BASES)):
        ops.append((OP_UPLOAD, b, rnd.randrange(4), 0, 0, 0))
        frame_of[b] = ops[-1][2]
        ops.append((OP_MOVE, b, 30 + 30 * b, 40, 0, 0))
    while len(ops) < OPS_PER_RUN:
        b = rnd.randrange(len(BASES))
        k = rnd.random()
        if k < 0.18:
            f = rnd.randrange(4)
            frame_of[b] = f
            ops.append((OP_UPLOAD, b, f, 0, 0, 0))
            k = 0.5        # then a move
        elif k < 0.32:
            ops.append((OP_PROP, b, rnd.randrange(2), 0, 0, 0))
            k = 0.5
        elif k < 0.42:
            ops.append((OP_PAL, b, rnd.choice((0, 1, 2, 3, 5, 7, 0x10, 0x13)), 0, 0, 0))
            continue
        elif k < 0.50:
            n = len(FRAMES[obj16][frame_of[b]][1])
            w, h = _cell_shape(n, obj16)
            off = len(maps)
            maps += [rnd.choice((0, 1, 2, 4, 6, 0x10, 0x12)) for _ in range(w * h)]
            ops.append((OP_MPAL, b, w, h, off & 0xFF, off >> 8))
            continue
        if k < 0.75:
            y = rnd.choice((0, 8, 40, 100, 130, 150))   # 150 = hidden
            ops.append((OP_MOVE, b, rnd.randrange(0, 256), y, 0, 0))
        else:
            # signed x from -40 to 300: past the screen's left edge and past
            # the u8's own right edge (x + 8 + dx > 255), carried as x + 64
            # in two bytes (a = low, d = high)
            x = rnd.randrange(-40, 301) + 64
            ops.append((OP_MOVEW, b, x & 0xFF, rnd.choice((8, 40, 100)), 0, x >> 8))
    return ops, maps or [0]


def program(ops, maps, obj16):
    frames = FRAMES[obj16]
    data, foff, fn, fpw = [], [], [], []
    for pw, objs in frames:
        foff.append(len(data))
        fn.append(len(objs))
        fpw.append(pw)
        for dy, dx, dt, pr in objs:
            data += [dy, dx, dt, pr]
    script = [v for op in ops for v in op]

    def arr(name, typ, vals):
        return "    const %s: array[%s, %d] = [%s]\n" % (
            name, typ, len(vals), ", ".join(str(v) for v in vals))

    return (
        'module "main" {\n'
        '    import "graphics.sprite"\n'
        '    import "platform.video"\n'
        + arr("D", "u8", data) + arr("FOFF", "u8", foff) + arr("FN", "u8", fn)
        + arr("FPW", "u8", fpw) + arr("BASE", "u8", list(BASES))
        + arr("M", "u8", maps) + arr("P", "u8", script) +
        '    var fz_go: u16 = 0\n'
        '    var fz_step: u16 = 0\n'
        '    function main() {\n'
        '        sprite.set_meta(36, 0, 1, 2)\n'
        '        while true {\n'
        '            video.wait_vblank()\n'
        '            if fz_step < fz_go {\n'
        '                var j: u16 = fz_step * 6\n'
        '                var op: u8 = P[j]\n'
        '                var b: u8 = BASE[P[j + 1]]\n'
        '                var a: u8 = P[j + 2]\n'
        '                var c: u8 = P[j + 3]\n'
        '                if op == 0 {\n'
        '                    sprite.set_meta_list(b, FPW[a], %d, D, FOFF[a], FN[a])\n'
        '                } else if op == 1 {\n'
        '                    if a == 1 {\n'
        '                        sprite.set_prop(b, FLIP_X)\n'
        '                    } else {\n'
        '                        sprite.set_prop(b, 0)\n'
        '                    }\n'
        '                } else if op == 2 {\n'
        '                    sprite.set_palette(b, a)\n'
        '                } else if op == 3 {\n'
        '                    sprite.move(b, a, c)\n'
        '                } else if op == 4 {\n'
        '                    var x: i16 = P[j + 5]\n'
        '                    x = x * 256 + a - 64\n'
        '                    var y: i16 = c\n'
        '                    sprite.move_world(b, x, y)\n'
        '                } else {\n'
        '                    var mo: u16 = P[j + 5]\n'
        '                    mo = mo * 256 + P[j + 4]\n'
        '                    sprite.set_meta_palettes(b, a, c, M, mo)\n'
        '                }\n'
        '                fz_step = fz_step + 1\n'
        '            }\n'
        '        }\n'
        '    }\n'
        '    export main\n'
        '}\n') % TILE0


# ----------------------------------------------------------------- the model
class Model:
    """The documented rules. Per OAM slot: tile, flips, palette, position;
    per base: frame, latched prop (its FLIP_X), object count."""

    def __init__(self, obj16):
        self.obj16 = obj16
        self.tile, self.flip, self.pal, self.pos = {}, {}, {}, {}
        self.frame, self.bprop, self.count = {}, {}, {}

    def upload(self, base, f):
        pw, objs = FRAMES[self.obj16][f]
        prev = self.count.get(base, 0)
        for k, (dy, dx, dt, own) in enumerate(objs):
            s = base + k
            self.tile[s] = TILE0 + dt
            self.flip[s] = (own ^ self.bprop.get(base, 0)) & (FLIP_X | FLIP_Y)
            self.pal.setdefault(s, 0)        # an upload keeps the slot's
        for s in range(base + len(objs), base + prev):
            self.pos[s] = PARK                # stale children park
        self.frame[base] = f
        self.count[base] = len(objs)

    def set_prop(self, base, p):
        self.bprop[base] = p
        pw, objs = FRAMES[self.obj16][self.frame[base]]
        for k, (_, _, _, own) in enumerate(objs):
            self.flip[base + k] = (own ^ p) & (FLIP_X | FLIP_Y)

    def set_palette(self, base, slot):
        for k in range(self.count[base]):
            self.pal[base + k] = slot & PAL_BITS

    def set_meta_palettes(self, base, w, h, cells):
        # the dense fan order: 8x8 = row-major cells; 8x16 = column-major
        # objects, each taking its UPPER cell
        if self.obj16:
            order = [cells[(p * 2) * w + c] for c in range(w) for p in range(h // 2)]
        else:
            order = list(cells[:w * h])
        for k, m in enumerate(order):
            self.pal[base + k] = m & PAL_BITS

    def move(self, base, x, y, signed):
        pw, objs = FRAMES[self.obj16][self.frame[base]]
        flipped = self.bprop.get(base, 0) & FLIP_X
        for k, (dy, dx, _, _) in enumerate(objs):
            s = base + k
            if (y & 0xFF) >= SCREEN_H:
                self.pos[s] = PARK
                continue
            mx = (pw - 8 - dx) if flipped else dx
            sx = x + mx + 8                    # the hardware x (+8 offset)
            if signed and not (0 <= sx <= 255):
                self.pos[s] = PARK             # off the u8: parked, not wrapped
            else:
                self.pos[s] = ((y + 16 + dy) & 0xFF, sx & 0xFF)

    def expect(self, base):
        out = []
        for k in range(self.count[base]):
            s = base + k
            out.append((s, self.pos.get(s), self.tile[s],
                        self.flip[s] | self.pal.get(s, 0)))
        return out


def apply(model, op, maps):
    kind, bi, a, b, c, d = op
    base = BASES[bi]
    if kind == OP_UPLOAD:
        model.upload(base, a)
    elif kind == OP_PROP:
        model.set_prop(base, FLIP_X if a else 0)
    elif kind == OP_PAL:
        model.set_palette(base, a)
    elif kind == OP_MOVE:
        model.move(base, a, b, signed=False)
    elif kind == OP_MOVEW:
        model.move(base, a + (d << 8) - 64, b, signed=True)
    else:
        off = c | (d << 8)
        model.set_meta_palettes(base, a, b, maps[off:off + a * b])


# -------------------------------------------------------------------- driver
def _symbols(noi):
    out = {}
    with open(noi, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
            if m:
                out[m.group(1)] = int(m.group(2), 16) & 0xFFFF
    return out


def _sym(syms, suffix):
    hits = [v for k, v in syms.items() if k.endswith(suffix) and "$" not in k]
    return hits[0] if hits else None


def run(seed, obj16, tmp):
    ops, maps = make_script(seed, obj16)
    proj = os.path.join(tmp, "fz%d%s" % (seed, "_16" if obj16 else ""))
    os.makedirs(os.path.join(proj, "src"))
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "fz"\nversion = "1.0.0"\n'
                'target_platforms = [ "gameboy",]\n\n[source]\nfolder = "src/"\n\n'
                '[build]\noutput_dir = "build"\nobj_8x16 = %s\n' % ("true" if obj16 else "false"))
    with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
        f.write(program(ops, maps, obj16))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--debug", "--platform", "gameboy", proj],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT)
    rom = os.path.join(proj, "build", "gameboy", "fz.gb")
    noi = rom[:-3] + ".noi"
    if not (os.path.exists(rom) and os.path.exists(noi)):
        check("seed %d %s builds" % (seed, "8x16" if obj16 else "8x8"), False,
              (r.stdout + r.stderr)[-800:])
        return
    syms = _symbols(noi)
    go, step, oam = _sym(syms, "fz_go"), _sym(syms, "fz_step"), _sym(syms, "_shadow_OAM")
    if None in (go, step, oam):
        check("the handshake symbols are in the .noi", False, (go, step, oam))
        return
    from pyboy import PyBoy
    pb = PyBoy(rom, window="null", cgb=False)
    model = Model(obj16)
    first_bad = None
    compared = 0
    for i, op in enumerate(ops):
        # the first op also waits out the program's start-up, whose data
        # initialiser zeroes fz_go: write it on every poll
        for _ in range(600 if i == 0 else 20):
            pb.memory[go], pb.memory[go + 1] = (i + 1) & 0xFF, (i + 1) >> 8
            pb.tick(1, False)
            if pb.memory[step] | (pb.memory[step + 1] << 8) == i + 1:
                break
        else:
            pb.stop(save=False)
            check("seed %d, %s objects: op %d ran" % (seed, "8x16" if obj16 else "8x8", i),
                  False, op)
            return
        apply(model, op, maps)
        for bi, base in enumerate(BASES):
            if base not in model.frame:
                continue
            for s, pos, tile, attr in model.expect(base):
                o = [pb.memory[oam + s * 4 + k] for k in range(4)]
                got_attr = o[3] & (FLIP_X | FLIP_Y | PAL_BITS)
                bad = []
                if o[2] != tile:
                    bad.append("tile %d != %d" % (o[2], tile))
                if got_attr != attr:
                    bad.append("attr %02X != %02X" % (got_attr, attr))
                if pos is not None and (o[0], o[1]) != pos:
                    bad.append("pos %s != %s" % ((o[0], o[1]), pos))
                compared += 1
                if bad and first_bad is None:
                    first_bad = "op %d %s on base %d, slot %d: %s; last ops %s" % (
                        i, op, base, s, ", ".join(bad), ops[max(0, i - 4):i + 1])
    pb.stop(save=False)
    check("seed %d, %s objects: %d ops, %d object states match the model"
          % (seed, "8x16" if obj16 else "8x8", len(ops), compared),
          first_bad is None, first_bad)


def main():
    try:
        import pyboy  # noqa: F401
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    tmp = tempfile.mkdtemp(prefix="meta_list_model_")
    try:
        for obj16 in (False, True):
            for seed in SEEDS:
                run(seed, obj16, tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    print("=" * 60)
    print("list metasprites against the model (random op sequences)")
    print("=" * 60)
    main()
    print()
    if FAILS:
        print("FAILED: %d check(s)" % len(FAILS))
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("meta_list_model_test: all checks passed")
