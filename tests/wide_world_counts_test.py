#!/usr/bin/env python3
"""E6: a VM8 world's placed objects, doors and triggers past 255.

The world index of an object, door or trigger is read only at room load (and
by `start` for the player's spot); everything at run time keys on the pool
slot. So a family past 255 entries changes the `scenes` module and the
rooms.mos walks, no opcode and no operand:

  - its count is `const <F>_COUNT: u16` and its readers take a u16 index
    (`obj_pinned` too); 255 or fewer stays u8 and byte for byte;
  - it is in ROOM ORDER (objects are already; doors and triggers are sorted
    STABLY by their room, so a room's own order is kept) with a range table
    `<F>_FIRST[scene]` (u16, scenes + 1) behind a reader `<f>_first_at`;
  - rooms.mos walks only `<f>_first_at(rm) .. <f>_first_at(rm + 1)`, its end
    read once, instead of the whole world;
  - the per-console scene fork decides "wide" on the FULL world, so every
    branch has the range tables its export list names;
  - vm.door's doors per room is `VM_DOOR_POOL`, DERIVED by the build from the
    scenes module's `DOOR_ROOM_MAX` (the busiest room, stated past 4), no knob.

A hand-written (non-VM) world indexes these tables itself and keeps its u8
counts and the 255 refusal (`scene_transpile_test`).

Checks: the generated text of a narrow and a wide world, the forked world,
the build's `_door_pool`, and a real GB ROM (GBDK + PyBoy present): a world
of 9 rooms with 267 objects, 273 doors and 269 triggers (8 filler rooms of 33
each, under `[build] actor_pool` / `trigger_pool` = 36), written to the file
ROUND ROBIN (so file order is not room order), boots into its LAST room,
whose entries sit past index 255 and whose 9 doors pass vm.door's default 4.
After boot vm.door holds 9 doors, vm.trigger 5 triggers, vm.actor the room's
two NPCs at their placed x, and the player stands on its placed spot.
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import mosaik8_build  # noqa: E402
import mosaik_scenes  # noqa: E402
import vm_fixture  # noqa: E402

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


N_FILL = 8           # filler rooms, PER objects / doors / triggers each
PER = 33
POOLS = "actor_pool = 36\ntrigger_pool = 36"
TEST_DOORS = 9       # the last room's doors: past vm.door's default 4
TEST_TRIGS = 5
NPC_X = (100, 132)
PLAYER = (120, 96)


def _scene(name):
    return {"name": name, "map": [[0] * 20 for _ in range(18)]}


def world_dict(wide=True):
    """The test world as a dict. `wide` False keeps every family under 256
    (2 filler rooms) for the narrow checks."""
    n_fill = N_FILL if wide else 2
    scenes = [_scene("f%02d" % s) for s in range(n_fill)] + [_scene("zz")]
    for s in range(n_fill):
        scenes[s]["object"] = [{"kind": "npc", "x": 8 + 16 * k, "y": 8}
                               for k in range(PER)]
    scenes[-1]["object"] = ([{"kind": "player", "x": PLAYER[0], "y": PLAYER[1]}]
                            + [{"kind": "npc", "x": x, "y": 40} for x in NPC_X])
    # The LAST room's doors and triggers come FIRST in the file and the
    # fillers' ROUND ROBIN after them: file order is not room order.
    doors = [{"from": "zz", "tx": 2 * k, "ty": 16, "to": "f00", "ex": 16, "ey": 16,
              "id": k} for k in range(TEST_DOORS if wide else 3)]
    trigs = [{"from": "zz", "tx": 2 * k, "ty": 1, "tw": 1, "th": 1, "on_enter": "noop",
              "id": k} for k in range(TEST_TRIGS)]
    for k in range(PER):
        for s in range(n_fill):
            if wide or k < 4:    # narrow: vm.door's default 4 a room
                doors.append({"from": "f%02d" % s, "tx": 2 * k, "ty": 16, "to": "zz",
                              "ex": 16, "ey": 16, "id": 100 + PER * s + k})
            trigs.append({"from": "f%02d" % s, "tx": 2 * k, "ty": 1, "on_enter": "noop",
                          "id": 100 + PER * s + k})
    return {"world": {"module": "scenes", "map_w": 20, "map_h": 18, "vm": True,
                      "start_scene": "zz"},
            "tileset": {"png": "tiles.png"},
            "kinds": {"player": 0, "npc": 1},
            "scene": scenes, "door": doors, "trigger": trigs}


SCRIPTS = """[[script]]
name = "main"
events = [
  { event = "stop" },
]

