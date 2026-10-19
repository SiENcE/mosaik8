"""The LIST metasprite layer against a MODEL, over random op sequences, on
the Game Boy, the Atari Lynx and the PC Engine.

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
per handshake, and after every op each console's sprite state is decoded
into a LOGICAL form and compared with a Python model of the documented rules:

  tile      = upload tile + the object's dtile
  flips     = the base's FLIP_X XOR the object's own props; the descriptor
              carries them in the REFERENCE encoding (0x20 X, 0x40 Y) and a
              console with other bits translates
  palette   = GB family: the last palette written to that OAM SLOT (an upload
              and a flip keep it); cc65: the same, except that a base given
              ONE palette (set_palette) re-applies it to every object an
              upload brings, until a per-cell map replaces it
  position  = base + dx, mirrored about the frame width under FLIP_X; a
              hidden base parks every object; a move_world parks exactly the
              objects whose screen x does not fit the console's u8, and
              nothing it computes outlives it

What is read, per console: the Game Boy's shadow OAM (both OBJ modes, 8x8
and the [build] obj_8x16 the conversions run); the Lynx's SCBs (sprctl0
flips, hpos / vpos, which move 7 px to the far edge under a flip, and the
penpal pair a palette slot points at); the PC Engine's SATB mirror (+32 /
+64 origin, X / Y invert bits, palette nibble). Several seeds. Needs the
console's toolchain and emulator (PyBoy; the libretro Lynx / PCE cores) and
skips that console politely without them.
"""
import os
import random
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
FLIP_X, FLIP_Y = 0x20, 0x40          # the REFERENCE encoding the descriptors carry
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


class Console:
    """What differs between the consoles, as data."""

    def __init__(self, name, ext, obj16, hide_y, park, headroom, row_park,
                 pal_values, fan_pal, ys):
        self.name, self.ext, self.obj16 = name, ext, obj16
        self.hide_y = hide_y          # a base at y >= this parks every object
        self.park = park              # (x, y) a parked object is LOGICALLY at
        self.headroom = headroom      # how far left of x 0 the hardware can draw
        self.row_park = row_park      # PCE: a child whose row passes the bottom parks
        self.pal_values = pal_values  # palette values worth writing here
        self.fan_pal = fan_pal        # cc65: set_palette is re-applied by an upload
        self.ys = ys                  # y values a plain move uses (incl. hidden)

    @property
    def label(self):
        if self.name == "gameboy":
            return "gameboy %s" % ("8x16" if self.obj16 else "8x8")
        return self.name


CONSOLES = [
    Console("gameboy", "gb", False, 144, (0 - 8, 200 - 16), 8, None,
            (0, 1, 2, 3, 5, 7, 0x10, 0x13), False, (0, 8, 40, 100, 130, 150)),
    Console("gameboy", "gb", True, 144, (0 - 8, 200 - 16), 8, None,
            (0, 1, 2, 3, 5, 7, 0x10, 0x13), False, (0, 8, 40, 100, 130, 150)),
    Console("lynx", "lnx", False, 200, (200, 220), 0, None,
            (0, 1, 2, 3), True, (0, 8, 40, 90, 120, 210)),
    Console("pce", "pce", False, 224, (200, 224), 0, 224,
            (0, 1, 2, 3), True, (0, 8, 40, 200, 216, 230)),
]


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


