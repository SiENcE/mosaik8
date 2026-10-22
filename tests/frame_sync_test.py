#!/usr/bin/env python3
"""`[build] frame_sync` shows a locked VM8 game frame at the lock's own
v-blank, and a tall shmup streams its rows as one rectangle.

The contract this pins:
  - the knob: absent = off, a bool only, an APPLIED build key;
  - the build states VM_FRAME_SYNC only inside the frame-lock arm (a lock of
    2..8) and only on the GB family; compile_program always supplies the
    False default (statement-level guards must fold);
  - vm.core's run() holds right after the lock samples the clock, releases
    around the pending-exception service (a fade / room load presents frames
    of its own) and holds again after it, and at the tail waits out the
    lock's frames but its LAST with the hold on, then releases before the
    wait that ends the game frame;
  - the GB family's `sprite.vbl_hold` is real (DISABLE_OAM_DMA /
    ENABLE_OAM_DMA plus `gbs_vbl_held`), and gbs_wait_vblank's scroll commit
    stands down while it is held; a GB program that never calls it carries
    neither (byte-identical), and the SMS / Game Gear hold is unchanged;
  - engine.scroll2d's tall-shmup row: put_row2d takes `row_rect` (one
    set_tiles + one set_attrs) under VM_TALL_SHMUP only, on both arms, and
    without it the C carries no row_rect;
  - a metatile world exports `map_tile_rc` and its 2D streamer reads by
    column and row (no `r * w` / `idx % w` / `idx / w` per tile).

ROM-measured (2026-10-22, the studio's projects/raid-vm8, stage 1, PyBoy, SCY
per LCD frame over 600): steps after 1 / 2 / 3 / 4 display frames went
22 / 184 / 47 / 17 -> 1 / 252 / 31 / 0 with the row rectangle + frame_sync,
and the frames on which OAM changed without SCY went 205 -> 1. The ROM
behaviour is pinned by projects/vm-tallshmup/verify.py (its frame-sync arm).
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from mosaik import MosaikCompiler           # noqa: E402
from mosaik8_build import BuildConfig       # noqa: E402

FAILS = []


def check(label, cond, note=""):
    print("[%s] %s%s" % ("PASS" if cond else "FAIL", label,
                         ("  -- %s" % note) if note else ""))
    if not cond:
        FAILS.append(label)


def cfg(value):
    c = BuildConfig.__new__(BuildConfig)
    c.config = ({"build": {}} if value is None
                else {"build": {"frame_sync": value}})
    return c


def _src(rel):
    return open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8").read()


def _mos_function(text, name, indent="    "):
    """The body of one mosaik function (a whole-file match finds copies)."""
    m = re.search(r"function %s\(.*?\n%s\}\n" % (re.escape(name), indent), text, re.S)
    return m.group(0) if m else ""


def _c_function(text, sig):
    i = text.find(sig)
    if i < 0:
        return ""
    j = text.find("\n}\n", i)
    return text[i:j + 3]


_HOLD = '''
module "app" {
    import "platform.video"
    import "graphics.sprite"
    import "graphics.bkg"
    function main() {
        loop {
            sprite.vbl_hold(1)
            bkg.move(0, 1)
            sprite.vbl_hold(0)
            video.wait_vblank()
        }
    }
    export main
}
'''

_PLAIN = _HOLD.replace("            sprite.vbl_hold(1)\n", "").replace(
    "            sprite.vbl_hold(0)\n", "")

_ROWS = '''
module "app" {
    import "engine.scroll2d"
    function tile_at(c: u16, r: u16) -> u8 {
        return 1
    }
    function main() {
        scroll2d.fill2d(20, 120, tile_at)
        var y: u16 = 900
        loop {
            scroll2d.update2d(0, y, tile_at)
            y = y - 1
        }
    }
    export main
}
'''


def compile_src(src, platform, defines=None, libs=()):
    sources = [("m.mos", src)] + [(lib, _src(lib)) for lib in libs]
    return MosaikCompiler().compile_program(sources, platform=platform,
                                            defines=defines or {})


def main():
    # 1. The knob.
    check("absent -> off", cfg(None).get_frame_sync() is False)
    check("true reads true", cfg(True).get_frame_sync() is True)
    for bad in (1, "yes"):
        try:
            cfg(bad).get_frame_sync()
            check("%r refused" % (bad,), False)
        except ValueError:
            check("%r refused" % (bad,), True)
    check("the key is an APPLIED build key (config honesty)",
          "frame_sync" in BuildConfig.APPLIED_KEYS["build"])

    # 2. The build states it under the lock, on the GB family; the compiler
    # defaults it.
    build = _src("mosaik8_build.py")
    arm = re.search(r"if _flock > 1:\n(.*?)\n            if self\.config\.get_proj_under_lock",
                    build, re.S)
    arm = arm.group(1) if arm else ""
    check("VM_FRAME_SYNC is stated INSIDE the frame-lock arm",
          "defines['VM_FRAME_SYNC'] = True" in arm)
    check("...under the knob and the GB family only",
          re.search(r"if self\.config\.get_frame_sync\(\) and platform in \(\s*"
                    r"'gameboy', 'gameboy_color', 'analogue_pocket', 'megaduck'\):",
                    arm) is not None)
    check("compile_program always supplies VM_FRAME_SYNC = False",
          "setdefault('VM_FRAME_SYNC', False)" in _src("mosaik/compiler.py"))

    # 3. vm.core's run(): hold, release around the exception service, present.
    run = _mos_function(_src("lib/vm/core.mos"), "run")
    check("run() was found", bool(run))
    check("the hold follows the lock's clock sample at the top",
          re.search(r"lk_t0 = system\.frames\(\)\s*\n\s*\}\s*\n(?:\s*--.*\n)*"
                    r"\s*if VM_FRAME_SYNC \{\s*\n\s*sprite\.vbl_hold\(1\)", run) is not None)
    pend = re.search(r"if pend_code != 0 \{\n(.*?)\n            \}\n", run, re.S)
    pend = pend.group(1) if pend else ""
    check("the exception service OPENS with the release",
          re.match(r"\s*if VM_FRAME_SYNC \{\s*\n\s*sprite\.vbl_hold\(0\)", pend) is not None)
    check("...and CLOSES with the hold",
          re.search(r"if VM_FRAME_SYNC \{\s*\n\s*sprite\.vbl_hold\(1\)\s*\n\s*\}\s*$", pend)
          is not None)
    tail = re.search(r"if VM_FRAME_SYNC \{\s*\n(?:\s*--.*\n)*\s*var sy: u8 = "
                     r"system\.frames\(\) - lk_t0\s*\n\s*while sy \+ 1 < VM_FRAME_LOCK \{"
                     r"\s*\n\s*video\.wait_vblank\(\)\s*\n\s*sy = system\.frames\(\) - lk_t0"
                     r"\s*\n\s*\}\s*\n\s*sprite\.vbl_hold\(0\)\s*\n\s*\}\s*\n\s*"
                     r"video\.wait_vblank\(\)", run)
    check("the tail waits out all but the lock's LAST frame held, then "
          "releases before the wait that ends the game frame", tail is not None)

    # 4. The GB family's hold and the scroll commit.
    held = compile_src(_HOLD, "gameboy_color")
    plain = compile_src(_PLAIN, "gameboy_color")
    sms = compile_src(_HOLD, "sms")
    hold = _c_function(held, "void gbs_spr_vbl_hold(uint8_t on) {")
    check("the GB hold stands the OAM DMA down; the release only marks the "
          "present PENDING (re-enabling the ISR's copy there races a v-blank)",
          "if (on) { DISABLE_OAM_DMA; gbs_vbl_held = 1; }" in hold
          and "else if (gbs_vbl_held) gbs_vbl_held = 2;" in hold
          and "ENABLE_OAM_DMA" not in hold, held[:200] if not hold else "")
    wait = _c_function(held, "void gbs_wait_vblank(void) {")
    check("the wait after a release copies the table in its OWN v-blank, then "
          "hands the copy back to the ISR",
          re.search(r"vsync\(\);\s*\n\s*if \(gbs_vbl_held == 2\) \{.*?\n\s*"
                    r"refresh_OAM\(\);\s*\n\s*ENABLE_OAM_DMA;\s*\n\s*gbs_vbl_held = 0;",
                    wait) is not None, "" if "refresh_OAM" in wait else wait)
    check("gbs_wait_vblank's scroll commit stands down while held",
          re.search(r"if \(!gbs_vbl_held\) \{\s*\n\s*SCX_REG = gbs_scr_shx;\s*\n\s*"
                    r"SCY_REG = gbs_scr_shy;\s*\n\s*\}", wait) is not None,
          "" if "gbs_vbl_held" in wait else wait)
    check("a GB program that never holds carries no hold state",
          "gbs_vbl_held" not in plain and "gbs_spr_vbl_hold" not in plain)
    sms_hold = _c_function(sms, "void gbs_spr_vbl_hold(uint8_t on) {")
    check("the SMS hold is unchanged and has no GB state",
          "DISABLE_VBL_TRANSFER" in sms_hold and "gbs_vbl_held" not in sms)

    # 5. The tall shmup's row rectangle.
    s2d = _src("lib/engine/scroll2d.mos")
    rows = [m.group(0) for m in re.finditer(
        r"function put_row2d\(.*?\n        \}\n", s2d, re.S)]
    check("both arms define put_row2d", len(rows) == 2)
    check("each put_row2d tries row_rect under VM_TALL_SHMUP first",
          len(rows) == 2 and all(re.search(
              r"if VM_TALL_SHMUP \{\s*\n(?:\s*var hr: u8 = r & \(HW - 1\)\s*\n)?"
              r"\s*if row_rect\(r, hr, c, tile_at\) \{\s*\n\s*return", r) for r in rows))
    lib = ("lib/engine/scroll2d.mos",)
    on = compile_src(_ROWS, "gameboy_color", {"VM_TALL_SHMUP": True}, lib)
    off = compile_src(_ROWS, "gameboy_color", {"VM_TALL_SHMUP": False}, lib)
    rr = _c_function(on, "uint8_t engine_scroll2d_row_rect(uint16_t r, uint8_t hr, "
                         "uint16_t c0, gbs_fnptr_0 tile_at) {")
    check("with a tall shmup the row is ONE set_bkg_tiles of n cells",
          re.search(r"set_bkg_tiles\(hc, hr, n, 1, engine_scroll2d_rowbuf\)", rr)
          is not None, on[:300] if not rr else "")
    check("without one the C carries no row_rect (tree-shaken)",
          "row_rect" not in off and "row_rect" in on)

    # 6. A metatile world streams by column and row.
    emit_maps = _src("mosaik_scenes/transpile/emit_maps.py")
    check("the metatile shape emits map_tile_rc",
          "function map_tile_rc(scene: u8, x: u16, y: u16) -> u8 {" in emit_maps)
    check("...and exports it with the metatile table",
          '"METATILE_COUNT", "METATILES", "map_meta", "map_tile_rc"'
          in _src("mosaik_scenes/transpile/emit_tail.py"))
    prelude = _src("mosaik_vm/rooms/emit_prelude.py")
    check("a metatile world's tile_at reads scenes.map_tile_rc",
          'if info.get("metatiles"):' in prelude
          and "scenes.map_tile_rc(room, c, r)" in prelude)

    if FAILS:
        print("\nFAILED: %d" % len(FAILS))
        return 1
    print("\nall frame-sync checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
