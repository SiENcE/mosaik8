#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

"""A projectile is drawn at its sheet CELL's real size, not as one object.

`vm.projectile` rendered a shot with a single `sprite.set_tile`, which is right
on the GB family ONLY because `[build] obj_8x16` makes one hardware object 8x16
- the whole of a converted reference-engine launch cell (`gbs_import` lays the sheet
out at `PROJ_W, PROJ_H = 8, 16`). Off that mode an object is 8x8, so the same
code drew the TOP HALF of each frame and the flight-animation stride then
stepped to the top half of the NEXT one: the reported "wrong particle
animations" on the Master System and the Game Gear, and on neither GB build.

Verified on the SMS ROM of the shooter conversion (same frame and same inputs,
with and without the emitted `set_cell`): the shot draws as a half-height blob
before and as the full 8x16 bullet after.

The rules this pins:

* `set_cell(w, h)` is OPT-IN. Unregistered the cell is 1x1, the render keeps the
  single `set_tile` object and every existing game is byte-identical.
* the OAM cost per shot is `w*h`, HALVED where an object is 8x16, and the pool's
  base clamp must use `NPROJ * fan` - not NPROJ, or the block runs off the end
  of OAM.
* the ceiling it clamps against is the CONSOLE's. It used to be a literal 40
  (the Game Boy's), which was merely conservative while a shot was one object;
  with a two-object cell the same literal would drag the whole block DOWN into
  the actor fans on the consoles that have 64.
* parking a shot must assert the fan SHAPE first: `sprite.move` fans whatever
  w/h the last owner of that OAM base wrote, so half the shot would stay on
  screen.
"""

from mosaik import MosaikCompiler
from mosaik_vm.rooms import emit_rooms_mos

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = open(os.path.join(HERE, "..", "lib", "vm", "projectile.mos"),
           encoding="utf-8").read()


def _exported(src, name):
    """Is `name` in ANY of a module's `export` statements?

    NOT `src.split("export")[-1]`: a module may have several export lines, and
    taking only the last one reads as "not exported" the moment a new one is
    appended below it - which is exactly what W7j and W7c each did to a
    different module, breaking a green assertion for a reason that had nothing
    to do with what it pins.
    """
    return any(name in ln for ln in src.splitlines()
               if ln.strip().startswith("export "))


def check(label, cond):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond



# ---------------------------------------------------------------------------
# THE SHOT'S OWN COLLISION BOX (set_box) - the reference engine's projectile->def.bounds
# ---------------------------------------------------------------------------
def _test_projectile_box():
    """A shot collides with its sprite's authored bounds, not with its cell.

    The reference engine passes `projectile->def.bounds` to `bb_intersects`
    (projectiles.c), exactly the way it passes the TARGET's bounds. Ours used a
    fixed 8x8 at the cell's top-left - which since the cell became derived is
    the top-left QUARTER of a 16x16 shot, so it had to overlap several px INTO
    a target on the right and bottom before the hit registered.
    """
    print("")
    print("[projectile.set_box: the shot collides with its BOUNDS]")
    ok = True
    ok &= check("vm.projectile exposes set_box", "function set_box" in SRC)
    ok &= check("...and exports it", _exported(SRC, "set_box"))
    ok &= check("the box is OPT-IN: unset keeps the PROJ_SIZE square "
                "(byte-identical)", "if b_w != 0 {" in SRC)
    ok &= check("the fields are initialiser-free (BSS), costing no resident "
                "image", "var b_w: u8" in SRC)

    off = emit_rooms_mos({"types": ["topdown"], "uses_projectile": True})
    ok &= check("a project with no [projectiles] box_* emits NO set_box",
                "set_box" not in off)
    on = emit_rooms_mos({"types": ["topdown"], "uses_projectile": True,
                         "proj_box": (14, 14, 1, 1)})
    ok &= check("...and emits it verbatim when it does",
                "projectile.set_box(14, 14, 1, 1)" in on)

    # The geometry the fix is about, as the runtime computes it.
    def hits(bx, by, bw, bh, ax, ay, aw, ah):
        sx, sy = 0 + bx, 0 + by          # shot cell top-left at (0, 0)
        return not (sx + bw <= ax or ax + aw <= sx
                    or sy + bh <= ay or ay + ah <= sy)

    # a 14x14 target under the shot's bottom-right quadrant
    old = hits(0, 0, 8, 8, 12, 12, 14, 14)
    new = hits(1, 1, 14, 14, 12, 12, 14, 14)
    ok &= check("a target under the shot's bottom-right quadrant is MISSED by "
                "the old 8x8 corner box and HIT by the authored 14x14 one",
                (not old) and new)
    return ok