[[script]]
name = "noop"
events = [
  { event = "stop" },
]
"""


def _toml(w):
    import toml
    return toml.dumps(w)


def _array(src, name):
    m = re.search(r"const %s: array\[(u8|u16), (\d+)\] = \[(.*?)\]" % name, src, re.S)
    if not m:
        return None, None
    return m.group(1), [int(v) for v in re.findall(r"\d+", m.group(3))]


def _rooms_text(w, tmp):
    from mosaik_vm.rooms import generate_rooms
    root = os.path.join(tmp, "p")
    os.makedirs(os.path.join(root, "src"), exist_ok=True)
    with open(os.path.join(root, "mosaik.toml"), "w", encoding="utf-8") as f:
        f.write("[build]\n" + POOLS + "\n")
    shutil.copy(os.path.join(ROOT, "projects", "vm-clipdemo", "tiles.png"),
                os.path.join(root, "tiles.png"))
    out = os.path.join(root, "src", "rooms.mos")
    generate_rooms(root, out_path=out, world=w, base=root)
    with open(out, encoding="utf-8") as f:
        return f.read()


def generated_text(tmp):
    shutil.copy(os.path.join(ROOT, "projects", "vm-clipdemo", "tiles.png"),
                os.path.join(tmp, "tiles.png"))
    print("\n[narrow: 255 or fewer stays u8, file order, no ranges]")
    nw = world_dict(wide=False)
    src = mosaik_scenes.transpile(nw, tmp)
    check("the counts stay u8",
          all("const %s_COUNT: u8 = " % f in src for f in ("OBJ", "DOOR", "TRIG")),
          re.findall(r"const \w+_COUNT: \w+ = \d+", src))
    check("... the readers take a u8 index",
          "function door_from_at(i: u8)" in src and "function obj_x_at(i: u8)" in src
          and "function trig_from_at(i: u8)" in src)
    check("... no range table, no range reader",
          "_FIRST" not in src and "_first_at" not in src)
    _, dfrom = _array(src, "DOOR_FROM")
    check("... doors keep the FILE order (the last room's first)",
          dfrom is not None and dfrom[:3] == [2, 2, 2] and dfrom[3:5] == [0, 1], dfrom)
    check("... no DOOR_ROOM_MAX at 4 or fewer doors a room", "DOOR_ROOM_MAX" not in src)
    rsrc = _rooms_text(nw, tmp)
    check("... rooms.mos walks the whole world",
          "for i in 0..scenes.DOOR_COUNT {" in rsrc and "for i in 0..scenes.OBJ_COUNT {" in rsrc
          and "for i in 0..scenes.TRIG_COUNT {" in rsrc and "_first_at" not in rsrc)

    print("\n[wide: past 255 a family is u16, in room order, walked by range]")
    w = world_dict()
    src = mosaik_scenes.transpile(w, tmp)
    n_obj, n_door, n_trig = PER * N_FILL + 3, PER * N_FILL + TEST_DOORS, PER * N_FILL + TEST_TRIGS
    check("the counts are u16 (%d / %d / %d)" % (n_obj, n_door, n_trig),
          "const OBJ_COUNT: u16 = %d" % n_obj in src
          and "const DOOR_COUNT: u16 = %d" % n_door in src
          and "const TRIG_COUNT: u16 = %d" % n_trig in src,
          re.findall(r"const \w+_COUNT: \w+ = \d+", src))
    readers = re.findall(r"function ((?:obj|door|trig)_\w+_at)\(i: (u8|u16)\)", src)
    check("every object / door / trigger reader takes a u16 index (%d)" % len(readers),
          len(readers) >= 15 and all(t == "u16" for n, t in readers
                                     if not n.endswith("first_at")), readers)
    for fam, frm, n_test, per_fill in (("OBJ", "OBJ_SCENE", 3, PER),
                                       ("DOOR", "DOOR_FROM", TEST_DOORS, PER),
                                       ("TRIG", "TRIG_FROM", TEST_TRIGS, PER)):
        want = [per_fill * s for s in range(N_FILL + 1)] + [per_fill * N_FILL + n_test]
        ty, first = _array(src, fam + "_FIRST")
        check("%s_FIRST is u16, one entry a room + 1, each room's first index" % fam,
              ty == "u16" and first == want, (ty, first, want))
        _, rooms = _array(src, frm)
        check("... and %s is in ROOM ORDER" % frm,
              rooms == sorted(rooms) and rooms[-1] == N_FILL, rooms and rooms[-12:])
    _, dtx = _array(src, "DOOR_TX")
    check("the sort is STABLE: a room's doors keep their file order",
          dtx[-TEST_DOORS:] == [2 * k for k in range(TEST_DOORS)]
          and dtx[:PER] == [2 * k for k in range(PER)], (dtx[:PER], dtx[-TEST_DOORS:]))
    exports = re.search(r"export (.*)", src).group(1)
    check("the export list names the three range readers",
          all(" %s," % r in " " + exports + "," or (", " + r + ",") in exports + ","
              for r in ("obj_first_at", "door_first_at", "trig_first_at")), exports[:300])
    m = re.search(r"const DOOR_ROOM_MAX: u8 = (\d+)", src)
    check("DOOR_ROOM_MAX states the busiest room's %d doors" % PER,
          m is not None and int(m.group(1)) == PER, m and m.group(0))
    rsrc = _rooms_text(w, tmp)
    for fam, room in (("door", "rm"), ("trig", "rm"), ("obj", "rm"), ("obj", "start_room")):
        head = ("        var %s_end: u16 = scenes.%s_first_at(%s + 1)\n"
                "        for i in scenes.%s_first_at(%s)..%s_end {"
                % (fam, fam, room, fam, room, fam))
        check("rooms.mos walks the %s range of `%s`, its end read once" % (fam, room),
              head in rsrc, re.findall(r".*for i in .*", rsrc))
    check("... and no whole-world walk is left",
          "0..scenes.OBJ_COUNT" not in rsrc and "0..scenes.DOOR_COUNT" not in rsrc
          and "0..scenes.TRIG_COUNT" not in rsrc)

    print("\n[the per-console scene fork decides on the FULL world]")
    fw = world_dict()
    for s in range(3):
        fw["scene"][s]["platforms"] = ["gameboy"]
    fsrc = mosaik_scenes.transpile(fw, tmp)
    counts = re.findall(r"const OBJ_COUNT: (u8|u16) = (\d+)", fsrc)
    check("the module forks, and a branch drops the tagged rooms' objects to %d"
          % (n_obj - 3 * PER), len(counts) >= 2
          and ("u16", str(n_obj - 3 * PER)) in counts, counts)
    check("... every branch is still wide (u16 count, its own OBJ_FIRST)",
          all(t == "u16" for t, _ in counts)
          and fsrc.count("const OBJ_FIRST:") == len(counts), counts)
    check("... and the one export list names the readers once",
          fsrc.count("obj_first_at,") + fsrc.count("obj_first_at\n") == 1
          or re.search(r"export .*obj_first_at", fsrc) is not None)

    print("\n[the build derives vm.door's pool]")
    srcs = [("scenes.mos", 'module "scenes" {\n    const DOOR_ROOM_MAX: u8 = 9\n}\n'),
            ("other.mos", 'module "other" {\n    const DOOR_ROOM_MAX: u8 = 77\n}\n')]
    check("_door_pool reads the scenes module's DOOR_ROOM_MAX (9)",
          mosaik8_build._door_pool(srcs) == 9, mosaik8_build._door_pool(srcs))
    forked = [("scenes.mos", 'module "scenes" {\n    if platform == "gameboy" {\n'
               '    const DOOR_ROOM_MAX: u8 = 6\n    } else {\n'
               '    const DOOR_ROOM_MAX: u8 = 11\n    }\n}\n')]
    check("... the largest branch wins (11)", mosaik8_build._door_pool(forked) == 11)
    check("... and states nothing without one (the default 4)",
          mosaik8_build._door_pool([("scenes.mos", 'module "scenes" {\n}\n')]) is None)
    lib = open(os.path.join(ROOT, "lib", "vm", "door.mos"), encoding="utf-8").read()
    check("vm.door sizes its MAXD and all eight tables by VM_DOOR_POOL",
          "const MAXD = VM_DOOR_POOL" in lib and lib.count("VM_DOOR_POOL]") == 8
          and not re.search(r"array\[u(8|16), 4\]", lib))


def _syms(noi):
    out = {}
    with open(noi, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"DEF\s+(\S+)\s+0x([0-9A-Fa-f]+)\s*$", line.strip())
            if m:
                out[m.group(1)] = int(m.group(2), 16)
    return out


def rom():
    print("\n[a real GB ROM: the last room, every entry past index 255]")
    try:
        import pyboy  # noqa: F401
    except Exception:
        print("  (skip: PyBoy not installed)")
        return
    if not mosaik8_build.gbdk_available():
        print("  (skip: GBDK not installed)")
        return
    # Banked the way a real game of this size is: the world's tables do not
    # fit an unbanked 32 KB cart beside the runtime.
    fx = vm_fixture.build(_toml(world_dict()), SCRIPTS, debug=True,
                          extra_build=POOLS + '\ncode_banks = ["scenes", "rooms", '
                                      '"vm.core", "vm.actor", "vm.entity", "vm.trigger", '
                                      '"vm.door", "vm.player", "vm.canim", "engine.anim", '
                                      '"engine.camera"]')
    try:
        check("the fixture's script and world compiled",
              fx.scripts.returncode == 0 and fx.scenes.returncode == 0,
              (fx.scripts.stdout + fx.scripts.stderr + fx.scenes.stderr)[-400:])
        if not fx.rom:
            check("the fixture builds", False, (fx.build.stdout + fx.build.stderr)[-800:])
            return
        noi = os.path.splitext(fx.rom)[0] + ".noi"
        syms = _syms(noi)
        need = ["_vm_door_dn", "_vm_trigger_tn", "_vm_actor_a_active", "_vm_actor_a_x",
                "_vm_player_px", "_vm_player_py"]
        missing = [s for s in need if s not in syms]
        check("the .noi names every probe symbol", not missing, missing)
        if missing:
            return
        from pyboy import PyBoy
        pb = PyBoy(fx.rom, window="null", cgb=False, sound_emulated=False)
        try:
            for _ in range(300):
                pb.tick(1, False)

            def u8(sym, k=0):
                return pb.memory[(syms[sym] & 0xFFFF) + k]

            def u16(sym, k=0):
                a = (syms[sym] & 0xFFFF) + 2 * k
                return pb.memory[a] | (pb.memory[a + 1] << 8)
            dn, tn = u8("_vm_door_dn"), u8("_vm_trigger_tn")
            act = [u8("_vm_actor_a_active", k) for k in range(4)]
            ax = [u16("_vm_actor_a_x", k) for k in range(2)]
            px, py = u16("_vm_player_px"), u16("_vm_player_py")
        finally:
            pb.stop(save=False)
        check("vm.door holds the room's %d doors (past its old 4)" % TEST_DOORS,
              dn == TEST_DOORS, dn)
        check("vm.trigger holds the room's %d triggers" % TEST_TRIGS, tn == TEST_TRIGS, tn)
        check("vm.actor holds exactly the room's two NPCs", act == [1, 1, 0, 0], act)
        check("... at their placed x %s" % (NPC_X,), tuple(ax) == NPC_X, ax)
        check("the player starts on its placed spot (not start's fallback 80, 72)",
              abs(px - PLAYER[0]) <= 16 and abs(py - PLAYER[1]) <= 16
              and (px, py) != (80, 72), (px, py))
    finally:
        shutil.rmtree(fx.tmp, ignore_errors=True)


def main():
    print("=== wide_world_counts_test ===")
    tmp = tempfile.mkdtemp(prefix="wide_counts_")
    try:
        generated_text(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    rom()
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
