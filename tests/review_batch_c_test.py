#!/usr/bin/env python3
"""The 2026-11-03 engine review's BATCH C: first-party behaviour (plan
`docs/engine-review-open-items-plan.md` in the studio, 2026-11-07).

  M14  `actor.hit_scan` keyed the box HEIGHT on its own test (`a_bh == 0`)
       while `activate` clears only the WIDTH: a re-spawned actor was hit
       over the 8 px fallback width at the previous occupant's height and y
       offset (GB / SMS), where the Lynx / PCE arm keys on the width alone.
  H4   console half: `sram.peek` guarded `src` but not `dst` before
       `core.var_set`, which writes `heap[idx]` unguarded.

Each check reads the engine source sliced to the ONE function (never a
whole-file substring). Mutation: on the unfixed tree every check fails.
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

FAILS = []


def check(label, cond, detail=""):
    print("  [%s] %s" % ("PASS" if cond else "FAIL", label))
    if not cond:
        FAILS.append(label + ((" -- " + str(detail)) if detail else ""))


def _src(rel):
    return open(os.path.join(ROOT, *rel.split("/")), encoding="utf-8").read().replace("\r\n", "\n")


def _functions(text, name):
    """Every body of `function name(` in a .mos file, comments stripped."""
    out = []
    for m in re.finditer(r"(?:bank\(0\) )?(?:hot )?(?:local )?function %s\(" % re.escape(name), text):
        start = text.rfind("\n", 0, m.start()) + 1
        indent = m.start() - start
        end = text.find("\n" + " " * indent + "}\n", m.end())
        body = text[start:end + indent + 3]
        out.append("\n".join(ln.split("--")[0].rstrip() for ln in body.splitlines()
                             if ln.split("--")[0].strip()))
    return out


def m14():
    print("\n[M14: hit_scan keys the whole box on the width]")
    hs = _functions(_src("lib/vm/actor.mos"), "hit_scan")
    body = hs[0] if hs else ""
    height = body[body.find("var ah: i16 = a_bh[i]"):]
    check("the height arm tests a_bw[i] == 0 (the fallback is the WHOLE 8x8 box)",
          re.search(r"var ay: i16 = a_y\[i\]\s*\n\s*if a_bw\[i\] == 0 \{\s*\n\s*ah = 8", height)
          is not None, height[:300])
    check("...and never keys on the height alone", "if ah == 0" not in body, body[-500:])


def h4_console():
    print("\n[H4: sram.peek guards its destination]")
    peeks = _functions(_src("lib/vm/sram.mos"), "peek")
    real = [b for b in peeks if "core.var_set(" in b]
    body = real[0] if real else ""
    check("the battery-save peek writes only `if dst < NCELLS`",
          re.search(r"if dst < NCELLS \{\s*\n\s*core\.var_set\(dst, v\)", body) is not None,
          body[-300:])
    check("...and the no-battery twin stays empty", any("var_set" not in b for b in peeks))


def _regen_rooms(name):
    """The rooms module the CURRENT generator writes for a temp copy of a sample."""
    import shutil
    import tempfile
    from mosaik_vm.rooms import generate_rooms
    tmp = tempfile.mkdtemp(prefix="batch_c_")
    try:
        root = os.path.join(tmp, name)
        shutil.copytree(os.path.join(ROOT, "projects", name), root,
                        ignore=shutil.ignore_patterns("build"))
        generate_rooms(root)
        return open(os.path.join(root, "src", "rooms.mos"), encoding="utf-8").read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _load_room_body(src):
    i = src.find("function load_room(")
    return src[i:src.find("\n    }\n", i)] if i >= 0 else ""


def generators():
    print("\n[#17 / #18: the room loader]")
    for name in ("vm-combat", "vm-shmup"):
        src = _regen_rooms(name)
        check("#17 %s (static OAM, projectiles) resets the projectile pool per room"
              % name, "projectile.reset()" in _load_room_body(src.replace("\r\n", "\n")))
    src = _regen_rooms("vm-pointnclick").replace("\r\n", "\n")
    body = _load_room_body(src)
    check("#18 vm-pointnclick (actor On Inits, NO scene init) gets the pre-fade "
          "scheduler pass", "core.run_pending()" in body and "scenes.scene_init(" not in body,
          body[-400:])
    src = _regen_rooms("vm-showcase").replace("\r\n", "\n")
    check("#18 control: a world WITH a scene init keeps ONE pass, before its spawn",
          src.count("core.run_pending()") == 1
          and 0 <= src.find("core.run_pending()") < src.find("scenes.scene_init("),
          src.count("core.run_pending()"))


def music():
    print("\n[#32 / #33 / #31: the hUGE fallback (songs without a .uge)]")
    from mosaik_vm import huge, songs as s
    insts = [("i1", {"kind": "pulse", "duty": 0, "vol": 15, "decay": 0})]

    def song(n, vol=0, fx=None, starts=None):
        e = {"channels": ["pulse"],
             "rows": [s._norm_cell_row([6, 25, 1, vol], 1) for _ in range(n)]}
        if fx:
            e["fx"] = fx
        if starts:
            e["order_starts"] = starts
        return huge.render_song("S", e, insts, "s0")[0]

    t = song(4, vol=8)
    check("#32 a cell volume 8 is C08 (the level in the LOW nibble)",
          "0xC08" in t and "0xC80" not in t)
    check("#33 a 70-row song breaks to the order start on its last row (D00)",
          song(70).count("0xD00") == 1)
    check("#33 ...a song of exactly 64 rows needs no break (control)",
          "0xD00" not in song(64))
    # Three 32-row patterns: order position 2 starts at flat row 64, which is
    # hUGE pattern 1, not hUGE pattern 2.
    fx = [[[0, 0]] for _ in range(96)]
    fx[5] = [[11, 2]]
    t = song(96, fx=fx, starts=[0, 32, 64])
    check("#31 an order jump lands on its position's START (break into hUGE pattern "
          "1 at row 0), not on hUGE pattern 2", "0xD00" in t and "0xB02" not in t)
    import tempfile
    import shutil
    tmp = tempfile.mkdtemp(prefix="batch_c_songs_")
    try:
        row = "[6, 25, 1, 0]"
        pat = "[" + ", ".join([row] * 128) + "]"
        fxrow = "[0, 0]"             # a fx row is FLAT: cmd, param per channel
        brk = "[10, 5]"
        fxp1 = "[" + ", ".join([fxrow] * 3 + [brk] + [fxrow] * 124) + "]"
        fxp0 = "[" + ", ".join([fxrow] * 128) + "]"
        with open(os.path.join(tmp, "songs.toml"), "w", encoding="utf-8") as f:
            f.write("[song.long]\nchannels = [\"pulse\"]\npatterns = [%s, %s, %s]\n"
                    "fxpatterns = [%s, %s, %s]\norder = [0, 1, 2]\n"
                    % (pat, pat, pat, fxp0, fxp1, fxp0))
        defs = dict(s.load_song_defs(tmp))
        check("#31 a pattern break past flat row 255 records the row offset it loses",
              defs.get("long", {}).get("dropped_break_rows") == [5],
              defs.get("long", {}).get("dropped_break_rows"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def hud():
    print("\n[#19: a HUD number draws the whole cell]")
    import shutil
    import tempfile
    import mosaik_vm
    from mosaik_vm.hud import generate_hud
    tmp = tempfile.mkdtemp(prefix="batch_c_hud_")
    try:
        root = os.path.join(tmp, "vm-hud")
        shutil.copytree(os.path.join(ROOT, "projects", "vm-hud"), root,
                        ignore=shutil.ignore_patterns("build"))
        toml = os.path.join(root, "scripts", "hud.toml")
        text = open(toml, encoding="utf-8").read()
        with open(toml, "w", encoding="utf-8", newline="\n") as f:
            f.write(text.replace("width = 3", "width = 5", 1))
        out = generate_hud(root)
        src = open(out, encoding="utf-8").read().replace("\r\n", "\n")
        check("the value is the i16 cell as a u16, a negative clamped to 0",
              "var v: u16 = 0\n" in src and "if raw > 0 { v = raw }" in src
              and "var v: u8 = raw" not in src)
        check("a 5-digit field counts its digits up to 10000",
              "if v >= 10000 { nd = 5 }" in src)
        check("a gauge clamps the cell to 0..255 (HP -1 is an EMPTY gauge)",
              "if graw > 255 { v = 255 } else if graw > 0 { v = graw }" in src)
        with open(toml, "w", encoding="utf-8", newline="\n") as f:
            f.write(text.replace("width = 3", "width = 6", 1))
        try:
            generate_hud(root)
            check("a width past 5 is refused", False, "generated")
        except mosaik_vm.VmError as e:
            check("a width past 5 is refused", "width 6" in str(e), e)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _raises(fn, needle):
    import mosaik_vm
    try:
        fn()
        return False, "no error"
    except mosaik_vm.VmError as e:
        return needle in str(e), str(e)
    except Exception as e:                       # noqa: BLE001 - not the clear error
        return False, "%s: %s" % (type(e).__name__, e)


def config():
    print("\n[config validation]")
    import shutil
    import tempfile
    from mosaik_vm.rooms import generate_rooms
    from mosaik_vm.rooms.config import load_player_config
    from mosaik_vm import glue
    from mosaik_vm.rooms.init_fade import first_wait
    tmp = tempfile.mkdtemp(prefix="batch_c_cfg_")
    try:
        root = os.path.join(tmp, "vm-showcase")
        shutil.copytree(os.path.join(ROOT, "projects", "vm-showcase"), root,
                        ignore=shutil.ignore_patterns("build"))
        wt = os.path.join(root, "world.toml")
        text = open(wt, encoding="utf-8").read()
        with open(wt, "w", encoding="utf-8", newline="\n") as f:
            f.write(text.replace('start_scene = "title"', 'start_scene = "titel"'))
        ok, why = _raises(lambda: generate_rooms(root), "names no scene")
        check("a start_scene naming no scene is an error (it booted room 0)", ok, why)
        st = os.path.join(tmp, "p")
        os.makedirs(st)
        with open(os.path.join(st, "studio.toml"), "w", encoding="utf-8") as f:
            f.write('[player]\njump = "high"\n')
        ok, why = _raises(lambda: load_player_config(st), "not a number")
        check("a non-numeric [player] value is an error (it fell to the default)", ok, why)
        with open(os.path.join(st, "studio.toml"), "w", encoding="utf-8") as f:
            f.write('[audio]\ngb = "hugee"\n')
        ok, why = _raises(lambda: glue._glue_audio_setting(st), "music driver")
        check("an [audio] driver typo is an error (it wired no driver)", ok, why)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check("huge_conflict reads the `gb` / `gbc` aliases as the GB family",
          glue.huge_conflict(["gb", "gbc"])[0] is True)
    check("...and still refuses a non-GB console (control)",
          glue.huge_conflict(["gb", "sms"])[0] is False)
    scripts = {"sub": [{"event": "ret"}, {"event": "fade_in"}]}
    check("init_fade: a called sub's `ret` returns to the caller (its fade_in "
          "after the ret is never reached)",
          first_wait([{"event": "call", "script": "sub"}, {"event": "wait", "frames": 5}],
                     scripts, returns={"sub"}) == "wait")
    check("init_fade: a top-level `ret` ends the thread before its fade_in",
          first_wait([{"event": "ret"}, {"event": "fade_in"}], {}) is None)


def main():
    print("=== review_batch_c_test ===")
    m14()
    h4_console()
    generators()
    music()
    hud()
    config()
    print("\n%s" % ("ALL PASS" if not FAILS else "%d FAILED" % len(FAILS)))
    for f in FAILS:
        print("  - " + f)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