# ---------------------------------------------------------------------------
# THE PER-OBJECT DESCRIPTOR (set_desc) - the reference engine's own projectiles_render
# ---------------------------------------------------------------------------
def _test_projectile_desc():
    """A shot draws through the generated clips table, and its `tile` operand
    is then a FRAME NUMBER rather than a tile offset.

    That distinction is the whole defect this arm shipped with. `launch16`
    folded the room's VRAM base into `p_tile` for the dense path, where it
    belongs; under a descriptor `render` hands `p_tile + p_fidx` to
    `clips.draw` as its frame index and passes the base SEPARATELY as the tile
    argument, so folding it in twice offset the index by it. Measured on
    the shooter conversion with the pool state read live: a base of 92
    turned frame 0 into `F_OFF[6 * 16] + 92` = 577 against a 528-entry table,
    and the fan drew whatever ROM bytes follow - both objects at the same x,
    tile = the base itself, no FLIP_X. After: `23:(y124 x80 t92 p00)
    24:(y124 x88 t92 p20)`, which is the descriptor's own (0, 0) and (0, 8)
    with the mirrored half flipped.
    """
    print("")
    print("[projectile.set_desc: a shot is a per-OBJECT metasprite]")
    ok = True
    ok &= check("vm.projectile exposes set_desc", "function set_desc" in SRC)
    ok &= check("...and exports it", _exported(SRC, "set_desc"))
    ok &= check("it is OPT-IN: unset, the dense set_tile / set_meta path runs",
                "var has_pdesc: u8" in SRC and "if has_pdesc == 1 {" in SRC)

    launch = SRC.split("function launch16")[1].split("function launch_angle")[0]
    ok &= check("a DESCRIPTOR launch stores the operand as a FRAME NUMBER",
                "p_tile[slot] = tile" in launch.replace("tile + g_tbase", ""))
    ok &= check("...and the dense one still folds in the room's VRAM base",
                "p_tile[slot] = tile + g_tbase" in launch)
    ok &= check("the fork is on has_pdesc, so neither path can take the "
                "other's meaning", "if has_pdesc == 1 {" in launch)

    render = SRC.split("function render()")[1]
    ok &= check("the descriptor arm passes the frame index and the tile base "
                "as SEPARATE arguments",
                "g_pdraw(base, g_pkind, 0, 0, p_tile[i] + p_fidx[i], g_tbase)"
                in render)
    ok &= check("parking a LIST base does not assert a dense shape over it",
                "if has_pdesc == 0 and p_fan > 1 {" in render)

    # The generator wires the seam only for a project that has a pool.
    base = {"types": ["shmup"], "uses_projectile": True, "proj_cell": (2, 2)}
    off = emit_rooms_mos(base)
    ok &= check("a project with no [projectiles] desc_kind emits NO set_desc "
                "(byte-identical)", "set_desc" not in off)
    on = emit_rooms_mos(dict(base, proj_desc_kind=6))
    ok &= check("...and emits the clips seam when it does",
                "projectile.set_desc(clips.draw, 6)" in on)
    ok &= check("kind 0 is a real kind, not 'unset'",
                "projectile.set_desc(clips.draw, 0)"
                in emit_rooms_mos(dict(base, proj_desc_kind=0)))
    return ok


# ---------------------------------------------------------------------------
# THE FOLDED CEILING IN A REAL BUILD
# ---------------------------------------------------------------------------
#: A first-party sample with a projectile pool on the GB family AND on SMS/GG
#: (its generated rooms.mos wires vm.projectile), built fresh from a temp copy
#: so neither a stale build nor the real sample is ever read or written.
SUBJECT = "vm-shmup"


def _test_built_ceiling():
    """Build the subject for the three consoles and read the folded const.

    The const folds to a #define in whichever TU the module banks into, so
    every generated C file is scanned rather than guessing the file. Skips
    only without a toolchain; a subject that builds and does NOT carry the
    define is a FAIL (it would prove nothing about the fork).
    """
    import glob
    import shutil
    import subprocess
    import tempfile
    sys.path.insert(0, os.path.join(HERE, ".."))
    from mosaik8_build import gbdk_available
    print("")
    print("[the built OAM ceiling folds per console (%s)]" % SUBJECT)
    if not gbdk_available():
        print("  [skip] GBDK not installed - no build to read the folded clamp from")
        return True
    root = os.path.abspath(os.path.join(HERE, ".."))
    tmp = tempfile.mkdtemp(prefix="projcell_")
    ok = True
    try:
        proj = os.path.join(tmp, SUBJECT)
        shutil.copytree(os.path.join(root, "projects", SUBJECT), proj,
                        ignore=shutil.ignore_patterns("build"))
        for plat, want in (("gameboy", 40), ("sms", 64), ("gamegear", 64)):
            r = subprocess.run([sys.executable, os.path.join(root, "mosaik8.py"),
                                "build", "--platform", plat, proj],
                               capture_output=True, text=True, cwd=root,
                               encoding="utf-8", errors="replace")
            out = (r.stdout or "") + (r.stderr or "")
            if not check("%s: the subject builds" % plat, "ROM created" in out):
                print(out[-2000:])
                ok = False
                continue
            defs = set()
            for f in glob.glob(os.path.join(proj, "build", plat, "*.c")):
                c = open(f, encoding="utf-8", errors="replace").read()
                for ln in c.splitlines():
                    if "define vm_projectile_OAM_MAX" in ln:
                        defs.add(ln.strip())
            ok &= check("%s: the built ceiling folds to %d" % (plat, want),
                        defs == {"#define vm_projectile_OAM_MAX (%d)" % want})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return ok


