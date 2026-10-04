#!/usr/bin/env python3
"""W7b - the follow camera's OPTIONS: the reference engine's `camera_settings` parity byte
plus its four camera properties.

The BEHAVIOUR is measured
on a real ROM by `projects/vm-camprops/verify.py` (in the default suite path via
`project_verify_test.py`); this file pins the contracts a ROM cannot show:

  * state 4 IS the reference engine's byte, flag for flag, and its polarity is the
    OPPOSITE of the bool it replaced - 0 is the full pin, 3 resumes following.
    The polarity is the whole risk of the row, so it is asserted from three
    sides: the ISA constants, the RefVM, and the lowering.
  * the four properties are CONSECUTIVE ids behind ONE range test, and the
    flag that folds them is the OR of all four - the reference engine's event writes one
    property per use, unlike EVENT_CAMERA_SET_BOUNDS, whose flag may key off
    its first id.
  * a dead zone is per SCENE and a follow offset is GLOBAL (the reference VM's
    `camera_reset()` zeroes the deadzones and nothing else). A one-room probe
    cannot tell those apart, so it is a source contract.
  * a project that tunes NEITHER keeps the plain follow path character for
    character - which is what makes the row free on the Lynx and PC Engine,
    where `vm.player` cannot bank.

    python tests/camera_props_test.py
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

passed = failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print("  [ok] %s" % label)
    else:
        failed += 1
        print("  [FAILED] %s%s" % (label, ("  -- " + detail) if detail else ""))


def _read(*parts):
    return io.open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_isa():
    print("[the ids and the flag values]")
    from mosaik_vm import isa
    check("camera_lock is still state 4 (the id does not move, the MEANING did)",
          isa.STATES.get("camera_lock") == 4)
    for name, sid in (("camera_deadzone_x", 36), ("camera_deadzone_y", 37),
                      ("camera_offset_x", 38), ("camera_offset_y", 39)):
        check("%s is state %d" % (name, sid), isa.STATES.get(name) == sid,
              repr(isa.STATES.get(name)))
    ids = [isa.STATES["camera_deadzone_x"], isa.STATES["camera_deadzone_y"],
           isa.STATES["camera_offset_x"], isa.STATES["camera_offset_y"]]
    check("the four are CONSECUTIVE, so set_state spends one range test",
          ids == list(range(ids[0], ids[0] + 4)), repr(ids))
    # the reference engine's camera.h, number for number.
    check("the flag values are the reference engine's own",
          (isa.CAM_LOCK_X, isa.CAM_LOCK_Y, isa.CAM_NO_LEFT, isa.CAM_NO_RIGHT,
           isa.CAM_NO_UP, isa.CAM_NO_DOWN) == (0x01, 0x02, 0x04, 0x08,
                                               0x10, 0x20))
    check("following both is 3 (camera_init's value) and the pin is 0",
          isa.CAM_FOLLOW_BOTH == 3 and isa.CAM_PINNED == 0)
    check("the dead zone's ceiling is the reference engine's 40",
          isa.CAMERA_DEADZONE_MAX == 40)


def test_lowering():
    print("[the lowering: which byte each event writes]")
    from mosaik_vm import events, isa
    from mosaik_vm.compiler import Compiler

    def bytes_of(ev):
        cc = Compiler()
        out = []
        for name, ops in events.EVENTS[ev["event"]](ev, cc):
            out.append((name, ops))
        return out

    rel = bytes_of({"event": "camera_release"})
    check("camera_release writes the settings state",
          rel[-1][0] == "SET_STATE"
          and rel[-1][1] == [isa.STATES["camera_lock"]], repr(rel))
    check("... and its default byte is 3, follow both",
          events._cam_settings({}) == 3)
    check("axis 1 is x only, axis 2 is y only",
          (events._cam_settings({"axis": 1}),
           events._cam_settings({"axis": 2})) == (1, 2))
    check("the four block flags OR in the reference engine's own pairing "
          "(left = X_MIN, right = X_MAX)",
          events._cam_settings({"no_left": 1, "no_right": 1, "no_up": 1,
                                "no_down": 1}) == 0x3F)
    # An out-of-range axis is a VmError, not a silently different camera.
    try:
        events._cam_settings({"axis": 5})
        check("an impossible axis is refused", False)
    except Exception as exc:                        # noqa: BLE001
        check("an impossible axis is refused", "axis" in str(exc), str(exc))

    for n, name in enumerate(("camera_deadzone_x", "camera_deadzone_y",
                              "camera_offset_x", "camera_offset_y")):
        got = bytes_of({"event": "camera_prop", "prop": n, "value": 7})
        check("camera_prop %d writes %s" % (n, name),
              got[-1] == ("SET_STATE", [isa.STATES[name]]), repr(got))
    try:
        events.EVENTS["camera_prop"]({"event": "camera_prop", "prop": 9}, None)
        check("an impossible property is refused", False)
    except Exception as exc:                        # noqa: BLE001
        check("an impossible property is refused", "prop" in str(exc),
              str(exc))


def test_refvm():
    print("[the reference interpreter]")
    from mosaik_vm.refvm import RefVM
    vm = RefVM(b'')
    check("the byte starts at 3 (follow both), as camera_init leaves it",
          vm.cam_set == 3 and vm.cam_lock == 0)
    vm._set_state(4, 0)
    check("writing 0 PINS (follow neither axis)", vm.cam_lock == 1)
    check("... and the read-back masks the two lock bits off",
          vm._get_state(4) & 3 == 0)
    vm._set_state(4, 0x0D)          # follow x, block left and right
    check("writing a byte with a lock bit resumes following",
          vm.cam_lock == 0 and vm.cam_set == 0x0D)
    check("... and reads back whole while nothing else owns the camera",
          vm._get_state(4) == 0x0D)
    vm._set_state(36, 99)
    vm._set_state(37, -5)
    check("a dead zone is clamped to the reference engine's 0..40",
          vm.cam_deadzone == [40, 0], repr(vm.cam_deadzone))
    vm._set_state(38, 24)
    vm._set_state(39, -8)
    check("an offset is its signed byte and is NOT clamped",
          vm.cam_offset == [24, -8], repr(vm.cam_offset))


def test_source_contracts():
    print("[the source contracts a ROM cannot show]")
    core = _read("lib", "vm", "core.mos")
    check("core.mos's consts match the ISA ids",
          "const ST_CAM_DEADZONE_X = 36" in core
          and "const ST_CAM_OFFSET_Y = 39" in core)
    arm = core.split("local function set_state")[1]
    arm = arm.split("\n    bank(0)")[0]
    check("the properties are behind VM_CAM_PROPS - set_state is bank(0) "
          "resident, so four unguarded else-ifs are paid by every project",
          "if VM_CAM_PROPS {" in arm)
    check("... and the four consecutive ids collapse to ONE range test",
          "player.set_cam_prop(sid - ST_CAM_DEADZONE_X" in arm)
    check("the settings byte goes through one call, not an if/else on 0",
          "player.set_cam_settings(v)" in arm
          and "player.cam_hold()" not in arm)

    player = _read("lib", "vm", "player.mos")
    check("vm.player exports the three new entry points",
          "export cam_settings, set_cam_settings, set_cam_prop" in player)
    check("the parity byte defaults to 3, so an unwritten state is the camera "
          "every project already had",
          "var cam_set: u8 = 3" in player)
    check("follow_axis takes the camera's PREVIOUS position, which is what "
          "the reference VM's camera_clamp_x is",
          "local function follow_axis(cur: u16," in player)
    # The lifetimes. The reference VM's camera_reset() zeroes the deadzones ONLY.
    clear = player.split("local function clear_wide()")[1]
    clear = clear.split("\n    function ")[0]
    check("clear_wide zeroes the DEAD ZONES (per scene, the reference VM's camera_reset)",
          "cam_dz_x = 0" in clear and "cam_dz_y = 0" in clear)
    check("... and NOT the offsets, which are global (only camera_init "
          "clears them there)",
          "cam_off_x" not in clear and "cam_off_y" not in clear)
    # Both follow paths consult the byte, or a wide room ignores half of it.
    for fn in ("local function place_camera()", "local function calc_wide_cam()"):
        body = player.split(fn)[1].split("\n    -- ")[0]
        check("%s is gated on VM_CAM_FOLLOW_OPTS" % fn.split()[-1],
              "if VM_CAM_FOLLOW_OPTS {" in body)
        check("... and its OFF arm keeps the plain follow verbatim",
              "camera.follow(" in body or "center - HALFW" in body)


def test_build_defines():
    print("[the build's derived flags]")
    import mosaik8_build as B

    def defines(src):
        return B._vm_dispatch_defines([("scripts.mos", src)])

    # A blob is scanned for the STATE ids it writes; the scan needs a real
    # scripts module, so lean on two projects instead of synthesising one.
    def proj(name):
        import glob
        d = os.path.join(ROOT, "projects", name, "src")
        srcs = [(f, io.open(f, encoding="utf-8").read())
                for f in sorted(glob.glob(os.path.join(d, "*.mos")))]
        return B._vm_dispatch_defines(srcs)

    d = proj("vm-camprops")
    check("a project that writes the properties states VM_CAM_PROPS",
          d.get("VM_CAM_PROPS") is True)
    check("... and VM_CAM_FOLLOW_OPTS with it",
          d.get("VM_CAM_FOLLOW_OPTS") is True)
    d = proj("vm-cam")
    check("a project that only RELEASES the camera keeps the properties folded",
          d.get("VM_CAM_PROPS") is False
          and d.get("VM_CAM_FOLLOW_OPTS") is True)
    d = proj("vm-snake")
    check("a project that touches neither states both False (the plain "
          "follow, character for character)",
          d.get("VM_CAM_PROPS") is False
          and d.get("VM_CAM_FOLLOW_OPTS") is False)


def test_generated_c_is_unchanged_off():
    print("[byte-identical off, on the generated C]")
    import glob
    from mosaik import MosaikCompiler

    player = _read("lib", "vm", "player.mos")
    gates = player.count("if VM_CAM_FOLLOW_OPTS {")
    check("the gate appears exactly THREE times - once in the narrow follow "
          "and once per axis of the wide one - never sprinkled", gates == 3,
          "%d occurrences" % gates)

    # Compile vm.player on its own, both ways, on a GBDK console and a cc65
    # one. The cc65 half is the point of the gate: `vm.player` is in every GB
    # project's `code_banks` and costs bank 0 nothing there, but the Lynx and
    # the PC Engine link one flat MAIN.
    # vm.player imports engine.camera and the animation packs, so the compile
    # needs the whole library tree - a lone module is an "unknown module" error.
    # vm.player imports engine.camera, so the compile needs the library tree -
    # a lone module is an "unknown module" error. `music_huge.mos` is left OUT:
    # it is Game Boy APU assembly and a hard error on the SMS, which is exactly
    # one of the consoles this comparison is for.
    srcs = []
    for sub in ("vm", "engine"):
        for f in sorted(glob.glob(os.path.join(ROOT, "lib", sub, "*.mos"))):
            if os.path.basename(f) == "music_huge.mos":
                continue
            srcs.append((os.path.basename(f),
                         io.open(f, encoding="utf-8").read()))
    for plat in ("gameboy", "gameboy_color", "sms", "lynx", "pce"):
        out = {}
        for on in (False, True):
            got = MosaikCompiler().compile_program(
                srcs, platform=plat,
                defines={"VM_CAM_FOLLOW_OPTS": on, "VM_CAM_PROPS": on})
            if got.startswith("Compilation error"):
                check("vm.player compiles on %s with the gate %s"
                      % (plat, on), False, got.splitlines()[0])
                return
            out[on] = got
        check("%s: the gate ON is BIGGER, so the OFF arm really is the old "
              "path" % plat, len(out[True]) > len(out[False]),
              "off %d on %d" % (len(out[False]), len(out[True])))
    check(True, "vm.player compiles on every console class, gate on and off")


if __name__ == "__main__":
    print("W7b camera options checks")
    print("=" * 50)
    test_isa()
    test_lowering()
    test_refvm()
    test_source_contracts()
    test_build_defines()
    test_generated_c_is_unchanged_off()
    print("=" * 50)
    if failed:
        print("%d check(s) FAILED, %d passed" % (failed, passed))
        sys.exit(1)
    print("All W7b camera checks passed (%d)" % passed)