def make_script(seed, con):
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
            ops.append((OP_PAL, b, rnd.choice(con.pal_values), 0, 0, 0))
            continue
        elif k < 0.50:
            n = len(FRAMES[con.obj16][frame_of[b]][1])
            w, h = _cell_shape(n, con.obj16)
            off = len(maps)
            maps += [rnd.choice(con.pal_values) for _ in range(w * h)]
            ops.append((OP_MPAL, b, w, h, off & 0xFF, off >> 8))
            continue
        if k < 0.75:
            ops.append((OP_MOVE, b, rnd.randrange(0, 256), rnd.choice(con.ys), 0, 0))
        else:
            # signed x from -40 to 300: past the screen's left edge and past
            # the u8's own right edge, carried as x + 64 in two bytes
            x = rnd.randrange(-40, 301) + 64
            ops.append((OP_MOVEW, b, x & 0xFF, rnd.choice((8, 40, 90)), 0, x >> 8))
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
    """The documented rules, per OAM slot in LOGICAL form: tile, flips
    (reference encoding), palette, position (x, y) or the console's park."""

    def __init__(self, con):
        self.con = con
        self.tile, self.flip, self.pal, self.pos = {}, {}, {}, {}
        self.frame, self.bprop, self.count, self.spal = {}, {}, {}, {}

    def _objs(self, base):
        return FRAMES[self.con.obj16][self.frame[base]]

    def upload(self, base, f):
        self.frame[base] = f
        pw, objs = self._objs(base)
        prev = self.count.get(base, 0)
        for k, (dy, dx, dt, own) in enumerate(objs):
            s = base + k
            self.tile[s] = TILE0 + dt
            self.flip[s] = (own ^ self.bprop.get(base, 0)) & (FLIP_X | FLIP_Y)
        for s in range(base + len(objs), base + prev):
            self.pos[s] = self.con.park       # stale children park
        self.count[base] = len(objs)
        if self.con.fan_pal and self.spal.get(base):
            for k in range(len(objs)):
                self.pal[base + k] = self.spal[base] - 1

    def set_prop(self, base, p):
        self.bprop[base] = p
        pw, objs = self._objs(base)
        for k, (_, _, _, own) in enumerate(objs):
            self.flip[base + k] = (own ^ p) & (FLIP_X | FLIP_Y)

    def set_palette(self, base, slot):
        if self.con.fan_pal:
            slot &= 3
            self.spal[base] = slot + 1
        else:
            slot &= 0x17
        for k in range(self.count[base]):
            self.pal[base + k] = slot

    def set_meta_palettes(self, base, w, h, cells):
        if self.con.obj16:
            # 8x16: column-major objects, each taking its UPPER cell
            order = [cells[(p * 2) * w + c] for c in range(w) for p in range(h // 2)]
        else:
            order = list(cells[:w * h])
        for k, m in enumerate(order):
            self.pal[base + k] = (m & 3) if self.con.fan_pal else (m & 0x17)
            if self.con.fan_pal:
                self.spal[base + k] = 0

    def move(self, base, x, y, signed):
        con = self.con
        pw, objs = self._objs(base)
        flipped = self.bprop.get(base, 0) & FLIP_X
        y &= 0xFF
        for k, (dy, dx, _, _) in enumerate(objs):
            s = base + k
            if y >= con.hide_y:
                self.pos[s] = con.park
                continue
            mx = (pw - 8 - dx) if flipped else dx
            sx = x + mx                         # the screen x, signed
            if signed and not (-con.headroom <= sx <= 255 - con.headroom):
                self.pos[s] = con.park          # off the u8: parked, not wrapped
            elif con.row_park and y + dy >= con.row_park:
                self.pos[s] = con.park          # PCE: past the bottom row
            else:
                lx = (sx + con.headroom) & 0xFF
                self.pos[s] = (lx - con.headroom, (y + dy) & 0xFF)

    def expect(self, base):
        for k in range(self.count[base]):
            s = base + k
            yield s, self.pos.get(s), self.tile[s], self.flip[s], self.pal.get(s)


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


# --------------------------------------------------- what each console holds
class GbState:
    """PyBoy; the shadow OAM. A parked object is (x 0, y 200) raw."""

    def __init__(self, rom, labels):
        from pyboy import PyBoy
        self.pb = PyBoy(rom, window="null", cgb=False)
        self.oam = labels["_shadow_OAM"]

    def tick(self):
        self.pb.tick(1, False)

    def peek(self, a):
        return self.pb.memory[a]

    def poke(self, a, v):
        self.pb.memory[a] = v

    def slot(self, s):
        y, x, t, a = (self.pb.memory[self.oam + s * 4 + k] for k in range(4))
        return (x - 8, y - 16), t, a & (FLIP_X | FLIP_Y), a & 0x17

    def close(self):
        self.pb.stop(save=False)


class Cc65State:
    """A libretro core; the Lynx SCBs or the PCE SATB mirror."""

    def __init__(self, rom, labels, platform):
        sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
        import lynx_probe as lp
        from libretro import SessionBuilder
        from libretro.drivers.path import ExplicitPathDriver
        core = lp.CORE if platform == "lynx" else os.path.join(
            ROOT, "emu", "libretro", "mednafen_pce_fast_libretro.dll")
        sysdir = lp.SYSTEM_DIR
        b = (SessionBuilder.defaults(core).with_content(rom)
             .with_paths(ExplicitPathDriver(corepath=core, system=sysdir, save=sysdir,
                                            assets=sysdir, playlist=sysdir))
             .with_perf(None))
        self._ctx = b.build()
        self.session = self._ctx.__enter__()
        self.ram = lp.Ram(self.session)
        self.platform = platform
        self.tiles = labels["_gbs_spr_tile"]
        if platform == "lynx":
            self.scb = labels["_gbs_scb"]
            self.pp = labels["_gbs_pal_penpal"]
            self.penpal = None    # read once the program has started
        else:
            self.satb = labels["_gbs_satb"]

    def tick(self):
        self.session.run()

    def peek(self, a):
        return self.ram.read(a)

    def poke(self, a, v):
        self.ram.mem[self.ram._at(a)] = v

    def slot(self, s):
        tile = self.peek(self.tiles + s)
        if self.platform == "lynx":
            q = self.scb + 32 * s
            c0 = self.peek(q)
            fx, fy = bool(c0 & 0x20), bool(c0 & 0x10)
            hx, vy = self.ram.read(q + 7, 2, True), self.ram.read(q + 9, 2, True)
            pos = (hx - 7 * fx, vy - 7 * fy)
            if self.penpal is None:
                pp = self.pp
                self.penpal = {(self.peek(pp + 2 * k), self.peek(pp + 2 * k + 1)): k
                               for k in range(4)}
            pal = self.penpal.get((self.peek(q + 15), self.peek(q + 16)), "?")
        else:
            q = self.satb + 8 * s
            yw, xw = self.ram.read(q, 2), self.ram.read(q + 2, 2)
            attr = self.ram.read(q + 6, 2)
            fx, fy = bool(attr & 0x0800), bool(attr & 0x8000)
            pos = (xw - 32, yw - 64)
            pal = attr & 0x000F
        return pos, tile, (FLIP_X if fx else 0) | (FLIP_Y if fy else 0), pal

    def close(self):
        self._ctx.__exit__(None, None, None)


# -------------------------------------------------------------------- driver
def available(con):
    if con.name == "gameboy":
        try:
            import pyboy  # noqa: F401
        except Exception:
            return "PyBoy not installed"
        return None if mosaik8_build.gbdk_available() else "GBDK not installed"
    if not mosaik8_build.cc65_available():
        return "cc65 not installed"
    try:
        sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
        import libretro  # noqa: F401
    except Exception:
        return "libretro.py not installed"
    core = "mednafen_pce_fast_libretro.dll" if con.name == "pce" else None
    if core and not os.path.exists(os.path.join(ROOT, "emu", "libretro", core)):
        return "the PCE core is not installed"
    return None


def _labels(path):
    sys.path.insert(0, os.path.join(ROOT, "emu", "libretro"))
    import lynx_probe as lp
    return lp.load_labels(path)


def run(seed, con, tmp):
    ops, maps = make_script(seed, con)
    tag = "%s seed %d" % (con.label, seed)
    proj = os.path.join(tmp, "fz%d_%s%s" % (seed, con.name, "_16" if con.obj16 else ""))
    os.makedirs(os.path.join(proj, "src"))
    with open(os.path.join(proj, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write('[project]\nname = "fz"\nversion = "1.0.0"\n'
                'target_platforms = [ "%s",]\n\n[source]\nfolder = "src/"\n\n'
                '[build]\noutput_dir = "build"\nobj_8x16 = %s\n'
                % (con.name, "true" if con.obj16 else "false"))
    with open(os.path.join(proj, "src", "main.mos"), "w", encoding="utf-8") as f:
        f.write(program(ops, maps, con.obj16))
    r = subprocess.run([sys.executable, os.path.join(ROOT, "mosaik8.py"), "build",
                        "--debug", "--platform", con.name, proj],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=ROOT)
    rom = os.path.join(proj, "build", con.name, "fz." + con.ext)
    lbl = (rom[:-3] + ".noi") if con.name == "gameboy" else rom + ".lbl"
    if not (os.path.exists(rom) and os.path.exists(lbl)):
        check("%s builds" % tag, False, (r.stdout + r.stderr)[-800:])
        return
    labels = _labels(lbl)
    go, step = labels.get("_fz_go"), labels.get("_fz_step")
    if go is None or step is None:
        check("%s: the handshake symbols are labelled" % tag, False)
        return
    st = GbState(rom, labels) if con.name == "gameboy" else Cc65State(rom, labels, con.name)
    model = Model(con)
    first_bad = None
    compared = 0
    try:
        for i, op in enumerate(ops):
            # the first op also waits out the program's start-up, whose data
            # initialiser zeroes fz_go: write it on every poll
            for _ in range(900 if i == 0 else 30):
                st.poke(go, (i + 1) & 0xFF)
                st.poke(go + 1, (i + 1) >> 8)
                st.tick()
                if st.peek(step) | (st.peek(step + 1) << 8) == i + 1:
                    break
            else:
                check("%s: op %d ran" % (tag, i), False, op)
                return
            apply(model, op, maps)
            for base in BASES:
                if base not in model.frame:
                    continue
                for s, pos, tile, flip, pal in model.expect(base):
                    got_pos, got_tile, got_flip, got_pal = st.slot(s)
                    bad = []
                    if got_tile != tile:
                        bad.append("tile %d != %d" % (got_tile, tile))
                    if got_flip != flip:
                        bad.append("flips %02X != %02X" % (got_flip, flip))
                    if pal is not None and got_pal != pal:
                        bad.append("palette %s != %s" % (got_pal, pal))
                    if pos is not None and got_pos != pos:
                        bad.append("pos %s != %s" % (got_pos, pos))
                    compared += 1
                    if bad and first_bad is None:
                        first_bad = "op %d %s on base %d, slot %d: %s; last ops %s" % (
                            i, op, base, s, ", ".join(bad), ops[max(0, i - 4):i + 1])
    finally:
        st.close()
    check("%s: %d ops, %d object states match the model" % (tag, len(ops), compared),
          first_bad is None, first_bad)


def main(consoles=None, seeds=SEEDS):
    tmp = tempfile.mkdtemp(prefix="meta_list_model_")
    try:
        for con in consoles or CONSOLES:
            why = available(con)
            if why:
                print("  (skip %s: %s)" % (con.label, why))
                continue
            for seed in seeds:
                run(seed, con, tmp)
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