def main():
    print("a projectile is drawn at its cell's real size")
    print("=" * 58)
    ok = True

    ok &= check("set_cell is exported", "export" in SRC and "set_cell" in SRC)
    ok &= check("the default cell is 1x1 (opt-in, byte-identical off)",
                "var p_cw: u8 = 1" in SRC and "var p_ch: u8 = 1" in SRC
                and "var p_fan: u8 = 1" in SRC)
    ok &= check("a 1x1 cell keeps the single set_tile object",
                "if p_fan == 1 {" in SRC and "sprite.set_tile(base, t)" in SRC)
    ok &= check("a bigger cell fans a metasprite",
                "sprite.set_meta(base, t, p_cw, p_ch)" in SRC)
    ok &= check("the per-shot base steps by the FAN, not by 1",
                "return proj_slot + i * p_fan" in SRC)
    # The block SIZES ITSELF to the space left, and the base is never moved.
    # Clamping the base down walks the whole projectile range backwards into
    # the actor fans below it, and a live shot then overwrites them - measured
    # on the shooter conversion (SMS) as the score digits showing shot art whenever
    # something was in flight.
    ok &= check("the fit clamps the SLOT COUNT, not the base",
                "local function fit_slots()" in SRC
                and "p_slots = n" in SRC
                and "b = OAM_MAX - span" not in SRC)
    ok &= check("a launch may only claim a slot the block has",
                "while i < p_slots {" in SRC)
    ok &= check("the ceiling is the room's own (set_top), not the whole table",
                "function set_top(t: u8)" in SRC and "if p_top > 0 {" in SRC)
    ok &= check("parking asserts the fan shape first",
                "sprite.set_meta(pb, 0, p_cw, p_ch)" in SRC)

    # The 8x16 fork: one object per two stacked tiles wherever the mode is
    # ON. It is a BUILD-stated flag (`VM_OBJ16`, from
    # `platforms.obj16_effective`), never an `if platform` list here - the
    # mode is a per-project choice on six consoles, so the list spelling was
    # right only because every project that calls set_cell asks for it.
    ok &= check("obj_8x16 halves the fan, off the build-stated flag",
                "if VM_OBJ16 {" in SRC.split("function set_cell")[1][:800]
                and 'if platform == "gameboy"'
                not in SRC.split("function set_cell")[1][:800])

    # The OAM ceiling is a per-console FORK, not a literal. (It cannot be
    # checked by compiling this module alone - it has imports, and a lone
    # module emits nothing - so read the fork itself, and then the generated C
    # of a real build below.)
    fork = SRC.split("const NPROJ")[1].split("var proj_slot")[0]
    ok &= check("the OAM ceiling forks per console",
                'if platform == "sms"' in fork
                and "const OAM_MAX: u8 = 64" in fork
                and "const OAM_MAX: u8 = 40" in fork)
    ok &= _test_built_ceiling()

    # The generator wires it only for a world whose sheet cell needs it.
    base = {"types": ["shmup"], "uniform": False, "has_collision": True,
            "uses_projectile": True}
    plain = emit_rooms_mos(base)
    ok &= check("a 1x1 sheet emits no set_cell (byte-identical)",
                "set_cell" not in plain)
    tall = emit_rooms_mos(dict(base, proj_cell=(2, 2)))
    ok &= check("a 16x16 sheet emits set_cell(2, 2)",
                "projectile.set_cell(2, 2)" in tall)
    ok &= check("the generated shell hands the pool the room's OAM ceiling",
                "projectile.set_top(" in plain)
    ok &= _test_projectile_box()
    ok &= _test_projectile_desc()

    print("=" * 58)
    print("All checks passed" if ok else "Some tests failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
